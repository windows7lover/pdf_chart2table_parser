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

from collections import defaultdict
from dataclasses import dataclass, field

from .model import Color, Path, Region, TextSpan
from .primitives import (
    KNOWN_CLOSED_SHAPES as _KNOWN_CLOSED_SHAPES,
    LEGEND_GAP as _LEGEND_GAP,
    box_bounds as _box_bounds,
    centroid as _centroid,
    hue_dist as _hue_dist,
    hue_of as _hue_of,
    is_white as _is_near_white,
    on_border as _on_border_prim,
    round_color as _round_color,
    shape_of as _shape_of,
)

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
# Recognised closed shapes (circle, square, diamond, triangle, star) that are
# used as upper-limit arrows or larger decorative markers may legitimately
# exceed the default 10% threshold.  Allow up to 20% for these shapes.
_MAX_MARK_FRAC_KNOWN = 0.20
# A mark must be at least this many points across (drops degenerate dots and
# the zero-height horizontal line swatches of a legend).
_MIN_MARK_SIZE = 0.5
# A real data mark is a 2D shape: both bbox sides must reach this. Tick marks /
# spine / gridline segments are ~1D (one side near zero), so this rejects them.
_MIN_MARK_SIDE = 1.5
# Marker aspect ratios stay near 1; reject elongated shapes (segments).
_MAX_ASPECT = 3.0
# A matplotlib marker glyph (circle ``o``, star ``*``) is a CLOSED loop: even
# when emitted as an "open" polyline its first and last flattened vertex coincide
# (start == end), so the gap between endpoints is ~0 relative to its size. A
# many-vertex path whose endpoints sit far apart (it traverses across its bbox and
# never returns) is a dense CURVE SEGMENT drawn open, not a glyph. Reject such
# paths as marks so a wiggly curve drawn as many small dense segments is not
# mistaken for a scatter series (lines.py collects them into the real curve).
# Only applied to many-vertex paths (>= the "circle" vertex threshold); few-vertex
# glyphs (squares/triangles/crosses) are genuinely open and untouched.
_GLYPH_VERTS = 40           # matches primitives.shape_of "circle" threshold
_MAX_GLYPH_ENDPOINT_GAP = 0.5  # gap (start->end) as a fraction of the longer bbox side
# A mark whose centroid sits within this of the region border is on a
# spine/frame edge (tick marks live there), not off-axis data.
_BORDER_TOL = 2.0
# Plot-box clip tolerance: a mark centroid may sit this fraction of the axis
# span outside the calibrated spine box and still count as in-plot data (covers
# markers that straddle the spine and minor calibration slack). Beyond it the
# mark is a legend swatch / annotation / out-of-axis phantom and is dropped.
_CLIP_FRAC = 0.03
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

# Colormap-scatter merge: a scatter where each point is drawn in a DISTINCT
# colour sampled from a continuous colourmap (viridis, jet, …) produces one
# group per colour — many of them singletons — that the hue-gradient merge above
# cannot join (a full colourmap spans the whole hue wheel, far beyond 20°). The
# result is a many-point scatter collapsing to a handful of tiny groups that the
# downstream count filter then discards. This pass recovers it: when MANY
# (≥ _CMAP_MIN_GROUPS) groups share the SAME shape AND ~the same marker size
# (diameter within _CMAP_SIZE_TOL px) and together span a WIDE hue range
# (≥ _CMAP_HUE_SPAN°), they are one colormap-coded series — merge them, keeping
# ALL their points. Runs AFTER _merge_hue_gradient_singles, so a bimodal palette
# (e.g. a blue cluster + a red cluster) has already collapsed to 2 groups and
# fails the ≥4-group requirement — only a genuinely many-coloured ramp qualifies.
_CMAP_MIN_GROUPS = 4     # need at least this many same-shape/size colour groups
_CMAP_SIZE_TOL = 1.0     # marker diameters must agree within this many px
_CMAP_HUE_SPAN = 90.0    # representative hues must span at least this many degrees
# A colourmap-coded scatter is point-PER-colour: each colour group is ~1 mark.
# DISCRETE multi-colour series (e.g. 5 power-law fits, each its own solid colour
# with many markers) must NOT be merged. Require the cluster's groups to be sparse
# (median marks-per-group at or below this) before treating it as a colormap.
_CMAP_MAX_MEDIAN_MARKS = 2

