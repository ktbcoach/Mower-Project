#!/usr/bin/env bash
# Install + enable the lg580p systemd service: switch-gated IMU+GNSS fusion
# logging (`lg580p fuse --switch`) at boot, 50 Hz fused CSV/GPX.
#
# Usage (from the repo root):  sudo bash scripts/install_lg580p_service.sh
# Override with env vars, e.g.:
#     sudo PORT=/dev/serial0 BAUD=460800 HAT_STACK=0 \
#          GPS_LED=1 LOGGING_LED=2 CONTACT_CH=1 \
#          RTCM_SOURCE=/dev/ttyUSB0 RTCM_BAUD=57600 \
#          bash scripts/install_lg580p_service.sh
#
# Requires SPI enabled for the LSM6DSO IMU: sudo raspi-config nonint do_spi 0
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
# RTCM correction radio (optional). Set RTCM_SOURCE=/dev/ttyUSB0 to enable.
RTCM_SOURCE="${RTCM_SOURCE:-}"
RTCM_BAUD="${RTCM_BAUD:-57600}"
if [[ -n "$RTCM_SOURCE" ]]; then
  RTCM_ARGS="--rtcm-source ${RTCM_SOURCE} --rtcm-baud ${RTCM_BAUD}"
else
  RTCM_ARGS=""
fi

# LSM6DSO IMU + EKF tuning. Defaults match `lg580p fuse`'s own defaults (this
# rover's mounting: lateral antenna baseline, body-aligned IMU) — override only
# if the mounting or tuning changes. See docs/EKF.md.
IMU_BUS="${IMU_BUS:-0}"
IMU_CS="${IMU_CS:-0}"
IMU_ODR="${IMU_ODR:-208}"
AXIS_REMAP="${AXIS_REMAP:-x,y,z}"
LEVER_ARM="${LEVER_ARM:-0.0127,0.4445,0}"
HEADING_OFFSET="${HEADING_OFFSET:--90}"
RATE="${RATE:-50}"
COAST_MAX="${COAST_MAX:-5}"
GYRO_CAL="${GYRO_CAL:-5}"
FLOAT_SCALE="${FLOAT_SCALE:-40}"

TEMPLATE="$APPDIR/scripts/lg580p.service"
TARGET="/etc/systemd/system/lg580p.service"

echo "Installing lg580p service (fused IMU+GNSS logging, 50 Hz):"
echo "  user=$RUN_USER  python=$PYTHON"
echo "  port/baud=$PORT @ $BAUD  HAT stack=$HAT_STACK"
echo "  GPS LED=$GPS_LED  logging LED=$LOGGING_LED  contact ch=$CONTACT_CH"
echo "  IMU bus=$IMU_BUS cs=$IMU_CS odr=${IMU_ODR}Hz  axis-remap=$AXIS_REMAP  lever-arm=$LEVER_ARM"
echo "  heading-offset=$HEADING_OFFSET  rate=${RATE}Hz  coast-max=${COAST_MAX}s  float-scale=$FLOAT_SCALE"
[[ -n "$RTCM_ARGS" ]] && echo "  RTCM=$RTCM_SOURCE @ $RTCM_BAUD"

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
  -e "s#__IMU_BUS__#${IMU_BUS}#g" \
  -e "s#__IMU_CS__#${IMU_CS}#g" \
  -e "s#__IMU_ODR__#${IMU_ODR}#g" \
  -e "s#__AXIS_REMAP__#${AXIS_REMAP}#g" \
  -e "s#__LEVER_ARM__#${LEVER_ARM}#g" \
  -e "s#__HEADING_OFFSET__#${HEADING_OFFSET}#g" \
  -e "s#__RATE__#${RATE}#g" \
  -e "s#__COAST_MAX__#${COAST_MAX}#g" \
  -e "s#__GYRO_CAL__#${GYRO_CAL}#g" \
  -e "s#__FLOAT_SCALE__#${FLOAT_SCALE}#g" \
  -e "s#__RTCM_ARGS__#${RTCM_ARGS}#g" \
  "$TEMPLATE" > "$TARGET"

mkdir -p "$APPDIR/logs"
chown "$RUN_USER" "$APPDIR/logs"

systemctl daemon-reload
systemctl enable lg580p.service
systemctl restart lg580p.service

echo
echo "Done. Fused CSV/GPX logged on every boot (switch-gated)."
echo "  status:  systemctl status lg580p"
echo "  logs:    journalctl -u lg580p -f"
echo "  stop:    sudo systemctl stop lg580p"
echo
echo "NOTE: lg580p and the Watson service both read a serial port and the HAT —"
echo "run only one at a time. Disable the other: sudo systemctl disable --now watson-dms"
echo
echo "NOTE: rover -> base \$PRSTAT telemetry (--telemetry) isn't wired into the"
echo "fusion pipeline yet; it's only available via 'lg580p collect --switch'."
