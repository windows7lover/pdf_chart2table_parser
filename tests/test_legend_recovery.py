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


def test_style_matching_with_path_closeback():
    """Two blue series whose source paths close back to the axis after the last
    data vertex (a common PDF convention for filled area / log plot lines).

    The series endpoint (last x_px/y_px) matches an *interior* point of the
    path, not p.points[-1]. _series_style must search all path points to find
    the matching path and return the correct solid/dashed style.

    Mirrors the astro_2606.02711 pattern where all 8 legend entries were None
    because _series_style could not match any path.
    """
    # Solid path: real data ends at (150, 130), then path closes back to (150, 200).
    solid = Path(
        points=[(60, 100), (90, 110), (120, 120), (150, 130), (150, 200)],
        stroke=BLUE, fill=None, width=1.0, dashes=None, closed=False,
        bbox=(60, 100, 150, 200),
    )
    # Dashed path: data ends at (150, 165), then closes back to (150, 200).
    dashed = Path(
        points=[(60, 150), (90, 155), (120, 160), (150, 165), (150, 200)],
        stroke=BLUE, fill=None, width=1.0, dashes="[2 2] 0",
        closed=False, bbox=(60, 150, 150, 200),
    )
    region = _region((50, 40, 300, 250), path_indices=[0, 1])
    paths = [solid, dashed]

    legend = [("dashed", BLUE, "Train"), ("line", BLUE, "Test")]
    # Series endpoints are the last *data* points, not the axis-closing vertex.
    s_solid = _series_at(BLUE, 60, 100, 150, 130)   # last data at (150, 130)
    s_dashed = _series_at(BLUE, 60, 150, 150, 165)  # last data at (150, 165)

    _apply_legend_labels([s_solid, s_dashed], legend, region, paths)
    assert s_solid.label == "Test", f"solid should be 'Test', got {s_solid.label!r}"
    assert s_dashed.label == "Train", f"dashed should be 'Train', got {s_dashed.label!r}"


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


def test_multi_legend_colour_match_propagates_to_correct_sibling():
    """2×3 grid: two legend panels (one per row); each empty panel gets the
    legend whose entry colors match its data-path colors.

    Mirrors the 2606.03405 page-6 pattern:
      row 0 (regions 0-2): region 0 has legend with BLUE/RED entries;
        region 2 has BLUE+RED data paths -> should get the row-0 legend.
      row 1 (regions 3-5): region 3 has legend with GREEN/ORANGE entries;
        region 5 has GREEN+ORANGE data paths -> should get the row-1 legend.
    """
    GREEN = (0.0, 0.5, 0.0)
    ORANGE = (1.0, 0.5, 0.0)

    # Row 0: regions 0,1,2 share y-axis (same row).
    r0 = _region((0, 0, 100, 100), row=0, col=0, shares_y_with=[1, 2], shares_x_with=[3])
    r1 = _region((110, 0, 210, 100), row=0, col=1, shares_y_with=[0, 2], shares_x_with=[4])
    r2 = _region((220, 0, 320, 100), row=0, col=2, shares_y_with=[0, 1], shares_x_with=[5])
    # Row 1: regions 3,4,5 share y-axis.
    r3 = _region((0, 110, 100, 210), row=1, col=0, shares_y_with=[4, 5], shares_x_with=[0])
    r4 = _region((110, 110, 210, 210), row=1, col=1, shares_y_with=[3, 5], shares_x_with=[1])
    r5 = _region((220, 110, 320, 210), row=1, col=2, shares_y_with=[3, 4], shares_x_with=[2])

    # Fake data paths: region 2 has BLUE+RED paths; region 5 has GREEN+ORANGE paths.
    # Regions 1 and 4 are empty (no paths, no legend).
    def _data_path(color):
        return Path(points=[(0, 0), (10, 10)], stroke=color, fill=None,
                    width=1.0, dashes=None, closed=False, bbox=(0, 0, 10, 10))

    all_paths = [_data_path(BLUE), _data_path(RED), _data_path(GREEN), _data_path(ORANGE)]

    # Assign path indices to regions: region 2 gets paths 0,1; region 5 gets 2,3.
    r2.path_indices = [0, 1]
    r5.path_indices = [2, 3]

    legends = [
        [("line", BLUE, "Alpha"), ("line", RED, "Beta")],  # r0: row-0 legend
        [],   # r1: no legend
        [],   # r2: no legend, but has BLUE+RED data paths
        [("line", GREEN, "Gamma"), ("line", ORANGE, "Delta")],  # r3: row-1 legend
        [],   # r4: no legend
        [],   # r5: no legend, but has GREEN+ORANGE data paths
    ]

    resolved = _resolve_legends(legends, [r0, r1, r2, r3, r4, r5], paths=all_paths)

    # Region 2 should get the row-0 legend (BLUE/RED match).
    assert resolved[2] == [("line", BLUE, "Alpha"), ("line", RED, "Beta")], \
        f"region 2 should get row-0 legend, got {resolved[2]}"
    # Region 5 should get the row-1 legend (GREEN/ORANGE match).
    assert resolved[5] == [("line", GREEN, "Gamma"), ("line", ORANGE, "Delta")], \
        f"region 5 should get row-1 legend, got {resolved[5]}"
    # Regions with no data-path colors and no direct donor stay empty.
    # Region 1 and 4 have no path_indices → fall back to direct-donor check.
    # r1 has shares_y_with=[0,2]; neither 0 nor 2 is the only direct legend donor
    # with an unambiguous link (both r0 and r3 are in the same group but r3 is not
    # in r1's shares_y_with) → region 1 gets r0's legend via direct_donors=[0].
    assert resolved[1] == [("line", BLUE, "Alpha"), ("line", RED, "Beta")], \
        f"region 1 should get row-0 legend via direct donor, got {resolved[1]}"
    # Region 0 and 3 already had their own legends.
    assert resolved[0] == legends[0]
    assert resolved[3] == legends[3]


