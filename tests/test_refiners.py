"""Regression tests for refiners.drop_spurious_lines.

Reproduces the spurious-line bugs: a connector through scatter markers
(2410.00955) and a straight reference/fit line drawn through scatter data
(2510.04789 orange fit). Guards that a genuine dense line+marker DATA curve and
a pure line chart are NOT touched.
"""
from __future__ import annotations

from pdf_chart2table.model import Axis, Path, Region, Series
from pdf_chart2table.refiners import (
    drop_spurious_lines,
    is_decoration_line,
    refine_decoration,
    refine_dropped_curve,
    refine_region_overcapture,
)


def _series(marker, pix, color=(0, 0, 0)):
    return Series(label=None, marker=marker, color=color,
                  points=[{"x": x, "y": y, "x_px": x, "y_px": y} for x, y in pix])


def test_connector_through_scatter_dropped():
    # 4 scatter markers; a line whose vertices ARE the marker positions.
    marks = _series("o", [(10, 10), (20, 25), (30, 15), (40, 35)])
    connector = _series(None, [(10, 10), (20, 25), (30, 15), (40, 35)])
    kept, reasons = drop_spurious_lines([marks, connector])
    assert marks in kept and connector not in kept
    assert any("connector" in r for r in reasons)


def test_straight_fit_line_dropped():
    # A straight line in the SAME colour as the markers is a same-colour overlay
    # (decoration) -> dropped. (Both default to black via _series.)
    marks = _series("s", [(10, 12), (25, 40), (40, 8), (55, 33)])  # scatter
    fit = _series(None, [(5 + i, 5 + 2 * i) for i in range(60)])    # perfectly straight
    kept, reasons = drop_spurious_lines([marks, fit])
    assert marks in kept and fit not in kept
    assert any("straight" in r for r in reasons)


def test_chromatic_straight_fit_line_kept():
    # 2006.01115_p33c2: black square markers + a straight RED linear fit line
    # through them. A straight line in a SATURATED colour distinct from the
    # markers is a chromatic fit/trend line (DATA), not decoration -> KEEP it.
    marks = _series("s", [(10, 12), (25, 40), (40, 8), (55, 33)], color=(0, 0, 0))
    fit = _series(None, [(5 + i, 5 + 2 * i) for i in range(60)], color=(1.0, 0.0, 0.0))
    kept, reasons = drop_spurious_lines([marks, fit])
    assert marks in kept and fit in kept, "chromatic fit line distinct from markers must be kept"
    assert not any("straight" in r for r in reasons)


def test_chromatic_connector_still_dropped():
    # Even a colour-distinct line is dropped when it is a CONNECTOR (its vertices
    # sit ON the markers): the connector test fires before the fit-line colour
    # gate, so a red line tracing red... no -- tracing distinct markers is still
    # decoration when it merely joins them.
    marks = _series("o", [(10, 10), (20, 25), (30, 15), (40, 35)], color=(0, 0, 0))
    connector = _series(None, [(10, 10), (20, 25), (30, 15), (40, 35)], color=(1.0, 0.0, 0.0))
    kept, reasons = drop_spurious_lines([marks, connector])
    assert connector not in kept, "a connector through markers is decoration regardless of colour"
    assert any("connector" in r for r in reasons)


def test_grey_straight_line_through_markers_dropped():
    # A near-grey (unsaturated) straight line through markers is NOT a chromatic
    # fit line: gridline/spine/baseline-shaped decoration -> still dropped.
    marks = _series("s", [(10, 12), (25, 40), (40, 8), (55, 33)], color=(0, 0, 0))
    grey = _series(None, [(5 + i, 5 + 2 * i) for i in range(60)], color=(0.5, 0.5, 0.5))
    kept, reasons = drop_spurious_lines([marks, grey])
    assert grey not in kept, "an unsaturated straight line is not a chromatic fit -> dropped"
    assert any("straight" in r for r in reasons)


def test_genuine_line_plus_marker_data_kept():
    # A dense curve through markers: most line vertices fall BETWEEN markers, so
    # the connector fraction is low and the curve is not straight -> KEEP it.
    marks = _series("o", [(10, 10), (30, 30), (50, 12), (70, 40)])
    curve = _series(None, [(10 + i, 10 + 18 * (i / 60.0) ** 2 * (1 if i < 40 else -1))
                           for i in range(61)])
    kept, _ = drop_spurious_lines([marks, curve])
    assert curve in kept, "a dense data curve through markers must be kept"


