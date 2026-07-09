#!/usr/bin/env bash
# Base-Pi NTRIP -> radio bridge launcher (VTrans RTN -> XStream base radio).
#
# Manual run from the base Pi:   bash scripts/start_base.sh
#
# Credentials come from scripts/ntrip-base.env (gitignored) or the environment:
#     NTRIP_USER / NTRIP_PASSWORD
# Override the caster/mountpoint/radio with env vars (see defaults below).
set -euo pipefail

APPDIR="$(cd "$(dirname "$0")/.." && pwd)"

# Load credentials (and any overrides) from the gitignored env file, if present.
ENV_FILE="${ENV_FILE:-$APPDIR/scripts/ntrip-base.env}"
if [[ -f "$ENV_FILE" ]]; then
  set -a; . "$ENV_FILE"; set +a
fi

# Prefer the project venv python (has pyserial); fall back to system python3.
if [[ -x "$APPDIR/.venv/bin/python" ]]; then
  PYTHON="$APPDIR/.venv/bin/python"
else
  PYTHON="$(command -v python3)"
fi

HOST="${NTRIP_HOST:-20.185.11.35}"
PORT="${NTRIP_PORT:-2101}"
MOUNTPOINT="${NTRIP_MOUNTPOINT:-VCAP_RTCM3}"
SERIAL="${BASE_SERIAL:-/dev/ttyUSB0}"
SERIAL_BAUD="${BASE_SERIAL_BAUD:-19200}"
# Network-RTK / VRS mountpoints need an approximate position. Set both LAT/LON.
LAT="${LAT:-}"
LON="${LON:-}"
# VRS GGA tracks the rover's reported position (LAT/LON, if set, seed it until
# the first rover fix arrives). Set GGA_FROM_ROVER=0 to use a fixed LAT/LON only.
GGA_FROM_ROVER="${GGA_FROM_ROVER:-1}"

if [[ -z "${NTRIP_USER:-}" || -z "${NTRIP_PASSWORD:-}" ]]; then
  echo "NTRIP_USER / NTRIP_PASSWORD not set. Put them in $ENV_FILE or export them." >&2
  exit 1
fi

GGA_ARGS=()
if [[ -n "$LAT" && -n "$LON" ]]; then
  GGA_ARGS=(--lat "$LAT" --lon "$LON")
elif [[ -n "$LAT" || -n "$LON" ]]; then
  echo "Set BOTH LAT and LON (or neither) — only one was given." >&2
  exit 1
fi
if [[ "$GGA_FROM_ROVER" != "0" ]]; then
  GGA_ARGS+=(--gga-from-rover)
  GGA_NOTE="  (GGA follows rover${LAT:+ seeded $LAT,$LON})"
else
  GGA_NOTE="${LAT:+  (GGA $LAT,$LON)}"
fi

echo "# base bridge: $HOST:$PORT/$MOUNTPOINT -> $SERIAL @ $SERIAL_BAUD$GGA_NOTE"
# ntrip_to_serial.py reads NTRIP_USER/NTRIP_PASSWORD from the environment.
exec "$PYTHON" "$APPDIR/tools/ntrip_to_serial.py" \
  --host "$HOST" --port "$PORT" --mountpoint "$MOUNTPOINT" \
  --serial "$SERIAL" --serial-baud "$SERIAL_BAUD" "${GGA_ARGS[@]}"
