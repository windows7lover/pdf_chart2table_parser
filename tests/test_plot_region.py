"""Tests for plot_region: detecting chart plotting region(s) per page.

Region count must match the fixture's ``n_panels`` (1 for the flat schema).
Multi-panel fixtures must be row-major ordered, and shared-x/shared-y detected
from missing tick-label text must match the fixture truth (mirrors check_m1.py).
"""

from __future__ import annotations

import pytest

from conftest import fixture_names, load_truth, multipanel_names, pdf_path
from pdf_chart2table.pdf_vector import load_pdf
from pdf_chart2table.plot_region import detect_regions

ALL_FIXTURES = fixture_names()
MULTIPANEL = multipanel_names()
SINGLEPANEL = [n for n in ALL_FIXTURES if n not in MULTIPANEL]

# Bands (points) below / left of a panel in which to scan for tick labels.
_TICK_BELOW = 18.0
_TICK_LEFT = 30.0


def _regions_for(name):
    page = load_pdf(pdf_path(name))[0]
    regions = detect_regions(page.paths, page.texts, page.width, page.height)
    return page, regions


def _has_xtick_text(page, region) -> bool:
    x0, _, x1, y1 = region.bbox
    for t in page.texts:
        cx = 0.5 * (t.bbox[0] + t.bbox[2])
        cy = 0.5 * (t.bbox[1] + t.bbox[3])
        if x0 - 2 <= cx <= x1 + 2 and y1 < cy <= y1 + _TICK_BELOW:
            return True
    return False


def _has_ytick_text(page, region) -> bool:
    x0, y0, _, y1 = region.bbox
    for t in page.texts:
        cx = 0.5 * (t.bbox[0] + t.bbox[2])
        cy = 0.5 * (t.bbox[1] + t.bbox[3])
        if y0 - 2 <= cy <= y1 + 2 and x0 - _TICK_LEFT <= cx < x0:
            return True
    return False


def _detect_shared(page, regions) -> tuple[bool, bool]:
    if len(regions) <= 1:
        return False, False
    max_row = max(r.row for r in regions)
    min_col = min(r.col for r in regions)
    shared_x = any(r.row != max_row and not _has_xtick_text(page, r)
                   for r in regions)
    shared_y = any(r.col != min_col and not _has_ytick_text(page, r)
                   for r in regions)
    return shared_x, shared_y


def test_same_panel_overlap_detection():
    """Strong overlaps are 'same panel'; disjoint / weak overlaps are not."""
    from pdf_chart2table.plot_region import _same_panel

    a = (100.0, 100.0, 200.0, 200.0)
    # Identical -> same panel (IoU 1).
    assert _same_panel(a, a)
    # b fully contains a (containment 1.0 of the smaller) -> same panel.
    b = (90.0, 90.0, 210.0, 210.0)
    assert _same_panel(a, b)
    # Disjoint -> not same panel.
    assert not _same_panel(a, (300.0, 300.0, 400.0, 400.0))
    # Slight corner touch (low IoU, low containment) -> not same panel.
    assert not _same_panel(a, (190.0, 190.0, 290.0, 290.0))


def test_dedup_keeps_one_per_overlapping_pair():
    """Two overlapping candidates collapse to one; the white patch is preferred."""
    from pdf_chart2table.plot_region import _dedup_candidates

    outer = (90.0, 90.0, 210.0, 210.0)   # encloses inner; not a patch
    inner = (100.0, 100.0, 200.0, 200.0)  # white axes-patch
    # With no paths/texts neither calibrates, so the is_patch tag decides.
    kept = _dedup_candidates([(outer, False), (inner, True)], [], [])
    assert kept == [inner]


def test_dedup_preserves_separate_panels():
    """Non-overlapping candidates (separate subplots) all survive."""
    from pdf_chart2table.plot_region import _dedup_candidates

    left = (100.0, 100.0, 200.0, 200.0)
    right = (300.0, 100.0, 400.0, 200.0)
    kept = _dedup_candidates([(left, True), (right, True)], [], [])
    assert set(kept) == {left, right}


def _cell(cx, cy, w=4.0, h=4.0):
    from pdf_chart2table.model import Path as VPath
    x0, y0, x1, y1 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    return VPath(points=pts, stroke=None, fill=(0.2, 0.4, 0.8), width=None,
                 dashes=None, closed=True, bbox=(x0, y0, x1, y1))


