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
    from PIL import Image, ImageDraw, ImageEnhance, ImageTk
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
        self._photo_img = None  # PIL copy of the window-size image
        self._size = None
        self._glass = {}        # (key, w, h) -> PhotoImage

    def _base(self):
        if self._img is None:
            self._img = Image.open(self.path).convert("RGB")
        return self._img

    def invalidate(self):
        self._img = None
        self._photo = None
        self._photo_img = None
        self._size = None
        self._glass.clear()

    def photo(self, w, h):
        """PhotoImage at window size (cover-crop), or None if unavailable.
        Also keeps the PIL copy so glass tiles crop pixel-aligned regions."""
        if not HAS_PIL or not self.path or w < 4 or h < 4:
            return None
        if self._size == (w, h) and self._photo is not None:
            return self._photo
        try:
            img = self._base()
            iw, ih = img.size
            scale = max(w / iw, h / ih)
            img = img.resize((max(w, int(iw * scale + .5)),
                              max(h, int(ih * scale + .5))),
                             Image.LANCZOS)
            x, y = (img.width - w) // 2, (img.height - h) // 2
            img = img.crop((x, y, x + w, y + h))
            self._photo_img = img
            self._photo = ImageTk.PhotoImage(img)
            self._size = (w, h)
            return self._photo
        except OSError:
            return None

    def fusion_colors(self):
        """(card, card_in) surface colors derived from the image: average
        color darkened, so panels blend with the wallpaper instead of
        covering it with flat gray blocks. None when unavailable."""
        if not HAS_PIL or not self.path:
            return None
        try:
            avg = self._base().resize((1, 1), Image.LANCZOS).getpixel((0, 0))
            base = "#%02x%02x%02x" % avg
            return shade(base, 0.38), shade(base, 0.30)
        except OSError:
            return None

    def glass_photo(self, key, box, radius, brightness=0.55):
        """Rounded 'glass' tile for a card: the wallpaper region behind the
        card (pixel-aligned with photo()), darkened, with transparent
        rounded corners - the real wallpaper shows through the corners and
        the card surface carries the wallpaper texture. None if
        unavailable; call photo(win_w, win_h) first."""
        if not HAS_PIL or not self.path:
            return None
        x, y, w, h = box
        win_img = getattr(self, "_photo_img", None)
        if win_img is None or w < 8 or h < 8:
            return None
        if x < 0 or y < 0 or x + w > win_img.width or y + h > win_img.height:
            return None
        ck = (key, w, h)
        if ck in self._glass:
            return self._glass[ck]
        img = win_img.crop((x, y, x + w, y + h))
        img = ImageEnhance.Brightness(img).enhance(brightness)
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1],
                                               radius=radius, fill=255)
        rgba = img.convert("RGBA")
        rgba.putalpha(mask)
        photo = ImageTk.PhotoImage(rgba)
        self._glass[ck] = photo
        return photo


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
        self._wallpaper = None
        self._root_win = None
        self._glass_ref = None
        self.inner = tk.Frame(self, bg=bg)
        self._win = self.create_window(0, 0, anchor="nw", window=self.inner)
        self.bind("<Configure>", lambda e: self.redraw())
        # canvas has no content-based size request of its own: mirror the
        # inner frame's requested height so pack() gives the card room
        self.inner.bind("<Configure>", self._sync_size)

    def set_wallpaper(self, wallpaper, root_win):
        """Enable the glass layer (wallpaper-backed card surface)."""
        self._wallpaper = wallpaper
        self._root_win = root_win

    def set_colors(self, bg=None, surface=None):
        """Recolor after a theme/background change."""
        if bg:
            self.config(bg=bg)
        if surface:
            self._card_bg = surface
            self.inner.config(bg=surface)
        self.redraw()

    def _sync_size(self, _e=None):
        need = self.inner.winfo_reqheight() + self.radius + 10
        self.config(height=need)

    def redraw(self):
        # Only remove the decorative layer (tag "card_bg") - NEVER delete
        # the window item holding `inner`, or all child widgets vanish.
        self.delete("card_bg")
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
        self.create_polygon(pts, smooth=True, fill=self._card_bg,
                            outline="", tags="card_bg")
        # Glass layer: the wallpaper region behind this card, darkened,
        # with transparent rounded corners (falls back to the flat polygon
        # above when no image / Pillow is available).
        if self._wallpaper is not None and self._root_win is not None:
            try:
                x = self.winfo_rootx() - self._root_win.winfo_rootx()
                y = self.winfo_rooty() - self._root_win.winfo_rooty()
                glass = self._wallpaper.glass_photo(
                    "card%d" % id(self), (x, y, w, h), self.radius)
                if glass is not None:
                    self._glass_ref = glass  # keep referenced
                    self.create_image(0, 0, anchor="nw", image=glass,
                                      tags="card_bg")
            except Exception:
                pass
        self.coords(self._win, r // 2 + 8, r // 2 + 4)
        self.itemconfigure(self._win, width=w - r - 16, height=h - r - 10)
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


def apply_dark_theme(root, background=None, surface=None):
    """Apply the dark ttk styles. `background` only recolors the window
    surface (root + gaps around the rounded cards); widget panels stay in
    the fixed dark palette so text contrast is guaranteed - unless
    `surface` (a (card, card_in) color pair derived from a wallpaper) is
    given, in which case panels blend with the image. Re-run any time."""
    background = background or CONTENT
    if luminance(background) > 0.55:
        # keep text readable even if a bright custom color slips through
        background = shade(background, 0.5)
    card_c, card_in = surface if surface else (CARD, CARD_IN)
    if luminance(card_c) > 0.45:  # fusion color must stay dark enough
        card_c = shade(card_c, 0.6)
        card_in = shade(card_in, 0.6)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    root.configure(bg=background)
    style.configure(".", background=card_c, foreground=TEXT, font=FONT,
                    borderwidth=0, focuscolor=ACCENT)
    style.configure("TFrame", background=card_c)
    style.configure("TLabel", background=card_c, foreground=TEXT)
    style.configure("Sub.TLabel", background=card_c, foreground=SUB)
    # Notebook (tabs)
    style.configure("TNotebook", background=background, borderwidth=0,
                    tabmargins=(8, 6, 8, 0))
    style.configure("TNotebook.Tab", background=card_in, foreground=SUB,
                    padding=(16, 7), borderwidth=0)
    style.map("TNotebook.Tab",
              background=[("selected", card_c), ("active", card_in)],
              foreground=[("selected", TEXT), ("active", TEXT)],
              expand=[("selected", (0, 0, 0, 1))])
    style.configure("TButton", background=card_c, foreground=TEXT,
                    borderwidth=0, focusthickness=0, padding=(12, 5))
    style.map("TButton",
              background=[("pressed", ACCENT), ("active", card_in)],
              foreground=[("disabled", SUB)])
    style.configure("Danger.TButton", background=DANGER, foreground="#ffffff")
    style.map("Danger.TButton",
              background=[("pressed", "#ff8a63"), ("active", "#ff8a63")])
    # Treeview (default style name so every tree picks it up unchanged)
    style.configure("Treeview", background=card_in, foreground=TEXT,
                    fieldbackground=card_in, rowheight=28, borderwidth=0,
                    font=FONT)
    style.configure("Treeview.Heading", background=card_c,
                    foreground=SUB, font=("Microsoft YaHei", 10, "bold"),
                    relief="flat", borderwidth=0, padding=(8, 5))
    style.map("Treeview", background=[("selected", ACCENT)],
              foreground=[("selected", "#ffffff")])
    style.map("Treeview.Heading", background=[("active", card_in)])
    style.configure("Vertical.TScrollbar", background=card_c,
                    troughcolor=background, borderwidth=0, arrowsize=12)
    # Entry / Combobox / Spinbox / Checkbutton (default style names)
    style.configure("TEntry", fieldbackground=card_in, foreground=TEXT,
                    insertcolor=TEXT, borderwidth=0, padding=5)
    style.configure("TCombobox", fieldbackground=card_in,
                    foreground=TEXT, background=CARD, arrowcolor=SUB,
                    borderwidth=0, padding=5)
    style.map("TCombobox",
              fieldbackground=[("readonly", card_in)],
              foreground=[("readonly", TEXT)])
    root.option_add("*TCombobox*Listbox.background", card_in)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    style.configure("TSpinbox", fieldbackground=card_in, foreground=TEXT,
                    background=card_c, arrowcolor=SUB, borderwidth=0, padding=4)
    style.map("TSpinbox",
              fieldbackground=[("readonly", card_in)],
              foreground=[("readonly", TEXT)],
              arrowcolor=[("active", ACCENT)])
    style.configure("TCheckbutton", background=card_c,
                    foreground=TEXT, focuscolor=CARD, font=FONT)
    style.map("TCheckbutton", background=[("active", card_c)],
              indicatorcolor=[("selected", ACCENT), ("!selected", card_in)])
    return background
