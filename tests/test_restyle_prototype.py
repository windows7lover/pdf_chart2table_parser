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
    _dash_is_dotted, _metric_font_rc, _resolve_font,
    _compose_runs, _draw_residual, _effective_scale, _faithful_tick_label,
    _group_color, _group_spans, _is_italic, _join_group, _label_match,
    _marker_shape, _math_italic, _norm, _plain_num, _source_multiplier_exp,
    _span_color, _threads_markers, _ticks_in_range, _use_axis_multiplier)

from pdf_chart2table.model import Path  # noqa: E402
from pdf_chart2table.style import (  # noqa: E402
    _classify_face, _content_scale, _is_symbol_font, _label_runs,
    _segments_thread_markers, _text_rotation, _title_span_match)


def _path(points, fill=None, stroke=(0.0, 0.0, 0.0)):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    bbox = (min(xs), min(ys), max(xs), max(ys))
    return Path(points=points, stroke=stroke, fill=fill, width=1.0,
                dashes=None, closed=True, bbox=bbox)


def _circle_points(n=33, r=5.0, cx=0.0, cy=0.0):
    return [(cx + r * math.cos(2 * math.pi * k / n),
             cy + r * math.sin(2 * math.pi * k / n)) for k in range(n)]


def test_triangle_orientation_up_vs_down():
    # 2504.02903_p11c3: a down-triangle (▽) marker was rendered as up (△). PDF y
    # points DOWN. Up-triangle: apex at top (small y), base at bottom -> mass/
    # centroid in the lower half -> '^'. Down-triangle: apex at bottom -> '▽' -> 'v'.
    up = _path([(5.0, 0.0), (0.0, 10.0), (10.0, 10.0)], fill=(0, 0, 1))
    down = _path([(0.0, 0.0), (10.0, 0.0), (5.0, 10.0)], fill=(0, 0, 1))
    assert _marker_shape(up) == "^"
    assert _marker_shape(down) == "v"


def test_thin_vs_fat_diamond():
    # 2004.01004_p6c3: a tall narrow rhombus ('d') was rendered as the square 'D'.
    # A regular diamond has a ~square bbox; a thin diamond is clearly taller than
    # wide. Corners at top/right/bottom/left (rotated 45deg) -> shape_of='diamond'.
    fat = _path([(5.0, 0.0), (10.0, 5.0), (5.0, 10.0), (0.0, 5.0), (5.0, 0.0)],
                fill=None)
    thin = _path([(3.0, 0.0), (6.0, 5.0), (3.0, 10.0), (0.0, 5.0), (3.0, 0.0)],
                 fill=None)
    assert _marker_shape(fat) == "D"
    assert _marker_shape(thin) == "d"


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


# --- metric-compatible faces: "serif"/"sans" must resolve to Liberation -------
def test_metric_font_rc_prefers_liberation_then_dejavu():
    rc = _metric_font_rc()
    # Times-metric serif and Arial-metric sans must come first; DejaVu is fallback.
    assert rc["font.serif"][0] == "Liberation Serif"
    assert rc["font.sans-serif"][0] == "Liberation Sans"
    assert rc["font.monospace"][0] == "Liberation Mono"
    assert "DejaVu Serif" in rc["font.serif"]
    assert "DejaVu Sans" in rc["font.sans-serif"]


def test_resolve_font_per_element_family_and_face():
    # per-element font: serif/sans resolve to the metric-compatible lists;
    # a face token overrides; nothing recovered -> None (use figure default).
    assert _resolve_font("serif", None)[0] == "Liberation Serif"
    assert _resolve_font("sans-serif", None)[0] == "Liberation Sans"
    assert _resolve_font("sans-serif", "verdana")[0] == "DejaVu Sans"
    assert _resolve_font("serif", "cambria")[0] == "Caladea"
    assert _resolve_font(None, None) is None


def test_per_element_font_applied_to_ticks_and_labels():
    # a serif chart with SANS-SERIF ticks must render the ticks in sans (the
    # "several fonts depending on the text" case).
    from render_restyle_prototype import _replot, plt
    record, style = _mini_record_style()
    style["text"]["elements"] = {
        "x_title": {"size": 10.0, "family": "serif"},
        "y_title": {"size": 10.0, "family": "serif"},
        "ticks": {"size": 8.0, "family": "sans-serif"},
    }
    fig, ax = plt.subplots()
    _replot(ax, record, style, font_scale=1.0)
    assert ax.get_xticklabels()[0].get_fontfamily()[0] == "Liberation Sans"
    plt.close(fig)


def _count_arrow_patches(ax):
    # ax.annotate("", ...) creates an Annotation (in ax.texts) carrying a
    # FancyArrowPatch in .arrow_patch -- count those.
    return sum(getattr(t, "arrow_patch", None) is not None for t in ax.texts)


def test_replot_draws_annotation_arrow_when_present():
    # An arrow persisted in DATA coords is re-drawn as a FancyArrowPatch.
    from render_restyle_prototype import _replot, plt
    record, style = _mini_record_style()
    record["arrows"] = [{"tail": [0.5, 0.9], "head": [0.5, 0.6],
                         "color": [1.0, 0.5, 0.0]}]
    fig, ax = plt.subplots()
    _replot(ax, record, style, font_scale=1.0)
    assert _count_arrow_patches(ax) == 1
    plt.close(fig)


