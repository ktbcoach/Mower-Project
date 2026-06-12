"""Parser for the Watson DMS-SGP02 decimal ASCII serial output.

The DMS-SGP02 streams space-delimited, carriage-return-terminated ASCII
"strings" over RS-232. The factory-default string looks like::

    G 161409.9 -000.8 +00.1 273.4 +028.9 +44.86405 -091.46836 00894 <CR>
    │   │        │      │     │     │      │          │           │
    │   UTC      Bank   Elev  Head  Vel    Latitude   Longitude   Altitude(ft)
    └─ status label

The leading label letter encodes the heading source and an over-range flag:

    G / g   GPS True North heading (dual-antenna GPS valid)
    T / t   GPS Ground Track heading (true-north lost, still moving)
    I / i   Relative/inertial heading (GPS unavailable)
    R / r   Reference mode (diagnostic)

A lowercase label means an attitude/heading over-range error is active for
that frame (see manual, RS-232 Output Format).

Invalid numeric fields are transmitted as asterisks, e.g. ``******.*`` for an
invalid UTC or ``+**.*****`` for an invalid latitude. Those parse to ``None``.

The set of channels in the string is user-configurable on the unit (manual
Appendix A). ``DEFAULT_CHANNELS`` matches the documented factory-default
string above. If you reconfigure the unit's output channels, pass a matching
``channels`` list to :func:`parse_line`.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional, Sequence

# Channel order of the factory-default decimal string (manual p.7-8).
DEFAULT_CHANNELS: tuple[str, ...] = (
    "time",
    "bank",
    "elevation",
    "heading",
    "velocity",
    "latitude",
    "longitude",
    "altitude",
)

# Valid leading label characters (uppercase = nominal, lowercase = over-range).
_LABELS = "GTIRgtir"

_HEADING_MODE = {
    "g": "gps_true_north",
    "t": "gps_track",
    "i": "relative",
    "r": "reference",
}

_FT_PER_M = 3.280839895


@dataclass
class DmsReading:
    """One parsed frame from the DMS-SGP02."""

    raw: str
    label: str
    heading_mode: str
    over_range: bool
    utc: Optional[str] = None             # "HH:MM:SS.s"
    utc_seconds: Optional[float] = None   # seconds since midnight UTC
    bank_deg: Optional[float] = None
    elevation_deg: Optional[float] = None
    heading_deg: Optional[float] = None
    velocity_kph: Optional[float] = None
    latitude_deg: Optional[float] = None
    longitude_deg: Optional[float] = None
    altitude_ft: Optional[float] = None

    @property
    def altitude_m(self) -> Optional[float]:
        if self.altitude_ft is None:
            return None
        return self.altitude_ft / _FT_PER_M

    @property
    def has_gps_fix(self) -> bool:
        """True when both latitude and longitude are valid."""
        return self.latitude_deg is not None and self.longitude_deg is not None

    def as_dict(self) -> dict:
        return asdict(self)


class ParseError(ValueError):
    """Raised when a line cannot be interpreted as a DMS data string."""


def _is_invalid(token: str) -> bool:
    """A field filled with asterisks marks invalid data."""
    return "*" in token


def _to_float(token: str) -> Optional[float]:
    if _is_invalid(token):
        return None
    return float(token)


def _parse_utc(token: str) -> tuple[Optional[str], Optional[float]]:
    """Convert an ``HHMMSS.S`` token to ("HH:MM:SS.s", seconds-since-midnight)."""
    if _is_invalid(token):
        return None, None
    # Format is six integer digits, a decimal point and one fractional digit.
    whole, _, frac = token.partition(".")
    whole = whole.zfill(6)
    hh = int(whole[0:2])
    mm = int(whole[2:4])
    ss = int(whole[4:6])
    tenths = int(frac) if frac else 0
    pretty = f"{hh:02d}:{mm:02d}:{ss:02d}.{tenths:d}"
    seconds = hh * 3600 + mm * 60 + ss + tenths / 10.0
    return pretty, seconds


def is_data_line(line: str) -> bool:
    """Cheap check: does this look like a DMS data string (vs. a header line)?"""
    line = line.strip()
    return bool(line) and line[0] in _LABELS


def parse_line(
    line: str,
    channels: Sequence[str] = DEFAULT_CHANNELS,
    strict: bool = False,
) -> Optional[DmsReading]:
    """Parse one DMS-SGP02 decimal ASCII line into a :class:`DmsReading`.

    Returns ``None`` for blank lines and non-data lines (e.g. the power-on
    identification header), unless ``strict`` is set, in which case a
    :class:`ParseError` is raised for anything that is not a valid data frame.
    """
    raw = line.rstrip("\r\n")
    stripped = raw.strip()
    if not stripped:
        if strict:
            raise ParseError("empty line")
        return None

    label = stripped[0]
    if label not in _LABELS:
        if strict:
            raise ParseError(f"unrecognized label {label!r} in: {stripped!r}")
        return None  # header / init message / noise

    tokens = stripped[1:].split()
    if len(tokens) != len(channels):
        if strict:
            raise ParseError(
                f"expected {len(channels)} fields, got {len(tokens)}: {stripped!r}"
            )
        return None

    reading = DmsReading(
        raw=raw,
        label=label,
        heading_mode=_HEADING_MODE[label.lower()],
        over_range=label.islower(),
    )

    for name, token in zip(channels, tokens):
        if name == "time":
            reading.utc, reading.utc_seconds = _parse_utc(token)
        elif name == "bank":
            reading.bank_deg = _to_float(token)
        elif name == "elevation":
            reading.elevation_deg = _to_float(token)
        elif name == "heading":
            reading.heading_deg = _to_float(token)
        elif name == "velocity":
            reading.velocity_kph = _to_float(token)
        elif name == "latitude":
            reading.latitude_deg = _to_float(token)
        elif name == "longitude":
            reading.longitude_deg = _to_float(token)
        elif name == "altitude":
            reading.altitude_ft = _to_float(token)
        else:
            raise ParseError(f"unknown channel name {name!r}")

    return reading
