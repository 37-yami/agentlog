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

try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:  # pragma: no cover
    HAS_PIL = False


class Wallpaper:
    """Fullscreen background image, scaled to cover the window (Pillow
    optional - without it the solid color mode is all you get)."""

    def __init__(self, path=None):
        self.path = path
        self._img = None        # last loaded PIL image (unscaled)
        self._photo = None      # current PhotoImage (keep referenced)
        self._size = None

    def photo(self, w, h):
        """PhotoImage at window size (cover-crop), or None if unavailable."""
        if not HAS_PIL or not self.path or w < 4 or h < 4:
            return None
        if self._size == (w, h) and self._photo is not None:
            return self._photo
        try:
            if self._img is None:
                self._img = Image.open(self.path).convert("RGB")
            iw, ih = self._img.size
            scale = max(w / iw, h / ih)
            img = self._img.resize((max(w, int(iw * scale + .5)),
                                    max(h, int(ih * scale + .5))),
                                   Image.LANCZOS)
            x, y = (img.width - w) // 2, (img.height - h) // 2
            img = img.crop((x, y, x + w, y + h))
            self._photo = ImageTk.PhotoImage(img)
            self._size = (w, h)
            return self._photo
        except OSError:
            return None


class Card(tk.Canvas):
    """Rounded-card container: place widgets inside `card.inner` exactly as
    you would in a Frame - pack/grid all work. Call redraw() after the card
    is placed/resized (bound automatically on <Configure>)."""

    RADIUS = 14

    def __init__(self, master, bg=CARD, radius=None, **kw):
        self.radius = radius or self.RADIUS
        super().__init__(master, bg=master["bg"] if hasattr(master, "keys")
                         else CONTENT, highlightthickness=0, bd=0, **kw)
        self._card_bg = bg
        self.inner = tk.Frame(self, bg=bg)
        self._win = self.create_window(0, 0, anchor="nw", window=self.inner)
        self.bind("<Configure>", lambda e: self.redraw())
        # canvas has no content-based size request of its own: mirror the
        # inner frame's requested height so pack() gives the card room
        self.inner.bind("<Configure>", self._sync_size)

    def _sync_size(self, _e=None):
        need = self.inner.winfo_reqheight() + self.radius + 10
        self.config(height=need)

    def set_colors(self, bg=None, surface=None):
        """Recolor after a theme/background change."""
        if bg:
            self.config(bg=bg)
        if surface:
            self._card_bg = surface
            self.inner.config(bg=surface)
        self.redraw()

    def redraw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 4 or h < 4:
            return
        r = min(self.radius, w // 2, h // 2)
        import math
        pts = []

        def arc(cx, cy, a0, a1):
            steps = r // 2 or 1
            for i in range(steps + 1):
                a = math.radians(a0 + (a1 - a0) * i / steps)
                pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))

        arc(r, r, 180, 270); arc(w - r, r, 270, 360)
        arc(w - r, h - r, 0, 90); arc(r, h - r, 90, 180)
        self.create_polygon(pts, smooth=True, fill=self._card_bg, outline="")
        self.coords(self._win, r // 2 + 8, r // 2 + 4)
        self.itemconfigure(self._win, width=w - r - 16, height=h - r - 8)
        self.tag_raise(self._win)


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
    """Apply the dark ttk styles. `background` only recolors the window
    surface (root + gaps around the rounded cards); widget panels stay in
    the fixed dark palette so text contrast is guaranteed. Re-run any time
    to switch the background."""
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
    style.configure(".", background=CARD, foreground=TEXT, font=FONT,
                    borderwidth=0, focuscolor=ACCENT)
    style.configure("TFrame", background=CARD)
    style.configure("TLabel", background=CARD, foreground=TEXT)
    style.configure("Sub.TLabel", background=CARD, foreground=SUB)
    # Notebook (tabs)
    style.configure("TNotebook", background=background, borderwidth=0,
                    tabmargins=(8, 6, 8, 0))
    style.configure("TNotebook.Tab", background=CARD_IN, foreground=SUB,
                    padding=(16, 7), borderwidth=0)
    style.map("TNotebook.Tab",
              background=[("selected", CARD), ("active", CARD_IN)],
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
    style.configure("TCheckbutton", background=CARD,
                    foreground=TEXT, focuscolor=CARD, font=FONT)
    style.map("TCheckbutton", background=[("active", CARD)],
              indicatorcolor=[("selected", ACCENT), ("!selected", CARD_IN)])
    return background
