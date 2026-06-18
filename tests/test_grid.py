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


def test_chromatic_gridlines_rejected():
    # 2507.19945 / 2001.01928-style: data-coloured (saturated magenta) full-span
    # strokes that fall on ticks must NOT be emitted as a grid -- a real grid is
    # near-neutral, so a chromatic colour is a data false positive.
    ys = (140, 180, 220, 260)
    paths = [_line(110, y, 290, y, stroke=(0.70, 0.0, 0.70)) for y in ys]
    assert detect_grid(_region(paths), paths, y_ticks=list(ys)) is None


def test_neutral_grey_grid_still_kept_with_ticks():
    # No-regression companion to the chromatic-rejection test: a genuine grey grid
    # on the same tick positions is still recovered.
    ys = (140, 180, 220, 260)
    paths = [_line(110, y, 290, y, stroke=(0.8, 0.8, 0.8)) for y in ys]
    grid = detect_grid(_region(paths), paths, y_ticks=list(ys))
    assert grid and grid.get("y") and grid["color"] == [0.8, 0.8, 0.8]


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


# --------------------------------------------------------------------------
# Reference / guide lines (grid.detect_reference_lines)
# --------------------------------------------------------------------------
# A plot-spanning DASHED line that is chromatic OR not on a labelled tick is a
# reference / guide annotation (a zero rule, a y=+-c marker) that detect_grid
# drops -- recover it separately. A regular grid must NOT become reference lines.

from pdf_chart2table.grid import detect_reference_lines


def test_chromatic_dashed_line_at_tick_is_reference_line():
    # 2210.11827-style: a GREEN dashed zero-rule sits ON the y=0 tick, so
    # detect_grid drops it (chromatic), but it is a reference line.
    paths = [_line(110, 200, 290, 200, stroke=(0.0, 0.5, 0.0),
                   dashes="[ 4.94 2.14 ] 0")]
    assert detect_grid(_region(paths), paths, y_ticks=[200.0]) is None
    refs = detect_reference_lines(_region(paths), paths, y_ticks=[200.0])
    assert refs and len(refs) == 1
    assert refs[0]["orient"] == "h" and refs[0]["color"] == [0.0, 0.5, 0.0]
    assert refs[0]["dashes"] == "[ 4.94 2.14 ] 0"


def test_grey_dashed_lines_off_ticks_are_reference_lines():
    # 2104.00653-style: a symmetric pair of grey dashed +-c rules at NON-tick
    # positions (ticks at 150/200/250) must be recovered as reference lines.
    paths = [_line(110, y, 290, y, stroke=(0.15, 0.15, 0.15), dashes="[ 1.98 1.98 ] 0")
             for y in (170, 230)]
    refs = detect_reference_lines(_region(paths), paths,
                                  y_ticks=[150.0, 200.0, 250.0])
    assert refs and len(refs) == 2
    assert all(r["orient"] == "h" for r in refs)


def test_solid_grid_at_ticks_not_reference_lines():
    # Precision guard: a regular SOLID grey grid sitting on the ticks stays a grid
    # (no dashes -> not a reference line at all).
    ys = (140, 180, 220, 260)
    paths = [_line(110, y, 290, y, stroke=(0.8, 0.8, 0.8)) for y in ys]
    assert detect_reference_lines(_region(paths), paths, y_ticks=list(ys)) is None


def test_grey_dashed_grid_on_ticks_not_reference_lines():
    # Precision guard: grey DASHED lines that all sit on ticks are a (dashed) grid,
    # not isolated reference rules -> not recovered as reference lines.
    ys = (140, 180, 220, 260)
    paths = [_line(110, y, 290, y, stroke=(0.6, 0.6, 0.6), dashes="[ 1.0 1.0 ] 0")
             for y in ys]
    assert detect_reference_lines(_region(paths), paths, y_ticks=list(ys)) is None


def test_many_grey_dashed_offtick_lines_are_a_grid_not_references():
    # Precision guard: a regular repeating set (a minor grid) of grey dashed lines
    # off the labelled ticks is a grid, not reference rules -> rejected by count.
    ys = (130, 150, 170, 190, 210, 230)  # 6 lines, none on labelled ticks
    paths = [_line(110, y, 290, y, stroke=(0.7, 0.7, 0.7), dashes="[ 1.0 1.0 ] 0")
             for y in ys]
    assert detect_reference_lines(_region(paths), paths,
                                  y_ticks=[140.0, 220.0]) is None


def test_partial_span_dashed_segment_not_reference_line():
    # A PARTIAL-span dashed segment (a hysteresis-jump arrow / data fragment,
    # 2005.03896-style) covers < 80% of the span -> not a reference line.
    paths = [_line(200, 150, 200, 230, stroke=(0.0, 0.0, 1.0),
                   dashes="[ 1.8 1.8 ] 0")]  # vertical, ~40% of 200 height
    assert detect_reference_lines(_region(paths), paths, x_ticks=[160.0, 240.0]) is None
