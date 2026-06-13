#!/usr/bin/env bash
# One-time Raspberry Pi setup for the Sequent Microsystems Multi-IO HAT + DMS-SGP02.
#
# The Multi-IO HAT connects its RS232 transceiver to GPIO12/GPIO13 (Pi UART5),
# NOT the primary UART on GPIO14/15. This script:
#   1. Enables the uart5 device-tree overlay so /dev/ttyAMA5 appears.
#   2. Enables I2C (required by the Multi-IO HAT for all other functions).
#   3. Disables the serial login console on the primary UART (ttyAMA0/serial0)
#      as a precaution — it does not affect UART5.
#
# Hardware note: the RS232 RX jumper on J2 must be installed (pin 33 / GPIO13).
# Silkscreen errata: the UPPER DB9 connector is RS232, LOWER is RS485.
#
# Run with sudo, then reboot:  sudo bash scripts/setup_pi.sh && sudo reboot

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root: sudo bash scripts/setup_pi.sh" >&2
  exit 1
fi

CONFIG=/boot/firmware/config.txt
[[ -f "$CONFIG" ]] || CONFIG=/boot/config.txt   # older Raspberry Pi OS

echo "Using boot config: $CONFIG"

# 1. Enable UART5 on GPIO12/GPIO13 -> /dev/ttyAMA5
ensure_line() {
  local line="$1"
  grep -qxF "$line" "$CONFIG" || echo "$line" >> "$CONFIG"
}
echo "Enabling UART5 overlay (GPIO12/GPIO13 -> /dev/ttyAMA5)..."
ensure_line "dtoverlay=uart5"

# 2. Enable I2C (needed by the Multi-IO HAT).
echo "Enabling I2C..."
ensure_line "dtparam=i2c_arm=on"

# 3. Disable the serial login console on the primary UART (ttyAMA0 / serial0).
echo "Disabling serial login console on primary UART..."
systemctl stop  serial-getty@ttyAMA0.service 2>/dev/null || true
systemctl disable serial-getty@ttyAMA0.service 2>/dev/null || true
systemctl stop  serial-getty@ttyS0.service   2>/dev/null || true
systemctl disable serial-getty@ttyS0.service   2>/dev/null || true

CMDLINE=/boot/firmware/cmdline.txt
[[ -f "$CMDLINE" ]] || CMDLINE=/boot/cmdline.txt
if [[ -f "$CMDLINE" ]]; then
  sed -i 's/console=serial0,[0-9]* //; s/console=ttyAMA0,[0-9]* //; s/console=ttyS0,[0-9]* //' "$CMDLINE"
fi

echo
echo "Done. Reboot for changes to take effect:  sudo reboot"
echo "After reboot, verify the device node exists:"
echo "    ls -l /dev/ttyAMA5"
echo "Then run the baud-rate sweep:"
echo "    python -m watson_dms detect --port /dev/ttyAMA5"
