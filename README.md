# HR-widgets

Desktop widgets for Linux with switchable visual styles. Open the app, pick a style, and add widgets to your desktop. Widgets sit on the desktop, under your other windows.

**Widgets:** Clock + calendar, System monitor (CPU, memory, network), Media player (MPRIS), Quick notes.
**Styles:** Glassmorphism, Neumorphism, Claymorphism, Brutalism, Aurora, Terminal.

Drag a widget to move it. Right-click a widget for a menu: open the manager, make it larger or smaller, or remove it. Ctrl + scroll over a widget also resizes it (50% to 200%), and the manager has per-widget and all-widget size sliders. Removing from the manager can be undone. Widgets and their positions are restored on login.

## Requirements

- Linux with a Wayland session (GNOME, KDE Plasma, Sway, Hyprland, ...)
- [Flatpak](https://flatpak.org/setup/) installed for your distro
- `git`

Everything else (GTK 4, libadwaita, playerctl, psutil, gtk4-layer-shell) is bundled in the Flatpak.

## Download (v1.0.1, x86_64)

The quickest way, no build needed. Get the files from the [latest release](https://github.com/hrithik1520/HR-widgets/releases/latest):

- `Widgits-1.0.1-x86_64.flatpak`: the app
- `widgits-pin@hrithik.dev.zip`: the GNOME extension (GNOME only, see below)

```sh
flatpak install --user Widgits-1.0.1-x86_64.flatpak
flatpak run dev.hrithik.Widgits
# GNOME only:
gnome-extensions install --force widgits-pin@hrithik.dev.zip
```

The Flatpak needs [Flatpak set up](https://flatpak.org/setup/) on your distro and pulls the GNOME runtime from Flathub the first time. On GNOME, log out and back in, then run `gnome-extensions enable widgits-pin@hrithik.dev`. To start Widgits at login, copy the autostart entry:

```sh
mkdir -p ~/.config/autostart
printf '[Desktop Entry]\nType=Application\nName=Widgits\nExec=flatpak run dev.hrithik.Widgits --background\n' > ~/.config/autostart/dev.hrithik.Widgits.desktop
```

Or build from source instead:

## Install from source

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
git clone https://github.com/hrithik1520/HR-widgets.git
cd HR-widgets
./install.sh
```

The first build downloads the GNOME 50 runtime and SDK (about 1 GB) and compiles a few libraries, so it takes several minutes. It also adds a launcher to your app menu and sets Widgits to start at login.

### 3. Run

Open **Widgits** from your app menu, or run:

```sh
flatpak run dev.hrithik.Widgits
```

Pick a style, click **Add** on a widget, and it appears on your desktop.

## Pixel Clock theme

The **Pixel Clock** is a Material You style clock: a scalloped analog face over a digital time and date. Its colors come from a theme, set in the manager under "Pixel Clock theme" or in `config.json`:

| Mode | Colors |
|---|---|
| `dark` (default) | Fixed dark palette: bg `#1C1C1E`, text `#FFFFFF`, secondary `#9A9A9F`, accent `#E8622A` |
| `adaptive` | Material You palette generated from your wallpaper |
| `auto` | Follows the system light/dark setting, dark if it can't be read |

For `adaptive`, the seed color comes from the first source that works: `theme.seed` in the config, the current wallpaper (`wallpaper_path` in the config, then GNOME's `picture-uri-dark` / `picture-uri`, then KDE Plasma's applet config), the desktop accent color, and finally `#E8622A`. Adaptive needs the `materialyoucolor` package, which the Flatpak bundles on x86_64; without it, adaptive falls back to dark. The palette re-checks every 30 seconds, so it follows wallpaper changes.

`config.json` lives in `~/.var/app/dev.hrithik.Widgits/config/widgits/` (Flatpak) or `~/.config/widgits/`:

```json
{
  "theme": {"mode": "adaptive", "seed": null, "wallpaper_path": null},
  "pixel_face": "filled"
}
```

`pixel_face` can be `"filled"` (tonal face) or `"frame"` (outline only). In the Flatpak, wallpapers in `~/Pictures` and `~/.local/share/backgrounds` are readable; for a wallpaper elsewhere, set `seed` or copy the image into one of those folders.

## Warped Clock

An analog clock with no card: numerals melt around a tall, tilted oval, with thin leaf-tipped hands, small text above them and a `day date` line below. All colors come from the theme, so it follows Dark, Adaptive or Auto. If the wallpaper makes the ink hard to read (under 4.5:1 contrast), the ink flips to the opposite tone or gains a thin halo. Colors fade over about 300 ms when the wallpaper or theme changes.

Adjust it under Appearance, or in `config.json`:

```json
{
  "theme": {"mode": "adaptive", "variant": "tonal_spot"},
  "warped": {
    "backing": "none",
    "plate_opacity": 1.0,
    "accent_hands": true,
    "tilt": -12,
    "distortion": 0.9,
    "font": "Playfair Display",
    "micro_text": "quartz",
    "smooth": true
  }
}
```

`variant` is `tonal_spot`, `vibrant` or `monochrome` (near-grayscale ink for a poster look). `backing` is `none` or `plate` (a surface-colored oval behind the ring). `distortion` runs from 0 (a plain oval) to 1 (full melt). Playfair Display is bundled in the Flatpak; without it the clock falls back to DM Serif Display, Fraunces or a system serif.

## Mono Card

A square black info card in a dot-matrix, monoline spirit: the date in bold monospace, a large thin time with a slashed zero and small superscript seconds, an optional sleep timeline, the temperature, and Bluetooth and headphone icons. Colors come from the theme: pure black with a `#FF4D00` accent in Dark, or a Material You palette in Adaptive. The `vibrant` color variant gives the closest Nothing look, and `monochrome` gives a white panel with black text. The palette crossfades on theme changes, and the text on the accent is nudged to black or white if the contrast drops under 4.5:1.

- **Temperature:** from [Open-Meteo](https://open-meteo.com) (no key), refreshed every 15 minutes and cached. Set your location under Appearance or in the config.
- **Bluetooth:** read from BlueZ over D-Bus. The icon dims when the adapter is off and shows a dot when a device is connected. The headphones icon only appears while a connected device is an audio headset or headphones.
- **Sleep:** Linux has no native sleep source, so the card reads `~/.config/clockwidget/sleep.json` (see [examples/sleep.json](examples/sleep.json)). Times can be 12 or 24 hour. Turn the block off with `sleep.enabled`. With no file it shows "NO DATA".

```json
{
  "mono": {
    "show_seconds": true,
    "show_bluetooth": true,
    "show_headphones": true,
    "corner_radius": 48,
    "shadow": false,
    "sleep": {"enabled": true},
    "weather": {"lat": 15.49, "lon": 73.83, "unit": "C"}
  }
}
```

The dot-matrix time and temperature are drawn by the app, so they don't depend on a font. Space Mono, used for the date and labels, is bundled in the Flatpak.

## Keeping widgets under other windows

- **KDE, Sway, Hyprland and other layer-shell compositors:** works automatically. Widgets sit on the desktop layer.
- **GNOME:** GNOME has no desktop layer for apps, so `install.sh` installs a small extension from `gnome-extension/`. It keeps widgets on every workspace, below other windows, and remembers where you drag them. To activate it:

  1. Log out and log back in (GNOME on Wayland only detects new extensions at login).
  2. Run `gnome-extensions enable widgits-pin@hrithik.dev`
  3. Restart Widgits.

  If widgets still float above your apps, check that it loaded: `gnome-extensions info widgits-pin@hrithik.dev` should say `State: ACTIVE`, and `journalctl -b -o cat | grep widgits-pin` should show one "pinning" line per widget. Widgets also stay visible in Alt-Tab and the Overview on GNOME.

## Update

```sh
cd HR-widgets
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

You need Python 3, GTK 4, libadwaita, playerctl with GObject introspection, psutil, and optionally gtk4-layer-shell (desktop layer on non-GNOME compositors) and `pip install materialyoucolor` (adaptive theme).

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
