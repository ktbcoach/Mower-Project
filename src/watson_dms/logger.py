"""CSV and GPX logging for parsed DMS-SGP02 readings."""

from __future__ import annotations

import csv
import datetime as _dt
from pathlib import Path
from typing import Optional, TextIO

from .parser import DmsReading

CSV_FIELDS = [
    "host_time",       # ISO-8601 timestamp from the Pi when the line arrived
    "label",
    "heading_mode",
    "over_range",
    "utc",
    "bank_deg",
    "elevation_deg",
    "heading_deg",
    "velocity_kph",
    "latitude_deg",
    "longitude_deg",
    "altitude_ft",
    "altitude_m",
]


class CsvLogger:
    """Append parsed readings to a CSV file (one row per frame)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new_file = not self.path.exists() or self.path.stat().st_size == 0
        self._fh: TextIO = self.path.open("a", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._fh, fieldnames=CSV_FIELDS)
        if new_file:
            self._writer.writeheader()

    def write(self, reading: DmsReading, host_time: Optional[_dt.datetime] = None) -> None:
        host_time = host_time or _dt.datetime.now(_dt.timezone.utc)
        self._writer.writerow(
            {
                "host_time": host_time.isoformat(),
                "label": reading.label,
                "heading_mode": reading.heading_mode,
                "over_range": int(reading.over_range),
                "utc": reading.utc or "",
                "bank_deg": _fmt(reading.bank_deg),
                "elevation_deg": _fmt(reading.elevation_deg),
                "heading_deg": _fmt(reading.heading_deg),
                "velocity_kph": _fmt(reading.velocity_kph),
                "latitude_deg": _fmt(reading.latitude_deg, 6),
                "longitude_deg": _fmt(reading.longitude_deg, 6),
                "altitude_ft": _fmt(reading.altitude_ft),
                "altitude_m": _fmt(reading.altitude_m, 2),
            }
        )

    def flush(self) -> None:
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> "CsvLogger":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class GpxLogger:
    """Write GPS fixes to a GPX 1.1 track for import into mapping tools.

    Only frames with a valid GPS fix are recorded. The GPX header/footer are
    written on open/close, so always use this as a context manager (or call
    :meth:`close`) to produce a valid file.
    """

    def __init__(self, path: str | Path, track_name: str = "DMS-SGP02 track"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh: TextIO = self.path.open("w", encoding="utf-8")
        self._fh.write(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<gpx version="1.1" creator="watson_dms" '
            'xmlns="http://www.topografix.com/GPX/1/1">\n'
            f"  <trk><name>{_xml_escape(track_name)}</name><trkseg>\n"
        )
        self._open = True

    def write(self, reading: DmsReading, host_time: Optional[_dt.datetime] = None) -> bool:
        """Record a fix. Returns False (and writes nothing) if there's no fix."""
        if not reading.has_gps_fix:
            return False
        host_time = host_time or _dt.datetime.now(_dt.timezone.utc)
        ele = reading.altitude_m
        ele_tag = f"<ele>{ele:.2f}</ele>" if ele is not None else ""
        self._fh.write(
            f'    <trkpt lat="{reading.latitude_deg:.6f}" '
            f'lon="{reading.longitude_deg:.6f}">'
            f"{ele_tag}<time>{host_time.isoformat()}</time></trkpt>\n"
        )
        return True

    def flush(self) -> None:
        if self._open:
            self._fh.flush()

    def close(self) -> None:
        if self._open:
            self._fh.write("  </trkseg></trk>\n</gpx>\n")
            self._fh.close()
            self._open = False

    def __enter__(self) -> "GpxLogger":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _fmt(value: Optional[float], places: int = 3) -> str:
    return "" if value is None else f"{value:.{places}f}"


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
