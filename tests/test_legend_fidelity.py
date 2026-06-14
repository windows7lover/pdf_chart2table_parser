"""Regression tests for legend render/label fidelity (2009.07658_p32c1):

#7 a Greek glyph next to its value ('θ' + '=3°') sits on the SAME baseline but
   with a different glyph height, so a centre-y row test drops 'θ' -> the label
   becomes '=3°'. The vertical-OVERLAP row test keeps the whole 'θ =3°'.
#6 the legend box is drawn as a white FILL rect plus a SEPARATE dark STROKE rect
   (so neither single path has both) -> detection must accept a coincident
   white-background sibling.
"""

from __future__ import annotations

from pdf_chart2table.labels import _assemble_label
from pdf_chart2table.model import Path, Region, TextSpan
from pdf_chart2table.style import match_series_styles


# --- #7: same-baseline multi-glyph label assembly -------------------------
def test_assemble_keeps_greek_prefix():
    # 'θ'(124-136)  '=3'(127-139)  '◦'(124-132): same baseline, different heights.
    texts = [
        TextSpan(text="θ", bbox=(414, 124, 420, 136), size=12.0),
        TextSpan(text="=3", bbox=(423, 127, 438, 139), size=12.0),
        TextSpan(text="◦", bbox=(438, 124, 442, 132), size=8.0),
    ]
    label, consumed = _assemble_label(0, (124 + 136) / 2, texts, set())
    assert label.startswith("θ")          # the Greek prefix is NOT dropped
    assert "=3" in label and "◦" in label
    assert consumed == {0, 1, 2}


# --- #6: legend box drawn as separate fill + stroke rects -----------------
RB = (100.0, 100.0, 400.0, 300.0)   # region: 300 x 200


def _rect(b, stroke=None, fill=None):
    x0, y0, x1, y1 = b
    return Path(points=[(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)],
                stroke=stroke, fill=fill, width=0.5, dashes=None, closed=True,
                bbox=b)


def test_legend_box_from_separate_fill_and_stroke():
    box = (350.0, 110.0, 395.0, 180.0)            # 45 x 70: narrow, in-region
    paths = [_rect(box, fill=(1.0, 1.0, 1.0)),     # white background rect
             _rect(box, stroke=(0.15, 0.15, 0.15))]  # dark border rect
    _, meta = match_series_styles(paths, RB, [])
    assert meta["legend_box"] is True


def test_full_frame_not_legend_box():
    # The plot frame spans ~full width -> must NOT be taken for a legend box.
    frame = (101.0, 101.0, 399.0, 299.0)
    paths = [_rect(frame, fill=(1.0, 1.0, 1.0)), _rect(frame, stroke=(0.1, 0.1, 0.1))]
    _, meta = match_series_styles(paths, RB, [])
    assert meta["legend_box"] is False
