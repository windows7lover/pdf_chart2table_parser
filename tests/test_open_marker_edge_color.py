"""Regression: an OPEN marker (white fill + coloured edge) keeps its EDGE colour.

Bug (2505.19730_p6c2): blue open-circle markers are drawn as two coincident paths
-- a white-fill blob (fill=white, stroke=None) and a coloured-edge outline
(fill=None, stroke=blue). The exact-colour grouping splits them; when the white-fill
group is kept first, ``_coalesce_duplicate`` left the series colour white, so the
series rendered invisible. The kept (white/open) group now adopts the duplicate's
visible edge stroke so the series takes the real (edge) colour.
"""
from __future__ import annotations

from pdf_chart2table.marks import Mark, SeriesMarks, _coalesce_duplicate

BLUE = (0.0, 0.0, 1.0)


def _grp(shape, fill, stroke):
    marks = [Mark(cx=float(i), cy=float(i), shape=shape, fill=fill, stroke=stroke,
                  size=4.0) for i in range(5)]
    return SeriesMarks(shape=shape, fill=fill, stroke=stroke, marks=marks)


def test_open_marker_adopts_edge_stroke():
    kept = _grp("diamond", (1.0, 1.0, 1.0), None)   # white-fill blob, no edge
    dup = _grp("square", None, BLUE)                # coincident blue edge outline
    _coalesce_duplicate(kept, dup)
    assert kept.stroke == BLUE                       # series colour -> blue edge


def test_coloured_kept_not_overwritten():
    kept = _grp("circle", (1.0, 0.0, 0.0), None)    # red FILL (visible) -> keep red
    dup = _grp("square", None, BLUE)
    _coalesce_duplicate(kept, dup)
    assert kept.stroke is None                       # red fill stays the identity
