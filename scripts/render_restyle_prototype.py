"""Prototype: faithful-restyle reconstruction for a small sample.

For each sampled chart we emit a 3-panel PNG:
  1. ORIGINAL        -- the vector chart region, rasterized as-is;
  2. ORIGINAL+PIXELS -- the same region with the extracted marker pixels (red);
  3. RECONSTRUCTED   -- the extracted (x,y) re-plotted in the ORIGINAL's style.

The re-plot is made as faithful as possible: each series in its own extracted
COLOR, drawn as a LINE (no marker) or SCATTER (its true marker shape), with the
original stroke WIDTH and DASH pattern recovered from the source vector paths; the
original axis scale (log/linear), calibrated data ranges (orientation preserved),
original tick values+labels, and aspect ratio; plus FONT family + sizes (base,
ticks, axis titles, chart title), LEGEND layout (orientation/ncol + position), and
axis-LABEL positions -- all recovered from the source page's text spans.

Outputs per chart: ``<chart_id>.png`` (3-panel overview), ``<chart_id>_reconstruction.pdf``
(original | reconstruction, side by side -- easy to open), and
``<chart_id>_reconstruction.eps`` (pure-vector re-plot). The original itself is
already vector: see the .pdf/.svg crops in extract_out/.

The styling actually used is written back into the chart JSON under a clearly
labelled top-level ``"style"`` key (STYLE ONLY -- rendering metadata, not measurements).
For this prototype the augmented JSON is written next to the PNG in the bundle; the
canonical extract_out JSON is left untouched.

Usage:
    uv run python scripts/render_restyle_prototype.py --root <root> [--n 20]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import random
import re
import shutil
import tempfile
import unicodedata

import fitz
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.markers import MarkerStyle
from PIL import Image

# Make a home-dir TinyTeX install discoverable so matplotlib usetex can find latex.
_TT = os.path.expanduser("~/.TinyTeX/bin/x86_64-linux")
if os.path.isdir(_TT) and _TT not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _TT + os.pathsep + os.environ.get("PATH", "")
_LATEX_OK = shutil.which("latex") is not None

# --- LaTeX rendering of mangled/unicode labels -----------------------------
# usetex crashes on raw unicode (ω, Ω, χ, µ) and bare LaTeX specials (_, %, ...),
# so labels are converted: unicode -> $\command$, specials -> escaped.
_UNI2TEX = {
    "α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "δ": r"\delta",
    "ε": r"\epsilon", "ζ": r"\zeta", "η": r"\eta", "θ": r"\theta",
    "ϑ": r"\vartheta", "ι": r"\iota", "κ": r"\kappa", "λ": r"\lambda",
    "μ": r"\mu", "µ": r"\mu", "ν": r"\nu", "ξ": r"\xi", "π": r"\pi",
    "ρ": r"\rho", "σ": r"\sigma", "ς": r"\varsigma", "τ": r"\tau",
    "υ": r"\upsilon", "φ": r"\phi", "ϕ": r"\varphi", "χ": r"\chi",
    "ψ": r"\psi", "ω": r"\omega", "Γ": r"\Gamma", "Δ": r"\Delta",
    "Θ": r"\Theta", "Λ": r"\Lambda", "Ξ": r"\Xi", "Π": r"\Pi",
    "Σ": r"\Sigma", "Φ": r"\Phi", "Ψ": r"\Psi", "Ω": r"\Omega",
    "×": r"\times", "·": r"\cdot", "±": r"\pm", "∓": r"\mp", "−": "-",
    "∞": r"\infty", "≈": r"\approx", "≤": r"\leq", "≥": r"\geq",
    "≠": r"\neq", "→": r"\rightarrow", "←": r"\leftarrow", "↔": r"\leftrightarrow",
    "⟨": r"\langle", "⟩": r"\rangle", "′": r"\prime", "″": r"\prime\prime",
    "√": r"\surd", "∝": r"\propto", "°": r"{}^\circ", "ℏ": r"\hbar",
    "∂": r"\partial", "∇": r"\nabla", "∑": r"\sum", "∫": r"\int", "•": r"\bullet",
}
_SUB = "₀₁₂₃₄₅₆₇₈₉"
_SUP = "⁰¹²³⁴⁵⁶⁷⁸⁹"
_SPECIAL = {"_": r"\_", "%": r"\%", "&": r"\&", "#": r"\#", "$": r"\$",
            "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
            "^": r"\textasciicircum{}", "\\": r"\textbackslash{}",
            # chars that mis-render in LaTeX text mode -> force math mode
            "|": r"$|$", "<": r"$<$", ">": r"$>$"}


def _demath_alnum(s):
    """Map Mathematical Alphanumeric Symbols (𝑅 𝑋 𝐻 𝔹 𝛼 ...) and letterlike math
    chars (ℎ) to their plain base via NFKC. matplotlib's default + math fonts lack
    the U+1D400–U+1D7FF block, so those glyphs render as dummy boxes (2202.11139:
    '2D−𝑅$_{ℎ}$$^{𝑋}$'). NFKC collapses each to a plain letter/digit (or a Greek
    letter that _UNI2TEX then maps), which renders correctly -- italic inside $...$.
    Only chars in those math blocks are touched, so ordinary text is unchanged."""
    if not s:
        return s
    out = []
    for ch in s:
        o = ord(ch)
        if 0x1D400 <= o <= 0x1D7FF or ch in "ℎℏℬℰℱℋℐℒℳℜℛℕℙℚℝℤℂ℘":
            out.append(unicodedata.normalize("NFKC", ch) or ch)
        else:
            out.append(ch)
    return "".join(out)


def _latexify(s):
    """Make a (possibly unicode/mangled) label safe + nice for usetex."""
    if not s:
        return s
    s = _demath_alnum(s)
    # Already-formatted inline mathtext (our sub/superscript markup '$^{..}$' /
    # '$_{..}$') is valid in usetex as-is; escaping its '$', '_', '{' would break
    # it. Pass such strings through untouched.
    if "$" in s:
        return s
    out = []
    for ch in s:
        if ch in _UNI2TEX:
            out.append("$" + _UNI2TEX[ch] + "$")
        elif ch in _SUB:
            out.append("$_{%d}$" % _SUB.index(ch))
        elif ch in _SUP:
            out.append("$^{%d}$" % _SUP.index(ch))
        elif ch in _SPECIAL:
            out.append(_SPECIAL[ch])
        else:
            out.append(ch)
    return "".join(out)


def _math_italic(text):
    """Render a run as math-italic: letters/digits slant in math mode, unicode
    symbols map to their math command (already italic). Used to compose mixed
    italic/roman labels token by token (e.g. the 'τ' of 'τ (s)')."""
    text = _demath_alnum(text)
    inner = []
    for idx, ch in enumerate(text):
        if ch in _UNI2TEX:
            cmd = _UNI2TEX[ch]
            # A TeX control WORD (\delta) run straight into a following letter/digit
            # forms an unknown command (\deltaV) and crashes mathtext -- terminate
            # it with a space (2003.13177_p25c1: 'δV' -> '\delta V').
            nxt = text[idx + 1] if idx + 1 < len(text) else ""
            if cmd[-1].isalpha() and nxt.isalnum():
                cmd += " "
            inner.append(cmd)
        elif ch == " ":
            inner.append(r"\ ")
        else:
            inner.append(ch)            # latin/digits italic in math; /,(,),- ok
    return "$" + "".join(inner) + "$"


def _compose_runs(runs):
    """Build a label string from [text, is_italic] runs: italic runs as math-italic,
    roman runs latexified normally. The result already carries '$', so the usual
    _latexify pass-through leaves it intact."""
    return "".join(_math_italic(t) if it else _latexify(t) for t, it in runs)


# --------------------------------------------------------------------------
# Style recovery now lives in the extraction library (pdf_chart2table.style)
# and is written into chart.json at parse time. Re-exported here so existing
# tests importing these names from this module keep working, and so the
# drawing code can reuse the shared helpers.
# --------------------------------------------------------------------------
from pdf_chart2table.style import (  # noqa: E402
    _axis_style, _baseline, _classify_family, _clean, _group_color,
    _group_spans, _is_bold, _is_italic, _is_latex_font, _join_group,
    _label_match, _marker_shape, _median, _norm, _parse_dashes, _reading_pos,
    _round_color, _span_color, _spans_in_region, _star_spikes, _threads_markers,
    build_style, match_series_styles, recover_text_style, recover_tick_style)


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def _color(c):
    if not c:
        return None
    try:
        return tuple(min(1.0, max(0.0, float(v))) for v in c[:3])
    except Exception:
        return None


def _dash_is_dotted(dashes) -> bool:
    """True when a PDF dash pattern reads as DOTS rather than dashes: its first
    "on" segment is short (<= ~1.5pt), e.g. '[ .046 .046 ] 0' (fine grid) or
    '[ .907 2.72 ] 0' (dotted reference line). Longer on-segments are dashes."""
    try:
        nums = [float(x) for x in re.findall(r"[\d.]+", str(dashes))]
    except Exception:
        return False
    return bool(nums) and nums[0] <= 1.5


def _anchor_loc(a):
    """Map a normalized (x, y) legend anchor to a matplotlib loc string."""
    x, y = a
    h = "left" if x < 0.4 else "right" if x > 0.6 else "center"
    v = "upper" if y > 0.6 else "lower" if y < 0.4 else "center"
    if h == "center" and v == "center":
        return "center"
    if v == "center":
        return f"center {h}"
    if h == "center":
        return f"{v} center"
    return f"{v} {h}"


def _fit_pix_to_value(pairs, scale):
    """Least-squares pixel->value map from labeled (pixel, value) ticks, so the
    UNLABELED detected ticks (minor ticks) can be placed at their true values."""
    import math
    pts = [(p, (math.log10(v) if scale == "log" and v > 0 else v))
           for p, v in pairs if p is not None and v is not None
           and (scale != "log" or v > 0)]
    if len(pts) < 2:
        return None
    n = len(pts)
    sx = sum(p for p, _ in pts); sy = sum(q for _, q in pts)
    sxx = sum(p * p for p, _ in pts); sxy = sum(p * q for p, q in pts)
    den = n * sxx - sx * sx
    if den == 0:
        return None
    a = (n * sxy - sx * sy) / den
    b = (sy - a * sx) / n
    return lambda px: (10 ** (a * px + b)) if scale == "log" else (a * px + b)


def _effective_scale(axis_style):
    """Guard against the extractor mis-tagging a LINEAR axis as 'log'. A genuine
    log axis spans >= ~1 decade; if the labeled ticks span less (e.g. 700..860),
    it is linear and a log render would print '7x10^2' instead of '700'."""
    if not axis_style or axis_style.get("scale") != "log":
        return (axis_style or {}).get("scale")
    vals = [t.get("value") for t in axis_style.get("ticks", [])
            if t.get("value") and t["value"] > 0]
    if len(vals) >= 2 and max(vals) / min(vals) < 8.0:
        return "linear"
    return "log"


def _ticks_in_range(values, data_range):
    """Drop tick values that fall WILDLY outside the calibrated ``data_range``.

    A single mis-extracted tick label (e.g. '680.18' on an axis spanning
    0.03..0.10) otherwise forces matplotlib's view to that outlier and collapses
    the real data to a flat line. ``data_range`` is the parser's calibrated axis
    extent (already trusted for ``set_*lim``); ticks outside it by more than the
    span itself are spurious and are removed. A small margin keeps legitimate
    edge ticks. Returns ``values`` unchanged when no usable range is given."""
    if not values or not data_range or None in data_range:
        return values
    lo, hi = sorted(data_range)
    span = hi - lo
    if span <= 0:
        return values
    margin = span  # generous: keep anything within one full span of the edges
    kept = [v for v in values if lo - margin <= v <= hi + margin]
    return kept if len(kept) >= 2 else values


def _faithful_tick_label(value, label):
    """The ORIGINAL tick-label string when it faithfully renders ``value`` -- so a
    tick at 1.0 labelled "1" stays "1" (not matplotlib's "1.0"), and the original
    "500" vs "5×10²" choice is preserved. Returns None when the label is missing,
    mangled (LaTeX garbage), or doesn't match the value (so we fall back to plain
    numeric formatting)."""
    if label is None:
        return None
    s = str(label).strip()
    if not s:
        return None
    t = (s.replace("−", "-").replace("—", "-")
          .replace("×", "x").replace("·", "").replace(" ", ""))
    m = re.fullmatch(r"([-+]?(?:\d+\.?\d*|\.\d+))(?:[xX]10\^?\{?\(?([-+]?\d+)\}?\)?)?", t)
    if not m:
        return None
    try:
        num = float(m.group(1)) * (10.0 ** int(m.group(2))) if m.group(2) else float(m.group(1))
    except ValueError:
        return None
    return s if abs(num - value) <= 1e-6 * max(abs(value), abs(num)) + 1e-9 else None


def _plain_num(x):
    """Plain numeric tick text: integers without a trailing ``.0``; never
    scientific/offset notation."""
    if abs(x - round(x)) < 1e-9 and abs(x) < 1e15:
        return str(int(round(x)))
    return "%g" % x


def _use_axis_multiplier(values):
    """True when tick magnitudes are extreme enough that the original factored a
    common ×10ⁿ multiplier into an axis header (e.g. ``×10⁻⁴`` with mantissa
    labels ``.2 .4 …``) rather than printing full values like ``0.0002``."""
    mx = max((abs(v) for v in values if v is not None), default=0.0)
    return mx > 0 and (mx >= 1e4 or mx < 1e-2)


def _apply_ticks(ax, axis, ticks, scale, data_range=None):
    """Place the parser's DETECTED ticks: labeled ones as MAJOR (matplotlib
    formats the labels -- extracted label strings are often mangled), and the
    unlabeled detected ticks as MINOR (mapped pixel->value), so both the COUNT
    and positions match the original instead of matplotlib's auto ticks."""
    ticks = ticks or []
    setter = ax.set_xticks if axis == "x" else ax.set_yticks
    major = [t["value"] for t in ticks if t.get("value") is not None]
    major = _ticks_in_range(major, data_range)
    if len(major) >= 2:
        setter(sorted(major))
    elif scale == "log":
        return  # too few labeled to trust; defer to matplotlib's decade ticks
    f = _fit_pix_to_value(
        [(t.get("pixel"), t.get("value")) for t in ticks], scale)
    if f is not None:
        minor = [f(t["pixel"]) for t in ticks
                 if t.get("value") is None and t.get("pixel") is not None]
        minor = [m for m in minor if scale != "log" or m > 0]
        minor = _ticks_in_range(minor, data_range) if minor else minor
        if minor:
            from matplotlib.ticker import NullFormatter
            setter(sorted(minor), minor=True)
            # minor ticks must NOT be labelled (matplotlib's log minor formatter
            # otherwise prints garbage like "1.003e3" for off-decade positions).
            (ax.xaxis if axis == "x" else ax.yaxis).set_minor_formatter(
                NullFormatter())


