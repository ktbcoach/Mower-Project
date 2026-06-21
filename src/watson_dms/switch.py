"""Physical logging switch + status LED via the Pi's GPIO (gpiozero).

The Sequent Multi-IO HAT talks to the Pi over I2C only (plus UART5 on
GPIO12/13), so the rest of the GPIO header is free for a simple toggle switch
and an indicator LED.

Wiring (defaults; both pins are configurable):
  * Switch: a toggle/SPST between GPIO16 (pin 36) and GND. No external voltage —
    the Pi's internal pull-up holds the line high; closing the switch pulls it
    to ground. Closed = logging ON.
  * LED: anode -> GPIO26 (pin 37) through a ~330 Ω resistor, cathode -> GND.

gpiozero is imported lazily so the rest of the package (parser, logger) stays
importable on a dev machine without GPIO.
"""

from __future__ import annotations

DEFAULT_SWITCH_PIN = 16
DEFAULT_LED_PIN = 26

# LED indicator modes.
_OFF, _SOLID, _BLINK = 0, 1, 2


class LoggingControls:
    """Reads the logging switch and drives the status LED.

    Parameters
    ----------
    switch_pin:
        BCM number of the toggle switch (to ground).
    led_pin:
        BCM number of the status LED, or ``None`` to run without an indicator.
    closed_is_on:
        If True (default), a closed switch (pin pulled to ground) means logging
        is ON. Set False if your switch is wired the other way.
    bounce_time:
        Debounce window in seconds for the switch.
    """

    def __init__(
        self,
        switch_pin: int = DEFAULT_SWITCH_PIN,
        led_pin: int | None = DEFAULT_LED_PIN,
        closed_is_on: bool = True,
        bounce_time: float = 0.05,
    ):
        try:
            from gpiozero import LED, Button
        except ImportError as exc:  # pragma: no cover - Pi-only dependency
            raise ImportError(
                "gpiozero is required for switch/LED support.\n"
                "On Raspberry Pi OS it's preinstalled — create the venv with\n"
                "    python3 -m venv --system-site-packages .venv\n"
                "or install it:  pip install gpiozero lgpio"
            ) from exc

        self._closed_is_on = closed_is_on
        self._switch = Button(switch_pin, pull_up=True, bounce_time=bounce_time)
        self._led = LED(led_pin) if led_pin is not None else None
        self._led_mode = -1  # force first update to take effect

    @property
    def logging_on(self) -> bool:
        """True when the switch selects logging."""
        active = self._switch.is_active  # True when pulled to ground (closed)
        return active if self._closed_is_on else not active

    def update_indicator(self, logging: bool, reading) -> None:
        """Reflect state on the single GPIO LED: off (idle) / blink (logging,
        searching) / solid (logging with a GPS fix).

        ``reading`` is the latest :class:`DmsReading` or ``None``. Only acts on
        state *changes* so a background blink isn't restarted every frame.
        """
        if self._led is None:
            return
        has_fix = bool(reading is not None and reading.has_gps_fix)
        mode = _OFF if not logging else (_SOLID if has_fix else _BLINK)
        if mode == self._led_mode:
            return
        self._led_mode = mode
        if mode == _OFF:
            self._led.off()
        elif mode == _SOLID:
            self._led.on()
        else:
            self._led.blink(on_time=0.25, off_time=0.25)  # runs in background

    def close(self) -> None:
        if self._led is not None:
            self._led.close()
        self._switch.close()

    def __enter__(self) -> "LoggingControls":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