def test_chart_type_gate_skips_heatmap_grid():
    """A dense grid of filled cells tiling the panel is gated out (heatmap)."""
    from pdf_chart2table.plot_region import _is_chart_type

    bbox = (100.0, 100.0, 200.0, 200.0)
    # 8x8 packed grid of abutting filled cells -> heatmap.
    paths = [_cell(102.5 + 12.5 * c, 102.5 + 12.5 * r)
             for r in range(8) for c in range(8)]
    assert not _is_chart_type(bbox, paths)


def test_chart_type_gate_keeps_scatter_cloud():
    """A sparse scatter of marker glyphs (few cells per implied grid) is kept."""
    from pdf_chart2table.plot_region import _is_chart_type

    bbox = (100.0, 100.0, 200.0, 200.0)
    # Markers at many distinct, irregular x/y -> low grid fill -> NOT a heatmap.
    pts = [(112, 180), (128, 150), (141, 133), (159, 121), (172, 160),
           (135, 190), (118, 142), (163, 175), (149, 128), (124, 167),
           (155, 145), (170, 138), (130, 172), (145, 155), (160, 112),
           (115, 125), (138, 118), (152, 185), (167, 152), (122, 158)]
    paths = [_cell(x, y) for x, y in pts]
    assert _is_chart_type(bbox, paths)


def _bar(x0, bottom, w, h, fill=(0.07, 0.44, 0.75)):
    """An upright filled bar resting on ``bottom`` (a tall axis-aligned rect)."""
    from pdf_chart2table.model import Path as VPath
    y0, y1, x1 = bottom - h, bottom, x0 + w
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    return VPath(points=pts, stroke=None, fill=fill, width=None,
                 dashes=None, closed=True, bbox=(x0, y0, x1, y1))


def test_chart_type_gate_skips_bar_chart():
    """A contiguous run of bottom-aligned upright bars is gated out (histogram)."""
    from pdf_chart2table.plot_region import _is_chart_type

    bbox = (100.0, 100.0, 200.0, 200.0)
    # 8 bars of width 10 abutting at left edges 100,110,...; varying heights;
    # all resting on baseline y=190 -> a histogram.
    heights = [80, 65, 50, 40, 30, 22, 15, 10]
    paths = [_bar(100 + 10 * i, 190.0, 10.0, heights[i]) for i in range(8)]
    assert not _is_chart_type(bbox, paths)


def test_chart_type_gate_keeps_spike_line_plot():
    """Thin, widely spaced vertical marks (diffraction spikes / error bars) stay.

    They share a baseline but do NOT abut (width << centre-spacing), so they are
    a real line/scatter overlay and must be kept, not mistaken for bars.
    """
    from pdf_chart2table.plot_region import _is_chart_type

    bbox = (100.0, 100.0, 200.0, 200.0)
    # 1pt-wide spikes spaced 12pt apart on baseline y=190 -> NOT bars.
    paths = [_bar(100 + 12 * i, 190.0, 1.0, 50.0) for i in range(8)]
    assert _is_chart_type(bbox, paths)


def test_chart_type_gate_keeps_square_markers():
    """Square marker glyphs (scatter) are not 'tall', so never read as bars."""
    from pdf_chart2table.plot_region import _is_chart_type

    bbox = (100.0, 100.0, 200.0, 200.0)
    # Square 6x6 marker glyphs at assorted positions sharing no real baseline.
    pts = [(112, 180), (128, 150), (141, 133), (159, 121), (172, 160),
           (135, 190), (118, 142), (163, 175), (149, 128), (124, 167)]
    paths = [_cell(x, y, 6.0, 6.0) for x, y in pts]
    assert _is_chart_type(bbox, paths)


def _gray_dot(cx, cy, r=0.7, color=(0.66, 0.66, 0.66)):
    """A tiny grayscale filled bezier circle (simulated density-map glyph)."""
    from pdf_chart2table.model import Path as VPath
    x0, y0, x1, y1 = cx - r, cy - r, cx + r, cy + r
    pts = [(cx, y0), (x1, cy), (cx, y1), (x0, cy), (cx, y0)]
    return VPath(points=pts, stroke=color, fill=color, width=None,
                 dashes=None, closed=True, bbox=(x0, y0, x1, y1))