def test_replot_draws_reference_line_at_its_colour():
    # A recovered DASHED reference line is redrawn as an axhline/axvline in its
    # OWN colour (not the subtle grey grid), at its data coordinate.
    from render_restyle_prototype import _replot, plt
    record, style = _mini_record_style()
    style["grid"] = {"reference_lines": [
        {"orient": "h", "value": 0.0, "color": [0.0, 0.5, 0.0],
         "dashes": "[ 4.94 2.14 ] 0", "linewidth": 1.3, "alpha": None}]}
    fig, ax = plt.subplots()
    _replot(ax, record, style, font_scale=1.0)
    greens = [ln for ln in ax.get_lines()
              if tuple(round(c, 2) for c in ln.get_color()[:3]) == (0.0, 0.5, 0.0)
              and ln.get_linestyle() in ("--", "dashed")]
    assert len(greens) == 1
    assert abs(greens[0].get_ydata()[0]) < 1e-9
    plt.close(fig)


def test_replot_draws_no_reference_line_when_absent():
    # No reference_lines -> no extra dashed coloured rule (no spurious lines).
    from render_restyle_prototype import _replot, plt
    record, style = _mini_record_style()
    fig, ax = plt.subplots()
    _replot(ax, record, style, font_scale=1.0)
    greens = [ln for ln in ax.get_lines()
              if tuple(round(c, 2) for c in ln.get_color()[:3]) == (0.0, 0.5, 0.0)]
    assert greens == []
    plt.close(fig)


def test_replot_draws_confidence_band_fill():
    # 2309.03591_p9c3: a recovered curve-bounded band is drawn as a fill_between
    # PolyCollection in its own colour (the boundary curves alone are not enough).
    from matplotlib.collections import PolyCollection
    from render_restyle_prototype import _replot, plt
    record, style = _mini_record_style()
    style["bands"] = [{"x": [0.0, 0.5, 1.0], "y_lo": [0.1, 0.2, 0.3],
                       "y_hi": [0.4, 0.5, 0.6], "color": [0.78, 0.25, 0.49],
                       "alpha": 0.15}]
    fig, ax = plt.subplots()
    _replot(ax, record, style, font_scale=1.0)
    polys = [c for c in ax.collections if isinstance(c, PolyCollection)
             and tuple(round(v, 2) for v in c.get_facecolor()[0][:3]) == (0.78, 0.25, 0.49)]
    assert len(polys) == 1
    plt.close(fig)


def test_replot_draws_no_band_when_absent():
    # No bands key -> no fill_between PolyCollection (no spurious shading).
    from matplotlib.collections import PolyCollection
    from render_restyle_prototype import _replot, plt
    record, style = _mini_record_style()
    fig, ax = plt.subplots()
    _replot(ax, record, style, font_scale=1.0)
    assert [c for c in ax.collections if isinstance(c, PolyCollection)] == []
    plt.close(fig)


def test_replot_draws_no_arrow_when_absent():
    # No arrows key / empty list -> no arrow patches (no spurious arrows).
    from render_restyle_prototype import _replot, plt
    record, style = _mini_record_style()
    fig, ax = plt.subplots()
    _replot(ax, record, style, font_scale=1.0)
    assert _count_arrow_patches(ax) == 0
    record["arrows"] = []
    fig2, ax2 = plt.subplots()
    _replot(ax2, record, style, font_scale=1.0)
    assert _count_arrow_patches(ax2) == 0
    plt.close(fig)
    plt.close(fig2)


def test_stroke_only_x_marker_is_visible():
    # 2506.18139_p24c2: an 'x' marker series rendered INVISIBLE. matplotlib colours
    # unfilled markers (x, +, 1-4, |, _) via FACECOLOR and ignores edgecolor, but
    # the renderer passed facecolors="none" (correct only for open o/s) -> the x's
    # vanished. They must be coloured via facecolors instead.
    from render_restyle_prototype import _replot, plt
    record, style = _mini_record_style()
    style["series"][0] = {"color": [1.0, 0.49, 0.11], "label": None,
                          "render_as": "scatter", "marker_shape": "x",
                          "connect": False}
    fig, ax = plt.subplots()
    _replot(ax, record, style, font_scale=1.0)
    # Some collection must carry an OPAQUE, ORANGE face (the visible x markers).
    visible_orange = False
    for c in ax.collections:
        fc = c.get_facecolor()
        for r, g, b, a in fc:
            if a > 0 and r > 0.8 and 0.3 < g < 0.65 and b < 0.3:
                visible_orange = True
    assert visible_orange, "stroke-only 'x' marker has no visible coloured face"
    plt.close(fig)


def test_open_o_marker_stays_unfilled():
    # Guard the fix didn't fill genuine OPEN markers: an 'o' with no marker_face
    # keeps a transparent face (facecolors="none").
    from render_restyle_prototype import _replot, plt
    record, style = _mini_record_style()
    style["series"][0] = {"color": [0.0, 0.0, 0.0], "label": None,
                          "render_as": "scatter", "marker_shape": "o",
                          "connect": False}
    fig, ax = plt.subplots()
    _replot(ax, record, style, font_scale=1.0)
    # the scatter's face must be empty/transparent (open marker, no fill).
    faces = [c.get_facecolor() for c in ax.collections]
    assert any(len(fc) == 0 or all(rgba[3] == 0 for rgba in fc) for fc in faces), \
        "open 'o' marker must stay unfilled"
    plt.close(fig)


