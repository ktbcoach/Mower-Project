#!/usr/bin/env bash
# Install + enable the base-station NTRIP->radio bridge service (auto-start at boot).
#
# Usage (from the repo root):  sudo bash scripts/install_base_service.sh
#
# Credentials: create scripts/ntrip-base.env (gitignored) with
#     NTRIP_USER=...
#     NTRIP_PASSWORD=...
# (this script writes a template on first run if it's missing).
#
# Override defaults with env vars, e.g.:
#     sudo MOUNTPOINT=VRS_RTCM3 SERIAL=/dev/ttyUSB0 SERIAL_BAUD=19200 \
#          bash scripts/install_base_service.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root: sudo bash scripts/install_base_service.sh" >&2
  exit 1
fi

APPDIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_USER="${SUDO_USER:-$(id -un)}"
if [[ -x "$APPDIR/.venv/bin/python" ]]; then
  PYTHON="$APPDIR/.venv/bin/python"
else
  PYTHON="$(command -v python3)"
fi

ENV_FILE="${ENV_FILE:-$APPDIR/scripts/ntrip-base.env}"
HOST="${HOST:-20.185.11.35}"
PORT="${PORT:-2101}"
MOUNTPOINT="${MOUNTPOINT:-VCAP_RTCM3}"
SERIAL="${SERIAL:-/dev/ttyUSB0}"
SERIAL_BAUD="${SERIAL_BAUD:-19200}"
# Where the rover's $PRSTAT telemetry is mirrored for the display (see
# tools/rover_display.py, which defaults to this same path).
STATUS_FILE="${STATUS_FILE:-$APPDIR/rover-status.txt}"
# Network-RTK / VRS mountpoints need an approximate position (GGA). Set both
# LAT and LON (decimal degrees) to enable; single-base mountpoints leave unset.
LAT="${LAT:-}"
LON="${LON:-}"
if [[ -n "$LAT" && -n "$LON" ]]; then
  GGA_ARGS="--lat ${LAT} --lon ${LON}"
elif [[ -n "$LAT" || -n "$LON" ]]; then
  echo "Set BOTH LAT and LON (or neither) — only one was given." >&2
  exit 1
else
  GGA_ARGS=""
fi

# First run: drop a credential template and stop so the user can fill it in.
if [[ ! -f "$ENV_FILE" ]]; then
  echo "Creating credential file template: $ENV_FILE"
  cat > "$ENV_FILE" <<'EOF'
# VTrans NTRIP credentials (gitignored — never commit).
NTRIP_USER=
NTRIP_PASSWORD=
EOF
  chown "$RUN_USER" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "  -> edit it, fill in NTRIP_USER / NTRIP_PASSWORD, then re-run this script." >&2
  exit 1
fi

# Refuse to install with empty creds.
if ! grep -q '^NTRIP_USER=.\+' "$ENV_FILE" || ! grep -q '^NTRIP_PASSWORD=.\+' "$ENV_FILE"; then
  echo "NTRIP_USER / NTRIP_PASSWORD are empty in $ENV_FILE — fill them in first." >&2
  exit 1
fi
chmod 600 "$ENV_FILE"

TEMPLATE="$APPDIR/scripts/base-ntrip.service"
TARGET="/etc/systemd/system/base-ntrip.service"

echo "Installing base-ntrip service:"
echo "  user=$RUN_USER  python=$PYTHON"
echo "  caster=$HOST:$PORT  mountpoint=$MOUNTPOINT"
echo "  radio=$SERIAL @ $SERIAL_BAUD"
echo "  telemetry status file=$STATUS_FILE"
[[ -n "$GGA_ARGS" ]] && echo "  GGA position=$LAT,$LON (VRS)"

sed \
  -e "s#__USER__#${RUN_USER}#g" \
  -e "s#__APPDIR__#${APPDIR}#g" \
  -e "s#__PYTHON__#${PYTHON}#g" \
  -e "s#__ENV_FILE__#${ENV_FILE}#g" \
  -e "s#__HOST__#${HOST}#g" \
  -e "s#__PORT__#${PORT}#g" \
  -e "s#__MOUNTPOINT__#${MOUNTPOINT}#g" \
  -e "s#__SERIAL__#${SERIAL}#g" \
  -e "s#__SERIAL_BAUD__#${SERIAL_BAUD}#g" \
  -e "s#__STATUS_FILE__#${STATUS_FILE}#g" \
  -e "s#__GGA_ARGS__#${GGA_ARGS}#g" \
  "$TEMPLATE" > "$TARGET"

systemctl daemon-reload
systemctl enable base-ntrip.service
systemctl restart base-ntrip.service

echo
echo "Done. Corrections stream on every boot."
echo "  status:  systemctl status base-ntrip"
echo "  logs:    journalctl -u base-ntrip -f"
echo "  stop:    sudo systemctl stop base-ntrip"