def _axis_view(record, style, which):
    """Merge an axis's DATA (record) with its STYLE (style) into the dict the
    tick/limit helpers consume. data_range + per-tick pixel/value are DATA and
    live in the record (``record["x_axis"]["data_range"]``, ``record["xticks"]``);
    the displayed tick-label strings live in the STYLE block. Join the labels to
    the data ticks BY INDEX (same order the extractor wrote both)."""
    ast = style.get(f"{which}_axis")
    if ast is None:
        return None
    adata = record.get(f"{which}_axis") or {}
    dticks = record.get("xticks" if which == "x" else "yticks") or []
    slabels = ast.get("ticks") or []
    ticks = []
    for i, t in enumerate(dticks):
        lbl = slabels[i].get("label") if i < len(slabels) else None
        ticks.append({"pixel": t.get("pixel"), "value": t.get("value"),
                      "label": lbl})
    return {
        "scale": ast.get("scale"),
        "title": ast.get("title"),
        "tick_direction": ast.get("tick_direction"),
        "tick_length": ast.get("tick_length"),
        "data_range": adata.get("data_range"),  # DATA, from the record
        "ticks": ticks,
    }


def _replot(ax, record, style, tex=False, font_scale=1.0):
    """Draw the extracted data into ``ax`` in the original's style."""
    L = _latexify if tex else (lambda x: x)
    # Round line caps recovered from the source (PDF lineCap 1): thick lines get
    # rounded ends instead of matplotlib's default projecting/butt caps.
    _capkw = ({"solid_capstyle": "round", "dash_capstyle": "round"}
              if style.get("round_caps") else {})
    has_label = False
    for ser, st in zip(record["series"], style["series"]):
        pts = ser["points"]
        if not pts:
            continue
        col = _color(st.get("color"))
        if col and min(col) > 0.9:  # near-white = background captured as a series
            continue
        xs = [p["x"] for p in pts]
        ys = [p["y"] for p in pts]
        lab = L(st.get("label"))
        has_label = has_label or bool(st.get("label"))
        ls = st.get("linestyle") or "-"
        if isinstance(ls, list):  # JSON round-trip: [offset, [on, off]]
            ls = (ls[0], tuple(ls[1]))
        alpha = st.get("alpha")  # recovered transparency (None = opaque)
        if st.get("render_as") == "scatter":
            mk = st.get("marker_shape") or st.get("marker") or "o"  # geometry wins
            md = st.get("markersize")  # recovered glyph diameter in points
            # font_scale magnifies the PNG review panel; scale linear style dims
            # (marker diameter, line/edge widths) by it too so markers/lines keep
            # their proportion to the (also-scaled) text. Deliverables use 1.0.
            if md:
                sz = max(2.0, min(2500.0, (md * font_scale) ** 2))  # s = diameter^2
            else:
                sz = {"*": 14, "^": 12, "x": 10}.get(mk, 9) * font_scale ** 2
            # line+marker series: draw the connecting line first, then the markers.
            # Points are emitted in TRUE draw order, so plot as-is (do NOT re-sort
            # by x -- that scrambles sideways/folded curves).
            if st.get("connect"):
                ax.plot(xs, ys, color=col, alpha=alpha,
                        linewidth=(st.get("linewidth") or 0.8) * font_scale,
                        linestyle=ls, zorder=1, **_capkw)
            # FACE / EDGE colour + EDGE width are recovered independently. A None
            # face = open marker (no fill). Fall back to the series colour.
            face = _color(st.get("marker_face"))
            edge = _color(st.get("marker_edge")) or col
            ew = st.get("marker_edge_width")
            ax.scatter(xs, ys, s=sz, marker=mk, label=lab, zorder=2, alpha=alpha,
                       facecolors=(face if face is not None else "none"),
                       edgecolors=edge,
                       linewidths=(ew * font_scale if ew else None))
        else:
            ax.plot(xs, ys, color=col, alpha=alpha, label=lab,
                    linewidth=(st.get("linewidth") or 1.2) * font_scale,
                    linestyle=ls, **_capkw)
        # Recovered per-point error bars (vertical whiskers): draw the bars only
        # (fmt="none"), on top of the markers/line already drawn above.
        yerr = [p.get("y_err") for p in pts]
        if any(e is not None for e in yerr):
            ax.errorbar(xs, ys, yerr=[e or 0.0 for e in yerr], fmt="none",
                        ecolor=col, alpha=alpha, zorder=1.5,
                        elinewidth=0.8 * font_scale, capsize=2.0 * font_scale,
                        capthick=0.8 * font_scale)

    xa, ya = _axis_view(record, style, "x"), _axis_view(record, style, "y")
    x_scale = _effective_scale(xa)
    y_scale = _effective_scale(ya)
    if x_scale == "log":
        ax.set_xscale("log")
    if y_scale == "log":
        ax.set_yscale("log")
    xr = xa.get("data_range") if xa else None
    yr = ya.get("data_range") if ya else None
    if xr and None not in xr and xr[0] != xr[1]:
        ax.set_xlim(xr[0], xr[1])
    if yr and None not in yr and yr[0] != yr[1]:
        ax.set_ylim(yr[1], yr[0])  # PDF y grows downward -> flip to match original
    # Recovered text style is noisy (LaTeX-mangled spans, pre-transform sizes,
    # multi-panel pages) so every value is sanity-gated before it is applied;
    # anything implausible falls back to matplotlib defaults.
    txt = style.get("text") or {}
    base_fs = txt.get("base_font_size")
    font_ok = bool(base_fs) and 3 <= base_fs <= 40

    def _fs(v):  # accept a recovered (scale-corrected) point size only if sane,
        # then magnify by font_scale so a panel larger than the original crop keeps
        # the same font-to-plot proportion (font_scale=1.0 leaves it untouched).
        return v * font_scale if (font_ok and v and 3 <= v <= 44) else None

    def _w(flag):
        return "bold" if flag else "normal"

    def _i(flag):
        return "italic" if flag else "normal"

    def _plain_linear(axis_obj, scale, ticks):
        # On linear axes, label major ticks with the ORIGINAL label string when it
        # faithfully renders the value (keeps "1" not "1.0", "500" not "5x10^2"),
        # else plain integers/decimals (no scientific/offset text).
        if scale == "log":
            # Use the ORIGINAL label strings on a log axis too, when they faithfully
            # render the value: a source that printed plain decades (1000, 100, ..,
            # 0.1) must NOT be re-rendered as matplotlib's default 10^n (2001.01801).
            # Ticks without a faithful recovered label fall back to 10^n, so a source
            # that genuinely used 10^n (its labels won't parse as plain decimals) is
            # unchanged.
            llut = {}
            for t in (ticks or []):
                v = t.get("value")
                if v is None:
                    continue
                txt = _faithful_tick_label(v, t.get("label"))
                if txt is not None:
                    llut[round(v, 9)] = txt
            if not llut:
                return  # nothing faithfully recovered -> matplotlib's decade format
            import math as _m
            from matplotlib.ticker import FuncFormatter as _FF

            def _logfmt(x, pos=None):
                s = llut.get(round(x, 9))
                if s is not None:
                    return s
                if x > 0:
                    e = _m.log10(x)
                    if abs(e - round(e)) < 1e-6:
                        return r"$10^{%d}$" % int(round(e))
                return _plain_num(x)
            axis_obj.set_major_formatter(_FF(_logfmt))
            return
        vals = [t.get("value") for t in (ticks or []) if t.get("value") is not None]
        # Extreme magnitudes: reproduce the original's factored "×10ⁿ" axis header
        # (mantissa ticks + a multiplier label) instead of full/scientific values.
        if _use_axis_multiplier(vals):
            from matplotlib.ticker import ScalarFormatter
            fmt = ScalarFormatter(useMathText=True)
            fmt.set_scientific(True)
            fmt.set_powerlimits((-2, 4))
            axis_obj.set_major_formatter(fmt)
            return
        lut = {}
        for t in (ticks or []):
            v = t.get("value")
            if v is None:
                continue
            txt = _faithful_tick_label(v, t.get("label"))
            if txt is not None:
                lut[round(v, 9)] = txt
        from matplotlib.ticker import FuncFormatter
        axis_obj.set_major_formatter(
            FuncFormatter(lambda x, pos=None: lut.get(round(x, 9)) or _plain_num(x)))

    # Unified per-element text style (title / axis titles / ticks), recovered
    # from the source spans: size, colour, bold, italic, per-token runs, rotation.
    elems = txt.get("elements") or {}

    def _ecolor(name):
        e = elems.get(name)
        return _color(e.get("color")) if e and e.get("color") else None

    def _label_kwargs(name):
        """matplotlib text kwargs for an axis-title element (one code path)."""
        e = elems.get(name) or {}
        runs = e.get("runs")
        return runs, {
            "fontsize": _fs(e.get("size")) or _fs(base_fs),
            "fontweight": _w(e.get("bold")),
            "color": _ecolor(name) or "black",
            "fontstyle": "normal" if runs else _i(e.get("italic")),
        }
    if xa:
        _apply_ticks(ax, "x", xa.get("ticks"), x_scale, xa.get("data_range"))
        _plain_linear(ax.xaxis, x_scale, xa.get("ticks"))
        xr, xkw = _label_kwargs("x_title")
        ax.set_xlabel(_compose_runs(xr) if xr else L(xa.get("title") or ""), **xkw)
        p = (elems.get("x_title") or {}).get("pos")  # only a plausibly-placed label
        if p and 0.2 <= p[0] <= 0.8 and -0.35 <= p[1] <= 0.02:
            ax.xaxis.set_label_coords(p[0], p[1])
    if ya:
        _apply_ticks(ax, "y", ya.get("ticks"), y_scale, ya.get("data_range"))
        _plain_linear(ax.yaxis, y_scale, ya.get("ticks"))
        yr, ykw = _label_kwargs("y_title")
        ax.set_ylabel(_compose_runs(yr) if yr else L(ya.get("title") or ""), **ykw)
        p = (elems.get("y_title") or {}).get("pos")
        if p and -0.35 <= p[0] <= 0.02 and 0.2 <= p[1] <= 0.8:
            ax.yaxis.set_label_coords(p[0], p[1])
    _tsize = (elems.get("ticks") or {}).get("size")
    if _fs(_tsize):
        ax.tick_params(labelsize=_tsize * font_scale)
    # match the original axis frame / tick line weight
    alw = style.get("axis_linewidth")
    if alw:
        for sp in ax.spines.values():
            sp.set_linewidth(alw)
    # match a COLOURED axis frame (spines + tick marks + tick labels); None=black.
    acol = _color(style.get("axis_color"))
    if acol:
        for sp in ax.spines.values():
            sp.set_edgecolor(acol)
        ax.tick_params(axis="both", which="both", color=acol, labelcolor=acol)
    # Tick-LABEL colour from the recovered tick text (more faithful than the spine
    # colour for the digits); applied last so it wins when present.
    _tc = _ecolor("ticks")
    if _tc:
        ax.tick_params(axis="both", which="both", labelcolor=_tc)
    # match the original tick appearance, all parser-sourced where possible:
    # per-axis direction (in/out) and tick LENGTH; minor ticks were already placed
    # at their detected positions by _apply_ticks. Top/right presence is heuristic.
    ts = style.get("ticks_style") or {}
    common = {"which": "both"}
    if alw:
        common["width"] = alw
    x_dir = (xa or {}).get("tick_direction") or ts.get("direction")
    y_dir = (ya or {}).get("tick_direction") or ts.get("direction")
    x_len = (xa or {}).get("tick_length")
    y_len = (ya or {}).get("tick_length")
    ax.tick_params(axis="x", direction=(x_dir or "out"),
                   top=bool(ts.get("top")), **common)
    ax.tick_params(axis="y", direction=(y_dir or "out"),
                   right=bool(ts.get("right")), **common)
    # tick LENGTH (points): majors at recovered length, minors a bit shorter.
    # Floor to ~2.5pt: inward ticks measured at ~1-2pt are barely visible, so a
    # faithfully-tiny length reads as "missing/too small" -- nudge to a legible
    # minimum (2006.09651, 2006.10101, 2003.01158).
    _tmin = 2.5
    if x_len:
        ax.tick_params(axis="x", which="major", length=round(max(x_len, _tmin), 2))
        ax.tick_params(axis="x", which="minor", length=round(0.6 * max(x_len, _tmin), 2))
    if y_len:
        ax.tick_params(axis="y", which="major", length=round(max(y_len, _tmin), 2))
        ax.tick_params(axis="y", which="minor", length=round(0.6 * max(y_len, _tmin), 2))
    # Round the tick marks + spines too when the source used round caps, so the
    # short tick bars match the rounded line ends (2001.01038_p13c4). Tick bars
    # are drawn as MARKERS, so set_solid_capstyle is a no-op -- the cap must come
    # from the marker itself (MarkerStyle(..., capstyle="round")).
    if style.get("round_caps"):
        for ln in ax.get_xticklines() + ax.get_yticklines():
            try:
                ln.set_marker(MarkerStyle(ln.get_marker(), capstyle="round"))
            except (AttributeError, ValueError, TypeError):
                pass
        for sp in ax.spines.values():
            try:
                sp.set_capstyle("round")
            except (AttributeError, ValueError):
                pass
    if (elems.get("ticks") or {}).get("bold"):
        for lb in ax.get_xticklabels() + ax.get_yticklabels():
            lb.set_fontweight("bold")
    # in-graph text annotations (recovered text that is NOT data or legend)
    for a in (txt.get("annotations") or []):
        ax.text(a["x"], a["y"], L(a.get("text") or ""), transform=ax.transAxes,
                ha="center", va="center",
                rotation=a.get("rotation") or 0,  # diagonal labels along a curve
                rotation_mode="anchor",
                fontsize=_fs(a.get("size")) or _fs(base_fs),
                color=_color(a.get("color")) or "black",
                fontweight="bold" if a.get("bold") else "normal")
    # Background grid (from the extractor): drawn behind the data, aligned with
    # the ticks, in the recovered grey colour.
    g = style.get("grid")
    if g:
        # Classify the recovered dash pattern: a small "on" segment (<= ~1.5pt) is
        # a DOTTED grid/reference line (render ":"), a longer one is dashed ("--"),
        # none is solid ("-"). Mapping every dash pattern to "--" lost the dotted
        # look the source used (2003.09710 grid, 2006.05506 ref line = dark dots).
        # A dotted grid uses a FINE, closely-spaced dot pattern (a tight custom dash)
        # rather than matplotlib's coarser ":" -- closer to the source's fine dots
        # (2003.09710). The recovered dash period is often sub-point (0.046pt) so it
        # can't be reproduced literally; this approximates "thin + many dots".
        _dotted = bool(g.get("dashes")) and _dash_is_dotted(g["dashes"])
        if not g.get("dashes"):
            gls = "-"
        elif _dotted:
            gls = (0, (0.5, 1.0))   # short dots, tight gaps
        else:
            gls = "--"
        gcolor = _color(g.get("color")) or "0.85"
        # Keep the grid SUBTLE like the source: a thin line at a low opacity. The
        # recovered width can be sub-point; floor just enough to stay visible but
        # not heavy. Dotted grids floor thinner (0.25) than solid/dashed (0.3).
        glw = min(max(g.get("linewidth") or 0.4, 0.25 if _dotted else 0.3), 0.8)
        galpha = g.get("alpha")
        if galpha is None:
            galpha = 0.5 if (gcolor != "0.85" and max(gcolor) < 0.5) else None
        xl, yl = g.get("x_lines"), g.get("y_lines")
        if xl or yl:
            # Draw each recovered grid / reference line at its exact position
            # (incl. log-axis minors and lone axhline/axvline references) rather
            # than relying on matplotlib's auto major ticks.
            for xv in (xl or []):
                ax.axvline(xv, zorder=0, linestyle=gls, color=gcolor,
                           linewidth=glw, alpha=galpha)
            for yv in (yl or []):
                ax.axhline(yv, zorder=0, linestyle=gls, color=gcolor,
                           linewidth=glw, alpha=galpha)
        else:
            axis = ("both" if (g.get("x") and g.get("y"))
                    else ("x" if g.get("x") else "y"))
            ax.grid(True, axis=axis, which="major", zorder=0, linestyle=gls,
                    color=gcolor, linewidth=glw, alpha=galpha)
        ax.set_axisbelow(True)
    # Draw a legend only when it is actually present on THIS panel.
    if has_label and txt.get("show_legend", True):
        leg = txt.get("legend")
        # frame only if the original legend was boxed (else borderless -- matplotlib's
        # faint gray box is rarely in the original).
        # Compact padding -- matplotlib's defaults make the box bulkier than the
        # tight legends these papers use.
        kw = {"fontsize": _fs((leg or {}).get("fontsize")) or _fs(base_fs) or 8,
              "frameon": bool(style.get("legend_box")),
              "borderpad": 0.3, "labelspacing": 0.3, "handlelength": 1.0,
              "handletextpad": 0.4, "columnspacing": 1.0, "borderaxespad": 0.3}
        # Recovered legend-box style: border colour/width, fill, and square vs
        # rounded corners -- so the frame matches the original instead of
        # matplotlib's default light-grey fancybox. linewidth is applied to the
        # frame patch after the legend is (re)created below.
        frame = style.get("legend_frame")
        if frame:
            kw["frameon"] = True
            kw["fancybox"] = bool(frame.get("rounded"))
            if frame.get("edge_color") is not None:
                kw["edgecolor"] = tuple(frame["edge_color"])
            if frame.get("face_color") is not None:
                kw["facecolor"] = tuple(frame["face_color"])
                kw["framealpha"] = 1.0  # opaque white fill, as in the original
        if leg:
            kw["ncol"] = leg.get("ncol", 1)
            a = leg.get("anchor")
            if a and -0.1 <= a[0] <= 1.1 and -0.1 <= a[1] <= 1.1:
                kw["loc"] = "center"  # place the box AT the recovered center
                kw["bbox_to_anchor"] = tuple(a)
            elif a:
                kw["loc"] = _anchor_loc(a)
            if leg.get("bold"):
                kw["prop"] = {"weight": "bold", "size": kw.pop("fontsize")}
            # Present entries in the ORIGINAL legend's top-to-bottom order (not the
            # series-extraction order): reorder the auto-collected handles/labels to
            # match leg["order"] (2202.11909_p25c1 had PTE/aI swapped).
            if leg.get("order"):
                _h, _lab = ax.get_legend_handles_labels()
                _want = [L(o) for o in leg["order"]]
                _used, _idx = set(), []
                for w in _want:
                    for j, ll in enumerate(_lab):
                        if j not in _used and ll == w:
                            _used.add(j); _idx.append(j); break
                _idx += [j for j in range(len(_lab)) if j not in _used]
                if len(_idx) == len(_lab) and _idx != list(range(len(_lab))):
                    kw["handles"] = [_h[j] for j in _idx]
                    kw["labels"] = [_lab[j] for j in _idx]
        legend_obj = ax.legend(**kw)
        # FIT the legend size to the original: measure the rendered legend width
        # in axes fraction and rescale the font toward the recovered extent.
        # The recovered FONT SIZE is measured directly from the PDF and is
        # accurate; the recovered WIDTH (w_frac) over-estimates the handle (it adds
        # a 3.2*fontsize swatch allowance), so it must never GROW the font (that
        # inflated legends 1.4-1.6x: 2002.02623 9.3->13.6). Only allow SHRINK (cap
        # at 1.0) when the rendered legend genuinely overflows the recovered width.
        tw = (leg or {}).get("w_frac")
        if legend_obj is not None and tw and tw > 0:
            ax.figure.canvas.draw()
            bb = legend_obj.get_window_extent()
            inv = ax.transAxes.inverted()
            (p0x, _), (p1x, _) = inv.transform((bb.x0, bb.y0)), inv.transform((bb.x1, bb.y1))
            cur_w = abs(p1x - p0x)
            if cur_w > 1e-3:
                r = max(0.55, min(1.0, tw / cur_w))
                if abs(r - 1.0) > 0.08:  # re-create at fitted size
                    if "prop" in kw:
                        kw["prop"] = dict(kw["prop"], size=kw["prop"]["size"] * r)
                    else:
                        kw["fontsize"] = (kw.get("fontsize") or 8) * r
                    legend_obj.remove()
                    legend_obj = ax.legend(**kw)
        # Apply the recovered border width to whichever legend frame is final.
        if frame and legend_obj is not None and frame.get("linewidth"):
            legend_obj.get_frame().set_linewidth(frame["linewidth"])
        # Colour the legend entry TEXT to match the source (only entries the source
        # drew chromatically; black/grey labels were omitted at recovery so they
        # stay matplotlib-default black). Keyed by the rendered (latexified) label;
        # recorded independent of legend-layout recovery so it tints auto-legends.
        lcs = txt.get("legend_label_colors")
        if legend_obj is not None and lcs:
            want = {L(k): tuple(v) for k, v in lcs.items()}
            for t in legend_obj.get_texts():
                c = want.get(t.get_text())
                if c is not None:
                    t.set_color(c)


