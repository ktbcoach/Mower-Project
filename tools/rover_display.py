#!/usr/bin/env python3
"""Base-station rover status dashboard (Tkinter).

Reads the latest ``$PRSTAT`` telemetry line that ``ntrip_to_serial.py --status-file``
mirrors from the rover (over the radio link) and shows it on the base Pi's
screen: RTK fix state, satellites + signal, position + heading, and link/logging
health. Refreshes a couple times a second; flags the link LOST if telemetry goes
stale.

Run:   python3 tools/rover_display.py [--status-file PATH] [--fullscreen]
Needs Tkinter (`sudo apt install python3-tk`). No serial access — it only reads
the status file, so it coexists with the base bridge that owns the radio port.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import tkinter as tk
from pathlib import Path

# Import the shared telemetry parser from the repo's src/ without needing an
# installed package (base clone has the source tree).
_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir():
    sys.path.insert(0, str(_SRC))
from lg580p.telemetry import RoverStatus, parse_status_sentence  # noqa: E402

STALE_S = 5.0  # no telemetry newer than this -> LINK LOST

BG = "#12151a"
FG = "#e8eaed"
DIM = "#8a929e"
CARD = "#1c2129"

FIX_COLORS = {
    4: "#2ecc71",  # RTK fixed
    5: "#a3d977",  # RTK float
    2: "#e67e22",  # DGPS
    1: "#f1c40f",  # GPS
}
FIX_NO = "#e74c3c"


def _cn0_color(cn0) -> str:
    if cn0 is None:
        return DIM
    if cn0 >= 45:
        return "#2ecc71"
    if cn0 >= 40:
        return "#a3d977"
    if cn0 >= 35:
        return "#e67e22"
    return "#e74c3c"


def _age_color(age) -> str:
    """Correction age: fresh corrections are what let RTK promote to Fixed."""
    if age is None:
        return DIM
    if age <= 2:
        return "#2ecc71"   # fresh — healthy
    if age <= 8:
        return "#e67e22"   # aging — link may be too slow to Fix
    return "#e74c3c"       # stale — corrections can't keep up


class Dashboard:
    def __init__(self, root: tk.Tk, status_file: str):
        self.root = root
        self.status_file = status_file
        root.title("Rover Status")
        root.configure(bg=BG)

        # Big fix-state banner.
        self.banner = tk.Label(root, text="STARTING", font=("DejaVu Sans", 40, "bold"),
                               bg=FIX_NO, fg="#000000", pady=14)
        self.banner.pack(fill="x", padx=10, pady=(10, 6))

        self.cells: dict[str, tk.Label] = {}
        grid = tk.Frame(root, bg=BG)
        grid.pack(fill="both", expand=True, padx=10, pady=6)
        layout = [
            ("Sats (used / view)", "sats"), ("Signal C/N0 max", "cn0max"),
            ("Signal C/N0 avg", "cn0avg"),  ("HDOP", "hdop"),
            ("Latitude", "lat"),            ("Longitude", "lon"),
            ("Heading", "hdg"),             ("Speed", "speed"),
            ("Corrections", "corr"),        ("Corr age", "corrage"),
            ("Logging", "log"),
        ]
        for i, (title, key) in enumerate(layout):
            self._make_cell(grid, i // 2, i % 2, title, key)
        for c in range(2):
            grid.columnconfigure(c, weight=1, uniform="col")

        # Footer: link health + timestamp.
        self.footer = tk.Label(root, text="", font=("DejaVu Sans Mono", 14),
                               bg=BG, fg=DIM, anchor="w")
        self.footer.pack(fill="x", padx=14, pady=(0, 10))

        self._tick()

    def _make_cell(self, parent, r, c, title, key):
        card = tk.Frame(parent, bg=CARD, bd=0)
        card.grid(row=r, column=c, sticky="nsew", padx=6, pady=6)
        tk.Label(card, text=title, font=("DejaVu Sans", 13), bg=CARD, fg=DIM,
                 anchor="w").pack(fill="x", padx=14, pady=(8, 0))
        val = tk.Label(card, text="--", font=("DejaVu Sans Mono", 28, "bold"),
                       bg=CARD, fg=FG, anchor="w")
        val.pack(fill="x", padx=14, pady=(0, 10))
        self.cells[key] = val

    def _read(self):
        try:
            mtime = os.path.getmtime(self.status_file)
            with open(self.status_file) as fh:
                line = fh.readline()
        except OSError:
            return None, None
        return parse_status_sentence(line), mtime

    def _tick(self):
        st, mtime = self._read()
        age = None if mtime is None else time.time() - mtime
        live = st is not None and age is not None and age < STALE_S
        self._render(st if live else None, st, age, live)
        self.root.after(500, self._tick)

    def _render(self, st, last, age, live):
        # Banner: fix state (or LINK LOST when telemetry is stale/missing).
        if not live:
            self.banner.config(text="LINK LOST", bg=FIX_NO, fg="#000000")
        else:
            name = (st.fix_quality_name or "no fix").upper().replace("_", "-")
            self.banner.config(text=name, bg=FIX_COLORS.get(st.fix_quality, FIX_NO),
                               fg="#000000")

        s = st if st is not None else RoverStatus()

        def fmt(v, suffix="", nd=None):
            if v is None:
                return "--"
            if nd is not None:
                return f"{v:.{nd}f}{suffix}"
            return f"{v}{suffix}"

        used = "--" if s.sats_used is None else str(s.sats_used)
        view = "--" if s.sats_tracked is None else str(s.sats_tracked)
        self.cells["sats"].config(text=f"{used} / {view}")
        self.cells["cn0max"].config(text=fmt(s.cn0_max, " dB", 1),
                                    fg=_cn0_color(s.cn0_max))
        self.cells["cn0avg"].config(text=fmt(s.cn0_avg, " dB", 1),
                                    fg=_cn0_color(s.cn0_avg))
        self.cells["hdop"].config(text=fmt(s.hdop, "", 1))
        self.cells["lat"].config(text=fmt(s.latitude_deg, "", 7))
        self.cells["lon"].config(text=fmt(s.longitude_deg, "", 7))
        hdg = "--" if s.heading_deg is None else f"{s.heading_deg:.1f}°"
        self.cells["hdg"].config(text=hdg)
        self.cells["speed"].config(
            text="--" if s.speed_kph is None else f"{s.speed_kph:.1f} km/h")
        self.cells["corr"].config(
            text="--" if s.corr is None else ("FLOWING" if s.corr else "none"),
            fg="#2ecc71" if s.corr else FG)
        self.cells["corrage"].config(
            text="--" if s.age_of_diff is None else f"{s.age_of_diff:.1f} s",
            fg=_age_color(s.age_of_diff))
        self.cells["log"].config(
            text="--" if s.logging is None else ("LOGGING" if s.logging else "idle"),
            fg="#2ecc71" if s.logging else FG)

        if age is None:
            self.footer.config(text="no telemetry file yet — is the base bridge running?")
        else:
            tag = "live" if live else "STALE"
            seq = "--" if last is None or last.seq is None else last.seq
            self.footer.config(text=f"link {tag}  ·  last {age:4.1f}s ago  ·  seq {seq}")


def main() -> int:
    p = argparse.ArgumentParser(description="Base-station rover status dashboard")
    default_file = str(Path(__file__).resolve().parent.parent / "rover-status.txt")
    p.add_argument("--status-file", default=default_file,
                   help=f"telemetry file written by the base bridge (default: {default_file})")
    p.add_argument("--fullscreen", action="store_true", help="run full-screen (kiosk)")
    args = p.parse_args()

    root = tk.Tk()
    root.geometry("760x520")
    if args.fullscreen:
        root.attributes("-fullscreen", True)
        root.bind("<Escape>", lambda _e: root.attributes("-fullscreen", False))
    Dashboard(root, args.status_file)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
