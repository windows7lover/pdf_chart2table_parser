"""Extract a chart's textual context: title, axis titles, legend, caption.

Given a detected ``Region`` plus the page's paths and texts, this module pulls
out the words around the plot (not the numeric tick labels, which ``axes.py``
handles). Geometry is in PDF points (y increases downward).

Layout assumptions (matplotlib/pgfplots conventions, confirmed on fixtures):
  - the region bbox is the plotting area (often the white axes-patch, which can
    include the tick-label margin);
  - the **plot title** is horizontal text just above the region's top edge,
    centred over the plot;
  - the **x-axis title** is horizontal text below the bottom edge, below the
    numeric tick labels;
  - the **y-axis title** is text to the left of the left edge with a vertical
    writing direction (``TextSpan.dir`` ~ (0, +-1));
  - a **legend** is a cluster of small swatch paths (a short line and/or a small
    marker) each immediately left of a text label;
  - the **caption** is the nearest "Figure N"/"Fig. N" paragraph, normally below
    the figure (fallback above).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .model import BBox, Color, Path, Region, TextSpan
from .primitives import bbox_center as _bbox_center, join_scripts as _join_scripts

# Numeric tick label (to exclude from title detection).
_NUMERIC = re.compile(r"^[-+]?\d*\.?\d+$")
# Caption opener: "Figure 3", "Fig. 3", "Figure 3:" ...
_CAPTION_RE = re.compile(r"^\s*(figure|fig\.?)\s*\d", re.IGNORECASE)
# Marker-shape proxy strings emitted by Type3/glyph-path fonts instead of real
# label text. These are the matplotlib marker codes that appear verbatim when the
# PDF uses glyph-path rendering for legend text (e.g. circle -> 'o').
_MARKER_PROXIES: frozenset[str] = frozenset({"o", "s", "^", "v", "D", "*", "+", "x"})

# How far above the top edge to look for the plot title (points).
_TITLE_ABOVE = 30.0
# How far below the bottom edge to look for the x-axis title (points).
_XTITLE_BELOW = 45.0
# How far left of the left edge to look for the y-axis title (points).
_YTITLE_LEFT = 60.0
# A span is "centred" over the plot if its centre x is within this fraction of
# the plot half-width of the plot centre.
_CENTER_FRAC = 0.5
# Vertical-writing threshold: |dy| larger than this means rotated (y title).
_VERTICAL_DIR = 0.7
# A legend swatch path is "small" if both bbox sides are under this (points).
_SWATCH_MAX = 30.0
# Max horizontal gap between a swatch group and the label text to its right.
_SWATCH_GAP = 18.0
# A swatch line/marker often abuts or slightly overlaps the label's (space-padded)
# bbox; allow this much overlap so a swatch ending exactly at the label start
# (gap ~ -0.001) still pairs, instead of being missed and flipping the swatch-side.
_SWATCH_OVERLAP = 2.5
# Vertical overlap tolerance for pairing a swatch with its label row.
_ROW_TOL = 6.0
# Tighter vertical tolerance for matching a continuation span to a label's row;
# legend rows can be stacked only ~5pt apart, so a loose tol grabs the next
# row's text. Half the typical row pitch keeps rows separate.
_LABEL_ROW_TOL = 2.5
# Subscript/superscript glyphs are smaller than the base span and their centre-y
# can shift by up to ~0.75× the base font size (e.g. "E_N" where N is a small
# raised/lowered character, or a raised "^2" exponent). We allow them as
# continuations when their cy offset is within this fraction of the anchor font
# size AND they are smaller. A raised exponent sits higher than a lowered index
# drops, so the fraction must cover ~0.65 of the base size (seen on
# "‖Z_t‖_F^2" math legends) while staying under half the legend row pitch
# (~0.8-1.0× the base size) so it never grabs the next row's text.
_SUB_ROW_TOL_FRAC = 0.75
# A legend marker glyph is small; a path larger than this in either dimension is
# a data curve / box overlapping the legend, not a marker swatch.
_MARKER_SWATCH_MAX = 14.0
# Within a label row, consecutive spans further apart than this (relative to
# effective font size) start a new field -> stop assembling. A slightly wider
# gap (1.3 vs a tighter 1.2) tolerates invisible math operator glyphs rendered
# as paths (e.g. ≤ in "0 ≤ J ≤ 15") that leave a gap between adjacent spans.
_SPAN_GAP = 1.3
# Adjacent spans closer than this (relative to font size) are joined with no
# space (a hyphenated token split across spans); wider gaps get a space.
_ADJ_GAP = 0.25
# When the declared span size is implausibly small (LaTeX scaling artifact), use
# this fraction of the bbox height as the effective size for gap calculations.
_SIZE_BBOX_FRAC = 0.6
# Bin cy to this grid (points) when sorting legend text anchors so that spans
# on the same visual row but with slightly different cy values are ordered by x
# (leftmost first). Half a typical row pitch keeps adjacent rows distinct.
_ROW_BIN = 6.0
# Swatches/labels must lie within the region bbox expanded by this margin
# (legends sit inside the plot area, occasionally just past an edge).
_LEGEND_MARGIN = 8.0
# --- legend_bbox clustering (robust box estimation) ---
# Two legend rows belong to the same cluster only if the vertical gap between
# them (top of the lower minus bottom of the upper) is at most this multiple of
# the taller row's height. Legend rows are stacked tightly (gap < row height);
# an axis-tick row or a distant annotation sits far below the last legend row
# and is split off into its own cluster. 1.6 tolerates the slightly looser
# pitch of 2-row math legends while still cutting at a full blank-row gap.
_CLUSTER_VGAP_FRAC = 1.6
# Two legend rows belong to the same cluster only if their x-ranges overlap by
# at least this fraction of the narrower row's width (legend rows share a left
# swatch column, so their boxes overlap substantially in x). A row that sits in
# a different x-column (e.g. a 2-column legend or a stray axis label) gets its
# own cluster, which is then merged only if mutually close (see below).
_CLUSTER_XOVERLAP_FRAC = 0.15
# Two clusters are merged into one legend when they are mutually close: the gap
# between their boxes (in both x and y) is within this many points. This keeps a
# genuinely split legend (e.g. two adjacent columns) together while leaving a
# far-away false row-pair (axis ticks) as a separate, discarded cluster.
_CLUSTER_MERGE_GAP = 12.0
# A legend never covers more than this fraction of the plot (region) area. If the
# best cluster's box still exceeds it, the detection is treated as unreliable and
# legend_bbox is returned as None (no box) rather than a bloated one. Kept a bit
# above the 0.30 rule-of-thumb so dense legends in small panels are not dropped;
# the mark extractor keeps an independent 0.25 backstop.
_LEGEND_MAX_PLOT_FRAC = 0.40
# When recovering legend swatches by x-column (for mangled / glyph-path labels),
# a swatch is part of the legend only when it is vertically contiguous with an
# emitted entry row: within this multiple of the row height above or below it.
# This stops a data marker series whose points span the label's x-column from
# being swept in (which would snap the legend box onto a row of real data).
_LEGEND_RECOVER_VGAP_FRAC = 1.6
# A legend frame is a stroked, (near-)unfilled rectangle inside the region whose
# area is at most this fraction of the region (it is a sub-box, not the axes
# patch / spine frame which fills most of the region).
_FRAME_MAX_PLOT_FRAC = 0.55
# Caption paragraph: lines within this vertical gap belong to the same block.
_LINE_GAP = 6.0
# How far below/above the region to accept a caption opener (points).
_CAPTION_BELOW = 90.0
_CAPTION_ABOVE = 60.0
# Inline colored-text legend: max Euclidean distance (in [0,1]^3 RGB space)
# between a text span's color and a path stroke color for them to be considered
# the same color.  0.05 tolerates small gamma/rounding differences between
# the color used by the PDF path renderer and the text renderer.
_INLINE_COLOR_TOL = 0.05
# Minimum number of alphanumeric characters in an inline label.  Single-char
# labels (except clearly letter-labels like "a"/"b") tend to be axis-tick
# annotations written in a colored font rather than series names.  We require
# at least 2 characters to suppress stray tick coloring.
_INLINE_MIN_ALNUM = 2


@dataclass
class Labels:
    """Textual context extracted around one chart region."""

    title: str | None = None
    x_title: str | None = None
    y_title: str | None = None
    # Each entry: (style, color, label_text). ``style`` is the swatch style used
    # to disambiguate same-colour series: "marker" (a marker glyph), "dashed" (a
    # dashed line sample) or "line" (a solid line sample).
    legend: list[tuple[str, Color | None, str]] = field(default_factory=list)
    caption: str | None = None
    # Bounding box enclosing all detected legend swatches and their label spans,
    # or None when no legend was found. Used by the mark extractor to exclude
    # legend-region mini-curve decorations from data extraction.
    legend_bbox: BBox | None = None


def _cx(b: BBox) -> float:
    return _bbox_center(b)[0]


def _cy(b: BBox) -> float:
    return _bbox_center(b)[1]


def _eff_size(t: TextSpan) -> float:
    """Effective font size for gap calculations.

    PyMuPDF returns a scaled ``size`` for LaTeX-generated PDFs that can be a
    fraction of the visual point size (e.g. 0.87 for ~12pt text). Using it
    directly makes ``_SPAN_GAP * size`` too tight, truncating multi-span labels
    like "Linear cavit y(y), g'=0.4". We cap from below by
    ``_SIZE_BBOX_FRAC * bbox_height`` so that normal inter-word gaps are
    accepted regardless of the declared size.
    """
    s = t.size or 10.0
    h = t.bbox[3] - t.bbox[1]
    return max(s, _SIZE_BBOX_FRAC * h)


def _is_numeric(text: str) -> bool:
    s = text.strip().replace("−", "-")  # unicode minus
    return bool(_NUMERIC.match(s))


def _is_proxy_label(label: str) -> bool:
    """True when ``label`` is a Type3/glyph-path artifact, not a real legend text.

    Two cases:
    1. Exact match against a known marker-shape proxy string (matplotlib codes
       like 'o', 's', '^' that Type3 fonts emit verbatim instead of the real
       label text when the legend is rendered as glyph paths).
    2. No alphanumeric character at all — a lone punctuation or box glyph.

    Real single-letter labels (e.g. "A", "B", "R") that happen to not match a
    marker code are left through. Labels with any multi-character word content
    (e.g. "9R", "C1", "AT") are always real.
    """
    stripped = label.strip()
    if stripped in _MARKER_PROXIES:
        return True
    # No alphanumeric at all -> punctuation/box/arrow glyph artifact.
    if not re.search(r"[a-zA-Z0-9]", stripped):
        return True
    return False


def _horizontal(t: TextSpan) -> bool:
    return abs(t.dir[1]) < _VERTICAL_DIR


def _vertical(t: TextSpan) -> bool:
    return abs(t.dir[1]) >= _VERTICAL_DIR


def _detect_title(region: Region, texts: list[TextSpan]) -> str | None:
    """Horizontal, non-numeric text just above the top edge, centred."""
    x0, y0, x1, _ = region.bbox
    cx = 0.5 * (x0 + x1)
    half = 0.5 * (x1 - x0)
    cands = []
    for t in texts:
        if not _horizontal(t) or _is_numeric(t.text) or not t.text.strip():
            continue
        if not (y0 - _TITLE_ABOVE <= _cy(t.bbox) <= y0):
            continue
        if abs(_cx(t.bbox) - cx) > _CENTER_FRAC * half:
            continue
        cands.append(t)
    if not cands:
        return None
    # If multiple spans share the title line, join left-to-right.
    cands.sort(key=lambda t: t.bbox[0])
    return _join_spans(cands)


def _detect_x_title(region: Region, texts: list[TextSpan]) -> str | None:
    """Horizontal, non-numeric text below the bottom edge, below tick labels.

    Tick labels sit just under the axis; the axis title is the centred word(s)
    further down. We take the lowest centred non-numeric horizontal span.
    """
    x0, y0, x1, y1 = region.bbox
    cx = 0.5 * (x0 + x1)
    half = 0.5 * (x1 - x0)
    cands = []
    for t in texts:
        if not _horizontal(t) or _is_numeric(t.text) or not t.text.strip():
            continue
        if not (y1 < _cy(t.bbox) <= y1 + _XTITLE_BELOW):
            continue
        if abs(_cx(t.bbox) - cx) > _CENTER_FRAC * half:
            continue
        cands.append(t)
    if not cands:
        return None
    # Take the bottom-most row (axis title is below the numeric tick labels).
    bottom = max(_cy(t.bbox) for t in cands)
    row = [t for t in cands if abs(_cy(t.bbox) - bottom) <= _ROW_TOL]
    row.sort(key=lambda t: t.bbox[0])
    return _join_spans(row)


def _detect_y_title(region: Region, texts: list[TextSpan]) -> str | None:
    """Vertically-written text to the left of the left edge (left of y ticks)."""
    x0, y0, _, y1 = region.bbox
    cy = 0.5 * (y0 + y1)
    half = 0.5 * (y1 - y0)
    cands = []
    for t in texts:
        if not _vertical(t) or _is_numeric(t.text) or not t.text.strip():
            continue
        if not (x0 - _YTITLE_LEFT <= _cx(t.bbox) < x0):
            continue
        if abs(_cy(t.bbox) - cy) > _CENTER_FRAC * half:
            continue
        cands.append(t)
    if not cands:
        return None
    cands.sort(key=lambda t: -t.bbox[1])  # bottom-to-top = reading order
    return _join_spans(cands)


def _join_spans(spans: list[TextSpan]) -> str:
    """Join span texts with single spaces, collapsing whitespace."""
    return re.sub(r"\s+", " ", " ".join(s.text for s in spans)).strip()


def _swatch_style(p: Path) -> str | None:
    """Style signature of a small legend swatch path, or None if not a swatch.

    A short flat path (wide, near-zero height) is a line sample: "dashed" if it
    carries a dash pattern, else "line" (solid). A small 2D blob is a "marker".
    Degenerate axis ticks / spine corners (a few points long in only one
    dimension) are not swatches and return None.
    """
    w = p.bbox[2] - p.bbox[0]
    h = p.bbox[3] - p.bbox[1]
    if h <= 1.5 and w >= 5.0:
        return "dashed" if p.dashes else "line"
    if 2.0 <= w <= _MARKER_SWATCH_MAX and 2.0 <= h <= _MARKER_SWATCH_MAX:
        return "marker"
    return None


def _inside(b: BBox, region: Region, margin: float) -> bool:
    rx0, ry0, rx1, ry1 = region.bbox
    return (rx0 - margin <= _cx(b) <= rx1 + margin
            and ry0 - margin <= _cy(b) <= ry1 + margin)


def _is_axis_tick_anchor(t: TextSpan, region: Region) -> bool:
    """True when ``t`` is an axis TICK label, not a legend entry.

    Legend numeric entries (years "2016", temperatures "300 K", currents "5")
    sit INSIDE the plot beside their swatch; axis tick numbers sit in the margin
    just OUTSIDE the plotting area (y-ticks left of the left spine, x-ticks below
    the bottom spine). The legend candidate filter admits spans within
    ``_LEGEND_MARGIN`` of the region so legends that nudge past an edge still
    pair — but that same slack lets a y/x tick number sneak in as a false legend
    anchor (2006.03681_p4c3: y-ticks '2000'/'1800' paired with the legend's own
    line samples sitting to their right, flipping swatch_side and dropping the
    real entries). So a PURELY-NUMERIC anchor whose centre is outside the region
    proper is treated as a tick label and skipped; non-numeric labels and numeric
    labels strictly inside the plot are unaffected.
    """
    if not _is_numeric(t.text):
        return False
    return not _inside(t.bbox, region, 0.0)


def _assemble_label(
    start: int, ty: float, texts: list[TextSpan], used: set[int]
) -> tuple[str, set[int]]:
    """Assemble the full label beginning at span ``start`` on row ``ty``.

    Continuation spans further to the right on the same row are appended while
    consecutive spans stay close horizontally (a multi-span label such as
    "BN-x5-Sigmoid" or "T = 100"); a large horizontal gap (the next legend
    column) ends it. Returns (text, span-indices-consumed).

    Subscript/superscript glyphs have a smaller font size and their centre-y
    can be offset by up to ~0.5× the anchor font size, exceeding the normal
    _LABEL_ROW_TOL. We admit them with a looser size-relative tolerance so that
    labels like "E_N(a⁻ : CR)" are not truncated to "E(a : CR)".
    """
    first = texts[start]
    base_size = _eff_size(first)

    def _same_row(t: TextSpan) -> bool:
        cy_off = abs(_cy(t.bbox) - ty)
        if cy_off <= _LABEL_ROW_TOL:
            return True
        # Accept a smaller span (subscript/superscript) whose vertical offset
        # is within _SUB_ROW_TOL_FRAC × the anchor font size.
        if (t.size is not None and t.size < base_size
                and cy_off <= _SUB_ROW_TOL_FRAC * base_size):
            return True
        # Vertical-OVERLAP fallback: glyphs of different heights on the SAME
        # baseline (e.g. a Greek 'θ' next to '=3') have offset centres yet overlap
        # strongly in y. Without this the anchor 'θ' can't grab '=3°' and is
        # dropped (2009.07658: 'θ=3°' -> '=3°'). Rows are ~1 line-height apart, so
        # this never reaches the next row.
        lo, hi = max(t.bbox[1], first.bbox[1]), min(t.bbox[3], first.bbox[3])
        h_min = min(t.bbox[3] - t.bbox[1], first.bbox[3] - first.bbox[1]) or 1.0
        if hi - lo >= 0.6 * h_min:
            return True
        return False

    cont = sorted(
        (i for i, t in enumerate(texts)
         if i != start and i not in used and _horizontal(t) and t.text.strip()
         and _same_row(t)
         and t.bbox[0] >= first.bbox[2] - 1.0),
        key=lambda i: texts[i].bbox[0],
    )
    picked = [start]
    prev = first
    for i in cont:
        t = texts[i]
        if t.bbox[0] - prev.bbox[2] > _SPAN_GAP * _eff_size(prev):
            break
        picked.append(i)
        prev = t
    # Join the spans, marking sub/superscripts as inline mathtext (so 'P'+lowered
    # 'in' -> 'P$_{in}$', 'cm'+raised'-3' -> 'cm$^{-3}$') and inserting a space
    # only where spans are horizontally separated.
    items = [(texts[i].text, texts[i].size,
              0.5 * (texts[i].bbox[1] + texts[i].bbox[3]),
              texts[i].bbox[0], texts[i].bbox[2]) for i in picked]
    label = _join_scripts(items)
    return label, set(picked)


def _union(boxes: list[BBox]) -> BBox:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _box_gap(a: BBox, b: BBox) -> float:
    """Min separation between two boxes (0 if they overlap/touch)."""
    dx = max(b[0] - a[2], a[0] - b[2], 0.0)
    dy = max(b[1] - a[3], a[1] - b[3], 0.0)
    return max(dx, dy)


def _same_cluster(a: BBox, b: BBox) -> bool:
    """True when two legend-row boxes are tightly stacked in the same column.

    Legend rows sit just below one another (small vertical gap) and share a
    left swatch column (their x-ranges overlap). A far-below row (an axis-tick
    line that grabbed a stray swatch) or a row in a different x-column fails
    one of these and starts a new cluster.
    """
    # Vertical adjacency relative to the taller row.
    h = max(a[3] - a[1], b[3] - b[1], 1.0)
    vgap = max(b[1] - a[3], a[1] - b[3])  # >0 only when separated
    if vgap > _CLUSTER_VGAP_FRAC * h:
        return False
    # Horizontal overlap relative to the narrower row.
    ox = min(a[2], b[2]) - max(a[0], b[0])
    w = min(a[2] - a[0], b[2] - b[0])
    if w <= 0:
        return False
    return ox >= _CLUSTER_XOVERLAP_FRAC * w


def _cluster_rows(boxes: list[BBox]) -> list[list[int]]:
    """Group row-pair boxes into clusters of tightly-stacked, aligned rows.

    Returns lists of indices into ``boxes``. Rows are connected when
    ``_same_cluster`` holds; the transitive closure forms each cluster.
    """
    n = len(boxes)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if _same_cluster(boxes[i], boxes[j]):
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def _merge_close_clusters(
    cluster_boxes: list[tuple[int, BBox]]
) -> list[list[int]]:
    """Merge clusters whose boxes are mutually close (a split / multi-column
    legend). ``cluster_boxes`` is (cluster_id, box); returns lists of cluster
    ids. Clusters far apart (a stray axis-tick cluster) stay separate."""
    n = len(cluster_boxes)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if _box_gap(cluster_boxes[i][1], cluster_boxes[j][1]) <= _CLUSTER_MERGE_GAP:
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(cluster_boxes[i][0])
    return list(groups.values())


def _detect_frame(region: Region, paths: list[Path]) -> BBox | None:
    """A stroked, (near-)clear-filled rectangle inside the region that is a
    legend frame candidate (a sub-box, smaller than the axes patch). Returns the
    smallest such rectangle's bbox, or None.

    Heuristic: a closed path whose bbox is a small-to-moderate fraction of the
    region, strictly inside it, that is stroked (a border). We do not require a
    specific fill colour (matplotlib legends are typically white/clear); we only
    require it not span the whole region (that is the spine/axes patch)."""
    rx0, ry0, rx1, ry1 = region.bbox
    region_area = max((rx1 - rx0) * (ry1 - ry0), 1.0)
    best: BBox | None = None
    best_area = 0.0
    for p in paths:
        if not p.closed or p.stroke is None:
            continue
        bx0, by0, bx1, by1 = p.bbox
        w = bx1 - bx0
        h = by1 - by0
        if w <= 8.0 or h <= 8.0:
            continue
        area = w * h
        frac = area / region_area
        if frac > _FRAME_MAX_PLOT_FRAC or frac < 0.01:
            continue
        # Must sit (almost) entirely inside the region.
        if not (rx0 - _LEGEND_MARGIN <= bx0 and bx1 <= rx1 + _LEGEND_MARGIN
                and ry0 - _LEGEND_MARGIN <= by0 and by1 <= ry1 + _LEGEND_MARGIN):
            continue
        # Roughly rectangular (a real frame, not a diagonal data path): its bbox
        # area should match a 4-corner box. We approximate by requiring the path
        # to have few points (rectangles flatten to ~4-6 vertices).
        if len(p.points) > 8:
            continue
        if area > best_area:
            best, best_area = p.bbox, area
    return best


def _legend_box(
    entry_boxes: list[BBox], region: Region, paths: list[Path]
) -> BBox | None:
    """Robust legend bounding box from the per-entry row boxes.

    Strategy:
      1. Cluster row boxes into tightly-stacked, x-aligned groups.
      2. Merge mutually-close clusters (split / multi-column legends).
      3. Pick the merged group with the most rows (the legend); tie-break by
         larger area (the denser block).
      4. If a stroked frame rectangle tightly contains that group, snap to it.
      5. Reject (return None) if the resulting box exceeds the plot-fraction cap
         — a real legend is a small fraction of the plot, so a bloated box means
         the row-pairs did not form a compact legend.
    """
    if not entry_boxes:
        return None
    rx0, ry0, rx1, ry1 = region.bbox
    region_area = max((rx1 - rx0) * (ry1 - ry0), 1.0)

    raw = _cluster_rows(entry_boxes)
    raw_boxes = [_union([entry_boxes[i] for i in g]) for g in raw]
    merged = _merge_close_clusters(list(enumerate(raw_boxes)))

    candidates: list[tuple[int, BBox]] = []  # (row_count, box)
    for ids in merged:
        members = [i for cid in ids for i in raw[cid]]
        box = _union([entry_boxes[i] for i in members])
        candidates.append((len(members), box))
    # Most rows, then largest area.
    candidates.sort(
        key=lambda c: (c[0], (c[1][2] - c[1][0]) * (c[1][3] - c[1][1])),
        reverse=True,
    )
    box = candidates[0][1]

    # Prefer a frame rectangle if it tightly wraps the chosen cluster.
    frame = _detect_frame(region, paths)
    if frame is not None:
        fx0, fy0, fx1, fy1 = frame
        bx0, by0, bx1, by1 = box
        # Frame contains the cluster (with small slack) -> snap to the frame.
        if (fx0 - 4.0 <= bx0 and bx1 <= fx1 + 4.0
                and fy0 - 4.0 <= by0 and by1 <= fy1 + 4.0):
            box = frame

    bw = box[2] - box[0]
    bh = box[3] - box[1]
    if (bw * bh) / region_area > _LEGEND_MAX_PLOT_FRAC:
        return None
    return box


def _detect_legend(
    region: Region, paths: list[Path], texts: list[TextSpan]
) -> tuple[list[tuple[str, Color | None, str]], BBox | None]:
    """Pair each legend label with the swatch immediately to its left.

    A legend entry is a text label whose left edge has a small swatch (a short
    line and/or a small marker) just left of it on the same row. We anchor on
    the label text (filtered to non-numeric horizontal spans inside the region,
    so paragraph text / other figures' legends are excluded), find its swatch,
    assemble any continuation spans into the full label, and capture the swatch
    ``style`` ("marker" / "line" / "dashed") and colour. Anchoring on the label
    (not the swatch) keeps stray data markers near the legend from hijacking it.

    Returns ``(entries, legend_bbox)`` where ``legend_bbox`` is the bounding box
    enclosing all swatch paths and label spans, or None when no legend found.
    That bbox lets the mark extractor exclude mini-curve decorations rendered
    inside legend boxes from data extraction.
    """
    swatches = [
        p for p in paths
        if _swatch_style(p) is not None
        and (p.stroke if p.stroke is not None else p.fill) is not None
        and _inside(p.bbox, region, _LEGEND_MARGIN)
    ]

    out: list[tuple[str, Color | None, str]] = []
    used: set[int] = set()
    # One merged (swatch + label spans) bbox per emitted entry, fed to the
    # clustering-based legend_bbox estimator below.
    entry_boxes: list[BBox] = []
    pick_cols: list[tuple[float, float]] = []  # swatch x-columns of emitted rows
    # Process labels top-to-bottom, left-to-right (legend reading order).
    # Bin cy to a coarse grid so spans on the same visual row (e.g. "ε" and
    # "-PCA" with cy 0.4 pts apart) sort by x rather than by fractional cy,
    # ensuring the leftmost span on a row is always the anchor.
    order = sorted(range(len(texts)),
                   key=lambda i: (round(_cy(texts[i].bbox) / _ROW_BIN), texts[i].bbox[0]))

    # Which side do swatches sit on? Most legends draw the sample to the LEFT of
    # the label; some draw it to the RIGHT (2006.03604: "Np [—] Tc [—]"). Picking
    # the wrong side drops labels AND mis-colours the ones it does pair (it grabs
    # the neighbouring column's swatch). Choose the side that pairs MORE labels,
    # defaulting to LEFT on a tie (the common case, behaviour-preserving).
    def _row_for(t, side):
        ty = _cy(t.bbox)
        if side == "left":
            return [p for p in swatches if abs(_cy(p.bbox) - ty) <= _ROW_TOL
                    and -_SWATCH_OVERLAP <= t.bbox[0] - p.bbox[2] <= _SWATCH_GAP]
        return [p for p in swatches if abs(_cy(p.bbox) - ty) <= _ROW_TOL
                and -_SWATCH_OVERLAP <= p.bbox[0] - t.bbox[2] <= _SWATCH_GAP]

    _cand = [texts[i] for i in order
             if i not in used and _horizontal(texts[i]) and texts[i].text.strip()
             and _inside(texts[i].bbox, region, _LEGEND_MARGIN)
             and not _is_axis_tick_anchor(texts[i], region)]
    _left_n = sum(1 for t in _cand if _row_for(t, "left"))
    _right_n = sum(1 for t in _cand if _row_for(t, "right"))
    swatch_side = "right" if _right_n > _left_n else "left"

    for ti in order:
        t = texts[ti]
        if ti in used or not _horizontal(t) or not t.text.strip():
            continue
        if not _inside(t.bbox, region, _LEGEND_MARGIN):
            continue
        # An axis tick number (numeric span just outside the plot) is not a
        # legend entry, even if a swatch (the legend's own line sample, or a
        # spine) happens to sit beside it. Skip it before swatch pairing.
        if _is_axis_tick_anchor(t, region):
            continue
        # Skip purely numeric anchors unless a swatch is immediately to the
        # left — a numeric like "0" in "0 ≤ J ≤ 15" can start a legend entry
        # when the ≤ signs are rendered as path glyphs (invisible in text).
        # We admit numeric anchors only when there IS a swatch match; the
        # assembled-label non-numeric check below then guards against bare
        # tick labels that accidentally pick up a swatch.
        ty = _cy(t.bbox)
        # Swatches on this row, on the detected side (left for most legends).
        row = _row_for(t, swatch_side)
        if _is_numeric(t.text) and not row:
            continue
        if not row:
            continue
        label, consumed = _assemble_label(ti, ty, texts, used)
        # A purely-numeric label IS a real legend entry when it has a genuine
        # swatch beside it (years "2016"/"2020", a current "5", a temperature
        # "300"): 2203.00695_p24c1 lost its "2016"/"2020" year entries. The `row`
        # requirement (checked above) already guards against bare axis ticks, which
        # almost never have a _swatch_style sample next to them, so the numeric
        # label is admitted here.
        if not label or _is_proxy_label(label):
            continue
        # Pick the swatch CLOSEST in row to this label (tie-break: prefer a marker
        # glyph). Tightly-stacked legends (row pitch < _ROW_TOL) put several swatch
        # rows inside the tolerance band, so taking the first would grab an adjacent
        # row's colour (2502.18732_p6c3: H=30/60/70 each took the swatch one row up,
        # mis-colouring every entry). Closest-cy pairs each label with its own row.
        pick = min(row, key=lambda p: (abs(_cy(p.bbox) - ty),
                                       0 if _swatch_style(p) == "marker" else 1))
        color = pick.stroke if pick.stroke is not None else pick.fill
        out.append((_swatch_style(pick), color, label))
        used |= consumed
        # Merge this entry's swatch + consumed text spans into one row box.
        boxes = [p.bbox for p in row] + [texts[idx].bbox for idx in consumed]
        eb = _union(boxes)
        entry_boxes.append(eb)
        pick_cols.append((eb[0], eb[2]))  # full entry x-range (swatch + label)

    # A LONE numeric entry is almost always a tick that incidentally picked up a
    # swatch, not a legend (test_pure_numeric_anchor_still_filtered). But numeric
    # entries inside a MULTI-row legend are real (year legends "2016"/"2020",
    # 2203.00695_p24c1). So drop a single numeric entry; keep numerics when the
    # legend has >= 2 entries (a genuine stacked cluster).
    if len(out) == 1 and _is_numeric(out[0][2]):
        out, entry_boxes, pick_cols = [], [], []

    # Entries whose LABEL is mangled (e.g. LaTeX math: tildes/subscripts rendered
    # as glyph paths) emit no row, so their swatches would leak into the data as
    # fake marker/curve series. Recover them: any swatch aligned in the emitted
    # entries' x-column is part of the legend. _legend_box clusters by tight
    # vertical stacking, so only swatches contiguous with the legend are kept.
    #
    # The x-column alone is NOT sufficient: a single-column data scatter / a
    # marker series whose points happen to span the label's x-range will have
    # markers at many DIFFERENT y-levels that all fall in the column. Sweeping
    # those in lets _legend_box snap the legend onto the densest row of DATA
    # markers (dropping them from extraction). Require vertical contiguity with
    # the emitted entry rows too: a swatch is part of the legend only when it
    # sits within a tight vertical band of an existing entry row (real legends
    # stack tightly), not scattered across the plot height.
    if entry_boxes and pick_cols:
        cx0 = min(c[0] for c in pick_cols)
        cx1 = max(c[1] for c in pick_cols)
        # Reference row height for the contiguity band (tallest entry row).
        row_h = max(eb[3] - eb[1] for eb in entry_boxes)
        vband = _LEGEND_RECOVER_VGAP_FRAC * max(row_h, 1.0)
        # Candidate swatches in the entry x-column.
        col_swatches = [p for p in swatches
                        if cx0 - 4.0 <= 0.5 * (p.bbox[0] + p.bbox[2]) <= cx1 + 4.0]
        # Grow the legend column by VERTICAL CONTIGUITY (transitive): a swatch
        # joins only when its vertical gap to an already-accepted entry/swatch row
        # is within the band. A real stacked legend chains row-to-row; data
        # markers scattered down the column sit a full data-gap below the legend
        # text row (> band) and never chain in, so they stay as data.
        accepted = list(entry_boxes)
        added = True
        while added:
            added = False
            for p in list(col_swatches):
                if any(p.bbox[1] - a[3] <= vband and a[1] - p.bbox[3] <= vband
                       for a in accepted):
                    accepted.append(p.bbox)
                    entry_boxes.append(p.bbox)
                    col_swatches.remove(p)
                    added = True

    return out, _legend_box(entry_boxes, region, paths)


def _color_dist(a: Color, b: Color) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5


# --- glyph-path legend (no TextSpan labels) -------------------------------
# Some LaTeX/pgfplots charts render the legend label text as GLYPH PATHS (vector
# outlines), not as selectable TextSpans, so the swatch-based detector finds no
# label to anchor on. The swatches (small coloured markers) and the label text
# (small black glyph paths) then leak into the data extraction as spurious
# series. We cannot recover the label STRINGS without OCR, but we can locate the
# legend geometrically: a vertical stack of ≥2 distinctly-coloured marker
# swatches sharing one x-column, with the label glyph paths immediately to their
# right. Returning that bounding box lets the mark extractor exclude both the
# swatches and the glyph-path label characters.
#
# Swatch x-centres of one legend column agree within this many points.
_GLYPH_COL_XTOL = 4.0
# A swatch column is a legend only when it stacks at least this many distinctly-
# coloured markers (a single-colour stack is more likely a data column).
_GLYPH_MIN_DISTINCT = 2
# Two swatch colours are "distinct" when their RGB differ by more than this.
_GLYPH_COLOR_TOL = 0.05
# Label glyph paths sit to the right of the swatch column within this many
# points (the row's label text); used to extend the legend box rightward.
_GLYPH_LABEL_GAP = 60.0
# A label glyph path is small (a single character outline); reject anything
# larger in either dimension (it would be a data curve, not a glyph).
_GLYPH_CHAR_MAX = 14.0
# A real legend always carries label TEXT next to its swatches.  We require at
# least one alphabetic text span sitting to the right of the swatch column and
# vertically aligned with one of its rows before accepting the column as a
# legend.  A swatch column with NO such label text is a DATA column (a colormap
# scatter draws distinctly-coloured markers stacked at one x-position), not a
# legend — accepting it would drop real data points.  Text must start within
# this many points to the right of the column and within this vertical slack of
# a swatch row centre.
_GLYPH_TEXT_GAP = 60.0
_GLYPH_TEXT_ROW_TOL = 6.0


def _detect_glyph_legend_box(
    region: Region, paths: list[Path], texts: list[TextSpan] | None = None
) -> BBox | None:
    """Locate a swatch-only (glyph-path-text) legend and return its bounding box.

    Fires only as a fallback when no text-anchored legend was found. Looks for a
    column of marker swatches (``_swatch_style`` == "marker") sharing an x-centre
    (within ``_GLYPH_COL_XTOL``) that stacks at least ``_GLYPH_MIN_DISTINCT``
    DISTINCTLY-coloured swatches vertically — the signature of a legend key
    column (data columns repeat the same colour set at each x). The box is the
    swatch column extended rightward to cover the small glyph paths (the label
    characters rendered as vector outlines) that sit on the swatch rows.

    A genuine legend always carries label TEXT beside its swatches.  We require
    at least one alphabetic text span (``texts``) sitting just to the right of
    the swatch column and aligned with one of its rows.  A swatch column with NO
    label text is a DATA column — a colormap scatter stacks distinctly-coloured
    markers at one x-position — not a legend; accepting it would drop real data
    points (this is the 2409.17350 false positive).  When ``texts`` is None the
    check is skipped (legacy callers).

    Returns the legend ``BBox`` or None. Label strings are not produced (the
    text may be unreadable glyph outlines); the box is used by the mark extractor
    to drop the swatches and any label-character glyphs from data.
    """
    # Coloured marker swatches inside the region (exclude black/white).
    cand: list[tuple[float, float, BBox, Color]] = []  # (cx, cy, bbox, colour)
    for p in paths:
        if _swatch_style(p) != "marker":
            continue
        if not _inside(p.bbox, region, _LEGEND_MARGIN):
            continue
        col = p.fill if p.fill is not None else p.stroke
        if col is None or col == (0.0, 0.0, 0.0) or col == (1.0, 1.0, 1.0):
            continue
        cx = 0.5 * (p.bbox[0] + p.bbox[2])
        cy = 0.5 * (p.bbox[1] + p.bbox[3])
        cand.append((cx, cy, p.bbox, col))

    if len(cand) < _GLYPH_MIN_DISTINCT:
        return None

    # Group swatches into x-columns (centres within _GLYPH_COL_XTOL).
    cand.sort(key=lambda c: c[0])
    columns: list[list[tuple[float, float, BBox, Color]]] = []
    for c in cand:
        if columns and abs(c[0] - columns[-1][-1][0]) <= _GLYPH_COL_XTOL:
            columns[-1].append(c)
        else:
            columns.append([c])

    # A legend column stacks ≥ _GLYPH_MIN_DISTINCT distinctly-coloured swatches.
    def _distinct_count(col: list) -> int:
        reps: list[Color] = []
        for _, _, _, c in col:
            if all(_color_dist(c, r) > _GLYPH_COLOR_TOL for r in reps):
                reps.append(c)
        return len(reps)

    legend_cols = [c for c in columns if _distinct_count(c) >= _GLYPH_MIN_DISTINCT]
    if not legend_cols:
        return None
    # Prefer the column with the most distinct colours (the real legend key).
    col = max(legend_cols, key=_distinct_count)

    sx0 = min(b[0] for _, _, b, _ in col)
    sy0 = min(b[1] for _, _, b, _ in col)
    sx1 = max(b[2] for _, _, b, _ in col)
    sy1 = max(b[3] for _, _, b, _ in col)

    # Extend rightward over the small glyph paths (label characters) that sit on
    # the swatch rows just to the right of the column.
    box_x1 = sx1
    n_label_glyphs = 0
    for p in paths:
        bw = p.bbox[2] - p.bbox[0]
        bh = p.bbox[3] - p.bbox[1]
        if bw <= 0 or bh <= 0 or bw > _GLYPH_CHAR_MAX or bh > _GLYPH_CHAR_MAX:
            continue
        pcx = 0.5 * (p.bbox[0] + p.bbox[2])
        pcy = 0.5 * (p.bbox[1] + p.bbox[3])
        if sx1 < pcx <= sx1 + _GLYPH_LABEL_GAP and sy0 - 2.0 <= pcy <= sy1 + 2.0:
            box_x1 = max(box_x1, p.bbox[2])
            n_label_glyphs += 1

    # Require LABEL EVIDENCE beside the swatch column.  A genuine legend writes a
    # label next to each swatch row — as readable TEXT or, when the label is
    # rendered as vector outlines, as small label-character GLYPH paths just to
    # the right of the column.  A bare swatch column with NEITHER is a DATA
    # column (a colormap scatter stacks distinctly-coloured markers at one
    # x-position), not a legend; treating it as one drops real data points (the
    # 2409.17350 false positive).  ``texts`` is optional for legacy callers; when
    # absent we fall back to the glyph-path evidence alone.
    row_centres = [0.5 * (b[1] + b[3]) for _, _, b, _ in col]
    has_text_label = False
    if texts is not None:
        for t in texts:
            stripped = t.text.strip()
            if not stripped or not any(c.isalpha() for c in stripped):
                continue  # numeric tick labels / punctuation are not legend text
            if not _horizontal(t):
                continue
            tx0, ty0, _, ty1 = t.bbox
            tyc = 0.5 * (ty0 + ty1)
            # Text must start just to the right of the swatch column and line up
            # vertically with one of its rows.
            if not (sx1 - 2.0 <= tx0 <= sx1 + _GLYPH_TEXT_GAP):
                continue
            if any(abs(tyc - rc) <= _GLYPH_TEXT_ROW_TOL for rc in row_centres):
                has_text_label = True
                break
    if not has_text_label and n_label_glyphs == 0:
        return None

    return (sx0, sy0, box_x1, sy1)


def _detect_inline_labels(
    region: Region, paths: list[Path], texts: list[TextSpan]
) -> list[tuple[str, Color | None, str]]:
    """Detect inline colored-text series labels (no swatch box).

    Some charts write series names directly on/beside the curve in the curve's
    own color rather than placing a separate legend box. Strategy:
      1. Collect stroke colors of data paths inside the region (excluding black
         and white, which are axis / background colors).
      2. Find horizontal, non-numeric text spans inside the region whose own
         text color is close to one of those path stroke colors.
      3. Deduplicate: if the same label text appears multiple times with the
         same color (e.g. repeated in multiple sub-panels sharing one page),
         emit it only once per unique (color, text) pair.

    Conservative guards:
      - Only triggers when the swatch-based detector found no legend at all.
      - Requires at least _INLINE_MIN_ALNUM alphanumeric characters in the
        assembled label to avoid picking up isolated colored axis tick numerals.
      - Excludes known marker-proxy labels.
    """
    # Collect non-black, non-white path stroke colors inside the region.
    path_colors: list[Color] = []
    for p in paths:
        c = p.stroke
        if c is None:
            continue
        if not _inside(p.bbox, region, _LEGEND_MARGIN):
            continue
        # Skip black (axis spines / tick marks) and white (background).
        if c == (0.0, 0.0, 0.0) or c == (1.0, 1.0, 1.0):
            continue
        path_colors.append(c)

    if not path_colors:
        return []

    seen: set[tuple[Color, str]] = set()
    out: list[tuple[str, Color | None, str]] = []
    order = sorted(range(len(texts)),
                   key=lambda i: (round(_cy(texts[i].bbox) / _ROW_BIN), texts[i].bbox[0]))
    used: set[int] = set()
    for ti in order:
        t = texts[ti]
        if ti in used:
            continue
        if not _horizontal(t) or not t.text.strip():
            continue
        if not _inside(t.bbox, region, _LEGEND_MARGIN):
            continue
        tc = t.color
        if tc is None:
            continue  # black text — not an inline colored label
        # Find the closest path color.
        best_dist = min(_color_dist(tc, pc) for pc in path_colors)
        if best_dist > _INLINE_COLOR_TOL:
            continue  # text color not close to any curve color
        # Assemble the full label from this anchor span.
        label, consumed = _assemble_label(ti, _cy(t.bbox), texts, used)
        if not label or _is_numeric(label) or _is_proxy_label(label):
            continue
        # Require enough alphanumeric content to avoid colored axis ticks.
        if sum(1 for c in label if c.isalnum()) < _INLINE_MIN_ALNUM:
            continue
        # Find which path color this text is closest to.
        matched_pc = min(path_colors, key=lambda pc: _color_dist(tc, pc))
        key = (matched_pc, label)
        if key in seen:
            used |= consumed
            continue
        seen.add(key)
        out.append(("line", matched_pc, label))
        used |= consumed

    return out


def _detect_caption(region: Region, texts: list[TextSpan]) -> str | None:
    """Nearest "Figure N"/"Fig. N" paragraph below (fallback above) the region.

    Synthetic fixtures have no caption, so this returns None there. On real
    pages, we find the closest caption opener span vertically near the figure,
    then gather the contiguous block of spans (same-ish lines) following it.
    """
    _, y0, _, y1 = region.bbox
    openers = []
    for i, t in enumerate(texts):
        if _CAPTION_RE.match(t.text):
            cyt = _cy(t.bbox)
            if y1 <= cyt <= y1 + _CAPTION_BELOW:
                openers.append((cyt - y1, i))  # below: positive distance
            elif y0 - _CAPTION_ABOVE <= cyt < y0:
                openers.append((y0 - cyt + 1000, i))  # above: deprioritised
    if not openers:
        return None
    openers.sort()
    start = openers[0][1]

    # Gather the caption block: order spans by reading order, then accumulate
    # from the opener while consecutive lines stay close vertically.
    ordered = sorted(texts, key=lambda t: (round(t.bbox[1] / 4.0), t.bbox[0]))
    start_y = _cy(texts[start].bbox)
    idx = next(i for i, t in enumerate(ordered)
               if t is texts[start])
    block = [ordered[idx]]
    prev_y = start_y
    for t in ordered[idx + 1:]:
        gap = _cy(t.bbox) - prev_y
        # New line allowed; stop if a large vertical jump (paragraph break).
        if gap > _LINE_GAP + (t.size or 12.0):
            break
        block.append(t)
        prev_y = _cy(t.bbox)
    return _join_spans(block)


def detect_labels(
    region: Region,
    paths: list[Path],
    texts: list[TextSpan],
    page=None,
) -> Labels:
    """Extract the chart's textual context around ``region``.

    ``page`` is accepted for API symmetry with the rest of the pipeline but is
    unused (all geometry comes from ``region``, ``paths`` and ``texts``).
    """
    legend_entries, legend_bbox = _detect_legend(region, paths, texts)
    # Fallback: if the swatch-based detector found nothing, try the inline
    # colored-text strategy (series names written directly on the curves).
    if not legend_entries:
        legend_entries = _detect_inline_labels(region, paths, texts)
    # Fallback: a glyph-path legend (label text rendered as vector outlines, no
    # TextSpan) leaves no entry to anchor on. Locate its bounding box from the
    # swatch column so the mark extractor drops the swatches + label glyphs.
    # Label strings stay empty (glyph outlines are unreadable without OCR).
    if legend_bbox is None:
        legend_bbox = _detect_glyph_legend_box(region, paths, texts)
    return Labels(
        title=_detect_title(region, texts),
        x_title=_detect_x_title(region, texts),
        y_title=_detect_y_title(region, texts),
        legend=legend_entries,
        caption=_detect_caption(region, texts),
        legend_bbox=legend_bbox,
    )