def _original_image(record):
    src_pdf = record["source"]["pdf"]
    page0 = record["source"]["page"]  # already 0-based
    bbox = record["source"]["region_bbox"]
    margin = 12.0
    clip = fitz.Rect(bbox[0] - margin, bbox[1] - margin,
                     bbox[2] + margin, bbox[3] + margin)
    dpi = 150
    with fitz.open(src_pdf) as doc:
        pix = doc[page0].get_pixmap(dpi=dpi, clip=clip)
        arr = Image.open(io.BytesIO(pix.tobytes("png")))
    return arr, clip, dpi


def _box(ax, record, style, font_scale=1.0):
    _replot(ax, record, style, font_scale=font_scale)
    ar = style.get("aspect_ratio")
    if ar and ar > 0:
        try:
            ax.set_box_aspect(1.0 / ar)
        except Exception:
            pass


_CROP_MARGIN = 18.0  # io_store crops the original to region_bbox + this margin


def _recon_figure(record, style, tex=False):
    """A standalone reconstruction figure matched to the original crop's geometry.

    The region bbox IS the plot box (the calibration places the spines at the
    region edges), and the original crop is that region plus a fixed _CROP_MARGIN
    on every side. We size the recon figure the SAME way -- plot area = region
    W x H, surrounded by _CROP_MARGIN -- so the two side-by-side panels are at the
    identical physical scale and the recovered point sizes/line widths render
    directly comparable. (Simpler than the old per-side margin floors; tick/title
    text living in the margin band may overflow slightly on large-font charts --
    tunable via _CROP_MARGIN.)
    """
    bb = record["source"]["region_bbox"]
    w = max(bb[2] - bb[0], 1.0)
    h = max(bb[3] - bb[1], 1.0)
    m = _CROP_MARGIN
    fw, fh = w + 2 * m, h + 2 * m
    fig = plt.figure(figsize=(fw / 72.0, fh / 72.0))
    ax = fig.add_axes([m / fw, m / fh, w / fw, h / fh])
    _replot(ax, record, style, tex=tex)
    title = style.get("title")
    if title:
        t = style.get("text") or {}
        te = (t.get("elements") or {}).get("title") or {}
        tcol = _color(te.get("color")) if te.get("color") else None
        # Fall back to the base font size, never matplotlib's oversized default
        # title size, when the title's own size wasn't recovered.
        ax.set_title(_latexify(title) if tex else title,
                     fontsize=te.get("size") or t.get("base_font_size"),
                     color=tcol or "black",
                     fontweight="bold" if te.get("bold") else "normal")
    return fig


