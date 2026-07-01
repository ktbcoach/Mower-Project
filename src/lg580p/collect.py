"""Live collection: serial -> NMEA assembler -> log, continuous or switch-gated."""

from __future__ import annotations

import datetime as _dt
import sys
import time
from pathlib import Path
from typing import Optional

from . import serial_io
from .assembler import GnssAssembler
from .logger import CsvLogger, GpxLogger

_FLUSH_INTERVAL_S = 2.0


def _log(msg: str) -> None:
    print(f"# {msg}", file=sys.stderr, flush=True)


def _corr_tag(injector) -> str:
    if injector is None:
        return ""
    return "CORR " if injector.flowing else "  .  "


def _status(r) -> str:
    q = r.fix_quality_name or "----"
    lat = f"{r.latitude_deg:.7f}" if r.latitude_deg is not None else "   --.-------"
    lon = f"{r.longitude_deg:.7f}" if r.longitude_deg is not None else "  ---.-------"
    hdg = f"{r.heading_deg:5.1f}" if r.heading_deg is not None else "  --.-"
    sats = f"{r.num_sats:2d}" if r.num_sats is not None else "--"
    return f"{q:<9} sats={sats} lat={lat} lon={lon} hdg={hdg}"


class _Session:
    def __init__(self, log_dir: Path, gpx: bool):
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.csv = CsvLogger(log_dir / f"lg580p-{stamp}.csv")
        self.gpx = GpxLogger(log_dir / f"lg580p-{stamp}.gpx") if gpx else None
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


def _make_injector(ser, rtcm_source, rtcm_baud):
    if not rtcm_source:
        return None
    from .rtcm import RtcmInjector
    inj = RtcmInjector(ser, rtcm_source, rtcm_baud)
    inj.start()
    _log(f"RTCM injection from {rtcm_source} @ {rtcm_baud}")
    return inj


def collect(
    port: str = serial_io.DEFAULT_PORT,
    baud: int = serial_io.DEFAULT_BAUD,
    csv_path: Optional[str | Path] = None,
    gpx_path: Optional[str | Path] = None,
    quiet: bool = False,
    fix_only: bool = False,
    emit_on: str = "GGA",
    rtcm_source: Optional[str] = None,
    rtcm_baud: int = 57600,
) -> None:
    """Continuous logging to fixed paths (no switch). Stops on Ctrl-C."""
    asm = GnssAssembler(emit_on=emit_on)
    csv_logger = CsvLogger(csv_path) if csv_path else None
    gpx_logger = GpxLogger(gpx_path) if gpx_path else None
    injector = None
    count = fixes = 0
    try:
        with serial_io.open_port(port, baud) as ser:
            injector = _make_injector(ser, rtcm_source, rtcm_baud)
            if not quiet:
                _log(f"Listening on {port} @ {baud} 8N1 — Ctrl-C to stop")
            for line in serial_io.read_lines(ser):
                reading = asm.push(line)
                if reading is None:
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
                    sys.stdout.write("\r" + _corr_tag(injector) + _status(reading))
                    sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    finally:
        if injector:
            injector.stop()
        if csv_logger:
            csv_logger.close()
        if gpx_logger:
            gpx_logger.close()
        if not quiet:
            _log(f"Done. {count} epochs logged, {fixes} with a fix.")


def collect_switched(
    port: str = serial_io.DEFAULT_PORT,
    baud: int = serial_io.DEFAULT_BAUD,
    controls=None,
    log_dir: str | Path = "logs",
    fix_only: bool = False,
    gpx: bool = True,
    quiet: bool = False,
    emit_on: str = "GGA",
    rtcm_source: Optional[str] = None,
    rtcm_baud: int = 57600,
) -> None:
    """Switch-gated collection service (one file set per switch-ON period)."""
    if controls is None:
        raise ValueError("collect_switched requires a controls instance")

    asm = GnssAssembler(emit_on=emit_on)
    log_dir = Path(log_dir)
    session: Optional[_Session] = None
    last_reading = None
    last_flush = time.monotonic()
    injector = None

    _log(f"service up on {port} @ {baud} 8N1; waiting for switch")
    try:
        with serial_io.open_port(port, baud, timeout=0.25) as ser:
            injector = _make_injector(ser, rtcm_source, rtcm_baud)
            for line in serial_io.read_lines(ser, idle_tick=True):
                logging_on = controls.logging_on

                if logging_on and session is None:
                    session = _Session(log_dir, gpx)
                    _log(f"logging STARTED -> {session.name}")
                elif not logging_on and session is not None:
                    _log(f"logging STOPPED ({session.count} epochs, "
                         f"{session.fixes} fixes) -> {session.name}")
                    session.close()
                    session = None

                reading = asm.push(line) if line is not None else None
                if reading is not None:
                    last_reading = reading
                    if session is not None and not (fix_only and not reading.has_gps_fix):
                        session.write(reading, _dt.datetime.now(_dt.timezone.utc))

                controls.update_indicator(session is not None, last_reading)

                now = time.monotonic()
                if session is not None and now - last_flush >= _FLUSH_INTERVAL_S:
                    session.flush()
                    last_flush = now

                if not quiet and reading is not None:
                    state = "LOG" if session is not None else "idle"
                    sys.stdout.write(f"\r[{state}] {_corr_tag(injector)}{_status(reading)}")
                    sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    finally:
        if injector:
            injector.stop()
        if session is not None:
            session.close()
            _log(f"logging stopped on exit -> {session.name}")
        controls.update_indicator(False, None)
        controls.close()
        _log("service stopped")
