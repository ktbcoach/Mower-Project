#!/usr/bin/env python3
"""Correct bursty host_time stamps in old watson_dms CSV logs.

Background
----------
Logs captured before the "prompt-read" fix were timestamped in bursts. The old
serial reader called ``ser.read(256)``, which blocked until 256 bytes arrived
*or* the 0.25 s port timeout fired. At 9600 baud the timeout almost always won,
so ~4 frames' worth of buffered bytes were returned at once and stamped a few
milliseconds apart, followed by a ~250 ms gap before the next read. The result:
clusters of 3-4 identical-ish timestamps, then a quarter-second jump.

The frames themselves arrived from the DMS at a steady cadence (~62 ms at 9600
baud). This script reconstructs that cadence by:

  1. Grouping rows into bursts (consecutive rows whose host_time deltas are
     below --eps are one burst — they came from a single read()).
  2. Taking each burst's *latest* stamp as its anchor: the last frame in a read
     completed at roughly the moment the read returned, so its timestamp is the
     most trustworthy one in the burst.
  3. Estimating the true inter-frame period from the overall span and frame
     count (or an explicit --period).
  4. Re-spacing each burst's frames *backward* from the anchor at that period,
     so every frame lands at its real arrival time instead of bunched at the
     read boundary.

Genuine pauses in the stream (e.g. logging toggled off/on, or the unit dropped
out) are preserved: if the gap before a burst is much larger than the frames in
it can account for, it is left as a real gap rather than smeared over.

The original stamps are kept in a new ``host_time_raw`` column for traceability;
``host_time`` is overwritten with the corrected value. All other columns pass
through untouched.

Usage
-----
    python retime_csv.py run.csv                 # writes run_retimed.csv
    python retime_csv.py run.csv -o fixed.csv
    python retime_csv.py run.csv --period 0.0625 # force 16 fps cadence
    python retime_csv.py run.csv --eps 0.03      # looser burst grouping
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import statistics
import sys
from pathlib import Path

RAW_COL = "host_time_raw"


def _parse_ts(s: str) -> _dt.datetime | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return _dt.datetime.fromisoformat(s)
    except ValueError:
        return None


def _detect_bursts(times: list[float], eps: float) -> list[list[int]]:
    """Group row indices into bursts.

    A new burst starts whenever the gap from the previous row exceeds ``eps``.
    Rows with no parseable timestamp are dropped from grouping by the caller.
    """
    bursts: list[list[int]] = []
    cur: list[int] = []
    prev: float | None = None
    for i, t in enumerate(times):
        if prev is not None and (t - prev) > eps:
            bursts.append(cur)
            cur = []
        cur.append(i)
        prev = t
    if cur:
        bursts.append(cur)
    return bursts


def retime(
    rows: list[dict],
    eps: float = 0.02,
    period: float | None = None,
    gap_factor: float = 2.0,
) -> dict:
    """Return corrected timestamps plus a stats summary.

    ``eps``        max delta (s) for two rows to be in the same burst.
    ``period``     forced inter-frame period (s); estimated if None.
    ``gap_factor`` a pre-burst gap larger than gap_factor * (n * period) is
                   treated as a genuine pause and preserved, not back-filled.
    """
    parsed = [_parse_ts(r.get("host_time", "")) for r in rows]
    have = [i for i, t in enumerate(parsed) if t is not None]
    if len(have) < 2:
        raise SystemExit("Need at least two rows with valid host_time to retime.")

    t0 = parsed[have[0]]
    # Seconds-since-start for every row that has a timestamp, in file order.
    rel = {i: (parsed[i] - t0).total_seconds() for i in have}
    ordered = sorted(have)  # file order is already chronological for these
    times = [rel[i] for i in ordered]

    bursts = _detect_bursts(times, eps)

    # Estimate the true frame period. The total span under-counts it: each
    # burst's frames are squeezed into a few ms, so span/(n-1) is too small.
    # Instead measure the spacing between burst *anchors* (read boundaries) and
    # divide by the frames that arrived between them — this recovers the real
    # inter-frame cadence (e.g. 250 ms read interval / 4 frames = 62.5 ms).
    span = times[-1] - times[0]
    n_frames = len(times)
    anchors = [max(times[j] for j in b) for b in bursts]
    frames_after_first = n_frames - len(bursts[0])
    if len(anchors) > 1 and frames_after_first > 0:
        est_period = (anchors[-1] - anchors[0]) / frames_after_first
    else:
        est_period = span / (n_frames - 1) if n_frames > 1 else 0.0
    use_period = period if period is not None else est_period

    # Anchor = latest stamp in each burst (the read-return instant).
    new_rel: dict[int, float] = {}
    real_gaps = 0
    prev_anchor: float | None = None
    for idxs in bursts:
        # idxs holds positions into `ordered`/`times` (file order).
        anchor = max(times[j] for j in idxs)
        n = len(idxs)

        # Detect a genuine pause before this burst.
        if prev_anchor is not None:
            gap = anchor - (n - 1) * use_period - prev_anchor
            if gap > gap_factor * max(use_period, 1e-9) * 1:
                real_gaps += 1  # left as-is; we simply don't pull the burst back past prev

        # Space frames backward from the anchor at the estimated period,
        # preserving their original within-burst order.
        for k, j in enumerate(idxs):
            offset = (n - 1 - k) * use_period
            new_rel[ordered[j]] = anchor - offset
        prev_anchor = anchor

    # Enforce monotonic non-decreasing time (numerical safety).
    last = None
    for i in ordered:
        if last is not None and new_rel[i] < last:
            new_rel[i] = last
        last = new_rel[i]

    corrected: list[str | None] = [None] * len(rows)
    for i in ordered:
        corrected[i] = (t0 + _dt.timedelta(seconds=new_rel[i])).isoformat()

    burst_sizes = [len(b) for b in bursts]
    return {
        "corrected": corrected,
        "stats": {
            "rows": len(rows),
            "timed_rows": n_frames,
            "bursts": len(bursts),
            "mean_burst": statistics.mean(burst_sizes) if burst_sizes else 0,
            "max_burst": max(burst_sizes) if burst_sizes else 0,
            "span_s": span,
            "est_period_s": est_period,
            "used_period_s": use_period,
            "est_rate_hz": (1.0 / use_period) if use_period else 0.0,
            "real_gaps": real_gaps,
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", help="Input CSV (a watson_dms log)")
    ap.add_argument("-o", "--output", help="Output path (default: <name>_retimed.csv)")
    ap.add_argument("--eps", type=float, default=0.02,
                    help="Max gap (s) for rows to count as one burst (default 0.02)")
    ap.add_argument("--period", type=float, default=None,
                    help="Force inter-frame period in seconds (else auto-estimated)")
    ap.add_argument("--gap-factor", type=float, default=2.0,
                    help="Pre-burst gap > gap_factor*frames is a real pause (default 2)")
    args = ap.parse_args()

    src = Path(args.csv)
    if not src.exists():
        raise SystemExit(f"No such file: {src}")
    dst = Path(args.output) if args.output else src.with_name(src.stem + "_retimed.csv")

    with src.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise SystemExit("Input CSV has no header.")
        in_fields = list(reader.fieldnames)
        rows = list(reader)

    result = retime(rows, eps=args.eps, period=args.period, gap_factor=args.gap_factor)
    corrected = result["corrected"]
    s = result["stats"]

    # Output columns: keep originals, add host_time_raw right after host_time.
    out_fields = list(in_fields)
    if RAW_COL not in out_fields:
        pos = out_fields.index("host_time") + 1 if "host_time" in out_fields else len(out_fields)
        out_fields.insert(pos, RAW_COL)

    with dst.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=out_fields)
        writer.writeheader()
        for i, row in enumerate(rows):
            out = dict(row)
            out[RAW_COL] = row.get("host_time", "")
            if corrected[i] is not None:
                out["host_time"] = corrected[i]
            writer.writerow(out)

    print(f"Read  {s['rows']} rows ({s['timed_rows']} timestamped) from {src.name}")
    print(f"Bursts: {s['bursts']}  (mean {s['mean_burst']:.1f}, max {s['max_burst']} frames/burst)")
    print(f"Span: {s['span_s']:.3f} s  ->  period {s['used_period_s']*1000:.1f} ms "
          f"({s['est_rate_hz']:.1f} Hz)"
          + ("  [forced]" if args.period is not None else "  [estimated]"))
    if s["real_gaps"]:
        print(f"Preserved {s['real_gaps']} genuine pause(s) larger than the burst could fill.")
    print(f"Wrote {dst}")


if __name__ == "__main__":
    main()
