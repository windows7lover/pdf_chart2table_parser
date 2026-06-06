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
    is_sparse_on_dense(region, paths, n_extracted_points) -> bool
"""

from __future__ import annotations

import colorsys
from collections import defaultdict
from dataclasses import dataclass, field

from .model import Color, Path, Region, TextSpan

# ---------------------------------------------------------------------------
# Fix 1: filled-region interior over-sampling
# ---------------------------------------------------------------------------
# A large filled path (a DOS band, SED confidence envelope) has its centroid
# area much larger than a data marker.  Any other candidate path whose centroid
# falls *strictly inside* that polygon (not near the boundary) is an interior
# glyph sampled from the fill — reject it.
#
# A filled band must span a significant fraction of the region WIDTH: DOS
# spectra and SED envelopes are horizontal bands that run most of the x-axis.
# This excludes individual scatter point glyphs and small filled circles.
_LARGE_FILL_WIDTH_FRAC = 0.6   # fill bbox width ≥ 60% of region width
# In addition the fill height must be at least this fraction (a thin stripe
# spanning the full width is still a real band).
_LARGE_FILL_HEIGHT_FRAC = 0.05  # fill bbox height ≥ 5% of region height
# A candidate centroid is "on the boundary" of the large fill when it is within
# this many points of the fill's bbox edge.  Boundary points are data (the top
# edge of a DOS band is a spectrum); only interior points are rejected.
_FILL_BOUNDARY_TOL = 3.0

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
# Hue-gradient merge: single-mark groups with the same shape and hue within this
# many degrees are collapsed into one series.  Handles scatter plots where each
# data point is rendered with a slightly different colour along a gradient ramp
# (e.g. a sequential blue colourmap) so each colour bucket has exactly one mark.
# Only groups of one mark are merged this way; multi-mark groups keep their
# identity.
_HUE_MERGE_DEG = 20.0

# Legend-box oversized guard: when the detected legend_bbox covers more than
# this fraction of the calibrated plot-box area, the detection is likely
# wrong (it is misidentifying part of the plot as a legend).  In that case
# the legend-box mark filter is suppressed so real data marks inside the
# erroneously large bbox are not discarded.
_MAX_LEGEND_PLOT_FRAC = 0.25

# Sparse-on-dense guard: skip an extraction if the number of extracted marker
# points is very small relative to the region's total path count AND the region
# contains at least a few dense (line/curve) paths.  Such regions have the real
# data encoded as curves; the stray markers are noise (annotation glyphs, peak
# symbols, etc.), not a genuine scatter series.
_SOD_MAX_POINTS = 8        # only apply when total extracted points ≤ this
_SOD_MIN_PATH_RATIO = 12   # region_paths / n_points must exceed this
_SOD_MIN_REGION_PATHS = 60 # skip guard only fires in non-trivial regions
_SOD_MIN_DENSE_PATHS = 2   # require at least this many >8-vertex paths (lines)
# A path with more than this many vertices in a region is a "dense" line/curve,
# not a single data-mark glyph.
_DENSE_PATH_VERTS = 8


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


def _hue_of(c: Color | None) -> float | None:
    """Return hue in degrees [0, 360) for colour ``c``, or None if no colour."""
    if c is None:
        return None
    h, _, _ = colorsys.rgb_to_hsv(c[0], c[1], c[2])
    return h * 360.0


def _hue_dist(a: float, b: float) -> float:
    """Circular distance between two hue angles (both in degrees)."""
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


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


# Maximum fraction of the plot height/width from any edge that a text span's
# centre can be and still count as a "legend text" candidate.  Text deeper
# inside the chart (beyond this fraction from all edges) is axis annotation /
# data label, not a legend entry, so the swatch check is skipped for it.
_LEGEND_BORDER_FRAC = 0.20


def _is_legend_swatch(
    cx: float,
    cy: float,
    texts: list[TextSpan],
    plot_box: tuple | None = None,
) -> bool:
    """A mark just to the left of, and vertically aligned with, a text span.

    When ``plot_box`` is given, only consider text spans whose vertical centre
    lies within ``_LEGEND_BORDER_FRAC`` of the plot-box edges (top, bottom, left
    or right).  Text spans deeper inside the chart area are data annotations, not
    legend entries, and do not trigger swatch filtering.  This avoids false
    positives on charts with embedded annotations whose text happens to be at
    the same y-level as data markers.
    """
    for t in texts:
        tx0, ty0, _, ty1 = t.bbox
        th = ty1 - ty0
        ty_center = 0.5 * (ty0 + ty1)
        # Positional check: mark is to the left of text and vertically aligned.
        if not (abs(cy - ty_center) <= 0.6 * th + 2
                and tx0 - _LEGEND_GAP <= cx <= tx0 + 2):
            continue
        # Edge proximity check: when a calibrated plot box is available, only
        # treat the text as legend text if its y-centre is within
        # _LEGEND_BORDER_FRAC of one of the four plot-box edges.  Annotation
        # text deep in the middle of the chart is not legend text.
        if plot_box is not None:
            bx0, by0, bx1, by1 = plot_box
            xlo, xhi = (bx0, bx1) if bx0 <= bx1 else (bx1, bx0)
            ylo, yhi = (by0, by1) if by0 <= by1 else (by1, by0)
            x_span = xhi - xlo
            y_span = yhi - ylo
            if x_span > 0 and y_span > 0:
                near_top = ty_center - ylo <= _LEGEND_BORDER_FRAC * y_span
                near_bot = yhi - ty_center <= _LEGEND_BORDER_FRAC * y_span
                near_lft = tx0 - xlo <= _LEGEND_BORDER_FRAC * x_span
                near_rgt = xhi - tx0 <= _LEGEND_BORDER_FRAC * x_span
                if not (near_top or near_bot or near_lft or near_rgt):
                    continue
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


def _is_near_white(c: Color) -> bool:
    """True when colour is white or very near white (axes-patch background)."""
    return c[0] >= 0.95 and c[1] >= 0.95 and c[2] >= 0.95


def _collect_large_fills(paths: list[Path], region: Region) -> list[Path]:
    """Return wide, non-white filled bands inside the region.

    A path is a "large fill" (DOS band / SED envelope / confidence band) when:
      - it has a non-white fill colour (white = axes-patch background, skipped),
      - its bbox WIDTH spans ≥ _LARGE_FILL_WIDTH_FRAC of the region width, and
      - its bbox HEIGHT spans ≥ _LARGE_FILL_HEIGHT_FRAC of the region height.

    The width requirement ensures we only flag wide horizontal bands (DOS/SED
    style), not individual filled circles or small scatter glyphs that happen
    to be larger than a data marker.
    """
    rw = region.bbox[2] - region.bbox[0]
    rh = region.bbox[3] - region.bbox[1]
    if rw <= 0 or rh <= 0:
        return []
    min_bw = _LARGE_FILL_WIDTH_FRAC * rw
    min_bh = _LARGE_FILL_HEIGHT_FRAC * rh
    large: list[Path] = []
    for idx in region.path_indices:
        p = paths[idx]
        if p.fill is None or _is_near_white(p.fill):
            continue
        bw = p.bbox[2] - p.bbox[0]
        bh = p.bbox[3] - p.bbox[1]
        if bw >= min_bw and bh >= min_bh:
            large.append(p)
    return large


def _point_in_bbox_interior(
    cx: float, cy: float, bbox: tuple, tol: float
) -> bool:
    """True when (cx, cy) is strictly inside bbox by more than tol on every side."""
    x0, y0, x1, y1 = bbox
    return (x0 + tol < cx < x1 - tol) and (y0 + tol < cy < y1 - tol)


def _is_interior_of_large_fill(
    cx: float, cy: float, large_fills: list[Path], shape: str
) -> bool:
    """True when (cx, cy) is in the interior of a large filled shape AND the
    candidate is an unrecognised shape.

    Recognised marker shapes (circle, square, triangle, star) are NOT rejected
    even when inside a large fill: they are real data markers overlaid on a
    confidence band.  Only unrecognised ``marker``-class paths (the typical
    interior-sampling artifact from DOS/SED fills) are rejected.

    Uses a bbox test (conservative: only rejects centroids well inside the
    fill's bounding box).  The boundary tolerance _FILL_BOUNDARY_TOL keeps the
    top-edge trace of a DOS band.
    """
    # Never reject recognised marker shapes — they are real data overlaid on the fill.
    if shape in _KNOWN_CLOSED_SHAPES - {"marker"}:
        return False
    for fp in large_fills:
        if _point_in_bbox_interior(cx, cy, fp.bbox, _FILL_BOUNDARY_TOL):
            return True
    return False


# ---------------------------------------------------------------------------
# Fix 2: shape-aware aspect / min-side relaxation
# ---------------------------------------------------------------------------
# A recognised marker shape (triangle, circle/star, generic closed marker) that
# is small or slightly elongated should still pass.  The strict _MIN_MARK_SIDE
# and _MAX_ASPECT are only enforced on *unrecognised* open paths (which could be
# tick/spine/gridline segments).
#
# Relaxed bounds for recognised closed shapes:
_KNOWN_SHAPE_MIN_SIDE = 0.5    # smaller than default 1.5 — lets small/thin closed glyphs through
_KNOWN_SHAPE_MAX_ASPECT = 6.0  # wider than default 3.0 — lets thin diamonds / h-bar markers through

# Shapes returned by _shape_of that are "recognised closed" and get relaxed bounds.
_KNOWN_CLOSED_SHAPES = frozenset({"circle", "star", "square", "triangle", "marker"})


def _is_data_mark(
    p: Path, region: Region, large_fills: list[Path] | None = None
) -> bool:
    rw = region.bbox[2] - region.bbox[0]
    rh = region.bbox[3] - region.bbox[1]
    bw = p.bbox[2] - p.bbox[0]
    bh = p.bbox[3] - p.bbox[1]
    if bw >= _MAX_MARK_FRAC * rw or bh >= _MAX_MARK_FRAC * rh:
        return False
    if bw < _MIN_MARK_SIZE and bh < _MIN_MARK_SIZE:
        return False
    if p.fill is None and p.stroke is None:
        return False

    # Classify shape early so we can apply shape-aware bounds (Fix 2).
    shape = _shape_of(p)
    known_closed = shape in _KNOWN_CLOSED_SHAPES

    # Reject ~1D segments (tick marks / spines / gridlines): a real data mark
    # is a 2D glyph (closed shape or fill) with extent on BOTH sides.
    # Recognised closed shapes get a relaxed min-side threshold.
    min_side = _KNOWN_SHAPE_MIN_SIDE if known_closed else _MIN_MARK_SIDE
    if min(bw, bh) < min_side:
        return False
    # Recognised closed shapes also get a relaxed aspect ratio threshold
    # (thin diamonds, horizontal-bar markers, inverted-triangle markers).
    max_aspect = _KNOWN_SHAPE_MAX_ASPECT if known_closed else _MAX_ASPECT
    long, short = max(bw, bh), min(bw, bh)
    if short > 0 and long / short > max_aspect:
        return False

    # A mark centred on the frame edge is a tick, not off-axis data.
    cx, cy = _centroid(p.points)
    if _on_border(cx, cy, region):
        return False

    # Fix 1: reject paths whose centroid is interior to a large filled region.
    if large_fills and _is_interior_of_large_fill(cx, cy, large_fills, shape):
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


def _merge_hue_gradient_singles(groups: list[SeriesMarks]) -> list[SeriesMarks]:
    """Merge single-mark groups that share the same shape and similar hue.

    Some charts encode a continuous variable as a colour gradient: each data
    point is drawn with a slightly different hue/saturation along a sequential
    colourmap.  The exact-colour grouping then produces one group per mark
    (each with exactly one point), which ``_is_real_series`` would reject.

    This pass collapses groups of exactly one mark whose active colour (fill or
    stroke) has a hue within ``_HUE_MERGE_DEG`` of an existing merged group.
    Multi-mark groups are never touched -- only isolated singles are merged.
    """
    # Split groups into singles (1 mark) and non-singles
    singles = [sm for sm in groups if len(sm.marks) == 1]
    others = [sm for sm in groups if len(sm.marks) != 1]

    if not singles:
        return groups

    # Candidates to merge into: existing multi-mark groups first, then newly
    # formed merged groups from earlier singles.
    candidates: list[SeriesMarks] = list(others)
    new_groups: list[SeriesMarks] = []
    for sm in singles:
        color = sm.fill or sm.stroke
        h = _hue_of(color)
        matched = None
        if h is not None:
            for existing in candidates:
                if existing.shape != sm.shape:
                    continue
                ec = existing.fill or existing.stroke
                eh = _hue_of(ec)
                if eh is not None and _hue_dist(h, eh) <= _HUE_MERGE_DEG:
                    matched = existing
                    break
        if matched is not None:
            matched.marks.extend(sm.marks)
        else:
            # Start a new merged group from this single; add to candidates so
            # later singles with similar hue accumulate here.
            new_sm = SeriesMarks(shape=sm.shape, fill=sm.fill,
                                 stroke=sm.stroke, marks=list(sm.marks))
            new_groups.append(new_sm)
            candidates.append(new_sm)

    # others were mutated in place; combine with newly created groups.
    return others + new_groups


def _in_legend_box(cx: float, cy: float, legend_bbox: tuple | None) -> bool:
    """True when the centroid falls inside the legend bounding box.

    The legend box (from ``labels.detect_labels``) can contain mini-curve
    decorations -- small marker paths drawn to illustrate the series shape
    inside the legend box -- that are not data points.  Excluding them prevents
    a spurious series from appearing in the extraction.
    """
    if legend_bbox is None:
        return False
    lx0, ly0, lx1, ly1 = legend_bbox
    return lx0 <= cx <= lx1 and ly0 <= cy <= ly1


def classify_marks(
    region: Region,
    paths: list[Path],
    texts: list[TextSpan],
    plot_box: tuple | None = None,
    legend_bbox: tuple | None = None,
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

    ``legend_bbox`` is the bounding box of the detected legend region (from
    ``labels.detect_labels``); marks whose centroid falls inside it are
    mini-curve decorations in the legend box, not data points, and are dropped.
    """
    region_texts = [texts[i] for i in region.text_indices]
    # Build large-fill set once for the region (Fix 1: interior over-sampling).
    large_fills = _collect_large_fills(paths, region)

    # Legend-box oversized guard: if the detected legend_bbox covers too large a
    # fraction of the plot area it is likely a mis-detection; suppress legend-box
    # mark filtering in that case so real data marks are not discarded.
    effective_legend_bbox = legend_bbox
    if legend_bbox is not None and plot_box is not None:
        bx0, by0, bx1, by1 = plot_box
        plot_w = abs(bx1 - bx0)
        plot_h = abs(by1 - by0)
        plot_area = plot_w * plot_h
        lx0, ly0, lx1, ly1 = legend_bbox
        leg_w = abs(lx1 - lx0)
        leg_h = abs(ly1 - ly0)
        leg_area = leg_w * leg_h
        if plot_area > 0 and leg_area / plot_area > _MAX_LEGEND_PLOT_FRAC:
            effective_legend_bbox = None

    groups: dict[tuple, SeriesMarks] = {}
    for i in region.path_indices:
        p = paths[i]
        if not _is_data_mark(p, region, large_fills):
            continue
        cx, cy = _centroid(p.points)
        if not _in_plot_box(cx, cy, plot_box):
            continue
        if _in_legend_box(cx, cy, effective_legend_bbox):
            continue
        if _is_legend_swatch(cx, cy, region_texts, plot_box):
            continue
        shape = _shape_of(p)
        key = (shape, _round_color(p.fill), _round_color(p.stroke))
        sm = groups.get(key)
        if sm is None:
            sm = SeriesMarks(shape=shape, fill=p.fill, stroke=p.stroke)
            groups[key] = sm
        sm.marks.append(Mark(cx=cx, cy=cy, shape=shape, fill=p.fill, stroke=p.stroke))

    # Order series by first appearance (stable, deterministic); merge groups
    # that mark identical positions (filled+stroke duplicate of one series);
    # then merge isolated single-mark groups with similar hue (gradient scatter).
    result = _merge_duplicate_series(list(groups.values()))
    return _merge_hue_gradient_singles(result)


def is_sparse_on_dense(
    region: Region,
    paths: list[Path],
    n_extracted_points: int,
) -> bool:
    """Return True when the extraction looks like sparse noise on a dense chart.

    A chart whose primary data is encoded as curves (polylines, splines) will
    have many paths with high vertex counts.  If the marker extractor only found
    a handful of points AND the region contains at least a few such dense paths,
    the markers are likely annotation glyphs or stray noise rather than genuine
    scatter data -- skip rather than emit a near-empty table.

    Conditions (all must hold):
      * n_extracted_points <= _SOD_MAX_POINTS  (small result)
      * total paths in region >= _SOD_MIN_REGION_PATHS  (non-trivial chart)
      * total_paths / n_extracted_points >= _SOD_MIN_PATH_RATIO  (sparsity ratio)
      * paths with > _DENSE_PATH_VERTS vertices >= _SOD_MIN_DENSE_PATHS  (has lines)
    """
    if n_extracted_points <= 0 or n_extracted_points > _SOD_MAX_POINTS:
        return False
    total = len(region.path_indices)
    if total < _SOD_MIN_REGION_PATHS:
        return False
    if total / n_extracted_points < _SOD_MIN_PATH_RATIO:
        return False
    dense = sum(
        1 for idx in region.path_indices
        if len(paths[idx].points) > _DENSE_PATH_VERTS
    )
    return dense >= _SOD_MIN_DENSE_PATHS
