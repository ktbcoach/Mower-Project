#!/usr/bin/env python3
"""Android base station — NTRIP -> USB-serial radio bridge + rover dashboard.

The phone/tablet edition of the Pi/Windows base station, as ONE self-contained
Kivy app for Pydroid 3. Unlike the Pi (separate systemd bridge + Tkinter display
talking through rover-status.txt), Android has no console and one USB device, so
this app does both in-process:

  * a background thread pulls the VTrans RTN RTCM3 stream from the NTRIP caster
    and writes it out the USB-C -> USB-serial converter (the base radio), and
    drains the same port for the rover's $PRSTAT telemetry (full-duplex);
  * the Kivy UI shows the same rover-status dashboard as tools/rover_display.py
    (fix-state banner, sats/signal, position/heading, link health) plus a
    bridge-status line and Start/Stop + Settings controls.

USB serial uses usb4a + usbserial4a (Android USB Host API; handles the on-screen
permission prompt). On a desktop it falls back to pyserial + a COM port, so you
can develop and test the exact same file on the Windows base box first.

--- Pydroid 3 setup -------------------------------------------------------------
  1. Pydroid 3 -> Menu -> Pip, install:  kivy   usbserial4a   (usb4a comes with it)
  2. Copy this file onto the device (Pydroid's file browser, or Downloads).
  3. Plug the USB-serial radio into the phone via a USB-C OTG adapter.
  4. Open this file in Pydroid and press Run. Tap "Settings", fill in your VTrans
     NTRIP_USER / NTRIP_PASSWORD (and mountpoint/baud if not the defaults), Save.
  5. Tap "Start". Accept the Android USB-permission dialog when it appears; the
     bridge connects automatically once granted.

Config persists to base_config.json next to this file. Credentials live only in
that on-device file — never commit it.

--- Desktop dev (Windows/Linux/Mac) --------------------------------------------
  pip install kivy pyserial
  Set "serial_port" (e.g. COM3) in Settings; the USB permission flow is skipped.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import threading
import time
from dataclasses import dataclass
from typing import Optional

# --- platform: Android USB host vs. desktop pyserial ---------------------------
ON_ANDROID = False
_IMPORT_ERR = ""
try:
    from usb4a import usb            # noqa: F401  (present only on Android)
    from usbserial4a import serial4a  # noqa: F401
    ON_ANDROID = True
except Exception as _e:  # not installed / not on Android
    _IMPORT_ERR = str(_e) or _e.__class__.__name__

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "base_config.json")

DEFAULTS = {
    "host": "20.185.11.35",
    "port": 2101,
    "mountpoint": "VCAP_RTCM3",
    "user": "",
    "password": "",
    "serial_baud": 19200,
    "serial_port": "COM3",   # desktop only; Android auto-picks the USB device
    "lat": "",               # set BOTH lat+lon for VRS / network-RTK mountpoints
    "lon": "",
    "gga_interval": 10.0,
}

STALE_S = 5.0  # no telemetry newer than this -> LINK LOST

# ==============================================================================
# Telemetry wire format (inlined from src/lg580p so this file stands alone).
# Must stay byte-identical to lg580p.telemetry / lg580p.nmea.
# ==============================================================================

FIX_QUALITY = {
    0: "no_fix", 1: "gps", 2: "dgps", 3: "pps", 4: "rtk_fixed",
    5: "rtk_float", 6: "estimated", 7: "manual", 8: "simulation",
}

_PRSTAT_FIELDS = (
    "seq", "fix_quality", "sats_used", "sats_tracked", "cn0_max", "cn0_avg",
    "latitude_deg", "longitude_deg", "altitude_m", "heading_deg",
    "heading_quality", "hdop", "speed_kph", "logging", "corr",
)


def checksum_ok(sentence: str) -> bool:
    """Validate the ``*HH`` XOR checksum of an NMEA sentence."""
    sentence = sentence.strip()
    if not sentence.startswith("$"):
        return False
    star = sentence.rfind("*")
    if star == -1 or star + 3 > len(sentence):
        return False
    body = sentence[1:star]
    given = sentence[star + 1 : star + 3]
    calc = 0
    for ch in body:
        calc ^= ord(ch)
    try:
        return calc == int(given, 16)
    except ValueError:
        return False


@dataclass
class RoverStatus:
    seq: Optional[int] = None
    fix_quality: Optional[int] = None
    sats_used: Optional[int] = None
    sats_tracked: Optional[int] = None
    cn0_max: Optional[float] = None
    cn0_avg: Optional[float] = None
    latitude_deg: Optional[float] = None
    longitude_deg: Optional[float] = None
    altitude_m: Optional[float] = None
    heading_deg: Optional[float] = None
    heading_quality: Optional[int] = None
    hdop: Optional[float] = None
    speed_kph: Optional[float] = None
    logging: Optional[bool] = None
    corr: Optional[bool] = None

    @property
    def fix_quality_name(self) -> Optional[str]:
        if self.fix_quality is None:
            return None
        return FIX_QUALITY.get(self.fix_quality, f"unknown({self.fix_quality})")


def parse_status_sentence(line: str) -> Optional[RoverStatus]:
    """Parse a ``$PRSTAT`` line, or ``None`` if it isn't one / checksum fails."""
    line = line.strip()
    if not line.startswith("$PRSTAT,") or not checksum_ok(line):
        return None
    star = line.rfind("*")
    parts = line[1:star].split(",")[1:]  # drop the talker
    if len(parts) < len(_PRSTAT_FIELDS):
        return None
    d = dict(zip(_PRSTAT_FIELDS, parts))

    def _i(v):
        return int(v) if v not in ("", None) else None

    def _fl(v):
        return float(v) if v not in ("", None) else None

    def _b(v):
        return None if v in ("", None) else (v == "1")

    return RoverStatus(
        seq=_i(d["seq"]), fix_quality=_i(d["fix_quality"]),
        sats_used=_i(d["sats_used"]), sats_tracked=_i(d["sats_tracked"]),
        cn0_max=_fl(d["cn0_max"]), cn0_avg=_fl(d["cn0_avg"]),
        latitude_deg=_fl(d["latitude_deg"]), longitude_deg=_fl(d["longitude_deg"]),
        altitude_m=_fl(d["altitude_m"]), heading_deg=_fl(d["heading_deg"]),
        heading_quality=_i(d["heading_quality"]), hdop=_fl(d["hdop"]),
        speed_kph=_fl(d["speed_kph"]), logging=_b(d["logging"]), corr=_b(d["corr"]),
    )


