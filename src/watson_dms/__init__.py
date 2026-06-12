"""Data collection toolkit for the Watson DMS-SGP02 GPS/inertial system."""

from .parser import (
    DEFAULT_CHANNELS,
    DmsReading,
    ParseError,
    is_data_line,
    parse_line,
)

__all__ = [
    "DEFAULT_CHANNELS",
    "DmsReading",
    "ParseError",
    "is_data_line",
    "parse_line",
]

__version__ = "0.1.0"
