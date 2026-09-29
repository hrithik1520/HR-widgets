#!/usr/bin/env python3
"""Widgits: desktop widgets with switchable visual styles.

Open the app to pick a style and add widgets (clock, system monitor, media, notes)
to your desktop. Widgets are frameless cards: drag to move, right-click to remove.
The chosen widgets are restored on the next start; `--background` restores them
without opening the manager (used for autostart).
"""
import collections
import json
import sys
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Playerctl", "2.0")
import psutil
from gi.repository import Adw, Gdk, GLib, Gtk, Playerctl

try:  # desktop-layer placement on KDE, Sway, Hyprland, etc.; absent or unsupported on GNOME
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell as LayerShell
except (ValueError, ImportError):
    LayerShell = None

CONFIG_DIR = Path(GLib.get_user_config_dir()) / "widgits"
DATA_DIR = Path(GLib.get_user_data_dir()) / "widgits"
STATE_FILE = CONFIG_DIR / "state.json"
POSITIONS_FILE = CONFIG_DIR / "positions.json"
HISTORY = 60

STYLES = {  # id -> display name
    "glass": "Glassmorphism",
    "neu": "Neumorphism",
    "clay": "Claymorphism",
    "brutal": "Brutalism",
    "aurora": "Aurora",
    "terminal": "Terminal",
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

.preview { min-width: 92px; min-height: 52px; padding: 10px 14px; }
.preview label { font-size: 15px; font-weight: 700; }
"""


def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


class Widget(Gtk.Window):
    """Frameless card. With layer-shell it sits on the desktop layer, below all apps
    (drag updates its margins). Otherwise it is a normal window and the GNOME
    extension pins it below other windows. Right-click removes it."""

    kind = ""
    label = ""
    width = 320

    def __init__(self, app, item):
        self.item = item
        self.title_text = f"{self.label} {item['id']}"
        super().__init__(application=app, title=self.title_text, decorated=False,
                         resizable=False, default_width=self.width)
        self.add_css_class("widgit")
        self.card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, css_classes=["card"])
        self.style_class = None
        self.apply_style(item["style"])
        remove = Gtk.GestureClick(button=3)
        remove.connect("pressed", lambda *_: app.remove_widget(item["id"]))
        self.card.add_controller(remove)

        # The margin leaves room for the shadows of the neumorphic/brutalist cards.
        outer = Gtk.Box(margin_top=28, margin_bottom=28, margin_start=28, margin_end=28)
        outer.append(self.card)
        self.layered = LayerShell is not None and LayerShell.is_supported()
        if self.layered:
            self.setup_layer()
            self.set_child(outer)
        else:
            self.set_child(Gtk.WindowHandle(child=outer))

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
        n = self.item["id"] - 1
        self.pos = load_json(POSITIONS_FILE, {}).get(self.title_text, [20 + 370 * (n % 3), 20 + 390 * (n // 3)])
        self.apply_margins()
        drag = Gtk.GestureDrag(button=1)
        drag.connect("drag-begin", lambda *_: setattr(self, "origin", list(self.pos)))
        drag.connect("drag-update", lambda _g, dx, dy: self.move_to(self.origin[0] + dx, self.origin[1] + dy))
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
        """Delete data owned by this instance; called when the user removes it."""


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
        self.every(500, self.refresh)

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
        if self.pending:
            GLib.source_remove(self.pending)
            self.pending = 0
        self.file.unlink(missing_ok=True)


WIDGETS = {cls.kind: cls for cls in (ClockWidget, SystemWidget, MediaWidget, NotesWidget)}
CATALOG = (  # kind, icon, description
    ("clock", "preferences-system-time-symbolic", "Time, date and a month calendar"),
    ("system", "utilities-system-monitor-symbolic", "Live CPU, memory and network graphs"),
    ("media", "multimedia-player-symbolic", "Now playing with play, pause and skip"),
    ("notes", "document-edit-symbolic", "A scratchpad that saves as you type"),
)


class Manager(Adw.ApplicationWindow):
    """Where you choose a style and add or remove desktop widgets."""

    def __init__(self, app):
        super().__init__(application=app, title="Widgits", default_width=560, default_height=780)
        self.app = app
        self.style = "glass"

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24,
                       margin_top=18, margin_bottom=24, margin_start=18, margin_end=18)

        styles = Adw.PreferencesGroup(title="1. Pick a style")
        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.SINGLE, max_children_per_line=3,
                                min_children_per_line=2, homogeneous=True, row_spacing=4, column_spacing=4)
        for sid, name in STYLES.items():
            tile = Gtk.Box(css_classes=["card", "preview", sid], halign=Gtk.Align.CENTER)
            tile.append(Gtk.Label(label=name))
            holder = Gtk.Box(margin_top=14, margin_bottom=14, margin_start=14, margin_end=14)
            holder.append(tile)
            holder.style_id = sid
            self.flow.append(holder)
        self.flow.select_child(self.flow.get_child_at_index(0))
        self.flow.connect("child-activated", lambda _f, child: setattr(self, "style", child.get_child().style_id))
        styles.add(self.flow)
        page.append(styles)

        add = Adw.PreferencesGroup(title="2. Add a widget", description="Added with the style selected above.")
        for kind, icon, desc in CATALOG:
            row = Adw.ActionRow(title=WIDGETS[kind].label, subtitle=desc)
            row.add_prefix(Gtk.Image(icon_name=icon))
            button = Gtk.Button(label="Add", valign=Gtk.Align.CENTER, css_classes=["suggested-action", "pill"])
            button.connect("clicked", lambda _b, k=kind: app.add_widget(k, self.style))
            row.add_suffix(button)
            add.add(row)
        page.append(add)

        self.desktop = Adw.PreferencesGroup(title="On your desktop", description="Right-click a widget to remove it.")
        page.append(self.desktop)

        view = Adw.ToolbarView(content=Gtk.ScrolledWindow(child=Adw.Clamp(child=page, maximum_size=620),
                                                            vexpand=True))
        view.add_top_bar(Adw.HeaderBar())
        self.set_content(view)
        self.rows = []
        self.refresh()

    def refresh(self):
        for row in self.rows:
            self.desktop.remove(row)
        self.rows = []
        items = self.app.state["items"]
        if not items:
            items = []
            empty = Adw.ActionRow(title="Nothing yet", subtitle="Add a widget above and it appears on your desktop.")
            self.desktop.add(empty)
            self.rows.append(empty)
        names = Gtk.StringList.new(list(STYLES.values()))
        ids = list(STYLES)
        for item in items:
            row = Adw.ActionRow(title=f"{WIDGETS[item['type']].label} {item['id']}")
            dropdown = Gtk.DropDown(model=names, valign=Gtk.Align.CENTER, selected=ids.index(item["style"]))
            dropdown.connect("notify::selected",
                             lambda d, _p, i=item: self.app.set_style(i["id"], ids[d.get_selected()]))
            trash = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"])
            trash.connect("clicked", lambda _b, i=item: self.app.remove_widget(i["id"]))
            row.add_suffix(dropdown)
            row.add_suffix(trash)
            self.desktop.add(row)
            self.rows.append(row)


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id="dev.hrithik.Widgits")
        self.add_main_option("background", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Restore saved widgets without opening the manager", None)
        self.background = False
        self.started = False
        self.manager = None
        self.windows = {}
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
        window.present()

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
        window = self.windows.pop(item_id, None)
        if window:
            window.cleanup()
            window.destroy()
        self.state["items"] = [i for i in self.state["items"] if i["id"] != item_id]
        self.changed()

    def set_style(self, item_id, style):
        for item in self.state["items"]:
            if item["id"] == item_id and item["style"] != style:
                item["style"] = style
                self.windows[item_id].apply_style(style)
        save_json(STATE_FILE, self.state)


if __name__ == "__main__":
    sys.exit(App().run(sys.argv))
