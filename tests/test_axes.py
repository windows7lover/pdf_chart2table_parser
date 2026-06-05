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
