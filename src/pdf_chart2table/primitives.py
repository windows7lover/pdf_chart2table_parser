"""Shared primitive-classification vocabulary.

This module is the single home for the low-level judgments that the pipeline's
classifiers (marks, lines, plot_region, extract, axes, labels) previously each
re-derived: colour predicates, polyline/bbox geometry, and matplotlib
marker-shape classification.

Centralising these removes the verbatim duplication that caused inconsistency
between modules (e.g. three separate ``_round_color`` definitions, two
``_is_saturated`` with the same ``_SAT_SPREAD``, two identical ``_on_border``).

Consumers keep their own *tuned, divergent* role thresholds (a marker glyph and
a curve are intentionally different views of the same path in marks vs lines);
only the shared primitive computations live here.

Public API:
    Colour: round_color, is_saturated, is_near_white, is_white, hue_of, hue_dist
    Geometry: centroid, bbox_center, on_border, box_bounds, in_box
    Shape: shape_of, is_diamond_geometry, MARKER_CODE, KNOWN_CLOSED_SHAPES
"""

from __future__ import annotations

import colorsys
import re

from .model import Color, Path, Region


# A base (non-script) run is re-rendered italic only when its text is a simple
# variable token (letters/digits) -- safe in mathtext. Anything else stays roman
# so we never risk a mathtext parse error on stray punctuation/spaces.
_SAFE_ITALIC = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")


