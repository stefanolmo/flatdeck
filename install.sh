#!/usr/bin/env bash
# Installa FlatDeck per l'utente corrente su Ubuntu/Debian (venv in ~/.local/share/flatdeck).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PREFIX="$HOME/.local/share/flatdeck"

echo "==> Dipendenze di sistema (richiede sudo)"
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip \
    libxcb-cursor0 libxkbcommon-x11-0 libegl1 libgl1 libdbus-1-3 libfontconfig1

echo "==> Ambiente virtuale e pacchetto"
python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install --upgrade pip
"$PREFIX/venv/bin/pip" install "$HERE"

echo "==> Launcher e icona"
mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/scalable/apps"
ln -sf "$PREFIX/venv/bin/flatdeck" "$HOME/.local/bin/flatdeck"
cp "$HERE/packaging/flatdeck.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/flatdeck.svg"
sed -e "s|@EXEC@|$PREFIX/venv/bin/flatdeck|" -e "s|@ICON@|flatdeck|" "$HERE/packaging/flatdeck.desktop" \
    > "$HOME/.local/share/applications/flatdeck.desktop"
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
echo "Fatto. Avvia 'flatdeck' dal menu applicazioni o dal terminale (prova: flatdeck examples/demo_copper.gbr examples/demo_outline.gbr examples/TrafficLight_drill.txt)."
