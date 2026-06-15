"""Regression test for a dense curve segment passing near the legend.

Bug (2004.08077_p7c2): a 3-series error-bar plot lost its top (14.4 µW/cm²)
series entirely. Its curve is drawn as dense ~700-vertex sub-strokes tiling narrow
x-windows; the topmost windows pass UNDER the top-right legend. ``_off_chart``
excluded any NARROW path near the legend as a swatch, so those dense segments were
dropped, leaving the colour with too few segments to assemble (< _MIN_SEGMENT_COUNT)
and the whole series vanished. A legend swatch is SPARSE (a short line or a marker
glyph); a many-vertex path near the legend is a curve segment, not a swatch.
"""
from __future__ import annotations

from pdf_chart2table.lines import _MIN_SEGMENT_VERTS, _off_chart
from pdf_chart2table.model import Path, Region, TextSpan

REGION = Region(bbox=(0.0, 0.0, 200.0, 100.0), path_indices=[], text_indices=[])
# A legend label near the top-right, overlapping the path's row.
LEGEND_TEXT = [TextSpan(text="14.4", bbox=(150.0, 45.0, 180.0, 53.0), size=8.0,
                        dir=(1.0, 0.0))]
PBOX = (150.0, 40.0, 170.0, 60.0)  # narrow (bw=20 < 0.25*200) interior path near it


def _path(npts):
    pts = [(150.0 + 20.0 * i / (npts - 1), 40.0 + 20.0 * i / (npts - 1))
           for i in range(npts)]
    return Path(points=pts, stroke=(0.0, 0.0, 1.0), fill=None, width=0.3,
                dashes=None, closed=False, bbox=PBOX)


def test_dense_segment_near_legend_is_not_a_swatch():
    # a dense (>= segment threshold) narrow path near the legend is curve detail
    dense = _path(_MIN_SEGMENT_VERTS + 10)
    assert _off_chart(dense, REGION, LEGEND_TEXT) is False


def test_sparse_glyph_near_legend_still_excluded():
    # a sparse narrow path near the legend is a genuine swatch -> still excluded
    sparse = _path(5)
    assert _off_chart(sparse, REGION, LEGEND_TEXT) is True