def test_multi_legend_ambiguous_colour_leaves_empty():
    """When two legend panels have the same entry colors, an empty panel with
    those data colors cannot be assigned to either uniquely -> stays empty.

    Garbled/duplicate legend scenario: wrong_entry is worse than missing.
    """
    # Two legend panels with identical BLUE entries.
    r0 = _region((0, 0, 100, 100), row=0, col=0, shares_y_with=[1])
    r1 = _region((110, 0, 210, 100), row=0, col=1, shares_y_with=[0])
    r2 = _region((220, 0, 320, 100), row=0, col=2)  # unrelated (no sibling links)

    # r2 is not a sibling at all, so no propagation expected regardless.
    # Both r0 and r1 have BLUE legends; r1 has a sibling BLUE legend too.
    # For sibling group {0,1}: both have legends -> no empty panel to propagate to.
    legends = [
        [("line", BLUE, "LegendA")],
        [("line", BLUE, "LegendB")],
        [],
    ]
    resolved = _resolve_legends(legends, [r0, r1, r2])
    # r0 and r1 each keep their own legend; r2 is unrelated and stays empty.
    assert resolved[0] == [("line", BLUE, "LegendA")]
    assert resolved[1] == [("line", BLUE, "LegendB")]
    assert resolved[2] == []


def test_multi_legend_tied_score_leaves_empty():
    """Empty panel whose data path colors match BOTH legend panels equally well
    must NOT receive either legend (ambiguous -> leave empty).
    """
    # Region 0 has legend with BLUE; region 1 has legend with BLUE (same color).
    # Region 2 has BLUE data path -> tied between r0 and r1 -> stays empty.
    r0 = _region((0, 0, 100, 100), row=0, col=0, shares_y_with=[1, 2])
    r1 = _region((110, 0, 210, 100), row=0, col=1, shares_y_with=[0, 2])
    r2 = _region((220, 0, 320, 100), row=0, col=2, shares_y_with=[0, 1])

    def _data_path(color):
        return Path(points=[(0, 0), (10, 10)], stroke=color, fill=None,
                    width=1.0, dashes=None, closed=False, bbox=(0, 0, 10, 10))

    all_paths = [_data_path(BLUE)]
    r2.path_indices = [0]

    legends = [
        [("line", BLUE, "LegendA")],
        [("line", BLUE, "LegendB")],
        [],
    ]
    resolved = _resolve_legends(legends, [r0, r1, r2], paths=all_paths)
    # Tied: both score 1 for region 2 -> no propagation.
    assert resolved[2] == [], f"expected empty, got {resolved[2]}"


