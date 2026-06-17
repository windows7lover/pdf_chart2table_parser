"""Axis-spanning highlight bands (axvspan/axhspan): recover a full-height narrow
chromatic rectangle as an x-span, while a curve-following CI band and a near-white
background rectangle are NOT recovered.

Motivated by 2404.01379_p28c2: a cyan semi-transparent vertical highlight band
(~Vz 0.7-1.0) spanning the full plot height was dropped in the reconstruction.
The discriminator must separate such bands from CI bands / plain backgrounds.
"""
from __future__ import annotations

from pdf_chart2table.model import Path
from pdf_chart2table.style import recover_spans

# Plot box (PDF points). Identity-ish calibrations so pixel == data for x,
# and a flipped linear map for y (PDF y grows downward).
PLOT = (100.0, 50.0, 300.0, 250.0)  # x0, y0, x1, y1  (W=200, H=200)
XCAL = {"scale": "linear", "a": 1.0, "b": 0.0}            # data x = pixel x
YCAL = {"scale": "linear", "a": -1.0, "b": 300.0}         # data y = 300 - pixel y


def _rect(x0, y0, x1, y1, fill, alpha=None):
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    return Path(points=pts, stroke=None, fill=fill, width=0.0, dashes=None,
                closed=True, bbox=(x0, y0, x1, y1), fill_alpha=alpha)


def test_full_height_narrow_chromatic_rect_is_x_span():
    # Cyan band, full plot height, narrow width (50/200 = 0.25), semi-transparent.
    band = _rect(240.0, 50.0, 290.0, 250.0, (0.0, 1.0, 1.0), alpha=0.6)
    spans = recover_spans([band], PLOT, XCAL, YCAL)
    assert len(spans) == 1
    sp = spans[0]
    assert sp["axis"] == "x"
    assert sp["lo"] == 240.0 and sp["hi"] == 290.0   # data == pixel for XCAL
    assert sp["color"] == [0.0, 1.0, 1.0]
    assert sp["alpha"] == 0.6


def test_full_width_narrow_rect_is_y_span():
    # Horizontal band: full plot width, narrow height -> a y-span (axhspan).
    band = _rect(100.0, 120.0, 300.0, 150.0, (1.0, 0.9, 0.2), alpha=0.5)
    spans = recover_spans([band], PLOT, XCAL, YCAL)
    assert len(spans) == 1
    assert spans[0]["axis"] == "y"


def test_ci_band_following_a_curve_not_recovered():
    # A CI / confidence band traces a curve: tall AND wide, many distinct points.
    fwd = [(100.0 + 4.0 * i, 140.0 + 0.5 * i) for i in range(50)]
    back = [(x, y + 30.0) for x, y in reversed(fwd)]
    pts = fwd + back + [fwd[0]]
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    band = Path(points=pts, stroke=None, fill=(0.6, 0.7, 0.95), width=0.0,
                dashes=None, closed=True,
                bbox=(min(xs), min(ys), max(xs), max(ys)), fill_alpha=0.4)
    assert recover_spans([band], PLOT, XCAL, YCAL) == []


def test_near_white_background_rect_not_recovered():
    # The white plot background: spans BOTH dims and is near-white -> excluded.
    bg = _rect(100.0, 50.0, 300.0, 250.0, (1.0, 1.0, 1.0))
    assert recover_spans([bg], PLOT, XCAL, YCAL) == []


def test_chromatic_full_plot_rect_not_a_band():
    # A chromatic fill that spans BOTH dimensions (whole plot) is a background
    # tint, not a band -> excluded (full in both, narrow in neither).
    tint = _rect(100.0, 50.0, 300.0, 250.0, (0.6, 0.8, 0.95))
    assert recover_spans([tint], PLOT, XCAL, YCAL) == []


def test_no_spans_without_calibration():
    band = _rect(240.0, 50.0, 290.0, 250.0, (0.0, 1.0, 1.0), alpha=0.6)
    assert recover_spans([band], PLOT, None, YCAL) == []
    assert recover_spans([band], PLOT, XCAL, None) == []