def join_scripts(items) -> str:
    """Join text spans into one string, marking SUB/SUPERSCRIPTS as inline
    mathtext so '10'+raised'5' -> '10$^{5}$', 'cm'+raised'-3' -> 'cm$^{-3}$',
    'P'+lowered'in' -> 'P$_{in}$'. A base span flagged ITALIC and whose text is a
    simple variable token is wrapped in mathtext ('M' italic -> '$M$', so a legend
    'M$_{s}$' becomes '$M$$_{s}$' = slanted M).

    ``items`` is an iterable of ``(text, size, cy, x0, x1[, italic[, bold]])`` (any
    order; sorted by x0; the italic/bold flags are optional and default False). A
    span SMALLER than the base (largest) size whose centre is raised above the
    baseline -> superscript; smaller and lowered -> subscript. Consecutive
    same-script, same-emphasis spans merge into one group. A wide horizontal gap
    inserts a space. A base run that is bold/italic and is a simple variable token
    is wrapped in mathtext ('$M$' italic, '$\\mathbf{M}$' bold, '$\\boldsymbol{M}$'
    bold-italic). Falls back to a plain concatenation when sizes are absent (so
    non-script labels are returned UNCHANGED -- no '$', no behaviour change)."""
    items = sorted(items, key=lambda it: it[3])
    sizes = [it[1] for it in items if it[1]]
    if not sizes:
        return "".join(it[0] for it in items)
    base = max(sizes)
    # Baseline reference from the BASE-RUN spans only (>= 0.92x): a near-full-size
    # subscript (~0.90x) is script-eligible (sz < 0.92x below), so including it
    # here would drag the baseline onto the subscript row and mis-tier the rest.
    base_cys = [it[2] for it in items if it[1] and it[1] >= 0.92 * base]
    base_cy = sorted(base_cys)[len(base_cys) // 2] if base_cys else None
    out, buf, cur, cur_it, cur_bd = [], [], None, False, False
    prev_x1 = None

    def flush():
        nonlocal buf, cur, cur_it, cur_bd
        if buf:
            seg = "".join(buf)
            if cur:
                out.append("$%s{%s}$" % (cur, seg))
            elif (cur_it or cur_bd) and _SAFE_ITALIC.match(seg.strip()):
                s = seg.strip()
                if cur_it and cur_bd:
                    out.append(r"$\boldsymbol{%s}$" % s)
                elif cur_bd:
                    out.append(r"$\mathbf{%s}$" % s)
                else:
                    out.append("$%s$" % s)
            else:
                out.append(seg)
            buf = []

    for it in items:
        text, sz, cy, x0, x1 = it[0], it[1], it[2], it[3], it[4]
        italic = bool(it[5]) if len(it) > 5 else False
        bold = bool(it[6]) if len(it) > 6 else False
        script = None
        # A sub/superscript is SMALLER than the base and vertically OFFSET. Two
        # tiers: a clearly-small span (<0.82x) needs only a modest offset; a
        # near-full-size span (0.82-0.92x, common for legend subscripts like the
        # 'D' in 'V_D', rendered ~0.90x) must be offset MUCH more, so an ordinary
        # full-size token with a descender (whose bbox-centre dips slightly) is
        # never mistaken for a script.
        if base_cy is not None and sz and sz < 0.92 * base:
            thr = 0.12 * base if sz < 0.82 * base else 0.30 * base
            if cy < base_cy - thr:              # raised (smaller PDF y)
                script = "^"
            elif cy > base_cy + thr:            # lowered
                script = "_"
        eff_it = italic and script is None       # emphasis applies to base runs only
        eff_bd = bold and script is None
        if script != cur or eff_it != cur_it or eff_bd != cur_bd:
            flush()
            cur, cur_it, cur_bd = script, eff_it, eff_bd
        if (prev_x1 is not None and script is None
                and (x0 - prev_x1) > 0.28 * base):
            buf.append(" ")
        buf.append(text)
        prev_x1 = x1
    flush()
    return re.sub(r"\s+", " ", "".join(out)).strip()

# --------------------------------------------------------------------------
# Colour predicates
# --------------------------------------------------------------------------

# A stroke/fill is a saturated *series* colour when its RGB spread exceeds this;
# grays / blacks / whites (gridlines, spines, backgrounds) fall below it.
SAT_SPREAD = 0.2

# Max horizontal gap from a legend text span's left edge in which a swatch /
# mini-curve glyph is considered "in the legend" (used to suppress legend
# decorations from data extraction). The exact positional test differs per
# consumer (marks vs lines); only this gap constant is shared.
LEGEND_GAP = 40.0


def round_color(c: Color | None) -> tuple | None:
    """Quantise a colour to 2 decimals for use as a grouping key, or None."""
    return tuple(round(v, 2) for v in c) if c is not None else None


def is_saturated(c: Color | None) -> bool:
    """True when ``c`` is a saturated series colour (RGB spread > SAT_SPREAD)."""
    return c is not None and (max(c) - min(c)) > SAT_SPREAD


def is_near_white(c: Color | None, white_min: float = 0.9) -> bool:
    """True when ``c`` is white or near-white (min channel >= ``white_min``).

    ``white_min`` defaults to 0.9 (the threshold used by lines for plot-frame /
    background strokes). ``marks`` historically used 0.95 for the axes-patch
    background; callers needing that pass ``white_min=0.95``.
    """
    return c is not None and min(c) >= white_min


def is_white(c: Color | None) -> bool:
    """True when ``c`` is pure-ish white (every channel >= 0.95)."""
    return c is not None and c[0] >= 0.95 and c[1] >= 0.95 and c[2] >= 0.95


def hue_of(c: Color | None) -> float | None:
    """Hue in degrees [0, 360) for colour ``c``, or None if no colour."""
    if c is None:
        return None
    h, _, _ = colorsys.rgb_to_hsv(c[0], c[1], c[2])
    return h * 360.0


def hue_dist(a: float, b: float) -> float:
    """Circular distance between two hue angles (both in degrees)."""
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------

def centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    """Mean of a polyline's vertices (pixel centroid of a mark)."""
    n = len(points)
    return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n)


def bbox_center(b: tuple[float, float, float, float]) -> tuple[float, float]:
    """Centre of a bbox ``(x0, y0, x1, y1)``."""
    return 0.5 * (b[0] + b[2]), 0.5 * (b[1] + b[3])


def on_border(cx: float, cy: float, region: Region, tol: float = 2.0) -> bool:
    """Centroid sits on a region spine/frame edge (where tick marks live)."""
    x0, y0, x1, y1 = region.bbox
    return (
        abs(cx - x0) <= tol or abs(cx - x1) <= tol
        or abs(cy - y0) <= tol or abs(cy - y1) <= tol
    )


