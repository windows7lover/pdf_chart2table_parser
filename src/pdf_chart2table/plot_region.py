"""Automatic detection of chart plotting region(s) on a page.

Real papers draw chart frames in several ways and pages are full of non-chart
vector content (body text, equations, tables, schematics, raster image grids).
Detection therefore proceeds in two stages:

1. **Candidate regions.** Three signals propose bounding boxes:
   - matplotlib's white-filled, unstroked **axes-patch** rectangle (one per
     panel; preserves subplot splitting);
   - the bounding box of the **long perpendicular spine lines** ("L" or frame);
   - a **merged-spine frame** built from stacked short collinear segments, which
     is how pgfplots/TikZ and small matplotlib panels draw their frame (each
     spine is many short segments, not one long path). This is the only signal
     that fires for those toolchains.

2. **Chart-vs-not gate.** Every candidate must enclose *chart-like content*
   (saturated-colour data marks/lines or long data polylines) AND have *numeric
   tick labels* just outside its bottom or left edge. This rejects tables,
   equation blocks, schematics and image grids, which is essential on real
   pages (precision over recall).

Returns one ``Region`` per surviving candidate. The white-patch signal yields
one region per subplot panel (with shared-axis links); the merged-spine
fallback yields one region per figure.
"""

from __future__ import annotations

import re
from collections import defaultdict

from .calibrate import calibrate_region
from .model import BBox, Path, Region, TextSpan
from .primitives import is_saturated as _is_saturated

# A candidate plot rectangle must cover at least this fraction of the page
# (real chart panels are small figures on a text page: ~0.04-0.07) and at most
# this fraction (excludes the full-page background rectangle).
_MIN_AREA_FRAC = 0.03
_MAX_AREA_FRAC = 0.97
# Lower floor used only when splitting an enclosing frame into its inner panel
# patches: a packed multi-panel figure can draw panels just under _MIN_AREA_FRAC.
_SPLIT_MIN_AREA_FRAC = 0.008
# An inner panel must be at most this fraction of the enclosing frame's area to
# count as a sub-panel (a frame split into >=2 panels has each well under half).
_SPLIT_MAX_INNER_FRAC = 0.7
# A line is a "spine" candidate if it spans at least this fraction of the
# page along one axis while being essentially straight on the other.
_MIN_SPINE_FRAC = 0.3
# Tolerance (points) for treating a polyline as a straight axis-aligned segment.
_STRAIGHT_TOL = 1.0
# Tolerance (points) for treating two panel edges as aligned (shared axis).
_ALIGN_TOL = 3.0

# --- overlap deduplication (NMS) -------------------------------------------
# Two candidates are "the same panel" if their IoU exceeds this, or if one
# contains at least this fraction of the other. Real toolchains emit several
# candidates per panel (figure-background patch + inner axes patch, merged
# spine, stroked rect frame); only one must survive.
_DEDUP_IOU = 0.5
_DEDUP_CONTAIN = 0.8

# --- merged-spine detection ------------------------------------------------
# A near-axis-aligned segment counts toward a spine if it is this thin across
# and at least this long along its axis.
_SEG_THIN = 1.2
_SEG_MIN_LEN = 2.0
# A merged spine (union span of segments sharing a coordinate) must reach this
# fraction of the page to count as a frame edge.
_SPINE_SPAN_FRAC = 0.06
# A spine corner is formed when the vertical edge x and the horizontal edge's
# left end (and the horizontal edge y and the vertical edge's bottom) coincide
# within this tolerance.
_CORNER_TOL = 10.0
# A merged-spine frame must be at least this many points on each side.
_FRAME_MIN_SIDE = 30.0
# Minimum ratio of (total segment length / span) for a merged spine edge to
# count as a genuine frame spine.  Tick marks have two tiny segments at the
# ends of the axis and reach only ~0.03-0.05; real frame spines cover >= 0.4.
_SPINE_COVERAGE_MIN = 0.4
# A gap larger than this in a vertical spine's segments indicates that two
# panels are stacked (a horizontal gutter between them).  The spine is then
# split into separate per-panel sub-edges.  Gutters between stacked panels
# are typically 30-40 pt; the white space between rows in a multi-row figure
# that IS correctly handled via white-patches is <= 14 pt.
_SPINE_GAP_MIN = 20.0

# --- chart-vs-not gate -----------------------------------------------------
# How far below / left of a candidate to scan for numeric tick labels.
_TICK_BELOW = 22.0
_TICK_LEFT = 38.0
# Minimum numeric tick labels (bottom + left) for a candidate to be a chart.
_MIN_NUM_TICKS = 2
# Chart content: either this many saturated-colour paths, or this many long
# data polylines, must fall inside the candidate.
_MIN_SATURATED = 3
_MIN_POLYLINES = 2
# A polyline is a "data line" if it has more than this many vertices.
_POLYLINE_VERTS = 10
# Strict content (rect-frame candidates only): this many saturated-colour
# *stroked* paths (data series lines/markers). Schematic diagrams have colored
# box fills and connector polylines but no saturated data strokes, so requiring
# stroked colour keeps the permissive rect-frame path from flagging them.
_MIN_SAT_STROKE = 3

_NUMERIC = re.compile(r"^[-+]?\d*\.?\d+$")

# --- chart-type gate (skip non line/scatter regions) -----------------------
# Heatmap/imshow: a dense grid of filled rectangles tiling the panel. Cells are
# uniformly sized and their left/top edges cluster into a small number of
# columns/rows, so the cell count fills most of rows*cols (a packed grid). A
# scatter cloud of marker glyphs instead spreads over many distinct x and y, so
# its cells fill only a few percent of the implied grid (verified ~0.04-0.09 on
# synthetic scatter vs 1.0 on a real heatmap).
_HEATMAP_MIN_CELLS = 16
_HEATMAP_MIN_LINES = 3       # at least this many rows AND columns
_HEATMAP_FILL = 0.6          # n_cells / (rows*cols) >= this -> packed grid

