"""Serial line reader for the LG580P (self-contained, no Watson dependency).

The LG580P Flex pHAT uses the Pi's primary UART (GPIO14/15 -> /dev/serial0).
NMEA sentences are CRLF-terminated; we split on CR/LF and decode as text.
"""

from __future__ import annotations

from typing import Iterator, Optional

try:
    import serial  # pyserial
except ImportError as exc:  # pragma: no cover - exercised only without pyserial
    raise ImportError(
        "pyserial is required for live serial reading. Install it with:\n"
        "    pip install pyserial"
    ) from exc

DEFAULT_PORT = "/dev/serial0"          # Pi primary UART (GPIO14/15)
DEFAULT_BAUD = 460800                  # confirmed default on this LG580P
SUPPORTED_BAUDS = (9600, 115200, 230400, 460800, 921600)


def open_port(port: str = DEFAULT_PORT, baud: int = DEFAULT_BAUD,
              timeout: float = 1.0) -> "serial.Serial":
    """Open the serial port with 8N1 framing."""
    return serial.Serial(
        port=port,
        baudrate=baud,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=timeout,
    )


def read_lines(ser: "serial.Serial", encoding: str = "ascii",
               idle_tick: bool = False) -> "Iterator[Optional[str]]":
    """Yield CR/LF-terminated lines decoded as text, indefinitely.

    Prompt read: block for the first byte (up to the port timeout) then drain
    whatever else has arrived, so each sentence is yielded as it completes.
    Undecodable bytes are replaced rather than raising. With ``idle_tick`` set,
    yields ``None`` on a read timeout so callers can poll other inputs.
    """
    buffer = bytearray()
    while True:
        chunk = ser.read(1)
        if not chunk:
            if idle_tick:
                yield None
            continue
        waiting = ser.in_waiting
        if waiting:
            chunk += ser.read(waiting)
        buffer.extend(chunk)
        while True:
            cr = buffer.find(b"\r")
            lf = buffer.find(b"\n")
            idx = min(x for x in (cr, lf) if x != -1) if (cr != -1 or lf != -1) else -1
            if idx == -1:
                break
            line = bytes(buffer[:idx])
            del buffer[: idx + 1]
            text = line.decode(encoding, errors="replace").strip()
            if text:
                yield text