def box_bounds(plot_box: tuple) -> tuple[float, float, float, float]:
    """Normalise a plot box to ``(xlo, ylo, xhi, yhi)`` (edges in either order)."""
    bx0, by0, bx1, by1 = plot_box
    xlo, xhi = (bx0, bx1) if bx0 <= bx1 else (bx1, bx0)
    ylo, yhi = (by0, by1) if by0 <= by1 else (by1, by0)
    return xlo, ylo, xhi, yhi


def in_box(cx: float, cy: float, plot_box: tuple | None, frac: float = 0.0) -> bool:
    """Centroid is inside the calibrated plot box plus a ``frac`` span tolerance.

    With ``frac=0`` this is the strict containment test; with ``frac>0`` (e.g.
    0.03) it allows the centroid to sit that fraction of the axis span outside
    the spine box. ``plot_box`` is ``(x0, y0, x1, y1)`` in either order; with no
    box given, always True (legacy behaviour).
    """
    if plot_box is None:
        return True
    xlo, ylo, xhi, yhi = box_bounds(plot_box)
    xtol = frac * (xhi - xlo)
    ytol = frac * (yhi - ylo)
    return (xlo - xtol <= cx <= xhi + xtol) and (ylo - ytol <= cy <= yhi + ytol)


# --------------------------------------------------------------------------
# Marker-shape classification
# --------------------------------------------------------------------------

# Recognised closed marker shapes that get relaxed size/aspect bounds and a
# matplotlib marker code.
KNOWN_CLOSED_SHAPES = frozenset(
    {"circle", "star", "square", "diamond", "triangle", "marker"}
)

# Recognised marker glyph shapes including the OPEN stroked crosses (``+``/``x``).
# A small cross/plus is a genuine data marker (drawn as two short crossing
# strokes), not a line fragment, so it earns the same relaxed size/aspect bounds
# as the closed shapes in ``marks._is_data_mark`` and is excluded from the line
# classifiers' fragment/curve collection (see ``is_marker_glyph``).
KNOWN_MARKER_SHAPES = KNOWN_CLOSED_SHAPES | {"plus", "cross"}

# Marker class -> the matplotlib-style marker code reported on a Series.
MARKER_CODE = {
    "circle": "o",
    "square": "s",
    "triangle": "^",
    "diamond": "D",
    "star": "*",
    "plus": "+",
    "cross": "x",
    "marker": None,
}


def is_diamond_geometry(p: Path) -> bool:
    """True when a 5-vertex closed path has diamond (rotated-square) geometry.

    A matplotlib ``D`` diamond marker has its four corners at top (0, +h),
    right (+w, 0), bottom (0, -h), left (-w, 0) — the top/bottom vertices are
    centred on the x-axis. A regular ``s`` square has corners at (±w, ±h) — the
    top vertices are at x ≈ cx ± w/2.

    Test: the vertex with the highest y-coordinate (the "top" vertex) should be
    at x ≈ centroid_x (|x_top - cx| < bbox_width / 4).
    """
    if p.fill is None:
        return False  # only filled paths can be diamonds
    pts = p.points
    # Remove the closing duplicate (first == last) if present.
    unique = list(dict.fromkeys(pts))  # preserves order, deduplicates
    if len(unique) != 4:
        return False
    cx = sum(pt[0] for pt in unique) / 4
    top = max(unique, key=lambda pt: pt[1])
    bw = p.bbox[2] - p.bbox[0]
    if bw <= 0:
        return False
    return abs(top[0] - cx) < bw / 4


