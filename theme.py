"""Light theme for the launcher — flat, modern, professional."""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk


PALETTE = {
    "bg":            "#F4F5F7",
    "surface":       "#FFFFFF",
    "surface_alt":   "#F9FAFB",
    "border":        "#E1E4E8",
    "border_strong": "#C5CAD1",
    "text":          "#1F2328",
    "text_muted":    "#57606A",
    "text_subtle":   "#6E7781",
    "accent":        "#1F6FEB",
    "accent_hover":  "#1A5FCC",
    "accent_active": "#155AB8",
    "accent_text":   "#FFFFFF",
    "success":       "#1A7F37",
    "warning":       "#9A6700",
    "danger":        "#CF222E",
    "row_hover":     "#F1F3F5",
    "row_selected":  "#DDEBFF",
}


def apply_theme(root: tk.Tk) -> ttk.Style:
    root.configure(bg=PALETTE["bg"])

    base_family = _pick_font(root, ["Segoe UI Variable Text", "Segoe UI", "Calibri", "Arial"])
    heading_family = _pick_font(root, ["Segoe UI Variable Display", "Segoe UI Semibold", "Segoe UI", "Calibri"])

    root.option_add("*Font", (base_family, 10))
    tkfont.nametofont("TkDefaultFont").configure(family=base_family, size=10)
    tkfont.nametofont("TkTextFont").configure(family=base_family, size=10)
    tkfont.nametofont("TkHeadingFont").configure(family=heading_family, size=11)
    tkfont.nametofont("TkMenuFont").configure(family=base_family, size=10)

    style = ttk.Style(root)
    style.theme_use("clam")

    style.configure(".", background=PALETTE["bg"], foreground=PALETTE["text"], borderwidth=0,
                    focuscolor=PALETTE["accent"])

    style.configure("App.TFrame", background=PALETTE["bg"])
    style.configure("Surface.TFrame", background=PALETTE["surface"])
    style.configure("Sidebar.TFrame", background=PALETTE["surface"])
    style.configure("Card.TFrame", background=PALETTE["surface"], relief="flat")
    style.configure("Toolbar.TFrame", background=PALETTE["surface"])
    style.configure("Statusbar.TFrame", background=PALETTE["surface_alt"])

    style.configure("TLabel", background=PALETTE["bg"], foreground=PALETTE["text"])
    style.configure("Surface.TLabel", background=PALETTE["surface"], foreground=PALETTE["text"])
    style.configure("Muted.TLabel", background=PALETTE["surface"], foreground=PALETTE["text_muted"], font=(base_family, 9))
    style.configure("Subtle.TLabel", background=PALETTE["surface"], foreground=PALETTE["text_subtle"], font=(base_family, 9))
    style.configure("H1.TLabel", background=PALETTE["surface"], foreground=PALETTE["text"], font=(heading_family, 18, "bold"))
    style.configure("H2.TLabel", background=PALETTE["surface"], foreground=PALETTE["text"], font=(heading_family, 13, "bold"))
    style.configure("H3.TLabel", background=PALETTE["surface"], foreground=PALETTE["text"], font=(heading_family, 11, "bold"))
    style.configure("FieldLabel.TLabel", background=PALETTE["surface"], foreground=PALETTE["text_muted"], font=(base_family, 9))
    style.configure("Status.TLabel", background=PALETTE["surface_alt"], foreground=PALETTE["text_muted"], font=(base_family, 9))

    style.configure("TButton", padding=(14, 7), background=PALETTE["surface"],
                    foreground=PALETTE["text"], borderwidth=1, relief="solid",
                    bordercolor=PALETTE["border_strong"])
    style.map("TButton",
              background=[("active", PALETTE["row_hover"]), ("pressed", PALETTE["border"])],
              bordercolor=[("active", PALETTE["border_strong"])])

    style.configure("Accent.TButton", padding=(16, 8), background=PALETTE["accent"],
                    foreground=PALETTE["accent_text"], borderwidth=0, relief="flat",
                    font=(base_family, 10, "bold"))
    style.map("Accent.TButton",
              background=[("active", PALETTE["accent_hover"]), ("pressed", PALETTE["accent_active"])])

    style.configure("Ghost.TButton", padding=(10, 6), background=PALETTE["surface"],
                    foreground=PALETTE["text_muted"], borderwidth=0, relief="flat")
    style.map("Ghost.TButton",
              background=[("active", PALETTE["row_hover"]), ("pressed", PALETTE["border"])],
              foreground=[("active", PALETTE["text"])])

    style.configure("Danger.TButton", padding=(12, 6), background=PALETTE["surface"],
                    foreground=PALETTE["danger"], borderwidth=1, relief="solid",
                    bordercolor=PALETTE["border_strong"])
    style.map("Danger.TButton",
              background=[("active", "#FBEAEC"), ("pressed", "#F4D3D7")])

    style.configure("Launch.TButton", padding=(20, 12), background=PALETTE["accent"],
                    foreground=PALETTE["accent_text"], borderwidth=0, relief="flat",
                    font=(heading_family, 12, "bold"))
    style.map("Launch.TButton",
              background=[("active", PALETTE["accent_hover"]), ("pressed", PALETTE["accent_active"])])

    style.configure("Stop.TButton", padding=(16, 8), background=PALETTE["danger"],
                    foreground=PALETTE["accent_text"], borderwidth=0, relief="flat",
                    font=(base_family, 10, "bold"))
    style.map("Stop.TButton",
              background=[("active", "#A0202F"), ("pressed", "#85182A")])

    style.configure("TEntry", fieldbackground=PALETTE["surface"], background=PALETTE["surface"],
                    foreground=PALETTE["text"], borderwidth=1, relief="solid",
                    bordercolor=PALETTE["border_strong"], padding=6, insertcolor=PALETTE["text"])
    style.map("TEntry",
              bordercolor=[("focus", PALETTE["accent"]), ("hover", PALETTE["border_strong"])],
              fieldbackground=[("disabled", PALETTE["surface_alt"])],
              foreground=[("disabled", PALETTE["text_subtle"])])

    style.configure("TCombobox", fieldbackground=PALETTE["surface"], background=PALETTE["surface"],
                    foreground=PALETTE["text"], borderwidth=1, relief="solid",
                    bordercolor=PALETTE["border_strong"], padding=5, arrowsize=14,
                    selectbackground=PALETTE["surface"], selectforeground=PALETTE["text"])
    style.map("TCombobox",
              bordercolor=[("focus", PALETTE["accent"])],
              fieldbackground=[("readonly", PALETTE["surface"])],
              foreground=[("readonly", PALETTE["text"])])
    root.option_add("*TCombobox*Listbox.background", PALETTE["surface"])
    root.option_add("*TCombobox*Listbox.foreground", PALETTE["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", PALETTE["row_selected"])
    root.option_add("*TCombobox*Listbox.selectForeground", PALETTE["text"])
    root.option_add("*TCombobox*Listbox.borderWidth", 0)

    style.configure("TCheckbutton", background=PALETTE["surface"], foreground=PALETTE["text"],
                    focuscolor=PALETTE["surface"])
    style.map("TCheckbutton", background=[("active", PALETTE["surface"])])

    style.configure("TNotebook", background=PALETTE["surface"], borderwidth=0, tabmargins=(0, 6, 0, 0))
    style.configure("TNotebook.Tab", padding=(16, 8), background=PALETTE["surface"],
                    foreground=PALETTE["text_muted"], borderwidth=0, font=(base_family, 10, "bold"))
    style.map("TNotebook.Tab",
              background=[("selected", PALETTE["surface"]), ("active", PALETTE["row_hover"])],
              foreground=[("selected", PALETTE["accent"]), ("active", PALETTE["text"])])

    style.configure("TSeparator", background=PALETTE["border"])

    style.configure("Treeview", background=PALETTE["surface"], fieldbackground=PALETTE["surface"],
                    foreground=PALETTE["text"], borderwidth=0, rowheight=28,
                    font=(base_family, 10))
    style.configure("Treeview.Heading", background=PALETTE["surface_alt"], foreground=PALETTE["text_muted"],
                    relief="flat", borderwidth=0, padding=(8, 6), font=(base_family, 9, "bold"))
    style.map("Treeview",
              background=[("selected", PALETTE["row_selected"])],
              foreground=[("selected", PALETTE["text"])])

    style.configure("Vertical.TScrollbar", background=PALETTE["surface"], troughcolor=PALETTE["surface"],
                    bordercolor=PALETTE["surface"], arrowcolor=PALETTE["text_muted"])
    style.configure("Horizontal.TScrollbar", background=PALETTE["surface"], troughcolor=PALETTE["surface"],
                    bordercolor=PALETTE["surface"], arrowcolor=PALETTE["text_muted"])

    return style


def _pick_font(root: tk.Tk, candidates: list[str]) -> str:
    available = set(tkfont.families(root))
    for name in candidates:
        if name in available:
            return name
    return candidates[-1]
