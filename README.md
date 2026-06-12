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
│   ├── collect.py        # serial -> parse -> log loop with live status
│   └── __main__.py       # `python -m watson_dms` CLI
├── tests/test_parser.py  # parser tests vs. the manual's worked example
├── scripts/setup_pi.sh   # enable UART, free /dev/serial0 from the console
└── docs/HARDWARE.md       # wiring, power, command-mode reference
```

## Setup on the Pi

```bash
# 1. Free the GPIO UART for the pHAT (then reboot).
sudo bash scripts/setup_pi.sh
sudo reboot

# 2. Install (a venv keeps it tidy).
cd gps-collector
python3 -m venv .venv && source .venv/bin/activate
pip install -e .            # installs pyserial + the `watson-dms` command
```

Add your user to the `dialout` group if you get permission errors on the port:
`sudo usermod -aG dialout $USER` (log out/in afterward).

## Usage

Run as a module (`python -m watson_dms ...`) or via the installed `watson-dms`
command. All commands take `--port` (default `/dev/serial0`).

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
- [ ] Verify against the real unit on the Pi (run `detect` → `capture`)
- [ ] Optional: live web/TUI dashboard, MQTT streaming, binary-format support
