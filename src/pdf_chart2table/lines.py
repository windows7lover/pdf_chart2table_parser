"""Extract marker-less data curves (polylines) from a chart region.

A line chart with no markers draws each series as one (or a few) stroked open
polyline in a series colour. Curves come in three drawn forms, all handled:
  * one long SOLID polyline (the classic case);
  * one long DASHED / DOTTED polyline carrying the full geometry;
  * many short same-colour SEGMENTS (a dash-dot curve drawn as fragments), which
    we collect and join, x-ordered, into one polyline.
We return each curve's vertices in pixel space, mapped to data by calibration.

What we keep (precision over recall):
  * OPEN stroked polylines with real 2D extent, off the region border and out of
    the legend, in a *saturated* (non-gray) series colour — OR a *dashed* curve
    in any colour (a dashed multi-vertex path is unambiguously a data curve, not
    a solid 2-point gridline/spine), so black/gray dashed curves are kept too;
  * a *solid* black/gray curve when it is unambiguously DATA: many vertices, a
    wide span, varying in BOTH axes and in the plot interior (so black ResNet /
    optimizer curves are recovered) — while gridlines/spines/boxes are excluded.
We drop:
  * near-white strokes (plot background / frame);
  * gray / black strokes that are gridlines / spines / small boxes (axis-aligned,
    ~1-D, few-vertex or low-span — i.e. not a traced curve);
  * axis-aligned 1-D segments (ticks / spines / gridlines);
  * legend swatches (short, near / inside the legend text block);
  * any colour already claimed by a marker series (line+marker plots -> use the
    markers; see ``classify_lines(..., marker_colors=...)``).

A colour's curve is "clean" only if its parts concatenate / merge into ONE curve
that is monotone in x (a single-valued function). A colour may carry two genuinely
different curves drawn in different dash forms (e.g. a solid Testing curve and a
dashed Training curve in the same condition colour); we key candidate curves by
``(colour, dash-form)`` so BOTH are kept when their y-trajectories differ. When a
colour is drawn in several forms that trace the SAME path (a coarse dashed path
plus fine dash-dot fragments, or one curve stroked solid+dashed on top of itself)
the forms overlap in both x and y, so the widest (best-covering) one is kept and
the redundant ones dropped. Parts that overlap in x and cannot be ordered are
SKIPPED (logged), never guessed.

Public API:
    classify_lines(region, paths, texts, marker_colors) -> (list[SeriesLine], list[str])
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .model import Color, Path, Region, TextSpan

# A data curve must span at least this fraction of the region on its long axis.
_MIN_SPAN_FRAC = 0.25
# A long polyline needs at least this many vertices to be a curve on its own.
_MIN_VERTS = 3
# A merged fragment curve needs at least this many joined points to be a curve
# (rules out a couple of stray same-colour ticks getting merged into "a curve").
_MIN_FRAG_POINTS = 8
# A fragment is a short dash/dash-dot SEGMENT: at most this many vertices. A dense
# many-vertex glyph (a marker circle/star outline) is a marker, not a curve
# fragment, so it must not be collected and merged into a fake curve.
_MAX_FRAG_VERTS = 6
# A stroke is a saturated series colour if its RGB spread exceeds this; grays /
# blacks / whites (gridlines, spines) fall below it.
_SAT_SPREAD = 0.2
# An unsaturated (black/gray) stroke qualifies as a DATA curve only if it varies
# in BOTH axes: its shorter bbox side is at least this fraction of its longer
# side. Gridlines / spines are ~1-D (one side ~0) and fall below it, so they are
# rejected even when long and interior.
_MIN_2D_RATIO = 0.08
# A black/gray (low-saturation) curve is inherently ambiguous with boxes / glyphs
# / gridlines, so we admit it as DATA only when it is clearly a traced curve:
# many vertices and a wide span. A small few-vertex gray box / legend frame falls
# below these and is rejected (precision over recall on the ambiguous low-sat case).
_MIN_LOWSAT_VERTS = 8
_MIN_LOWSAT_SPAN_FRAC = 0.4
# A near-white stroke (min channel above this) is the plot background / frame,
# never a data curve.
_WHITE_MIN = 0.9
# Centroid within this of the region border => on a spine/frame edge.
_BORDER_TOL = 2.0
# Legend swatch: a short colored segment within this gap left of legend text.
_LEGEND_GAP = 40.0
# Two same-colour curves "overlap" (and so cannot be cleanly separated) if their
# x-ranges share more than this fraction of the smaller range.
_OVERLAP_FRAC = 0.5
# Two same-colour curves of different dash form are the SAME path drawn twice
# (so dedup to one) when, over their shared x range, their y values agree within
# this fraction of the combined y-extent; beyond it they are distinct curves.
_SAME_CURVE_YTOL = 0.1


@dataclass
class SeriesLine:
    """One clean line series: its ordered pixel vertices and colour/width/dash."""

    color: Color | None
    width: float | None
    dashes: str | None
    points: list[tuple[float, float]] = field(default_factory=list)


def _round_color(c: Color | None) -> tuple | None:
    return tuple(round(v, 2) for v in c) if c is not None else None


def _is_saturated(c: Color | None) -> bool:
    return c is not None and (max(c) - min(c)) > _SAT_SPREAD


def _is_near_white(c: Color | None) -> bool:
    return c is not None and min(c) >= _WHITE_MIN


def _varies_2d(p: Path) -> bool:
    """True if the path bends through BOTH axes (a real curve), not a straight
    axis-aligned line. Gridlines/spines are ~1-D (one bbox side ~0) and fail it,
    so a black/gray path that passes is a data curve, not a gridline/spine."""
    b = p.bbox
    bw, bh = b[2] - b[0], b[3] - b[1]
    long, short = max(bw, bh), min(bw, bh)
    return long > 0 and short / long >= _MIN_2D_RATIO


def _is_data_lowsat(p: Path, region: Region) -> bool:
    """An unsaturated (black/gray) stroke that is clearly a DATA curve: a long,
    multi-vertex, 2-D, interior open path (not a 2-point or axis-aligned grid /
    spine). Dashed black/gray curves also qualify and are handled by the caller;
    this guards the SOLID black/gray case that ``_is_saturated`` used to drop.
    Requires many vertices and a wide span so a small gray box / legend frame is
    not mistaken for data (precision over recall on the ambiguous low-sat case)."""
    if len(p.points) < _MIN_LOWSAT_VERTS or _is_near_white(p.stroke):
        return False
    if not _varies_2d(p):
        return False
    b = p.bbox
    bw, bh = b[2] - b[0], b[3] - b[1]
    rw = region.bbox[2] - region.bbox[0]
    rh = region.bbox[3] - region.bbox[1]
    if max(bw, bh) < _MIN_LOWSAT_SPAN_FRAC * min(rw, rh):
        return False
    cx, cy = 0.5 * (b[0] + b[2]), 0.5 * (b[1] + b[3])
    return not _on_border(cx, cy, region)


def _on_border(cx: float, cy: float, region: Region) -> bool:
    x0, y0, x1, y1 = region.bbox
    return (
        abs(cx - x0) <= _BORDER_TOL or abs(cx - x1) <= _BORDER_TOL
        or abs(cy - y0) <= _BORDER_TOL or abs(cy - y1) <= _BORDER_TOL
    )


def _near_legend(cx: float, cy: float, texts: list[TextSpan]) -> bool:
    """Centroid sits inside the legend block: just left of, or overlapping, a
    legend text span (so swatches and the marker/segment glyphs beside the
    labels are excluded)."""
    for t in texts:
        tx0, ty0, tx1, ty1 = t.bbox
        th = ty1 - ty0
        if abs(cy - 0.5 * (ty0 + ty1)) <= 0.6 * th + 2 and tx0 - _LEGEND_GAP <= cx <= tx1:
            return True
    return False


def _off_chart(p: Path, region: Region, texts: list[TextSpan]) -> bool:
    """True if this path is on the frame border, or is a small legend glyph.

    The legend test applies only to *small* paths (swatches / marker glyphs):
    a wide data curve whose centroid happens to fall near a legend label (it can
    end up beside the legend) is data, not a swatch, so it is not excluded.
    """
    b = p.bbox
    cx, cy = 0.5 * (b[0] + b[2]), 0.5 * (b[1] + b[3])
    if _on_border(cx, cy, region):
        return True
    rw = region.bbox[2] - region.bbox[0]
    small = (b[2] - b[0]) < _MIN_SPAN_FRAC * rw
    return small and _near_legend(cx, cy, texts)


def _is_long_curve(p: Path, region: Region, texts: list[TextSpan]) -> bool:
    """A single path carrying a whole curve: open, multi-vertex, 2-D extent,
    off-axis and out of the legend. Saturated colours qualify; unsaturated ones
    (black/gray) qualify when DASHED (a dashed multi-vertex path is a data curve,
    not a solid gridline) OR when the SOLID path is clearly data: long,
    multi-vertex, 2-D and interior (``_is_data_lowsat`` -- so a black/gray data
    curve is kept while an axis-aligned/2-point gridline or spine is rejected)."""
    if p.closed or p.stroke is None:
        return False
    if len(p.points) < _MIN_VERTS:
        return False
    if (not _is_saturated(p.stroke) and p.dashes is None
            and not _is_data_lowsat(p, region)):
        return False
    b = p.bbox
    bw, bh = b[2] - b[0], b[3] - b[1]
    rw = region.bbox[2] - region.bbox[0]
    rh = region.bbox[3] - region.bbox[1]
    if max(bw, bh) < _MIN_SPAN_FRAC * min(rw, rh):
        return False
    return not _off_chart(p, region, texts)


def _is_fragment(p: Path, region: Region, texts: list[TextSpan]) -> bool:
    """A short same-colour curve fragment (a dash-dot piece) to be joined with
    its siblings: open, off the frame border and out of the legend. Saturated
    fragments qualify; unsaturated (black/gray) ones qualify only when they are
    NOT axis-aligned 1-D segments (``_varies_2d``), so black dotted curves are
    recovered while black gridlines / spines / ticks (axis-aligned) stay out.
    A dense many-vertex glyph (a marker outline) is not a segment, so it is
    excluded -- it must not be merged into a fake curve."""
    if p.closed or p.stroke is None or len(p.points) > _MAX_FRAG_VERTS:
        return False
    if not _is_saturated(p.stroke) and (_is_near_white(p.stroke) or not _varies_2d(p)):
        return False
    return not _off_chart(p, region, texts)


def _x_overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
    lo = max(a[0], b[0])
    hi = min(a[1], b[1])
    if hi <= lo:
        return False
    inter = hi - lo
    smaller = min(a[1] - a[0], b[1] - b[0])
    return smaller > 0 and inter / smaller > _OVERLAP_FRAC


def _dedupe_points(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Sort vertices by x and drop exact duplicates shared at segment joins."""
    pts = sorted(pts, key=lambda q: q[0])
    out: list[tuple[float, float]] = []
    for q in pts:
        if not out or out[-1] != q:
            out.append(q)
    return out