def _compose_pdf(crop_pdf, recon_pdf, out_pdf, resid_pdf=None):
    """Side-by-side PDF: original VECTOR crop | VECTOR reconstruction, plus an
    optional RESIDUAL panel (unexplained ink) when ``resid_pdf`` is given."""
    cap = 14.0  # caption band height (points)
    rec = fitz.open(recon_pdf)
    r1 = rec[0].rect
    crop = fitz.open(crop_pdf) if (crop_pdf and os.path.exists(crop_pdf)) else None
    r0 = crop[0].rect if crop else fitz.Rect(0, 0, r1.width, r1.height)
    res = fitz.open(resid_pdf) if resid_pdf else None
    r2 = res[0].rect if res else None
    gap = 18.0
    # Place panels at their NATURAL size (no scaling): the plot area is the
    # region's W x H in crop and reconstruction, so unscaled => SAME physical size;
    # top-aligning the pages lines the plot areas up vertically.
    heights = [r0.height, r1.height] + ([r2.height] if r2 else [])
    H = cap + max(heights)
    W = r0.width + gap + r1.width + ((gap + r2.width) if r2 else 0)
    out = fitz.open()
    pg = out.new_page(width=W, height=H)
    if crop:
        pg.show_pdf_page(fitz.Rect(0, cap, r0.width, cap + r0.height), crop, 0)
    x1 = r0.width + gap
    pg.show_pdf_page(fitz.Rect(x1, cap, x1 + r1.width, cap + r1.height), rec, 0)
    pg.insert_text((4, 11), "original", fontsize=9)
    pg.insert_text((x1 + 4, 11), "reconstruction", fontsize=9)
    if res:
        x2 = x1 + r1.width + gap
        pg.show_pdf_page(fitz.Rect(x2, cap, x2 + r2.width, cap + r2.height), res, 0)
        pg.insert_text((x2 + 4, 11), "residual (unexplained)", fontsize=9)
        res.close()
    out.save(out_pdf)
    out.close(); rec.close()
    if crop:
        crop.close()


