"""Load a fitz page into normalized vector primitives (Path, TextSpan).

Drawings come from ``page.get_drawings()`` whose paths contain items:
  - ('l', p1, p2)              straight line segment
  - ('c', p1, p2, p3, p4)      cubic bezier (p1 start, p2/p3 controls, p4 end)
  - ('re', Rect, orientation)  rectangle
  - ('qu', Quad)               quad

Each drawing path is turned into one ``Path`` whose ``points`` are the
flattened polyline of all its items (beziers subdivided). Text comes from
``page.get_text("dict")``.
"""

from __future__ import annotations

import fitz

from .font_recovery import FontDecoder, is_broken_text
from .model import BBox, Color, Path, PageData, Point, TextSpan

# Number of segments used to flatten each cubic bezier curve.
_BEZIER_STEPS = 8


def _bezier_points(p0, p1, p2, p3, steps: int = _BEZIER_STEPS) -> list[Point]:
    """Sample a cubic bezier at ``steps`` segments, excluding the start point."""
    out: list[Point] = []
    for i in range(1, steps + 1):
        t = i / steps
        mt = 1.0 - t
        a, b, c, d = mt**3, 3 * mt**2 * t, 3 * mt * t**2, t**3
        x = a * p0.x + b * p1.x + c * p2.x + d * p3.x
        y = a * p0.y + b * p1.y + c * p2.y + d * p3.y
        out.append((x, y))
    return out


def _flatten_items(items) -> list[Point]:
    """Flatten one drawing path's items into a single polyline."""
    pts: list[Point] = []

    def add(p: Point):
        if not pts or pts[-1] != p:
            pts.append(p)

    for it in items:
        op = it[0]
        if op == "l":
            p1, p2 = it[1], it[2]
            add((p1.x, p1.y))
            add((p2.x, p2.y))
        elif op == "c":
            p0, p1, p2, p3 = it[1], it[2], it[3], it[4]
            add((p0.x, p0.y))
            for p in _bezier_points(p0, p1, p2, p3):
                add(p)
        elif op == "re":
            r = it[1]
            for p in [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1),
                      (r.x0, r.y1), (r.x0, r.y0)]:
                add(p)
        elif op == "qu":
            q = it[1]
            for corner in (q.ul, q.ur, q.lr, q.ll, q.ul):
                add((corner.x, corner.y))
    return pts


def _bbox_of(points: list[Point]) -> BBox:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _as_color(c) -> Color | None:
    if c is None:
        return None
    return (float(c[0]), float(c[1]), float(c[2]))


def _alpha(o) -> float | None:
    """Normalize a PDF opacity to [0,1]; None when opaque/absent (so the common
    fully-opaque case adds nothing to the record)."""
    if o is None:
        return None
    try:
        a = float(o)
    except (TypeError, ValueError):
        return None
    if a >= 0.995:
        return None
    return max(0.0, a)


# Math-SYMBOL fonts carry the PDF italic flag but are not italic TEXT (a single
# glyph like 'µ' or '≪' must not be slanted as a variable). Excluded from italic.
_SYMBOL_FONT_KEYS = ("cmsy", "cmex", "msam", "msbm", "rsfs", "symbol", "dingbat",
                     "wingding", "marvosym", "esint", "stmary", "mathematica")


def _span_italic(flags: int, fontname: str) -> bool:
    """True when a span is italic/oblique TEXT (bit 1 of fitz flags, or an
    italic/oblique font name), excluding math-symbol fonts."""
    name = (fontname or "").lower()
    if any(k in name for k in _SYMBOL_FONT_KEYS):
        return False
    return bool(flags & 2) or "italic" in name or "oblique" in name


def _span_bold(flags: int, fontname: str) -> bool:
    """True when a span is bold TEXT (bit 4 of fitz flags, or a bold font name),
    excluding math-symbol fonts."""
    name = (fontname or "").lower()
    if any(k in name for k in _SYMBOL_FONT_KEYS):
        return False
    return bool(flags & 16) or "bold" in name


