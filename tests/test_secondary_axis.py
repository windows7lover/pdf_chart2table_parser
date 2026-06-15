"""Dual-y-axis step 1: detect + calibrate a RIGHT (twin) y-axis.

A dual-y chart draws a second scale just right of the right spine (2011.06321_p8:
left sigma 0..1, right sigma 0..0.3 over the same pixel span). detect_secondary_y_axis
mirrors the left-label gather at the right spine; _apply fits it. Returns None when
there are fewer than three numeric right-side labels (not a real second axis).
"""
from __future__ import annotations

from pdf_chart2table.model import Region, TextSpan
from pdf_chart2table.axes import detect_secondary_y_axis
from pdf_chart2table.calibrate import _apply

# Plot region; right spine at x=180. Right labels sit just right of it.
REGION = Region(bbox=(110.0, 239.0, 180.0, 327.0))


def _num(s, cy, x0=183.0):
    return TextSpan(text=s, bbox=(x0, cy - 3, x0 + 12, cy + 3), size=7.0,
                    dir=(1.0, 0.0), color=None)


def test_secondary_axis_detected_and_calibrated():
    texts = [_num("0.0", 327.0), _num("0.1", 297.0),
             _num("0.2", 268.0), _num("0.3", 239.0)]
    sec = detect_secondary_y_axis(REGION, [], texts)
    assert sec is not None
    _apply(sec)
    assert sec.calibration is not None and sec.calibration["r2"] > 0.99
    lo, hi = sorted(sec.data_range)
    assert abs(lo - 0.0) < 0.02 and abs(hi - 0.3) < 0.02, sec.data_range


def test_no_secondary_axis_when_too_few_labels():
    # Only two right-side numbers -> not a second axis.
    texts = [_num("0.0", 327.0), _num("0.3", 239.0)]
    assert detect_secondary_y_axis(REGION, [], texts) is None


def test_left_side_labels_ignored():
    # Numbers LEFT of the region are the primary axis, not the secondary one.
    texts = [TextSpan(text="0.%d" % i, bbox=(90, 320 - 25 * i, 100, 326 - 25 * i),
                      size=7.0, dir=(1.0, 0.0), color=None) for i in range(4)]
    assert detect_secondary_y_axis(REGION, [], texts) is None
