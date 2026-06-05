"""Tests for axes.py: tick-mark detection and tick<->label pairing.

Parametrized over all fixtures (single-panel and per-panel). We assert that the
recovered labeled-tick *values* match the ground-truth ``xticks``/``yticks``:
detection may find a subset (minor ticks have no label, edge ticks may sit
outside the spine), but every value it does report must be correct and it must
recover most of them.
"""

from __future__ import annotations

import pytest

from conftest import fixture_names, load_truth, pdf_path
from pdf_chart2table.axes import detect_axes
from pdf_chart2table.pdf_vector import load_pdf
from pdf_chart2table.plot_region import detect_regions

ALL_FIXTURES = fixture_names()
_TOL = 1e-3  # tick values are exact decimals; allow float noise only.


def _axis_cases():
    """Yield (id, region, paths, texts, truth_ticks) for every axis of every panel."""
    cases = []
    for name in ALL_FIXTURES:
        truth = load_truth(name)
        page = load_pdf(pdf_path(name))[0]
        regions = detect_regions(page.paths, page.texts, page.width, page.height)
        if truth.get("n_panels", 1) > 1:
            panels = sorted(truth["panels"], key=lambda p: (p["row"], p["col"]))
            specs = [(p["xticks"], p["yticks"]) for p in panels]
        else:
            specs = [(truth["xticks"], truth["yticks"])]
        # Regions are row-major; truth panels sorted the same way.
        for region, (xt, yt) in zip(regions, specs):
            cid = f"{name}-r{region.row}c{region.col}"
            cases.append((f"{cid}-x", region, page, xt))
            cases.append((f"{cid}-y", region, page, yt))
    return cases


_CASES = _axis_cases()


def _detected_values(axis):
    return sorted(t.value for t in axis.ticks if t.value is not None)


def _approx_subset(got, truth) -> tuple[bool, list]:
    """Every detected value matches some truth value within tolerance."""
    extra = []
    for v in got:
        if not any(abs(v - t) <= _TOL + _TOL * abs(t) for t in truth):
            extra.append(v)
    return not extra, extra


def _count_matched(got, truth) -> int:
    return sum(
        1 for t in truth if any(abs(v - t) <= _TOL + _TOL * abs(t) for v in got)
    )


@pytest.mark.parametrize("cid,region,page,truth_ticks",
                         [(c[0], c[1], c[2], c[3]) for c in _CASES],
                         ids=[c[0] for c in _CASES])
def test_detected_tick_values_correct(cid, region, page, truth_ticks):
    """Detected labeled-tick values are all correct (subset of truth)."""
    x_axis, y_axis = detect_axes(region, page.paths, page.texts)
    axis = x_axis if cid.endswith("-x") else y_axis
    got = _detected_values(axis)
    ok, extra = _approx_subset(got, truth_ticks)
    assert ok, f"{cid}: spurious tick values {extra} not in truth {truth_ticks}"


@pytest.mark.parametrize("cid,region,page,truth_ticks",
                         [(c[0], c[1], c[2], c[3]) for c in _CASES],
                         ids=[c[0] for c in _CASES])
def test_recovers_most_ticks(cid, region, page, truth_ticks):
    """Detection finds most labeled ticks.

    Inner shared-axis panels legitimately have no labels (skipped here); axes
    showing a single in-range decade can't be matched beyond that one tick.
    """
    x_axis, y_axis = detect_axes(region, page.paths, page.texts)
    axis = x_axis if cid.endswith("-x") else y_axis
    got = _detected_values(axis)
    if not got:
        pytest.skip(f"{cid}: no tick labels on this axis (shared/inner panel)")
    matched = _count_matched(got, truth_ticks)
    # A couple of extreme ticks can sit at/just outside the spine; require most.
    need = max(1, min(len(truth_ticks), 2), len(truth_ticks) - 2)
    assert matched >= need, (
        f"{cid}: only matched {matched}/{len(truth_ticks)} ticks; got {got}"
    )


# --- regression: decimal-point and unit-suffix label parsing ----------------
from pdf_chart2table.axes import _is_numeric_span, _parse_plain


def test_decimal_point_span_is_numeric():
    # A lone "." (or sign) must count so "0.2" assembled from spans keeps its dot.
    assert _is_numeric_span(".")
    assert _is_numeric_span("-")
    assert _is_numeric_span("0")
    assert not _is_numeric_span("M")     # lone unit letter is not a label span
    assert not _is_numeric_span("loss")


def test_parse_plain_decimals_and_suffixes():
    assert _parse_plain("0.5") == 0.5
    assert _parse_plain("0.000") == 0.0
    assert _parse_plain("-0.25") == -0.25
    assert _parse_plain("5M") == 5_000_000.0
    assert _parse_plain("10k") == 10_000.0
    assert _parse_plain("50%") == 50.0


# --- minus-glyph: complex paths (circles, rings) must not be mistaken --------
from pdf_chart2table.axes import _is_minus_glyph
from pdf_chart2table.model import Path as VPath


