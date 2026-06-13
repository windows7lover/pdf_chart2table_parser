"""Regression test: an OPEN marker (white fill + coloured edge, e.g. an open
circle) must take the EDGE colour as the series colour, not the white fill --
otherwise the renderer drops the series as 'background' and the markers vanish
(the 2008.09734_p19c2 bug)."""

from __future__ import annotations

from pdf_chart2table.extract import _build_series
from pdf_chart2table.marks import Mark, SeriesMarks
from pdf_chart2table.model import Axis

_CAL = {"a": 1.0, "b": 0.0, "scale": "linear"}
_AX = Axis(scale="linear", pixel_range=(0.0, 100.0), calibration=_CAL)


def _sm(fill, stroke):
    marks = [Mark(cx, cx, "circle", fill, stroke, 4.0) for cx in (10.0, 20.0, 30.0)]
    return SeriesMarks(shape="circle", fill=fill, stroke=stroke, marks=marks)


def test_open_marker_uses_edge_colour():
    s = _build_series(_sm(fill=(1.0, 1.0, 1.0), stroke=(0.83, 0.27, 0.49)), _AX, _AX)
    assert s.color == (0.83, 0.27, 0.49)   # edge, NOT the white fill


def test_filled_marker_keeps_fill_colour():
    s = _build_series(_sm(fill=(0.2, 0.4, 0.8), stroke=(0.0, 0.0, 0.0)), _AX, _AX)
    assert s.color == (0.2, 0.4, 0.8)      # solid fill wins


def test_no_fill_uses_edge():
    s = _build_series(_sm(fill=None, stroke=(0.1, 0.6, 0.6)), _AX, _AX)
    assert s.color == (0.1, 0.6, 0.6)
