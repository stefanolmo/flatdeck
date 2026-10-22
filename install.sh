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
ICONS="$HOME/.local/share/icons/hicolor"
mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications" "$ICONS/scalable/apps" "$ICONS/256x256/apps"
ln -sf "$PREFIX/venv/bin/flatdeck" "$HOME/.local/bin/flatdeck"
cp "$HERE/packaging/flatdeck.svg" "$ICONS/scalable/apps/flatdeck.svg"
cp "$HERE/packaging/flatdeck-256.png" "$ICONS/256x256/apps/flatdeck.png"
gtk-update-icon-cache -f -t "$ICONS" 2>/dev/null || true
# percorso assoluto dell'icona: funziona anche se la cache del tema icone non è aggiornata
sed -e "s|@EXEC@|$PREFIX/venv/bin/flatdeck|" -e "s|@ICON@|$ICONS/256x256/apps/flatdeck.png|" "$HERE/packaging/flatdeck.desktop" \
    > "$HOME/.local/share/applications/flatdeck.desktop"
command -v desktop-file-validate >/dev/null && desktop-file-validate "$HOME/.local/share/applications/flatdeck.desktop" || true
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

echo "==> Icona sul desktop"
DESK="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
if [ -z "$DESK" ] || [ ! -d "$DESK" ]; then
    for d in "$HOME/Desktop" "$HOME/Scrivania"; do [ -d "$d" ] && DESK="$d" && break; done
fi
if [ -d "$DESK" ]; then
    cp "$HOME/.local/share/applications/flatdeck.desktop" "$DESK/flatdeck.desktop"
    chmod +x "$DESK/flatdeck.desktop"
    gio set "$DESK/flatdeck.desktop" metadata::trusted true 2>/dev/null || true   # GNOME: permette il doppio clic
    echo "   Creata $DESK/flatdeck.desktop (se appare senza icona: tasto destro -> Consenti l'avvio)"
else
    echo "   Cartella Desktop non trovata: l'icona è solo nel menu applicazioni (Mostra applicazioni -> FlatDeck)."
fi
echo "Fatto. Avvia 'flatdeck' dal menu applicazioni o dal terminale (prova: flatdeck examples/TrafficLight_copperTop.gtl examples/TrafficLight_drill.txt)."
