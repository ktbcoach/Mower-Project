#!/usr/bin/env python3
"""
Mower run viewer.

Loads a raw LG580P/Watson CSV *or* a fused EKF CSV (`lg580p fuse`) — the format
is auto-detected — and displays:
  - Green shaded area  : total swept blade path (54-inch cut)
  - Centre track       : coloured by solution quality (green=RTK-fixed,
                         amber=float, red=coast/IMU dead-reckon)
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
from matplotlib.collections import LineCollection, PolyCollection
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

# Chassis footprint (top-down outline; mower body frame, relative to the blades).
REAR_WHEEL_TRACK_M        = 33.5 * IN   # rear drive wheels, centre-to-centre
REAR_WHEEL_WIDTH_M        = 8 * IN      # rear tyre width (across-track)
REAR_WHEEL_DIA_M          = 18 * IN     # rear tyre diameter (fore-aft)
REAR_AXLE_BEHIND_BLADES_M = 21 * IN     # rear wheels 21" behind the outer blades
FRONT_TRACK_M             = 32 * IN     # front caster wheels, centre-to-centre
FRONT_AHEAD_OF_REAR_M     = 48 * IN     # caster axle 48" ahead of the rear axle
FRONT_WHEEL_WIDTH_M       = 5 * IN      # front caster tyre width (across-track)
FRONT_WHEEL_DIA_M         = 12 * IN     # front caster tyre diameter (fore-aft)
CHUTE_OUT_M               = 36 * IN     # discharge chute tip, right of centreline
CHUTE_DEPTH_M             = 12 * IN     # chute fore-aft size (visual)


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


# Heading correction differs by log format. The raw collect/Watson CSV stores the
# PQTMTAR *baseline* heading, so the viewer rotates it to vehicle-forward with
# HEADING_OFFSET_DEG. The fused EKF CSV stores yaw with that offset already
# applied (fuse's --heading-offset defaults to -90, matching this file), so its
# heading is already forward — apply 0. Override either with --heading-offset.
DEFAULT_HEADING_OFFSET = {"raw": HEADING_OFFSET_DEG, "fused": 0.0}

# Centre-track colouring by solution quality. ``source`` is the fused
# solution_source (rtk_fixed/float/coast/coast_stale) or, for a raw log, the GGA
# fix_quality_name. Each track segment takes the colour of its *worse* endpoint
# (by _SEVERITY), so a single float/coast sample stands out. Unknown/blank → grey.
QUALITY_COLORS = {
    "rtk_fixed":   "#1b7f3b",   # green  — RTK fixed
    "rtk_float":   "#e8a33d",   # amber  — RTK float
    "coast":       "#e02020",   # red    — IMU dead-reckon (GNSS gap)
    "coast_stale": "#a01515",   # dark red — coasted past coast-max
    "dgps":        "#f1c40f",   # yellow
    "gps":         "#f1c40f",
    "no_fix":      "#888888",
}
_NEUTRAL = "#555555"
_SEVERITY = {"rtk_fixed": 0, "dgps": 1, "gps": 1, "no_fix": 1,
             "rtk_float": 2, "coast": 3, "coast_stale": 4}
# Legend order (worst last) — only qualities present in the file are shown.
_QUALITY_ORDER = ["rtk_fixed", "dgps", "gps", "rtk_float", "coast", "coast_stale", "no_fix"]


def _severity(source: str) -> int:
    return _SEVERITY.get(source, 0)


def _segment_color(src_a: str, src_b: str) -> str:
    """Colour for the track segment between two samples: the worse endpoint's."""
    worse = src_a if _severity(src_a) >= _severity(src_b) else src_b
    return QUALITY_COLORS.get(worse, _NEUTRAL)


