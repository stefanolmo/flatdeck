#!/usr/bin/env bash
# Install FlatDeck for the current user on Ubuntu/Debian (venv in ~/.local/share/flatdeck).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PREFIX="${FLATDECK_PREFIX:-$HOME/.local/share/flatdeck}"

PY="${PYTHON:-python3}"
echo "==> FlatDeck installer: $("$PY" --version 2>&1), $(uname -m)"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || { echo "Python 3.10 or newer is needed"; exit 1; }

if [ "${FLATDECK_SKIP_APT:-0}" != "1" ]; then
    echo "==> System dependencies (needs sudo; set FLATDECK_SKIP_APT=1 to skip)"
    sudo apt-get update
    sudo apt-get install -y python3 python3-venv python3-pip \
        libxcb-cursor0 libxkbcommon-x11-0 libegl1 libgl1 libdbus-1-3 libfontconfig1
fi

echo "==> Virtual environment"
"$PY" -m venv "$PREFIX/venv"

echo "==> Python packages (PySide6 alone is about 100 MB: this can take a few minutes on a slow connection)"
# Install from a clean copy of the sources: pip copies the whole folder it is given, so a big .venv or data files in the
# repository would make this step look frozen (and it would leave build/ and *.egg-info in the repository).
SRC="$(mktemp -d)"
trap 'rm -rf "${SRC:?}"' EXIT
cp -r "$HERE/flatdeck" "$HERE/pyproject.toml" "$HERE/README.md" "$SRC/"
PIPOPTS=(--progress-bar on --timeout 60 --retries 5)
[ "${FLATDECK_VERBOSE:-0}" = "1" ] && PIPOPTS+=(-v)
export PIP_DISABLE_PIP_VERSION_CHECK=1
"$PREFIX/venv/bin/pip" install "${PIPOPTS[@]}" "$SRC"
# pip would keep an old copy if the version did not change: always replace the package itself
"$PREFIX/venv/bin/pip" install --force-reinstall --no-deps "${PIPOPTS[@]}" "$SRC"
"$PREFIX/venv/bin/python" -c "import flatdeck, PySide6; print('   FlatDeck', flatdeck.__version__, 'with PySide6', PySide6.__version__)"

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
