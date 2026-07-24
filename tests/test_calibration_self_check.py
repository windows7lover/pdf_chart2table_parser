"""Tests for the calibration self-consistency gate (prefer-drop-over-wrong).

Downstream (chart2table) found emitted calibrations that do not reproduce the
axis's OWN serialized ticks: ``fit_calibration`` may EXCLUDE an outlier tick
from the fit (``_drop_outlier`` / the drop-worst R^2 fallback) while the axis
still serializes that tick as labeled -- incoherent output that poisons every
downstream consistency check and, at worst, mis-calibrates all values.

``_apply`` now self-checks: a tick the calibration cannot reproduce (within
10% of the tick-value range, in fit space) gets its VALUE stripped (pixel +
label kept -> an honest unlabeled minor tick); more than 2 such ticks means
the fit itself is untrustworthy -> the axis is dropped (calibration None).
"""
from __future__ import annotations

from pdf_chart2table.calibrate import _apply, _self_check, to_data
from pdf_chart2table.model import Axis, Tick


def _axis(pairs):
    return Axis(ticks=[Tick(pixel=px, value=v) for px, v in pairs])


def test_clean_axis_untouched():
    ax = _apply(_axis([(10, 0), (20, 20), (30, 40), (40, 60), (50, 80)]))
    assert ax.calibration is not None
    assert all(t.value is not None for t in ax.ticks)


def test_merged_label_tick_stripped_not_serialized_as_labeled():
    # Merged-adjacent-label class bug ('250'+'280' -> 250280): with enough
    # clean ticks, fit_calibration EXCLUDES the outlier from the fit -- but
    # the tick used to stay serialized with the bogus value, so the emitted
    # calibration contradicted its own labeled ticks (downstream R12).
    # Now: calibration kept, poisoned tick's value -> None.
    pairs = [(10 * (i + 1), 10 * i) for i in range(9)]   # clean 0..80
    pairs.append((100, 500))                             # misread (should be 90)
    ax = _apply(_axis(pairs))
    assert ax.calibration is not None
    assert ax.ticks[-1].value is None, "excluded outlier must not stay labeled"
    assert ax.ticks[-1].pixel == 100   # pixel/label kept (honest minor tick)
    # The kept calibration reproduces the surviving ticks.
    for t in ax.ticks[:-1]:
        assert abs(to_data(ax.calibration, t.pixel) - t.value) < 1.0


def test_untrustworthy_fit_drops_axis():
    # A calibration that misses MOST of its ticks can never be repaired by
    # stripping -> reject (wrong values are worse than a skipped chart).
    ax = _axis([(0, 0), (10, 10), (20, 20), (30, 30)])
    bad = {"scale": "linear", "a": 5.0, "b": 0.0, "r2": 1.0}
    assert _self_check(ax, bad) is False


def test_log_axis_nonpositive_tick_is_a_miss():
    # A stored tick with value <= 0 on a log axis can never be consistent; it
    # is stripped like any other miss when the rest of the axis is sound.
    ax = _axis([(0, 1.0), (10, 10.0), (20, 100.0), (30, 1000.0), (40, -5.0)])
    calib = {"scale": "log", "a": 0.1, "b": 0.0, "r2": 1.0}
    assert _self_check(ax, calib) is True
    assert ax.ticks[-1].value is None
    assert all(t.value is not None for t in ax.ticks[:-1])