def _is_noise_cloud(pts: list[tuple[float, float]]) -> bool:
    """True if x-sorted ``pts`` look like a scatter / noise cloud rather than a
    coherent curve: too many x-neighbours jump by more than half the y-extent (a
    single-valued curve moves smoothly in y as x advances; a cloud is multivalued
    everywhere). Used to drop shattered/noisy candidate "series"."""
    if len(pts) < _MIN_FRAG_POINTS:
        return False  # too few to judge as a cloud; other guards handle these
    ys = [y for _, y in pts]
    yspan = max(ys) - min(ys)
    if yspan <= 0:
        return False
    big = 0.5 * yspan
    jumps = sum(abs(pts[i + 1][1] - pts[i][1]) > big for i in range(len(pts) - 1))
    return jumps > 0.2 * len(pts)


def _merge_long(parts: list[Path]) -> list[tuple[float, float]] | None:
    """Concatenate same-colour long polylines into one x-monotone curve, or None.

    Returns the vertices sorted by x if the parts do not overlap in x (so they
    tile one curve); otherwise None (ambiguous, must skip). A merged result that
    looks like a scatter / noise cloud (multivalued everywhere) is also dropped.
    """
    ranges = [(p.bbox[0], p.bbox[2]) for p in parts]
    for i in range(len(ranges)):
        for j in range(i + 1, len(ranges)):
            if _x_overlap(ranges[i], ranges[j]):
                return None
    pts: list[tuple[float, float]] = []
    for p in parts:
        pts.extend(p.points)
    pts = _dedupe_points(pts)
    if _is_noise_cloud(pts):
        return None
    return pts


