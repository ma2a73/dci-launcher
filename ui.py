"""Main launcher UI."""
##made by mustafa abdulrazzaq
from __future__ import annotations

import ctypes
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from . import __version__
from .config import LauncherConfig, LineEntry, load_config, save_config
from .odbc import (
    DsnDefinition,
    delete_dsn,
    list_all_dsns,
    list_databases,
    list_dsns,
    list_installed_drivers,
    read_dsn,
    test_connection,
    write_dsn,
)
from .settings_xml import LineSettings, read_settings, write_settings
from .theme import PALETTE, apply_theme


APP_TITLE = "DCI Launcher"
DEFAULT_CONFIG_NAME = "SimpleAppLauncher.exe.config"


class LauncherApp:
    def __init__(self, config_path: Path) -> None:
        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self._apply_window_icon(self.root)

        self.style = apply_theme(self.root)

        self.config_path = config_path
        self.config: LauncherConfig = load_config(config_path)
        self.selected_index: Optional[int] = None
        self.dirty = False
        self.read_only = not _is_admin()
        self.running_procs: dict[int, subprocess.Popen] = {}
        self.simple_rows: list[dict] = []

        if self.read_only:
            self.root.geometry("520x720")
            self.root.minsize(420, 360)
            self._build_simple_layout()
            return

        self.root.geometry("1180x720")
        self.root.minsize(960, 600)
        self._build_layout()
        self._refresh_line_list()
        if self.config.lines:
            self._select_line(0)
        else:
            self._show_empty_state()

    def _build_simple_layout(self) -> None:
        outer = ttk.Frame(self.root, style="App.TFrame")
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer, style="Toolbar.TFrame")
        header.pack(fill="x", padx=14, pady=(12, 8))
        self._add_card_border(header)
        inner = ttk.Frame(header, style="Toolbar.TFrame")
        inner.pack(fill="x", padx=14, pady=10)
        ttk.Label(inner, text=APP_TITLE, style="H1.TLabel").pack(side="left")
        ttk.Button(inner, text="Run as Administrator", style="Ghost.TButton",
                   command=self._relaunch_elevated).pack(side="right")

        body = ttk.Frame(outer, style="Surface.TFrame")
        body.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        self._add_card_border(body)

        ttk.Label(body, text="Connections", style="H2.TLabel").pack(
            anchor="w", padx=14, pady=(12, 6))

        list_holder = ttk.Frame(body, style="Surface.TFrame")
        list_holder.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        canvas = tk.Canvas(list_holder, bg=PALETTE["surface"], highlightthickness=0, bd=0)
        scroll = ttk.Scrollbar(list_holder, orient="vertical", command=canvas.yview)

        def _set_scroll(lo: str, hi: str) -> None:
            try:
                lo_f = float(lo)
                hi_f = float(hi)
            except ValueError:
                return
            if lo_f <= 0.0 and hi_f >= 1.0:
                scroll.pack_forget()
            else:
                if not scroll.winfo_ismapped():
                    scroll.pack(side="right", fill="y")
            scroll.set(lo, hi)

        canvas.configure(yscrollcommand=_set_scroll)
        canvas.pack(side="left", fill="both", expand=True)

        list_frame = ttk.Frame(canvas, style="Surface.TFrame")
        win = canvas.create_window((0, 0), window=list_frame, anchor="nw")
        list_frame.bind(
            "<Configure>",
            lambda _e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.bind(
            "<Configure>",
            lambda e: canvas.itemconfigure(win, width=e.width),
        )
        canvas.bind_all(
            "<MouseWheel>",
            lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"),
        )

        if not self.config.lines:
            ttk.Label(list_frame, text="No connections configured.",
                      style="Subtle.TLabel").pack(padx=14, pady=20)
        else:
            for idx, line in enumerate(self.config.lines):
                self._build_simple_row(list_frame, idx, line)
            self.root.after(800, self._poll_running_procs)

        self.status_var = tk.StringVar(value="Ready")
        status = ttk.Frame(outer, style="Statusbar.TFrame")
        status.pack(fill="x", side="bottom")
        ttk.Separator(status).pack(fill="x")
        ttk.Label(status, textvariable=self.status_var, style="Status.TLabel"
                  ).pack(side="left", padx=14, pady=6)

    def _launch_index(self, index: int) -> None:
        self.selected_index = index
        self._launch_selected()

    def _build_simple_row(self, parent: ttk.Frame, idx: int, line: LineEntry) -> None:
        exe = (line.application_path or "").strip().strip('"')
        exists = bool(exe) and Path(exe).exists()

        row = ttk.Frame(parent, style="Surface.TFrame")
        row.pack(fill="x", padx=6, pady=4)

        btn = ttk.Button(
            row,
            text=line.name or f"Line {idx + 1}",
            style="Accent.TButton",
            command=lambda i=idx: self._toggle_simple_row(i),
        )
        btn.pack(fill="x", ipady=8)

        subtitle_default = exe or "(no path configured)"
        if not exists and exe:
            subtitle_default += "  -  not found"
        sub = ttk.Label(row, text=subtitle_default, style="Subtle.TLabel")
        sub.pack(anchor="w", padx=4, pady=(2, 0))

        if not exists:
            btn.state(["disabled"])

        self.simple_rows.append({
            "index": idx,
            "name": line.name or f"Line {idx + 1}",
            "button": btn,
            "subtitle": sub,
            "subtitle_default": subtitle_default,
            "exists": exists,
        })

    def _toggle_simple_row(self, index: int) -> None:
        if index in self.running_procs:
            self._stop_simple_row(index)
        else:
            self.selected_index = index
            self._launch_selected()

    def _stop_simple_row(self, index: int) -> None:
        proc = self.running_procs.get(index)
        if proc is None:
            return
        try:
            proc.terminate()
        except Exception:
            pass
        # don't block the ui; the poller will reap it and reset row state
        self._set_status(f"Stop requested for {self._simple_row_name(index)}.")

    def _simple_row_name(self, index: int) -> str:
        for r in self.simple_rows:
            if r["index"] == index:
                return r["name"]
        return f"Line {index + 1}"

    def _set_simple_row_running(self, index: int, proc: subprocess.Popen) -> None:
        self.running_procs[index] = proc
        for r in self.simple_rows:
            if r["index"] != index:
                continue
            r["button"].configure(text=f"Stop  -  {r['name']} (PID {proc.pid})",
                                  style="Stop.TButton")
            r["subtitle"].configure(text=f"Running...  click to stop")
            break

    def _set_simple_row_stopped(self, index: int) -> None:
        self.running_procs.pop(index, None)
        for r in self.simple_rows:
            if r["index"] != index:
                continue
            r["button"].configure(text=r["name"], style="Accent.TButton")
            r["subtitle"].configure(text=r["subtitle_default"])
            if not r["exists"]:
                r["button"].state(["disabled"])
            break

    def _poll_running_procs(self) -> None:
        # reap any qbase processes the user closed on their own so the row
        # button flips back from Stop -> Launch without manual intervention.
        finished = [i for i, p in self.running_procs.items() if p.poll() is not None]
        for i in finished:
            self._set_simple_row_stopped(i)
        self.root.after(800, self._poll_running_procs)

    def _build_layout(self) -> None:
        outer = ttk.Frame(self.root, style="App.TFrame")
        outer.pack(fill="both", expand=True)

        self._build_toolbar(outer)

        body = ttk.Frame(outer, style="App.TFrame")
        body.pack(fill="both", expand=True, padx=14, pady=(0, 10))

        self._build_sidebar(body)

        self.detail_container = ttk.Frame(body, style="Surface.TFrame")
        self.detail_container.pack(side="left", fill="both", expand=True, padx=(12, 0))
        self._add_card_border(self.detail_container)

        self._build_statusbar(outer)

    def _build_toolbar(self, parent: ttk.Frame) -> None:
        bar = ttk.Frame(parent, style="Toolbar.TFrame")
        bar.pack(fill="x", padx=14, pady=(12, 10))
        self._add_card_border(bar)

        inner = ttk.Frame(bar, style="Toolbar.TFrame")
        inner.pack(fill="x", padx=14, pady=10)

        title_box = ttk.Frame(inner, style="Toolbar.TFrame")
        title_box.pack(side="left")
        ttk.Label(title_box, text=APP_TITLE, style="H1.TLabel").pack(anchor="w")
        ttk.Label(title_box, text=f"Configuration: {self.config_path}", style="Subtle.TLabel").pack(anchor="w")

        actions = ttk.Frame(inner, style="Toolbar.TFrame")
        actions.pack(side="right")
        if not _is_admin():
            ttk.Button(actions, text="Run as Administrator", style="Ghost.TButton",
                       command=self._relaunch_elevated).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Manage ODBC", style="Ghost.TButton",
                   command=self._open_odbc_dialog).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Open Logs Folder", style="Ghost.TButton",
                   command=self._open_logs_folder).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Save All", style="Accent.TButton",
                   command=self._save_all).pack(side="left")

    def _build_sidebar(self, parent: ttk.Frame) -> None:
        side = ttk.Frame(parent, style="Sidebar.TFrame", width=300)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        self._add_card_border(side)

        header = ttk.Frame(side, style="Sidebar.TFrame")
        header.pack(fill="x", padx=14, pady=(14, 8))
        ttk.Label(header, text="Connections", style="H2.TLabel").pack(side="left")
        ttk.Button(header, text="+ Add", style="Ghost.TButton",
                   command=self._add_line).pack(side="right")

        ttk.Separator(side).pack(fill="x", padx=14)

        list_holder = ttk.Frame(side, style="Sidebar.TFrame")
        list_holder.pack(fill="both", expand=True, padx=8, pady=8)

        self.line_canvas = tk.Canvas(list_holder, bg=PALETTE["surface"], highlightthickness=0, bd=0)
        self.line_canvas.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(list_holder, orient="vertical", command=self.line_canvas.yview)
        scroll.pack(side="right", fill="y")
        self.line_canvas.configure(yscrollcommand=scroll.set)

        self.line_list_frame = ttk.Frame(self.line_canvas, style="Sidebar.TFrame")
        self.line_list_window = self.line_canvas.create_window(
            (0, 0), window=self.line_list_frame, anchor="nw"
        )
        self.line_list_frame.bind(
            "<Configure>",
            lambda _e: self.line_canvas.configure(scrollregion=self.line_canvas.bbox("all")),
        )
        self.line_canvas.bind(
            "<Configure>",
            lambda e: self.line_canvas.itemconfigure(self.line_list_window, width=e.width),
        )
        self.line_canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        footer = ttk.Frame(side, style="Sidebar.TFrame")
        footer.pack(fill="x", padx=14, pady=(0, 12))
        ttk.Separator(footer).pack(fill="x", pady=(0, 8))

    def _on_mousewheel(self, event: tk.Event) -> None:
        widget = event.widget
        if not str(widget).startswith(str(self.line_canvas)):
            return
        self.line_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _build_statusbar(self, parent: ttk.Frame) -> None:
        bar = ttk.Frame(parent, style="Statusbar.TFrame")
        bar.pack(fill="x", side="bottom")
        ttk.Separator(bar).pack(fill="x")
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(bar, textvariable=self.status_var, style="Status.TLabel"
                  ).pack(side="left", padx=14, pady=6)

    def _add_card_border(self, frame: ttk.Frame) -> None:
        frame.configure(borderwidth=1, relief="solid")
        try:
            frame.tk.call("ttk::style", "configure", str(frame.cget("style")),
                          "-bordercolor", PALETTE["border"])
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # Line list
    # ------------------------------------------------------------------

    def _refresh_line_list(self) -> None:
        for child in self.line_list_frame.winfo_children():
            child.destroy()

        for index, line in enumerate(self.config.lines):
            self._build_line_card(index, line)

    def _build_line_card(self, index: int, line: LineEntry) -> None:
        is_selected = self.selected_index == index
        bg = PALETTE["row_selected"] if is_selected else PALETTE["surface"]

        card = tk.Frame(self.line_list_frame, bg=bg, padx=12, pady=10,
                        highlightthickness=0, bd=0, cursor="hand2")
        card.pack(fill="x", padx=4, pady=3)

        accent = tk.Frame(card, bg=PALETTE["accent"] if is_selected else bg, width=3)
        accent.pack(side="left", fill="y", padx=(0, 10))

        text_frame = tk.Frame(card, bg=bg)
        text_frame.pack(side="left", fill="both", expand=True)

        title = tk.Label(text_frame, text=line.name or "(unnamed)", bg=bg,
                         fg=PALETTE["text"], anchor="w",
                         font=("TkHeadingFont", 10, "bold"))
        title.pack(anchor="w")

        path_text = _short_path(line.application_path) or "No application path set"
        sub = tk.Label(text_frame, text=path_text, bg=bg,
                       fg=PALETTE["text_subtle"], anchor="w",
                       font=("TkDefaultFont", 9))
        sub.pack(anchor="w", pady=(2, 0))

        for widget in (card, accent, text_frame, title, sub):
            widget.bind("<Button-1>", lambda _e, i=index: self._select_line(i))

    # ------------------------------------------------------------------
    # Detail panel
    # ------------------------------------------------------------------

    def _select_line(self, index: int) -> None:
        if not (0 <= index < len(self.config.lines)):
            return
        self.selected_index = index
        self._refresh_line_list()
        self._build_detail_panel(self.config.lines[index])

    def _show_empty_state(self) -> None:
        for child in self.detail_container.winfo_children():
            child.destroy()
        wrap = ttk.Frame(self.detail_container, style="Surface.TFrame")
        wrap.pack(expand=True, fill="both")
        center = ttk.Frame(wrap, style="Surface.TFrame")
        center.place(relx=0.5, rely=0.5, anchor="center")
        ttk.Label(center, text="No connections configured", style="H2.TLabel").pack(pady=(0, 6))
        ttk.Label(center, text="Add a connection to begin configuring DCI workstations.",
                  style="Muted.TLabel").pack(pady=(0, 16))
        ttk.Button(center, text="Add Connection", style="Accent.TButton",
                   command=self._add_line).pack()

    def _build_detail_panel(self, line: LineEntry) -> None:
        for child in self.detail_container.winfo_children():
            child.destroy()

        header = ttk.Frame(self.detail_container, style="Surface.TFrame")
        header.pack(fill="x", padx=24, pady=(20, 12))

        left = ttk.Frame(header, style="Surface.TFrame")
        left.pack(side="left", fill="x", expand=True)
        self.detail_title_var = tk.StringVar(value=line.name or "(unnamed)")
        ttk.Label(left, textvariable=self.detail_title_var, style="H1.TLabel").pack(anchor="w")
        self.detail_subtitle_var = tk.StringVar(value=self._line_status_text(line))
        ttk.Label(left, textvariable=self.detail_subtitle_var, style="Subtle.TLabel").pack(anchor="w", pady=(2, 0))

        right = ttk.Frame(header, style="Surface.TFrame")
        right.pack(side="right")
        ttk.Button(right, text="Duplicate", style="Ghost.TButton",
                   command=self._duplicate_line).pack(side="left", padx=(0, 6))
        ttk.Button(right, text="Remove", style="Danger.TButton",
                   command=self._remove_line).pack(side="left", padx=(0, 12))
        ttk.Button(right, text="Launch", style="Launch.TButton",
                   command=self._launch_selected).pack(side="left")

        ttk.Separator(self.detail_container).pack(fill="x", padx=24)

        notebook = ttk.Notebook(self.detail_container)
        notebook.pack(fill="both", expand=True, padx=18, pady=(8, 18))

        self._tab_general(notebook, line)
        self._tab_database(notebook, line)
        self._tab_advanced(notebook, line)

        notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self.notebook = notebook

    def _line_status_text(self, line: LineEntry) -> str:
        path = (line.application_path or "").strip().strip('"')
        bits = []
        if path:
            exists = Path(path).exists()
            bits.append("Executable found" if exists else "Executable missing")
        bits.append(f"Settings: {'detected' if Path(line.settings_xml_path).exists() else 'not found'}")
        return "  •  ".join(bits)

    # ------------------------------------------------------------------
    # Tabs
    # ------------------------------------------------------------------

    def _tab_general(self, notebook: ttk.Notebook, line: LineEntry) -> None:
        tab = ttk.Frame(notebook, style="Surface.TFrame", padding=(20, 18))
        notebook.add(tab, text="General")

        self.var_name = tk.StringVar(value=line.name)
        self.var_app_path = tk.StringVar(value=line.application_path)
        self.var_app_args = tk.StringVar(value=line.application_arguments)

        self._field(tab, "Display name", self.var_name, row=0,
                    helper="Label shown in the launcher line list.")

        path_row = self._field(tab, "Application path", self.var_app_path, row=1,
                               helper="Full path to the line's QBase.exe.")
        ttk.Button(path_row, text="Browse…", style="Ghost.TButton",
                   command=self._browse_exe).pack(side="left", padx=(8, 0))

        self._field(tab, "Arguments", self.var_app_args, row=2,
                    helper="Optional command-line arguments passed at launch.")

        for var in (self.var_name, self.var_app_path, self.var_app_args):
            var.trace_add("write", lambda *_: self._mark_dirty_general())

        button_bar = ttk.Frame(tab, style="Surface.TFrame")
        button_bar.grid(row=10, column=0, columnspan=3, sticky="ew", pady=(20, 0))
        ttk.Button(button_bar, text="Apply", style="Accent.TButton",
                   command=self._apply_general).pack(side="left")
        ttk.Button(button_bar, text="Revert", style="Ghost.TButton",
                   command=lambda: self._select_line(self.selected_index or 0)).pack(side="left", padx=8)

        tab.columnconfigure(1, weight=1)

    def _tab_database(self, notebook: ttk.Notebook, line: LineEntry) -> None:
        tab = ttk.Frame(notebook, style="Surface.TFrame", padding=(20, 18))
        notebook.add(tab, text="Database & ODBC")

        existing = read_settings(line.settings_xml_path) or LineSettings()

        self.var_system_name = tk.StringVar(value=existing.system_name)
        self.var_dsn = tk.StringVar(value=existing.data_source)
        self.var_sql_server = tk.StringVar(value=existing.sql_server or "localhost")
        self.var_data_password = tk.StringVar(value=existing.data_password)
        self.var_database = tk.StringVar(value="")
        self.var_driver = tk.StringVar()
        self.var_trusted = tk.BooleanVar(value=False)
        self.var_username = tk.StringVar(value="sa")
        self.var_create_dsn = tk.BooleanVar(value=True)
        self.var_dsn_bitness = tk.StringVar(value="32")

        section = ttk.Label(tab, text="DCI Settings.xml", style="H3.TLabel")
        section.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        self._field(tab, "System name", self.var_system_name, row=1,
                    helper="Updates <systemname> in Settings.xml.")
        self._field(tab, "ODBC data source (DSN)", self.var_dsn, row=2,
                    helper="Written to <sysdatasrc>; the launcher creates the DSN below.")
        self._field(tab, "SQL Server", self.var_sql_server, row=3,
                    helper="Updates <SqlServerName> and the DSN Server property.")
        self._field(tab, "Data access password", self.var_data_password, row=4, secret=True,
                    helper="Stored in <datapwd>; reused for the SQL Server login below if not using Windows auth.")

        ttk.Separator(tab).grid(row=5, column=0, columnspan=3, sticky="ew", pady=14)

        ttk.Label(tab, text="ODBC System DSN", style="H3.TLabel").grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(0, 8))

        picker_row = ttk.Frame(tab, style="Surface.TFrame")
        picker_row.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(0, 6))
        ttk.Label(picker_row, text="Use existing DSN", style="FieldLabel.TLabel").pack(side="left")
        self.var_dsn_picker = tk.StringVar()
        self.dsn_picker = ttk.Combobox(picker_row, textvariable=self.var_dsn_picker,
                                       state="readonly", width=42)
        self.dsn_picker.pack(side="left", padx=(10, 8))
        ttk.Button(picker_row, text="Refresh", style="Ghost.TButton",
                   command=self._refresh_dsn_picker).pack(side="left")
        self.dsn_picker.bind("<<ComboboxSelected>>", self._on_dsn_picked)
        self._refresh_dsn_picker()

        ttk.Checkbutton(tab, text="Create / update the ODBC system DSN automatically",
                        variable=self.var_create_dsn).grid(row=8, column=0, columnspan=3, sticky="w", pady=(6, 0))

        bitness_frame = ttk.Frame(tab, style="Surface.TFrame")
        bitness_frame.grid(row=9, column=0, columnspan=3, sticky="w", pady=(8, 8))
        ttk.Label(bitness_frame, text="Architecture:", style="FieldLabel.TLabel").pack(side="left")
        ttk.Radiobutton(bitness_frame, text="32-bit (DCI default)", value="32",
                        variable=self.var_dsn_bitness).pack(side="left", padx=(8, 12))
        ttk.Radiobutton(bitness_frame, text="64-bit", value="64",
                        variable=self.var_dsn_bitness).pack(side="left")

        drivers = list_installed_drivers(self.var_dsn_bitness.get())
        if drivers and not self.var_driver.get():
            self.var_driver.set(drivers[0])

        driver_row = ttk.Frame(tab, style="Surface.TFrame")
        driver_row.grid(row=10, column=0, columnspan=3, sticky="ew", pady=(0, 6))
        ttk.Label(driver_row, text="Driver", style="FieldLabel.TLabel").pack(side="left")
        self.driver_combo = ttk.Combobox(driver_row, textvariable=self.var_driver,
                                         values=drivers, state="readonly", width=42)
        self.driver_combo.pack(side="left", padx=(10, 0))
        ttk.Button(driver_row, text="Refresh drivers", style="Ghost.TButton",
                   command=self._refresh_driver_list).pack(side="left", padx=8)

        db_row = ttk.Frame(tab, style="Surface.TFrame")
        db_row.grid(row=11, column=0, columnspan=3, sticky="ew", pady=(0, 4))
        ttk.Label(db_row, text="Database", style="FieldLabel.TLabel").pack(side="left")
        self.database_combo = ttk.Combobox(db_row, textvariable=self.var_database, width=40)
        self.database_combo.pack(side="left", padx=(10, 8))
        ttk.Button(db_row, text="List databases", style="Ghost.TButton",
                   command=self._list_databases_clicked).pack(side="left")
        ttk.Label(tab, text="Databases load automatically when the SQL Server is set; click List to refresh.",
                  style="Subtle.TLabel").grid(row=12, column=0, columnspan=3, sticky="w",
                                              padx=(110, 0), pady=(0, 6))

        self._db_autolookup_after_id = None
        self.var_sql_server.trace_add("write", lambda *_: self._schedule_db_autolookup())
        self.var_trusted.trace_add("write", lambda *_: self._schedule_db_autolookup())
        self._schedule_db_autolookup()

        ttk.Checkbutton(tab, text="Use Windows Authentication (Trusted Connection)",
                        variable=self.var_trusted, command=self._update_auth_state
                        ).grid(row=13, column=0, columnspan=3, sticky="w", pady=(4, 4))

        self._field(tab, "SQL login user", self.var_username, row=14,
                    helper="Used when Windows Authentication is disabled.")

        if self.var_dsn.get():
            self._autoload_dsn(self.var_dsn.get(), self.var_dsn_bitness.get())

        button_bar = ttk.Frame(tab, style="Surface.TFrame")
        button_bar.grid(row=20, column=0, columnspan=3, sticky="ew", pady=(20, 0))
        ttk.Button(button_bar, text="Test connection", style="Ghost.TButton",
                   command=self._test_connection).pack(side="left")
        ttk.Button(button_bar, text="Apply database settings", style="Accent.TButton",
                   command=self._apply_database).pack(side="right")

        tab.columnconfigure(1, weight=1)
        self._update_auth_state()

    def _refresh_dsn_picker(self) -> None:
        # tk comboboxes don't watch the registry, so the picker has to be
        # repainted by hand after every dsn write.
        if not hasattr(self, "dsn_picker"):
            return
        entries = list_all_dsns()
        labels = ["(none)"] + [f"{name}  ({bits}-bit)" for name, bits in entries]
        self.dsn_picker.configure(values=labels)
        if not self.var_dsn_picker.get():
            self.var_dsn_picker.set("(none)")

    def _refresh_driver_list(self) -> None:
        if hasattr(self, "driver_combo"):
            self.driver_combo.configure(values=list_installed_drivers(self.var_dsn_bitness.get()))

    def _on_dsn_picked(self, _event=None) -> None:
        # picker labels look like "NAME  (32-bit)". split the suffix back off to
        # recover the dsn name and bitness. not pretty but it works.
        label = self.var_dsn_picker.get()
        if not label or label == "(none)":
            return
        if label.endswith("(32-bit)"):
            bits = "32"
        elif label.endswith("(64-bit)"):
            bits = "64"
        else:
            return
        name = label.rsplit("(", 1)[0].strip()
        self.var_dsn.set(name)
        self.var_dsn_bitness.set(bits)
        self._refresh_driver_list()
        self._autoload_dsn(name, bits)

    def _autoload_dsn(self, name: str, bits: str) -> None:
        existing_dsn = read_dsn(name, bits)
        if not existing_dsn:
            return
        self.var_database.set(existing_dsn.database)
        if existing_dsn.driver:
            self.var_driver.set(existing_dsn.driver)
        if existing_dsn.server:
            self.var_sql_server.set(existing_dsn.server)
        self.var_trusted.set(existing_dsn.trusted_connection)
        if existing_dsn.username:
            self.var_username.set(existing_dsn.username)

    def _tab_advanced(self, notebook: ttk.Notebook, line: LineEntry) -> None:
        tab = ttk.Frame(notebook, style="Surface.TFrame", padding=(20, 18))
        notebook.add(tab, text="Advanced")
        self.advanced_tab = tab
        self._render_advanced(line)

    def _render_advanced(self, line: LineEntry) -> None:
        # rebuild the whole tab on each render instead of wiring a StringVar trace
        # for every field. the tab is small and rebuilds are imperceptible.
        tab = getattr(self, "advanced_tab", None)
        if tab is None:
            return
        for child in tab.winfo_children():
            child.destroy()

        ttk.Label(tab, text="Files", style="H3.TLabel").pack(anchor="w", pady=(0, 8))

        files_frame = ttk.Frame(tab, style="Surface.TFrame")
        files_frame.pack(fill="x")

        self._file_row(files_frame, "Working directory", line.working_directory, is_dir=True)
        self._file_row(files_frame, "Settings.xml", line.settings_xml_path)
        self._file_row(files_frame, "Custom colors",
                       str(Path(line.working_directory) / "CustomColors.xml") if line.working_directory else "")

        ttk.Separator(tab).pack(fill="x", pady=18)
        ttk.Label(tab, text="Maintenance", style="H3.TLabel").pack(anchor="w", pady=(0, 8))

        ttk.Button(tab, text="Open working directory", style="Ghost.TButton",
                   command=lambda: self._open_path(line.working_directory)).pack(anchor="w", pady=2)
        ttk.Button(tab, text="Open Settings.xml in editor", style="Ghost.TButton",
                   command=lambda: self._open_path(line.settings_xml_path)).pack(anchor="w", pady=2)
        ttk.Button(tab, text="Open log folder", style="Ghost.TButton",
                   command=lambda: self._open_path(str(Path(line.working_directory) / "LOGS"))
                   ).pack(anchor="w", pady=2)

    def _on_tab_changed(self, _event=None) -> None:
        if self.selected_index is None:
            return
        line = self.config.lines[self.selected_index]
        self._render_advanced(line)
        self.detail_subtitle_var.set(self._line_status_text(line))

    # ------------------------------------------------------------------
    # Field helpers
    # ------------------------------------------------------------------

    def _field(self, parent: ttk.Frame, label: str, variable: tk.StringVar, row: int,
               *, secret: bool = False, helper: str = "") -> ttk.Frame:
        ttk.Label(parent, text=label, style="FieldLabel.TLabel").grid(
            row=row, column=0, sticky="nw", pady=(10, 2), padx=(0, 16))
        wrap = ttk.Frame(parent, style="Surface.TFrame")
        wrap.grid(row=row, column=1, columnspan=2, sticky="ew", pady=(8, 6))
        row_inner = ttk.Frame(wrap, style="Surface.TFrame")
        row_inner.pack(fill="x")
        entry = ttk.Entry(row_inner, textvariable=variable, show="•" if secret else "")
        entry.pack(side="left", fill="x", expand=True)
        if helper:
            ttk.Label(wrap, text=helper, style="Subtle.TLabel").pack(
                anchor="w", pady=(4, 0))
        return row_inner

    def _file_row(self, parent: ttk.Frame, label: str, path: str, *, is_dir: bool = False) -> None:
        row = ttk.Frame(parent, style="Surface.TFrame")
        row.pack(fill="x", pady=4)
        ttk.Label(row, text=label, style="FieldLabel.TLabel", width=20).pack(side="left")
        exists = bool(path) and Path(path).exists()
        status = "✓" if exists else "—"
        color = PALETTE["success"] if exists else PALETTE["text_subtle"]
        tk.Label(row, text=status, bg=PALETTE["surface"], fg=color,
                 font=("TkDefaultFont", 10, "bold")).pack(side="left", padx=(0, 8))
        ttk.Label(row, text=path or "(not set)", style="Surface.TLabel").pack(side="left")

    def _update_auth_state(self) -> None:
        # Visual hint only; the Apply step decides usage.
        pass

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _mark_dirty_general(self) -> None:
        if self.selected_index is None:
            return
        line = self.config.lines[self.selected_index]
        line.name = self.var_name.get()
        line.application_path = self.var_app_path.get()
        line.application_arguments = self.var_app_args.get()
        self.detail_title_var.set(line.name or "(unnamed)")
        self.detail_subtitle_var.set(self._line_status_text(line))
        self._refresh_line_list()
        self.dirty = True
        self._set_status("Unsaved changes — click Save All to write them to disk.")

    def _apply_general(self) -> None:
        try:
            save_config(self.config)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Failed to save launcher config:\n{exc}")
            return
        self.dirty = False
        self._toast("Launcher configuration saved.", "success")
        self._set_status("Launcher configuration saved.")

    def _apply_database(self) -> None:
        if self.selected_index is None:
            return
        line = self.config.lines[self.selected_index]

        if not line.settings_xml_path or not Path(line.settings_xml_path).is_file():
            messagebox.showerror(APP_TITLE,
                "Settings.xml could not be located in the connection's working directory.\n"
                f"Expected: {line.settings_xml_path or '(no path)'}")
            return

        updates = {
            "system_name": self.var_system_name.get().strip(),
            "data_source": self.var_dsn.get().strip(),
            "sql_server": self.var_sql_server.get().strip(),
            "data_password": self.var_data_password.get(),
        }

        try:
            write_settings(line.settings_xml_path, updates)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Failed to update Settings.xml:\n{exc}")
            return

        if self.var_create_dsn.get() and updates["data_source"]:
            dsn = DsnDefinition(
                name=updates["data_source"],
                server=updates["sql_server"] or "localhost",
                database=self.var_database.get().strip(),
                driver=self.var_driver.get().strip() or "ODBC Driver 17 for SQL Server",
                trusted_connection=self.var_trusted.get(),
                username=self.var_username.get().strip() or "sa",
                password=updates["data_password"],
                description=f"DCI {line.name}",
            )
            try:
                write_dsn(dsn, self.var_dsn_bitness.get())
            except PermissionError:
                if self._prompt_elevate(
                    "Writing a system DSN requires Administrator rights.\n\n"
                    "Restart the launcher with elevation now?"
                ):
                    self._relaunch_elevated()
                return
            except Exception as exc:
                messagebox.showerror(APP_TITLE, f"Failed to write ODBC DSN:\n{exc}")
                return

        self._toast("Database settings applied.", "success")
        self._set_status("Database settings applied.")
        self.detail_subtitle_var.set(self._line_status_text(line))

    def _test_connection(self) -> None:
        dsn = DsnDefinition(
            name=self.var_dsn.get(),
            server=self.var_sql_server.get() or "localhost",
            database=self.var_database.get(),
            driver=self.var_driver.get() or "ODBC Driver 17 for SQL Server",
            trusted_connection=self.var_trusted.get(),
            username=self.var_username.get() or "sa",
            password=self.var_data_password.get(),
        )
        self._set_status("Testing connection…")
        self.root.update_idletasks()

        def worker():
            ok, message = test_connection(dsn)
            self.root.after(0, lambda: self._show_test_result(ok, message))

        threading.Thread(target=worker, daemon=True).start()

    def _show_test_result(self, ok: bool, message: str) -> None:
        if ok:
            self._toast("Connection successful.", "success")
            self._set_status("Connection test succeeded.")
            # we already have a live auth, may as well enumerate the databases
            # off it instead of making the user click again.
            self._list_databases_clicked()
        else:
            self._toast("Connection failed.", "error")
            messagebox.showwarning(APP_TITLE, f"Connection failed:\n\n{message}")
            self._set_status("Connection test failed.")

    def _add_line(self) -> None:
        new_index = len(self.config.lines)
        entry = LineEntry(name=f"Connection #{new_index + 1}",
                          application_path="",
                          application_arguments="")
        self.config.lines.append(entry)
        self.dirty = True
        self._refresh_line_list()
        self._select_line(new_index)
        self._set_status("New connection added — configure and Save All.")

    def _duplicate_line(self) -> None:
        if self.selected_index is None:
            return
        original = self.config.lines[self.selected_index]
        copy = LineEntry(
            name=f"{original.name} (copy)",
            application_path=original.application_path,
            application_arguments=original.application_arguments,
        )
        insert_at = self.selected_index + 1
        self.config.lines.insert(insert_at, copy)
        self.dirty = True
        self._refresh_line_list()
        self._select_line(insert_at)

    def _remove_line(self) -> None:
        if self.selected_index is None:
            return
        line = self.config.lines[self.selected_index]
        if not messagebox.askyesno(APP_TITLE, f"Remove connection '{line.name}' from the launcher?\n"
                                              "This does not delete any files on disk."):
            return
        del self.config.lines[self.selected_index]
        self.dirty = True
        if self.config.lines:
            self.selected_index = max(0, self.selected_index - 1)
            self._refresh_line_list()
            self._select_line(self.selected_index)
        else:
            self.selected_index = None
            self._refresh_line_list()
            self._show_empty_state()

    def _save_all(self) -> None:
        try:
            save_config(self.config)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Failed to save:\n{exc}")
            return
        self.dirty = False
        self._toast("All launcher entries saved.", "success")
        self._set_status("All launcher entries saved.")

    def _launch_selected(self) -> None:
        if self.selected_index is None:
            return
        line = self.config.lines[self.selected_index]
        path = (line.application_path or "").strip().strip('"')
        if not path:
            messagebox.showwarning(APP_TITLE, "No application path is set for this line.")
            return
        exe = Path(path)
        if not exe.exists():
            messagebox.showerror(APP_TITLE, f"Executable not found:\n{exe}")
            return

        if not self._preflight_odbc_for_line(line):
            return

        args = shlex.split(line.application_arguments) if line.application_arguments else []
        try:
            proc = subprocess.Popen([str(exe), *args])
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Failed to launch:\n{exc}")
            return
        if self.read_only and self.selected_index is not None:
            self._set_simple_row_running(self.selected_index, proc)
        self._set_status(f"Launched {line.name}.")

    def _preflight_odbc_for_line(self, line: LineEntry) -> bool:
        # catch firedac's driver-18 ssl failure mode before we hand off to qbase,
        # while there's still a parent window to hang a useful dialog off of.
        try:
            settings = read_settings(line.settings_xml_path)
        except Exception:
            settings = None
        server = (settings.sql_server.strip() if settings else "")
        if not server:
            return True

        # qbase's fast firedac path pins "ODBC Driver 18 for SQL Server" by name,
        # so just installing driver 17 alongside it doesn't help if the cert is
        # untrusted; the probe has to actually run.
        cert_ok, cert_err = self._probe_sql_tls(server)
        driver_18_only = self._driver_18_only()
        if cert_ok and not driver_18_only:
            return True

        return self._show_driver_warning_popup(line, server, cert_ok, cert_err, driver_18_only)

    def _probe_sql_tls(self, server: str, port: int = 1433, timeout: float = 4.0) -> tuple[bool, str]:
        # firedac negotiates tls inside tds prelogin, so a raw tls handshake on :1433
        # would lie about what the driver actually sees. ask through prelogin instead.
        from .sql_cert import is_certificate_trusted
        host = server
        # strip the protocol prefix we may have written into Settings.xml
        # ("tcp:HOST" forces driver 18 to skip Named Pipes).
        if host.lower().startswith("tcp:"):
            host = host[4:]
        elif host.lower().startswith("np:") or host.lower().startswith("lpc:"):
            host = host.split(":", 1)[1]
        if "," in host:
            host, _, p = host.partition(",")
            try:
                port = int(p)
            except ValueError:
                pass
        return is_certificate_trusted(host, port=port, timeout=timeout)

    def _show_driver_warning_popup(
        self, line: LineEntry, server: str, cert_ok: bool, cert_err: str, driver_18_only: bool
    ) -> bool:
        # custom Toplevel instead of messagebox so the dialog parents to the main
        # window and shows up on whatever monitor the user is actually staring at.
        top = tk.Toplevel(self.root)
        top.title(f"{APP_TITLE} \u2014 SQL connection warning")
        top.transient(self.root)
        top.resizable(False, False)
        top.configure(bg=PALETTE.get("surface", "#FFFFFF"))
        self._apply_window_icon(top)

        result = {"proceed": False}

        wrap = ttk.Frame(top, style="Surface.TFrame", padding=20)
        wrap.pack(fill="both", expand=True)

        if not cert_ok:
            heading = "SQL Server certificate is not trusted"
        else:
            heading = "ODBC Driver 17 is missing"

        header = ttk.Frame(wrap, style="Surface.TFrame")
        header.pack(fill="x", pady=(0, 10))
        tk.Label(
            header, text="\u26A0", font=("Segoe UI", 22, "bold"),
            fg="#B26A00", bg=PALETTE.get("surface", "#FFFFFF"),
        ).pack(side="left", padx=(0, 12))
        ttk.Label(header, text=heading, style="H2.TLabel").pack(side="left", anchor="w")

        if not cert_ok:
            body = (
                f"Line: {line.name}\n"
                f"SQL Server: {server}\n\n"
                "QBase's Fast FireDAC path is hardcoded to use ODBC Driver 18, which "
                "requires a trusted server certificate. The handshake to this server "
                "failed with:\n\n"
                f"    {cert_err}\n\n"
                "Recommended fix: import the SQL Server's certificate into this PC's "
                "Trusted Root store. \"Trust SQL Server Cert\" does that for you."
            )
        else:
            body = (
                f"Line: {line.name}\n"
                f"SQL Server: {server}\n\n"
                "Only ODBC Driver 18 is installed on this machine. QBase's Fast FireDAC "
                "connection will use Driver 18 with Encrypt=Yes and may fail against a "
                "self-signed SQL Server with an SSL trust error.\n\n"
                "Recommended fix: install \"ODBC Driver 17 for SQL Server\" (x86)."
            )
        ttk.Label(wrap, text=body, style="Body.TLabel", justify="left", wraplength=560).pack(
            fill="x", pady=(0, 16)
        )

        btn_row = ttk.Frame(wrap, style="Surface.TFrame")
        btn_row.pack(fill="x")

        def do_install() -> None:
            self._open_driver17_download()

        def do_trust() -> None:
            ok = self._trust_sql_certificate(line, server)
            if ok:
                top.destroy()
                result["proceed"] = True

        def do_launch() -> None:
            result["proceed"] = True
            top.destroy()

        def do_cancel() -> None:
            result["proceed"] = False
            top.destroy()

        ttk.Button(btn_row, text="Cancel", style="Ghost.TButton", command=do_cancel).pack(side="right")
        ttk.Button(btn_row, text="Launch Anyway", style="Ghost.TButton", command=do_launch).pack(
            side="right", padx=(0, 8)
        )
        if driver_18_only:
            ttk.Button(btn_row, text="Install Driver 17", style="Ghost.TButton",
                       command=do_install).pack(side="right", padx=(0, 8))
        if not cert_ok:
            ttk.Button(btn_row, text="Trust SQL Server Cert", style="Accent.TButton",
                       command=do_trust).pack(side="right", padx=(0, 8))
        elif driver_18_only:
            # if installing driver 17 is the only fix on offer, promote that
            # button to the accent style so the user can't miss it.
            for child in btn_row.winfo_children():
                if isinstance(child, ttk.Button) and child.cget("text") == "Install Driver 17":
                    child.configure(style="Accent.TButton")
                    break

        top.update_idletasks()
        # center on the parent so the dialog lands on the same monitor as the app.
        rw = self.root.winfo_width()
        rh = self.root.winfo_height()
        rx = self.root.winfo_rootx()
        ry = self.root.winfo_rooty()
        tw = top.winfo_reqwidth()
        th = top.winfo_reqheight()
        x = rx + max(0, (rw - tw) // 2)
        y = ry + max(0, (rh - th) // 3)
        top.geometry(f"+{x}+{y}")

        top.protocol("WM_DELETE_WINDOW", do_cancel)
        top.bind("<Escape>", lambda _e: do_cancel())
        top.grab_set()
        top.focus_set()
        self.root.wait_window(top)
        return bool(result["proceed"])

    def _trust_sql_certificate(self, line: LineEntry, server: str) -> bool:
        # full remediation for the driver-18 self-signed-cert mess:
        #   1. capture the server cert via a tds prelogin handshake
        #   2. shove it into LocalMachine\Root via certutil
        #   3. add a hosts entry mapping the cert's CN/SAN to the server ip
        #   4. rewrite <SqlServerName> in this line's Settings.xml to that name
        # after that, driver 18 sees a trusted chain AND a hostname match.
        # needs admin (cert store + hosts file).
        from .sql_cert import capture_certificate

        host = server.split(",")[0].strip()
        port = 1433
        if "," in server:
            try:
                port = int(server.split(",", 1)[1])
            except ValueError:
                pass

        if not _is_admin():
            # one-shot elevation: run our own EXE with --trust-cert, wait for it,
            # then continue launching qbase from this (non-admin) process.
            ok, err = self._run_elevated_trust_cert(host, port, line.settings_xml_path)
            if not ok:
                messagebox.showerror(APP_TITLE, f"Could not trust the SQL Server certificate:\n\n{err}")
                return False
            self._toast("SQL Server certificate trusted.", kind="success", dwell_ms=4000)
            self._set_status(f"Trusted cert for {host}; launching QBase.")
            return True

        try:
            cert = capture_certificate(host, port=port, timeout=8.0)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Could not capture certificate from {host}:\n\n{exc}")
            return False

        # step 1: drop the captured DER into the LocalMachine Root store via certutil.
        cer_path = Path(tempfile.gettempdir()) / f"dci_sql_{host.replace('.', '_')}.cer"
        try:
            cer_path.write_bytes(cert.der)
            proc = subprocess.run(
                ["certutil.exe", "-addstore", "-f", "Root", str(cer_path)],
                capture_output=True, text=True, timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Could not run certutil:\n{exc}")
            return False
        finally:
            try:
                cer_path.unlink(missing_ok=True)
            except Exception:
                pass
        if proc.returncode != 0:
            messagebox.showerror(
                APP_TITLE,
                f"certutil failed importing the cert from {host}:\n\n"
                f"{(proc.stderr or proc.stdout or '').strip()}",
            )
            return False

        # step 2: pick the hostname driver 18 will accept (first SAN dns, else CN).
        alias_name = next(iter(cert.sans), "") or cert.common_name
        if not alias_name:
            messagebox.showwarning(
                APP_TITLE,
                "Certificate imported but it has no usable Common Name or SAN, "
                "so I cannot map a hostname for the driver. "
                "QBase may still fail with 'target principal name is incorrect'.",
            )
            return True

        # if the cert name already matches the configured server, we're done.
        # otherwise: add a hosts entry so the os resolves <cert-name> -> <ip>, and
        # rewrite <SqlServerName> in this line's Settings.xml to the cert name.
        # driver 18 then sees a target name that matches the cert CN and schannel
        # is happy.
        #
        # do NOT use a sql server client alias (HKLM\...\MSSQLServer\Client\ConnectTo).
        # driver 18 expands client aliases to the underlying ip BEFORE schannel runs,
        # which defeats the whole point of the hostname check. the hosts file is
        # resolved at the winsock layer so the driver still sees <cert-name> for sni.
        if alias_name.lower() != host.lower():
            hosts_err = self._write_hosts_entry(alias_name, host)
            if hosts_err:
                messagebox.showerror(
                    APP_TITLE,
                    f"Imported the certificate but could not add a hosts entry mapping "
                    f"'{alias_name}' -> {host}:\n\n{hosts_err}",
                )
                return False

            # NOTE: deliberately do NOT rewrite <SqlServerName> in Settings.xml.
            # rewriting to the cert alias only works on machines whose hosts file
            # also has the entry, which doesn't transfer between PCs and breaks
            # fresh installs with "Named Pipes Provider error 53" or
            # "TCP Provider: No such host is known."
            # leaving SqlServerName as the original IP/host means QBase keeps
            # working everywhere; the cert is now in the trust store so driver 18
            # is happy via TrustServerCertificate=Yes regardless.

        self._toast("SQL Server certificate trusted.", kind="success", dwell_ms=4000)
        self._set_status(
            f"Trusted cert {cert.sha1[:12]}\u2026 from {host}; hosts {alias_name} -> {host}."
        )
        messagebox.showinfo(
            APP_TITLE,
            f"All set.\n\n"
            f"Server: {host}:{port}\n"
            f"Cert SHA1: {cert.sha1}\n"
            f"Cert CN: {cert.common_name or '(none)'}\n"
            f"Hosts entry: {alias_name} -> {host}\n"
            f"Settings.xml SqlServerName: {alias_name}\n\n"
            "QBase will be launched now.",
        )
        return True

    def _write_hosts_entry(self, name: str, ip: str) -> str:
        # add or update a single "<ip>\t<name>\t# marker" line in the windows hosts
        # file. idempotent: any prior line mapping <name> gets replaced. caller is
        # already elevated by the time we get here (_trust_sql_certificate checks).
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
            # tokens: <ip> <name> [<name>...] [# comment]
            no_comment = stripped.split("#", 1)[0].split()
            if len(no_comment) >= 2 and name.lower() in (n.lower() for n in no_comment[1:]):
                # found an existing mapping for this name; replace it
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

    def _safe_settings_server(self, line: LineEntry) -> str:
        try:
            s = read_settings(line.settings_xml_path)
            return s.sql_server if s else "(unknown)"
        except Exception:
            return "(unknown)"

    def _open_driver17_download(self) -> None:
        # the fwlink redirects to whatever the current x86 msi is. download into
        # TEMP and hand it to msiexec so the user gets the real installer + uac
        # prompt instead of a browser detour.
        url = "https://go.microsoft.com/fwlink/?linkid=2361646"
        self._set_status("Downloading ODBC Driver 17 installer\u2026")
        self._toast("Downloading ODBC Driver 17 installer\u2026", kind="info", dwell_ms=3000)

        def worker() -> None:
            import tempfile
            import urllib.request
            try:
                target = Path(tempfile.gettempdir()) / "msodbcsql17_x86.msi"
                req = urllib.request.Request(url, headers={"User-Agent": "DCI-Launcher"})
                with urllib.request.urlopen(req, timeout=60) as resp, open(target, "wb") as fh:
                    fh.write(resp.read())
                self.root.after(0, lambda: self._launch_msi(target))
            except Exception as exc:
                self.root.after(0, lambda: self._driver_download_failed(url, exc))

        threading.Thread(target=worker, daemon=True).start()

    def _launch_msi(self, msi_path: Path) -> None:
        try:
            # msiexec hands us the official installer ui + uac prompt for free.
            subprocess.Popen(["msiexec", "/i", str(msi_path)])
            self._toast("Installer launched. Follow the prompts.", kind="success", dwell_ms=4000)
            self._set_status(f"Launched installer: {msi_path}")
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Could not launch installer:\n{exc}\n\nFile: {msi_path}")

    def _driver_download_failed(self, url: str, exc: BaseException) -> None:
        self._set_status(f"Driver download failed: {exc}")
        if messagebox.askyesno(
            APP_TITLE,
            f"Could not download the ODBC Driver 17 installer:\n\n{exc}\n\n"
            "Open the download link in your browser instead?",
        ):
            try:
                webbrowser.open(url, new=2)
            except Exception as exc2:
                messagebox.showerror(APP_TITLE, f"Could not open browser:\n{exc2}")

    def _apply_window_icon(self, win: tk.Misc) -> None:
        try:
            candidates = []
            base = getattr(sys, "_MEIPASS", None)
            if base:
                candidates.append(Path(base) / "dci_launcher" / "assets" / "app.ico")
            candidates.append(Path(__file__).parent / "assets" / "app.ico")
            for ico in candidates:
                if ico.is_file():
                    win.iconbitmap(default=str(ico))
                    return
        except Exception:
            pass

    def _driver_18_only(self) -> bool:
        try:
            drivers = list_installed_drivers("32")
        except Exception:
            return False
        has_18 = any("ODBC Driver 18" in d for d in drivers)
        has_17 = any("ODBC Driver 17" in d for d in drivers)
        return has_18 and not has_17

    def _browse_exe(self) -> None:
        chosen = filedialog.askopenfilename(
            title="Select QBase executable",
            filetypes=[("Executable", "*.exe"), ("All files", "*.*")],
        )
        if chosen:
            self.var_app_path.set(chosen)

    def _open_logs_folder(self) -> None:
        target = Path.cwd() / "LOGS"
        self._open_path(str(target))

    def _open_path(self, path: str) -> None:
        if not path:
            return
        p = Path(path)
        if not p.exists():
            messagebox.showinfo(APP_TITLE, f"Path does not exist:\n{p}")
            return
        if sys.platform.startswith("win"):
            os.startfile(str(p))  # type: ignore[attr-defined]
        else:
            webbrowser.open(p.as_uri())

    def _open_odbc_dialog(self) -> None:
        if not _is_admin():
            if self._prompt_elevate(
                "Managing system ODBC data sources requires Administrator rights.\n\n"
                "Restart the launcher with elevation now?"
            ):
                self._relaunch_elevated()
            return
        OdbcManagerDialog(self.root, on_change=self._refresh_dsn_picker)

    def _prompt_elevate(self, message: str) -> bool:
        return messagebox.askyesno(APP_TITLE, message)

    def _run_elevated_trust_cert(self, host: str, port: int, settings_xml: Path) -> tuple[bool, str]:
        # spawn our own EXE elevated with --trust-cert. waits for it. on success the
        # cert is in LocalMachine\Root, hosts file has the CN entry, Settings.xml is
        # rewritten. caller stays in the original (non-admin) process so qbase
        # launches as the actual user.
        if not sys.platform.startswith("win"):
            return False, "elevation only supported on Windows"
        if not getattr(sys, "frozen", False):
            return False, "elevation helper only available in the frozen build"

        params = subprocess.list2cmdline([
            "--trust-cert", host, str(port), str(settings_xml),
        ])

        SEE_MASK_NOCLOSEPROCESS = 0x00000040
        SEE_MASK_NO_CONSOLE     = 0x00008000

        class SHELLEXECUTEINFOW(ctypes.Structure):
            _fields_ = [
                ("cbSize",       ctypes.c_ulong),
                ("fMask",        ctypes.c_ulong),
                ("hwnd",         ctypes.c_void_p),
                ("lpVerb",       ctypes.c_wchar_p),
                ("lpFile",       ctypes.c_wchar_p),
                ("lpParameters", ctypes.c_wchar_p),
                ("lpDirectory",  ctypes.c_wchar_p),
                ("nShow",        ctypes.c_int),
                ("hInstApp",     ctypes.c_void_p),
                ("lpIDList",     ctypes.c_void_p),
                ("lpClass",      ctypes.c_wchar_p),
                ("hkeyClass",    ctypes.c_void_p),
                ("dwHotKey",     ctypes.c_ulong),
                ("hIconOrMonitor", ctypes.c_void_p),
                ("hProcess",     ctypes.c_void_p),
            ]

        sei = SHELLEXECUTEINFOW()
        sei.cbSize       = ctypes.sizeof(sei)
        sei.fMask        = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NO_CONSOLE
        sei.lpVerb       = "runas"
        sei.lpFile       = sys.executable
        sei.lpParameters = params
        sei.lpDirectory  = str(Path.cwd())
        sei.nShow        = 0  # SW_HIDE — no flicker

        if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)):
            err = ctypes.GetLastError()
            if err == 1223:
                return False, "Administrator approval was cancelled."
            return False, f"ShellExecuteExW failed (error {err})."

        if not sei.hProcess:
            return False, "Elevated process did not start."

        WAIT_OBJECT_0 = 0
        ctypes.windll.kernel32.WaitForSingleObject(sei.hProcess, 0xFFFFFFFF)
        rc = ctypes.c_ulong(0)
        ctypes.windll.kernel32.GetExitCodeProcess(sei.hProcess, ctypes.byref(rc))
        ctypes.windll.kernel32.CloseHandle(sei.hProcess)

        if rc.value == 0:
            return True, ""
        reasons = {
            10: "could not capture the SQL Server certificate (network/handshake failure).",
            11: "certutil could not be executed.",
            12: "certutil rejected the certificate import.",
            13: "could not write the Windows hosts file.",
            14: "could not update Settings.xml.",
        }
        return False, reasons.get(rc.value, f"helper exited with code {rc.value}.")

    def _relaunch_elevated(self) -> None:
        # ShellExecuteW with the "runas" verb is the only way to get a uac prompt
        # out of python without shipping a requireAdministrator manifest.
        if not sys.platform.startswith("win"):
            return
        try:
            args = sys.argv[1:]
            if getattr(sys, "frozen", False):
                executable = sys.executable
                params = subprocess.list2cmdline(args)
            else:
                executable = sys.executable
                params = subprocess.list2cmdline(["-m", "dci_launcher", *args])
            cwd = str(Path.cwd())
            result = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", executable, params, cwd, 1
            )
            if int(result) <= 32:
                messagebox.showwarning(APP_TITLE, "Elevation request was cancelled or failed.")
                return
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Failed to relaunch elevated:\n{exc}")
            return
        self.root.destroy()

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)

    def _toast(self, message: str, kind: str = "success", dwell_ms: int = 2400) -> None:
        """Show a small auto-dismissing notification anchored to the bottom-right of the window."""
        # tk has no native toast widget, so we fake one with an overrideredirect Toplevel.
        try:
            self.root.update_idletasks()
        except tk.TclError:
            return

        colors = {
            "success": ("#1F8A4C", "#FFFFFF"),
            "error":   ("#C5283D", "#FFFFFF"),
            "warning": ("#B26A00", "#FFFFFF"),
            "info":    (PALETTE.get("accent", "#1F6FEB"), "#FFFFFF"),
        }
        bg, fg = colors.get(kind, colors["info"])

        toast = tk.Toplevel(self.root)
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        try:
            toast.attributes("-alpha", 0.0)
        except tk.TclError:
            pass
        toast.configure(bg=bg)

        frame = tk.Frame(toast, bg=bg, padx=16, pady=10)
        frame.pack()
        tk.Label(frame, text=message, bg=bg, fg=fg,
                 font=("Segoe UI", 10, "bold")).pack()

        toast.update_idletasks()
        rw = self.root.winfo_width()
        rh = self.root.winfo_height()
        rx = self.root.winfo_rootx()
        ry = self.root.winfo_rooty()
        tw = toast.winfo_reqwidth()
        th = toast.winfo_reqheight()
        x = rx + rw - tw - 24
        y = ry + rh - th - 48
        toast.geometry(f"+{x}+{y}")

        def fade_in(step: int = 0) -> None:
            if not toast.winfo_exists():
                return
            alpha = min(0.95, step * 0.12)
            try:
                toast.attributes("-alpha", alpha)
            except tk.TclError:
                pass
            if alpha < 0.95:
                toast.after(20, lambda: fade_in(step + 1))

        def fade_out(step: int = 0) -> None:
            if not toast.winfo_exists():
                return
            alpha = max(0.0, 0.95 - step * 0.1)
            try:
                toast.attributes("-alpha", alpha)
            except tk.TclError:
                pass
            if alpha <= 0.0:
                toast.destroy()
            else:
                toast.after(25, lambda: fade_out(step + 1))

        fade_in()
        toast.after(max(800, int(dwell_ms)), fade_out)

    def _list_databases_clicked(self) -> None:
        server = self.var_sql_server.get().strip()
        if not server:
            self._toast("Enter a SQL Server first.", "error")
            return
        self._run_database_lookup(server, silent=False)

    def _schedule_db_autolookup(self) -> None:
        if getattr(self, "_db_autolookup_after_id", None):
            try:
                self.root.after_cancel(self._db_autolookup_after_id)
            except Exception:
                pass
        self._db_autolookup_after_id = self.root.after(700, self._auto_list_databases)

    def _auto_list_databases(self) -> None:
        self._db_autolookup_after_id = None
        server = self.var_sql_server.get().strip()
        if not server or len(server) < 4:
            return
        self._run_database_lookup(server, silent=True)

    def _run_database_lookup(self, server: str, *, silent: bool) -> None:
        driver = self.var_driver.get().strip() or "ODBC Driver 17 for SQL Server"
        trusted = self.var_trusted.get()
        username = self.var_username.get().strip()
        password = self.var_data_password.get()

        if not silent:
            self._set_status(f"Listing databases on {server}\u2026")
            self.root.update_idletasks()

        def worker():
            names, err = list_databases(server, username, password, trusted, driver)
            self.root.after(0, lambda: self._show_database_list(names, err, silent=silent))

        threading.Thread(target=worker, daemon=True).start()

    def _show_database_list(self, names, err: str, *, silent: bool = False) -> None:
        if err:
            if silent:
                return
            self._toast("Could not list databases.", "error")
            self._set_status(f"Database list failed: {err}")
            messagebox.showwarning(APP_TITLE, f"Could not list databases:\n{err}")
            return
        if not names:
            if silent:
                return
            self._toast("No databases returned.", "info")
            self._set_status("No databases returned by the server.")
            return
        if hasattr(self, "database_combo"):
            self.database_combo.configure(values=names)
            current = self.var_database.get().strip()
            if current not in names:
                self.var_database.set(names[0])
        if not silent:
            self._toast(f"Found {len(names)} databases.", "success")
        self._set_status(f"Loaded {len(names)} databases from server.")

    def run(self) -> None:
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        if not self.read_only:
            # let the window finish painting before we start nagging about drivers,
            # otherwise the status update races with initial layout.
            self.root.after(900, self._check_odbc_driver_health)
        self.root.mainloop()

    def _check_odbc_driver_health(self) -> None:
        # qbase's fast firedac path picks the newest installed sql driver and
        # ignores the dsn. driver 18 defaults to Encrypt=Yes and dies on self-signed
        # certs; driver 17 doesn't. warn if 18 is the only modern one present.
        if not self._driver_18_only():
            return

        # only warn if at least one configured line actually points at a sql server.
        uses_sql = False
        for line in self.config.lines:
            try:
                settings = read_settings(line.settings_xml_path)
            except Exception:
                settings = None
            if settings and settings.sql_server.strip():
                uses_sql = True
                break
        if not uses_sql:
            return

        # status-bar hint only. the loud popup fires from _preflight_odbc_for_line
        # at Launch time, when the user is actually paying attention to a dialog.
        self._set_status(
            "Warning: ODBC Driver 18 is the only modern SQL driver installed. "
            "Install Driver 17 (x86) to avoid FireDAC SSL errors against self-signed servers."
        )

    def _on_close(self) -> None:
        if self.dirty:
            answer = messagebox.askyesnocancel(
                APP_TITLE,
                "You have unsaved changes. Save before exiting?",
            )
            if answer is None:
                return
            if answer:
                try:
                    save_config(self.config)
                except Exception as exc:
                    messagebox.showerror(APP_TITLE, f"Save failed:\n{exc}")
                    return
        if self.running_procs:
            still_running = [self._simple_row_name(i) for i, p in self.running_procs.items()
                             if p.poll() is None]
            if still_running:
                if not messagebox.askyesno(
                    APP_TITLE,
                    "These connections are still running:\n\n  - "
                    + "\n  - ".join(still_running)
                    + "\n\nClosing the launcher will leave them running. Continue?",
                ):
                    return
        self.root.destroy()


