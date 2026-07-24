"""Tests for the series role tagging (data / fit / uncertain).

Downstream dataset builders drop every ``marker=None`` series because the JSON
gave no way to tell a marker-less DATA curve (e.g. a loss curve) from a fit /
guide line. The fix: ``drop_spurious_lines`` tags the lines it keeps on
marker-present charts (labels only -- keep/drop behaviour unchanged) and
``classify_line_roles`` tags everything still untagged (pure-line charts,
promoted curves) from marker-independent geometry, never dropping anything.
``io_store._series_record`` serializes ``role`` and ``dashes``.
"""
from __future__ import annotations

import math

from pdf_chart2table.model import Series
from pdf_chart2table.refiners import classify_line_roles, drop_spurious_lines


PLOT_BOX = (0.0, 0.0, 100.0, 100.0)


def _series(marker, pix, color=(0, 0, 0), dashes=None, role=None):
    return Series(label=None, marker=marker, color=color, dashes=dashes,
                  role=role,
                  points=[{"x": x, "y": y, "x_px": x, "y_px": y} for x, y in pix])


def _curve(n=60, color=(0, 0, 1.0), dashes=None):
    """A clearly non-straight (sine) polyline spanning the plot box."""
    return _series(None, [(5 + 90 * i / n, 50 + 30 * math.sin(6.28 * i / n))
                          for i in range(n)], color=color, dashes=dashes)


# ---------------------------------------------------------------- pure-line

def test_pure_line_curved_tagged_data():
    s = _curve()
    classify_line_roles([s], PLOT_BOX)
    assert s.role == "data"


def test_pure_line_dashed_straight_tagged_fit():
    # A dashed straight full-span line on a pure line chart is a fit/guide.
    curve = _curve()
    fit = _series(None, [(5 + i, 5 + 1.5 * i) for i in range(60)],
                  color=(1.0, 0.0, 0.0), dashes="[3 3] 0")
    classify_line_roles([curve, fit], PLOT_BOX)
    assert curve.role == "data"
    assert fit.role == "fit"


def test_pure_line_solid_straight_neutral_with_curved_companion_tagged_fit():
    # Solid straight + neutral grey + the chart's data lives in a curved line
    # -> corroborated guide line.
    curve = _curve()
    guide = _series(None, [(5 + i, 5 + 1.5 * i) for i in range(60)],
                    color=(0.5, 0.5, 0.5))
    classify_line_roles([curve, guide], PLOT_BOX)
    assert curve.role == "data"
    assert guide.role == "fit"


def test_pure_line_lone_solid_straight_tagged_uncertain():
    # A lone solid straight line may be genuine linear data (e.g. an I-V
    # curve) -- flag, don't guess.
    line = _series(None, [(5 + i, 5 + 1.5 * i) for i in range(60)])
    classify_line_roles([line], PLOT_BOX)
    assert line.role == "uncertain"


def test_pure_line_chromatic_solid_straight_tagged_uncertain():
    # Solid straight in a chromatic colour lacks the neutral-guide
    # corroboration even next to a curved companion -> uncertain.
    curve = _curve()
    line = _series(None, [(5 + i, 5 + 1.5 * i) for i in range(60)],
                   color=(1.0, 0.0, 0.0))
    classify_line_roles([curve, line], PLOT_BOX)
    assert line.role == "uncertain"


def test_pure_line_short_straight_segment_tagged_data():
    # Straight but SHORT (below _STRAIGHT_MIN_SPAN of the plot diagonal):
    # not a full-span guide -> data.
    seg = _series(None, [(10 + i * 0.2, 10 + i * 0.3) for i in range(30)])
    classify_line_roles([seg], PLOT_BOX)
    assert seg.role == "data"


def test_tiny_line_tagged_uncertain_and_marker_tagged_data():
    stub = _series(None, [(10, 10), (12, 12)])
    marks = _series("o", [(10, 10), (20, 25), (30, 15)])
    classify_line_roles([stub, marks], PLOT_BOX)
    assert stub.role == "uncertain"
    assert marks.role == "data"


def test_classify_never_drops_or_retags():
    # Tag-only contract: series list unchanged; already-tagged roles preserved.
    pre = _series(None, [(5 + i, 5 + 1.5 * i) for i in range(60)], role="fit")
    curve = _curve()
    series = [pre, curve]
    classify_line_roles(series, PLOT_BOX)
    assert series == [pre, curve]
    assert pre.role == "fit"


# ---------------------------------------------------------------- marker-present

def test_marker_chart_kept_lines_tagged_and_drops_unchanged():
    # Same scenario as test_chromatic_straight_fit_line_kept + a genuine curve:
    # keep/drop identical to before, kept lines now carry roles.
    marks = _series("s", [(10, 12), (25, 40), (40, 8), (55, 33)])
    fit = _series(None, [(5 + i, 5 + 2 * i) for i in range(60)],
                  color=(1.0, 0.0, 0.0))
    curve = _curve(color=(0.0, 0.6, 0.0))
    kept, reasons = drop_spurious_lines([marks, fit, curve])
    assert kept == [marks, fit, curve] and not reasons   # behaviour unchanged
    assert fit.role == "fit"
    assert curve.role == "data"


def test_marker_chart_dashed_same_colour_fit_tagged_fit():
    marks = _series("o", [(10, 12), (25, 40), (40, 8), (55, 33)])
    fit = _series(None, [(5 + i, 5 + 2 * i) for i in range(60)],
                  dashes="[2 2] 0")
    kept, _ = drop_spurious_lines([marks, fit])
    assert fit in kept
    assert fit.role == "fit"


def test_marker_chart_connector_still_dropped():
    marks = _series("o", [(10, 10), (20, 25), (30, 15), (40, 35)])
    connector = _series(None, [(10, 10), (20, 25), (30, 15), (40, 35)])
    kept, reasons = drop_spurious_lines([marks, connector])
    assert connector not in kept
    assert any("connector" in r for r in reasons)


def test_marker_chart_curved_chromatic_fit_tagged_fit():
    # A dense saturated distinct-colour CURVE tracing the markers survives the
    # connector drop as a chromatic fit -- and must be tagged "fit" even
    # though it fails the straight test.
    mark_pts = [(10 + 10 * i, 40 + 12 * math.sin(i)) for i in range(6)]
    marks = _series("o", mark_pts, color=(0, 0, 1.0))
    # >= _FIT_DENSITY_RATIO * len(marker_pts) vertices, all within 3px of marks.
    fit_pts = [(x + 0.1 * (j % 3), y) for x, y in mark_pts for j in range(4)]
    fit = _series(None, fit_pts, color=(1.0, 0.0, 0.0))
    kept, _ = drop_spurious_lines([marks, fit])
    assert fit in kept
    assert fit.role == "fit"


# ---------------------------------------------------------------- serialization

def test_series_record_emits_role_and_dashes():
    from pdf_chart2table.io_store import _series_record

    s = _series(None, [(1, 2)], dashes="[3 3] 0", role="fit")
    (rec,) = _series_record([s])
    assert rec["role"] == "fit"
    assert rec["dashes"] == "[3 3] 0"
    assert rec["points"][0]["x"] == 1
