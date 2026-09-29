import GLib from 'gi://GLib';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const APP_ID = 'dev.hrithik.Widgits';
const MANAGER_TITLE = 'Widgits';
const STORE = GLib.build_filenamev([GLib.get_user_config_dir(), 'widgits', 'gnome-positions.json']);

function loadPositions() {
    try {
        const [ok, bytes] = GLib.file_get_contents(STORE);
        return ok ? JSON.parse(new TextDecoder().decode(bytes)) : {};
    } catch (_e) {
        return {};
    }
}

function savePositions(positions) {
    GLib.mkdir_with_parents(GLib.path_get_dirname(STORE), 0o755);
    GLib.file_set_contents(STORE, JSON.stringify(positions));
}

export default class WidgitsPin extends Extension {
    enable() {
        this._tracked = new Map();   // Meta.Window -> [signal ids]
        this._timeouts = new Set();
        this._positions = loadPositions();

        this._createdId = global.display.connect('window-created', (_d, win) => this._track(win));
        // Any restack (click, focus, new window) pushes widgets back under everything.
        this._restackId = global.display.connect('restacked', () => this._lowerAll());
        for (const actor of global.get_window_actors())
            this._track(actor.meta_window);
    }

    disable() {
        global.display.disconnect(this._createdId);
        global.display.disconnect(this._restackId);
        for (const [win, ids] of this._tracked)
            ids.forEach(id => win.disconnect(id));
        this._tracked = null;
        this._timeouts.forEach(id => GLib.source_remove(id));
        this._timeouts = null;
        this._positions = null;
    }

    _isWidget(win) {
        // The manager window shares the app id but must behave like a normal window.
        if (win.get_title() === MANAGER_TITLE)
            return false;
        return win.get_gtk_application_id() === APP_ID || win.get_wm_class() === APP_ID;
    }

    // App id and title arrive after window-created, so retry briefly.
    _track(win, attempt = 0) {
        if (!this._tracked || this._tracked.has(win))
            return;
        if (!this._isWidget(win) || !win.get_title()) {
            if (attempt < 20) {
                const id = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 250, () => {
                    this._timeouts?.delete(id);
                    this._track(win, attempt + 1);
                    return GLib.SOURCE_REMOVE;
                });
                this._timeouts.add(id);
            }
            return;
        }

        const title = win.get_title();
        console.log(`widgits-pin: pinning "${title}"`);
        win.stick();
        const saved = this._positions[title];
        if (saved)
            win.move_frame(true, saved[0], saved[1]);
        win.lower();

        this._tracked.set(win, [
            win.connect('position-changed', () => this._scheduleSave(win, title)),
            win.connect('unmanaged', () => this._tracked?.delete(win)),
        ]);
    }

    _lowerAll() {
        if (this._lowering || !this._tracked?.size)
            return;
        // Lowering restacks again; the guard stops that from recursing.
        this._lowering = true;
        const order = global.display.sort_windows_by_stacking([...this._tracked.keys()]);
        const bottom = global.display.sort_windows_by_stacking(
            global.get_window_actors().map(a => a.meta_window).filter(w => w.get_window_type() === 0))
            .slice(0, this._tracked.size);
        if (!order.every(w => bottom.includes(w)))
            order.forEach(w => w.lower());
        this._lowering = false;
    }

    _scheduleSave(win, title) {
        if (win._widgitsSave)
            GLib.source_remove(win._widgitsSave);
        win._widgitsSave = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 600, () => {
            this._timeouts?.delete(win._widgitsSave);
            win._widgitsSave = 0;
            const r = win.get_frame_rect();
            this._positions[title] = [r.x, r.y];
            savePositions(this._positions);
            return GLib.SOURCE_REMOVE;
        });
        this._timeouts.add(win._widgitsSave);
    }
}