# ==============================================================================
# NTRIP + RTCM helpers (ported from tools/ntrip_to_serial.py).
# ==============================================================================

def _nmea_checksum(body: str) -> str:
    cs = 0
    for ch in body:
        cs ^= ord(ch)
    return f"{cs:02X}"


def build_gga(lat: float, lon: float, alt: float = 100.0) -> bytes:
    """A minimal valid GGA at a fixed position (for VRS mountpoints)."""
    t = time.gmtime()
    hhmmss = f"{t.tm_hour:02d}{t.tm_min:02d}{t.tm_sec:02d}.00"
    lat_h = "N" if lat >= 0 else "S"
    lon_h = "E" if lon >= 0 else "W"
    lat, lon = abs(lat), abs(lon)
    lat_d, lon_d = int(lat), int(lon)
    lat_m, lon_m = (lat - lat_d) * 60, (lon - lon_d) * 60
    body = (
        f"GPGGA,{hhmmss},{lat_d:02d}{lat_m:07.4f},{lat_h},"
        f"{lon_d:03d}{lon_m:07.4f},{lon_h},1,10,1.0,{alt:.1f},M,0.0,M,,"
    )
    return f"${body}*{_nmea_checksum(body)}\r\n".encode("ascii")


class RtcmScanner:
    """Tally RTCM3 message types in a byte stream (no CRC check)."""

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
            frame_len = 3 + length + 3
            if len(self.buf) < frame_len:
                return
            if length >= 2:
                p = self.buf[3:5]
                msg = (p[0] << 4) | (p[1] >> 4)
                self.counts[msg] = self.counts.get(msg, 0) + 1
            del self.buf[:frame_len]

    def summary(self) -> str:
        if not self.counts:
            return ""
        return "  ".join(f"{m}:{n}" for m, n in sorted(self.counts.items()))


