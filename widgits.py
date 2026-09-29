#!/usr/bin/env python3
"""Widgits: desktop widgets with switchable visual styles.

Open the app to pick a style and add widgets (clock, system monitor, media, notes)
to your desktop. Widgets are frameless cards: drag to move, right-click to remove.
The chosen widgets are restored on the next start; `--background` restores them
without opening the manager (used for autostart).
"""
import cairo
import collections
import copy
import json
import math
import re
import sys
import threading
import urllib.request
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Playerctl", "2.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
import psutil
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gsk, Gtk, Pango, PangoCairo, Playerctl

try:  # Material You palettes for the adaptive theme; without it adaptive falls back to dark
    from materialyoucolor.dynamiccolor.material_dynamic_colors import MaterialDynamicColors
    from materialyoucolor.hct import Hct
    from materialyoucolor.quantize import QuantizeCelebi
    from materialyoucolor.scheme.scheme_monochrome import SchemeMonochrome
    from materialyoucolor.scheme.scheme_tonal_spot import SchemeTonalSpot
    from materialyoucolor.scheme.scheme_vibrant import SchemeVibrant
    from materialyoucolor.score.score import Score
except ImportError:
    Hct = None

try:  # desktop-layer placement on KDE, Sway, Hyprland, etc.; absent or unsupported on GNOME
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell as LayerShell
except (ValueError, ImportError):
    LayerShell = None

CONFIG_DIR = Path(GLib.get_user_config_dir()) / "widgits"
DATA_DIR = Path(GLib.get_user_data_dir()) / "widgits"
STATE_FILE = CONFIG_DIR / "state.json"
CONFIG_FILE = CONFIG_DIR / "config.json"
POSITIONS_FILE = CONFIG_DIR / "positions.json"
HISTORY = 60
SCALE_MIN, SCALE_MAX = 0.5, 2.0

STYLES = {  # id -> display name
    "glass": "Glassmorphism",
    "neu": "Neumorphism",
    "clay": "Claymorphism",
    "brutal": "Brutalism",
    "aurora": "Aurora",
    "terminal": "Terminal",
    "plain": "No background",
}

CSS = """
window.widgit { background: transparent; }
.card { padding: 18px 22px; border-radius: 24px; }
.card .time { font-size: 52px; font-weight: 200; letter-spacing: -1px; }
.card .date { font-size: 13px; opacity: 0.65; }
.card .label { font-size: 11px; font-weight: 700; letter-spacing: 1.5px; opacity: 0.55; }
.card .value { font-size: 20px; font-weight: 300; }
.card .track { font-size: 15px; font-weight: 600; }
.card .artist { font-size: 12px; opacity: 0.6; }
.card calendar { background: transparent; color: inherit; border: none; padding: 0; }
.card textview, .card textview text { background: transparent; color: inherit; font-size: 14px; }
.card button.flat { color: inherit; min-width: 36px; min-height: 36px; }
.card progressbar trough { min-height: 4px; background: color-mix(in srgb, currentColor 15%, transparent); }
.card progressbar progress { min-height: 4px; background: currentColor; }

.card.glass {
  background-image: linear-gradient(135deg, alpha(white, 0.18), alpha(white, 0.03)),
                    linear-gradient(alpha(#0e0e18, 0.55), alpha(#0e0e18, 0.55));
  border: 1px solid alpha(white, 0.28); color: #f4f4fa;
  box-shadow: 0 12px 36px alpha(black, 0.40);
}
.card.neu {
  background: #e0e5ec; color: #4a5568; border-radius: 30px;
  box-shadow: 9px 9px 18px #a3b1c6, -9px -9px 18px #ffffff;
}
.card.clay {
  background-image: linear-gradient(145deg, #ffdbe9, #ffc3da); color: #5b2a45; border-radius: 38px;
  box-shadow: inset -8px -8px 16px alpha(#c0507f, 0.35), inset 8px 8px 16px alpha(white, 0.85),
              12px 14px 26px alpha(black, 0.28);
}
.card.brutal {
  background: #ffe14d; color: #000; border: 3px solid #000; border-radius: 0;
  box-shadow: 8px 8px 0 #000; font-family: monospace; font-weight: 700;
}
.card.aurora {
  background-image: linear-gradient(135deg, #4b2a91, #1f7fb8 55%, #22c7a9); color: #fff;
  border-radius: 30px; box-shadow: 0 14px 34px alpha(#1f7fb8, 0.45);
}
.card.terminal {
  background: alpha(black, 0.86); color: #33ff66; border: 1px solid #33ff66; border-radius: 6px;
  font-family: monospace; box-shadow: 0 0 20px alpha(#33ff66, 0.35);
}

.card.plain { background: none; box-shadow: none; color: #ffffff; }
.card.plain.preview { border: 1px dashed alpha(currentColor, 0.5); color: inherit; }

.preview { min-width: 92px; min-height: 52px; padding: 10px 14px; }
.preview label { font-size: 15px; font-weight: 700; }

/* manager: a dark "stage" so every style previews on the same backdrop, in light or dark system themes */
.stage { background-image: linear-gradient(135deg, #1b1b2f, #2b1f3d 55%, #142c3a); border-radius: 26px; padding: 22px; }
.stage-title { color: #ffffff; font-size: 12px; font-weight: 800; letter-spacing: 1.8px; }
.stage-sub { color: alpha(white, 0.72); font-size: 13px; }
.stage flowboxchild { border-radius: 24px; background: none; }
.stage flowboxchild:selected { outline: 2px solid white; outline-offset: -8px; }
.tile { padding: 18px; }
.tile .label { opacity: 1; }
.tile-material { background: #1C1C1E; color: #ffffff; border: 1px solid #48484A; border-radius: 30px; }
.card.mono { padding: 0; }
.tile-shell { background: alpha(white, 0.05); border: 1px solid alpha(white, 0.09); border-radius: 22px; padding: 14px;
              transition: transform 160ms ease-out, background 160ms ease-out, box-shadow 160ms ease-out; }
.tile-shell:hover { background: alpha(white, 0.10); transform: translateY(-3px); box-shadow: 0 12px 26px alpha(black, 0.35); }
.tile-name { color: #ffffff; font-size: 15px; font-weight: 700; }
.tile-desc { color: alpha(white, 0.72); font-size: 12px; }
.tile-shell .tile-add { color: #ffffff; }
.pv-hours, .pv-mins { font-size: 64px; font-weight: 300; letter-spacing: -2px; color: #ffffff; }
.pv-mins { color: #E8622A; }
.stage-section { color: #ffffff; font-size: 13px; font-weight: 800; letter-spacing: 1.6px; }
.card.plain.tile { color: white; border: 1px dashed alpha(white, 0.55); }
.tile-add { border: 1.5px solid currentColor; border-radius: 999px; color: inherit; background: transparent;
            min-height: 36px; padding: 0 16px; font-weight: 700; }
.tile-add:hover { background: color-mix(in srgb, currentColor 14%, transparent); }
.badge { background: alpha(@accent_bg_color, 0.9); color: @accent_fg_color; border-radius: 999px;
         padding: 1px 8px; font-size: 11px; font-weight: 700; }
"""


def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


# ---- Theme: dark | adaptive | auto ------------------------------------------------------
# config.json: {"theme": {"mode": "dark", "seed": "#RRGGBB", "wallpaper_path": "/path"}, "pixel_face": "filled"}
FALLBACK_SEED = "#E8622A"
DARK = {"bg": "#1C1C1E", "text": "#FFFFFF", "secondary": "#9A9A9F", "accent": "#E8622A",
        "face": "#2C2C2E", "outline": "#48484A", "ink": "#F2F0EB", "on_accent": "#000000"}
LIGHT = {"bg": "#F5F5F7", "text": "#1C1C1E", "secondary": "#6B6B70", "accent": "#E8622A",
         "face": "#E5E5EA", "outline": "#C7C7CC", "ink": "#0E0E0E", "on_accent": "#FFFFFF"}
# Mono card: pure black with a signal-orange accent (fixed dark), and its inverse for a light system
MONO_DARK = {**DARK, "bg": "#000000", "text": "#FFFFFF", "secondary": "#FFFFFF", "accent": "#FF4D00", "on_accent": "#000000"}
MONO_LIGHT = {**LIGHT, "bg": "#FFFFFF", "text": "#000000", "secondary": "#000000", "accent": "#FF4D00", "on_accent": "#000000"}
VARIANTS = {"tonal_spot": "Tonal spot", "vibrant": "Vibrant", "monochrome": "Monochrome"}
THEME_MODES = {"dark": "Dark", "adaptive": "Adaptive (wallpaper)", "auto": "Auto (system light/dark)"}
_seed_cache = {}


_config_cache = (None, None)


def load_config():
    """Config with defaults filled in. Cached until config.json changes, since widgets ask often."""
    global _config_cache
    try:
        st = CONFIG_FILE.stat()
        stamp = (st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = None
    if _config_cache[1] is not None and _config_cache[0] == stamp:
        return copy.deepcopy(_config_cache[1])
    result = _build_config(load_json(CONFIG_FILE, {}))
    _config_cache = (stamp, result)
    return copy.deepcopy(result)


def _build_config(cfg):
    theme = {"mode": "dark", "seed": None, "wallpaper_path": None, "variant": "tonal_spot", **cfg.get("theme", {})}
    warped = {"backing": "none", "plate_opacity": 1.0, "accent_hands": True, "tilt": -12, "distortion": 0.9,
              "font": "Playfair Display", "micro_text": "quartz", "smooth": True, **cfg.get("warped", {})}
    mono = {"show_seconds": True, "show_bluetooth": True, "show_headphones": True, "corner_radius": 48, "shadow": False,
            **{k: v for k, v in cfg.get("mono", {}).items() if k not in ("sleep", "weather")},
            "sleep": {"enabled": True, **cfg.get("mono", {}).get("sleep", {})},
            "weather": {"lat": 15.49, "lon": 73.83, "unit": "C", **cfg.get("mono", {}).get("weather", {})}}
    return {"theme": theme, "pixel_face": cfg.get("pixel_face", "filled"), "warped": warped, "mono": mono,
            "size": min(max(cfg.get("size", 1.0), SCALE_MIN), SCALE_MAX)}


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def argb_to_hex(argb):
    return f"#{argb & 0xFFFFFF:06X}"


def gsettings_string(schema, key):
    source = Gio.SettingsSchemaSource.get_default()
    if not source or not source.lookup(schema, True):
        return None
    try:
        return Gio.Settings.new(schema).get_string(key)
    except (GLib.Error, TypeError):
        return None


def wallpaper_path(theme):
    """Configured path, then GNOME's gsettings, then KDE Plasma's applet config."""
    candidates = [theme.get("wallpaper_path")]
    for key in ("picture-uri-dark", "picture-uri"):
        uri = gsettings_string("org.gnome.desktop.background", key)
        candidates.append(Gio.File.new_for_uri(uri).get_path() if uri else None)
    try:
        rc = (Path(GLib.get_home_dir()) / ".config" / "plasma-org.kde.plasma.desktop-appletsrc").read_text()
        found = re.findall(r"^Image=(.+)$", rc, re.M)
        candidates.append(Gio.File.new_for_uri(found[-1]).get_path() if found else None)
    except OSError:
        pass
    for path in candidates:
        if path and Path(path).is_file() and not path.endswith(".xml"):
            return path
    return None


def seed_from_wallpaper(path):
    """Dominant Material seed color (ARGB int) of an image, cached per file version."""
    key = (path, Path(path).stat().st_mtime)
    if key not in _seed_cache:
        try:
            pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 96, 96, True)
        except GLib.Error:
            return None
        data, n, stride = pix.get_pixels(), pix.get_n_channels(), pix.get_rowstride()
        pixels = [[data[y * stride + x * n], data[y * stride + x * n + 1], data[y * stride + x * n + 2]]
                  for y in range(pix.get_height()) for x in range(pix.get_width())]
        ranked = Score.score(QuantizeCelebi(pixels, 128))
        _seed_cache[key] = ranked[0] if ranked else None
    return _seed_cache[key]


