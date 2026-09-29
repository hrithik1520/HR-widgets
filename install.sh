#!/bin/sh
# Installs Widgits (Flatpak) and, on GNOME, the extension that pins widgets under other windows.
set -e
cd "$(dirname "$0")"
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
if command -v flatpak-builder >/dev/null; then
  builder="flatpak-builder"
else
  echo "flatpak-builder not found, using the Flatpak version (org.flatpak.Builder)..."
  flatpak install --user -y flathub org.flatpak.Builder
  builder="flatpak run --user org.flatpak.Builder"
fi
$builder --user --install --force-clean --install-deps-from=flathub build-dir dev.hrithik.Widgits.yml
if [ "${XDG_CURRENT_DESKTOP#*GNOME}" != "$XDG_CURRENT_DESKTOP" ]; then
  dest="$HOME/.local/share/gnome-shell/extensions/widgits-pin@hrithik.dev"
  mkdir -p "$dest" && cp gnome-extension/* "$dest/"
  echo "GNOME extension installed. Log out/in, then: gnome-extensions enable widgits-pin@hrithik.dev"
fi
mkdir -p "$HOME/.config/autostart"
cp dev.hrithik.Widgits.desktop "$HOME/.config/autostart/"
sed -i 's|^Exec=.*|Exec=flatpak run dev.hrithik.Widgits --background|' "$HOME/.config/autostart/dev.hrithik.Widgits.desktop"
echo "Run: flatpak run dev.hrithik.Widgits"
