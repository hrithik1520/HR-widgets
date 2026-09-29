# HR-Widgits

Desktop widgets for Linux with switchable visual styles. Open the app, pick a style, and add widgets to your desktop.

**Widgets:** Clock + calendar, System monitor (CPU, memory, network), Media player (MPRIS), Quick notes.
**Styles:** Glassmorphism, Neumorphism, Claymorphism, Brutalism, Aurora, Terminal.

Drag a widget to move it, right-click to remove it. Your widgets and their positions are restored on login.

## Install (Flatpak)

```sh
git clone https://github.com/hrithik1520/HR-Widgits.git
cd HR-Widgits
./install.sh
flatpak run dev.hrithik.Widgits
```

`install.sh` uses `flatpak-builder` (or installs the Flatpak version of it), builds the app, and sets up autostart.

## Staying under other windows

- **KDE, Sway, Hyprland and other layer-shell compositors:** widgets sit on the desktop layer automatically.
- **GNOME:** GNOME has no desktop layer for apps, so `install.sh` installs a small extension (`gnome-extension/`) that keeps widgets on every workspace and below other windows. Log out and back in, then run:

  ```sh
  gnome-extensions enable widgits-pin@hrithik.dev
  ```

## Run without Flatpak

Needs Python 3, GTK 4, libadwaita, playerctl (with GObject introspection) and psutil.

```sh
python3 widgits.py
```

Use `--background` to restore saved widgets without opening the manager.
