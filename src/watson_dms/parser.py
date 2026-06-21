"""Parser for the Watson DMS-SGP02 decimal ASCII serial output.

The DMS-SGP02 streams space-delimited, carriage-return-terminated ASCII
"strings" over RS-232. Each string starts with a status label letter, then the
configured data fields. Example (factory-default channel set)::

    G 161409.9 -000.8 +00.1 273.4 +028.9 +44.86405 -091.46836 00894 <CR>
    └─ label

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
Appendix A, "Set Output Channels"). ``DEFAULT_CHANNELS`` is the set this unit is
currently configured to emit. ``FACTORY_CHANNELS`` is the original factory
layout, kept for reference. To parse a different configuration, pass a matching
``channels`` list to :func:`parse_line`.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional, Sequence

# Original factory-default string (manual p.7-8), kept for reference/tests.
FACTORY_CHANNELS: tuple[str, ...] = (
    "time",
    "bank",
    "elevation",
    "heading",
    "velocity",
    "latitude",
    "longitude",
    "altitude",
)

# Current unit configuration (set via the DMS "Set Output Channels" menu):
# time, heading, X/Y/Z accel, X/Y/Z angular rate, heading rate, velocity,
# latitude+longitude, status bits.
DEFAULT_CHANNELS: tuple[str, ...] = (
    "time",
    "heading",
    "x_accel",
    "y_accel",
    "z_accel",
    "x_rate",
    "y_rate",
    "z_rate",
    "heading_rate",
    "velocity",
    "latitude",
    "longitude",
    "status",
)

# Known layouts keyed by field count, used to auto-detect which configuration
# the unit is emitting. The DMS reverts to its EEPROM default on power-up, so a
# custom channel set only sticks if saved (send a '"' in command mode) — until
# then it falls back to the factory 8-field string. The two known layouts have
# distinct field counts (8 vs 13), so the count is unambiguous.
_KNOWN_LAYOUTS = {
    len(FACTORY_CHANNELS): FACTORY_CHANNELS,
    len(DEFAULT_CHANNELS): DEFAULT_CHANNELS,
}

# Sentinel: let parse_line pick the layout from the field count.
AUTO = "auto"

# Valid leading label characters (uppercase = nominal, lowercase = over-range).
_LABELS = "GTIRgtir"

_HEADING_MODE = {
    "g": "gps_true_north",
    "t": "gps_track",
    "i": "relative",
    "r": "reference",
}

_FT_PER_M = 3.280839895

# Channel name -> DmsReading attribute, for fields parsed as a plain float.
_FLOAT_FIELDS = {
    "bank": "bank_deg",
    "elevation": "elevation_deg",
    "heading": "heading_deg",
    "velocity": "velocity_kph",
    "latitude": "latitude_deg",
    "longitude": "longitude_deg",
    "altitude": "altitude_ft",
    "x_accel": "x_accel_g",
    "y_accel": "y_accel_g",
    "z_accel": "z_accel_g",
    "forward_accel": "forward_accel_g",
    "lateral_accel": "lateral_accel_g",
    "vertical_accel": "vertical_accel_g",
    "x_rate": "x_rate_dps",
    "y_rate": "y_rate_dps",
    "z_rate": "z_rate_dps",
    "heading_rate": "heading_rate_dps",
    "temperature": "temperature_c",
}


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
    # Accelerations (g)
    x_accel_g: Optional[float] = None
    y_accel_g: Optional[float] = None
    z_accel_g: Optional[float] = None
    forward_accel_g: Optional[float] = None
    lateral_accel_g: Optional[float] = None
    vertical_accel_g: Optional[float] = None
    # Angular rates (deg/s)
    x_rate_dps: Optional[float] = None
    y_rate_dps: Optional[float] = None
    z_rate_dps: Optional[float] = None
    heading_rate_dps: Optional[float] = None
    # Other
    temperature_c: Optional[float] = None
    status: Optional[int] = None          # status bits (parsed from octal)
    status_raw: Optional[str] = None      # raw octal token, e.g. "040"
    flags: Optional[int] = None           # flag bits (parsed from octal)
    flags_raw: Optional[str] = None

    @property
    def altitude_m(self) -> Optional[float]:
        if self.altitude_ft is None:
            return None
        return self.altitude_ft / _FT_PER_M

    @property
    def has_gps_fix(self) -> bool:
        """True when both latitude and longitude are valid."""
        return self.latitude_deg is not None and self.longitude_deg is not None

    @property
    def ready(self) -> Optional[bool]:
        """Status 'Ready' flag (bit 5), or None if status not in the stream."""
        if self.status is None:
            return None
        return bool((self.status >> 5) & 1)

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


def _parse_octal(token: str) -> tuple[Optional[str], Optional[int]]:
    """Status/flag bits are 3 octal ASCII digits, e.g. '040'."""
    if _is_invalid(token):
        return None, None
    try:
        return token, int(token, 8)
    except ValueError:
        return token, None


def _parse_utc(token: str) -> tuple[Optional[str], Optional[float]]:
    """Convert an ``HHMMSS.S`` token to ("HH:MM:SS.s", seconds-since-midnight)."""
    if _is_invalid(token):
        return None, None
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
    channels: "Sequence[str] | str" = AUTO,
    strict: bool = False,
) -> Optional[DmsReading]:
    """Parse one DMS-SGP02 decimal ASCII line into a :class:`DmsReading`.

    With ``channels=AUTO`` (the default), the field layout is auto-detected from
    the field count (factory 8-field or the custom 13-field config). Pass an
    explicit channel list to force a specific layout.

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

    if channels == AUTO:
        layout = _KNOWN_LAYOUTS.get(len(tokens))
        if layout is None:
            if strict:
                raise ParseError(
                    f"no known layout for {len(tokens)} fields: {stripped!r}"
                )
            return None
        channels = layout
    elif len(tokens) != len(channels):
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
        elif name in _FLOAT_FIELDS:
            setattr(reading, _FLOAT_FIELDS[name], _to_float(token))
        elif name == "status":
            reading.status_raw, reading.status = _parse_octal(token)
        elif name == "flags":
            reading.flags_raw, reading.flags = _parse_octal(token)
        else:
            raise ParseError(f"unknown channel name {name!r}")

    return reading