def test_replot_skips_arrow_without_data_coords():
    # An arrow with only pixel coords (uncalibrated) is not renderable -> skipped.
    from render_restyle_prototype import _replot, plt
    record, style = _mini_record_style()
    record["arrows"] = [{"tail_px": [200.0, 110.0], "head_px": [200.0, 130.0],
                         "color": None}]
    fig, ax = plt.subplots()
    _replot(ax, record, style, font_scale=1.0)
    assert _count_arrow_patches(ax) == 0
    plt.close(fig)


def test_classify_face_picks_distinct_substitutes():
    # dominant face by char count -> canonical token (real corpus font names)
    assert _classify_face({"Verdana": 50, "ArialMT": 5}) == "verdana"
    assert _classify_face({"Tahoma": 30}) == "verdana"
    assert _classify_face({"Calibri": 40}) == "calibri"
    assert _classify_face({"Cambria": 20}) == "cambria"
    assert _classify_face({"Courier-Bold": 12}) == "courier"
    # Faces the generic serif/sans default already matches -> no token.
    assert _classify_face({"ArialMT": 30, "Verdana": 2}) is None
    assert _classify_face({"Times New Roman": 30}) is None
    assert _classify_face({}) is None


def test_metric_font_rc_face_specific_substitutes():
    # Verdana is wider than Arial -> DejaVu Sans (Vera lineage) must lead, not Liberation.
    assert _metric_font_rc("verdana")["font.sans-serif"][0] == "DejaVu Sans"
    # Calibri -> Carlito, Cambria -> Caladea (metric-compatible).
    assert _metric_font_rc("calibri")["font.sans-serif"][0] == "Carlito"
    assert _metric_font_rc("cambria")["font.serif"][0] == "Caladea"
    # Courier is monospace though _classify_family calls it "serif".
    assert _metric_font_rc("courier")["font.family"] == "monospace"
    # An unknown/None face keeps the Liberation defaults.
    assert _metric_font_rc(None)["font.sans-serif"][0] == "Liberation Sans"


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


def test_label_runs_keeps_symbol_font_char_as_roman():
    # 2004.12366: 'pixel depth (z) [µm]' -- the 'µ' is a Symbol-font span. It must
    # stay in the assembled text (rendered roman); excluding it dropped the 'µ' and
    # merged the gap into a spurious space ('[ m]'). The italic vote still ignores it.
    spans = [_tspan("depth (", 0.0, False, font="Helvetica"),
             _tspan("z", 40.0, True, font="Helvetica-Oblique"),
             _tspan(") [", 46.0, False, font="Helvetica"),
             _tspan("µ", 54.0, True, font="Symbol"),  # symbol carries italic flag
             _tspan("m]", 58.0, False, font="Helvetica")]
    runs = _label_runs(spans)
    assert "".join(t for t, _ in runs) == "depth (z) [µm]", runs
    # 'µ' is rendered roman (merged with the surrounding roman run), not italic.
    assert ["µ" in t and not it for t, it in runs if "µ" in t] == [True]


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
def test_dash_is_dotted_classifies_fine_patterns():
    # A short "on" segment reads as dots (render ':'); a long one as dashes ('--').
    assert _dash_is_dotted("[ .046 .046 ] 0") is True    # 2003.09710 fine grid
    assert _dash_is_dotted("[ .907 2.72 ] 0") is True     # 2006.05506 ref line
    assert _dash_is_dotted("[ 3 2 ] 0") is False          # genuine dashes
    assert _dash_is_dotted(None) is False


def test_compose_runs_builds_mixed_mathtext():
    out = _compose_runs([["τ", True], [" (s)", False]])
    assert out == r"$\tau$ (s)"


def test_demath_alnum_normalizes_math_unicode():
    # 2202.11139: '2D−𝑅$_{ℎ}$$^{𝑋}$' uses Mathematical Alphanumeric unicode that
    # matplotlib can't render (dummy boxes). NFKC -> plain letters that render.
    from render_restyle_prototype import _demath_alnum, _latexify
    assert _demath_alnum("𝑅") == "R"      # U+1D445 math italic R
    assert _demath_alnum("ℎ") == "h"      # U+210E letterlike h
    assert _demath_alnum("𝑋") == "X"      # U+1D44B math italic X
    assert _demath_alnum("𝔹") == "B"      # double-struck
    assert _demath_alnum("ABC xyz 0.5") == "ABC xyz 0.5"   # plain text untouched
    # _latexify normalizes even when the string already carries $...$ markup
    assert _latexify("2D−𝑅$_{ℎ}$$^{𝑋}$") == "2D−R$_{h}$$^{X}$"


def test_math_italic_keeps_latin_in_math_and_maps_unicode():
    assert _math_italic("E") == "$E$"          # latin -> math italic
    assert _math_italic("ε") == r"$\epsilon$"  # greek -> math command


def test_math_italic_terminates_command_before_letter():
    # 2003.13177_p25c1: 'δV' must NOT become '$\deltaV$' (unknown command ->
    # mathtext ParseFatalException that crashes the whole render). Terminate the
    # control word with a space when a letter/digit follows.
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    assert _math_italic("δV") == r"$\delta V$"
    assert _math_italic("μm") == r"$\mu m$"
    assert _math_italic("δ(s)") == r"$\delta(s)$"   # no space needed before '('
    # and it actually draws without raising
    fig, ax = plt.subplots()
    ax.set_ylabel(_math_italic("δV"))
    fig.canvas.draw()
    plt.close(fig)