def _open_line(x0, x1, y, n=200, stroke=(0.2, 0.5, 0.8)):
    """A stroked-only polyline going left to right (open data line)."""
    from pdf_chart2table.model import Path as VPath
    pts = [(x0 + (x1 - x0) * i / (n - 1), y) for i in range(n)]
    return VPath(points=pts, stroke=stroke, fill=None, width=1.0,
                 dashes=None, closed=False,
                 bbox=(x0, y, x1, y))


def test_2d_gate_skips_uniform_density_map():
    """Hundreds of same-gray tiny glyphs with no open data line -> density map."""
    from pdf_chart2table.plot_region import _2d_map_skip_reason

    bbox = (100.0, 100.0, 400.0, 400.0)
    # 250 identical gray dots spread across the panel.
    import random
    random.seed(42)
    paths = [_gray_dot(random.uniform(102, 398), random.uniform(102, 398))
             for _ in range(250)]
    reason = _2d_map_skip_reason(bbox, paths)
    assert reason is not None
    assert "density" in reason or "contour" in reason


def test_2d_gate_keeps_line_marker_chart():
    """Same gray dots BUT with a wide open stroked data line -> keep it (line+marker)."""
    from pdf_chart2table.plot_region import _2d_map_skip_reason

    bbox = (100.0, 100.0, 400.0, 400.0)
    import random
    random.seed(42)
    dots = [_gray_dot(random.uniform(102, 398), random.uniform(102, 398))
            for _ in range(250)]
    # Add a monotone left-to-right data line spanning the panel.
    line = _open_line(102, 398, 250.0, n=200)
    assert _2d_map_skip_reason(bbox, dots + [line]) is None


def test_2d_gate_skips_dense_fill_lattice():
    """Tiny fills at >0.15/pt^2 density -> dispersion lattice."""
    from pdf_chart2table.plot_region import _2d_map_skip_reason
    from pdf_chart2table.model import Path as VPath

    # 100x100 panel with 2000 tiny 1-pt fills -> density = 0.20/pt^2.
    bbox = (0.0, 0.0, 100.0, 100.0)
    import random
    random.seed(7)
    paths = []
    for _ in range(2000):
        cx, cy = random.uniform(1, 99), random.uniform(1, 99)
        p = VPath(points=[(cx - 0.5, cy), (cx + 0.5, cy), (cx, cy)],
                  stroke=None, fill=(0.1, 0.4, 0.7), width=None,
                  dashes=None, closed=True,
                  bbox=(cx - 0.5, cy - 0.5, cx + 0.5, cy + 0.5))
        paths.append(p)
    reason = _2d_map_skip_reason(bbox, paths)
    assert reason is not None
    assert "dispersion" in reason or "density" in reason


