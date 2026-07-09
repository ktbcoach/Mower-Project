"""Parsed GNSS reading assembled from LG580P NMEA (and PQTM) sentences.

Unlike the Watson unit (one self-contained line per frame), an NMEA receiver
spreads a fix across several sentences per epoch (GGA for position/quality,
RMC/VTG for velocity, PQTMTAR for dual-antenna heading). :class:`GnssReading`
is the merged result for one epoch, produced by :class:`lg580p.assembler`.

Attribute names ``latitude_deg`` / ``longitude_deg`` / ``altitude_m`` /
``has_gps_fix`` intentionally match the Watson reading so the existing GPX
writer can log either.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Optional

_M_PER_FT = 0.3048

# GGA fix-quality codes -> name (NMEA 0183).
FIX_QUALITY = {
    0: "no_fix",
    1: "gps",
    2: "dgps",
    3: "pps",
    4: "rtk_fixed",
    5: "rtk_float",
    6: "estimated",
    7: "manual",
    8: "simulation",
}


@dataclass
class GnssReading:
    """One assembled GNSS epoch from the LG580P."""

    utc: Optional[str] = None             # "HH:MM:SS.ss"
    utc_seconds: Optional[float] = None   # seconds since midnight UTC
    date: Optional[str] = None            # "YYYY-MM-DD" (from RMC)
    latitude_deg: Optional[float] = None
    longitude_deg: Optional[float] = None
    altitude_m: Optional[float] = None    # MSL altitude (GGA)
    geoid_sep_m: Optional[float] = None
    fix_quality: Optional[int] = None     # GGA quality code
    num_sats: Optional[int] = None
    hdop: Optional[float] = None
    # Correction health (GGA): age of differential data (s) and reference
    # station ID. Age climbing above a few seconds is the classic reason RTK
    # stays Float instead of promoting to Fixed on a bandwidth-limited link.
    age_of_diff: Optional[float] = None
    ref_station_id: Optional[int] = None
    speed_kph: Optional[float] = None     # over ground
    course_deg: Optional[float] = None    # course over ground (track)
    # Dual-antenna heading (PQTMTAR) — populated once that parser is finalized.
    heading_deg: Optional[float] = None      # true heading (THS / dual-antenna)
    heading_quality: Optional[int] = None    # PQTMTAR heading quality indicator
    pitch_deg: Optional[float] = None
    roll_deg: Optional[float] = None
    heading_accuracy_deg: Optional[float] = None
    baseline_m: Optional[float] = None
    # Signal-quality summary for the epoch (aggregated from GSV C/N0 across all
    # constellations; best C/N0 kept per satellite). Handy for spotting a weak
    # antenna at a glance.
    sats_tracked: Optional[int] = None       # unique sats in view with a C/N0
    cn0_max: Optional[float] = None          # strongest satellite C/N0 (dB-Hz)
    cn0_avg: Optional[float] = None          # mean C/N0 of tracked sats (dB-Hz)
    # Which sentence types contributed to this epoch (diagnostic).
    sources: list = field(default_factory=list)

    @property
    def fix_quality_name(self) -> Optional[str]:
        if self.fix_quality is None:
            return None
        return FIX_QUALITY.get(self.fix_quality, f"unknown({self.fix_quality})")

    @property
    def has_gps_fix(self) -> bool:
        return (
            self.fix_quality is not None
            and self.fix_quality > 0
            and self.latitude_deg is not None
            and self.longitude_deg is not None
        )

    @property
    def rtk_fixed(self) -> bool:
        return self.fix_quality == 4

    @property
    def rtk_float(self) -> bool:
        return self.fix_quality == 5

    @property
    def altitude_ft(self) -> Optional[float]:
        if self.altitude_m is None:
            return None
        return self.altitude_m / _M_PER_FT

    def as_dict(self) -> dict:
        return asdict(self)