def _is_starlike(pts: list[tuple[float, float]]) -> bool:
    """True when a many-vertex closed glyph has the REGULAR radial spikes of a
    star (a few evenly spaced tips) rather than the smooth ~constant radius of a
    circle.

    Vertex count alone cannot tell them apart: a circle flattened to 9-39 points
    (common in vector charts) and a star both clear a raw count threshold, which
    is why a blanket ``n >= 9 -> star`` mislabelled filled circles as stars. Radii
    from the centroid are binned by polar angle (which washes out the per-vertex
    jitter of a doubled-arc circle), then the local maxima are counted: a real
    star has 4-7 significant, evenly spaced spikes; a circle has ~none.
    """
    import math
    n = len(pts)
    if n < 9:
        return False
    cx = sum(x for x, _ in pts) / n
    cy = sum(y for _, y in pts) / n
    nbins = 24
    binmax = [0.0] * nbins
    for x, y in pts:
        a = math.atan2(y - cy, x - cx)
        r = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
        b = int((a + math.pi) / (2 * math.pi) * nbins) % nbins
        if r > binmax[b]:
            binmax[b] = r
    rs = [v for v in binmax if v > 0]
    if len(rs) < 6:
        return False
    mean = sum(rs) / len(rs)
    if mean <= 0:
        return False
    amp = (max(rs) - min(rs)) / mean
    margin = 0.10 * mean
    m = len(rs)
    spikes = sum(1 for i in range(m)
                 if rs[i] > mean + margin
                 and rs[i] >= rs[(i - 1) % m] and rs[i] >= rs[(i + 1) % m])
    return 4 <= spikes <= 7 and amp > 0.35


def shape_of(p: Path) -> str:
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
        # A 9-39-vertex closed glyph is a circle OR a star; tell them apart by
        # radial geometry rather than assuming star (filled circles flatten to
        # this many points and were being mislabelled, so their markers rendered
        # as '*').
        return "star" if _is_starlike(p.points) else "circle"
    if n == 5:
        # A 5-vertex closed path is either a square (axis-aligned corners) or a
        # diamond (rotated 45°, corners at top/right/bottom/left). Distinguish by
        # the position of the top vertex relative to the centroid x.
        return "diamond" if is_diamond_geometry(p) else "square"
    if n == 4:
        # A 4-vertex path is one of: a triangle (3 distinct corners, the 4th
        # repeats the first to close), or a cross/plus glyph drawn as two crossing
        # strokes flattened into one polyline (4 distinct endpoints, no repeat).
        # Distinguish by DISTINCT-vertex count, not by the fill flag: matplotlib
        # scatter emits ``×``/``+`` strokes with a non-None fill too, so a fill
        # check alone would mis-send every filled cross/plus to "triangle".
        unique = list(dict.fromkeys(p.points))
        if len(unique) == 3:
            return "triangle"
        return _cross_or_plus(p)
    if n == 3:
        return "triangle"
    if 6 <= n <= 8 and _is_asterisk(p):
        return "star"
    return "cross" if not filled else "marker"


def _is_asterisk(p) -> bool:
    """True when an OPEN 6-8 vertex glyph is an asterisk ``*`` -- several strokes
    radiating from the centre to the bbox boundary (2004.01004: a '*' is 4 crossing
    strokes = 8 endpoints at the 4 corners + 4 edge-midpoints). Distinct from a '+'
    or 'x' (4 vertices, handled above) and from a closed polygon marker."""
    if p.closed:
        return False
    x0, y0, x1, y1 = p.bbox
    rx, ry = (x1 - x0) / 2.0, (y1 - y0) / 2.0
    if rx <= 0 or ry <= 0:
        return False
    cx, cy = 0.5 * (x0 + x1), 0.5 * (y0 + y1)
    pts = list(dict.fromkeys(p.points))
    if not (6 <= len(pts) <= 9):
        return False
    # Every vertex sits well away from the centre (near the boundary): the glyph
    # is spokes from the middle, not a compact blob.
    return all(abs((px - cx) / rx) >= 0.6 or abs((py - cy) / ry) >= 0.6
               for px, py in pts)


# A vertex counts as a "corner" of the bbox when it lies within this fraction of
# the bbox size from a corner; as an "edge midpoint" when within this fraction of
# an edge centre. Used to tell an ``x`` (corner-anchored) from a ``+`` (midpoint-
# anchored) 4-vertex open glyph.
_CROSS_CORNER_FRAC = 0.3


