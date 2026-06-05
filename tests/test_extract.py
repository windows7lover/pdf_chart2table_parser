"""Tests for extract.py precision guards.

Covers the iteration-6 precision fixes owned by extract.py:
  * a region with no usable data points must SKIP, not emit an empty
    "extracted" result (the manifest's 0-series / 0-point bug);
  * an extraction's confidence reflects both axes being calibrated.
"""

from __future__ import annotations

from pdf_chart2table.extract import extract_region
from pdf_chart2table.model import Axis, Path as VPath, Region


def _calib(a, b, scale="linear", r2=1.0):
    return {"scale": scale, "a": a, "b": b, "r2": r2}


def _axes(x_r2=1.0, y_r2=1.0):
    """A pair of calibrated x/y axes spanning the region used below."""
    x = Axis(scale="linear", pixel_range=(100.0, 300.0),
             calibration=_calib(0.05, -5.0, r2=x_r2))
    y = Axis(scale="linear", pixel_range=(100.0, 300.0),
             calibration=_calib(-0.05, 15.0, r2=y_r2))
    return x, y


def _square(cx, cy, *, fill=None, stroke=None, half=2.5):
    pts = [(cx - half, cy - half), (cx + half, cy - half), (cx + half, cy + half),
           (cx - half, cy + half), (cx - half, cy - half)]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return VPath(points=pts, stroke=stroke, fill=fill, width=1.0, dashes=None,
                 closed=True, bbox=(min(xs), min(ys), max(xs), max(ys)))


def _region(n_paths):
    return Region(bbox=(100.0, 100.0, 300.0, 300.0),
                  path_indices=list(range(n_paths)))


def test_empty_region_skips():
    """Both axes calibrated but no data marks -> skipped, not empty-extracted."""
    res = extract_region(_region(0), _axes(), paths=[], texts=[])
    assert res.status == "skipped"
    assert res.table is None
    assert res.skip_reason


def test_single_point_region_skips():
    """A lone isolated marker is noise (e.g. a boxplot flier), not data -> skip."""
    paths = [_square(150, 200, fill=(0.0, 0.0, 1.0))]
    res = extract_region(_region(len(paths)), _axes(), paths, texts=[])
    assert res.status == "skipped"
    assert res.skip_reason == "no data points"


def test_real_series_extracts_with_full_confidence():
    """Several markers in one series -> extracted; both axes clean -> conf 1.0."""
    paths = [_square(130 + 20 * i, 250 - 8 * i, fill=(0.0, 0.0, 1.0))
             for i in range(5)]
    res = extract_region(_region(len(paths)), _axes(), paths, texts=[])
    assert res.status == "extracted"
    assert res.table is not None and len(res.table.series) == 1
    assert sum(len(s.points) for s in res.table.series) == 5
    assert res.table.confidence == 1.0


def test_confidence_tracks_weaker_axis_fit():
    """Confidence is the weaker axis's R^2 (a borderline fit lowers it)."""
    paths = [_square(130 + 20 * i, 250 - 8 * i, fill=(0.0, 0.0, 1.0))
             for i in range(5)]
    res = extract_region(_region(len(paths)), _axes(x_r2=1.0, y_r2=0.98),
                         paths, texts=[])
    assert res.status == "extracted"
    assert res.table.confidence == 0.98