def test_chromatic_dense_fit_curve_over_scatter_kept():
    # 2206.03650_p3c2: blue scatter "Exp. Data" + a RED fitted exponential decay
    # sampled at many vertices that passes THROUGH the data points. Because the
    # fit fits the data, ~all of its vertices land within a few px of a marker, so
    # the connector fraction is high (~0.9). But it is a SATURATED colour distinct
    # from the markers AND far denser than them (many vertices per data point), so
    # it is a chromatic fit curve (DATA), not a same-colour 1:1 connector -> KEEP.
    import math
    scatter = _series(
        "o", [(10 + 8 * i, 50 * math.exp(-0.25 * i)) for i in range(12)],
        color=(0.12, 0.56, 1.0),
    )
    # Dense red curve: 200 samples tracing the same exponential through the points.
    fit = _series(
        None, [(10 + 8 * (t / 200.0) * 11, 50 * math.exp(-0.25 * (t / 200.0) * 11))
               for t in range(201)],
        color=(1.0, 0.0, 0.0),
    )
    kept, reasons = drop_spurious_lines([scatter, fit])
    assert scatter in kept, "the scatter series must be kept"
    assert fit in kept, "a dense chromatic fit curve through scatter must be recovered, not dropped as a connector"
    assert not any("connector" in r for r in reasons)


def test_multitrack_connector_kept():
    # Two marker trajectories in one colour (markers ~= 2x the line's vertices):
    # the line is a DISTINCT series, not a 1:1 connector -> keep (multitrack guard).
    line = _series(None, [(10, 10), (20, 25), (30, 15), (40, 35)])  # 4 verts
    marks = _series("o", [(10, 10), (20, 25), (30, 15), (40, 35),     # on the line
                          (10, 60), (20, 62), (30, 58), (40, 64)])    # a 2nd track
    kept, _ = drop_spurious_lines([marks, line])
    assert line in kept, "multitrack line (markers ~2x vertices) must be kept"


def test_is_decoration_line_for_audit():
    # used by residual_audit to score refiner-dropped lines as explained.
    markers = [(10, 10), (20, 25), (30, 15), (40, 35)]
    connector = [(10, 10), (20, 25), (30, 15), (40, 35)]
    straight = [(5 + i, 5 + 2 * i) for i in range(60)]
    data_curve = [(10 + i, 10 + ((i - 30) ** 2) * 0.02) for i in range(60)]
    assert is_decoration_line(connector, markers, diag=60)      # connector
    assert is_decoration_line(straight, markers, diag=60)        # straight fit
    assert not is_decoration_line(data_curve, markers, diag=60)  # real curve
    assert not is_decoration_line(straight, [], diag=60)         # no markers -> keep


def test_pure_line_chart_untouched():
    # No marker series at all -> never strip the line.
    line = _series(None, [(i, i) for i in range(50)])  # even a straight line
    kept, reasons = drop_spurious_lines([line])
    assert kept == [line] and reasons == []


# ===========================================================================
# Residual step-2 refiners: refine_dropped_curve / region_overcapture /
# decoration. Synthetic fixtures exercise the PROMOTE and REJECT/dedup paths.
# ===========================================================================

# Plot box: pixel x in [0, 100], y in [0, 100] (top-left origin). Identity-ish
# calibration so a vertex at px maps to data ~= px.
def _axes():
    cal = {"scale": "linear", "a": 1.0, "b": 0.0, "r2": 1.0}
    x = Axis(scale="linear", pixel_range=(0.0, 100.0),
             data_range=(0.0, 100.0), calibration=cal)
    y = Axis(scale="linear", pixel_range=(0.0, 100.0),
             data_range=(0.0, 100.0), calibration=cal)
    return x, y


def _region():
    return Region(bbox=(0.0, 0.0, 100.0, 100.0))


def _path(pix, stroke=(0.2, 0.4, 0.9)):
    xs = [x for x, _ in pix]
    ys = [y for _, y in pix]
    return Path(points=pix, stroke=stroke, fill=None, width=1.0,
                dashes=None, closed=False,
                bbox=(min(xs), min(ys), max(xs), max(ys)))


def test_dropped_curve_promoted():
    # A long wavy residual polyline, inside the plot box, NOT overlapping an
    # existing series, calibrating in range -> PROMOTE as a new series.
    import math
    existing = [_series("o", [(5, 90), (15, 88), (25, 85)])]  # unrelated scatter
    curve = [(10 + i, 50 + 20 * math.sin(i / 8.0)) for i in range(40)]
    out, reasons = refine_dropped_curve(
        existing, [_path(curve)], _region(), _axes())
    assert len(out) == len(existing) + 1, "genuine dropped curve must be promoted"
    assert any("promoted" in r for r in reasons)
    assert out[-1].marker is None and len(out[-1].points) == 40


