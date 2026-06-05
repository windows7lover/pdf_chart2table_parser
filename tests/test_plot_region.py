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
    assert not _covered_by_image(box, None)
