#!/usr/bin/env python3
"""NTRIP client -> serial radio bridge (runs on the base-side Pi).

Pulls an RTCM3 correction stream from an NTRIP caster (e.g. the VTrans RTN for
northern Vermont) and writes it to a serial port — the transparent radio that
carries corrections to the rover, where `lg580p collect --rtcm-source ...`
injects them into the LG580P.

Network-RTK / VRS mountpoints require the client to send its approximate
position as an NMEA GGA sentence; pass --lat/--lon (fixed operating area) to
enable that. Single-base mountpoints don't need it.

Standalone: only needs pyserial + the standard library.

Example:
    python3 ntrip_to_serial.py \
        --host rtn.vtrans.vermont.gov --port 2101 --mountpoint VRS_RTCM3 \
        --user USER --password PASS \
        --serial /dev/ttyUSB0 --serial-baud 57600 \
        --lat 44.42 --lon -72.98 --alt 200
"""

from __future__ import annotations

import argparse
import base64
import socket
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial required:  pip install pyserial")


def nmea_checksum(body: str) -> str:
    cs = 0
    for ch in body:
        cs ^= ord(ch)
    return f"{cs:02X}"


def build_gga(lat: float, lon: float, alt: float) -> bytes:
    """A minimal valid GGA at a fixed position (for VRS mountpoints)."""
    t = time.gmtime()
    hhmmss = f"{t.tm_hour:02d}{t.tm_min:02d}{t.tm_sec:02d}.00"
    lat_h = "N" if lat >= 0 else "S"
    lon_h = "E" if lon >= 0 else "W"
    lat, lon = abs(lat), abs(lon)
    lat_d = int(lat)
    lat_m = (lat - lat_d) * 60
    lon_d = int(lon)
    lon_m = (lon - lon_d) * 60
    body = (
        f"GPGGA,{hhmmss},{lat_d:02d}{lat_m:07.4f},{lat_h},"
        f"{lon_d:03d}{lon_m:07.4f},{lon_h},1,10,1.0,{alt:.1f},M,0.0,M,,"
    )
    return f"${body}*{nmea_checksum(body)}\r\n".encode("ascii")


def connect(args) -> socket.socket:
    sock = socket.create_connection((args.host, args.port), timeout=10)
    auth = base64.b64encode(f"{args.user}:{args.password}".encode()).decode()
    req = (
        f"GET /{args.mountpoint} HTTP/1.1\r\n"
        f"Host: {args.host}:{args.port}\r\n"
        f"Ntrip-Version: Ntrip/2.0\r\n"
        f"User-Agent: NTRIP lg580p-bridge/0.1\r\n"
        f"Authorization: Basic {auth}\r\n"
        f"Connection: close\r\n\r\n"
    )
    sock.sendall(req.encode())
    header = b""
    sock.settimeout(10)
    while b"\r\n\r\n" not in header:
        chunk = sock.recv(256)
        if not chunk:
            raise ConnectionError("caster closed during handshake")
        header += chunk
    line = header.split(b"\r\n", 1)[0].decode(errors="replace")
    if "200" not in line and "ICY 200" not in line:
        raise ConnectionError(f"caster rejected request: {line!r}")
    print(f"# connected: {line}")
    return sock


def run(args) -> int:
    ser = serial.Serial(args.serial, args.serial_baud, timeout=1)
    print(f"# forwarding RTCM -> {args.serial} @ {args.serial_baud}")
    gga = None
    if args.lat is not None and args.lon is not None:
        gga = build_gga(args.lat, args.lon, args.alt)
        print(f"# will send GGA every {args.gga_interval}s (VRS position)")

    total = 0
    while True:
        try:
            sock = connect(args)
            if gga:
                sock.sendall(gga)          # initial position so VRS starts
            last_gga = time.monotonic()
            sock.settimeout(30)
            while True:
                data = sock.recv(1024)
                if not data:
                    raise ConnectionError("stream ended")
                ser.write(data)
                total += len(data)
                sys.stdout.write(f"\r# {total} bytes forwarded")
                sys.stdout.flush()
                if gga and time.monotonic() - last_gga >= args.gga_interval:
                    sock.sendall(gga)
                    last_gga = time.monotonic()
        except KeyboardInterrupt:
            print("\n# stopped")
            return 0
        except Exception as exc:
            print(f"\n# connection error: {exc}; retrying in 5s")
            time.sleep(5)


def main() -> int:
    p = argparse.ArgumentParser(description="NTRIP -> serial radio bridge")
    p.add_argument("--host", required=True)
    p.add_argument("--port", type=int, default=2101)
    p.add_argument("--mountpoint", required=True)
    p.add_argument("--user", default="")
    p.add_argument("--password", default="")
    p.add_argument("--serial", required=True, help="base radio serial port")
    p.add_argument("--serial-baud", type=int, default=57600)
    p.add_argument("--lat", type=float, help="fixed GGA latitude (VRS mountpoints)")
    p.add_argument("--lon", type=float, help="fixed GGA longitude")
    p.add_argument("--alt", type=float, default=100.0, help="fixed GGA altitude (m)")
    p.add_argument("--gga-interval", type=float, default=10.0)
    return run(p.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
