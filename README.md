# HR-Widgits

Desktop widgets for Linux with switchable visual styles. Open the app, pick a style, and add widgets to your desktop. Widgets sit on the desktop, under your other windows.

**Widgets:** Clock + calendar, System monitor (CPU, memory, network), Media player (MPRIS), Quick notes.
**Styles:** Glassmorphism, Neumorphism, Claymorphism, Brutalism, Aurora, Terminal.

Drag a widget to move it, right-click to remove it. Widgets and their positions are restored on login.

## Requirements

- Linux with a Wayland session (GNOME, KDE Plasma, Sway, Hyprland, ...)
- [Flatpak](https://flatpak.org/setup/) installed for your distro
- `git`

Everything else (GTK 4, libadwaita, playerctl, psutil, gtk4-layer-shell) is bundled in the Flatpak.

## Install

### 1. Install Flatpak and flatpak-builder

| Distro | Command |
|---|---|
| Fedora | `sudo dnf install flatpak flatpak-builder` |
| Ubuntu / Debian / Mint | `sudo apt install flatpak flatpak-builder` |
| Arch / Manjaro | `sudo pacman -S flatpak flatpak-builder` |
| openSUSE | `sudo zypper install flatpak flatpak-builder` |

If you skip `flatpak-builder`, `install.sh` installs the Flatpak version of it automatically. Ubuntu and Debian users may need to log out and back in after installing Flatpak for the first time.

### 2. Clone and install

```sh
git clone https://github.com/hrithik1520/HR-Widgits.git
cd HR-Widgits
./install.sh
```

The first build downloads the GNOME 50 runtime and SDK (about 1 GB) and compiles a few libraries, so it takes several minutes. It also adds a launcher to your app menu and sets Widgits to start at login.

### 3. Run

Open **Widgits** from your app menu, or run:

```sh
flatpak run dev.hrithik.Widgits
```

Pick a style, click **Add** on a widget, and it appears on your desktop.

## Keeping widgets under other windows

- **KDE, Sway, Hyprland and other layer-shell compositors:** works automatically. Widgets sit on the desktop layer.
- **GNOME:** GNOME has no desktop layer for apps, so `install.sh` installs a small extension from `gnome-extension/`. It keeps widgets on every workspace, below other windows, and remembers where you drag them. To activate it:

  1. Log out and log back in (GNOME on Wayland only detects new extensions at login).
  2. Run `gnome-extensions enable widgits-pin@hrithik.dev`
  3. Restart Widgits.

  If widgets still float above your apps, check that it loaded: `gnome-extensions info widgits-pin@hrithik.dev` should say `State: ACTIVE`, and `journalctl -b -o cat | grep widgits-pin` should show one "pinning" line per widget. Widgets also stay visible in Alt-Tab and the Overview on GNOME.

## Update

```sh
cd HR-Widgits
git pull
./install.sh
```

If `gnome-extension/` changed, log out and back in afterwards.

## Uninstall

```sh
flatpak uninstall --delete-data dev.hrithik.Widgits
rm -f ~/.config/autostart/dev.hrithik.Widgits.desktop
rm -rf ~/.local/share/gnome-shell/extensions/widgits-pin@hrithik.dev
rm -rf ~/.config/widgits
```

## Where data is stored

Inside the Flatpak: `~/.var/app/dev.hrithik.Widgits/` (widget list, positions, notes). On GNOME the extension keeps its own positions in `~/.config/widgits/gnome-positions.json`.

## Run without Flatpak

You need Python 3, GTK 4, libadwaita, playerctl with GObject introspection, psutil, and optionally gtk4-layer-shell for the desktop layer on non-GNOME compositors.

```sh
# Fedora
sudo dnf install python3-gobject gtk4 libadwaita playerctl python3-psutil gtk4-layer-shell
# Ubuntu / Debian
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-playerctl-2.0 python3-psutil
# Arch
sudo pacman -S python-gobject gtk4 libadwaita playerctl python-psutil gtk4-layer-shell

python3 widgits.py
```

Use `--background` to restore saved widgets without opening the manager.

## Troubleshooting

- **"does not support the Session Lock protocol"**: a harmless warning from gtk4-layer-shell on GNOME. Ignore it.
- **`flatpak-builder: command not found`**: install it as shown above, or rerun `./install.sh`, which falls back to the Flatpak version.
- **No media shown**: the Media widget follows MPRIS players (Spotify, Firefox, VLC, ...). Start playback and it appears.
- **Widgets in the wrong place after an update**: drag them once; positions are saved per widget.
