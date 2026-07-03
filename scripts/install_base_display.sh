#!/usr/bin/env bash
# Put a "Rover Status" launcher on the base Pi's desktop for tools/rover_display.py.
#
# Usage (from the repo root, as the desktop user — NOT root):
#     bash scripts/install_base_display.sh
#
# Override the status file with STATUS_FILE=... (must match the base service).
# Needs Tkinter:  sudo apt install -y python3-tk
set -euo pipefail

APPDIR="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -x "$APPDIR/.venv/bin/python" ]]; then
  PYTHON="$APPDIR/.venv/bin/python"
else
  PYTHON="$(command -v python3)"
fi
STATUS_FILE="${STATUS_FILE:-$APPDIR/rover-status.txt}"

DESKTOP_DIR="${DESKTOP_DIR:-$HOME/Desktop}"
mkdir -p "$DESKTOP_DIR"
TARGET="$DESKTOP_DIR/rover-status.desktop"

sed \
  -e "s#__PYTHON__#${PYTHON}#g" \
  -e "s#__APPDIR__#${APPDIR}#g" \
  -e "s#__STATUS_FILE__#${STATUS_FILE}#g" \
  "$APPDIR/scripts/rover-display.desktop" > "$TARGET"
chmod +x "$TARGET"

# Mark trusted so the file-manager launches it without the "untrusted" prompt.
gio set "$TARGET" metadata::trusted true 2>/dev/null || true

echo "Installed desktop launcher: $TARGET"
echo "  python=$PYTHON"
echo "  status file=$STATUS_FILE"
if ! "$PYTHON" -c "import tkinter" 2>/dev/null; then
  echo
  echo "NOTE: Tkinter isn't available for $PYTHON."
  echo "      Install it:  sudo apt install -y python3-tk"
fi
echo
echo "Test it now:  $PYTHON $APPDIR/tools/rover_display.py --status-file $STATUS_FILE"
