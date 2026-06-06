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
    """A lone isolated coloured marker is a degenerate tiny-n group, not a real
    series -> rejected, region skips."""
    paths = [_square(150, 200, fill=(0.0, 0.0, 1.0))]
    res = extract_region(_region(len(paths)), _axes(), paths, texts=[])
    assert res.status == "skipped"
    assert res.skip_reason == "no clean series found"


def test_tiny_n_annotation_series_dropped():
    """Two markers at distinct positions are a degenerate tiny-n group (e.g.
    'Peak' annotation crosses), not a real series -> rejected, region skips."""
    paths = [_square(150, 200, fill=(0.0, 0.0, 1.0)),
             _square(220, 160, fill=(0.0, 0.0, 1.0))]
    res = extract_region(_region(len(paths)), _axes(), paths, texts=[])
    assert res.status == "skipped"
    assert res.skip_reason == "no clean series found"


def test_out_of_box_markers_dropped_via_plot_box():
    """Markers outside the calibrated spine box are dropped; only in-box data
    is kept. Axes pixel_range is the spine box, narrower than region.bbox."""
    x = Axis(scale="linear", pixel_range=(130.0, 270.0),
             calibration=_calib(0.05, -5.0))
    y = Axis(scale="linear", pixel_range=(130.0, 270.0),
             calibration=_calib(-0.05, 15.0))
    in_box = [_square(150 + 20 * i, 250 - 8 * i, fill=(0.0, 0.0, 1.0))
              for i in range(4)]
    out = [_square(110, 110, fill=(0.0, 0.0, 1.0))]  # outside spine box
    res = extract_region(_region(5), (x, y), in_box + out, texts=[])
    assert res.status == "extracted"
    assert sum(len(s.points) for s in res.table.series) == 4


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


def _dense_line(x0, y0, x1, y1, n=20, *, stroke=(0.0, 0.0, 0.0)):
    """A polyline with ``n`` vertices simulating a data curve (not a mark)."""
    pts = [(x0 + (x1 - x0) * i / (n - 1), y0 + (y1 - y0) * i / (n - 1))
           for i in range(n)]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return VPath(points=pts, stroke=stroke, fill=None, width=1.0, dashes=None,
                 closed=False, bbox=(min(xs), min(ys), max(xs), max(ys)))


def _spine(x0, y0, x1, y1, *, stroke=(0.0, 0.0, 0.0)):
    """A 2-vertex spine/tick segment."""
    return VPath(points=[(x0, y0), (x1, y1)], stroke=stroke, fill=None,
                 width=1.0, dashes=None, closed=False,
                 bbox=(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))


def test_sparse_on_dense_skips():
    """A region with few marker points but many dense closed-glyph paths is
    rejected as sparse noise on a dense chart, not emitted as extracted.

    Dense paths that are CLOSED (filled glyphs, not open data lines) cannot be
    recovered as line series, so n_points stays at the few marker points.  The
    is_sparse_on_dense guard then fires because n_points <= threshold AND the
    region contains several dense (high-vertex) paths."""
    # 6 data markers (2 series x 3 marks each)
    markers = [_square(130 + 20 * i, 250 - 8 * i, fill=(0.0, 0.0, 1.0))
               for i in range(3)]
    markers += [_square(135 + 20 * i, 240 - 8 * i, fill=(1.0, 0.0, 0.0))
                for i in range(3)]
    # 3 dense closed-glyph paths (each > 8 vertices, closed=True -> not a line).
    # Closed filled shapes (marker glyphs drawn individually) are a dense chart
    # signature but cannot be extracted as line series.
    def _closed_poly(cx, cy, r=5.0, n=20, fill=(0.5, 0.5, 0.5)):
        import math
        pts = [(cx + r * math.cos(2 * math.pi * k / n),
                cy + r * math.sin(2 * math.pi * k / n)) for k in range(n)]
        pts.append(pts[0])
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return VPath(points=pts, stroke=None, fill=fill, width=None, dashes=None,
                     closed=True, bbox=(min(xs), min(ys), max(xs), max(ys)))
    dense = [_closed_poly(150, 150 + 20 * i, r=50.0, n=20) for i in range(3)]
    # 75 spine/tick 2-vertex segments: total_paths = 6+3+75 = 84, ratio = 84/6 = 14 > 12
    spines = [_spine(110 + i, 100, 110 + i, 290) for i in range(75)]
    all_paths = markers + dense + spines
    region = Region(bbox=(100.0, 100.0, 300.0, 300.0),
                    path_indices=list(range(len(all_paths))),
                    text_indices=[])
    res = extract_region(region, _axes(), all_paths, texts=[])
    assert res.status == "skipped"
    assert res.skip_reason == "sparse markers on dense chart"


def test_sparse_legitimate_no_dense_lines_extracts():
    """A sparse but genuine scatter chart with NO dense line paths (dense=0)
    must NOT be rejected by the sparse-on-dense guard, even when ratio > threshold."""
    # 7 markers (legitimate sparse scatter), no dense lines at all
    markers = [_square(130 + 20 * i, 250 - 8 * i, fill=(0.0, 0.0, 1.0))
               for i in range(7)]
    # Only 2-vertex spine/tick paths (non-dense): 7+98=105 paths, ratio=105/7=15 > 12
    # Without dense lines the sparse-on-dense guard must NOT fire.
    spines = [_spine(110 + i, 100, 110 + i, 290) for i in range(98)]
    all_paths = markers + spines
    region = Region(bbox=(100.0, 100.0, 300.0, 300.0),
                    path_indices=list(range(len(all_paths))),
                    text_indices=[])
    res = extract_region(region, _axes(), all_paths, texts=[])
    assert res.status == "extracted"
    assert sum(len(s.points) for s in res.table.series) == 7
