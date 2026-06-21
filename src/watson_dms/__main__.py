"""Command-line entry point: ``python -m watson_dms <command>``."""

from __future__ import annotations

import argparse
import datetime as _dt
import sys

from . import serial_reader
from .capture import detect_baud, raw_capture
from .collect import collect, collect_switched
from .parser import parse_line


def _default_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def cmd_capture(args: argparse.Namespace) -> int:
    print(f"# Raw capture on {args.port} @ {args.baud} for {args.seconds}s\n")
    lines = raw_capture(args.port, args.baud, args.seconds)
    for line in lines:
        print(repr(line))
    print(f"\n# {len(lines)} lines captured.")
    return 0 if lines else 1


def cmd_detect(args: argparse.Namespace) -> int:
    print(f"# Sweeping baud rates on {args.port} "
          f"({args.seconds}s each): {serial_reader.SUPPORTED_BAUDS}\n")
    baud = detect_baud(args.port, args.seconds)
    if baud is None:
        print("\n# No valid DMS frames detected. Check wiring, power (12V), "
              "and that the unit has finished its ~5s init.")
        return 1
    print(f"\n# Best match: {baud} baud")
    return 0


def _make_controls(args: argparse.Namespace):
    """Build the logging-control backend (HAT button/LEDs or Pi GPIO)."""
    if args.source == "hat":
        from .hat_controls import HatLoggingControls
        return HatLoggingControls(stack=args.hat_stack, status_led=args.led)
    from .switch import LoggingControls
    led_pin = None if args.no_led else args.led_pin
    return LoggingControls(
        switch_pin=args.switch_pin,
        led_pin=led_pin,
        closed_is_on=not args.switch_invert,
    )


def cmd_collect(args: argparse.Namespace) -> int:
    if args.switch:
        try:
            controls = _make_controls(args)
        except ImportError as exc:
            print(f"# {exc}", file=sys.stderr)
            return 2
        except Exception as exc:  # e.g. I2C/HAT not ready — let systemd retry
            print(f"# could not initialize {args.source} controls: {exc}\n"
                  f"# (HAT seated? I2C enabled? try: i2cdetect -y 1)", file=sys.stderr)
            return 1
        collect_switched(
            port=args.port,
            baud=args.baud,
            controls=controls,
            log_dir=args.log_dir,
            fix_only=args.fix_only,
            gpx=not args.no_gpx,
            quiet=args.quiet,
        )
        return 0

    csv_path = args.csv
    gpx_path = args.gpx
    if csv_path is None and gpx_path is None:
        # Default to a timestamped CSV so data is never silently dropped.
        csv_path = f"{args.log_dir}/dms-{_default_stamp()}.csv"
    collect(
        port=args.port,
        baud=args.baud,
        csv_path=csv_path,
        gpx_path=gpx_path,
        quiet=args.quiet,
        fix_only=args.fix_only,
    )
    return 0


def cmd_parse(args: argparse.Namespace) -> int:
    """Parse a previously captured text file (one frame per line)."""
    stream = sys.stdin if args.file == "-" else open(args.file, encoding="utf-8")
    n = 0
    with stream:
        for line in stream:
            reading = parse_line(line)
            if reading is not None:
                print(reading.as_dict())
                n += 1
    print(f"# {n} frames parsed.", file=sys.stderr)
    return 0


