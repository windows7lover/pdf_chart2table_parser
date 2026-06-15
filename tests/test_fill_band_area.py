"""Regression: a thin filled CURVE must not be mistaken for a shaded band.

Bug (2505.16060_p10c2): a purple data curve carried a fill attribute whose
outline traced out-and-back, giving a wide bbox (136x9) but ~zero enclosed area.
_fill_band_colors flagged its colour as a "band colour" purely from the bbox, so
the real purple stroked curve of the same colour was dropped as a band outline.
A true band encloses a meaningful fraction of its bbox; a thin curve does not.
"""
from __future__ import annotations

from pdf_chart2table.model import Path, Region
from pdf_chart2table.lines import (_fill_band_colors, _enclosed_area_frac,
                                    _BAND_MIN_FILL_FRAC)


def test_threshold_keeps_thin_stacked_bands():
    # The bottom band of a multi-band/stacked chart can be a slim wiggly strip
    # that fills only ~0.12 of its (tall) bbox (2308.02111_p17c4). The guard must
    # stay below that, or the all-bands-are-data logic collapses the chart.
    assert _BAND_MIN_FILL_FRAC <= 0.1

REGION = Region(bbox=(0.0, 0.0, 200.0, 200.0))
PURPLE = (0.56, 0.4, 0.88)
GREEN = (0.2, 0.7, 0.3)


def _thin_fill_curve(color):
    # wide bbox, but the outline goes out along a line and back -> ~zero area
    fwd = [(x, 100.0 + 0.02 * x) for x in range(0, 181, 10)]
    pts = fwd + fwd[::-1]
    return Path(points=pts, stroke=None, fill=color, width=0.0, dashes=None,
                closed=True, bbox=(0.0, 100.0, 180.0, 103.6))


def _solid_band(color):
    # a real filled band: a tall+wide rectangle (fills its bbox)
    pts = [(10.0, 40.0), (190.0, 40.0), (190.0, 150.0), (10.0, 150.0)]
    return Path(points=pts, stroke=None, fill=color, width=0.0, dashes=None,
                closed=True, bbox=(10.0, 40.0, 190.0, 150.0))


def test_thin_fill_curve_not_a_band_color():
    p = _thin_fill_curve(PURPLE)
    assert _enclosed_area_frac(p) < 0.15
    region = Region(bbox=(0.0, 0.0, 200.0, 200.0), path_indices=[0])
    assert _fill_band_colors([p], region) == set()


def test_solid_band_still_detected():
    p = _solid_band(GREEN)
    assert _enclosed_area_frac(p) >= 0.15
    region = Region(bbox=(0.0, 0.0, 200.0, 200.0), path_indices=[0])
    assert _fill_band_colors([p], region) == {GREEN}
