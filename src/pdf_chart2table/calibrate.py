"""Pixel -> data calibration from detected (pixel, value) tick pairs.

An axis is calibrated by fitting ``value`` against ``pixel`` under two models and
keeping the better one:

* **linear**: ``value      ~= a*pixel + b``
* **log**:    ``log10(value) ~= a*pixel + b``   (only if all values > 0)

We pick whichever has the higher R². The result is stored on the ``Axis``
(``scale``, ``data_range``, ``calibration={a,b,r2}``); ``to_data`` /
``to_data_array`` invert the fit. At least two *distinct* labeled ticks are
required; otherwise the axis is left uncalibrated (``calibration=None``), which
callers treat as a skip reason.

Shared axes: under matplotlib ``sharex``/``sharey`` the inner panels carry tick
*marks* but no tick *labels*, so they cannot self-calibrate. ``calibrate_panels``
fills such a panel's missing axis by borrowing the fit from an aligned sibling
(via ``Region.shares_x_with`` / ``shares_y_with``) that does have labeled ticks,
provided the panels share the same pixel range on that axis.

Public API:
    fit_calibration(ticks) -> dict | None
    to_data(calib, pixel) -> float
    to_data_array(calib, pixels) -> np.ndarray
    calibrate_region(region, paths, texts) -> (x_axis, y_axis)
    calibrate_panels(regions, paths, texts) -> list[(x_axis, y_axis)]
"""

from __future__ import annotations

import math

import numpy as np

from .axes import detect_axes
from .model import Axis, Path, Region, TextSpan

# Tolerance (points) for treating two panels' pixel ranges as the same axis.
_RANGE_TOL = 3.0


def _linfit(px: np.ndarray, val: np.ndarray) -> tuple[float, float, float]:
    """Least-squares fit val ~= a*px + b; return (a, b, r2)."""
    a, b = np.polyfit(px, val, 1)
    pred = a * px + b
    ss_res = float(np.sum((val - pred) ** 2))
    ss_tot = float(np.sum((val - np.mean(val)) ** 2))
    r2 = 1.0 if ss_tot == 0 else 1.0 - ss_res / ss_tot
    return float(a), float(b), r2


# Below this R² gap the linear/log fits are a tie and we look at minor ticks.
_R2_TIE = 1e-3
# A minor tick is "log-consistent" if its mantissa is within this of an integer.
_LOG_MANTISSA_TOL = 0.12
# Outlier rejection: a label-band contaminant (e.g. a stray corner label parsed
# as a tick) ruins the linear fit. If the fit is this poor and there are at
# least this many labeled ticks, drop the single worst-residual tick and refit;
# accept the drop only if the refit is at least this good (a true outlier; well
# behaved synthetic axes always fit ~1.0 and never trigger this).
_OUTLIER_R2_MAX = 0.95
_OUTLIER_MIN_TICKS = 5
_OUTLIER_R2_GOOD = 0.999

# Sanity floor: a calibration whose (pixel, value) pairs aren't monotonic, or
# whose best fit is worse than this, is almost certainly mis-paired label text
# (stray caption/legend numbers, or two panels' ticks merged). Reject it ->
# skip the axis (and hence the chart). Precision over recall. Synthetic axes
# fit ~1.0 and are strictly monotonic, so they never trip this.
_MIN_FIT_R2 = 0.97

# Magnitude sanity for LINEAR axes: a real linear chart axis essentially never
# carries a tick value beyond this. Values of 1e16/1e23/1e34 are tick-LABEL
# parse errors (a "10^n" superscript misread on a non-log axis, or merged tick
# digits) that produce a monotonic, R2=1.0 two-point fit and so slip every other
# guard. Synthetic linear axes top out at 100; the largest sane real linear
# axes (counts, FLOPs) stay well under this. -> reject the axis (skip).
_MAX_LINEAR_ABS = 1e13
# Single-tick outlier (>=3 labeled ticks): if one labeled value is wildly out of
# step with the spacing implied by the rest, it is a misread. Reject the axis if
# the largest gap between sorted values exceeds this multiple of the median gap.
_TICK_GAP_OUTLIER = 50.0


