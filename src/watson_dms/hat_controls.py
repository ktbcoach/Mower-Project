"""Logging control via the Sequent Multi-IO HAT (I2C): dry-contact input + LEDs.

Uses the HAT's built-in I/O instead of wiring to the Pi's GPIO. Requires the
Sequent ``multiio`` library (PyPI: ``SMmultiio``) and I2C enabled.

Two input modes:
  * ``"contact"`` (default) — a switch on a **dry-contact / opto input** channel
    (read via ``get_opto(channel)``). This is a *level* input: closed = logging
    ON, like a toggle switch.
  * ``"button"`` — the onboard **momentary push button**; each press toggles a
    logging latch. (Fallback; the dry-contact input is preferred.)

Two independent status LEDs:
  * **GPS LED** (default LED 1): off = no fix · blinking = fix but heading is
    inertial/track (not dual-GPS) · solid = dual-GPS true-north fix.
  * **Logging LED** (default LED 2): off = idle · blinking = logging.

LED blinking is software-timed (the HAT has no hardware blink).

Exposes the interface used by the collection loop: ``logging_on`` property,
``update_indicator(logging, reading)``, and ``close``.
"""

from __future__ import annotations

import time

DEFAULT_STACK = 0          # HAT stack address (jumpers); 0 for a single board
DEFAULT_GPS_LED = 1        # onboard LED for GPS fix status
DEFAULT_LOGGING_LED = 2    # onboard LED for logging status
DEFAULT_CONTACT_CH = 1     # dry-contact / opto input channel

# LED states.
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
            else:  # entering blink
                self._on = True
                self._last = now
                self._write(self._num, 1)
            return
        if mode == _BLINK and now - self._last >= self._period:
            self._on = not self._on
            self._write(self._num, 1 if self._on else 0)
            self._last = now


class HatLoggingControls:
    """Read a HAT input (dry-contact level or button) and drive two status LEDs.

    Parameters
    ----------
    stack, i2c:
        HAT stack-level address and I2C bus number (1 on a Pi 4).
    gps_led, logging_led:
        Onboard LED numbers for GPS-fix status and logging status. Pass ``None``
        for either to leave that LED unused.
    input_mode:
        ``"contact"`` (dry-contact/opto level input, default) or ``"button"``.
    contact_channel:
        Opto/dry-contact channel to read in contact mode (1-based).
    contact_invert:
        If True, an OPEN contact means logging ON (default: CLOSED = ON).
    blink_period:
        Half-period (seconds) of LED blinking.
    """

    def __init__(
        self,
        stack: int = DEFAULT_STACK,
        i2c: int = 1,
        gps_led: int | None = DEFAULT_GPS_LED,
        logging_led: int | None = DEFAULT_LOGGING_LED,
        input_mode: str = "contact",
        contact_channel: int = DEFAULT_CONTACT_CH,
        contact_invert: bool = False,
        blink_period: float = 0.4,
    ):
        try:
            import multiio
        except ImportError as exc:  # pragma: no cover - Pi-only dependency
            raise ImportError(
                "The Sequent Multi-IO library is required for the HAT.\n"
                "Install it with:  pip install SMmultiio   (imports as 'multiio'; "
                "and enable I2C)"
            ) from exc

        self._mio = multiio.SMmultiio(stack, i2c)
        self._input_mode = input_mode
        self._contact_channel = contact_channel
        self._contact_invert = contact_invert

        # Button-mode state (unused in contact mode).
        self._logging = False
        self._debounce = 0.3
        self._last_toggle = 0.0
        self._last_pressed = False
        self._last_contact = False

        self._gps_led = _Led(self._write_led, gps_led, blink_period) if gps_led else None
        self._logging_led = (
            _Led(self._write_led, logging_led, blink_period) if logging_led else None
        )

        if input_mode == "button":
            self._last_pressed = self._read_button()
        # Start both LEDs off.
        if self._gps_led:
            self._gps_led.set(_OFF)
        if self._logging_led:
            self._logging_led.set(_OFF)

    # --- input ----------------------------------------------------------------

    @property
    def logging_on(self) -> bool:
        """Whether logging should be active. Call once per loop iteration."""
        if self._input_mode == "contact":
            return self._read_contact()
        return self._read_button_toggle()

    def _read_contact(self) -> bool:
        try:
            active = bool(self._mio.get_opto(self._contact_channel))
            self._last_contact = active
        except OSError:
            active = self._last_contact  # transient I2C hiccup — hold last state
        return (not active) if self._contact_invert else active

    def _read_button(self) -> bool:
        try:
            return bool(self._mio.get_button())
        except OSError:
            return self._last_pressed

    def _read_button_toggle(self) -> bool:
        pressed = self._read_button()
        now = time.monotonic()
        if pressed and not self._last_pressed and (now - self._last_toggle) > self._debounce:
            self._logging = not self._logging
            self._last_toggle = now
        self._last_pressed = pressed
        return self._logging

    # --- indicator LEDs -------------------------------------------------------

    def update_indicator(self, logging: bool, reading) -> None:
        """Drive the GPS and logging LEDs. Call every loop iteration (animates
        blinking). ``reading`` is the latest :class:`DmsReading` or ``None``.
        """
        if self._gps_led:
            self._gps_led.set(self._gps_mode(reading))
        if self._logging_led:
            self._logging_led.set(_BLINK if logging else _OFF)

    @staticmethod
    def _gps_mode(reading) -> int:
        if reading is None or not reading.has_gps_fix:
            return _OFF                       # no position fix
        if reading.heading_mode == "gps_true_north":
            return _SOLID                     # dual-antenna GPS fix (label G)
        return _BLINK                         # fix, but track/inertial heading

    def _write_led(self, number: int, val: int) -> None:
        try:
            self._mio.set_led(number, val)
        except OSError:
            pass  # don't let an I2C hiccup kill collection

    def close(self) -> None:
        if self._gps_led:
            self._gps_led.set(_OFF)
        if self._logging_led:
            self._logging_led.set(_OFF)

    def __enter__(self) -> "HatLoggingControls":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