def _residual_paths(record, chart_json):
    """In-region paths the extractor left UNEXPLAINED (the residual), each tagged
    missed=True when it is a candidate dropped CURVE. Reuses the residual audit so
    the panel shows exactly what the audit metric counts. [] on any failure."""
    try:
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        from residual_audit import audit_chart
        from pdf_chart2table.pdf_vector import load_pdf
        d, _n, _e, residual, missed = audit_chart(chart_json)
        missed_idx = {m["idx"] for m in missed}
        page = load_pdf(record["source"]["pdf"], [record["source"]["page"]])[0]
        return [(page.paths[r["idx"]].points, r["idx"] in missed_idx)
                for r in residual]
    except Exception:
        return []


def _draw_residual(ax, arr, clip, dpi, resid, show_title=True):
    """The unexplained residual ink: candidate dropped curves in bold red, other
    residual (decoration) in thin orange.

    The panel's POINT is the leftover ink, so an empty residual must read as "all
    ink accounted for" -- NOT a faded copy of the whole chart (a near-empty
    residual over a 0.30-alpha full backdrop looked like the entire graph shown
    faint). So: no residual -> plain "fully explained" panel; with residual -> a
    LIGHT backdrop for spatial context + the leftover ink drawn boldly on top."""
    w = getattr(arr, "width", None) or arr.shape[1]   # PIL Image vs ndarray
    h = getattr(arr, "height", None) or arr.shape[0]
    if not resid:
        ax.set_xlim(0, w); ax.set_ylim(h, 0)
        ax.text(0.5, 0.5, "no unexplained ink\n(fully explained)",
                transform=ax.transAxes, ha="center", va="center",
                fontsize=13, color="green")
        if show_title:
            ax.set_title("residual (none — fully explained)")
        ax.axis("off")
        return
    ax.imshow(arr, alpha=0.15)
    s = dpi / 72.0
    for pts, is_missed in resid:
        xs = [(x - clip.x0) * s for x, y in pts]
        ys = [(y - clip.y0) * s for x, y in pts]
        ax.plot(xs, ys, color=("red" if is_missed else "orange"),
                lw=(1.8 if is_missed else 1.0), solid_capstyle="round")
    if show_title:
        nmiss = sum(1 for _, m in resid if m)
        ax.set_title(f"residual (unexplained: {len(resid)}, missed-curve: {nmiss})")
    ax.axis("off")


