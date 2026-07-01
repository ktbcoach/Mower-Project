"""CSV and GPX logging for assembled LG580P GnssReadings."""

from __future__ import annotations

import csv
import datetime as _dt
from pathlib import Path
from typing import Optional, TextIO

from .reading import GnssReading

CSV_FIELDS = [
    "host_time",       # ISO-8601 timestamp from the Pi when the epoch completed
    "utc",
    "date",
    "latitude_deg",
    "longitude_deg",
    "altitude_m",
    "fix_quality",
    "fix_quality_name",
    "num_sats",
    "hdop",
    "speed_kph",
    "course_deg",
    "heading_deg",
    "heading_quality",
    "pitch_deg",
    "roll_deg",
    "baseline_m",
    "sources",
]


class CsvLogger:
    """Append assembled readings to a CSV file (one row per epoch)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new_file = not self.path.exists() or self.path.stat().st_size == 0
        self._fh: TextIO = self.path.open("a", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._fh, fieldnames=CSV_FIELDS)
        if new_file:
            self._writer.writeheader()

    def write(self, r: GnssReading, host_time: Optional[_dt.datetime] = None) -> None:
        host_time = host_time or _dt.datetime.now(_dt.timezone.utc)
        self._writer.writerow(
            {
                "host_time": host_time.isoformat(),
                "utc": r.utc or "",
                "date": r.date or "",
                "latitude_deg": _fmt(r.latitude_deg, 7),
                "longitude_deg": _fmt(r.longitude_deg, 7),
                "altitude_m": _fmt(r.altitude_m, 3),
                "fix_quality": "" if r.fix_quality is None else r.fix_quality,
                "fix_quality_name": r.fix_quality_name or "",
                "num_sats": "" if r.num_sats is None else r.num_sats,
                "hdop": _fmt(r.hdop, 2),
                "speed_kph": _fmt(r.speed_kph, 3),
                "course_deg": _fmt(r.course_deg, 2),
                "heading_deg": _fmt(r.heading_deg, 2),
                "heading_quality": "" if r.heading_quality is None else r.heading_quality,
                "pitch_deg": _fmt(r.pitch_deg, 2),
                "roll_deg": _fmt(r.roll_deg, 2),
                "baseline_m": _fmt(r.baseline_m, 3),
                "sources": "|".join(r.sources),
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
    """Write GPS fixes to a GPX 1.1 track. Only frames with a fix are recorded."""

    def __init__(self, path: str | Path, track_name: str = "LG580P track"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh: TextIO = self.path.open("w", encoding="utf-8")
        self._fh.write(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<gpx version="1.1" creator="lg580p" '
            'xmlns="http://www.topografix.com/GPX/1/1">\n'
            f"  <trk><name>{_xml_escape(track_name)}</name><trkseg>\n"
        )
        self._open = True

    def write(self, r: GnssReading, host_time: Optional[_dt.datetime] = None) -> bool:
        if not r.has_gps_fix:
            return False
        host_time = host_time or _dt.datetime.now(_dt.timezone.utc)
        ele = f"<ele>{r.altitude_m:.3f}</ele>" if r.altitude_m is not None else ""
        self._fh.write(
            f'    <trkpt lat="{r.latitude_deg:.7f}" lon="{r.longitude_deg:.7f}">'
            f"{ele}<time>{host_time.isoformat()}</time></trkpt>\n"
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


def _fmt(value: Optional[float], places: int) -> str:
    return "" if value is None else f"{value:.{places}f}"


def _xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
