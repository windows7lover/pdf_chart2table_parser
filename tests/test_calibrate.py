"""Tests for calibrate.py: scale detection, round-trip, and shared axes.

Covers the M2 milestone gate:
  1. linear-vs-log scale matches ground truth (single + per-panel),
  2. calibration round-trips each detected tick within ~0.5% of axis range
     (in log space for log axes),
  3. shared-axis inner panels (no labels) borrow a valid calibration.

Axes that cannot self-calibrate (fewer than two labeled in-range ticks) are
reported as uncalibratable and skipped rather than faked.
"""

from __future__ import annotations

import math

import pytest

from conftest import fixture_names, load_truth, pdf_path
from pdf_chart2table.calibrate import calibrate_panels, calibrate_region, to_data
from pdf_chart2table.pdf_vector import load_pdf
from pdf_chart2table.plot_region import detect_regions

ALL_FIXTURES = fixture_names()
_ROUND_TRIP_FRAC = 0.005  # 0.5% of axis range (log space for log axes)


def _panels_for(name):
    """Return [(region, x_axis, y_axis, panel_truth), ...] for a fixture."""
    truth = load_truth(name)
    page = load_pdf(pdf_path(name))[0]
    regions = detect_regions(page.paths, page.texts, page.width, page.height)
    if truth.get("n_panels", 1) > 1:
        ptruth = sorted(truth["panels"], key=lambda p: (p["row"], p["col"]))
        axes = calibrate_panels(regions, page.paths, page.texts)
        return [(r, xa, ya, pt) for r, (xa, ya), pt in zip(regions, axes, ptruth)]
    xa, ya = calibrate_region(regions[0], page.paths, page.texts)
    flat = {"x_axis": truth["x_axis"], "y_axis": truth["y_axis"],
            "xticks": truth["xticks"], "yticks": truth["yticks"]}
    return [(regions[0], xa, ya, flat)]


def _axis_cases():
    cases = []
    for name in ALL_FIXTURES:
        for region, xa, ya, pt in _panels_for(name):
            cid = f"{name}-r{region.row}c{region.col}"
            cases.append((f"{cid}-x", xa, pt["x_axis"]["scale"]))
            cases.append((f"{cid}-y", ya, pt["y_axis"]["scale"]))
    return cases


_CASES = _axis_cases()


@pytest.mark.parametrize("cid,axis,truth_scale",
                         _CASES, ids=[c[0] for c in _CASES])
def test_scale_detection(cid, axis, truth_scale):
    """Detected linear/log scale matches ground truth (when calibratable)."""
    if axis.calibration is None:
        pytest.skip(f"{cid}: uncalibratable (<2 labeled in-range ticks)")
    assert axis.scale == truth_scale, (
        f"{cid}: detected {axis.scale}, expected {truth_scale}"
    )


@pytest.mark.parametrize("cid,axis,truth_scale",
                         _CASES, ids=[c[0] for c in _CASES])
def test_round_trip(cid, axis, truth_scale):
    """Mapping each detected tick pixel back reproduces its value."""
    if axis.calibration is None:
        pytest.skip(f"{cid}: uncalibratable")
    labeled = [t for t in axis.ticks if t.value is not None]
    if len(labeled) < 2:
        pytest.skip(f"{cid}: <2 labeled ticks")

    if axis.scale == "log":
        vals = [math.log10(t.value) for t in labeled]
        rng = max(vals) - min(vals)
        errs = [abs(math.log10(to_data(axis.calibration, t.pixel))
                    - math.log10(t.value)) for t in labeled]
    else:
        vals = [t.value for t in labeled]
        rng = max(vals) - min(vals)
        errs = [abs(to_data(axis.calibration, t.pixel) - t.value)
                for t in labeled]
    assert rng > 0
    assert max(errs) <= _ROUND_TRIP_FRAC * rng, (
        f"{cid}: round-trip err {max(errs):.4g} > {_ROUND_TRIP_FRAC * rng:.4g}"
    )


# --------------------------------------------------------------------------
# Shared axes: inner panels with no tick labels borrow a sibling's calibration.
# --------------------------------------------------------------------------

def _borrowed_panel(name, axis_attr, predicate):
    """Return (borrowed_axis, sibling_axis) for the inner shared panel."""
    truth = load_truth(name)
    page = load_pdf(pdf_path(name))[0]
    regions = detect_regions(page.paths, page.texts, page.width, page.height)
    axes = calibrate_panels(regions, page.paths, page.texts)
    which = 0 if axis_attr == "x" else 1
    borrowed = sibling = None
    for r, pair in zip(regions, axes):
        ax = pair[which]
        labeled = [t for t in ax.ticks if t.value is not None]
        if predicate(r) and len(labeled) == 0:
            borrowed = ax
        elif len(labeled) >= 2:
            sibling = ax
    return borrowed, sibling


