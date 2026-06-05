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
