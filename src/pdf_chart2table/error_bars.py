"""Detect error-bar decoration (whiskers + caps) and separate it from the data.

A matplotlib-style error bar is drawn, per data point, as a short *vertical*
stroke (the whisker) running through the marker, plus two short *horizontal*
strokes (the caps) at the whisker ends. These primitives are decoration, not a
data series: the marker is the datum, the whisker is its uncertainty. Left in,
the extractor traces the whisker/cap strokes as a jagged marker-less polyline --
a fake navy "series" of ~one vertex per whisker end that zig-zags through the
real square markers (observed on 2510.04789_p3c4).

This mirrors ``arrows.py``: identify the clear decoration pattern, return the
path indices so the caller can drop them from ``region.path_indices`` before
series extraction. Conservative by design -- only short strokes that are
(a) near-vertical/horizontal, (b) co-located in x with a real marker centroid,
and (c) drawn in the marker's colour are flagged, so genuine vertical/horizontal
data is untouched.

Public API:
    detect_error_bars(region, paths) -> set[int]  (path indices to drop)
"""
from __future__ import annotations

from .model import Path, Region
from .primitives import centroid as _centroid, round_color as _round_color

# A whisker/cap stroke is "thin" in its short dimension (a true 1D segment).
_THIN_PX = 1.2
# x of a whisker (or x-centre of a cap) must sit within this many px of a marker
# centroid's x to count as anchored to that datum.
_X_TOL = 2.0
# A marker glyph is small relative to the plot diagonal and roughly square.
_MARK_MAX_FRAC = 0.15
_MARK_MIN_SIDE = 2.0
_MARK_MIN_ASPECT = 0.5
# Need at least this many anchored whiskers before treating the navy strokes as
# error bars (one isolated vertical stroke could be genuine data).
_MIN_WHISKERS = 2


def _bmax(b):
    return max(b[2] - b[0], b[3] - b[1])


def _marker_centroids(region: Region, paths: list[Path], diag: float):
    """Centroids and colours of the small square/round filled glyphs (markers)."""
    out = []
    for i in region.path_indices:
        p = paths[i]
        if p.fill is None:
            continue
        b = p.bbox
        bw, bh = b[2] - b[0], b[3] - b[1]
        if max(bw, bh) >= _MARK_MAX_FRAC * diag:
            continue
        if min(bw, bh) < _MARK_MIN_SIDE:
            continue
        long, short = max(bw, bh), min(bw, bh)
        if long and short / long < _MARK_MIN_ASPECT:
            continue
        cx, cy = _centroid(p.points)
        out.append((cx, cy, _round_color(p.fill)))
    return out


def detect_error_bars(region: Region, paths: list[Path]) -> set[int]:
    """Return the set of path indices that are error-bar whiskers/caps.

    Whiskers are short near-vertical strokes whose x coincides with a marker
    centroid; caps are short near-horizontal strokes centred on a marker x at a
    whisker end. Only fires when markers exist and at least ``_MIN_WHISKERS``
    anchored verticals are found, so genuine vertical/horizontal data series are
    never removed.
    """
    idxs = list(getattr(region, "path_indices", []))
    if not idxs:
        return set()
    diag = _bmax(region.bbox) or 1.0
    markers = _marker_centroids(region, paths, diag)
    if not markers:
        return set()
    mark_xs = [(mx, col) for mx, _my, col in markers]

    def _anchored(cx: float, color) -> bool:
        # x near a marker centroid AND drawn in that marker's colour.
        return any(abs(cx - mx) <= _X_TOL and (color is None or color == mc)
                   for mx, mc in mark_xs)

    whiskers: set[int] = set()
    caps: set[int] = set()
    max_len = 0.6 * diag  # an error bar never spans most of the plot
    for i in idxs:
        p = paths[i]
        if p.fill is not None and p.stroke is None:
            # a filled cap glyph (matplotlib also emits these); allow it below
            pass
        b = p.bbox
        bw, bh = b[2] - b[0], b[3] - b[1]
        color = _round_color(p.stroke or p.fill)
        if len(p.points) > 3:
            continue
        if bw <= _THIN_PX and _THIN_PX < bh <= max_len:
            # near-vertical stroke: a whisker
            cx = (b[0] + b[2]) / 2.0
            if _anchored(cx, color):
                whiskers.add(i)
        elif bh <= _THIN_PX and _THIN_PX < bw <= max_len:
            # near-horizontal stroke: a cap (its x-centre is on the marker)
            cx = (b[0] + b[2]) / 2.0
            if _anchored(cx, color):
                caps.add(i)

    if len(whiskers) < _MIN_WHISKERS:
        return set()
    return whiskers | caps


def recover_error_bars(
    region: Region, paths: list[Path]
) -> tuple[set[int], list[tuple[float, float, float]]]:
    """Like :func:`detect_error_bars`, but also return each WHISKER's geometry so
    the recovered error bars can be re-drawn on the reconstruction.

    Returns ``(idx_to_remove, whiskers)`` where ``idx_to_remove`` is the whisker+
    cap path-index set (decoration to strip before extraction) and ``whiskers``
    is ``[(cx, y_top_px, y_bottom_px), ...]`` -- the x-centre and vertical pixel
    extent of each whisker, used to attach a per-point ``y_err`` to the series.
    Empty (``set(), []``) when no error bars are found.
    """
    idx = detect_error_bars(region, paths)
    if not idx:
        return set(), []
    diag = _bmax(region.bbox) or 1.0
    max_len = 0.6 * diag
    whiskers: list[tuple[float, float, float]] = []
    for i in idx:
        p = paths[i]
        b = p.bbox
        bw, bh = b[2] - b[0], b[3] - b[1]
        if len(p.points) <= 3 and bw <= _THIN_PX and _THIN_PX < bh <= max_len:
            whiskers.append(((b[0] + b[2]) / 2.0, b[1], b[3]))  # (cx, top, bottom)
    return idx, whiskers
