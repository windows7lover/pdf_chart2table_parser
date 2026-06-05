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
# A path colour is "saturated" (a data series) if its RGB spread exceeds this.
_SAT_SPREAD = 0.2
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

    v_edges = [(x, min(s for s, _ in segs), max(e for _, e in segs))
               for x, segs in v_by_x.items()]
    v_edges = [e for e in v_edges if e[2] - e[1] >= _SPINE_SPAN_FRAC * height]
    h_edges = [(y, min(s for s, _ in segs), max(e for _, e in segs))
               for y, segs in h_by_y.items()]
    h_edges = [e for e in h_edges if e[2] - e[1] >= _SPINE_SPAN_FRAC * width]

    frames: list[BBox] = []
    for vx, vy0, vy1 in v_edges:
        for hy, hx0, hx1 in h_edges:
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


def _is_saturated(color) -> bool:
    return color is not None and (max(color) - min(color)) > _SAT_SPREAD


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


def _is_chart_type(bbox: BBox, paths: list[Path]) -> bool:
    """Reject regions that are clearly not line/scatter charts (precision gate).

    Gates dense filled-cell grids (heatmap / imshow) and bar charts / histograms
    (a contiguous run of bottom-aligned upright bars). Contour/KDE, boxplots and
    pie charts are intentionally NOT gated: their available vector primitives do
    not separate them from legitimate confidence-band line plots and error-bar
    charts, so a gate would drop real charts (precision loss the wrong way).
    """
    return not _is_heatmap(bbox, paths) and not _is_bar_chart(bbox, paths)


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

    # Chart-type gate: drop regions that are heatmaps or bar charts / histograms,
    # which pass the content+ticks gate but are not line/scatter charts and would
    # emit garbage (a cell grid or a few bar tops). Precision over recall.
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
        regions.append(Region(
            bbox=bbox,
            path_indices=[i for i, p in enumerate(paths)
                          if _contained(bbox, p.bbox)],
            text_indices=[i for i, t in enumerate(texts)
                          if _contained(bbox, t.bbox)],
            row=r,
            col=c,
        ))

    # Shared-axis grouping: panels in the same column share x; same row share y.
    if len(regions) > 1:
        for i, ri in enumerate(regions):
            ri.shares_x_with = [j for j, rj in enumerate(regions)
                                if j != i and rj.col == ri.col]
            ri.shares_y_with = [j for j, rj in enumerate(regions)
                                if j != i and rj.row == ri.row]

    return regions
