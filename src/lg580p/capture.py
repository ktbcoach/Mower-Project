"""Raw capture and baud-rate auto-detection for the LG580P.

Run this first on the real hardware to confirm wiring, find the baud rate, and
see exactly which NMEA/PQTM sentences the receiver emits.
"""

from __future__ import annotations

import time
from typing import Optional

from . import serial_io
from .nmea import checksum_ok, is_sentence


def raw_capture(port: str, baud: int, seconds: float = 5.0) -> list[str]:
    """Open the port at ``baud`` and collect lines for ``seconds``."""
    lines: list[str] = []
    deadline = time.monotonic() + seconds
    with serial_io.open_port(port, baud, timeout=0.5) as ser:
        for line in serial_io.read_lines(ser):
            lines.append(line)
            if time.monotonic() >= deadline:
                break
    return lines


def _score(lines: list[str]) -> int:
    """Count lines that are valid, checksum-passing NMEA sentences."""
    return sum(1 for ln in lines if is_sentence(ln) and checksum_ok(ln))


def detect_baud(port: str, seconds_per_baud: float = 3.0,
                candidates=serial_io.SUPPORTED_BAUDS) -> Optional[int]:
    """Sweep supported baud rates; return the one with the most valid NMEA."""
    best_baud: Optional[int] = None
    best_score = 0
    for baud in candidates:
        try:
            lines = raw_capture(port, baud, seconds_per_baud)
        except Exception as exc:  # pragma: no cover - hardware-dependent
            print(f"  {baud:>6} baud: error: {exc}")
            continue
        score = _score(lines)
        print(f"  {baud:>6} baud: {len(lines):>4} lines, {score:>4} valid NMEA")
        if lines and score > best_score:
            best_score = score
            best_baud = baud
    return best_baud