# Bar chart / histogram: several filled rectangles that are TALL (height clearly
# exceeds width, so they read as upright bars not marker glyphs), share a common
# bottom baseline, and ABUT into a contiguous run (each bar's width equals its
# centre-to-centre spacing to its neighbour). This abutting run is the decisive
# signal: verified adjacency ~1.0 on real histograms (2606.01408 p39, 2606.02190,
# 2606.01059) vs <=0.73 on diffraction-spike / error-bar line plots whose thin
# vertical marks are widely separated. Scatter/line marker glyphs are roughly
# square (not "tall"), so they never enter this test.
_BAR_MIN = 4               # at least this many bars on the shared baseline
_BAR_TALL_RATIO = 1.5      # a bar's height must exceed this * its width
_BAR_ADJACENCY = 0.85      # median bar width / median centre-spacing >= this

# 2D non-linear chart gates (contour/density/dispersion/credible-band):
#
# Gate A — UNIFORM DENSITY MAP (e.g. Bayesian posterior contour filled with
# many tiny same-colour gray dots). Signal: >=N small filled glyphs (bbox < 3pt)
# that all share one dominant grayscale colour, with NO wide open data line
# spanning >=0.5 of the panel width (which would indicate a real line chart
# whose markers happen to be identical, e.g. a noisy spectrum).
_DENSITY_MIN_GRAY_FILLS = 200  # minimum count of uniform-gray small fills
_DENSITY_DOM_FRAC = 0.90       # >= this fraction share one dominant color
_DENSITY_GLYPH_MAX = 3.0       # small glyph: bbox width/height < this (pt)
_DENSITY_GRAY_SPREAD = 0.02    # fill is grayscale if R≈G≈B within this spread
_DENSITY_LINE_MIN_PTS = 100    # open data line must have at least this many pts
_DENSITY_LINE_WIDTH_FRAC = 0.5 # … and span >= this fraction of panel width
_DENSITY_LINE_XDEC_MAX = 0.05  # … with x-decrease fraction < this (monotone)

# Gate B — DISPERSION LATTICE / EXTREME FILL DENSITY (e.g. band-structure
# electronic dispersion plot with thousands of tiny colored glyphs). Signal:
# fill density (fills per pt^2) far exceeds what a scatter series ever achieves.
# Verified: dispersion ~0.254 fills/pt^2, densest real scatter ~0.092 fills/pt^2.
_DISPERSION_DENSITY = 0.15     # fills/pt^2 threshold (well above real scatter)
_DISPERSION_GLYPH_MAX = 3.0    # same small-glyph size threshold

# Gate C — TALL CREDIBLE BAND (e.g. neutron-star M-R credible region spanning
# the full panel height as a single colored filled polygon). Signal: a single
# non-white colored filled polygon with no stroke whose height exceeds this
# fraction of the panel height. Verified: legitimate confidence-band line plots
# have fills reaching at most ~0.74 of panel height; credible-region fills reach
# ~1.1 (clipped beyond the panel boundary).
_BAND_MIN_VERTS = 20           # polygon must have at least this many vertices
_BAND_HEIGHT_FRAC = 0.85       # fill height / panel height >= this
_BAND_FILL_SPREAD = 0.10       # fill must be non-white (R,G,B spread > this)


def _is_white(color) -> bool:
    return color is not None and all(abs(c - 1.0) < 1e-3 for c in color)


def _area(b: BBox) -> float:
    return (b[2] - b[0]) * (b[3] - b[1])


def _is_rect_path(p: Path) -> bool:
    """A path whose flattened points trace an axis-aligned rectangle."""
    b = p.bbox
    if b[2] - b[0] < 1 or b[3] - b[1] < 1:
        return False
    # All points must lie on the bbox edges.
    for x, y in p.points:
        on_v = abs(x - b[0]) < _STRAIGHT_TOL or abs(x - b[2]) < _STRAIGHT_TOL
        on_h = abs(y - b[1]) < _STRAIGHT_TOL or abs(y - b[3]) < _STRAIGHT_TOL
        if not (on_v or on_h):
            return False
    return True


def _patch_regions(paths: list[Path], page_area: float) -> list[BBox]:
    """All white-filled, unstroked rectangles within the size band (one per panel)."""
    out: list[BBox] = []
    for p in paths:
        if not _is_white(p.fill) or p.stroke is not None:
            continue
        if not _is_rect_path(p):
            continue
        frac = _area(p.bbox) / page_area
        if frac < _MIN_AREA_FRAC or frac > _MAX_AREA_FRAC:
            continue
        out.append(p.bbox)
    return out


def _inner_patch_boxes(paths: list[Path], page_area: float) -> list[BBox]:
    """White axes-patch rectangles down to a smaller floor, for panel splitting.

    A multi-panel figure draws each panel's white axes-patch; in tightly packed
    figures a panel can fall just under ``_MIN_AREA_FRAC``. This collects all
    white-rect patches above a lower floor so the contain-and-split step can
    recover the individual panels of a figure whose outer frame would otherwise
    win as one wide region.
    """
    out: list[BBox] = []
    for p in paths:
        if not _is_white(p.fill) or p.stroke is not None or not _is_rect_path(p):
            continue
        frac = _area(p.bbox) / page_area
        if _SPLIT_MIN_AREA_FRAC <= frac <= _MAX_AREA_FRAC:
            out.append(p.bbox)
    return out


def _contains_box(outer: BBox, inner: BBox) -> bool:
    """True if ``inner`` lies essentially within ``outer`` (centroid + extent)."""
    cx = 0.5 * (inner[0] + inner[2])
    cy = 0.5 * (inner[1] + inner[3])
    return (outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]
            and inner[0] >= outer[0] - _ALIGN_TOL and inner[2] <= outer[2] + _ALIGN_TOL
            and inner[1] >= outer[1] - _ALIGN_TOL and inner[3] <= outer[3] + _ALIGN_TOL)


