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
    _compose_runs, _draw_residual, _effective_scale, _faithful_tick_label,
    _group_color, _group_spans, _is_italic, _join_group, _label_match,
    _marker_shape, _math_italic, _norm, _plain_num, _span_color,
    _threads_markers, _ticks_in_range, _use_axis_multiplier)

from pdf_chart2table.model import Path  # noqa: E402
from pdf_chart2table.style import _is_symbol_font, _label_runs  # noqa: E402


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


# --- symbol fonts carry the italic flag but are NOT italic text ----------------
def test_symbol_fonts_excluded_from_italic_vote():
    # CMSY (≪/→), CMEX (big ops), AMS msam/msbm, rsfs, dingbats are symbol fonts.
    assert _is_symbol_font({"font": "CMSY10"})
    assert _is_symbol_font({"font": "ABCDEF+CMEX10"})
    assert _is_symbol_font({"font": "MSBM10"})
    # math-ITALIC (CMMI), text-italic (CMTI) and roman (CMR) are NOT symbol fonts.
    assert not _is_symbol_font({"font": "CMMI10"})
    assert not _is_symbol_font({"font": "CMTI10"})
    assert not _is_symbol_font({"font": "CMR10"})


def _tspan(text, x0, italic, font="CMR10", size=10.0):
    """Horizontal text span carrying the italic flag, for _label_runs tests."""
    w = max(1.0, 0.55 * size * len(text))
    return {"text": text, "size": size, "bbox": (x0, 0.0, x0 + w, size),
            "dir": (1.0, 0.0), "font": font, "flags": 2 if italic else 0}


# --- per-token italic runs: a math var + roman units render token by token -----
def test_label_runs_splits_mixed_italic_and_roman():
    # 'τ (s)': italic math variable then a roman unit -> two runs.
    spans = [_tspan("τ", 0.0, True, font="CMMI10"),
             _tspan("(s)", 30.0, False, font="CMR10")]
    runs = _label_runs(spans)
    assert runs == [["τ", True], [" (s)", False]]


def test_label_runs_none_when_uniform():
    # all-italic (or all-roman) -> the whole-label boolean suffices, no runs.
    allit = [_tspan("a", 0.0, True, font="CMMI10"),
             _tspan("b", 10.0, True, font="CMMI10")]
    assert _label_runs(allit) is None
    allrom = [_tspan("R", 0.0, False), _tspan("e", 6.0, False)]
    assert _label_runs(allrom) is None


def test_label_runs_none_when_scripted():
    # a group already carrying sub/superscript mathtext keeps that path (no runs).
    spans = [_tspan("E", 0.0, True, font="CMMI10"),
             _tspan("g$_{0}$", 8.0, False)]  # '$' marks existing script markup
    assert _label_runs(spans) is None


# --- renderer composition of runs into a mathtext string -----------------------
def test_compose_runs_builds_mixed_mathtext():
    out = _compose_runs([["τ", True], [" (s)", False]])
    assert out == r"$\tau$ (s)"


def test_math_italic_keeps_latin_in_math_and_maps_unicode():
    assert _math_italic("E") == "$E$"          # latin -> math italic
    assert _math_italic("ε") == r"$\epsilon$"  # greek -> math command


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


def test_tick_label_uses_faithful_original_string():
    # integer tick labelled "1" must stay "1", not become matplotlib's "1.0"
    assert _faithful_tick_label(1.0, "1") == "1"
    assert _faithful_tick_label(1.0, "1.0") == "1.0"
    # the original 500 vs 5x10^2 choice is preserved when it matches the value
    assert _faithful_tick_label(500.0, "500") == "500"
    assert _faithful_tick_label(500.0, "5x10^2") == "5x10^2"
    assert _faithful_tick_label(500.0, "5×10²".replace("²", "2")) is not None  # tolerant parse


def test_tick_label_rejects_mismatched_or_mangled():
    assert _faithful_tick_label(500.0, "5") is None      # label != value
    assert _faithful_tick_label(1.0, None) is None        # no label
    assert _faithful_tick_label(1.0, "C_Min") is None     # mangled (non-numeric)


def test_plain_num_drops_trailing_zero_and_avoids_scientific():
    assert _plain_num(1.0) == "1"
    assert _plain_num(500.0) == "500"
    assert _plain_num(0.5) == "0.5"


def test_axis_multiplier_only_for_extreme_magnitudes():
    # ×10^-4 axis (2503): tiny values -> factored multiplier header
    assert _use_axis_multiplier([0.0, 5e-5, 1e-4, 1.5e-4])
    # large axis -> factored too
    assert _use_axis_multiplier([0.0, 2e4, 5e4])
    # normal magnitudes -> plain (1.0->1, 500 stay literal, no factoring)
    assert not _use_axis_multiplier([0.0, 0.5, 1.0])
    assert not _use_axis_multiplier([0.0, 100.0, 500.0])
    assert not _use_axis_multiplier([])


# --- span grouping (multi-token annotations merged into one coherent label) ---

def _span(text, x0, y0, size=8.0, color=(0.0, 0.0, 0.0), dir=(1.0, 0.0)):
    """A synthetic horizontal text span: width ~ 0.55*size per char, height=size."""
    w = max(1.0, 0.55 * size * len(text))
    return {"text": text, "size": size, "color": color, "dir": dir,
            "bbox": (x0, y0, x0 + w, y0 + size)}


