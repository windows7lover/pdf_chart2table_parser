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
import numpy as np

from . import primitives
from .font_recovery import FontDecoder, is_broken_text

_STYLE_NOTE = ("STYLE ONLY -- rendering attributes used to redraw the extracted "
               "data in the original chart's style; NOT extracted measurements.")
# Legend entries stacked in one column share a left edge to within this many
# points (real legends align to <1pt; allow slack). A larger spread means the
# matched spans are scattered (a false vertical-legend match), not a column.
_LEGEND_COL_TOL = 6.0
# Typical text box-height / font-size for a normal (un-transformed) font. Used to
# divide the intrinsic font metric out of a measured content-transform ratio so a
# scaled-up figure's fonts/line widths are not over-inflated (see _content_scale).
_FONT_BOX_METRIC = 1.36


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


def _asterisk_spokes(p):
    """Number of radiating spokes of an asterisk glyph (already shape-classified as
    an asterisk by ``primitives._is_asterisk``). The flattened outline visits each
    spoke TIP -- a vertex near the bbox boundary -- so the spoke count is the number
    of DISTINCT angular directions of those tips from the centre. Tips within ~25°
    of each other are the same spoke (the pen returns to centre between strokes, so
    each tip may appear once). Returns the count (e.g. 8 for four crossing strokes:
    vertical + horizontal + two diagonals)."""
    import math
    pts = list(dict.fromkeys(p.points))
    cx = sum(x for x, _ in pts) / len(pts)
    cy = sum(y for _, y in pts) / len(pts)
    angs = sorted(math.degrees(math.atan2(y - cy, x - cx)) % 360.0
                  for x, y in pts
                  if (x - cx) ** 2 + (y - cy) ** 2 > 0)
    if not angs:
        return 0
    spokes = 1
    for i in range(1, len(angs)):
        if angs[i] - angs[i - 1] > 25.0:
            spokes += 1
    # Wrap-around: the last and first tip may be the same spoke straddling 0/360.
    if spokes > 1 and (angs[0] + 360.0 - angs[-1]) <= 25.0:
        spokes -= 1
    return spokes


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
        # A triangle marker points one of four ways. The TWO base corners share an
        # edge of the bbox (a flat side); the lone APEX sits on the opposite edge.
        # Decide the axis the flat base lies along, then the apex direction along
        # the perpendicular axis. PDF y points DOWN, so a glyph whose centroid sits
        # in the lower half (larger y) is widest at the bottom -> apex UP.
        uniq = list(dict.fromkeys(pts))[:3]
        xs = [x for x, _ in uniq]
        ys = [y for _, y in uniq]
        xlo, xhi = min(xs), max(xs)
        ylo, yhi = min(ys), max(ys)
        bw, bh = xhi - xlo, yhi - ylo
        # The base is the pair of corners sharing a coordinate. If two corners
        # share an x (a VERTICAL base) the apex points left/right; if two share a
        # y (a HORIZONTAL base) it points up/down. Use the centroid offset from the
        # bbox centre along each axis: it leans toward the apex (away from the
        # 2-corner base), and the larger lean names the pointing axis.
        if bw > 0 and bh > 0:
            cx = sum(xs) / 3.0
            cy = sum(ys) / 3.0
            dx = (cx - 0.5 * (xlo + xhi)) / bw    # >0 -> centroid right of centre
            dy = (cy - 0.5 * (ylo + yhi)) / bh
            if abs(dx) > abs(dy):
                # centroid leans toward the 2-corner base; apex is the opposite way
                # (2004.01004_p6c3: V_ds-after right-triangle '▷' was rendered '^').
                return "<" if dx > 0 else ">"
        # HORIZONTAL base (or ambiguous): up vs down. Centroid in the lower half
        # (larger PDF y) -> widest at the bottom -> apex UP (2504.02903_p11c3:
        # CdTe '▽' was rendered '△').
        yc = sum(y for _, y in pts) / len(pts)
        if yhi > ylo and (yc - ylo) / (yhi - ylo) < 0.5:
            return "v"      # mass toward the top -> apex points DOWN
        return "^"          # 3-corner glyph: was falling through to 's' (square)
    if shp == "diamond":
        # A regular diamond ('D', square bbox) vs a THIN diamond ('d', a tall
        # narrow rhombus). matplotlib 'd' is ~0.6x as wide as tall, so a glyph
        # whose bbox is clearly taller than wide is the thin variant
        # (2004.01004_p6c3: an open 'd' was rendered as the fat 'D').
        bw = p.bbox[2] - p.bbox[0]
        bh = p.bbox[3] - p.bbox[1]
        if bw > 0 and bh / bw >= 1.25:
            return "d"      # taller-than-wide rhombus -> thin diamond
        return "D"          # 45°-rotated square
    # An ASTERISK ('*' as drawn by MATLAB/many tools) is an OPEN glyph of several
    # straight strokes radiating through a common centre, with NO enclosed fill --
    # geometrically distinct from matplotlib's '*' (a FILLED 5-point star). Map it
    # to the tuple marker (numsides, 2, 0), matplotlib's unfilled asterisk, so the
    # spoke count is faithful (2004.01004_p6c3: the before-correction series are
    # 8-spoke asterisks rendered as filled stars). shape_of lumps it under "star".
    if shp == "star" and p.fill is None and primitives._is_asterisk(p):
        n_spokes = _asterisk_spokes(p)
        if n_spokes >= 3:
            return (n_spokes, 2, 0)
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


