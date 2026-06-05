---
name: pdf-vector-inspect
description: Use when debugging what vector primitives or text a PDF chart contains, or diagnosing why region detection / series extraction is wrong. Runs scripts/inspect_pdf.py to dump per-page path/color/marker-vs-line/text-span counts and render an overlay PNG of what the parser "sees".
---

# Inspect a PDF chart's vectors and text

When detection or extraction looks off, look at the raw primitives before
touching heuristics. This is the M1 debug loop.

## Run it

```bash
export UV_LINK_MODE=copy
uv run python scripts/inspect_pdf.py <pdf> [--page N] [--out overlay.png]
```

- `<pdf>` must be a **.pdf** — fitz (PyMuPDF) **cannot open .eps**, even though
  fixtures ship both. Always pass the `.pdf`.
- `--page` is 0-based; omit to inspect every page (one overlay per page).
- `--out` defaults to `<pdf_stem>_overlay.png`.

## What the printout tells you

- **drawing paths / items** — `l`=line segment, `c`=bezier curve, `re`=rect.
- **candidate markers vs lines** — paths with <=8 vertices are likely markers;
  long polylines (>8) are likely data lines, axes, or frame.
- **stroke / fill color histogram** — each data series is one consistent
  (stroke, fill) color; counts roughly equal the point count per series. Black
  `(0,0,0)` and gray `(0.8,0.8,0.8)` are usually axes/frame/gridlines, not data.
- **text spans** — exact strings + bboxes. Tick labels, axis titles, legend
  labels, title all come from here (no OCR in the vector case).

## Reading the overlay PNG

The page is rasterized as the background; on top:
- each **vector path bbox** is drawn, edge-colored by its stroke color — so the
  three series in a 3-cluster scatter show up as three colors of boxes;
- each **text span bbox** is a magenta dashed rectangle.

If markers have no boxes, they may be font glyphs (read via `get_text`) rather
than paths. If a "series" box spans the whole plot, it is a gridline/frame, not
data — exclude it.

## Key fitz facts

- `page.get_drawings()` returns dicts with: `items` (ops `l`/`c`/`re`), `color`
  (stroke RGB or None), `fill` (RGB or None), `width`, `dashes`, `closePath`,
  `rect` (bbox), `type`.
- `page.get_text("dict")` → blocks → lines → spans, each span has `text` + `bbox`.
- Coordinates are PDF points; the overlay multiplies by the pixmap zoom (2x).
