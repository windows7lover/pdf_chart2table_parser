"""Curve-bounded confidence/uncertainty BANDS (filled fill_between regions):
recover a translucent fill polygon bounded above+below by curve-like edges as a
shaded overlay, while marker fills / legend swatches / opaque area fills / plain
closed shapes / few-point axvspan rectangles are NOT turned into bands.

Motivated by 2309.03591_p9c3 (and 2002.05937_p5c2): light-pink + purple CI bands
between curve pairs were dropped -- only the boundary lines rendered. The
discriminator must recover genuine fill_between bands while staying precise.
"""
from __future__ import annotations

from pdf_chart2table.model import Path
from pdf_chart2table.style import recover_bands

# Plot box (PDF points); identity x calibration, flipped linear y (PDF y down).
PLOT = (100.0, 50.0, 300.0, 250.0)  # W=200, H=200
XCAL = {"scale": "linear", "a": 1.0, "b": 0.0}
YCAL = {"scale": "linear", "a": -1.0, "b": 300.0}


def _ci_band(x0=100.0, n=50, dx=4.0, slope=0.5, thick=30.0,
             fill=(0.78, 0.25, 0.49), alpha=0.15):
    """A fill_between polygon: forward along the upper envelope, back along the
    lower. Wide x-span, real enclosed area, many vertices, translucent."""
    fwd = [(x0 + dx * i, 140.0 + slope * i) for i in range(n)]
    back = [(x, y + thick) for x, y in reversed(fwd)]
    pts = fwd + back + [fwd[0]]
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    return Path(points=pts, stroke=None, fill=fill, width=0.0, dashes=None,
                closed=True, bbox=(min(xs), min(ys), max(xs), max(ys)),
                fill_alpha=alpha)


def _rect(x0, y0, x1, y1, fill, alpha=None, closed=True):
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    return Path(points=pts, stroke=None, fill=fill, width=0.0, dashes=None,
                closed=closed, bbox=(x0, y0, x1, y1), fill_alpha=alpha)


def test_curve_bounded_band_recovered():
    bands = recover_bands([_ci_band()], PLOT, XCAL, YCAL)
    assert len(bands) == 1
    b = bands[0]
    assert b["color"] == [0.78, 0.25, 0.49]
    assert b["alpha"] == 0.15
    # Aligned x grid with matching lo/hi arrays for fill_between.
    assert len(b["x"]) == len(b["y_lo"]) == len(b["y_hi"]) > 2
    # A real band has positive thickness over its interior (endpoints may meet
    # where the fill polygon closes the two envelopes together).
    assert sum(lo < hi for lo, hi in zip(b["y_lo"], b["y_hi"])) > len(b["x"]) // 2
    # x spans the polygon's full data range.
    assert b["x"][0] < b["x"][-1]


def test_two_nested_bands_both_recovered():
    outer = _ci_band(fill=(0.78, 0.25, 0.49), thick=40.0)
    inner = _ci_band(fill=(0.39, 0.0, 0.65), thick=18.0)
    bands = recover_bands([outer, inner], PLOT, XCAL, YCAL)
    assert len(bands) == 2
    assert {tuple(b["color"]) for b in bands} == {
        (0.78, 0.25, 0.49), (0.39, 0.0, 0.65)}


# ---- precision guards: these must NOT become bands ----

def test_opaque_area_fill_not_a_band():
    # No alpha (fully opaque) -> a solid area / violin fill (data), not a CI band.
    band = _ci_band(alpha=None)
    assert recover_bands([band], PLOT, XCAL, YCAL) == []
    # High alpha (mostly opaque) is likewise excluded.
    assert recover_bands([_ci_band(alpha=0.9)], PLOT, XCAL, YCAL) == []


def test_marker_glyph_fill_not_a_band():
    # A small translucent filled diamond (a marker face): tiny x-span -> rejected.
    glyph = Path(points=[(150.0, 150.0), (152.0, 148.0), (154.0, 150.0),
                         (152.0, 152.0), (150.0, 150.0)],
                 stroke=None, fill=(0.78, 0.25, 0.49), width=0.0, dashes=None,
                 closed=True, bbox=(150.0, 148.0, 154.0, 152.0), fill_alpha=0.3)
    assert recover_bands([glyph], PLOT, XCAL, YCAL) == []


def test_legend_swatch_rect_not_a_band():
    # A small translucent legend swatch rectangle: narrow x-span -> rejected.
    sw = _rect(110.0, 60.0, 130.0, 70.0, (0.78, 0.25, 0.49), alpha=0.3)
    assert recover_bands([sw], PLOT, XCAL, YCAL) == []