def _split_enclosing_frames(
    candidates: list[tuple[BBox, bool]],
    inner_patches: list[BBox],
    paths: list[Path],
    texts: list[TextSpan],
) -> list[tuple[BBox, bool]]:
    """Replace a frame that wraps >=2 calibratable inner panels with the panels.

    A whole-figure frame (merged spine across panels, or an outer figure-bg
    patch) can enclose several side-by-side / stacked panel patches. Keeping it
    concatenates the panels' data along one axis. So if a candidate strictly
    contains >=2 distinct calibratable inner panel patches, drop it and emit
    those inner patches instead.
    """
    calibratable = [
        b for b in inner_patches if _n_calibrated_axes(b, paths, texts) >= 1
    ]
    out: list[tuple[BBox, bool]] = []
    added: list[BBox] = []
    for bbox, is_patch in candidates:
        # Inner panels strictly smaller than this candidate and contained in it.
        inside = [b for b in calibratable
                  if _contains_box(bbox, b) and _area(b) <= _SPLIT_MAX_INNER_FRAC * _area(bbox)]
        # Distinct inner panels (don't count an inner patch twice via overlap).
        distinct: list[BBox] = []
        for b in inside:
            if not any(_same_panel(b, d) for d in distinct):
                distinct.append(b)
        if len(distinct) >= 2:
            for b in distinct:
                if not any(_same_panel(b, a) for a in added):
                    out.append((b, True))
                    added.append(b)
        else:
            out.append((bbox, is_patch))
    return out


def _rect_frame_regions(paths: list[Path], page_area: float) -> list[BBox]:
    """Axis-aligned rectangle paths in the size band, regardless of fill.

    Some toolchains draw the axes frame as a single *stroked* rectangle (no
    white fill), so the white-patch path misses it. This returns every
    rectangle-tracing path within the area band as a frame candidate; the
    chart-vs-not gate (saturated data marks + numeric ticks) keeps tables and
    other framed-but-not-chart blocks out, preserving precision.
    """
    out: list[BBox] = []
    for p in paths:
        if not _is_rect_path(p):
            continue
        frac = _area(p.bbox) / page_area
        if frac < _MIN_AREA_FRAC or frac > _MAX_AREA_FRAC:
            continue
        out.append(p.bbox)
    return out


def _spine_frame(paths: list[Path], width: float, height: float) -> BBox | None:
    """Bounding box of the long perpendicular spine lines, if found."""
    h_lines = []  # horizontal spines
    v_lines = []  # vertical spines
    for p in paths:
        b = p.bbox
        dx, dy = b[2] - b[0], b[3] - b[1]
        if dy < _STRAIGHT_TOL and dx >= _MIN_SPINE_FRAC * width:
            h_lines.append(b)
        elif dx < _STRAIGHT_TOL and dy >= _MIN_SPINE_FRAC * height:
            v_lines.append(b)
    if not h_lines or not v_lines:
        return None
    x0 = min(b[0] for b in v_lines)
    x1 = max(b[2] for b in v_lines)
    y0 = min(b[1] for b in h_lines)
    y1 = max(b[3] for b in h_lines)
    # Widen to the horizontal extent of the bottom/top spines too.
    x0 = min(x0, min(b[0] for b in h_lines))
    x1 = max(x1, max(b[2] for b in h_lines))
    y0 = min(y0, min(b[1] for b in v_lines))
    y1 = max(y1, max(b[3] for b in v_lines))
    return (x0, y0, x1, y1)


def _merged_spine_frames(paths: list[Path], width: float, height: float) -> list[BBox]:
    """Frames recovered from stacked collinear short segments (pgfplots etc.).

    A plot frame whose spines are drawn as many short segments shows up as a
    single x with a tall union of vertical segments (left/right edge) and a
    single y with a wide union of horizontal segments (top/bottom edge). We pair
    a vertical edge with a horizontal edge that meets it at a corner and take
    their combined extent as the frame box.

    Panel-merge fix: two adjacent sub-panels can share a long horizontal or
    vertical frame spine, causing the algorithm to union them into one wide
    (or tall) region.  Two guards prevent this:

    1. *Coverage filter* – tick marks at the ends of an axis contribute two
       tiny segments whose total length is only ~3-5% of the edge span.  Real
       frame spines cover >= 40% of their span.  Tick-mark edges are dropped.

    2. *V-edge gap split* – when a vertical spine has a gap (> _SPINE_GAP_MIN)
       in its segments, two panels are stacked vertically; the spine is split
       into per-panel sub-edges so each sub-panel forms its own frame.

    3. *Segment-right pairing* – when building a frame from a left vertical
       edge and a bottom horizontal edge, the frame's right boundary is the
       right end of the H-edge *segment* that starts near the left V edge,
       rather than the rightmost extent of all H-edge segments.  This prevents
       a shared horizontal spine that spans several side-by-side sub-panels
       from merging them into one wide frame.
    """
    v_by_x: dict[int, list[tuple[float, float]]] = defaultdict(list)
    h_by_y: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for p in paths:
        b = p.bbox
        dx, dy = b[2] - b[0], b[3] - b[1]
        if dx < _SEG_THIN and dy >= _SEG_MIN_LEN:
            v_by_x[round(b[0])].append((b[1], b[3]))
        elif dy < _SEG_THIN and dx >= _SEG_MIN_LEN:
            h_by_y[round(b[1])].append((b[0], b[2]))

    def _coverage(segs: list[tuple[float, float]]) -> float:
        """Ratio of total segment length to the overall span (0-1, >1 if segs overlap)."""
        span = max(e for _, e in segs) - min(s for s, _ in segs)
        if span <= 0:
            return 0.0
        return sum(e - s for s, e in segs) / span

    def _sub_edges(
        segs: list[tuple[float, float]], min_span: float
    ) -> list[tuple[float, float]]:
        """Split segment list at gaps > _SPINE_GAP_MIN; return (start, end) per run."""
        merged: list[list[float]] = []
        for s, e in sorted(segs):
            if merged and s <= merged[-1][1] + _SPINE_GAP_MIN:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])
        return [(r[0], r[1]) for r in merged if r[1] - r[0] >= min_span]

    min_v_span = _SPINE_SPAN_FRAC * height
    min_h_span = _SPINE_SPAN_FRAC * width

    # Build V edges: coverage-filtered and gap-split into sub-edges.
    # When the raw segments have low coverage (e.g. only tick-mark endpoints at
    # the top and bottom of an axis), fall back to the full union span so that
    # axis-boundary frames are still detected (they will be uncalibratable and
    # skipped, preserving chart numbering without emitting bad data).
    v_edges: list[tuple[int, float, float]] = []  # (x, vy0, vy1)
    for x, raw_segs in v_by_x.items():
        if _coverage(raw_segs) < _SPINE_COVERAGE_MIN:
            # Low-coverage V: emit one full-span edge from the union of all segs.
            vy0 = min(s for s, _ in raw_segs)
            vy1 = max(e for _, e in raw_segs)
            if vy1 - vy0 >= min_v_span:
                v_edges.append((x, vy0, vy1))
        else:
            for vy0, vy1 in _sub_edges(raw_segs, min_v_span):
                v_edges.append((x, vy0, vy1))

    # Build H edges as individual segments (not unioned spans) so that when
    # multiple sub-panels share a horizontal spine, each segment pairs only
    # with its own left V edge.  Each segment must be wide enough to clear the
    # minimum frame-side requirement.
    h_segs: list[tuple[int, float, float]] = []  # (y, seg_x0, seg_x1)
    for y, raw_segs in h_by_y.items():
        if _coverage(raw_segs) < _SPINE_COVERAGE_MIN:
            continue
        # Merge overlapping raw segments into contiguous runs, then emit each
        # run as a separate H segment.
        for hx0, hx1 in _sub_edges(raw_segs, min_h_span):
            h_segs.append((y, hx0, hx1))

    frames: list[BBox] = []
    for vx, vy0, vy1 in v_edges:
        for hy, hx0, hx1 in h_segs:
            # Left vertical edge meeting bottom horizontal edge at a corner.
            if abs(vx - hx0) > _CORNER_TOL or abs(hy - vy1) > _CORNER_TOL:
                continue
            x0, y0, x1, y1 = vx, vy0, hx1, hy
            if x1 - x0 < _FRAME_MIN_SIDE or y1 - y0 < _FRAME_MIN_SIDE:
                continue
            frames.append((x0, y0, x1, y1))

    return frames


