"""Chart *style* recovery: the top-level ``style`` block of a chart record.

This is rendering metadata (STYLE ONLY -- attributes used to redraw the extracted
data in the original chart's style), NOT extracted measurements. It is produced at
PARSE time by ``build_chart_style`` (wired in ``cli.parse_pdf``) so the chart JSON
is self-contained: a downstream renderer reads ``d["style"]`` and never re-opens
the source PDF to derive style.

The recovery functions match each series to the source vector path it was traced
from (width / dash / marker shape / connect), recover tick appearance, and recover
font family + sizes, legend layout, and in-graph annotations from the page's text
spans. The text recovery needs per-span font name + flags (bold/italic), which the
normalized ``TextSpan`` does not carry, so ``recover_text_style`` reads the fitz
``get_text("dict")`` once at parse time (the only extra fitz read; the renderer no
longer does any).
"""
from __future__ import annotations

import math
import re

import fitz

from . import primitives
from .font_recovery import FontDecoder, is_broken_text

_STYLE_NOTE = ("STYLE ONLY -- rendering attributes used to redraw the extracted "
               "data in the original chart's style; NOT extracted measurements.")
# Legend entries stacked in one column share a left edge to within this many
# points (real legends align to <1pt; allow slack). A larger spread means the
# matched spans are scattered (a false vertical-legend match), not a column.
_LEGEND_COL_TOL = 6.0


