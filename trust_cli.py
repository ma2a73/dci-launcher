"""Headless cert-trust + hosts-entry + Settings.xml rewriter.

Designed to be invoked as `DCI Launcher.exe --trust-cert HOST PORT SETTINGS_XML`
from an elevated relaunch of the launcher itself. Writes a small log next to
the launcher EXE and exits with 0 on success, non-zero on failure.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from .settings_xml import write_settings
    from .sql_cert import capture_certificate
except ImportError:
    from dci_launcher.settings_xml import write_settings
    from dci_launcher.sql_cert import capture_certificate


def _log(msg: str) -> None:
    try:
        log_path = Path(sys.executable).resolve().parent / "trust-cert.log"
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(msg.rstrip() + "\r\n")
    except Exception:
        pass


def _write_hosts_entry(name: str, ip: str) -> str:
    hosts_path = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "drivers" / "etc" / "hosts"
    marker = "# DCI launcher - SQL Server SSL cert CN match"
    new_line = f"{ip}\t{name}\t{marker}"
    try:
        text = hosts_path.read_text(encoding="utf-8", errors="replace") if hosts_path.exists() else ""
    except Exception as exc:
        return f"read {hosts_path}: {exc}"

    lines = text.splitlines()
    kept: list[str] = []
    replaced = False
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            kept.append(raw); continue
        no_comment = stripped.split("#", 1)[0].split()
        if len(no_comment) >= 2 and name.lower() in (n.lower() for n in no_comment[1:]):
            if not replaced:
                kept.append(new_line); replaced = True
            continue
        kept.append(raw)

    if not replaced:
        if kept and kept[-1].strip():
            kept.append("")
        kept.append(new_line)

    new_text = "\r\n".join(kept) + "\r\n"
    try:
        hosts_path.write_text(new_text, encoding="utf-8")
    except Exception as exc:
        return f"write {hosts_path}: {exc}"
    return ""


def run_trust_cert(host: str, port: int, settings_xml: Path) -> int:
    _log(f"--- trust-cert host={host} port={port} settings={settings_xml}")
    try:
        cert = capture_certificate(host, port=port, timeout=8.0)
    except Exception as exc:
        _log(f"capture failed: {exc}")
        return 10

    cer_path = Path(tempfile.gettempdir()) / f"dci_sql_{host.replace('.', '_')}.cer"
    try:
        cer_path.write_bytes(cert.der)
        proc = subprocess.run(
            ["certutil.exe", "-addstore", "-f", "Root", str(cer_path)],
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        _log(f"certutil exec failed: {exc}")
        return 11
    finally:
        try:
            cer_path.unlink(missing_ok=True)
        except Exception:
            pass
    if proc.returncode != 0:
        _log(f"certutil rc={proc.returncode} out={proc.stdout!r} err={proc.stderr!r}")
        return 12

    alias_name = next(iter(cert.sans), "") or cert.common_name
    if not alias_name:
        _log("cert has no CN/SAN; skipping hosts + settings rewrite")
        return 0

    if alias_name.lower() != host.lower():
        hosts_err = _write_hosts_entry(alias_name, host)
        if hosts_err:
            _log(f"hosts write failed: {hosts_err}")
            return 13
        # deliberately do NOT rewrite <SqlServerName> in Settings.xml. rewriting
        # to the cert alias only works on machines whose hosts file also has the
        # entry, which doesn't transfer between PCs and breaks fresh installs
        # with "Named Pipes Provider error 53" or "TCP Provider: No such host is
        # known." leaving SqlServerName as the original IP/host plus trusting
        # the cert in the Root store is enough for driver 18.

    _log(f"OK sha1={cert.sha1} cn={cert.common_name} alias={alias_name}")
    return 0
