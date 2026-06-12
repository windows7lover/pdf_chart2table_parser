"""Regression tests for background-grid detection (grid.detect_grid).

Reproduces the "missing grid" feedback (2108/2110): light-grey axis-aligned
interior lines must be recognised as a grid (and recorded as style), while data
curves / spines / a lone stray rule must not be.
"""
from __future__ import annotations

from pdf_chart2table.grid import detect_grid
from pdf_chart2table.model import Path, Region


def _line(x0, y0, x1, y1, stroke=(0.8, 0.8, 0.8)):
    return Path(points=[(x0, y0), (x1, y1)], stroke=stroke, fill=None, width=0.5,
                dashes=None, closed=False,
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
