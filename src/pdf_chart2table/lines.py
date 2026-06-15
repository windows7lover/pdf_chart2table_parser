"""Extract marker-less data curves (polylines) from a chart region.

A line chart with no markers draws each series as one (or a few) stroked open
polyline in a series colour. Curves come in three drawn forms, all handled:
  * one long SOLID polyline (the classic case);
  * one long DASHED / DOTTED polyline carrying the full geometry;
  * many short same-colour SEGMENTS (a dash-dot curve drawn as fragments), which
    we collect and join, x-ordered, into one polyline.
We return each curve's vertices in pixel space, mapped to data by calibration.

What we keep (precision over recall):
  * OPEN stroked polylines with real 2D extent, off the region border and out of
    the legend, in a *saturated* (non-gray) series colour — OR a *dashed* curve
    in any colour (a dashed multi-vertex path is unambiguously a data curve, not
    a solid 2-point gridline/spine), so black/gray dashed curves are kept too;
  * a *solid* black/gray curve when it is unambiguously DATA: many vertices, a
    wide span, varying in BOTH axes and in the plot interior (so black ResNet /
    optimizer curves are recovered) — while gridlines/spines/boxes are excluded.
We drop:
  * near-white strokes (plot background / frame);
  * gray / black strokes that are gridlines / spines / small boxes (axis-aligned,
    ~1-D, few-vertex or low-span — i.e. not a traced curve);
  * axis-aligned 1-D segments (ticks / spines / gridlines);
  * legend swatches (short, near / inside the legend text block);
  * any colour already claimed by a marker series (line+marker plots -> use the
    markers; see ``classify_lines(..., marker_colors=...)``).

A colour's curve is "clean" only if its parts concatenate / merge into ONE curve
that is monotone in x (a single-valued function). A colour may carry two genuinely
different curves drawn in different dash forms (e.g. a solid Testing curve and a
dashed Training curve in the same condition colour); we key candidate curves by
``(colour, dash-form)`` so BOTH are kept when their y-trajectories differ. When a
colour is drawn in several forms that trace the SAME path (a coarse dashed path
plus fine dash-dot fragments, or one curve stroked solid+dashed on top of itself)
the forms overlap in both x and y, so the widest (best-covering) one is kept and
the redundant ones dropped. Parts that overlap in x and cannot be ordered are
SKIPPED (logged), never guessed.

Public API:
    classify_lines(region, paths, texts, marker_colors) -> (list[SeriesLine], list[str])
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .model import Color, Path, Region, TextSpan
from .primitives import (
    LEGEND_GAP as _LEGEND_GAP,
    box_bounds as _box_bounds,
    is_marker_glyph as _is_marker_glyph,
    is_near_white as _is_near_white_prim,
    is_saturated as _is_saturated_prim,
    on_border as _on_border_prim,
    round_color as _round_color,
)

# A data curve must span at least this fraction of the region on its long axis.
_MIN_SPAN_FRAC = 0.25
# A long polyline needs at least this many vertices to be a curve on its own.
_MIN_VERTS = 3
# A merged fragment curve needs at least this many joined points to be a curve
# (rules out a couple of stray same-colour ticks getting merged into "a curve").
_MIN_FRAG_POINTS = 8
# A fragment is a short dash/dash-dot SEGMENT: at most this many vertices. A dense
# many-vertex glyph (a marker circle/star outline) is a marker, not a curve
# fragment, so it must not be collected and merged into a fake curve.
_MAX_FRAG_VERTS = 6
# A wiggly/noisy data curve may be emitted by the renderer as MANY small dense
# open polylines (each spanning a few px in x but carrying ~100 vertices of fine
# detail), tiling the x-axis. Each piece is too short to be a _long_curve and too
# dense to be a _fragment, yet collectively they ARE the curve. We collect
# same-colour OPEN paths with at least this many vertices whose endpoints do not
# close back (a marker glyph is a closed loop; a curve segment traverses across)
# and, only when enough of them tile a wide x-span, merge them into one curve.
_MIN_SEGMENT_VERTS = 40             # dense (>= "circle" glyph threshold) => curve detail, not a dash
_MIN_SEGMENT_ENDPOINT_GAP = 0.5     # start->end gap as a fraction of the longer bbox side
# Guard against fabricating a curve from a few stray dense glyphs: require many
# tiling segments that together cover a wide fraction of the region width.
_MIN_SEGMENT_COUNT = 6
_MIN_SEGMENT_TOTAL_SPAN_FRAC = 0.5
# Dash recovery: a fit drawn as many short GAPPED collinear fragments is merged
# into one curve whose every source fragment reports ``dashes=None`` (each piece
# is itself a tiny solid stroke), so the curve looks solid. When a curve is
# assembled from at least this many short fragments separated by gaps -- i.e. the
# dash pattern was rasterised into the fragment set -- we RECOVER a "dashed"
# signal onto it so downstream (style-faithful reconstruction) knows it is
# dashed. Only applies to curves whose source paths were all solid short
# fragments (the fragmentation IS the dash); a curve already carrying a real
# dash pattern keeps it.
_MIN_DASH_RECOVERY_FRAGMENTS = 6
# An unsaturated (black/gray) stroke qualifies as a DATA curve only if it varies
# in BOTH axes: its shorter bbox side is at least this fraction of its longer
# side. Gridlines / spines are ~1-D (one side ~0) and fall below it, so they are
# rejected even when long and interior.
_MIN_2D_RATIO = 0.08
# A black/gray (low-saturation) curve is inherently ambiguous with boxes / glyphs
# / gridlines, so we admit it as DATA only when it is clearly a traced curve:
# many vertices and a wide span. A small few-vertex gray box / legend frame falls
# below these and is rejected (precision over recall on the ambiguous low-sat case).
# Loosened (recall): the 2-D variation + interior + span guards already reject
# gridlines/boxes, so a slightly shorter / fewer-vertex black curve is still
# recoverable as data (e.g. fits, baselines that genuinely bend).
_MIN_LOWSAT_VERTS = 6
_MIN_LOWSAT_SPAN_FRAC = 0.3
# A near-white stroke (min channel above this) is the plot background / frame,
# never a data curve.
_WHITE_MIN = 0.9
# An axis-aligned (1-D) segment spanning at least this fraction of the region on
# its long axis is a full-width/height GRIDLINE or spine, not a dash/dot fragment
# of a data curve -- even when SATURATED (e.g. a coloured dashed grid). A genuine
# dash fragment is short; a grid rule runs the whole plot. Rejected as a fragment.
_GRID_SPAN_FRAC = 0.6
# Centroid within this of the region border => on a spine/frame edge.
_BORDER_TOL = 2.0
# Plot-box clip tolerance: a vertex may sit this fraction of the axis span
# outside the calibrated spine box and still count as in-plot. Vertices beyond
# it are dropped before a curve is built (kills the out-of-box tail of an
# axis/connector diagonal and inset/legend strokes).
_CLIP_FRAC = 0.03
# After clipping to the plot box a curve must retain at least this fraction of
# its original vertices to remain a real series; a stroke that is mostly outside
# the box (a connector / inset / off-plot line) is dropped.
_MIN_KEPT_FRAC = 0.5
# A curve hugging a single spine (all vertices within this fraction of the box
# span from one edge) is an axis / baseline / zero line, not data: rejected.
_SPINE_BAND_FRAC = 0.02
# A nearly-flat long curve (y-extent below this fraction of the plot height) that
# also has ALL vertices within _SPINE_FLAT_EDGE_FRAC of one edge is a floor/ceiling
# artefact (e.g. a model-band envelope sampled along the x-axis, a zero-floor row
# drawn just inside the bottom spine) and is rejected.  The two-condition guard
# avoids dropping legitimate near-flat data curves (which pass the flat test but
# are far from every edge) or legitimate high-variation edge-grazing series.
_SPINE_FLAT_YSPAN_FRAC = 0.02   # y-extent < 2 % of plot height => "essentially flat"
_SPINE_FLAT_EDGE_FRAC  = 0.03   # all pts within 3 % of one edge => "hugging an edge"
#   Rationale: 3 % catches zero-floor artefacts that barely miss the tight 2 %
#   band (e.g. an artefact at 2.2 % from the spine) while correctly keeping
#   genuine flat data series that sit 4-5 % from the spine edge (e.g. the
#   Thomson opacity plateau that IS the bottom data curve on a log y-axis).
# A DASHED path whose y-extent exceeds its x-extent by this factor is a
# near-vertical connector (e.g. an errorbar or state-transition connector drawn
# as a dashed diagonal between stacked data points) and is rejected.  Real
# dashed data series are roughly horizontal or diagonal, not near-vertical.
# The guard applies ONLY to y-MONOTONIC paths: a straight connector marches
# steadily in one y-direction, whereas a genuine tall, narrow data curve (a
# sharp peak / valley) reverses direction in y, so it is kept even when its
# overall y-extent exceeds its x-extent (see ``_y_monotone``).
_NEAR_VERT_RATIO = 2.0
# Minimum y-span in pixels for the scatter-cloud check to be meaningful.  A
# curve with total y-extent below this is essentially flat; any adjacent-jump
# test on it would be dominated by sub-pixel sampling noise, not real scatter.
_MIN_CLOUD_YSPAN = 2.0
# Two same-colour curves "overlap" (and so cannot be cleanly separated) if their
# x-ranges share more than this fraction of the smaller range.
_OVERLAP_FRAC = 0.5
# A filled path whose bbox width spans at least this fraction of the region width
# is treated as a shaded background band (DOS envelope, confidence region, etc.).
# A stroked path whose colour matches the fill colour of such a band is the
# band's boundary outline -- NOT a data series -- and is rejected.
# (Lower than marks._LARGE_FILL_WIDTH_FRAC = 0.6 to catch slightly narrower bands.)
_FILL_BAND_MIN_WIDTH_FRAC = 0.4
# Same for height: a fill must be non-trivially tall to count as a band.
_FILL_BAND_MIN_HEIGHT_FRAC = 0.04
# ...AND the filled polygon must ENCLOSE a meaningful fraction of its bbox: a real
# shaded band (DOS envelope / confidence region) fills most of its box, whereas a
# thin data CURVE that merely carries a fill attribute (its outline tracing out
# and back along nearly the same path) encloses ~zero area despite a wide bbox.
# Without this, such a curve poisons its colour as a "band colour" and the real
# stroked curve of the same colour is wrongly dropped as a band outline
# (2505.16060_p10c2: a purple data curve lost because its own filled outline had a
# 136x9 bbox but zero enclosed area). Kept LOW: a thin fill-curve's out-and-back
# outline collapses to ~0.00 enclosed area, whereas a genuine (even thin) stacked
# band fills >= ~0.12 of its bbox -- so 0.05 separates them with margin and does
# not exclude the slim bottom band of a multi-band/stacked chart (2308.02111_p17c4,
# whose all-bands-are-data logic broke when its 0.127-fill band was excluded).
_BAND_MIN_FILL_FRAC = 0.05
# Two same-colour curves of different dash form are the SAME path drawn twice
# (so dedup to one) when, over their shared x range, their y values agree within
# this fraction of the combined y-extent; beyond it they are distinct curves.
_SAME_CURVE_YTOL = 0.1
# Geometric coincidence test for marker-connector suppression.
# A line is the connector drawn through a marker series if this fraction of its
# vertices each lie within _COINCIDE_TOL pixels of a marker centroid.
_COINCIDE_TOL = 5.0   # px – marker centroids are usually ≤1px from line vertices
_COINCIDE_FRAC = 0.65  # 65 % of line vertices must match a centroid.
# At 3 vertices (the minimum for a long curve): 2/3 ≈ 0.667 ≥ 0.65, so a
# connector whose 3rd marker was missed by mark-detection is still suppressed.
# 80 % was too strict: a single missed marker out of 3 caused frac=0.67 < 0.80,
# letting the connector escape as a spurious series.
# Maximum ratio of n_centroids / n_verts to still call a line a connector.
# When a colour carries two distinct marker trajectories (e.g. a solid and a
# dashed series both with same-colour markers), the combined centroid count is
# ~2× the per-trajectory count; a line on one trajectory would still pass the
# proximity test (each vertex is near ONE of the two per-x centroids), but the
# bloated ratio reveals the multi-trajectory scenario and prevents suppression.
_COINCIDE_MULTITRACK_RATIO = 1.5


@dataclass
class SeriesLine:
    """One clean line series: its ordered pixel vertices and colour/width/dash."""

    color: Color | None
    width: float | None
    dashes: str | None
    points: list[tuple[float, float]] = field(default_factory=list)
    # Vertices in TRUE draw order (the source polyline's order) for single-path
    # curves; empty when the curve was merged from several paths (order then
    # genuinely ambiguous). ``points`` stays x-sorted for all internal analysis;
    # callers that need the real connection order (sideways / folded curves)
    # should prefer ``raw_points`` when non-empty.
    raw_points: list[tuple[float, float]] = field(default_factory=list)


def _is_saturated(c: Color | None) -> bool:
    return _is_saturated_prim(c)


def _is_near_white(c: Color | None) -> bool:
    return _is_near_white_prim(c, _WHITE_MIN)


def _varies_2d(p: Path) -> bool:
    """True if the path bends through BOTH axes (a real curve), not a straight
    axis-aligned line. Gridlines/spines are ~1-D (one bbox side ~0) and fail it,
    so a black/gray path that passes is a data curve, not a gridline/spine."""
    b = p.bbox
    bw, bh = b[2] - b[0], b[3] - b[1]
    long, short = max(bw, bh), min(bw, bh)
    return long > 0 and short / long >= _MIN_2D_RATIO


def _y_monotone(pts: list[tuple[float, float]]) -> bool:
    """True if the path marches steadily in one y-direction (a straight
    connector), False if it reverses (a peak / valley data curve).

    Vertices are taken in DRAW order (not x-sorted). A monotone run never
    changes the sign of its y-step; a tall narrow data curve (a sharp peak)
    goes up then down, so it is non-monotone and must not be mistaken for a
    near-vertical connector. Sub-pixel jitter is ignored via a small epsilon."""
    eps = 1.0
    sign = 0
    for a, b in zip(pts, pts[1:]):
        dy = b[1] - a[1]
        if abs(dy) < eps:
            continue
        s = 1 if dy > 0 else -1
        if sign and s != sign:
            return False
        sign = s
    return True


def _is_data_lowsat(p: Path, region: Region) -> bool:
    """An unsaturated (black/gray) stroke that is clearly a DATA curve: a long,
    multi-vertex, 2-D, interior open path (not a 2-point or axis-aligned grid /
    spine). Dashed black/gray curves also qualify and are handled by the caller;
    this guards the SOLID black/gray case that ``_is_saturated`` used to drop.
    Requires many vertices and a wide span so a small gray box / legend frame is
    not mistaken for data (precision over recall on the ambiguous low-sat case)."""
    if len(p.points) < _MIN_LOWSAT_VERTS or _is_near_white(p.stroke):
        return False
    if not _varies_2d(p):
        return False
    b = p.bbox
    bw, bh = b[2] - b[0], b[3] - b[1]
    rw = region.bbox[2] - region.bbox[0]
    rh = region.bbox[3] - region.bbox[1]
    if max(bw, bh) < _MIN_LOWSAT_SPAN_FRAC * min(rw, rh):
        return False
    cx, cy = 0.5 * (b[0] + b[2]), 0.5 * (b[1] + b[3])
    return not _on_border(cx, cy, region)


def _on_border(cx: float, cy: float, region: Region) -> bool:
    return _on_border_prim(cx, cy, region, _BORDER_TOL)


def _clip_to_box(pts, plot_box):
    """Keep only vertices inside the plot box (plus tolerance), or return the
    input unchanged when no box is given (legacy)."""
    if plot_box is None:
        return pts
    xlo, ylo, xhi, yhi = _box_bounds(plot_box)
    xtol = _CLIP_FRAC * (xhi - xlo)
    ytol = _CLIP_FRAC * (yhi - ylo)
    return [(x, y) for x, y in pts
            if xlo - xtol <= x <= xhi + xtol and ylo - ytol <= y <= yhi + ytol]


def _is_spine_line(pts, plot_box) -> bool:
    """A curve whose vertices all hug one edge of the plot box is an axis /
    baseline / zero line, not data.

    Two conditions trigger rejection:

    1. *Tight-band*: all vertices within 2 % of one edge (the classic spine /
       gridline case, unchanged).

    2. *Flat-and-near*: the curve is essentially horizontal (y-extent < 2 % of
       plot height) AND all vertices sit within 5 % of the bottom or top edge.
       This catches zero-floor artefacts — dense over-sampled model-band envelopes
       and flat noise strokes drawn just above or below the real axis line — without
       dropping legitimate near-flat data curves (which span more than 2 % of the
       plot height in y and/or are not near any edge).
    """
    if plot_box is None:
        return False
    xlo, ylo, xhi, yhi = _box_bounds(plot_box)
    height = yhi - ylo
    xband = _SPINE_BAND_FRAC * (xhi - xlo)
    yband = _SPINE_BAND_FRAC * height
    xs = [x for x, _ in pts]
    ys = [y for _, y in pts]
    near_left   = all(abs(x - xlo) <= xband for x in xs)
    near_right  = all(abs(x - xhi) <= xband for x in xs)
    near_bottom = all(abs(y - ylo) <= yband for y in ys)
    near_top    = all(abs(y - yhi) <= yband for y in ys)
    if near_left or near_right or near_bottom or near_top:
        return True
    # Plot-box FRAME / spine-set: a path (>=4 vertices) whose every vertex hugs
    # SOME box edge (not all the same one) and that spans most of the box is the
    # rectangular border -- or the four spines drawn / joined as one path with
    # corner jumps -- not data. (2003.00176: the frame leaked in as two black
    # "series".) A real curve's interior vertices are not near any edge, so it is
    # unaffected; the >=4 floor keeps a genuine 2-point corner-to-corner line.
    if len(pts) >= 4:
        on_edge = all(
            abs(x - xlo) <= xband or abs(x - xhi) <= xband
            or abs(y - ylo) <= yband or abs(y - yhi) <= yband
            for x, y in pts)
        if on_edge:
            bw, bh = max(xs) - min(xs), max(ys) - min(ys)
            if bw >= 0.7 * (xhi - xlo) and bh >= 0.7 * height:
                return True
    # Flat-and-near: nearly horizontal AND hugging the bottom or top edge.
    if height > 0:
        yspan = max(ys) - min(ys)
        edge_band = _SPINE_FLAT_EDGE_FRAC * height
        is_flat = yspan < _SPINE_FLAT_YSPAN_FRAC * height
        if is_flat and (
            all(abs(y - ylo) <= edge_band for y in ys)
            or all(abs(y - yhi) <= edge_band for y in ys)
        ):
            return True
    return False


def _fill_band_colors(paths: list[Path], region: Region) -> set[tuple]:
    """Return rounded fill colours of wide non-white filled bands in the region.

    A filled path is a "band" when its bbox width spans at least
    ``_FILL_BAND_MIN_WIDTH_FRAC`` of the region width AND its height spans at
    least ``_FILL_BAND_MIN_HEIGHT_FRAC`` of the region height (so individual
    small marker fills are excluded).  Any stroked path whose stroke colour
    matches one of these is the boundary outline of the band, not a data series.
    """
    rw = region.bbox[2] - region.bbox[0]
    rh = region.bbox[3] - region.bbox[1]
    if rw <= 0 or rh <= 0:
        return set()
    min_bw = _FILL_BAND_MIN_WIDTH_FRAC * rw
    min_bh = _FILL_BAND_MIN_HEIGHT_FRAC * rh
    colors: set[tuple] = set()
    for idx in region.path_indices:
        p = paths[idx]
        if p.fill is None or _is_near_white(p.fill):
            continue
        bw = p.bbox[2] - p.bbox[0]
        bh = p.bbox[3] - p.bbox[1]
        if bw >= min_bw and bh >= min_bh and _enclosed_area_frac(p) >= _BAND_MIN_FILL_FRAC:
            c = _round_color(p.fill)
            if c is not None:
                colors.add(c)
    return colors


def _enclosed_area_frac(p: Path) -> float:
    """Shoelace polygon area of the path divided by its bbox area.

    A solid shaded band fills most of its box (ratio near 0.5-1.0); a thin curve
    whose outline traces out-and-back encloses ~zero area (ratio ~0)."""
    pts = p.points
    if len(pts) < 3:
        return 0.0
    a = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        a += x1 * y2 - x2 * y1
    area = abs(a) / 2.0
    bbox_area = (p.bbox[2] - p.bbox[0]) * (p.bbox[3] - p.bbox[1])
    return area / bbox_area if bbox_area > 0 else 0.0


def _near_legend(cx: float, cy: float, texts: list[TextSpan]) -> bool:
    """Centroid sits inside the legend block: just left of, or overlapping, a
    legend text span (so swatches and the marker/segment glyphs beside the
    labels are excluded)."""
    for t in texts:
        tx0, ty0, tx1, ty1 = t.bbox
        th = ty1 - ty0
        if abs(cy - 0.5 * (ty0 + ty1)) <= 0.6 * th + 2 and tx0 - _LEGEND_GAP <= cx <= tx1:
            return True
    return False


def _off_chart(p: Path, region: Region, texts: list[TextSpan]) -> bool:
    """True if this path is on the frame border, or is a small legend glyph.

    The legend test applies only to *small* paths (swatches / marker glyphs):
    a wide data curve whose centroid happens to fall near a legend label (it can
    end up beside the legend) is data, not a swatch, so it is not excluded.

    The border test uses the centroid of the path's bbox CLIPPED to the region,
    not the raw bbox centroid.  A data curve extending slightly beyond the region
    (e.g. into an adjacent panel) has its unconstrained centroid near the edge;
    using the clipped centroid avoids mis-rejecting such curves as frame/spine
    artefacts.
    """
    b = p.bbox
    rx0, ry0, rx1, ry1 = region.bbox
    # Clip the bbox to the region before computing the centroid for the border
    # check.  An out-of-region path extension pulls the raw centroid toward the
    # edge; the clipped centroid stays inside the region where the real data is.
    clipped_cx = 0.5 * (max(b[0], rx0) + min(b[2], rx1))
    clipped_cy = 0.5 * (max(b[1], ry0) + min(b[3], ry1))
    if _on_border(clipped_cx, clipped_cy, region):
        return True
    rw = rx1 - rx0
    cx, cy = 0.5 * (b[0] + b[2]), 0.5 * (b[1] + b[3])
    small = (b[2] - b[0]) < _MIN_SPAN_FRAC * rw
    # A legend swatch is SPARSE (a short line or a marker glyph). A dense path
    # (>= the "denser than a glyph" segment threshold) that happens to be narrow
    # and near the legend is a curve SEGMENT passing under/beside the legend, not
    # a swatch -- excluding it drops a real series (2004.08077_p7c2: the top curve
    # tiles narrow x-windows past the top-right legend).
    sparse = len(p.points) < _MIN_SEGMENT_VERTS
    return small and sparse and _near_legend(cx, cy, texts)


def _is_long_curve(p: Path, region: Region, texts: list[TextSpan]) -> bool:
    """A single path carrying a whole curve: open, multi-vertex, 2-D extent,
    off-axis and out of the legend. Saturated colours qualify; unsaturated ones
    (black/gray) qualify when DASHED (a dashed multi-vertex path is a data curve,
    not a solid gridline) OR when the SOLID path is clearly data: long,
    multi-vertex, 2-D and interior (``_is_data_lowsat`` -- so a black/gray data
    curve is kept while an axis-aligned/2-point gridline or spine is rejected).
    Paths with a non-white fill are shade/band regions (fill_between overlays),
    not data lines, and are rejected."""
    if p.closed or p.stroke is None:
        return False
    if p.fill is not None and not _is_near_white(p.fill):
        return False
    if len(p.points) < _MIN_VERTS:
        return False
    if (not _is_saturated(p.stroke) and p.dashes is None
            and not _is_data_lowsat(p, region)):
        return False
    b = p.bbox
    bw, bh = b[2] - b[0], b[3] - b[1]
    rw = region.bbox[2] - region.bbox[0]
    rh = region.bbox[3] - region.bbox[1]
    if max(bw, bh) < _MIN_SPAN_FRAC * min(rw, rh):
        return False
    # A dashed path that is near-vertical (y-extent >> x-extent) AND marches
    # steadily in one y-direction is a connector drawn between stacked states
    # (errorbar, state-transition line), not a data series.  Real dashed data
    # series are roughly horizontal or diagonal -- OR a sharp tall peak/valley,
    # which is near-vertical in bbox but reverses direction in y (non-monotone),
    # so it is kept.
    if (p.dashes is not None and bw > 0 and bh > bw * _NEAR_VERT_RATIO
            and _y_monotone(p.points)):
        return False
    return not _off_chart(p, region, texts)


def _is_fragment(p: Path, region: Region, texts: list[TextSpan]) -> bool:
    """A short same-colour curve fragment (a dash-dot piece) to be joined with
    its siblings: open, off the frame border and out of the legend. Saturated
    fragments qualify; unsaturated (black/gray) ones qualify only when they are
    NOT axis-aligned 1-D segments (``_varies_2d``), so black dotted curves are
    recovered while black gridlines / spines / ticks (axis-aligned) stay out.
    A dense many-vertex glyph (a marker outline) is not a segment, so it is
    excluded -- it must not be merged into a fake curve.

    A low-saturation stroke (black/gray) combined with a non-None fill is the
    signature of a *marker glyph outline* (e.g. a black-bordered coloured
    square/circle drawn by matplotlib): the fill is the marker face colour and
    the stroke is the marker edge colour.  Merging many such glyphs would
    produce a spurious "line series", so we reject them here.  Real dashed /
    dash-dot curve fragments are stroked-only (fill=None).

    A full-span axis-aligned segment (a dashed gridline / spine running the whole
    plot width or height) is rejected even when SATURATED: it is decoration, not
    a dash fragment of a data curve.  A genuine fragment is short, so it never
    spans most of the region on one axis while being ~1-D.
    """
    if p.closed or p.stroke is None or len(p.points) > _MAX_FRAG_VERTS:
        return False
    # A non-white fill indicates a shade/band region or a marker glyph, never a
    # data curve fragment -- reject regardless of stroke saturation.
    if p.fill is not None and not _is_near_white(p.fill):
        return False
    # A small compact recognised-shape glyph (an OPEN ``□``/``△``/``+``/``×``
    # marker) is a data point, not a dash fragment. Collecting such glyphs would
    # build a jagged fake line series, so exclude them here (mirrors the
    # filled-marker exclusion above for the open-stroked case).
    if _is_marker_glyph(p):
        return False
    if not _is_saturated(p.stroke) and (_is_near_white(p.stroke) or not _varies_2d(p)):
        return False
    # Full-span axis-aligned (1-D) segment => gridline / spine, not a fragment.
    if not _varies_2d(p):
        b = p.bbox
        bw, bh = b[2] - b[0], b[3] - b[1]
        rw = region.bbox[2] - region.bbox[0]
        rh = region.bbox[3] - region.bbox[1]
        if (rw > 0 and bw >= _GRID_SPAN_FRAC * rw) or (rh > 0 and bh >= _GRID_SPAN_FRAC * rh):
            return False
    return not _off_chart(p, region, texts)


def _is_curve_segment(p: Path, region: Region, texts: list[TextSpan]) -> bool:
    """A dense OPEN sub-segment of a wiggly curve (~100 vertices over a few px),
    drawn as one of many tiling pieces. Distinguished from a marker glyph (a
    closed loop: first vertex ≈ last vertex) by its endpoints sitting far apart
    (it traverses its bbox and never returns). Saturated colours qualify;
    unsaturated (black/gray) ones must vary in 2-D (not an axis-aligned tick).
    Off-frame / legend / banded paths are excluded (same gates as a fragment)."""
    if p.closed or p.stroke is None or len(p.points) < _MIN_SEGMENT_VERTS:
        return False
    if p.fill is not None and not _is_near_white(p.fill):
        return False
    if not _is_saturated(p.stroke) and (_is_near_white(p.stroke) or not _varies_2d(p)):
        return False
    b = p.bbox
    side = max(b[2] - b[0], b[3] - b[1])
    if side <= 0:
        return False
    x0, y0 = p.points[0]
    x1, y1 = p.points[-1]
    gap = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
    if gap <= _MIN_SEGMENT_ENDPOINT_GAP * side:
        return False  # endpoints close back -> a glyph loop, not a curve segment
    return not _off_chart(p, region, texts)


def _x_overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
    lo = max(a[0], b[0])
    hi = min(a[1], b[1])
    if hi <= lo:
        return False
    inter = hi - lo
    smaller = min(a[1] - a[0], b[1] - b[0])
    return smaller > 0 and inter / smaller > _OVERLAP_FRAC


def _dedupe_points(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Sort vertices by x and drop exact duplicates shared at segment joins."""
    pts = sorted(pts, key=lambda q: q[0])
    out: list[tuple[float, float]] = []
    for q in pts:
        if not out or out[-1] != q:
            out.append(q)
    return out


