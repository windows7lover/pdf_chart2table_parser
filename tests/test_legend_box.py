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


def test_data_markers_in_label_column_not_swept_into_legend():
    """Regression (2302.01559_p38c3): a single legend/annotation label near the
    top of the plot, whose x-column happens to overlap a data marker series, must
    NOT have the scattered data markers swept into the legend box. Those markers
    sit a full data-gap below the label row (not a tight legend stack), so the
    column-recovery must reject them -- otherwise the legend box snaps onto a row
    of real data and drops those points from extraction.
    """
    # One labelled entry at the top (row y~70) with its own swatch.
    label_y = 70
    swatch_at_label = _swatch(462, label_y)
    texts = [_text("zhat", 470, label_y)]
    # Data markers sharing the label's x-column but scattered DOWN the plot,
    # spaced far below the label row (a real data series, not a legend stack).
    data = [_swatch(462, 110), _swatch(462, 150),
            _swatch(462, 190), _swatch(462, 110 + 35)]
    entries, bbox = _detect_legend(REGION, [swatch_at_label] + data, texts)
    assert bbox is not None
    # The box must stay at the label row, NOT extend down over the data markers.
    assert bbox[3] <= label_y + 12, f"legend box swept over data: {bbox}"


def _cswatch(cx, cy, color):  # small filled coloured marker (a real data marker)
    return Path(points=[(cx - 1.5, cy), (cx, cy - 1.5), (cx + 1.5, cy),
                        (cx, cy + 1.5), (cx - 1.5, cy)],
                stroke=None, fill=color, width=None, dashes=None,
                closed=False, bbox=(cx - 1.5, cy - 1.5, cx + 1.5, cy + 1.5))


def test_isolated_annotation_entry_dropped_from_legend():
    """Regression (2503.09016_p4c1): a peak ANNOTATION label in the plot interior
    ("EH3") sits beside a real DATA marker that is read as a swatch, emitting a
    spurious legend entry far below the two real, tightly-stacked legend rows.

    Left in, that outlier (a) widens the recovery sweep's x-column so it chains
    through the data markers, bloating legend_bbox past the plot-fraction cap (it
    then returns None and the real legend swatches leak into the data), and (b)
    duplicates a series colour, breaking the colour-keyed label->series match.
    The isolated entry must be culled: only the two clustered rows remain, and a
    compact legend box forms.
    """
    region = Region(bbox=(362.0, 78.0, 538.0, 246.0),
                    path_indices=[], text_indices=[])
    green = (0.18, 0.59, 0.31)
    blue = (0.18, 0.49, 0.73)
    # Two genuine legend rows, tightly stacked at the top with coloured swatches.
    paths = [
        _cswatch(385, 98, green), _cswatch(385, 113, blue),
        # A green data marker in the plot interior beside the "EH3" annotation.
        _cswatch(497, 180, green),
    ]
    texts = [
        _text("before irradiation", 397, 98),
        _text("dose", 397, 113),
        _text("EH3", 505, 180),  # peak annotation far below the legend stack
    ]
    entries, bbox = _detect_legend(region, paths, texts)
    labels = [e[2] for e in entries]
    assert "EH3" not in labels, f"isolated annotation kept as legend entry: {labels}"
    assert len(entries) == 2, f"expected 2 legend rows, got {labels}"
    assert bbox is not None, "compact legend box should form once outlier is dropped"
    # Box stays on the two top rows, not stretched down to the annotation.
    assert bbox[3] <= 130, f"legend box swept down to annotation: {bbox}"
