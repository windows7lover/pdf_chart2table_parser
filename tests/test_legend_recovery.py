"""Tests for legend recovery: multi-span label assembly, style-aware
(colour AND solid/dashed/marker) matching, and shared-legend propagation to the
sibling panels of a split multi-panel figure.

These build small synthetic ``Path`` / ``TextSpan`` / ``Region`` / ``Series``
objects directly (no PDF) to exercise the geometry deterministically.
"""

from __future__ import annotations

from pdf_chart2table.cli import _apply_legend_labels, _resolve_legends, _sibling_groups
from pdf_chart2table.labels import detect_labels
from pdf_chart2table.model import Path, Region, Series, TextSpan

BLUE = (0.0, 0.0, 1.0)
RED = (1.0, 0.0, 0.0)


def _line(x0, y, x1, color, dashes=None):
    """A horizontal line swatch on row y."""
    return Path(points=[(x0, y), (x1, y)], stroke=color, fill=None, width=1.0,
                dashes=dashes, closed=False, bbox=(x0, y, x1, y))


def _span(text, x0, y, w=40.0, h=6.0, size=6.0):
    return TextSpan(text=text, bbox=(x0, y, x0 + w, y + h), size=size, dir=(1.0, 0.0))


def _region(bbox, **kw):
    return Region(bbox=bbox, **kw)


# --------------------------------------------------------------------------
# 1. Multi-span label assembly
# --------------------------------------------------------------------------

def test_multi_span_label_assembly():
    """A label split across spans (e.g. "BN-x5-Sigmoid", "T = 100") is joined
    into one entry; the next legend row is a separate entry."""
    region = _region((50.0, 40.0, 300.0, 200.0))
    swatch = _line(60.0, 50.0, 80.0, BLUE)
    # Row 1: three adjacent spans forming one label.
    s1a = _span("BN-", 84.0, 47.0, w=12.0)
    s1b = _span("x5-", 96.5, 47.0, w=12.0)
    s1c = _span("Sigmoid", 109.0, 47.0, w=24.0)
    # Row 2 (well below): a single-span label with its own swatch.
    swatch2 = _line(60.0, 70.0, 80.0, RED)
    s2 = _span("Adam", 84.0, 67.0, w=24.0)

    labels = detect_labels(region, [swatch, swatch2], [s1a, s1b, s1c, s2])
    found = {lab for _, _, lab in labels.legend}
    assert found == {"BN-x5-Sigmoid", "Adam"}


def test_label_stops_at_large_gap():
    """A far-away span on the same row (next legend column) is not merged."""
    region = _region((50.0, 40.0, 400.0, 200.0))
    swatch = _line(60.0, 50.0, 80.0, BLUE)
    near = _span("GD", 84.0, 47.0, w=14.0)
    far = _span("Momentum", 250.0, 47.0, w=40.0)  # big horizontal gap
    labels = detect_labels(region, [swatch], [near, far])
    assert {lab for _, _, lab in labels.legend} == {"GD"}


# --------------------------------------------------------------------------
# 2. Style-aware matching: same colour, solid vs dashed -> different labels
# --------------------------------------------------------------------------

def _series_at(color, x0, y0, x1, y1):
    """A line series whose endpoints sit at the given pixel coordinates."""
    return Series(label=None, marker=None, color=color, points=[
        {"x": 0, "y": 0, "x_px": x0, "y_px": y0},
        {"x": 1, "y": 1, "x_px": x1, "y_px": y1},
    ])


def test_style_aware_matching_solid_vs_dashed():
    """Two blue series, one solid one dashed, with a solid "Test" and a dashed
    "Train" blue legend entry: colour alone is ambiguous; the series' own path
    (matched by endpoints) supplies the disambiguating style."""
    # A solid blue curve and a dashed blue curve in one region (>=4 vertices).
    solid = Path(points=[(60, 100), (90, 110), (120, 120), (150, 130)],
                 stroke=BLUE, fill=None, width=1.0, dashes=None, closed=False,
                 bbox=(60, 100, 150, 130))
    dashed = Path(points=[(60, 150), (90, 155), (120, 160), (150, 165)],
                  stroke=BLUE, fill=None, width=1.0, dashes="[2 2] 0",
                  closed=False, bbox=(60, 150, 150, 165))
    region = _region((50, 40, 300, 200), path_indices=[0, 1])
    paths = [solid, dashed]

    legend = [("dashed", BLUE, "Train"), ("line", BLUE, "Test")]
    s_solid = _series_at(BLUE, 60, 100, 150, 130)   # matches the solid path
    s_dashed = _series_at(BLUE, 60, 150, 150, 165)  # matches the dashed path

    _apply_legend_labels([s_solid, s_dashed], legend, region, paths)
    assert s_solid.label == "Test"
    assert s_dashed.label == "Train"