# Per-face overrides layered onto the metric-font defaults, keyed by the token
# from style._classify_face. Verdana/Tahoma are wide -> DejaVu Sans (Bitstream-
# Vera lineage) fits better than Arial-metric Liberation Sans; Calibri -> Carlito;
# Cambria -> Caladea (both metric-compatible); Courier is monospace though
# _classify_family labels it "serif".
_FACE_OVERRIDES = {
    "verdana": {"font.sans-serif": ["DejaVu Sans", "Liberation Sans"]},
    "calibri": {"font.sans-serif": ["Carlito", "Liberation Sans", "DejaVu Sans"]},
    "cambria": {"font.serif": ["Caladea", "Liberation Serif", "DejaVu Serif"]},
    "courier": {"font.family": "monospace"},
}


def _metric_font_rc(face=None):
    """Prefer metric-compatible faces over matplotlib's stock DejaVu.

    DejaVu Serif/Sans glyph metrics differ visibly from the papers' Times/
    Helvetica. Liberation Serif/Sans/Mono are metric-compatible with Times New
    Roman / Arial / Courier, so the recovered "serif"/"sans-serif" family
    defaults to them (falling back to DejaVu when Liberation isn't installed).
    `face` (style._classify_face) layers a specific substitute via
    _FACE_OVERRIDES when the generic default mismatches that face's metrics."""
    rc = {
        "font.serif": ["Liberation Serif", "DejaVu Serif"],
        "font.sans-serif": ["Liberation Sans", "DejaVu Sans"],
        "font.monospace": ["Liberation Mono", "DejaVu Sans Mono"],
    }
    rc.update(_FACE_OVERRIDES.get(face, {}))
    return rc


