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

import fitz
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image

from pdf_chart2table import pdf_vector

_STYLE_NOTE = ("STYLE ONLY -- rendering attributes used to redraw the extracted "
               "data in the original chart's style; NOT extracted measurements.")

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


def _latexify(s):
    """Make a (possibly unicode/mangled) label safe + nice for usetex."""
    if not s:
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


# --------------------------------------------------------------------------
# Recover stroke width + dash per series colour from the source vector paths
# --------------------------------------------------------------------------

def _round_color(c, q: float = 0.06):
    return tuple(round(v / q) * q for v in c[:3])


def _parse_dashes(d):
    """fitz dash string (e.g. ``"[3 2] 0"``) -> matplotlib linestyle.

    Returns ``"-"`` for solid, else ``(offset, (on, off, ...))`` in points.
    """
    if not d:
        return "-"
    nums = [float(x) for x in re.findall(r"[-\d.]+", str(d))]
    if not nums:
        return "-"
    if len(nums) == 1:
        pat, off = nums, 0.0
    else:
        pat, off = nums[:-1], nums[-1]
    pat = [p for p in pat if p > 0]
    if not pat:
        return "-"
    return (float(off), tuple(pat))


def _median(v):
    return sorted(v)[len(v) // 2] if v else None


def _star_spikes(pts):
    """Count the regular radial SPIKES of a glyph outline (a star's tips).

    Radii are taken from the centroid, sorted by polar angle, then collapsed into
    a fixed number of angular SECTORS (max radius per sector). The local maxima of
    that smoothed profile are the spikes. Smoothing is the key: a noisy or
    doubled-arc circle (e.g. drawn as two overlapping 33-vertex loops) has radii
    that zig-zag at almost every vertex, which a raw coefficient-of-variation or
    raw sign-change count cannot distinguish from a star; binning by angle washes
    that jitter out (-> 0 spikes) while leaving a real star's few regular tips
    intact (a 5-point star -> 5 spikes). Also returns the radial amplitude
    (max-min)/mean so callers can require the spikes to be significant."""
    import math
    n = len(pts)
    if n < 6:
        return 0, 0.0
    cx = sum(x for x, _ in pts) / n
    cy = sum(y for _, y in pts) / n
    polar = sorted((math.atan2(y - cy, x - cx),
                    ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5) for x, y in pts)
    nbins = 24
    binmax = [0.0] * nbins
    for a, r in polar:
        b = int((a + math.pi) / (2 * math.pi) * nbins) % nbins
        if r > binmax[b]:
            binmax[b] = r
    rs = [v for v in binmax if v > 0]
    if len(rs) < 6:
        return 0, 0.0
    mean = sum(rs) / len(rs)
    if mean <= 0:
        return 0, 0.0
    amp = (max(rs) - min(rs)) / mean
    margin = 0.10 * mean
    m = len(rs)
    spikes = sum(1 for i in range(m)
                 if rs[i] > mean + margin
                 and rs[i] >= rs[(i - 1) % m] and rs[i] >= rs[(i + 1) % m])
    return spikes, amp


def _marker_shape(p):
    """Classify a small marker glyph by its outline geometry (overrides the
    extractor's often-wrong marker field): disk 'o', star '*', square 's'."""
    pts = p.points
    if len(pts) < 3:
        return None
    cx = sum(x for x, _ in pts) / len(pts)
    cy = sum(y for _, y in pts) / len(pts)
    rs = [((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 for x, y in pts]
    mean = sum(rs) / len(rs)
    if mean <= 0:
        return None
    cv = (sum((r - mean) ** 2 for r in rs) / len(rs)) ** 0.5 / mean
    n = len(pts)
    if p.fill is not None and n >= 10 and cv < 0.20:
        return "o"          # filled, smooth, ~constant radius -> disk
    # A star is REGULAR radial alternation: a small number of evenly spaced tips
    # (4-7 spikes). Raw cv mistakes a noisy/doubled-arc circle (high cv but no
    # regular tips) for a star; the angle-smoothed spike count separates them.
    spikes, amp = _star_spikes(pts)
    if 4 <= spikes <= 7 and amp > 0.35:
        return "*"          # regular alternating spikes -> star
    if cv <= 0.32 and n <= 6:
        return "s"          # few corners, low variation -> square/diamond
    return "o"


def _threads_markers(paths, pts, tol, frac=0.8):
    """Does any same-colour ``paths`` (the candidate connectors) thread the marker
    ``pts``? True when >= ``frac`` of the marker points lie within ``tol`` of a
    single path -- i.e. that path is the line drawn THROUGH the markers. A
    separate fit/curve that does not pass through the markers threads ~none, so it
    does not trigger a connection."""
    if not paths or not pts:
        return False
    need = max(2, int(round(frac * len(pts))))
    for p in paths:
        pp = p.points
        if len(pp) < 2:
            continue
        near = 0
        for sx, sy in pts:
            if min((sx - qx) ** 2 + (sy - qy) ** 2 for qx, qy in pp) <= tol * tol:
                near += 1
        if near >= need:
            return True
    return False


def recover_tick_style(paths, region_bbox):
    """Recover tick appearance from the original: direction (in/out), whether the
    top/right axes carry ticks, and whether minor ticks are present."""
    x0, y0, x1, y1 = region_bbox

    def side_ticks(kind, ref):
        out = []
        for p in paths:
            if p.stroke is None or (p.stroke and max(p.stroke) > 0.5):
                continue
            b = p.bbox
            bw, bh = b[2] - b[0], b[3] - b[1]
            if kind == "h":  # horizontal axis (bottom/top): short vertical strokes
                if bw < 2.0 and 1.0 < bh < 10.0 and min(abs(b[1] - ref),
                                                         abs(b[3] - ref)) < 3.0:
                    out.append(p)
            else:            # vertical axis (left/right): short horizontal strokes
                if bh < 2.0 and 1.0 < bw < 10.0 and min(abs(b[0] - ref),
                                                        abs(b[2] - ref)) < 3.0:
                    out.append(p)
        return out

    bottom = side_ticks("h", y1)
    top = side_ticks("h", y0)
    left = side_ticks("v", x0)
    right = side_ticks("v", x1)
    direction = None
    if bottom:
        up = sum(1 for p in bottom if p.bbox[1] < y1 - 1.0)
        direction = "in" if up >= len(bottom) / 2 else "out"
    elif left:
        rin = sum(1 for p in left if p.bbox[2] > x0 + 1.0)
        direction = "in" if rin >= len(left) / 2 else "out"
    return {"direction": direction,
            "top": len(top) >= 3, "right": len(right) >= 3,
            "minor": len(bottom) > 8 or len(left) > 8}


def match_series_styles(pdf, page0, region_bbox, series):
    """Per-series width/linestyle/marker, matched by GEOMETRY (not just colour).

    Each extracted series carries its pixel points, so we match it to the actual
    vector path it was traced from and read that path's width + dash pattern. This
    distinguishes same-colour series (e.g. a solid and a dashed black curve), which
    a colour-keyed lookup cannot. Returns a list aligned to ``series``."""
    try:
        paths = pdf_vector.load_pdf(pdf, [page0])[0].paths
    except Exception:
        return [{} for _ in series]
    x0, y0, x1, y1 = region_bbox
    diag = max(x1 - x0, y1 - y0)
    inreg = [p for p in paths
             if not (p.bbox[2] < x0 or p.bbox[0] > x1 or
                     p.bbox[3] < y0 or p.bbox[1] > y1)]
    out = []
    for s in series:
        col = s.get("color")
        pts = [(p["x_px"], p["y_px"]) for p in s.get("points", [])
               if p.get("x_px") is not None]
        if not col or not pts:
            out.append({})
            continue
        rcS = _round_color(col)
        samp = pts[::max(1, len(pts) // 16)]
        cands = []
        for p in inreg:
            cc = set()
            if p.stroke is not None:
                cc.add(_round_color(p.stroke))
            if p.fill is not None:
                cc.add(_round_color(p.fill))
            if rcS in cc:
                cands.append(p)
        if not cands:
            out.append({})
            continue

        def score(p):
            pp = p.points[::max(1, len(p.points) // 200)] or p.points
            tot = 0.0
            for sx, sy in samp:
                tot += min((sx - qx) ** 2 + (sy - qy) ** 2 for qx, qy in pp) ** 0.5
            return tot / len(samp)

        # prefer paths with real 2D extent (the traced curve, not a tick/marker)
        big = [p for p in cands
               if max(p.bbox[2] - p.bbox[0], p.bbox[3] - p.bbox[1]) > 0.15 * diag]
        best = min(big or cands, key=score)
        # fragment-drawn dash: the curve is many short same-colour segments rather
        # than one long path -> visually dashed even though each piece is solid.
        frag = sum(1 for p in big
                   if max(p.bbox[2]-p.bbox[0], p.bbox[3]-p.bbox[1]) < 0.5 * diag)
        longest = max((max(p.bbox[2]-p.bbox[0], p.bbox[3]-p.bbox[1]) for p in big),
                      default=0.0)
        ls = _parse_dashes(best.dashes)
        # only infer dashed from fragmentation when NO single path spans the curve
        # (a continuous solid line has one long path -> must stay solid).
        if ls == "-" and len(big) >= 5 and frag >= 5 and longest < 0.55 * diag:
            ls = (0.0, (3.0, 2.0))
        small_paths = [p for p in cands
                       if 0.2 < max(p.bbox[2]-p.bbox[0], p.bbox[3]-p.bbox[1]) <= 12.0]
        smalls = [max(p.bbox[2]-p.bbox[0], p.bbox[3]-p.bbox[1]) for p in small_paths]
        shapes = [s for s in (_marker_shape(p) for p in small_paths) if s]
        mshape = max(set(shapes), key=shapes.count) if shapes else None
        # CONNECT only on EVIDENCE that the original drew a line THROUGH the
        # markers. A marker series is joined iff some same-colour "big" path
        # actually THREADS the points -- i.e. (most of) the series' marker points
        # lie within a few px of that path. This restores a genuine line+marker
        # plot whose connector was suppressed at extraction (2005: 15/15 threaded)
        # while leaving pure scatter unconnected, where the only same-colour long
        # path is a separate fit/curve that MISSES the markers (2205, 2410: 0
        # threaded) or a model curve that grazes just a few (2102: 3/9).
        tol = max(4.0, 1.5 * (_median(smalls) or 0.0))
        connect = _threads_markers(big, pts, tol)
        out.append({"width": best.width, "linestyle": ls,
                    "markersize": _median(smalls), "marker_shape": mshape,
                    "connect": connect})

    # Axis frame/spine stroke width: dark, axis-aligned border lines near the
    # region edge, or the plot-frame rectangle. Sets spine + tick line weight.
    spine_w = []
    for p in inreg:
        if p.stroke is None or p.width is None or max(p.stroke) > 0.5:
            continue
        bw, bh = p.bbox[2] - p.bbox[0], p.bbox[3] - p.bbox[1]
        near = (abs(p.bbox[1] - y0) < 6 or abs(p.bbox[3] - y1) < 6 or
                abs(p.bbox[0] - x0) < 6 or abs(p.bbox[2] - x1) < 6)
        thin_long = ((bw > 0.5 * (x1 - x0) and bh < 3) or
                     (bh > 0.5 * (y1 - y0) and bw < 3))
        frame = (bw > 0.7 * (x1 - x0) and bh > 0.7 * (y1 - y0) and
                 len(p.points) <= 6)
        if (thin_long and near) or frame:
            spine_w.append(p.width)
    # Legend frame: a mid-sized, white-FILLED, stroked rectangle (not the plot
    # frame) -> the original legend is boxed. Matplotlib legend frames are often a
    # light-GRAY stroke (~0.8) with many points (rounded corners), so we accept
    # light strokes and any vertex count; the white fill is the discriminator that
    # keeps random gridline/curve strokes from being mistaken for a legend box.
    legend_box = False
    for p in inreg:
        if p.stroke is None or max(p.stroke) > 0.9:
            continue
        if p.fill is None or min(p.fill) < 0.85:   # white-ish background fill
            continue
        bw, bh = p.bbox[2] - p.bbox[0], p.bbox[3] - p.bbox[1]
        if (0.08 * (x1 - x0) < bw < 0.85 * (x1 - x0)
                and 0.03 * (y1 - y0) < bh < 0.6 * (y1 - y0)):
            legend_box = True
            break
    meta = {"axis_linewidth": _median(spine_w),
            "ticks": recover_tick_style(inreg, region_bbox),
            "legend_box": legend_box}
    return out, meta


# --------------------------------------------------------------------------
# Recover font + position metadata from the source page's text spans
# --------------------------------------------------------------------------

def _classify_family(font_weights):
    """font_weights: {font_name: total_chars} -> 'serif' | 'sans-serif'."""
    if not font_weights:
        return "serif"
    name = max(font_weights, key=font_weights.get).lower()
    sans = ("helvetica", "arial", "sans", "cmss", "phv", "calibri",
            "dejavusans", "verdana", "tahoma", "frutiger", "myriad")
    if any(h in name for h in sans):
        return "sans-serif"
    return "serif"  # Computer Modern / Times / Nimbus / unknown -> serif


def _norm(s):
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def _label_match(span_norm, label_norm):
    """Does a text span belong to a legend label? Exact match, or the span
    CONTAINS the whole label, or the span is a SUBSTANTIAL chunk of it (>=4
    chars). The length floor stops short tick labels like '0'/'10'/'x' from
    matching a longer entry such as 'X (x100)' (norm 'xx100' contains '10')."""
    if not span_norm or not label_norm:
        return False
    if span_norm == label_norm or label_norm in span_norm:
        return True
    return span_norm in label_norm and len(span_norm) >= 4


def _spans_in_region(pdf, page0, region_bbox, margin=44.0):
    """Text spans whose center lies within region+/-margin, plus font weights."""
    x0, y0, x1, y1 = region_bbox
    spans, fonts = [], {}
    try:
        with fitz.open(pdf) as doc:
            td = doc[page0].get_text("dict")
    except Exception:
        return spans, fonts
    for b in td.get("blocks", []):
        for ln in b.get("lines", []):
            d = ln.get("dir", (1.0, 0.0))
            for s in ln.get("spans", []):
                t = (s.get("text") or "").strip()
                if not t:
                    continue
                bb = s["bbox"]
                cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
                if cx < x0 - margin or cx > x1 + margin:
                    continue
                if cy < y0 - margin or cy > y1 + margin:
                    continue
                spans.append({"text": t, "size": s.get("size"), "bbox": bb,
                              "dir": d, "font": s.get("font", ""),
                              "flags": s.get("flags", 0)})
                fn = s.get("font")
                if fn:
                    fonts[fn] = fonts.get(fn, 0) + len(t)
    return spans, fonts


def _is_bold(span):
    return bool(span.get("flags", 0) & 16) or "bold" in (span.get("font") or "").lower()


def _is_italic(span):
    return bool(span.get("flags", 0) & 2) or any(
        k in (span.get("font") or "").lower() for k in ("ital", "oblique"))


def _is_latex_font(font_weights):
    """Dominant font looks like a TeX font (Computer Modern / Latin Modern / ...)."""
    if not font_weights:
        return False
    name = max(font_weights, key=font_weights.get).lower()
    return any(k in name for k in (
        "cmr", "cmmi", "cmsy", "cmss", "cmex", "cmbx", "cmti", "cmtt",
        "lmroman", "lmr", "latinmodern", "latin modern", "nimbusrom",
        "msam", "msbm", "rsfs", "eufm", "stix"))


def recover_text_style(pdf, page0, region_bbox, axis_titles, series_labels,
                       title_text, tick_labels):
    """Recover font family/sizes, legend layout, and axis-label positions."""
    spans, fonts = _spans_in_region(pdf, page0, region_bbox)
    if not spans:
        return None
    x0, y0, x1, y1 = region_bbox
    w, h = (x1 - x0) or 1.0, (y1 - y0) or 1.0

    # Per-chart CONTENT-TRANSFORM scale. PyMuPDF reports font `size` and stroke
    # `width` in pre-transform units while coordinates are page-space; for a
    # figure drawn small then scaled up (common), size << on-page height. The
    # ratio (span box-height / size) of horizontal text recovers that scale, so
    # we can rescale fonts AND line widths back to their true on-page size.
    ratios = [(s["bbox"][3] - s["bbox"][1]) / s["size"]
              for s in spans
              if s.get("size") and s["size"] > 0.05 and abs(s["dir"][1]) < 0.3]
    scale = sorted(ratios)[len(ratios) // 2] if ratios else 1.0
    scale = min(50.0, max(0.2, scale))
    # box-height/size is ~1.0-1.4 for normal (untransformed) text, so a ratio in
    # that band is NOT a real transform -- snap to 1.0 to avoid inflating fonts
    # (e.g. a spurious 1.12 made every label ~12 % too big). Only large ratios
    # (a figure genuinely drawn small then scaled up) are applied.
    if 0.7 <= scale <= 1.5:
        scale = 1.0

    # Bold is emphasis: if MOST region text reads bold it's a font-flag quirk, not
    # real emphasis, so bold detection is then unreliable and disabled.
    _bold_spans = sum(1 for s in spans if _is_bold(s))
    bold_reliable = _bold_spans <= 0.5 * len(spans)
    _ital_spans = sum(1 for s in spans if _is_italic(s))
    italic_reliable = _ital_spans <= 0.6 * len(spans)

    sizes = sorted(s["size"] for s in spans if s.get("size"))
    base = sizes[len(sizes) // 2] * scale if sizes else None

    def to_frac(cx, cy):
        return (cx - x0) / w, (y1 - cy) / h  # matplotlib axes fraction (y up)

    def find(text):
        key = _norm(text)
        if not key:
            return None
        for s in spans:
            sk = _norm(s["text"])
            if sk and (sk == key or sk in key or key in sk):
                return s
        return None

    def size_of(text):
        s = find(text)
        return round(s["size"] * scale, 2) if (s and s.get("size")) else None

    def bold_of(text):
        s = find(text)
        return bool(s and _is_bold(s)) and bold_reliable

    def italic_of(text):
        s = find(text)
        return bool(s and _is_italic(s)) and italic_reliable

    def label_pos(text):
        s = find(text)
        if not s:
            return None
        bb = s["bbox"]
        fx, fy = to_frac((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2)
        return [round(fx, 3), round(fy, 3)]

    # tick-label font size: spans whose text is one of the tick labels
    tickset = {_norm(t) for t in (tick_labels or []) if t}
    tsz = sorted(s["size"] for s in spans
                 if s.get("size") and _norm(s["text"]) in tickset)
    tick_size = (tsz[len(tsz) // 2] * scale) if tsz else base

    # legend: one matched span PER label (dedup -> can't exceed #series)
    labset = [_norm(l) for l in (series_labels or []) if l]
    matched, used = [], set()
    for lk in labset:
        for i, s in enumerate(spans):
            if i in used:
                continue
            if _label_match(_norm(s["text"]), lk):
                matched.append(s)
                used.add(i)
                break
    # The legend is only present on THIS panel if at least half its entry labels
    # are found as text inside the region; otherwise it lives on another panel
    # and must NOT be drawn on the reconstruction.
    show_legend = len(matched) >= max(1, (len(labset) + 1) // 2)
    legend = None
    if len(matched) >= 2:
        ys = sorted((s["bbox"][1] + s["bbox"][3]) / 2 for s in matched)
        rows = 1 + sum(1 for a, b in zip(ys, ys[1:]) if b - a > 1.4 * (base or 8))
        xext = max(s["bbox"][2] for s in matched) - min(s["bbox"][0] for s in matched)
        yext = max(s["bbox"][3] for s in matched) - min(s["bbox"][1] for s in matched)
        horizontal = rows == 1 and xext > 3 * max(yext, 1e-6)
        ncol = min(len(matched), len(labset)) if horizontal else 1
        mx0 = min(s["bbox"][0] for s in matched)
        mx1 = max(s["bbox"][2] for s in matched)
        my0 = min(s["bbox"][1] for s in matched)
        my1 = max(s["bbox"][3] for s in matched)
        cxf, cyf = to_frac((mx0 + mx1) / 2, (my0 + my1) / 2)
        # original legend extent in axes fraction (text + a swatch allowance to
        # the left), used to fit the reconstruction's legend size to the original.
        sw = 3.2 * (matched[0].get("size") or base or 8)
        wfrac = (mx1 - (mx0 - sw)) / w
        hfrac = (my1 - my0) / h
        legend = {
            "orientation": "horizontal" if horizontal else "vertical",
            "ncol": int(ncol),
            "anchor": [round(cxf, 3), round(cyf, 3)],
            "fontsize": (round(matched[0]["size"] * scale, 2)
                         if matched[0].get("size") else base),
            "bold": bold_reliable and any(_is_bold(s) for s in matched),
            "w_frac": round(wfrac, 4), "h_frac": round(hfrac, 4),
        }

    # In-graph text ANNOTATIONS: spans inside the plot box that are not ticks,
    # axis/chart titles, or legend entries (e.g. "B = 620 mT", "T = 2 K", "x10^5",
    # "(a)"). Recovered as text (clearly NOT data or legend) and re-drawn in place.
    # Legend text must be excluded or it draws twice (once as legend, once here),
    # so we exclude both (a) any span matching a series label and (b) any span
    # inside the legend's bounding region (catches multi-span / mangled entries
    # the per-label match missed).
    # Grow the legend to its full extent: a label like "X (x100)" is drawn as
    # several spans, but only one matched the series label. Pull in spans on the
    # same row, just right of a matched span, so the WHOLE entry is treated as
    # legend (not re-drawn as an annotation).
    bh = base or 8
    leg_spans = list(matched)
    for s in spans:
        if any(s is m for m in matched):
            continue
        scy = (s["bbox"][1] + s["bbox"][3]) / 2
        for m in matched:
            mcy = (m["bbox"][1] + m["bbox"][3]) / 2
            if (abs(scy - mcy) < 0.8 * bh and
                    m["bbox"][0] - 2 <= s["bbox"][0] < m["bbox"][2] + 14 * bh):
                leg_spans.append(s)
                break
    consumed = set(tickset)
    consumed.update(_norm(s["text"]) for s in leg_spans)
    consumed.update(_norm(l) for l in (series_labels or []) if _norm(l))
    for tt in (axis_titles.get("x"), axis_titles.get("y"), title_text):
        if _norm(tt):
            consumed.add(_norm(tt))
    leg_box = None
    if leg_spans:
        pad = 2.0 * bh
        leg_box = (min(s["bbox"][0] for s in leg_spans) - 7.0 * bh,  # swatch
                   min(s["bbox"][1] for s in leg_spans) - pad,
                   max(s["bbox"][2] for s in leg_spans) + pad,
                   max(s["bbox"][3] for s in leg_spans) + pad)
    annotations = []
    for s in spans:
        nk = _norm(s["text"])
        if not nk or nk in consumed or len(s["text"]) > 24:
            continue
        bb = s["bbox"]
        cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
        if leg_box and leg_box[0] <= cx <= leg_box[2] and leg_box[1] <= cy <= leg_box[3]:
            continue  # inside the legend region -> it's a legend entry, not an annotation
        fx, fy = to_frac(cx, cy)
        if not (0.02 <= fx <= 0.98 and 0.02 <= fy <= 0.98):
            continue  # inside the plot only (skip margin captions/equations)
        annotations.append({
            "text": s["text"], "x": round(fx, 3), "y": round(fy, 3),
            "size": round(s["size"] * scale, 2) if s.get("size") else None,
            "bold": bold_reliable and _is_bold(s)})

    tick_spans = [s for s in spans if _norm(s["text"]) in tickset]
    tick_bold = bool(tick_spans) and \
        sum(_is_bold(s) for s in tick_spans) >= len(tick_spans) / 2

    return {
        "_note": "STYLE ONLY -- font + position recovered from source text spans.",
        "content_scale": round(scale, 2),  # also applied to line widths
        "latex_like": _is_latex_font(fonts),
        "font_family": _classify_family(fonts),
        "base_font_size": round(base, 2) if base else None,
        "tick_font_size": round(tick_size, 2) if tick_size else None,
        "tick_bold": tick_bold,
        "title_font_size": size_of(title_text),
        "title_bold": bold_of(title_text),
        "x_title_font_size": size_of(axis_titles.get("x")),
        "x_title_bold": bold_of(axis_titles.get("x")),
        "x_title_italic": italic_of(axis_titles.get("x")),
        "y_title_font_size": size_of(axis_titles.get("y")),
        "y_title_bold": bold_of(axis_titles.get("y")),
        "y_title_italic": italic_of(axis_titles.get("y")),
        "title_italic": italic_of(title_text),
        "x_label_pos": label_pos(axis_titles.get("x")),
        "y_label_pos": label_pos(axis_titles.get("y")),
        "show_legend": show_legend,
        "legend": legend,
        "annotations": annotations,  # in-graph text, NOT data or legend
    }


# --------------------------------------------------------------------------
# Style block
# --------------------------------------------------------------------------

def _clean(t):
    """Drop non-printable / missing-glyph control chars that render as boxes."""
    if not t:
        return t
    s = "".join(ch for ch in t if ch.isprintable())
    return s.strip() or None


def _axis_style(ax, ticks):
    if not ax:
        return None
    return {
        "scale": ax.get("scale"),
        "title": _clean(ax.get("title")),
        "data_range": ax.get("data_range"),
        "tick_direction": ax.get("tick_direction"),  # authoritative, from the parser
        "tick_length": ax.get("tick_length"),        # median tick length (pt)
        "ticks": [{"pixel": t.get("pixel"), "value": t.get("value"),
                   "label": _clean(t.get("label"))} for t in (ticks or [])],
    }


def build_style(d: dict, series_styles: list) -> dict:
    rb = d["source"]["region_bbox"]
    w, h = rb[2] - rb[0], rb[3] - rb[1]
    series = []
    for s, stl in zip(d.get("series", []), series_styles):
        col = s.get("color")
        lw = stl.get("width")
        ls = stl.get("linestyle", "-")
        md = stl.get("markersize")
        is_scatter = bool(s.get("marker"))
        # stroke width is page-space geometry (like coords/marker size), NOT
        # pre-transform like font size -> use as-is, do not apply the scale.
        lw_pt = round(min(6.0, max(0.3, lw)), 2) if lw else None
        series.append({
            "label": _clean(s.get("label")),
            "color": col,
            "marker": s.get("marker"),
            "render_as": "scatter" if is_scatter else "line",
            "linewidth": lw_pt,
            "linestyle": ls,
            # marker diameter is page-space geometry (no scale); only for scatter
            "markersize": (round(md, 2) if (md and is_scatter) else None),
            "marker_shape": stl.get("marker_shape"),  # from glyph geometry
            "connect": bool(stl.get("connect")),      # draw connecting line too
        })
    title = d.get("title")
    title = title.get("text") if isinstance(title, dict) else title
    title = _clean(title)
    return {
        "_note": _STYLE_NOTE,
        "aspect_ratio": (w / h) if h else None,
        "title": title,
        "x_axis": _axis_style(d.get("x_axis"), d.get("xticks")),
        "y_axis": _axis_style(d.get("y_axis"), d.get("yticks")),
        "series": series,
        "grid": d.get("grid"),  # background grid style (from the extractor)
    }


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


def _replot(ax, record, style, tex=False):
    """Draw the extracted data into ``ax`` in the original's style."""
    L = _latexify if tex else (lambda x: x)
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
        if st.get("render_as") == "scatter":
            mk = st.get("marker_shape") or st.get("marker") or "o"  # geometry wins
            md = st.get("markersize")  # recovered glyph diameter in points
            if md:
                sz = max(2.0, min(300.0, md * md))  # scatter s = (diameter)^2
            else:
                sz = {"*": 14, "^": 12, "x": 10}.get(mk, 9)
            # line+marker series: draw the connecting line first, then the markers.
            # Points are emitted in TRUE draw order, so plot as-is (do NOT re-sort
            # by x -- that scrambles sideways/folded curves).
            if st.get("connect"):
                ax.plot(xs, ys, color=col,
                        linewidth=st.get("linewidth") or 0.8, linestyle=ls, zorder=1)
            ax.scatter(xs, ys, s=sz, color=col, label=lab, marker=mk, zorder=2)
        else:
            ax.plot(xs, ys, color=col,
                    label=lab, linewidth=st.get("linewidth") or 1.2, linestyle=ls)

    xa, ya = style["x_axis"], style["y_axis"]
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

    def _fs(v):  # accept a recovered (scale-corrected) point size only if sane
        return v if (font_ok and v and 3 <= v <= 44) else None

    def _w(flag):
        return "bold" if flag else "normal"

    def _i(flag):
        return "italic" if flag else "normal"

    def _plain_linear(axis_obj, scale):
        # force plain integers/decimals on linear axes (no 7x10^2 / offset text)
        if scale != "log":
            from matplotlib.ticker import ScalarFormatter
            fmt = ScalarFormatter(useOffset=False)
            fmt.set_scientific(False)
            axis_obj.set_major_formatter(fmt)

    if xa:
        _apply_ticks(ax, "x", xa.get("ticks"), x_scale, xa.get("data_range"))
        _plain_linear(ax.xaxis, x_scale)
        ax.set_xlabel(L(xa.get("title") or ""),
                      fontsize=_fs(txt.get("x_title_font_size")) or _fs(base_fs),
                      fontweight=_w(txt.get("x_title_bold")),
                      fontstyle=_i(txt.get("x_title_italic")))
        p = txt.get("x_label_pos")  # only a plausibly-placed x label
        if p and 0.2 <= p[0] <= 0.8 and -0.35 <= p[1] <= 0.02:
            ax.xaxis.set_label_coords(p[0], p[1])
    if ya:
        _apply_ticks(ax, "y", ya.get("ticks"), y_scale, ya.get("data_range"))
        _plain_linear(ax.yaxis, y_scale)
        ax.set_ylabel(L(ya.get("title") or ""),
                      fontsize=_fs(txt.get("y_title_font_size")) or _fs(base_fs),
                      fontweight=_w(txt.get("y_title_bold")),
                      fontstyle=_i(txt.get("y_title_italic")))
        p = txt.get("y_label_pos")
        if p and -0.35 <= p[0] <= 0.02 and 0.2 <= p[1] <= 0.8:
            ax.yaxis.set_label_coords(p[0], p[1])
    if _fs(txt.get("tick_font_size")):
        ax.tick_params(labelsize=txt["tick_font_size"])
    # match the original axis frame / tick line weight
    alw = style.get("axis_linewidth")
    if alw:
        for sp in ax.spines.values():
            sp.set_linewidth(alw)
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
    # tick LENGTH (points): majors at recovered length, minors a bit shorter
    if x_len:
        ax.tick_params(axis="x", which="major", length=round(x_len, 2))
        ax.tick_params(axis="x", which="minor", length=round(0.6 * x_len, 2))
    if y_len:
        ax.tick_params(axis="y", which="major", length=round(y_len, 2))
        ax.tick_params(axis="y", which="minor", length=round(0.6 * y_len, 2))
    if txt.get("tick_bold"):
        for lb in ax.get_xticklabels() + ax.get_yticklabels():
            lb.set_fontweight("bold")
    # in-graph text annotations (recovered text that is NOT data or legend)
    for a in (txt.get("annotations") or []):
        ax.text(a["x"], a["y"], L(a.get("text") or ""), transform=ax.transAxes,
                ha="center", va="center",
                fontsize=_fs(a.get("size")) or _fs(base_fs),
                fontweight="bold" if a.get("bold") else "normal")
    # Background grid (from the extractor): drawn behind the data, aligned with
    # the ticks, in the recovered grey colour.
    g = style.get("grid")
    if g:
        axis = ("both" if (g.get("x") and g.get("y"))
                else ("x" if g.get("x") else "y"))
        gls = ":" if g.get("dashes") else "-"
        ax.grid(True, axis=axis, which="major", zorder=0, linestyle=gls,
                color=_color(g.get("color")) or "0.85",
                linewidth=g.get("linewidth") or 0.5)
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
              "borderpad": 0.3, "labelspacing": 0.3, "handlelength": 1.4,
              "handletextpad": 0.4, "columnspacing": 1.0, "borderaxespad": 0.3}
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
        legend_obj = ax.legend(**kw)
        # FIT the legend size to the original: measure the rendered legend width
        # in axes fraction and rescale the font so it matches the recovered extent.
        tw = (leg or {}).get("w_frac")
        if legend_obj is not None and tw and tw > 0:
            ax.figure.canvas.draw()
            bb = legend_obj.get_window_extent()
            inv = ax.transAxes.inverted()
            (p0x, _), (p1x, _) = inv.transform((bb.x0, bb.y0)), inv.transform((bb.x1, bb.y1))
            cur_w = abs(p1x - p0x)
            if cur_w > 1e-3:
                r = max(0.55, min(1.6, tw / cur_w))
                if abs(r - 1.0) > 0.08:  # re-create at fitted size
                    if "prop" in kw:
                        kw["prop"] = dict(kw["prop"], size=kw["prop"]["size"] * r)
                    else:
                        kw["fontsize"] = (kw.get("fontsize") or 8) * r
                    legend_obj.remove()
                    ax.legend(**kw)


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


def _box(ax, record, style):
    _replot(ax, record, style)
    ar = style.get("aspect_ratio")
    if ar and ar > 0:
        try:
            ax.set_box_aspect(1.0 / ar)
        except Exception:
            pass


def _recon_figure(record, style, tex=False):
    """A standalone reconstruction figure sized to the original's PHYSICAL extent.

    The data axes is set to the region's point dimensions (1 pt = 1/72 in), so the
    recovered point-based font sizes and stroke widths render at the same visual
    scale as the original vector crop -- making a side-by-side directly comparable.
    """
    bb = record["source"]["region_bbox"]
    w = max(bb[2] - bb[0], 1.0)
    h = max(bb[3] - bb[1], 1.0)
    lm = max(46.0, 0.18 * w)  # room for y ticks + y title
    bm = max(38.0, 0.16 * h)  # room for x ticks + x title
    tm, rm = 18.0, 16.0
    fw, fh = lm + w + rm, tm + h + bm
    fig = plt.figure(figsize=(fw / 72.0, fh / 72.0))
    ax = fig.add_axes([lm / fw, bm / fh, w / fw, h / fh])
    _replot(ax, record, style, tex=tex)
    title = style.get("title")
    if title:
        t = style.get("text") or {}
        ax.set_title(_latexify(title) if tex else title,
                     fontsize=t.get("title_font_size"),
                     fontweight="bold" if t.get("title_bold") else "normal")
    return fig


def _compose_pdf(crop_pdf, recon_pdf, out_pdf):
    """Side-by-side PDF: original VECTOR crop | VECTOR reconstruction (no raster)."""
    cap = 14.0  # caption band height (points)
    rec = fitz.open(recon_pdf)
    r1 = rec[0].rect
    crop = fitz.open(crop_pdf) if (crop_pdf and os.path.exists(crop_pdf)) else None
    r0 = crop[0].rect if crop else fitz.Rect(0, 0, r1.width, r1.height)
    gap = 18.0
    # Place BOTH panels at their NATURAL size (no scaling): the plot area is the
    # region's W x H in both the crop and the reconstruction, so leaving them
    # unscaled makes the two graphs the SAME physical size. The crop's region
    # margin (_CROP_MARGIN=18pt) equals the recon's top margin, so top-aligning
    # the pages also lines the plot areas up vertically.
    H = cap + max(r0.height, r1.height)
    W = r0.width + gap + r1.width
    out = fitz.open()
    pg = out.new_page(width=W, height=H)
    if crop:
        pg.show_pdf_page(fitz.Rect(0, cap, r0.width, cap + r0.height), crop, 0)
    x1 = r0.width + gap
    pg.show_pdf_page(fitz.Rect(x1, cap, x1 + r1.width, cap + r1.height), rec, 0)
    pg.insert_text((4, 11), "original", fontsize=9)
    pg.insert_text((x1 + 4, 11), "reconstruction", fontsize=9)
    out.save(out_pdf)
    out.close(); rec.close()
    if crop:
        crop.close()


def render_bundle(record, style, crop_pdf, out_png, out_eps, out_pdf=None):
    arr, clip, dpi = _original_image(record)
    txt = style.get("text") or {}
    title = style.get("title")
    tfs = txt.get("title_font_size")
    rc = {}
    if txt.get("font_family"):
        rc["font.family"] = txt["font_family"]
    base = txt.get("base_font_size")
    if base and 3 <= base <= 40:  # scale-corrected; final plausibility guard
        rc["font.size"] = base

    def overlay(ax):
        s = dpi / 72.0
        for ser in record["series"]:
            xs = [(p["x_px"] - clip.x0) * s for p in ser["points"]]
            ys = [(p["y_px"] - clip.y0) * s for p in ser["points"]]
            ax.scatter(xs, ys, s=10, facecolors="none", edgecolors="red",
                       linewidths=0.6)

    with plt.rc_context(rc):
        # PNG: 3-panel raster overview (original | original+pixels | reconstruction)
        fig, (a0, a1, a2) = plt.subplots(1, 3, figsize=(17, 5))
        a0.imshow(arr); a0.set_title("original"); a0.axis("off")
        a1.imshow(arr); a1.set_title("original + extracted pixels"); a1.axis("off")
        overlay(a1)
        a2.set_title("reconstructed (original style)")
        _box(a2, record, style)
        if title:
            fig.suptitle(title, fontsize=tfs,
                         fontweight="bold" if txt.get("title_bold") else "normal")
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
            try:
                _compose_pdf(crop_pdf, tmp, out_pdf)
            finally:
                os.remove(tmp)


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
    ok = 0
    for r in picked:
        if ok >= args.n:
            break
        cid, aid = r["chart_id"], r["arxiv_id"]
        jp = os.path.join(extract_out, aid,
                          f"page{r['page']}_chart{r['chart']}.json")
        try:
            d = json.load(open(jp))
            pdf, page0 = d["source"]["pdf"], d["source"]["page"]
            rbbox = d["source"]["region_bbox"]
            series_styles, meta = match_series_styles(pdf, page0, rbbox,
                                                       d.get("series", []))
            xa, ya = d.get("x_axis") or {}, d.get("y_axis") or {}
            ttl = d.get("title")
            ttl = ttl.get("text") if isinstance(ttl, dict) else ttl
            tick_labels = [t.get("label") for t in (d.get("xticks") or [])] + \
                          [t.get("label") for t in (d.get("yticks") or [])]
            text_style = recover_text_style(
                pdf, page0, rbbox,
                {"x": xa.get("title"), "y": ya.get("title")},
                [s.get("label") for s in d.get("series", [])],
                ttl, tick_labels)
            style = build_style(d, series_styles)
            style["text"] = text_style
            alw = meta.get("axis_linewidth")
            if alw:
                style["axis_linewidth"] = round(min(3.0, max(0.2, alw)), 2)
            style["ticks_style"] = meta.get("ticks")
            style["legend_box"] = meta.get("legend_box", False)
            d["style"] = style  # augment the SAME record, clearly labelled
            bundle = os.path.join(outdir, cid)
            os.makedirs(bundle, exist_ok=True)
            with open(os.path.join(bundle, "chart.json"), "w") as f:
                json.dump(d, f, indent=2)
            crop_pdf = jp[:-5] + ".pdf"  # vector region crop alongside the json
            render_bundle(d, style, crop_pdf,
                          os.path.join(bundle, f"{cid}.png"),
                          os.path.join(bundle, f"{cid}_reconstruction.eps"),
                          os.path.join(bundle, f"{cid}_reconstruction.pdf"))
            ok += 1
            print(f"  ok {cid} (nser={r['n_series']} npts={r['n_points']})",
                  flush=True)
        except Exception as e:
            print(f"  ERR {cid}: {e}", flush=True)
    print(f"\nDONE: {ok}/{len(picked)} rendered -> {outdir}")


if __name__ == "__main__":
    main()