def test_axvspan_rectangle_not_a_band():
    # A wide translucent rectangle is a few-point axvspan (recover_spans' job),
    # not a curve-following envelope -> rejected here (< _BAND_MIN_VERTS).
    wide = _rect(110.0, 60.0, 290.0, 240.0, (0.0, 1.0, 1.0), alpha=0.3)
    assert recover_bands([wide], PLOT, XCAL, YCAL) == []


def test_thin_outline_curve_not_a_band():
    # A thin data CURVE that merely carries a fill: its outline traces out-and-back
    # along nearly the same path, enclosing ~zero area -> rejected (area guard).
    fwd = [(100.0 + 4.0 * i, 150.0 + 0.3 * i) for i in range(50)]
    back = [(x, y + 0.2) for x, y in reversed(fwd)]  # ~zero thickness
    pts = fwd + back + [fwd[0]]
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    curve = Path(points=pts, stroke=None, fill=(0.78, 0.25, 0.49), width=0.0,
                 dashes=None, closed=True,
                 bbox=(min(xs), min(ys), max(xs), max(ys)), fill_alpha=0.3)
    assert recover_bands([curve], PLOT, XCAL, YCAL) == []


def test_near_white_fill_not_a_band():
    bg = _ci_band(fill=(0.98, 0.98, 0.98))
    assert recover_bands([bg], PLOT, XCAL, YCAL) == []


def test_no_bands_without_calibration():
    band = _ci_band()
    assert recover_bands([band], PLOT, None, YCAL) == []
    assert recover_bands([band], PLOT, XCAL, None) == []


# ---- fill-to-baseline AREAS (#80): translucent fill under a density/KDE curve ----

def _area_to_baseline(cx=180.0, n=40, dx=1.5, peak=120.0,
                      fill=(0.27, 0.51, 0.71), alpha=0.25, baseline=250.0):
    """A fill_between polygon whose TOP follows a bell curve and whose BOTTOM is
    flat at the plot-box bottom (PDF y=250 = max pixel y). Narrow x-span (a KDE
    peak), so the strict band width gate would reject it without the baseline
    discriminator. Top run forward (x increasing), baseline run back."""
    import math
    half = n * dx / 2.0
    x0 = cx - half
    fwd = [(x0 + dx * i,
            baseline - peak * math.exp(-((dx * i - half) ** 2) / (2 * (half / 2.5) ** 2)))
           for i in range(n)]
    back = [(x, baseline) for x, _ in reversed(fwd)]
    pts = fwd + back + [fwd[0]]
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    return Path(points=pts, stroke=None, fill=fill, width=0.0, dashes=None,
                closed=True, bbox=(min(xs), min(ys), max(xs), max(ys)),
                fill_alpha=alpha)


def test_fill_to_baseline_area_recovered():
    # Narrow (x-span < 0.4 of box width) translucent fill UNDER a curve down to the
    # axis baseline -> recovered as a band even though it fails the strict width
    # gate, because one envelope is flat at the plot-box bottom.
    area = _area_to_baseline()
    bx0, _, bx1, _ = area.bbox
    assert (bx1 - bx0) < 0.4 * (PLOT[2] - PLOT[0])  # would fail the strict gate
    bands = recover_bands([area], PLOT, XCAL, YCAL)
    assert len(bands) == 1
    b = bands[0]
    assert b["color"] == [0.27, 0.51, 0.71]
    assert b["alpha"] == 0.25
    # The lower envelope is ~FLAT at the data baseline (pixel 250 -> data 50 here);
    # most grid points pin to the baseline (a couple may cross near the closing
    # endpoints where the two envelopes meet).
    assert sum(abs(v - 50.0) < 1.0 for v in b["y_lo"]) > 0.8 * len(b["y_lo"])
    # The upper envelope rises well above the baseline (the curve peak).
    assert max(b["y_hi"]) - 50.0 > 50.0


def test_narrow_midplot_band_not_an_area():
    # A NARROW two-curve band floating in the MIDDLE of the plot (neither envelope
    # flat at the bottom) is not a fill-to-baseline area -> strict width gate
    # still rejects it.
    narrow = _ci_band(x0=180.0, n=15, dx=2.0, thick=20.0)
    bx0, _, bx1, _ = narrow.bbox
    assert (bx1 - bx0) < 0.4 * (PLOT[2] - PLOT[0])
    assert recover_bands([narrow], PLOT, XCAL, YCAL) == []