def test_dropped_curve_dedup_rejected():
    # Residual path duplicates an existing series (same geometry) -> REJECT.
    import math
    pts = [(10 + i, 50 + 20 * math.sin(i / 8.0)) for i in range(40)]
    existing = [Series(label=None, marker=None, color=(0.2, 0.4, 0.9),
                       points=[{"x": x, "y": y, "x_px": x, "y_px": y}
                               for x, y in pts])]
    out, reasons = refine_dropped_curve(
        existing, [_path(pts)], _region(), _axes())
    assert out == existing and reasons == [], "duplicate residual must not be re-added"


def test_overcapture_rejected():
    # A coherent residual block that lies mostly OUTSIDE the plot box (an
    # adjacent subplot bled in) must NOT be promoted.
    import math
    far = [(200 + i, 250 + 20 * math.sin(i / 8.0)) for i in range(40)]  # off-box
    out, reasons = refine_dropped_curve(
        [], [_path(far)], _region(), _axes())
    assert out == [] and reasons == [], "adjacent-subplot bleed must be rejected"
    # the guard itself:
    plot_box = (0.0, 0.0, 100.0, 100.0)
    assert refine_region_overcapture(far, plot_box) is True
    inside = [(10 + i, 40 + i) for i in range(40)]
    assert refine_region_overcapture(inside, plot_box) is False


def test_decoration_not_promoted():
    # Residual that is a connector through markers / straight fit must NOT be
    # promoted by refine_dropped_curve (refine_decoration guard).
    markers = [(10, 10), (40, 40), (70, 12), (90, 50)]
    marks = _series("o", markers)
    straight = [(5 + i * 1.4, 5 + i * 1.4) for i in range(45)]  # R^2=1 fit
    out, reasons = refine_dropped_curve(
        [marks], [_path(straight)], _region(), _axes())
    assert len(out) == 1 and reasons == [], "straight fit residual must not be promoted"
    # the guard itself:
    marker_px = markers
    assert refine_decoration(straight, marker_px, diag=140.0) is True
    wavy = [(10 + i, 50 + ((i - 20) ** 2) * 0.03) for i in range(40)]
    assert refine_decoration(wavy, marker_px, diag=140.0) is False
    assert refine_decoration(straight, [], diag=140.0) is False  # no markers -> keep


def test_dropped_curve_out_of_range_rejected():
    # A short residual or one that calibrates out of range -> REJECT.
    # (calibration slack is small; place the path so its data lands far below 0.)
    cal = {"scale": "linear", "a": 1.0, "b": 0.0, "r2": 1.0}
    x = Axis(scale="linear", pixel_range=(40.0, 60.0),
             data_range=(40.0, 60.0), calibration=cal)
    y = Axis(scale="linear", pixel_range=(40.0, 60.0),
             data_range=(40.0, 60.0), calibration=cal)
    import math
    curve = [(10 + i, 50 + 20 * math.sin(i / 8.0)) for i in range(40)]
    out, reasons = refine_dropped_curve([], [_path(curve)], _region(), (x, y))
    assert out == [] and reasons == [], "out-of-range residual must not be promoted"


# --- safety-net guards: band-colour / light-grey / vertical (missed-curve track) ---
from pdf_chart2table.primitives import round_color as _rc  # noqa: E402


def test_band_colour_curve_not_promoted():
    # A curve whose colour matches a real shaded-band colour is the band's edge
    # outline, not a data series -> never promote (even if otherwise valid).
    import math
    curve = [(10 + i, 50 + 20 * math.sin(i / 8.0)) for i in range(40)]
    p = _path(curve, stroke=(0.2, 0.4, 0.9))
    out, reasons = refine_dropped_curve(
        [], [p], _region(), _axes(), band_colors=frozenset({_rc((0.2, 0.4, 0.9))}))
    assert out == [] and reasons == []


def test_light_grey_curve_not_promoted():
    # A light-grey horizontal path is a guide / reference / band edge, not data.
    import math
    curve = [(10 + i, 50 + 8 * math.sin(i / 8.0)) for i in range(40)]
    p = _path(curve, stroke=(0.8, 0.8, 0.8))
    out, _ = refine_dropped_curve([], [p], _region(), _axes())
    assert out == []


def test_vertical_guide_not_promoted():
    # A near-constant-x path is a vertical reference/threshold line, not y=f(x).
    vert = [(50.0, 10.0 + i) for i in range(40)]
    p = _path(vert, stroke=(0.9, 0.2, 0.2))
    out, _ = refine_dropped_curve([], [p], _region(), _axes())
    assert out == []


def test_black_curve_still_promoted():
    # Black is a common DATA colour: a valid black curve is still recovered.
    import math
    curve = [(10 + i, 50 + 20 * math.sin(i / 8.0)) for i in range(40)]
    p = _path(curve, stroke=(0.0, 0.0, 0.0))
    out, _ = refine_dropped_curve([], [p], _region(), _axes())
    assert len(out) == 1