# --------------------------------------------------------------------------
# 4. Order-based fallback in _apply_legend_labels (black/neutral-swatch pattern)
# --------------------------------------------------------------------------

def _series_no_color(y0):
    """A series with no colour (or a black colour that doesn't match swatches)."""
    return Series(label=None, marker=None, color=None, points=[
        {"x": 0, "y": 0, "x_px": 0, "y_px": y0},
    ])


def test_order_fallback_full_unmatched_fires():
    """Case 1: NO colour match at all and counts equal -> assign by order.

    Mirrors 2606.03405 where legend swatches carry neutral/black colours that
    fail colour matching. With 2 series and 2 entries, both are assigned
    top-to-bottom (series 0 at lower y_px -> earlier in PDF top-down order).
    """
    # Two series with no colour.
    s0 = _series_no_color(y0=20.0)  # appears first top-down (smaller y_px)
    s1 = _series_no_color(y0=80.0)  # appears second

    # Two legend entries (already in reading order, i.e. top-to-bottom).
    legend = [("line", None, "Alpha"), ("line", None, "Beta")]

    _apply_legend_labels([s0, s1], legend)
    assert s0.label == "Alpha", f"expected 'Alpha', got {s0.label!r}"
    assert s1.label == "Beta",  f"expected 'Beta', got {s1.label!r}"


def test_order_fallback_single_remaining_fires():
    """Case 2: ONE series and ONE entry remain after partial colour matching.

    Blue series gets matched by colour; the remaining grey series and grey
    entry are assigned unconditionally.
    """
    GREY = (0.5, 0.5, 0.5)
    s_blue = Series(label=None, marker=None, color=BLUE, points=[
        {"x": 0, "y": 0, "x_px": 0, "y_px": 10.0},
    ])
    s_grey = Series(label=None, marker=None, color=None, points=[
        {"x": 0, "y": 0, "x_px": 0, "y_px": 50.0},
    ])
    # Legend: "BlueLabel" has a matching blue colour; "GreyLabel" has None colour.
    legend = [("line", BLUE, "BlueLabel"), ("line", None, "GreyLabel")]

    _apply_legend_labels([s_blue, s_grey], legend)
    assert s_blue.label == "BlueLabel", f"expected 'BlueLabel', got {s_blue.label!r}"
    assert s_grey.label == "GreyLabel", f"expected 'GreyLabel', got {s_grey.label!r}"


def test_order_fallback_partial_multi_blocked():
    """Excluded case: some colour matches made AND N>1 remain -> NO fallback.

    One blue series matched; two remaining series and two remaining entries are
    NOT assigned (order is uncertain when partial matching already fired with
    N>1 left over).
    """
    s_blue = Series(label=None, marker=None, color=BLUE, points=[
        {"x": 0, "y": 0, "x_px": 0, "y_px": 10.0},
    ])
    s_grey1 = Series(label=None, marker=None, color=None, points=[
        {"x": 0, "y": 0, "x_px": 0, "y_px": 50.0},
    ])
    s_grey2 = Series(label=None, marker=None, color=None, points=[
        {"x": 0, "y": 0, "x_px": 0, "y_px": 90.0},
    ])
    # Legend: blue entry matched; two unlabelled entries remain.
    legend = [
        ("line", BLUE, "BlueLabel"),
        ("line", None, "Unlabelled1"),
        ("line", None, "Unlabelled2"),
    ]

    _apply_legend_labels([s_blue, s_grey1, s_grey2], legend)
    assert s_blue.label == "BlueLabel", f"expected 'BlueLabel', got {s_blue.label!r}"
    # The remaining two must NOT be assigned (partial-multi blocked).
    assert s_grey1.label is None, f"expected None, got {s_grey1.label!r}"
    assert s_grey2.label is None, f"expected None, got {s_grey2.label!r}"


