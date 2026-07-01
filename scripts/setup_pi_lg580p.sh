#!/usr/bin/env bash
# One-time Raspberry Pi setup for the SparkFun LG580P GNSS Flex pHAT.
#
# The LG580P pHAT uses the Pi's PRIMARY UART (GPIO14/15 -> /dev/serial0). This:
#   1. Enables the UART hardware.
#   2. Maps the capable PL011 to GPIO14/15 (dtoverlay=disable-bt) for reliable
#      high baud rates (up to 921600) — the mini-UART is flaky at those speeds.
#   3. Disables the serial login console so it doesn't fight the receiver.
#
# NOTE: This is separate from setup_pi.sh (the Watson unit's UART5 setup).
# Run with sudo, then reboot:  sudo bash scripts/setup_pi_lg580p.sh && sudo reboot
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root: sudo bash scripts/setup_pi_lg580p.sh" >&2
  exit 1
fi

CONFIG=/boot/firmware/config.txt
[[ -f "$CONFIG" ]] || CONFIG=/boot/config.txt
echo "Using boot config: $CONFIG"

ensure_line() {
  local line="$1"
  grep -qxF "$line" "$CONFIG" || echo "$line" >> "$CONFIG"
}
echo "Enabling primary UART on the PL011 (GPIO14/15 -> /dev/serial0)..."
ensure_line "enable_uart=1"
ensure_line "dtoverlay=disable-bt"

echo "Disabling the serial login console..."
systemctl stop  serial-getty@ttyAMA0.service 2>/dev/null || true
systemctl disable serial-getty@ttyAMA0.service 2>/dev/null || true
systemctl stop  serial-getty@serial0.service  2>/dev/null || true
systemctl disable serial-getty@serial0.service  2>/dev/null || true

CMDLINE=/boot/firmware/cmdline.txt
[[ -f "$CMDLINE" ]] || CMDLINE=/boot/cmdline.txt
if [[ -f "$CMDLINE" ]]; then
  sed -i 's/console=serial0,[0-9]* //; s/console=ttyAMA0,[0-9]* //; s/console=ttyS0,[0-9]* //' "$CMDLINE"
fi

echo
echo "Done. Reboot:  sudo reboot"
echo "After reboot, verify /dev/serial0 and find the baud rate:"
echo "    ls -l /dev/serial0"
echo "    python -m lg580p detect --port /dev/serial0"