# ── CSV loading ────────────────────────────────────────────────────────────────
def load_csv(path: Path) -> tuple[list[dict], str]:
    """Load rows with a valid position + heading, auto-detecting the log format.

    Two formats are supported (returned as the second tuple element):
      * ``"raw"``   — collect/Watson CSV: ``latitude_deg`` / ``heading_deg``,
                      speed in km/h (``speed_kph`` or ``velocity_kph``).
      * ``"fused"`` — EKF CSV (``lg580p fuse``): ``fused_lat`` /
                      ``fused_heading_deg``, speed in m/s (``speed_mps``),
                      plus ``solution_source`` and ``pos_sigma_m`` context.
    """
    rows: list[dict] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        cols = set(reader.fieldnames or [])
        fused = "fused_lat" in cols
        lat_k = "fused_lat" if fused else "latitude_deg"
        lon_k = "fused_lon" if fused else "longitude_deg"
        hdg_k = "fused_heading_deg" if fused else "heading_deg"
        for row in reader:
            try:
                lat = float(row[lat_k])
                lon = float(row[lon_k])
                hdg = float(row[hdg_k])
            except (KeyError, ValueError, TypeError):
                continue
            if fused:
                mps = row.get("speed_mps") or ""
                velocity_kph = f"{float(mps) * 3.6:.3f}" if mps else ""
                source = row.get("solution_source", "")   # rtk_fixed | float | coast | …
                sigma = row.get("pos_sigma_m", "")
            else:
                # LG580P logs speed_kph; Watson logs velocity_kph — accept either.
                velocity_kph = row.get("velocity_kph") or row.get("speed_kph") or ""
                source = row.get("fix_quality_name", "")
                sigma = ""
            rows.append(
                {
                    "lat":          lat,
                    "lon":          lon,
                    "hdg":          hdg,
                    "host_time":    row.get("host_time", ""),
                    "velocity_kph": velocity_kph,
                    "source":       source,
                    "sigma":        sigma,
                    "label":        row.get("label", ""),
                }
            )
    return rows, ("fused" if fused else "raw")


