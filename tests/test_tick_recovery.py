"""Regression tests for tick-direction recovery (axes._x/_y_tick_positions).

Reproduces the bugs fixed while iterating on the restyle prototype:
* inward-only ticks were missed (direction came back None / 'out') because only
  the bottom/left spine was scanned -- direction is now voted across BOTH spines.
"""
from __future__ import annotations

from pdf_chart2table.axes import _x_tick_positions, _y_tick_positions
from pdf_chart2table.model import Path, Region

REGION = Region(bbox=(100.0, 100.0, 300.0, 300.0), path_indices=[], text_indices=[])


def _seg(x0, y0, x1, y1):
    return Path(points=[(x0, y0), (x1, y1)], stroke=(0.0, 0.0, 0.0), fill=None,
                width=0.8, dashes=None, closed=False,
                bbox=(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))


def test_x_ticks_inward_on_top_spine_detected():
    # short vertical ticks on the TOP spine (y=100) extending DOWN into the plot.
    # Bottom-spine-only detection returned None here; both-spine voting -> 'in'.
    ticks = [_seg(cx, 100.0, cx, 105.0) for cx in (140, 180, 220, 260)]
    _, direction, _ = _x_tick_positions(ticks, REGION)
    assert direction == "in"


def test_x_ticks_outward_on_bottom_spine_detected():
    ticks = [_seg(cx, 300.0, cx, 305.0) for cx in (140, 180, 220, 260)]
    positions, direction, _ = _x_tick_positions(ticks, REGION)
    assert direction == "out"
    assert len(positions) == 4  # bottom ticks are also the calibration positions


def test_y_ticks_inward_on_right_spine_detected():
    # short horizontal ticks on the RIGHT spine (x=300) extending LEFT into plot.
    ticks = [_seg(300.0, cy, 295.0, cy) for cy in (140, 180, 220, 260)]
    _, direction, _ = _y_tick_positions(ticks, REGION)
    assert direction == "in"
