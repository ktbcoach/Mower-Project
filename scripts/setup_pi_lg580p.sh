#!/usr/bin/env bash
# One-time Raspberry Pi setup for the LG580P rover (GNSS pHAT + Multi-IO HAT).
#
#   1. LG580P GNSS pHAT: PRIMARY UART on the PL011 (GPIO14/15 -> /dev/serial0),
#      mapped via dtoverlay=disable-bt for reliable high baud (up to 921600 —
#      the mini-UART is flaky). Serial login console disabled.
#   2. Multi-IO HAT: I2C (switch + status LEDs) and UART5 (GPIO12/13 ->
#      /dev/ttyAMA5) — the HAT's RS232 port carries the RTCM correction radio.
#
# Hardware: the RTCM radio plugs into the Multi-IO HAT's RS232 (UPPER DB9;
# silkscreen reversed). The J2 RX jumper must be installed (GPIO13 / pin 33).
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

echo "Enabling Multi-IO HAT: I2C (switch/LEDs) + UART5 (RS232 RTCM radio)..."
ensure_line "dtparam=i2c_arm=on"
ensure_line "dtoverlay=uart5"   # GPIO12/13 -> /dev/ttyAMA5

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
echo "After reboot, verify the device nodes and find the GNSS baud rate:"
echo "    ls -l /dev/serial0 /dev/ttyAMA5   # GNSS ; HAT RS232 (RTCM radio)"
echo "    python -m lg580p detect --port /dev/serial0"
