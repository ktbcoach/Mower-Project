"""Live collection loops: serial -> parse -> log.

Two modes:
  * :func:`collect` — always logs to fixed CSV/GPX paths (manual / dev use).
  * :func:`collect_switched` — a long-running service driven by a physical
    switch. The process stays up and synced to the serial stream; flipping the
    switch ON opens a fresh timestamped log session, OFF closes it. A status
    LED reflects the state.
"""

from __future__ import annotations

import datetime as _dt
import sys
import time
from pathlib import Path
from typing import Optional

from . import serial_reader
from .logger import CsvLogger, GpxLogger
from .parser import parse_line

# How often to flush buffered CSV/GPX data to disk. A mower can lose power at
# any moment, so we don't want much sitting in the buffer.
_FLUSH_INTERVAL_S = 2.0


def _log(msg: str) -> None:
    """Operational message to stderr (captured by journald under systemd)."""
    print(f"# {msg}", file=sys.stderr, flush=True)


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


class _Session:
    """One logging session: a paired CSV (+ optional GPX) under ``log_dir``."""

    def __init__(self, log_dir: Path, gpx: bool):
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.csv = CsvLogger(log_dir / f"dms-{stamp}.csv")
        self.gpx = GpxLogger(log_dir / f"dms-{stamp}.gpx") if gpx else None
        self.count = 0
        self.fixes = 0
        self.name = self.csv.path.name

    def write(self, reading, now) -> None:
        self.csv.write(reading, now)
        if self.gpx:
            self.gpx.write(reading, now)
        self.count += 1
        if reading.has_gps_fix:
            self.fixes += 1

    def flush(self) -> None:
        self.csv.flush()
        if self.gpx:
            self.gpx.flush()

    def close(self) -> None:
        self.csv.close()
        if self.gpx:
            self.gpx.close()


def collect_switched(
    port: str = serial_reader.DEFAULT_PORT,
    baud: int = serial_reader.DEFAULT_BAUD,
    controls=None,
    log_dir: str | Path = "logs",
    fix_only: bool = False,
    gpx: bool = True,
    quiet: bool = False,
) -> None:
    """Switch-gated collection service.

    ``controls`` is a :class:`watson_dms.switch.LoggingControls`. The loop runs
    until interrupted; logging starts/stops with the switch, one file set per
    ON period. Designed to be launched by systemd at boot.
    """
    if controls is None:
        raise ValueError("collect_switched requires a LoggingControls instance")

    log_dir = Path(log_dir)
    session: Optional[_Session] = None
    last_flush = time.monotonic()

    _log(f"service up on {port} @ {baud} 8N1; waiting for switch")
    try:
        with serial_reader.open_port(port, baud) as ser:
            for line in serial_reader.read_lines(ser):
                logging_on = controls.logging_on

                # Handle switch transitions.
                if logging_on and session is None:
                    session = _Session(log_dir, gpx)
                    _log(f"logging STARTED -> {session.name}")
                elif not logging_on and session is not None:
                    _log(f"logging STOPPED ({session.count} frames, "
                         f"{session.fixes} fixes) -> {session.name}")
                    session.close()
                    session = None

                reading = parse_line(line)
                if reading is None:
                    continue  # header / noise

                controls.update_indicator(logging_on, reading.has_gps_fix)

                if session is not None and not (fix_only and not reading.has_gps_fix):
                    session.write(reading, _dt.datetime.now(_dt.timezone.utc))

                now = time.monotonic()
                if session is not None and now - last_flush >= _FLUSH_INTERVAL_S:
                    session.flush()
                    last_flush = now

                if not quiet:
                    state = "LOG" if session is not None else "idle"
                    sys.stdout.write(f"\r[{state}] {_status(reading)}")
                    sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    finally:
        if session is not None:
            session.close()
            _log(f"logging stopped on exit -> {session.name}")
        controls.update_indicator(False, False)
        controls.close()
        _log("service stopped")


def collect(
    port: str = serial_reader.DEFAULT_PORT,
    baud: int = serial_reader.DEFAULT_BAUD,
    csv_path: Optional[str | Path] = None,
    gpx_path: Optional[str | Path] = None,
    quiet: bool = False,
    fix_only: bool = False,
) -> None:
    """Continuous collection to fixed paths (no switch). Stops on Ctrl-C."""
    csv_logger = CsvLogger(csv_path) if csv_path else None
    gpx_logger = GpxLogger(gpx_path) if gpx_path else None
    count = 0
    fixes = 0

    try:
        with serial_reader.open_port(port, baud) as ser:
            if not quiet:
                _log(f"Listening on {port} @ {baud} 8N1 — Ctrl-C to stop")
            for line in serial_reader.read_lines(ser):
                reading = parse_line(line)
                if reading is None:
                    if not quiet:
                        _log(line)  # header / non-data
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
            _log(f"Done. {count} frames logged, {fixes} with a GPS fix.")