class PermissionPending(Exception):
    """Raised while the Android USB-permission dialog is outstanding."""


def open_serial(cfg: dict, stop: threading.Event):
    """Open the base radio. Android: first USB device via usb4a (waits for the
    permission grant). Desktop: pyserial on cfg['serial_port']. Returns
    (port, human_label)."""
    baud = int(cfg["serial_baud"])
    if ON_ANDROID:
        devices = usb.get_usb_device_list()
        if not devices:
            raise IOError("no USB device found — check the OTG cable/adapter")
        dev = devices[0]
        name = dev.getDeviceName()
        if not usb.has_usb_permission(dev):
            usb.request_usb_permission(dev)
            # Poll until the user accepts the system dialog (or we're stopped).
            while not stop.is_set() and not usb.has_usb_permission(dev):
                time.sleep(0.4)
            if stop.is_set():
                raise PermissionPending(name)
        port = serial4a.get_serial_port(name, baud, 8, "N", 1)
        try:
            label = dev.getProductName() or name
        except Exception:
            label = name
        return port, f"{label} @ {baud}"
    else:
        import serial
        port = serial.Serial(cfg["serial_port"], baud, timeout=1)
        return port, f"{cfg['serial_port']} @ {baud}"


def ntrip_connect(cfg: dict) -> socket.socket:
    sock = socket.create_connection((cfg["host"], int(cfg["port"])), timeout=10)
    auth = base64.b64encode(f"{cfg['user']}:{cfg['password']}".encode()).decode()
    req = (
        f"GET /{cfg['mountpoint']} HTTP/1.1\r\n"
        f"Host: {cfg['host']}:{cfg['port']}\r\n"
        f"Ntrip-Version: Ntrip/2.0\r\n"
        f"User-Agent: NTRIP lg580p-android/0.1\r\n"
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
    return sock


# ==============================================================================
# Bridge worker — one background thread does socket + serial (no port locking).
# ==============================================================================

class Bridge:
    def __init__(self):
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.cfg: dict = {}
        # Shared state read by the UI (guard with self.lock).
        self.conn = "idle"
        self.detail = ""
        self.error = ""
        self.port_label = ""
        self.total_bytes = 0
        self.rtcm = ""
        self.status: Optional[RoverStatus] = None
        self.status_ts = 0.0
        self._telem_buf = bytearray()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "conn": self.conn, "detail": self.detail, "error": self.error,
                "port_label": self.port_label, "total_bytes": self.total_bytes,
                "rtcm": self.rtcm, "status": self.status, "status_ts": self.status_ts,
                "running": self.running,
            }

    def _set(self, **kw):
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def start(self, cfg: dict):
        if self.running:
            return
        self.cfg = dict(cfg)
        self._stop.clear()
        with self.lock:
            self.conn, self.error, self.total_bytes, self.rtcm = "starting", "", 0, ""
            self.status, self.status_ts = None, 0.0
        self._thread = threading.Thread(target=self._run, name="bridge", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    # -- internals --
    def _feed_telemetry(self, data: bytes):
        self._telem_buf.extend(data)
        while True:
            nl = self._telem_buf.find(b"\n")
            if nl == -1:
                if len(self._telem_buf) > 4096:
                    del self._telem_buf[:-512]
                return
            line = bytes(self._telem_buf[:nl]).decode("ascii", "replace").strip()
            del self._telem_buf[: nl + 1]
            if line.startswith("$PRSTAT"):
                st = parse_status_sentence(line)
                if st is not None:
                    self._set(status=st, status_ts=time.time())

    def _drain_telemetry(self, port):
        try:
            n = getattr(port, "in_waiting", 0) or 0
        except Exception:
            n = 0
        if n:
            try:
                data = port.read(n)
            except Exception:
                data = b""
            if data:
                self._feed_telemetry(data)

    def _run(self):
        port = None
        try:
            # Open the radio once; reused across NTRIP reconnects.
            self._set(conn="opening radio")
            try:
                port, label = open_serial(self.cfg, self._stop)
            except PermissionPending:
                self._set(conn="stopped", error="USB permission not granted — tap Start again")
                return
            except Exception as exc:
                # Surface the real reason instead of letting the thread die silently.
                if not ON_ANDROID:
                    hint = (f"usbserial4a not loaded ({_IMPORT_ERR or 'not installed'}); "
                            f"install it in Pydroid, or set a valid serial_port on desktop")
                    self._set(conn="stopped", error=f"{exc} — {hint}")
                else:
                    self._set(conn="stopped", error=f"USB radio open failed: {exc}")
                return
            self._set(port_label=label, detail="radio open")

            gga = None
            lat, lon = self.cfg.get("lat", ""), self.cfg.get("lon", "")
            if lat and lon:
                gga = build_gga(float(lat), float(lon))

            scanner = RtcmScanner()
            total = 0
            while not self._stop.is_set():
                try:
                    self._set(conn="connecting", detail=f"{self.cfg['host']}:{self.cfg['port']}/{self.cfg['mountpoint']}")
                    sock = ntrip_connect(self.cfg)
                    self._set(conn="streaming", error="")
                    if gga:
                        sock.sendall(gga)
                    last_gga = time.monotonic()
                    sock.settimeout(1.0)
                    while not self._stop.is_set():
                        try:
                            data = sock.recv(1024)
                            if not data:
                                raise ConnectionError("stream ended")
                            port.write(data)
                            scanner.feed(data)
                            total += len(data)
                            self._set(total_bytes=total, rtcm=scanner.summary())
                        except socket.timeout:
                            pass  # no RTCM this second — fall through to telemetry
                        # Full-duplex: drain the rover's $PRSTAT from the radio.
                        self._drain_telemetry(port)
                        now = time.monotonic()
                        if gga and now - last_gga >= float(self.cfg.get("gga_interval", 10.0)):
                            sock.sendall(gga)
                            last_gga = now
                    try:
                        sock.close()
                    except Exception:
                        pass
                except Exception as exc:
                    if self._stop.is_set():
                        break
                    self._set(conn="reconnecting", error=str(exc))
                    # Sleep in small steps so Stop is responsive.
                    for _ in range(10):
                        if self._stop.is_set():
                            break
                        self._drain_telemetry(port)
                        time.sleep(0.5)
        finally:
            if port is not None:
                try:
                    port.close()
                except Exception:
                    pass
            self._set(conn="stopped")


# ==============================================================================
# Config persistence
# ==============================================================================

def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH) as fh:
            cfg.update(json.load(fh))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w") as fh:
        json.dump(cfg, fh, indent=2)


