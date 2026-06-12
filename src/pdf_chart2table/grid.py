"""Detect a background grid in a chart region.

Grid lines are light-gray, axis-aligned, thin strokes that span most of the plot
interior (they line up with the ticks). They are decoration, not data, so we
record their presence/style separately and the caller can redraw them behind the
series instead of (a) tracing them as fake curves or (b) losing them entirely.

Public API:
    detect_grid(region, paths) -> dict | None
        {"x": bool (vertical lines), "y": bool (horizontal lines),
         "color": [r,g,b], "linewidth": float|None, "dashes": str|None}
"""
from __future__ import annotations

from .model import Path, Region


def _is_grey(c) -> bool:
    if c is None:
        return False
    mn, mx = min(c), max(c)
    # grey (low saturation), lighter than the data/spines but not pure white.
    return (mx - mn) <= 0.15 and 0.4 <= mx <= 0.96


def detect_grid(region: Region, paths: list[Path]):
    x0, y0, x1, y1 = region.bbox
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return None
    hlines, vlines = [], []
    for i in getattr(region, "path_indices", []):
        p = paths[i]
        if p.stroke is None or not _is_grey(p.stroke):
            continue
        b = p.bbox
        bw, bh = b[2] - b[0], b[3] - b[1]
        cx, cy = 0.5 * (b[0] + b[2]), 0.5 * (b[1] + b[3])
        if bw > 0.6 * w and bh < 2.0 and y0 + 2 < cy < y1 - 2:
            hlines.append(p)          # horizontal grid line -> y-axis grid
        elif bh > 0.6 * h and bw < 2.0 and x0 + 2 < cx < x1 - 2:
            vlines.append(p)          # vertical grid line -> x-axis grid
    # require >=2 parallel lines so a single stray grey rule isn't called a grid
    grid: dict = {}
    ref = None
    if len(hlines) >= 2:
        grid["y"] = True
        ref = hlines[0]
    if len(vlines) >= 2:
        grid["x"] = True
        ref = ref or vlines[0]
    if not grid:
        return None
    grid["color"] = list(ref.stroke)
    grid["linewidth"] = ref.width
    grid["dashes"] = ref.dashes
    return grid
