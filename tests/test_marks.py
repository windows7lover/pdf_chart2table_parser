"""M4 marker/series extraction tests, gated by scripts/eval_extraction.py.

For each scatter / marker-bearing fixture we run the extract pipeline, turn the
``ChartResult`` into the prediction schema ``eval_extraction`` expects, and
assert it matches the fixture ground truth: same series count and every matched
series within tolerance (~1% of axis range, log space for log axes).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from pdf_chart2table.extract import extract_pdf
from pdf_chart2table.marks import classify_marks
from pdf_chart2table.model import Path as VPath, Region, TextSpan

FIXTURES = Path(__file__).parent / "fixtures"


def _square(cx, cy, *, fill=None, stroke=None, half=2.0):
    pts = [(cx - half, cy - half), (cx + half, cy - half), (cx + half, cy + half),
           (cx - half, cy + half), (cx - half, cy - half)]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return VPath(points=pts, stroke=stroke, fill=fill, width=1.0, dashes=None,
                 closed=True, bbox=(min(xs), min(ys), max(xs), max(ys)))


def test_filled_plus_stroke_coincident_markers_merged():
    # Each data point drawn as a filled square AND a stroke-only outline at the
    # same position must collapse to ONE series, not two.
    region = Region(bbox=(100.0, 100.0, 300.0, 300.0),
                    path_indices=list(range(8)), text_indices=[])
    centers = [(130, 250), (170, 220), (210, 200), (250, 190)]
    paths = []
    for cx, cy in centers:
        paths.append(_square(cx, cy, fill=(0.0, 0.0, 1.0)))            # filled
        paths.append(_square(cx, cy, stroke=(0.0, 0.0, 1.0)))         # stroke-only
    series = classify_marks(region, paths, [])
    assert len(series) == 1
    assert len(series[0].marks) == 4


def test_out_of_box_marker_dropped_in_box_kept():
    # A plot box smaller than the region (spine-to-spine). Markers inside the box
    # are data; one well outside it (a legend swatch / annotation) is dropped.
    region = Region(bbox=(100.0, 100.0, 300.0, 300.0),
                    path_indices=list(range(4)), text_indices=[])
    plot_box = (130.0, 130.0, 270.0, 270.0)
    paths = [
        _square(150, 250, fill=(0.0, 0.0, 1.0)),   # in box
        _square(200, 220, fill=(0.0, 0.0, 1.0)),   # in box
        _square(240, 180, fill=(0.0, 0.0, 1.0)),   # in box
        _square(120, 110, fill=(0.0, 0.0, 1.0)),   # outside box -> drop
    ]
    series = classify_marks(region, paths, [], plot_box=plot_box)
    assert len(series) == 1
    assert len(series[0].marks) == 3


def test_no_plot_box_keeps_all_marks():
    # Legacy behaviour: without a plot box, no clipping is applied.
    region = Region(bbox=(100.0, 100.0, 300.0, 300.0),
                    path_indices=list(range(2)), text_indices=[])
    paths = [_square(150, 250, fill=(0.0, 0.0, 1.0)),
             _square(120, 110, fill=(0.0, 0.0, 1.0))]
    series = classify_marks(region, paths, [])
    assert sum(len(s.marks) for s in series) == 2


def _circle(cx, cy, *, fill=None, stroke=None, r=2.0, n=48):
    import math
    pts = [(cx + r * math.cos(2 * math.pi * k / n),
            cy + r * math.sin(2 * math.pi * k / n)) for k in range(n)]
    pts.append(pts[0])
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return VPath(points=pts, stroke=stroke, fill=fill, width=1.0, dashes=None,
                 closed=True, bbox=(min(xs), min(ys), max(xs), max(ys)))


def test_filled_blob_plus_square_outline_merges_to_square():
    """Regression (2302.01559_p38c3): a marker drawn as a coloured FILL blob
    (flattens to a ~circle) PLUS a coincident stroked square OUTLINE in a
    different edge colour is ONE physical glyph. The merged series must adopt the
    square shape and combine fill + edge colour, not keep the disc blob.

    Before the fix, the two same-position groups collapsed to whichever was drawn
    first (the red disc), so the markers reported as circles ('o') and the real
    square shape was lost.
    """
    region = Region(bbox=(100.0, 100.0, 300.0, 300.0),
                    path_indices=list(range(8)), text_indices=[])
    centers = [(130, 250), (170, 210), (210, 250), (250, 210)]
    paths = []
    for cx, cy in centers:
        paths.append(_circle(cx, cy, fill=(1.0, 0.0, 0.0), stroke=(1.0, 0.0, 0.0)))
        paths.append(_square(cx, cy, stroke=(0.0, 0.0, 0.0)))  # black edge outline
    series = classify_marks(region, paths, [])
    assert len(series) == 1
    sm = series[0]
    assert len(sm.marks) == 4
    assert sm.shape == "square"          # outline shape wins over the fill blob
    assert sm.fill == (1.0, 0.0, 0.0)    # red fill kept (series colour = fill)


def test_distinct_marker_series_not_merged():
    # Two square series at DIFFERENT positions stay separate.
    region = Region(bbox=(100.0, 100.0, 300.0, 300.0),
                    path_indices=list(range(6)), text_indices=[])
    paths = [
        _square(130, 250, fill=(0.0, 0.0, 1.0)),
        _square(170, 220, fill=(0.0, 0.0, 1.0)),
        _square(210, 200, fill=(0.0, 0.0, 1.0)),
        _square(135, 180, fill=(1.0, 0.0, 0.0)),
        _square(175, 175, fill=(1.0, 0.0, 0.0)),
        _square(215, 170, fill=(1.0, 0.0, 0.0)),
    ]
    series = classify_marks(region, paths, [])
    assert len(series) == 2


def test_numeric_text_not_treated_as_legend_label():
    """Data markers near purely-numeric text spans (tick labels, annotation
    numbers like '8', '9', '10') must NOT be dropped as legend swatches.

    A real legend entry always contains at least one letter.  Digit-only
    strings are axis tick labels or data-point annotation numbers and must
    be skipped by the swatch filter.  Regression: log-scale scatter charts
    where point labels are numbers caused data markers to be mis-classified
    as legend swatches.
    """
    region = Region(bbox=(100.0, 100.0, 400.0, 400.0),
                    path_indices=list(range(4)), text_indices=list(range(3)))
    # Numeric text spans inside the plot — tick labels / point annotations.
    texts = [
        TextSpan(text="8",  bbox=(150, 120, 165, 133)),   # upper area
        TextSpan(text="9",  bbox=(150, 210, 165, 223)),   # mid-region
        TextSpan(text="10", bbox=(150, 305, 165, 318)),   # lower area
    ]
    # Data marks to the left of each numeric label (well inside the region).
    paths = [
        _square(135, 126, fill=(0.0, 0.0, 1.0)),   # left of "8"
        _square(135, 216, fill=(0.0, 0.0, 1.0)),   # left of "9"
        _square(135, 311, fill=(0.0, 0.0, 1.0)),   # left of "10"
        _square(250, 250, fill=(0.0, 0.0, 1.0)),   # interior, no nearby text
    ]
    series = classify_marks(region, paths, texts)
    # All 4 data marks should be kept — numeric text is not legend text.
    assert len(series) == 1, f"expected 1 series, got {len(series)}"
    assert len(series[0].marks) == 4, (
        f"expected 4 marks (numeric text not swatch-filter), got {len(series[0].marks)}"
    )


def test_swatch_inside_plot_box_not_dropped():
    """When a chart has an embedded legend (legend text INSIDE the plot area),
    data markers near the legend text must NOT be dropped as legend swatches.
    The border-proximity check on the text span should allow them through."""
    # Create a small plot box, with legend text in the MIDDLE (60% from top).
    plot_box = (100.0, 100.0, 300.0, 300.0)  # spine box
    # Legend text at y=220 (60% from top of 200-high box): 100 + 0.6*200 = 220
    # This is NOT near any border (border threshold = 20% = 40px from edges).
    legend_text = TextSpan(text="Series A", bbox=(160, 215, 240, 225))
    region = Region(bbox=(80.0, 80.0, 320.0, 320.0),
                    path_indices=list(range(6)), text_indices=[0])
    # 6 data marks spread across the plot, some near the legend text y-level
    paths = [
        _square(110, 130, fill=(0.0, 0.0, 1.0)),  # far from legend text
        _square(140, 160, fill=(0.0, 0.0, 1.0)),
        _square(170, 190, fill=(0.0, 0.0, 1.0)),
        _square(200, 220, fill=(0.0, 0.0, 1.0)),  # near legend text cy=220
        _square(230, 250, fill=(0.0, 0.0, 1.0)),
        _square(260, 270, fill=(0.0, 0.0, 1.0)),
    ]
    series = classify_marks(region, paths, [legend_text], plot_box=plot_box)
    # All 6 data marks should be kept; none dropped as false-positive swatches.
    assert len(series) == 1, f"expected 1 series, got {len(series)}"
    assert len(series[0].marks) == 6, (
        f"expected 6 marks, got {len(series[0].marks)}"
    )


def test_hue_gradient_singles_merged():
    """Single-mark groups with matching shape and similar hue are merged into
    one series to handle gradient-coloured scatter plots."""
    region = Region(bbox=(100.0, 100.0, 300.0, 300.0),
                    path_indices=list(range(8)), text_indices=[])
    # 5 blue circles with slightly different RGB (sequential colourmap gradient)
    paths = [
        _square(120 + 20 * i, 250 - 10 * i,
                stroke=(0.22 - 0.02 * i, 0.52 - 0.03 * i, 0.74 - 0.02 * i))
        for i in range(5)
    ]
    # 3 red crosses at different positions (different hue, should stay separate)
    paths += [
        _square(150, 180, stroke=(0.8, 0.1, 0.1)),
        _square(190, 160, stroke=(0.85, 0.15, 0.1)),
        _square(230, 140, stroke=(0.7, 0.05, 0.05)),
    ]
    series = classify_marks(region, paths, [])
    # Blue gradient: 5 singles with hue ~200–210° → 1 merged series
    # Red crosses: 3 singles with hue ~0–5° → 1 merged series
    assert len(series) == 2, f"expected 2 series (blue + red), got {len(series)}"
    mark_counts = sorted(len(s.marks) for s in series)
    assert mark_counts == [3, 5], f"expected [3, 5] marks, got {mark_counts}"


def test_colormap_scatter_singles_merged():
    """A colourmap-coded scatter: same shape + same size markers, each in a
    DISTINCT colour spanning the whole hue wheel (viridis-style), must merge into
    ONE series preserving every point — not be shattered into tiny groups and
    dropped.  The hue-gradient merge (20°) cannot join these (the ramp spans the
    whole wheel), so the colormap-scatter merge must."""
    region = Region(bbox=(100.0, 100.0, 300.0, 300.0),
                    path_indices=list(range(8)), text_indices=[])
    # 8 same-size squares, colours sampled across a wide hue range (purple ->
    # blue -> teal -> green -> yellow), one point per colour (all singletons).
    colors = [
        (0.27, 0.00, 0.33),   # dark purple
        (0.21, 0.20, 0.55),   # indigo
        (0.16, 0.34, 0.55),   # blue
        (0.13, 0.46, 0.50),   # teal
        (0.18, 0.56, 0.42),   # green-teal
        (0.42, 0.65, 0.25),   # green
        (0.65, 0.71, 0.13),   # yellow-green
        (0.99, 0.91, 0.14),   # yellow
    ]
    paths = [
        _square(120 + 20 * i, 250 - 12 * i, fill=colors[i])
        for i in range(8)
    ]
    series = classify_marks(region, paths, [])
    assert len(series) == 1, f"expected 1 colormap series, got {len(series)}"
    assert len(series[0].marks) == 8, (
        f"expected all 8 points recovered, got {len(series[0].marks)}"
    )


def test_distinct_color_series_not_colormap_merged():
    """GUARD: a few genuinely-distinct multi-mark series (3 colours, each a real
    series of several points) must NOT be collapsed by the colormap merge.  Too
    few groups (< 4) AND each is a real multi-point series, so they stay separate."""
    region = Region(bbox=(100.0, 100.0, 300.0, 300.0),
                    path_indices=list(range(12)), text_indices=[])
    # Three colours, each with 4 marks at distinct positions (real series).
    reds = [_square(120 + 15 * i, 130, fill=(0.85, 0.10, 0.10)) for i in range(4)]
    greens = [_square(120 + 15 * i, 180, fill=(0.10, 0.70, 0.15)) for i in range(4)]
    blues = [_square(120 + 15 * i, 230, fill=(0.10, 0.15, 0.85)) for i in range(4)]
    series = classify_marks(region, reds + greens + blues, [])
    assert len(series) == 3, (
        f"expected 3 distinct series (no colormap merge), got {len(series)}"
    )
    assert sorted(len(s.marks) for s in series) == [4, 4, 4]


def _wide_filled_band(fill, x0, y0, x1, y1):
    """A wide filled band path (DOS / SED confidence region)."""
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    return VPath(points=pts, stroke=None, fill=fill, width=None, dashes=None,
                 closed=True, bbox=(x0, y0, x1, y1))


def _dos_artifact(cx, cy, *, fill=None, stroke=None, half=2.0):
    """A 2-vertex closed path — the shape DOS interior sampling produces.

    In PDF DOS charts the interior of a filled band is sampled as many small
    open (2-vertex) paths that classify as 'marker' (unrecognised shape).  They
    have bw≈0 or bh≈0, so they would normally fail _MIN_MARK_SIDE, but when
    we call _is_data_mark with no shape awareness they DO pass if we make them
    2D.  Use a tiny 2×2 closed open shape (2 diag points) to simulate them.
    """
    pts = [(cx - half, cy - half), (cx + half, cy + half)]
    return VPath(points=pts, stroke=stroke, fill=fill, width=1.0, dashes=None,
                 closed=False, bbox=(cx - half, cy - half, cx + half, cy + half))


def test_filled_band_interior_unrecognised_paths_rejected():
    """Fix 1: unrecognised interior paths (DOS artifact glyphs) inside a wide
    colored band are rejected; recognised exterior markers survive.

    The fix targets unrecognised 'marker'-class paths that arise from DOS/SED
    fill interior sampling.  Real data markers (circle, square, triangle, star)
    are NOT rejected even when inside a fill, because they are legitimate data
    points overlaid on a confidence band.
    """
    # Region: x 100–300, y 100–300.  Blue fill band: x 110–250, y 140–250.
    # Width=140 (70% of 200), height=110 (55% > 5% threshold).
    band_x0, band_y0, band_x1, band_y1 = 110.0, 140.0, 250.0, 250.0
    band = _wide_filled_band((0.2, 0.4, 0.9), band_x0, band_y0, band_x1, band_y1)

    region = Region(bbox=(100.0, 100.0, 300.0, 300.0),
                    path_indices=list(range(7)), text_indices=[])

    # 4 DOS-artifact paths inside the band (unrecognised 'cross' shape, 2 pts).
    # These are interior fill-sampling artefacts, not real data marks.
    interior = [
        _dos_artifact(150, 180, fill=(0.2, 0.4, 0.9)),
        _dos_artifact(180, 200, fill=(0.2, 0.4, 0.9)),
        _dos_artifact(210, 220, fill=(0.2, 0.4, 0.9)),
        _dos_artifact(140, 190, fill=(0.2, 0.4, 0.9)),
    ]
    # 2 recognised square markers outside the band (real data).
    exterior = [
        _square(160, 120, fill=(1.0, 0.0, 0.0)),   # above band
        _square(220, 270, fill=(1.0, 0.0, 0.0)),   # below band
    ]
    paths = interior + exterior + [band]

    series = classify_marks(region, paths, [])
    mark_counts = {s.fill: len(s.marks) for s in series}
    # Interior DOS artifacts (blue unrecognised paths) should be rejected.
    assert (0.2, 0.4, 0.9) not in mark_counts or mark_counts[(0.2, 0.4, 0.9)] == 0, (
        f"interior DOS artifacts should be rejected, got {mark_counts}"
    )
    # Exterior real markers (red squares) should be kept.
    red = (1.0, 0.0, 0.0)
    assert red in mark_counts and mark_counts[red] == 2, (
        f"exterior markers should be kept, got {mark_counts}"
    )


def test_recognised_markers_inside_fill_kept():
    """Fix 1 (conservative): recognised marker shapes (square, circle) inside
    a wide confidence band are NOT rejected — they are real data points.

    Pattern: ML scatter with markers + same-color shaded confidence interval.
    """
    band = _wide_filled_band((0.0, 0.62, 0.45), 110.0, 120.0, 290.0, 185.0)
    region = Region(bbox=(100.0, 100.0, 300.0, 300.0),
                    path_indices=list(range(5)), text_indices=[])
    # 4 real square data markers, 3 inside the green band
    paths = [
        _square(130, 155, fill=(0.0, 0.62, 0.45)),  # inside band
        _square(160, 165, fill=(0.0, 0.62, 0.45)),  # inside band
        _square(200, 175, fill=(0.0, 0.62, 0.45)),  # inside band
        _square(270, 130, fill=(0.0, 0.62, 0.45)),  # inside band (near edge)
        band,
    ]
    series = classify_marks(region, paths, [])
    total_marks = sum(len(s.marks) for s in series)
    assert total_marks == 4, f"all 4 recognised markers should be kept, got {total_marks}"


def _thin_triangle(cx, cy, *, fill=None, stroke=None, w=2.0, h=5.0):
    """A thin/tall triangle (3 vertices) — simulates an inverted-triangle marker."""
    pts = [(cx - w, cy + h), (cx + w, cy + h), (cx, cy - h)]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return VPath(points=pts, stroke=stroke, fill=fill, width=1.0, dashes=None,
                 closed=True, bbox=(min(xs), min(ys), max(xs), max(ys)))


def test_thin_triangle_marker_not_dropped():
    """Fix 2: a thin triangle (aspect > 3, min-side < 1.5 old thresholds) that
    is a recognised shape must NOT be dropped by the aspect / min-side gate."""
    region = Region(bbox=(100.0, 100.0, 300.0, 300.0),
                    path_indices=list(range(4)), text_indices=[])
    # Thin triangle: bw=4 (2*w), bh=10 (2*h), aspect=10/4=2.5 but min-side=4≥1.5.
    # Previously would pass aspect; let's use very thin ones (aspect 5+):
    paths = [
        _thin_triangle(130 + 30 * i, 200 - 10 * i, fill=(0.0, 0.6, 0.0), w=1.5, h=8.0)
        for i in range(4)
    ]
    series = classify_marks(region, paths, [])
    assert len(series) == 1, f"expected 1 triangle series, got {len(series)}"
    assert len(series[0].marks) == 4, f"expected 4 marks, got {len(series[0].marks)}"
    assert series[0].shape == "triangle"


def _tiny_circle(cx, cy, *, fill=None, stroke=None, r=0.7):
    """A tiny circle (many vertices, small radius) — small marker at low DPI."""
    import math
    n = 40
    pts = [(cx + r * math.cos(2 * math.pi * k / n),
            cy + r * math.sin(2 * math.pi * k / n)) for k in range(n)]
    pts.append(pts[0])
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return VPath(points=pts, stroke=stroke, fill=fill, width=1.0, dashes=None,
                 closed=True, bbox=(min(xs), min(ys), max(xs), max(ys)))


def test_tiny_circle_marker_not_dropped():
    """Fix 2: a tiny circle (min-side < 1.5, the old threshold) that is a
    recognised 'circle' shape must NOT be dropped."""
    region = Region(bbox=(100.0, 100.0, 300.0, 300.0),
                    path_indices=list(range(4)), text_indices=[])
    paths = [
        _tiny_circle(130 + 30 * i, 200 - 10 * i, fill=(0.7, 0.3, 0.1))
        for i in range(4)
    ]
    series = classify_marks(region, paths, [])
    assert len(series) == 1, f"expected 1 circle series, got {len(series)}"
    assert len(series[0].marks) == 4, f"expected 4 marks, got {len(series[0].marks)}"


def _open_circle_glyph(cx, cy, *, fill=None, stroke=None, r=2.0, n=65):
    """A matplotlib 'o' glyph: a 65-vertex loop emitted as an OPEN polyline whose
    first and last vertex coincide (start == end). This is the real marker case
    -- the endpoint gap is ~0 even though closed=False."""
    import math
    pts = [(cx + r * math.cos(2 * math.pi * k / n),
            cy + r * math.sin(2 * math.pi * k / n)) for k in range(n)]
    pts.append(pts[0])  # close the loop -> start == end
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return VPath(points=pts, stroke=stroke, fill=fill, width=1.0, dashes=None,
                 closed=False, bbox=(min(xs), min(ys), max(xs), max(ys)))


def _open_curve_segment(x0, x1, y0, y1, stroke, *, n=60):
    """A dense OPEN curve segment: ~n vertices traversing from (x0,y0) to
    (x1,y1), so its endpoints sit far apart (it never returns to its start) --
    the 2208.14630 case of a wiggly curve emitted as many small dense segments."""
    import math
    pts = []
    for k in range(n):
        t = k / (n - 1)
        x = x0 + (x1 - x0) * t
        y = y0 + (y1 - y0) * t + 1.5 * math.sin(8 * math.pi * t)
        pts.append((x, y))
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return VPath(points=pts, stroke=stroke, fill=None, width=1.0, dashes=None,
                 closed=False, bbox=(min(xs), min(ys), max(xs), max(ys)))


def test_dense_open_curve_segment_not_a_mark():
    """Regression for 2208.14630_p20c2: a dense OPEN curve segment (many vertices,
    endpoints far apart) must NOT be claimed as a data mark -- it is a piece of a
    wiggly line curve, not a scatter glyph."""
    from pdf_chart2table.marks import _is_data_mark
    region = Region(bbox=(100.0, 100.0, 300.0, 300.0),
                    path_indices=list(range(1)), text_indices=[])
    seg = _open_curve_segment(150, 156, 200, 206, (1.0, 0.0, 0.0))
    assert not _is_data_mark(seg, region)


def test_real_open_circle_glyph_still_a_mark():
    """Guard: a genuine 65-vertex circle glyph emitted as an OPEN polyline whose
    endpoints coincide (start == end) must STILL be a data mark -- the curve-
    segment guard keys on the endpoint GAP, not on open-ness or vertex count, so
    real line+marker series keep their markers."""
    region = Region(bbox=(100.0, 100.0, 300.0, 300.0),
                    path_indices=list(range(5)), text_indices=[])
    paths = [
        _open_circle_glyph(130 + 30 * i, 200 - 10 * i, fill=(0.0, 0.0, 1.0))
        for i in range(5)
    ]
    series = classify_marks(region, paths, [])
    assert len(series) == 1, f"expected 1 marker series, got {len(series)}"
    assert len(series[0].marks) == 5


def test_stroke_optional_same_fill_merged():
    """Regression for 2410.00955_p10c1 over-segmentation: one physical marker
    series whose glyphs are mostly drawn filled+stroked but one point is drawn
    with the stroke dropped (fill only) must collapse to ONE series, not split
    into a (fill, stroke) group and a (fill, None) group at different positions."""
    region = Region(bbox=(100.0, 100.0, 300.0, 300.0),
                    path_indices=list(range(4)), text_indices=[])
    GREEN = (0.46, 0.78, 0.58)
    FILL = (0.87, 0.95, 0.89)
    paths = [
        _square(130, 250, fill=FILL, stroke=GREEN),   # filled + stroked
        _square(170, 220, fill=FILL, stroke=GREEN),    # filled + stroked
        _square(210, 200, fill=FILL, stroke=GREEN),    # filled + stroked
        _square(250, 190, fill=FILL, stroke=None),     # stroke dropped on this one
    ]
    series = classify_marks(region, paths, [])
    assert len(series) == 1, f"expected 1 merged series, got {len(series)}"
    assert len(series[0].marks) == 4


def test_stroke_optional_distinct_strokes_not_merged():
    """Guard: two series with the SAME fill but DISTINCT non-None edge strokes
    are genuinely different series and must NOT be merged by the stroke-optional
    pass (it only folds a stroke-None group into a matching stroked group)."""
    region = Region(bbox=(100.0, 100.0, 300.0, 300.0),
                    path_indices=list(range(6)), text_indices=[])
    FILL = (0.8, 0.8, 0.9)
    RED = (1.0, 0.0, 0.0)
    BLUE = (0.0, 0.0, 1.0)
    paths = [
        _square(130, 250, fill=FILL, stroke=RED),
        _square(170, 220, fill=FILL, stroke=RED),
        _square(210, 200, fill=FILL, stroke=RED),
        _square(135, 180, fill=FILL, stroke=BLUE),
        _square(175, 175, fill=FILL, stroke=BLUE),
        _square(215, 170, fill=FILL, stroke=BLUE),
    ]
    series = classify_marks(region, paths, [])
    assert len(series) == 2, f"expected 2 distinct series, got {len(series)}"


# Scatter and line-with-markers fixtures whose data points are markers.
MARKER_FIXTURES = [
    "linear_scatter_1series",
    "two_linear_scatter",
    "gaussian_clusters_3",
    "sqrt_scatter_large",
    "noisy_quadratic_scatter",
    "minimal_scatter_nolegend",
    "convergence_semilogy_3",
    "damped_sine_small",
    "power_law_loglog",   # mix of circle + diamond markers
]


def _load_eval():
    spec = importlib.util.spec_from_file_location(
        "eval_extraction",
        Path(__file__).parents[1] / "scripts" / "eval_extraction.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


EVAL = _load_eval()


def _result_to_pred(result) -> dict:
    """Convert an extracted ChartResult to the eval prediction schema."""
    t = result.table
    return {
        "x_axis": {"scale": t.x_axis.scale},
        "y_axis": {"scale": t.y_axis.scale},
        "series": [
            {
                "label": s.label,
                "marker": s.marker,
                "color": list(s.color) if s.color else None,
                "points": s.points,
            }
            for s in t.series
        ],
    }


@pytest.mark.parametrize("name", MARKER_FIXTURES)
def test_extract_matches_truth(name):
    truth = json.loads((FIXTURES / f"{name}.json").read_text())
    results = extract_pdf(str(FIXTURES / f"{name}.pdf"))
    extracted = [r for r in results if r.status == "extracted"]
    assert len(extracted) == 1, f"{name}: expected one extracted chart, got {len(extracted)}"

    pred = _result_to_pred(extracted[0])
    assert len(pred["series"]) == len(truth["series"]), (
        f"{name}: series count {len(pred['series'])} != truth {len(truth['series'])}"
    )

    ok = EVAL.evaluate(pred, truth, tol=0.01)
    assert ok, f"{name}: eval reported point error beyond tolerance"


def test_oversized_legend_bbox_does_not_filter_data_marks():
    """Guard: when legend_bbox covers > _MAX_LEGEND_PLOT_FRAC of the plot area,
    it is likely a mis-detection — data marks inside it must NOT be dropped.

    Regression for charts where the label detector returns an oversized legend
    bbox spanning >50% of the plot box (e.g. 2606.04724 page 21 chart 2), which
    caused blue and red series to be silently lost.
    """
    region = Region(bbox=(100.0, 100.0, 400.0, 400.0),
                    path_indices=list(range(12)), text_indices=[])
    plot_box = (110.0, 110.0, 390.0, 390.0)
    # Oversized legend bbox: covers 80% of the plot box area.
    # plot_box area = 280*280 = 78400; legend 0.8*78400 = oversized
    oversized_legend = (115.0, 115.0, 355.0, 355.0)  # 240*240 / 78400 ≈ 73%

    # 4 blue marks + 4 red marks all inside the oversized legend bbox — they ARE
    # real data but would be lost with naive legend-box filtering.
    paths = (
        [_square(130 + 20 * i, 200, fill=(0.0, 0.0, 1.0)) for i in range(4)]
        + [_square(130 + 20 * i, 250, fill=(1.0, 0.0, 0.0)) for i in range(4)]
        # 4 extra marks well outside legend (shouldn't affect the test)
        + [_square(130 + 20 * i, 300, fill=(0.0, 0.6, 0.0)) for i in range(4)]
    )
    series = classify_marks(region, paths, [], plot_box=plot_box,
                            legend_bbox=oversized_legend)
    # All three colour groups should survive — legend box filtering suppressed.
    assert len(series) == 3, (
        f"expected 3 series with oversized legend guard, got {len(series)}"
    )
    counts = sorted(len(s.marks) for s in series)
    assert counts == [4, 4, 4], f"expected [4,4,4] marks, got {counts}"


def test_normal_legend_bbox_still_filters_marks():
    """Sanity check: a properly-sized legend bbox (< _MAX_LEGEND_PLOT_FRAC of
    plot area) still filters marks inside it as before.
    """
    region = Region(bbox=(100.0, 100.0, 400.0, 400.0),
                    path_indices=list(range(8)), text_indices=[])
    plot_box = (110.0, 110.0, 390.0, 390.0)
    # Small legend bbox: covers ~5% of plot area (fits in top-right corner).
    small_legend = (340.0, 120.0, 385.0, 155.0)  # 45*35 / (280*280) ≈ 2%

    # 4 blue marks outside the legend (data) + 4 red marks inside the legend
    # (mini-curve decorations that should be dropped).
    paths = (
        [_square(130 + 20 * i, 250, fill=(0.0, 0.0, 1.0)) for i in range(4)]
        + [_square(345 + 8 * i, 135, fill=(1.0, 0.0, 0.0)) for i in range(4)]
    )
    series = classify_marks(region, paths, [], plot_box=plot_box,
                            legend_bbox=small_legend)
    # Only blue marks survive; red marks (inside valid legend bbox) filtered out.
    assert len(series) == 1, (
        f"expected 1 series (red filtered by small legend), got {len(series)}"
    )
    assert len(series[0].marks) == 4, (
        f"expected 4 blue marks, got {len(series[0].marks)}"
    )


# ---------------------------------------------------------------------------
# Tests for iteration-3 fixes
# ---------------------------------------------------------------------------

def _diamond(cx, cy, *, fill=None, stroke=None, w=3.0, h=5.0):
    """A diamond (rotated square) marker: corners at top (0,h), right (w,0),
    bottom (0,-h), left (-w,0).  5 vertices (4 corners + close).  This matches
    the matplotlib ``D`` marker pattern: top vertex at x ≈ cx."""
    pts = [(cx, cy + h), (cx + w, cy), (cx, cy - h), (cx - w, cy), (cx, cy + h)]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return VPath(points=pts, stroke=stroke, fill=fill, width=1.0, dashes=None,
                 closed=True, bbox=(min(xs), min(ys), max(xs), max(ys)))


def _axis_square(cx, cy, *, fill=None, stroke=None, half=3.0):
    """An axis-aligned square (corners at ±half in both axes).  5 vertices."""
    pts = [(cx - half, cy - half), (cx + half, cy - half),
           (cx + half, cy + half), (cx - half, cy + half),
           (cx - half, cy - half)]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return VPath(points=pts, stroke=stroke, fill=fill, width=1.0, dashes=None,
                 closed=True, bbox=(min(xs), min(ys), max(xs), max(ys)))


def test_diamond_shape_detected():
    """Fix: a 5-vertex path with diamond geometry (top vertex at x≈cx) must be
    classified as 'diamond', not 'square'.

    The matplotlib ``D`` marker has 4 corners at top/right/bottom/left — the
    top vertex is at x = centroid x, unlike a square where the top vertices are
    at x = cx ± w/2.
    """
    from pdf_chart2table.marks import _shape_of
    region = Region(bbox=(100.0, 100.0, 300.0, 300.0),
                    path_indices=list(range(4)), text_indices=[])
    # 4 diamond markers at different positions (same fill colour).
    paths = [_diamond(130 + 30 * i, 200, fill=(0.8, 0.2, 0.4)) for i in range(4)]
    series = classify_marks(region, paths, [])
    # All 4 diamonds should form 1 series with shape "diamond".
    assert len(series) == 1, f"expected 1 diamond series, got {len(series)}"
    assert series[0].shape == "diamond", (
        f"expected shape 'diamond', got '{series[0].shape}'"
    )
    assert len(series[0].marks) == 4, (
        f"expected 4 marks, got {len(series[0].marks)}"
    )


def test_square_still_square():
    """Fix sanity: an axis-aligned 5-vertex square must still classify as
    'square', not be mislabelled as 'diamond'."""
    from pdf_chart2table.marks import _shape_of
    region = Region(bbox=(100.0, 100.0, 300.0, 300.0),
                    path_indices=list(range(4)), text_indices=[])
    paths = [_axis_square(130 + 30 * i, 200, fill=(0.2, 0.6, 0.9)) for i in range(4)]
    series = classify_marks(region, paths, [])
    assert len(series) == 1, f"expected 1 square series, got {len(series)}"
    assert series[0].shape == "square", (
        f"expected shape 'square', got '{series[0].shape}'"
    )


def test_large_triangle_marker_not_dropped():
    """Fix: an open triangle marker whose height exceeds 10% of the region
    height must NOT be dropped when it is a recognised triangle shape.

    Astronomy upper-limit arrows are drawn as open triangles (no fill) that
    can span 12-15% of the plot height.  The relaxed _MAX_MARK_FRAC_KNOWN = 20%
    threshold must allow them through.
    """
    # Region 200 px tall.  Triangle bh = 24 px = 12% of region height.
    # This exceeds the default _MAX_MARK_FRAC = 10% but not the known-shape
    # limit of 20%.
    region = Region(bbox=(100.0, 100.0, 300.0, 300.0),
                    path_indices=list(range(2)), text_indices=[])
    # Open triangle (no fill): 3 vertices forming a downward-pointing triangle.
    pts_a = [(150.0, 210.0), (140.0, 186.0), (160.0, 186.0)]  # bh=24, bw=20
    pts_b = [(200.0, 230.0), (188.0, 202.0), (212.0, 202.0)]
    def _tri_path(pts, stroke):
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return VPath(points=pts, stroke=stroke, fill=None, width=1.0,
                     dashes=None, closed=True,
                     bbox=(min(xs), min(ys), max(xs), max(ys)))
    paths = [_tri_path(pts_a, (0.86, 0.08, 0.24)),
             _tri_path(pts_b, (0.86, 0.08, 0.24))]
    series = classify_marks(region, paths, [])
    total = sum(len(s.marks) for s in series)
    assert total == 2, (
        f"both large triangles should be captured, got {total} marks"
    )


def test_faint_series_locus_guard_rejects_boundary_marks():
    """Fix: a 2-mark group where one mark is in the tolerance zone at the
    plot-box boundary is dropped by the locus guard.

    Marginal-panel contour points can sit just inside the 3% tolerance fringe
    of the main panel's plot box.  The locus guard requires all marks in small
    groups to be strictly inside (not in the tolerance zone).

    Chart setup:
    - plot_box is (130, 130, 270, 270).  x-span = 140, tolerance = 4.2.
    - Strong blue series (5 marks) inside the box.
    - Red 2-mark group: one mark at x=126 (4 px outside box left edge) — that
      would normally be DROPPED by _in_plot_box (outside even with tolerance).

    For the guard test: one mark is just barely inside the tolerance (x=127 is
    3 px outside, within 4.2 px tol → passes _in_plot_box) but NOT strictly
    inside (x < 130).  The locus guard removes this group.
    """
    plot_box = (130.0, 130.0, 270.0, 270.0)
    region = Region(bbox=(100.0, 100.0, 300.0, 300.0),
                    path_indices=list(range(7)), text_indices=[])

    # Strong anchor: 5 blue marks, all strictly inside.
    strong = [_square(150 + 20 * i, 200, fill=(0.0, 0.0, 1.0)) for i in range(5)]

    # Faint 2-mark red group: one mark strictly inside, one at x=127 (boundary zone).
    # x=127 is 3 px outside the left edge 130; tolerance is 4.2 px so it passes
    # _in_plot_box but fails _strictly_in_plot_box.
    faint = [_square(200, 200, fill=(1.0, 0.0, 0.0)),   # inside
             _square(127, 220, fill=(1.0, 0.0, 0.0))]    # boundary zone

    paths = strong + faint

    series = classify_marks(region, paths, [], plot_box=plot_box)
    shapes_and_counts = {(s.fill, len(s.marks)) for s in series}

    # Blue strong series should survive.
    assert any(f == (0.0, 0.0, 1.0) and n == 5 for f, n in shapes_and_counts), (
        "strong blue series should be kept"
    )
    # Red faint group (one mark at boundary) should be dropped by locus guard.
    assert not any(f == (1.0, 0.0, 0.0) for f, n in shapes_and_counts), (
        "boundary-proximate 2-mark red group should be rejected by locus guard"
    )


def test_faint_series_inside_box_not_dropped():
    """Sanity: a 2-mark group where ALL marks are strictly inside the plot box
    must still be admitted by the locus guard (normal faint-series recovery).

    This ensures the guard only removes boundary-adjacent groups, not all small
    groups.
    """
    plot_box = (130.0, 130.0, 270.0, 270.0)
    region = Region(bbox=(100.0, 100.0, 300.0, 300.0),
                    path_indices=list(range(7)), text_indices=[])

    # Strong anchor: 5 blue marks.
    strong = [_square(150 + 20 * i, 200, fill=(0.0, 0.0, 1.0)) for i in range(5)]

    # Faint 2-mark green group, both strictly inside.
    faint = [_square(160, 190, fill=(0.0, 0.8, 0.0)),
             _square(200, 210, fill=(0.0, 0.8, 0.0))]

    paths = strong + faint

    series = classify_marks(region, paths, [], plot_box=plot_box)
    green_series = [s for s in series if s.fill == (0.0, 0.8, 0.0)]
    assert len(green_series) == 1, (
        f"faint green series (strictly inside box) should be kept, "
        f"got {len(green_series)}"
    )
    assert len(green_series[0].marks) == 2, (
        f"expected 2 green marks, got {len(green_series[0].marks)}"
    )


# ---------------------------------------------------------------------------
# Regression: stroked cross / plus / open-square / triangle marker glyphs must
# be recognised as MARKERS (not rejected as ~1-D segments and swept into a fake
# line series).  Repro: 2202.08374_p4c4 (× series traced as a 40-pt zig-zag).
# ---------------------------------------------------------------------------

def _cross_glyph(cx, cy, *, stroke, half=0.7):
    """A small ``×`` marker: two diagonal strokes flattened to a 4-vertex open
    polyline whose vertices sit at the bbox corners (TL→BR→BL→TR)."""
    pts = [(cx - half, cy - half), (cx + half, cy + half),
           (cx - half, cy + half), (cx + half, cy - half)]
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    return VPath(points=pts, stroke=stroke, fill=None, width=1.0, dashes=None,
                 closed=False, bbox=(min(xs), min(ys), max(xs), max(ys)))


def _plus_glyph(cx, cy, *, stroke, half=0.7):
    """A small ``+`` marker: vertical + horizontal stroke flattened to a 4-vertex
    open polyline whose vertices sit at the bbox edge midpoints."""
    pts = [(cx, cy - half), (cx, cy + half), (cx - half, cy), (cx + half, cy)]
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    return VPath(points=pts, stroke=stroke, fill=None, width=1.0, dashes=None,
                 closed=False, bbox=(min(xs), min(ys), max(xs), max(ys)))


def _open_square(cx, cy, *, stroke, half=0.8):
    pts = [(cx - half, cy - half), (cx + half, cy - half), (cx + half, cy + half),
           (cx - half, cy + half), (cx - half, cy - half)]
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    return VPath(points=pts, stroke=stroke, fill=None, width=1.0, dashes=None,
                 closed=False, bbox=(min(xs), min(ys), max(xs), max(ys)))


def test_small_cross_glyph_classified_as_x():
    """A small open ``×`` (4 corner-anchored vertices) classifies as 'cross'."""
    from pdf_chart2table.marks import _shape_of
    assert _shape_of(_cross_glyph(200, 200, stroke=(0.25, 0.25, 0.25))) == "cross"


def test_small_plus_glyph_classified_as_plus():
    """A small open ``+`` (4 midpoint-anchored vertices) classifies as 'plus'."""
    from pdf_chart2table.marks import _shape_of
    assert _shape_of(_plus_glyph(200, 200, stroke=(0.25, 0.25, 0.25))) == "plus"


def test_small_cross_markers_recognised_not_rejected():
    """A series of small ``×`` cross glyphs (min bbox side ~1.4 px, below the old
    strict 1.5 min-side) must be recognised as ONE marker series, not rejected.

    Repro: the 2202.08374_p4c4 r_he × series — each glyph was rejected as a
    ~1-D segment and the strokes were then merged into a 40-pt zig-zag line."""
    region = Region(bbox=(100.0, 100.0, 300.0, 300.0),
                    path_indices=list(range(6)), text_indices=[])
    gray = (0.25, 0.25, 0.25)
    paths = [_cross_glyph(130 + 25 * i, 200 - 5 * i, stroke=gray, half=0.7)
             for i in range(6)]
    series = classify_marks(region, paths, [])
    assert len(series) == 1, f"expected 1 cross series, got {len(series)}"
    assert series[0].shape == "cross"
    assert len(series[0].marks) == 6


def test_small_open_square_markers_recognised():
    """Small open (unfilled) square glyphs form one 's' marker series."""
    region = Region(bbox=(100.0, 100.0, 300.0, 300.0),
                    path_indices=list(range(6)), text_indices=[])
    blue = (0.0, 0.45, 0.74)
    paths = [_open_square(130 + 25 * i, 200 - 5 * i, stroke=blue, half=0.8)
             for i in range(6)]
    series = classify_marks(region, paths, [])
    assert len(series) == 1, f"expected 1 square series, got {len(series)}"
    assert series[0].shape == "square"
    assert len(series[0].marks) == 6


# ---------------------------------------------------------------------------
# Regression: a FILLED ×/+ scatter glyph must classify as cross/plus, not
# triangle.  matplotlib scatter emits ``×``/``+`` strokes with a non-None fill,
# so the old ``if filled: return "triangle"`` fast-path mis-sent every filled
# cross/plus to triangle (-> rendered as a square).  Repro: 2110.09149_p10c1.
# ---------------------------------------------------------------------------

def _filled_cross(cx, cy, *, half=7.0):
    """A matplotlib-scatter ``×``: 4 corner-anchored vertices, non-None fill."""
    pts = [(cx - half, cy + half), (cx + half, cy - half),
           (cx - half, cy - half), (cx + half, cy + half)]
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    return VPath(points=pts, stroke=(0.12, 0.47, 0.71), fill=(0.12, 0.47, 0.71),
                 width=2.0, dashes=None, closed=False,
                 bbox=(min(xs), min(ys), max(xs), max(ys)))


def _filled_plus(cx, cy, *, half=7.0):
    """A matplotlib-scatter ``+``: 4 edge-midpoint vertices, non-None fill."""
    pts = [(cx - half, cy), (cx + half, cy), (cx, cy + half), (cx, cy - half)]
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    return VPath(points=pts, stroke=(0.12, 0.47, 0.71), fill=(0.12, 0.47, 0.71),
                 width=2.0, dashes=None, closed=False,
                 bbox=(min(xs), min(ys), max(xs), max(ys)))


def _filled_triangle(cx, cy, *, half=7.0):
    """A matplotlib-scatter ``^``: 3 distinct vertices + a closing repeat, fill."""
    pts = [(cx, cy - half), (cx - half, cy + half), (cx + half, cy + half),
           (cx, cy - half)]
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    return VPath(points=pts, stroke=(0.12, 0.47, 0.71), fill=(0.12, 0.47, 0.71),
                 width=2.0, dashes=None, closed=False,
                 bbox=(min(xs), min(ys), max(xs), max(ys)))


def test_filled_cross_glyph_classified_as_cross():
    """A FILLED ``×`` scatter glyph (4 corner vertices) classifies as 'cross'."""
    from pdf_chart2table.primitives import shape_of
    assert shape_of(_filled_cross(200, 200)) == "cross"


def test_filled_plus_glyph_classified_as_plus():
    """A FILLED ``+`` scatter glyph (4 midpoint vertices) classifies as 'plus'."""
    from pdf_chart2table.primitives import shape_of
    assert shape_of(_filled_plus(200, 200)) == "plus"


def test_filled_triangle_glyph_still_triangle():
    """Guard: a FILLED triangle (3 distinct vertices) must STAY 'triangle' and
    not be mis-sent to cross/plus by the vertex-count routing."""
    from pdf_chart2table.primitives import shape_of
    assert shape_of(_filled_triangle(200, 200)) == "triangle"


def _ngon(cx, cy, n, r=4.0, fill=(0.2, 0.4, 0.6)):
    """A closed regular n-gon (a circle flattened to n vertices)."""
    import math
    pts = [(cx + r * math.cos(2 * math.pi * k / n),
            cy + r * math.sin(2 * math.pi * k / n)) for k in range(n)]
    pts.append(pts[0])
    xs = [x for x, _ in pts]
    ys = [y for _, y in pts]
    return VPath(points=pts, stroke=None, fill=fill, width=None, dashes=None,
                 closed=True, bbox=(min(xs), min(ys), max(xs), max(ys)))


def _star(cx, cy, tips=5, r_out=5.0, r_in=2.0, fill=(0.2, 0.4, 0.6)):
    """A closed star: alternating long/short radii (the real star geometry)."""
    import math
    pts = []
    for k in range(2 * tips):
        r = r_out if k % 2 == 0 else r_in
        a = math.pi * k / tips
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    pts.append(pts[0])
    xs = [x for x, _ in pts]
    ys = [y for _, y in pts]
    return VPath(points=pts, stroke=None, fill=fill, width=None, dashes=None,
                 closed=True, bbox=(min(xs), min(ys), max(xs), max(ys)))


def test_circle_flattened_to_few_vertices_is_circle_not_star():
    """A filled circle flattened to 9-39 vertices must classify as 'circle'.

    The old ``n >= 9 -> star`` rule called every such glyph a star, so circle
    markers rendered as '*' (2002.09528, 2005.00241 K-valley)."""
    from pdf_chart2table.primitives import shape_of
    for n in (12, 15, 24, 33):
        assert shape_of(_ngon(200, 200, n)) == "circle", n


def test_real_star_glyph_still_classified_as_star():
    """A genuine star (regular alternating radii) must STILL classify as 'star'."""
    from pdf_chart2table.primitives import shape_of
    assert shape_of(_star(200, 200, tips=5)) == "star"
    assert shape_of(_star(200, 200, tips=6)) == "star"


def _word_cands(widths, heights, cy=50.0, x0=10.0, gap=0.2):
    """A same-row run of glyphs placed letter-tight (bbox gap ``gap``)."""
    cands, x = [], x0
    for k, (w, h) in enumerate(zip(widths, heights)):
        x += w / 2
        cands.append((k, x, cy, w, h))
        x += w / 2 + gap
    return cands


def test_text_run_flags_size_varying_tight_word():
    """A tight horizontal run of size-VARYING glyphs (a legend word drawn as
    vector outlines, e.g. 'Experiment') is flagged as text, not data."""
    from pdf_chart2table.marks import _text_run_indices
    cands = _word_cands([4, 3, 5, 2.5, 4, 3.5], [5, 4, 5, 4, 5, 4])
    assert _text_run_indices(cands) == {0, 1, 2, 3, 4, 5}


def test_text_run_keeps_uniform_spaced_data_row():
    """A flat data series (identical glyphs, spaced apart) is NEVER flagged."""
    from pdf_chart2table.marks import _text_run_indices
    cands = [(k, 10.0 + k * 30.0, 50.0, 4.0, 4.0) for k in range(6)]
    assert _text_run_indices(cands) == set()


def test_text_run_keeps_uniform_tight_data_row():
    """Even tightly packed marks are kept when they are IDENTICAL in size (CV~0):
    only size-varying runs (letters) are text."""
    from pdf_chart2table.marks import _text_run_indices
    cands = [(k, 10.0 + k * 4.2, 50.0, 4.0, 4.0) for k in range(6)]
    assert _text_run_indices(cands) == set()


def test_text_run_flags_whole_line_including_short_neighbour():
    """A short word ('Fit', 2 glyphs) on the SAME baseline as a confirmed word is
    flagged too (whole text line dropped), so it leaves no 2-point stub series."""
    from pdf_chart2table.marks import _text_run_indices
    word = _word_cands([4, 3, 5, 2.5, 4, 3.5], [5, 4, 5, 4, 5, 4])
    # a short 2-glyph word far to the right on the same row
    short = [(6, 90.0, 50.0, 3.0, 5.0), (7, 94.0, 50.0, 2.0, 4.0)]
    assert _text_run_indices(word + short) == {0, 1, 2, 3, 4, 5, 6, 7}


def test_marker_shape_render_classifies_cross_and_plus():
    """The renderer's ``_marker_shape`` must return 'x'/'+' for cross/plus glyphs
    (previously returned 's', so ×/+ markers were drawn as squares), and keep
    square/triangle/circle correct."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "render_restyle_prototype",
        Path(__file__).parents[1] / "scripts" / "render_restyle_prototype.py",
    )
    rrp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rrp)
    assert rrp._marker_shape(_filled_cross(200, 200)) == "x"
    assert rrp._marker_shape(_filled_plus(200, 200)) == "+"
    # Guard the non-cross shapes resolve correctly.
    assert rrp._marker_shape(_axis_square(200, 200, fill=(0.2, 0.6, 0.9))) == "s"
    # A triangle must classify as '^', not 's' (the old code had no triangle case
    # so it fell through to square -- 2003.07592 Isotropic rendered as squares).
    assert rrp._marker_shape(_filled_triangle(200, 200)) == "^"
