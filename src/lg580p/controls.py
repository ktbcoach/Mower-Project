"""Logging switch + status LEDs via the Sequent Multi-IO HAT (I2C), for GNSS.

Reuses the same Multi-IO HAT as the Watson build (it coexists with the LG580P
pHAT — different pins). A toggle switch on dry-contact input 1 gates logging;
two onboard LEDs show status:

  * GPS LED  (default 1): off = no fix · blink = fix but not RTK-fixed
    (GPS/DGPS/RTK-float) · solid = RTK **fixed**.
  * Logging LED (default 2): off = idle · blink = logging.

Requires the Sequent ``multiio`` library (PyPI ``SMmultiio``) and I2C enabled.
"""

from __future__ import annotations

import time

DEFAULT_STACK = 0
DEFAULT_GPS_LED = 1
DEFAULT_LOGGING_LED = 2
DEFAULT_CONTACT_CH = 1

_OFF, _SOLID, _BLINK = 0, 1, 2


class _Led:
    """One onboard LED with off/solid/blink behavior; blink is software-timed."""

    def __init__(self, write_fn, number: int, period: float):
        self._write = write_fn
        self._num = number
        self._period = period
        self._mode = None
        self._on = False
        self._last = 0.0

    def set(self, mode: int) -> None:
        now = time.monotonic()
        if mode != self._mode:
            self._mode = mode
            if mode == _OFF:
                self._on = False
                self._write(self._num, 0)
            elif mode == _SOLID:
                self._on = True
                self._write(self._num, 1)
            else:
                self._on = True
                self._last = now
                self._write(self._num, 1)
            return
        if mode == _BLINK and now - self._last >= self._period:
            self._on = not self._on
            self._write(self._num, 1 if self._on else 0)
            self._last = now


class HatLoggingControls:
    """Read the dry-contact logging switch and drive the two status LEDs."""

    def __init__(
        self,
        stack: int = DEFAULT_STACK,
        i2c: int = 1,
        gps_led: int | None = DEFAULT_GPS_LED,
        logging_led: int | None = DEFAULT_LOGGING_LED,
        contact_channel: int = DEFAULT_CONTACT_CH,
        contact_invert: bool = False,
        blink_period: float = 0.4,
    ):
        try:
            import multiio
        except ImportError as exc:  # pragma: no cover - Pi-only dependency
            raise ImportError(
                "The Sequent Multi-IO library is required for the HAT.\n"
                "Install it with:  pip install SMmultiio   (and enable I2C)"
            ) from exc

        self._mio = multiio.SMmultiio(stack, i2c)
        self._contact_channel = contact_channel
        self._contact_invert = contact_invert
        self._last_contact = False

        self._gps_led = _Led(self._write_led, gps_led, blink_period) if gps_led else None
        self._logging_led = (
            _Led(self._write_led, logging_led, blink_period) if logging_led else None
        )
        if self._gps_led:
            self._gps_led.set(_OFF)
        if self._logging_led:
            self._logging_led.set(_OFF)

    @property
    def logging_on(self) -> bool:
        """Dry-contact level (closed = ON). Call once per loop iteration."""
        try:
            active = bool(self._mio.get_opto(self._contact_channel))
            self._last_contact = active
        except OSError:
            active = self._last_contact
        return (not active) if self._contact_invert else active

    def update_indicator(self, logging: bool, reading) -> None:
        if self._gps_led:
            self._gps_led.set(self._gps_mode(reading))
        if self._logging_led:
            self._logging_led.set(_BLINK if logging else _OFF)

    @staticmethod
    def _gps_mode(reading) -> int:
        if reading is None or not reading.has_gps_fix:
            return _OFF
        if reading.rtk_fixed:
            return _SOLID          # RTK fixed
        return _BLINK              # GPS / DGPS / RTK float

    def _write_led(self, number: int, val: int) -> None:
        try:
            self._mio.set_led(number, val)
        except OSError:
            pass

    def close(self) -> None:
        if self._gps_led:
            self._gps_led.set(_OFF)
        if self._logging_led:
            self._logging_led.set(_OFF)

    def __enter__(self) -> "HatLoggingControls":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
