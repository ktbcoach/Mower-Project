"""Compact rover -> base status telemetry over the correction radio link.

The rover packs its latest fix into a single NMEA-style ``$PRSTAT`` sentence and
sends it back out the RTCM radio; the base reads it and shows it on the station
display. One short line per second keeps it from starving the RTCM stream that
shares the same half-duplex link.

Field order (all numeric, blank = unknown), XOR checksum like standard NMEA:

    $PRSTAT,seq,fixq,satsUsed,satsTracked,cn0Max,cn0Avg,lat,lon,alt,
            hdg,hdgQ,hdop,speedKph,logging,corr*CS

``build_status_sentence`` (rover) and ``parse_status_sentence`` (base) are the
two ends; keeping both here means the wire format has a single definition.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from . import nmea

TALKER = "PRSTAT"

# Field names in wire order (after the talker), for parsing.
_FIELDS = (
    "seq", "fix_quality", "sats_used", "sats_tracked", "cn0_max", "cn0_avg",
    "latitude_deg", "longitude_deg", "altitude_m", "heading_deg",
    "heading_quality", "hdop", "speed_kph", "logging", "corr",
)


@dataclass
class RoverStatus:
    """One decoded telemetry frame from the rover."""

    seq: Optional[int] = None
    fix_quality: Optional[int] = None
    sats_used: Optional[int] = None
    sats_tracked: Optional[int] = None
    cn0_max: Optional[float] = None
    cn0_avg: Optional[float] = None
    latitude_deg: Optional[float] = None
    longitude_deg: Optional[float] = None
    altitude_m: Optional[float] = None
    heading_deg: Optional[float] = None
    heading_quality: Optional[int] = None
    hdop: Optional[float] = None
    speed_kph: Optional[float] = None
    logging: Optional[bool] = None
    corr: Optional[bool] = None

    @property
    def fix_quality_name(self) -> Optional[str]:
        from .reading import FIX_QUALITY
        if self.fix_quality is None:
            return None
        return FIX_QUALITY.get(self.fix_quality, f"unknown({self.fix_quality})")

    @property
    def rtk_fixed(self) -> bool:
        return self.fix_quality == 4

    @property
    def rtk_float(self) -> bool:
        return self.fix_quality == 5


def _checksum(body: str) -> str:
    cs = 0
    for ch in body:
        cs ^= ord(ch)
    return f"{cs:02X}"


def _num(value, fmt: str) -> str:
    return "" if value is None else format(value, fmt)


def _flag(value) -> str:
    return "" if value is None else ("1" if value else "0")


def build_status_sentence(reading, seq: int, logging_on: bool, corr_flowing: bool) -> str:
    """Build a ``$PRSTAT`` sentence from a GnssReading (may be ``None``)."""
    def g(attr):
        return getattr(reading, attr, None) if reading is not None else None

    fields = [
        str(seq & 0xFFFF),
        _num(g("fix_quality"), "d"),
        _num(g("num_sats"), "d"),
        _num(g("sats_tracked"), "d"),
        _num(g("cn0_max"), ".1f"),
        _num(g("cn0_avg"), ".1f"),
        _num(g("latitude_deg"), ".7f"),
        _num(g("longitude_deg"), ".7f"),
        _num(g("altitude_m"), ".1f"),
        _num(g("heading_deg"), ".1f"),
        _num(g("heading_quality"), "d"),
        _num(g("hdop"), ".1f"),
        _num(g("speed_kph"), ".1f"),
        _flag(logging_on),
        _flag(corr_flowing),
    ]
    body = TALKER + "," + ",".join(fields)
    return f"${body}*{_checksum(body)}"


def parse_status_sentence(line: str) -> Optional[RoverStatus]:
    """Parse a ``$PRSTAT`` line, or ``None`` if it isn't one / checksum fails."""
    line = line.strip()
    if not line.startswith("$" + TALKER + ",") or not nmea.checksum_ok(line):
        return None
    star = line.rfind("*")
    parts = line[1:star].split(",")[1:]  # drop the talker
    if len(parts) < len(_FIELDS):
        return None
    d = dict(zip(_FIELDS, parts))

    def _i(v):
        return int(v) if v not in ("", None) else None

    def _fl(v):
        return float(v) if v not in ("", None) else None

    def _b(v):
        return None if v in ("", None) else (v == "1")

    return RoverStatus(
        seq=_i(d["seq"]),
        fix_quality=_i(d["fix_quality"]),
        sats_used=_i(d["sats_used"]),
        sats_tracked=_i(d["sats_tracked"]),
        cn0_max=_fl(d["cn0_max"]),
        cn0_avg=_fl(d["cn0_avg"]),
        latitude_deg=_fl(d["latitude_deg"]),
        longitude_deg=_fl(d["longitude_deg"]),
        altitude_m=_fl(d["altitude_m"]),
        heading_deg=_fl(d["heading_deg"]),
        heading_quality=_i(d["heading_quality"]),
        hdop=_fl(d["hdop"]),
        speed_kph=_fl(d["speed_kph"]),
        logging=_b(d["logging"]),
        corr=_b(d["corr"]),
    )