def test_shared_x_inner_panel_borrows():
    """subplots_2x1_sharex top panel has no x labels -> borrows bottom panel."""
    borrowed, sibling = _borrowed_panel(
        "subplots_2x1_sharex", "x", lambda r: r.row == 0)
    assert borrowed is not None, "expected a label-less top panel"
    assert sibling is not None
    assert borrowed.calibration is not None, "inner panel should borrow calibration"
    assert borrowed.scale == sibling.scale
    # Its own tick marks must map to the sibling's tick values, round-trip.
    sib_vals = sorted(t.value for t in sibling.ticks if t.value is not None)
    got = sorted(to_data(borrowed.calibration, t.pixel) for t in borrowed.ticks)
    rng = max(sib_vals) - min(sib_vals)
    for v in sib_vals:
        assert any(abs(v - g) <= _ROUND_TRIP_FRAC * rng for g in got), (
            f"borrowed panel did not reproduce sibling tick {v}; got {got}"
        )


def test_shared_y_inner_panel_borrows():
    """subplots_1x2_sharey right panel has no y labels -> borrows left panel."""
    borrowed, sibling = _borrowed_panel(
        "subplots_1x2_sharey", "y", lambda r: r.col == 1)
    assert borrowed is not None, "expected a label-less right panel"
    assert sibling is not None
    assert borrowed.calibration is not None
    assert borrowed.scale == sibling.scale
    sib_vals = sorted(t.value for t in sibling.ticks if t.value is not None)
    got = sorted(to_data(borrowed.calibration, t.pixel) for t in borrowed.ticks)
    rng = max(sib_vals) - min(sib_vals)
    for v in sib_vals:
        assert any(abs(v - g) <= _ROUND_TRIP_FRAC * rng for g in got), (
            f"borrowed panel did not reproduce sibling tick {v}; got {got}"
        )


# --------------------------------------------------------------------------
# Sanity rejection: non-monotonic or poorly-fitting label pairs -> no fit.
# (Two side-by-side panels' x-ticks leaking into one band, stray caption nums.)
# --------------------------------------------------------------------------

def _ticks(pairs):
    from pdf_chart2table.model import Tick
    return [Tick(pixel=px, value=v) for px, v in pairs]


def test_fit_rejects_non_monotonic_pairs():
    """Pixel-ordered values that aren't monotonic (panel concatenation) -> None."""
    from pdf_chart2table.calibrate import fit_calibration

    # 0,20,40,60,80,100 then a second panel's 0,20,...: not monotonic in pixel.
    pairs = [(10, 0), (20, 20), (30, 40), (40, 60), (50, 0), (60, 20)]
    assert fit_calibration(_ticks(pairs)) is None


def test_fit_accepts_clean_monotonic_pairs():
    """A clean strictly-monotonic linear axis still fits."""
    from pdf_chart2table.calibrate import fit_calibration

    pairs = [(10, 0), (20, 20), (30, 40), (40, 60), (50, 80)]
    calib = fit_calibration(_ticks(pairs))
    assert calib is not None and calib["scale"] == "linear"


# --------------------------------------------------------------------------
# Magnitude sanity: a mis-parsed tick label (a 10^n superscript read on a
# linear axis, or merged digits) yields a perfect two-point fit to an absurd
# value. Reject it -> skip the axis. (Wallclock-time / histogram blow-ups that
# the judge saw reach ~1e16-1e34.)
# --------------------------------------------------------------------------

def test_fit_rejects_log_runaway_linear_magnitude():
    """A two-tick linear fit to an absurd value (1e34) is rejected."""
    from pdf_chart2table.calibrate import fit_calibration

    # [0, 1e34]: monotonic, R2=1.0, but the value is a parse error.
    assert fit_calibration(_ticks([(110, 0.0), (231, 1e34)])) is None


def test_fit_rejects_exponent_misread():
    """A single tick orders of magnitude off the others (e.g. 1e23) -> None."""
    from pdf_chart2table.calibrate import fit_calibration

    # Real ticks would be ~1e7..1e8; the last is a 1e23 misread.
    assert fit_calibration(_ticks([(80, 1.0e7), (120, 1.0e23)])) is None


def test_fit_rejects_gap_outlier_among_many_ticks():
    """With >=3 ticks, one value far off the spacing of the rest is rejected."""
    from pdf_chart2table.calibrate import fit_calibration

    # 0,2,4,6 then a huge outlier 1e10 (still under the 1e13 ceiling, caught by
    # the gap-outlier rule instead). Monotonic, but the spacing jumps wildly.
    assert fit_calibration(
        _ticks([(10, 0), (20, 2), (30, 4), (40, 6), (50, 1e10)])) is None


def test_fit_accepts_large_but_sane_linear_axis():
    """A legitimately large linear axis (counts in the thousands) still fits."""
    from pdf_chart2table.calibrate import fit_calibration

    pairs = [(10, 8450), (30, 8500), (50, 8550)]
    calib = fit_calibration(_ticks(pairs))
    assert calib is not None and calib["scale"] == "linear"


def test_fit_accepts_wide_log_axis():
    """A log axis spanning many decades is NOT rejected by the magnitude guard."""
    from pdf_chart2table.calibrate import fit_calibration

    # 1e0 .. 1e6 over evenly spaced pixels: a genuine 6-decade log axis.
    pairs = [(10, 1.0), (20, 100.0), (30, 1e4), (40, 1e6)]
    calib = fit_calibration(_ticks(pairs))
    assert calib is not None and calib["scale"] == "log"