def _overlaps(a: BBox, b: BBox) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def _inter_area(a: BBox, b: BBox) -> float:
    iw = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    ih = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return iw * ih


def _same_panel(a: BBox, b: BBox) -> bool:
    """Do two candidates describe the same panel (strong overlap)?"""
    inter = _inter_area(a, b)
    if inter <= 0:
        return False
    aa, ab = _area(a), _area(b)
    union = aa + ab - inter
    iou = inter / union if union > 0 else 0.0
    contain = inter / min(aa, ab) if min(aa, ab) > 0 else 0.0
    return iou >= _DEDUP_IOU or contain >= _DEDUP_CONTAIN


def _is_numeric_label(text: str) -> bool:
    s = text.strip().replace("−", "-")  # unicode minus
    return bool(_NUMERIC.match(s)) or s == "10"  # bare "10" precedes a log exp


def _has_chart_content(bbox: BBox, paths: list[Path]) -> bool:
    x0, y0, x1, y1 = bbox
    sat = poly = 0
    for p in paths:
        cx = 0.5 * (p.bbox[0] + p.bbox[2])
        cy = 0.5 * (p.bbox[1] + p.bbox[3])
        if not (x0 <= cx <= x1 and y0 <= cy <= y1):
            continue
        if _is_saturated(p.stroke) or _is_saturated(p.fill):
            sat += 1
        if len(p.points) > _POLYLINE_VERTS:
            poly += 1
    return sat >= _MIN_SATURATED or poly >= _MIN_POLYLINES


def _has_data_strokes(bbox: BBox, paths: list[Path]) -> bool:
    """Strict content check: enough saturated-colour *stroked* data paths."""
    x0, y0, x1, y1 = bbox
    n = 0
    for p in paths:
        cx = 0.5 * (p.bbox[0] + p.bbox[2])
        cy = 0.5 * (p.bbox[1] + p.bbox[3])
        if x0 <= cx <= x1 and y0 <= cy <= y1 and _is_saturated(p.stroke):
            n += 1
    return n >= _MIN_SAT_STROKE


def _num_tick_labels(bbox: BBox, texts: list[TextSpan]) -> int:
    """Numeric labels along the bottom (x) or left (y) edge of the candidate.

    Labels may sit just *outside* the frame (stroked-spine charts) or just
    *inside* it (the white axes-patch includes the tick-label margin), so both
    bands are scanned. Distinct labels are counted per edge.
    """
    x0, y0, x1, y1 = bbox
    n = 0
    for t in texts:
        if not _is_numeric_label(t.text):
            continue
        cx = 0.5 * (t.bbox[0] + t.bbox[2])
        cy = 0.5 * (t.bbox[1] + t.bbox[3])
        below = (x0 - 2 <= cx <= x1 + 2
                 and y1 - _TICK_BELOW <= cy <= y1 + _TICK_BELOW)
        left = (y0 - 2 <= cy <= y1 + 2
                and x0 - _TICK_LEFT <= cx <= x0 + _TICK_LEFT)
        if below or left:
            n += 1
    return n


def _is_chart(bbox: BBox, paths: list[Path], texts: list[TextSpan]) -> bool:
    """Chart-vs-not gate: needs data content AND numeric tick labels."""
    return (_has_chart_content(bbox, paths)
            and _num_tick_labels(bbox, texts) >= _MIN_NUM_TICKS)


def _contained(bbox: BBox, item_bbox: BBox) -> bool:
    cx = 0.5 * (item_bbox[0] + item_bbox[2])
    cy = 0.5 * (item_bbox[1] + item_bbox[3])
    return bbox[0] <= cx <= bbox[2] and bbox[1] <= cy <= bbox[3]


def _cluster_coords(values: list[float]) -> list[float]:
    """Group near-equal coordinates (within _ALIGN_TOL) and return their centers."""
    centers: list[float] = []
    for v in sorted(values):
        if centers and abs(v - centers[-1]) <= _ALIGN_TOL:
            continue
        centers.append(v)
    return centers