def _merge_fragments(parts: list[Path]) -> list[tuple[float, float]] | None:
    """Join many short same-colour segments into one x-ordered polyline, or None.

    Collects every endpoint, orders by x and de-duplicates. The result is only a
    curve if it is roughly single-valued (``_is_noise_cloud`` rejects scatter /
    grid clouds) and has enough joined points to be a curve.
    """
    pts: list[tuple[float, float]] = []
    for p in parts:
        pts.extend(p.points)
    pts = _dedupe_points(pts)
    if len(pts) < _MIN_FRAG_POINTS:
        return None
    if _is_noise_cloud(pts):
        return None
    return pts


def _xspan(pts: list[tuple[float, float]]) -> float:
    xs = [x for x, _ in pts]
    return max(xs) - min(xs)


def _dash_form(dashes: str | None) -> str:
    """Collapse a dash pattern to its form: 'solid' or 'dashed'."""
    return "solid" if dashes is None else "dashed"


def _interp_y(pts: list[tuple[float, float]], x: float) -> float | None:
    """Linearly interpolate the curve's y at x (pts are x-sorted), or None if x
    is outside its x range."""
    if x < pts[0][0] or x > pts[-1][0]:
        return None
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        if x0 <= x <= x1:
            if x1 == x0:
                return y0
            t = (x - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return pts[-1][1]


def _same_curve(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> bool:
    """True if two x-sorted curves trace the same path (same x extent and y
    agreeing within ``_SAME_CURVE_YTOL`` of the combined y-extent over their
    shared x range) -- i.e. one curve drawn twice, to be deduped to one."""
    lo = max(a[0][0], b[0][0])
    hi = min(a[-1][0], b[-1][0])
    if hi <= lo:
        return False
    ys = [y for _, y in a] + [y for _, y in b]
    yspan = max(ys) - min(ys)
    if yspan <= 0:
        return True
    tol = _SAME_CURVE_YTOL * yspan
    for k in range(11):
        x = lo + (hi - lo) * k / 10
        ya, yb = _interp_y(a, x), _interp_y(b, x)
        if ya is None or yb is None:
            continue
        if abs(ya - yb) > tol:
            return False
    return True


def classify_lines(
    region: Region,
    paths: list[Path],
    texts: list[TextSpan],
    marker_colors: set[tuple] | None = None,
) -> tuple[list[SeriesLine], list[str]]:
    """Extract clean marker-less line series from ``region``.

    ``marker_colors`` is the set of rounded colours already emitted as marker
    series; line curves of those colours are dropped (line+marker dedupe).

    Each colour may be drawn as one long solid/dashed path, several x-disjoint
    long paths, or many short fragments, and may carry two genuinely different
    curves in different dash forms (e.g. a solid Testing and a dashed Training
    curve in one condition colour). We build a candidate curve per
    ``(colour, dash-form)``; per colour we then drop candidates that trace the
    SAME path as a wider one (one curve drawn twice) but KEEP those with a
    distinct y-trajectory. Returns ``(series, reasons)`` where ``reasons`` logs
    each colour/form found but skipped (curves that cannot be cleanly ordered).
    """
    marker_colors = marker_colors or set()
    region_texts = [texts[i] for i in region.text_indices]

    # Per (colour, dash-form): long-path parts; fragments are dash-dot pieces.
    long_groups: dict[tuple, list[Path]] = defaultdict(list)
    frag_groups: dict[tuple, list[Path]] = defaultdict(list)
    for i in region.path_indices:
        p = paths[i]
        color = _round_color(p.stroke)
        if _is_long_curve(p, region, region_texts):
            long_groups[(color, _dash_form(p.dashes))].append(p)
        elif _is_fragment(p, region, region_texts):
            frag_groups[(color, _dash_form(p.dashes))].append(p)

    # Build candidate curves per (colour, dash-form) (a curve plus exemplar path).
    candidates: dict[tuple, list[tuple[list[tuple[float, float]], Path]]] = defaultdict(list)
    reasons: list[str] = []
    for (color, _form), parts in long_groups.items():
        if color in marker_colors:
            continue
        verts = _merge_long(parts)
        if verts is None:
            reasons.append(f"line color {color}: overlapping curves, cannot separate")
            continue
        candidates[color].append((verts, parts[0]))
    for (color, _form), parts in frag_groups.items():
        if color in marker_colors:
            continue
        verts = _merge_fragments(parts)
        if verts is None:
            continue  # too few / multivalued fragments: not a usable curve
        candidates[color].append((verts, parts[0]))

    # Per colour: keep candidate forms with distinct y-trajectories; dedup forms
    # that trace the same path (one curve drawn twice -> keep the widest).
    series: list[SeriesLine] = []
    for color, cands in candidates.items():
        kept: list[tuple[list[tuple[float, float]], Path]] = []
        for verts, ex in sorted(cands, key=lambda c: -_xspan(c[0])):
            if any(_same_curve(verts, k[0]) for k in kept):
                continue
            kept.append((verts, ex))
        for verts, ex in kept:
            series.append(SeriesLine(color=ex.stroke, width=ex.width,
                                     dashes=ex.dashes, points=verts))
    return series, reasons