def render_bundle(record, style, crop_pdf, out_png, out_eps, out_pdf=None,
                  chart_json=None):
    arr, clip, dpi = _original_image(record)
    txt = style.get("text") or {}
    title = style.get("title")
    _te = (txt.get("elements") or {}).get("title") or {}
    tfs = _te.get("size")
    rc = {}
    if txt.get("font_family"):
        rc["font.family"] = txt["font_family"]
        rc.update(_metric_font_rc(txt.get("font_face")))
    base = txt.get("base_font_size")
    # The 4-panel PNG forces each panel to ~5in, while recovered font sizes are in
    # the original crop's points; the original is shown as a raster MAGNIFIED to
    # fill its panel, so absolute-point recon fonts look tiny beside it. Magnify
    # the reconstruction fonts by the same panel/crop ratio to restore the
    # font-to-plot proportion (the vector EPS/PDF deliverables keep scale 1.0).
    panel_w_pt, panel_h_pt = 5.0 * 72.0, 4.2 * 72.0  # usable area of one (22,5)/4 axes
    png_font_scale = max(1.0, min(4.5, min(panel_w_pt / max(clip.width, 1.0),
                                           panel_h_pt / max(clip.height, 1.0))))
    if base and 3 <= base <= 40:  # scale-corrected; final plausibility guard
        rc["font.size"] = base * png_font_scale

    def overlay(ax):
        s = dpi / 72.0
        for ser in record["series"]:
            xs = [(p["x_px"] - clip.x0) * s for p in ser["points"]]
            ys = [(p["y_px"] - clip.y0) * s for p in ser["points"]]
            ax.scatter(xs, ys, s=10, facecolors="none", edgecolors="red",
                       linewidths=0.6)

    resid = _residual_paths(record, chart_json) if chart_json else []

    with plt.rc_context(rc):
        # PNG: 4-panel overview (original | +extracted pixels | reconstruction |
        # residual = unexplained ink, so misses are visible at a glance).
        fig, (a0, a1, a2, a3) = plt.subplots(1, 4, figsize=(22, 5))
        a0.imshow(arr); a0.set_title("original"); a0.axis("off")
        a1.imshow(arr); a1.set_title("original + extracted pixels"); a1.axis("off")
        overlay(a1)
        a2.set_title("reconstructed (original style)")
        _box(a2, record, style, font_scale=png_font_scale)
        _draw_residual(a3, arr, clip, dpi, resid)
        if title:
            fig.suptitle(title, fontsize=(tfs * png_font_scale if tfs else None),
                         fontweight="bold" if _te.get("bold") else "normal")
        fig.tight_layout(); fig.savefig(out_png, dpi=110); plt.close(fig)

        # EPS: pure-vector reconstruction at physical scale
        figE = _recon_figure(record, style)
        figE.savefig(out_eps, format="eps"); plt.close(figE)

        # PDF: original vector crop | vector reconstruction, both physical scale.
        # Use real LaTeX (usetex) for the re-plot when the source is a TeX font and
        # latex is installed; fall back to normal rendering if LaTeX errors.
        if out_pdf:
            tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False).name
            use_tex = _LATEX_OK and txt.get("latex_like")
            done = False
            if use_tex:
                try:
                    with plt.rc_context({"text.usetex": True,
                                         "text.latex.preamble": r"\usepackage{type1cm}"}):
                        figP = _recon_figure(record, style, tex=True)
                        figP.savefig(tmp, format="pdf"); plt.close(figP)
                    done = True
                except Exception as e:
                    plt.close("all")
                    print(f"    usetex fallback ({e.__class__.__name__})", flush=True)
            if not done:
                figP = _recon_figure(record, style)
                figP.savefig(tmp, format="pdf"); plt.close(figP)
            # Residual panel: original (faded) + unexplained ink, sized like the
            # crop so the three panels line up.
            tmpR = None
            if resid:
                tmpR = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False).name
                bb = record["source"]["region_bbox"]
                rw, rh = max(bb[2]-bb[0], 1.0), max(bb[3]-bb[1], 1.0)
                figR = plt.figure(figsize=((rw+24)/72.0, (rh+24)/72.0))
                axR = figR.add_axes([0.02, 0.02, 0.96, 0.96])
                _draw_residual(axR, arr, clip, dpi, resid, show_title=False)
                figR.savefig(tmpR, format="pdf"); plt.close(figR)
            try:
                _compose_pdf(crop_pdf, tmp, out_pdf, resid_pdf=tmpR)
            finally:
                os.remove(tmp)
                if tmpR:
                    os.remove(tmpR)