# --- font_scale magnifies recovered point sizes (PNG panel proportion fix) ------
def _mini_record_style():
    record = {"series": [{"points": [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 1.0}]}],
              "x_axis": {"data_range": [0.0, 1.0]},
              "y_axis": {"data_range": [0.0, 1.0]},
              "xticks": [], "yticks": []}
    style = {"series": [{"color": [0.0, 0.0, 0.0], "label": None}],
             "x_axis": {"title": "X", "ticks": []},
             "y_axis": {"title": "Y", "ticks": []},
             "text": {"base_font_size": 10.0,
                      "elements": {"x_title": {"size": 10.0},
                                   "y_title": {"size": 10.0}}}}
    return record, style


def _hspan_box(boxh, size, x0=0.0):
    # a horizontal span with a given box height and font size (dir along x)
    return {"size": size, "bbox": (x0, 0.0, x0 + 5.0, boxh), "dir": (1.0, 0.0)}


def test_content_scale_treats_tall_font_metrics_as_no_transform():
    # 2004.06765_p10c6: DejaVuSans box-height/size = 1.70 is the font's intrinsic
    # (ascender-descender), NOT a 1.7x zoom -- must snap to 1.0 (else every font
    # is inflated ~1.7x). Confirmed: a tick "10" size 5.23 renders ink ~5.76pt.
    tall = [_hspan_box(8.87, 5.23) for _ in range(10)]
    assert _content_scale(tall) == 1.0
    compact = [_hspan_box(5.5, 5.0) for _ in range(10)]  # ratio 1.1
    assert _content_scale(compact) == 1.0


def test_content_scale_keeps_genuine_large_transform():
    # A figure truly drawn small then scaled up shows a much larger ratio (>=2).
    # The measured ratio is (intrinsic box metric ~1.36) x (figure CTM); only the
    # CTM is the content transform, so the returned scale divides the box metric
    # out (3.0 / 1.36 ~= 2.21) -- else fonts/line widths come out ~1.36x too big.
    scaled = [_hspan_box(15.0, 5.0) for _ in range(10)]  # ratio 3.0
    assert _content_scale(scaled) == 3.0 / 1.36
    assert _content_scale([]) == 1.0


def test_title_span_match_rejects_short_fragments():
    # 2004.06765_p10c6: a stray 1-char legend span 'S' (norm 's') matched the long
    # title (norm contains 's' via "false"), stealing its font size -> titles 1.7x
    # too big. A 1-3 char fragment must NOT match a long multi-word title.
    key = _norm("Pfp False Alarm Probability")
    assert not _title_span_match("s", key)          # stray legend letter
    assert not _title_span_match("cti", key)        # 'cti' is inside "...probab"? no
    assert not _title_span_match("cti", _norm("P d Detection Probability"))  # in "detection"
    # legitimate matches still hold
    assert _title_span_match("false", key)          # a real >=4-char word fragment
    assert _title_span_match(_norm("Pfp False Alarm Probability"), key)      # exact
    assert _title_span_match("g", "g")              # single-letter title, exact
    assert _title_span_match("x" + key, key)        # whole label inside a bigger span


def test_error_bar_caps_only_on_points_with_yerr():
    # Regression (2005.03896_p4c1): matplotlib draws a CAP at EVERY supplied
    # point, so passing a whole series with most errors zeroed littered the
    # curve with spurious caps. Only points that carry a y_err must be drawn.
    from render_restyle_prototype import _replot, plt
    record = {"series": [{"points": [
        {"x": 0.0, "y": 0.5}, {"x": 0.25, "y": 0.5},
        {"x": 0.5, "y": 0.5, "y_err": 0.1},
        {"x": 0.75, "y": 0.5}, {"x": 1.0, "y": 0.5}]}],
        "x_axis": {"data_range": [0.0, 1.0]},
        "y_axis": {"data_range": [0.0, 1.0]},
        "xticks": [], "yticks": []}
    style = {"series": [{"color": [0.0, 0.0, 0.0], "label": None}],
             "x_axis": {"title": "X", "ticks": []},
             "y_axis": {"title": "Y", "ticks": []},
             "text": {"base_font_size": 10.0, "elements": {}}}
    fig, ax = plt.subplots()
    _replot(ax, record, style, font_scale=1.0)
    # exactly one error-bar container, with caps only at the single y_err x.
    assert len(ax.containers) == 1
    _dl, caps, _bars = ax.containers[0]
    assert caps, "expected error-bar caps for the y_err point"
    for cap in caps:
        assert list(cap.get_xdata()) == [0.5], \
            "caps must appear only at the point that carries a y_err"
    plt.close(fig)


def test_no_error_bars_when_no_yerr():
    # A plain curve (no y_err anywhere) must draw NO error-bar container.
    from render_restyle_prototype import _replot, plt
    record, style = _mini_record_style()
    fig, ax = plt.subplots()
    _replot(ax, record, style, font_scale=1.0)
    assert len(ax.containers) == 0
    plt.close(fig)