def cmd_hat_test(args: argparse.Namespace) -> int:
    """Cycle the HAT's LEDs and report button presses (wiring/ID check)."""
    import time
    try:
        import multiio
    except ImportError as exc:
        print(f"# 'multiio' not installed: {exc}\n# Install with: pip install SMmultiio",
              file=sys.stderr)
        return 2
    try:
        mio = multiio.SMmultiio(args.hat_stack, 1)
    except Exception as exc:
        print(f"# Could not open Multi-IO HAT at stack {args.hat_stack}: {exc}\n"
              f"# Is I2C enabled and the HAT seated? Check: i2cdetect -y 1", file=sys.stderr)
        return 2

    print(f"# Cycling LEDs 1..{args.leds} (watch the board to map numbers):")
    for n in range(1, args.leds + 1):
        try:
            mio.set_led(n, 1)
            print(f"  LED {n} ON")
            time.sleep(0.6)
            mio.set_led(n, 0)
        except Exception as exc:
            print(f"  LED {n}: {exc}")
    try:
        mio.get_button_latch()  # clear any stale latch
    except OSError:
        pass

    print(f"\n# Press/hold the HAT button — watching {args.seconds:.0f}s (Ctrl-C to stop).")
    print("#   live  = get_button()  (instantaneous state, bit 0)")
    print("#   latch = get_button_latch()  (firmware latch, bit 1)")
    edges = 0       # rising edges of the live state (what the logger uses)
    latches = 0     # firmware latch events
    last_live = None
    deadline = time.monotonic() + args.seconds
    try:
        while time.monotonic() < deadline:
            try:
                live = bool(mio.get_button())
                latched = bool(mio.get_button_latch())
            except OSError as exc:
                print(f"  I2C read error: {exc}")
                time.sleep(0.2)
                continue
            if live != last_live:
                if live:
                    edges += 1
                    print(f"  live: PRESSED   (rising edge #{edges})")
                    mio.set_led(1, 1)
                else:
                    print("  live: released")
                    mio.set_led(1, 0)
                last_live = live
            if latched:
                latches += 1
                print(f"  latch event #{latches}")
            time.sleep(0.03)
    except KeyboardInterrupt:
        pass
    print(f"# Done: {edges} live press(es), {latches} firmware-latch event(s).")
    if edges and not latches:
        print("# -> Button works via get_button(); firmware latch unused. "
              "The logger uses get_button(), so you're good.")
    elif not edges and not latches:
        print("# -> No button activity seen. Check the HAT stack address "
              "(--hat-stack) and `i2cdetect -y 1`.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="watson_dms",
        description="Collect data from a Watson DMS-SGP02 GPS/inertial unit.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--port", default=serial_reader.DEFAULT_PORT,
                        help=f"serial device (default: {serial_reader.DEFAULT_PORT})")

    c = sub.add_parser("capture", parents=[common],
                       help="dump raw lines at a fixed baud (wiring check)")
    c.add_argument("--baud", type=int, default=serial_reader.DEFAULT_BAUD)
    c.add_argument("--seconds", type=float, default=5.0)
    c.set_defaults(func=cmd_capture)

    d = sub.add_parser("detect", parents=[common],
                       help="auto-detect the unit's baud rate")
    d.add_argument("--seconds", type=float, default=3.0,
                   help="seconds to listen at each baud rate")
    d.set_defaults(func=cmd_detect)

    co = sub.add_parser("collect", parents=[common],
                        help="parse and log frames to CSV/GPX")
    co.add_argument("--baud", type=int, default=serial_reader.DEFAULT_BAUD)
    co.add_argument("--log-dir", default="logs",
                    help="directory for log files (default: logs)")
    co.add_argument("--fix-only", action="store_true",
                    help="only log frames that have a valid GPS fix")
    co.add_argument("--quiet", action="store_true", help="suppress the live status line")
    # Continuous mode (no switch):
    co.add_argument("--csv", help="CSV output path (default: <log-dir>/dms-<timestamp>.csv)")
    co.add_argument("--gpx", help="also write a GPX track of GPS fixes")
    # Switch/button-gated service mode:
    sw = co.add_argument_group("switch mode (button/switch gates logging)")
    sw.add_argument("--switch", action="store_true",
                    help="gate logging with a button/switch; one log set per ON period")
    sw.add_argument("--source", choices=("hat", "gpio"), default="hat",
                    help="control source: 'hat' = Multi-IO HAT button+LEDs (default), "
                         "'gpio' = switch+LED wired to Pi GPIO")
    sw.add_argument("--no-gpx", action="store_true",
                    help="write only CSV per session, not GPX")
    # HAT source (--source hat):
    sw.add_argument("--hat-stack", type=int, default=0,
                    help="Multi-IO HAT stack address (default: 0)")
    sw.add_argument("--led", type=int, default=1,
                    help="HAT onboard LED number for status (default: 1)")
    # GPIO source (--source gpio):
    sw.add_argument("--switch-pin", type=int, default=16,
                    help="[gpio] BCM pin of the switch-to-ground (default: 16)")
    sw.add_argument("--switch-invert", action="store_true",
                    help="[gpio] treat an OPEN switch as ON (default: closed = ON)")
    sw.add_argument("--led-pin", type=int, default=26,
                    help="[gpio] BCM pin of the status LED (default: 26)")
    sw.add_argument("--no-led", action="store_true",
                    help="[gpio] run without a status LED")
    co.set_defaults(func=cmd_collect)

    ht = sub.add_parser("hat-test",
                        help="identify Multi-IO HAT LED numbers and test the button")
    ht.add_argument("--hat-stack", type=int, default=0,
                    help="Multi-IO HAT stack address (default: 0)")
    ht.add_argument("--leds", type=int, default=4,
                    help="how many LEDs to cycle through (default: 4)")
    ht.add_argument("--seconds", type=float, default=15.0,
                    help="how long to watch for button presses (default: 15)")
    ht.set_defaults(func=cmd_hat_test)

    pr = sub.add_parser("parse", help="parse a captured text file (offline)")
    pr.add_argument("file", help="file of raw frames, or - for stdin")
    pr.set_defaults(func=cmd_parse)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