def _is_noise_cloud(pts: list[tuple[float, float]]) -> bool:
    """True if x-sorted ``pts`` look like a scatter / noise cloud rather than a
    coherent curve: too many x-neighbours jump by more than half the y-extent (a
    single-valued curve moves smoothly in y as x advances; a cloud is multivalued
    everywhere). Used to drop shattered/noisy candidate "series".

    A curve with total y-extent below ``_MIN_CLOUD_YSPAN`` (2 px) is essentially
    flat; any per-step jump would be dominated by sub-pixel rendering noise
    rather than real multivaluedness, so we skip the cloud check entirely for
    near-flat curves.  This preserves genuine constant-value data series (e.g. a
    Thomson opacity plateau on a log y-axis) that would otherwise appear "noisy"
    when sorted by x because their 0.1 px y-variation exceeds half the 0.15 px
    total yspan."""
    if len(pts) < _MIN_FRAG_POINTS:
        return False  # too few to judge as a cloud; other guards handle these
    ys = [y for _, y in pts]
    yspan = max(ys) - min(ys)
    if yspan <= 0 or yspan < _MIN_CLOUD_YSPAN:
        return False
    big = 0.5 * yspan
    jumps = sum(abs(pts[i + 1][1] - pts[i][1]) > big for i in range(len(pts) - 1))
    return jumps > 0.2 * len(pts)