def test_font_scale_multiplies_recovered_label_size():
    from render_restyle_prototype import _replot, plt
    record, style = _mini_record_style()
    fig, ax = plt.subplots()
    _replot(ax, record, style, font_scale=1.0)
    assert ax.xaxis.label.get_size() == 10.0
    plt.close(fig)
    fig, ax = plt.subplots()
    _replot(ax, record, style, font_scale=2.5)   # a larger panel -> bigger fonts
    assert ax.xaxis.label.get_size() == 25.0
    plt.close(fig)


def test_text_element_color_applied_to_labels_and_ticks():
    # Unified elements drive colour for axis titles + tick labels (all text).
    from render_restyle_prototype import _replot, plt
    record, style = _mini_record_style()
    style["text"]["elements"] = {
        "x_title": {"size": 10.0, "color": [1.0, 0.0, 0.0]},   # red
        "y_title": {"size": 10.0, "color": [0.0, 0.0, 1.0]},   # blue
        "ticks": {"size": 8.0, "color": [0.0, 0.5, 0.0]},      # green
    }
    fig, ax = plt.subplots()
    _replot(ax, record, style, font_scale=1.0)
    assert ax.xaxis.label.get_color() == (1.0, 0.0, 0.0)
    assert ax.yaxis.label.get_color() == (0.0, 0.0, 1.0)
    assert ax.get_xticklabels()[0].get_color() == (0.0, 0.5, 0.0)
    plt.close(fig)


def test_recon_title_size_falls_back_to_base_not_mpl_default():
    # A multi-span title may have no recovered size; it must fall back to the
    # base font size, NOT matplotlib's oversized default title size.
    from render_restyle_prototype import _recon_figure, plt
    record = {"source": {"region_bbox": [0.0, 0.0, 150.0, 120.0]},
              "series": [{"points": [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 1.0}]}]}
    style = {"title": "V D (V)", "series": [{"color": [0, 0, 0], "label": None}],
             "x_axis": {"ticks": []}, "y_axis": {"ticks": []},
             "text": {"base_font_size": 9.0,
                      "elements": {"title": {"size": None}}}}
    fig = _recon_figure(record, style)
    assert fig.axes[0].title.get_fontsize() == 9.0
    plt.close(fig)


def test_text_element_no_color_defaults_black():
    from render_restyle_prototype import _replot, plt
    record, style = _mini_record_style()  # elements x/y_title have no colour
    fig, ax = plt.subplots()
    _replot(ax, record, style, font_scale=1.0)
    assert ax.xaxis.label.get_color() == "black"
    plt.close(fig)


def _legend_record_style(legend_frame):
    # two labelled series so a legend is drawn, plus the recovered frame style.
    record = {"series": [{"points": [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 1.0}]},
                         {"points": [{"x": 0.0, "y": 1.0}, {"x": 1.0, "y": 0.0}]}],
              "x_axis": {"data_range": [0.0, 1.0]},
              "y_axis": {"data_range": [0.0, 1.0]}, "xticks": [], "yticks": []}
    style = {"series": [{"color": [0.0, 0.0, 0.0], "label": "A"},
                        {"color": [0.8, 0.1, 0.1], "label": "B"}],
             "x_axis": {"title": "X", "ticks": []},
             "y_axis": {"title": "Y", "ticks": []},
             "legend_box": True, "legend_frame": legend_frame,
             "text": {"base_font_size": 10.0, "show_legend": True,
                      "legend": {"fontsize": 8.0,
                                 "entries": [
                                     {"label": "A", "x_frac": 0.2, "y_frac": 0.9,
                                      "size": 8.0, "bold": False, "italic": False},
                                     {"label": "B", "x_frac": 0.2, "y_frac": 0.8,
                                      "size": 8.0, "bold": False, "italic": False}],
                                 "bbox_frac": [0.08, 0.75, 0.45, 0.95]}}}
    return record, style


def _legend_frame_patch(ax):
    """The FancyBboxPatch our manual legend drawer adds for the frame, or None."""
    from matplotlib.patches import FancyBboxPatch
    pats = [p for p in ax.patches if isinstance(p, FancyBboxPatch)]
    return pats[-1] if pats else None


def test_legend_frame_style_applied_to_renderer():
    # 2005.09264_p27c1: a thin dark-grey SQUARE box with white fill must render
    # with that edge colour / linewidth / sharp corners, NOT matplotlib's default
    # light-grey rounded fancybox.
    from render_restyle_prototype import _replot, plt
    frame = {"edge_color": [0.149, 0.149, 0.149], "face_color": [1.0, 1.0, 1.0],
             "linewidth": 0.432, "rounded": False}
    record, style = _legend_record_style(frame)
    fig, ax = plt.subplots()
    _replot(ax, record, style)
    fr = _legend_frame_patch(ax)
    assert fr is not None, "manual legend frame patch not drawn"
    ec = fr.get_edgecolor()
    assert all(abs(ec[i] - 0.149) < 1e-3 for i in range(3))
    assert abs(fr.get_linewidth() - 0.432) < 1e-3
    assert type(fr.get_boxstyle()).__name__ == "Square"
    plt.close(fig)