def _assign_grid(boxes: list[BBox]) -> tuple[list[int], list[int]]:
    """Assign a (row, col) to each bbox by clustering top edges / left edges."""
    row_centers = _cluster_coords([b[1] for b in boxes])
    col_centers = _cluster_coords([b[0] for b in boxes])

    def nearest(v: float, centers: list[float]) -> int:
        return min(range(len(centers)), key=lambda i: abs(v - centers[i]))

    rows = [nearest(b[1], row_centers) for b in boxes]
    cols = [nearest(b[0], col_centers) for b in boxes]
    return rows, cols


def _n_calibrated_axes(bbox: BBox, paths: list[Path], texts: list[TextSpan]) -> int:
    """How many of the two axes calibrate for this exact bbox (0, 1 or 2).

    The true plot region has tick marks/labels right at its edges and so
    calibrates; an enclosing figure-background patch (whose ticks sit well
    inside it) does not. This is the primary tie-breaker between same-panel
    candidates.
    """
    x_axis, y_axis = calibrate_region(Region(bbox=bbox), paths, texts)
    return (x_axis.calibration is not None) + (y_axis.calibration is not None)


def _dedup_candidates(
    candidates: list[tuple[BBox, bool]],
    paths: list[Path],
    texts: list[TextSpan],
) -> list[BBox]:
    """NMS over same-panel candidates; keep the best one per panel.

    Each candidate is ``(bbox, is_patch)`` where ``is_patch`` marks a true white
    axes-patch. Preference, highest first: more calibratable axes, then white
    axes-patch, then larger area. Non-overlapping candidates all survive
    (separate subplot panels are preserved).
    """
    scored = [
        (bbox, (_n_calibrated_axes(bbox, paths, texts), is_patch, _area(bbox)))
        for bbox, is_patch in candidates
    ]
    # Best candidates first; greedily suppress later same-panel duplicates.
    scored.sort(key=lambda c: c[1], reverse=True)
    kept: list[BBox] = []
    for bbox, _score in scored:
        if any(_same_panel(bbox, k) for k in kept):
            continue
        kept.append(bbox)
    return kept


def _split_multi_row_boxes(
    boxes: list[BBox],
    paths: list[Path],
    texts: list[TextSpan],
    page_area: float,
) -> list[BBox]:
    """Split a merged box that contains >=2 y-row groups of calibratable inner panels.

    After deduplication, a merged-spine box may still span multiple stacked
    sub-panels when the individual panel patches were too small to be collected
    by ``_inner_patch_boxes`` (i.e. their area fraction falls below
    ``_SPLIT_MIN_AREA_FRAC``).  This pass recovers those panels by scanning
    all white unstroked rectangle patches contained within each box.

    Guard: at least 2 of the distinct inner patches must be independently
    calibratable (``_n_calibrated_axes >= 1``).  If fewer are calibratable the
    outer box is kept intact.  This prevents over-splitting on pages whose
    large outer frame encloses non-calibratable phantom sub-patches (e.g. inset
    zoom boxes, schematic overlays).
    """
    out: list[BBox] = []
    already: list[BBox] = []

    for box in boxes:
        box_area = _area(box)

        # Collect all white unstroked rect patches strictly inside ``box``.
        inner: list[BBox] = []
        for p in paths:
            if p.fill != (1.0, 1.0, 1.0) or p.stroke is not None:
                continue
            if not _is_rect_path(p):
                continue
            b = p.bbox
            if not _contains_box(box, b):
                continue
            if _area(b) >= _SPLIT_MAX_INNER_FRAC * box_area:
                continue
            inner.append(b)

        # Deduplicate inner patches (e.g. two signals for the same sub-panel).
        distinct: list[BBox] = []
        for b in inner:
            if not any(_same_panel(b, d) for d in distinct):
                distinct.append(b)

        # Need at least 2 distinct patches in >= 2 y-rows.
        if len(distinct) < 2:
            out.append(box)
            continue

        y_rows = _cluster_coords([b[1] for b in distinct])
        if len(y_rows) < 2:
            out.append(box)
            continue

        # Guard: require >= 2 independently calibratable inner patches.
        n_cal = sum(1 for b in distinct if _n_calibrated_axes(b, paths, texts) >= 1)
        if n_cal < 2:
            out.append(box)
            continue

        # Split: emit each distinct inner patch (avoiding duplicates across boxes).
        for b in distinct:
            if not any(_same_panel(b, a) for a in already):
                out.append(b)
                already.append(b)

    return out


def _split_multi_col_boxes(
    boxes: list[BBox],
    paths: list[Path],
    texts: list[TextSpan],
    page_area: float,
) -> list[BBox]:
    """Split a merged box that contains >=2 x-column groups of calibratable inner panels.

    Complement to ``_split_multi_row_boxes`` for the side-by-side layout: when
    a merged-spine frame spans multiple adjacent sub-panels in the same row (all
    at the same y position, different x positions), the row split can't fire
    because ``len(y_rows) < 2``.  This pass handles that case.

    A common occurrence is a pgfplots or TikZ figure whose two side-by-side
    panels share a horizontal spine drawn with a very small inter-panel gap
    (< _SPINE_GAP_MIN); the merged H-segment then yields one wide frame.  If
    each sub-panel has its own white axes-patch, those patches are collected here
    and the wide frame is replaced by its two (or more) column-separated panels.

    The same calibratability guard as ``_split_multi_row_boxes`` applies: at
    least 2 inner patches must independently calibrate on at least one axis.
    """
    out: list[BBox] = []
    already: list[BBox] = []

    for box in boxes:
        box_area = _area(box)

        # Collect all white unstroked rect patches strictly inside ``box``.
        inner: list[BBox] = []
        for p in paths:
            if p.fill != (1.0, 1.0, 1.0) or p.stroke is not None:
                continue
            if not _is_rect_path(p):
                continue
            b = p.bbox
            if not _contains_box(box, b):
                continue
            if _area(b) >= _SPLIT_MAX_INNER_FRAC * box_area:
                continue
            inner.append(b)

        # Deduplicate inner patches.
        distinct: list[BBox] = []
        for b in inner:
            if not any(_same_panel(b, d) for d in distinct):
                distinct.append(b)

        # Need at least 2 distinct patches in >= 2 x-columns.
        if len(distinct) < 2:
            out.append(box)
            continue

        x_cols = _cluster_coords([b[0] for b in distinct])
        if len(x_cols) < 2:
            out.append(box)
            continue

        # Guard: require >= 2 independently calibratable inner patches.
        n_cal = sum(1 for b in distinct if _n_calibrated_axes(b, paths, texts) >= 1)
        if n_cal < 2:
            out.append(box)
            continue

        # Split: emit each distinct inner patch (avoiding duplicates across boxes).
        for b in distinct:
            if not any(_same_panel(b, a) for a in already):
                out.append(b)
                already.append(b)

    return out