# ==============================================================================
# Kivy UI — same dashboard as tools/rover_display.py, in Kivy.
# ==============================================================================

from kivy.app import App                                # noqa: E402
from kivy.clock import Clock                             # noqa: E402
from kivy.core.window import Window                      # noqa: E402
from kivy.graphics import Color, Rectangle               # noqa: E402
from kivy.uix.boxlayout import BoxLayout                 # noqa: E402
from kivy.uix.button import Button                       # noqa: E402
from kivy.uix.gridlayout import GridLayout               # noqa: E402
from kivy.uix.label import Label                         # noqa: E402
from kivy.uix.popup import Popup                         # noqa: E402
from kivy.uix.textinput import TextInput                 # noqa: E402


def rgba(h: str, a: float = 1.0):
    h = h.lstrip("#")
    return (int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255, a)


BG = rgba("#12151a")
FG = rgba("#e8eaed")
DIM = rgba("#8a929e")
CARD = rgba("#1c2129")
GREEN = rgba("#2ecc71")
FIX_COLORS = {4: rgba("#2ecc71"), 5: rgba("#a3d977"), 2: rgba("#e67e22"), 1: rgba("#f1c40f")}
FIX_NO = rgba("#e74c3c")


def cn0_color(cn0):
    if cn0 is None:
        return DIM
    if cn0 >= 45:
        return rgba("#2ecc71")
    if cn0 >= 40:
        return rgba("#a3d977")
    if cn0 >= 35:
        return rgba("#e67e22")
    return rgba("#e74c3c")


