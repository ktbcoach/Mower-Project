"""Live collection loop: serial -> parse -> log -> optional status line."""

from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path
from typing import Optional

from . import serial_reader
from .logger import CsvLogger, GpxLogger
from .parser import parse_line


def _status(reading) -> str:
    fix = "FIX " if reading.has_gps_fix else "----"
    lat = f"{reading.latitude_deg:.6f}" if reading.latitude_deg is not None else "   --.------"
    lon = f"{reading.longitude_deg:.6f}" if reading.longitude_deg is not None else "  ---.------"
    hdg = f"{reading.heading_deg:5.1f}" if reading.heading_deg is not None else "  --.-"
    vel = f"{reading.velocity_kph:5.1f}" if reading.velocity_kph is not None else "  --.-"
    over = "!" if reading.over_range else " "
    return (
        f"{fix}{over} mode={reading.heading_mode:<14} "
        f"lat={lat} lon={lon} hdg={hdg} vel={vel}kph"
    )


def collect(
    port: str = serial_reader.DEFAULT_PORT,
    baud: int = serial_reader.DEFAULT_BAUD,
    csv_path: Optional[str | Path] = None,
    gpx_path: Optional[str | Path] = None,
    quiet: bool = False,
    fix_only: bool = False,
) -> None:
    """Read frames forever, parse them, and log to CSV and/or GPX.

    Stops cleanly on Ctrl-C. ``fix_only`` skips frames without a GPS fix
    (useful when walking a boundary and you only care about position).
    """
    csv_logger = CsvLogger(csv_path) if csv_path else None
    gpx_logger = GpxLogger(gpx_path) if gpx_path else None
    count = 0
    fixes = 0

    try:
        with serial_reader.open_port(port, baud) as ser:
            if not quiet:
                print(f"# Listening on {port} @ {baud} 8N1 — Ctrl-C to stop", file=sys.stderr)
            for line in serial_reader.read_lines(ser):
                reading = parse_line(line)
                if reading is None:
                    if not quiet:
                        print(f"# {line}", file=sys.stderr)  # header / non-data
                    continue
                if fix_only and not reading.has_gps_fix:
                    continue
                now = _dt.datetime.now(_dt.timezone.utc)
                count += 1
                if reading.has_gps_fix:
                    fixes += 1
                if csv_logger:
                    csv_logger.write(reading, now)
                if gpx_logger:
                    gpx_logger.write(reading, now)
                if not quiet:
                    sys.stdout.write("\r" + _status(reading))
                    sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    finally:
        if csv_logger:
            csv_logger.close()
        if gpx_logger:
            gpx_logger.close()
        if not quiet:
            print(f"\n# Done. {count} frames logged, {fixes} with a GPS fix.", file=sys.stderr)