# Faint-series locus guard: a small mark cluster (≤ _LOCUS_SMALL_MAX marks)
# admitted by faint-series recovery must have ALL marks strictly inside the
# calibrated plot box — the tolerance that normal marks enjoy (_CLIP_FRAC) is
# NOT extended to small clusters.  This prevents scattered contour / credible-
# band points from an adjacent marginal panel from slipping through as a
# spurious "faint series" just because they land within the 3% tolerance zone
# at the shared border.  Note: the test is applied on pixel centroids using the
# same plot_box passed to classify_marks.
_LOCUS_SMALL_MAX = 2  # apply strict check to groups with ≤ this many marks

# Legend-box oversized guard: when the detected legend_bbox covers more than
# this fraction of the calibrated plot-box area, the detection is likely
# wrong (it is misidentifying part of the plot as a legend).  In that case
# the legend-box mark filter is suppressed so real data marks inside the
# erroneously large bbox are not discarded.
_MAX_LEGEND_PLOT_FRAC = 0.40  # vs the region (matches labels._legend_box cap)

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

# Glyph-text-run guard: a legend / annotation label drawn as VECTOR GLYPH OUTLINES
# (no ToUnicode text) leaves a horizontal row of small glyph paths that the mark
# extractor would otherwise read as a scatter series (2001.11305: "Experiment"
# became 11 black points). A run of letters is told apart from a real flat data
# series by TWO signals a data series never has together: the glyphs are packed
# letter-tight (bbox gaps ~0) AND vary in size (a series repeats ONE identical
# glyph). Both must hold, so an evenly-spaced uniform data row is never flagged.
_TEXTRUN_MIN_LEN = 5          # letters in a row before it counts as a word
_TEXTRUN_GAP_FRAC = 0.9       # max bbox gap as a fraction of median glyph width
_TEXTRUN_ROW_FRAC = 0.7       # same-row tolerance as a fraction of median height
_TEXTRUN_MIN_SIZE_CV = 0.18   # min width/height CV (letters vary; markers don't)


@dataclass
class Mark:
    """One data mark: its pixel centroid and shape/colour signature.

    ``size`` is the marker's diameter in pixels (the longer bbox side); it lets
    the colormap-scatter merge require that merged glyphs share a marker size.
    """

    cx: float
    cy: float
    shape: str
    fill: Color | None
    stroke: Color | None
    size: float = 0.0


