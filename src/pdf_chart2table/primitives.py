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

from .model import Color, Path, Region

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
        return "star"
    if n == 5:
        # A 5-vertex closed path is either a square (axis-aligned corners) or a
        # diamond (rotated 45°, corners at top/right/bottom/left). Distinguish by
        # the position of the top vertex relative to the centroid x.
        return "diamond" if is_diamond_geometry(p) else "square"
    if n == 4:
        return "triangle" if filled else "plus"
    if n == 3:
        return "triangle"
    return "cross" if not filled else "marker"
