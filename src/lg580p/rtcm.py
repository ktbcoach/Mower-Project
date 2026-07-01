"""Forward RTCM3 correction bytes from a serial radio into the LG580P.

The rover's transparent serial radio (receiving RTCM from the base) presents a
byte stream on a Pi serial port (e.g. /dev/ttyUSB0). The LG580P accepts RTCM3
on its UART RX — which is the Pi's TX on /dev/serial0 — so we just copy bytes
from the radio port to the already-open LG580P port. Runs in a background thread
so NMEA logging continues uninterrupted (the port is full-duplex: the main loop
reads NMEA, this thread writes RTCM).
"""

from __future__ import annotations

import threading
import time
from typing import Optional


class RtcmInjector:
    """Pump RTCM bytes from a source serial port into the LG580P serial port."""

    def __init__(self, dest_serial, source_port: str, source_baud: int):
        self._dest = dest_serial          # open LG580P Serial (write RTCM here)
        self._source_port = source_port
        self._source_baud = source_baud
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.bytes_forwarded = 0
        self.last_rx = 0.0                # monotonic time of last bytes received
        self.error: Optional[str] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="rtcm", daemon=True)
        self._thread.start()

    @property
    def flowing(self) -> bool:
        """True if corrections arrived within the last few seconds."""
        return self.last_rx > 0 and (time.monotonic() - self.last_rx) < 3.0

    def _run(self) -> None:
        import serial  # local import so the package stays importable without pyserial
        while not self._stop.is_set():
            try:
                with serial.Serial(self._source_port, self._source_baud, timeout=0.5) as src:
                    self.error = None
                    while not self._stop.is_set():
                        data = src.read(512)
                        if data:
                            self._dest.write(data)
                            self.bytes_forwarded += len(data)
                            self.last_rx = time.monotonic()
            except Exception as exc:  # radio absent/unplugged — report and retry
                self.error = str(exc)
                self._stop.wait(2.0)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.5)
