"""Regression: a label's leading character orphaned across the cy-bin boundary
must be re-absorbed as the row anchor.

Bug (2111.05667_p5, 2105.10437_p9): the legend anchor is the leftmost span in
its coarse cy-bin. A first character drawn a hair lower than the rest of its
label (e.g. a Greek 'κ' ~1.5pt below its '=') lands in a different cy-bin, so the
anchor becomes the '=' and rightward-only assembly drops the 'κ' -> "= 25 ns"
instead of "κ = 25 ns". _extend_anchor_left walks left over a touching,
vertically-overlapping unconsumed span to restore the true first character.
"""
from __future__ import annotations

from pdf_chart2table.model import Path, Region, TextSpan
from pdf_chart2table.labels import _detect_legend

REGION = Region(bbox=(100.0, 50.0, 280.0, 300.0))


def _line(x0, y, x1):
    return Path(points=[(x0, y), (x1, y)], stroke=(0.0, 0.0, 1.0), fill=None,
                width=0.5, dashes=None, closed=False, bbox=(x0, y, x1, y))


def _txt(s, x0, y0, x1, y1, size=7.5):
    return TextSpan(text=s, bbox=(x0, y0, x1, y1), size=size, dir=(1.0, 0.0),
                    color=None)


def test_left_orphan_greek_reabsorbed():
    # Two rows. Row A: 'κ' and '= 1' share a cy-bin (assembles fine). Row B: the
    # 'κ' sits ~1.5pt lower than '= 25' (straddling the 6pt bin boundary at y=231)
    # so it is orphaned; the fix must still yield 'κ = 25'.
    paths = [_line(120.0, 219.5, 137.0), _line(120.0, 230.5, 137.0)]
    texts = [
        # Row A (cy ~219-220, same bin)
        _txt("κ", 138.9, 216.8, 142.2, 224.3),
        _txt(" = 1", 142.2, 215.4, 158.8, 222.9),
        # Row B (κ cy~231.3 in a different bin than '= 25' cy~229.9)
        _txt("κ", 138.9, 227.5, 142.2, 235.0),
        _txt(" = 25", 142.2, 226.0, 158.8, 233.6),
    ]
    entries, _box = _detect_legend(REGION, paths, texts)
    labels = sorted(e[2].strip() for e in entries)
    assert labels == ["κ = 1", "κ = 25"], labels


def test_no_left_orphan_is_noop():
    # A normal legend whose anchors are already leftmost is unchanged.
    paths = [_line(120.0, 219.5, 137.0), _line(120.0, 240.5, 137.0)]
    texts = [
        _txt("Model A", 140.0, 216.0, 175.0, 223.0),
        _txt("Model B", 140.0, 237.0, 175.0, 244.0),
    ]
    entries, _box = _detect_legend(REGION, paths, texts)
    assert sorted(e[2] for e in entries) == ["Model A", "Model B"]
