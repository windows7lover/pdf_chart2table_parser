"""Inspect the vector primitives and text a PDF chart contains.

Prints a per-page summary (path/item/color/marker-vs-line breakdown, text spans)
and renders an overlay PNG: the rasterized page with every vector path bbox and
text span bbox drawn on top, so a human can see what the parser "sees".

NOTE: PyMuPDF (fitz) can only open PDF, NOT EPS. Use the .pdf fixtures.

Run: uv run python scripts/inspect_pdf.py <pdf> [--page N] [--out overlay.png]
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import fitz
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


def _round_color(c, nd=2):
    """Round an RGB tuple (or None) to a hashable key for histogramming."""
    if c is None:
        return None
    return tuple(round(float(v), nd) for v in c)


def _path_len(path):
    """Number of geometric vertices implied by a path's items."""
    n = 0
    for it in path["items"]:
        op = it[0]
        if op == "l":
            n += 1
        elif op == "c":
            n += 3
        elif op == "re":
            n += 4
    return n


def _spans(page):
    td = page.get_text("dict")
    return [
        s
        for b in td["blocks"]
        for line in b.get("lines", [])
        for s in line.get("spans", [])
    ]


def summarize_page(page, page_index):
    drawings = page.get_drawings()
    spans = _spans(page)

    item_counts = Counter()
    stroke_hist = Counter()
    fill_hist = Counter()
    vertex_counts = []
    for path in drawings:
        for it in path["items"]:
            item_counts[it[0]] += 1
        stroke_hist[_round_color(path["color"])] += 1
        fill_hist[_round_color(path["fill"])] += 1
        vertex_counts.append(_path_len(path))

    # Markers: small paths with few vertices; lines: long polylines.
    marker_candidates = sum(1 for n in vertex_counts if 0 < n <= 8)
    line_candidates = sum(1 for n in vertex_counts if n > 8)

    print(f"\n=== page {page_index} ===  rect={tuple(round(v, 1) for v in page.rect)}")
    print(f"drawing paths: {len(drawings)}")
    items_str = ", ".join(f"{op}={item_counts[op]}" for op in sorted(item_counts))
    print(f"  items: {items_str or '(none)'}  "
          f"(l=line, c=curve, re=rect)")
    print(f"  candidate markers (<=8 verts): {marker_candidates}  |  "
          f"candidate lines (>8 verts): {line_candidates}")

    print("  stroke colors (rounded -> count):")
    for col, n in stroke_hist.most_common(10):
        print(f"    {str(col):<22} {n}")
    print("  fill colors (rounded -> count):")
    for col, n in fill_hist.most_common(10):
        print(f"    {str(col):<22} {n}")

    print(f"text spans: {len(spans)}")
    for s in spans:
        bbox = tuple(round(v, 1) for v in s["bbox"])
        print(f"    {s['text']!r:<24} bbox={bbox}")

    return drawings, spans


def render_overlay(page, drawings, spans, out_path, zoom=2.0):
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    import numpy as np

    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:  # RGBA
        img = img[:, :, :3]

    fig, ax = plt.subplots(figsize=(pix.width / 100, pix.height / 100), dpi=100)
    ax.imshow(img)

    # PDF points -> pixmap pixels is just multiply by zoom.
    for path in drawings:
        x0, y0, x1, y1 = path["rect"]
        stroke = path["color"]
        edge = stroke if stroke is not None else (0.5, 0.5, 0.5)
        ax.add_patch(
            Rectangle(
                (x0 * zoom, y0 * zoom),
                (x1 - x0) * zoom,
                (y1 - y0) * zoom,
                fill=False,
                edgecolor=edge,
                linewidth=0.5,
            )
        )

    for s in spans:
        x0, y0, x1, y1 = s["bbox"]
        ax.add_patch(
            Rectangle(
                (x0 * zoom, y0 * zoom),
                (x1 - x0) * zoom,
                (y1 - y0) * zoom,
                fill=False,
                edgecolor="magenta",
                linewidth=0.7,
                linestyle="--",
            )
        )

    ax.set_xlim(0, pix.width)
    ax.set_ylim(pix.height, 0)
    ax.set_axis_off()
    fig.tight_layout(pad=0)
    fig.savefig(out_path, dpi=100)
    plt.close(fig)
    print(f"\nwrote overlay -> {out_path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", help="path to a .pdf (fitz cannot open .eps)")
    parser.add_argument("--page", type=int, default=None,
                        help="0-based page to inspect (default: all)")
    parser.add_argument("--out", default=None,
                        help="overlay PNG path (default: <pdf_stem>_overlay.png)")
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if pdf_path.suffix.lower() == ".eps":
        parser.error("fitz cannot open EPS; pass the .pdf fixture instead.")

    doc = fitz.open(pdf_path)
    pages = [args.page] if args.page is not None else range(doc.page_count)

    out_base = Path(args.out) if args.out else pdf_path.with_name(f"{pdf_path.stem}_overlay.png")

    for pi in pages:
        page = doc[pi]
        drawings, spans = summarize_page(page, pi)
        # One overlay per page; suffix with page index when rendering multiple.
        if args.page is not None or doc.page_count == 1:
            out = out_base
        else:
            out = out_base.with_name(f"{out_base.stem}_p{pi}{out_base.suffix}")
        render_overlay(page, drawings, spans, out)


if __name__ == "__main__":
    main()
