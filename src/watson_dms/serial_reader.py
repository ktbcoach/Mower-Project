"""Serial line reader for the DMS-SGP02.

Thin wrapper over pyserial that yields decoded text lines. The DMS terminates
each frame with a carriage return (``\\r``); pyserial's ``readline`` splits on
``\\n`` by default, so we read raw bytes and split on CR ourselves.
"""

from __future__ import annotations

from typing import Iterator

try:
    import serial  # pyserial
except ImportError as exc:  # pragma: no cover - exercised only without pyserial
    raise ImportError(
        "pyserial is required for live serial reading. Install it with:\n"
        "    pip install pyserial"
    ) from exc

# The Sequent Microsystems Multi-IO HAT routes RS232 to GPIO12/GPIO13 (UART5),
# which appears as /dev/ttyAMA5 after enabling dtoverlay=uart5 in config.txt.
DEFAULT_PORT = "/dev/ttyAMA5"
DEFAULT_BAUD = 9600

# Baud rates the unit supports (manual, Setting Baud Rate).
SUPPORTED_BAUDS = (4800, 9600, 19200, 38400)


def open_port(
    port: str = DEFAULT_PORT,
    baud: int = DEFAULT_BAUD,
    timeout: float = 1.0,
) -> "serial.Serial":
    """Open the serial port with the DMS-SGP02's 8N1 framing."""
    return serial.Serial(
        port=port,
        baudrate=baud,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=timeout,
    )


def read_lines(
    ser: "serial.Serial",
    encoding: str = "ascii",
) -> Iterator[str]:
    """Yield CR-terminated lines decoded as text, indefinitely.

    Bytes that fail to decode are replaced rather than raising, so a noisy
    line never kills the stream. The caller is responsible for closing
    ``ser`` (e.g. via a ``with`` block).
    """
    buffer = bytearray()
    while True:
        chunk = ser.read(256)
        if not chunk:
            continue  # read timeout, no data yet
        buffer.extend(chunk)
        # Frames are CR-terminated; tolerate stray LFs too.
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