def _log_minor_consistent(a: float, b: float, minor_px: list[float]) -> bool:
    """Do the unlabeled minor ticks land on log minor positions (mantissa 1-9)?

    Under a log fit the value of a minor tick is ``10**(a*px+b)``; its mantissa
    ``value / 10**floor(log10 value)`` should be a near-integer 2..9. This
    distinguishes a genuine log axis from a linear one when only two labeled
    decade ticks are available (both fits then have R²=1).
    """
    if len(minor_px) < 2:
        return False
    good = 0
    for px in minor_px:
        logv = a * px + b
        mant = 10.0 ** (logv - math.floor(logv))
        if abs(mant - round(mant)) <= _LOG_MANTISSA_TOL and 1 <= round(mant) <= 9:
            good += 1
    return good >= max(2, int(0.7 * len(minor_px)))


def _drop_outlier(px: np.ndarray, val: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Drop one gross outlier tick if it clearly wrecks an otherwise linear fit.

    Real charts sometimes leak a stray label into the tick band (e.g. a corner
    label merged with the first x-tick), giving one badly off (pixel, value)
    pair. With enough remaining ticks we drop the worst-residual one when doing
    so turns a poor fit into a near-perfect one. Conservative by construction:
    synthetic axes already fit ~1.0 and never enter this branch.
    """
    if len(px) < _OUTLIER_MIN_TICKS:
        return px, val
    _, _, r2 = _linfit(px, val)
    if r2 >= _OUTLIER_R2_MAX:
        return px, val
    a, b, _ = _linfit(px, val)
    resid = np.abs(val - (a * px + b))
    worst = int(np.argmax(resid))
    keep = np.arange(len(px)) != worst
    px2, val2 = px[keep], val[keep]
    if len(set(val2.tolist())) < 2:
        return px, val
    _, _, r2_2 = _linfit(px2, val2)
    if r2_2 >= _OUTLIER_R2_GOOD:
        return px2, val2
    return px, val


def _values_sane(scale: str, val: np.ndarray) -> bool:
    """Reject labeled tick values that are physically implausible (parse errors).

    Two checks (precision over recall):
    * **linear magnitude**: a linear axis with a value beyond ``_MAX_LINEAR_ABS``
      is a mis-parsed label (e.g. a ``10^34`` superscript read on a non-log axis)
      that otherwise yields a perfect two-point fit. Log axes legitimately span
      many decades, so this applies to linear only.
    * **gap outlier**: with >=3 ticks, one value sitting orders of magnitude off
      the spacing of the rest (largest sorted gap >> median gap) is a misread.
    """
    if scale == "linear" and float(np.max(np.abs(val))) > _MAX_LINEAR_ABS:
        return False
    if len(val) >= 3:
        sv = np.sort(val if scale != "log" else np.log10(val))
        gaps = np.diff(sv)
        med = float(np.median(gaps))
        if med > 0 and float(np.max(gaps)) > _TICK_GAP_OUTLIER * med:
            return False
    return True


def fit_calibration(ticks) -> dict | None:
    """Fit a pixel->value mapping from labeled ticks.

    ``ticks`` is any iterable of objects with ``.pixel`` and ``.value``; ticks
    with ``value is None`` are unlabeled minor ticks, used only to break a
    linear/log tie. Returns ``{"scale", "a", "b", "r2"}`` or ``None`` if fewer
    than two distinct labeled ticks are available.
    """
    pts = [(t.pixel, t.value) for t in ticks if t.value is not None]
    minor_px = [t.pixel for t in ticks if t.value is None]
    # Deduplicate by pixel so repeated detections don't bias the fit.
    seen: dict[float, float] = {}
    for px, val in pts:
        seen.setdefault(round(px, 3), val)
    px = np.array(list(seen.keys()), dtype=float)
    val = np.array(list(seen.values()), dtype=float)
    if len(px) < 2 or len(set(val.tolist())) < 2:
        return None

    px, val = _drop_outlier(px, val)

    # Sanity gate: labeled values must be monotonic in pixel order. A non-
    # monotonic sequence means mis-paired labels (e.g. two side-by-side panels'
    # x-ticks captured in one band: 0,20,..,100,0,20,..). Reject -> skip.
    order = np.argsort(px)
    vo = val[order]
    if not (np.all(np.diff(vo) > 0) or np.all(np.diff(vo) < 0)):
        return None

    a_lin, b_lin, r2_lin = _linfit(px, val)
    best = {"scale": "linear", "a": a_lin, "b": b_lin, "r2": r2_lin}

    if np.all(val > 0):
        a_log, b_log, r2_log = _linfit(px, np.log10(val))
        log_fit = {"scale": "log", "a": a_log, "b": b_log, "r2": r2_log}
        if r2_log > r2_lin + _R2_TIE:
            best = log_fit
        elif abs(r2_log - r2_lin) <= _R2_TIE and _log_minor_consistent(
            a_log, b_log, minor_px
        ):
            # Fits tie on the labeled ticks; minor ticks reveal a log axis.
            best = log_fit
    if best["r2"] < _MIN_FIT_R2:
        return None
    if not _values_sane(best["scale"], val):
        return None
    return best


def to_data(calib: dict, pixel: float) -> float:
    """Map one pixel coordinate to a data value via a fitted calibration."""
    lin = calib["a"] * pixel + calib["b"]
    return 10.0 ** lin if calib["scale"] == "log" else lin


def to_data_array(calib: dict, pixels) -> np.ndarray:
    """Vectorized :func:`to_data`."""
    px = np.asarray(pixels, dtype=float)
    lin = calib["a"] * px + calib["b"]
    return np.power(10.0, lin) if calib["scale"] == "log" else lin


def _apply(axis: Axis) -> Axis:
    """Fit ``axis`` in place from its labeled ticks and record the result."""
    calib = fit_calibration(axis.ticks)
    if calib is None:
        axis.calibration = None
        return axis
    axis.scale = calib["scale"]
    axis.calibration = calib
    if axis.pixel_range is not None:
        d0 = to_data(calib, axis.pixel_range[0])
        d1 = to_data(calib, axis.pixel_range[1])
        axis.data_range = (d0, d1)
    return axis


def calibrate_region(
    region: Region,
    paths: list[Path],
    texts: list[TextSpan],
) -> tuple[Axis, Axis]:
    """Detect and calibrate both axes of a single region."""
    x_axis, y_axis = detect_axes(region, paths, texts)
    return _apply(x_axis), _apply(y_axis)


def _ranges_match(a: tuple | None, b: tuple | None) -> bool:
    if a is None or b is None:
        return False
    return abs(a[0] - b[0]) <= _RANGE_TOL and abs(a[1] - b[1]) <= _RANGE_TOL


def calibrate_panels(
    regions: list[Region],
    paths: list[Path],
    texts: list[TextSpan],
) -> list[tuple[Axis, Axis]]:
    """Calibrate every panel, borrowing shared-axis calibration where needed.

    A panel whose x (resp. y) axis has no labeled ticks borrows the calibration
    from an aligned ``shares_x_with`` (resp. ``shares_y_with``) sibling that is
    calibrated and shares the same pixel range on that axis.
    """
    axes = [calibrate_region(r, paths, texts) for r in regions]

    def borrow(i: int, which: int, siblings: list[int]) -> None:
        """which: 0=x axis, 1=y axis."""
        target = axes[i][which]
        if target.calibration is not None:
            return
        # Only the genuine shared-axis case borrows: a panel with NO tick labels
        # at all. A panel that has some labels but too few to fit is its own
        # independent (uncalibratable) axis, not a sibling of the labeled one.
        if any(t.value is not None for t in target.ticks):
            return
        for j in siblings:
            src = axes[j][which]
            if src.calibration is None:
                continue
            if not _ranges_match(target.pixel_range, src.pixel_range):
                continue
            target.scale = src.scale
            target.calibration = dict(src.calibration)
            if target.pixel_range is not None:
                target.data_range = (
                    to_data(target.calibration, target.pixel_range[0]),
                    to_data(target.calibration, target.pixel_range[1]),
                )
            return

    for i, r in enumerate(regions):
        borrow(i, 0, r.shares_x_with)
        borrow(i, 1, r.shares_y_with)

    return axes
