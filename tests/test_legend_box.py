"""Regression test for legend-box recovery with mangled-label entries.

Reproduces the 2001.00255 bug: a legend whose entries have LaTeX/math labels
(rendered as glyph paths, so not recognised as text) emitted only the one
plain-text entry, undersizing legend_bbox -> the other entries' swatches leaked
into the data as fake marker series. The box must extend down the swatch column
to cover ALL entries.
"""
from __future__ import annotations

from pdf_chart2table.labels import _detect_legend
from pdf_chart2table.model import Path, Region, TextSpan

REGION = Region(bbox=(389.0, 61.9, 511.4, 130.0),
                path_indices=[], text_indices=[])


def _swatch(cx, cy):  # small filled marker glyph (a legend swatch)
    return Path(points=[(cx - 1.5, cy), (cx, cy - 1.5), (cx + 1.5, cy),
                        (cx, cy + 1.5), (cx - 1.5, cy)],
                stroke=(0, 0, 0), fill=(0, 0, 0), width=0.5, dashes=None,
                closed=True, bbox=(cx - 1.5, cy - 1.5, cx + 1.5, cy + 1.5))


def _text(s, x0, cy):
    return TextSpan(text=s, bbox=(x0, cy - 3, x0 + 30, cy + 3), size=6.0,
                    dir=(1.0, 0.0))


def test_legend_box_extends_over_mangled_entries():
    # row 1 has a readable label ("numerical"); rows 2-4 are mangled (no text),
    # only their swatches exist, stacked in the same x-column below.
    paths = [_swatch(462, 70), _swatch(462, 82), _swatch(462, 94), _swatch(462, 106)]
    texts = [_text("numerical", 470, 70)]
    entries, bbox = _detect_legend(REGION, paths, texts)
    assert bbox is not None
    # the box must reach down past the labelled row to cover the lower swatches
    assert bbox[3] >= 104, f"legend box too short: {bbox}"
    assert bbox[1] <= 72