# --------------------------------------------------------------------------
# 5. Gradient legend: a colour-ramp legend (blue->red temperature scale) whose
# swatch colours sit just OUTSIDE _COLOR_TOL of their series. Greedy per-series
# matching pairs only the single nearest entry; the global 1-to-1 nearest-colour
# assignment recovers all four in temperature order (2308.12778_p3c2: only
# '55 mK' of four matched before the fix).
# --------------------------------------------------------------------------

def _gradient_series(color, y_px):
    return Series(label=None, marker=None, color=color, points=[
        {"x": 0, "y": 0, "x_px": 0, "y_px": y_px},
    ])


def test_gradient_legend_global_assignment():
    """Four blue->red series matched to four ramp-coloured entries 1-to-1.

    Legend swatch colours are the saturated ramp endpoints; series colours are
    the (slightly desaturated) data hues. Only the bluest pair is within
    _COLOR_TOL, so greedy matching alone would drop three labels. The global
    assignment maps every series to its nearest entry unambiguously.
    """
    # Legend entries (top-to-bottom): coldest (blue) -> hottest (red).
    legend = [
        ("marker", (0.0, 0.0, 1.0), "55 mK"),
        ("marker", (0.1255, 0.0, 0.8745), "118 mK"),
        ("marker", (0.3333, 0.0, 0.6667), "214 mK"),
        ("marker", (0.9961, 0.0, 0.0039), "333 mK"),
    ]
    # Series in PDF order (reddest first, as extracted for 2308.12778_p3c2).
    s0 = _gradient_series((0.8863, 0.0, 0.1137), 10.0)  # red   -> 333 mK
    s1 = _gradient_series((0.5137, 0.0, 0.4863), 20.0)  # purple-> 214 mK
    s2 = _gradient_series((0.2118, 0.0, 0.7882), 30.0)  #        -> 118 mK
    s3 = _gradient_series((0.0118, 0.0, 0.9882), 40.0)  # blue  -> 55 mK
    _apply_legend_labels([s0, s1, s2, s3], legend)
    assert s0.label == "333 mK", f"got {s0.label!r}"
    assert s1.label == "214 mK", f"got {s1.label!r}"
    assert s2.label == "118 mK", f"got {s2.label!r}"
    assert s3.label == "55 mK", f"got {s3.label!r}"


def test_gradient_assignment_bails_on_ambiguous_colours():
    """The global-assignment helper refuses an ambiguous near-tie.

    Two series sit symmetrically between two entry colours so swapping the
    assignment costs almost nothing (margin < _GRADIENT_MARGIN). The helper
    returns False and leaves every label None rather than risk a swap.
    """
    from pdf_chart2table.cli import _apply_gradient_assignment

    legend = [
        ("marker", (0.0, 0.0, 1.0), "A"),
        ("marker", (0.0, 0.0, 0.96), "B"),
    ]
    s0 = _gradient_series((0.0, 0.0, 0.99), 10.0)
    s1 = _gradient_series((0.0, 0.0, 0.97), 20.0)
    applied = _apply_gradient_assignment([s0, s1], legend)
    assert applied is False
    assert s0.label is None and s1.label is None


def test_gradient_assignment_skips_when_entry_colour_missing():
    """Not fully coloured (an entry has no swatch colour) -> helper bails."""
    from pdf_chart2table.cli import _apply_gradient_assignment

    legend = [("marker", (0.0, 0.0, 1.0), "A"), ("marker", None, "B")]
    s0 = _gradient_series((0.0, 0.0, 0.99), 10.0)
    s1 = _gradient_series((1.0, 0.0, 0.0), 20.0)
    assert _apply_gradient_assignment([s0, s1], legend) is False
    assert s0.label is None and s1.label is None


# --------------------------------------------------------------------------
# Inline labels: a BLACK series labelled in plain black text, sitting in the
# same left-aligned column as the coloured inline labels, is recovered too
# (2005.13879_p3c3: the "θ = 21.8°" black curve was dropped from the legend).
# --------------------------------------------------------------------------

