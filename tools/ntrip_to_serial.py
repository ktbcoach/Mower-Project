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

Use an RTCM 3.x mountpoint (e.g. the nearest station, VCAP_RTCM3) — NOT a CMRx
mountpoint (Trimble proprietary; the LG580P can't decode it). Credentials come
from NTRIP_USER / NTRIP_PASSWORD.

Examples:
    # List available mountpoints:
    python3 ntrip_to_serial.py --host 20.185.11.35 --port 2101 --list

    # Validate a mountpoint (no radio — MONITOR mode):
    python3 ntrip_to_serial.py --host 20.185.11.35 --port 2101 --mountpoint VCAP_RTCM3

    # Bridge corrections to the base radio:
    python3 ntrip_to_serial.py --host 20.185.11.35 --port 2101 --mountpoint VCAP_RTCM3 \
        --serial /dev/ttyUSB0 --serial-baud 57600
"""

from __future__ import annotations

import argparse
import base64
import os
import socket
import sys
import threading
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


class RtcmScanner:
    """Tally RTCM3 message types in a byte stream (validation aid; no CRC check)."""

    def __init__(self):
        self.buf = bytearray()
        self.counts: dict[int, int] = {}

    def feed(self, data: bytes) -> None:
        self.buf.extend(data)
        while True:
            i = self.buf.find(0xD3)
            if i == -1:
                self.buf.clear()
                return
            if i > 0:
                del self.buf[:i]
            if len(self.buf) < 3:
                return
            length = ((self.buf[1] & 0x03) << 8) | self.buf[2]
            frame_len = 3 + length + 3  # 0xD3 + len header, payload, 3-byte CRC
            if len(self.buf) < frame_len:
                return
            if length >= 2:
                p = self.buf[3:5]
                msg = (p[0] << 4) | (p[1] >> 4)
                self.counts[msg] = self.counts.get(msg, 0) + 1
            del self.buf[:frame_len]

    def summary(self) -> str:
        if not self.counts:
            return "(no complete RTCM3 frames yet)"
        return "  ".join(f"{m}:{n}" for m, n in sorted(self.counts.items()))


def list_sourcetable(args) -> int:
    """Fetch and print the caster's sourcetable (available mountpoints)."""
    sock = socket.create_connection((args.host, args.port), timeout=10)
    auth = base64.b64encode(f"{args.user}:{args.password}".encode()).decode()
    req = (
        f"GET / HTTP/1.1\r\n"
        f"Host: {args.host}:{args.port}\r\n"
        f"Ntrip-Version: Ntrip/2.0\r\n"
        f"User-Agent: NTRIP lg580p-bridge/0.1\r\n"
        f"Authorization: Basic {auth}\r\n"
        f"Connection: close\r\n\r\n"
    )
    sock.sendall(req.encode())
    data = b""
    sock.settimeout(10)
    try:
        while b"ENDSOURCETABLE" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    except socket.timeout:
        pass
    text = data.decode(errors="replace")
    print(f"# {text.splitlines()[0] if text else '(no response)'}")
    mounts = [ln.split(";") for ln in text.splitlines() if ln.startswith("STR;")]
    if mounts:
        print(f"# {len(mounts)} mountpoint(s):")
        for p in mounts:
            name = p[1] if len(p) > 1 else "?"
            fmt = p[3] if len(p) > 3 else ""
            print(f"    {name:<28} {fmt}")
    else:
        print("# no STR entries (sourcetable empty, restricted, or account inactive):")
        print(text[:600])
    return 0


def _write_atomic(path: str, text: str) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w") as fh:
        fh.write(text)
    os.replace(tmp, path)


class RoverPosition:
    """Thread-safe holder for the rover's latest reported position.

    The status thread writes it (from ``$PRSTAT`` telemetry); the main loop
    reads it to build the VRS GGA so the virtual base tracks the rover.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._pos: tuple[float, float, float] | None = None

    def set(self, lat: float, lon: float, alt: float) -> None:
        with self._lock:
            self._pos = (lat, lon, alt)

    def get(self) -> tuple[float, float, float] | None:
        with self._lock:
            return self._pos


def _prstat_position(line: str) -> tuple[float, float, float] | None:
    """Extract (lat, lon, alt) from a ``$PRSTAT`` line, or None.

    Field order (see lg580p.telemetry): PRSTAT,seq,fixq,satsU,satsT,cn0max,
    cn0avg,LAT,LON,ALT,...  — so lat/lon/alt are parts[7:10]. Validates the XOR
    checksum first (a radio glitch mustn't relocate the virtual base). Parsed
    inline so this file stays standalone (stdlib + pyserial only).
    """
    star = line.rfind("*")
    if star == -1 or star + 3 > len(line):
        return None
    if nmea_checksum(line[1:star]) != line[star + 1 : star + 3].upper():
        return None
    parts = line[1:star].split(",")
    try:
        lat, lon = float(parts[7]), float(parts[8])
        alt = float(parts[9]) if parts[9] else 0.0
    except (IndexError, ValueError):
        return None
    if lat == 0.0 and lon == 0.0:  # no real fix yet
        return None
    return lat, lon, alt


def status_reader(ser, path: str | None, rover_pos: "RoverPosition | None",
                  stop: threading.Event) -> None:
    """Read the radio for rover ``$PRSTAT`` telemetry. Mirrors the latest line to
    ``path`` (the base display polls it) and, when ``rover_pos`` is given, feeds
    the parsed rover position to the GGA builder. Runs in its own thread; the
    serial port is full-duplex so this reads while the main loop writes RTCM.
    """
    buf = bytearray()
    while not stop.is_set():
        try:
            data = ser.read(256)
        except Exception:
            time.sleep(0.5)
            continue
        if not data:
            continue
        buf.extend(data)
        while True:
            nl = buf.find(b"\n")
            if nl == -1:
                if len(buf) > 4096:  # runaway with no newline — keep the tail
                    del buf[:-512]
                break
            line = bytes(buf[:nl]).decode("ascii", "replace").strip()
            del buf[: nl + 1]
            if line.startswith("$PRSTAT"):
                if path:
                    try:
                        _write_atomic(path, line + "\n")
                    except OSError as exc:
                        print(f"# status-file write failed: {exc}")
                if rover_pos is not None:
                    pos = _prstat_position(line)
                    if pos is not None:
                        rover_pos.set(*pos)


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
    ser = None
    stop = threading.Event()
    rover_pos = RoverPosition() if args.gga_from_rover else None
    if args.serial:
        ser = serial.Serial(args.serial, args.serial_baud, timeout=1)
        print(f"# forwarding RTCM -> {args.serial} @ {args.serial_baud}")
        if args.status_file or rover_pos is not None:
            threading.Thread(
                target=status_reader, args=(ser, args.status_file, rover_pos, stop),
                name="status", daemon=True,
            ).start()
            if args.status_file:
                print(f"# rover telemetry -> {args.status_file}")
    else:
        print("# MONITOR mode (no --serial): validating the stream only")

    # GGA position for VRS mountpoints. A fixed --lat/--lon is the "seed"; with
    # --gga-from-rover the rover's own reported position takes over as soon as
    # the first telemetry arrives (and keeps tracking it), so the virtual base
    # follows the rover with no hand-entered coordinate.
    seed = None
    if args.lat is not None and args.lon is not None:
        seed = (args.lat, args.lon, args.alt)
    if rover_pos is not None:
        how = "seeded until first rover fix" if seed else "waiting for first rover fix"
        print(f"# GGA follows the rover's reported position ({how})")
    elif seed:
        print(f"# sending GGA every {args.gga_interval:g}s (fixed position for VRS mountpoints)")

    def gga_position():
        if rover_pos is not None:
            pos = rover_pos.get()
            if pos is not None:
                return pos
        return seed

    scanner = RtcmScanner()
    total = 0
    while True:
        try:
            sock = connect(args)
            sock.settimeout(1.0)
            last_report = last_rx = time.monotonic()
            last_gga = 0.0
            last_sent_key = None
            while True:
                now = time.monotonic()
                # Send GGA as soon as we have a position, then every interval.
                # A short recv timeout (below) lets this fire promptly even when
                # the caster is sending nothing yet (it waits for our GGA first).
                pos = gga_position()
                if pos is not None and (last_gga == 0.0 or now - last_gga >= args.gga_interval):
                    sock.sendall(build_gga(*pos))
                    last_gga = now
                    key = (round(pos[0], 5), round(pos[1], 5))  # ~1 m
                    if key != last_sent_key:
                        print(f"# GGA position -> {pos[0]:.7f},{pos[1]:.7f}")
                        last_sent_key = key
                try:
                    data = sock.recv(1024)
                except socket.timeout:
                    if now - last_rx > 30:
                        raise ConnectionError("no data for 30s")
                    continue
                if not data:
                    raise ConnectionError("stream ended")
                last_rx = time.monotonic()
                if ser:
                    ser.write(data)
                scanner.feed(data)
                total += len(data)
                if now - last_report >= 3:
                    print(f"# {total} bytes; RTCM msgs {scanner.summary()}")
                    last_report = now
        except KeyboardInterrupt:
            stop.set()
            print("\n# stopped")
            return 0
        except Exception as exc:
            print(f"\n# connection error: {exc}; retrying in 5s")
            time.sleep(5)


def main() -> int:
    p = argparse.ArgumentParser(description="NTRIP -> serial radio bridge")
    p.add_argument("--host", required=True)
    p.add_argument("--port", type=int, default=2101)
    p.add_argument("--mountpoint", help="required unless --list")
    p.add_argument("--list", action="store_true",
                   help="print the caster's sourcetable (mountpoints) and exit")
    # Prefer env vars so credentials stay out of shell history / process list
    # (and out of the repo). Set NTRIP_USER / NTRIP_PASSWORD, or pass --user/--password.
    p.add_argument("--user", default=os.environ.get("NTRIP_USER", ""))
    p.add_argument("--password", default=os.environ.get("NTRIP_PASSWORD", ""))
    p.add_argument("--serial", help="base radio serial port; omit to MONITOR/validate only")
    p.add_argument("--serial-baud", type=int, default=57600)
    p.add_argument("--status-file",
                   help="mirror the rover's latest $PRSTAT telemetry here (for the base display)")
    p.add_argument("--lat", type=float, help="fixed GGA latitude (VRS mountpoints)")
    p.add_argument("--lon", type=float, help="fixed GGA longitude")
    p.add_argument("--alt", type=float, default=100.0, help="fixed GGA altitude (m)")
    p.add_argument("--gga-interval", type=float, default=10.0)
    p.add_argument("--gga-from-rover", action="store_true",
                   help="build the VRS GGA from the rover's reported position (via $PRSTAT "
                        "telemetry) so the virtual base tracks the rover; --lat/--lon, if "
                        "given, seed it until the first rover fix arrives")
    args = p.parse_args()
    if args.list:
        return list_sourcetable(args)
    if not args.mountpoint:
        p.error("--mountpoint is required (or use --list to see available ones)")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
