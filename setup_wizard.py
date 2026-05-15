"""First-run setup: unpack the bundled DCI tree and provision ODBC DSNs.

Only runs in the frozen/PyInstaller build. When the EXE is launched and the
target directory is missing the launcher's .config, this module:

  1. asks the user where to unpack
  2. copies the bundled tree (skip-existing, never overwrites edits)
  3. parses each Line*/Settings.xml and creates a 32-bit ODBC DSN per line
  4. returns the resolved target so __main__ can chdir into it and load the
     pre-filled launcher .config from there.
"""

from __future__ import annotations

import shutil
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Iterable, List, Tuple

from .odbc import DsnDefinition, write_dsn
from .settings_xml import read_settings


SETUP_MARKER = ".dci_launcher_setup"
DEFAULT_INSTALL_ROOT = Path(r"C:\DCI")
LAUNCHER_CONFIG_NAME = "SimpleAppLauncher.exe.config"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> Path | None:
    """Return the directory inside the frozen bundle that holds the staged tree."""
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return None
    candidate = Path(base) / "dci_bundle"
    return candidate if candidate.is_dir() else None


def needs_setup(target: Path) -> bool:
    if not target.exists():
        return True
    return not (target / LAUNCHER_CONFIG_NAME).is_file()


def run_setup(parent: tk.Misc | None = None) -> Path | None:
    """Drive the first-run setup. Returns the resolved install dir, or None on cancel."""
    src = bundle_root()
    if src is None:
        # not a frozen build, nothing to unpack
        return None

    target = _ask_install_dir(parent)
    if target is None:
        return None

    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        messagebox.showerror("DCI Launcher Setup", f"Could not create install folder:\n{exc}")
        return None

    copied, skipped = _copy_tree(src, target)

    dsn_results = _provision_dsns(target)

    _write_marker(target, copied, skipped, dsn_results)

    summary_lines = [
        f"Install location: {target}",
        f"Files copied: {copied}",
        f"Files left untouched: {skipped}",
        "",
        "ODBC DSNs:",
    ]
    if dsn_results:
        for name, ok, detail in dsn_results:
            tag = "OK " if ok else "!! "
            summary_lines.append(f"  {tag}{name} \u2014 {detail}")
    else:
        summary_lines.append("  (no Line*/Settings.xml found)")

    messagebox.showinfo("DCI Launcher Setup", "\n".join(summary_lines))
    return target


def _ask_install_dir(parent: tk.Misc | None) -> Path | None:
    initial = str(DEFAULT_INSTALL_ROOT if DEFAULT_INSTALL_ROOT.parent.exists() else Path.home())
    chosen = filedialog.askdirectory(
        parent=parent,
        title="Select where to install the DCI launcher tree",
        initialdir=initial,
        mustexist=False,
    )
    if not chosen:
        return None
    return Path(chosen).resolve()


def _copy_tree(src: Path, dst: Path) -> Tuple[int, int]:
    """Recursive skip-existing copy. Returns (copied, skipped) file counts."""
    copied = 0
    skipped = 0
    for entry in src.rglob("*"):
        rel = entry.relative_to(src)
        out = dst / rel
        if entry.is_dir():
            out.mkdir(parents=True, exist_ok=True)
            continue
        if out.exists():
            skipped += 1
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(entry, out)
            copied += 1
        except OSError:
            skipped += 1
    return copied, skipped


def _line_dirs(target: Path) -> List[Path]:
    if not target.is_dir():
        return []
    out: List[Path] = []
    for child in sorted(target.iterdir()):
        if not child.is_dir():
            continue
        name = child.name.lower()
        if not name.startswith("line"):
            continue
        if not (child / "Settings.xml").is_file():
            continue
        out.append(child)
    return out


def _provision_dsns(target: Path) -> List[Tuple[str, bool, str]]:
    """Read each Line*/Settings.xml and create a matching 32-bit ODBC DSN."""
    results: List[Tuple[str, bool, str]] = []
    for line_dir in _line_dirs(target):
        settings_path = line_dir / "Settings.xml"
        try:
            settings = read_settings(settings_path)
        except Exception as exc:
            results.append((line_dir.name, False, f"could not read Settings.xml: {exc}"))
            continue
        if settings is None:
            results.append((line_dir.name, False, "Settings.xml missing or unreadable"))
            continue

        dsn_name = (settings.data_source or "").strip()
        sql_server = (settings.sql_server or "").strip()
        # ODBC.INI Server field doesn't want a protocol prefix; FireDAC handles
        # the "tcp:" prefix itself but a raw DSN entry should be the bare name.
        if sql_server.lower().startswith("tcp:"):
            sql_server = sql_server[4:]
        elif sql_server.lower().startswith(("np:", "lpc:")):
            sql_server = sql_server.split(":", 1)[1]
        if not dsn_name or not sql_server:
            results.append((line_dir.name, False,
                            "skipped (no <sysdatasrc> or <SqlServerName> in Settings.xml)"))
            continue

        dsn = DsnDefinition(
            name=dsn_name,
            server=sql_server,
            database="Qbase",
            driver=_pick_qbase_driver(),
            trusted_connection=False,
            username="sa",
            password=(settings.data_password or "").strip(),
            description=f"DCI {line_dir.name} ({dsn_name})",
            encrypt=False,
            trust_server_certificate=True,
        )
        try:
            write_dsn(dsn, bitness="32")
            results.append((dsn_name, True, f"32-bit DSN -> {sql_server} ({dsn.driver})"))
        except PermissionError:
            results.append((dsn_name, False, "needs admin to write HKLM (rerun elevated)"))
        except Exception as exc:
            results.append((dsn_name, False, f"failed: {exc}"))
    return results


def _pick_qbase_driver() -> str:
    # prefer a modern Microsoft driver (better SSL, actively maintained), fall
    # back to the legacy "SQL Server" driver (SQLSRV32.dll) which ships with
    # every Windows install. either works for QBase. only the modern drivers
    # care about the cert chain, and the trust-cert helper handles that.
    try:
        from .odbc import list_installed_drivers
    except ImportError:
        from dci_launcher.odbc import list_installed_drivers
    try:
        installed = list_installed_drivers(bitness="32")
    except Exception:
        installed = []
    for candidate in ("ODBC Driver 17 for SQL Server",
                      "ODBC Driver 18 for SQL Server",
                      "SQL Server"):
        if candidate in installed:
            return candidate
    return "SQL Server"


def _write_marker(target: Path, copied: int, skipped: int,
                  dsn_results: Iterable[Tuple[str, bool, str]]) -> None:
    from datetime import datetime
    marker = target / SETUP_MARKER
    lines = [
        f"DCI launcher setup completed at {datetime.now().isoformat(timespec='seconds')}",
        f"Files copied: {copied}",
        f"Files left untouched: {skipped}",
        "DSNs:",
    ]
    for name, ok, detail in dsn_results:
        lines.append(f"  [{'OK' if ok else 'FAIL'}] {name} - {detail}")
    try:
        marker.write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        pass
