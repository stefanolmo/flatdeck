#!/usr/bin/env bash
# Install FlatDeck for the current user on Ubuntu/Debian (venv in ~/.local/share/flatdeck).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PREFIX="$HOME/.local/share/flatdeck"

echo "==> System dependencies (needs sudo)"
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip \
    libxcb-cursor0 libxkbcommon-x11-0 libegl1 libgl1 libdbus-1-3 libfontconfig1

echo "==> Virtual environment and package"
python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install --upgrade pip
"$PREFIX/venv/bin/pip" install "$HERE"

echo "==> Launcher and icon"
ICONS="$HOME/.local/share/icons/hicolor"
mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications" "$ICONS/scalable/apps" "$ICONS/256x256/apps"
ln -sf "$PREFIX/venv/bin/flatdeck" "$HOME/.local/bin/flatdeck"
cp "$HERE/packaging/flatdeck.svg" "$ICONS/scalable/apps/flatdeck.svg"
cp "$HERE/packaging/flatdeck-256.png" "$ICONS/256x256/apps/flatdeck.png"
gtk-update-icon-cache -f -t "$ICONS" 2>/dev/null || true
# absolute icon path: works even if the icon theme cache is stale
sed -e "s|@EXEC@|$PREFIX/venv/bin/flatdeck|" -e "s|@ICON@|$ICONS/256x256/apps/flatdeck.png|" "$HERE/packaging/flatdeck.desktop" \
    > "$HOME/.local/share/applications/flatdeck.desktop"
command -v desktop-file-validate >/dev/null && desktop-file-validate "$HOME/.local/share/applications/flatdeck.desktop" || true
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

echo "==> Desktop icon"
DESK="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
if [ -z "$DESK" ] || [ ! -d "$DESK" ]; then
    for d in "$HOME/Desktop" "$HOME/Scrivania"; do [ -d "$d" ] && DESK="$d" && break; done
fi
if [ -d "$DESK" ]; then
    cp "$HOME/.local/share/applications/flatdeck.desktop" "$DESK/flatdeck.desktop"
    chmod +x "$DESK/flatdeck.desktop"
    gio set "$DESK/flatdeck.desktop" metadata::trusted true 2>/dev/null || true   # GNOME: allows double-click
    echo "   Created $DESK/flatdeck.desktop (if it shows without an icon: right-click -> Allow Launching)"
else
    echo "   Desktop folder not found: the icon is only in the applications menu (Show Applications -> FlatDeck)."
fi
echo "Done. Start 'flatdeck' from the applications menu or a terminal (try: flatdeck examples/TrafficLight_copperTop.gtl examples/TrafficLight_drill.txt)."
