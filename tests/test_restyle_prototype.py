"""Regression tests for the restyle-reconstruction helpers (scripts/).

Each test reproduces a specific bug found by inspecting reconstructions:
* 2010.12950: a LINEAR axis (700..860) was tagged 'log' by the extractor, so it
  rendered '7x10^2' -- _effective_scale must demote a sub-decade log axis.
* 2002.04278: tick labels like '10'/'x' matched the legend entry 'X (x100)'
  (norm 'xx100' contains '10') -- _label_match needs a length floor.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import math  # noqa: E402

from render_restyle_prototype import (  # noqa: E402
    _effective_scale, _is_italic, _label_match, _marker_shape, _norm,
    _threads_markers, _ticks_in_range)

from pdf_chart2table.model import Path  # noqa: E402


def _path(points, fill=None, stroke=(0.0, 0.0, 0.0)):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    bbox = (min(xs), min(ys), max(xs), max(ys))
    return Path(points=points, stroke=stroke, fill=fill, width=1.0,
                dashes=None, closed=True, bbox=bbox)


def _circle_points(n=33, r=5.0, cx=0.0, cy=0.0):
    return [(cx + r * math.cos(2 * math.pi * k / n),
             cy + r * math.sin(2 * math.pi * k / n)) for k in range(n)]


def _doubled_noisy_circle(n=33, r=5.0, cx=0.0, cy=0.0, noise=0.06):
    # Two overlapping loops with small per-vertex radial jitter -> high cv (~0.33)
    # but no regular spikes; reproduces 2102.11637's 66-vertex filled circles.
    import random
    rng = random.Random(1)
    pts = []
    for _ in range(2):
        for k in range(n):
            a = 2 * math.pi * k / n
            rr = r * (1.0 + rng.uniform(-noise, noise))
            pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return pts


def _star_points(npoints=5, r_out=5.0, r_in=2.0, cx=0.0, cy=0.0, edge_samples=3):
    verts = []
    for k in range(npoints * 2):
        ang = math.pi * k / npoints - math.pi / 2
        rad = r_out if k % 2 == 0 else r_in
        verts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
    pts = []
    for i in range(len(verts)):
        a, b = verts[i], verts[(i + 1) % len(verts)]
        for s in range(edge_samples):
            t = s / edge_samples
            pts.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return pts


def test_log_axis_under_one_decade_demoted_to_linear():
    ax = {"scale": "log",
          "ticks": [{"value": v} for v in (700, 720, 740, 760, 780, 800)]}
    assert _effective_scale(ax) == "linear"


def test_genuine_log_axis_kept():
    ax = {"scale": "log", "ticks": [{"value": v} for v in (0.1, 1, 10, 100, 1000)]}
    assert _effective_scale(ax) == "log"


def test_linear_axis_unchanged():
    ax = {"scale": "linear", "ticks": [{"value": v} for v in (0, 10, 20)]}
    assert _effective_scale(ax) == "linear"


def test_short_tick_label_does_not_match_long_legend_entry():
    label = _norm("X (x100)")          # -> 'xx100'
    assert not _label_match(_norm("10"), label)
    assert not _label_match(_norm("x"), label)
    assert not _label_match(_norm("0"), label)


def test_real_legend_chunks_match():
    label = _norm("X (x100)")
    assert _label_match(_norm("(x100)"), label)      # substantial chunk
    assert _label_match(_norm("Sideband"), _norm("Sideband"))


def test_italic_detected_from_flag_and_font_name():
    assert _is_italic({"flags": 2, "font": "Times"})            # italic flag bit
    assert _is_italic({"flags": 0, "font": "NimbusRomNo9L-Ital"})
    assert _is_italic({"flags": 0, "font": "CMMI10-Oblique"})
    assert not _is_italic({"flags": 0, "font": "Helvetica"})


# --- Bug A: filled circle drawn as a noisy/doubled loop must NOT be a star -----
def test_noisy_doubled_circle_is_disk_not_star():
    # 2102.11637_p6c5: half the data markers are filled circles encoded as a
    # 66-vertex doubled loop with cv~0.33; raw cv wrongly flagged them '*'.
    p = _path(_doubled_noisy_circle(), fill=(0.0, 0.0, 0.0))
    assert _marker_shape(p) == "o"


def test_smooth_filled_circle_is_disk():
    p = _path(_circle_points(), fill=(0.0, 0.0, 0.0))
    assert _marker_shape(p) == "o"


def test_real_star_still_classified_as_star():
    # A genuine star (alternating long/short radii) must stay '*'.
    for npoints in (5, 6):
        p = _path(_star_points(npoints=npoints))
        assert _marker_shape(p) == "*", npoints


# --- Bug B: connect a marker series only when a path THREADS the markers --------
def test_connect_true_when_line_threads_markers():
    # A line+marker series: a connector polyline whose vertices ARE the markers.
    pts = [(float(x), 10.0 + 0.5 * x) for x in range(0, 60, 4)]  # 15 points
    connector = _path(pts)  # passes exactly through every marker
    assert _threads_markers([connector], pts, tol=4.0)


def test_connect_false_when_only_path_misses_markers():
    # Pure scatter: the only same-colour long path is a fit line FAR from the
    # markers (e.g. 2205/2410 -- a power-law fit / dropped connector elsewhere).
    pts = [(float(x), 50.0 + 0.3 * x) for x in range(0, 60, 4)]
    fit = _path([(0.0, 0.0), (60.0, 5.0)])  # a straight line well below the data
    assert not _threads_markers([fit], pts, tol=4.0)


# --- Bug C: a single mis-extracted tick must not collapse the view -------------
def test_outlier_tick_dropped_from_range():
    # 2204.11743_p19c4: ticks 0.03..0.09 plus a spurious '680.18'. Forcing 680.18
    # as a y-tick expanded the view to [0.1, 680] and flattened the curve.
    kept = _ticks_in_range([0.03, 0.05, 0.07, 0.09, 680.18],
                           data_range=[0.1, 0.03])
    assert 680.18 not in kept
    assert kept == [0.03, 0.05, 0.07, 0.09]


def test_legitimate_edge_ticks_kept():
    # Ticks within (and at) the calibrated range, plus one just past an edge by
    # less than the span, are ALL legitimate and must survive.
    kept = _ticks_in_range([0, 10, 20, 30], data_range=[0, 25])
    assert kept == [0, 10, 20, 30]


def test_range_filter_noop_without_range():
    vals = [0.03, 0.05, 680.18]
    assert _ticks_in_range(vals, None) == vals
    assert _ticks_in_range(vals, [None, 1.0]) == vals


def test_range_filter_keeps_all_when_too_few_survive():
    # If filtering would leave <2 ticks, keep the original set (don't strip the
    # axis bare on an unusual but possibly-correct calibration).
    kept = _ticks_in_range([100.0, 200.0], data_range=[0.0, 1.0])
    assert kept == [100.0, 200.0]
