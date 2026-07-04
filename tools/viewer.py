#!/usr/bin/env python3
"""
Mower run viewer.

Loads an LG580P (or Watson) CSV file and displays:
  - Green shaded area  : total swept blade path (54-inch cut)
  - Red line           : mower centre track
  - Black line + arrow : antenna baseline and forward heading at the slider position
  - Slider             : scrub through every logged frame

Usage:
    python tools/viewer.py [path/to/run.csv]
    python tools/viewer.py          # opens a file-picker dialog

Dependencies:
    pip install matplotlib numpy
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.collections import PolyCollection
from matplotlib.widgets import Slider

# ── Mower geometry (LG580P dual-antenna mounting) ───────────────────────────────
IN = 0.0254   # inches → metres

# Heading correction: the LG580P's PQTMTAR heading runs along the antenna
# baseline. The primary (position) antenna is on the LEFT and the secondary on
# the RIGHT, so the baseline (main→secondary) points to the mower's RIGHT — i.e.
# 90° clockwise of travel. True forward heading = reported + HEADING_OFFSET_DEG.
# (If the heading arrow ends up pointing backwards, flip this to +90.)
HEADING_OFFSET_DEG = -90.0

# Position is reported at the MAIN (left) antenna. Offsets below are in the mower
# body frame relative to that antenna: forward = +, right = +.
ANTENNA_SPACING_M         = 1.0       # configured 1 m baseline (main→secondary)
RIGHT_ANT_RIGHT_OF_CL_M   = 18 * IN   # right/secondary antenna, 18" right of centreline
# The main (left) antenna is one baseline left of the secondary, so it sits
# (baseline − 18") ≈ 21.4" LEFT of the centreline; centreline is that far to its right.
CENTERLINE_RIGHT_OF_GPS_M = ANTENNA_SPACING_M - RIGHT_ANT_RIGHT_OF_CL_M
BLADES_FWD_OF_GPS_M       = 24 * IN   # outer blades sit 24" ahead of the antennas

# Cutting deck: 3 blades. Outer blades 36" apart (centres ±18" from centreline),
# 9" radius each → 54" cut width. Centre blade on the centreline, 6" ahead of the
# outer blades.
CUT_WIDTH_M          = 54 * IN
HALF_CUT_M           = CUT_WIDTH_M / 2
BLADE_RADIUS_M       = 9 * IN
OUTER_BLADE_OFF_M    = 18 * IN        # outer blade centres, ± from centreline
CENTRE_BLADE_FWD_M   = 6 * IN         # centre blade ahead of the outer blades
DECK_DEPTH_M         = 24 * IN        # fore-aft blade envelope (visual only)
N_BLADES             = 3

# Heading arrow length from the mower centre.
ARROW_LEN_M = 24 * IN


# ── Geometry helpers ───────────────────────────────────────────────────────────
def _lat_lon_to_en(lat: float, lon: float, lat0: float, lon0: float) -> tuple[float, float]:
    """Equirectangular projection → (Easting, Northing) metres from origin (lat0, lon0)."""
    R = 6_371_000.0
    north = (lat - lat0) * math.pi / 180.0 * R
    east  = (lon - lon0) * math.pi / 180.0 * R * math.cos(math.radians(lat0))
    return east, north


def _forward_unit(heading_deg: float) -> tuple[float, float]:
    """Unit vector in the heading direction, in (E, N) components (compass CW from N)."""
    θ = math.radians(heading_deg)
    return math.sin(θ), math.cos(θ)


def _right_unit(heading_deg: float) -> tuple[float, float]:
    """Unit vector 90° right of heading, in (E, N) components."""
    θ = math.radians(heading_deg)
    return math.cos(θ), -math.sin(θ)


# ── CSV loading ────────────────────────────────────────────────────────────────
def load_csv(path: Path) -> list[dict]:
    """Return only rows that have a valid GPS fix and heading."""
    rows: list[dict] = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                lat = float(row["latitude_deg"])
                lon = float(row["longitude_deg"])
                hdg = float(row["heading_deg"])
            except (KeyError, ValueError, TypeError):
                continue
            if not (row["latitude_deg"] and row["longitude_deg"] and row["heading_deg"]):
                continue
            rows.append(
                {
                    "lat":          lat,
                    "lon":          lon,
                    "hdg":          hdg,
                    "host_time":    row.get("host_time", ""),
                    # LG580P logs speed_kph; Watson logs velocity_kph — accept either.
                    "velocity_kph": row.get("velocity_kph") or row.get("speed_kph") or "",
                    "label":        row.get("label", ""),
                }
            )
    return rows


# ── Per-frame geometry ─────────────────────────────────────────────────────────
def compute_frames(rows: list[dict]) -> dict:
    """Pre-compute all display geometry in local metres.

    Position (lat/lon) is the MAIN (left) antenna. The reported heading is
    corrected to true forward (HEADING_OFFSET_DEG) and everything else is placed
    in the mower body frame relative to the main antenna.
    """
    lat0 = sum(r["lat"] for r in rows) / len(rows)
    lon0 = sum(r["lon"] for r in rows) / len(rows)

    centres:    list[tuple[float, float]] = []   # mower centreline, at the antenna line
    ant_main:   list[tuple[float, float]] = []   # left / main antenna (GPS position)
    ant_sec:    list[tuple[float, float]] = []   # right / secondary antenna
    deck_ctrs:  list[tuple[float, float]] = []   # cut-swath centre (on centreline)
    left_tips:  list[tuple[float, float]] = []   # left edge of the cut swath
    right_tips: list[tuple[float, float]] = []   # right edge of the cut swath
    headings:   list[float] = []                 # true forward heading

    for r in rows:
        E, N   = _lat_lon_to_en(r["lat"], r["lon"], lat0, lon0)
        hdg    = (r["hdg"] + HEADING_OFFSET_DEG) % 360.0
        fx, fy = _forward_unit(hdg)
        rx, ry = _right_unit(hdg)

        main = (E, N)
        sec  = (E + ANTENNA_SPACING_M * rx,        N + ANTENNA_SPACING_M * ry)
        cen  = (E + CENTERLINE_RIGHT_OF_GPS_M * rx, N + CENTERLINE_RIGHT_OF_GPS_M * ry)
        deck = (cen[0] + BLADES_FWD_OF_GPS_M * fx, cen[1] + BLADES_FWD_OF_GPS_M * fy)
        lt   = (deck[0] - HALF_CUT_M * rx,         deck[1] - HALF_CUT_M * ry)
        rt   = (deck[0] + HALF_CUT_M * rx,         deck[1] + HALF_CUT_M * ry)

        centres.append(cen)
        ant_main.append(main)
        ant_sec.append(sec)
        deck_ctrs.append(deck)
        left_tips.append(lt)
        right_tips.append(rt)
        headings.append(hdg)

    return {
        "rows":       rows,
        "centres":    np.array(centres),
        "ant_main":   np.array(ant_main),
        "ant_sec":    np.array(ant_sec),
        "deck_ctrs":  np.array(deck_ctrs),
        "left_tips":  np.array(left_tips),
        "right_tips": np.array(right_tips),
        "headings":   np.array(headings),
    }


def _swept_quads(geom: dict, max_seg_m: float = 5.0) -> list[np.ndarray]:
    """
    Build the swept blade area as one quadrilateral per frame-to-frame segment.

    Each quad spans the left/right swath edges of consecutive frames:
        left_tip[i] → left_tip[i+1] → right_tip[i+1] → right_tip[i]

    Drawing them individually (instead of one big polygon) means a serpentine
    path produces no spurious connectors, and overlapping passes simply stack
    their translucent green for a darker band. Segments longer than
    ``max_seg_m`` (e.g. the mower was lifted and repositioned) are skipped so a
    jump doesn't paint a huge false swath.
    """
    lt = geom["left_tips"]
    rt = geom["right_tips"]
    ctr = geom["centres"]
    quads: list[np.ndarray] = []
    seg = np.linalg.norm(np.diff(ctr, axis=0), axis=1)
    for i in range(len(lt) - 1):
        if seg[i] > max_seg_m:
            continue
        quads.append(np.array([lt[i], lt[i + 1], rt[i + 1], rt[i]]))
    return quads


# ── Viewer ─────────────────────────────────────────────────────────────────────
def view(path: Path) -> None:
    rows = load_csv(path)
    if not rows:
        print("No frames with GPS fix + heading found in the file.", file=sys.stderr)
        sys.exit(1)

    n    = len(rows)
    geom = compute_frames(rows)

    # ── Figure layout ────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(11, 9))
    plt.subplots_adjust(bottom=0.12)
    ax.set_aspect("equal")
    ax.set_facecolor("#f5f5f0")
    ax.set_xlabel("Easting (m)")
    ax.set_ylabel("Northing (m)")
    ax.set_title(path.name)
    ax.grid(True, linestyle="--", linewidth=0.4, alpha=0.5)

    # ── Static layers ────────────────────────────────────────────────────────
    # Green swept-area fill: one translucent quad per segment, so overlapping
    # passes stack into darker bands and the serpentine path has no false edges.
    quads = _swept_quads(geom)
    if quads:
        sweep = PolyCollection(
            quads,
            facecolors="#4caf50",
            edgecolors="none",
            alpha=0.25,
            zorder=1,
        )
        ax.add_collection(sweep)

    # Red centre track
    ax.plot(geom["centres"][:, 0], geom["centres"][:, 1],
            color="red", linewidth=1.5, zorder=3)

    # Start / end markers
    ax.plot(*geom["centres"][0],  "go", markersize=7, zorder=4, label="Start")
    ax.plot(*geom["centres"][-1], "rs", markersize=7, zorder=4, label="End")

    # ── Dynamic layer (updated by slider) ────────────────────────────────────
    ant_line,  = ax.plot([], [], color="black",  linewidth=2.5,  zorder=5)
    centre_dot, = ax.plot([], [], "ko", markersize=5, zorder=5)
    live_patches: list = []   # arrow + deck oval + blades, redrawn each frame

    def _update(idx: int) -> None:
        main = geom["ant_main"][idx]
        sec  = geom["ant_sec"][idx]
        cen  = geom["centres"][idx]
        deck = geom["deck_ctrs"][idx]
        hdg  = geom["headings"][idx]

        # Antenna baseline (main=left to secondary=right)
        ant_line.set_data([main[0], sec[0]], [main[1], sec[1]])

        # Mower centre dot
        centre_dot.set_data([cen[0]], [cen[1]])

        # Clear previous frame's movable patches.
        for p in live_patches:
            p.remove()
        live_patches.clear()

        fx, fy = _forward_unit(hdg)
        rx, ry = _right_unit(hdg)

        # Deck oval (3-blade housing): 54" wide (across-track) × ~24" deep,
        # nudged forward to enclose the leading centre blade. matplotlib measures
        # the ellipse angle CCW from +x (east); the width axis follows "right".
        oval_c = (deck[0] + (CENTRE_BLADE_FWD_M / 2) * fx,
                  deck[1] + (CENTRE_BLADE_FWD_M / 2) * fy)
        deck_angle = math.degrees(math.atan2(ry, rx))
        oval = mpatches.Ellipse(
            oval_c,
            width=CUT_WIDTH_M,       # across-track (54")
            height=DECK_DEPTH_M,     # fore-aft
            angle=deck_angle,
            facecolor="none",
            edgecolor="#333333",
            linewidth=1.6,
            zorder=6,
        )
        ax.add_patch(oval)
        live_patches.append(oval)

        # Three blades: outer pair at ±18" across-track, centre blade on the
        # centreline and 6" further forward.
        for off_r, off_f in (
            (-OUTER_BLADE_OFF_M, 0.0),
            (0.0, CENTRE_BLADE_FWD_M),
            (OUTER_BLADE_OFF_M, 0.0),
        ):
            bx = deck[0] + off_r * rx + off_f * fx
            by = deck[1] + off_r * ry + off_f * fy
            blade = mpatches.Circle(
                (bx, by), BLADE_RADIUS_M,
                facecolor="#bbbbbb", edgecolor="#333333",
                linewidth=0.8, alpha=0.55, zorder=6,
            )
            ax.add_patch(blade)
            live_patches.append(blade)

        # Forward heading arrow from the mower centre.
        arrow = mpatches.FancyArrowPatch(
            posA=(cen[0], cen[1]),
            posB=(cen[0] + fx * ARROW_LEN_M, cen[1] + fy * ARROW_LEN_M),
            arrowstyle="-|>",
            color="black",
            mutation_scale=16,
            linewidth=2.0,
            zorder=7,
        )
        ax.add_patch(arrow)
        live_patches.append(arrow)

        # Status line in title
        r = rows[idx]
        vel = f"{float(r['velocity_kph']):.1f} km/h" if r["velocity_kph"] else "-- km/h"
        ax.set_title(
            f"{path.name}   frame {idx + 1}/{n}   hdg {hdg:.1f}° (fwd)   "
            f"{vel}   {r['host_time'][:19]}"
        )
        fig.canvas.draw_idle()

    _update(0)

    # ── Slider ───────────────────────────────────────────────────────────────
    ax_slider = plt.axes([0.15, 0.04, 0.70, 0.025])
    slider = Slider(ax_slider, "Frame", 0, n - 1, valinit=0, valstep=1, color="#1565c0")
    slider.on_changed(lambda v: _update(int(v)))

    # ── Legend ───────────────────────────────────────────────────────────────
    legend_elements = [
        mpatches.Patch(facecolor="#4caf50", alpha=0.5,
                       label=f'Blade sweep (54" / {CUT_WIDTH_M:.2f} m)'),
        plt.Line2D([0], [0], color="red",   linewidth=2, label="Centre track"),
        plt.Line2D([0], [0], color="black", linewidth=2, label="Antenna baseline + heading"),
        mpatches.Patch(facecolor="#bbbbbb", edgecolor="#333333",
                       label="Deck (3 blades, 54\" cut)"),
        plt.Line2D([0], [0], color="green", marker="o", linestyle="",
                   markersize=7, label="Start"),
        plt.Line2D([0], [0], color="red",   marker="s", linestyle="",
                   markersize=7, label="End"),
    ]
    ax.legend(handles=legend_elements, loc="upper right", fontsize=8)

    ax.margins(0.08)
    plt.show()


# ── Entry point ────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="View a mowing run CSV (LG580P or Watson).")
    parser.add_argument("csv", nargs="?", help="Path to CSV file (opens file dialog if omitted)")
    args = parser.parse_args()

    if args.csv:
        path = Path(args.csv)
    else:
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            chosen = filedialog.askopenfilename(
                title="Open mowing run CSV",
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            )
            root.destroy()
        except Exception:
            parser.error("Provide a CSV path as an argument (tkinter file dialog not available).")

        if not chosen:
            print("No file selected.", file=sys.stderr)
            sys.exit(0)
        path = Path(chosen)

    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(1)

    view(path)


if __name__ == "__main__":
    main()
