"""Regression tests for ``cli._attach_error_bars`` (which series an error bar
attaches to).

Reproduces the 2005.03896_p4c1 failure: a smooth resonance curve was drawn as a
marker-less dense polyline, and a set of DASHED annotation arrows whose x fell
on the curve's vertices were recovered as "error-bar whiskers". The whisker
geometry was then attached as per-point ``y_err`` to the marker-LESS curve,
synthesising spurious vertical error bars (and, with the old renderer, a cap on
every curve vertex).

An error bar is the uncertainty on a *plotted datum* (a marker); it belongs to a
marker/scatter series, never to a marker-less continuous curve. ``_attach_error_bars``
must only attach ``y_err`` to marker-bearing series.
"""

from __future__ import annotations

from pdf_chart2table.cli import _attach_error_bars
from pdf_chart2table.model import Axis, Series


def _yaxis():
    # y_px in [100, 300] maps linearly to data; a 1px whisker is a small y_err.
    return Axis(scale="linear", pixel_range=(100.0, 300.0),
                calibration={"scale": "linear", "a": -0.05, "b": 15.0, "r2": 1.0})


def _pt(x, y, x_px, y_px):
    return {"x": x, "y": y, "x_px": x_px, "y_px": y_px}


def test_yerr_not_attached_to_markerless_curve():
    """A whisker over a marker-LESS curve's vertices must NOT become its y_err."""
    curve = Series(label=None, marker=None, points=[
        _pt(1.0, 5.0, 200.0, 200.0),   # x_px==whisker cx, y_px inside whisker span
        _pt(1.1, 6.0, 210.0, 195.0),
    ])
    whiskers = [(200.0, 190.0, 210.0)]  # (cx, top_px, bottom_px) spanning y_px=200
    _attach_error_bars([curve], whiskers, _yaxis())
    assert all("y_err" not in p for p in curve.points), \
        "error bar must not attach to a marker-less curve"


def test_yerr_attaches_to_marker_series():
    """A genuine whisker over a MARKER datum still attaches (no regression)."""
    scatter = Series(label="A", marker="o", points=[
        _pt(1.0, 5.0, 200.0, 200.0),
    ])
    whiskers = [(200.0, 190.0, 210.0)]
    _attach_error_bars([scatter], whiskers, _yaxis())
    assert "y_err" in scatter.points[0], "error bar must attach to a marker datum"
    assert scatter.points[0]["y_err"] > 0


def test_whisker_prefers_marker_over_coincident_curve():
    """With both a marker series and a marker-less curve at the same x, the
    whisker attaches to the marker datum, never the curve."""
    curve = Series(label=None, marker=None, points=[
        _pt(1.0, 5.0, 200.0, 200.0),
    ])
    scatter = Series(label="A", marker="o", points=[
        _pt(1.0, 5.0, 200.0, 201.0),
    ])
    whiskers = [(200.0, 190.0, 210.0)]
    _attach_error_bars([curve, scatter], whiskers, _yaxis())
    assert all("y_err" not in p for p in curve.points)
    assert "y_err" in scatter.points[0]
