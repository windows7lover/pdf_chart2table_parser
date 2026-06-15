"""Regression: discrete multi-colour series must NOT be merged as a colormap.

Bug (2001.01769_p17c3): five power-law series, each its own SOLID colour with ~16
circle markers, were merged into ONE teal series by ``_merge_colormap_scatter``
(which fired on "≥4 same-shape colour groups spanning a wide hue range"). A real
colormap scatter is point-PER-colour (each group ~1 mark); discrete series have
MANY marks per colour. The merge now requires the cluster's groups to be sparse
(median marks-per-group ≤ _CMAP_MAX_MEDIAN_MARKS).
"""
from __future__ import annotations

from pdf_chart2table.marks import Mark, SeriesMarks, _merge_colormap_scatter

# five distinct solid hues, same shape/size
HUES = [(1.0, 0.0, 0.5), (0.0, 0.5, 1.0), (1.0, 0.5, 0.0),
        (0.0, 0.8, 0.5), (0.6, 0.0, 0.8)]


def _group(color, n):
    marks = [Mark(cx=10.0 + i, cy=20.0 + i, shape="circle", fill=color,
                  stroke=color, size=4.0) for i in range(n)]
    return SeriesMarks(shape="circle", fill=color, stroke=color, marks=marks)


def test_discrete_dense_colour_series_not_merged():
    groups = [_group(c, 16) for c in HUES]          # 16 marks per colour = discrete
    out = _merge_colormap_scatter(groups)
    assert len(out) == 5, f"discrete series merged: {len(out)}"


def test_sparse_colormap_still_merges():
    groups = [_group(c, 1) for c in HUES]           # 1 mark per colour = colormap
    out = _merge_colormap_scatter(groups)
    assert len(out) == 1, f"colormap not merged: {len(out)}"