def _tall_band(y0_band, y1_band, n_pts=60, fill=(0.0, 0.45, 0.7)):
    """A many-vertex colored filled polygon spanning [y0_band, y1_band] (credible band)."""
    from pdf_chart2table.model import Path as VPath
    import math
    # Build a zigzag polygon with n_pts vertices so it passes the _BAND_MIN_VERTS check.
    left_x, right_x = 150.0, 200.0
    pts = []
    for i in range(n_pts // 2):
        t = i / (n_pts // 2 - 1)
        y = y0_band + t * (y1_band - y0_band)
        pts.append((left_x + 2 * math.sin(i), y))
    for i in range(n_pts // 2 - 1, -1, -1):
        t = i / (n_pts // 2 - 1)
        y = y0_band + t * (y1_band - y0_band)
        pts.append((right_x + 2 * math.sin(i), y))
    pts.append(pts[0])
    return VPath(points=pts, stroke=None, fill=fill, width=None,
                 dashes=None, closed=True,
                 bbox=(min(p[0] for p in pts), y0_band,
                       max(p[0] for p in pts), y1_band))


def test_2d_gate_skips_tall_fill_band():
    """A large colored polygon spanning the full panel height -> credible band."""
    from pdf_chart2table.plot_region import _2d_map_skip_reason

    bbox = (100.0, 100.0, 300.0, 300.0)  # 200x200 panel
    # Band spans y=85 to y=305 -> height 220 > 0.85 * 200 = 170.
    band = _tall_band(85.0, 305.0)
    reason = _2d_map_skip_reason(bbox, [band])
    assert reason is not None
    assert "band" in reason or "credible" in reason


def test_2d_gate_keeps_moderate_error_band():
    """A colored polygon covering <0.85 of panel height is a confidence band, not rejected."""
    from pdf_chart2table.plot_region import _2d_map_skip_reason

    bbox = (100.0, 100.0, 300.0, 300.0)  # 200x200 panel
    # Band spans y=130 to y=270 -> height 140 = 0.70 * panel height < 0.85 threshold.
    band = _tall_band(130.0, 270.0)
    assert _2d_map_skip_reason(bbox, [band]) is None


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_region_count(name):
    truth = load_truth(name)
    _, regions = _regions_for(name)
    assert len(regions) == truth.get("n_panels", 1)


@pytest.mark.parametrize("name", SINGLEPANEL)
def test_single_panel_region(name):
    _, regions = _regions_for(name)
    assert len(regions) == 1
    region = regions[0]
    assert region.row == 0 and region.col == 0
    assert region.shares_x_with == []
    assert region.shares_y_with == []


@pytest.mark.parametrize("name", MULTIPANEL)
def test_multipanel_row_major_order(name):
    """Regions are sorted top-to-bottom, then left-to-right."""
    _, regions = _regions_for(name)
    keys = [(r.row, r.col) for r in regions]
    assert keys == sorted(keys)
    # Geometric order: non-decreasing top edge, and within a row, left-to-right.
    prev = None
    for r in regions:
        cur = (round(r.bbox[1]), r.bbox[0])
        if prev is not None:
            assert cur >= prev
        prev = cur


@pytest.mark.parametrize("name", MULTIPANEL)
def test_multipanel_grid_dimensions(name):
    truth = load_truth(name)
    n_rows, n_cols = truth["grid"]
    _, regions = _regions_for(name)
    assert max(r.row for r in regions) + 1 == n_rows
    assert max(r.col for r in regions) + 1 == n_cols


@pytest.mark.parametrize("name", MULTIPANEL)
def test_multipanel_shared_axes(name):
    truth = load_truth(name)
    page, regions = _regions_for(name)
    det_sx, det_sy = _detect_shared(page, regions)
    assert det_sx == bool(truth["shared_x"])
    assert det_sy == bool(truth["shared_y"])


def test_split_enclosing_frame_into_inner_panels():
    """A frame wrapping >=2 calibratable inner patches is split into them."""
    from pdf_chart2table.plot_region import _split_enclosing_frames
    from unittest.mock import patch

    frame = (100.0, 100.0, 500.0, 200.0)        # wide whole-figure frame
    left = (110.0, 110.0, 240.0, 190.0)         # inner panel 1
    right = (310.0, 110.0, 440.0, 190.0)        # inner panel 2

    # Both inner patches calibrate; the frame itself is the only candidate.
    with patch("pdf_chart2table.plot_region._n_calibrated_axes",
               side_effect=lambda b, p, t: 2 if b in (left, right) else 0):
        out = _split_enclosing_frames([(frame, False)], [left, right], [], [])
    boxes = {b for b, _ in out}
    assert boxes == {left, right}


def test_split_keeps_single_panel_frame():
    """A frame containing only one inner panel is left unsplit."""
    from pdf_chart2table.plot_region import _split_enclosing_frames
    from unittest.mock import patch

    frame = (100.0, 100.0, 260.0, 200.0)
    inner = (110.0, 110.0, 240.0, 190.0)
    with patch("pdf_chart2table.plot_region._n_calibrated_axes",
               side_effect=lambda b, p, t: 2 if b == inner else 1):
        out = _split_enclosing_frames([(frame, False)], [inner], [], [])
    assert [b for b, _ in out] == [frame]


def _white_rect(x0, y0, x1, y1):
    """A white unstroked rectangle Path (axes-patch sub-panel)."""
    from pdf_chart2table.model import Path as VPath
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    return VPath(points=pts, stroke=None, fill=(1.0, 1.0, 1.0), width=None,
                 dashes=None, closed=True, bbox=(x0, y0, x1, y1))


def test_split_multi_row_boxes_splits_stacked_panels():
    """A merged box with 2+ y-rows of calibratable inner patches is split."""
    from pdf_chart2table.plot_region import _split_multi_row_boxes
    from unittest.mock import patch

    # A merged box spanning two rows of inner patches.
    merged = (100.0, 100.0, 300.0, 400.0)
    top_left = (110.0, 110.0, 190.0, 185.0)   # row 0
    top_right = (210.0, 110.0, 290.0, 185.0)  # row 0
    bot_left = (110.0, 215.0, 190.0, 290.0)   # row 1
    bot_right = (210.0, 215.0, 290.0, 290.0)  # row 1

    paths = [_white_rect(*b) for b in (top_left, top_right, bot_left, bot_right)]
    page_area = 600.0 * 800.0

    # All four inner patches calibrate; merged box does not.
    inner = {top_left, top_right, bot_left, bot_right}
    with patch("pdf_chart2table.plot_region._n_calibrated_axes",
               side_effect=lambda b, p, t: 1 if b in inner else 0):
        result = _split_multi_row_boxes([merged], paths, [], page_area)

    assert set(result) == inner


def test_split_multi_row_boxes_guard_prevents_over_split():
    """If fewer than 2 inner patches calibrate, the outer box is kept intact."""
    from pdf_chart2table.plot_region import _split_multi_row_boxes
    from unittest.mock import patch

    merged = (100.0, 100.0, 300.0, 400.0)
    top = (110.0, 110.0, 290.0, 185.0)   # row 0 — not calibratable
    bot = (110.0, 215.0, 290.0, 290.0)   # row 1 — not calibratable

    paths = [_white_rect(*b) for b in (top, bot)]
    page_area = 600.0 * 800.0

    with patch("pdf_chart2table.plot_region._n_calibrated_axes",
               return_value=0):
        result = _split_multi_row_boxes([merged], paths, [], page_area)

    assert result == [merged]


def test_split_multi_row_boxes_single_row_not_split():
    """A box with inner patches all in one y-row is not split by the row splitter."""
    from pdf_chart2table.plot_region import _split_multi_row_boxes
    from unittest.mock import patch

    merged = (100.0, 100.0, 400.0, 250.0)
    left = (110.0, 110.0, 190.0, 240.0)   # same row
    right = (310.0, 110.0, 390.0, 240.0)  # same row

    paths = [_white_rect(*b) for b in (left, right)]
    page_area = 600.0 * 800.0

    with patch("pdf_chart2table.plot_region._n_calibrated_axes",
               side_effect=lambda b, p, t: 1 if b in (left, right) else 0):
        result = _split_multi_row_boxes([merged], paths, [], page_area)

    assert result == [merged]


def test_split_multi_col_boxes_splits_sidebyside_panels():
    """A merged wide box with 2+ x-column groups of calibratable inner patches is split.

    Models a scatter panel beside a violin/box panel: a single merged spine
    frame spans both, but each panel has its own white axes-patch at a different
    x position.  The col splitter must recover them.
    """
    from pdf_chart2table.plot_region import _split_multi_col_boxes
    from unittest.mock import patch

    # Wide merged frame spanning two side-by-side panels.
    merged = (100.0, 100.0, 500.0, 300.0)
    left = (110.0, 110.0, 240.0, 290.0)   # col 0
    right = (360.0, 110.0, 490.0, 290.0)  # col 1 (same row)

    paths = [_white_rect(*b) for b in (left, right)]
    page_area = 600.0 * 800.0

    inner = {left, right}
    with patch("pdf_chart2table.plot_region._n_calibrated_axes",
               side_effect=lambda b, p, t: 1 if b in inner else 0):
        result = _split_multi_col_boxes([merged], paths, [], page_area)

    assert set(result) == inner


def test_split_multi_col_boxes_guard_prevents_over_split():
    """If fewer than 2 inner patches calibrate, the outer box is kept intact."""
    from pdf_chart2table.plot_region import _split_multi_col_boxes
    from unittest.mock import patch

    merged = (100.0, 100.0, 500.0, 300.0)
    left = (110.0, 110.0, 240.0, 290.0)
    right = (360.0, 110.0, 490.0, 290.0)

    paths = [_white_rect(*b) for b in (left, right)]
    page_area = 600.0 * 800.0

    with patch("pdf_chart2table.plot_region._n_calibrated_axes", return_value=0):
        result = _split_multi_col_boxes([merged], paths, [], page_area)

    assert result == [merged]


def test_split_multi_col_boxes_single_col_not_split():
    """A box with all inner patches in one x-column is not split."""
    from pdf_chart2table.plot_region import _split_multi_col_boxes
    from unittest.mock import patch

    merged = (100.0, 100.0, 300.0, 500.0)
    top = (110.0, 110.0, 290.0, 240.0)   # same col
    bot = (110.0, 360.0, 290.0, 490.0)   # same col

    paths = [_white_rect(*b) for b in (top, bot)]
    page_area = 600.0 * 800.0

    with patch("pdf_chart2table.plot_region._n_calibrated_axes",
               side_effect=lambda b, p, t: 1 if b in (top, bot) else 0):
        result = _split_multi_col_boxes([merged], paths, [], page_area)

    assert result == [merged]


# --- regression: reject markers-on-a-raster-image regions --------------------
from pdf_chart2table.plot_region import _covered_by_image


def test_region_mostly_covered_by_image_is_rejected():
    box = (100.0, 100.0, 200.0, 200.0)  # area 10000
    # An image covering ~64% of the box -> reject.
    assert _covered_by_image(box, [(100.0, 100.0, 180.0, 180.0)])
    # A small image (16%) -> keep.
    assert not _covered_by_image(box, [(100.0, 100.0, 140.0, 140.0)])
    # No images -> keep.
    assert not _covered_by_image(box, [])


def test_2d_map_at_half_coverage_rejected():
    # A 2D density/imshow map covers ~54% of the region (the real 2104.03045 /
    # 2105.10232 band): must be rejected at the 0.50 gate -- these charts emit
    # garbage series from the sparse vector overlay. (Was missed at 0.55.)
    box = (0.0, 0.0, 100.0, 100.0)  # area 10000
    img = (0.0, 0.0, 73.5, 73.5)    # ~0.54 coverage
    assert _covered_by_image(box, [img])
    # A genuine chart with a smaller inset image (~40%) is still KEPT.
    assert not _covered_by_image(box, [(0.0, 0.0, 63.0, 63.0)])


# --- panel-merge regression: adjacent sub-panels must not be fused -----------

def _vseg(x: float, y0: float, y1: float):
    """Vertical spine segment (thin but tall) as a Path."""
    from pdf_chart2table.model import Path as VPath
    pts = [(x, y0), (x, y1)]
    return VPath(points=pts, stroke=(0.0, 0.0, 0.0), fill=None,
                 width=0.5, dashes=None, closed=False,
                 bbox=(x, y0, x, y1))


def _hseg(y: float, x0: float, x1: float):
    """Horizontal spine segment (thin but wide) as a Path."""
    from pdf_chart2table.model import Path as VPath
    pts = [(x0, y), (x1, y)]
    return VPath(points=pts, stroke=(0.0, 0.0, 0.0), fill=None,
                 width=0.5, dashes=None, closed=False,
                 bbox=(x0, y, x1, y))


def test_merged_spine_splits_stacked_panels():
    """Two vertically stacked panels whose V spines have a gap are not merged.

    Models a figure where each panel has its own top/bottom H spine, the left
    and right V spines are drawn in two separate segments (one per panel), and
    there is a > _SPINE_GAP_MIN gutter between them.
    """
    from pdf_chart2table.plot_region import _merged_spine_frames

    # Page: 600 x 800.  Two stacked panels each ~130 pt tall with a 30 pt gutter.
    # Top panel: y=80-210.  Bottom panel: y=240-370.
    # V spines at x=100 and x=300, each as two separate segments.
    # H spines: y=80, y=210, y=240, y=370, each spanning x=[100, 300].
    paths = [
        # Left V, top segment
        _vseg(100, 80, 210),
        # Left V, bottom segment
        _vseg(100, 240, 370),
        # Right V, top segment
        _vseg(300, 80, 210),
        # Right V, bottom segment
        _vseg(300, 240, 370),
        # H spines
        _hseg(80, 100, 300),
        _hseg(210, 100, 300),
        _hseg(240, 100, 300),
        _hseg(370, 100, 300),
    ]
    frames = _merged_spine_frames(paths, width=600.0, height=800.0)
    # Both panels must be found as separate frames.
    assert len(frames) == 2
    ys = sorted(f[1] for f in frames)
    assert abs(ys[0] - 80) <= 5    # top panel y0
    assert abs(ys[1] - 240) <= 5   # bottom panel y0


def test_merged_spine_splits_sidebyside_panels_at_h_segment_boundary():
    """Two side-by-side panels sharing a single wide H spine are not merged.

    The wide H spine has two separate segments (one per panel); the frame
    builder must use each segment's right end rather than the global maximum,
    so each sub-panel forms its own narrow frame.
    """
    from pdf_chart2table.plot_region import _merged_spine_frames

    # Page: 600 x 800.  Left panel x=50-250; right panel x=300-500.
    # V spines at x=50, x=250, x=300, x=500 (all spanning y=100-300).
    # H spines: top y=100 and bottom y=300, each as two segments.
    paths = [
        # V spines
        _vseg(50, 100, 300),
        _vseg(250, 100, 300),
        _vseg(300, 100, 300),
        _vseg(500, 100, 300),
        # H spines: two segments per row (one per panel; 50 pt gap between them)
        _hseg(100, 50, 250),
        _hseg(100, 300, 500),
        _hseg(300, 50, 250),
        _hseg(300, 300, 500),
    ]
    frames = _merged_spine_frames(paths, width=600.0, height=800.0)
    assert len(frames) == 2
    xs = sorted(f[0] for f in frames)
    assert abs(xs[0] - 50) <= 5    # left panel x0
    assert abs(xs[1] - 300) <= 5   # right panel x0


def test_low_coverage_v_edge_falls_back_to_full_span():
    """A V spine drawn as sparse tick stubs (low coverage) still forms a frame.

    Models an axis whose vertical boundary is only marked by short tick stubs at
    the top and bottom rather than a continuous spine: coverage is far below
    _SPINE_COVERAGE_MIN, but the union span clears min_v_span.  The builder must
    fall back to one full-span edge so the frame is still detected (it can then
    be calibrated or skipped downstream) rather than dropping the whole region.
    """
    from pdf_chart2table.plot_region import _merged_spine_frames

    # Page 600 x 800 -> min_v_span = 0.06*800 = 48, min_h_span = 0.06*600 = 36.
    # Left V at x=100 as two short stubs (len 10 each) far apart: union span
    # 80..300 = 220 >= 48, coverage = 20/220 ~= 0.09 < _SPINE_COVERAGE_MIN (0.4).
    paths = [
        _vseg(100, 80, 90),    # top stub
        _vseg(100, 290, 300),  # bottom stub
        _vseg(300, 80, 300),   # full right V spine
        _hseg(80, 100, 300),   # top H spine
        _hseg(300, 100, 300),  # bottom H spine
    ]
    frames = _merged_spine_frames(paths, width=600.0, height=800.0)
    # Without the low-coverage fallback the sparse left V is dropped and no
    # frame forms; with it, exactly one frame spanning x=100..300 is found.
    assert len(frames) == 1
    f = frames[0]
    assert abs(f[0] - 100) <= 5    # x0 from the recovered left edge
    assert abs(f[1] - 80) <= 5     # y0


# --- nested-inset regression: a corner zoom panel must not pollute the main --

def _txt(s: str, cx: float, cy: float):
    """A numeric tick-label text span centered at (cx, cy)."""
    from pdf_chart2table.model import TextSpan
    return TextSpan(text=s, bbox=(cx - 3, cy - 3, cx + 3, cy + 3), size=6.0)


def _sat_line(x0: float, x1: float, y: float, color=(1.0, 0.0, 0.0)):
    """A saturated-colour stroked polyline (a data series line)."""
    from pdf_chart2table.model import Path as VPath
    pts = [(x0 + (x1 - x0) * i / 20.0, y) for i in range(21)]
    return VPath(points=pts, stroke=color, fill=None, width=1.0,
                 dashes=None, closed=False, bbox=(x0, y, x1, y))


def _axes_box(x0: float, y0: float, x1: float, y1: float, *, ticks: bool):
    """Build (paths, texts) for a rectangular axes frame with merged-spine edges.

    When ``ticks`` is True the box also gets short outward tick marks plus
    numeric tick labels along the bottom and left edges, so it calibrates on
    both axes (a genuine plot/inset axes box). When False it is a bare framed
    rectangle with non-numeric text inside (a legend / annotation box) and does
    NOT calibrate.
    """
    paths = [_hseg(y0, x0, x1), _hseg(y1, x0, x1),
             _vseg(x0, y0, y1), _vseg(x1, y0, y1)]
    texts = []
    if ticks:
        for frac, val in [(0.1, "0"), (0.5, "5"), (0.9, "10")]:
            tx = x0 + (x1 - x0) * frac
            paths.append(_vseg(tx, y1, y1 + 4))          # outward x tick
            texts.append(_txt(val, tx, y1 + 9))           # x tick label
        for frac, val in [(0.1, "0"), (0.5, "1"), (0.9, "2")]:
            ty = y1 - (y1 - y0) * frac
            paths.append(_hseg(ty, x0 - 4, x0))           # outward y tick
            texts.append(_txt(val, x0 - 9, ty))           # y tick label
    return paths, texts


def test_nested_inset_paths_excluded_from_main_region():
    """A calibratable inset axes box inside the main plot is trimmed out.

    Models a main plot (its own frame + ticks + data lines) with a smaller
    axes box drawn in a corner — its own frame, its own tick labels, a second
    coordinate system. The inset's interior paths (its frame, ticks, and data
    lines) must NOT be folded into the main region's path set.
    """
    main_p, main_t = _axes_box(100, 100, 400, 400, ticks=True)
    main_p += [_sat_line(110, 250, 150 + 20 * i) for i in range(4)]
    inset_p, inset_t = _axes_box(280, 280, 380, 380, ticks=True)
    inset_p += [_sat_line(290, 360, 300 + 15 * i, color=(0, 0, 1))
                for i in range(4)]

    paths = main_p + inset_p
    texts = main_t + inset_t
    regions = detect_regions(paths, texts, 500.0, 500.0)

    # One region (the main plot); the inset is not emitted as its own region.
    assert len(regions) == 1
    region = regions[0]
    # The main region must NOT contain any path whose centroid lies inside the
    # inset box (its 4 frame edges, 6 ticks, and 4 data lines are all excluded).
    inset_box = (280.0, 280.0, 380.0, 380.0)
    for i in region.path_indices:
        b = paths[i].bbox
        cx, cy = 0.5 * (b[0] + b[2]), 0.5 * (b[1] + b[3])
        in_inset = (inset_box[0] <= cx <= inset_box[2]
                    and inset_box[1] <= cy <= inset_box[3])
        assert not in_inset, f"inset path {i} leaked into main region"
    # The main plot's own data lines (outside the inset) are retained.
    assert len(region.path_indices) > 0


def test_legend_box_inside_plot_is_not_treated_as_inset():
    """GUARD: a framed legend/annotation box inside the plot is NOT an inset.

    A legend box is a rectangle with no tick marks and only non-numeric text,
    so it does not calibrate on either axis. The inset rule requires BOTH axes
    to calibrate, so the legend box must not be mistaken for a nested inset and
    its enclosed paths must remain part of the main region.
    """
    from pdf_chart2table.plot_region import _inset_boxes

    main_p, main_t = _axes_box(100, 100, 400, 400, ticks=True)
    main_p += [_sat_line(110, 250, 150 + 20 * i) for i in range(4)]
    # Legend box: a bare framed rectangle with non-numeric labels, no ticks.
    legend_p, _ = _axes_box(280, 280, 380, 380, ticks=False)
    legend_t = [_txt("SiN", 310, 300), _txt("SiP", 310, 330)]  # non-numeric

    paths = main_p + legend_p
    texts = main_t + legend_t

    # The legend box must not be detected as an inset of the main region.
    candidate_boxes = [(100.0, 100.0, 400.0, 400.0), (280.0, 280.0, 380.0, 380.0)]
    kept = [(100.0, 100.0, 400.0, 400.0)]
    assert _inset_boxes(candidate_boxes, kept, paths, texts) == []

    # And end-to-end: the legend box's frame paths stay in the main region.
    regions = detect_regions(paths, texts, 500.0, 500.0)
    assert len(regions) == 1
    region = regions[0]
    legend_box = (280.0, 280.0, 380.0, 380.0)
    n_legend_paths = sum(
        1 for i in region.path_indices
        if legend_box[0] <= 0.5 * (paths[i].bbox[0] + paths[i].bbox[2]) <= legend_box[2]
        and legend_box[1] <= 0.5 * (paths[i].bbox[1] + paths[i].bbox[3]) <= legend_box[3]
    )
    assert n_legend_paths > 0, "legend-box paths were wrongly stripped as an inset"
