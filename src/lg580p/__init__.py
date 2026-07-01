"""Data collection toolkit for the SparkFun LG580P (Quectel) RTK GNSS receiver.

NMEA/PQTM-based sibling of the ``watson_dms`` package. Importing this package
pulls in only the pure-Python parser/assembler (no pyserial); the serial and
CLI modules import ``serial_io`` explicitly.
"""

from .assembler import GnssAssembler
from .nmea import checksum_ok, is_sentence, parse
from .reading import FIX_QUALITY, GnssReading

__all__ = [
    "GnssAssembler",
    "GnssReading",
    "FIX_QUALITY",
    "checksum_ok",
    "is_sentence",
    "parse",
]

__version__ = "0.1.0"