def _filled_cells(bbox: BBox, paths: list[Path]) -> list[BBox]:
    """Filled, axis-aligned rectangle-like paths inside ``bbox`` (heatmap cells
    or scatter marker glyphs; both are short filled paths)."""
    x0, y0, x1, y1 = bbox
    out: list[BBox] = []
    for p in paths:
        if p.fill is None or len(p.points) > 6:
            continue
        b = p.bbox
        if b[2] - b[0] < 1 or b[3] - b[1] < 1:
            continue
        cx, cy = 0.5 * (b[0] + b[2]), 0.5 * (b[1] + b[3])
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            out.append(b)
    return out


def _is_heatmap(bbox: BBox, paths: list[Path]) -> bool:
    """A dense grid of filled cells tiling the panel (heatmap / imshow)."""
    cells = _filled_cells(bbox, paths)
    if len(cells) < _HEATMAP_MIN_CELLS:
        return False
    cols = _cluster_coords(sorted(b[0] for b in cells))
    rows = _cluster_coords(sorted(b[1] for b in cells))
    if len(cols) < _HEATMAP_MIN_LINES or len(rows) < _HEATMAP_MIN_LINES:
        return False
    grid = len(cols) * len(rows)
    return grid > 0 and len(cells) / grid >= _HEATMAP_FILL