def _merge_long(parts: list[Path]) -> list[tuple[float, float]] | None:
    """Concatenate same-colour long polylines into one x-monotone curve, or None.

    Returns the vertices sorted by x if the parts do not overlap in x (so they
    tile one curve); otherwise None (ambiguous, must skip). A merged result that
    looks like a scatter / noise cloud (multivalued everywhere) is also dropped.
    """
    ranges = [(p.bbox[0], p.bbox[2]) for p in parts]
    for i in range(len(ranges)):
        for j in range(i + 1, len(ranges)):
            if _x_overlap(ranges[i], ranges[j]):
                return None
    pts: list[tuple[float, float]] = []
    for p in parts:
        pts.extend(p.points)
    pts = _dedupe_points(pts)
    if _is_noise_cloud(pts):
        return None
    return pts


def _split_into_curves(parts: list[Path]) -> list[list[Path]]:
    """Split a set of possibly x-overlapping same-colour paths into groups where
    each group's paths tile the x-axis without overlap (i.e. each group can be
    merged into one curve).

    This handles the case where a colour carries multiple distinct curves, each
    drawn as several x-disjoint segments. The greedy assignment places each path
    into the first existing group that has no x-overlap with it, or starts a new
    group. The result is a list of groups, each ready for ``_merge_long``.
    """
    clusters: list[list[Path]] = []
    for p in parts:
        xr = (p.bbox[0], p.bbox[2])
        placed = False
        for cluster in clusters:
            if not any(_x_overlap(xr, (c.bbox[0], c.bbox[2])) for c in cluster):
                cluster.append(p)
                placed = True
                break
        if not placed:
            clusters.append([p])
    return clusters


