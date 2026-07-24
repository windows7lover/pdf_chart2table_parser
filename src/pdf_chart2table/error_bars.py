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

A separate pass handles MARKER-LESS error bars (matplotlib ``fmt='none'``),
where the I-beam itself is the datum: see ``recover_markerless_error_bars``.

Public API:
    detect_error_bars(region, paths) -> set[int]  (path indices to drop)
    recover_error_bars(region, paths) -> (idx, whiskers)  (marker-anchored)
    recover_markerless_error_bars(region, paths, exclude) -> (idx, points)
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
# Markers can be tiny (matplotlib renders a small scatter glyph as a ~1px
# filled+stroked circle/triangle, e.g. 2010.14886_p15c1 has ~0.9x1.5px glyphs);
# require only that the glyph is not a sub-pixel speck so true marker series are
# still detected. The centring + colour + count guards keep this precise.
_MARK_MIN_SIDE = 0.8
_MARK_MIN_ASPECT = 0.5
# A genuine error-bar whisker is CENTRED on its datum: the marker lies near the
# whisker's midpoint, so the bar extends both above and below the point. The
# marker's y may sit at most this fraction of the whisker's half-height away from
# the midpoint. This is the precision guard that separates a real error bar from
# a gridline / connector / steep data curve that merely passes vertically
# through a marker (those are not centred on the point).
_CENTRE_FRAC = 0.5
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

    def _anchored(cx: float, color) -> bool:
        # x near a marker centroid AND drawn in that marker's colour.
        return any(abs(cx - mx) <= _X_TOL and (color is None or color == mc)
                   for mx, _my, mc in markers)

    def _centred_whisker(cx: float, top: float, bot: float, color) -> bool:
        # An error-bar whisker is centred on a marker that shares its x AND
        # colour: the marker's y sits near the whisker's midpoint, so the bar
        # extends both above and below the datum. A gridline / connector / steep
        # curve passing vertically through a marker fails this (the marker is not
        # at the segment's midpoint).
        mid = (top + bot) / 2.0
        half = abs(bot - top) / 2.0 or 1.0
        return any(abs(cx - mx) <= _X_TOL and (color is None or color == mc)
                   and abs(my - mid) <= _CENTRE_FRAC * half
                   for mx, my, mc in markers)

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
            # near-vertical stroke: a whisker (must be CENTRED on its marker)
            cx = (b[0] + b[2]) / 2.0
            if _centred_whisker(cx, b[1], b[3], color):
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


# --------------------------------------------------------------------------
# Marker-less error bars: the I-beam IS the datum (no central marker glyph)
# --------------------------------------------------------------------------
# When a chart draws its points ONLY as error bars (matplotlib ``fmt='none'``),
# there is no marker for ``detect_error_bars`` to anchor to, so the whisker+cap
# strokes are left in and traced by ``lines.py`` as a fake jagged polyline (a
# phantom series of whisker ENDPOINTS -- observed: a 10-point zig-zag for 5
# real points). This pass finds the I-beams directly and recovers the datum at
# each bar's CENTRE (the true value for a symmetric error bar, which is how
# matplotlib draws them), so the phantom can be stripped and the real points +
# uncertainty emitted instead.
#
# Precision-first (policy: never fabricate data): a bar is recovered ONLY as a
# confident I-beam -- a whisker with a cap at BOTH ends -- and only when at
# least ``_MIN_IBEAMS`` same-colour I-beams of one orientation are present (a
# lone vertical stroke could be a data spike; a capless whisker is ambiguous
# with a gridline / bar / annotation line and is deliberately NOT recovered).
_MIN_IBEAMS = 3
# A cap sits at a whisker END: its centre-x within _CAP_X_TOL of the whisker x
# (vertical bar) and its y within _CAP_END_TOL of the whisker top/bottom.
_CAP_X_TOL = 3.0
_CAP_END_TOL = 2.5
# A cap is short relative to its whisker's length (a wide horizontal stroke is a
# gridline / data segment, not a cap).
_CAP_MAX_LEN_FRAC = 0.5