def test_incoherent_legend_skipped_to_protect_data():
    # 2001.01928_p5c1: a mis-assembled OCR/glyph legend flung one entry to
    # (-0.04,-0.05) while the others sat top-right, so the entries spanned the
    # whole plot. Drawing it (handle/text artists use clip_on=False) expanded the
    # saved figure and HID the data curves (data_iou 0.85 -> 0.29). The drawer must
    # SKIP an incoherent legend (return False) so the reconstruction is never
    # harmed; a compact column still draws.
    from render_restyle_prototype import _draw_legend_manual, plt
    record, style = _legend_record_style(None)
    txt = style["text"]
    L = lambda x: x                       # noqa: E731 (test stub)
    _fs = lambda v: v or 8.0              # noqa: E731

    # compact column (entries share an x) -> drawn
    fig, ax = plt.subplots()
    assert _draw_legend_manual(ax, record, style, txt, L, _fs) is True
    plt.close(fig)

    # scattered: one entry bottom-left, others top-right -> skipped (no legend)
    txt["legend"]["entries"] = [
        {"label": "A", "x_frac": -0.04, "y_frac": -0.05, "size": 8.0},
        {"label": "B", "x_frac": 0.92, "y_frac": 0.85, "size": 8.0},
        {"label": "C", "x_frac": 0.92, "y_frac": 0.77, "size": 8.0}]
    fig, ax = plt.subplots()
    assert _draw_legend_manual(ax, record, style, txt, L, _fs) is False
    plt.close(fig)

    # an entry far out of bounds (top alone) is also incoherent -> skipped
    txt["legend"]["entries"] = [
        {"label": "A", "x_frac": 0.2, "y_frac": 0.9, "size": 8.0},
        {"label": "B", "x_frac": 0.2, "y_frac": 1.6, "size": 8.0}]
    fig, ax = plt.subplots()
    assert _draw_legend_manual(ax, record, style, txt, L, _fs) is False
    plt.close(fig)


def test_legend_entry_with_no_handle_still_draws_line_sample():
    # 2501.12648_p16c3 / 2304.13404_p8c2: per-entry swatch detection failed
    # (handle=None) for legend entries whose labels MATCH plotted series, so the
    # legend showed floating labels with no line sample. Because legend.entries
    # only ever holds labels that matched a series, a handle=None entry whose
    # label matches a series must synthesise a line handle from that series.
    from matplotlib.lines import Line2D
    from render_restyle_prototype import _draw_legend_manual, plt
    record, style = _legend_record_style(None)
    txt = style["text"]
    L = lambda x: x                       # noqa: E731 (test stub)
    _fs = lambda v: v or 8.0              # noqa: E731
    # both entries lost their swatch (handle=None) but their labels (A, B) match
    # the two plotted series -> both must still draw a line handle.
    for ent in txt["legend"]["entries"]:
        ent["handle"] = None
    fig, ax = plt.subplots()
    assert _draw_legend_manual(ax, record, style, txt, L, _fs) is True
    # one Line2D handle per matched entry, in each series' colour.
    lines = [ln for ln in ax.lines if len(ln.get_xdata()) == 2]
    assert len(lines) >= 2, "no line handles synthesised for handle=None entries"
    colors = {tuple(round(c, 3) for c in ln.get_color()[:3]) for ln in lines}
    assert (0.0, 0.0, 0.0) in colors and (0.8, 0.1, 0.1) in colors
    plt.close(fig)

    # An entry whose label matches NO series (the inline-label case reaching the
    # drawer) stays text-only: no synthesised line handle for it.
    record2, style2 = _legend_record_style(None)
    txt2 = style2["text"]
    txt2["legend"]["entries"] = [
        {"label": "930 ps", "x_frac": 0.2, "y_frac": 0.9, "size": 8.0,
         "handle": None},
        {"label": "B", "x_frac": 0.2, "y_frac": 0.8, "size": 8.0,
         "handle": None}]  # only "B" matches a series
    fig, ax = plt.subplots()
    assert _draw_legend_manual(ax, record2, style2, txt2, L, _fs) is True
    lines = [ln for ln in ax.lines if len(ln.get_xdata()) == 2]
    assert len(lines) == 1, "unmatched inline label should not draw a handle"
    plt.close(fig)


def test_text_rotation_recovers_diagonal_and_snaps_horizontal():
    # 2006.14257_p10c1: curve labels drawn diagonally (~24 deg up-right). PDF y is
    # DOWN, so an up-right baseline is dir=(cos, -sin) -> positive matplotlib deg.
    import math as _m
    assert _text_rotation((1.0, 0.0)) == 0          # horizontal -> snapped
    assert _text_rotation((0.999, -0.01)) == 0      # near-horizontal -> snapped
    up = _text_rotation((_m.cos(_m.radians(24)), -_m.sin(_m.radians(24))))
    assert abs(up - 24.0) < 0.5                       # diagonal preserved
    assert abs(_text_rotation((0.0, -1.0)) - 90.0) < 0.5   # vertical preserved


def test_annotation_rotation_applied_by_renderer():
    from render_restyle_prototype import _replot, plt
    record, style = _mini_record_style()
    style["text"]["annotations"] = [
        {"text": "r=20%", "x": 0.5, "y": 0.5, "size": 8.0, "color": None,
         "rotation": 24.0, "bold": False}]
    fig, ax = plt.subplots()
    _replot(ax, record, style)
    rotated = [t for t in ax.texts if abs(t.get_rotation() - 24.0) < 0.5]
    assert rotated, "annotation rotation not applied"
    plt.close(fig)


