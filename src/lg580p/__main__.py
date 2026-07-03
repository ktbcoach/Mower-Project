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


def _default_stamp() -> str:
    import datetime as _dt
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def cmd_collect(args: argparse.Namespace) -> int:
    from .collect import collect, collect_switched
    if args.switch:
        try:
            from .controls import HatLoggingControls
            controls = HatLoggingControls(
                stack=args.hat_stack,
                gps_led=args.gps_led,
                logging_led=args.logging_led,
                contact_channel=args.contact_channel,
                contact_invert=args.contact_invert,
            )
        except ImportError as exc:
            print(f"# {exc}", file=sys.stderr)
            return 2
        except Exception as exc:
            print(f"# could not initialize HAT controls: {exc}\n"
                  f"# (HAT seated? I2C enabled? try: i2cdetect -y 1)", file=sys.stderr)
            return 1
        collect_switched(
            port=args.port, baud=args.baud, controls=controls,
            log_dir=args.log_dir, fix_only=args.fix_only,
            gpx=not args.no_gpx, quiet=args.quiet,
            rtcm_source=args.rtcm_source, rtcm_baud=args.rtcm_baud,
            telemetry=args.telemetry, telemetry_interval=args.telemetry_interval,
        )
        return 0

    csv_path = args.csv
    gpx_path = args.gpx
    if csv_path is None and gpx_path is None:
        csv_path = f"{args.log_dir}/lg580p-{_default_stamp()}.csv"
    collect(port=args.port, baud=args.baud, csv_path=csv_path, gpx_path=gpx_path,
            quiet=args.quiet, fix_only=args.fix_only,
            rtcm_source=args.rtcm_source, rtcm_baud=args.rtcm_baud,
            telemetry=args.telemetry, telemetry_interval=args.telemetry_interval)
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    """Send PQTM config command(s) and show the receiver's response."""
    from .command import baseline_commands, build, send
    saved = False
    if args.config_cmd == "set-baseline":
        saved = not args.no_save
        sentences = baseline_commands(args.meters, save=saved)
    elif args.config_cmd == "get-baseline":
        sentences = ["PQTMCFGBLD,R"]
    elif args.config_cmd == "save":
        saved = True
        sentences = ["PQTMSAVEPAR"]
    elif args.config_cmd == "send":
        saved = args.save
        sentences = [args.sentence] + (["PQTMSAVEPAR"] if saved else [])
    else:
        print("# no config subcommand", file=sys.stderr)
        return 2

    print("# Sending:")
    for s in sentences:
        print("   " + build(s).strip())
    responses = send(args.port, args.baud, sentences, args.listen)
    acks = [r for r in responses if r.startswith("$PQTM")]
    print(f"\n# {len(responses)} lines seen; PQTM responses:")
    for r in acks:
        print("   " + r)
    if not acks:
        print("   (none — is the port free? stop the logger/service first)")
    if saved:
        print("\n# Saved to flash — power-cycle the module to apply.")
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

    co = sub.add_parser("collect", parents=[common], help="parse and log to CSV/GPX")
    co.add_argument("--baud", type=int, default=serial_io.DEFAULT_BAUD)
    co.add_argument("--log-dir", default="logs",
                    help="directory for log files (default: logs)")
    co.add_argument("--fix-only", action="store_true",
                    help="only log epochs that have a GPS fix")
    co.add_argument("--quiet", action="store_true", help="suppress the live status line")
    co.add_argument("--csv", help="CSV output path (default: <log-dir>/lg580p-<ts>.csv)")
    co.add_argument("--gpx", help="also write a GPX track of fixes")
    co.add_argument("--rtcm-source",
                    help="serial port of the RTCM correction radio (e.g. /dev/ttyUSB0); "
                         "forwarded to the LG580P for RTK")
    co.add_argument("--rtcm-baud", type=int, default=57600,
                    help="baud of the RTCM correction radio (default: 57600)")
    co.add_argument("--telemetry", action="store_true",
                    help="send $PRSTAT status out the RTCM radio for the base display "
                         "(requires --rtcm-source; shares the radio full-duplex)")
    co.add_argument("--telemetry-interval", type=float, default=1.0,
                    help="seconds between telemetry sends (default: 1.0)")
    sw = co.add_argument_group("switch mode (Multi-IO HAT dry-contact + LEDs)")
    sw.add_argument("--switch", action="store_true",
                    help="gate logging with the HAT dry-contact switch")
    sw.add_argument("--no-gpx", action="store_true", help="write only CSV per session")
    sw.add_argument("--hat-stack", type=int, default=0, help="HAT stack address (default: 0)")
    sw.add_argument("--gps-led", type=int, default=1,
                    help="HAT LED for GPS status: off=no fix, blink=fix, solid=RTK fixed (default: 1)")
    sw.add_argument("--logging-led", type=int, default=2,
                    help="HAT LED for logging status: off=idle, blink=logging (default: 2)")
    sw.add_argument("--contact-channel", type=int, default=1,
                    help="dry-contact/opto input channel (default: 1)")
    sw.add_argument("--contact-invert", action="store_true",
                    help="invert: OPEN contact = logging ON")
    co.set_defaults(func=cmd_collect)

    pr = sub.add_parser("parse", help="assemble readings from a captured file")
    pr.add_argument("file", help="file of NMEA sentences, or - for stdin")
    pr.add_argument("--emit-on", default="GGA",
                    help="sentence type that ends an epoch (default: GGA)")
    pr.set_defaults(func=cmd_parse)

    cfg = sub.add_parser("config", parents=[common],
                         help="send PQTM config commands (baseline, save, raw)")
    cfg.add_argument("--baud", type=int, default=serial_io.DEFAULT_BAUD)
    cfg.add_argument("--listen", type=float, default=2.0,
                     help="seconds to collect the response (default: 2)")
    csub = cfg.add_subparsers(dest="config_cmd", required=True)

    sb = csub.add_parser("set-baseline",
                         help="set dual-antenna baseline distance (meters, 0-5)")
    sb.add_argument("meters", type=float, help="antenna separation in meters (0=auto)")
    sb.add_argument("--no-save", action="store_true",
                    help="don't persist to flash (default: save)")

    csub.add_parser("get-baseline", help="read the configured baseline distance")
    csub.add_parser("save", help="save current config to flash (PQTMSAVEPAR)")

    snd = csub.add_parser("send", help="send a raw PQTM/NMEA command (checksum added)")
    snd.add_argument("sentence", help="command body, e.g. 'PQTMCFGBLD,W,1.000'")
    snd.add_argument("--save", action="store_true", help="also send PQTMSAVEPAR")

    cfg.set_defaults(func=cmd_config)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
