"""Detect a background grid in a chart region.

Grid lines are axis-aligned, thin strokes that span most of the plot interior and
line up with the ticks. They are decoration, not data, so we record their
presence/style separately and the caller can redraw them behind the series
instead of (a) tracing them as fake curves or (b) losing them entirely.

Grid lines and tick marks are the SAME primitive (a thin axis-aligned segment at
an axis coordinate) -- see ``axes.axis_segments`` -- so this detector consumes
that shared, COLOR-AGNOSTIC classifier rather than its own colour gate. That is
why dark / dashed grids (previously missed by a grey-only test) are now caught.

Ticks are used BOTH ways:
  * cross-validation (reject false positives): a real grid sits ON tick
    positions, so a stray pair of reference lines that miss the ticks is rejected.
  * false-negative recovery: a faint / dash-fragmented grid line that doesn't on
    its own clear the full-span bar is still recovered when it sits on a tick
    (collinear fragments at one coordinate are unioned first).

Public API:
    detect_grid(region, paths, x_ticks=None, y_ticks=None) -> dict | None
        {"x": bool (vertical lines), "y": bool (horizontal lines),
         "color": [r,g,b]|None, "linewidth": float|None, "dashes": str|None}
"""
from __future__ import annotations

from collections import Counter

from .axes import axis_segments
from .model import Path, Region

_COORD_TOL = 2.0          # merge collinear segments whose coordinate is within this
_TICK_ALIGN_TOL = 3.0     # a grid line is tick-aligned within this many points
_SPAN_STRONG = 0.6        # standalone gridline: covers >= 60% of the plot span
_SPAN_TICK = 0.4          # tick-confirmed gridline: lower bar, but must sit on a tick


def _is_grey(c) -> bool:
    if c is None:
        return False
    mn, mx = min(c), max(c)
    return (mx - mn) <= 0.15 and 0.4 <= mx <= 0.96


def _union_fraction(intervals: list[tuple[float, float]], dim: float) -> float:
    """Fraction of ``dim`` covered by the union of ``intervals`` (merges dash gaps)."""
    if dim <= 0 or not intervals:
        return 0.0
    ivs = sorted((min(a, b), max(a, b)) for a, b in intervals)
    covered = 0.0
    cs, ce = ivs[0]
    for lo, hi in ivs[1:]:
        if lo <= ce:
            ce = max(ce, hi)
        else:
            covered += ce - cs
            cs, ce = lo, hi
    covered += ce - cs
    return covered / dim


def _cluster_by_coord(segs: list[dict]) -> list[tuple[float, list[dict]]]:
    """Group segments whose axis coordinate is within _COORD_TOL."""
    out: list[tuple[float, list[dict]]] = []
    for s in sorted(segs, key=lambda s: s["coord"]):
        if out and abs(s["coord"] - out[-1][0]) <= _COORD_TOL:
            out[-1][1].append(s)
        else:
            out.append((s["coord"], [s]))
    return out


def _aligned(coord: float, ticks) -> bool:
    return bool(ticks) and any(abs(coord - t) <= _TICK_ALIGN_TOL for t in ticks)


def _grid_lines(segs, orient, dim, lo_b, hi_b, ticks):
    """Gridline coordinates for one orientation, each with its member segments.

    Clusters collinear (dash-fragmented) segments, keeps interior clusters that
    either clear the strong span bar OR clear the lower bar AND sit on a tick."""
    items = [s for s in segs if s["orient"] == orient and s["role"] in ("gridline", "other")]
    lines = []
    for coord, members in _cluster_by_coord(items):
        if not (lo_b + 2 < coord < hi_b - 2):     # interior only (skip spines/border)
            continue
        cov = _union_fraction([(m["lo"], m["hi"]) for m in members], dim)
        aligned = _aligned(coord, ticks)
        grey = any(_is_grey(m["stroke"]) for m in members)
        # Ticks are the discriminator for ANY-colour grids; absent a tick, only a
        # GREY full-span line is unambiguously a grid (a dark full-span line with
        # no tick is more likely data / a reference line). A tick also recovers a
        # faint / dash-fragmented line that doesn't clear the strong span bar.
        if (cov >= _SPAN_STRONG and (grey or aligned)) or (cov >= _SPAN_TICK and aligned):
            lines.append((coord, members))
    return lines


def _axis_has_grid(lines, ticks) -> bool:
    if len(lines) < 2:
        return False
    if ticks:  # cross-validation: >=2 of the lines must sit on ticks
        return sum(1 for c, _ in lines if _aligned(c, ticks)) >= 2
    return True


def detect_grid(region: Region, paths: list[Path],
                x_ticks: list[float] | None = None,
                y_ticks: list[float] | None = None):
    x0, y0, x1, y1 = region.bbox
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return None
    segs = axis_segments(paths, region)
    # vertical grid lines sit at x positions (x-axis grid); horizontal at y.
    vlines = _grid_lines(segs, "v", h, x0, x1, x_ticks)
    hlines = _grid_lines(segs, "h", w, y0, y1, y_ticks)

    grid: dict = {}
    if _axis_has_grid(vlines, x_ticks):
        grid["x"] = True
    if _axis_has_grid(hlines, y_ticks):
        grid["y"] = True
    if not grid:
        return None

    members = [m for _, ms in (vlines if grid.get("x") else []) for m in ms]
    members += [m for _, ms in (hlines if grid.get("y") else []) for m in ms]

    def _key(s):
        st = tuple(round(v, 3) for v in s["stroke"]) if s["stroke"] else None
        return (st, round(s["width"] or 0, 3), s["dashes"])

    counts = Counter(_key(s) for s in members)
    best = max(counts, key=lambda k: (counts[k], _is_grey(k[0]) if k[0] else False))
    ref = next(s for s in members if _key(s) == best)
    grid["color"] = list(ref["stroke"]) if ref["stroke"] else None
    grid["linewidth"] = ref["width"]
    grid["dashes"] = ref["dashes"]
    return grid