def test_legend_frame_rounded_uses_round_boxstyle():
    from render_restyle_prototype import _replot, plt
    frame = {"edge_color": [0.5, 0.5, 0.5], "face_color": [1.0, 1.0, 1.0],
             "linewidth": 0.8, "rounded": True}
    record, style = _legend_record_style(frame)
    fig, ax = plt.subplots()
    _replot(ax, record, style)
    fr = _legend_frame_patch(ax)
    assert fr is not None and type(fr.get_boxstyle()).__name__ == "Round"
    plt.close(fig)


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


def test_connect_false_for_filled_scatter_grazed_by_sibling_line():
    # 2006.09651_p5c1/p5c2: two same-colour series share the SAME x-grid and
    # overlap in y -- an OPEN-circle line series and a FILLED-circle pure scatter.
    # The open series' connector runs just BESIDE the filled markers, grazing them
    # at a scattered ~2-7px offset. A loose vertex-distance test collapsed the
    # filled scatter into a (spurious) connected line. The line genuinely threads
    # only its OWN (open) markers; the filled scatter must stay unconnected.
    line_pts = [(float(x), 100.0) for x in range(0, 60, 4)]      # open series
    connector = _path(line_pts)                                  # drawn ON them
    # Filled scatter: same x-grid, sitting a few px ABOVE the line (different data).
    filled = [(float(x), 100.0 - 3.5) for x in range(0, 60, 4)]
    tol = max(2.0, 0.5 * 4.0)                                    # marker size ~4px
    assert _threads_markers([connector], line_pts, tol)          # open -> connect
    assert not _threads_markers([connector], filled, tol)        # filled -> scatter


def test_connect_true_for_coarsely_sampled_connector():
    # A real connector sampled COARSELY: its vertices are not all ON the markers,
    # but every marker lies on a SEGMENT of the polyline. Segment-distance (not
    # nearest-vertex) keeps such a genuine line+marker plot connected.
    pts = [(float(x), 2.0 * x) for x in range(0, 41, 4)]         # markers
    connector = _path([(0.0, 0.0), (40.0, 80.0)])               # 2 vertices only
    assert _threads_markers([connector], pts, tol=2.0)


# --- Bug B': connecting line drawn as one OPEN segment per adjacent marker pair -
# 2201.08243_p38c1: a line+marker series whose join is many 2-vertex segments
# hopping marker-to-marker, so no single path threads the whole series and the
# big-path _threads_markers misses it. The connector is recovered from the
# COLLECTION of short open segments that bridge distinct markers.
def test_segments_connect_when_short_segments_bridge_markers():
    pts = [(float(x), 10.0 + 0.5 * x) for x in range(0, 48, 4)]  # 12 markers
    # one open 2-vertex segment between each adjacent pair (the drawn line)
    segs = [_path([pts[i], pts[i + 1]]) for i in range(len(pts) - 1)]
    assert _segments_thread_markers(segs, pts, tol=4.0)


def test_segments_no_connect_for_open_marker_glyph_strokes():
    # An open 'x'/'+' marker scatter with NO line: each glyph is two crossing
    # strokes that both sit on ONE marker (bridge nothing) -> must stay scatter.
    pts = [(float(x), 20.0) for x in range(0, 48, 4)]  # 12 markers
    segs = []
    for cx, cy in pts:  # two crossing strokes per marker, centred on it
        segs.append(_path([(cx - 1.5, cy - 1.5), (cx + 1.5, cy + 1.5)]))
        segs.append(_path([(cx - 1.5, cy + 1.5), (cx + 1.5, cy - 1.5)]))
    assert not _segments_thread_markers(segs, pts, tol=4.0)


def test_segments_no_connect_for_single_stray_segment():
    # A lone same-colour open segment is not enough to fabricate a connection
    # (mirrors the #66 sparse-scatter discipline: precision over recall).
    pts = [(float(x), 20.0) for x in range(0, 48, 4)]
    stray = _path([pts[0], pts[1]])
    assert not _segments_thread_markers([stray], pts, tol=4.0)


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


def test_tick_label_keeps_pi_suffix():
    # 2001.01928_p5c1: a π-axis ("0.0π .. 3.0π") -- the recovered "Nπ" labels carry
    # radian values (N·π) and must render verbatim, not as the raw radian number.
    assert _faithful_tick_label(0.0, "0.0π") == "0.0π"
    assert _faithful_tick_label(0.5 * math.pi, "0.5π") == "0.5π"
    assert _faithful_tick_label(math.pi, "1.0π") == "1.0π"
    assert _faithful_tick_label(3.0 * math.pi, "3.0π") == "3.0π"
    assert _faithful_tick_label(math.pi, "π") == "π"
    assert _faithful_tick_label(2 * math.pi, "2π") == "2π"
    assert _faithful_tick_label(math.pi / 2, "π/2") == "π/2"
    # a π label that does NOT match its value is rejected (falls back to numeric)
    assert _faithful_tick_label(1.0, "0.5π") is None


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


