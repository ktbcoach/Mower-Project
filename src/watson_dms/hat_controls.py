"""Logging control via the Sequent Multi-IO HAT (I2C): dry-contact input + LEDs.

Uses the HAT's built-in I/O instead of wiring to the Pi's GPIO. Requires the
Sequent ``multiio`` library (PyPI: ``SMmultiio``) and I2C enabled.

Two input modes:
  * ``"contact"`` (default) — a switch on a **dry-contact / opto input** channel
    (read via ``get_opto(channel)``). This is a *level* input: closed = logging
    ON, like a toggle switch. Robust and the simplest behavior.
  * ``"button"`` — the onboard **momentary push button**; each press toggles a
    logging latch. (Kept as a fallback; the dry-contact input is preferred.)

Status is shown on an onboard LED: off = idle, blink = logging but searching
for a GPS fix, solid = logging with a fix. (The blink is software-timed; the
HAT has no hardware blink.)

Exposes the same interface as :class:`watson_dms.switch.LoggingControls`
(``logging_on`` property, ``update_indicator``, ``close``) so the collection
loop is agnostic to which backend drives it.
"""

from __future__ import annotations

import time

DEFAULT_STACK = 0          # HAT stack address (jumpers); 0 for a single board
DEFAULT_STATUS_LED = 1     # onboard LED used for logging status
DEFAULT_CONTACT_CH = 1     # dry-contact / opto input channel

# LED indicator modes.
_OFF, _SOLID, _BLINK = 0, 1, 2


class HatLoggingControls:
    """Read a HAT input (dry-contact level or button) and drive a status LED.

    Parameters
    ----------
    stack, i2c:
        HAT stack-level address and I2C bus number (1 on a Pi 4).
    status_led:
        Onboard LED number used as the logging indicator.
    input_mode:
        ``"contact"`` (dry-contact/opto level input, default) or ``"button"``.
    contact_channel:
        Opto/dry-contact channel to read in contact mode (1-based).
    contact_invert:
        If True, an OPEN contact means logging ON (default: CLOSED = ON).
    blink_period:
        Half-period (seconds) of the "searching for fix" blink.
    """

    def __init__(
        self,
        stack: int = DEFAULT_STACK,
        i2c: int = 1,
        status_led: int = DEFAULT_STATUS_LED,
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
        self._status_led = status_led
        self._blink_period = blink_period
        self._input_mode = input_mode
        self._contact_channel = contact_channel
        self._contact_invert = contact_invert

        # Button-mode state (unused in contact mode).
        self._logging = False
        self._debounce = 0.3
        self._last_toggle = 0.0
        self._last_pressed = False
        # Contact-mode last-known value (for transient I2C errors).
        self._last_contact = False

        # LED state.
        self._led_mode = -1
        self._blink_on = False
        self._last_blink = 0.0

        if input_mode == "button":
            self._last_pressed = self._read_button()
        self._write_led(0)

    # --- input ----------------------------------------------------------------

    @property
    def logging_on(self) -> bool:
        """Whether logging should be active. Call once per loop iteration."""
        if self._input_mode == "contact":
            return self._read_contact()
        return self._read_button_toggle()

    def _read_contact(self) -> bool:
        """Level read of the dry-contact/opto channel (closed = ON by default)."""
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
        """Momentary button: toggle logging on each rising edge (debounced)."""
        pressed = self._read_button()
        now = time.monotonic()
        if pressed and not self._last_pressed and (now - self._last_toggle) > self._debounce:
            self._logging = not self._logging
            self._last_toggle = now
        self._last_pressed = pressed
        return self._logging

    # --- indicator LED --------------------------------------------------------

    def update_indicator(self, logging: bool, has_fix: bool) -> None:
        """off = idle · solid = logging+fix · blink = logging, searching."""
        mode = _OFF if not logging else (_SOLID if has_fix else _BLINK)

        if mode != self._led_mode:
            self._led_mode = mode
            if mode == _OFF:
                self._write_led(0)
            elif mode == _SOLID:
                self._write_led(1)
            else:  # entering blink
                self._blink_on = True
                self._last_blink = time.monotonic()
                self._write_led(1)
            return

        if mode == _BLINK:
            now = time.monotonic()
            if now - self._last_blink >= self._blink_period:
                self._blink_on = not self._blink_on
                self._write_led(1 if self._blink_on else 0)
                self._last_blink = now

    def _write_led(self, val: int) -> None:
        try:
            self._mio.set_led(self._status_led, val)
        except OSError:
            pass  # don't let an I2C hiccup kill collection

    def close(self) -> None:
        self._write_led(0)

    def __enter__(self) -> "HatLoggingControls":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
