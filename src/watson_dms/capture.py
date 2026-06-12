"""Diagnostic raw capture and baud-rate auto-detection for the DMS-SGP02.

Use this first, on the real hardware, to confirm the serial wiring and the
unit's current baud rate before relying on the parser. It does not assume the
output format — it just shows you the bytes coming off the wire.
"""

from __future__ import annotations

import time
from typing import Optional

from . import serial_reader
from .parser import is_data_line


def raw_capture(port: str, baud: int, seconds: float = 5.0) -> list[str]:
    """Open the port at ``baud`` and collect lines for ``seconds``.

    Returns the decoded lines seen. Never raises on bad bytes — decode errors
    are replaced — so you can eyeball framing/baud problems (garbage = wrong
    baud or wiring).
    """
    lines: list[str] = []
    deadline = time.monotonic() + seconds
    with serial_reader.open_port(port, baud, timeout=0.5) as ser:
        for line in serial_reader.read_lines(ser):
            lines.append(line)
            if time.monotonic() >= deadline:
                break
    return lines


def _score(lines: list[str]) -> int:
    """Number of lines that look like valid DMS data frames."""
    return sum(1 for ln in lines if is_data_line(ln))


def detect_baud(
    port: str,
    seconds_per_baud: float = 3.0,
    candidates=serial_reader.SUPPORTED_BAUDS,
) -> Optional[int]:
    """Sweep the supported baud rates and return the one that yields the most
    recognizable DMS data lines, or ``None`` if nothing looked valid.
    """
    best_baud: Optional[int] = None
    best_score = 0
    for baud in candidates:
        try:
            lines = raw_capture(port, baud, seconds_per_baud)
        except Exception as exc:  # pragma: no cover - hardware-dependent
            print(f"  {baud:>6} baud: error: {exc}")
            continue
        score = _score(lines)
        print(
            f"  {baud:>6} baud: {len(lines):>4} lines, "
            f"{score:>4} look like DMS frames"
        )
        if lines and score > best_score:
            best_score = score
            best_baud = baud
    return best_baud
