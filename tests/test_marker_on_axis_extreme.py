"""Regression test: a data marker at the axis EXTREME (on the spine) is kept.

Bug (2005.11717_p17c2): the first/last data points sit at x = xmin / xmax, i.e.
exactly on the plot-box spine. ``_is_data_mark`` dropped any mark whose centroid is
on the frame edge (where tick marks live), so both endpoint markers were lost (the
series spanned only the interior points). A recognised 2-D marker glyph on the
spine is genuine edge data; ticks are 1-D and already rejected by the min-side
check, so recognised closed glyphs are now exempt from the on-border drop.
"""
from __future__ import annotations

import math

from pdf_chart2table.marks import _is_data_mark
from pdf_chart2table.model import Path, Region

REGION = Region(bbox=(100.0, 100.0, 300.0, 300.0), path_indices=[], text_indices=[])


def _circle(cx, cy, r=2.0, n=24):
    pts = [(cx + r * math.cos(2 * math.pi * i / n),
            cy + r * math.sin(2 * math.pi * i / n)) for i in range(n + 1)]
    return Path(points=pts, stroke=(1.0, 0.0, 0.0), fill=(1.0, 0.0, 0.0),
                width=0.5, dashes=None, closed=True,
                bbox=(cx - r, cy - r, cx + r, cy + r))


def test_marker_on_left_spine_is_kept():
    # centroid at x == region left edge (the x=xmin endpoint marker)
    assert _is_data_mark(_circle(100.0, 200.0), REGION)


def test_marker_on_right_spine_is_kept():
    assert _is_data_mark(_circle(300.0, 220.0), REGION)


def test_interior_marker_still_kept():
    assert _is_data_mark(_circle(200.0, 200.0), REGION)


def test_vertical_tick_on_spine_still_rejected():
    # a 1-D vertical segment on the left spine is a tick, not a marker -> rejected
    tick = Path(points=[(100.0, 198.0), (100.0, 202.0)], stroke=(0.0, 0.0, 0.0),
                fill=None, width=0.5, dashes=None, closed=False,
                bbox=(100.0, 198.0, 100.0, 202.0))
    assert not _is_data_mark(tick, REGION)
