"""Validate tick spacing; OCR-recover a mis-extracted (merged) tick value.

A real axis is regularly spaced: linear ticks have a constant value step per
pixel, log ticks a constant log10 step. A tick whose value breaks that fit had
its label mis-read by the vector text reader -- overwhelmingly two ADJACENT
labels merged into one ('250'+'280' -> '250280', '-8'+'0' -> '-80'). One bad
value wrecks the whole axis calibration.

When a tick breaks the spacing we OCR ITS label region. OCR re-segments the
merged glyphs ('250280' -> boxes '250','280'), so we keep the number nearest the
tick's own pixel -- the value the vector reader merged away. If OCR can't confirm
a value that fits the spacing, the bad tick is dropped (a wrong value is worse
than a missing one). After correcting an axis it is re-fitted.

On by default; a NO-OP when there is no spacing outlier (the common case, so no
OCR engine is built), and when OCR is disabled (``PDFCHART_OCR=0``) it degrades
to simply DROPPING the outlier before the fit.
"""

from __future__ import annotations

import math

import fitz
import numpy as np

from . import calibrate as _calib
from . import ocr_backfill as _ocr
from .axes import _parse_plain
from .model import Axis, Region

_OUTLIER_STEPS = 0.5   # |residual| > this * median tick step => mis-extracted
_BAND = 20.0           # label-band depth beyond the spine (PDF pts)
_YBAND = 52.0          # y-tick label band width (left of the y spine)


def _logify(v, scale):
    return math.log10(v) if (scale == "log" and v and v > 0) else (
        None if scale == "log" else v)


def _robust_fit(ticks, scale):
    """Median-slope + median-intercept fit of value(/log10) vs pixel, with the
    median tick step. Robust to a minority of mis-read ticks. None if too few."""
    pairs = []
    for i, t in enumerate(ticks):
        if t.value is None:
            continue
        v = _logify(t.value, scale)
        if v is None:
            continue
        pairs.append((float(t.pixel), float(v), i))
    if len(pairs) < 3:
        return None
    # CONSECUTIVE-pair slopes (sorted by pixel): a single extreme outlier (a
    # merged label) corrupts only the 1-2 gaps adjacent to it, so the median
    # slope stays robust -- unlike all-pairs slopes, where one wild value skews
    # many pairs and can drag the fit onto the outlier.
    sp = sorted(pairs, key=lambda p: p[0])
    slopes = [(sp[k + 1][1] - sp[k][1]) / (sp[k + 1][0] - sp[k][0])
              for k in range(len(sp) - 1) if sp[k + 1][0] != sp[k][0]]
    if not slopes:
        return None
    A = sorted(slopes)[len(slopes) // 2]
    B = sorted(v - A * px for px, v, _ in pairs)[len(pairs) // 2]
    steps = sorted(abs(sp[k + 1][1] - sp[k][1]) for k in range(len(sp) - 1))
    step = next((s for s in steps if s > 1e-9), 1.0)
    return A, B, step, pairs


def spacing_outliers(ticks, scale):
    """Indices of ticks whose value breaks the regular pixel<->value spacing."""
    fit = _robust_fit(ticks, scale)
    if fit is None:
        return []
    A, B, step, pairs = fit
    out = [i for px, v, i in pairs if abs(v - (A * px + B)) / step > _OUTLIER_STEPS]
    # If most ticks are "outliers" the fit itself is untrustworthy -> do nothing.
    if len(out) > len(pairs) // 2:
        return []
    return out


def _fits(fit, pixel, value, scale):
    A, B, step, _ = fit
    v = _logify(value, scale)
    if v is None:
        return False
    return abs(v - (A * pixel + B)) / step <= _OUTLIER_STEPS


def _median_diff(xs):
    xs = sorted(xs)
    d = sorted(xs[k + 1] - xs[k] for k in range(len(xs) - 1))
    return d[len(d) // 2] if d else None


def _ocr_clip(fitz_page, clip):
    """OCR a clip; return [(text, center_x_pdf, center_y_pdf)] (PDF coords)."""
    eng = _ocr._engine()
    if eng is None:
        return []
    try:
        pix = fitz_page.get_pixmap(matrix=fitz.Matrix(_ocr._RENDER_SCALE,
                                                      _ocr._RENDER_SCALE),
                                   clip=fitz.Rect(*clip))
    except Exception:
        return []
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        img = img[:, :, :3]
    try:
        res, _ = eng(np.ascontiguousarray(img))
    except Exception:
        return []
    s = _ocr._RENDER_SCALE
    out = []
    for box, text, _conf in res or []:
        cx = sum(p[0] for p in box) / 4.0
        cy = sum(p[1] for p in box) / 4.0
        out.append((text, clip[0] + cx / s, clip[1] + cy / s))
    return out


def _correct_axis(axis: Axis, which: str, plot_box, fitz_page) -> bool:
    """Correct/drop spacing-outlier ticks on one axis. Returns True if changed."""
    outliers = spacing_outliers(axis.ticks, axis.scale)
    if not outliers:
        return False
    fit = _robust_fit(axis.ticks, axis.scale)
    px0, py0, px1, py1 = plot_box
    spacing = _median_diff([t.pixel for t in axis.ticks if t.value is not None]) or 30.0
    eng = _ocr._engine()
    changed = False
    for i in outliers:
        t = axis.ticks[i]
        if eng is None:  # OCR disabled -> safe degradation: drop the bad tick
            t.value = t.label = None
            changed = True
            continue
        if which == "x":
            clip = (t.pixel - spacing, py1 + 1, t.pixel + spacing, py1 + 1 + _BAND)
            toks = [(txt, cx) for txt, cx, cy in _ocr_clip(fitz_page, clip)]
        else:
            clip = (px0 - _YBAND, t.pixel - spacing, px0 - 1, t.pixel + spacing)
            toks = [(txt, cy) for txt, cx, cy in _ocr_clip(fitz_page, clip)]
        # parse numbers; nearest the tick pixel first (un-merges concatenations)
        cand = sorted(
            ((abs(pos - t.pixel), _parse_plain(txt), txt) for txt, pos in toks),
            key=lambda c: c[0])
        best = next(((v, raw) for _, v, raw in cand
                     if v is not None and _fits(fit, t.pixel, v, axis.scale)), None)
        if best:
            t.value, t.label = best[0], best[1]
        else:
            t.value = t.label = None
        changed = True
    return changed


def validate_and_correct(x_axis: Axis, y_axis: Axis, region: Region, fitz_page):
    """Fix spacing-outlier tick values (OCR re-read) and re-fit the affected axes.

    Needs both axes' pixel ranges to locate the label bands (x labels sit below
    the bottom spine = max(y pixel range); y labels left of the left spine)."""
    if x_axis.pixel_range is None or y_axis.pixel_range is None:
        return x_axis, y_axis
    px0, px1 = min(x_axis.pixel_range), max(x_axis.pixel_range)
    py0, py1 = min(y_axis.pixel_range), max(y_axis.pixel_range)
    box = (px0, py0, px1, py1)
    if _correct_axis(x_axis, "x", box, fitz_page):
        _calib._apply(x_axis)
    if _correct_axis(y_axis, "y", box, fitz_page):
        _calib._apply(y_axis)
    return x_axis, y_axis