@dataclass
class SeriesMarks:
    """All marks sharing one (shape, fill, stroke) signature."""

    shape: str
    fill: Color | None
    stroke: Color | None
    marks: list[Mark] = field(default_factory=list)


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

    Purely-numeric text spans (tick labels, data-point annotation numbers like
    "8", "9", "10") are never treated as legend entry labels — a real legend
    entry always contains at least one letter.  This prevents scatter data
    markers adjacent to log-axis point labels from being mis-classified as
    legend swatches.
    """
    for t in texts:
        # A legend entry label always contains at least one letter.  Skip
        # purely-numeric strings (axis tick labels, data-point annotations).
        stripped = t.text.strip()
        if stripped and not any(c.isalpha() for c in stripped):
            continue
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
    xlo, ylo, xhi, yhi = _box_bounds(plot_box)
    xtol = _CLIP_FRAC * (xhi - xlo)
    ytol = _CLIP_FRAC * (yhi - ylo)
    return (xlo - xtol <= cx <= xhi + xtol) and (ylo - ytol <= cy <= yhi + ytol)


def _strictly_in_plot_box(cx: float, cy: float, plot_box: tuple | None) -> bool:
    """Centroid is strictly inside the calibrated plot box — NO tolerance.

    Used for the faint-series locus guard: small mark clusters (≤
    _LOCUS_SMALL_MAX marks) must have every mark strictly inside the plot box,
    not merely within the _CLIP_FRAC tolerance zone.  This prevents scattered
    contour/credible-band points from an adjacent marginal panel from being
    admitted as a faint series just because they sit within the 3% border.
    """
    if plot_box is None:
        return True
    xlo, ylo, xhi, yhi = _box_bounds(plot_box)
    return (xlo <= cx <= xhi) and (ylo <= cy <= yhi)


def _cv(vals: list[float]) -> float:
    """Coefficient of variation (std/mean); 0 for an empty/constant list."""
    if not vals:
        return 0.0
    mean = sum(vals) / len(vals)
    if mean <= 0:
        return 0.0
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    return var ** 0.5 / mean


def _text_run_indices(cands: list[tuple]) -> set[int]:
    """Indices (into ``cands``) of candidate marks that are really GLYPH-OUTLINE
    TEXT (a legend/annotation word drawn as vector paths), not data points.

    ``cands`` items are ``(idx, cx, cy, w, h)``. A word is a same-row run of
    >= _TEXTRUN_MIN_LEN glyphs that are packed letter-tight (consecutive bbox
    gap <= _TEXTRUN_GAP_FRAC x median width) AND vary in size (width or height
    CV >= _TEXTRUN_MIN_SIZE_CV). A genuine flat data series fails the size-CV
    test (it repeats one identical glyph), so it is never flagged.
    """
    if len(cands) < _TEXTRUN_MIN_LEN:
        return set()
    heights = sorted(c[4] for c in cands)
    medh = heights[len(heights) // 2] or 1.0
    rows: list[list[tuple]] = []
    for c in sorted(cands, key=lambda c: c[2]):          # by cy
        for r in rows:
            if abs(c[2] - r[0][2]) <= _TEXTRUN_ROW_FRAC * medh:
                r.append(c)
                break
        else:
            rows.append([c])
    flagged: set[int] = set()
    for r in rows:
        items = sorted(r, key=lambda c: c[1])            # by cx
        widths = sorted(c[3] for c in items)
        medw = widths[len(widths) // 2] or 1.0
        gap_max = _TEXTRUN_GAP_FRAC * medw
        run = [items[0]]
        runs = [run]
        for cur in items[1:]:
            prev = run[-1]
            gap = (cur[1] - cur[3] / 2) - (prev[1] + prev[3] / 2)  # bbox gap
            if gap <= gap_max:
                run.append(cur)
            else:
                run = [cur]
                runs.append(run)
        # A row that contains a confirmed WORD (a long, size-varying tight run) is
        # a text LINE -- flag every glyph on it, so short neighbouring words on the
        # same baseline (e.g. "Fit" beside "Experiment") are dropped too.
        if any(_is_word(run) for run in runs):
            flagged.update(c[0] for c in items)
    return flagged


def _is_word(run: list[tuple]) -> bool:
    """A tight horizontal run is a glyph-text word when it is long enough AND its
    glyphs vary in size (the discriminator vs a uniform flat data series)."""
    if len(run) < _TEXTRUN_MIN_LEN:
        return False
    size_cv = max(_cv([c[3] for c in run]), _cv([c[4] for c in run]))
    return size_cv >= _TEXTRUN_MIN_SIZE_CV


def _on_border(cx: float, cy: float, region: Region) -> bool:
    """Centroid sits on a region spine/frame edge (where tick marks live)."""
    return _on_border_prim(cx, cy, region, _BORDER_TOL)


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


def _is_open_curve_segment(p: Path) -> bool:
    """True when ``p`` is a dense CURVE SEGMENT drawn as an open polyline, not a
    marker glyph.

    A matplotlib marker (circle, star) is a closed loop: its first and last
    flattened vertex coincide, so the endpoint gap is ~0 relative to its size.
    A curve segment traverses its bbox (start far from end) and has many vertices.
    Only many-vertex paths are tested -- few-vertex glyphs (triangles, crosses,
    squares) are legitimately open and never flagged here.
    """
    if p.closed or len(p.points) < _GLYPH_VERTS:
        return False
    b = p.bbox
    side = max(b[2] - b[0], b[3] - b[1])
    if side <= 0:
        return False
    x0, y0 = p.points[0]
    x1, y1 = p.points[-1]
    gap = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
    return gap > _MAX_GLYPH_ENDPOINT_GAP * side


def _is_data_mark(
    p: Path, region: Region, large_fills: list[Path] | None = None
) -> bool:
    rw = region.bbox[2] - region.bbox[0]
    rh = region.bbox[3] - region.bbox[1]
    bw = p.bbox[2] - p.bbox[0]
    bh = p.bbox[3] - p.bbox[1]
    # Quick reject for degenerate/invisible paths before shape classification.
    if bw < _MIN_MARK_SIZE and bh < _MIN_MARK_SIZE:
        return False
    if p.fill is None and p.stroke is None:
        return False

    # A many-vertex OPEN path whose endpoints do not close back on themselves is
    # a dense curve segment, not a marker glyph (glyphs are closed loops). Reject
    # it so a wiggly curve drawn as many small dense segments is not read as a
    # scatter series; lines.py collects those segments into the real curve.
    if _is_open_curve_segment(p):
        return False

    # Classify shape so we can apply shape-aware bounds (Fix 2 + Fix 3).
    shape = _shape_of(p)
    # A recognised marker glyph — including the OPEN ``+``/``×`` crosses — earns
    # the relaxed size/aspect/min-side bounds: a small cross marker (two short
    # crossing strokes) is a data point, not a tick/segment, so it must not be
    # rejected by the strict 1-D-segment thresholds and then swept into a fake
    # line by ``lines._is_fragment``. The open crosses are only relaxed when they
    # carry the full glyph geometry (>= 4 flattened vertices); a 2-vertex diagonal
    # ``cross`` is a line/tick segment and keeps the strict 1-D thresholds.
    known_closed = shape in _KNOWN_CLOSED_SHAPES or (
        shape in {"plus", "cross"} and len(p.points) >= 4
    )

    # Reject oversized paths: a data mark is small relative to the plot.
    # Recognised closed shapes (circle, square, diamond, triangle, …) get a
    # relaxed threshold (_MAX_MARK_FRAC_KNOWN = 20%) to accommodate astronomy
    # upper-limit arrows and other intentionally larger marker glyphs.
    # Unrecognised open paths keep the strict 10% limit.
    max_frac = _MAX_MARK_FRAC_KNOWN if known_closed else _MAX_MARK_FRAC
    if bw >= max_frac * rw or bh >= max_frac * rh:
        return False

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

    # A mark centred on the frame edge is a tick, not off-axis data -- UNLESS it
    # is a recognised 2D marker glyph. A data point at the axis EXTREME (x=xmin or
    # xmax, y=ymin or ymax) legitimately sits on the spine (2005.11717_p17c2: the
    # ±0.2 endpoint markers); ticks are 1-D and already rejected above, so a
    # recognised closed glyph here is genuine edge data, not a tick.
    cx, cy = _centroid(p.points)
    if not known_closed and _on_border(cx, cy, region):
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


def _coalesce_duplicate(kept: SeriesMarks, dup: SeriesMarks) -> None:
    """Fold a coincident duplicate group ``dup`` into ``kept`` (same positions).

    One physical marker can be emitted as two overlaid paths: a filled blob plus a
    separate stroked OUTLINE (e.g. a red-filled glyph with a black square edge).
    The exact-colour grouping splits these into two same-position groups; the
    surviving series must describe the SINGLE real marker, not just whichever path
    was drawn first.  Reconcile the SHAPE so the marker renders faithfully:

      * shape: prefer the duplicate's recognised non-blob outline shape when
        ``kept`` only has the rounded fill blob (``circle``/generic ``marker``).
        A stroked outline carries the true glyph geometry (square / diamond / …);
        the filled interior flattens to a near-circular blob.

    Only the shape is reconciled. Fill/stroke colours are deliberately left as the
    kept group's: the series colour is ``fill or stroke`` (the visible glyph
    colour), and overwriting the kept stroke here would defeat the downstream
    ``_merge_stroke_optional`` pass, which folds a stroke-None group into a
    matching ``(shape, fill)`` stroked group (e.g. a ``cross`` series drawn as a
    fill-only group at some points and a fill+stroke group at others). Positions
    are unchanged (the groups already coincide). Conservative: shape is only
    overridden blob -> recognised shape, never the reverse, so genuine
    circle/star series are untouched.
    """
    _BLOB_SHAPES = {"circle", "marker"}
    if kept.shape in _BLOB_SHAPES and dup.shape not in _BLOB_SHAPES:
        kept.shape = dup.shape
    # An OPEN marker is a white-fill blob plus a separate COLOURED-EDGE outline at
    # the same spot. If the kept group is that white fill with no stroke, the series
    # colour would be white (invisible); adopt the duplicate's visible edge stroke
    # so the series takes the real (edge) colour (2505.19730_p6c2: blue open markers
    # rendered white). Only when kept has no colour identity and dup's stroke shows.
    def _visible(c):
        return c is not None and min(c) <= 0.9
    if (kept.stroke is None and _visible(dup.stroke)
            and (kept.fill is None or min(kept.fill) > 0.9)):
        kept.stroke = dup.stroke


def _merge_duplicate_series(groups: list[SeriesMarks]) -> list[SeriesMarks]:
    """Collapse groups that mark identical positions (filled+stroke duplicates)
    into one series, keeping the first occurrence; distinct series stay separate.

    The surviving series adopts the true outline SHAPE of its coincident
    duplicates so a marker drawn as a filled blob plus a separate stroked outline
    renders as the single real glyph (see ``_coalesce_duplicate``)."""
    kept: list[SeriesMarks] = []
    for sm in groups:
        dup_of = next((k for k in kept if _same_positions(sm, k)), None)
        if dup_of is not None:
            _coalesce_duplicate(dup_of, sm)
            continue
        kept.append(sm)
    return kept


def _merge_stroke_optional(groups: list[SeriesMarks]) -> list[SeriesMarks]:
    """Merge groups that share shape + fill but differ only by a missing stroke.

    One physical marker glyph (a filled circle / diamond) is sometimes rendered
    with its edge stroke and sometimes without — e.g. a renderer drops the edge
    on a single data point, so the same series splits into a
    ``(shape, fill, stroke)`` group and a ``(shape, fill, None)`` group at
    DIFFERENT positions (so ``_merge_duplicate_series`` cannot join them). These
    are the same series: collapse the stroke-None group into the group with a
    matching non-None stroke.

    Conservative: only fires when the fill is non-None and exactly one side has
    ``stroke is None``. Two groups with distinct non-None strokes (genuinely
    different edge colours) are never merged, and a stroke-None group with no
    matching filled+stroked partner stays separate.
    """
    # Index multi-style groups (non-None stroke) by (shape, rounded fill).
    stroked: dict[tuple, SeriesMarks] = {}
    for sm in groups:
        if sm.fill is not None and sm.stroke is not None:
            stroked.setdefault((sm.shape, _round_color(sm.fill)), sm)

    if not stroked:
        return groups

    result: list[SeriesMarks] = []
    for sm in groups:
        if (sm.fill is not None and sm.stroke is None
                and (sm.shape, _round_color(sm.fill)) in stroked):
            stroked[(sm.shape, _round_color(sm.fill))].marks.extend(sm.marks)
            continue
        result.append(sm)
    return result


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


def _group_size(sm: SeriesMarks) -> float:
    """Representative marker diameter of a group (mean of its marks' sizes)."""
    sizes = [m.size for m in sm.marks if m.size > 0]
    return sum(sizes) / len(sizes) if sizes else 0.0


def _merge_colormap_scatter(groups: list[SeriesMarks]) -> list[SeriesMarks]:
    """Merge same-shape, same-size colour groups that span a wide hue range.

    Recovers a colormap-coded scatter (each point a distinct colour from a
    continuous map, all the same marker shape and size) that the exact-colour
    grouping shattered into many tiny groups.  See ``_CMAP_*`` for the rationale
    and guards.  DATA recovery only: every original mark (and its coordinate) is
    preserved; no centroid is moved.

    Guards (precision-first): considers only candidate groups that have a hue
    (purely-black/grey/None-colour groups are excluded) and clusters them by
    (shape, rounded size).  A cluster is merged ONLY when it has
    ≥ _CMAP_MIN_GROUPS groups AND its representative hues span ≥ _CMAP_HUE_SPAN°.
    A handful of genuinely-distinct sparse series (too few groups) or a tight
    palette (narrow hue span) is left untouched.
    """
    if len(groups) < _CMAP_MIN_GROUPS:
        return groups

    # Candidate groups: those with a determinable hue, keyed by (shape, size).
    by_shape_size: dict[tuple, list[tuple[SeriesMarks, float]]] = defaultdict(list)
    for sm in groups:
        h = _hue_of(sm.fill or sm.stroke)
        if h is None:
            continue
        sz = _group_size(sm)
        size_key = round(sz / _CMAP_SIZE_TOL)
        by_shape_size[(sm.shape, size_key)].append((sm, h))

    # Find the single largest qualifying cluster (one colormap per region is the
    # common case; merging just the dominant cluster is the conservative choice).
    best_key = None
    best_n = 0
    for key, members in by_shape_size.items():
        if len(members) < _CMAP_MIN_GROUPS:
            continue
        hues = [h for _, h in members]
        span = max(
            _hue_dist(a, b) for a in hues for b in hues
        )
        if span < _CMAP_HUE_SPAN:
            continue
        # Sparsity guard: a real colormap is point-per-colour (each group ~1 mark);
        # discrete multi-colour series have many marks per colour -- don't merge.
        counts = sorted(len(sm.marks) for sm, _ in members)
        if counts[len(counts) // 2] > _CMAP_MAX_MEDIAN_MARKS:
            continue
        if len(members) > best_n:
            best_n = len(members)
            best_key = key

    if best_key is None:
        return groups

    merge_set = {id(sm) for sm, _ in by_shape_size[best_key]}
    merged_marks: list[Mark] = []
    first: SeriesMarks | None = None
    result: list[SeriesMarks] = []
    for sm in groups:
        if id(sm) in merge_set:
            if first is None:
                first = sm
            merged_marks.extend(sm.marks)
        else:
            result.append(sm)
    if first is not None:
        # Insert the merged series at the first member's original position.
        merged = SeriesMarks(shape=first.shape, fill=first.fill,
                             stroke=first.stroke, marks=merged_marks)
        out: list[SeriesMarks] = []
        inserted = False
        for sm in groups:
            if id(sm) in merge_set:
                if not inserted:
                    out.append(merged)
                    inserted = True
            else:
                out.append(sm)
        return out
    return result


def _looks_like_colormap_scatter(
    region: Region,
    paths: list[Path],
    large_fills: list[Path] | None,
    plot_box: tuple | None,
) -> bool:
    """True when the region's in-plot data marks look like a colormap scatter.

    Scans the region's data-mark candidates (passing ``_is_data_mark`` and the
    plot-box clip, but WITHOUT any legend-box filtering) and groups them by
    ``(shape, rounded size, rounded colour)``.  Returns True when at least
    ``_CMAP_MIN_GROUPS`` distinct colours share one shape and ~one marker size
    and their representative hues span ≥ ``_CMAP_HUE_SPAN°`` -- the signature of a
    point-per-colour colourmap, not a legend.  Used only to decide whether to
    trust a detected legend_bbox (a colormap lattice is data, not a legend key).
    """
    # (shape, size_key) -> {rounded_colour: representative hue}
    by_shape_size: dict[tuple, dict[tuple, float]] = defaultdict(dict)
    for i in region.path_indices:
        p = paths[i]
        if not _is_data_mark(p, region, large_fills):
            continue
        cx, cy = _centroid(p.points)
        if not _in_plot_box(cx, cy, plot_box):
            continue
        col = p.fill or p.stroke
        h = _hue_of(col)
        if h is None:
            continue
        shape = _shape_of(p)
        size = max(p.bbox[2] - p.bbox[0], p.bbox[3] - p.bbox[1])
        size_key = round(size / _CMAP_SIZE_TOL)
        rc = _round_color(col)
        by_shape_size[(shape, size_key)].setdefault(rc, h)

    for hue_by_color in by_shape_size.values():
        if len(hue_by_color) < _CMAP_MIN_GROUPS:
            continue
        hues = list(hue_by_color.values())
        span = max(_hue_dist(a, b) for a in hues for b in hues)
        if span >= _CMAP_HUE_SPAN:
            return True
    return False


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
    # fraction of the REGION it is likely a mis-detection; suppress legend-box
    # mark filtering so real data marks are not discarded. Measured against the
    # region (same reference labels._legend_box uses) -- measuring against the
    # smaller plot box previously re-rejected legitimately-large corner legends
    # that labels had already accepted, leaking their swatches in as data.
    effective_legend_bbox = legend_bbox
    if legend_bbox is not None:
        rx0, ry0, rx1, ry1 = region.bbox
        region_area = abs(rx1 - rx0) * abs(ry1 - ry0)
        lx0, ly0, lx1, ly1 = legend_bbox
        leg_area = abs(lx1 - lx0) * abs(ly1 - ly0)
        if region_area > 0 and leg_area / region_area > _MAX_LEGEND_PLOT_FRAC:
            effective_legend_bbox = None

    # Colormap-scatter legend-box guard: a colourmap-coded scatter draws each data
    # point in a distinct colour from a continuous map. The glyph-legend detector
    # (labels._detect_glyph_legend_box) can mistake a column of these distinctly
    # coloured data points for a legend key column and return a legend_bbox that
    # sits ON the data, dropping the points inside it. When the region's in-plot
    # marks already look like a colormap scatter (many same-shape/same-size marks
    # spanning a wide hue range), that "legend" is a misdetection of the data
    # lattice -- suppress the legend-box filter so the colormap points survive.
    if effective_legend_bbox is not None and _looks_like_colormap_scatter(
        region, paths, large_fills, plot_box
    ):
        effective_legend_bbox = None

    # First pass: collect candidate marks (path index + geometry) so a horizontal
    # run of glyph-OUTLINE text (a borderless legend whose label is drawn as vector
    # paths, invisible to the text-based swatch filter) can be detected and dropped
    # before grouping -- otherwise the letters become a spurious scatter series.
    cand_marks = []  # (i, cx, cy, w, h)
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
        cand_marks.append((i, cx, cy, p.bbox[2] - p.bbox[0], p.bbox[3] - p.bbox[1]))

    text_idx = _text_run_indices(cand_marks)

    groups: dict[tuple, SeriesMarks] = {}
    for i, cx, cy, _w, _h in cand_marks:
        if i in text_idx:
            continue
        p = paths[i]
        shape = _shape_of(p)
        size = max(p.bbox[2] - p.bbox[0], p.bbox[3] - p.bbox[1])
        key = (shape, _round_color(p.fill), _round_color(p.stroke))
        sm = groups.get(key)
        if sm is None:
            sm = SeriesMarks(shape=shape, fill=p.fill, stroke=p.stroke)
            groups[key] = sm
        sm.marks.append(
            Mark(cx=cx, cy=cy, shape=shape, fill=p.fill, stroke=p.stroke, size=size)
        )

    # Order series by first appearance (stable, deterministic); merge groups
    # that mark identical positions (filled+stroke duplicate of one series);
    # then merge isolated single-mark groups with similar hue (gradient scatter).
    all_groups = list(groups.values())

    # Faint-series locus guard: small mark clusters (≤ _LOCUS_SMALL_MAX) that
    # have any centroid outside the strict plot-box interior are dropped.
    # Normal marks already passed _in_plot_box (with 3% tolerance); this extra
    # check removes marks that are in the tolerance zone at the boundary — a
    # hallmark of scattered contour/credible-band points from an adjacent
    # marginal panel that coincidentally overlap the 3% fringe of the main box.
    # Strong groups (> _LOCUS_SMALL_MAX marks) are unaffected.
    if plot_box is not None:
        all_groups = [
            sm for sm in all_groups
            if len(sm.marks) > _LOCUS_SMALL_MAX
            or all(_strictly_in_plot_box(m.cx, m.cy, plot_box) for m in sm.marks)
        ]

    result = _merge_duplicate_series(all_groups)
    result = _merge_stroke_optional(result)
    result = _merge_hue_gradient_singles(result)
    return _merge_colormap_scatter(result)


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