def _recovered_dashes(parts: list[Path], exemplar: Path) -> str | None:
    """Recover a dash signal for a curve drawn as gapped short fragments.

    A dashed fit/guide is often rasterised as MANY tiny solid sub-strokes (each
    ``dashes=None``), so the merged curve looks solid. When the curve is built
    from at least ``_MIN_DASH_RECOVERY_FRAGMENTS`` such short solid fragments the
    fragmentation itself is the dash pattern -> return ``"dashed"``. Otherwise
    return the exemplar's own dash form (a genuinely dashed path keeps it, a
    single continuous solid path stays solid)."""
    if (exemplar.dashes is None
            and len(parts) >= _MIN_DASH_RECOVERY_FRAGMENTS
            and all(p.dashes is None for p in parts)
            and all(len(p.points) <= _MAX_FRAG_VERTS for p in parts)):
        return "dashed"
    return exemplar.dashes


def _merge_fragments(parts: list[Path]) -> list[tuple[float, float]] | None:
    """Join many short same-colour segments into one x-ordered polyline, or None.

    Collects every endpoint, orders by x and de-duplicates. The result is only a
    curve if it is roughly single-valued (``_is_noise_cloud`` rejects scatter /
    grid clouds) and has enough joined points to be a curve.
    """
    pts: list[tuple[float, float]] = []
    for p in parts:
        pts.extend(p.points)
    pts = _dedupe_points(pts)
    if len(pts) < _MIN_FRAG_POINTS:
        return None
    if _is_noise_cloud(pts):
        return None
    return pts