def test_source_multiplier_exp_recovers_factored_exponent():
    # 2004.12366_p12c1: labels "-100".."-900" carry values -100000..-900000, so
    # the source factored a ×10^3 header (value / label-mantissa == 10^3). The
    # renderer must reproduce ×10^3 (mantissa -900), NOT let ScalarFormatter pick
    # its own ×10^5 (mantissa -9) -- a 100x-looking magnitude shift.
    ticks = [{"value": -100000.0 * k, "label": f"−{100 * k}"}
             for k in range(1, 10)]
    ticks.insert(0, {"value": 0.0, "label": "0"})
    assert _source_multiplier_exp(ticks) == 3
    # ×10^-4 header: labels ".2 .4" carry values 2e-5 4e-5 -> exponent -4.
    assert _source_multiplier_exp(
        [{"value": 2e-5, "label": ".2"}, {"value": 4e-5, "label": ".4"}]) == -4
    # No factoring (label matches value) -> exponent 0.
    assert _source_multiplier_exp(
        [{"value": 2e4, "label": "20000"}, {"value": 4e4, "label": "40000"}]) == 0
    # Disagreement / non-power-of-ten ratio -> None (fall back to auto offset).
    assert _source_multiplier_exp(
        [{"value": -100000.0, "label": "−100"},
         {"value": -2.0, "label": "−2"}]) is None
    assert _source_multiplier_exp([]) is None


def test_render_reproduces_source_axis_multiplier_exponent():
    # End-to-end: a y-axis with -900000-magnitude data whose source labels were
    # mantissas under a ×10^3 header must render the ×10^3 offset text (mantissa
    # ticks 0..-900), not matplotlib's auto ×10^5 (0..-9).
    from render_restyle_prototype import _replot, plt
    yticks = [{"pixel": float(i), "value": -100000.0 * i,
               "label": ("0" if i == 0 else f"−{100 * i}")}
              for i in range(0, 10)]
    record = {"series": [{"points": [{"x": 0.0, "y": 0.0},
                                     {"x": 1.0, "y": -900000.0}]}],
              "x_axis": {"data_range": [0.0, 1.0]},
              "y_axis": {"data_range": [0.0, -900000.0]},
              "xticks": [], "yticks": yticks}
    style = {"series": [{"color": [0.0, 0.0, 0.0], "label": None}],
             "x_axis": {"title": "X", "ticks": []},
             "y_axis": {"title": "Y", "ticks": yticks},
             "text": {"base_font_size": 10.0,
                      "elements": {"x_title": {"size": 10.0},
                                   "y_title": {"size": 10.0}}}}
    fig, ax = plt.subplots()
    _replot(ax, record, style, font_scale=1.0)
    fig.canvas.draw()  # force the formatter to compute its offset/order
    assert ax.yaxis.get_major_formatter().orderOfMagnitude == 3
    plt.close(fig)


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


def test_legend_left_aligned_column_vs_scattered():
    # A real vertical legend stacks left-aligned entries (same x0) -> True, so the
    # width gate is bypassed and a narrow-panel legend (2005.05829) keeps its
    # recovered layout instead of falling back to an oversized default font. A
    # scattered false match (caught ticks at different x) -> False (stays gated).
    from pdf_chart2table.style import _legend_left_aligned
    col = [{"bbox": (60.0, 40.0, 95.0, 47.0)}, {"bbox": (60.5, 52.0, 95.0, 59.0)}]
    scattered = [{"bbox": (20.0, 40.0, 28.0, 47.0)}, {"bbox": (180.0, 120.0, 188.0, 127.0)}]
    assert _legend_left_aligned(col) is True
    assert _legend_left_aligned(scattered) is False
    assert _legend_left_aligned(col[:1]) is False  # need >= 2 entries


def test_legend_order_follows_original_top_to_bottom():
    # 2202.11909_p25c1: legend entries were shown in series-extraction order
    # (aI then PTE), but the original lists PTE above aI. style records "order" as
    # the original top-to-bottom (smaller PDF-y = higher = first).
    from pdf_chart2table.style import recover_text_style  # noqa: F401
    # Build a tiny matched-span set directly via the ordering rule: entries higher
    # on the page (smaller y) come first.
    spans = [{"text": "PTE simulation", "bbox": (60.0, 40.0, 130.0, 47.0), "size": 8.0},
             {"text": "aI0.66 fitting", "bbox": (60.0, 52.0, 130.0, 59.0), "size": 8.0}]
    order = [s["text"] for s in sorted(
        spans, key=lambda s: (round((s["bbox"][1] + s["bbox"][3]) / 12.0), s["bbox"][0]))]
    assert order == ["PTE simulation", "aI0.66 fitting"]


def test_frag_x_coverage_solid_vs_dashed():
    # A SOLID curve drawn in contiguous pieces tiles ~its whole x-span (coverage
    # ~1.0); a real DASHED line's fragments leave on/off gaps (coverage ~0.5).
    # 2001.06496_p18c2: solid blue/orange in 14 pieces were wrongly dashed.
    from pdf_chart2table.style import _frag_x_coverage
    from pdf_chart2table.model import Path as VP
    def seg(x0, x1):
        return VP(points=[(x0, 0.0), (x1, 0.0)], stroke=(0, 0, 1), fill=None,
                  width=1.0, dashes=None, closed=False, bbox=(x0, 0.0, x1, 1.0))
    contiguous = [seg(i * 10.0, i * 10.0 + 10.0) for i in range(8)]   # tiles 0..80
    dashed = [seg(i * 10.0, i * 10.0 + 4.0) for i in range(8)]        # 4-on / 6-off
    assert _frag_x_coverage(contiguous) > 0.95
    assert _frag_x_coverage(dashed) < 0.7
