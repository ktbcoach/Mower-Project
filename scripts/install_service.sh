#!/usr/bin/env bash
# Install + enable the watson-dms systemd service so logging auto-starts at boot.
#
# Fills the placeholders in watson-dms.service with the real user, app path and
# Python interpreter, installs it to /etc/systemd/system, and enables it.
#
# Usage (from the repo root):
#     sudo bash scripts/install_service.sh
#
# Override defaults with env vars, e.g.:
#     sudo PORT=/dev/ttyAMA5 BAUD=9600 SWITCH_PIN=16 LED_PIN=26 \
#          bash scripts/install_service.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root: sudo bash scripts/install_service.sh" >&2
  exit 1
fi

APPDIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_USER="${SUDO_USER:-$(id -un)}"

# Prefer the project venv's Python if present, else system python3.
if [[ -x "$APPDIR/.venv/bin/python" ]]; then
  PYTHON="$APPDIR/.venv/bin/python"
else
  PYTHON="$(command -v python3)"
fi

PORT="${PORT:-/dev/ttyAMA5}"
BAUD="${BAUD:-9600}"
SWITCH_PIN="${SWITCH_PIN:-16}"
LED_PIN="${LED_PIN:-26}"

TEMPLATE="$APPDIR/scripts/watson-dms.service"
TARGET="/etc/systemd/system/watson-dms.service"

echo "Installing service:"
echo "  user       = $RUN_USER"
echo "  app dir    = $APPDIR"
echo "  python     = $PYTHON"
echo "  port/baud  = $PORT @ $BAUD"
echo "  switch/led = GPIO$SWITCH_PIN / GPIO$LED_PIN"

sed \
  -e "s#__USER__#${RUN_USER}#g" \
  -e "s#__APPDIR__#${APPDIR}#g" \
  -e "s#__PYTHON__#${PYTHON}#g" \
  -e "s#__PORT__#${PORT}#g" \
  -e "s#__BAUD__#${BAUD}#g" \
  -e "s#__SWITCH_PIN__#${SWITCH_PIN}#g" \
  -e "s#__LED_PIN__#${LED_PIN}#g" \
  "$TEMPLATE" > "$TARGET"

mkdir -p "$APPDIR/logs"
chown "$RUN_USER" "$APPDIR/logs"

systemctl daemon-reload
systemctl enable watson-dms.service
systemctl restart watson-dms.service

echo
echo "Done. The logger will now start on every boot."
echo "  status:  systemctl status watson-dms"
echo "  logs:    journalctl -u watson-dms -f"
echo "  stop:    sudo systemctl stop watson-dms"
