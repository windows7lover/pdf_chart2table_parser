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

from .model import Axis, BBox, Path, Region, Tick, TextSpan

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


def _center(b: BBox) -> tuple[float, float]:
    return 0.5 * (b[0] + b[2]), 0.5 * (b[1] + b[3])


def _is_minus_glyph(p: Path) -> bool:
    b = p.bbox
    w, h = b[2] - b[0], b[3] - b[1]
    return (
        p.fill is not None
        and p.stroke is None
        and _MINUS_W[0] <= w <= _MINUS_W[1]
        and h <= _MINUS_H
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

def _x_tick_positions(paths: list[Path], region: Region) -> list[float]:
    """X pixel positions of bottom-axis tick marks (short vertical segments)."""
    x0, _, x1, y1 = region.bbox
    xs: list[float] = []
    for p in paths:
        b = p.bbox
        w, h = b[2] - b[0], b[3] - b[1]
        if w > _TICK_THIN_MAX or not (0 < h <= _TICK_LEN_MAX):
            continue
        # The tick touches the bottom spine and extends below it.
        if abs(b[1] - y1) > _SPINE_TOL or b[3] < y1 - _SPINE_TOL:
            continue
        cx = 0.5 * (b[0] + b[2])
        if x0 - _SPINE_TOL <= cx <= x1 + _SPINE_TOL:
            xs.append(cx)
    return _cluster(xs)


def _y_tick_positions(paths: list[Path], region: Region) -> list[float]:
    """Y pixel positions of left-axis tick marks (short horizontal segments)."""
    x0, y0, _, y1 = region.bbox
    ys: list[float] = []
    for p in paths:
        b = p.bbox
        w, h = b[2] - b[0], b[3] - b[1]
        if h > _TICK_THIN_MAX or not (0 < w <= _TICK_LEN_MAX):
            continue
        if abs(b[2] - x0) > _SPINE_TOL or b[0] > x0 + _SPINE_TOL:
            continue
        cy = 0.5 * (b[1] + b[3])
        if y0 - _SPINE_TOL <= cy <= y1 + _SPINE_TOL:
            ys.append(cy)
    return _cluster(ys)


# --------------------------------------------------------------------------
# Tick <-> label pairing
# --------------------------------------------------------------------------

def _x_label_spans(texts: list[TextSpan], region: Region) -> list[TextSpan]:
    """Text spans sitting in the label band just below the bottom spine."""
    x0, _, x1, y1 = region.bbox
    out = []
    for t in texts:
        cx, cy = _center(t.bbox)
        if y1 < cy <= y1 + _LABEL_BAND and x0 - _LABEL_BAND <= cx <= x1 + _LABEL_BAND:
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
        # The minus precedes the leftmost digit of the number it negates: the
        # mantissa for a plain label, the *exponent* for a 10^n label.
        ordered = sorted(g, key=lambda t: t.bbox[0])
        if ordered[0].text.strip() == "10" and len(ordered) >= 2:
            ref = ordered[1]
        else:
            ref = ordered[0]
        _, ref_cy = _center(ref.bbox)
        neg = _has_minus(paths, ref_cy, ref.bbox[0])
        val = _label_value(g, neg)
        label = "".join(t.text for t in sorted(g, key=lambda t: t.bbox[0]))
        labeled[idx] = Tick(pixel=positions[idx], value=val, label=label)

    return [labeled.get(i, Tick(pixel=positions[i])) for i in range(len(positions))]


def _x_ticks(paths: list[Path], texts: list[TextSpan], region: Region) -> list[Tick]:
    return _ticks_from(_x_tick_positions(paths, region),
                       _x_label_spans(texts, region), paths, "x")


def _y_ticks(paths: list[Path], texts: list[TextSpan], region: Region) -> list[Tick]:
    return _ticks_from(_y_tick_positions(paths, region),
                       _y_label_spans(texts, region), paths, "y")


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
        return text
    return None


def _x_title(texts: list[TextSpan], region: Region, label_spans: list[TextSpan]) -> str | None:
    """Horizontal text below the numeric tick labels, centered on the region.

    Excludes panel sub-captions (``(a) Network``), single stray letters, and
    numeric/tick text; prefers the centered word(s) on the row just beyond the
    tick labels (the real axis title) over anything further down.
    """
    x0, _, x1, y1 = region.bbox
    numeric = [t for t in label_spans if _is_numeric_span(t.text)]
    label_y = max((_center(t.bbox)[1] for t in numeric), default=y1)
    # Keep candidates whose center lies within the region's horizontal extent so a
    # sibling panel's title (same text row, different panel) is not absorbed.
    cands = [
        t for t in texts
        if t.dir == (1.0, 0.0) and x0 - _ALIGN_TOL <= _center(t.bbox)[0] <= x1 + _ALIGN_TOL
    ]
    return _title_from_rows(
        cands, label_y + 1, y1 + _TITLE_BAND, 0.5 * (x0 + x1), x1 - x0, along=1
    )


def _y_title(texts: list[TextSpan], region: Region) -> str | None:
    """Rotated text (dir ~ (0,-1)) at the far left of the region."""
    x0, y0, _, y1 = region.bbox
    cy_region = 0.5 * (y0 + y1)
    best: TextSpan | None = None
    for t in texts:
        if abs(t.dir[1]) < 0.7:  # not vertically rotated
            continue
        cx, cy = _center(t.bbox)
        if cx >= x0 or abs(cy - cy_region) > (y1 - y0):
            continue
        if _is_subcaption(t.text) or _is_numeric_span(t.text.replace(" ", "")):
            continue
        if best is None or cx < _center(best.bbox)[0]:
            best = t
    return best.text if best else None


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
    """
    x0, y0, x1, y1 = region.bbox

    x_ticks = _x_ticks(paths, texts, region)
    y_ticks = _y_ticks(paths, texts, region)

    x_labels = _x_label_spans(texts, region)
    x_axis = Axis(
        title=_x_title(texts, region, x_labels),
        pixel_range=(x0, x1),
        ticks=x_ticks,
    )
    y_axis = Axis(
        title=_y_title(texts, region),
        pixel_range=(y0, y1),
        ticks=y_ticks,
    )
    return x_axis, y_axis