def _cross_or_plus(p: Path) -> str:
    """Classify a 4-vertex open cross glyph as ``cross`` (×) or ``plus`` (+).

    A matplotlib ``x`` marker is two diagonal strokes, so its flattened vertices
    sit at the bbox CORNERS; a ``+`` marker is a vertical + horizontal stroke, so
    its vertices sit at the bbox EDGE MIDPOINTS. We score the 4 vertices: if more
    are corner-anchored it is a cross, if more are midpoint-anchored it is a plus.
    Falls back to ``plus`` when ambiguous (the historical default)."""
    x0, y0, x1, y1 = p.bbox
    bw, bh = x1 - x0, y1 - y0
    if bw <= 0 or bh <= 0:
        return "plus"
    cx, cy = 0.5 * (x0 + x1), 0.5 * (y0 + y1)
    ctol_x, ctol_y = _CROSS_CORNER_FRAC * bw, _CROSS_CORNER_FRAC * bh
    corner = midpoint = 0
    for px, py in dict.fromkeys(p.points):
        near_xedge = abs(px - x0) <= ctol_x or abs(px - x1) <= ctol_x
        near_yedge = abs(py - y0) <= ctol_y or abs(py - y1) <= ctol_y
        near_xmid = abs(px - cx) <= ctol_x
        near_ymid = abs(py - cy) <= ctol_y
        if near_xedge and near_yedge:
            corner += 1
        elif (near_xmid and near_yedge) or (near_ymid and near_xedge):
            midpoint += 1
    return "cross" if corner > midpoint else "plus"


# A marker glyph is compact (both bbox sides comparable). An elongated path
# (aspect above this) is a dash / segment, not a glyph, even if its vertex count
# matches a marker shape.
_MAX_GLYPH_ASPECT = 2.5


def is_marker_glyph(p: Path) -> bool:
    """True when ``p`` is a small, compact data-marker glyph (not a line fragment).

    A scatter / line-with-markers point is drawn as a small recognised-shape glyph
    (open square ``□``, triangle ``△``, diamond, filled circle, or a ``+``/``×``
    cross of two short crossing strokes). The line classifiers (``lines.py``)
    independently sweep up short same-colour strokes as dash/curve fragments; a
    small open marker glyph would otherwise be mis-collected into a jagged fake
    line. This region-free predicate lets ``lines.py`` exclude such glyphs.

    Conservative gates (precision over recall): the path must
      * carry a recognised marker shape (KNOWN_MARKER_SHAPES), AND
      * have at least 4 flattened vertices (a 2-point segment is a genuine line /
        dash fragment, NEVER a glyph), AND
      * be COMPACT — both bbox sides comparable (aspect below ``_MAX_GLYPH_ASPECT``)
        so an elongated dash (which can be a 4-vertex zig segment) is not caught, AND
      * fold back on itself — a marker glyph (a closed loop, or a ``+``/``×`` whose
        strokes cross) reverses direction in x, whereas a short line / dash
        fragment marches monotonically across its bbox. A monotone open polyline
        is therefore NOT a glyph (this distinguishes a 4-point cross marker from a
        4-point line segment, which would otherwise share the ``plus``/``cross``
        shape label).
    """
    if len(p.points) < 4:
        return False
    if shape_of(p) not in KNOWN_MARKER_SHAPES:
        return False
    x0, y0, x1, y1 = p.bbox
    bw, bh = x1 - x0, y1 - y0
    long, short = max(bw, bh), min(bw, bh)
    if short <= 0 or long / short > _MAX_GLYPH_ASPECT:
        return False
    # An open polyline that is monotone in x is a line/dash segment, not a glyph.
    if not p.closed and _x_monotone(p.points):
        return False
    return True


def _x_monotone(pts: list[tuple[float, float]]) -> bool:
    """True when ``pts`` never reverse x-direction (a straight/curved line run).

    A marker glyph (a closed loop, or a ``+``/``×`` of crossing strokes) reverses
    in x as it traces; a line / dash fragment advances monotonically. Sub-pixel
    jitter is ignored via a small epsilon."""
    eps = 0.05
    sign = 0
    for a, b in zip(pts, pts[1:]):
        dx = b[0] - a[0]
        if abs(dx) < eps:
            continue
        s = 1 if dx > 0 else -1
        if sign and s != sign:
            return False
        sign = s
    return True
