"""Logging control via the Sequent Multi-IO HAT's onboard button + LEDs (I2C).

Uses the HAT's built-in push button and LEDs instead of wiring to the Pi's
GPIO. Requires the ``multiio`` library (https://github.com/SequentMicrosystems/multiio-rpi)
and I2C enabled.

The button is **momentary**, so each press toggles a logging latch (rather than
holding a level like a switch). The HAT firmware debounces and latches presses:
``get_button_latch()`` returns True if the button was pressed since the last
read and clears itself — so we poll it once per loop and flip state on each True.

Exposes the same interface as :class:`watson_dms.switch.LoggingControls`
(``logging_on`` property, ``update_indicator``, ``close``) so the collection
loop is agnostic to which backend drives it.
"""

from __future__ import annotations

import time

DEFAULT_STACK = 0       # HAT stack address (jumpers); 0 for a single board
DEFAULT_STATUS_LED = 1  # which onboard LED to use for logging status

# LED indicator modes.
_OFF, _SOLID, _BLINK = 0, 1, 2


class HatLoggingControls:
    """Read the HAT button (toggle) and drive an onboard LED for status.

    Parameters
    ----------
    stack:
        HAT stack-level address (set by the board's address jumpers).
    i2c:
        I2C bus number (1 on a Pi 4).
    status_led:
        Onboard LED number to use as the logging indicator.
    blink_period:
        Half-period (seconds) of the "searching for fix" blink.
    """

    def __init__(
        self,
        stack: int = DEFAULT_STACK,
        i2c: int = 1,
        status_led: int = DEFAULT_STATUS_LED,
        blink_period: float = 0.4,
    ):
        try:
            import multiio
        except ImportError as exc:  # pragma: no cover - Pi-only dependency
            raise ImportError(
                "The Sequent Multi-IO library is required for the HAT button/LEDs.\n"
                "Install it with:  pip install SMmultiio   (imports as 'multiio'; "
                "and enable I2C)"
            ) from exc

        self._mio = multiio.SMmultiio(stack, i2c)
        self._status_led = status_led
        self._blink_period = blink_period

        self._logging = False          # latched state toggled by button presses
        self._led_mode = -1            # force first indicator update to apply
        self._blink_on = False
        self._last_blink = 0.0

        # Clear any press latched before we started, and start with LED off.
        try:
            self._mio.get_button_latch()
        except OSError:
            pass
        self._write_led(0)

    @property
    def logging_on(self) -> bool:
        """Poll the button; each latched press toggles logging. Call once/loop."""
        try:
            if self._mio.get_button_latch():
                self._logging = not self._logging
        except OSError:
            pass  # transient I2C hiccup — keep last known state
        return self._logging

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

        # Animate the software blink (no hardware blink on the HAT).
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
