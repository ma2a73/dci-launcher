"""Entry point — `python -m dci_launcher`."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

try:
    from .ui import DEFAULT_CONFIG_NAME, LauncherApp
    from .setup_wizard import is_frozen, needs_setup, run_setup, LAUNCHER_CONFIG_NAME
except ImportError:
    # frozen/PyInstaller runs this file as a top-level script with no package context
    from dci_launcher.ui import DEFAULT_CONFIG_NAME, LauncherApp
    from dci_launcher.setup_wizard import is_frozen, needs_setup, run_setup, LAUNCHER_CONFIG_NAME


def _maybe_run_first_time_setup() -> Path | None:
    """If the EXE is launched somewhere with no installed tree, drive setup first."""
    if not is_frozen():
        return None

    # If we're already sitting next to a real .config (e.g. user double-clicked
    # the EXE inside C:\DCI after a previous install), no setup needed.
    here = Path(sys.executable).resolve().parent
    if (here / LAUNCHER_CONFIG_NAME).is_file():
        return here

    try:
        from .setup_wizard import DEFAULT_INSTALL_ROOT
    except ImportError:
        from dci_launcher.setup_wizard import DEFAULT_INSTALL_ROOT

    if not needs_setup(DEFAULT_INSTALL_ROOT):
        return DEFAULT_INSTALL_ROOT

    target = run_setup(parent=None)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dci_launcher",
                                     description="Configure and launch DCI Qbase line instances.")
    parser.add_argument("--config", "-c", default=None,
                        help="Path to SimpleAppLauncher.exe.config (defaults to one next to the launcher).")
    parser.add_argument("--no-setup", action="store_true",
                        help="Skip the first-run setup wizard even if the install tree is missing.")
    parser.add_argument("--trust-cert", nargs=3, metavar=("HOST", "PORT", "SETTINGS_XML"),
                        default=None,
                        help="Internal: import SQL Server cert, write hosts entry, "
                             "rewrite Settings.xml. Exits 0 on success.")
    args = parser.parse_args(argv)

    if args.trust_cert:
        try:
            from .trust_cli import run_trust_cert
        except ImportError:
            from dci_launcher.trust_cli import run_trust_cert
        host, port_s, settings_xml = args.trust_cert
        try:
            port = int(port_s)
        except ValueError:
            print(f"invalid port: {port_s}", file=sys.stderr)
            return 2
        return run_trust_cert(host, port, Path(settings_xml))

    if args.config:
        config_path = Path(args.config).resolve()
    else:
        install_dir: Path | None = None
        if not args.no_setup:
            install_dir = _maybe_run_first_time_setup()
        if install_dir is not None:
            try:
                os.chdir(install_dir)
            except OSError:
                pass
            config_path = install_dir / DEFAULT_CONFIG_NAME
        else:
            candidates = [
                Path.cwd() / DEFAULT_CONFIG_NAME,
                Path(sys.argv[0]).resolve().parent / DEFAULT_CONFIG_NAME,
            ]
            config_path = next((p for p in candidates if p.exists()), candidates[0])

    app = LauncherApp(config_path)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
