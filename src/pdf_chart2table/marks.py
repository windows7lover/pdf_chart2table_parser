"""Classify small data-mark paths in a region and group them into series.

A scatter / line-with-markers chart draws each data point as a small path:
matplotlib renders a circle ``o`` as an ~8-bezier closed loop (≈65 flattened
points), a square ``s`` as a 4-corner rect (5 points), a triangle ``^`` /
diamond ``D`` as a small closed polygon (3-4 points), a star ``*`` as a ~10-arm
polygon, and ``+`` / ``x`` as small stroked crosses. Every mark of one series
shares the same fill+stroke colour and size; its centroid (mean of points) is
the data point in pixel space.

We keep only *small* paths inside the region, drop the long spines / gridlines /
tick segments and the legend swatches (which sit just left of legend text), and
group the survivors by ``(shape, rounded fill colour, rounded stroke colour)``
into one :class:`Mark` list per series. One data point may be drawn as two
coincident paths (a filled glyph plus a stroke outline), which would otherwise
become two series at identical positions; we merge groups whose point sets are
(near-)positionally identical into one series.

Public API:
    classify_marks(region, paths, texts) -> list[SeriesMarks]
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .model import Color, Path, Region, TextSpan

# A data mark's bbox is at most this fraction of the region on each side.
_MAX_MARK_FRAC = 0.1
# A mark must be at least this many points across (drops degenerate dots and
# the zero-height horizontal line swatches of a legend).
_MIN_MARK_SIZE = 0.5
# A real data mark is a 2D shape: both bbox sides must reach this. Tick marks /
# spine / gridline segments are ~1D (one side near zero), so this rejects them.
_MIN_MARK_SIDE = 1.5
# Marker aspect ratios stay near 1; reject elongated shapes (segments).
_MAX_ASPECT = 3.0
# A mark whose centroid sits within this of the region border is on a
# spine/frame edge (tick marks live there), not off-axis data.
_BORDER_TOL = 2.0
# Plot-box clip tolerance: a mark centroid may sit this fraction of the axis
# span outside the calibrated spine box and still count as in-plot data (covers
# markers that straddle the spine and minor calibration slack). Beyond it the
# mark is a legend swatch / annotation / out-of-axis phantom and is dropped.
_CLIP_FRAC = 0.03
# Legend swatch: a mark sitting within this gap to the left of a text span and
# vertically centred on it.
_LEGEND_GAP = 40.0
# Two marker groups are the SAME series drawn twice (filled glyph + stroke
# outline, or two coincident shapes) when their point sets match one-to-one with
# every pair within this many points: merge them into one series.
_DUP_POS_TOL = 1.5


@dataclass
class Mark:
    """One data mark: its pixel centroid and shape/colour signature."""

    cx: float
    cy: float
    shape: str
    fill: Color | None
    stroke: Color | None


@dataclass
class SeriesMarks:
    """All marks sharing one (shape, fill, stroke) signature."""

    shape: str
    fill: Color | None
    stroke: Color | None
    marks: list[Mark] = field(default_factory=list)


def _round_color(c: Color | None) -> tuple | None:
    return tuple(round(v, 2) for v in c) if c is not None else None


def _centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    n = len(points)
    return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n)


def _shape_of(p: Path) -> str:
    """Classify a small mark path by vertex count / open-vs-closed.

    Counts use the bezier-flattened polyline produced by ``pdf_vector`` (8
    segments per cubic), so the thresholds are matplotlib-marker specific but
    robust for vector charts.
    """
    n = len(p.points)
    filled = p.fill is not None
    if n >= 40:
        return "circle"
    if n >= 9:
        return "star"
    if n == 5:
        return "square"
    if n == 4:
        return "triangle" if filled else "plus"
    if n == 3:
        return "triangle"
    return "cross" if not filled else "marker"


def _is_legend_swatch(cx: float, cy: float, texts: list[TextSpan]) -> bool:
    """A mark just to the left of, and vertically aligned with, a text span."""
    for t in texts:
        tx0, ty0, _, ty1 = t.bbox
        th = ty1 - ty0
        if abs(cy - 0.5 * (ty0 + ty1)) <= 0.6 * th + 2 and tx0 - _LEGEND_GAP <= cx <= tx0 + 2:
            return True
    return False


def _in_plot_box(cx: float, cy: float, plot_box: tuple | None) -> bool:
    """Centroid is inside the calibrated plot box (spine-to-spine) plus a small
    tolerance. ``plot_box`` is ``(x0, y0, x1, y1)`` from the axis ``pixel_range``
    pair (edges in either order). With no box given, always True (legacy)."""
    if plot_box is None:
        return True
    bx0, by0, bx1, by1 = plot_box
    xlo, xhi = (bx0, bx1) if bx0 <= bx1 else (bx1, bx0)
    ylo, yhi = (by0, by1) if by0 <= by1 else (by1, by0)
    xtol = _CLIP_FRAC * (xhi - xlo)
    ytol = _CLIP_FRAC * (yhi - ylo)
    return (xlo - xtol <= cx <= xhi + xtol) and (ylo - ytol <= cy <= yhi + ytol)


def _on_border(cx: float, cy: float, region: Region) -> bool:
    """Centroid sits on a region spine/frame edge (where tick marks live)."""
    x0, y0, x1, y1 = region.bbox
    return (
        abs(cx - x0) <= _BORDER_TOL or abs(cx - x1) <= _BORDER_TOL
        or abs(cy - y0) <= _BORDER_TOL or abs(cy - y1) <= _BORDER_TOL
    )


def _is_data_mark(p: Path, region: Region) -> bool:
    rw = region.bbox[2] - region.bbox[0]
    rh = region.bbox[3] - region.bbox[1]
    bw = p.bbox[2] - p.bbox[0]
    bh = p.bbox[3] - p.bbox[1]
    if bw >= _MAX_MARK_FRAC * rw or bh >= _MAX_MARK_FRAC * rh:
        return False
    if bw < _MIN_MARK_SIZE and bh < _MIN_MARK_SIZE:
        return False
    # Reject ~1D segments (tick marks / spines / gridlines): a real data mark
    # is a 2D glyph (closed shape or fill) with extent on BOTH sides.
    if min(bw, bh) < _MIN_MARK_SIDE:
        return False
    long, short = max(bw, bh), min(bw, bh)
    if short > 0 and long / short > _MAX_ASPECT:
        return False
    if p.fill is None and p.stroke is None:
        return False
    # A mark centred on the frame edge is a tick, not off-axis data.
    cx, cy = _centroid(p.points)
    if _on_border(cx, cy, region):
        return False
    return True


def _same_positions(a: SeriesMarks, b: SeriesMarks) -> bool:
    """True if two groups mark the SAME points (same count, every mark of one
    within ``_DUP_POS_TOL`` of a distinct mark of the other) -- a filled glyph
    plus its stroke outline drawn at identical positions."""
    if len(a.marks) != len(b.marks):
        return False
    remaining = list(b.marks)
    for m in a.marks:
        for n in remaining:
            if abs(m.cx - n.cx) <= _DUP_POS_TOL and abs(m.cy - n.cy) <= _DUP_POS_TOL:
                remaining.remove(n)
                break
        else:
            return False
    return True


def _merge_duplicate_series(groups: list[SeriesMarks]) -> list[SeriesMarks]:
    """Collapse groups that mark identical positions (filled+stroke duplicates)
    into one series, keeping the first occurrence; distinct series stay separate."""
    kept: list[SeriesMarks] = []
    for sm in groups:
        if any(_same_positions(sm, k) for k in kept):
            continue
        kept.append(sm)
    return kept


def classify_marks(
    region: Region,
    paths: list[Path],
    texts: list[TextSpan],
    plot_box: tuple | None = None,
) -> list[SeriesMarks]:
    """Group the region's small data marks into per-series lists.

    Excludes long spines/gridlines/tick segments (too large or too elongated)
    and legend swatches (aligned with legend text). Marks are grouped by
    ``(shape, rounded fill, rounded stroke)``; each group's centroids are its
    data points in pixel space.

    ``plot_box`` is the calibrated spine-to-spine box ``(x0, y0, x1, y1)``; marks
    whose centroid falls outside it (with a small tolerance) are legend swatches,
    annotation glyphs or out-of-axis phantoms and are dropped. When omitted, no
    box clipping is applied (legacy behaviour).
    """
    region_texts = [texts[i] for i in region.text_indices]
    groups: dict[tuple, SeriesMarks] = {}
    for i in region.path_indices:
        p = paths[i]
        if not _is_data_mark(p, region):
            continue
        cx, cy = _centroid(p.points)
        if not _in_plot_box(cx, cy, plot_box):
            continue
        if _is_legend_swatch(cx, cy, region_texts):
            continue
        shape = _shape_of(p)
        key = (shape, _round_color(p.fill), _round_color(p.stroke))
        sm = groups.get(key)
        if sm is None:
            sm = SeriesMarks(shape=shape, fill=p.fill, stroke=p.stroke)
            groups[key] = sm
        sm.marks.append(Mark(cx=cx, cy=cy, shape=shape, fill=p.fill, stroke=p.stroke))

    # Order series by first appearance (stable, deterministic); merge groups
    # that mark identical positions (filled+stroke duplicate of one series).
    return _merge_duplicate_series(list(groups.values()))