def _thin_strokes(region: Region, paths: list[Path], exclude: set[int],
                  max_len: float):
    """Classify each thin, short in-region stroke as a vertical/horizontal
    segment. Yields ``(i, cx, cy, lo, hi, length, orient, color)`` where orient
    is 'v' (near-vertical) or 'h' (near-horizontal); lo/hi are the extent along
    the LONG axis (y for 'v', x for 'h')."""
    for i in getattr(region, "path_indices", []):
        if i in exclude:
            continue
        p = paths[i]
        if len(p.points) > 3:
            continue
        b = p.bbox
        bw, bh = b[2] - b[0], b[3] - b[1]
        color = _round_color(p.stroke or p.fill)
        cx, cy = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
        if bw <= _THIN_PX and _THIN_PX < bh <= max_len:
            yield (i, cx, cy, b[1], b[3], bh, "v", color)
        elif bh <= _THIN_PX and _THIN_PX < bw <= max_len:
            yield (i, cx, cy, b[0], b[2], bw, "h", color)


def _find_ibeams(strokes, whisker_orient):
    """Match whiskers of ``whisker_orient`` ('v' or 'h') to perpendicular caps at
    BOTH ends. Returns ``[(whisker_idx, cap_idx_a, cap_idx_b, cx, cy, half),...]``
    where (cx, cy) is the bar centre and ``half`` its half-length (the error)."""
    cap_orient = "h" if whisker_orient == "v" else "v"
    caps = [s for s in strokes if s[6] == cap_orient]
    out = []
    for w in strokes:
        if w[6] != whisker_orient:
            continue
        _i, wcx, wcy, wlo, whi, wlen, _o, wcol = w
        # A cap sits centred on the whisker's cross-axis, at one of its ends,
        # is short, and shares its colour.
        near_lo, near_hi = None, None
        for c in caps:
            _ci, ccx, ccy, _clo, _chi, clen, _co, ccol = c
            if wcol is not None and ccol is not None and wcol != ccol:
                continue
            if clen > _CAP_MAX_LEN_FRAC * wlen:
                continue
            if whisker_orient == "v":
                if abs(ccx - wcx) > _CAP_X_TOL:
                    continue
                if abs(ccy - wlo) <= _CAP_END_TOL:
                    near_lo = c
                elif abs(ccy - whi) <= _CAP_END_TOL:
                    near_hi = c
            else:  # horizontal whisker, vertical caps at left/right ends
                if abs(ccy - wcy) > _CAP_X_TOL:
                    continue
                if abs(ccx - wlo) <= _CAP_END_TOL:
                    near_lo = c
                elif abs(ccx - whi) <= _CAP_END_TOL:
                    near_hi = c
        if near_lo is not None and near_hi is not None:
            mid = (wlo + whi) / 2.0
            half = abs(whi - wlo) / 2.0
            if whisker_orient == "v":
                out.append((w[0], near_lo[0], near_hi[0], wcx, mid, half))
            else:
                out.append((w[0], near_lo[0], near_hi[0], mid, wcy, half))
    return out


def recover_markerless_error_bars(
    region: Region, paths: list[Path], exclude: set[int] | None = None
) -> tuple[set[int], list[tuple[float, float, float, str]]]:
    """Recover MARKER-LESS error bars (no central marker glyph).

    Returns ``(idx_to_strip, points)`` where ``idx_to_strip`` is the whisker+cap
    path indices (decoration to remove so they are not traced as a phantom
    polyline) and ``points`` is ``[(cx_px, cy_px, err_px, orient), ...]`` -- one
    per confident I-beam, its CENTRE (the datum) and half-length (the symmetric
    error). ``orient`` is 'v' (y-error) or 'h' (x-error). Empty when fewer than
    ``_MIN_IBEAMS`` same-orientation I-beams are found (precision over recall;
    the caller then leaves the region untouched).

    ``exclude`` are path indices already claimed by the marker-based
    ``recover_error_bars`` so the two passes never double-count.
    """
    idxs = list(getattr(region, "path_indices", []))
    if not idxs:
        return set(), []
    exclude = exclude or set()
    diag = _bmax(region.bbox) or 1.0
    max_len = 0.6 * diag
    strokes = list(_thin_strokes(region, paths, exclude, max_len))
    if len(strokes) < _MIN_IBEAMS:
        return set(), []

    best_idx: set[int] = set()
    best_points: list[tuple[float, float, float, str]] = []
    for orient in ("v", "h"):
        ibeams = _find_ibeams(strokes, orient)
        if len(ibeams) < _MIN_IBEAMS:
            continue
        idx = set()
        pts = []
        for wi, ca, cb, cx, cy, half in ibeams:
            idx.update((wi, ca, cb))
            pts.append((cx, cy, half, orient))
        # Prefer the orientation that explains more bars (a chart is x- OR
        # y-error, not usually both marker-less).
        if len(pts) > len(best_points):
            best_idx, best_points = idx, pts
    return best_idx, best_points
