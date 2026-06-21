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
│   ├── switch.py         # GPIO logging switch + status LED (gpiozero)
│   ├── collect.py        # collection loops (continuous + switch-gated)
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

# 2. Install. Use --system-site-packages so the OS-provided gpiozero/lgpio
#    (for the GPIO switch + LED) are visible inside the venv.
cd gps-collector
python3 -m venv --system-site-packages .venv && source .venv/bin/activate
pip install -e .            # pyserial + the `watson-dms` command
```

Make sure your user is in the `dialout` (serial) and `gpio` (GPIO) groups:
`sudo usermod -aG dialout,gpio $USER` (log out/in afterward).

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

## Auto-start at boot with a physical switch

The intended field setup: the Pi boots straight into the logger, and a toggle
switch on the mower starts/stops recording. See
[`docs/HARDWARE.md`](docs/HARDWARE.md#logging-switch--status-led-gpio) for wiring
(switch → GPIO16/GND, LED → GPIO26).

```bash
# Install + enable the service (auto-starts on every boot).
sudo bash scripts/install_service.sh
# Override pins/port if needed:
#   sudo SWITCH_PIN=16 LED_PIN=26 PORT=/dev/ttyAMA5 BAUD=9600 \
#        bash scripts/install_service.sh

systemctl status watson-dms        # check it's running
journalctl -u watson-dms -f        # watch session start/stop live
```

Behavior: the service stays up and synced to the serial stream. Flip the switch
**ON** and it opens a fresh `logs/dms-<timestamp>.csv` + `.gpx`; flip **OFF** and
it flushes and closes them. The **LED** is off when idle, blinks while searching
for a fix, and is solid once logging with a GPS fix. CSV is flushed every ~2 s so
an abrupt power-off loses at most a couple of seconds.

To try switch mode by hand (without the service):

```bash
python -m watson_dms collect --switch --switch-pin 16 --led-pin 26
```

CSV columns include host timestamp, heading mode, over-range flag, UTC,
bank/elevation/heading, velocity, lat/lon, and altitude (ft + m). GPX contains
only valid GPS fixes, ready to import into QGIS / Google Earth.

## Using the parser as a library

```python
from watson_dms import parse_line

r = parse_line("G 161409.9 -000.8 +00.1 273.4 +028.9 +44.86405 -091.46836 00894")
print(r.heading_mode, r.heading_deg, r.latitude_deg, r.longitude_deg, r.has_gps_fix)
# gps_true_north 273.4 44.86405 -91.46836 True
```

If you reconfigure the unit's output channels (manual Appendix A), pass a
matching `channels=` list to `parse_line`.

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
- [x] Switch-gated logging + status LED (GPIO)
- [x] systemd service for auto-start at boot
- [ ] Verify against the real unit on the Pi (run `detect` → `capture`)
- [ ] Verify switch/LED + service on the Pi
- [ ] Optional: live web/TUI dashboard, MQTT streaming, binary-format support
