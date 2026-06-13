#!/usr/bin/env python3
"""
Mower run viewer.

Loads a watson_dms CSV file and displays:
  - Green shaded area  : total swept blade path (54-inch deck)
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
from typing import Optional

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.collections import PolyCollection
from matplotlib.widgets import Slider

# ── Mower geometry ─────────────────────────────────────────────────────────────
DECK_WIDTH_M      = 54 * 0.0254   # 54 inches → 1.3716 m
HALF_DECK_M       = DECK_WIDTH_M / 2
ANTENNA_SPACING_M = 1.0           # 1 m between antennas

# The DMS reports position at the AFT (right) antenna.
# Antennas are 90° to direction of travel; fore antenna is on the LEFT.
# → aft = right, fore = left.  Centre is 0.5 m leftward of the aft GPS position.
AFT_TO_CENTRE_M   = ANTENNA_SPACING_M / 2   # 0.5 m

# Deck geometry: 3 blades in an oval, 54" wide × 14" deep fore-aft.
# (14" = one blade radius (54/3/2 = 9") + 5".)  The BACK edge of the deck sits
# ~12" forward of the antenna baseline, so the oval's fore-aft centre — where
# the blades cut full 54" width — is 12 + 14/2 = 19" ahead of the antennas.
DECK_DEPTH_M       = 14 * 0.0254            # fore-aft depth of the oval (0.356 m)
DECK_BACK_FWD_M    = 12 * 0.0254            # back of deck ahead of antennas (0.305 m)
DECK_CENTRE_FWD_M  = DECK_BACK_FWD_M + DECK_DEPTH_M / 2   # 19" → 0.483 m
N_BLADES           = 3
BLADE_RADIUS_M     = (DECK_WIDTH_M / N_BLADES) / 2        # 9" → 0.229 m

# Arrow drawn from centre toward heading tip.  Scaled to ~60 % of antenna spacing.
ARROW_LEN_M = ANTENNA_SPACING_M * 0.6


# ── Geometry helpers ───────────────────────────────────────────────────────────
def _lat_lon_to_en(lat: float, lon: float, lat0: float, lon0: float) -> tuple[float, float]:
    """Equirectangular projection → (Easting, Northing) metres from origin (lat0, lon0)."""
    R = 6_371_000.0
    north = (lat - lat0) * math.pi / 180.0 * R
    east  = (lon - lon0) * math.pi / 180.0 * R * math.cos(math.radians(lat0))
    return east, north


def _left_unit(heading_deg: float) -> tuple[float, float]:
    """Unit vector 90° left of heading, in (E, N) components."""
    θ = math.radians(heading_deg)
    return -math.cos(θ), math.sin(θ)


def _forward_unit(heading_deg: float) -> tuple[float, float]:
    """Unit vector in the heading direction, in (E, N) components."""
    θ = math.radians(heading_deg)
    return math.sin(θ), math.cos(θ)


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
                    "velocity_kph": row.get("velocity_kph", ""),
                    "label":        row.get("label", ""),
                }
            )
    return rows


# ── Per-frame geometry ─────────────────────────────────────────────────────────
def compute_frames(rows: list[dict]) -> dict:
    """Pre-compute all display geometry in local metres."""
    lat0 = sum(r["lat"] for r in rows) / len(rows)
    lon0 = sum(r["lon"] for r in rows) / len(rows)

    centres:     list[tuple[float, float]] = []   # midpoint of the antenna baseline
    fore_ants:   list[tuple[float, float]] = []   # left / fore antenna
    aft_ants:    list[tuple[float, float]] = []   # right / aft antenna (GPS position)
    deck_ctrs:   list[tuple[float, float]] = []   # oval centre (19" forward of baseline)
    left_tips:   list[tuple[float, float]] = []   # left blade tip (at deck centre)
    right_tips:  list[tuple[float, float]] = []   # right blade tip (at deck centre)
    headings:    list[float] = []

    for r in rows:
        E, N    = _lat_lon_to_en(r["lat"], r["lon"], lat0, lon0)
        lx, ly  = _left_unit(r["hdg"])
        fx, fy  = _forward_unit(r["hdg"])

        aft_x,  aft_y  = E, N
        cx,     cy     = E  + AFT_TO_CENTRE_M   * lx, N  + AFT_TO_CENTRE_M   * ly
        fore_x, fore_y = E  + ANTENNA_SPACING_M * lx, N  + ANTENNA_SPACING_M * ly
        # Deck cuts forward of the antennas; blades reach full width at the
        # oval's fore-aft centre, DECK_CENTRE_FWD_M ahead of the baseline.
        dcx,    dcy    = cx + DECK_CENTRE_FWD_M * fx, cy + DECK_CENTRE_FWD_M * fy
        lt_x,   lt_y   = dcx + HALF_DECK_M      * lx, dcy + HALF_DECK_M      * ly
        rt_x,   rt_y   = dcx - HALF_DECK_M      * lx, dcy - HALF_DECK_M      * ly

        centres.append((cx, cy))
        fore_ants.append((fore_x, fore_y))
        aft_ants.append((aft_x, aft_y))
        deck_ctrs.append((dcx, dcy))
        left_tips.append((lt_x, lt_y))
        right_tips.append((rt_x, rt_y))
        headings.append(r["hdg"])

    return {
        "rows":       rows,
        "centres":    np.array(centres),
        "fore_ants":  np.array(fore_ants),
        "aft_ants":   np.array(aft_ants),
        "deck_ctrs":  np.array(deck_ctrs),
        "left_tips":  np.array(left_tips),
        "right_tips": np.array(right_tips),
        "headings":   np.array(headings),
    }


def _swept_quads(geom: dict, max_seg_m: float = 5.0) -> list[np.ndarray]:
    """
    Build the swept blade area as one quadrilateral per frame-to-frame segment.

    Each quad spans the left/right blade tips of consecutive frames:
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
        fore = geom["fore_ants"][idx]
        aft  = geom["aft_ants"][idx]
        ctr  = geom["centres"][idx]
        deck = geom["deck_ctrs"][idx]
        hdg  = geom["headings"][idx]

        # Antenna baseline (fore=left to aft=right)
        ant_line.set_data([fore[0], aft[0]], [fore[1], aft[1]])

        # Centre dot
        centre_dot.set_data([ctr[0]], [ctr[1]])

        # Clear previous frame's movable patches.
        for p in live_patches:
            p.remove()
        live_patches.clear()

        fx, fy = _forward_unit(hdg)
        lx, ly = _left_unit(hdg)

        # Deck oval (3-blade housing): 54" wide × 14" deep, centred 19" forward.
        # The ellipse "width" axis must lie along the across-track (left-right)
        # direction; matplotlib measures angle CCW from +x (east).
        deck_angle = math.degrees(math.atan2(ly, lx))
        oval = mpatches.Ellipse(
            (deck[0], deck[1]),
            width=DECK_WIDTH_M,      # across-track (54")
            height=DECK_DEPTH_M,     # fore-aft (14")
            angle=deck_angle,
            facecolor="none",
            edgecolor="#333333",
            linewidth=1.6,
            zorder=6,
        )
        ax.add_patch(oval)
        live_patches.append(oval)

        # Three blade circles spread across the deck width.
        for k in range(N_BLADES):
            # Offset of each blade centre from the deck centre, across-track.
            off = (k - (N_BLADES - 1) / 2) * (DECK_WIDTH_M / N_BLADES)
            bx, by = deck[0] + off * lx, deck[1] + off * ly
            blade = mpatches.Circle(
                (bx, by), BLADE_RADIUS_M,
                facecolor="#bbbbbb", edgecolor="#333333",
                linewidth=0.8, alpha=0.55, zorder=6,
            )
            ax.add_patch(blade)
            live_patches.append(blade)

        # Forward heading arrow from antenna centre.
        arrow = mpatches.FancyArrowPatch(
            posA=(ctr[0], ctr[1]),
            posB=(ctr[0] + fx * ARROW_LEN_M, ctr[1] + fy * ARROW_LEN_M),
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
            f"{path.name}   frame {idx + 1}/{n}   hdg {hdg:.1f}°   {vel}   {r['host_time'][:19]}"
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
                       label=f'Blade sweep ({54}"  /{DECK_WIDTH_M:.2f} m)'),
        plt.Line2D([0], [0], color="red",   linewidth=2, label="Centre track"),
        plt.Line2D([0], [0], color="black", linewidth=2, label="Antenna baseline + heading"),
        mpatches.Patch(facecolor="#bbbbbb", edgecolor="#333333",
                       label='Deck (3-blade oval, 54×14")'),
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
    parser = argparse.ArgumentParser(description="View a watson_dms mowing run CSV.")
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
                title="Open watson_dms run CSV",
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
