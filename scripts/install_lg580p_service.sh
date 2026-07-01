#!/usr/bin/env bash
# Install + enable the lg580p systemd service (auto-start logging at boot).
#
# Usage (from the repo root):  sudo bash scripts/install_lg580p_service.sh
# Override with env vars, e.g.:
#     sudo PORT=/dev/serial0 BAUD=460800 HAT_STACK=0 \
#          GPS_LED=1 LOGGING_LED=2 CONTACT_CH=1 \
#          bash scripts/install_lg580p_service.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root: sudo bash scripts/install_lg580p_service.sh" >&2
  exit 1
fi

APPDIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_USER="${SUDO_USER:-$(id -un)}"
if [[ -x "$APPDIR/.venv/bin/python" ]]; then
  PYTHON="$APPDIR/.venv/bin/python"
else
  PYTHON="$(command -v python3)"
fi

PORT="${PORT:-/dev/serial0}"
BAUD="${BAUD:-460800}"
HAT_STACK="${HAT_STACK:-0}"
GPS_LED="${GPS_LED:-1}"
LOGGING_LED="${LOGGING_LED:-2}"
CONTACT_CH="${CONTACT_CH:-1}"

TEMPLATE="$APPDIR/scripts/lg580p.service"
TARGET="/etc/systemd/system/lg580p.service"

echo "Installing lg580p service:"
echo "  user=$RUN_USER  python=$PYTHON"
echo "  port/baud=$PORT @ $BAUD  HAT stack=$HAT_STACK"
echo "  GPS LED=$GPS_LED  logging LED=$LOGGING_LED  contact ch=$CONTACT_CH"

sed \
  -e "s#__USER__#${RUN_USER}#g" \
  -e "s#__APPDIR__#${APPDIR}#g" \
  -e "s#__PYTHON__#${PYTHON}#g" \
  -e "s#__PORT__#${PORT}#g" \
  -e "s#__BAUD__#${BAUD}#g" \
  -e "s#__HAT_STACK__#${HAT_STACK}#g" \
  -e "s#__GPS_LED__#${GPS_LED}#g" \
  -e "s#__LOGGING_LED__#${LOGGING_LED}#g" \
  -e "s#__CONTACT_CH__#${CONTACT_CH}#g" \
  "$TEMPLATE" > "$TARGET"

mkdir -p "$APPDIR/logs"
chown "$RUN_USER" "$APPDIR/logs"

systemctl daemon-reload
systemctl enable lg580p.service
systemctl restart lg580p.service

echo
echo "Done. Logs on every boot."
echo "  status:  systemctl status lg580p"
echo "  logs:    journalctl -u lg580p -f"
echo "  stop:    sudo systemctl stop lg580p"
echo
echo "NOTE: lg580p and the Watson service both read a serial port and the HAT —"
echo "run only one at a time. Disable the other: sudo systemctl disable --now watson-dms"