_lum_cache = {}


def wallpaper_luminance(theme):
    """Mean relative luminance (0-1) of the current wallpaper, or None if it can't be read."""
    path = wallpaper_path(theme)
    if not path:
        return None
    key = (path, Path(path).stat().st_mtime)
    if key not in _lum_cache:
        try:
            pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 48, 48, True)
        except GLib.Error:
            return None
        data, n, stride = pix.get_pixels(), pix.get_n_channels(), pix.get_rowstride()
        lin = [(i / 255 / 12.92 if i / 255 <= 0.04045 else ((i / 255 + 0.055) / 1.055) ** 2.4) for i in range(256)]
        total = count = 0
        for y in range(pix.get_height()):
            for x in range(pix.get_width()):
                o = y * stride + x * n
                total += 0.2126 * lin[data[o]] + 0.7152 * lin[data[o + 1]] + 0.0722 * lin[data[o + 2]]
                count += 1
        _lum_cache[key] = total / count if count else None
    return _lum_cache[key]


def luminance(hex_color):
    def lin(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in hex_to_rgb(hex_color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(l1, l2):
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def adaptive_seed(theme):
    """First available: config seed, wallpaper, GTK accent color, fallback."""
    if theme.get("seed"):
        return 0xFF000000 | int(theme["seed"].lstrip("#"), 16)
    path = wallpaper_path(theme)
    seed = seed_from_wallpaper(path) if path else None
    if seed:
        return seed
    style = Adw.StyleManager.get_default()
    if hasattr(style, "get_accent_color_rgba") and style.get_system_supports_accent_colors():
        c = style.get_accent_color_rgba()
        return 0xFF000000 | int(c.red * 255) << 16 | int(c.green * 255) << 8 | int(c.blue * 255)
    return 0xFF000000 | int(FALLBACK_SEED.lstrip("#"), 16)


def resolve_tokens(theme, fixed=None):
    """Palette for the theme. `fixed` is a (dark, light) pair used by the non-adaptive modes."""
    dark, light = fixed or (DARK, LIGHT)
    mode = theme["mode"]
    if mode == "auto":
        return dark if Adw.StyleManager.get_default().get_dark() else light
    if mode == "adaptive" and Hct is not None:
        try:
            scheme_class = {"vibrant": SchemeVibrant, "monochrome": SchemeMonochrome}.get(theme["variant"], SchemeTonalSpot)
            scheme = scheme_class(Hct.from_int(adaptive_seed(theme)), True, 0.0)
            role = lambda name: argb_to_hex(getattr(MaterialDynamicColors, name).get_hct(scheme).to_int())
            return {"bg": role("surface"),
                    "text": role("onSurface"), "secondary": role("onSurfaceVariant"),
                    "accent": role("primary"), "face": role("surfaceContainerHigh"),
                    "outline": role("outline"), "ink": role("onSurface"), "on_accent": role("onPrimary")}
        except (ValueError, OSError):
            pass
    return dark


class Scaler(Gtk.Widget):
    """Draws its child at a uniform scale; input and layout follow the scale."""

    __gtype_name__ = "WidgitsScaler"

    def __init__(self, child, scale=1.0):
        super().__init__()
        self.child, self.scale = child, scale
        child.set_parent(self)

    def set_scale(self, scale):
        if abs(scale - self.scale) > 1e-3:
            self.scale = scale
            self.queue_resize()

    def animate_to(self, target, ms=160):
        """Ease to a new scale (skipped automatically when the system disables animations)."""
        if getattr(self, "anim", None):
            self.anim.pause()
        self.anim = Adw.TimedAnimation.new(self, self.scale, target, ms,
                                           Adw.CallbackAnimationTarget.new(self.set_scale))
        self.anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self.anim.play()

    def do_measure(self, orientation, for_size):
        s = self.scale
        along = for_size if orientation == Gtk.Orientation.VERTICAL and for_size > 0 else -1
        minimum, natural, _, _ = self.child.measure(orientation, int(along / s) if along > 0 else -1)
        return math.ceil(minimum * s), math.ceil(natural * s), -1, -1

    def do_size_allocate(self, width, height, baseline):
        s = self.scale
        self.child.allocate(math.ceil(width / s), math.ceil(height / s), -1, Gsk.Transform().scale(s, s))

    def do_dispose(self):
        if self.child is not None:
            self.child.unparent()
            self.child = None
        Gtk.Widget.do_dispose(self)


class Widget(Gtk.Window):
    """Frameless card. With layer-shell it sits on the desktop layer, below all apps
    (drag updates its margins). Otherwise it is a normal window and the GNOME
    extension pins it below other windows. Right-click removes it."""

    kind = ""
    label = ""
    width = 320

    def __init__(self, app, item):
        self.item = item
        self.app = app
        self.title_text = f"{self.label} {item['id']}"
        super().__init__(application=app, title=self.title_text, decorated=False, resizable=False)
        self.add_css_class("widgit")
        self.card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, css_classes=["card"],
                            width_request=self.width - 56)
        self.style_class = None
        self.apply_style(item["style"])
        self.setup_menu(app)

        # The margin leaves room for the shadows of the neumorphic/brutalist cards.
        outer = Gtk.Box(margin_top=28, margin_bottom=28, margin_start=28, margin_end=28)
        outer.append(self.card)
        self.scaler = Scaler(outer, self.effective_scale())
        self.layered = LayerShell is not None and LayerShell.is_supported()
        if self.layered:
            self.setup_layer()
            self.set_child(self.scaler)
        else:
            self.set_child(Gtk.WindowHandle(child=self.scaler))
        self.setup_resize_gestures(app)

    def effective_scale(self):
        return self.item.get("scale", 1.0) * load_config()["size"]

    def apply_scale(self):
        self.scaler.animate_to(self.effective_scale())

    def fade(self, target, done=None):
        """Fade the whole widget in or out."""
        self._fade = Adw.TimedAnimation.new(self, self.get_opacity(), target, 220,
                                            Adw.PropertyAnimationTarget.new(self, "opacity"))
        if done:
            self._fade.connect("done", lambda *_: done())
        self._fade.play()

    def natural_size(self):
        w = self.scaler.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
        return w, self.scaler.measure(Gtk.Orientation.VERTICAL, w)[1]

    def setup_resize_gestures(self, app):
        """Ctrl+scroll over a widget resizes it."""
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

        def on_scroll(controller, _dx, dy):
            if not controller.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK:
                return False
            app.set_scale(self.item["id"], self.item.get("scale", 1.0) * (1 - 0.05 * dy))
            return True
        scroll.connect("scroll", on_scroll)
        self.card.add_controller(scroll)

    def setup_menu(self, app):
        """Right-click menu: open the manager or remove this widget (nothing happens on left click)."""
        actions = Gio.SimpleActionGroup()
        step = lambda f: (lambda *_: app.set_scale(self.item["id"], self.item.get("scale", 1.0) * f))
        for name, handler in (("manage", lambda *_: app.show_manager()),
                              ("bigger", step(1.1)), ("smaller", step(1 / 1.1)),
                              ("reset", lambda *_: app.set_scale(self.item["id"], 1.0)),
                              ("remove", lambda *_: app.remove_widget(self.item["id"]))):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            actions.add_action(action)
        self.insert_action_group("widget", actions)
        menu = Gio.Menu()
        menu.append("Open Widgits", "widget.manage")
        size = Gio.Menu()
        size.append("Larger", "widget.bigger")
        size.append("Smaller", "widget.smaller")
        size.append("Reset size", "widget.reset")
        menu.append_section(None, size)
        menu.append("Remove from desktop", "widget.remove")
        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(self.card)
        popover.set_has_arrow(False)
        self.connect("destroy", lambda *_: popover.unparent())
        click = Gtk.GestureClick(button=3)

        def show(_g, _n, x, y):
            rect = Gdk.Rectangle()
            rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
            popover.set_pointing_to(rect)
            popover.popup()
        click.connect("pressed", show)
        self.card.add_controller(click)

    def apply_style(self, style):
        if self.style_class:
            self.card.remove_css_class(self.style_class)
        self.style_class = style
        self.card.add_css_class(style)

    def every(self, ms, fn):
        """Run fn periodically until this widget is closed."""
        source = GLib.timeout_add(ms, fn)
        self.connect("destroy", lambda *_: GLib.source_remove(source))

    def setup_layer(self):
        LayerShell.init_for_window(self)
        LayerShell.set_namespace(self, "widgits")
        LayerShell.set_layer(self, LayerShell.Layer.BACKGROUND)
        LayerShell.set_anchor(self, LayerShell.Edge.TOP, True)
        LayerShell.set_anchor(self, LayerShell.Edge.LEFT, True)
        saved = load_json(POSITIONS_FILE, {}).get(self.title_text)
        self.pos = saved or self.app.find_slot(*self.natural_size())
        self.apply_margins()
        if not saved:
            self.save_position()
        drag = Gtk.GestureDrag(button=1)
        drag.connect("drag-begin", lambda *_: setattr(self, "origin", list(self.pos)))
        drag.connect("drag-update", lambda _g, dx, dy: self.move_to(self.origin[0] + dx * self.scaler.scale,
                                                                     self.origin[1] + dy * self.scaler.scale))
        drag.connect("drag-end", lambda *_: self.save_position())
        self.card.add_controller(drag)

    def apply_margins(self):
        LayerShell.set_margin(self, LayerShell.Edge.LEFT, int(self.pos[0]))
        LayerShell.set_margin(self, LayerShell.Edge.TOP, int(self.pos[1]))

    def move_to(self, x, y):
        self.pos = [max(0, x), max(0, y)]
        self.apply_margins()

    def save_position(self):
        positions = load_json(POSITIONS_FILE, {})
        positions[self.title_text] = [int(v) for v in self.pos]
        save_json(POSITIONS_FILE, positions)

    def cleanup(self):
        """Called when the user removes the widget; persist anything that should survive an undo."""


class ClockWidget(Widget):
    kind, label = "clock", "Clock"

    def __init__(self, app, item):
        super().__init__(app, item)
        self.time = Gtk.Label(xalign=0, css_classes=["time"])
        self.date = Gtk.Label(xalign=0, css_classes=["date"])
        self.card.append(self.time)
        self.card.append(self.date)
        self.card.append(Gtk.Calendar())
        self.tick()
        self.every(1000, self.tick)

    def tick(self):
        now = datetime.now()
        self.time.set_text(now.strftime("%H:%M"))
        self.date.set_text(now.strftime("%A, %d %B %Y"))
        return True


class AnalogFace(Gtk.DrawingArea):
    """Analog clock face: round, square, or a Material You scalloped face (pixel)."""

    def __init__(self, shape):
        super().__init__(content_width=200, content_height=200)
        self.shape = shape
        self.palette = {k: hex_to_rgb(v) for k, v in DARK.items()}
        self.frame = False
        self.set_draw_func(self.draw)

    def set_theme(self, tokens, frame=False):
        self.palette = {k: hex_to_rgb(v) for k, v in tokens.items()}
        self.frame = frame
        self.queue_draw()

    @staticmethod
    def polar(cx, cy, angle, r):
        return cx + r * math.sin(angle), cy - r * math.cos(angle)

    def edge(self, angle, half):
        """Distance from the center to the face boundary along angle."""
        if self.shape == "square":
            return half / max(abs(math.sin(angle)), abs(math.cos(angle)))
        return half

    def outline(self, cr, cx, cy, half):
        if self.shape == "round":
            cr.arc(cx, cy, half, 0, 2 * math.pi)
        elif self.shape == "square":
            r = half * 0.22
            cr.new_sub_path()
            for (x, y), start in (((cx + half - r, cy - half + r), -90), ((cx + half - r, cy + half - r), 0),
                                  ((cx - half + r, cy + half - r), 90), ((cx - half + r, cy - half + r), 180)):
                cr.arc(x, y, r, math.radians(start), math.radians(start + 90))
            cr.close_path()
        else:  # pixel: a 12-lobe scalloped circle
            for i in range(361):
                a = math.radians(i)
                x, y = self.polar(cx, cy, a, half * (0.95 + 0.05 * math.cos(12 * a)))
                cr.line_to(x, y)
            cr.close_path()

    def draw(self, _area, cr, w, h):
        now = datetime.now()
        cx, cy, half = w / 2, h / 2, min(w, h) / 2 - 4
        pixel = self.shape == "pixel"
        p = self.palette
        fg = self.get_color()
        ink = p["text"] if pixel else (fg.red, fg.green, fg.blue)
        accent = p["accent"] if pixel else (1.0, 0.42, 0.35)
        cr.set_line_cap(1)  # round caps

        self.outline(cr, cx, cy, half)
        if pixel and self.frame:
            cr.set_source_rgb(*p["outline"])
            cr.set_line_width(3)
            cr.stroke()
        elif pixel:
            cr.set_source_rgb(*p["face"])
            cr.fill()
        else:
            cr.set_source_rgba(*ink, 0.10)
            cr.fill_preserve()
            cr.set_source_rgba(*ink, 0.55)
            cr.set_line_width(2.5)
            cr.stroke()

        for i in range(12):  # hour markers
            a = math.radians(i * 30)
            if pixel:
                cr.arc(*self.polar(cx, cy, a, half * 0.82), 2.2 if i % 3 else 3.2, 0, 2 * math.pi)
                cr.set_source_rgb(*(ink if i % 3 == 0 else p["secondary"]))
                cr.fill()
            else:
                d = self.edge(a, half)
                big = i % 3 == 0
                cr.move_to(*self.polar(cx, cy, a, d * (0.80 if big else 0.86)))
                cr.line_to(*self.polar(cx, cy, a, d * 0.93))
                cr.set_source_rgba(*ink, 0.9 if big else 0.5)
                cr.set_line_width(3.2 if big else 1.6)
                cr.stroke()

        hour = math.radians(((now.hour % 12) + now.minute / 60) * 30)
        minute = math.radians((now.minute + now.second / 60) * 6)
        second = math.radians(now.second * 6)
        thick = 1.6 if pixel else 1.0
        for angle, length, width, color in ((hour, 0.48, 6.5, accent if pixel else ink), (minute, 0.72, 4.5, ink)):
            cr.move_to(cx, cy)
            cr.line_to(*self.polar(cx, cy, angle, half * length))
            cr.set_source_rgb(*color)
            cr.set_line_width(width * thick)
            cr.stroke()

        cr.move_to(*self.polar(cx, cy, second + math.pi, half * 0.14))
        cr.line_to(*self.polar(cx, cy, second, half * 0.80))
        cr.set_source_rgb(*accent)
        cr.set_line_width(1.8)
        cr.stroke()
        cr.arc(cx, cy, 5 * thick, 0, 2 * math.pi)
        cr.set_source_rgb(*accent)
        cr.fill()


class Tweened:
    """Mixin for drawing areas: a palette of RGBA tuples that crossfades to a new target in ~300 ms."""

    pal = pal_from = pal_to = None
    tween_start = tween_id = 0

    def animate_palette(self, target):
        if self.pal is None:
            self.pal = target
            self.queue_draw()
            return
        self.pal_from, self.pal_to = dict(self.pal), target
        self.tween_start = GLib.get_monotonic_time()
        if not self.tween_id:
            self.tween_id = GLib.timeout_add(30, self.step_tween)

    def step_tween(self):
        t = min((GLib.get_monotonic_time() - self.tween_start) / 300000, 1.0)
        e = t * t * (3 - 2 * t)
        self.pal = {k: tuple(a + (b - a) * e for a, b in zip(self.pal_from[k], self.pal_to[k]))
                    for k in self.pal_to}
        self.queue_draw()
        if t >= 1.0:
            self.tween_id = 0
            return False
        return True


class WarpedFace(Tweened, Gtk.DrawingArea):
    """Bezel-less analog face: numerals melt around a tall, tilted ellipse. Only ink on a transparent
    background. Every color comes from the theme palette; numeral paths are built once and cached."""

    W, H = 220, 280
    FOLLOW = 0.4  # how much numerals lean along the ring tangent (0 = upright, 1 = fully tangential)

    def __init__(self):
        super().__init__(content_width=self.W, content_height=self.H)
        self.opts = load_config()["warped"]
        self.key = None
        self.paths = []
        self.ring_bottom = self.H
        self.set_draw_func(self.draw)

    # ---- theme
    def set_theme(self, tokens, wall_lum, opts):
        self.opts = opts
        ink, bg = tokens["ink"], tokens["bg"]
        halo = None
        if opts["backing"] != "plate" and wall_lum is not None:
            # Keep the ink readable on the wallpaper: flip to the opposite tone, else add a surface halo.
            if contrast(luminance(ink), wall_lum) < 4.5:
                if contrast(luminance(bg), wall_lum) > contrast(luminance(ink), wall_lum):
                    ink, bg = bg, ink
                halo = bg
        rgba = lambda h, a=1.0: (*hex_to_rgb(h), a)
        target = {"ink": rgba(ink), "secondary": rgba(tokens["secondary"]), "accent": rgba(tokens["accent"]),
                  "plate": rgba(tokens["bg"], opts["plate_opacity"] if opts["backing"] == "plate" else 0.0),
                  "halo": rgba(halo, 0.55) if halo else rgba(bg, 0.0)}
        self.animate_palette(target)

    # ---- geometry (cached)
    def pick_font(self):
        families = {f.get_name() for f in self.get_pango_context().get_font_map().list_families()}
        for name in (self.opts["font"], "Playfair Display", "DM Serif Display", "Fraunces"):
            if name in families:
                return name
        return "serif"

    def build_paths(self):
        font, d, tilt = self.pick_font(), float(self.opts["distortion"]), math.radians(self.opts["tilt"])
        key = (font, d, tilt)
        if key == self.key:
            return
        self.key = key
        w, h = self.W, self.H
        cr = cairo.Context(cairo.ImageSurface(cairo.FORMAT_A8, 1, 1))
        cr.select_font_face(font, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(0.125 * h)
        cx, cy = w / 2, h / 2 - 12
        a = w / 2 * (0.78 - 0.16 * d)
        b = (h / 2 - 34) * (0.74 + 0.20 * d)
        theta0 = math.radians(-110)
        placed = {}
        for n in range(1, 13):
            th = math.radians(n * 30 - 90)
            ex, ey = a * math.cos(th), b * math.sin(th)
            px = cx + ex * math.cos(tilt) - ey * math.sin(tilt)
            py = cy + ex * math.sin(tilt) + ey * math.cos(tilt)
            tx, ty = -a * math.sin(th), b * math.cos(th)
            tangent = math.atan2(tx * math.sin(tilt) + ty * math.cos(tilt), tx * math.cos(tilt) - ty * math.sin(tilt))
            while tangent > math.pi / 2:      # fold so numerals never read upside down
                tangent -= math.pi
            while tangent <= -math.pi / 2:
                tangent += math.pi
            rot = tangent * self.FOLLOW * d
            scale = 0.72 + 0.75 * abs(math.sin(th - theta0)) ** 1.6
            if n in (10, 12):
                scale = 0.6
            scale = 1 + (scale - 1) * d

            text = str(n)
            cr.new_path()
            cr.text_path(text)
            ext = cr.text_extents(text)
            ox, oy = ext.x_bearing + ext.width / 2, ext.y_bearing + ext.height / 2
            hg = ext.height
            subpaths, cur = [], None
            for op, pts in cr.copy_path_flat():
                if op == cairo.PATH_MOVE_TO:
                    cur = [pts]
                    subpaths.append(cur)
                elif op == cairo.PATH_LINE_TO and cur is not None:
                    cur.append(pts)
            out = []
            for sub in subpaths:
                pts = []
                for x, y in sub:
                    u, v = (x - ox) / hg, (y - oy) / hg
                    drip = (v + 0.5)
                    v2 = v + d * 0.32 * drip ** 2                 # melt: the bottom of each glyph sags
                    u2 = u * (1 - d * 0.20 * drip) + d * 0.04 * math.sin(v * 5 + n)  # taper + wobble
                    X, Y = u2 * hg * scale, v2 * hg * scale
                    pts.append((px + X * math.cos(rot) - Y * math.sin(rot),
                                py + X * math.sin(rot) + Y * math.cos(rot)))
                out.append(pts)
            placed[n] = out

        def bbox(subs):
            xs = [p[0] for s_ in subs for p in s_]
            ys = [p[1] for s_ in subs for p in s_]
            return min(xs), min(ys), max(xs), max(ys)

        # Fuse 10 and 11: pull 11 in so their boxes overlap by about 30%.
        x0, y0, x1, y1 = bbox(placed[10])
        u0, v0, u1, v1 = bbox(placed[11])
        c10, c11 = ((x0 + x1) / 2, (y0 + y1) / 2), ((u0 + u1) / 2, (v0 + v1) / 2)
        dist = math.hypot(c11[0] - c10[0], c11[1] - c10[1])
        want = 0.7 * ((x1 - x0) + (u1 - u0)) / 2
        if dist > want > 0:
            pull = (dist - want) / dist * d
            dx, dy = (c10[0] - c11[0]) * pull, (c10[1] - c11[1]) * pull
            placed[11] = [[(x + dx, y + dy) for x, y in sub] for sub in placed[11]]

        self.paths = [placed[n] for n in range(1, 13)]
        self.center, self.axes, self.tilt_rad = (cx, cy), (a, b), tilt
        self.ring_bottom = max(p[1] for subs in self.paths for sub in subs for p in sub)

    # ---- drawing
    def draw(self, _area, cr, w, h):
        self.build_paths()
        if self.pal is None:
            return
        p, o = self.pal, self.opts
        cx, cy = self.center
        a, b = self.axes
        cr.set_line_cap(cairo.LINE_CAP_ROUND)

        if p["plate"][3] > 0.001:  # optional backing plate: the surface color behind the ring
            cr.save()
            cr.translate(cx, cy)
            cr.rotate(self.tilt_rad)
            cr.scale(a * 1.32, b * 1.2)
            cr.arc(0, 0, 1, 0, 2 * math.pi)
            cr.restore()
            cr.set_source_rgba(*p["plate"])
            cr.fill()

        for subs in self.paths:
            cr.new_path()
            for sub in subs:
                cr.move_to(*sub[0])
                for pt in sub[1:]:
                    cr.line_to(*pt)
                cr.close_path()
            cr.set_fill_rule(cairo.FILL_RULE_WINDING)  # variable fonts overlap contours
            if p["halo"][3] > 0.001:
                cr.set_source_rgba(*p["halo"])
                cr.set_line_width(1.6)
                cr.stroke_preserve()
            cr.set_source_rgba(*p["ink"])
            cr.fill()

        cr.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(9)
        cr.set_source_rgba(*p["secondary"])
        lines = str(o["micro_text"]).lower().split("\n")
        for i, line in enumerate(lines):  # tiny stacked text above the hands
            e = cr.text_extents(line)
            cr.move_to(cx - e.width / 2 - e.x_bearing, cy - b * 0.34 + i * 11)
            cr.show_text(line)
        footer = datetime.now().strftime("%a %d").lower()
        e = cr.text_extents(footer)
        cr.move_to(cx - e.width / 2 - e.x_bearing, min(self.ring_bottom + 16, h - 6))
        cr.show_text(footer)

        now = datetime.now()
        sec = now.second / 60 if o["smooth"] else 0
        hour = math.radians(((now.hour % 12) + (now.minute + sec) / 60) * 30)
        minute = math.radians((now.minute + sec) * 6)
        reach = min(a, b)
        self.hand(cr, cx, cy, hour, reach * 0.52, 3.4, p["accent"] if o["accent_hands"] else p["ink"], p["ink"])
        self.hand(cr, cx, cy, minute, reach * 0.86, 3.0, p["ink"], p["ink"])
        cr.arc(cx, cy, 2.4, 0, 2 * math.pi)
        cr.set_source_rgba(*p["ink"])
        cr.fill()

    @staticmethod
    def hand(cr, cx, cy, angle, length, half_width, tip, stem):
        """Thin stem with a leaf-shaped tip."""
        cr.save()
        cr.translate(cx, cy)
        cr.rotate(angle)
        cr.move_to(0, length * 0.16)
        cr.line_to(0, -length * 0.62)
        cr.set_source_rgba(*stem)
        cr.set_line_width(1.3)
        cr.stroke()
        cr.move_to(0, -length * 0.5)
        cr.curve_to(half_width * 1.6, -length * 0.66, half_width * 1.2, -length * 0.9, 0, -length)
        cr.curve_to(-half_width * 1.2, -length * 0.9, -half_width * 1.6, -length * 0.66, 0, -length * 0.5)
        cr.set_source_rgba(*tip)
        cr.fill()
        cr.restore()


class WarpedClockWidget(Widget):
    """Warped Clock: melting numerals on a tilted oval, no card. Ink follows the theme."""

    kind, label = "clock_warped", "Warped Clock"
    width = 320

    def apply_style(self, style):
        self.card.add_css_class("plain")  # always transparent; the only look is ink

    def __init__(self, app, item):
        super().__init__(app, item)
        self.face = WarpedFace()
        self.card.set_halign(Gtk.Align.CENTER)
        self.card.append(self.face)
        self.refresh_theme()
        self.tick_id = 0
        self.schedule()
        self.every(30000, self.refresh_theme)  # follow wallpaper / system changes

    def schedule(self):
        if self.tick_id:
            GLib.source_remove(self.tick_id)
        smooth = load_config()["warped"]["smooth"]
        self.tick_id = GLib.timeout_add(1000 if smooth else 60000, lambda: self.face.queue_draw() or True)
        self.connect("destroy", lambda *_: self.tick_id and GLib.source_remove(self.tick_id))

    def refresh_theme(self):
        cfg = load_config()
        tokens = resolve_tokens(cfg["theme"])
        self.face.set_theme(tokens, wallpaper_luminance(cfg["theme"]), cfg["warped"])
        return True


# ---- Mono card ------------------------------------------------------------------------
SLEEP_FILE = Path.home() / ".config" / "clockwidget" / "sleep.json"
WEATHER_FILE = DATA_DIR / "weather.json"
AUDIO_ICONS = ("audio-headset", "audio-headphones")


def rounded_rect(cr, x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


GW = 0.5  # glyph width, as a fraction of its height


def round_poly(cr, pts, r):
    """Open polyline whose inner corners are rounded with radius r."""
    cr.move_to(*pts[0])
    for i in range(1, len(pts) - 1):
        p0, p1, p2 = pts[i - 1], pts[i], pts[i + 1]
        d1 = math.dist(p1, p0)
        d2 = math.dist(p1, p2)
        l1, l2 = min(r, d1 / 2), min(r, d2 / 2)
        a = (p1[0] + (p0[0] - p1[0]) / d1 * l1, p1[1] + (p0[1] - p1[1]) / d1 * l1)
        b = (p1[0] + (p2[0] - p1[0]) / d2 * l2, p1[1] + (p2[1] - p1[1]) / d2 * l2)
        cr.line_to(*a)
        k = 0.5523
        cr.curve_to(a[0] + (p1[0] - a[0]) * k, a[1] + (p1[1] - a[1]) * k,
                    b[0] + (p1[0] - b[0]) * k, b[1] + (p1[1] - b[1]) * k, *b)
    cr.line_to(*pts[-1])


def _glyph(cr, ch):
    """Squarish rounded monoline glyph in a GW x 1 box; the zero carries a diagonal slash."""
    W, r = GW, 0.16
    if ch == "0":
        rounded_rect(cr, 0, 0, W, 1, 0.17)
        cr.move_to(0.09, 0.14); cr.line_to(W - 0.09, 0.86)
    elif ch == "1":
        round_poly(cr, [(0.1, 0.2), (W / 2 + 0.03, 0), (W / 2 + 0.03, 1)], 0.02)
    elif ch == "2":
        round_poly(cr, [(0, 0.2), (0, 0), (W, 0), (W, 0.5), (0, 0.5), (0, 1), (W, 1)], r)
    elif ch == "3":
        round_poly(cr, [(0, 0), (W, 0), (W, 1), (0, 1)], r)
        cr.move_to(0.18, 0.5); cr.line_to(W, 0.5)
    elif ch == "4":
        round_poly(cr, [(0, 0), (0, 0.55), (W, 0.55)], 0.1)
        cr.move_to(W * 0.72, 0); cr.line_to(W * 0.72, 1)
    elif ch == "5":
        round_poly(cr, [(W, 0), (0, 0), (0, 0.5), (W, 0.5), (W, 1), (0, 1), (0, 0.82)], r)
    elif ch == "6":
        round_poly(cr, [(W, 0.2), (W, 0), (0, 0), (0, 1), (W, 1), (W, 0.5), (0, 0.5)], r)
    elif ch == "7":
        round_poly(cr, [(0, 0.2), (0, 0), (W, 0), (W, 1)], 0.14)
    elif ch == "8":
        rounded_rect(cr, 0.02, 0, W - 0.04, 0.5, 0.15)
        rounded_rect(cr, 0, 0.5, W, 0.5, 0.17)
    elif ch == "9":
        round_poly(cr, [(0, 0.8), (0, 1), (W, 1), (W, 0), (0, 0), (0, 0.5), (W, 0.5)], r)
    elif ch == "C":
        round_poly(cr, [(W, 0.2), (W, 0), (0, 0), (0, 1), (W, 1), (W, 0.8)], r)
    elif ch == "F":
        round_poly(cr, [(W, 0), (0, 0), (0, 1)], r)
        cr.move_to(0, 0.5); cr.line_to(W * 0.8, 0.5)
    elif ch == "-":
        cr.move_to(0.08, 0.5); cr.line_to(W - 0.08, 0.5)


ADVANCE = {":": 0.69, "°": 0.33, " ": 0.3, ".": 0.16}


def mono_text(cr, text, x, y, h, lw, rgba, draw=True):
    """Draw text in the thin monoline face (top-left at x, y; glyph height h). Returns its width."""
    cursor = 0.0
    for ch in text:
        if draw:
            cr.new_path()  # arcs would otherwise connect to whatever was drawn before
            cr.save()
            cr.translate(x + cursor * h, y)
            cr.scale(h, h)
            cr.set_source_rgba(*rgba)
            cr.set_line_width(lw / h)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.set_line_join(cairo.LINE_JOIN_ROUND)
            if ch == ":":  # two small square dots, sitting low
                for cy in (0.5, 0.9):
                    cr.rectangle(0.31, cy - 0.055, 0.11, 0.11)
                cr.fill()
            elif ch == "°":
                cr.arc(0.1, 0.13, 0.1, 0, 2 * math.pi); cr.stroke()
            elif ch == ".":
                cr.arc(0.05, 0.95, lw / h * 0.7, 0, 2 * math.pi); cr.fill()
            else:
                _glyph(cr, ch); cr.stroke()
            cr.restore()
        cursor += ADVANCE.get(ch, GW + 0.08)
    return cursor * h


def parse_minutes(hm):
    h, m = hm.split(":")
    return int(h) * 60 + int(m)


def sleep_layout(data):
    """Turn sleep.json into (window_start, window_end, [(start, end, stage)]) in minutes.
    Times may be 12- or 24-hour labels, so each one is moved forward until it follows the previous."""
    segs, prev = [], None
    for seg in data.get("segments", []):
        a, b = parse_minutes(seg["start"]), parse_minutes(seg["end"])
        while prev is not None and a < prev:
            a += 720
        while b < a:
            b += 720
        segs.append((a, b, seg.get("stage", ""), seg["start"]))
        prev = b
    total = re.match(r"\s*(\d+)\s*H\s*(\d+)?", str(data.get("total", "")), re.I)
    total_min = int(total.group(1)) * 60 + int(total.group(2) or 0) if total else 480
    if not segs:
        return 0, total_min, []
    span = segs[-1][1] - segs[0][0]
    pad = min(max((total_min - span) / 2, 15), 120)
    return segs[0][0] - pad, segs[-1][1] + pad, segs


class MonoCard(Tweened, Gtk.DrawingArea):
    """Info card in the mono / dot-matrix spirit: date, thin monoline time, sleep timeline, temperature and
    Bluetooth status. Every color comes from the theme palette."""

    W, H = 400, 390

    def __init__(self):
        super().__init__(content_width=self.W, content_height=self.H)
        self.opts = load_config()["mono"]
        self.temp = None
        self.sleep = None
        self.bt = {"powered": None, "connected": False, "headphones": False}
        self.set_draw_func(self.draw)

    # ---- theme
    def set_theme(self, tokens, opts):
        self.opts = opts
        acc, on = tokens["accent"], tokens["on_accent"]
        if contrast(luminance(acc), luminance(on)) < 4.5:  # nudge the text on the accent toward black or white
            on = max(("#000000", "#FFFFFF"), key=lambda c: contrast(luminance(acc), luminance(c)))
        rgba = lambda h, a=1.0: (*hex_to_rgb(h), a)
        self.animate_palette({"bg": rgba(tokens["bg"]), "text": rgba(tokens["text"]),
                              "secondary": rgba(tokens["secondary"]), "accent": rgba(acc), "on": rgba(on)})

    # ---- drawing helpers
    def text(self, cr, s, x, y, px, rgba, bold=False, align="left"):
        """Monospace text with its visual center at y."""
        layout = PangoCairo.create_layout(cr)
        desc = Pango.FontDescription(f"Space Mono, monospace {'Bold' if bold else ''}")
        desc.set_absolute_size(px * Pango.SCALE)
        layout.set_font_description(desc)
        layout.set_text(s, -1)
        w, h = layout.get_pixel_size()
        cr.new_path()
        cr.set_source_rgba(*rgba)
        cr.move_to(x - (w if align == "right" else w / 2 if align == "center" else 0), y - h / 2)
        PangoCairo.show_layout(cr, layout)
        return w

    def draw(self, _area, cr, w, h):
        if self.pal is None:
            return
        p, o, W, H = self.pal, self.opts, self.W, self.H
        pad = 42

        if o["shadow"]:
            for i in range(8, 0, -1):
                rounded_rect(cr, 3 - i, 10 - i, W - 6 + 2 * i, H - 6 + 2 * i, float(o["corner_radius"]) + i)
                cr.set_source_rgba(0, 0, 0, 0.04)
                cr.fill()
        rounded_rect(cr, 0, 0, W, H, float(o["corner_radius"]))
        cr.set_source_rgba(*p["bg"])
        cr.fill()

        now = datetime.now()
        self.text(cr, f"{now:%A} {now.day}", pad, 0.135 * H, 22, p["text"], bold=True)

        sleep_on = o["sleep"]["enabled"]
        th = 0.154 * H
        foot_c = 0.88 * H
        ty = 0.22 * H if sleep_on else (0.2 * H + foot_c - 0.06 * H - th) / 2
        lw = max(1.4, 0.026 * th)
        width = mono_text(cr, f"{now:%H:%M}", pad, ty, th, lw, p["text"])
        if o["show_seconds"]:
            mono_text(cr, f"{now:%S}", pad + width + 0.3 * th, ty, 0.38 * th, max(1.2, 0.026 * th), p["text"])

        if sleep_on:
            self.draw_sleep(cr, p, pad)

        temp = "--" if self.temp is None else f"{round(self.temp)}"
        fh = 0.059 * H
        mono_text(cr, f"{temp}°{o['weather']['unit'].upper()[:1]}", pad, foot_c - fh / 2, fh, max(1.3, 0.03 * fh), p["text"])

        x = W - pad
        icons = []
        if o["show_bluetooth"]:
            icons.append("bt")
        if o["show_headphones"] and self.bt["headphones"]:
            icons.append("hp")
        for icon in icons:  # right-aligned, drawn right to left
            cr.new_path()
            cr.save()
            cr.set_line_width(1.7)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.set_line_join(cairo.LINE_JOIN_ROUND)
            if icon == "bt":
                cr.translate(x - 6, foot_c)
                cr.set_source_rgba(*p["text"][:3], 1.0 if self.bt["powered"] else 0.35)
                cr.move_to(-4.5, -5); cr.line_to(4.5, 5); cr.line_to(0, 9.5); cr.line_to(0, -9.5); cr.line_to(4.5, -5); cr.line_to(-4.5, 5)
                cr.stroke()
                if self.bt["connected"]:
                    cr.arc(8, -8, 2.6, 0, 2 * math.pi)
                    cr.set_source_rgba(*p["accent"])
                    cr.fill()
                x -= 12 + 22
            else:  # headphones with sound waves above
                cr.translate(x - 11, foot_c + 2)
                cr.set_source_rgba(*p["text"])
                cr.arc(0, 2, 9, math.pi, 2 * math.pi); cr.stroke()
                for ex in (-11.5, 6.5):
                    rounded_rect(cr, ex, 2, 5, 9, 2.5); cr.stroke()
                for rad in (13, 17):
                    cr.arc(0, 2, rad, math.radians(240), math.radians(300)); cr.stroke()
                x -= 22 + 22
            cr.restore()

    def draw_sleep(self, cr, p, pad):
        W, H = self.W, self.H
        data = self.sleep
        c = 0.515 * H
        self.text(cr, "Sleep", pad, c, 13, p["text"], bold=True)
        pill = (data or {}).get("total", "NO DATA").upper()
        pw = 14 + 8 * len(pill)
        rounded_rect(cr, W - pad - pw, c - 10, pw, 20, 7)
        cr.set_source_rgba(*p["accent"])
        cr.fill()
        self.text(cr, pill, W - pad - pw / 2, c, 12, p["on"], bold=True, align="center")

        inset = 31
        px0, py, pwid, ph = inset, 0.573 * H, W - 2 * inset, 0.184 * H
        rounded_rect(cr, px0, py, pwid, ph, 0.34 * ph)
        cr.set_source_rgba(*p["accent"])
        cr.fill()

        w0, w1, segs = sleep_layout(data or {})
        span = max(w1 - w0, 1)
        x0, x1 = px0 + 15, px0 + pwid - 15
        to_x = lambda t: x0 + (t - w0) / span * (x1 - x0)
        row = py + 0.50 * ph
        cr.new_path()
        cr.set_line_width(1.1)
        cr.set_line_cap(cairo.LINE_CAP_BUTT)
        cr.set_source_rgba(*p["on"][:3], 0.6)
        for i in range(int(span // 5) + 1):
            x = to_x(w0 + i * 5)
            cr.move_to(x, row - 4.5)
            cr.line_to(x, row + 4.5)
        cr.stroke()
        for a, b, stage, start in segs:  # thick bars mark the stages on top of the ticks
            xa, xb = to_x(a), to_x(b)
            rounded_rect(cr, xa, row - 2.5, max(xb - xa, 5), 5, 2.5)
            cr.set_source_rgba(*p["on"])
            cr.fill()
            self.text(cr, start, xa, py + 0.30 * ph, 12, p["on"], bold=True)
            self.text(cr, stage, xa, py + 0.73 * ph, 12, p["on"], bold=True)


class MonoCardWidget(Widget):
    """Mono card: date, time, temperature, Bluetooth status and an optional sleep timeline."""

    kind, label = "mono_card", "Mono Card"
    width = 456

    def apply_style(self, style):
        self.card.add_css_class("plain")
        self.card.add_css_class("mono")

    def __init__(self, app, item):
        super().__init__(app, item)
        self.face = MonoCard()
        self.card.append(self.face)
        self.refresh_theme()
        self.refresh_sleep()
        self.refresh_bluetooth()
        self.refresh_weather()
        self.every(1000, lambda: self.face.queue_draw() or True)
        self.every(30000, self.refresh_theme)
        self.every(60000, self.refresh_sleep)
        self.every(5000, self.refresh_bluetooth)
        self.every(900000, self.refresh_weather)  # 15 minutes

    def refresh_theme(self):
        cfg = load_config()
        tokens = resolve_tokens(cfg["theme"], fixed=(MONO_DARK, MONO_LIGHT))
        self.face.set_theme(tokens, cfg["mono"])
        self.refresh_weather(cached=True)
        return True

    def refresh_sleep(self):
        for path in (SLEEP_FILE, CONFIG_DIR / "sleep.json"):
            data = load_json(path, None)
            if data:
                self.face.sleep = data
                break
        else:
            self.face.sleep = None
        return True

    def refresh_bluetooth(self):
        def done(conn, result):
            try:
                objects = conn.call_finish(result).unpack()[0]
            except GLib.Error:
                self.face.bt = {"powered": None, "connected": False, "headphones": False}
                return
            powered = any(i.get("org.bluez.Adapter1", {}).get("Powered") for i in objects.values()
                          if "org.bluez.Adapter1" in i)
            devices = [i["org.bluez.Device1"] for i in objects.values()
                       if i.get("org.bluez.Device1", {}).get("Connected")]
            self.face.bt = {"powered": powered, "connected": bool(devices),
                            "headphones": any(d.get("Icon") in AUDIO_ICONS for d in devices)}

        def got_bus(_source, result):
            try:
                conn = Gio.bus_get_finish(result)
            except GLib.Error:
                return
            conn.call("org.bluez", "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects", None,
                      GLib.VariantType("(a{oa{sa{sv}}})"), Gio.DBusCallFlags.NONE, 2000, None, done)
        Gio.bus_get(Gio.BusType.SYSTEM, None, got_bus)
        return True

    def refresh_weather(self, cached=False):
        w = load_config()["mono"]["weather"]
        key = [w["lat"], w["lon"], w["unit"]]
        cache = load_json(WEATHER_FILE, {})
        if cache.get("key") == key and cache.get("temp") is not None:
            self.face.temp = cache["temp"]
            if cached or GLib.get_real_time() / 1e6 - cache.get("time", 0) < 840:
                return True

        def fetch():
            unit = "fahrenheit" if w["unit"].upper().startswith("F") else "celsius"
            url = ("https://api.open-meteo.com/v1/forecast?current=temperature_2m"
                   f"&latitude={w['lat']}&longitude={w['lon']}&temperature_unit={unit}")
            try:
                with urllib.request.urlopen(url, timeout=10) as r:
                    temp = json.load(r)["current"]["temperature_2m"]
            except (OSError, ValueError, KeyError):
                return
            save_json(WEATHER_FILE, {"key": key, "temp": temp, "time": GLib.get_real_time() / 1e6})
            GLib.idle_add(lambda: setattr(self.face, "temp", temp) or False)
        threading.Thread(target=fetch, daemon=True).start()
        return True


class AnalogClockWidget(Widget):
    shape = "round"
    width = 300

    def __init__(self, app, item):
        super().__init__(app, item)
        self.face = AnalogFace(self.shape)
        self.card.set_halign(Gtk.Align.CENTER)
        self.card.append(self.face)
        self.every(1000, lambda: self.face.queue_draw() or True)


class RoundClockWidget(AnalogClockWidget):
    kind, label, shape = "clock_round", "Round Clock", "round"


class SquareClockWidget(AnalogClockWidget):
    kind, label, shape = "clock_square", "Square Clock", "square"


class MaterialMixin:
    """Shared Material You theming for the Pixel widgets: card colors follow the theme config."""

    @property
    def mat(self):
        return f"mat-{self.item['id']}"

    def apply_style(self, style):
        # The look comes from the theme, not the style picker; only "No background" is honoured.
        self.card.remove_css_class("plain")
        self.card.add_css_class(self.mat)
        if style == "plain":
            self.card.add_css_class("plain")

    def setup_material(self):
        self.css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        self.connect("destroy", lambda *_: Gtk.StyleContext.remove_provider_for_display(
            Gdk.Display.get_default(), self.css))
        self.refresh_theme()
        self.every(30000, self.refresh_theme)  # follow wallpaper / system changes

    def refresh_theme(self):
        cfg = load_config()
        t = resolve_tokens(cfg["theme"])
        self.on_tokens(t, cfg)
        m = self.mat
        self.css.load_from_string(f"""
            .card.{m} {{ background: {t['bg']}; color: {t['text']}; border: 1px solid {t['outline']};
                        border-radius: 36px; box-shadow: 0 10px 30px alpha(black, 0.35); }}
            .card.{m}.plain {{ background: none; border-color: transparent; box-shadow: none; }}
            .card.{m} .date {{ color: {t['secondary']}; opacity: 1; font-size: 14px; }}
            .card.{m} .hours {{ font-size: 92px; font-weight: 300; letter-spacing: -3px; color: {t['text']}; }}
            .card.{m} .mins {{ font-size: 92px; font-weight: 300; letter-spacing: -3px; color: {t['accent']}; }}""")
        return True


class PixelClockWidget(MaterialMixin, AnalogClockWidget):
    """Material You analog clock (scalloped face), themed dark/adaptive/auto."""

    kind, label, shape = "clock_pixel", "Pixel Analog", "pixel"

    def __init__(self, app, item):
        super().__init__(app, item)
        self.setup_material()

    def on_tokens(self, tokens, cfg):
        self.face.set_theme(tokens, frame=cfg["pixel_face"] == "frame")


class PixelDigitalClockWidget(MaterialMixin, Widget):
    """Material You digital clock: stacked hours and minutes, minutes in the accent color."""

    kind, label = "clock_pixel_digital", "Pixel Digital"

    def __init__(self, app, item):
        super().__init__(app, item)
        self.hours = Gtk.Label(xalign=0, css_classes=["hours"])
        self.mins = Gtk.Label(xalign=0, css_classes=["mins"])
        self.date = Gtk.Label(xalign=0, css_classes=["date"], margin_top=6)
        for w in (self.hours, self.mins, self.date):
            self.card.append(w)
        self.card.set_spacing(0)
        self.tick()
        self.every(1000, self.tick)
        self.setup_material()

    def tick(self):
        now = datetime.now()
        self.hours.set_text(now.strftime("%H"))
        self.mins.set_text(now.strftime("%M"))
        self.date.set_text(now.strftime("%A, %d %B"))
        return True

    def on_tokens(self, tokens, cfg):
        pass  # colors are applied through the CSS in MaterialMixin


class Spark(Gtk.DrawingArea):
    """Filled sparkline in the card's text color over a rolling window of values."""

    def __init__(self, auto_scale=False):
        super().__init__(content_height=44, hexpand=True)
        self.auto = auto_scale
        self.data = collections.deque([0.0] * HISTORY, maxlen=HISTORY)
        self.set_draw_func(self.draw)

    def push(self, v):
        self.data.append(v)
        self.queue_draw()

    def draw(self, _area, cr, w, h):
        c = self.get_color()
        top = max(max(self.data), 1.0) if self.auto else 1.0
        step = w / (HISTORY - 1)
        pts = [(i * step, h - 2 - (v / top) * (h - 4)) for i, v in enumerate(self.data)]
        cr.move_to(0, h)
        for x, y in pts:
            cr.line_to(x, y)
        cr.line_to(w, h)
        cr.close_path()
        cr.set_source_rgba(c.red, c.green, c.blue, 0.16)
        cr.fill()
        cr.move_to(*pts[0])
        for x, y in pts[1:]:
            cr.line_to(x, y)
        cr.set_source_rgba(c.red, c.green, c.blue, 0.95)
        cr.set_line_width(1.8)
        cr.stroke()


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}/s" if unit == "B" else f"{n:.1f} {unit}/s"
        n /= 1024
    return f"{n:.1f} TB/s"


class SystemWidget(Widget):
    kind, label = "system", "System"

    def __init__(self, app, item):
        super().__init__(app, item)
        self.rows = {}
        for key, title, auto in (("cpu", "CPU", False), ("mem", "MEMORY", False),
                                 ("down", "DOWNLOAD", True), ("up", "UPLOAD", True)):
            head = Gtk.Box()
            head.append(Gtk.Label(label=title, xalign=0, hexpand=True, css_classes=["label"]))
            val = Gtk.Label(xalign=1, css_classes=["value"])
            head.append(val)
            spark = Spark(auto)
            self.card.append(head)
            self.card.append(spark)
            self.rows[key] = (val, spark)
        self.last_net = psutil.net_io_counters()
        psutil.cpu_percent()
        self.every(1000, self.tick)

    def tick(self):
        cpu = psutil.cpu_percent()
        mem = psutil.virtual_memory().percent
        net = psutil.net_io_counters()
        down, up = net.bytes_recv - self.last_net.bytes_recv, net.bytes_sent - self.last_net.bytes_sent
        self.last_net = net
        for key, text, v in (("cpu", f"{cpu:.0f}%", cpu / 100), ("mem", f"{mem:.0f}%", mem / 100),
                             ("down", human(down), down), ("up", human(up), up)):
            label, spark = self.rows[key]
            label.set_text(text)
            spark.push(v)
        return True


class MediaWidget(Widget):
    kind, label = "media", "Media"

    def __init__(self, app, item):
        super().__init__(app, item)
        self.title = Gtk.Label(xalign=0, ellipsize=3, css_classes=["track"])
        self.artist = Gtk.Label(xalign=0, ellipsize=3, css_classes=["artist"])
        self.bar = Gtk.ProgressBar()
        self.card.append(Gtk.Label(label="NOW PLAYING", xalign=0, css_classes=["label"]))
        self.card.append(self.title)
        self.card.append(self.artist)
        self.card.append(self.bar)

        controls = Gtk.Box(halign=Gtk.Align.CENTER, spacing=10)
        self.play = Gtk.Button(icon_name="media-playback-start-symbolic", css_classes=["flat", "circular"])
        for icon, cmd in (("media-skip-backward-symbolic", "previous"), (None, "play_pause"),
                          ("media-skip-forward-symbolic", "next")):
            btn = self.play if icon is None else Gtk.Button(icon_name=icon, css_classes=["flat", "circular"])
            btn.connect("clicked", lambda _b, c=cmd: self.control(c))
            controls.append(btn)
        self.card.append(controls)

        self.manager = Playerctl.PlayerManager()
        self.manager.connect("name-appeared", self.on_name)
        self.player = None
        for name in self.manager.props.player_names:
            self.on_name(self.manager, name)
        self.refresh()
        self.every(1000, self.refresh)

    def on_name(self, _mgr, name):
        if self.player is None:
            self.player = Playerctl.Player.new_from_name(name)

    def control(self, cmd):
        if self.player:
            getattr(self.player, cmd)()

    def refresh(self):
        p = self.player
        if p is not None and p.props.player_name not in [n.name for n in self.manager.props.player_names]:
            self.player = p = None
        if p is None:
            self.title.set_text("Nothing playing")
            self.artist.set_text("Start a player to see it here")
            self.bar.set_fraction(0)
            return True
        self.title.set_text(p.get_title() or "Unknown")
        self.artist.set_text(p.get_artist() or "")
        playing = p.props.playback_status == Playerctl.PlaybackStatus.PLAYING
        self.play.set_icon_name("media-playback-pause-symbolic" if playing else "media-playback-start-symbolic")
        try:
            length = p.props.metadata.lookup_value("mpris:length")
            length = length.get_int64() if length else 0
            self.bar.set_fraction(min(p.props.position / length, 1) if length else 0)
        except GLib.Error:
            self.bar.set_fraction(0)
        return True


class NotesWidget(Widget):
    kind, label = "notes", "Notes"

    def __init__(self, app, item):
        super().__init__(app, item)
        self.file = DATA_DIR / f"notes-{item['id']}.txt"
        self.card.append(Gtk.Label(label="QUICK NOTES", xalign=0, css_classes=["label"]))
        self.view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=4, bottom_margin=4)
        self.card.append(Gtk.ScrolledWindow(child=self.view, min_content_height=160, hexpand=True))
        self.buf = self.view.get_buffer()
        if self.file.exists():
            self.buf.set_text(self.file.read_text())
        self.pending = 0
        self.buf.connect("changed", self.on_changed)
        self.connect("close-request", lambda *_: self.flush() or False)

    def on_changed(self, *_):
        if not self.pending:
            self.pending = GLib.timeout_add_seconds(1, self.save)

    def flush(self):
        if self.pending:
            GLib.source_remove(self.pending)
            self.save()

    def save(self):
        self.pending = 0
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(self.buf.get_text(self.buf.get_start_iter(), self.buf.get_end_iter(), False))
        return False

    def cleanup(self):
        self.flush()


WIDGETS = {cls.kind: cls for cls in (ClockWidget, RoundClockWidget, SquareClockWidget, PixelClockWidget,
                                       PixelDigitalClockWidget, WarpedClockWidget, MonoCardWidget, SystemWidget, MediaWidget, NotesWidget)}
CATALOG = (  # kind, icon, description
    ("clock", "preferences-system-time-symbolic", "Time, date and a month calendar"),
    ("clock_round", "preferences-system-time-symbolic", "Classic analog clock with a round face"),
    ("clock_square", "preferences-system-time-symbolic", "Analog clock with a rounded-square face"),
    ("clock_pixel", "preferences-system-time-symbolic", "Material You analog clock with a scalloped face"),
    ("clock_pixel_digital", "preferences-system-time-symbolic", "Material You digital clock, stacked hours and minutes"),
    ("clock_warped", "preferences-system-time-symbolic", "Melting numerals on a tilted oval, ink only, no card"),
    ("mono_card", "preferences-system-time-symbolic", "Black info card: date, big thin time, sleep timeline, temperature"),
    ("system", "utilities-system-monitor-symbolic", "Live CPU, memory and network graphs"),
    ("media", "multimedia-player-symbolic", "Now playing with play, pause and skip"),
    ("notes", "document-edit-symbolic", "A scratchpad that saves as you type"),
)


MODE_HELP = {"dark": "A fixed dark palette.",
             "adaptive": "Material You colors generated from your wallpaper.",
             "auto": "Follows the system light or dark setting."}
MATERIAL_KINDS = {"clock_pixel", "clock_pixel_digital", "clock_warped", "mono_card"}
STYLELESS = {"clock_warped": "Ink only", "mono_card": "Theme colors"}
ICONS = {kind: icon for kind, icon, _ in CATALOG}
PAGES = (("gallery", "Gallery", "view-grid-symbolic"),
         ("desktop", "On Desktop", "user-desktop-symbolic"),
         ("appearance", "Appearance", "preferences-desktop-appearance-symbolic"))


SECTIONS = (("Clocks", ("clock", "clock_round", "clock_square", "clock_pixel", "clock_pixel_digital", "clock_warped")),
            ("Info cards", ("mono_card", "system", "media")),
            ("Tools", ("notes",)))


def build_preview(kind, style):
    """Small live mock of a widget for the gallery. Returns (widget, card to restyle or None)."""
    cfg = load_config()

    def card(*children, width=230, extra=()):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, width_request=width,
                      css_classes=["card", *extra])
        for child in children:
            box.append(child)
        return box

    label = lambda text, *classes: Gtk.Label(label=text, xalign=0, css_classes=list(classes))
    if kind == "clock":
        box = card(label("10:42", "time"), label("Tuesday, 29 September", "date"))
        return Scaler(box, 0.72), (box.add_css_class(style) or box)
    if kind in ("clock_round", "clock_square"):
        box = card(AnalogFace(kind.split("_")[1]), width=244)
        box.set_halign(Gtk.Align.CENTER)
        return Scaler(box, 0.6), (box.add_css_class(style) or box)
    if kind == "clock_pixel":
        face = AnalogFace("pixel")
        return Scaler(card(face, width=244, extra=("tile-material",)), 0.6), None
    if kind == "clock_pixel_digital":
        box = card(label("10", "pv-hours"), label("42", "pv-mins"), width=170, extra=("tile-material",))
        return Scaler(box, 0.5), None
    if kind == "clock_warped":
        face = WarpedFace()
        face.set_theme(resolve_tokens(cfg["theme"]), None, cfg["warped"])
        return Scaler(face, 0.55), None
    if kind == "mono_card":
        face = MonoCard()
        face.temp, face.bt = 25.0, {"powered": True, "connected": True, "headphones": True}
        face.sleep = {"total": "8H 32M", "segments": [{"start": "10:32", "end": "11:30", "stage": "REM"},
                                                     {"start": "03:32", "end": "04:30", "stage": "REM"}]}
        face.set_theme(MONO_DARK, cfg["mono"])
        return Scaler(face, 0.4), None
    if kind == "system":
        rows = []
        for name, value in (("CPU", "38%"), ("MEMORY", "61%"), ("DOWNLOAD", "1.2 MB/s")):
            head = Gtk.Box()
            head.append(Gtk.Label(label=name, xalign=0, hexpand=True, css_classes=["label"]))
            head.append(Gtk.Label(label=value, xalign=1, css_classes=["value"]))
            spark = Spark(False)
            for i in range(HISTORY):
                spark.data.append(0.5 + 0.35 * math.sin(i / 6 + len(rows)))
            rows += [head, spark]
        box = card(*rows)
        return Scaler(box, 0.62), (box.add_css_class(style) or box)
    if kind == "media":
        bar = Gtk.ProgressBar(fraction=0.4)
        box = card(label("NOW PLAYING", "label"), label("Midnight City", "track"), label("M83", "artist"), bar)
        return Scaler(box, 0.8), (box.add_css_class(style) or box)
    box = card(label("QUICK NOTES", "label"),
               Gtk.Label(label="Ship v2\nCall mom\nBuy milk", xalign=0, css_classes=["value"]))
    return Scaler(box, 0.8), (box.add_css_class(style) or box)


class Manager(Adw.ApplicationWindow):
    """Gallery to browse and add widgets, a list to manage them, and theme settings."""

    def __init__(self, app):
        super().__init__(application=app, title="Widgits", default_width=960, default_height=700)
        self.set_size_request(360, 400)
        self.app = app
        self.style = "glass"
        self.tiles = []
        self.sizes = {}

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=180)
        self.stack.add_named(self.build_gallery(), "gallery")
        self.stack.add_named(self.build_desktop(), "desktop")
        self.stack.add_named(self.build_appearance(), "appearance")

        self.split = Adw.NavigationSplitView(min_sidebar_width=210, max_sidebar_width=250)
        self.split.set_sidebar(Adw.NavigationPage(title="Widgits", child=self.build_sidebar()))
        content = Adw.ToolbarView(content=self.stack)
        content.add_top_bar(Adw.HeaderBar())
        self.content_page = Adw.NavigationPage(title="Gallery", child=content)
        self.split.set_content(self.content_page)

        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 680sp"))
        narrow.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(narrow)

        self.toasts = Adw.ToastOverlay(child=self.split)
        self.set_content(self.toasts)
        self.sidebar.select_row(self.sidebar.get_row_at_index(0))
        self.refresh()

    # ---- sidebar
    def build_sidebar(self):
        self.sidebar = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.badge = Gtk.Label(css_classes=["badge"], visible=False)
        for pid, label, icon in PAGES:
            box = Gtk.Box(spacing=12, margin_top=6, margin_bottom=6)
            box.append(Gtk.Image(icon_name=icon))
            box.append(Gtk.Label(label=label, xalign=0, hexpand=True))
            if pid == "desktop":
                box.append(self.badge)
            row = Gtk.ListBoxRow(child=box)
            row.page = (pid, label)
            self.sidebar.append(row)
        self.sidebar.connect("row-selected", lambda _l, row: row and self.show_page(*row.page))
        self.sidebar.connect("row-activated", lambda *_: self.split.set_show_content(True))
        view = Adw.ToolbarView(content=self.sidebar)
        view.add_top_bar(Adw.HeaderBar())
        return view

    def show_page(self, pid, label):
        self.stack.set_visible_child_name(pid)
        self.content_page.set_title(label)

    def go_to(self, pid):
        index = [p[0] for p in PAGES].index(pid)
        self.sidebar.select_row(self.sidebar.get_row_at_index(index))
        self.split.set_show_content(True)

    # ---- gallery
    def build_gallery(self):
        stage = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, css_classes=["stage"])
        stage.append(Gtk.Label(label="STYLE", xalign=0, css_classes=["stage-title"]))
        stage.append(Gtk.Label(label="How the next widget you add will look. Self-styled widgets follow the theme.",
                               xalign=0, wrap=True, css_classes=["stage-sub"]))
        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.SINGLE, max_children_per_line=7,
                                min_children_per_line=2, homogeneous=False, row_spacing=2, column_spacing=2)
        for sid, name in STYLES.items():
            chip = Gtk.Box(css_classes=["card", "preview", sid], halign=Gtk.Align.CENTER)
            chip.append(Gtk.Label(label=name))
            holder = Gtk.Box(margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
            holder.append(chip)
            holder.style_id = sid
            holder.update_property([Gtk.AccessibleProperty.LABEL], [f"{name} style"])
            self.flow.append(holder)
        self.flow.select_child(self.flow.get_child_at_index(0))
        self.flow.connect("child-activated", lambda _f, child: self.set_style(child.get_child().style_id))
        stage.append(self.flow)

        info = {kind: (icon, desc) for kind, icon, desc in CATALOG}
        for title, kinds in SECTIONS:
            stage.append(Gtk.Label(label=title.upper(), xalign=0, css_classes=["stage-section"], margin_top=14))
            grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4, min_children_per_line=1,
                               homogeneous=True, row_spacing=6, column_spacing=6)
            for kind in kinds:
                grid.append(self.build_tile(kind, *info[kind]))
            stage.append(grid)
        return Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                  child=Adw.Clamp(child=stage, maximum_size=1040, margin_top=18, margin_bottom=24,
                                                  margin_start=18, margin_end=18))

    def build_tile(self, kind, icon, desc):
        label = WIDGETS[kind].label
        shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, css_classes=["tile-shell"], width_request=200)
        preview, restyle = build_preview(kind, self.style)
        if restyle is not None:
            self.tiles.append(restyle)
        holder = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, height_request=160, overflow=Gtk.Overflow.HIDDEN)
        preview.set_halign(Gtk.Align.CENTER)
        preview.set_valign(Gtk.Align.CENTER)
        holder.append(preview)
        shell.append(holder)
        shell.append(Gtk.Label(label=label, xalign=0, css_classes=["tile-name"], margin_top=4))
        shell.append(Gtk.Label(label=desc, xalign=0, wrap=True, max_width_chars=20, css_classes=["tile-desc"], vexpand=True, yalign=0))
        button = Gtk.Button(css_classes=["tile-add"], halign=Gtk.Align.START, margin_top=6)
        inner = Gtk.Box(spacing=6)
        inner.append(Gtk.Image(icon_name="list-add-symbolic"))
        inner.append(Gtk.Label(label="Add"))
        button.set_child(inner)
        button.update_property([Gtk.AccessibleProperty.LABEL], [f"Add {label} to desktop"])
        button.connect("clicked", lambda _b: self.add(kind))
        shell.append(button)
        outer = Gtk.Box(margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
        outer.append(shell)
        return outer

    def set_style(self, style):
        for tile in self.tiles:
            tile.remove_css_class(self.style)
            tile.add_css_class(style)
        self.style = style

    def add(self, kind):
        self.app.add_widget(kind, self.style)
        toast = Adw.Toast(title=f"{WIDGETS[kind].label} added to your desktop", button_label="View", timeout=4)
        toast.connect("button-clicked", lambda _t: self.go_to("desktop"))
        self.toasts.add_toast(toast)

    # ---- on desktop
    def build_desktop(self):
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        listing = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                     child=Adw.Clamp(child=self.list, maximum_size=720, margin_top=18,
                                                     margin_bottom=24, margin_start=18, margin_end=18, valign=Gtk.Align.START))
        empty = Adw.StatusPage(icon_name="user-desktop-symbolic", title="Your desktop is empty",
                               description="Widgets you add show up here, where you can restyle or remove them.")
        browse = Gtk.Button(label="Browse the gallery", halign=Gtk.Align.CENTER, css_classes=["suggested-action", "pill"])
        browse.connect("clicked", lambda _b: self.go_to("gallery"))
        empty.set_child(browse)
        self.desktop_stack = Gtk.Stack()
        self.desktop_stack.add_named(empty, "empty")
        self.desktop_stack.add_named(listing, "list")
        return self.desktop_stack

    def refresh(self):
        self.sizes = {}
        while row := self.list.get_first_child():
            self.list.remove(row)
        items = self.app.state["items"]
        for item in items:
            self.list.append(self.build_row(item))
        self.desktop_stack.set_visible_child_name("list" if items else "empty")
        self.badge.set_visible(bool(items))
        self.badge.set_text(str(len(items)))

    def build_row(self, item):
        cls = WIDGETS[item["type"]]
        name = f"{cls.label} {item['id']}"
        row = Adw.ExpanderRow(title=name)
        row.add_prefix(Gtk.Image(icon_name=ICONS[item["type"]]))
        material = item["type"] in MATERIAL_KINDS
        row.set_subtitle(self.summary(item))

        if item["type"] not in STYLELESS:  # these widgets draw their own look from the theme
            if material:
                options = [("glass", "Themed card"), ("plain", "No background")]
            else:
                options = list(STYLES.items())
            ids = [o[0] for o in options]
            current = item["style"] if item["style"] in ids else ids[0]
            style = Adw.ComboRow(title="Style", model=Gtk.StringList.new([o[1] for o in options]),
                                 selected=ids.index(current))
            if material:
                style.set_subtitle("Colors follow the Appearance theme")

            def restyle(r, _p):
                self.app.set_style(item["id"], ids[r.get_selected()])
                row.set_subtitle(self.summary(item))
            style.connect("notify::selected", restyle)
            row.add_row(style)

        size = Adw.ActionRow(title="Size", subtitle="Ctrl + scroll over a widget also resizes it")
        value = Gtk.Label(label=f"{round(item.get('scale', 1.0) * 100)}%", width_chars=5, xalign=1,
                          css_classes=["numeric"])
        slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, SCALE_MIN * 100, SCALE_MAX * 100, 5)
        slider.set_value(item.get("scale", 1.0) * 100)
        slider.set_size_request(180, -1)
        slider.set_valign(Gtk.Align.CENTER)
        slider.update_property([Gtk.AccessibleProperty.LABEL], [f"Size of {name}"])
        slider.connect("value-changed", lambda sc: self.app.set_scale(item["id"], sc.get_value() / 100))
        reset = Gtk.Button(icon_name="edit-undo-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                           tooltip_text="Reset to 100%")
        reset.update_property([Gtk.AccessibleProperty.LABEL], [f"Reset size of {name}"])
        reset.connect("clicked", lambda _b: slider.set_value(100))
        for w in (slider, value, reset):
            size.add_suffix(w)
        row.add_row(size)
        self.sizes[item["id"]] = (slider, value, row, item)

        trash = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                           tooltip_text="Remove from desktop", width_request=44, height_request=44)
        trash.update_property([Gtk.AccessibleProperty.LABEL], [f"Remove {name}"])
        trash.connect("clicked", lambda _b: self.app.remove_widget(item["id"]))
        row.add_suffix(trash)
        return row

    def summary(self, item):
        if item["type"] in STYLELESS:
            style = STYLELESS[item["type"]]
        else:
            style = "Themed" if item["type"] in MATERIAL_KINDS and item["style"] != "plain" else \
                STYLES.get(item["style"], "No background")
        return f"{style} · {round(item.get('scale', 1.0) * 100)}%"

    def sync_size(self, item_id, scale):
        """Keep the manager's slider and labels in step when a widget is resized from the desktop."""
        entry = self.sizes.get(item_id)
        if not entry:
            return
        slider, value, row, item = entry
        value.set_text(f"{round(scale * 100)}%")
        row.set_subtitle(self.summary(item))
        if abs(slider.get_value() - scale * 100) > 0.5:
            slider.set_value(scale * 100)

    def offer_undo(self, item):
        undone = []
        toast = Adw.Toast(title=f"Removed {WIDGETS[item['type']].label} {item['id']}", button_label="Undo", timeout=6)

        def undo(_t):
            undone.append(True)
            self.app.restore_widget(item)
        toast.connect("button-clicked", undo)
        toast.connect("dismissed", lambda _t: undone or self.app.purge(item))
        self.toasts.add_toast(toast)

    # ---- appearance
    def build_appearance(self):
        cfg = load_config()
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(title="Pixel widgets theme",
                                     description="Applies to Pixel Analog and Pixel Digital. Other styles are set per widget.")
        modes = list(THEME_MODES)
        mode = Adw.ComboRow(title="Theme", model=Gtk.StringList.new(list(THEME_MODES.values())),
                            selected=modes.index(cfg["theme"]["mode"]), subtitle=MODE_HELP[cfg["theme"]["mode"]])

        def on_mode(row, _p):
            chosen = modes[row.get_selected()]
            row.set_subtitle(MODE_HELP[chosen])
            self.app.update_config(lambda c: c.setdefault("theme", {}).update(mode=chosen))
        mode.connect("notify::selected", on_mode)
        group.add(mode)

        variants = list(VARIANTS)
        variant = Adw.ComboRow(title="Color variant", subtitle="How Adaptive tints the palette",
                               model=Gtk.StringList.new(list(VARIANTS.values())),
                               selected=variants.index(cfg["theme"]["variant"]) if cfg["theme"]["variant"] in variants else 0)
        variant.connect("notify::selected", lambda r, _p: self.app.update_config(
            lambda c: c.setdefault("theme", {}).update(variant=variants[r.get_selected()])))
        group.add(variant)

        seed = Adw.EntryRow(title="Seed color (optional, e.g. #E8622A)", text=cfg["theme"]["seed"] or "",
                            show_apply_button=True)

        def on_seed(row):
            text = row.get_text().strip()
            if text and not re.fullmatch(r"#[0-9A-Fa-f]{6}", text):
                row.add_css_class("error")
                self.toasts.add_toast(Adw.Toast(title="Use a color like #E8622A"))
                return
            row.remove_css_class("error")
            self.app.update_config(lambda c: c.setdefault("theme", {}).update(seed=text or None))
        seed.connect("apply", on_seed)
        group.add(seed)

        face = Adw.ComboRow(title="Analog face", subtitle="Tonal fill or an outline only",
                            model=Gtk.StringList.new(["Filled", "Frame outline"]),
                            selected=1 if cfg["pixel_face"] == "frame" else 0)
        face.connect("notify::selected", lambda r, _p: self.app.update_config(
            lambda c: c.update(pixel_face="frame" if r.get_selected() else "filled")))
        group.add(face)
        page.add(group)

        wc = cfg["warped"]
        warped = Adw.PreferencesGroup(title="Warped clock",
                                      description="Uses the theme above. Ink flips or gains a halo if the wallpaper makes it hard to read.")

        def set_warped(**kw):
            self.app.update_config(lambda c: c.setdefault("warped", {}).update(kw))

        backing = Adw.ComboRow(title="Backing", model=Gtk.StringList.new(["None", "Plate"]),
                               selected=1 if wc["backing"] == "plate" else 0)
        backing.connect("notify::selected", lambda r, _p: set_warped(backing="plate" if r.get_selected() else "none"))
        warped.add(backing)
        accent = Adw.SwitchRow(title="Accent hour hand", subtitle="Tip in the theme's primary color", active=wc["accent_hands"])
        accent.connect("notify::active", lambda r, _p: set_warped(accent_hands=r.get_active()))
        warped.add(accent)
        for title, key, low, high, step, scale_by in (("Distortion", "distortion", 0, 100, 5, 100),
                                                      ("Tilt", "tilt", -30, 30, 1, 1)):
            row = Adw.ActionRow(title=title)
            value = Gtk.Label(width_chars=5, xalign=1, css_classes=["numeric"], label=f"{round(wc[key] * scale_by)}")
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, low, high, step)
            scale.set_value(wc[key] * scale_by)
            scale.set_size_request(180, -1)
            scale.set_valign(Gtk.Align.CENTER)
            scale.update_property([Gtk.AccessibleProperty.LABEL], [f"Warped clock {title.lower()}"])

            def changed(sc, key=key, value=value, scale_by=scale_by):
                value.set_text(f"{round(sc.get_value())}")
                set_warped(**{key: sc.get_value() / scale_by})
            scale.connect("value-changed", changed)
            row.add_suffix(scale)
            row.add_suffix(value)
            warped.add(row)
        micro = Adw.EntryRow(title="Small text above the hands", text=wc["micro_text"], show_apply_button=True)
        micro.connect("apply", lambda r: set_warped(micro_text=r.get_text()))
        warped.add(micro)
        page.add(warped)

        mc = cfg["mono"]
        mono = Adw.PreferencesGroup(title="Mono card",
                                    description="Sleep data is read from ~/.config/clockwidget/sleep.json. "
                                                "Temperature comes from Open-Meteo.")

        def set_mono(**kw):
            self.app.update_config(lambda c: c.setdefault("mono", {}).update(kw))

        def set_nested(section, **kw):
            self.app.update_config(lambda c: c.setdefault("mono", {}).setdefault(section, {}).update(kw))

        for title, active, apply in (("Sleep block", mc["sleep"]["enabled"], lambda v: set_nested("sleep", enabled=v)),
                                     ("Seconds", mc["show_seconds"], lambda v: set_mono(show_seconds=v)),
                                     ("Bluetooth icon", mc["show_bluetooth"], lambda v: set_mono(show_bluetooth=v)),
                                     ("Headphones icon", mc["show_headphones"], lambda v: set_mono(show_headphones=v))):
            row = Adw.SwitchRow(title=title, active=active)
            row.connect("notify::active", lambda r, _p, apply=apply: apply(r.get_active()))
            mono.add(row)
        for title, key in (("Latitude", "lat"), ("Longitude", "lon")):
            entry = Adw.EntryRow(title=title, text=str(mc["weather"][key]), show_apply_button=True)

            def apply_coord(r, key=key):
                try:
                    set_nested("weather", **{key: float(r.get_text())})
                    r.remove_css_class("error")
                except ValueError:
                    r.add_css_class("error")
                    self.toasts.add_toast(Adw.Toast(title="Enter a number, like 15.49"))
            entry.connect("apply", apply_coord)
            mono.add(entry)
        unit = Adw.ComboRow(title="Temperature unit", model=Gtk.StringList.new(["Celsius", "Fahrenheit"]),
                            selected=1 if mc["weather"]["unit"].upper().startswith("F") else 0)
        unit.connect("notify::selected", lambda r, _p: set_nested("weather", unit="F" if r.get_selected() else "C"))
        mono.add(unit)
        radius_row = Adw.ActionRow(title="Corner radius")
        radius_value = Gtk.Label(width_chars=5, xalign=1, css_classes=["numeric"], label=str(mc["corner_radius"]))
        radius = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 72, 2)
        radius.set_value(mc["corner_radius"])
        radius.set_size_request(180, -1)
        radius.set_valign(Gtk.Align.CENTER)
        radius.update_property([Gtk.AccessibleProperty.LABEL], ["Mono card corner radius"])

        def on_radius(sc):
            radius_value.set_text(str(round(sc.get_value())))
            set_mono(corner_radius=round(sc.get_value()))
        radius.connect("value-changed", on_radius)
        radius_row.add_suffix(radius)
        radius_row.add_suffix(radius_value)
        mono.add(radius_row)
        page.add(mono)

        sizing = Adw.PreferencesGroup(title="Widget size",
                                      description="Scales every widget at once, on top of each widget's own size.")
        overall = Adw.ActionRow(title="All widgets", subtitle="50% to 200%")
        label = Gtk.Label(label=f"{round(cfg['size'] * 100)}%", width_chars=5, xalign=1, css_classes=["numeric"])
        slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, SCALE_MIN * 100, SCALE_MAX * 100, 5)
        slider.set_value(cfg["size"] * 100)
        slider.set_size_request(220, -1)
        slider.set_valign(Gtk.Align.CENTER)
        slider.update_property([Gtk.AccessibleProperty.LABEL], ["Size of all widgets"])

        def on_size(sc):
            label.set_text(f"{round(sc.get_value())}%")
            self.app.update_config(lambda c: c.update(size=round(sc.get_value() / 100, 2)))
        slider.connect("value-changed", on_size)
        reset = Gtk.Button(icon_name="edit-undo-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                           tooltip_text="Reset to 100%")
        reset.update_property([Gtk.AccessibleProperty.LABEL], ["Reset size of all widgets"])
        reset.connect("clicked", lambda _b: slider.set_value(100))
        for w in (slider, label, reset):
            overall.add_suffix(w)
        sizing.add(overall)
        page.add(sizing)

        hint = Adw.PreferencesGroup(description="Leave the seed empty to use your wallpaper. "
                                                "The seed is only used in Adaptive mode.")
        page.add(hint)
        return page


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id="dev.hrithik.Widgits")
        self.add_main_option("background", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Restore saved widgets without opening the manager", None)
        self.background = False
        self.started = False
        self.manager = None
        self.windows = {}
        self._save_id = 0
        self.state = load_json(STATE_FILE, {"next": 1, "items": []})

    def do_handle_local_options(self, options):
        self.background = options.contains("background")
        return -1

    def do_activate(self):
        if not self.started:
            self.started = True
            provider = Gtk.CssProvider()
            provider.load_from_string(CSS)
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            for item in self.state["items"]:
                self.spawn(item)
            if self.background and self.state["items"]:
                return
        self.show_manager()

    def show_manager(self):
        if self.manager is None:
            self.manager = Manager(self)
            self.manager.connect("close-request", lambda *_: setattr(self, "manager", None) or False)
        self.manager.present()

    def spawn(self, item):
        window = WIDGETS[item["type"]](self, item)
        self.windows[item["id"]] = window
        window.set_opacity(0.0)
        window.present()
        window.fade(1.0)

    def find_slot(self, w, h):
        """First free spot on the primary monitor where a w x h widget doesn't touch the others."""
        monitors = Gdk.Display.get_default().get_monitors()
        geo = monitors.get_item(0).get_geometry() if monitors.get_n_items() else None
        mw, mh = (geo.width, geo.height) if geo else (1920, 1080)
        gap = step = 24
        taken = [(win.pos[0], win.pos[1], *win.natural_size()) for win in self.windows.values() if hasattr(win, "pos")]
        clash = lambda x, y: any(x < ox + ow + gap and x + w + gap > ox and y < oy + oh + gap and y + h + gap > oy
                                 for ox, oy, ow, oh in taken)
        for y in range(gap, max(mh - h - gap, gap) + 1, step):
            for x in range(gap, max(mw - w - gap, gap) + 1, step):
                if not clash(x, y):
                    return [x, y]
        return [gap, gap]

    def changed(self):
        save_json(STATE_FILE, self.state)
        if self.manager:
            self.manager.refresh()

    def add_widget(self, kind, style):
        item = {"id": self.state["next"], "type": kind, "style": style}
        self.state["next"] += 1
        self.state["items"].append(item)
        self.spawn(item)
        self.changed()

    def remove_widget(self, item_id):
        item = next((i for i in self.state["items"] if i["id"] == item_id), None)
        window = self.windows.pop(item_id, None)
        if window:
            window.cleanup()
            window.fade(0.0, window.destroy)
        self.state["items"] = [i for i in self.state["items"] if i["id"] != item_id]
        self.changed()
        if item and self.manager:
            self.manager.offer_undo(item)
        elif item:
            self.purge(item)

    def restore_widget(self, item):
        self.state["items"].append(item)
        self.state["items"].sort(key=lambda i: i["id"])
        self.spawn(item)
        self.changed()

    def purge(self, item):
        """Delete data belonging to a widget that was removed for good."""
        if item["type"] == "notes":
            (DATA_DIR / f"notes-{item['id']}.txt").unlink(missing_ok=True)

    def set_scale(self, item_id, value):
        """Size of one widget (0.5x to 2x). Saved shortly after the last change."""
        item = next((i for i in self.state["items"] if i["id"] == item_id), None)
        if item is None:
            return
        item["scale"] = round(min(max(value, SCALE_MIN), SCALE_MAX), 2)
        self.windows[item_id].apply_scale()
        if self._save_id:
            GLib.source_remove(self._save_id)
        self._save_id = GLib.timeout_add(400, self._save_state)
        if self.manager:
            self.manager.sync_size(item_id, item["scale"])

    def _save_state(self):
        self._save_id = 0
        save_json(STATE_FILE, self.state)
        return False

    def update_config(self, apply):
        cfg = load_json(CONFIG_FILE, {})
        apply(cfg)
        save_json(CONFIG_FILE, cfg)
        for window in self.windows.values():
            window.apply_scale()
            if hasattr(window, "refresh_theme"):
                window.refresh_theme()

    def set_style(self, item_id, style):
        for item in self.state["items"]:
            if item["id"] == item_id and item["style"] != style:
                item["style"] = style
                self.windows[item_id].apply_style(style)
        save_json(STATE_FILE, self.state)


if __name__ == "__main__":
    sys.exit(App().run(sys.argv))
