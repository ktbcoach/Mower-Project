#!/usr/bin/env bash
# One-time Raspberry Pi setup for the RS232 pHAT + DMS-SGP02.
#
# This frees the GPIO UART (GPIO14/15 -> /dev/serial0) for the pHAT by:
#   1. disabling the serial *login console* (which otherwise owns the port), and
#   2. enabling the serial *hardware*.
# On a Pi 4 it also disables Bluetooth so the capable PL011 UART (ttyAMA0) is
# mapped to the GPIO pins instead of the lower-quality mini-UART.
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

# 1. Disable the serial login console.
echo "Disabling serial login console..."
systemctl stop serial-getty@ttyAMA0.service 2>/dev/null || true
systemctl disable serial-getty@ttyAMA0.service 2>/dev/null || true
systemctl stop serial-getty@ttyS0.service 2>/dev/null || true
systemctl disable serial-getty@ttyS0.service 2>/dev/null || true

# Remove any console=serial0/ttyAMA0/ttyS0 entry from the kernel cmdline.
CMDLINE=/boot/firmware/cmdline.txt
[[ -f "$CMDLINE" ]] || CMDLINE=/boot/cmdline.txt
if [[ -f "$CMDLINE" ]]; then
  sed -i 's/console=serial0,[0-9]* //; s/console=ttyAMA0,[0-9]* //; s/console=ttyS0,[0-9]* //' "$CMDLINE"
fi

# 2. Enable the UART hardware and pin the PL011 to the GPIO header.
ensure_line() {
  local line="$1"
  grep -qxF "$line" "$CONFIG" || echo "$line" >> "$CONFIG"
}
echo "Enabling UART hardware in $CONFIG..."
ensure_line "enable_uart=1"
ensure_line "dtoverlay=disable-bt"   # Pi 4: give the PL011 to GPIO14/15

echo
echo "Done. Reboot for changes to take effect:  sudo reboot"
echo "After reboot, /dev/serial0 should point at the GPIO UART:"
echo "    ls -l /dev/serial0"