class BGBox(BoxLayout):
    """A BoxLayout with a solid background colour (Kivy Labels have none)."""

    def __init__(self, bg, **kw):
        super().__init__(**kw)
        with self.canvas.before:
            self._color = Color(*bg)
            self._rect = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_a):
        self._rect.pos = self.pos
        self._rect.size = self.size

    def set_bg(self, color):
        self._color.rgba = color


class ValueCard(BGBox):
    """A titled value cell matching the Tkinter dashboard cards."""

    def __init__(self, title, **kw):
        super().__init__(CARD, orientation="vertical", padding=(14, 8), **kw)
        self.title = Label(text=title, color=DIM, font_size="15sp", halign="left",
                           valign="middle", size_hint_y=0.4)
        self.title.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        self.value = Label(text="--", color=FG, font_size="26sp", bold=True,
                           halign="left", valign="middle", size_hint_y=0.6)
        self.value.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        self.add_widget(self.title)
        self.add_widget(self.value)

    def set(self, text, color=FG):
        self.value.text = text
        self.value.color = color


class Dashboard(BoxLayout):
    def __init__(self, bridge: Bridge, cfg: dict, **kw):
        super().__init__(orientation="vertical", padding=8, spacing=6, **kw)
        self.bridge = bridge
        self.cfg = cfg

        # Fix-state banner.
        self.banner_box = BGBox(FIX_NO, size_hint_y=None, height="88dp")
        self.banner = Label(text="IDLE", color=rgba("#000000"), font_size="40sp", bold=True)
        self.banner_box.add_widget(self.banner)
        self.add_widget(self.banner_box)

        # Bridge status line.
        self.bridge_line = Label(text="", color=DIM, font_size="13sp", halign="left",
                                 valign="middle", size_hint_y=None, height="24dp")
        self.bridge_line.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        self.add_widget(self.bridge_line)

        # Value grid (same cells/order as tools/rover_display.py).
        grid = GridLayout(cols=2, spacing=6)
        self.cells: dict[str, ValueCard] = {}
        layout = [
            ("Sats (used / view)", "sats"), ("Signal C/N0 max", "cn0max"),
            ("Signal C/N0 avg", "cn0avg"), ("HDOP", "hdop"),
            ("Latitude", "lat"), ("Longitude", "lon"),
            ("Heading", "hdg"), ("Speed", "speed"),
            ("Corrections", "corr"), ("Logging", "log"),
        ]
        for title, key in layout:
            card = ValueCard(title)
            self.cells[key] = card
            grid.add_widget(card)
        self.add_widget(grid)

        # Footer + controls.
        self.footer = Label(text="", color=DIM, font_size="13sp", halign="left",
                            valign="middle", size_hint_y=None, height="24dp")
        self.footer.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        self.add_widget(self.footer)

        controls = BoxLayout(size_hint_y=None, height="56dp", spacing=8)
        self.start_btn = Button(text="Start", font_size="20sp", on_release=self._toggle)
        settings_btn = Button(text="Settings", font_size="20sp", on_release=self._settings)
        controls.add_widget(self.start_btn)
        controls.add_widget(settings_btn)
        self.add_widget(controls)

        Clock.schedule_interval(self._refresh, 0.5)

    # -- controls --
    def _toggle(self, *_a):
        if self.bridge.running:
            self.bridge.stop()
        else:
            missing = not self.cfg.get("user") or not self.cfg.get("password")
            if missing:
                self._settings()
                return
            self.bridge.start(self.cfg)

    def _settings(self, *_a):
        self._popup = SettingsPopup(self.cfg, on_save=self._on_save)
        self._popup.open()

    def _on_save(self, new_cfg):
        self.cfg.update(new_cfg)
        save_config(self.cfg)

    # -- render --
    def _refresh(self, _dt):
        snap = self.bridge.snapshot()
        st = snap["status"]
        age = None if snap["status_ts"] == 0 else time.time() - snap["status_ts"]
        live = st is not None and age is not None and age < STALE_S

        self.start_btn.text = "Stop" if snap["running"] else "Start"

        # Banner: fix state, or LINK LOST / bridge state.
        if snap["running"] and not live:
            if st is None:
                self.banner.text = snap["conn"].upper()
                self.banner_box.set_bg(FIX_NO)
            else:
                self.banner.text = "LINK LOST"
                self.banner_box.set_bg(FIX_NO)
        elif not snap["running"]:
            self.banner.text = "START FAILED" if snap["error"] else "IDLE"
            self.banner_box.set_bg(FIX_NO)
        else:
            name = (st.fix_quality_name or "no fix").upper().replace("_", "-")
            self.banner.text = name
            self.banner_box.set_bg(FIX_COLORS.get(st.fix_quality, FIX_NO))

        s = st if st is not None else RoverStatus()

        def f(v, suffix="", nd=None):
            if v is None:
                return "--"
            return f"{v:.{nd}f}{suffix}" if nd is not None else f"{v}{suffix}"

        used = "--" if s.sats_used is None else str(s.sats_used)
        view = "--" if s.sats_tracked is None else str(s.sats_tracked)
        self.cells["sats"].set(f"{used} / {view}")
        self.cells["cn0max"].set(f(s.cn0_max, " dB", 1), cn0_color(s.cn0_max))
        self.cells["cn0avg"].set(f(s.cn0_avg, " dB", 1), cn0_color(s.cn0_avg))
        self.cells["hdop"].set(f(s.hdop, "", 1))
        self.cells["lat"].set(f(s.latitude_deg, "", 7))
        self.cells["lon"].set(f(s.longitude_deg, "", 7))
        self.cells["hdg"].set("--" if s.heading_deg is None else f"{s.heading_deg:.1f} deg")
        self.cells["speed"].set("--" if s.speed_kph is None else f"{s.speed_kph:.1f} km/h")
        self.cells["corr"].set("--" if s.corr is None else ("FLOWING" if s.corr else "none"),
                               GREEN if s.corr else FG)
        self.cells["log"].set("--" if s.logging is None else ("LOGGING" if s.logging else "idle"),
                              GREEN if s.logging else FG)

        # Bridge status line: connection + bytes + RTCM types + USB radio.
        parts = [f"NTRIP: {snap['conn']}"]
        if not snap["running"]:
            # Platform indicator: on a phone this MUST read "Android USB"; if it
            # reads "desktop pyserial", usbserial4a isn't installed in Pydroid.
            parts.append("Android USB" if ON_ANDROID else "desktop pyserial")
        if snap["port_label"]:
            parts.append(f"radio {snap['port_label']}")
        if snap["total_bytes"]:
            parts.append(f"{snap['total_bytes']} B")
        if snap["rtcm"]:
            parts.append(f"RTCM {snap['rtcm']}")
        if snap["error"]:
            parts.append(f"! {snap['error']}")
        self.bridge_line.text = "   ".join(parts)

        # Footer: link health.
        if age is None:
            self.footer.text = "no rover telemetry yet" if snap["running"] else "bridge stopped"
        else:
            tag = "live" if live else "STALE"
            seq = "--" if st is None or st.seq is None else st.seq
            self.footer.text = f"link {tag}   last {age:4.1f}s ago   seq {seq}"