def test_span_color_decodes_packed_int_and_passthrough_tuple():
    # magenta packed sRGB int 0xFF00FF -> (1, 0, 1)
    assert _span_color(0xFF00FF) == (1.0, 0.0, 1.0)
    assert _span_color(0x000000) == (0.0, 0.0, 0.0)
    # already a float tuple -> clamped passthrough
    assert _span_color((0.0, 0.5, 1.0)) == (0.0, 0.5, 1.0)
    assert _span_color(None) is None


def test_adjacent_same_baseline_spans_group_with_preserved_color():
    # "Slew rate out" laid out left-to-right on one baseline, all magenta.
    mag = (1.0, 0.0, 1.0)
    spans = [_span("Slew", 100, 50, color=mag),
             _span("rate", 124, 50, color=mag),
             _span("out", 148, 50, color=mag)]
    groups = _group_spans(spans, base=8.0)
    assert len(groups) == 1
    assert _join_group(groups[0]) == "Slew rate out"
    assert _group_color(groups[0]) == mag


def test_far_apart_spans_on_same_baseline_do_not_merge():
    # Same row but a big horizontal gap (separate labels) -> two groups.
    spans = [_span("left", 100, 50),
             _span("right", 400, 50)]   # >> 1.2*size gap
    groups = _group_spans(spans, base=8.0)
    assert len(groups) == 2


def test_different_baseline_spans_do_not_merge():
    # Two annotations stacked on different lines (cyan above magenta) stay apart.
    a = _span("Slew rate in", 100, 50, color=(0.0, 1.0, 1.0))
    b = _span("Slew rate out", 100, 90, color=(1.0, 0.0, 1.0))  # 40pt lower
    groups = _group_spans([a, b], base=8.0)
    assert len(groups) == 2
    texts = {_join_group(g) for g in groups}
    assert texts == {"Slew rate in", "Slew rate out"}


def test_superscript_absorbed_into_baseline_group():
    # exponent sits slightly above baseline at a smaller size -> one group.
    base_span = _span("P", 100, 50, size=8.0)
    sup = _span("1.41", 106, 47, size=6.0)  # raised, smaller, tight gap
    groups = _group_spans([base_span, sup], base=8.0)
    assert len(groups) == 1
    assert _join_group(groups[0]).startswith("P")


def _title_corroborated(title, spans, region_bbox, base=8.0):
    """Replicate the render's fake-title guard predicate over grouped spans."""
    x0, y0, x1, y1 = region_bbox
    w, h = (x1 - x0) or 1.0, (y1 - y0) or 1.0
    tkey = _norm(title)
    if not tkey:
        return False
    for grp in _group_spans(spans, base):
        gkey = _norm(_join_group(grp))
        if not gkey or not (tkey == gkey or tkey in gkey):
            continue
        gcx = (min(s["bbox"][0] for s in grp) + max(s["bbox"][2] for s in grp)) / 2
        gtop = min(s["bbox"][1] for s in grp)
        if 0.15 <= (gcx - x0) / w <= 0.85 and gtop <= y0 + 0.10 * h:
            return True
    return False


def test_interior_fragment_not_promoted_to_title():
    region = (0.0, 100.0, 200.0, 300.0)  # plot box y in [100, 300]
    # an in-plot caption fragment sitting in the MIDDLE of the plot
    interior = [_span("Slew", 60, 200), _span("rate", 84, 200),
                _span("MHz", 108, 200)]
    # the extractor mis-promoted a re-ordered fragment as the title
    assert not _title_corroborated("MHz Slew rate", interior, region)
    # even the contiguous interior string is NOT a title (it is not near the top)
    assert not _title_corroborated("Slew rate MHz", interior, region)


def test_real_top_center_title_is_corroborated():
    region = (0.0, 100.0, 200.0, 300.0)
    # one coherent line centered above the plot box -> a legitimate title
    top = [_span("Sample", 70, 90), _span("A", 100, 90)]
    assert _title_corroborated("Sample A", top, region)


def test_residual_empty_shows_fully_explained_not_ghost():
    # A near-empty residual must NOT be drawn over a faded full-chart backdrop
    # (that looked like "the whole graph, faint"). Empty -> no image, clear text.
    import types
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from PIL import Image
    arr = Image.new("RGB", (60, 40))      # PIL image, as the renderer passes
    clip = types.SimpleNamespace(x0=0.0, y0=0.0)
    fig, ax = plt.subplots()
    _draw_residual(ax, arr, clip, 72.0, [])
    assert len(ax.images) == 0           # no faded backdrop
    assert "fully explained" in ax.get_title()
    plt.close(fig)


def test_residual_nonempty_draws_ink_over_light_backdrop():
    import types
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from PIL import Image
    arr = Image.new("RGB", (60, 40))      # PIL image, as the renderer passes
    clip = types.SimpleNamespace(x0=0.0, y0=0.0)
    resid = [([(1.0, 1.0), (2.0, 2.0)], True)]  # one missed curve
    fig, ax = plt.subplots()
    _draw_residual(ax, arr, clip, 72.0, resid)
    assert len(ax.images) == 1           # light context backdrop present
    assert ax.images[0].get_alpha() < 0.2
    assert len(ax.lines) == 1            # the residual stroke drawn on top
    plt.close(fig)
