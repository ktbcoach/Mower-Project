"""Send Quectel PQTM configuration commands to the LG580P over serial.

Builds a proper NMEA command (computes the ``*HH`` checksum, adds CRLF), writes
it, and collects the receiver's response lines. Used to configure the module —
e.g. preload the dual-antenna baseline so heading resolves faster.

The receiver must not be in use by another process (stop the logger/service
first). Config changes only persist across power-cycles if followed by
``PQTMSAVEPAR`` and a module reset.
"""

from __future__ import annotations

import time

from . import serial_io


def build(body: str) -> str:
    """Wrap a command body (e.g. ``PQTMCFGBLD,W,1.000``) into a full sentence."""
    body = body.strip()
    if body.startswith("$"):
        body = body[1:]
    star = body.rfind("*")
    if star != -1:
        body = body[:star]
    cksum = 0
    for ch in body:
        cksum ^= ord(ch)
    return f"${body}*{cksum:02X}\r\n"


def send(port: str, baud: int, sentences: list[str], listen: float = 2.0) -> list[str]:
    """Send each command sentence, then collect response lines for ``listen`` s."""
    responses: list[str] = []
    with serial_io.open_port(port, baud, timeout=0.3) as ser:
        for body in sentences:
            ser.write(build(body).encode("ascii"))
        ser.flush()
        deadline = time.monotonic() + listen
        for line in serial_io.read_lines(ser):
            responses.append(line)
            if time.monotonic() >= deadline:
                break
    return responses


def baseline_commands(meters: float, save: bool = True) -> list[str]:
    """Command sequence to set (and optionally persist) the antenna baseline."""
    cmds = [f"PQTMCFGBLD,W,{meters:.3f}"]
    if save:
        cmds.append("PQTMSAVEPAR")
    return cmds