# Location presets for the Settings "quick fill" row. A VRS / network-RTK
# mountpoint needs an approximate position (GGA); a single-base mountpoint does
# not. The VRS mountpoint name matches docs/LG580P.md — confirm it against the
# caster's sourcetable if unsure (e.g. start_base.ps1 -ListMountpoints).
VRS_MOUNTPOINT = "VRS_RTCM3"
LOCATION_PRESETS = [
    # (button label, mountpoint, lat, lon)
    ("Current (VRS)", VRS_MOUNTPOINT, "44.585979", "-71.947149"),
    ("Perim site (VRS)", VRS_MOUNTPOINT, "44.420137", "-72.983771"),
    ("Single-base", "VCAP_RTCM3", "", ""),
]


class SettingsPopup(Popup):
    FIELDS = [
        ("host", "NTRIP host", False), ("port", "NTRIP port", False),
        ("mountpoint", "Mountpoint (RTCM 3.x)", False),
        ("user", "NTRIP user", False), ("password", "NTRIP password", True),
        ("serial_baud", "Radio baud", False),
        ("serial_port", "Serial port (desktop COMx; ignored on Android)", False),
        ("lat", "GGA lat (VRS only)", False), ("lon", "GGA lon (VRS only)", False),
    ]

    def __init__(self, cfg: dict, on_save, **kw):
        self._inputs: dict[str, TextInput] = {}
        body = BoxLayout(orientation="vertical", spacing=6, padding=6)

        # Quick-fill presets: set the mountpoint + GGA position in one tap.
        # VRS presets switch to the VRS mountpoint and fill lat/lon; the
        # single-base preset reverts and clears the position (no GGA sent).
        body.add_widget(Label(
            text="Quick fill (mountpoint + position):", color=DIM, font_size="12sp",
            halign="left", valign="middle", size_hint_y=None, height="22dp"))
        preset_row = BoxLayout(size_hint_y=None, height="44dp", spacing=6)
        for plabel, mp, plat, plon in LOCATION_PRESETS:
            preset_row.add_widget(Button(
                text=plabel, font_size="13sp",
                on_release=lambda _b, m=mp, la=plat, lo=plon: self._apply_preset(m, la, lo)))
        body.add_widget(preset_row)

        for key, label, secret in self.FIELDS:
            row = BoxLayout(size_hint_y=None, height="40dp", spacing=6)
            row.add_widget(Label(text=label, color=FG, font_size="13sp", size_hint_x=0.45,
                                 halign="left", valign="middle"))
            ti = TextInput(text=str(cfg.get(key, "")), multiline=False, password=secret,
                           size_hint_x=0.55, font_size="15sp")
            self._inputs[key] = ti
            row.add_widget(ti)
            body.add_widget(row)
        btns = BoxLayout(size_hint_y=None, height="48dp", spacing=8)
        btns.add_widget(Button(text="Save", on_release=self._save))
        btns.add_widget(Button(text="Cancel", on_release=lambda *_: self.dismiss()))
        body.add_widget(btns)
        super().__init__(title="Base station settings", content=body,
                         size_hint=(0.95, 0.95), **kw)
        self._on_save = on_save

    def _apply_preset(self, mountpoint, lat, lon):
        """Fill the mountpoint + position fields from a preset (still needs Save)."""
        self._inputs["mountpoint"].text = mountpoint
        self._inputs["lat"].text = lat
        self._inputs["lon"].text = lon

    def _save(self, *_a):
        out = {}
        for key, _label, _secret in self.FIELDS:
            val = self._inputs[key].text.strip()
            if key in ("port",):
                try:
                    val = int(val)
                except ValueError:
                    val = DEFAULTS[key]
            out[key] = val
        self._on_save(out)
        self.dismiss()


class BaseStationApp(App):
    title = "Rover Base Station"

    def build(self):
        Window.clearcolor = BG
        self.bridge = Bridge()
        self.cfg = load_config()
        return Dashboard(self.bridge, self.cfg)

    def on_stop(self):
        self.bridge.stop()


if __name__ == "__main__":
    BaseStationApp().run()
