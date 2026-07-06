#!/usr/bin/env python3
"""Raw serial port monitor — dumps whatever bytes arrive, no NTRIP/app involved.

Diagnostic tool for isolating "the phone app doesn't see rover telemetry" down
to hardware vs. software: plug the SAME USB-serial adapter used on the phone
into this machine (Windows/Linux/Mac) and run this. If $PRSTAT lines show up
here, the radio link and wiring are fine and the bug is Android-side software.
If NOTHING ever appears, it's the radio/wiring/half-duplex link itself, not the
app — the rover sends $PRSTAT once a second any time it has a fix, independent
of whether corrections are flowing, so this doesn't need NTRIP running at all.

Usage:
    python tools/serial_monitor.py COM3 --baud 19200
    python tools/serial_monitor.py /dev/ttyUSB0 --baud 19200

Highlights $PRSTAT lines; everything else prints as a hex+ascii dump so you can
tell "nothing arriving" apart from "garbage arriving" (e.g. wrong baud).
"""

from __future__ import annotations

import argparse
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial required:  pip install pyserial")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("port", help="serial port, e.g. COM3 or /dev/ttyUSB0")
    p.add_argument("--baud", type=int, default=19200)
    args = p.parse_args()

    ser = serial.Serial(args.port, args.baud, timeout=1)
    print(f"# listening on {args.port} @ {args.baud} — Ctrl+C to stop")
    print("# waiting for bytes... (rover sends $PRSTAT ~1/sec whenever it has a fix)")

    buf = bytearray()
    total = 0
    last_report = time.monotonic()
    try:
        while True:
            data = ser.read(256)
            if data:
                total += len(data)
                buf.extend(data)
                while True:
                    nl = buf.find(b"\n")
                    if nl == -1:
                        if len(buf) > 4096:
                            del buf[:-512]
                        break
                    line = bytes(buf[:nl]).decode("ascii", "replace").strip()
                    del buf[: nl + 1]
                    if not line:
                        continue
                    tag = " <-- $PRSTAT" if line.startswith("$PRSTAT") else ""
                    print(f"{time.strftime('%H:%M:%S')}  {line!r}{tag}")
            now = time.monotonic()
            if now - last_report >= 10:
                print(f"# {total} bytes received so far")
                last_report = now
    except KeyboardInterrupt:
        print(f"\n# stopped — {total} bytes received total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
