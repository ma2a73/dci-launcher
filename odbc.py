"""ODBC system DSN management via the Windows registry.

DCI Qbase is a 32-bit application, so by default DSNs are written to the
32-bit ODBC hive (WOW6432Node on 64-bit Windows). The 64-bit hive can also
be targeted.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional


IS_WINDOWS = sys.platform.startswith("win")

if IS_WINDOWS:
    import winreg


SQL_SERVER_DRIVERS = [
    # ordered newest -> oldest. the legacy "SQL Server" driver is still bundled
    # with windows and remains the only fallback on stripped-down installs.
    "ODBC Driver 18 for SQL Server",
    "ODBC Driver 17 for SQL Server",
    "ODBC Driver 13 for SQL Server",
    "SQL Server Native Client 11.0",
    "SQL Server",
]


@dataclass
class DsnDefinition:
    name: str
    server: str = "localhost"
    database: str = ""
    driver: str = "ODBC Driver 17 for SQL Server"
    trusted_connection: bool = False
    username: str = "sa"
    password: str = ""
    description: str = "DCI database connection"
    encrypt: bool = False
    trust_server_certificate: bool = True
    extras: Dict[str, str] = field(default_factory=dict)


def _hive_paths(bitness: str) -> tuple[str, str]:
    # 32-bit odbc dsns live under WOW6432Node on 64-bit windows; qbase is a
    # 32-bit app so its dsn has to land there, not under the native SOFTWARE\ODBC.
    if bitness == "32":
        base = r"SOFTWARE\WOW6432Node\ODBC"
    else:
        base = r"SOFTWARE\ODBC"
    return f"{base}\\ODBC.INI", f"{base}\\ODBCINST.INI"


def list_installed_drivers(bitness: str = "32") -> List[str]:
    if not IS_WINDOWS:
        return list(SQL_SERVER_DRIVERS)

    _, inst_path = _hive_paths(bitness)
    drivers: List[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{inst_path}\\ODBC Drivers") as key:
            i = 0
            while True:
                try:
                    name, _, _ = winreg.EnumValue(key, i)
                    drivers.append(name)
                    i += 1
                except OSError:
                    break
    except FileNotFoundError:
        pass

    sql_drivers = [d for d in drivers if "SQL Server" in d]
    return sql_drivers or [d for d in SQL_SERVER_DRIVERS if d in drivers] or SQL_SERVER_DRIVERS


def list_dsns(bitness: str = "32") -> List[str]:
    if not IS_WINDOWS:
        return []

    ini_path, _ = _hive_paths(bitness)
    names: List[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{ini_path}\\ODBC Data Sources") as key:
            i = 0
            while True:
                try:
                    name, _, _ = winreg.EnumValue(key, i)
                    names.append(name)
                    i += 1
                except OSError:
                    break
    except FileNotFoundError:
        pass
    return names


def list_all_dsns() -> List[tuple[str, str]]:
    """Return [(dsn_name, bitness), ...] from both ODBC hives."""
    out: List[tuple[str, str]] = []
    for bitness in ("32", "64"):
        for name in list_dsns(bitness):
            out.append((name, bitness))
    out.sort(key=lambda item: (item[0].lower(), item[1]))
    return out


def read_dsn(name: str, bitness: str = "32") -> Optional[DsnDefinition]:
    if not IS_WINDOWS:
        return None

    ini_path, _ = _hive_paths(bitness)
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{ini_path}\\{name}") as key:
            values: Dict[str, str] = {}
            i = 0
            while True:
                try:
                    n, v, _ = winreg.EnumValue(key, i)
                    values[n] = str(v)
                    i += 1
                except OSError:
                    break
    except FileNotFoundError:
        return None

    dsn = DsnDefinition(name=name)
    dsn.driver = values.pop("Driver", dsn.driver)
    dsn.server = values.pop("Server", dsn.server)
    dsn.database = values.pop("Database", values.pop("DefaultDatabase", ""))
    dsn.description = values.pop("Description", dsn.description)
    dsn.trusted_connection = values.pop("Trusted_Connection", "No").lower() in ("yes", "true", "1")
    dsn.encrypt = values.pop("Encrypt", "No").lower() in ("yes", "true", "1")
    dsn.trust_server_certificate = values.pop("TrustServerCertificate", "Yes").lower() in ("yes", "true", "1")
    dsn.username = values.pop("LastUser", dsn.username)
    dsn.extras = values
    return dsn


def write_dsn(dsn: DsnDefinition, bitness: str = "32") -> None:
    if not IS_WINDOWS:
        raise RuntimeError("ODBC registry operations require Windows.")

    ini_path, inst_path = _hive_paths(bitness)
    # if we can't resolve the driver dll from the friendly name, just write the
    # friendly name back to the registry; the driver manager will resolve it at
    # connect time from ODBCINST.INI's "ODBC Drivers" key.
    driver_dll = _resolve_driver_dll(dsn.driver, inst_path)

    sources_key = winreg.CreateKeyEx(
        winreg.HKEY_LOCAL_MACHINE, f"{ini_path}\\ODBC Data Sources", 0, winreg.KEY_SET_VALUE
    )
    with sources_key:
        winreg.SetValueEx(sources_key, dsn.name, 0, winreg.REG_SZ, dsn.driver)

    dsn_key = winreg.CreateKeyEx(
        winreg.HKEY_LOCAL_MACHINE, f"{ini_path}\\{dsn.name}", 0, winreg.KEY_SET_VALUE
    )
    with dsn_key:
        winreg.SetValueEx(dsn_key, "Driver", 0, winreg.REG_SZ, driver_dll or dsn.driver)
        winreg.SetValueEx(dsn_key, "Description", 0, winreg.REG_SZ, dsn.description)
        winreg.SetValueEx(dsn_key, "Server", 0, winreg.REG_SZ, dsn.server)
        if dsn.database:
            winreg.SetValueEx(dsn_key, "Database", 0, winreg.REG_SZ, dsn.database)
        winreg.SetValueEx(dsn_key, "Trusted_Connection", 0, winreg.REG_SZ,
                          "Yes" if dsn.trusted_connection else "No")
        if not dsn.trusted_connection and dsn.username:
            winreg.SetValueEx(dsn_key, "LastUser", 0, winreg.REG_SZ, dsn.username)
        winreg.SetValueEx(dsn_key, "Encrypt", 0, winreg.REG_SZ, "Yes" if dsn.encrypt else "No")
        winreg.SetValueEx(dsn_key, "TrustServerCertificate", 0, winreg.REG_SZ,
                          "Yes" if dsn.trust_server_certificate else "No")
        for key_name, value in dsn.extras.items():
            winreg.SetValueEx(dsn_key, key_name, 0, winreg.REG_SZ, value)


def delete_dsn(name: str, bitness: str = "32") -> None:
    if not IS_WINDOWS:
        return
    ini_path, _ = _hive_paths(bitness)
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, f"{ini_path}\\ODBC Data Sources", 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, name)
    except FileNotFoundError:
        pass

    try:
        winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, f"{ini_path}\\{name}")
    except FileNotFoundError:
        pass


def _resolve_driver_dll(driver_name: str, inst_path: str) -> Optional[str]:
    if not IS_WINDOWS:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{inst_path}\\{driver_name}") as key:
            value, _ = winreg.QueryValueEx(key, "Driver")
            return str(value)
    except FileNotFoundError:
        return None


def test_connection(dsn: DsnDefinition) -> tuple[bool, str]:
    """Best-effort connection test using pyodbc if available."""
    try:
        import pyodbc
    except ImportError:
        return False, "pyodbc is not installed — install it to enable connection tests."

    parts = [
        f"DRIVER={{{dsn.driver}}}",
        f"SERVER={dsn.server}",
    ]
    if dsn.database:
        parts.append(f"DATABASE={dsn.database}")
    if dsn.trusted_connection:
        parts.append("Trusted_Connection=Yes")
    else:
        parts.append(f"UID={dsn.username}")
        parts.append(f"PWD={dsn.password}")
    if dsn.trust_server_certificate:
        parts.append("TrustServerCertificate=Yes")
    parts.append("Encrypt=Yes" if dsn.encrypt else "Encrypt=No")

    try:
        conn = pyodbc.connect(";".join(parts), timeout=5)
        conn.close()
        return True, "Connection successful."
    except Exception as exc:
        return False, str(exc)


def list_databases(
    server: str,
    username: str = "",
    password: str = "",
    trusted: bool = False,
    driver: str = "ODBC Driver 17 for SQL Server",
) -> tuple[List[str], str]:
    """Return ([db_name, ...], error_message). Connects to master and queries sys.databases."""
    try:
        import pyodbc
    except ImportError:
        return [], "pyodbc is not installed."
    if not server.strip():
        return [], "Server is required."

    parts = [
        f"DRIVER={{{driver}}}",
        f"SERVER={server}",
        "DATABASE=master",
        "TrustServerCertificate=Yes",
        "Encrypt=No",
    ]
    if trusted:
        parts.append("Trusted_Connection=Yes")
    else:
        parts.append(f"UID={username}")
        parts.append(f"PWD={password}")

    try:
        conn = pyodbc.connect(";".join(parts), timeout=5)
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sys.databases "
            "WHERE name NOT IN ('tempdb') AND state_desc = 'ONLINE' "
            "ORDER BY name"
        )
        # tempdb is never a user db and offline ones show up in sys.databases
        # but blow up the second qbase tries to connect to them.
        names = [row[0] for row in cur.fetchall()]
        conn.close()
        return names, ""
    except Exception as exc:
        return [], str(exc)
