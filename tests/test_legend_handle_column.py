"""Regression test for handle-column legend detection (2002.05277_p21c2).

A legend keyed by marker SHAPE (only red/blue, distinguished by o/^/s) with
math labels rendered as glyph outlines leaves no text entry and too few distinct
swatch colours for the text/colour detectors. It must instead be located by its
HANDLE COLUMN: the left-aligned stack of identical short line samples every
matplotlib legend draws. Without it, the legend's label-glyph blobs were read as
a phantom 74-point black scatter series sitting in the legend box.
"""
from __future__ import annotations

from pdf_chart2table.labels import _detect_handle_legend
from pdf_chart2table.model import Path, Region, TextSpan

REGION = Region(bbox=(273.0, 117.6, 418.9, 232.0),
                path_indices=[], text_indices=[])


def _handle(cy, *, x0=279.8, length=13.4, color=(1.0, 0.0, 0.0), dashes=None):
    """A short horizontal legend line sample at row ``cy``."""
    return Path(points=[(x0, cy), (x0 + length, cy)], stroke=color, fill=None,
                width=1.0, dashes=dashes, closed=False,
                bbox=(x0, cy - 0.3, x0 + length, cy + 0.3))


def _glyph(cx, cy):
    """A small label-character glyph outline on a legend row."""
    return Path(points=[(cx, cy), (cx + 4, cy), (cx + 4, cy + 5), (cx, cy + 5)],
                stroke=(0, 0, 0), fill=(0, 0, 0), width=0.3, dashes=None,
                closed=True, bbox=(cx, cy, cx + 4, cy + 5))


def test_handle_column_legend_detected():
    rows = [125.5, 132.1, 138.7, 145.3, 151.9, 158.5]
    colors = [(1.0, 0.0, 0.0), (0.0, 0.45, 0.74)]
    paths = [_handle(cy, color=colors[i % 2]) for i, cy in enumerate(rows)]
    # label-character glyphs to the right of the handle column on each row
    for cy in rows:
        for cx in (300.0, 312.0, 324.0):
            paths.append(_glyph(cx, cy - 2.5))
    box = _detect_handle_legend(REGION, paths, [])
    assert box is not None
    bx0, by0, bx1, by1 = box
    assert abs(bx0 - 279.8) < 2.0           # left edge at the handle column
    assert bx1 > 324.0                       # extended over the label glyphs
    assert by0 <= 125.5 and by1 >= 158.5     # spans all six rows


def test_two_error_bar_caps_not_a_legend():
    # Two left-aligned identical horizontal segments (a vertical error bar's two
    # caps) must NOT be taken for a legend — below the >=3-row threshold.
    paths = [_handle(140.0), _handle(160.0)]
    for cx in (300.0, 312.0):
        paths.append(_glyph(cx, 137.5))
    assert _detect_handle_legend(REGION, paths, []) is None


def test_handle_column_without_labels_rejected():
    # A bare stack of handles with NO label evidence is not a legend.
    rows = [125.5, 132.1, 138.7, 145.3]
    paths = [_handle(cy) for cy in rows]
    assert _detect_handle_legend(REGION, paths, []) is None
