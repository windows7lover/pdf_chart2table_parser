"""Regression: recover a leading math-symbol GLYPH-PATH on a legend label.

Some legend labels begin with a math symbol (μ, λ, ℓ …) that the PDF renders as
a font-less filled glyph outline, not selectable text, so extraction yields a
truncated label like "=0.83". When a recognizer callback is supplied (wired in
cli.py via glyph_match), _detect_legend detects the glyph-path abutting the
label's first span and prepends the recovered unicode -> "μ=0.83".

Verified end-to-end on 2506.23737_p14c1 ('=0'/'=0.83' -> 'μ=0'/'μ=0.83').
"""
from __future__ import annotations

import math

from pdf_chart2table.model import Path, Region, TextSpan
from pdf_chart2table.labels import _detect_legend

REGION = Region(bbox=(20.0, 20.0, 200.0, 160.0))


def _line(x0, y, x1, color):
    return Path(points=[(x0, y), (x1, y)], stroke=color, fill=None, width=0.5,
                dashes=None, closed=False, bbox=(x0, y, x1, y))


def _glyph(cx, cy, w=4.0, h=4.6, color=(0.0, 0.0, 0.0)):
    # A complex filled outline (>= 20 pts) standing in for a CM math glyph.
    pts = [(cx + 0.5 * w * math.cos(2 * math.pi * i / 24),
            cy + 0.5 * h * math.sin(2 * math.pi * i / 24)) for i in range(24)]
    return Path(points=pts, stroke=None, fill=color, width=0.0, dashes=None,
                closed=True, bbox=(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2))


def _txt(s, x0, y0, x1, y1):
    return TextSpan(text=s, bbox=(x0, y0, x1, y1), size=6.0, dir=(1.0, 0.0),
                    color=None)


def _legend_inputs():
    # Mirror 2506.23737 geometry: a colored line swatch (closest in cy to the
    # label, so it wins `pick`), then the leading "μ" glyph-path (slightly
    # cy-offset, still overlapping the label row), then the truncated text.
    blue = (0.0, 0.0, 1.0)
    paths = [
        _line(40.0, 84.8, 56.0, blue),           # row-1 swatch (closest cy)
        _glyph(60.0, 86.8),                       # row-1 leading "μ" glyph-path
        _line(40.0, 99.8, 56.0, blue),            # row-2 swatch
        _glyph(60.0, 101.8),                      # row-2 leading "μ" glyph-path
    ]
    texts = [
        _txt("=0.83", 63.0, 82.0, 80.0, 88.0),    # cy 85
        _txt("=0", 63.0, 97.0, 71.0, 103.0),      # cy 100
    ]
    return paths, texts


def test_leading_glyph_prepended_with_recognizer():
    paths, texts = _legend_inputs()

    def stub(bbox):  # recognize any char-sized glyph region as mu
        return (r"\mu", "μ", 0.8)

    entries, _box = _detect_legend(REGION, paths, texts, glyph_recognize=stub)
    labels = sorted(e[2] for e in entries)
    assert labels == ["μ=0", "μ=0.83"], labels


def test_no_recognizer_is_noop():
    paths, texts = _legend_inputs()
    entries, _box = _detect_legend(REGION, paths, texts)  # glyph_recognize=None
    labels = sorted(e[2] for e in entries)
    assert labels == ["=0", "=0.83"], labels


def test_recognizer_returning_none_is_noop():
    paths, texts = _legend_inputs()
    entries, _box = _detect_legend(REGION, paths, texts,
                                   glyph_recognize=lambda b: None)
    labels = sorted(e[2] for e in entries)
    assert labels == ["=0", "=0.83"], labels
