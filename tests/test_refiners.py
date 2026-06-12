"""Regression tests for refiners.drop_spurious_lines.

Reproduces the spurious-line bugs: a connector through scatter markers
(2410.00955) and a straight reference/fit line drawn through scatter data
(2510.04789 orange fit). Guards that a genuine dense line+marker DATA curve and
a pure line chart are NOT touched.
"""
from __future__ import annotations

from pdf_chart2table.model import Series
from pdf_chart2table.refiners import drop_spurious_lines


def _series(marker, pix):
    return Series(label=None, marker=marker, color=(0, 0, 0),
                  points=[{"x": x, "y": y, "x_px": x, "y_px": y} for x, y in pix])


def test_connector_through_scatter_dropped():
    # 4 scatter markers; a line whose vertices ARE the marker positions.
    marks = _series("o", [(10, 10), (20, 25), (30, 15), (40, 35)])
    connector = _series(None, [(10, 10), (20, 25), (30, 15), (40, 35)])
    kept, reasons = drop_spurious_lines([marks, connector])
    assert marks in kept and connector not in kept
    assert any("connector" in r for r in reasons)


def test_straight_fit_line_dropped():
    marks = _series("s", [(10, 12), (25, 40), (40, 8), (55, 33)])  # scatter
    fit = _series(None, [(5 + i, 5 + 2 * i) for i in range(60)])    # perfectly straight
    kept, reasons = drop_spurious_lines([marks, fit])
    assert marks in kept and fit not in kept
    assert any("straight" in r for r in reasons)


def test_genuine_line_plus_marker_data_kept():
    # A dense curve through markers: most line vertices fall BETWEEN markers, so
    # the connector fraction is low and the curve is not straight -> KEEP it.
    marks = _series("o", [(10, 10), (30, 30), (50, 12), (70, 40)])
    curve = _series(None, [(10 + i, 10 + 18 * (i / 60.0) ** 2 * (1 if i < 40 else -1))
                           for i in range(61)])
    kept, _ = drop_spurious_lines([marks, curve])
    assert curve in kept, "a dense data curve through markers must be kept"


def test_multitrack_connector_kept():
    # Two marker trajectories in one colour (markers ~= 2x the line's vertices):
    # the line is a DISTINCT series, not a 1:1 connector -> keep (multitrack guard).
    line = _series(None, [(10, 10), (20, 25), (30, 15), (40, 35)])  # 4 verts
    marks = _series("o", [(10, 10), (20, 25), (30, 15), (40, 35),     # on the line
                          (10, 60), (20, 62), (30, 58), (40, 64)])    # a 2nd track
    kept, _ = drop_spurious_lines([marks, line])
    assert line in kept, "multitrack line (markers ~2x vertices) must be kept"


def test_pure_line_chart_untouched():
    # No marker series at all -> never strip the line.
    line = _series(None, [(i, i) for i in range(50)])  # even a straight line
    kept, reasons = drop_spurious_lines([line])
    assert kept == [line] and reasons == []
