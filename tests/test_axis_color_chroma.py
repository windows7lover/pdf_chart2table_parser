"""Regression test for the axis-colour chroma guard (style.match_series_styles).

Reproduces 2207.07378_p3c1: a saturated GREEN ([0.0, 0.48, 0.0]) frame/spine
stroke -- actually a green 'planar orbits' data series running along the frame --
was recovered as the axis colour, painting the recon plot frame green. Real chart
axes are NEUTRAL (black/grey); a chromatic axis colour is a data false positive.
Mirrors grid.py's chromatic-gridline rejection.
"""
from __future__ import annotations

from pdf_chart2table.model import Path
from pdf_chart2table.style import match_series_styles


def _frame(stroke):
    # A full plot-frame rectangle (bbox spans the region), the primitive the
    # spine-colour recovery keys on.
    bbox = (100.0, 100.0, 300.0, 300.0)
    return Path(points=[(100, 100), (300, 100), (300, 300), (100, 300), (100, 100)],
                stroke=stroke, fill=None, width=1.0, dashes=None, closed=True,
                bbox=bbox)


_REGION = (100.0, 100.0, 300.0, 300.0)


def test_chromatic_axis_color_rejected():
    # Saturated green frame stroke -> rejected, axis stays neutral default (None).
    _, meta = match_series_styles([_frame((0.0, 0.48, 0.0))], _REGION, [])
    assert meta["axis_color"] is None


def test_neutral_grey_axis_color_kept_neutral():
    # No-regression companion: a grey/black spine also yields the neutral default
    # (None -> matplotlib black), i.e. ordinary dark axes are unchanged.
    _, meta = match_series_styles([_frame((0.2, 0.2, 0.2))], _REGION, [])
    assert meta["axis_color"] is None
