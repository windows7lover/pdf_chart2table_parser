"""Regression: a subplot PANEL tag ("(a)", "(b)", "b)") must not be emitted as a
legend entry.

Bug (2001.00255_p20c1, single-panel): the panel tag '(b)' sits in the top-right
corner, picked up a nearby marker swatch, and became the chart's only "legend
entry" — a bogus 1-entry legend. A paren-wrapped single letter is a figure
identifier, never a series label. The required trailing ")" distinguishes it
from genuine single-letter labels (W, T, L, H).
"""
from __future__ import annotations

from pdf_chart2table.model import Path, Region, TextSpan
from pdf_chart2table.labels import _detect_legend, _is_proxy_label

REGION = Region(bbox=(20.0, 20.0, 200.0, 160.0))


def test_panel_tag_is_proxy_real_letters_are_not():
    for tag in ("(a)", "(b)", "b)", "a)", "(c)"):
        assert _is_proxy_label(tag), tag
    for real in ("W", "T", "L", "H", "f", "Model 2", "(ab)"):
        assert not _is_proxy_label(real), real


def test_lone_panel_tag_not_emitted_as_legend():
    paths = [Path(points=[(40.0, 30.0), (44.0, 34.0)], stroke=(0.0, 0.0, 0.0),
                  fill=(0.0, 0.0, 0.0), width=0.4, dashes=None, closed=True,
                  bbox=(40.0, 30.0, 44.0, 34.0))]
    texts = [TextSpan(text="(b)", bbox=(48.0, 29.0, 60.0, 35.0), size=6.0,
                      dir=(1.0, 0.0), color=None)]
    entries, _box = _detect_legend(REGION, paths, texts)
    assert entries == [], entries
