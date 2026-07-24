"""Axis / tick / tick-label detection within a chart region.

For a ``Region`` we locate the bottom (x) and left (y) axis spines, the short
perpendicular tick-mark line segments along each spine, and the numeric tick
*labels* (``TextSpan``s just outside the spine). Each tick mark is paired with
the nearest aligned label, whose text is parsed to a float.

Matplotlib quirks handled here (verified on the fixtures):

* Tick marks are short (~3.5pt) ``l`` segments perpendicular to the spine and
  just *outside* the plot area (below the bottom spine / left of the left spine).
* Log tick labels render as a power of ten: a ``"10"`` mantissa span plus a
  smaller superscript exponent span just to its upper-right -> ``10**exp``.
* The minus sign is **not** emitted as text. It is drawn as a small filled
  horizontal bar (a path glyph) immediately left of the leftmost digit span.
  This affects both linear (``-0.5``) and log (``10^{-4}``) labels, so we scan
  for that glyph and negate accordingly.

Public API:
    detect_axes(region, paths, texts) -> (x_axis, y_axis)   # Axis with ticks
"""

from __future__ import annotations

import math
import re as _re

from .model import Axis, Path, Region, Tick, TextSpan

# π-axis tick labels: "π", "2π", "-π", "π/2", "3π/2", "0.5π" -> a numeric value.
# (The π glyph U+03C0 survives extraction; "2π" arrives as separate "2"+"π" spans
# that _label_value joins before parsing.)
_PI_RE = _re.compile(r"^([+-]?\d*\.?\d*)π(?:/(\d+\.?\d*))?$")


def _parse_pi(s: str) -> float | None:
    m = _PI_RE.match(s.strip().replace("−", "-").replace(" ", ""))
    if not m:
        return None
    coeff_s, denom_s = m.group(1), m.group(2)
    coeff = 1.0 if coeff_s in ("", "+") else (-1.0 if coeff_s == "-" else None)
    if coeff is None:
        try:
            coeff = float(coeff_s)
        except ValueError:
            return None
    try:
        denom = float(denom_s) if denom_s else 1.0
    except ValueError:
        return None
    return coeff * math.pi / denom if denom else None
from .primitives import (
    bbox_center as _center,
    is_saturated as _is_saturated,
    join_scripts as _join_scripts,
)

# Geometry tolerances (PDF points).
_SPINE_TOL = 8.0        # how far a tick may sit from the spine coordinate
_TICK_LEN_MAX = 7.0     # max length of a tick-mark segment along its long axis
_TICK_THIN_MAX = 2.0    # max thickness of a tick-mark segment (perp. extent)
_CLUSTER_TOL = 2.0      # merge tick marks whose positions are within this
# Tick labels sit in a THIN band just outside the spine (x-labels directly below
# the bottom spine, y-labels directly left of the left spine). Keep it tight so
# axis titles, captions, legend numbers and panel sub-captions stay out. The
# x-band (vertical, below the spine) must exclude the axis-title row and any
# sub-caption, so it is tight; the y-band (horizontal, left of the spine) is a
# touch wider to admit a multi-digit / "10" mantissa label.
_LABEL_BAND = 18.0      # x: how far below the bottom spine to look for labels
_YLABEL_BAND = 30.0     # y: how far left of the left spine to look for labels
_ALIGN_TOL = 8.0        # perpendicular alignment tolerance tick<->label center
# Spans belonging to ONE tick label (mantissa "10" + raised exponent, or the
# digits of one number) are on the SAME row and within this horizontal gap.
# Neighbouring ticks are separated instead by their (vertical) tick spacing, so
# the row check -- not this gap -- is what keeps "100"+"90" from merging.
_LABEL_GROUP_GAP = 8.0
# Two SAME-size spans only join into one number when they nearly touch (the
# digits / decimal point of one value, e.g. "0"+"."+"5"). Same-size spans with a
# larger gap are distinct neighbouring ticks (e.g. "10000" "20000") and must NOT
# merge. A raised/smaller exponent (different size) may sit up to the full group
# gap away from its "10" mantissa, so the size-mismatch case keeps the wide gap.
_TOUCH_GAP = 3.0
_SIZE_TOL = 0.6         # font-size diff above which spans are mantissa+exponent
_MINUS_GAP = 3.5        # max gap between a minus glyph and the digit it negates
# A primary-axis tick label sits in a consistent COLUMN (y-axis: shared right
# edge near the spine) / ROW (x-axis: shared top edge just below the spine).
# A label leaking from a neighbouring panel or a secondary (twin) axis lands in
# a different column/row; drop groups whose alignment coordinate is this far from
# the dominant one. (Real per-panel labels align to < 1pt; this is generous.)
_COLUMN_TOL = 6.0
# Minus-sign glyph: a small filled, unstroked, flat horizontal bar.
_MINUS_W = (1.5, 9.0)
_MINUS_H = 2.5


def _is_minus_glyph(p: Path) -> bool:
    b = p.bbox
    w, h = b[2] - b[0], b[3] - b[1]
    # A real minus sign is a simple flat filled rectangle: very few path points
    # (typically 5 for a rect).  A circle/ring or complex glyph passing the
    # bounding-box test has many more points; reject it to avoid false negatives.
    return (
        p.fill is not None
        and p.stroke is None
        and _MINUS_W[0] <= w <= _MINUS_W[1]
        and h <= _MINUS_H
        and len(p.points) <= 8
    )