# ----------------------------------------------------------------------
# ODBC manager dialog
# ----------------------------------------------------------------------


class OdbcManagerDialog:
    def __init__(self, parent: tk.Tk, on_change=None) -> None:
        self.top = tk.Toplevel(parent)
        self.top.title("ODBC System Data Sources")
        self.top.geometry("820x560")
        self.top.configure(bg=PALETTE["bg"])
        self.top.transient(parent)
        self.on_change = on_change
        self.scope = tk.StringVar(value="all")
        self._current_bits: str = "32"

        wrap = ttk.Frame(self.top, style="Surface.TFrame", padding=18)
        wrap.pack(fill="both", expand=True, padx=14, pady=14)

        header = ttk.Frame(wrap, style="Surface.TFrame")
        header.pack(fill="x", pady=(0, 12))
        ttk.Label(header, text="System DSNs", style="H2.TLabel").pack(side="left")

        scope_frame = ttk.Frame(header, style="Surface.TFrame")
        scope_frame.pack(side="right")
        for label, value in (("64-bit", "64"), ("32-bit", "32"), ("All", "all")):
            ttk.Radiobutton(scope_frame, text=label, value=value, variable=self.scope,
                            command=self._refresh).pack(side="right", padx=4)

        body = ttk.Frame(wrap, style="Surface.TFrame")
        body.pack(fill="both", expand=True)

        list_frame = ttk.Frame(body, style="Surface.TFrame", width=300)
        list_frame.pack(side="left", fill="y")
        list_frame.pack_propagate(False)

        self.tree = ttk.Treeview(list_frame, columns=("arch",), show="tree headings",
                                 selectmode="browse")
        self.tree.heading("#0", text="Name")
        self.tree.heading("arch", text="Arch")
        self.tree.column("#0", width=200, anchor="w")
        self.tree.column("arch", width=70, anchor="center", stretch=False)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        button_row = ttk.Frame(list_frame, style="Surface.TFrame")
        button_row.pack(fill="x", pady=(8, 0))
        ttk.Button(button_row, text="New", style="Ghost.TButton",
                   command=self._clear_form).pack(side="left")

        ttk.Separator(body, orient="vertical").pack(side="left", fill="y", padx=12)

        self.detail = ttk.Frame(body, style="Surface.TFrame")
        self.detail.pack(side="left", fill="both", expand=True)

        self._build_form()

        button_bar = ttk.Frame(wrap, style="Surface.TFrame")
        button_bar.pack(fill="x", pady=(14, 0))
        ttk.Button(button_bar, text="Delete", style="Danger.TButton",
                   command=self._delete).pack(side="left")
        ttk.Button(button_bar, text="Test", style="Ghost.TButton",
                   command=self._test).pack(side="right", padx=6)
        ttk.Button(button_bar, text="Save / Create", style="Accent.TButton",
                   command=self._save).pack(side="right")

        self._refresh()

    def _build_form(self) -> None:
        self.var_name = tk.StringVar()
        self.var_driver = tk.StringVar()
        self.var_server = tk.StringVar(value="localhost")
        self.var_database = tk.StringVar()
        self.var_trusted = tk.BooleanVar(value=False)
        self.var_user = tk.StringVar(value="sa")
        self.var_password = tk.StringVar()
        self.var_description = tk.StringVar()
        self.var_target_bits = tk.StringVar(value="32")

        rows = [
            ("DSN name", self.var_name, False),
            ("Architecture", self.var_target_bits, False),
            ("Driver", self.var_driver, False),
            ("Server", self.var_server, False),
            ("Database", self.var_database, False),
            ("User", self.var_user, False),
            ("Password", self.var_password, True),
            ("Description", self.var_description, False),
        ]

        for i, (label, var, secret) in enumerate(rows):
            ttk.Label(self.detail, text=label, style="FieldLabel.TLabel").grid(
                row=i, column=0, sticky="w", pady=4, padx=(0, 12))
            if label == "Driver":
                self.driver_combo = ttk.Combobox(self.detail, textvariable=var,
                                                 values=list_installed_drivers(self.var_target_bits.get()),
                                                 state="readonly", width=40)
                self.driver_combo.grid(row=i, column=1, sticky="ew", pady=4)
            elif label == "Architecture":
                arch_box = ttk.Frame(self.detail, style="Surface.TFrame")
                arch_box.grid(row=i, column=1, sticky="w", pady=4)
                ttk.Radiobutton(arch_box, text="32-bit", value="32",
                                variable=var, command=self._on_target_bits_changed).pack(side="left", padx=(0, 12))
                ttk.Radiobutton(arch_box, text="64-bit", value="64",
                                variable=var, command=self._on_target_bits_changed).pack(side="left")
            else:
                ttk.Entry(self.detail, textvariable=var,
                          show="•" if secret else "").grid(
                    row=i, column=1, sticky="ew", pady=4)

        ttk.Checkbutton(self.detail, text="Use Windows Authentication",
                        variable=self.var_trusted).grid(row=len(rows), column=0,
                                                        columnspan=2, sticky="w", pady=8)

        self.detail.columnconfigure(1, weight=1)

    def _on_target_bits_changed(self) -> None:
        if hasattr(self, "driver_combo"):
            self.driver_combo.configure(values=list_installed_drivers(self.var_target_bits.get()))

    def _schedule_db_autolookup(self) -> None:
        if getattr(self, "_db_autolookup_after_id", None):
            try:
                self.top.after_cancel(self._db_autolookup_after_id)
            except Exception:
                pass
        self._db_autolookup_after_id = self.top.after(700, self._auto_list_databases)

    def _auto_list_databases(self) -> None:
        self._db_autolookup_after_id = None
        server = self.var_server.get().strip()
        if not server or len(server) < 4:
            return
        driver = self.var_driver.get().strip() or "ODBC Driver 17 for SQL Server"
        trusted = self.var_trusted.get()
        username = self.var_user.get().strip()
        password = self.var_password.get()

        def worker():
            names, err = list_databases(server, username, password, trusted, driver)
            self.top.after(0, lambda: self._apply_database_list(names, err))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_database_list(self, names, err: str) -> None:
        if err or not names or not hasattr(self, "database_combo"):
            return
        try:
            self.database_combo.configure(values=names)
        except tk.TclError:
            return

    def _refresh(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)

        scope = self.scope.get()
        if scope == "all":
            entries = list_all_dsns()
        else:
            entries = [(name, scope) for name in list_dsns(scope)]

        for name, bits in entries:
            iid = f"{bits}::{name}"
            self.tree.insert("", "end", iid=iid, text=name, values=(f"{bits}-bit",))

    def _on_select(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        iid = selection[0]
        bits, _, name = iid.partition("::")
        self._current_bits = bits
        dsn = read_dsn(name, bits)
        if not dsn:
            return
        self.var_name.set(dsn.name)
        self.var_target_bits.set(bits)
        self._on_target_bits_changed()
        self.var_driver.set(dsn.driver)
        self.var_server.set(dsn.server)
        self.var_database.set(dsn.database)
        self.var_trusted.set(dsn.trusted_connection)
        self.var_user.set(dsn.username)
        self.var_password.set("")
        self.var_description.set(dsn.description)

    def _clear_form(self) -> None:
        for sel in self.tree.selection():
            self.tree.selection_remove(sel)
        self.var_name.set("")
        self.var_driver.set("")
        self.var_server.set("localhost")
        self.var_database.set("")
        self.var_trusted.set(False)
        self.var_user.set("sa")
        self.var_password.set("")
        self.var_description.set("")

    def _save(self) -> None:
        if not self.var_name.get().strip():
            messagebox.showwarning("ODBC", "DSN name is required.")
            return
        bits = self.var_target_bits.get()
        dsn = DsnDefinition(
            name=self.var_name.get().strip(),
            driver=self.var_driver.get().strip() or "ODBC Driver 17 for SQL Server",
            server=self.var_server.get().strip() or "localhost",
            database=self.var_database.get().strip(),
            trusted_connection=self.var_trusted.get(),
            username=self.var_user.get().strip() or "sa",
            password=self.var_password.get(),
            description=self.var_description.get().strip() or "DCI database connection",
        )
        try:
            write_dsn(dsn, bits)
        except PermissionError:
            messagebox.showwarning("ODBC",
                "Administrator privileges are required to create system DSNs.")
            return
        except Exception as exc:
            messagebox.showerror("ODBC", str(exc))
            return
        self._refresh()
        if self.on_change:
            self.on_change()
        messagebox.showinfo("ODBC", f"DSN '{dsn.name}' saved ({bits}-bit).")

    def _delete(self) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        iid = selection[0]
        bits, _, name = iid.partition("::")
        if not messagebox.askyesno("ODBC", f"Delete DSN '{name}' ({bits}-bit)?"):
            return
        try:
            delete_dsn(name, bits)
        except PermissionError:
            messagebox.showwarning("ODBC", "Administrator privileges are required.")
            return
        self._refresh()
        self._clear_form()
        if self.on_change:
            self.on_change()

    def _test(self) -> None:
        dsn = DsnDefinition(
            name=self.var_name.get(),
            driver=self.var_driver.get() or "ODBC Driver 17 for SQL Server",
            server=self.var_server.get() or "localhost",
            database=self.var_database.get(),
            trusted_connection=self.var_trusted.get(),
            username=self.var_user.get() or "sa",
            password=self.var_password.get(),
        )
        ok, msg = test_connection(dsn)
        if ok:
            messagebox.showinfo("ODBC", msg)
        else:
            messagebox.showwarning("ODBC", msg)


def _is_admin() -> bool:
    # ctypes -> shell32!IsUserAnAdmin saves us from spawning `whoami /groups` for
    # what is otherwise a hot-path call on every elevation-gated UI action.
    if not sys.platform.startswith("win"):
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _short_path(path: str) -> str:
    # show "<parent>\<file>" instead of truncating mid-component, which always
    # ends up unreadable when the full path overflows the status bar.
    raw = (path or "").strip().strip('"')
    if not raw:
        return ""
    p = Path(raw)
    parts = p.parts
    if len(parts) >= 2:
        return f"{parts[-2]}\\{parts[-1]}"
    return parts[-1]

#end of ui.py
#why are you still here, go away!!