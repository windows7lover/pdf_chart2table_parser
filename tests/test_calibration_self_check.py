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


def test_tick_misses_rejects_contradicting_borrow_candidate():
    # 2008.09734 p26 region 5: a panel's own axis reads decade ticks
    # 1e16/1e19 (log 2-point fit), while a same-pixel-column sibling is
    # linear 80..240. The sibling's calibration maps the panel's tick pixels
    # to ~180/300 -- contradicting the panel's own labels by ~100% of range.
    # Borrowing must be refused for such a sibling.
    from pdf_chart2table.calibrate import _tick_misses

    labeled = [Tick(pixel=251.1, value=1e16), Tick(pixel=291.5, value=1e19)]
    sibling_linear = {"scale": "linear", "a": 2.9735, "b": -566.65, "r2": 1.0}
    assert _tick_misses(labeled, sibling_linear), \
        "a borrow candidate contradicting the target's own ticks must be rejected"
    # ...while the panel's own log fit reproduces them (no misses).
    own_log = {"scale": "log",
               "a": (19 - 16) / (291.5 - 251.1),
               "b": 16 - 251.1 * (19 - 16) / (291.5 - 251.1), "r2": 1.0}
    assert _tick_misses(labeled, own_log) == []


def test_borrow_still_fills_unlabeled_panel():
    # The genuine shared-axis case (no labeled ticks on the inner panel) has
    # nothing to contradict -> _tick_misses is empty and borrowing proceeds.
    from pdf_chart2table.calibrate import _tick_misses

    assert _tick_misses([], {"scale": "linear", "a": 1.0, "b": 0.0}) == []
    assert _tick_misses([Tick(pixel=10, value=5.0)],
                        {"scale": "linear", "a": 1.0, "b": 0.0}) == []


def test_log_axis_nonpositive_tick_is_a_miss():
    # A stored tick with value <= 0 on a log axis can never be consistent; it
    # is stripped like any other miss when the rest of the axis is sound.
    ax = _axis([(0, 1.0), (10, 10.0), (20, 100.0), (30, 1000.0), (40, -5.0)])
    calib = {"scale": "log", "a": 0.1, "b": 0.0, "r2": 1.0}
    assert _self_check(ax, calib) is True
    assert ax.ticks[-1].value is None
    assert all(t.value is not None for t in ax.ticks[:-1])


# --------------------------------------------------------------------------
# Spacing-pattern coherence FLAG (Axis.tick_consistency = "suspect").
# Downstream's _fix_scale found axes whose fitted scale contradicts the VALUE
# pattern of the labels (geometric values on a linear fit / arithmetic values
# on a log fit) yet pass every hard gate: monotonic, R^2 ~ 1.0, self-check
# clean. We FLAG (never drop or re-fit) so consumers can gate on it.
# --------------------------------------------------------------------------

def test_geometric_values_on_linear_fit_flagged_suspect():
    # Tick pixels PROPORTIONAL to geometric values 1,2,4,8: the linear fit is
    # exact (R^2 = 1.0), beats log, and reproduces every tick -- but labels
    # doubling tick-over-tick read like a log axis. Flag; keep the calibration.
    ax = _apply(_axis([(10, 1.0), (20, 2.0), (40, 4.0), (80, 8.0)]))
    assert ax.calibration is not None and ax.scale == "linear"
    assert ax.tick_consistency == "suspect"
    assert all(t.value is not None for t in ax.ticks)  # nothing dropped


def test_arithmetic_values_on_log_fit_flagged_suspect():
    # Tick pixels proportional to log10(value) so the LOG fit is exact, but the
    # values 10,40,70,100 step by a constant +30 over a full decade -- the
    # labels read like a linear axis. Flag; keep the calibration.
    ax = _apply(_axis([(100.0, 10.0), (160.2, 40.0), (184.5, 70.0),
                       (200.0, 100.0)]))
    assert ax.calibration is not None and ax.scale == "log"
    assert ax.tick_consistency == "suspect"


def test_clean_linear_axis_not_flagged():
    # Arithmetic values on a linear fit: coherent -> no flag. (Ratios of an
    # arithmetic run decay toward 1; median never exceeds the 1.5 gate.)
    ax = _apply(_axis([(0, 10.0), (10, 20.0), (20, 30.0), (30, 40.0),
                       (40, 50.0)]))
    assert ax.calibration is not None and ax.scale == "linear"
    assert ax.tick_consistency is None


def test_clean_log_decade_axis_not_flagged():
    # Decade ticks on a log fit: differences 9,90,900 are wildly non-constant
    # -> no arithmetic pattern -> no flag.
    ax = _apply(_axis([(0, 1.0), (10, 10.0), (20, 100.0), (30, 1000.0)]))
    assert ax.calibration is not None and ax.scale == "log"
    assert ax.tick_consistency is None


def test_three_geometric_ticks_below_floor_not_flagged():
    # Conservative floor: < 4 labeled ticks -> never flag, even if geometric.
    ax = _apply(_axis([(10, 1.0), (20, 2.0), (40, 4.0)]))
    assert ax.calibration is not None
    assert ax.tick_consistency is None