def _select(root: str, n: int, exclude=frozenset(), seed=None):
    """Pick ``n`` charts, one per arxiv id. ``exclude`` drops chart_ids AND their
    arxiv_ids (so a fresh draw shares no paper with an earlier one). With ``seed``
    set, the draw is a reproducible random sample over the whole pool (used to get
    a genuinely different set, not the next stride positions)."""
    excl_arxiv = {cid.rsplit("_p", 1)[0] for cid in exclude}
    rows = []
    with open(os.path.join(root, "figures_index.csv")) as f:
        for r in csv.DictReader(f):
            nser, npts = int(r["n_series"]), int(r["n_points"])
            if 1 <= nser <= 8 and npts >= 20:
                rows.append(r)
    rows = [r for r in rows
            if r["chart_id"] not in exclude and r["arxiv_id"] not in excl_arxiv]
    if seed is not None:
        random.Random(seed).shuffle(rows)
        ordered = rows
    else:
        rows.sort(key=lambda r: r["chart_id"])
        step = max(1, len(rows) // (n * 3))
        ordered = rows[::step]
    seen, picked = set(), []
    for r in ordered:
        if r["arxiv_id"] in seen:
            continue
        seen.add(r["arxiv_id"])
        picked.append(r)
        if len(picked) >= n:
            break
    return picked


def _render_one(task):
    """Render a single bundle (top-level so it is picklable for a process Pool)."""
    r, extract_out, outdir = task
    cid, aid = r["chart_id"], r["arxiv_id"]
    jp = os.path.join(extract_out, aid, f"page{r['page']}_chart{r['chart']}.json")
    try:
        d = json.load(open(jp))
        # Style is now written by the EXTRACTOR into the chart JSON; the renderer
        # is a pure replayer and never re-parses the PDF for style.
        style = d["style"]
        bundle = os.path.join(outdir, cid)
        os.makedirs(bundle, exist_ok=True)
        with open(os.path.join(bundle, "chart.json"), "w") as f:
            json.dump(d, f, indent=2)
        crop_pdf = jp[:-5] + ".pdf"  # vector region crop alongside the json
        render_bundle(d, style, crop_pdf,
                      os.path.join(bundle, f"{cid}.png"),
                      os.path.join(bundle, f"{cid}_reconstruction.eps"),
                      os.path.join(bundle, f"{cid}_reconstruction.pdf"),
                      chart_json=jp)
        # Include the ORIGINAL lossless vector crop for reference (PDF + SVG).
        # No PDF->EPS converter is installed, so no .eps for the original; add
        # one here if a converter (pdftops/gs/mutool/inkscape) becomes available.
        if os.path.exists(crop_pdf):
            shutil.copy(crop_pdf, os.path.join(bundle, f"{cid}_original.pdf"))
        crop_svg = crop_pdf[:-4] + ".svg"
        if os.path.exists(crop_svg):
            shutil.copy(crop_svg, os.path.join(bundle, f"{cid}_original.svg"))
        return (cid, f"ok {cid} (nser={r['n_series']} npts={r['n_points']})")
    except Exception as e:
        return (None, f"ERR {cid}: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--outdir", default=None)
    ap.add_argument("--seed", type=int, default=None,
                    help="random-sample the pool (reproducibly) instead of stride")
    ap.add_argument("--exclude", default="",
                    help="comma-separated chart_ids to skip (their papers too)")
    ap.add_argument("--only", default="",
                    help="comma-separated chart_ids to render exactly (re-render)")
    ap.add_argument("--jobs", type=int, default=len(os.sched_getaffinity(0)),
                    help="parallel render workers (default: all available CPUs)")
    args = ap.parse_args()
    extract_out = os.path.join(args.root, "extract_out")
    outdir = args.outdir or os.path.join(args.root, "restyle_prototype")
    os.makedirs(outdir, exist_ok=True)

    # Charts the user removed from the set (too complex / test cases).
    removed = {"2012.11311_p3c1", "2105.00820_p18c3", "2106.05226_p18c1",
               "2107.08202_p8c1", "2111.03727_p23c3", "2112.07702_p25c3",
               "2201.09344_p9c1"}
    only = {c for c in args.only.split(",") if c}
    if only:
        with open(os.path.join(args.root, "figures_index.csv")) as f:
            picked = [r for r in csv.DictReader(f) if r["chart_id"] in only]
        args.n = len(picked)
        print(f"re-rendering {len(picked)} requested charts -> {outdir}", flush=True)
    else:
        exclude = removed | {c for c in args.exclude.split(",") if c}
        # oversample candidates so render failures (missing json / errors) still
        # leave n successful bundles; render until n succeed.
        picked = _select(args.root, args.n * 3, exclude=exclude, seed=args.seed)
        print(f"selected {len(picked)} candidates (excluded {len(exclude)}) -> {outdir}",
              flush=True)
    # Render bundles in parallel: each chart is independent (reads its own JSON +
    # crop, writes its own bundle dir). The per-chart cost is dominated by the
    # residual panel's re-parse + mathtext, so spreading charts across cores is a
    # near-linear speedup. We oversample candidates (non-only mode), so render all
    # then keep the first n successes in the original pool order.
    tasks = [(r, extract_out, outdir) for r in picked]
    jobs = max(1, min(args.jobs, len(tasks)))
    ok = 0
    if jobs == 1:
        results = [_render_one(t) for t in tasks]
    else:
        from multiprocessing import Pool
        with Pool(jobs) as pool:
            results = pool.map(_render_one, tasks)
    succeeded = []
    for cid_ok, msg in results:
        if cid_ok is not None:
            succeeded.append(cid_ok)
        print(f"  {msg}", flush=True)
    # Oversampling (non-only mode) can yield more than n successes; keep the first
    # n in pool order and drop the surplus bundle dirs so the output is exactly n.
    for extra in succeeded[args.n:]:
        shutil.rmtree(os.path.join(outdir, extra), ignore_errors=True)
    ok = min(len(succeeded), args.n)
    print(f"\nDONE: {ok}/{len(picked)} rendered -> {outdir} (jobs={jobs})")


if __name__ == "__main__":
    main()
