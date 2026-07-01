"""Command-line entry point: ``python -m lg580p <command>``.

Current commands: detect / capture / parse. Logging (collect) + the HAT switch
service are added once the sentence set and baud are confirmed on hardware.
"""

from __future__ import annotations

import argparse
import sys


def cmd_capture(args: argparse.Namespace) -> int:
    from .capture import raw_capture
    print(f"# Raw capture on {args.port} @ {args.baud} for {args.seconds}s\n")
    lines = raw_capture(args.port, args.baud, args.seconds)
    # Summarize which sentence types were seen.
    from collections import Counter
    from .nmea import address, checksum_ok, is_sentence
    seen = Counter()
    for line in lines:
        print(line)
        if is_sentence(line):
            tag = address(line) or "?"
            seen[tag + (" ok" if checksum_ok(line) else " BAD-CKSUM")] += 1
    print(f"\n# {len(lines)} lines. Sentence types seen:")
    for tag, n in sorted(seen.items()):
        print(f"    {n:>4}  {tag}")
    return 0 if lines else 1


def cmd_detect(args: argparse.Namespace) -> int:
    from . import serial_io
    from .capture import detect_baud
    print(f"# Sweeping baud rates on {args.port} "
          f"({args.seconds}s each): {serial_io.SUPPORTED_BAUDS}\n")
    baud = detect_baud(args.port, args.seconds)
    if baud is None:
        print("\n# No valid NMEA detected. Check wiring (primary UART, serial "
              "console disabled), antenna, and power.")
        return 1
    print(f"\n# Best match: {baud} baud")
    return 0


def cmd_parse(args: argparse.Namespace) -> int:
    """Parse a captured NMEA text file into assembled GnssReadings."""
    from .assembler import GnssAssembler
    asm = GnssAssembler(emit_on=args.emit_on)
    stream = sys.stdin if args.file == "-" else open(args.file, encoding="utf-8")
    n = 0
    with stream:
        for line in stream:
            reading = asm.push(line)
            if reading is not None:
                print(reading.as_dict())
                n += 1
    print(f"# {n} readings assembled.", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    from . import serial_io
    p = argparse.ArgumentParser(
        prog="lg580p",
        description="Collect data from a SparkFun LG580P RTK GNSS receiver.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--port", default=serial_io.DEFAULT_PORT,
                        help=f"serial device (default: {serial_io.DEFAULT_PORT})")

    c = sub.add_parser("capture", parents=[common],
                       help="dump raw sentences at a fixed baud + summarize types")
    c.add_argument("--baud", type=int, default=serial_io.DEFAULT_BAUD)
    c.add_argument("--seconds", type=float, default=5.0)
    c.set_defaults(func=cmd_capture)

    d = sub.add_parser("detect", parents=[common], help="auto-detect the baud rate")
    d.add_argument("--seconds", type=float, default=3.0,
                   help="seconds to listen at each baud rate")
    d.set_defaults(func=cmd_detect)

    pr = sub.add_parser("parse", help="assemble readings from a captured file")
    pr.add_argument("file", help="file of NMEA sentences, or - for stdin")
    pr.add_argument("--emit-on", default="GGA",
                    help="sentence type that ends an epoch (default: GGA)")
    pr.set_defaults(func=cmd_parse)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
