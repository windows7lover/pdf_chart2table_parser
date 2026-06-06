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
# A stroke is a saturated series colour if its RGB spread exceeds this; grays /
# blacks / whites (gridlines, spines) fall below it.
_SAT_SPREAD = 0.2
# An unsaturated (black/gray) stroke qualifies as a DATA curve only if it varies
# in BOTH axes: its shorter bbox side is at least this fraction of its longer
# side. Gridlines / spines are ~1-D (one side ~0) and fall below it, so they are
# rejected even when long and interior.
_MIN_2D_RATIO = 0.08
# A black/gray (low-saturation) curve is inherently ambiguous with boxes / glyphs
# / gridlines, so we admit it as DATA only when it is clearly a traced curve:
# many vertices and a wide span. A small few-vertex gray box / legend frame falls
# below these and is rejected (precision over recall on the ambiguous low-sat case).
_MIN_LOWSAT_VERTS = 8
_MIN_LOWSAT_SPAN_FRAC = 0.4
# A near-white stroke (min channel above this) is the plot background / frame,
# never a data curve.
_WHITE_MIN = 0.9
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
_NEAR_VERT_RATIO = 2.0
# Minimum y-span in pixels for the scatter-cloud check to be meaningful.  A
# curve with total y-extent below this is essentially flat; any adjacent-jump
# test on it would be dominated by sub-pixel sampling noise, not real scatter.
_MIN_CLOUD_YSPAN = 2.0
# Legend swatch: a short colored segment within this gap left of legend text.
_LEGEND_GAP = 40.0
# Two same-colour curves "overlap" (and so cannot be cleanly separated) if their
# x-ranges share more than this fraction of the smaller range.
_OVERLAP_FRAC = 0.5
# Two same-colour curves of different dash form are the SAME path drawn twice
# (so dedup to one) when, over their shared x range, their y values agree within
# this fraction of the combined y-extent; beyond it they are distinct curves.
_SAME_CURVE_YTOL = 0.1
# Geometric coincidence test for marker-connector suppression.
# A line is the connector drawn through a marker series if this fraction of its
# vertices each lie within _COINCIDE_TOL pixels of a marker centroid.
_COINCIDE_TOL = 5.0   # px – marker centroids are usually ≤1px from line vertices
_COINCIDE_FRAC = 0.8  # 80 % of line vertices must match a centroid
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


def _round_color(c: Color | None) -> tuple | None:
    return tuple(round(v, 2) for v in c) if c is not None else None


def _is_saturated(c: Color | None) -> bool:
    return c is not None and (max(c) - min(c)) > _SAT_SPREAD


def _is_near_white(c: Color | None) -> bool:
    return c is not None and min(c) >= _WHITE_MIN


def _varies_2d(p: Path) -> bool:
    """True if the path bends through BOTH axes (a real curve), not a straight
    axis-aligned line. Gridlines/spines are ~1-D (one bbox side ~0) and fail it,
    so a black/gray path that passes is a data curve, not a gridline/spine."""
    b = p.bbox
    bw, bh = b[2] - b[0], b[3] - b[1]
    long, short = max(bw, bh), min(bw, bh)
    return long > 0 and short / long >= _MIN_2D_RATIO


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
    x0, y0, x1, y1 = region.bbox
    return (
        abs(cx - x0) <= _BORDER_TOL or abs(cx - x1) <= _BORDER_TOL
        or abs(cy - y0) <= _BORDER_TOL or abs(cy - y1) <= _BORDER_TOL
    )


def _box_bounds(plot_box: tuple) -> tuple[float, float, float, float]:
    bx0, by0, bx1, by1 = plot_box
    xlo, xhi = (bx0, bx1) if bx0 <= bx1 else (bx1, bx0)
    ylo, yhi = (by0, by1) if by0 <= by1 else (by1, by0)
    return xlo, ylo, xhi, yhi


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
    return small and _near_legend(cx, cy, texts)


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
    # A dashed path that is near-vertical (y-extent >> x-extent) is a connector
    # drawn between stacked states (errorbar, state-transition line), not a data
    # series.  Real dashed data series are roughly horizontal or diagonal.
    if p.dashes is not None and bw > 0 and bh > bw * _NEAR_VERT_RATIO:
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
    """
    if p.closed or p.stroke is None or len(p.points) > _MAX_FRAG_VERTS:
        return False
    # A non-white fill indicates a shade/band region or a marker glyph, never a
    # data curve fragment -- reject regardless of stroke saturation.
    if p.fill is not None and not _is_near_white(p.fill):
        return False
    if not _is_saturated(p.stroke) and (_is_near_white(p.stroke) or not _varies_2d(p)):
        return False
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

    # Per (colour, dash-form): long-path parts; fragments are dash-dot pieces.
    long_groups: dict[tuple, list[Path]] = defaultdict(list)
    frag_groups: dict[tuple, list[Path]] = defaultdict(list)
    for i in region.path_indices:
        p = paths[i]
        color = _round_color(p.stroke)
        if _is_long_curve(p, region, region_texts):
            long_groups[(color, _dash_form(p.dashes))].append(p)
        elif _is_fragment(p, region, region_texts):
            frag_groups[(color, _dash_form(p.dashes))].append(p)

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

    # Build candidate curves per (colour, dash-form) (a curve plus exemplar path).
    candidates: dict[tuple, list[tuple[list[tuple[float, float]], Path]]] = defaultdict(list)
    reasons: list[str] = []
    for (color, form), parts in long_groups.items():
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
                candidates[color].append((v, sg[0]))
            continue
        verts = _box_ok(verts)
        if verts is None:
            continue
        if _is_connector(verts, color, form):
            continue
        candidates[color].append((verts, parts[0]))
    for (color, form), parts in frag_groups.items():
        verts = _merge_fragments(parts)
        if verts is None:
            continue  # too few / multivalued fragments: not a usable curve
        verts = _box_ok(verts)
        if verts is None:
            continue
        if _is_connector(verts, color, form):
            continue
        candidates[color].append((verts, parts[0]))

    # Per colour: keep candidate forms with distinct y-trajectories; dedup forms
    # that trace the same path (one curve drawn twice -> keep the widest).
    series: list[SeriesLine] = []
    for color, cands in candidates.items():
        kept: list[tuple[list[tuple[float, float]], Path]] = []
        for verts, ex in sorted(cands, key=lambda c: -_xspan(c[0])):
            if any(_same_curve(verts, k[0]) for k in kept):
                continue
            kept.append((verts, ex))
        for verts, ex in kept:
            series.append(SeriesLine(color=ex.stroke, width=ex.width,
                                     dashes=ex.dashes, points=verts))
    return series, reasons
