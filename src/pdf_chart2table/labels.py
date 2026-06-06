"""Extract a chart's textual context: title, axis titles, legend, caption.

Given a detected ``Region`` plus the page's paths and texts, this module pulls
out the words around the plot (not the numeric tick labels, which ``axes.py``
handles). Geometry is in PDF points (y increases downward).

Layout assumptions (matplotlib/pgfplots conventions, confirmed on fixtures):
  - the region bbox is the plotting area (often the white axes-patch, which can
    include the tick-label margin);
  - the **plot title** is horizontal text just above the region's top edge,
    centred over the plot;
  - the **x-axis title** is horizontal text below the bottom edge, below the
    numeric tick labels;
  - the **y-axis title** is text to the left of the left edge with a vertical
    writing direction (``TextSpan.dir`` ~ (0, +-1));
  - a **legend** is a cluster of small swatch paths (a short line and/or a small
    marker) each immediately left of a text label;
  - the **caption** is the nearest "Figure N"/"Fig. N" paragraph, normally below
    the figure (fallback above).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .model import BBox, Color, Path, Region, TextSpan

# Numeric tick label (to exclude from title detection).
_NUMERIC = re.compile(r"^[-+]?\d*\.?\d+$")
# Caption opener: "Figure 3", "Fig. 3", "Figure 3:" ...
_CAPTION_RE = re.compile(r"^\s*(figure|fig\.?)\s*\d", re.IGNORECASE)

# How far above the top edge to look for the plot title (points).
_TITLE_ABOVE = 30.0
# How far below the bottom edge to look for the x-axis title (points).
_XTITLE_BELOW = 45.0
# How far left of the left edge to look for the y-axis title (points).
_YTITLE_LEFT = 60.0
# A span is "centred" over the plot if its centre x is within this fraction of
# the plot half-width of the plot centre.
_CENTER_FRAC = 0.5
# Vertical-writing threshold: |dy| larger than this means rotated (y title).
_VERTICAL_DIR = 0.7
# A legend swatch path is "small" if both bbox sides are under this (points).
_SWATCH_MAX = 30.0
# Max horizontal gap between a swatch group and the label text to its right.
_SWATCH_GAP = 18.0
# Vertical overlap tolerance for pairing a swatch with its label row.
_ROW_TOL = 6.0
# Tighter vertical tolerance for matching a continuation span to a label's row;
# legend rows can be stacked only ~5pt apart, so a loose tol grabs the next
# row's text. Half the typical row pitch keeps rows separate.
_LABEL_ROW_TOL = 2.5
# Subscript/superscript glyphs are smaller than the base span and their centre-y
# can shift by up to ~0.75× the base font size (e.g. "E_N" where N is a small
# raised/lowered character, or a raised "^2" exponent). We allow them as
# continuations when their cy offset is within this fraction of the anchor font
# size AND they are smaller. A raised exponent sits higher than a lowered index
# drops, so the fraction must cover ~0.65 of the base size (seen on
# "‖Z_t‖_F^2" math legends) while staying under half the legend row pitch
# (~0.8-1.0× the base size) so it never grabs the next row's text.
_SUB_ROW_TOL_FRAC = 0.75
# A legend marker glyph is small; a path larger than this in either dimension is
# a data curve / box overlapping the legend, not a marker swatch.
_MARKER_SWATCH_MAX = 14.0
# Within a label row, consecutive spans further apart than this (relative to
# effective font size) start a new field -> stop assembling. A slightly wider
# gap (1.3 vs a tighter 1.2) tolerates invisible math operator glyphs rendered
# as paths (e.g. ≤ in "0 ≤ J ≤ 15") that leave a gap between adjacent spans.
_SPAN_GAP = 1.3
# Adjacent spans closer than this (relative to font size) are joined with no
# space (a hyphenated token split across spans); wider gaps get a space.
_ADJ_GAP = 0.25
# When the declared span size is implausibly small (LaTeX scaling artifact), use
# this fraction of the bbox height as the effective size for gap calculations.
_SIZE_BBOX_FRAC = 0.6
# Bin cy to this grid (points) when sorting legend text anchors so that spans
# on the same visual row but with slightly different cy values are ordered by x
# (leftmost first). Half a typical row pitch keeps adjacent rows distinct.
_ROW_BIN = 6.0
# Swatches/labels must lie within the region bbox expanded by this margin
# (legends sit inside the plot area, occasionally just past an edge).
_LEGEND_MARGIN = 8.0
# Caption paragraph: lines within this vertical gap belong to the same block.
_LINE_GAP = 6.0
# How far below/above the region to accept a caption opener (points).
_CAPTION_BELOW = 90.0
_CAPTION_ABOVE = 60.0


@dataclass
class Labels:
    """Textual context extracted around one chart region."""

    title: str | None = None
    x_title: str | None = None
    y_title: str | None = None
    # Each entry: (style, color, label_text). ``style`` is the swatch style used
    # to disambiguate same-colour series: "marker" (a marker glyph), "dashed" (a
    # dashed line sample) or "line" (a solid line sample).
    legend: list[tuple[str, Color | None, str]] = field(default_factory=list)
    caption: str | None = None
    # Bounding box enclosing all detected legend swatches and their label spans,
    # or None when no legend was found. Used by the mark extractor to exclude
    # legend-region mini-curve decorations from data extraction.
    legend_bbox: BBox | None = None


def _cx(b: BBox) -> float:
    return 0.5 * (b[0] + b[2])


def _cy(b: BBox) -> float:
    return 0.5 * (b[1] + b[3])


def _eff_size(t: TextSpan) -> float:
    """Effective font size for gap calculations.

    PyMuPDF returns a scaled ``size`` for LaTeX-generated PDFs that can be a
    fraction of the visual point size (e.g. 0.87 for ~12pt text). Using it
    directly makes ``_SPAN_GAP * size`` too tight, truncating multi-span labels
    like "Linear cavit y(y), g'=0.4". We cap from below by
    ``_SIZE_BBOX_FRAC * bbox_height`` so that normal inter-word gaps are
    accepted regardless of the declared size.
    """
    s = t.size or 10.0
    h = t.bbox[3] - t.bbox[1]
    return max(s, _SIZE_BBOX_FRAC * h)


def _is_numeric(text: str) -> bool:
    s = text.strip().replace("−", "-")  # unicode minus
    return bool(_NUMERIC.match(s))


def _horizontal(t: TextSpan) -> bool:
    return abs(t.dir[1]) < _VERTICAL_DIR


def _vertical(t: TextSpan) -> bool:
    return abs(t.dir[1]) >= _VERTICAL_DIR


def _detect_title(region: Region, texts: list[TextSpan]) -> str | None:
    """Horizontal, non-numeric text just above the top edge, centred."""
    x0, y0, x1, _ = region.bbox
    cx = 0.5 * (x0 + x1)
    half = 0.5 * (x1 - x0)
    cands = []
    for t in texts:
        if not _horizontal(t) or _is_numeric(t.text) or not t.text.strip():
            continue
        if not (y0 - _TITLE_ABOVE <= _cy(t.bbox) <= y0):
            continue
        if abs(_cx(t.bbox) - cx) > _CENTER_FRAC * half:
            continue
        cands.append(t)
    if not cands:
        return None
    # If multiple spans share the title line, join left-to-right.
    cands.sort(key=lambda t: t.bbox[0])
    return _join_spans(cands)


def _detect_x_title(region: Region, texts: list[TextSpan]) -> str | None:
    """Horizontal, non-numeric text below the bottom edge, below tick labels.

    Tick labels sit just under the axis; the axis title is the centred word(s)
    further down. We take the lowest centred non-numeric horizontal span.
    """
    x0, y0, x1, y1 = region.bbox
    cx = 0.5 * (x0 + x1)
    half = 0.5 * (x1 - x0)
    cands = []
    for t in texts:
        if not _horizontal(t) or _is_numeric(t.text) or not t.text.strip():
            continue
        if not (y1 < _cy(t.bbox) <= y1 + _XTITLE_BELOW):
            continue
        if abs(_cx(t.bbox) - cx) > _CENTER_FRAC * half:
            continue
        cands.append(t)
    if not cands:
        return None
    # Take the bottom-most row (axis title is below the numeric tick labels).
    bottom = max(_cy(t.bbox) for t in cands)
    row = [t for t in cands if abs(_cy(t.bbox) - bottom) <= _ROW_TOL]
    row.sort(key=lambda t: t.bbox[0])
    return _join_spans(row)


def _detect_y_title(region: Region, texts: list[TextSpan]) -> str | None:
    """Vertically-written text to the left of the left edge (left of y ticks)."""
    x0, y0, _, y1 = region.bbox
    cy = 0.5 * (y0 + y1)
    half = 0.5 * (y1 - y0)
    cands = []
    for t in texts:
        if not _vertical(t) or _is_numeric(t.text) or not t.text.strip():
            continue
        if not (x0 - _YTITLE_LEFT <= _cx(t.bbox) < x0):
            continue
        if abs(_cy(t.bbox) - cy) > _CENTER_FRAC * half:
            continue
        cands.append(t)
    if not cands:
        return None
    cands.sort(key=lambda t: -t.bbox[1])  # bottom-to-top = reading order
    return _join_spans(cands)


def _join_spans(spans: list[TextSpan]) -> str:
    """Join span texts with single spaces, collapsing whitespace."""
    return re.sub(r"\s+", " ", " ".join(s.text for s in spans)).strip()


def _swatch_style(p: Path) -> str | None:
    """Style signature of a small legend swatch path, or None if not a swatch.

    A short flat path (wide, near-zero height) is a line sample: "dashed" if it
    carries a dash pattern, else "line" (solid). A small 2D blob is a "marker".
    Degenerate axis ticks / spine corners (a few points long in only one
    dimension) are not swatches and return None.
    """
    w = p.bbox[2] - p.bbox[0]
    h = p.bbox[3] - p.bbox[1]
    if h <= 1.5 and w >= 5.0:
        return "dashed" if p.dashes else "line"
    if 2.0 <= w <= _MARKER_SWATCH_MAX and 2.0 <= h <= _MARKER_SWATCH_MAX:
        return "marker"
    return None


def _inside(b: BBox, region: Region, margin: float) -> bool:
    rx0, ry0, rx1, ry1 = region.bbox
    return (rx0 - margin <= _cx(b) <= rx1 + margin
            and ry0 - margin <= _cy(b) <= ry1 + margin)


def _assemble_label(
    start: int, ty: float, texts: list[TextSpan], used: set[int]
) -> tuple[str, set[int]]:
    """Assemble the full label beginning at span ``start`` on row ``ty``.

    Continuation spans further to the right on the same row are appended while
    consecutive spans stay close horizontally (a multi-span label such as
    "BN-x5-Sigmoid" or "T = 100"); a large horizontal gap (the next legend
    column) ends it. Returns (text, span-indices-consumed).

    Subscript/superscript glyphs have a smaller font size and their centre-y
    can be offset by up to ~0.5× the anchor font size, exceeding the normal
    _LABEL_ROW_TOL. We admit them with a looser size-relative tolerance so that
    labels like "E_N(a⁻ : CR)" are not truncated to "E(a : CR)".
    """
    first = texts[start]
    base_size = _eff_size(first)

    def _same_row(t: TextSpan) -> bool:
        cy_off = abs(_cy(t.bbox) - ty)
        if cy_off <= _LABEL_ROW_TOL:
            return True
        # Accept a smaller span (subscript/superscript) whose vertical offset
        # is within _SUB_ROW_TOL_FRAC × the anchor font size.
        if (t.size is not None and t.size < base_size
                and cy_off <= _SUB_ROW_TOL_FRAC * base_size):
            return True
        return False

    cont = sorted(
        (i for i, t in enumerate(texts)
         if i != start and i not in used and _horizontal(t) and t.text.strip()
         and _same_row(t)
         and t.bbox[0] >= first.bbox[2] - 1.0),
        key=lambda i: texts[i].bbox[0],
    )
    picked = [start]
    prev = first
    for i in cont:
        t = texts[i]
        if t.bbox[0] - prev.bbox[2] > _SPAN_GAP * _eff_size(prev):
            break
        picked.append(i)
        prev = t
    # Join, inserting a space only where spans are horizontally separated (so a
    # hyphenated "BN-x5-Sigmoid" stays one token while "T = 100" keeps spaces).
    parts = [texts[picked[0]].text]
    for pa, pb in zip(picked, picked[1:]):
        a, b = texts[pa], texts[pb]
        sep = " " if b.bbox[0] - a.bbox[2] > _ADJ_GAP * _eff_size(a) else ""
        parts.append(sep + b.text)
    label = re.sub(r"\s+", " ", "".join(parts)).strip()
    return label, set(picked)


def _detect_legend(
    region: Region, paths: list[Path], texts: list[TextSpan]
) -> tuple[list[tuple[str, Color | None, str]], BBox | None]:
    """Pair each legend label with the swatch immediately to its left.

    A legend entry is a text label whose left edge has a small swatch (a short
    line and/or a small marker) just left of it on the same row. We anchor on
    the label text (filtered to non-numeric horizontal spans inside the region,
    so paragraph text / other figures' legends are excluded), find its swatch,
    assemble any continuation spans into the full label, and capture the swatch
    ``style`` ("marker" / "line" / "dashed") and colour. Anchoring on the label
    (not the swatch) keeps stray data markers near the legend from hijacking it.

    Returns ``(entries, legend_bbox)`` where ``legend_bbox`` is the bounding box
    enclosing all swatch paths and label spans, or None when no legend found.
    That bbox lets the mark extractor exclude mini-curve decorations rendered
    inside legend boxes from data extraction.
    """
    swatches = [
        p for p in paths
        if _swatch_style(p) is not None
        and (p.stroke if p.stroke is not None else p.fill) is not None
        and _inside(p.bbox, region, _LEGEND_MARGIN)
    ]

    out: list[tuple[str, Color | None, str]] = []
    used: set[int] = set()
    # Collect swatch + label bboxes for legend_bbox computation.
    legend_coords: list[tuple[float, float, float, float]] = []
    # Process labels top-to-bottom, left-to-right (legend reading order).
    # Bin cy to a coarse grid so spans on the same visual row (e.g. "ε" and
    # "-PCA" with cy 0.4 pts apart) sort by x rather than by fractional cy,
    # ensuring the leftmost span on a row is always the anchor.
    order = sorted(range(len(texts)),
                   key=lambda i: (round(_cy(texts[i].bbox) / _ROW_BIN), texts[i].bbox[0]))
    for ti in order:
        t = texts[ti]
        if ti in used or not _horizontal(t) or not t.text.strip():
            continue
        if not _inside(t.bbox, region, _LEGEND_MARGIN):
            continue
        # Skip purely numeric anchors unless a swatch is immediately to the
        # left — a numeric like "0" in "0 ≤ J ≤ 15" can start a legend entry
        # when the ≤ signs are rendered as path glyphs (invisible in text).
        # We admit numeric anchors only when there IS a swatch match; the
        # assembled-label non-numeric check below then guards against bare
        # tick labels that accidentally pick up a swatch.
        ty = _cy(t.bbox)
        lx = t.bbox[0]
        # Swatches on this row ending just left of the label.
        row = [p for p in swatches
               if abs(_cy(p.bbox) - ty) <= _ROW_TOL
               and 0 <= lx - p.bbox[2] <= _SWATCH_GAP]
        if _is_numeric(t.text) and not row:
            continue
        if not row:
            continue
        label, consumed = _assemble_label(ti, ty, texts, used)
        if not label or _is_numeric(label):
            continue
        # Prefer a marker swatch (its glyph), else the line sample's style.
        markers = [p for p in row if _swatch_style(p) == "marker"]
        pick = markers[0] if markers else row[0]
        color = pick.stroke if pick.stroke is not None else pick.fill
        out.append((_swatch_style(pick), color, label))
        used |= consumed
        # Track the extent of this entry (swatch + all consumed text spans).
        for p in row:
            legend_coords.append(p.bbox)
        for idx in consumed:
            legend_coords.append(texts[idx].bbox)

    if not legend_coords:
        return out, None
    x0 = min(b[0] for b in legend_coords)
    y0 = min(b[1] for b in legend_coords)
    x1 = max(b[2] for b in legend_coords)
    y1 = max(b[3] for b in legend_coords)
    return out, (x0, y0, x1, y1)


def _detect_caption(region: Region, texts: list[TextSpan]) -> str | None:
    """Nearest "Figure N"/"Fig. N" paragraph below (fallback above) the region.

    Synthetic fixtures have no caption, so this returns None there. On real
    pages, we find the closest caption opener span vertically near the figure,
    then gather the contiguous block of spans (same-ish lines) following it.
    """
    _, y0, _, y1 = region.bbox
    openers = []
    for i, t in enumerate(texts):
        if _CAPTION_RE.match(t.text):
            cyt = _cy(t.bbox)
            if y1 <= cyt <= y1 + _CAPTION_BELOW:
                openers.append((cyt - y1, i))  # below: positive distance
            elif y0 - _CAPTION_ABOVE <= cyt < y0:
                openers.append((y0 - cyt + 1000, i))  # above: deprioritised
    if not openers:
        return None
    openers.sort()
    start = openers[0][1]

    # Gather the caption block: order spans by reading order, then accumulate
    # from the opener while consecutive lines stay close vertically.
    ordered = sorted(texts, key=lambda t: (round(t.bbox[1] / 4.0), t.bbox[0]))
    start_y = _cy(texts[start].bbox)
    idx = next(i for i, t in enumerate(ordered)
               if t is texts[start])
    block = [ordered[idx]]
    prev_y = start_y
    for t in ordered[idx + 1:]:
        gap = _cy(t.bbox) - prev_y
        # New line allowed; stop if a large vertical jump (paragraph break).
        if gap > _LINE_GAP + (t.size or 12.0):
            break
        block.append(t)
        prev_y = _cy(t.bbox)
    return _join_spans(block)


def detect_labels(
    region: Region,
    paths: list[Path],
    texts: list[TextSpan],
    page=None,
) -> Labels:
    """Extract the chart's textual context around ``region``.

    ``page`` is accepted for API symmetry with the rest of the pipeline but is
    unused (all geometry comes from ``region``, ``paths`` and ``texts``).
    """
    legend_entries, legend_bbox = _detect_legend(region, paths, texts)
    return Labels(
        title=_detect_title(region, texts),
        x_title=_detect_x_title(region, texts),
        y_title=_detect_y_title(region, texts),
        legend=legend_entries,
        caption=_detect_caption(region, texts),
        legend_bbox=legend_bbox,
    )
