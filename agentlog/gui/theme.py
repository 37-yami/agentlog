"""Dark theme helpers for the agentlog GUI.

Keeps the original layout untouched - this only restyles widgets (dark ttk
theme) and provides the user-selectable background color (presets + custom,
persisted by the GUI itself).
"""
import tkinter as tk
from tkinter import ttk

# Palette (fixed dark widget colors; the window background is user-selectable)
CONTENT = "#191b26"   # default window background
CARD = "#20222e"      # bars / panels
CARD_IN = "#262938"   # tree fields, entries
ACCENT = "#7c6ff0"    # emphasis / selection
TEXT = "#e8eaf2"      # primary text
SUB = "#8b90a6"       # secondary text
DANGER = "#ff7043"    # warnings / destructive
FONT = ("Microsoft YaHei", 10)

# Preset background colors (dark-coordinated)
PRESET_COLORS = ["#12131b", "#0e1626", "#191226", "#0f1a17",
                 "#1d1712", "#141a24"]


def shade(hex_color, factor):
    """Darken (factor < 1) / lighten (factor > 1) a #rrggbb color."""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % (max(0, min(255, int(r * factor))),
                               max(0, min(255, int(g * factor))),
                               max(0, min(255, int(b * factor))))


def luminance(hex_color):
    """Relative luminance 0..1 (used to warn about too-bright backgrounds)."""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255


def apply_dark_theme(root, background=None):
    """Apply the dark ttk styles. `background` recolors the window surface
    (frames/labels); widget colors stay in the fixed dark palette so text
    contrast is guaranteed. Re-run any time to switch the background."""
    background = background or CONTENT
    if luminance(background) > 0.55:
        # keep text readable even if a bright custom color slips through
        background = shade(background, 0.5)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    root.configure(bg=background)
    style.configure(".", background=background, foreground=TEXT, font=FONT,
                    borderwidth=0, focuscolor=ACCENT)
    style.configure("TFrame", background=background)
    style.configure("TLabel", background=background, foreground=TEXT)
    style.configure("Sub.TLabel", background=background, foreground=SUB)
    # Notebook (tabs)
    style.configure("TNotebook", background=background, borderwidth=0,
                    tabmargins=(8, 6, 8, 0))
    style.configure("TNotebook.Tab", background=CARD, foreground=SUB,
                    padding=(16, 7), borderwidth=0)
    style.map("TNotebook.Tab",
              background=[("selected", CARD_IN), ("active", CARD)],
              foreground=[("selected", TEXT), ("active", TEXT)],
              expand=[("selected", (0, 0, 0, 1))])
    style.configure("TButton", background=CARD, foreground=TEXT,
                    borderwidth=0, focusthickness=0, padding=(12, 5))
    style.map("TButton",
              background=[("pressed", ACCENT), ("active", CARD_IN)],
              foreground=[("disabled", SUB)])
    style.configure("Danger.TButton", background=DANGER, foreground="#ffffff")
    style.map("Danger.TButton",
              background=[("pressed", "#ff8a63"), ("active", "#ff8a63")])
    # Treeview (default style name so every tree picks it up unchanged)
    style.configure("Treeview", background=CARD_IN, foreground=TEXT,
                    fieldbackground=CARD_IN, rowheight=28, borderwidth=0,
                    font=FONT)
    style.configure("Treeview.Heading", background=CARD,
                    foreground=SUB, font=("Microsoft YaHei", 10, "bold"),
                    relief="flat", borderwidth=0, padding=(8, 5))
    style.map("Treeview", background=[("selected", ACCENT)],
              foreground=[("selected", "#ffffff")])
    style.map("Treeview.Heading", background=[("active", CARD_IN)])
    style.configure("Vertical.TScrollbar", background=CARD,
                    troughcolor=background, borderwidth=0, arrowsize=12)
    # Entry / Combobox / Spinbox / Checkbutton (default style names)
    style.configure("TEntry", fieldbackground=CARD_IN, foreground=TEXT,
                    insertcolor=TEXT, borderwidth=0, padding=5)
    style.configure("TCombobox", fieldbackground=CARD_IN,
                    foreground=TEXT, background=CARD, arrowcolor=SUB,
                    borderwidth=0, padding=5)
    style.map("TCombobox",
              fieldbackground=[("readonly", CARD_IN)],
              foreground=[("readonly", TEXT)])
    root.option_add("*TCombobox*Listbox.background", CARD_IN)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    style.configure("TSpinbox", fieldbackground=CARD_IN, foreground=TEXT,
                    background=CARD, arrowcolor=SUB, borderwidth=0, padding=4)
    style.map("TSpinbox",
              fieldbackground=[("readonly", CARD_IN)],
              foreground=[("readonly", TEXT)],
              arrowcolor=[("active", ACCENT)])
    style.configure("TCheckbutton", background=background,
                    foreground=TEXT, focuscolor=background, font=FONT)
    style.map("TCheckbutton", background=[("active", background)],
              indicatorcolor=[("selected", ACCENT), ("!selected", CARD_IN)])
    return background
