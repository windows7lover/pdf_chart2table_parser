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
