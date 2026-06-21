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


def cmd_collect(args: argparse.Namespace) -> int:
    if args.switch:
        try:
            from .switch import LoggingControls
        except ImportError as exc:
            print(f"# {exc}", file=sys.stderr)
            return 2
        led_pin = None if args.no_led else args.led_pin
        controls = LoggingControls(
            switch_pin=args.switch_pin,
            led_pin=led_pin,
            closed_is_on=not args.switch_invert,
        )
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
    # Switch-gated service mode:
    sw = co.add_argument_group("switch mode (physical on/off switch)")
    sw.add_argument("--switch", action="store_true",
                    help="gate logging with a GPIO switch; one log set per ON period")
    sw.add_argument("--switch-pin", type=int, default=16,
                    help="BCM pin of the switch-to-ground (default: 16)")
    sw.add_argument("--switch-invert", action="store_true",
                    help="treat an OPEN switch as ON (default: closed = ON)")
    sw.add_argument("--led-pin", type=int, default=26,
                    help="BCM pin of the status LED (default: 26)")
    sw.add_argument("--no-led", action="store_true", help="run without a status LED")
    sw.add_argument("--no-gpx", action="store_true",
                    help="write only CSV per session, not GPX")
    co.set_defaults(func=cmd_collect)

    pr = sub.add_parser("parse", help="parse a captured text file (offline)")
    pr.add_argument("file", help="file of raw frames, or - for stdin")
    pr.set_defaults(func=cmd_parse)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
