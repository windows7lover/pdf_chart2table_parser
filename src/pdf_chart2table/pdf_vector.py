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


def _dashes_str(d) -> str | None:
    if not d:
        return None
    s = str(d).strip()
    # fitz reports a solid line as the string "[] 0"; treat as no dashes.
    if s in ("[] 0", "[]"):
        return None
    return s


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
            )
        )

    texts: list[TextSpan] = []
    td = page.get_text("dict")
    for block in td.get("blocks", []):
        for line in block.get("lines", []):
            ldir = line.get("dir", (1.0, 0.0))
            for span in line.get("spans", []):
                text = span.get("text", "")
                if text == "":
                    continue
                bb = span["bbox"]
                texts.append(
                    TextSpan(
                        text=text,
                        bbox=(bb[0], bb[1], bb[2], bb[3]),
                        size=span.get("size"),
                        dir=(float(ldir[0]), float(ldir[1])),
                    )
                )

    return paths, texts


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
            )
        )
    return out
