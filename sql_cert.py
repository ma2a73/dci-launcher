"""TDS prelogin + TLS handshake helper for SQL Server.

SQL Server doesn't speak raw TLS on port 1433. The TLS handshake records are
wrapped inside TDS PRELOGIN (0x12) packets. Once the handshake completes the
TLS record stream flows directly. This module does the bare minimum to capture
the server's certificate and to detect "self-signed / not trusted" cases the
Microsoft ODBC Driver 18 will refuse.

The prelogin packet shape (version + encryption + instopt + threadid + MARS) and
the ENCRYPT_ON flag mirror what pytds / Microsoft drivers send. SQL Server 2019
silently drops connections whose prelogin omits MARS, so we send the full thing.
"""

from __future__ import annotations

import hashlib
import socket
import ssl
import struct
from dataclasses import dataclass


TDS_PRELOGIN = 0x12

PL_VERSION = 0x00
PL_ENCRYPTION = 0x01
PL_INSTOPT = 0x02
PL_THREADID = 0x03
PL_MARS = 0x04
PL_TERMINATOR = 0xFF

ENCRYPT_OFF = 0x00
ENCRYPT_ON = 0x01
ENCRYPT_REQ = 0x02
ENCRYPT_NOT_SUP = 0x04


def _build_prelogin() -> bytes:
    # full pytds-style option set. SQL Server 2019 wants MARS in the table.
    opts = [
        (PL_VERSION, struct.pack(">IH", 0x09000000, 0)),
        (PL_ENCRYPTION, bytes([ENCRYPT_ON])),
        (PL_INSTOPT, b"\x00"),
        (PL_THREADID, struct.pack(">I", 0)),
        (PL_MARS, b"\x00"),
    ]
    options = bytearray()
    payload = bytearray()
    off = len(opts) * 5 + 1  # +1 for the terminator byte
    for opt, data in opts:
        options.extend(struct.pack(">BHH", opt, off, len(data)))
        off += len(data)
    options.append(PL_TERMINATOR)
    for _, data in opts:
        payload.extend(data)
    body = bytes(options) + bytes(payload)
    # status=EOM, spid=0, packetid=1, window=0
    header = struct.pack(">BBHHBB", TDS_PRELOGIN, 0x01, 8 + len(body), 0, 1, 0)
    return header + body


def _wrap_tds(data: bytes) -> bytes:
    return struct.pack(">BBHHBB", TDS_PRELOGIN, 0x01, 8 + len(data), 0, 1, 0) + data


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("socket closed mid-read")
        buf += chunk
    return buf


def _read_tds_packet(sock: socket.socket) -> bytes:
    header = _recv_exact(sock, 8)
    _ptype, _status, length = struct.unpack(">BBH", header[:4])
    body_len = length - 8
    if body_len <= 0:
        return b""
    return _recv_exact(sock, body_len)


def _do_tds_tls_handshake(sock: socket.socket, ctx: ssl.SSLContext, host: str) -> ssl.SSLObject:
    sock.sendall(_build_prelogin())
    _read_tds_packet(sock)  # discard server's prelogin response

    incoming = ssl.MemoryBIO()
    outgoing = ssl.MemoryBIO()
    sslobj = ctx.wrap_bio(incoming, outgoing, server_hostname=host)

    while True:
        try:
            sslobj.do_handshake()
            return sslobj
        except ssl.SSLWantReadError:
            out = outgoing.read()
            if out:
                sock.sendall(_wrap_tds(out))
            body = _read_tds_packet(sock)
            if not body:
                raise ConnectionError("server closed during TLS handshake")
            incoming.write(body)


@dataclass
class CertResult:
    der: bytes
    sha1: str  # hex thumbprint, uppercase
    common_name: str = ""  # cert subject CN, e.g. "SSL_Self_Signed_Fallback"
    sans: tuple[str, ...] = ()  # subjectAltName DNS entries


def _parse_subject_names(der: bytes) -> tuple[str, tuple[str, ...]]:
    # ssl._test_decode_cert is the only stdlib parser exposed for x509 metadata
    # and it insists on a PEM file on disk, so we drop one in tempfile and clean up.
    import os
    import tempfile
    pem = ssl.DER_cert_to_PEM_cert(der)
    with tempfile.NamedTemporaryFile(suffix=".pem", delete=False, mode="w") as fh:
        fh.write(pem)
        path = fh.name
    try:
        info = ssl._ssl._test_decode_cert(path)  # type: ignore[attr-defined]
    except Exception:
        return "", ()
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass

    cn = ""
    for rdn in info.get("subject", ()) or ():
        for key, value in rdn:
            if key == "commonName":
                cn = value
                break
        if cn:
            break

    sans: list[str] = []
    for kind, value in info.get("subjectAltName", ()) or ():
        if kind == "DNS":
            sans.append(value)
    return cn, tuple(sans)



def capture_certificate(host: str, port: int = 1433, timeout: float = 5.0) -> CertResult:
    """Connect, drive a TDS-wrapped TLS handshake, and return the server cert (DER)."""
    sock = socket.create_connection((host, port), timeout=timeout)
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        sslobj = _do_tds_tls_handshake(sock, ctx, host)
        der = sslobj.getpeercert(binary_form=True)
        if not der:
            raise RuntimeError("server presented no certificate")
        cn, sans = _parse_subject_names(der)
        return CertResult(
            der=der,
            sha1=hashlib.sha1(der).hexdigest().upper(),
            common_name=cn,
            sans=sans,
        )
    finally:
        try:
            sock.close()
        except Exception:
            pass


def is_certificate_trusted(host: str, port: int = 1433, timeout: float = 5.0) -> tuple[bool, str]:
    """Return (True, "") if the server cert validates against the local trust store.

    Only returns (False, message) for genuine cert verification failures. Network
    errors (unreachable host, prelogin protocol failures, generic SSLErrors) are
    treated as "not our problem" and return (True, "") so the launcher does not
    spam the trust-cert popup whenever the SQL Server is briefly unreachable or
    speaks a non-TLS dialect.
    """
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except Exception:
        return True, ""
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False  # qbase doesnt set HostNameInCertificate either
        try:
            _do_tds_tls_handshake(sock, ctx, host)
            return True, ""
        except ssl.SSLCertVerificationError as exc:
            return False, exc.verify_message or str(exc)
        except Exception:
            return True, ""
    finally:
        try:
            sock.close()
        except Exception:
            pass