def _dashes_str(d) -> str | None:
    if not d:
        return None
    s = str(d).strip()
    # fitz reports a solid line as the string "[] 0"; treat as no dashes.
    if s in ("[] 0", "[]"):
        return None
    return s


def _is_round_cap(lc) -> bool:
    """fitz reports lineCap as an int or a 3-tuple (start/mid/end); PDF cap 1 =
    round. Treat the path as round-capped when its (first) cap is 1."""
    if lc is None:
        return False
    if isinstance(lc, (tuple, list)):
        return bool(lc) and lc[0] == 1
    return lc == 1


def load_page(page: fitz.Page) -> tuple[list[Path], list[TextSpan]]:
    """Return (paths, texts) for a single fitz page."""
    paths: list[Path] = []
    for d in page.get_drawings():
        pts = _flatten_items(d["items"])
        if len(pts) < 2:
            continue
        paths.append(
            Path(
                points=pts,
                stroke=_as_color(d.get("color")),
                fill=_as_color(d.get("fill")),
                width=d.get("width"),
                dashes=_dashes_str(d.get("dashes")),
                closed=bool(d.get("closePath")),
                bbox=_bbox_of(pts),
                stroke_alpha=_alpha(d.get("stroke_opacity")),
                fill_alpha=_alpha(d.get("fill_opacity")),
                round_cap=_is_round_cap(d.get("lineCap")),
            )
        )

    texts: list[TextSpan] = []
    decoder = FontDecoder(page.parent)
    td = page.get_text("dict")
    for block in td.get("blocks", []):
        for line in block.get("lines", []):
            ldir = line.get("dir", (1.0, 0.0))
            for span in line.get("spans", []):
                text = span.get("text", "")
                if text == "":
                    continue
                # Recover text mangled by a broken ToUnicode map (private math /
                # symbol fonts). Gated: only broken spans engage, so normal text
                # is returned unchanged. Labels/titles only -- never coordinates.
                fontname = span.get("font", "")
                if is_broken_text(text, fontname):
                    rec = decoder.recover(page, [ord(c) for c in text], fontname)
                    if rec:
                        text = rec
                bb = span["bbox"]
                # PyMuPDF encodes text color as 0xRRGGBB integer; 0 = black.
                c_int = span.get("color", 0) or 0
                if c_int:
                    tc: tuple[float, float, float] | None = (
                        ((c_int >> 16) & 0xFF) / 255.0,
                        ((c_int >> 8) & 0xFF) / 255.0,
                        (c_int & 0xFF) / 255.0,
                    )
                else:
                    tc = None
                texts.append(
                    TextSpan(
                        text=text,
                        bbox=(bb[0], bb[1], bb[2], bb[3]),
                        size=span.get("size"),
                        dir=(float(ldir[0]), float(ldir[1])),
                        color=tc,
                        italic=_span_italic(span.get("flags", 0), fontname),
                        bold=_span_bold(span.get("flags", 0), fontname),
                    )
                )

    return paths, texts


def _image_rects(page: fitz.Page) -> list[tuple[float, float, float, float]]:
    """Bounding boxes of raster images placed on the page (PDF points)."""
    rects: list[tuple[float, float, float, float]] = []
    try:
        for info in page.get_image_info():
            b = info.get("bbox")
            if b:
                rects.append((b[0], b[1], b[2], b[3]))
    except Exception:
        pass
    return rects


def page_count(path: str) -> int:
    """Page count without loading any drawings (cheap)."""
    with fitz.open(path) as doc:
        return doc.page_count


def load_pdf(path: str, pages: list[int] | None = None) -> list[PageData]:
    """Load a PDF into a list of ``PageData`` (one per page, or selected pages)."""
    doc = fitz.open(path)
    out: list[PageData] = []
    indices = pages if pages is not None else range(doc.page_count)
    for i in indices:
        page = doc[i]
        p, t = load_page(page)
        out.append(
            PageData(
                page_index=i,
                width=page.rect.width,
                height=page.rect.height,
                paths=p,
                texts=t,
                image_rects=_image_rects(page),
            )
        )
    return out