def test_marker_vs_line_same_colour():
    """A marker series and a line series share a colour; style picks correctly."""
    legend = [("marker", BLUE, "scatter"), ("line", BLUE, "curve")]
    s_marker = Series(label=None, marker="o", color=BLUE, points=[{"x": 0, "y": 0}])
    solid = Path(points=[(60, 100), (90, 110), (120, 120), (150, 130)],
                 stroke=BLUE, fill=None, width=1.0, dashes=None, closed=False,
                 bbox=(60, 100, 150, 130))
    s_line = _series_at(BLUE, 60, 100, 150, 130)
    region = _region((50, 40, 300, 200), path_indices=[0])
    _apply_legend_labels([s_marker, s_line], legend, region, [solid])
    assert s_marker.label == "scatter"
    assert s_line.label == "curve"


def test_ambiguous_same_colour_same_style_left_unlabeled():
    """When colour AND style cannot disambiguate, leave the label None."""
    legend = [("line", BLUE, "A"), ("line", BLUE, "B")]
    solid = Path(points=[(60, 100), (90, 110), (120, 120), (150, 130)],
                 stroke=BLUE, fill=None, width=1.0, dashes=None, closed=False,
                 bbox=(60, 100, 150, 130))
    s = _series_at(BLUE, 60, 100, 150, 130)  # solid, but two solid blue entries
    region = _region((50, 40, 300, 200), path_indices=[0])
    _apply_legend_labels([s], legend, region, [solid])
    assert s.label is None


def test_unique_colour_matches_regardless_of_style():
    """A single colour-matching entry is used even without a style match."""
    legend = [("dashed", RED, "only")]
    s = Series(label=None, marker=None, color=RED, points=[{"x": 0, "y": 0}])
    _apply_legend_labels([s], legend)
    assert s.label == "only"


# --------------------------------------------------------------------------
# 3. Shared-legend propagation across sibling split panels
# --------------------------------------------------------------------------

def test_shared_legend_propagates_to_sibling_panels():
    """Two sibling panels (shared y axis); only the left carries a legend ->
    the right inherits it."""
    left = _region((50, 40, 250, 200), row=0, col=0, shares_y_with=[1])
    right = _region((260, 40, 460, 200), row=0, col=1, shares_y_with=[0])
    legends = [[("line", BLUE, "Adam")], []]  # right has none
    resolved = _resolve_legends(legends, [left, right])
    assert resolved[0] == [("line", BLUE, "Adam")]
    assert resolved[1] == [("line", BLUE, "Adam")]


def test_no_propagation_when_each_panel_has_its_own_legend():
    """If every sibling panel has its own legend, none is overwritten."""
    a = _region((50, 40, 250, 200), shares_x_with=[1])
    b = _region((50, 210, 250, 360), shares_x_with=[0])
    legends = [[("line", BLUE, "left")], [("line", RED, "right")]]
    resolved = _resolve_legends(legends, [a, b])
    assert resolved[0] == [("line", BLUE, "left")]
    assert resolved[1] == [("line", RED, "right")]


def test_no_propagation_across_unrelated_single_panels():
    """Two independent single-panel regions (no sibling link) are not merged."""
    a = _region((50, 40, 250, 200))
    b = _region((300, 40, 500, 200))
    legends = [[("line", BLUE, "x")], []]
    resolved = _resolve_legends(legends, [a, b])
    assert resolved[1] == []  # unrelated -> no propagation


def test_sibling_groups_components():
    a = _region((0, 0, 1, 1), shares_x_with=[1])
    b = _region((0, 0, 1, 1), shares_x_with=[0], shares_y_with=[2])
    c = _region((0, 0, 1, 1), shares_y_with=[1])
    d = _region((0, 0, 1, 1))  # lone panel
    groups = _sibling_groups([a, b, c, d])
    groups = sorted(sorted(g) for g in groups)
    assert groups == [[0, 1, 2], [3]]