# ── Per-frame geometry ─────────────────────────────────────────────────────────
def compute_frames(rows: list[dict], heading_offset_deg: float = HEADING_OFFSET_DEG) -> dict:
    """Pre-compute all display geometry in local metres.

    Position (lat/lon) is the MAIN (left) antenna. The reported heading is
    corrected to true forward (``heading_offset_deg`` — see
    ``DEFAULT_HEADING_OFFSET``) and everything else is placed in the mower body
    frame relative to the main antenna.
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
        hdg    = (r["hdg"] + heading_offset_deg) % 360.0
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
def view(path: Path, heading_offset: float | None = None) -> None:
    rows, fmt = load_csv(path)
    if not rows:
        print("No frames with GPS fix + heading found in the file.", file=sys.stderr)
        sys.exit(1)

    offset = heading_offset if heading_offset is not None else DEFAULT_HEADING_OFFSET[fmt]
    n    = len(rows)
    # ASCII only — some Windows consoles are cp1252 and crash on deg/middot.
    print(f"Loaded {n} frames | {fmt} format | heading offset {offset:+.0f} deg",
          file=sys.stderr)
    geom = compute_frames(rows, offset)

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

    # Centre track, coloured per-segment by solution quality (green=fixed,
    # amber=float, red=coast) so degraded stretches stand out on the map.
    pts = geom["centres"]
    if len(pts) >= 2:
        sources = [r.get("source", "") for r in rows]
        segs = np.stack([pts[:-1], pts[1:]], axis=1)
        seg_colors = [_segment_color(sources[i], sources[i + 1])
                      for i in range(len(pts) - 1)]
        ax.add_collection(LineCollection(segs, colors=seg_colors,
                                         linewidths=1.6, zorder=3))

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

        # Body-frame → world helpers: (right of centreline, forward of antennas).
        def bw(right_of_cl: float, fwd_of_ant: float) -> tuple[float, float]:
            r = CENTERLINE_RIGHT_OF_GPS_M + right_of_cl
            return (main[0] + r * rx + fwd_of_ant * fx,
                    main[1] + r * ry + fwd_of_ant * fy)

        def rect(right_of_cl, fwd_of_ant, across, alongf, **kw):
            hw, hl = across / 2, alongf / 2
            pts = [bw(right_of_cl - hw, fwd_of_ant - hl),
                   bw(right_of_cl + hw, fwd_of_ant - hl),
                   bw(right_of_cl + hw, fwd_of_ant + hl),
                   bw(right_of_cl - hw, fwd_of_ant + hl)]
            p = mpatches.Polygon(pts, closed=True, **kw)
            ax.add_patch(p)
            live_patches.append(p)

        # ── Mower footprint (rear wheels, front casters, chute, chassis) ──────
        rear_fwd  = BLADES_FWD_OF_GPS_M - REAR_AXLE_BEHIND_BLADES_M
        front_fwd = rear_fwd + FRONT_AHEAD_OF_REAR_M
        rtk = REAR_WHEEL_TRACK_M / 2
        ftk = FRONT_TRACK_M / 2

        # Faint chassis outline through the four wheel contact points.
        foot = [bw(-rtk, rear_fwd), bw(-ftk, front_fwd),
                bw(ftk, front_fwd), bw(rtk, rear_fwd)]
        chassis = mpatches.Polygon(foot, closed=True, facecolor="#000000", alpha=0.05,
                                   edgecolor="#888888", linewidth=1.0, linestyle="--",
                                   zorder=2)
        ax.add_patch(chassis)
        live_patches.append(chassis)

        # Rear drive wheels (dark), front casters (grey).
        for s in (-1, 1):
            rect(s * rtk, rear_fwd, REAR_WHEEL_WIDTH_M, REAR_WHEEL_DIA_M,
                 facecolor="#222222", edgecolor="none", alpha=0.85, zorder=5)
            rect(s * ftk, front_fwd, FRONT_WHEEL_WIDTH_M, FRONT_WHEEL_DIA_M,
                 facecolor="#555555", edgecolor="none", alpha=0.85, zorder=5)

        # Discharge chute: from the deck's right edge (27") out to 36" right.
        chute_ctr = (HALF_CUT_M + CHUTE_OUT_M) / 2
        rect(chute_ctr, BLADES_FWD_OF_GPS_M, CHUTE_OUT_M - HALF_CUT_M, CHUTE_DEPTH_M,
             facecolor="#e69138", edgecolor="#7f5410", alpha=0.5, zorder=5)

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
        # Solution context: fix-quality name (raw) or EKF source + 1-sigma (fused).
        extra = ""
        if r.get("source"):
            extra = f"   {r['source']}"
            if r.get("sigma"):
                extra += f" ±{r['sigma']}m"
        ax.set_title(
            f"{path.name}   frame {idx + 1}/{n}   hdg {hdg:.1f}° (fwd)   "
            f"{vel}{extra}   {r['host_time'][:19]}"
        )
        fig.canvas.draw_idle()

    _update(0)

    # ── Slider ───────────────────────────────────────────────────────────────
    ax_slider = plt.axes([0.15, 0.04, 0.70, 0.025])
    slider = Slider(ax_slider, "Frame", 0, n - 1, valinit=0, valstep=1, color="#1565c0")
    slider.on_changed(lambda v: _update(int(v)))

    # ── Legend ───────────────────────────────────────────────────────────────
    # Track-quality swatches: only the qualities actually present in this file.
    present = {r.get("source", "") for r in rows}
    track_entries = [
        plt.Line2D([0], [0], color=QUALITY_COLORS[q], linewidth=2.5,
                   label=f"Track: {q.replace('_', '-')}")
        for q in _QUALITY_ORDER if q in present
    ]
    if not track_entries:  # a file with no quality info — plain neutral track
        track_entries = [plt.Line2D([0], [0], color=_NEUTRAL, linewidth=2.5,
                                    label="Centre track")]
    legend_elements = [
        mpatches.Patch(facecolor="#4caf50", alpha=0.5,
                       label=f'Blade sweep (54" / {CUT_WIDTH_M:.2f} m)'),
        *track_entries,
        plt.Line2D([0], [0], color="black", linewidth=2, label="Antenna baseline + heading"),
        mpatches.Patch(facecolor="#bbbbbb", edgecolor="#333333",
                       label="Deck (3 blades, 54\" cut)"),
        mpatches.Patch(facecolor="#222222", label="Wheels / chassis footprint"),
        mpatches.Patch(facecolor="#e69138", alpha=0.6, label="Discharge chute (right)"),
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
    parser = argparse.ArgumentParser(
        description="View a mowing run CSV (raw LG580P/Watson or fused EKF log).")
    parser.add_argument("csv", nargs="?", help="Path to CSV file (opens file dialog if omitted)")
    parser.add_argument("--heading-offset", type=float, default=None,
                        help="degrees added to the logged heading to get vehicle-forward "
                             "(default: -90 for raw logs, 0 for fused logs)")
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

    view(path, args.heading_offset)


if __name__ == "__main__":
    main()
