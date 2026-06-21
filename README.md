# gps-collector

Data collection system for a **Watson DMS-SGP02** (dual-antenna GPS + inertial
measurement system) connected to a **Raspberry Pi 4** through an **RS232 pHAT**.

The DMS-SGP02 streams true-north heading, attitude, angular rates,
accelerations, and GPS position over RS-232. This project reads that stream,
parses it, and logs it to CSV and/or GPX — built for capturing position and
heading tracks (e.g. recording mowing boundaries and paths).

> New here? Read [`docs/HARDWARE.md`](docs/HARDWARE.md) first — wiring, **12 V
> power (the Pi can't power the unit)**, and the serial protocol.

## Layout

```
gps-collector/
├── src/watson_dms/
│   ├── parser.py         # decimal ASCII string -> DmsReading  (pure, tested)
│   ├── serial_reader.py  # pyserial wrapper, CR-terminated line reader
│   ├── capture.py        # raw dump + baud-rate auto-detect (run this first)
│   ├── logger.py         # CSV and GPX writers
│   ├── hat_controls.py   # Multi-IO HAT dry-contact input + LEDs over I2C (default)
│   ├── switch.py         # alt: switch + LED wired to Pi GPIO (gpiozero)
│   ├── collect.py        # collection loops (continuous + button/switch-gated)
│   └── __main__.py       # `python -m watson_dms` CLI
├── tests/test_parser.py     # parser tests vs. the manual's worked example
├── scripts/setup_pi.sh      # enable UART5 + I2C, free the serial console
├── scripts/watson-dms.service   # systemd unit template
├── scripts/install_service.sh   # fills the template + enables auto-start
└── docs/HARDWARE.md          # wiring, power, switch/LED, command-mode reference
```

## Setup on the Pi

```bash
# 1. Enable UART5 (/dev/ttyAMA5) + I2C for the Multi-IO HAT, then reboot.
sudo bash scripts/setup_pi.sh
sudo reboot

# 2. Install. --system-site-packages lets OS-provided libs (gpiozero/lgpio)
#    be visible; the [pi] extra adds multiio for the HAT button/LEDs.
cd gps-collector
python3 -m venv --system-site-packages .venv && source .venv/bin/activate
pip install -e ".[pi]"      # pyserial + SMmultiio + gpiozero + `watson-dms` cmd
```

Make sure your user is in the `dialout` (serial), `i2c` (HAT), and `gpio`
groups: `sudo usermod -aG dialout,i2c,gpio $USER` (log out/in afterward).

> The Sequent HAT library is `SMmultiio` on PyPI (it imports as `multiio`).
> If it won't install, get it from
> [github.com/SequentMicrosystems/multiio-rpi](https://github.com/SequentMicrosystems/multiio-rpi)
> (`python` subdir). Verify the HAT is seen with `i2cdetect -y 1`.

## Usage

Run as a module (`python -m watson_dms ...`) or via the installed `watson-dms`
command. All commands take `--port` (default `/dev/ttyAMA5`).

```bash
# Bring the unit up first: 12 V power, antennas with clear sky view, wait ~5 s
# for init (up to 5 min for first satellite lock).

# 1. Confirm wiring + find the baud rate (does NOT assume the format).
python -m watson_dms detect

# 2. Eyeball the raw frames at a known baud.
python -m watson_dms capture --baud 9600 --seconds 5

# 3. Collect: log to CSV (default) and a GPX track, with a live status line.
python -m watson_dms collect --gpx logs/track.gpx

# Boundary walk — only record frames with a real GPS fix:
python -m watson_dms collect --fix-only --gpx logs/boundary.gpx

# 4. Re-parse a captured text file offline.
python -m watson_dms capture --seconds 10 > raw.txt
python -m watson_dms parse raw.txt
```

## Auto-start at boot with a dry-contact switch

The field setup: the Pi boots straight into the logger, and a toggle switch on
the HAT's **dry-contact input 1** starts/stops recording. A HAT LED shows status.
See [`docs/HARDWARE.md`](docs/HARDWARE.md#logging-switch--status-led).

```bash
# First, map the HAT's LED numbers and confirm the switch toggles OPTO ch1:
python -m watson_dms hat-test

# Install + enable the service (auto-starts on every boot).
sudo bash scripts/install_service.sh
# Override if needed (stack = HAT address, LEDs/contact = channel numbers):
#   sudo HAT_STACK=0 GPS_LED=1 LOGGING_LED=2 CONTACT_CH=1 PORT=/dev/ttyAMA5 \
#        bash scripts/install_service.sh

systemctl status watson-dms        # check it's running
journalctl -u watson-dms -f        # watch session start/stop live
```

Behavior: the service stays up and synced to the serial stream. **Close the
switch** to start logging — it opens a fresh `logs/dms-<timestamp>.csv` + `.gpx`;
**open it** to stop (flush + close). Two HAT LEDs show status independently:
**LED 1 (GPS)** off = no fix, blinking = fix but inertial/track heading, solid =
dual-GPS true-north fix; **LED 2 (logging)** off = idle, blinking = logging. CSV
is flushed every ~2 s so an abrupt power-off loses at most a couple of seconds.

If logging runs *inverted* (records when the switch is open), add
`--contact-invert` to the command (or to the service's `ExecStart` line).

To try it by hand (without the service):

```bash
python -m watson_dms collect --switch       # --source hat, dry-contact ch1, are defaults
# onboard button instead of the switch:
python -m watson_dms collect --switch --hat-input button
# or a switch wired to Pi GPIO instead of the HAT:
python -m watson_dms collect --switch --source gpio --switch-pin 16 --led-pin 26
```

CSV columns include host timestamp, heading mode, over-range flag, UTC,
bank/elevation/heading, velocity, lat/lon, and altitude (ft + m). GPX contains
only valid GPS fixes, ready to import into QGIS / Google Earth.

## Using the parser as a library

```python
from watson_dms import parse_line

# Current channel config (DEFAULT_CHANNELS): time, heading, X/Y/Z accel,
# X/Y/Z rate, heading rate, velocity, lat, lon, status.
r = parse_line("G 161409.9 273.4 +0.01 -0.02 -1.00 +01.5 -00.2 +00.3 +00.0 "
               "+028.9 +44.86405 -091.46836 040")
print(r.heading_mode, r.heading_deg, r.z_accel_g, r.latitude_deg, r.has_gps_fix)
# gps_true_north 273.4 -1.0 44.86405 True
```

The output channel set is configurable on the unit. By default `parse_line`
**auto-detects** the layout by field count — factory 8-field (`FACTORY_CHANNELS`)
or the custom 13-field config (`DEFAULT_CHANNELS`) — which matters because the
DMS reverts to factory output on power-up unless a custom config is saved to
EEPROM. To force a specific layout, pass a `channels=` list to `parse_line`.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

The parser is validated against the exact example string in the owner's manual,
plus invalid-field (asterisk), over-range, and reconfigured-channel cases.

## Status / roadmap

- [x] Decimal ASCII parser (factory-default channel string) + tests
- [x] Serial reader, raw capture, baud auto-detect
- [x] CSV + GPX logging, live collection loop, CLI
- [x] Switch-gated logging + status LED (HAT dry-contact input/LED; button & GPIO options)
- [x] systemd service for auto-start at boot
- [ ] Verify against the real unit on the Pi (run `detect` → `capture`)
- [x] Confirm HAT dry-contact switch toggles OPTO ch1 (`hat-test`)
- [ ] Install + verify the service on the Pi
- [ ] Optional: live web/TUI dashboard, MQTT streaming, binary-format support