def _cspan(text, x0, y, color, w=40.0, h=6.0, size=6.0):
    """A horizontal text span carrying its fill colour (None = black)."""
    return TextSpan(text=text, bbox=(x0, y, x0 + w, y + h), size=size,
                    dir=(1.0, 0.0), color=color)


def test_inline_black_label_in_coloured_column():
    region = _region((50.0, 40.0, 300.0, 200.0))
    # Coloured data curves (non-black) inside the region.
    blue_curve = _line(60.0, 60.0, 280.0, BLUE)
    red_curve = _line(60.0, 80.0, 280.0, RED)
    # A black data curve (the one whose label the colour-keyed pass can't match).
    black_curve = _line(60.0, 100.0, 280.0, (0.0, 0.0, 0.0))
    # Inline labels: two coloured ones forming a left-aligned column, plus a
    # black label slotting into the same column just below them.
    t_blue = _cspan("theta = 0.8", 70.0, 58.0, BLUE)
    t_red = _cspan("theta = 3.5", 70.0, 78.0, RED)
    t_black = _cspan("theta = 21.8", 70.0, 98.0, None)

    labels = detect_labels(region, [blue_curve, red_curve, black_curve],
                           [t_blue, t_red, t_black])
    found = {(col, lab) for _, col, lab in labels.legend}
    assert ((0.0, 0.0, 0.0), "theta = 21.8") in found, found

    # And it flows through to the black series via colour matching.
    s_blue = _series_at(BLUE, 60, 60, 280, 60)
    s_red = _series_at(RED, 60, 80, 280, 80)
    s_black = _series_at((0.0, 0.0, 0.0), 60, 100, 280, 100)
    _apply_legend_labels([s_blue, s_red, s_black], labels.legend)
    assert s_black.label == "theta = 21.8", f"got {s_black.label!r}"


def test_inline_black_label_needs_two_coloured_anchors():
    """A lone black span (no confirmed coloured column) is NOT taken as a label
    -- the column geometry is what guards against black axis ticks leaking in."""
    region = _region((50.0, 40.0, 300.0, 200.0))
    blue_curve = _line(60.0, 60.0, 280.0, BLUE)
    black_curve = _line(60.0, 100.0, 280.0, (0.0, 0.0, 0.0))
    t_blue = _cspan("theta = 0.8", 70.0, 58.0, BLUE)
    t_black = _cspan("theta = 21.8", 70.0, 98.0, None)  # only ONE coloured anchor

    labels = detect_labels(region, [blue_curve, black_curve], [t_blue, t_black])
    found = {lab for _, _, lab in labels.legend}
    assert "theta = 21.8" not in found, found


# --------------------------------------------------------------------------
# Legend entry ORDER: the recovered order strings are cleaned the same way the
# per-series style labels are, so the renderer's order-match (which compares
# order entries against the plotted, already-cleaned labels) succeeds and the
# legend reads top-to-bottom instead of silently keeping extraction order
# (2005.13879_p3c3: a trailing BEL control glyph broke the match).
# --------------------------------------------------------------------------

def test_legend_order_strips_control_chars_top_to_bottom():
    import fitz

    from pdf_chart2table.style import recover_text_style

    doc = fitz.open()
    page = doc.new_page(width=300, height=300)
    region = (50.0, 40.0, 260.0, 200.0)
    # Coloured data curves at descending rows.
    page.draw_line(fitz.Point(60, 60), fitz.Point(250, 60), color=(0, 0, 1), width=1)
    page.draw_line(fitz.Point(60, 90), fitz.Point(250, 90), color=(1, 0, 0), width=1)
    # Inline labels carry a trailing BEL ("\x07") -- a mangled degree sign.
    page.insert_text(fitz.Point(70, 60), "A = 1\x07", fontsize=6, color=(0, 0, 1))
    page.insert_text(fitz.Point(70, 90), "A = 2\x07", fontsize=6, color=(1, 0, 0))
    series_labels = ["A = 1\x07", "A = 2\x07"]

    style = recover_text_style(page, region, {}, series_labels, None, [])
    order = style["legend"]["order"]
    # Control char stripped (matches the cleaned series labels), top-to-bottom.
    assert order == ["A = 1", "A = 2"], order
    assert all("\x07" not in o for o in order)
