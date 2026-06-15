"""Regression: when no compact legend BOX forms, scattered numeric "entries" are
axis/colorbar tick labels with an incidental swatch — not legend rows — and must
be dropped, while genuine (non-numeric) labels are kept.

Bugs:
  - 2107.04956_p3c1 leaked '3' and '2 0.6' alongside real labels (Gap, Model 2…).
  - 2301.02282_p12c11 leaked a whole '0.00'…'0.2' colorbar ladder.

A real numeric legend (years/temps) STILL clusters into a box, so box is not
None there and its numeric entries are preserved (covered separately).
"""
from __future__ import annotations

from pdf_chart2table.model import Path, Region, TextSpan
from pdf_chart2table.labels import _detect_legend, _all_numeric_tokens

REGION = Region(bbox=(0.0, 0.0, 100.0, 90.0))


def _marker(cx, cy, color=(0.0, 0.0, 0.0), r=1.6):
    pts = [(cx - r, cy - r), (cx + r, cy - r), (cx + r, cy + r), (cx - r, cy + r)]
    return Path(points=pts, stroke=color, fill=color, width=0.4, dashes=None,
                closed=True, bbox=(cx - r, cy - r, cx + r, cy + r))


def _txt(s, x0, y0, x1, y1):
    return TextSpan(text=s, bbox=(x0, y0, x1, y1), size=6.0, dir=(1.0, 0.0),
                    color=None)


def test_all_numeric_tokens_predicate():
    assert _all_numeric_tokens("2 0.6")
    assert _all_numeric_tokens("-2.6 -4.2")
    assert _all_numeric_tokens("2016")
    assert not _all_numeric_tokens("300 K")
    assert not _all_numeric_tokens("Model 2")
    assert not _all_numeric_tokens("10 nm")


def test_all_numeric_entries_dropped_when_no_box():
    # Six "entries" stacked tightly enough to chain into ONE cluster that spans
    # most of the plot -> _legend_box rejects it (> plot-fraction cap) -> box is
    # None. Three are real labels; three are numeric-token strays (a tick number,
    # a two-number pair, a negative two-number pair). The strays must be dropped.
    rows = [
        (8.0, "Gap"), (22.0, "Model 2"), (36.0, "3"),
        (50.0, "Model 3"), (64.0, "2 0.6"), (78.0, "-2.6 -4.2"),
    ]
    paths = [_marker(10.0, cy) for cy, _ in rows]
    texts = [_txt(s, 16.0, cy - 3.0, 70.0, cy + 3.0) for cy, s in rows]
    entries, box = _detect_legend(REGION, paths, texts)
    labels = sorted(e[2] for e in entries)
    assert box is None  # oversized cluster -> rejected
    assert labels == ["Gap", "Model 2", "Model 3"], labels
