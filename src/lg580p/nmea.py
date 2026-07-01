"""NMEA 0183 sentence parsing for the LG580P.

Low-level, stateless helpers: checksum validation, coordinate conversion, and
per-sentence field extraction. Sentences are talker-agnostic — GGA is handled
whether it arrives as ``$GPGGA``, ``$GNGGA``, ``$GBGGA`` etc. The multi-sentence
merge into a full fix lives in :mod:`lg580p.assembler`.
"""

from __future__ import annotations

from typing import Optional


def checksum_ok(sentence: str) -> bool:
    """Validate the ``*HH`` XOR checksum of an NMEA sentence."""
    sentence = sentence.strip()
    if not sentence.startswith("$"):
        return False
    star = sentence.rfind("*")
    if star == -1 or star + 3 > len(sentence):
        return False
    body = sentence[1:star]
    given = sentence[star + 1 : star + 3]
    calc = 0
    for ch in body:
        calc ^= ord(ch)
    try:
        return calc == int(given, 16)
    except ValueError:
        return False


def is_sentence(line: str) -> bool:
    """Cheap check that a line looks like an NMEA/PQTM sentence."""
    line = line.strip()
    return line.startswith("$") and "," in line


def address(sentence: str) -> Optional[str]:
    """Return the address field (e.g. ``GNGGA`` or ``PQTMTAR``), or None."""
    sentence = sentence.strip()
    if not sentence.startswith("$"):
        return None
    body = sentence[1:]
    star = body.rfind("*")
    if star != -1:
        body = body[:star]
    return body.split(",", 1)[0]


def sentence_type(sentence: str) -> Optional[str]:
    """Normalized type: standard NMEA talker dropped (``GNGGA`` -> ``GGA``),
    proprietary kept whole (``PQTMTAR``)."""
    addr = address(sentence)
    if addr is None:
        return None
    if addr.startswith("P"):  # proprietary (PQTM..., PSTM..., etc.)
        return addr
    return addr[2:] if len(addr) > 2 else addr


def _fields(sentence: str) -> list[str]:
    body = sentence.strip()[1:]
    star = body.rfind("*")
    if star != -1:
        body = body[:star]
    return body.split(",")[1:]  # drop the address field


def _f(value: str) -> Optional[float]:
    return float(value) if value not in ("", None) else None


def _i(value: str) -> Optional[int]:
    return int(value) if value not in ("", None) else None


def parse_coord(value: str, hemi: str, deg_digits: int) -> Optional[float]:
    """NMEA ``ddmm.mmmm`` / ``dddmm.mmmm`` + hemisphere -> signed degrees."""
    if not value or not hemi:
        return None
    try:
        deg = int(value[:deg_digits])
        minutes = float(value[deg_digits:])
    except ValueError:
        return None
    result = deg + minutes / 60.0
    if hemi in ("S", "W"):
        result = -result
    return result


def parse_time(value: str) -> tuple[Optional[str], Optional[float]]:
    """NMEA ``hhmmss.ss`` -> ("HH:MM:SS.ss", seconds since midnight)."""
    if not value:
        return None, None
    try:
        hh = int(value[0:2])
        mm = int(value[2:4])
        ss = float(value[4:])
    except ValueError:
        return None, None
    pretty = f"{hh:02d}:{mm:02d}:{ss:05.2f}"
    return pretty, hh * 3600 + mm * 60 + ss


def parse_date(value: str) -> Optional[str]:
    """RMC ``ddmmyy`` -> ISO ``20YY-MM-DD``."""
    if not value or len(value) != 6:
        return None
    dd, mm, yy = value[0:2], value[2:4], value[4:6]
    return f"20{yy}-{mm}-{dd}"


def parse(sentence: str) -> Optional[dict]:
    """Parse a single supported sentence into a partial-update dict.

    Returns ``None`` for a bad checksum, a non-sentence line, or an unsupported
    type. The dict always carries ``"type"``; other keys are whatever fields the
    sentence provides.
    """
    if not is_sentence(sentence) or not checksum_ok(sentence):
        return None
    typ = sentence_type(sentence)
    f = _fields(sentence)

    if typ == "GGA" and len(f) >= 9:
        utc, secs = parse_time(f[0])
        return {
            "type": "GGA",
            "utc": utc,
            "utc_seconds": secs,
            "latitude_deg": parse_coord(f[1], f[2], 2),
            "longitude_deg": parse_coord(f[3], f[4], 3),
            "fix_quality": _i(f[5]),
            "num_sats": _i(f[6]),
            "hdop": _f(f[7]),
            "altitude_m": _f(f[8]),
            "geoid_sep_m": _f(f[10]) if len(f) > 10 else None,
        }

    if typ == "RMC" and len(f) >= 9:
        # 0=time 1=status 2=lat 3=N/S 4=lon 5=E/W 6=speed(kn) 7=course 8=date
        utc, secs = parse_time(f[0])
        knots = _f(f[6])
        return {
            "type": "RMC",
            "utc": utc,
            "utc_seconds": secs,
            "latitude_deg": parse_coord(f[2], f[3], 2),
            "longitude_deg": parse_coord(f[4], f[5], 3),
            "speed_kph": None if knots is None else knots * 1.852,
            "course_deg": _f(f[7]),
            "date": parse_date(f[8]),
        }

    if typ == "VTG" and len(f) >= 7:
        return {
            "type": "VTG",
            "course_deg": _f(f[0]),   # true track
            "speed_kph": _f(f[6]),    # km/h
        }

    if typ == "THS" and len(f) >= 2:
        # $--THS,heading,status  (status: A/E/D valid, V = not valid)
        status = f[1].strip().upper()
        heading = _f(f[0]) if status not in ("", "V") else None
        return {"type": "THS", "heading_deg": heading}

    if typ == "PQTMTAR" and len(f) >= 12:
        # $PQTMTAR,MsgVer,UTC,Quality,Reserved,Baseline,Heading,Pitch,Roll,
        #         AccHeading,AccPitch,AccRoll,Sats*CS
        # Heading comes from THS (standard, unambiguous); from PQTMTAR we take
        # quality + baseline (positions confident). Pitch/Roll indices (6/7) are
        # provisional — confirm once the dual-antenna solution is live (all
        # angle fields are empty until the baseline resolves).
        return {
            "type": "PQTMTAR",
            "heading_quality": _i(f[2]),
            "baseline_m": _f(f[4]),
            "pitch_deg": _f(f[6]),
            "roll_deg": _f(f[7]),
            "heading_accuracy_deg": _f(f[8]),
        }

    return None