def _legend_left_aligned(matched: list) -> bool:
    """True when the matched legend spans share a left edge (one stacked column).

    A genuine vertical legend stacks its entries left-aligned; a sprawling false
    match (short labels catching scattered ticks across the plot) has left edges
    spread far apart. Used to exempt a real narrow-panel legend from the
    width-plausibility gate."""
    if len(matched) < 2:
        return False
    lefts = [s["bbox"][0] for s in matched]
    return (max(lefts) - min(lefts)) <= _LEGEND_COL_TOL


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
    extractor's often-wrong marker field): disk 'o', star '*', square 's',
    cross 'x', plus '+'."""
    pts = p.points
    if len(pts) < 3:
        return None
    # A cross/plus is an OPEN, low-2D-symmetry stroked glyph (two crossing
    # strokes) rather than a closed/filled square or triangle. The radius-CV
    # logic below would call it a square, so resolve it first via the extractor's
    # vertex-geometry classifier (4 distinct endpoints at bbox corners -> ×, at
    # edge midpoints -> +).
    shp = primitives.shape_of(p)
    if shp == "cross":
        return "x"
    if shp == "plus":
        return "+"
    if shp == "triangle":
        # Distinguish up (△ '^') from down (▽ 'v') by where the glyph's mass sits:
        # a filled up-triangle is widest at the BOTTOM so its centroid is in the
        # lower half (larger PDF y, which points down); a down-triangle's centroid
        # sits in the upper half (2504.02903_p11c3: CdTe '▽' was rendered '△').
        ys = [y for _, y in pts]
        ylo, yhi = min(ys), max(ys)
        yc = sum(ys) / len(ys)
        if yhi > ylo and (yc - ylo) / (yhi - ylo) < 0.5:
            return "v"      # mass toward the top -> apex points DOWN
        return "^"          # 3-corner glyph: was falling through to 's' (square)
    if shp == "diamond":
        return "D"          # 45°-rotated square
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


def _box_like(p):
    """Whether path ``p`` traces a rectangle OUTLINE (a legend frame / box), and
    if so whether its corners are rounded. Returns ``True`` (rounded), ``False``
    (sharp), or ``None`` when ``p`` is not box-like (e.g. a data curve that fills
    the bbox interior). A rectangle's vertices hug the bbox perimeter; a sharp box
    flattens to ~4-6 points, while a rounded (fancybox) frame draws its corner
    arcs as many points that still sit within a thin perimeter band -- so the
    point COUNT (not the geometry) distinguishes rounded from square."""
    pts = p.points
    if len(pts) < 4:
        return None
    bx0, by0, bx1, by1 = p.bbox
    bw, bh = bx1 - bx0, by1 - by0
    if bw < 8.0 or bh < 8.0:
        return None
    tol = max(2.5, 0.12 * min(bw, bh))
    near = sum(1 for x, y in pts
               if min(abs(x - bx0), abs(x - bx1)) < tol
               or min(abs(y - by0), abs(y - by1)) < tol)
    if near < 0.85 * len(pts):
        return None
    return len(pts) > 8


def match_series_styles(paths, region_bbox, series):
    """Per-series width/linestyle/marker, matched by GEOMETRY (not just colour).

    Each extracted series carries its pixel points, so we match it to the actual
    vector path it was traced from and read that path's width + dash pattern. This
    distinguishes same-colour series (e.g. a solid and a dashed black curve), which
    a colour-keyed lookup cannot. ``paths`` are the source page's vector paths
    (``model.Path``). Returns ``(per_series_styles, meta)``."""
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
        # Use the actual marker GLYPH paths (recognised shapes) for size AND
        # face/edge/width. Stray same-colour small paths (fit-line dashes, error-bar
        # caps, ticks, text) otherwise pollute these: they shrank markersize
        # (2002.02623_p25c2: 4.85pt -> 2.03pt) and made an OPEN marker look FILLED
        # (2503.07760_p4c1: an open '○' got a black face from stray filled paths).
        # Fall back to all small paths when no shape is recognised.
        glyph_paths = [p for p in small_paths if _marker_shape(p)] or small_paths
        msize = _median([max(p.bbox[2] - p.bbox[0], p.bbox[3] - p.bbox[1])
                         for p in glyph_paths])
        # CONNECT only on EVIDENCE that the original drew a line THROUGH the
        # markers. A marker series is joined iff some same-colour "big" path
        # actually THREADS the points -- i.e. (most of) the series' marker points
        # lie within a few px of that path. This restores a genuine line+marker
        # plot whose connector was suppressed at extraction (2005: 15/15 threaded)
        # while leaving pure scatter unconnected, where the only same-colour long
        # path is a separate fit/curve that MISSES the markers (2205, 2410: 0
        # threaded) or a model curve that grazes just a few (2102: 3/9).
        tol = max(4.0, 1.5 * (msize or 0.0))
        connect = _threads_markers(big, pts, tol)
        # Transparency: the traced path's stroke alpha (fall back to a same-colour
        # marker's fill alpha). None when fully opaque -> renderer leaves it solid.
        alpha = best.stroke_alpha
        if alpha is None and small_paths:
            alpha = next((p.fill_alpha for p in small_paths
                          if p.fill_alpha is not None), None)
        # Full marker styling -- FACE colour, EDGE colour and EDGE width are
        # independent (e.g. a red-filled circle with a black edge, or an open
        # circle = white/no face + coloured edge). Recover each from the marker
        # glyph paths so the reconstruction matches.
        def _modal(vals):
            return list(max(set(vals), key=vals.count)) if vals else None
        # Face vote INCLUDES None (an open marker has no fill): an OPEN series with
        # a couple of incidental filled glyphs (e.g. a filled legend sample) must
        # stay open by majority, not be flipped to filled (2503.07760_p4c1: 20 open
        # '○' + 2 filled glyphs were read as a black face). None wins -> open.
        _faces = [tuple(_round_color(p.fill)) if p.fill is not None else None
                  for p in glyph_paths]
        _mf = max(set(_faces), key=_faces.count) if _faces else None
        m_face = list(_mf) if _mf is not None else None
        m_edge = _modal([_round_color(p.stroke) for p in glyph_paths if p.stroke is not None])
        m_ew = _median([p.width for p in glyph_paths if p.width is not None])
        if m_face is not None and min(m_face) > 0.9:
            m_face = None  # white fill -> OPEN marker (renderer uses facecolor none)
        out.append({"width": best.width, "linestyle": ls,
                    "markersize": msize, "marker_shape": mshape,
                    "connect": connect, "alpha": alpha,
                    "marker_face": m_face, "marker_edge": m_edge,
                    "marker_edge_width": m_ew})

    # Axis frame/spine stroke width: dark, axis-aligned border lines near the
    # region edge, or the plot-frame rectangle. Sets spine + tick line weight.
    spine_w = []
    spine_cols = []
    for p in inreg:
        # A spine/frame is DARK (grey/black) or CHROMATIC (a coloured frame, any
        # brightness). Exclude only light-grey strokes (gridlines), so a bright
        # coloured axis is kept and its colour recovered.
        if p.stroke is None or p.width is None:
            continue
        if max(p.stroke) > 0.6 and (max(p.stroke) - min(p.stroke)) <= 0.12:
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
            spine_cols.append(_round_color(p.stroke))
    # The axis/frame colour (modal spine stroke); None when plain black -> the
    # renderer keeps matplotlib's default black axes.
    axis_color = None
    if spine_cols:
        modal = max(set(spine_cols), key=spine_cols.count)
        # Only a CHROMATIC frame counts as a coloured axis. A near-black / grey
        # spine (sat ~ 0) stays matplotlib-default black -> no cosmetic churn on
        # the many ordinary dark-grey axes; only genuine colour (e.g. a blue
        # frame) is recovered.
        if max(modal) - min(modal) > 0.12:
            axis_color = list(modal)
    # Legend frame: a box-like rectangle (sharp OR rounded corners) NARROWER than
    # the full plot frame, with a white-ish background -- either on the SAME path,
    # OR on a COINCIDENT sibling rectangle (papers commonly draw the legend as a
    # white FILL rect plus a separate BORDER-stroke rect, so neither single path
    # has both -- the 2009.07658 miss). The white background is the discriminator
    # vs gridlines. We recover the frame's STYLE (border colour/width, fill colour
    # and rounded-vs-square corners) so the renderer reproduces it instead of
    # drawing matplotlib's default light-grey fancybox.
    white_bgs = [p for p in inreg
                 if p.fill is not None and min(p.fill) >= 0.85
                 and _box_like(p) is not None]

    def _white_bg_for(b):
        for q in white_bgs:
            if all(abs(q.bbox[i] - b[i]) < 3.0 for i in range(4)):
                return q
        return None

    legend_frame = None
    for p in inreg:
        rounded = _box_like(p)
        if rounded is None or p.stroke is None:
            continue
        bw, bh = p.bbox[2] - p.bbox[0], p.bbox[3] - p.bbox[1]
        # narrower than the full frame (excludes the plot frame), not a sliver
        if not (0.08 * (x1 - x0) < bw < 0.7 * (x1 - x0)
                and 0.05 * (y1 - y0) < bh < 0.95 * (y1 - y0)):
            continue
        same_white = p.fill is not None and min(p.fill) >= 0.85
        bg = None if same_white else _white_bg_for(p.bbox)
        if not (same_white or bg is not None):
            continue
        face = p.fill if same_white else (bg.fill if bg else None)
        legend_frame = {
            "edge_color": [round(v, 3) for v in p.stroke],
            "face_color": [round(v, 3) for v in face] if face else None,
            "linewidth": round(p.width, 3) if p.width else None,
            "rounded": bool(rounded),
        }
        break
    meta = {"axis_linewidth": _median(spine_w),
            "axis_color": axis_color,
            "ticks": recover_tick_style(inreg, region_bbox),
            "legend_box": legend_frame is not None,
            "legend_frame": legend_frame}
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


def _span_color(c):
    """PyMuPDF dict-mode span color is a packed sRGB int. Return an (r,g,b)
    float tuple in 0..1, or None."""
    if c is None:
        return None
    if isinstance(c, (list, tuple)):
        try:
            return tuple(min(1.0, max(0.0, float(v))) for v in c[:3])
        except Exception:
            return None
    try:
        c = int(c)
    except Exception:
        return None
    return ((c >> 16 & 255) / 255.0, (c >> 8 & 255) / 255.0, (c & 255) / 255.0)


def _baseline(s):
    """Approximate text baseline of a span (bottom for horizontal text, using the
    writing direction so rotated lines compare along their own baseline axis)."""
    bb = s["bbox"]
    dx, dy = s.get("dir", (1.0, 0.0))
    if abs(dy) > 0.5:               # vertical text: baseline runs along x
        return bb[0] if dy < 0 else bb[2]
    return bb[3]                    # horizontal: bottom edge


def _reading_pos(s):
    """(along, across) position in the span's own writing direction: 'along' is
    the reading axis (x for horizontal text, y for vertical), 'across' is the
    line/baseline axis."""
    bb = s["bbox"]
    dx, dy = s.get("dir", (1.0, 0.0))
    if abs(dy) > 0.5:                       # vertical (rotated) text
        # reading runs along y; for dir (0,-1) text reads bottom->top
        along0, along1 = (-bb[3], -bb[1]) if dy < 0 else (bb[1], bb[3])
        across = (bb[0] + bb[2]) / 2
        return along0, along1, across
    return bb[0], bb[2], bb[3]              # horizontal: along x, baseline = bottom


def _group_spans(spans, base):
    """Group adjacent text spans that form ONE logical label.

    1) cluster spans into LINES by writing direction + baseline proximity
       (tolerant to sub/superscript vertical offset);
    2) within each line, sort along the reading axis and split where the
       inter-token gap is large relative to font size.
    Returns a list of groups (each a list of member spans in reading order).
    Conservative: only clearly-contiguous, same-baseline tokens merge.
    """
    bh = base or 8.0
    # --- step 1: line clustering (greedy by sorted baseline within same dir) ---
    lines = []
    for s in sorted(spans, key=lambda s: (s.get("dir", (1.0, 0.0)), _baseline(s))):
        d = s.get("dir", (1.0, 0.0))
        placed = False
        for ln in lines:
            if ln["dir"] != d:
                continue
            sz = max(s.get("size") or bh, ln["sz"], 1.0)
            if abs(_baseline(s) - ln["base"]) <= 0.7 * sz:
                ln["spans"].append(s)
                # track the line baseline as the MAX-size span's baseline (body text)
                if (s.get("size") or 0) >= ln["sz"]:
                    ln["base"], ln["sz"] = _baseline(s), s.get("size") or bh
                placed = True
                break
        if not placed:
            lines.append({"dir": d, "base": _baseline(s),
                          "sz": s.get("size") or bh, "spans": [s]})

    # --- step 2: split each line into groups by reading-axis gaps ---
    groups = []
    for ln in lines:
        ordered = sorted(ln["spans"], key=lambda s: _reading_pos(s)[0])
        cur = []
        prev_end = None
        for s in ordered:
            a0, a1, _ = _reading_pos(s)
            sz = max(s.get("size") or bh, 1.0)
            if cur and prev_end is not None and (a0 - prev_end) > 1.2 * sz:
                groups.append(cur)
                cur = []
            cur.append(s)
            prev_end = a1 if prev_end is None else max(prev_end, a1)
        if cur:
            groups.append(cur)
    return groups


def _join_group(group):
    """Concatenate a group's spans (in reading order) into one string. For
    HORIZONTAL text, mark sub/superscripts as inline mathtext (e.g. 'cm$^{-3}$',
    'E$_{g}$'). For rotated/vertical text (a y-title), the baseline test does not
    apply, so fall back to a plain reading-order join with word-gap spaces."""
    from .primitives import join_scripts as _join_scripts
    horizontal = all(abs((s.get("dir") or (1.0, 0.0))[1]) < 0.3 for s in group)
    if horizontal:
        items = [(s["text"], s.get("size"),
                  0.5 * (s["bbox"][1] + s["bbox"][3]), s["bbox"][0], s["bbox"][2])
                 for s in group]
        return _join_scripts(items)
    ordered = sorted(group, key=lambda s: _reading_pos(s)[0])
    parts = []
    prev_end = None
    for s in ordered:
        a0, a1, _ = _reading_pos(s)
        if prev_end is not None:
            sz = max(s.get("size") or 8.0, 1.0)
            if (a0 - prev_end) > 0.18 * sz:
                parts.append(" ")
        parts.append(s["text"])
        prev_end = a1 if prev_end is None else max(prev_end, a1)
    return "".join(parts)


def _title_span_match(sk, key):
    """True when span-norm ``sk`` should be taken as (part of) label-norm ``key``,
    for resolving a label's font size / weight / italic from its source span.

    Matches: exact; the whole label sitting INSIDE a bigger span (key in sk); or
    ``sk`` being a SUBSTANTIAL (>=4 char) fragment of a longer label. A 1-3 char
    fragment must NOT match a long multi-word title -- otherwise a stray legend
    letter ('S' in 'False', 'CTI' in 'Detection') steals the title's font size,
    inflating it (2004.06765_p10c6: titles came out ~1.7x too big)."""
    if not sk or not key:
        return False
    if sk == key or key in sk:
        return True
    return len(sk) >= 4 and sk in key


def _label_runs(group):
    """Per-token [text, italic] runs for a label whose spans MIX italic and roman
    (e.g. math var 'τ' + roman units ' (s)'), so the renderer can slant only the
    italic tokens. Returns None when the whole-label italic boolean already
    suffices: a uniform group (all/none italic), a scripted group (keep its
    sub/superscript markup), or fewer than two real text spans."""
    real = [s for s in group if not _is_symbol_font(s)]
    if len(real) < 2:
        return None
    flags = [_is_italic(s) for s in real]
    if all(flags) or not any(flags):
        return None
    if "$" in _join_group(group):
        return None
    ordered = sorted(real, key=lambda s: _reading_pos(s)[0])
    runs, prev_end, prev_ital = [], None, None
    for s in ordered:
        a0, a1, _ = _reading_pos(s)
        t = s["text"]
        if prev_end is not None and (a0 - prev_end) > 0.18 * max(s.get("size") or 8, 1):
            t = " " + t                       # restore inter-token space
        it = _is_italic(s)
        if runs and it == prev_ital:
            runs[-1][0] += t                  # merge same-style adjacent spans
        else:
            runs.append([t, it])
        prev_end = a1 if prev_end is None else max(prev_end, a1)
        prev_ital = it
    return runs


def _text_rotation(dxy):
    """Baseline orientation of a text span as matplotlib degrees (CCW, y-up).

    A label drawn DIAGONALLY (e.g. tilted along a curve, 2006.14257_p10c1) or
    vertically has a non-axis-aligned baseline ``dir``. PDF y points DOWN, so we
    negate dy for matplotlib's CCW-up convention. Near-horizontal snaps to 0 so
    ordinary labels are unaffected; diagonal and vertical (~90) are preserved."""
    dx, dy = dxy or (1.0, 0.0)
    rot = math.degrees(math.atan2(-dy, dx))
    return round(rot, 1) if abs(rot) > 3.0 else 0


def _group_color(group):
    """Dominant color of a group, weighted by character count."""
    counts = {}
    for s in group:
        c = s.get("color")
        if c is None:
            continue
        counts[c] = counts.get(c, 0) + max(1, len(s["text"]))
    if not counts:
        return None
    return max(counts, key=counts.get)


def _spans_in_region(fitz_page, region_bbox, margin=44.0):
    """Text spans whose center lies within region+/-margin, plus font weights.

    ``fitz_page`` is the source page; its ``get_text("dict")`` is read for the
    per-span font name + flags (bold/italic) that the normalized ``TextSpan`` does
    not carry. This is the only fitz read style recovery does."""
    x0, y0, x1, y1 = region_bbox
    spans, fonts = [], {}
    try:
        td = fitz_page.get_text("dict")
    except Exception:
        return spans, fonts
    decoder = FontDecoder(fitz_page.parent)
    for b in td.get("blocks", []):
        for ln in b.get("lines", []):
            d = ln.get("dir", (1.0, 0.0))
            for s in ln.get("spans", []):
                raw = s.get("text") or ""
                fn0 = s.get("font", "")
                # Match the broken-text recovery applied in pdf_vector so style
                # matching sees the same (recovered) text as the extracted record.
                if is_broken_text(raw, fn0):
                    rec = decoder.recover(fitz_page, [ord(c) for c in raw], fn0)
                    if rec:
                        raw = rec
                t = raw.strip()
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
                              "flags": s.get("flags", 0),
                              "color": _span_color(s.get("color"))})
                fn = s.get("font")
                if fn:
                    fonts[fn] = fonts.get(fn, 0) + len(t)
    return spans, fonts


def _is_bold(span):
    return bool(span.get("flags", 0) & 16) or "bold" in (span.get("font") or "").lower()


def _is_symbol_font(span):
    """Math-SYMBOL fonts (CMSY ≪/→, CMEX big-ops, AMS msam/msbm, rsfs, dingbats).
    They carry the PDF italic flag but are NOT italic text, so they must not vote
    in the italic decision (else a single ≪ flips a roman label to italic)."""
    name = (span.get("font") or "").lower()
    return any(k in name for k in (
        "cmsy", "cmex", "msam", "msbm", "rsfs", "symbol", "dingbat",
        "wingding", "marvosym", "esint", "stmary"))


def _is_italic(span):
    return bool(span.get("flags", 0) & 2) or any(
        k in (span.get("font") or "").lower() for k in ("ital", "oblique"))


def _content_scale(spans):
    """Per-chart CONTENT-TRANSFORM scale, from horizontal text box-height / size.

    A span's box-height / font-size equals the FONT's intrinsic
    (ascender - descender) -- a per-font constant, NOT a content transform. It runs
    ~1.0-1.4 for compact fonts but up to ~1.7-1.8 for tall-metric ones (DejaVuSans
    = 1.70), so any ratio in that band is just font metrics and snaps to 1.0 (a 1.70
    was inflating EVERY font ~1.7x on 2004.06765_p10c6). Only a figure genuinely
    drawn small then scaled up multiplies this into a much larger ratio (>=2); there
    we DO rescale fonts + line widths back to their true on-page size."""
    ratios = [(s["bbox"][3] - s["bbox"][1]) / s["size"]
              for s in spans
              if s.get("size") and s["size"] > 0.05
              and abs((s.get("dir") or (1.0, 0.0))[1]) < 0.3]
    if not ratios:
        return 1.0
    scale = min(50.0, max(0.2, sorted(ratios)[len(ratios) // 2]))
    return 1.0 if 0.7 <= scale < 2.0 else scale


def _is_latex_font(font_weights):
    """Dominant font looks like a TeX font (Computer Modern / Latin Modern / ...)."""
    if not font_weights:
        return False
    name = max(font_weights, key=font_weights.get).lower()
    return any(k in name for k in (
        "cmr", "cmmi", "cmsy", "cmss", "cmex", "cmbx", "cmti", "cmtt",
        "lmroman", "lmr", "latinmodern", "latin modern", "nimbusrom",
        "msam", "msbm", "rsfs", "eufm", "stix"))


def recover_text_style(fitz_page, region_bbox, axis_titles, series_labels,
                       title_text, tick_labels):
    """Recover font family/sizes, legend layout, and axis-label positions."""
    spans, fonts = _spans_in_region(fitz_page, region_bbox)
    if not spans:
        return None
    x0, y0, x1, y1 = region_bbox
    w, h = (x1 - x0) or 1.0, (y1 - y0) or 1.0

    scale = _content_scale(spans)

    # Bold is emphasis: if MOST region text reads bold it's a font-flag quirk, not
    # real emphasis, so bold detection is then unreliable and disabled.
    _bold_spans = sum(1 for s in spans if _is_bold(s))
    bold_reliable = _bold_spans <= 0.5 * len(spans)
    # Italic vote excludes symbol fonts (CMSY/CMEX/AMS): they carry the italic flag
    # but are not italic text, so counting them would spuriously trip the
    # font-quirk guard. The guard only fires when essentially EVERY real text span
    # is flagged italic (a genuine flag quirk); math-heavy panels that legitimately
    # have mostly-italic variables (e.g. 'ε/E') are no longer disabled wholesale.
    _txt_spans = [s for s in spans if not _is_symbol_font(s)]
    _ital_spans = sum(1 for s in _txt_spans if _is_italic(s))
    italic_reliable = (not _txt_spans) or _ital_spans <= 0.95 * len(_txt_spans)

    sizes = sorted(s["size"] for s in spans if s.get("size"))
    base = sizes[len(sizes) // 2] * scale if sizes else None

    def to_frac(cx, cy):
        return (cx - x0) / w, (y1 - cy) / h  # matplotlib axes fraction (y up)

    def find(text):
        key = _norm(text)
        if not key:
            return None
        for s in spans:
            if _title_span_match(_norm(s["text"]), key):
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
        return bool(s and _is_italic(s) and not _is_symbol_font(s)) and italic_reliable

    def find_group(text):
        """The group of spans (reading order) whose joined text best matches a
        label string -- so a multi-span title can be inspected token by token."""
        key = _norm(text)
        if not key:
            return None
        best, best_len = None, -1
        for grp in _group_spans(spans, base):
            gk = _norm(_join_group(grp))
            if gk and (gk == key or gk in key or key in gk):
                if len(gk) > best_len:
                    best, best_len = grp, len(gk)
        return best

    def runs_of(text):
        """Per-token italic runs for a label (see _label_runs); None when the
        whole-label italic boolean suffices or italic is unreliable."""
        if not italic_reliable:
            return None
        grp = find_group(text)
        return _label_runs(grp) if grp else None

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

    # legend: one matched span PER label (dedup -> can't exceed #series). PREFER
    # an EXACT normalized match: a short label like '3' (from '=3°') would loosely
    # match scattered tick spans ('300', '0.3') and blow the legend bbox up across
    # the whole plot (2009.07658 bug); the real legend entry matches exactly.
    labset = [_norm(l) for l in (series_labels or []) if l]
    matched, used = [], set()
    for lk in labset:
        exact = [(i, s) for i, s in enumerate(spans)
                 if i not in used and _norm(s["text"]) == lk]
        # FRAGMENT: a multi-glyph label ('θ =3°' -> norm 'θ3') is drawn as several
        # raw spans, none equal to the whole label -- match a span that is a piece
        # of it (norm in label). Prefer the LONGEST fragment so the legend's own
        # '10'/'20' span beats a stray single-digit tick ('0' is a sub-piece of
        # 'θ10'); the scatter gate below rejects the rest.
        frag = sorted(((i, s) for i, s in enumerate(spans)
                       if i not in used and _norm(s["text"]) and _norm(s["text"]) in lk),
                      key=lambda x: -len(_norm(x[1]["text"])))
        loose = [(i, s) for i, s in enumerate(spans)
                 if i not in used and _label_match(_norm(s["text"]), lk)]
        pick = exact[0] if exact else (frag[0] if frag else
                                       (loose[0] if loose else None))
        if pick:
            matched.append(pick[1])
            used.add(pick[0])
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
        # Plausibility gate: a VERTICAL legend is narrow. If a "vertical" match
        # sprawls across most of the plot WIDTH, it is unreliable (short labels
        # caught scattered ticks) -> emit NO layout so the renderer auto-places a
        # default-size legend. Height is NOT gated: a many-entry legend is
        # legitimately tall in a short panel. A genuinely horizontal legend is
        # wide by design, so it is exempt.
        #
        # EXCEPTION: a genuine vertical legend stacks its entries in one LEFT-
        # ALIGNED column (their left edges share an x), whereas a sprawling false
        # match catches ticks at scattered x. So when the matched entries are
        # left-aligned, keep the layout even past the width cap -- otherwise a
        # real legend in a NARROW/tall panel (wide labels vs a small plot width,
        # e.g. 2005.05829_p13c1 "1S/2S exciton" at wfrac 0.87) is wrongly dropped,
        # leaving the renderer to fall back to an oversized default-font legend.
        if not horizontal and wfrac > 0.6 and not _legend_left_aligned(matched):
            legend = None
        else:
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
    # Candidate annotation spans: not ticks/titles, not a matched legend label.
    # NOTE: the legend-box POSITION test is applied to whole GROUPS below, not to
    # individual spans -- testing per-span would clip a multi-token annotation that
    # happens to start inside the (generously padded) legend swatch region.
    cand = [s for s in spans
            if _norm(s["text"]) and _norm(s["text"]) not in consumed]
    # GROUP adjacent spans that form one logical label (annotation/equation),
    # so multi-token text is re-drawn as ONE coherent string in its own color
    # rather than scattered single-token pieces.
    annotations = []
    for grp in _group_spans(cand, base):
        text = _join_group(grp)
        if len(text) > 64:           # implausibly long -> likely not one label
            continue
        gx0 = min(s["bbox"][0] for s in grp)
        gx1 = max(s["bbox"][2] for s in grp)
        gy0 = min(s["bbox"][1] for s in grp)
        gy1 = max(s["bbox"][3] for s in grp)
        cx, cy = (gx0 + gx1) / 2, (gy0 + gy1) / 2
        if leg_box and leg_box[0] <= cx <= leg_box[2] and leg_box[1] <= cy <= leg_box[3]:
            continue  # the group's center is in the legend region -> a legend entry
        fx, fy = to_frac(cx, cy)
        if not (0.02 <= fx <= 0.98 and 0.02 <= fy <= 0.98):
            continue  # inside the plot only (skip margin captions/equations)
        gsz = sorted(s["size"] for s in grp if s.get("size"))
        msz = gsz[len(gsz) // 2] if gsz else None
        annotations.append({
            "text": text, "x": round(fx, 3), "y": round(fy, 3),
            "size": round(msz * scale, 2) if msz else None,
            "color": _group_color(grp),
            "rotation": _text_rotation(grp[0].get("dir")),
            "bold": bold_reliable and all(_is_bold(s) for s in grp)})

    tick_spans = [s for s in spans if _norm(s["text"]) in tickset]
    tick_bold = bool(tick_spans) and \
        sum(_is_bold(s) for s in tick_spans) >= len(tick_spans) / 2

    # FAKE-TITLE GUARD. The record's title may be a fragment of an in-plot caption
    # the extractor mis-promoted (e.g. "ns; Repetition Frequency = MHz"). A real
    # chart title is ONE coherent grouped span near the TOP-CENTER and ABOVE the
    # plot box. Accept the title only if it matches such a group; otherwise the
    # render drops it. Match = the title's normalized text equals the group's, or
    # is a clean substring of it (handles a title that is one line of a 2-line
    # caption) -- but a non-contiguous fragment (re-ordered tokens) will NOT match
    # any single group and is rejected.
    title_ok = False
    tkey = _norm(title_text)
    if tkey:
        for grp in _group_spans(spans, base):
            gtext = _join_group(grp)
            gkey = _norm(gtext)
            if not gkey:
                continue
            if not (tkey == gkey or tkey in gkey):
                continue
            gx0 = min(s["bbox"][0] for s in grp)
            gx1 = max(s["bbox"][2] for s in grp)
            gcx = (gx0 + gx1) / 2
            gtop = min(s["bbox"][1] for s in grp)
            fx = (gcx - x0) / w
            # top-center & at/above the plot box (small slack into the top margin)
            if 0.15 <= fx <= 0.85 and gtop <= y0 + 0.10 * h:
                title_ok = True
                break

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
        "x_title_runs": runs_of(axis_titles.get("x")),
        "y_title_font_size": size_of(axis_titles.get("y")),
        "y_title_bold": bold_of(axis_titles.get("y")),
        "y_title_italic": italic_of(axis_titles.get("y")),
        "y_title_runs": runs_of(axis_titles.get("y")),
        "title_italic": italic_of(title_text),
        "title_runs": runs_of(title_text),
        "x_label_pos": label_pos(axis_titles.get("x")),
        "y_label_pos": label_pos(axis_titles.get("y")),
        "show_legend": show_legend,
        "legend": legend,
        "annotations": annotations,  # in-graph text, NOT data or legend
        "title_ok": title_ok,        # title corroborated by a top-center span
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
    # STYLE = render-how only. data_range and per-tick pixel/value are DATA and
    # live authoritatively in the record's data section (d["x_axis"]["data_range"],
    # d["xticks"]/d["yticks"] with pixel+value); the renderer reads them from there
    # and joins these per-tick label strings by index. We keep only render-how:
    # scale (a rendering choice), title, tick_direction, tick_length, and the
    # displayed tick label text.
    return {
        "scale": ax.get("scale"),
        "title": _clean(ax.get("title")),
        "tick_direction": ax.get("tick_direction"),  # authoritative, from the parser
        "tick_length": ax.get("tick_length"),        # median tick length (pt)
        "ticks": [{"label": _clean(t.get("label"))} for t in (ticks or [])],
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
        # marker_shape normally comes from same-colour glyph geometry. But a
        # marker drawn as a coloured FILL blob with a separate edge OUTLINE
        # (e.g. red-filled square with a black square edge) is colour-matched to
        # the fill, whose flattened outline reads as a disk 'o' -- losing the real
        # square. The extractor merged that outline into the series and recorded
        # its true shape on ``marker``; prefer it when the geometry pass only saw
        # the generic disk so the marker renders as the shape the source drew.
        mshape = stl.get("marker_shape")
        emarker = s.get("marker")
        if mshape in (None, "o") and emarker and emarker != "o":
            mshape = emarker
        series.append({
            "label": _clean(s.get("label")),
            "color": col,
            "marker": s.get("marker"),
            "render_as": "scatter" if is_scatter else "line",
            "linewidth": lw_pt,
            "linestyle": ls,
            # marker diameter is page-space geometry (no scale); only for scatter
            "markersize": (round(md, 2) if (md and is_scatter) else None),
            "marker_shape": mshape,
            "connect": bool(stl.get("connect")),      # draw connecting line too
            # transparency (None = opaque); renderer passes to plot/scatter alpha
            "alpha": (round(stl["alpha"], 2)
                      if stl.get("alpha") is not None else None),
            # marker face / edge colour + edge width (independent; face None = open)
            "marker_face": stl.get("marker_face"),
            "marker_edge": stl.get("marker_edge"),
            "marker_edge_width": (round(stl["marker_edge_width"], 2)
                                  if stl.get("marker_edge_width") else None),
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
# Entry point: assemble the full top-level style block at parse time
# --------------------------------------------------------------------------

def build_chart_style(d: dict, page, fitz_page) -> dict:
    """Assemble the canonical top-level ``style`` block for one chart record.

    ``d`` is the canonical record dict (as written by ``io_store``); ``page`` is
    the parsed ``PageData`` (its ``.paths`` are the source vector paths);
    ``fitz_page`` is the source fitz page (read once for per-span font name +
    flags). Returns the same dict the renderer assembled previously."""
    rbbox = d["source"]["region_bbox"]
    series_styles, meta = match_series_styles(page.paths, rbbox, d.get("series", []))
    xa, ya = d.get("x_axis") or {}, d.get("y_axis") or {}
    ttl = d.get("title")
    ttl = ttl.get("text") if isinstance(ttl, dict) else ttl
    tick_labels = [t.get("label") for t in (d.get("xticks") or [])] + \
                  [t.get("label") for t in (d.get("yticks") or [])]
    text_style = recover_text_style(
        fitz_page, rbbox,
        {"x": xa.get("title"), "y": ya.get("title")},
        [s.get("label") for s in d.get("series", [])],
        ttl, tick_labels)
    style = build_style(d, series_styles)
    style["text"] = text_style
    # Fake-title guard: drop a title the text recovery could not corroborate
    # as a coherent top-center span (mis-promoted in-plot caption fragment).
    if style.get("title") and text_style and not text_style.get("title_ok"):
        style["title"] = None
    alw = meta.get("axis_linewidth")
    if alw:
        style["axis_linewidth"] = round(min(3.0, max(0.2, alw)), 2)
    style["ticks_style"] = meta.get("ticks")
    style["legend_box"] = meta.get("legend_box", False)
    style["legend_frame"] = meta.get("legend_frame")  # border/fill/corner style
    style["axis_color"] = meta.get("axis_color")  # coloured axes/frame (None=black)
    return style