def _position_faces(glyph_paths):
    """One face vote per marker POSITION (not per path).

    A marker glyph may be drawn as a fill-only path plus a coincident stroke-only
    outline at the same centre. These are ONE marker: cluster paths by centre and
    return, per cluster, the fill colour of any filled member (the marker's face)
    or ``None`` when the whole cluster is stroke-only (a genuinely open marker).
    Clustering uses the median glyph size as the coincidence tolerance, so the two
    halves of one marker land in the same cluster while distinct data points (which
    are spaced apart) stay separate."""
    if not glyph_paths:
        return []
    sizes = [max(p.bbox[2] - p.bbox[0], p.bbox[3] - p.bbox[1]) for p in glyph_paths]
    tol = max(1.0, 0.5 * (sorted(sizes)[len(sizes) // 2]))
    centres = [(0.5 * (p.bbox[0] + p.bbox[2]), 0.5 * (p.bbox[1] + p.bbox[3]))
               for p in glyph_paths]
    used = [False] * len(glyph_paths)
    faces = []
    for i, p in enumerate(glyph_paths):
        if used[i]:
            continue
        used[i] = True
        cluster = [p]
        cx, cy = centres[i]
        for j in range(i + 1, len(glyph_paths)):
            if used[j]:
                continue
            qx, qy = centres[j]
            if (qx - cx) ** 2 + (qy - cy) ** 2 <= tol ** 2:
                used[j] = True
                cluster.append(glyph_paths[j])
        fill = next((q.fill for q in cluster if q.fill is not None), None)
        faces.append(tuple(_round_color(fill)) if fill is not None else None)
    return faces


def _seg_dist_sq(px, py, ax, ay, bx, by):
    """Squared distance from point ``(px, py)`` to the segment ``a``--``b``."""
    dx, dy = bx - ax, by - ay
    dd = dx * dx + dy * dy
    if dd == 0.0:
        return (px - ax) ** 2 + (py - ay) ** 2
    t = ((px - ax) * dx + (py - ay) * dy) / dd
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    qx, qy = ax + t * dx, ay + t * dy
    return (px - qx) ** 2 + (py - qy) ** 2


def _min_seg_dist_sq(pts_arr, ax, ay, bx, by):
    """Vectorised per-point min point-to-segment squared distance.

    ``pts_arr`` is an ``(N, 2)`` float64 array; ``ax/ay/bx/by`` are length-``M``
    float64 arrays of segment endpoints. Returns the length-``N`` array of each
    point's minimum squared distance to ANY of the ``M`` segments. The per-(point,
    segment) distance mirrors :func:`_seg_dist_sq` term-for-term (incl. the
    degenerate zero-length-segment branch and the [0, 1] clamp) so the minimum --
    and therefore the ``<= tol2`` decision -- is bit-identical to the scalar loop.
    """
    dx = bx - ax                                   # (M,)
    dy = by - ay
    dd = dx * dx + dy * dy
    pmax = pts_arr[:, 0:1]                          # (N, 1)
    pmay = pts_arr[:, 1:2]
    rx = pmax - ax                                  # (N, M) via broadcast
    ry = pmay - ay
    # Avoid 0/0 in the projection; the dd==0 branch is selected explicitly below.
    safe_dd = np.where(dd == 0.0, 1.0, dd)
    t = (rx * dx + ry * dy) / safe_dd
    t = np.clip(t, 0.0, 1.0)
    qx = ax + t * dx
    qy = ay + t * dy
    d2 = (pts_arr[:, 0:1] - qx) ** 2 + (pts_arr[:, 1:2] - qy) ** 2
    # Degenerate (zero-length) segment: distance to the endpoint a, as scalar code.
    deg = rx * rx + ry * ry
    d2 = np.where(dd == 0.0, deg, d2)
    return d2.min(axis=1)                           # (N,)


def _threads_markers(paths, pts, tol, frac=0.8):
    """Does any same-colour ``paths`` (the candidate connectors) thread the marker
    ``pts``? True when >= ``frac`` of the marker points lie within ``tol`` of a
    single path -- i.e. that path is the line drawn THROUGH the markers. Distance
    is to the nearest SEGMENT of the polyline (not just a vertex), so a coarsely
    sampled connector whose markers fall mid-segment still counts.

    A separate fit/curve that does not pass through the markers threads ~none, so
    it does not trigger a connection. Crucially, ``tol`` must stay TIGHT: a genuine
    connector is drawn ON the markers (sub-pixel offset), whereas a sibling
    same-colour series' connector (e.g. the OPEN-circle line running just beside a
    FILLED-circle scatter that shares the same x-grid and overlaps in y) merely
    grazes them at a scattered, larger distance. A loose ``tol`` collapsed such a
    pure scatter into a connected line (2006.09651_p5c1/p5c2: the filled circles/
    squares were threaded by the open series' line)."""
    if not paths or not pts:
        return False
    need = max(2, int(round(frac * len(pts))))
    tol2 = tol * tol
    pts_arr = np.asarray(pts, dtype=np.float64)     # (N, 2)
    for p in paths:
        pp = p.points
        if len(pp) < 2:
            continue
        seg = np.asarray(pp, dtype=np.float64)
        ax, ay = seg[:-1, 0], seg[:-1, 1]
        bx, by = seg[1:, 0], seg[1:, 1]
        dmin = _min_seg_dist_sq(pts_arr, ax, ay, bx, by)
        near = int(np.count_nonzero(dmin <= tol2))
        if near >= need:
            return True
    return False


# A point this many ``tol``s off the threading path is an OUTLIER: connecting it
# (the renderer joins ALL points in draw order) would draw a spurious segment, so
# the series must stay UNCONNECTED. A genuine marker on a connector sits sub-pixel
# on the line, so this bound is very generous and never rejects a real line+marker.
_THREAD_OUTLIER_MULT = 6.0


def _connector_fits_all(paths, pts, tol, frac=0.8):
    """Connector evidence that survives the renderer joining ALL points in order.

    :func:`_threads_markers` accepts a path that threads ``frac`` of the points,
    which is right for recovering a suppressed connector. But the renderer draws
    one polyline through EVERY point in draw order: if a threaded path leaves even
    one point GROSSLY off the line (e.g. a lone isolated marker far above a small
    connected cluster -- 2504.21746_p6c1: 5 black circles joined by a line, a 6th
    25 px above), connecting that point fabricates a long spurious segment. So a
    series is connected iff some same-colour path both threads ``frac`` of the
    points AND leaves NO point more than ``_THREAD_OUTLIER_MULT * tol`` off it."""
    if not _threads_markers(paths, pts, tol, frac):
        return False
    need = max(2, int(round(frac * len(pts))))
    tol2 = tol * tol
    out2 = (_THREAD_OUTLIER_MULT * tol) ** 2
    pts_arr = np.asarray(pts, dtype=np.float64)
    for p in paths:
        pp = p.points
        if len(pp) < 2:
            continue
        seg = np.asarray(pp, dtype=np.float64)
        dmin = _min_seg_dist_sq(pts_arr, seg[:-1, 0], seg[:-1, 1],
                                seg[1:, 0], seg[1:, 1])
        if int(np.count_nonzero(dmin <= tol2)) >= need \
                and bool(np.all(dmin <= out2)):
            return True
    return False


# Minimum markers for the UNION (multi-piece) connector: a split curve is dense,
# so pooling pieces may only connect a many-point series -- this blocks a tiny
# stray group from connecting via a same-colour sibling's long line (cross-talk).
_UNION_MIN_PTS = 8


def _union_connector_fits_all(paths, pts, tol, frac=0.8):
    """Like :func:`_connector_fits_all` but the connector is the UNION of several
    same-colour "big" paths, not one single path.

    A connected curve is sometimes emitted as a handful of LONG contiguous
    same-colour stroked paths -- the PDF split the polyline into pieces (each a few
    hundred vertices spanning a fraction of the x-range), e.g. when a steep narrow
    feature breaks the stroke (2205.07176_p2c1: a smooth G-vs-Vg curve with a sharp
    full-height DIP at Vg=0, drawn as 5 pieces). No single piece threads ``frac`` of
    the markers, so :func:`_threads_markers` (which scans paths one at a time) misses
    it, and the pieces are too long to be the short marker-to-marker hops handled by
    :func:`_segments_thread_markers`. Here each marker is matched to the NEAREST
    segment across ALL the big paths: connect iff every marker lies on SOME piece
    (>= ``frac`` within ``tol``) AND none is more than ``_THREAD_OUTLIER_MULT*tol``
    off the nearest piece -- the same all-points-on-the-line signature as the single
    path case, just pooled across the pieces.

    This stays distinct from the #66 spurious case: a pure scatter's only big
    same-colour path is a SEPARATE fit/curve that misses the markers; pooling does
    not create coverage where none of the pieces pass through the points, so the
    union distance to most markers stays large and the frac/outlier tests fail.
    Likewise a sibling-line grazing a filled scatter (2006.09651) leaves those
    markers > ``tol`` off every piece, and a lone off-line marker (2504.21746) stays
    beyond the outlier bound -- both still reject.

    Guarded to series with enough markers (``_UNION_MIN_PTS``): a curve split into
    several long pieces is necessarily DENSE, so pooling can only legitimately
    connect a many-point series. A TINY series (e.g. a stray 3-point fragment)
    must not be "connected" just because a SAME-COLOUR SIBLING series' long line
    happens to pass near its points (cross-talk: 2005.03896_p4c1 -- a spurious
    3-point blue group threaded by the real 417-point blue curve)."""
    if len(paths) < 2 or len(pts) < _UNION_MIN_PTS:
        return False
    need = max(2, int(round(frac * len(pts))))
    tol2 = tol * tol
    out2 = (_THREAD_OUTLIER_MULT * tol) ** 2
    pts_arr = np.asarray(pts, dtype=np.float64)
    dmin = np.full(len(pts_arr), np.inf)
    for p in paths:
        pp = p.points
        if len(pp) < 2:
            continue
        seg = np.asarray(pp, dtype=np.float64)
        d = _min_seg_dist_sq(pts_arr, seg[:-1, 0], seg[:-1, 1],
                             seg[1:, 0], seg[1:, 1])
        dmin = np.minimum(dmin, d)
    return int(np.count_nonzero(dmin <= tol2)) >= need \
        and bool(np.all(dmin <= out2))


def _segments_thread_markers(segs, pts, tol, frac=0.8):
    """Does a connecting LINE drawn as many SHORT same-colour segments thread the
    markers ``pts``?  Some plots draw the join between adjacent markers as one
    open segment EACH (e.g. 2201.08243_p38c1: a ``<``-marker series whose line is
    12 two-vertex segments hopping marker-to-marker), so no single path threads
    the whole series and ``_threads_markers`` over the long ``big`` paths misses
    it.  Here the connector is recognised from the COLLECTION: True when the union
    of the segments' vertices lies within ``tol`` of >= ``frac`` of the markers
    AND there are at least two such segments bridging the points (so a single
    stray segment cannot fabricate a connection).

    ``segs`` are the candidate connector paths (already filtered to short, OPEN,
    non-marker strokes by the caller).  Each counted segment must BRIDGE two
    DISTINCT markers (its two endpoints fall on different marker points) — this is
    what makes it a join, and what separates it from an OPEN marker glyph (e.g. an
    ``x``/``+`` whose two strokes both sit on ONE marker, so they bridge nothing
    and a pure scatter is not falsely connected).  This stays distinct from the
    #66 spurious case (a single straight connector / fit path the original never
    drew through the markers): that is ONE path handled by ``_threads_markers``;
    it is not a set of many short segments each hopping between two markers."""
    if len(segs) < 2 or not pts:
        return False

    def _nearest(qx, qy):
        # index of the marker nearest (qx,qy) within tol, else None
        best_i, best_d = None, tol * tol
        for i, (sx, sy) in enumerate(pts):
            d = (sx - qx) ** 2 + (sy - qy) ** 2
            if d <= best_d:
                best_i, best_d = i, d
        return best_i

    bridged: set[int] = set()
    n_bridges = 0
    for s in segs:
        a, b = s.points[0], s.points[-1]
        ia, ib = _nearest(*a), _nearest(*b)
        if ia is not None and ib is not None and ia != ib:
            bridged.add(ia)
            bridged.add(ib)
            n_bridges += 1
    need = max(2, int(round(frac * len(pts))))
    return n_bridges >= 2 and len(bridged) >= need


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


# A solid curve drawn in pieces tiles ~its whole x-span; a real dashed line's
# fragments cover only this fraction of the span (on/off). Below it -> dashed.
_DASH_FRAG_MAX_COVERAGE = 0.8


def _frag_x_coverage(paths) -> float:
    """Fraction of the fragments' total x-span actually covered by their bboxes
    (interval union / span). ~1.0 for contiguous solid pieces; ~0.5-0.6 for a
    dashed line's on/off fragments."""
    ivs = sorted((p.bbox[0], p.bbox[2]) for p in paths)
    if not ivs:
        return 1.0
    span = max(b for _, b in ivs) - min(a for a, _ in ivs)
    if span <= 0:
        return 1.0
    covered, cur_a, cur_b = 0.0, ivs[0][0], ivs[0][1]
    for a, b in ivs[1:]:
        if a <= cur_b:
            cur_b = max(cur_b, b)
        else:
            covered += cur_b - cur_a
            cur_a, cur_b = a, b
    covered += cur_b - cur_a
    return covered / span


# --- Fragment-drawn dash classification ----------------------------------
# Some renderers rasterise a dashed/dotted/dash-dot curve as MANY tiny solid
# sub-strokes (each path ``dashes=None``, ~2 vertices, sub-point length) with
# real GAPS between the dash "on" runs. The merged curve then looks solid. We
# reconstruct the on/off pattern from the fragment geometry: chain touching
# fragments into "on" runs, measure each run's drawn length and the gap to the
# next run, and classify the line style from the run-length distribution.

# Consecutive fragments whose endpoints are within this many points belong to
# the SAME dash "on" run (sub-point flattening segments touch end-to-end).
_FRAG_TOUCH = 0.6
# A fragment is a dash sub-stroke only if it is this short (a long multi-vertex
# polyline is a genuine solid curve piece, not a dash fragment).
_FRAG_MAX_LEN = 1.5
# Need at least this many short fragments before fragmentation is treated as a
# dash signal (a handful of short pieces is just a wiggly solid curve).
_DASH_MIN_FRAGMENTS = 30
# A run counts as a "dot" when its drawn length is at or below this (points):
# comparable to a stroke width, so it renders as a round dot, not a dash.
_DOT_MAX_LEN = 1.6
# Dash-dot needs a clear LONG component: the long runs are at least this factor
# longer than the short (dot) runs.
_DASHDOT_RATIO = 2.5


def _frag_len(p) -> float:
    """Total drawn arc length of a fragment path (sum of its segment lengths)."""
    pts = p.points
    return sum(((pts[i][0] - pts[i + 1][0]) ** 2
                + (pts[i][1] - pts[i + 1][1]) ** 2) ** 0.5
               for i in range(len(pts) - 1))


def _classify_fragment_dash(cands, width):
    """Classify a fragment-drawn line style from same-colour fragment paths.

    Returns a matplotlib dash TUPLE ``(offset, (on, off[, on, off]))`` in absolute
    PDF points -- the MEASURED on/off period of the original -- when the fragments
    form a clear dashed / dotted / dash-dot pattern, else ``None`` (caller keeps
    the curve solid). Emitting the measured period (not a bare "--"/":"/"-." whose
    default period is far too sparse) makes the reconstruction match the original
    dash density. ``cands`` are the same-colour stroked paths of one series;
    ``width`` is the series' stroke width (for the dot threshold).

    Method: keep only the short sub-stroke fragments, order them along the curve
    (by midpoint x, the curves here are x-monotone), chain end-to-end-touching
    ones into "on" runs, and read the on-run length distribution + gaps:
      - dotted  (``:``): on-runs uniformly tiny (~ a stroke width) -> dots.
      - dash-dot(``-.``): on-runs bimodal -> long dashes alternating with dots.
      - dashed  (``--``): on-runs uniformly moderate, separated by gaps.
    A SOLID curve drawn in few long pieces has < ``_DASH_MIN_FRAGMENTS`` short
    fragments (or no real gaps) and returns ``None`` -> stays solid."""
    frags = [p for p in cands
             if p.stroke is not None and len(p.points) >= 2
             and _frag_len(p) <= _FRAG_MAX_LEN]
    if len(frags) < _DASH_MIN_FRAGMENTS:
        return None
    # Guard against a SOLID curve whose colour also has incidental short paths
    # (ticks, markers, axis): a real fragment-drawn dash is built almost ENTIRELY
    # of short sub-strokes, so the short fragments must dominate the total drawn
    # length. A solid curve is mostly LONG polylines (its short pieces are decor).
    short_len = sum(_frag_len(p) for p in frags)
    long_len = sum(_frag_len(p) for p in cands
                   if p.stroke is not None and _frag_len(p) > _FRAG_MAX_LEN)
    if short_len <= 2.0 * long_len:
        return None
    frags.sort(key=lambda p: 0.5 * (p.bbox[0] + p.bbox[2]))
    runs = []          # drawn length of each "on" run
    gaps = []          # gap length between consecutive runs
    cur = _frag_len(frags[0])
    prev_end = frags[0].points[-1]
    for p in frags[1:]:
        s, e = p.points[0], p.points[-1]
        d_s = ((prev_end[0] - s[0]) ** 2 + (prev_end[1] - s[1]) ** 2) ** 0.5
        d_e = ((prev_end[0] - e[0]) ** 2 + (prev_end[1] - e[1]) ** 2) ** 0.5
        gap = min(d_s, d_e)
        if gap > _FRAG_TOUCH:
            runs.append(cur)
            gaps.append(gap)
            cur = _frag_len(p)
        else:
            cur += _frag_len(p) + gap
        prev_end = e if d_s <= d_e else s
    runs.append(cur)
    if len(runs) < 4 or not gaps:
        return None
    runs_s = sorted(runs)
    med_run = runs_s[len(runs_s) // 2]
    med_gap = sorted(gaps)[len(gaps) // 2]
    # Require genuine OFF gaps comparable to the on length: a solid curve merely
    # split into pieces touches end-to-end (tiny gaps) and must stay solid.
    if med_gap < max(_FRAG_TOUCH, 0.4 * med_run):
        return None
    dot_thresh = max(_DOT_MAX_LEN, 1.6 * (width or 0.0))
    p10 = runs_s[max(0, int(0.1 * (len(runs_s) - 1)))]
    p90 = runs_s[int(0.9 * (len(runs_s) - 1))]
    # Emit the MEASURED on/off period (absolute PDF points) as a matplotlib dash
    # tuple, NOT a bare "--"/":"/"-." string: those use matplotlib's default
    # period, which renders dense fine dashing far too sparse (2001.01928: the red
    # dashed / green dotted curves lost ~2/3 of their ink, recon_ink 9170->3346).
    # The renderer sets lines.scale_dashes=False, so a tuple renders at its
    # absolute recovered length and the reconstruction matches the original dash
    # DENSITY. (Pixel-IoU still under-scores dashes -- dash PHASE is unrecoverable
    # -- but the curve no longer looks anemic.) ``on`` floored so a sub-point dot
    # still renders (with the round cap it becomes a ~linewidth dot).
    off = med_gap
    # dash-dot: a clearly BIMODAL on-run distribution (short dots + long dashes)
    # -> alternate long dash and dot, each followed by the measured gap.
    if p10 <= dot_thresh and p90 >= _DASHDOT_RATIO * max(p10, 1e-6) \
            and p90 > dot_thresh:
        return (0.0, (max(p90, 0.1), off, max(p10, 0.1), off))
    # dashed / dotted: one on/off period at the measured lengths (dotted naturally
    # has a tiny on-run, dashed a moderate one).
    return (0.0, (max(med_run, 0.1), off))


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
    # Tick detection needs a small margin BEYOND the region: OUTWARD ticks on the
    # top/right spines sit just outside the region box, so region-clipped `inreg`
    # drops them and top/right ticks are never recovered (2001.01801).
    _tm = 10.0
    inreg_ticks = [p for p in paths
                   if not (p.bbox[2] < x0 - _tm or p.bbox[0] > x1 + _tm or
                           p.bbox[3] < y0 - _tm or p.bbox[1] > y1 + _tm)]
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
        # A LINE series is traced from a STROKED path. A same-colour FILL polygon
        # (a shaded band) has an outline that runs ALONG the boundary curves, so
        # it ties the stroke at ~0 geometric distance — but it carries no
        # width/dash. Tie-break so a stroked path always beats a fill-only one at
        # equal distance, recovering the curve's true dash pattern + width
        # (2002.05937_p5c2: red dot-dash boundaries lost their dashes to the
        # coinciding pink band fill, rendering solid).
        def _rank(p):
            return (score(p), p.stroke is None and p.fill is not None)
        best = min(big or cands, key=_rank)
        # fragment-drawn dash: the curve is many short same-colour segments rather
        # than one long path -> visually dashed even though each piece is solid.
        frag = sum(1 for p in big
                   if max(p.bbox[2]-p.bbox[0], p.bbox[3]-p.bbox[1]) < 0.5 * diag)
        longest = max((max(p.bbox[2]-p.bbox[0], p.bbox[3]-p.bbox[1]) for p in big),
                      default=0.0)
        ls = _parse_dashes(best.dashes)
        # Infer dashed from fragmentation ONLY when (a) no single path spans the
        # curve AND (b) the fragments leave GAPS -- a real dashed line covers only
        # part of its x-span (on/off), whereas a SOLID curve merely drawn in many
        # contiguous pieces tiles ~the whole span and must stay solid
        # (2001.06496_p18c2: solid blue/orange curves in 14 pieces were dashed).
        if (ls == "-" and len(big) >= 5 and frag >= 5 and longest < 0.55 * diag
                and _frag_x_coverage(big) < _DASH_FRAG_MAX_COVERAGE):
            ls = (0.0, (3.0, 2.0))
        # Sub-point fragment-drawn dashing: when a dashed/dotted/dash-dot curve is
        # rasterised as MANY tiny solid sub-strokes (no PDF dash array, fragments
        # too small to be "big"), recover the line style from the fragment on/off
        # geometry (2001.01928_p5c1: black solid / red dashed / green dotted /
        # blue dash-dot). Only when the genuine-PDF-dash path saw solid.
        if ls == "-":
            frag_ls = _classify_fragment_dash(cands, best.width)
            if frag_ls is not None:
                ls = frag_ls
        # Upper bound on a MARKER glyph's bbox: small in absolute px, but a large
        # chart can draw genuinely big markers (2203.02869_p24c1: red 5-point stars
        # are 15.7px, above the historical 12px cap, so every star was excluded ->
        # mshape=None and the series rendered as tiny dots). Scale the cap with the
        # region while keeping it well below the marker-vs-curve threshold
        # (0.15*diag at L627) so a traced curve is never admitted as a marker.
        _msz_cap = max(12.0, 0.06 * diag)
        small_paths = [p for p in cands
                       if 0.2 < max(p.bbox[2]-p.bbox[0], p.bbox[3]-p.bbox[1]) <= _msz_cap]
        smalls = [max(p.bbox[2]-p.bbox[0], p.bbox[3]-p.bbox[1]) for p in small_paths]
        # Restrict the shape/size/face/edge vote to THIS series' OWN marker glyphs:
        # a glyph whose centre sits on one of the series' points. Same-colour paths
        # gathered above pool BOTH a filled and an open marker series of that colour
        # (2006.09651: 13 filled + 19 open blue circles are two datasets), so a
        # colour-wide modal vote collapsed both to 'open'. Voting with each series'
        # own glyphs gives the filled series a face and the open one none. Also drops
        # stray same-colour glyphs (legend samples) that sit off the data points.
        _ntol = max(5.0, (sorted(smalls)[len(smalls) // 2] if smalls else 0.0))
        _own = [p for p in small_paths
                if any((0.5 * (p.bbox[0] + p.bbox[2]) - px) ** 2
                       + (0.5 * (p.bbox[1] + p.bbox[3]) - py) ** 2 <= _ntol ** 2
                       for px, py in pts)]
        if _own:
            small_paths = _own
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
        # TIGHT tolerance (~half a marker): a real connector is drawn ON the
        # markers (seg-distance <1px), so a sibling same-colour series' line that
        # merely grazes a filled-circle scatter (2006.09651_p5c1/p5c2) is rejected.
        tol = max(2.0, 0.5 * (msize or 0.0))
        connect = _connector_fits_all(big, pts, tol)
        if not connect:
            # The connector may be split into several LONG contiguous same-colour
            # pieces (a steep feature broke the stroke -- 2205.07176_p2c1), so no
            # single path threads frac of the markers. Pool the pieces: connect when
            # every marker sits on SOME piece. Keeps the #66 outlier guard.
            connect = _union_connector_fits_all(big, pts, tol)
        if not connect:
            # The connecting line may instead be drawn as one short OPEN segment
            # between each adjacent marker pair (so no single path threads the
            # whole series). Recover it from the COLLECTION of short same-colour
            # open strokes whose endpoints hop marker-to-marker (2201.08243_p38c1),
            # distinct from filled/closed marker glyphs and from a single stray
            # connector. A slightly looser tol matches segment endpoints to marker
            # centres; the >=2-bridge requirement keeps pure scatter unconnected.
            seg_tol = max(4.0, 1.5 * (_median(smalls) or 0.0))

            def _is_open_seg(p):
                pp = p.points
                if p.fill is not None or len(pp) < 2 or len(pp) > 3:
                    return False
                if abs(pp[0][0] - pp[-1][0]) < 0.5 and abs(pp[0][1] - pp[-1][1]) < 0.5:
                    return False  # closed (a marker outline), not a join segment
                sz = max(p.bbox[2] - p.bbox[0], p.bbox[3] - p.bbox[1])
                return sz <= 0.15 * diag
            segs = [p for p in cands if _is_open_seg(p)]
            connect = _segments_thread_markers(segs, pts, seg_tol)
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
        #
        # A FILLED non-circular marker (triangle/square/diamond) is frequently
        # emitted as TWO coincident paths -- a fill-only glyph + a stroke-only
        # outline at the same centre (2011.13523_p7c2: red '^' = 11 filled + 11
        # outline paths). Counting each path as a separate face vote then ties
        # fill-vs-None and collapses the marker to OPEN. So vote ONCE per marker
        # POSITION: cluster the glyph paths by centre, and a position is FILLED if
        # any coincident member carries a fill (its outline twin does not cancel
        # the fill). A genuinely-open marker (stroke-only, no coincident fill twin)
        # still votes None (2006.10101_p37c1: open '^' is one stroke-only path).
        _faces = _position_faces(glyph_paths)
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
    # The axis/frame colour (modal spine stroke); None when plain black -> the
    # renderer keeps matplotlib's default black axes.
    #
    # Real chart axes are NEUTRAL (black / grey), never a saturated colour. A
    # CHROMATIC "spine" colour is a false positive: a thin axis-aligned data
    # stroke (e.g. a green 'planar orbits' series or a coloured legend rule) that
    # happened to run along the frame and got picked up as the spine. A genuinely
    # coloured frame only occurs on dual-axis (out-of-scope) charts, so rejecting
    # chromatic axis colours is safe for the in-scope single-axis charts and
    # avoids painting the frame a data-series colour. Mirrors grid.py
    # ``_is_chromatic`` (chroma = max-min channel > 0.15).
    axis_color = None  # neutral default (matplotlib black) for grey/black spines
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
    # Round line caps: a graphics-state choice the source applies to ALL strokes
    # (thick lines + tick marks get rounded ends, not butt). Recover it as one
    # global flag when the majority of genuine in-region strokes are round-capped
    # -> the renderer rounds line/marker/tick caps to match (2001.01038_p13c4).
    capped = [p for p in inreg if p.stroke is not None and p.width]
    round_caps = bool(capped) and sum(p.round_cap for p in capped) > 0.5 * len(capped)
    meta = {"axis_linewidth": _median(spine_w),
            "axis_color": axis_color,
            "ticks": recover_tick_style(inreg_ticks, region_bbox),
            "round_caps": round_caps,
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


def _classify_face(font_weights):
    """Canonical face token for the dominant font, when a specific metric-
    compatible substitute beats the generic serif/sans default; else None.

    serif->Liberation Serif (Times) and sans->Liberation Sans (Arial) are the
    renderer defaults, so only faces whose metrics those DON'T match need a
    token: Verdana/Tahoma (wide -> DejaVu Sans, Bitstream-Vera lineage), Calibri
    (-> Carlito), Cambria (-> Caladea), Courier (-> Liberation Mono)."""
    if not font_weights:
        return None
    name = max(font_weights, key=font_weights.get).lower()
    if "verdana" in name or "tahoma" in name:
        return "verdana"
    if "calibri" in name:
        return "calibri"
    if "cambria" in name:
        return "cambria"
    if "courier" in name or name.startswith("cour"):
        return "courier"
    return None


def _norm(s):
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def _is_junk_text(text):
    """True for text that is NEVER chart content: hidden LaTeXit/SVG metadata or
    an encoded base64/url-safe blob. Used to drop such spans from annotations.

    Two signatures (conservative -- real labels like '1560.3', 'GaAs',
    r'$\rho_{00}$' must NOT match):
      * literal '<latexit' / '</latexit>' marker (LaTeXit embeds editable source
        as hidden text in many arXiv figures).
      * a single long token (len >= 24, no spaces) made almost entirely of
        base64/url-safe chars with no natural-language vowel structure -- i.e.
        an encoded blob, not a word/number/formula."""
    t = (text or "").strip()
    if not t:
        return False
    if "<latexit" in t.lower():        # covers '<latexit' and '</latexit>'
        return True
    if len(t) >= 24 and " " not in t:
        b64 = sum(ch.isalnum() or ch in "+/_-=" for ch in t)
        if b64 / len(t) >= 0.95:
            letters = [ch for ch in t if ch.isalpha()]
            # A genuine word/identifier has vowels; an encoded blob is a random
            # mix of upper/lower with very few vowels relative to its letters.
            if letters:
                vowels = sum(ch in "aeiouAEIOU" for ch in letters)
                mixed_case = (any(ch.islower() for ch in letters) and
                              any(ch.isupper() for ch in letters))
                if mixed_case and vowels / len(letters) < 0.2:
                    return True
    return False


def _is_prose_title(text):
    """True for a record 'title' that is clearly a body-text sentence the
    extractor mis-promoted, not a chart title. Conservative: a real title may be
    multi-word ('SHG power [uW]') or hyphenated ('REGIME - II'), so we reject
    only LONG prose -- many words AND sentence punctuation. Length alone is not
    enough; a long technical title without sentence structure is kept."""
    t = (text or "").strip()
    if len(t) <= 48:
        return False
    words = t.split()
    if len(words) < 6:
        return False
    # Sentence punctuation inside the running text (not a trailing unit/symbol).
    has_sentence_punct = any(p in t[:-1] for p in (". ", ", ", "; ", ") "))
    return has_sentence_punct


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
    # Assemble from ALL spans (symbol-font chars like 'µ', '°', '±' are real label
    # text, e.g. '[µm]'); only the ITALIC VOTE excludes them. Dropping them here
    # lost the 'µ' and merged the gap into a spurious space (2004.12366 '[µm]' ->
    # '[ m]'). Symbol-font spans render roman.
    ordered = sorted(group, key=lambda s: _reading_pos(s)[0])
    runs, prev_end, prev_ital = [], None, None
    for s in ordered:
        a0, a1, _ = _reading_pos(s)
        t = s["text"]
        if prev_end is not None and (a0 - prev_end) > 0.18 * max(s.get("size") or 8, 1):
            t = " " + t                       # restore inter-token space
        it = _is_italic(s) and not _is_symbol_font(s)
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


def _text_color(c):
    """A text colour worth recording: CHROMATIC only (RGB spread > 0.12).

    Black / grey / white text returns None so the renderer leaves it
    matplotlib-default black and never overrides e.g. a coloured-axis tick
    labelcolor; only genuinely-coloured text (a blue label) is carried. Same
    rule the legend uses, so all text colour is gated consistently."""
    if c is None:
        return None
    return [round(v, 3) for v in c] if (max(c) - min(c)) > 0.12 else None


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
    we DO rescale fonts + line widths back to their true on-page size -- but only by
    the CTM part: the measured ratio is (intrinsic box metric ~1.36) x (figure CTM),
    so we divide the typical box metric out, else fonts/line widths come out ~1.36x
    too big (2001.01928_p5c1 ticks 10.3pt -> ~7.6pt true)."""
    ratios = [(s["bbox"][3] - s["bbox"][1]) / s["size"]
              for s in spans
              if s.get("size") and s["size"] > 0.05
              and abs((s.get("dir") or (1.0, 0.0))[1]) < 0.3]
    if not ratios:
        return 1.0
    scale = min(50.0, max(0.2, sorted(ratios)[len(ratios) // 2]))
    if 0.7 <= scale < 2.0:
        return 1.0
    return scale / _FONT_BOX_METRIC


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
                       title_text, tick_labels, legend_labels=None):
    """Recover font family/sizes, legend layout, and axis-label positions.

    ``legend_labels`` (optional) are the legend entry labels recovered directly
    from the legend structure (``labels.detect_labels``); they are used to build
    the legend layout ONLY when ``series_labels`` carry nothing (e.g. a colour-
    ambiguous legend whose labels could not be assigned to series). This lets a
    fully-readable text legend recover its box + entries instead of falling
    through to the OCR-glyph path (which loses the frame and double-draws the
    labels as annotations)."""
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
    # Group the region's spans into logical labels ONCE; _group_spans is O(n^2)
    # and deterministic, so the title/axis-title matching, legend layout and
    # title-guard all reuse this instead of rebuilding it per call.
    grouped = _group_spans(spans, base)

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
        for grp in grouped:
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
    # Source labels for the legend layout: the series labels normally, but fall
    # back to the legend's OWN recovered labels when NO series carries a label
    # (colour-ambiguous legend -> labels never assigned to series). This keeps a
    # readable text legend on the normal path (frame bbox + spans consumed) and
    # off the OCR-glyph fallback.
    _src_labels = [l for l in (series_labels or []) if l]
    if not _src_labels and legend_labels:
        _src_labels = [l for l in legend_labels if l and str(l).strip()]
    labset = [_norm(l) for l in _src_labels]
    orig_labels = list(_src_labels)  # parallel to labset
    matched, matched_labels, used = [], [], set()
    for lk, ol in zip(labset, orig_labels):
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
            matched_labels.append(ol)
            used.add(pick[0])
    # The legend is only present on THIS panel if at least half its entry labels
    # are found as text inside the region; otherwise it lives on another panel
    # and must NOT be drawn on the reconstruction.
    show_legend = len(matched) >= max(1, (len(labset) + 1) // 2)
    # Per-entry text COLOUR: papers often draw a legend label in its series'
    # colour (2001.01038_p13c4 'I_D^1/2' is blue). Recorded INDEPENDENT of the
    # legend-LAYOUT recovery (which a width gate may drop), keyed by the series
    # label, so the renderer can tint even an auto-placed legend. Only CHROMATIC
    # entries (sat > 0.12); black/grey labels omitted -> stay default black.
    legend_label_colors = {}
    for lab, s in zip(matched_labels, matched):
        tc = _text_color(s.get("color"))
        if tc is not None:
            # Key by the CLEANED label, matching the render entry's label
            # (render_entries below store _clean(label)); the drawer looks up
            # legend_label_colors by the entry label, so an uncleaned key (e.g.
            # one with a trailing control glyph) would silently miss.
            legend_label_colors[_clean(lab)] = tc
    legend = None
    if len(matched) >= 1:
        # Measure geometry from the FULL legend ENTRY, not the single matched
        # span: a multi-span entry ('V'+'D'+'= 1V') is matched on a fragment
        # ('= 1V'), whose left edge sits mid-entry. Expanding each match to its
        # _group_spans group recovers the entry's true left edge, so stacked
        # entries read as left-aligned (and width/anchor are correct) instead of
        # spuriously failing the width gate (2001.01038_p13c4).
        groups = grouped

        def _entry_box(m):
            for g in groups:
                if any(s is m for s in g):
                    return (min(s["bbox"][0] for s in g), min(s["bbox"][1] for s in g),
                            max(s["bbox"][2] for s in g), max(s["bbox"][3] for s in g))
            return tuple(m["bbox"])
        ent = [_entry_box(m) for m in matched]
        ys = sorted((b[1] + b[3]) / 2 for b in ent)
        rows = 1 + sum(1 for a, b in zip(ys, ys[1:]) if b - a > 1.4 * (base or 8))
        xext = max(b[2] for b in ent) - min(b[0] for b in ent)
        yext = max(b[3] for b in ent) - min(b[1] for b in ent)
        horizontal = rows == 1 and xext > 3 * max(yext, 1e-6)
        ncol = min(len(matched), len(labset)) if horizontal else 1
        mx0, mx1 = min(b[0] for b in ent), max(b[2] for b in ent)
        my0, my1 = min(b[1] for b in ent), max(b[3] for b in ent)
        cxf, cyf = to_frac((mx0 + mx1) / 2, (my0 + my1) / 2)
        # original legend extent in axes fraction (text + a swatch allowance to
        # the left), used to fit the reconstruction's legend size to the original.
        sw = 3.2 * (matched[0].get("size") or base or 8)
        wfrac = (mx1 - (mx0 - sw)) / w
        hfrac = (my1 - my0) / h
        # Entry order as drawn (top-to-bottom, then left-to-right), so the renderer
        # presents entries in the original order, not extraction order
        # (2202.11909_p25c1: 'PTE simulation'/'aI fitting' were swapped).
        # Clean each entry the same way the per-series style labels are cleaned
        # (_clean drops non-printable control glyphs, e.g. a trailing BEL from a
        # mangled degree-sign), so the renderer's order-match against the plotted
        # series labels succeeds instead of silently keeping extraction order.
        order = [_clean(matched_labels[i]) for i in sorted(
            range(len(matched)),
            key=lambda i: (round((ent[i][1] + ent[i][3]) / 12.0), ent[i][0]))]
        # The legend STYLE (font size, bold, entry order) is always recoverable
        # from the matched spans; only the LAYOUT (anchor + width fit) is gated.
        # A vertical legend is narrow: if a "vertical" match sprawls across most
        # of the plot width it is an unreliable placement (short labels catching
        # scattered ticks) -> drop only the ANCHOR so the renderer auto-places,
        # but KEEP the recovered font/bold. Left-aligned stacks are exempt
        # (genuine narrow-panel legends, 2005.05829_p13c1 "1S/2S exciton").
        left_aligned = _legend_left_aligned([{"bbox": b} for b in ent])
        # Per-entry render LAYOUT for the custom legend drawer (the renderer pairs
        # each entry's label with its series to draw the handle). One entry per
        # matched label: the label's left-edge anchor + vertical centre in axes
        # fraction, its font size, and bold/italic. Drawn top-to-bottom in `order`.
        def _entry_size(m):
            # BASE label size = the largest span in the entry's group; the matched
            # span may be a small sub/superscript fragment ('I_D^1/2' matched on
            # '1/2'), whose size would render the whole entry tiny (2001.01038).
            for g in groups:
                if any(s is m for s in g):
                    szs = [s.get("size") for s in g if s.get("size")]
                    if szs:
                        return max(szs)
            return m.get("size")
        render_entries = []
        for i, m in enumerate(matched):
            bx0, by0, bx1, by1 = ent[i]
            lx, lyc = to_frac(bx0, (by0 + by1) / 2)
            esz = _entry_size(m)
            render_entries.append({
                "label": _clean(matched_labels[i]),
                "x_frac": round(lx, 4),
                "y_frac": round(lyc, 4),
                "size": (round(esz * scale, 2) if esz else base),
                "bold": bool(bold_reliable and _is_bold(m)),
                "italic": bool(italic_reliable and _is_italic(m)),
            })
        # Frame box in axes fraction: labels' extent grown left by the swatch
        # allowance (the handle sits left of the leftmost label).
        fx0, fy1 = to_frac(mx0 - sw, my0)
        fx1, fy0 = to_frac(mx1, my1)
        legend = {
            "orientation": "horizontal" if horizontal else "vertical",
            "ncol": int(ncol),
            "fontsize": (round(matched[0]["size"] * scale, 2)
                         if matched[0].get("size") else base),
            "bold": bold_reliable and any(_is_bold(s) for s in matched),
            "order": order,
            "entries": render_entries,
            "bbox_frac": [round(fx0, 4), round(fy0, 4), round(fx1, 4), round(fy1, 4)],
        }
        if horizontal or wfrac <= 0.6 or left_aligned:
            legend["anchor"] = [round(cxf, 3), round(cyf, 3)]
            legend["w_frac"] = round(wfrac, 4)
            legend["h_frac"] = round(hfrac, 4)

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
        if _is_junk_text(text):      # hidden LaTeXit metadata / encoded blob
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

    # UNIFIED per-element text style: every text element (title, axis titles,
    # ticks) carries the SAME properties -- size, colour, bold, italic, per-token
    # runs and rotation -- recovered from its source spans. This replaces the
    # scattered *_font_size / *_bold / *_runs fields (annotations + legend already
    # carry their own colour). The renderer applies it through one code path.
    def _fonts_of(member_spans):
        """Per-element font weights {font_name: chars} from its own spans, so
        each text element recovers its OWN family/face (a serif title can sit
        over sans-serif ticks)."""
        fw = {}
        for sp in member_spans:
            fn = sp.get("font")
            if fn:
                fw[fn] = fw.get(fn, 0) + max(1, len(sp.get("text", "")))
        return fw

    def _elem(text):
        grp = find_group(text)
        s = grp[0] if grp else find(text)
        if s is None:
            return None
        members = grp or [s]
        # Size from the matched GROUP's median span (a multi-span title like
        # 'V_D(V)' has no single span matching the whole label, so size_of/find
        # returns None and the renderer falls back to matplotlib's oversized
        # default); fall back to the single-span size_of only when ungrouped.
        gsz = sorted(sp["size"] for sp in grp if sp.get("size")) if grp else []
        size = round(gsz[len(gsz) // 2] * scale, 2) if gsz else size_of(text)
        efw = _fonts_of(members)
        return {
            "size": size,
            "color": _text_color(_group_color(grp) if grp else s.get("color")),
            "bold": bold_of(text),
            "italic": italic_of(text),
            "runs": runs_of(text),
            "rotation": _text_rotation(s.get("dir")),
            "family": _classify_family(efw) if efw else None,
            "face": _classify_face(efw) if efw else None,
        }
    _tick_fw = _fonts_of(tick_spans)
    elements = {
        "title": _elem(title_text),
        "x_title": _elem(axis_titles.get("x")),
        "y_title": _elem(axis_titles.get("y")),
        "ticks": {"size": round(tick_size, 2) if tick_size else None,
                  "color": _text_color(_group_color(tick_spans)),
                  "bold": tick_bold,
                  "family": _classify_family(_tick_fw) if _tick_fw else None,
                  "face": _classify_face(_tick_fw) if _tick_fw else None},
    }
    for _ax in ("x_title", "y_title"):
        if elements[_ax]:
            elements[_ax]["pos"] = label_pos(axis_titles.get(_ax[0]))

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
    if tkey and _is_prose_title(title_text):
        tkey = ""                    # body-text sentence -> never a chart title
    if tkey:
        for grp in grouped:
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

    # Default family/face/latex vote over the chart's OWN structural text (tick
    # labels + legend entries) -- the region+margin span set also pulls in
    # surrounding paper BODY text (e.g. a Computer Modern caption / prose), which
    # would otherwise dominate the vote and mis-classify an Arial chart as serif
    # (2001.08430_p4c1: CMR body 91 chars vs ArialMT ticks 28 -> wrong "serif").
    # Tick labels + legend are unambiguously the chart's font; fall back to all
    # region fonts only when there is no structural text to vote with.
    _struct_fw = _fonts_of(list(tick_spans) + list(leg_spans))
    _family_fw = _struct_fw or fonts
    return {
        "_note": "STYLE ONLY -- font + position recovered from source text spans.",
        "content_scale": round(scale, 2),  # also applied to line widths
        "latex_like": _is_latex_font(_family_fw),
        "font_family": _classify_family(_family_fw),
        "font_face": _classify_face(_family_fw),
        "base_font_size": round(base, 2) if base else None,
        # Per-element text style (size/color/bold/italic/runs/rotation/pos) lives
        # in `elements`; the legacy flat *_font_size/*_bold/*_runs fields were
        # consolidated there (the renderer reads only `elements`).
        "show_legend": show_legend,
        "legend": legend,
        "legend_label_colors": legend_label_colors or None,  # per-entry text colour
        "annotations": annotations,  # in-graph text, NOT data or legend
        "title_ok": title_ok,        # title corroborated by a top-center span
        "elements": elements,        # unified per-element text style (color etc.)
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
# Axis-spanning highlight bands (axvspan / axhspan)
# --------------------------------------------------------------------------

# A highlight band must span (nearly) the full plot extent in one axis and be
# NARROW in the other -- a band, not the whole plot background or a wide CI fill.
_SPAN_MIN_FULL_FRAC = 0.9    # full-extent axis: bbox >= 90% of plot side
_SPAN_MAX_NARROW_FRAC = 0.6  # narrow axis: bbox <= 60% of plot side (a band)
# A simple rectangle has very few distinct vertices; a CI band tracing a curve
# has many. Cap the distinct-point count so curve-following fills are rejected.
_SPAN_MAX_POINTS = 6


def _distinct_points(p) -> int:
    return len(set((round(x, 2), round(y, 2)) for x, y in p.points))


def recover_spans(paths, region_bbox, x_calib, y_calib) -> list[dict]:
    """Recover axis-spanning highlight bands (axvspan/axhspan-style shaded regions).

    A HIGHLIGHT BAND is a FILLED, simple rectangle (<= ~6 distinct points) whose
    extent spans (nearly) the FULL plot height (vertical band) OR full width
    (horizontal band) and is NARROW in the other dimension. It must NOT be
    near-white (so the white plot background is excluded) and must be a rectangle
    (so a CI band following a curve -- tall AND wide, many points -- is excluded).

    Returns a list of ``{axis, lo, hi, color, alpha}`` in DATA coords, one per
    band. ``axis='x'`` is a vertical band (full height, axvspan over an x-range);
    ``axis='y'`` is a horizontal band (full width, axhspan over a y-range).
    Empty when no safe band is found (the common case).
    """
    if x_calib is None or y_calib is None:
        return []
    from .calibrate import to_data
    rx0, ry0, rx1, ry1 = region_bbox
    pw, ph = rx1 - rx0, ry1 - ry0
    if pw <= 0 or ph <= 0:
        return []
    spans: list[dict] = []
    for p in paths:
        if p.fill is None:
            continue
        # Not near-white: the white plot background / page fill is excluded.
        if min(p.fill[:3]) >= 0.9:
            continue
        bx0, by0, bx1, by1 = p.bbox
        # In-region: the band must lie within the plotting box (small slack).
        if (bx1 < rx0 - 2 or bx0 > rx1 + 2 or by1 < ry0 - 2 or by0 > ry1 + 2):
            continue
        bw, bh = bx1 - bx0, by1 - by0
        # A simple rectangle, not a curve-following CI band (many points).
        if _distinct_points(p) > _SPAN_MAX_POINTS:
            continue
        full_h = bh >= _SPAN_MIN_FULL_FRAC * ph
        full_w = bw >= _SPAN_MIN_FULL_FRAC * pw
        narrow_w = bw <= _SPAN_MAX_NARROW_FRAC * pw
        narrow_h = bh <= _SPAN_MAX_NARROW_FRAC * ph
        if full_h and narrow_w:        # vertical band: full height, narrow width
            lo = to_data(x_calib, bx0)
            hi = to_data(x_calib, bx1)
            axis = "x"
        elif full_w and narrow_h:      # horizontal band: full width, narrow height
            lo = to_data(y_calib, by0)
            hi = to_data(y_calib, by1)
            axis = "y"
        else:
            continue                   # spans both (background) or neither -> skip
        if lo > hi:
            lo, hi = hi, lo
        spans.append({
            "axis": axis,
            "lo": round(lo, 6),
            "hi": round(hi, 6),
            "color": [round(c, 4) for c in p.fill[:3]],
            "alpha": (round(p.fill_alpha, 3) if p.fill_alpha is not None else None),
        })
    # Deduplicate identical bands: a highlight band is often drawn as several
    # stacked identical rectangles (2502.15531_p22c3 emits each band twice).
    # Rendering the duplicates compounds translucency and darkens the band vs the
    # original; keep one per (axis, lo, hi, colour, alpha).
    _seen, _uniq = set(), []
    for s in spans:
        key = (s["axis"], s["lo"], s["hi"], tuple(s["color"]), s["alpha"])
        if key not in _seen:
            _seen.add(key)
            _uniq.append(s)
    return _uniq


# The axes BACKGROUND is a filled rectangle that covers ~the whole plot interior
# behind the grid + data (matplotlib's ax.set_facecolor). To recover it WITHOUT
# stealing data fills or partial highlight bands, require the fill to cover BOTH
# axes near-fully -- a band covers one axis fully but is narrow in the other, a
# violin/area fill is bounded by data, and the white plot patch is excluded by
# the non-white guard. The plot box (rbbox) is padded with tick-label margin, so
# the recovered facecolor rect sits slightly inside it; the height threshold is
# below the width threshold to tolerate that vertical padding.
_AXBG_MIN_WIDTH_FRAC = 0.9    # fill width / plot box width >= this
_AXBG_MIN_HEIGHT_FRAC = 0.8   # fill height / plot box height >= this (margin slack)


def recover_axes_facecolor(paths, region_bbox) -> dict | None:
    """Recover a non-white axes background fill covering ~the full plot interior.

    Returns ``{color: [r,g,b], alpha}`` for the single fill that spans nearly the
    full plot box on BOTH axes (the discriminator vs partial highlight bands /
    data fills), or ``None`` when no such fill exists (the common case). White /
    near-white fills (the default plot patch) are excluded.
    """
    rx0, ry0, rx1, ry1 = region_bbox
    pw, ph = rx1 - rx0, ry1 - ry0
    if pw <= 0 or ph <= 0:
        return None
    best = None  # (area, fill, alpha) -- keep the largest qualifying fill
    for p in paths:
        if p.fill is None:
            continue
        if min(p.fill[:3]) >= 0.9:        # white / near-white -> default patch
            continue
        if _distinct_points(p) > _SPAN_MAX_POINTS:  # a simple rectangle, not a curve
            continue
        bx0, by0, bx1, by1 = p.bbox
        bw, bh = bx1 - bx0, by1 - by0
        if bw < _AXBG_MIN_WIDTH_FRAC * pw or bh < _AXBG_MIN_HEIGHT_FRAC * ph:
            continue                       # not full-box on BOTH axes
        # Centred in the plot box: a background sits symmetrically inside it,
        # rejecting a full-width/tall band shoved against one edge.
        if abs((bx0 - rx0) - (rx1 - bx1)) > 0.1 * pw:
            continue
        if abs((by0 - ry0) - (ry1 - by1)) > 0.1 * ph:
            continue
        area = bw * bh
        if best is None or area > best[0]:
            best = (area, p.fill, p.fill_alpha)
    if best is None:
        return None
    _, fill, alpha = best
    return {
        "color": [round(c, 4) for c in fill[:3]],
        "alpha": (round(alpha, 3) if alpha is not None else None),
    }


# A curve-bounded confidence/uncertainty BAND is a filled fill_between polygon:
# its outline runs forward along the upper envelope (x increasing) then back
# along the lower envelope (x decreasing). It is recovered as a translucent
# shaded overlay, NOT as data points. Precision guards (band, not blob):
_BAND_MIN_WIDTH_FRAC = 0.4   # the fill must span a substantial x-range
_BAND_MIN_AREA_FRAC = 0.3    # ENCLOSE a real region (not a thin out-and-back curve)
_BAND_MAX_ALPHA = 0.6        # translucent: a CI overlay, not an opaque area/violin fill
_BAND_MIN_VERTS = 8          # a curve-following envelope, not a few-point rectangle
_BAND_RESAMPLE_N = 60        # x-grid resolution for the recovered envelopes
# A fill-to-baseline AREA (the translucent fill under a density/KDE curve down to
# the axis baseline) is the SAME fill_between shape as a band, but one envelope is
# ~flat at the plot-box bottom. It needn't span a wide x-range (a KDE peak can be
# narrow), so it gets a relaxed width gate — but ONLY when the flat-baseline
# discriminator holds, keeping precision (it is not a mid-plot two-curve band):
_AREA_MIN_WIDTH_FRAC = 0.1   # a narrow peak still spans a meaningful x-fraction
_AREA_BASELINE_FLAT_FRAC = 0.05  # baseline envelope y-range <= 5% of box height (flat)
_AREA_BASELINE_BOTTOM_FRAC = 0.06  # baseline sits within 6% of box height of y-min


def _baseline_envelope_idx(fwd, back, ry0, ry1) -> int | None:
    """Return 0 if ``fwd`` is a fill-to-baseline floor, 1 if ``back`` is, else None.

    A fill-to-baseline area has ONE envelope that is ~flat (small y-spread) AND
    pinned near the plot-box BOTTOM (max pixel y ``ry1``); the other follows a
    curve. This is the discriminator that lets such a (possibly narrow) fill in
    while a mid-plot two-curve band — neither envelope flat at the bottom — keeps
    the strict width gate."""
    ph = (ry1 - ry0) or 1.0
    flat_tol = _AREA_BASELINE_FLAT_FRAC * ph
    bottom_tol = _AREA_BASELINE_BOTTOM_FRAC * ph
    for idx, run in ((0, fwd), (1, back)):
        ys = [y for _, y in run]
        if not ys:
            continue
        flat = (max(ys) - min(ys)) <= flat_tol
        at_bottom = abs(max(ys) - ry1) <= bottom_tol and abs(min(ys) - ry1) <= bottom_tol
        if flat and at_bottom:
            return idx
    return None


def _shoelace_area_frac(pts) -> float:
    """Polygon area (shoelace) over bbox area: ~1 for a solid filled region,
    ~0 for a thin curve whose outline traces out and back along itself."""
    n = len(pts)
    if n < 3:
        return 0.0
    a = 0.0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    area = abs(a) * 0.5
    xs = [x for x, _ in pts]
    ys = [y for _, y in pts]
    bw = (max(xs) - min(xs)) or 1.0
    bh = (max(ys) - min(ys)) or 1.0
    return area / (bw * bh)


def _split_envelopes(pts):
    """Split a fill_between polygon into its upper and lower envelopes.

    A fill_between outline runs forward along one boundary (x increasing) to the
    rightmost vertex, then back along the other (x decreasing). Split at the
    x-extreme into the two monotone-in-x runs. Returns ``(fwd, back)`` lists of
    (x, y) or ``None`` when the path is not a clean forward/back envelope (e.g. a
    closed blob, marker, or self-crossing shape) -- a precision guard."""
    n = len(pts)
    if n < _BAND_MIN_VERTS:
        return None
    xs = [x for x, _ in pts]
    i_max = max(range(n), key=lambda i: xs[i])
    i_min = min(range(n), key=lambda i: xs[i])
    # The leftmost vertex should sit near a path end and the rightmost near the
    # middle: forward run start..i_max, return run i_max..end. Require i_max to be
    # an interior turning point with usable runs on both sides.
    if i_max < 2 or i_max > n - 3:
        return None
    fwd = pts[: i_max + 1]
    back = pts[i_max:]
    # Both runs must be (near-)monotone in x in opposite directions -- otherwise
    # the polygon is not a simple band (reject self-crossing / multi-lobe shapes).
    def _mono_frac(run, sign):
        steps = [run[k + 1][0] - run[k][0] for k in range(len(run) - 1)]
        if not steps:
            return 0.0
        ok = sum(1 for s in steps if s * sign >= -1e-6)
        return ok / len(steps)
    if _mono_frac(fwd, +1) < 0.9 or _mono_frac(back, -1) < 0.9:
        return None
    return fwd, back


def recover_bands(paths, region_bbox, x_calib, y_calib) -> list[dict]:
    """Recover FILLED curve-bounded confidence/uncertainty bands (fill_between).

    A genuine band is a translucent, non-white FILLED polygon that (a) spans a
    substantial x-range, (b) encloses a real area (not a thin out-and-back curve
    outline), (c) has a curve-following many-vertex outline (not a few-point
    axvspan rectangle, which ``recover_spans`` already handles), and (d) splits
    cleanly into an upper + lower envelope (a fill_between shape, not an arbitrary
    closed blob, marker glyph, or legend swatch). The translucency guard
    (``fill_alpha <= _BAND_MAX_ALPHA``) keeps opaque area/violin fills -- handled
    elsewhere as data -- from being turned into overlay bands.

    Returns ``[{x[], y_lo[], y_hi[], color, alpha}]`` in DATA coords (resampled to
    a common x-grid for fill_between), empty when no safe band is found.
    """
    if x_calib is None or y_calib is None:
        return []
    from .calibrate import to_data_array
    rx0, ry0, rx1, ry1 = region_bbox
    pw, ph = rx1 - rx0, ry1 - ry0
    if pw <= 0 or ph <= 0:
        return []
    bands: list[dict] = []
    for p in paths:
        if p.fill is None or min(p.fill[:3]) >= 0.9:
            continue
        # Translucent only: an opaque fill is a solid area/violin (data), not a
        # CI overlay. A None alpha (fully opaque) is also rejected here.
        if p.fill_alpha is None or p.fill_alpha > _BAND_MAX_ALPHA:
            continue
        bx0, by0, bx1, by1 = p.bbox
        if (bx1 < rx0 - 2 or bx0 > rx1 + 2 or by1 < ry0 - 2 or by0 > ry1 + 2):
            continue
        if _shoelace_area_frac(p.points) < _BAND_MIN_AREA_FRAC:
            continue
        env = _split_envelopes(p.points)
        if env is None:
            continue
        fwd, back = env
        # A fill-to-baseline AREA (one envelope flat at the plot-box bottom) needs
        # only a meaningful x-fraction; a mid-plot band keeps the strict width gate.
        is_area = _baseline_envelope_idx(fwd, back, ry0, ry1) is not None
        min_wfrac = _AREA_MIN_WIDTH_FRAC if is_area else _BAND_MIN_WIDTH_FRAC
        if (bx1 - bx0) < min_wfrac * pw:
            continue
        # Resample both envelopes onto a common x-grid (in pixel space), then map
        # to data coords. fill_between needs aligned x; the two envelopes share an
        # x-range but sample it at different vertices.
        xs_all = [x for x, _ in p.points]
        gx0, gx1 = min(xs_all), max(xs_all)
        grid = np.linspace(gx0, gx1, _BAND_RESAMPLE_N)
        fwd_x = np.array([x for x, _ in fwd]); fwd_y = np.array([y for _, y in fwd])
        bk_x = np.array([x for x, _ in back][::-1]); bk_y = np.array([y for _, y in back][::-1])
        # np.interp requires increasing x.
        if fwd_x[0] > fwd_x[-1]:
            fwd_x, fwd_y = fwd_x[::-1], fwd_y[::-1]
        ya = np.interp(grid, fwd_x, fwd_y)
        yb = np.interp(grid, bk_x, bk_y)
        gx = to_data_array(x_calib, grid)
        da = to_data_array(y_calib, ya)
        db = to_data_array(y_calib, yb)
        y_lo = np.minimum(da, db)
        y_hi = np.maximum(da, db)
        bands.append({
            "x": [round(float(v), 6) for v in gx],
            "y_lo": [round(float(v), 6) for v in y_lo],
            "y_hi": [round(float(v), 6) for v in y_hi],
            "color": [round(c, 4) for c in p.fill[:3]],
            "alpha": round(p.fill_alpha, 3),
        })
    return bands


# --------------------------------------------------------------------------
# Entry point: assemble the full top-level style block at parse time
# --------------------------------------------------------------------------

def _attach_legend_handle_geometry(text_style, paths, rbbox):
    """Record each legend entry's ACTUAL swatch geometry from the source paths.

    The custom legend drawer needs the original handle size to match it: the line
    sample's LENGTH and the marker's DIAMETER, recovered from the swatch path that
    sits just left of the entry's label. Entries with NO swatch (inline curve
    labels written on the data, not a boxed legend) get ``handle=None`` so the
    drawer renders the text alone — no spurious sample. Geometry is stored in axes
    fraction (line length) / points (marker diameter), matching the drawer.
    """
    if not text_style:
        return
    leg = text_style.get("legend") or {}
    entries = leg.get("entries") or []
    if not entries:
        return
    rx0, ry0, rx1, ry1 = rbbox
    rw = (rx1 - rx0) or 1.0
    rh = (ry1 - ry0) or 1.0
    for e in entries:
        if "handle" in e:
            continue  # already self-contained (OCR-recovered glyph legend)
        # Label's left edge + vertical centre back in PIXEL space.
        lx = rx0 + e["x_frac"] * rw
        cy = ry0 + (1.0 - e["y_frac"]) * rh
        size_pt = e.get("size") or 8.0
        rowtol = max(3.0, 0.55 * size_pt)
        gap = 3.2 * size_pt            # swatch sits within this far left of label
        line_len = None
        marker_d = None
        hcolor = None
        sx0 = sx1 = None     # the line sample's actual x-span (for spacing fidelity)
        dashes = None        # the line sample's dash pattern (for line-style fidelity)
        for p in paths:
            bx0, by0, bx1, by1 = p.bbox
            pcy = 0.5 * (by0 + by1)
            if abs(pcy - cy) > rowtol:
                continue
            if not (lx - gap <= bx1 <= lx + 2.0):     # right edge just left of label
                continue
            bw, bh = bx1 - bx0, by1 - by0
            col = p.fill if p.fill is not None else p.stroke
            if bh <= 1.5 and bw >= 4.0:               # a line sample
                if bw >= (line_len or 0.0):           # widest sample = the handle
                    sx0, sx1, dashes = bx0, bx1, p.dashes
                line_len = max(line_len or 0.0, bw)
                hcolor = hcolor or col
            elif 1.5 <= bw <= 0.12 * rw and 1.5 <= bh <= 0.12 * rh:  # a marker
                marker_d = max(marker_d or 0.0, max(bw, bh))
                hcolor = hcolor or col
        if line_len is None and marker_d is None:
            e["handle"] = None                        # inline label: no sample
        else:
            e["handle"] = {
                "line_len_frac": round(line_len / rw, 4) if line_len else None,
                # the sample's ACTUAL x-span in axes fraction -> the drawer keeps
                # the original handle position and handle->text gap (spacing).
                "x0_frac": round((sx0 - rx0) / rw, 4) if sx0 is not None else None,
                "x1_frac": round((sx1 - rx0) / rw, 4) if sx1 is not None else None,
                "dashes": dashes,                     # line-style fidelity
                "marker_d": round(marker_d, 2) if marker_d else None,
                "color": list(hcolor) if hcolor else None,
            }


def _recover_ocr_legend(text_style, d, page, fitz_page):
    """Populate text_style['legend'] for a glyph-outline / math legend via OCR.

    Calls labels.recover_glyph_legend (handle column + per-row OCR), converts the
    pixel entries to axes-fraction render entries with self-contained handle
    geometry, and computes the frame bbox. No-op when OCR is unavailable or no
    handle legend is found."""
    from .labels import recover_glyph_legend, _detect_handle_legend
    from .model import Region
    rbbox = d["source"]["region_bbox"]
    region = Region(bbox=tuple(rbbox))
    px = recover_glyph_legend(region, page.paths, [], fitz_page)
    if not px:
        return
    rx0, ry0, rx1, ry1 = rbbox
    rw = (rx1 - rx0) or 1.0
    rh = (ry1 - ry0) or 1.0
    size = text_style.get("base_font_size") or 8.0
    ents = []
    for e in px:
        ents.append({
            "label": e["label"],
            "x_frac": round((e["label_x0"] - rx0) / rw, 4),
            "y_frac": round(1.0 - (e["cy"] - ry0) / rh, 4),
            "size": size, "bold": False, "italic": False,
            # Per-entry label TEXT colour (chromatic only; black/grey -> None),
            # so a coloured OCR-legend label renders in its hue instead of black.
            "text_color": _text_color(e.get("text_color")),
            "handle": {
                "line_len_frac": round(e["line_len"] / rw, 4) if e.get("line_len") else None,
                "marker_d": round(e["marker_d"], 2) if e.get("marker_d") else None,
                "marker": e.get("marker"),
                "color": e.get("color"),
            },
        })
    box = _detect_handle_legend(region, page.paths, None)
    bbox_frac = None
    if box:
        bx0, by0, bx1, by1 = box
        bbox_frac = [round((bx0 - rx0) / rw, 4), round(1.0 - (by1 - ry0) / rh, 4),
                     round((bx1 - rx0) / rw, 4), round(1.0 - (by0 - ry0) / rh, 4)]
    text_style["legend"] = {
        "orientation": "vertical", "ncol": 1, "fontsize": size, "bold": False,
        "order": [e["label"] for e in ents], "entries": ents,
        "bbox_frac": bbox_frac, "ocr_recovered": True,
    }
    text_style["show_legend"] = True


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
    series_labels = [s.get("label") for s in d.get("series", [])]
    # When NO series carries a label (e.g. a colour-ambiguous legend whose entries
    # could not be assigned to series), recover the legend's OWN labels from its
    # structure so the text legend recovers normally (frame + entries) instead of
    # falling through to the OCR-glyph path. Only runs in that gap, so well-
    # labelled charts are unaffected.
    legend_labels = None
    if not any(series_labels):
        try:
            from .labels import detect_labels as _detect_labels
            from .model import Region as _Region
            _lbs = _detect_labels(_Region(bbox=tuple(rbbox)), page.paths, page.texts)
            legend_labels = [e[2] for e in (_lbs.legend or [])
                             if len(e) > 2 and e[2] and str(e[2]).strip()]
        except Exception:
            legend_labels = None
    text_style = recover_text_style(
        fitz_page, rbbox,
        {"x": xa.get("title"), "y": ya.get("title")},
        series_labels, ttl, tick_labels, legend_labels=legend_labels)
    # Glyph-outline / math legend: the text matcher found no entries (labels are
    # vector outlines, not selectable text). If a handle column is present, OCR
    # each row's label into an editable string and synthesise self-contained
    # render entries. Gated by OCR availability (no-op when PDFCHART_OCR=0).
    if text_style is not None and not ((text_style.get("legend") or {}).get("entries")):
        _recover_ocr_legend(text_style, d, page, fitz_page)
    # Record each legend entry's real swatch geometry (line length / marker
    # diameter, or handle=None for inline labels) so the drawer matches the
    # original's sample size instead of synthesising it.
    _attach_legend_handle_geometry(text_style, page.paths, rbbox)
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
    style["round_caps"] = meta.get("round_caps", False)
    style["legend_box"] = meta.get("legend_box", False)
    style["legend_frame"] = meta.get("legend_frame")  # border/fill/corner style
    style["axis_color"] = meta.get("axis_color")  # coloured axes/frame (None=black)
    # Axis-spanning highlight bands (axvspan/axhspan-style shaded regions). The
    # true plotting box (axis spine-to-spine) is the band's reference extent; fall
    # back to the region bbox when a pixel_range is unavailable.
    px, py = xa.get("pixel_range"), ya.get("pixel_range")
    plot_box = ((px[0], py[0], px[1], py[1])
                if (px and py and None not in px and None not in py) else rbbox)
    # Axes background: a non-white fill covering ~the full plot box interior,
    # applied below grid + data (renderer ax.set_facecolor). Recovered before
    # spans so a centred full-box fill is the background, not a (rejected) band.
    axes_facecolor = recover_axes_facecolor(page.paths, plot_box)
    if axes_facecolor:
        style["axes_facecolor"] = axes_facecolor
    spans = recover_spans(page.paths, plot_box,
                          xa.get("calibration"), ya.get("calibration"))
    if spans:
        style["spans"] = spans
    # Curve-bounded confidence/uncertainty bands (filled fill_between regions).
    bands = recover_bands(page.paths, plot_box,
                          xa.get("calibration"), ya.get("calibration"))
    if bands:
        style["bands"] = bands
    return style