def _xspan(pts: list[tuple[float, float]]) -> float:
    xs = [x for x, _ in pts]
    return max(xs) - min(xs)


def _dash_form(dashes: str | None) -> str:
    """Collapse a dash pattern to its form: 'solid' or 'dashed'."""
    return "solid" if dashes is None else "dashed"


# Stroke widths within ~0.5 pt are "the same" weight; coarser bucketing avoids
# splitting one curve whose segments vary slightly in reported width.
_WIDTH_BUCKET = 0.5


def _width_bucket(width: float | None) -> int:
    """Quantize stroke width so curves can be keyed by weight (color+dash+width).

    Two same-colour curves that overlap in x but differ in THICKNESS (e.g. a thin
    fit over a thick data curve) then fall into distinct groups and are both kept
    instead of colliding into 'cannot separate'. Same-trajectory duplicates are
    still collapsed downstream, so this can only disentangle, never duplicate."""
    return round((width or 0.0) / _WIDTH_BUCKET)


def _interp_y(pts: list[tuple[float, float]], x: float) -> float | None:
    """Linearly interpolate the curve's y at x (pts are x-sorted), or None if x
    is outside its x range."""
    if x < pts[0][0] or x > pts[-1][0]:
        return None
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        if x0 <= x <= x1:
            if x1 == x0:
                return y0
            t = (x - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return pts[-1][1]


def _same_curve(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> bool:
    """True if two x-sorted curves trace the same path (same x extent and y
    agreeing within ``_SAME_CURVE_YTOL`` of the combined y-extent over their
    shared x range) -- i.e. one curve drawn twice, to be deduped to one."""
    lo = max(a[0][0], b[0][0])
    hi = min(a[-1][0], b[-1][0])
    if hi <= lo:
        return False
    ys = [y for _, y in a] + [y for _, y in b]
    yspan = max(ys) - min(ys)
    if yspan <= 0:
        return True
    tol = _SAME_CURVE_YTOL * yspan
    for k in range(11):
        x = lo + (hi - lo) * k / 10
        ya, yb = _interp_y(a, x), _interp_y(b, x)
        if ya is None or yb is None:
            continue
        if abs(ya - yb) > tol:
            return False
    return True


def _marker_proximity_frac(
    verts: list[tuple[float, float]],
    centroids: list[tuple[float, float]],
) -> float:
    """Fraction of ``verts`` that lie within ``_COINCIDE_TOL`` of any centroid."""
    if not centroids or not verts:
        return 0.0
    n_close = sum(
        1 for x, y in verts
        if any(abs(x - cx) <= _COINCIDE_TOL and abs(y - cy) <= _COINCIDE_TOL
               for cx, cy in centroids)
    )
    return n_close / len(verts)


def classify_lines(
    region: Region,
    paths: list[Path],
    texts: list[TextSpan],
    marker_colors: set[tuple] | None = None,
    plot_box: tuple | None = None,
    marker_centroids: dict[tuple, list[tuple[float, float]]] | None = None,
) -> tuple[list[SeriesLine], list[str]]:
    """Extract clean marker-less line series from ``region``.

    ``marker_colors`` is the set of rounded colours already emitted as marker
    series.  ``marker_centroids`` maps each rounded colour to the list of its
    marker pixel centroids.  When ``marker_centroids`` is provided, a line in a
    marker colour is suppressed only when its vertices *geometrically coincide*
    with those centroids (it is the connector drawn through the markers).  This
    replaces the old colour-equality-only rule and correctly keeps a line of the
    same colour that is a DISTINCT data series (its vertices are far from the
    marker positions), while still dropping dashed or solid connectors whose
    vertices match the markers point-for-point.

    Each colour may be drawn as one long solid/dashed path, several x-disjoint
    long paths, or many short fragments, and may carry two genuinely different
    curves in different dash forms (e.g. a solid Testing and a dashed Training
    curve in one condition colour). We build a candidate curve per
    ``(colour, dash-form)``; per colour we then drop candidates that trace the
    SAME path as a wider one (one curve drawn twice) but KEEP those with a
    distinct y-trajectory. Returns ``(series, reasons)`` where ``reasons`` logs
    each colour/form found but skipped (curves that cannot be cleanly ordered).
    """
    marker_colors = marker_colors or set()
    marker_centroids = marker_centroids or {}
    region_texts = [texts[i] for i in region.text_indices]
    band_colors = _fill_band_colors(paths, region)

    def _is_connector(verts, color, form):
        """True when ``verts`` are a connector through a same-colour marker series.

        Decision tree (with centroid data available):
        1. If most vertices are close to centroids (frac >= _COINCIDE_FRAC) AND
           the centroid count is not much larger than the vertex count
           (n_centroids <= _COINCIDE_MULTITRACK_RATIO * n_verts, i.e. ~1:1
           scenario) -> suppress (clear connector case).
        2. If proximity passes but the centroid count is 2× (multitrack scenario:
           same colour carries two marker trajectories) -> suppress solid
           connectors only; keep dashed/dotted lines (they are distinct series
           paired with the solid+marker one in the same colour).
        3. If proximity fails (line is geometrically far from markers) -> keep.
        4. Without centroid data: legacy rule — suppress solid only.
        """
        if color not in marker_colors:
            return False
        if color in marker_centroids:
            ctrs = marker_centroids[color]
            frac = _marker_proximity_frac(verts, ctrs)
            if frac < _COINCIDE_FRAC:
                # Line is far from the markers: definitely a distinct series.
                return False
            if len(ctrs) <= _COINCIDE_MULTITRACK_RATIO * len(verts):
                # ~1:1 centroid-to-vertex ratio: line traces the single trajectory.
                return True
            # Multitrack scenario (centroids span two trajectories): solid
            # connector is still a connector (suppress); dashed/dotted line is
            # a distinct series alongside the solid+marker series (keep).
            return form == "solid"
        # No centroid data: legacy solid-only suppression.
        return form == "solid"

    # Per (colour, dash-form): long-path parts; fragments are dash-dot pieces;
    # segments are dense open curve pieces (a wiggly curve drawn as many tiling
    # sub-paths, each too dense to be a fragment and too short to be long).
    long_groups: dict[tuple, list[Path]] = defaultdict(list)
    frag_groups: dict[tuple, list[Path]] = defaultdict(list)
    seg_groups: dict[tuple, list[Path]] = defaultdict(list)
    for i in region.path_indices:
        p = paths[i]
        color = _round_color(p.stroke)
        if _is_long_curve(p, region, region_texts):
            long_groups[(color, _dash_form(p.dashes), _width_bucket(p.width))].append(p)
        elif _is_fragment(p, region, region_texts):
            frag_groups[(color, _dash_form(p.dashes), _width_bucket(p.width))].append(p)
        elif _is_curve_segment(p, region, region_texts):
            seg_groups[(color, _dash_form(p.dashes), _width_bucket(p.width))].append(p)

    # Suppress fill-region boundary outlines: a stroked path whose colour
    # matches the fill colour of a wide background band is the band's
    # boundary, not a data series — BUT only when at least one GENUINE
    # non-fill-band data element (another curve colour OR a marker colour)
    # already exists.  When every candidate colour is a fill-band colour the
    # fills ARE the data representation (e.g. a violin / stacked-area chart)
    # and we keep all boundary curves intact.
    if band_colors:
        # Genuine non-band colours: LONG-curve candidates (not fragments) in
        # non-band colours, OR marker colours not in the band.  We exclude
        # fragment-only colours (e.g. short unsaturated ticks that happen to
        # pass _is_fragment) because those never form a real series and would
        # otherwise mask the fill-as-data case (e.g. a violin/stacked-area
        # chart where every long-curve colour matches a fill-band colour but
        # axis ticks happen to create a black fragment entry).
        non_band_curve_keys = {k[0] for k in long_groups
                               if k[0] not in band_colors}
        non_band_marker_colors = {c for c in marker_colors if c not in band_colors}
        if non_band_curve_keys or non_band_marker_colors:
            # At least one genuine non-band data element exists: the
            # band-coloured strokes are background region outlines — drop them.
            for key in list(long_groups):
                if key[0] in band_colors:
                    del long_groups[key]
            for key in list(frag_groups):
                if key[0] in band_colors:
                    del frag_groups[key]

    # Clip a merged curve to the plot box and reject axis/baseline/connector
    # lines: drop the out-of-box tail, then drop the whole curve if most of it
    # lay outside the box or it merely hugs a spine.
    def _box_ok(verts):
        clipped = _clip_to_box(verts, plot_box)
        if len(clipped) < _MIN_VERTS or len(clipped) < _MIN_KEPT_FRAC * len(verts):
            return None
        if _is_spine_line(clipped, plot_box):
            return None
        return clipped

    def _raw_order(parts):
        """The single source path's vertices in TRUE draw order (box-clipped,
        consecutive-dedup), or None if the curve spans several paths (order then
        ambiguous -> caller keeps the x-sorted vertices)."""
        if len(parts) != 1:
            return None
        clipped = _clip_to_box(parts[0].points, plot_box)
        out: list[tuple[float, float]] = []
        for q in clipped:
            if not out or out[-1] != q:
                out.append(q)
        return out if len(out) >= _MIN_VERTS else None

    # Build candidate curves per (colour, dash-form): x-sorted verts, exemplar
    # path, the true draw-order vertices (or None for multi-path merges), and the
    # recovered dash form (gapped-fragment curves are tagged "dashed").
    candidates: dict[tuple, list[tuple[list[tuple[float, float]], Path,
                                       list[tuple[float, float]] | None,
                                       str | None]]] = defaultdict(list)
    reasons: list[str] = []
    for (color, form, _wb), parts in long_groups.items():
        verts = _merge_long(parts)
        if verts is None:
            # Some paths overlap in x: try to split into x-compatible groups,
            # each of which tiles one distinct curve (e.g. two solid red curves
            # of the same colour, each drawn as two x-disjoint segments).
            sub_groups = _split_into_curves(parts)
            if len(sub_groups) <= 1:
                # Cannot split further: truly ambiguous.
                reasons.append(f"line color {color}: overlapping curves, cannot separate")
                continue
            for sg in sub_groups:
                v = _merge_long(sg)
                if v is None:
                    reasons.append(f"line color {color}: overlapping sub-curve, skipped")
                    continue
                v = _box_ok(v)
                if v is None:
                    continue
                if _is_connector(v, color, form):
                    continue
                candidates[color].append((v, sg[0], _raw_order(sg), sg[0].dashes))
            continue
        verts = _box_ok(verts)
        if verts is None:
            continue
        if _is_connector(verts, color, form):
            continue
        candidates[color].append((verts, parts[0], _raw_order(parts), parts[0].dashes))
    for (color, form, _wb), parts in frag_groups.items():
        verts = _merge_fragments(parts)
        if verts is None:
            continue  # too few / multivalued fragments: not a usable curve
        verts = _box_ok(verts)
        if verts is None:
            continue
        if _is_connector(verts, color, form):
            continue
        candidates[color].append((verts, parts[0], _raw_order(parts),
                                  _recovered_dashes(parts, parts[0])))
    rw = region.bbox[2] - region.bbox[0]
    for (color, form, _wb), parts in seg_groups.items():
        # A wiggly curve drawn as many dense tiling segments. Require enough
        # pieces covering a wide x-span before treating them as a curve, so a
        # few stray dense glyphs cannot fabricate a series.
        if len(parts) < _MIN_SEGMENT_COUNT:
            continue
        xs0 = min(p.bbox[0] for p in parts)
        xs1 = max(p.bbox[2] for p in parts)
        if rw <= 0 or (xs1 - xs0) < _MIN_SEGMENT_TOTAL_SPAN_FRAC * rw:
            continue
        # The pieces tile the x-axis; split into x-compatible groups and merge
        # each (same machinery as the overlapping-long-curve case).
        for sg in _split_into_curves(parts):
            verts = _merge_long(sg)
            if verts is None:
                continue
            verts = _box_ok(verts)
            if verts is None:
                continue
            if _is_connector(verts, color, form):
                continue
            # A wiggly curve drawn as many short tiling segments is the same
            # gapped-fragment pattern as a dashed fit -> recover dashes.
            candidates[color].append((verts, sg[0], _raw_order(sg),
                                      _recovered_dashes(sg, sg[0])))

    # Per colour: keep candidate forms with distinct y-trajectories; dedup forms
    # that trace the same path (one curve drawn twice -> keep the widest).
    series: list[SeriesLine] = []
    for color, cands in candidates.items():
        kept: list[tuple[list[tuple[float, float]], Path,
                         list[tuple[float, float]] | None, str | None]] = []
        for verts, ex, raw, dash in sorted(cands, key=lambda c: -_xspan(c[0])):
            if any(_same_curve(verts, k[0]) for k in kept):
                continue
            kept.append((verts, ex, raw, dash))
        for verts, ex, raw, dash in kept:
            series.append(SeriesLine(color=ex.stroke, width=ex.width,
                                     dashes=dash, points=verts,
                                     raw_points=raw or []))
    return series, reasons