def _make_path(pts, fill, stroke=None):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return VPath(
        points=pts,
        stroke=stroke,
        fill=fill,
        width=None,
        dashes=None,
        closed=True,
        bbox=(min(xs), min(ys), max(xs), max(ys)),
    )


def test_simple_rect_is_minus_glyph():
    """A 5-point filled flat rectangle in the right size range IS a minus glyph."""
    pts = [(100.0, 10.0), (105.0, 10.0), (105.0, 11.0), (100.0, 11.0), (100.0, 10.0)]
    p = _make_path(pts, fill=(0.0, 0.0, 0.0))
    assert _is_minus_glyph(p), "flat 5-pt filled rect should be a minus glyph"


def test_complex_ring_is_not_minus_glyph():
    """A many-point circle/ring with the same bounding box is NOT a minus glyph."""
    import math
    n = 40
    pts = [(100.0 + 2.5 * (1 + math.cos(2 * math.pi * i / n)),
            10.0 + 1.0 * (1 + math.sin(2 * math.pi * i / n)))
           for i in range(n + 1)]
    p = _make_path(pts, fill=(0.0, 0.0, 0.0))
    assert not _is_minus_glyph(p), (
        "complex ring (many points) must not be mistaken for a minus glyph"
    )


# --- axis-title selection: real title, not a sub-caption / figure caption ----
from pdf_chart2table.axes import _is_subcaption, _x_title, _group_labels, _ticks_from
from pdf_chart2table.model import Region, TextSpan


def test_is_subcaption_rejects_enumerators_and_captions():
    assert _is_subcaption("(a)")
    assert _is_subcaption("(b) Network")
    assert _is_subcaption("a)")
    assert _is_subcaption("Figure 2: training curves")
    assert _is_subcaption("Table 1")
    assert not _is_subcaption("Epoch")
    assert not _is_subcaption("Test error (%)")


def _span(text, x0, y0, x1, y1, size=8.0):
    return TextSpan(text=text, bbox=(x0, y0, x1, y1), size=size)


def test_x_title_picks_real_title_over_subcaption():
    """Centered word on the row just below the ticks wins over a "(a)" caption."""
    region = Region(bbox=(100.0, 50.0, 300.0, 150.0))
    labels = [_span("0", 100, 152, 106, 159), _span("100", 294, 152, 306, 159)]
    texts = labels + [
        _span("Epoch", 185, 162, 215, 169),          # real axis title (centered)
        _span("(a) Network", 160, 185, 240, 193),     # panel sub-caption below
    ]
    assert _x_title(texts, region, labels) == "Epoch"


def test_x_title_none_when_only_caption():
    """No axis title -> None (a figure caption is not a title)."""
    region = Region(bbox=(100.0, 50.0, 300.0, 150.0))
    labels = [_span("0", 100, 152, 106, 159), _span("100", 294, 152, 306, 159)]
    texts = labels + [_span("Figure 2: x", 120, 165, 200, 173)]
    assert _x_title(texts, region, labels) is None


# --- tick<->label pairing: cross-panel / twin-axis contaminant rejection -----

def test_y_labels_drop_offcolumn_contaminant():
    """A label leaking from another column (different right edge) is dropped."""
    # Genuine left-axis labels share right edge ~95 (just left of spine at 100);
    # a stray ".5" from a neighbour panel sits far left (right edge ~60).
    positions = [60.0, 80.0, 100.0]  # y tick pixel positions
    spans = [
        _span("0", 80, 58, 95, 62),    # right edge 95
        _span("1", 80, 78, 95, 82),
        _span("2", 80, 98, 95, 102),
        _span(".5", 45, 108, 60, 112), # contaminant: right edge 60, below ticks
    ]
    ticks = _ticks_from(positions, spans, [], "y")
    vals = sorted(t.value for t in ticks if t.value is not None)
    assert vals == [0.0, 1.0, 2.0]


def test_group_labels_keeps_adjacent_full_numbers_separate():
    """Same-size numbers with a real gap are distinct ticks, not one merged value."""
    spans = [
        _span("10000", 100, 50, 130, 58),
        _span("20000", 137, 50, 167, 58),  # ~7pt gap, same size -> separate
    ]
    groups = _group_labels(spans, "x")
    assert len(groups) == 2


def test_group_labels_merges_decimal_parts():
    """Touching same-size digit/point spans form one number ("0"+"."+"5")."""
    spans = [
        _span("0", 100, 50, 104, 58),
        _span(".", 104, 50, 106, 58),
        _span("5", 106, 50, 110, 58),
    ]
    groups = _group_labels(spans, "y")
    assert len(groups) == 1


def test_group_labels_merges_mantissa_and_smaller_exponent():
    """A "10" mantissa joins its raised, smaller exponent even across a gap."""
    spans = [
        _span("10", 100, 50, 113, 60, size=10.0),
        _span("3", 119, 47, 123, 54, size=7.0),  # 6pt gap but smaller size
    ]
    groups = _group_labels(spans, "y")
    assert len(groups) == 1
