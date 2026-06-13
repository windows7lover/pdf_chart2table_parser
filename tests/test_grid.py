"""Regression tests for background-grid detection (grid.detect_grid).

Reproduces the "missing grid" feedback (2108/2110): light-grey axis-aligned
interior lines must be recognised as a grid (and recorded as style), while data
curves / spines / a lone stray rule must not be.
"""
from __future__ import annotations

from pdf_chart2table.axes import axis_segments
from pdf_chart2table.grid import detect_grid
from pdf_chart2table.model import Path, Region


def _line(x0, y0, x1, y1, stroke=(0.8, 0.8, 0.8), dashes=None):
    return Path(points=[(x0, y0), (x1, y1)], stroke=stroke, fill=None, width=0.5,
                dashes=dashes, closed=False,
                bbox=(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))


def _region(paths):
    return Region(bbox=(100.0, 100.0, 300.0, 300.0),
                  path_indices=list(range(len(paths))), text_indices=[])


def test_horizontal_grey_gridlines_detected():
    paths = [_line(110, y, 290, y) for y in (140, 180, 220, 260)]  # span >60% width
    grid = detect_grid(_region(paths), paths)
    assert grid and grid.get("y") and not grid.get("x")
    assert grid["color"] == [0.8, 0.8, 0.8]


def test_vertical_grey_gridlines_detected():
    paths = [_line(x, 110, x, 290) for x in (140, 180, 220, 260)]
    grid = detect_grid(_region(paths), paths)
    assert grid and grid.get("x") and not grid.get("y")


def test_black_lines_are_not_grid():
    paths = [_line(110, y, 290, y, stroke=(0.0, 0.0, 0.0)) for y in (140, 180, 220)]
    assert detect_grid(_region(paths), paths) is None


def test_single_grey_line_is_not_grid():
    paths = [_line(110, 200, 290, 200)]  # only one -> not enough for a grid
    assert detect_grid(_region(paths), paths) is None


def test_light_grey_gridlines_detected():
    # very light grey grid (e.g. ~0.93) must still be caught (2110-style)
    paths = [_line(110, y, 290, y, stroke=(0.93, 0.93, 0.93))
             for y in (140, 180, 220, 260)]
    assert (detect_grid(_region(paths), paths) or {}).get("y")


def test_dark_dashed_grid_recovered_via_ticks():
    # 2508.02902-style: DARK + DASHED full-span rules. Colour-agnostic detection
    # at the tick coordinates recovers them (the old grey-only gate missed these).
    ys = (140, 180, 220, 260)
    paths = [_line(110, y, 290, y, stroke=(0.1, 0.1, 0.1), dashes="[ .57 1.72 ] 0")
             for y in ys]
    grid = detect_grid(_region(paths), paths, y_ticks=list(ys))
    assert grid and grid.get("y")
    assert grid["dashes"] == "[ .57 1.72 ] 0" and grid["color"] == [0.1, 0.1, 0.1]


def test_dark_lines_off_ticks_not_grid():
    # Same dark full-span lines but NOT on ticks -> ambiguous with data/reference
    # lines, so without tick confirmation they must NOT be called a grid.
    paths = [_line(110, y, 290, y, stroke=(0.1, 0.1, 0.1)) for y in (140, 180, 220)]
    assert detect_grid(_region(paths), paths, y_ticks=[133.0, 167.0, 201.0]) is None


def test_records_grid_line_positions():
    ys = (140, 180, 220, 260)
    paths = [_line(110, y, 290, y) for y in ys]
    grid = detect_grid(_region(paths), paths)
    assert grid["y_px"] == sorted(ys) and grid.get("x_px") == []


def test_single_tick_aligned_reference_line_captured():
    # A lone full-span line that sits ON a tick is a reference line (e.g. y=0
    # axhline) and IS recorded, even though it is not a >=2-line grid.
    paths = [_line(110, 200, 290, 200, stroke=(0.0, 0.0, 0.0))]
    grid = detect_grid(_region(paths), paths, y_ticks=[200.0])
    assert grid and grid["y_px"] == [200.0]
    assert not grid.get("y")  # a single reference line is NOT a background grid


def test_single_offtick_line_not_captured():
    # A lone full-span line NOT on a tick is dropped (could be a stray rule).
    paths = [_line(110, 205, 290, 205, stroke=(0.0, 0.0, 0.0))]
    assert detect_grid(_region(paths), paths, y_ticks=[200.0]) is None


def test_axis_segments_classifies_gridline_vs_tick():
    # A full-span interior horizontal line is a gridline; a short segment at the
    # left spine is a tick. The classifier is the shared primitive both consume.
    grid_line = _line(110, 200, 290, 200)            # full-span interior
    tick = _line(96, 150, 100, 150)                  # short, at left spine (x0=100)
    paths = [grid_line, tick]
    rows = axis_segments(paths, _region(paths))
    roles = {round(r["coord"]): r["role"] for r in rows}
    assert roles.get(200) == "gridline"
    assert roles.get(150) == "tick"


def test_fragmented_grid_unioned_at_tick():
    # A dashed grid line drawn as separate collinear fragments (multiple short
    # paths at one y) is unioned and recovered when it sits on a tick.
    def frags(y):
        return [_line(110 + 30 * j, y, 130 + 30 * j, y, stroke=(0.2, 0.2, 0.2))
                for j in range(6)]  # 6 fragments spanning ~110..290
    paths = frags(150) + frags(210)
    grid = detect_grid(_region(paths), paths, y_ticks=[150.0, 210.0])
    assert grid and grid.get("y")