def _is_bar_chart(bbox: BBox, paths: list[Path]) -> bool:
    """A bar chart / histogram: a contiguous run of bottom-aligned upright bars.

    Collect filled, axis-aligned rectangles inside the panel (excluding the
    near-panel-sized axes frame itself), keep the *tall* ones (height clearly
    exceeds width, so they are upright bars and not the roughly-square marker
    glyphs of a scatter/line plot), restrict to the most populated bottom
    baseline, and check that those bars ABUT: their median width equals their
    median centre-to-centre spacing. Touching bars are the histogram/bar
    signature; thin, widely spaced vertical marks (diffraction spikes, error
    bars) of a real line/scatter plot fail the adjacency test and are kept.
    """
    x0, y0, x1, y1 = bbox
    rw, rh = x1 - x0, y1 - y0
    bars: list[BBox] = []
    for p in paths:
        if p.fill is None or not _is_rect_path(p):
            continue
        b = p.bbox
        w, h = b[2] - b[0], b[3] - b[1]
        if w < 1 or h < 1:
            continue
        # Skip the axes frame / background patch (spans most of the panel).
        if w > 0.8 * rw and h > 0.8 * rh:
            continue
        cx, cy = 0.5 * (b[0] + b[2]), 0.5 * (b[1] + b[3])
        if not (x0 <= cx <= x1 and y0 <= cy <= y1):
            continue
        if h > _BAR_TALL_RATIO * w:  # upright bar, not a square marker
            bars.append(b)
    if len(bars) < _BAR_MIN:
        return False

    # Restrict to the single most-populated bottom baseline (bars rest on it).
    base_counts: dict[int, int] = defaultdict(int)
    for b in bars:
        base_counts[round(b[3])] += 1
    baseline = max(base_counts, key=base_counts.get)
    on_base = sorted((b for b in bars if round(b[3]) == baseline), key=lambda b: b[0])
    if len(on_base) < _BAR_MIN:
        return False

    widths = sorted(b[2] - b[0] for b in on_base)
    med_w = widths[len(widths) // 2]
    centers = [0.5 * (b[0] + b[2]) for b in on_base]
    gaps = sorted(centers[i + 1] - centers[i] for i in range(len(centers) - 1))
    if not gaps:
        return False
    med_gap = gaps[len(gaps) // 2]
    if med_gap <= 0:
        return False
    return med_w / med_gap >= _BAR_ADJACENCY


def _has_open_data_line(bbox: BBox, paths: list[Path]) -> bool:
    """True if the region contains a wide, x-monotone stroked polyline.

    A 'real' data line (e.g. a noisy spectrum, a shock-tube profile) spans
    most of the panel width and marches left-to-right without reversing in x.
    Contour isolines and closed-loop curves do reverse in x (they form loops
    or arcs) and therefore fail this check even when they happen to span the
    panel width. Used to exclude regions whose tiny same-colour markers are
    actually data (line+marker style) rather than a density map background.
    """
    x0, y0, x1, y1 = bbox
    rw = x1 - x0
    if rw <= 0:
        return False
    for p in paths:
        if p.stroke is None or p.fill is not None:
            continue
        if len(p.points) < _DENSITY_LINE_MIN_PTS:
            continue
        cx = 0.5 * (p.bbox[0] + p.bbox[2])
        cy = 0.5 * (p.bbox[1] + p.bbox[3])
        if not (x0 <= cx <= x1 and y0 <= cy <= y1):
            continue
        if (p.bbox[2] - p.bbox[0]) < _DENSITY_LINE_WIDTH_FRAC * rw:
            continue
        # x-decrease fraction: for a left-to-right open line this is ~0;
        # for a closed contour loop it is >> 0 (x reverses many times).
        xs = [pt[0] for pt in p.points]
        n_steps = len(xs) - 1
        if n_steps <= 0:
            continue
        x_dec = sum(1 for i in range(n_steps) if xs[i + 1] < xs[i]) / n_steps
        if x_dec <= _DENSITY_LINE_XDEC_MAX:
            return True
    return False


def _is_uniform_density_map(bbox: BBox, paths: list[Path]) -> bool:
    """A 2D density map rendered as many tiny same-colour grayscale glyphs.

    Bayesian posterior contour maps (e.g. corner plots) shade parameter-space
    density with hundreds of small gray marker glyphs all drawn in the same
    shade of gray. This is fundamentally different from a line/scatter chart
    where identical same-colour markers trace a *functional* y=f(x) curve.
    The distinguishing signals are:
      (a) >= _DENSITY_MIN_GRAY_FILLS tiny (< 3pt) grayscale-filled paths;
      (b) >= _DENSITY_DOM_FRAC share one dominant gray shade (the density
          is encoded by opacity/count, not by varying hue);
      (c) no wide, x-monotone stroked data line (which would indicate a
          genuine line+marker chart, e.g. a dense spectrum trace).
    """
    x0, y0, x1, y1 = bbox
    small_gray: list[Path] = []
    for p in paths:
        if p.fill is None:
            continue
        b = p.bbox
        if b[2] - b[0] >= _DENSITY_GLYPH_MAX or b[3] - b[1] >= _DENSITY_GLYPH_MAX:
            continue
        if max(p.fill) - min(p.fill) >= _DENSITY_GRAY_SPREAD:
            continue  # coloured fill -> series marker, not density glyph
        cx, cy = 0.5 * (b[0] + b[2]), 0.5 * (b[1] + b[3])
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            small_gray.append(p)
    if len(small_gray) < _DENSITY_MIN_GRAY_FILLS:
        return False
    # Dominant colour: >= _DENSITY_DOM_FRAC must share the same rounded shade.
    counts: dict[tuple, int] = {}
    for p in small_gray:
        key = tuple(round(v, 1) for v in p.fill)  # type: ignore[arg-type]
        counts[key] = counts.get(key, 0) + 1
    dom = max(counts.values())
    if dom / len(small_gray) < _DENSITY_DOM_FRAC:
        return False
    # If there is also a real open data line, keep the region (line+marker chart).
    return not _has_open_data_line(bbox, paths)


def _is_dense_fill_lattice(bbox: BBox, paths: list[Path]) -> bool:
    """A dispersion lattice / density field: far too many tiny fills per unit area.

    Electronic band-structure dispersion plots render thousands of tiny glyphs
    (one per k-point) at a fill density that real scatter series never reach.
    Verified: dispersion ~0.254 fills/pt^2, densest legitimate scatter ~0.09.
    The threshold _DISPERSION_DENSITY = 0.15 sits well between these values.
    """
    x0, y0, x1, y1 = bbox
    area = (x1 - x0) * (y1 - y0)
    if area <= 0:
        return False
    n = 0
    for p in paths:
        if p.fill is None:
            continue
        b = p.bbox
        if b[2] - b[0] >= _DISPERSION_GLYPH_MAX or b[3] - b[1] >= _DISPERSION_GLYPH_MAX:
            continue
        cx, cy = 0.5 * (b[0] + b[2]), 0.5 * (b[1] + b[3])
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            n += 1
    return n / area >= _DISPERSION_DENSITY


def _has_tall_fill_band(bbox: BBox, paths: list[Path]) -> bool:
    """A single tall colored filled polygon spanning most of the panel height.

    Neutron-star M-R credible-region charts and similar Bayesian parameter-space
    plots draw one (or a few) large filled polygons that span almost the full
    panel height as a credible band. Legitimate confidence-band line charts draw
    shaded error bands that cover at most ~0.75 of the panel height; the
    credible-region fill reaches ~1.1 (extends beyond the clipped panel boundary).
    The threshold _BAND_HEIGHT_FRAC = 0.85 safely separates the two cases.
    """
    x0, y0, x1, y1 = bbox
    rh = y1 - y0
    if rh <= 0:
        return False
    for p in paths:
        if p.fill is None or p.stroke is not None:
            continue  # must be fill-only (no outline)
        if len(p.points) < _BAND_MIN_VERTS:
            continue
        if max(p.fill) - min(p.fill) <= _BAND_FILL_SPREAD:
            continue  # white or near-gray background rect -> skip
        b = p.bbox
        cx, cy = 0.5 * (b[0] + b[2]), 0.5 * (b[1] + b[3])
        if not (x0 <= cx <= x1 and y0 <= cy <= y1):
            continue
        if (b[3] - b[1]) >= _BAND_HEIGHT_FRAC * rh:
            return True
    return False


def _is_chart_type(bbox: BBox, paths: list[Path]) -> bool:
    """Reject regions that are clearly not line/scatter charts (precision gate).

    Gates dense filled-cell grids (heatmap / imshow) and bar charts /
    histograms, which pass the content+ticks gate but are not line/scatter
    charts and would emit garbage. These are silently dropped (no skip stub).

    2D non-linear chart types (contour/density maps, dispersion lattices,
    credible bands) are handled separately by ``_2d_map_skip_reason``, which
    returns a human-readable reason so the caller can emit a skip stub rather
    than silently discarding the region.
    """
    return not _is_heatmap(bbox, paths) and not _is_bar_chart(bbox, paths)


def _2d_map_skip_reason(bbox: BBox, paths: list[Path]) -> str | None:
    """Return a skip reason string if the region is a 2D non-linear chart type.

    Detects three families that are NOT line/scatter charts and would emit junk
    data if extracted:

    * **Uniform density map** (Bayesian posterior contour map): hundreds of
      tiny same-colour grayscale filled glyphs with no open data line.
    * **Dispersion lattice** (band-structure plot): extreme fill density
      (fills/pt^2) far exceeding any real scatter series.
    * **Tall credible band** (M-R or parameter-space credible region): a
      single large colored filled polygon spanning the full panel height.

    Returns ``None`` when none of the three signals fires (region is a normal
    line/scatter chart and must be kept).
    """
    if _is_uniform_density_map(bbox, paths):
        return "2d density/contour map: uniform grayscale fill cloud"
    if _is_dense_fill_lattice(bbox, paths):
        return "dispersion lattice: fill density too high for a scatter series"
    if _has_tall_fill_band(bbox, paths):
        return "2d credible-band/contour region: tall colored fill spans panel height"
    return None


def _covered_by_image(bbox: BBox, image_rects, frac: float = 0.55) -> bool:
    """True if a raster image covers >= ``frac`` of the candidate region.

    A region that is mostly an embedded image with vector markers drawn on top
    (a photo / micrograph / sky map with annotation points) is NOT a vector
    chart and must be rejected.
    """
    if not image_rects:
        return False
    bx0, by0, bx1, by1 = bbox
    area = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    if area <= 0:
        return False
    for ix0, iy0, ix1, iy1 in image_rects:
        ox = max(0.0, min(bx1, ix1) - max(bx0, ix0))
        oy = max(0.0, min(by1, iy1) - max(by0, iy0))
        if ox * oy >= frac * area:
            return True
    return False


def detect_regions(
    paths: list[Path],
    texts: list[TextSpan],
    page_width: float,
    page_height: float,
    image_rects: list[BBox] | None = None,
) -> list[Region]:
    """Detect chart plotting region(s) on a page. Returns one Region per panel.

    Candidate boxes come from the white axes-patch (one per subplot panel) or,
    when no patch exists, the long-spine "L" / merged-spine frame. Every
    candidate must pass the chart-vs-not gate (``_is_chart``). Regions mostly
    covered by an embedded raster image (markers-on-a-photo) are rejected.
    """
    page_area = page_width * page_height
    # Collect gated candidates tagged by whether they are a true white
    # axes-patch (preferred when merging same-panel overlaps).
    candidates: list[tuple[BBox, bool]] = []

    patch_boxes = _patch_regions(paths, page_area)
    if not patch_boxes:
        frame = _spine_frame(paths, page_width, page_height)
        patch_boxes = [frame] if frame is not None else []
    for b in patch_boxes:
        if _is_chart(b, paths, texts):
            candidates.append((b, True))

    # Merged-spine frames (pgfplots / small panels).
    for b in _merged_spine_frames(paths, page_width, page_height):
        if _is_chart(b, paths, texts):
            candidates.append((b, False))

    # Stroked/unfilled rectangle frames (single-rect axes drawn without a white
    # patch). These use a STRICTER content gate (saturated data strokes, not
    # bare polylines or box fills) because a raw rectangle also bounds schematic
    # diagrams, whose connector polylines would otherwise pass.
    for b in _rect_frame_regions(paths, page_area):
        if _has_data_strokes(b, paths) and _num_tick_labels(b, texts) >= _MIN_NUM_TICKS:
            candidates.append((b, False))

    if not candidates:
        return []

    # Split any frame that encloses >=2 calibratable inner panel patches into
    # those panels (a whole-figure frame would otherwise concatenate panels).
    inner_patches = _inner_patch_boxes(paths, page_area)
    candidates = _split_enclosing_frames(candidates, inner_patches, paths, texts)

    # Deduplicate candidates that describe the same panel (figure-background
    # patch + inner axes patch, merged spine, stroked frame); keep one each.
    boxes = _dedup_candidates(candidates, paths, texts)

    # Split any remaining merged box that spans >=2 y-row groups of inner
    # panel patches, provided >=2 of those patches are independently
    # calibratable.  This recovers stacked sub-panels whose individual patch
    # areas fall below the _SPLIT_MIN_AREA_FRAC floor used by
    # _split_enclosing_frames, while the calibratability guard prevents
    # over-splitting on pages whose frame encloses non-calibratable insets.
    boxes = _split_multi_row_boxes(boxes, paths, texts, page_area)

    # Complement to the row split: handle the side-by-side layout where a
    # merged frame spans >=2 adjacent sub-panels in the same row (scatter panel
    # beside violin/box distribution panel, or adjacent benchmarking panels).
    # Fires only when inner patches land in >= 2 distinct x-columns AND >= 2
    # independently calibrate; same guard prevents over-splitting on insets.
    boxes = _split_multi_col_boxes(boxes, paths, texts, page_area)

    # Chart-type gate (part 1): silently drop heatmaps and bar charts.
    # These pass the content+ticks gate but are not line/scatter charts and
    # would emit garbage. Precision over recall; no skip stub is written.
    boxes = [b for b in boxes if _is_chart_type(b, paths)]
    # Reject regions that are mostly an embedded raster image (markers-on-a-photo
    # / micrograph / sky map) rather than a vector chart.
    boxes = [b for b in boxes if not _covered_by_image(b, image_rects)]
    if not boxes:
        return []

    # Sort row-major. PDF y increases downward (top-left origin), so smaller y0
    # is higher on the page; ties broken left-to-right by x0.
    boxes.sort(key=lambda b: (round(b[1]), b[0]))
    rows, cols = _assign_grid(boxes)

    regions: list[Region] = []
    for bbox, r, c in zip(boxes, rows, cols):
        # Chart-type gate (part 2): 2D non-linear chart types (contour/density
        # maps, dispersion lattices, credible bands) are returned as regions
        # with skip_reason set so the caller can emit a skip stub for them,
        # rather than silently discarding them. This preserves the page-level
        # chart count (skip files document why each detected region was not
        # extracted) while still preventing junk extraction.
        skip = _2d_map_skip_reason(bbox, paths)
        regions.append(Region(
            bbox=bbox,
            path_indices=[i for i, p in enumerate(paths)
                          if _contained(bbox, p.bbox)],
            text_indices=[i for i, t in enumerate(texts)
                          if _contained(bbox, t.bbox)],
            row=r,
            col=c,
            skip_reason=skip,
        ))

    # Shared-axis grouping: panels in the same column share x; same row share y.
    if len(regions) > 1:
        for i, ri in enumerate(regions):
            ri.shares_x_with = [j for j, rj in enumerate(regions)
                                if j != i and rj.col == ri.col]
            ri.shares_y_with = [j for j, rj in enumerate(regions)
                                if j != i and rj.row == ri.row]

    return regions