def _cluster(values: list[float]) -> list[float]:
    """Average together positions within ``_CLUSTER_TOL`` of each other."""
    out: list[float] = []
    group: list[float] = []
    for v in sorted(values):
        if group and v - group[-1] > _CLUSTER_TOL:
            out.append(sum(group) / len(group))
            group = []
        group.append(v)
    if group:
        out.append(sum(group) / len(group))
    return out


# --------------------------------------------------------------------------
# Label parsing
# --------------------------------------------------------------------------

# Common axis-label unit suffixes -> multiplier (e.g. "5M" steps, "10k", "2G").
_SUFFIX_MULT = {"k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "B": 1e9, "T": 1e12}


def _split_unit(s: str) -> tuple[str, float]:
    """Strip a trailing ``%`` and/or one SI-ish suffix, returning (core, mult).

    ``"5M" -> ("5", 1e6)``, ``"50%" -> ("50", 1.0)``, ``"3.2" -> ("3.2", 1.0)``.
    """
    s = s.strip()
    if s.endswith("%"):
        s = s[:-1].strip()
    mult = 1.0
    if len(s) > 1 and s[-1] in _SUFFIX_MULT:
        mult = _SUFFIX_MULT[s[-1]]
        s = s[:-1].strip()
    return s, mult


def _is_numeric_span(text: str) -> bool:
    """True for spans that can be part of a numeric tick label.

    Tick labels are digit runs, decimals, ``"10"`` mantissas, exponents,
    scientific notation, or a number with a unit suffix (``5M``, ``10k``, ``50%``).
    This excludes axis-title words (``x``, ``time``, ``loss`` ...) that may align
    with a tick.
    """
    s = text.strip().replace("−", "-")
    if not s:
        return False
    if _parse_pi(s) is not None:
        return True  # π / 2π / π/2 ... : a numeric π-axis tick span
    core, _ = _split_unit(s)
    if not core:
        return False
    if all(c in ".+-" for c in core):
        return True  # lone decimal point / sign span: joins with adjacent digits
    return (any(c.isdigit() for c in core)
            and all(c in "0123456789.+-eE" for c in core))


def _parse_plain(text: str) -> float | None:
    """Parse an ordinary numeric label (decimal, scientific, unicode minus,
    optional unit suffix like ``5M`` / ``10k`` / ``50%``)."""
    s = text.strip().replace("−", "-").replace("×", "x")
    if not s:
        return None
    pv = _parse_pi(s)
    if pv is not None:
        return pv
    core, mult = _split_unit(s)
    try:
        return float(core) * mult
    except ValueError:
        return None


def _label_value(spans: list[TextSpan], negative: bool) -> float | None:
    """Turn a group of spans (mantissa + optional superscript) into a value.

    A power-of-ten label is two spans: ``"10"`` then a small higher exponent
    span. Otherwise it is a single ordinary numeric span.
    """
    spans = sorted(spans, key=lambda t: t.bbox[0])
    texts = [t.text.strip() for t in spans]

    if texts and texts[0] == "10" and len(spans) >= 2:
        # Exponent = remaining (smaller / raised) spans concatenated.
        exp_txt = "".join(texts[1:])
        try:
            exp = float(exp_txt)
        except ValueError:
            return None
        if negative:
            exp = -exp
        if abs(exp) > 300:  # spurious concatenation: not a real power-of-ten tick
            return None
        return 10.0 ** exp

    if len(spans) == 1:
        v = _parse_plain(texts[0])
        if v is None:
            return None
        return -v if negative else v

    # Multi-span non-power label: join and try once.
    v = _parse_plain("".join(texts))
    if v is None:
        return None
    return -v if negative else v


# --------------------------------------------------------------------------
# Tick-mark detection
# --------------------------------------------------------------------------

def _chromatic_mark(p: Path) -> bool:
    """True when a thin tick-candidate is a saturated DATA colour, not the neutral
    (black/grey) colour a real tick mark is drawn in.

    A '|'/thin-diamond/small-'+' data marker sitting on a spine is geometrically
    indistinguishable from a tick, so the color-blind position scans below would
    vote it as a tick and pollute calibration. A genuine tick is drawn in the
    spine colour (neutral, low RGB spread); exclude only *clearly* chromatic
    candidates -- a black data marker on the spine cannot be told from a tick by
    colour and is left alone."""
    return _is_saturated(p.stroke) or _is_saturated(p.fill)


def _x_tick_positions(paths: list[Path], region: Region):
    """X pixel positions of bottom-axis tick marks + their direction ("in"/"out").

    Detects ticks that point EITHER inward (up into the plot) or outward (down
    below the spine) -- charts with only inward ticks previously yielded no ticks
    and so could not be calibrated."""
    x0, y0, x1, y1 = region.bbox
    xs: list[float] = []
    lengths: list[float] = []
    inward = outward = 0   # voted across BOTH the bottom and top spines
    for p in paths:
        b = p.bbox
        w, h = b[2] - b[0], b[3] - b[1]
        if w > _TICK_THIN_MAX or not (0 < h <= _TICK_LEN_MAX):
            continue
        if _chromatic_mark(p):    # saturated data marker on the spine, not a tick
            continue
        cx = 0.5 * (b[0] + b[2])
        if not (x0 - _SPINE_TOL <= cx <= x1 + _SPINE_TOL):
            continue
        near_bottom = abs(b[1] - y1) <= _SPINE_TOL or abs(b[3] - y1) <= _SPINE_TOL
        near_top = abs(b[1] - y0) <= _SPINE_TOL or abs(b[3] - y0) <= _SPINE_TOL
        if near_bottom:
            # bottom ticks carry the labels -> use them for calibration positions
            xs.append(cx)
            lengths.append(h)
            if b[1] < y1 - 1.0:    # extends up into the plot
                inward += 1
            if b[3] > y1 + 1.0:    # extends down below the spine
                outward += 1
        elif near_top:
            lengths.append(h)
            if b[3] > y0 + 1.0:    # extends down into the plot
                inward += 1
            if b[1] < y0 - 1.0:    # extends up above the spine
                outward += 1
    direction = ("in" if inward >= outward else "out") if (inward or outward) else None
    length = sorted(lengths)[len(lengths) // 2] if lengths else None
    return _cluster(xs), direction, length


def _y_tick_positions(paths: list[Path], region: Region):
    """Y pixel positions of left-axis tick marks + their direction ("in"/"out")."""
    x0, y0, x1, y1 = region.bbox
    ys: list[float] = []
    lengths: list[float] = []
    inward = outward = 0   # voted across BOTH the left and right spines
    for p in paths:
        b = p.bbox
        w, h = b[2] - b[0], b[3] - b[1]
        if h > _TICK_THIN_MAX or not (0 < w <= _TICK_LEN_MAX):
            continue
        if _chromatic_mark(p):    # saturated data marker on the spine, not a tick
            continue
        cy = 0.5 * (b[1] + b[3])
        if not (y0 - _SPINE_TOL <= cy <= y1 + _SPINE_TOL):
            continue
        near_left = abs(b[2] - x0) <= _SPINE_TOL or abs(b[0] - x0) <= _SPINE_TOL
        near_right = abs(b[2] - x1) <= _SPINE_TOL or abs(b[0] - x1) <= _SPINE_TOL
        if near_left:
            ys.append(cy)
            lengths.append(w)
            if b[2] > x0 + 1.0:    # extends right into the plot
                inward += 1
            if b[0] < x0 - 1.0:    # extends left outside the spine
                outward += 1
        elif near_right:
            lengths.append(w)
            if b[0] < x1 - 1.0:    # extends left into the plot
                inward += 1
            if b[2] > x1 + 1.0:    # extends right outside the spine
                outward += 1
    direction = ("in" if inward >= outward else "out") if (inward or outward) else None
    length = sorted(lengths)[len(lengths) // 2] if lengths else None
    return _cluster(ys), direction, length


# --------------------------------------------------------------------------
# Shared axis-aligned-segment classifier (ticks + gridlines are one family)
# --------------------------------------------------------------------------

# A gridline spans at least this fraction of the plot on its long side. Tick
# marks are short (<= _TICK_LEN_MAX); gridlines run across the interior.
_GRID_SPAN_FRAC = 0.6


def axis_segments(paths: list[Path], region: Region) -> list[dict]:
    """Classify thin axis-aligned segments in a region into one primitive family.

    A tick mark and a gridline are the SAME primitive -- a thin axis-aligned
    segment at an axis coordinate -- differing only in length/position: a tick is
    the short segment AT a spine; a gridline is the full-span segment in the
    INTERIOR at the same coordinate. This scan is COLOR-AGNOSTIC (it gates on
    geometry, like the tick scan), which is why it catches dark/dashed grids that
    the old grey-only ``grid._is_grey`` gate missed.

    Returns one dict per thin axis-aligned segment::

        {"orient": "v"|"h", "coord": float, "length": float, "lo": float,
         "hi": float, "role": str, "stroke": Color|None, "width": float|None,
         "dashes": str|None}

    ``role`` is "spine" (full-span, on a border), "gridline" (full-span,
    interior), "tick" (short, near a spine) or "other". ``coord`` is the axis
    coordinate: cx for vertical segments (an x position), cy for horizontal.
    ``lo``/``hi`` are the segment's extent along its long axis (y for vertical,
    x for horizontal) -- used to union collinear dash fragments into one line.
    """
    x0, y0, x1, y1 = region.bbox
    w, h = x1 - x0, y1 - y0
    out: list[dict] = []
    if w <= 0 or h <= 0:
        return out
    for i in getattr(region, "path_indices", []):
        p = paths[i]
        if len(p.points) < 2:
            continue
        b = p.bbox
        bw, bh = b[2] - b[0], b[3] - b[1]
        cx, cy = 0.5 * (b[0] + b[2]), 0.5 * (b[1] + b[3])
        if bw <= _TICK_THIN_MAX and bh > bw:           # vertical segment
            orient, coord, length, lo, hi = "v", cx, bh, b[1], b[3]
            on_border = abs(cx - x0) <= _SPINE_TOL or abs(cx - x1) <= _SPINE_TOL
            full_span = bh > _GRID_SPAN_FRAC * h
            interior = x0 + 2 < cx < x1 - 2
            near_spine = (abs(b[1] - y1) <= _SPINE_TOL or abs(b[3] - y1) <= _SPINE_TOL
                          or abs(b[1] - y0) <= _SPINE_TOL or abs(b[3] - y0) <= _SPINE_TOL)
        elif bh <= _TICK_THIN_MAX and bw > bh:         # horizontal segment
            orient, coord, length, lo, hi = "h", cy, bw, b[0], b[2]
            on_border = abs(cy - y0) <= _SPINE_TOL or abs(cy - y1) <= _SPINE_TOL
            full_span = bw > _GRID_SPAN_FRAC * w
            interior = y0 + 2 < cy < y1 - 2
            near_spine = (abs(b[0] - x0) <= _SPINE_TOL or abs(b[2] - x0) <= _SPINE_TOL
                          or abs(b[0] - x1) <= _SPINE_TOL or abs(b[2] - x1) <= _SPINE_TOL)
        else:
            continue
        if full_span and on_border:
            role = "spine"
        elif full_span and interior:
            role = "gridline"
        elif length <= _TICK_LEN_MAX and near_spine:
            role = "tick"
        else:
            role = "other"
        out.append({"orient": orient, "coord": coord, "length": length,
                    "lo": lo, "hi": hi, "role": role, "stroke": p.stroke,
                    "width": p.width, "dashes": p.dashes,
                    "stroke_alpha": p.stroke_alpha})
    return out


# --------------------------------------------------------------------------
# Tick <-> label pairing
# --------------------------------------------------------------------------

# A y-axis tick label sits to the LEFT of the left spine and centres precisely
# (to within this tolerance) on its y-tick mark.  The bottom-most such label can
# straddle the bottom-left corner and leak into the x-label band, where it then
# merges with the x-origin label (e.g. y-tick "5" + x-tick "0" -> bogus "50").
# Genuine x-tick labels sit BELOW all y-ticks, so they never align this tightly
# with a y-tick mark; this lets us drop the corner y-label without losing the
# x-origin label.  Tighter than _ALIGN_TOL on purpose.
_YTICK_ALIGN_TOL = 2.5


def _x_label_spans(
    texts: list[TextSpan], region: Region, paths: list[Path] | None = None,
    ytick_ys: list[float] | None = None,
) -> list[TextSpan]:
    """Text spans sitting in the label band just below the bottom spine.

    When ``paths`` is supplied, a span at/left of the left spine that aligns
    tightly (in y) with a detected y-axis tick mark is treated as a y-axis tick
    label leaking into the bottom-left corner and excluded -- otherwise it would
    merge with the x-origin label into a spurious x-tick value.

    ``ytick_ys`` may be passed pre-computed (``_y_tick_positions(paths, region)
    [0]``) to avoid recomputing the y-tick scan; when None it is computed here.
    """
    x0, _, x1, y1 = region.bbox
    if ytick_ys is None:
        ytick_ys = _y_tick_positions(paths, region)[0] if paths is not None else []
    out = []
    for t in texts:
        cx, cy = _center(t.bbox)
        if not (y1 < cy <= y1 + _LABEL_BAND and x0 - _LABEL_BAND <= cx <= x1 + _LABEL_BAND):
            continue
        if cx <= x0 and any(abs(cy - yp) <= _YTICK_ALIGN_TOL for yp in ytick_ys):
            continue  # bottom y-axis tick label straddling the corner
        out.append(t)
    return out


def _y_label_spans(texts: list[TextSpan], region: Region) -> list[TextSpan]:
    """Text spans sitting in the label band just left of the left spine."""
    x0, y0, _, y1 = region.bbox
    out = []
    for t in texts:
        cx, cy = _center(t.bbox)
        if x0 - _YLABEL_BAND <= cx < x0 and y0 - _ALIGN_TOL <= cy <= y1 + _ALIGN_TOL:
            out.append(t)
    return out


def _has_minus(paths: list[Path], y_center: float, digit_left_x: float) -> bool:
    """Is there a minus-sign glyph immediately left of the digit at ``digit_left_x``?

    The glyph must be vertically aligned (same label row) AND horizontally
    adjacent (its right edge within ``_MINUS_GAP`` of the digit's left edge), so a
    minus belonging to a different label sharing the baseline is not picked up.
    """
    for p in paths:
        if not _is_minus_glyph(p):
            continue
        _, cy = _center(p.bbox)
        if abs(cy - y_center) <= _ALIGN_TOL and -_MINUS_GAP <= digit_left_x - p.bbox[2] <= _MINUS_GAP + 4:
            return True
    return False


def _group_labels(spans: list[TextSpan], axis: str) -> list[list[TextSpan]]:
    """Cluster numeric spans into one group per tick label.

    Spans of a single label (a plain number, or a ``"10"`` mantissa plus its
    raised exponent) share the same text *row* and are horizontally adjacent;
    distinct ticks are separated by the tick spacing. We therefore group by
    shared row (y-center within a label's height) AND a small horizontal gap, so
    two neighbouring ticks never merge into one (e.g. "100"+"90" -> "10090").
    Only numeric spans count.
    """
    spans = [t for t in spans if _is_numeric_span(t.text)]
    spans = sorted(spans, key=lambda t: t.bbox[0])
    groups: list[list[TextSpan]] = []
    for t in spans:
        cy = _center(t.bbox)[1]
        placed = False
        for g in groups:
            gcy = sum(_center(s.bbox)[1] for s in g) / len(g)
            # Same row, and horizontally adjacent to the group. One label is the
            # digits/decimal of one number (same size, touching) OR a "10"
            # mantissa plus its raised, smaller exponent (size mismatch). Two
            # same-size numbers with a real gap are distinct ticks -> never merge.
            gx0 = min(s.bbox[0] for s in g)
            gx1 = max(s.bbox[2] for s in g)
            gap = max(t.bbox[0] - gx1, gx0 - t.bbox[2])
            gsz = max((s.size for s in g if s.size), default=None)
            size_mismatch = (
                gsz is not None and t.size is not None and abs(gsz - t.size) > _SIZE_TOL
            )
            limit = _LABEL_GROUP_GAP if size_mismatch else _TOUCH_GAP
            if abs(cy - gcy) <= _ALIGN_TOL and gap <= limit:
                g.append(t)
                placed = True
                break
        if not placed:
            groups.append([t])
    return groups


def _align_coord(g: list[TextSpan], axis: str) -> float:
    """The coordinate that primary-axis labels share: right edge (y) / top (x)."""
    if axis == "y":
        return max(s.bbox[2] for s in g)   # right edge, abutting the left spine
    return min(s.bbox[1] for s in g)       # top edge, the row just below the spine


def _primary_column(groups: list[list[TextSpan]], axis: str) -> list[list[TextSpan]]:
    """Keep only label groups in the primary axis's column/row.

    Genuine bottom/left-axis tick labels align to a shared column (y-axis: a
    common right edge near the spine) or row (x-axis: a common top edge just
    below the spine). Labels leaking from an adjacent panel, or from a secondary
    (top/right twin) axis, sit at a different coordinate. We take the largest
    cluster of groups by that alignment coordinate -- ties broken toward the
    cluster nearest the spine -- and drop the rest. Precision over recall.
    """
    if len(groups) < 2:
        return groups
    coords = sorted(
        ((_align_coord(g, axis), g) for g in groups), key=lambda cg: cg[0]
    )
    clusters: list[list[tuple[float, list[TextSpan]]]] = []
    for c, g in coords:
        if clusters and c - clusters[-1][-1][0] <= _COLUMN_TOL:
            clusters[-1].append((c, g))
        else:
            clusters.append([(c, g)])
    if len(clusters) == 1:
        return groups
    # Largest cluster wins; tie -> the one nearest the spine (y: rightmost edge,
    # x: topmost row -> highest/lowest coord respectively).
    best = max(clusters, key=lambda cl: (len(cl), cl[-1][0] if axis == "y" else -cl[0][0]))
    return [g for _, g in best]


def _group_value(g: list[TextSpan], paths: list[Path]) -> float | None:
    """Parsed numeric value of a tick-label group, applying a leading minus glyph.

    The minus precedes the leftmost digit of the number it negates: the mantissa
    for a plain label, the *exponent* for a 10^n label.
    """
    ordered = sorted(g, key=lambda t: t.bbox[0])
    ref = ordered[1] if (ordered[0].text.strip() == "10" and len(ordered) >= 2) else ordered[0]
    _, ref_cy = _center(ref.bbox)
    neg = _has_minus(paths, ref_cy, ref.bbox[0])
    return _label_value(g, neg)


def _ticks_from(
    positions: list[float],
    spans: list[TextSpan],
    paths: list[Path],
    axis: str,
) -> list[Tick]:
    """Pair each numeric label group with its nearest tick position."""
    along = 0 if axis == "x" else 1  # index into the span center to align on
    groups = _group_labels(spans, axis)
    groups = _primary_column(groups, axis)

    def label_pos(g: list[TextSpan]) -> float:
        return sum(_center(t.bbox)[along] for t in g) / len(g)

    def group_label(g: list[TextSpan]) -> str:
        return "".join(t.text for t in sorted(g, key=lambda t: t.bbox[0]))

    # Some renderers (e.g. MATLAB) draw tick *labels* but no tick-mark path
    # segments on an axis. With no mark positions there is nothing to pair the
    # labels against, leaving the axis uncalibrated -> it would wrongly borrow a
    # sibling panel's calibration. The label center IS the tick location, so
    # when no marks were detected fall back to the label-group centers.
    if not positions and groups:
        return [
            Tick(pixel=label_pos(g), value=_group_value(g, paths), label=group_label(g))
            for g in groups
        ]

    labeled: dict[int, Tick] = {}
    used: set[int] = set()
    for g in groups:
        lp = label_pos(g)
        # Nearest unused tick position.
        cands = [(abs(positions[i] - lp), i) for i in range(len(positions)) if i not in used]
        if not cands:
            continue
        dist, idx = min(cands)
        if dist > _ALIGN_TOL:
            continue
        used.add(idx)
        labeled[idx] = Tick(
            pixel=positions[idx], value=_group_value(g, paths), label=group_label(g)
        )

    return [labeled.get(i, Tick(pixel=positions[i])) for i in range(len(positions))]


def _x_ticks(paths: list[Path], texts: list[TextSpan], region: Region,
             x_labels: list[TextSpan] | None = None):
    positions, direction, length = _x_tick_positions(paths, region)
    if x_labels is None:
        x_labels = _x_label_spans(texts, region, paths)
    return (_ticks_from(positions, x_labels, paths, "x"),
            direction, length)


def _y_ticks(paths: list[Path], texts: list[TextSpan], region: Region,
             y_labels: list[TextSpan] | None = None,
             y_pos: tuple | None = None):
    positions, direction, length = (
        y_pos if y_pos is not None else _y_tick_positions(paths, region))
    if y_labels is None:
        y_labels = _y_label_spans(texts, region)
    return (_ticks_from(positions, y_labels, paths, "y"),
            direction, length)


# --------------------------------------------------------------------------
# Axis scale multiplier (matplotlib offset text, e.g. "1e8")
# --------------------------------------------------------------------------

# Matplotlib renders large/small axis values with a normalised tick range (e.g.
# 0..1) and a separate "offset text" span (e.g. "1e8") placed just past the
# last tick label at the far end of the axis.  We detect this span, use it to
# rescale all tick values, and strip it from the axis title.
# The regex matches "1e8", "1E+8", "2.5e6", etc. (broader than the original
# "^1[eE][+-]?\d+$" which missed mantissas other than 1).
_OFFSET_RE = _re.compile(r'^\d+(\.\d+)?[eE][+-]?\d+$')


def _x_axis_multiplier(
    texts: list[TextSpan], region: Region, label_band_spans: list[TextSpan]
) -> float:
    """Return the axis-scale multiplier from a matplotlib offset-text span.

    Looks for a span matching a scientific-notation number in the x-label-band
    area that is NOT one of the already-identified tick-label spans (i.e. it
    sits outside the tight label-band height range or at the far right past the
    last tick) and whose center-x is beyond the rightmost tick label.  Returns
    1.0 if none found.
    """
    x0, _, x1, y1 = region.bbox
    # Pixel centre of the rightmost detected tick-label span.
    right_x = max((_center(s.bbox)[0] for s in label_band_spans), default=x0)
    # Look in a slightly wider vertical band than _LABEL_BAND.
    for t in texts:
        if t in label_band_spans:
            continue
        cx, cy = _center(t.bbox)
        if cy <= y1 or cy > y1 + _LABEL_BAND + 10:
            continue
        if cx <= right_x:
            continue
        s = t.text.strip()
        if _OFFSET_RE.match(s):
            val = _parse_plain(s)
            if val is not None and val != 0:
                return val
    return 1.0


def _y_axis_multiplier(
    texts: list[TextSpan], region: Region, label_band_spans: list[TextSpan]
) -> float:
    """Return the axis-scale multiplier from a matplotlib y-axis offset-text span.

    Matplotlib places the y-axis offset text just above the topmost tick label,
    to the left of the left spine (in the same horizontal band as the tick
    labels).  We look for a scientific-notation span in the y-label band that
    is NOT already a tick label and whose center-y is above (lower PDF y-coord
    than) the topmost tick label.  Returns 1.0 if none found.
    """
    x0, y0, _, _ = region.bbox
    # Pixel centre of the topmost (highest on page, smallest y in PDF coords)
    # detected tick-label span.
    top_y = min((_center(s.bbox)[1] for s in label_band_spans), default=y0)
    # Look in a slightly wider horizontal band than _YLABEL_BAND.
    for t in texts:
        if t in label_band_spans:
            continue
        cx, cy = _center(t.bbox)
        if cx >= x0 or cx < x0 - _YLABEL_BAND - 10:
            continue
        if cy >= top_y:
            continue
        s = t.text.strip()
        if _OFFSET_RE.match(s):
            val = _parse_plain(s)
            if val is not None and val != 0:
                return val
    # Origin-style "x10^n" offset near the axis TOP, drawn as separate spans
    # ("x"/"10"/"n") rather than matplotlib's single "1eN".
    return _superscript_mult(texts, region, exclude=label_band_spans)


def _superscript_mult(texts: list[TextSpan], region: Region, exclude=()) -> float:
    """Detect a multi-span 'x10^n' multiplier near the top-left of the y-axis.

    The mantissa span is ``"10"`` or a leading-times form ``"×10"`` / ``"x10"``
    (MATLAB renders the offset as ``×10`` plus a raised exponent). The exponent
    span may carry a unicode/ASCII minus (``"−4"`` / ``"-4"``) for small-value
    offsets like ``×10^-4``, so parse it as a signed integer rather than a bare
    digit run.

    ``exclude`` are spans that are already tick LABELS: on a log axis the topmost
    decade label "10^5" is itself a "10"+raised exponent near the axis top and
    must NOT be mistaken for a ``x10^5`` multiplier (the 2003.03611 bug).
    """
    x0, y0, x1, _ = region.bbox
    excl = set(id(s) for s in exclude)
    near = [t for t in texts
            if t.dir == (1.0, 0.0)
            and y0 - 22.0 <= _center(t.bbox)[1] <= y0 + 12.0
            and x0 - 15.0 <= _center(t.bbox)[0] <= x0 + 0.5 * (x1 - x0)
            and id(t) not in excl]
    for t in near:
        mant = t.text.strip().replace("×", "").replace("x", "").replace("X", "")
        if mant != "10":
            continue
        b = t.bbox
        bcy = _center(b)[1]
        # The exponent can be SEVERAL raised spans right of "10": a separate sign
        # glyph ("−") and the digits ("2") are emitted as DISTINCT spans, so the
        # old single-span scan parsed only "2" and silently dropped the minus
        # (rendering ×10^-2 as ×10^2 -> data off by 10^4). Collect the CONTIGUOUS
        # run of raised spans just right of the mantissa and parse the joined,
        # sign-normalised text. A gap (e.g. an unextracted minus glyph) breaks the
        # run, so we return no multiplier rather than guess a wrong sign.
        cands = sorted((e for e in near
                        if e.bbox[0] >= b[2] - 1.0 and _center(e.bbox)[1] < bcy),
                       key=lambda e: e.bbox[0])
        es, edge = "", b[2]
        for e in cands:
            if e.bbox[0] - edge > 3.0:     # contiguity break (missing glyph / gap)
                break
            es += e.text.strip()
            edge = e.bbox[2]
        m = _re.match(r"[+-]?\d+", es.replace("−", "-").replace(" ", ""))
        if m:
            try:
                return 10.0 ** int(m.group())
            except ValueError:
                pass
    return 1.0


# --------------------------------------------------------------------------
# Axis titles
# --------------------------------------------------------------------------

# Title band (beyond the tick labels): how far past the last tick label to look
# for an axis title, and how wide a row counts as "one line" of title text.
_TITLE_BAND = 28.0
_TITLE_ROW_TOL = 4.0


def _is_subcaption(text: str) -> bool:
    """Sub-caption / figure caption, never an axis title.

    Catches panel enumerators (``(a)``, ``(b) Network``, ``a)``) and figure /
    table caption lines (``Figure 2: ...``) that sit just below the axis.
    """
    s = text.strip()
    if not s:
        return False
    head = s.split()[0]
    if head.lower().rstrip(".:") in ("figure", "fig", "table"):
        return True
    # Leading "(a)" / "(b)" / "a)" enumerator.
    core = head.strip("()")
    return len(core) == 1 and core.isalpha() and (head != core)


def _title_from_rows(
    cands: list[TextSpan], lo: float, hi: float, center: float, span: float, along: int
) -> str | None:
    """Pick the real axis title from text rows in the band ``[lo, hi]``.

    The title is the centered word(s) on the row nearest the tick labels that is
    not numeric, not a single stray letter, and not a panel sub-caption. Spans on
    that row are joined left-to-right; we prefer the row closest to the ticks and,
    within a row, keep the longest centered non-numeric content.
    """
    perp = 1 - along  # axis we read the title's position along
    rows: list[list[TextSpan]] = []
    for t in sorted(cands, key=lambda s: _center(s.bbox)[along]):
        c = _center(t.bbox)[along]
        if not (lo < c <= hi):
            continue
        for r in rows:
            if abs(_center(r[0].bbox)[along] - c) <= _TITLE_ROW_TOL:
                r.append(t)
                break
        else:
            rows.append([t])
    rows.sort(key=lambda r: _center(r[0].bbox)[along])  # nearest the ticks first
    for r in rows:
        ordered = sorted(r, key=lambda s: s.bbox[0] if perp == 0 else s.bbox[1])
        text = "".join(s.text for s in ordered).strip()
        if not text or _is_subcaption(text):
            continue
        # Centeredness: the row's center should sit near the axis center.
        rc = sum(_center(s.bbox)[perp] for s in r) / len(r)
        if abs(rc - center) > 0.5 * span:
            continue
        compact = text.replace(" ", "")
        if _is_numeric_span(compact) or len(compact) < 2:
            continue
        # Mark sub/superscripts as inline mathtext ('T'+lowered 'j,H' -> 'T$_{j,H}$')
        # so the title renders with the script the source drew, instead of the
        # plain space-join that flattened it (2003.09710 'T j' -> 'T$_{j}$'). The
        # guards above run on the plain text; only the RETURN is marked up.
        if perp == 0:  # horizontal title (x-axis) -- script test is vertical-offset
            items = [(s.text, s.size, _center(s.bbox)[1], s.bbox[0], s.bbox[2])
                     for s in ordered]
            return _join_scripts(items)
        return text
    return None


def _x_title(texts: list[TextSpan], region: Region, label_spans: list[TextSpan]) -> str | None:
    """Horizontal text below the numeric tick labels, centered on the region.

    Excludes panel sub-captions (``(a) Network``), single stray letters,
    numeric/tick text, and matplotlib offset-text spans (``1eN``) that appear
    just past the last tick; prefers the centered word(s) on the row just
    beyond the tick labels (the real axis title) over anything further down.
    """
    x0, _, x1, y1 = region.bbox
    numeric = [t for t in label_spans if _is_numeric_span(t.text)]
    label_y = max((_center(t.bbox)[1] for t in numeric), default=y1)
    # Keep candidates whose center lies within the region's horizontal extent so a
    # sibling panel's title (same text row, different panel) is not absorbed.
    # Also exclude axis-offset spans (1eN) which are not title text.
    cands = [
        t for t in texts
        if t.dir == (1.0, 0.0)
        and x0 - _ALIGN_TOL <= _center(t.bbox)[0] <= x1 + _ALIGN_TOL
        and not _OFFSET_RE.match(t.text.strip())
    ]
    return _title_from_rows(
        cands, label_y + 1, y1 + _TITLE_BAND, 0.5 * (x0 + x1), x1 - x0, along=1
    )


def _y_title(texts: list[TextSpan], region: Region) -> str | None:
    """Rotated y-axis title (dir ~ (0,+-1)) just left of the region.

    Panel-aware: candidates must overlap the region's VERTICAL extent (so a
    stacked sibling panel's title above/below is not absorbed) and lie within a
    bounded distance left of the spine (so a far-left neighbour panel's title is
    not absorbed). The title is usually drawn as several spans (e.g. "Integrated"
    "PL" "intensity" "(Counts)"); we take the column nearest the axis and join its
    spans in reading order."""
    x0, y0, _, y1 = region.bbox
    h = y1 - y0
    max_left = max(0.5 * (region.bbox[2] - x0), 55.0)  # how far left to look
    cands = []
    for t in texts:
        if abs(t.dir[1]) < 0.7:  # vertical text only
            continue
        cx, cy = _center(t.bbox)
        if not (x0 - max_left <= cx < x0):
            continue
        if cy < y0 - 0.1 * h or cy > y1 + 0.1 * h:  # must overlap region height
            continue
        # A numeric / sub-caption-looking span is dropped only if it is NOT a
        # plausible sub/superscript FRAGMENT of the title -- a script sits to the
        # side of the (rotated) baseline, so it is offset PERPENDICULAR (in cx)
        # rather than aligned with its neighbours. A stray '0' subscript or '(E)'
        # in 'P_0(E)' would otherwise be dropped per-span, truncating the title to
        # 'P'. The joined result is re-checked against these guards below.
        junk = _is_subcaption(t.text) or _is_numeric_span(t.text.replace(" ", ""))
        cands.append((cx, cy, t.dir[1], t, junk))
    if not cands:
        return None
    # innermost column (closest to the axis) = the y-title of THIS panel. Anchor
    # the column on a NON-junk span so a stray tick/caption does not define it; a
    # script fragment (offset perpendicular) still falls within the column band.
    nonjunk = [c for c in cands if not c[4]]
    if not nonjunk:
        return None
    inner_cx = max(c[0] for c in nonjunk)
    # The body column is anchored on the innermost NON-junk spans (so a stray tick /
    # caption never defines the column); their mean cx is the rotated baseline
    # reference and their max size is the body font size.
    body = [c for c in nonjunk if abs(c[0] - inner_cx) <= 6.0]
    base_cx = sum(c[0] for c in body) / len(body)
    base_sz = max((c[3].size or 0.0) for c in body)
    # A sub/superscript fragment of a ROTATED title sits OFF the baseline: it is
    # SMALLER than the body text and offset PERPENDICULAR to the reading direction
    # (i.e. its cx differs from the baseline cx). Admit such a junk-looking span
    # ('P'+subscript'0'+'(E)') into the column; require both the size drop AND the
    # perpendicular offset so an aligned full-size tick is never pulled in.
    def _script_frag(c):
        cx, sz = c[0], (c[3].size or 0.0)
        return (c[4] and base_sz and sz < 0.92 * base_sz
                and abs(cx - base_cx) > 0.20 * base_sz)
    # A full-size junk span sitting ON the baseline (same size, aligned cx) is a
    # body fragment the per-token heuristic mis-flagged, NOT a stray tick/caption:
    # e.g. '(E)' in 'P_0(E)' trips _is_subcaption. Admit it (alongside genuine
    # script fragments) only when this column ALSO contains a script -- i.e. it is
    # already a scripted title -- so plain columns keep the conservative per-span
    # drop unchanged.
    def _body_aligned(c):
        cx, sz = c[0], (c[3].size or 0.0)
        return (c[4] and base_sz and sz >= 0.92 * base_sz
                and abs(cx - base_cx) <= 0.20 * base_sz)
    raw_scripts = any(_script_frag(c) for c in cands if abs(c[0] - inner_cx) <= 6.0)
    col = [c for c in cands if abs(c[0] - inner_cx) <= 6.0
           and (not c[4] or _script_frag(c)
                or (raw_scripts and _body_aligned(c)))]
    dn = col[0][2]
    has_script = any(_script_frag(c) for c in col)
    if not has_script:
        # No rotated sub/superscript -> plain reading-order space-join (UNCHANGED
        # behaviour for every ordinary y-title). dir (0,-1) reads bottom->top
        # (sort cy desc); dir (0,+1) reads top->bottom.
        col.sort(key=lambda c: c[1], reverse=(dn < 0))
        title = " ".join(" ".join(c[3].text.split()) for c in col)
        return " ".join(title.split()) or None
    # A rotated title WITH a script: remap the rotated coords to join_scripts'
    # horizontal convention -- reading axis -> x0/x1, perpendicular (cx) -> the
    # script-offset axis. A subscript 'P_0' sits at LARGER cx (right of the rotated
    # baseline) -> LARGER cy = lowered, which join_scripts marks as '_'.
    items = []
    for cx, cy, _d, t, _j in col:
        along0, along1 = (-t.bbox[3], -t.bbox[1]) if dn < 0 else (t.bbox[1], t.bbox[3])
        # Explicit WHITESPACE spans render inter-word spacing; keep a single space
        # so join_scripts sees the along-gap (collapsing to "" gave "PLintensity").
        txt = " " if t.text and not t.text.strip() else " ".join(t.text.split())
        items.append((txt, t.size, cx, along0, along1))
    title = _join_scripts(items)
    title = _re.sub(r"\s+", " ", title).strip()
    return title or None


# --------------------------------------------------------------------------
# Public entry
# --------------------------------------------------------------------------

def detect_axes(
    region: Region,
    paths: list[Path],
    texts: list[TextSpan],
) -> tuple[Axis, Axis]:
    """Detect the x (bottom) and y (left) axes of ``region``.

    Returns two ``Axis`` objects carrying their tick list (pixel + value where a
    numeric label was matched), the axis title text if found, and the pixel
    extent of the axis. Ticks without a matched numeric label keep ``value=None``
    and are kept (callers filter to labeled ticks for calibration).

    Handles matplotlib's axis-scale offset text (e.g. ``1e8`` placed beyond the
    last x-tick label, or above the topmost y-tick label): detected tick values
    are multiplied by the offset and the offset span is stripped from the title.
    """
    x0, y0, x1, y1 = region.bbox

    # Compute the y-tick scan once and reuse it: _x_label_spans needs the y-tick
    # positions (to drop a corner y-label leaking into the x band) and _y_ticks
    # needs the full scan -- both with identical (paths, region) args.
    y_pos = _y_tick_positions(paths, region)
    x_labels = _x_label_spans(texts, region, paths, ytick_ys=y_pos[0])
    x_mult = _x_axis_multiplier(texts, region, x_labels)

    x_ticks, x_dir, x_len = _x_ticks(paths, texts, region, x_labels)
    if x_mult != 1.0:
        x_ticks = [
            Tick(pixel=t.pixel, value=t.value * x_mult, label=t.label)
            if t.value is not None else t
            for t in x_ticks
        ]

    y_labels = _y_label_spans(texts, region)
    y_mult = _y_axis_multiplier(texts, region, y_labels)

    y_ticks, y_dir, y_len = _y_ticks(paths, texts, region, y_labels, y_pos)
    if y_mult != 1.0:
        y_ticks = [
            Tick(pixel=t.pixel, value=t.value * y_mult, label=t.label)
            if t.value is not None else t
            for t in y_ticks
        ]

    x_axis = Axis(
        title=_x_title(texts, region, x_labels),
        pixel_range=(x0, x1),
        ticks=x_ticks,
        tick_direction=x_dir,
        tick_length=x_len,
        multiplier=x_mult,
    )
    y_axis = Axis(
        title=_y_title(texts, region),
        pixel_range=(y0, y1),
        ticks=y_ticks,
        tick_direction=y_dir,
        tick_length=y_len,
        multiplier=y_mult,
    )
    return x_axis, y_axis
