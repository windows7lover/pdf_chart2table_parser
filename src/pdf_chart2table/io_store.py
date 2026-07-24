"""Serialize a detected chart to the canonical output artifacts.

Per the plan, each extracted chart in ``OUTDIR/<pdf_stem>/`` yields:

* ``page{n}_chart{k}.json`` -- the canonical lossless record (schema below);
* ``page{n}_chart{k}.markers.csv`` -- derived long-form table
  (``series,x,y,x_px,y_px``);
* ``page{n}_chart{k}.pdf`` / ``page{n}_chart{k}.svg`` -- the *original* vector
  graphics of the region, copied losslessly via PyMuPDF (we crop the page to the
  region bbox; we never redraw the chart).

Skipped charts get a small stub ``page{n}_chart{k}.skip.json``. A batch run
writes a top-level ``manifest.csv`` (+ parquet if available) indexing every
chart.

Coordinates everywhere are raw PDF points (top-left origin), matching PyMuPDF.

Public API:
    chart_to_record(pdf, page, region_bbox, x_axis, y_axis, *, ...) -> dict
    write_chart(record, outdir, page, k) -> dict
    write_skip(reason, source, outdir, page, k) -> dict
    crop_region_pdf(pdf, page_index, bbox, out_pdf, *, margin) -> str
    write_manifest(rows, outdir) -> str
"""

from __future__ import annotations

import csv
import dataclasses
import json
import os

import fitz

# Margin (PDF points) added around a region bbox when cropping so axis tick
# labels and titles drawn just outside the plot frame are kept in the crop.
_CROP_MARGIN = 18.0


# --------------------------------------------------------------------------
# Generic serialization
# --------------------------------------------------------------------------

def _to_plain(obj):
    """Recursively convert dataclasses / tuples to JSON-friendly structures.

    Generic on purpose: ``model.py`` is owned by another agent and may grow new
    fields; ``dataclasses.asdict`` lets those flow through untouched.
    """
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_plain(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_plain(v) for v in obj]
    return obj


def _axis_record(axis) -> dict | None:
    """Build the JSON sub-record for one ``Axis`` (or None if missing)."""
    if axis is None:
        return None
    pr = list(axis.pixel_range) if axis.pixel_range is not None else None
    dr = list(axis.data_range) if axis.data_range is not None else None
    return {
        "title": axis.title,
        "scale": axis.scale,
        "pixel_range": pr,
        "data_range": dr,
        "calibration": _to_plain(axis.calibration),
        "tick_direction": getattr(axis, "tick_direction", None),
        "tick_length": getattr(axis, "tick_length", None),
        # Offset-text axis-scale multiplier (tick values are ALREADY rescaled
        # by it; metadata only -- see model.Axis.multiplier).
        "multiplier": getattr(axis, "multiplier", 1.0),
        # "suspect" when the labeled tick VALUES' spacing pattern contradicts
        # the fitted scale (flag only, never alters the calibration).
        "tick_consistency": getattr(axis, "tick_consistency", None),
    }


def _ticks_record(axis) -> list[dict]:
    if axis is None:
        return []
    return [{"pixel": t.pixel, "value": t.value, "label": t.label}
            for t in axis.ticks]


def _label_record(lbl) -> dict | None:
    """Normalize an optional title/caption into {text, bbox} or None.

    Accepts a dataclass (e.g. ``TextSpan``), a plain dict, or a bare string so
    we stay robust to whatever the (parallel) ``labels`` module returns.
    """
    if lbl is None:
        return None
    if isinstance(lbl, str):
        return {"text": lbl, "bbox": None}
    if isinstance(lbl, dict):
        return {"text": lbl.get("text"), "bbox": lbl.get("bbox")}
    text = getattr(lbl, "text", None)
    bbox = getattr(lbl, "bbox", None)
    return {"text": text, "bbox": list(bbox) if bbox is not None else None}


def _series_record(series) -> list[dict]:
    """Normalize a list of Series dataclasses to the JSON schema."""
    out: list[dict] = []
    for s in series or []:
        d = _to_plain(s)
        out.append({
            "label": d.get("label"),
            "marker": d.get("marker"),
            "color": d.get("color"),
            # Role of the ink: "data" / "fit" / "uncertain" (see model.Series).
            # Lets consumers keep marker-less DATA curves instead of dropping
            # every marker=None series as a suspected fit line.
            "role": d.get("role"),
            # Dash form (raw PDF dash string or "dashed"); None when solid.
            # A dashed straight line is the classic fit/guide idiom.
            "dashes": d.get("dashes"),
            "points": d.get("points", []),
        })
    return out


# --------------------------------------------------------------------------
# Canonical record
# --------------------------------------------------------------------------

def chart_to_record(
    pdf: str,
    page: int,
    region_bbox,
    x_axis,
    y_axis,
    *,
    series=None,
    title=None,
    caption=None,
    arrows=None,
    grid=None,
    confidence: float = 0.0,
    vector_file: str | None = None,
) -> dict:
    """Build the canonical JSON dict for one detected chart.

    ``x_axis`` / ``y_axis`` are ``model.Axis`` instances (calibration may be
    None). ``series`` is a list of ``model.Series`` (empty if ``extract`` is not
    available). ``title`` / ``caption`` may be a TextSpan, dict, str, or None.
    """
    return {
        "source": {
            "pdf": pdf,
            "page": page,
            "region_bbox": list(region_bbox),
        },
        "title": _label_record(title),
        "caption": _label_record(caption),
        "x_axis": _axis_record(x_axis),
        "y_axis": _axis_record(y_axis),
        "xticks": _ticks_record(x_axis),
        "yticks": _ticks_record(y_axis),
        "series": _series_record(series),
        "arrows": list(arrows or []),  # annotation arrows (NOT data), removed
        "grid": grid,  # background grid style (NOT data), or None
        "confidence": confidence,
        "vector_file": vector_file,
    }


# --------------------------------------------------------------------------
# Lossless vector crop
# --------------------------------------------------------------------------

def crop_region_pdf(
    pdf: str,
    page_index: int,
    bbox,
    out_pdf: str,
    *,
    margin: float = _CROP_MARGIN,
) -> str:
    """Copy the chart region's *original* vectors to a one-page cropped PDF.

    We copy the single source page into a fresh document and set its cropbox to
    ``bbox`` (+ margin), clamped to the page. Nothing is re-drawn: the vector
    content is the unmodified original; the cropbox just restricts the visible
    (and rendered/SVG) area to the chart. Returns ``out_pdf``.
    """
    src = fitz.open(pdf)
    try:
        page_rect = src[page_index].rect
        x0, y0, x1, y1 = bbox
        crop = fitz.Rect(
            max(page_rect.x0, x0 - margin),
            max(page_rect.y0, y0 - margin),
            min(page_rect.x1, x1 + margin),
            min(page_rect.y1, y1 + margin),
        )
        out = fitz.open()
        try:
            out.insert_pdf(src, from_page=page_index, to_page=page_index)
            out[0].set_cropbox(crop)
            out.save(out_pdf)
        finally:
            out.close()
    finally:
        src.close()
    return out_pdf


def _write_svg_from_pdf(crop_pdf: str, out_svg: str) -> str:
    """Render the cropped single-page PDF to SVG (cropbox-restricted)."""
    doc = fitz.open(crop_pdf)
    try:
        svg = doc[0].get_svg_image()
    finally:
        doc.close()
    with open(out_svg, "w") as f:
        f.write(svg)
    return out_svg


# --------------------------------------------------------------------------
# Writers
# --------------------------------------------------------------------------

_MARKER_COLUMNS = ["series", "x", "y", "x_px", "y_px"]


def _write_markers_csv(record: dict, path: str) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(_MARKER_COLUMNS)
        for s in record.get("series", []):
            label = s.get("label")
            for pt in s.get("points", []):
                w.writerow([
                    label,
                    pt.get("x"),
                    pt.get("y"),
                    pt.get("x_px"),
                    pt.get("y_px"),
                ])


def _counts(record: dict) -> tuple[int, int]:
    series = record.get("series", [])
    n_points = sum(len(s.get("points", [])) for s in series)
    return len(series), n_points


def write_chart(record: dict, outdir: str, page: int, k: int) -> dict:
    """Write the JSON record, markers.csv, and vector crop (PDF + SVG).

    Returns a manifest row dict for this chart.
    """
    os.makedirs(outdir, exist_ok=True)
    stem = f"page{page}_chart{k}"

    crop_pdf = os.path.join(outdir, f"{stem}.pdf")
    crop_svg = os.path.join(outdir, f"{stem}.svg")
    src = record["source"]
    crop_region_pdf(src["pdf"], src["page"], src["region_bbox"], crop_pdf)
    _write_svg_from_pdf(crop_pdf, crop_svg)

    # The canonical record references the SVG as its renderable vector artifact.
    record = dict(record)
    record["vector_file"] = f"{stem}.svg"

    with open(os.path.join(outdir, f"{stem}.json"), "w") as f:
        json.dump(record, f, indent=2)

    _write_markers_csv(record, os.path.join(outdir, f"{stem}.markers.csv"))

    n_series, n_points = _counts(record)
    return {
        "file": src["pdf"],
        "page": page,
        "chart": k,
        "status": "extracted",
        "reason": None,
        "n_series": n_series,
        "n_points": n_points,
        "confidence": record.get("confidence"),
    }


def write_skip(reason: str, source: dict, outdir: str, page: int, k: int) -> dict:
    """Write a skip stub ``page{page}_chart{k}.skip.json`` and return its row."""
    os.makedirs(outdir, exist_ok=True)
    stem = f"page{page}_chart{k}"
    stub = {"status": "skipped", "reason": reason, "source": source}
    with open(os.path.join(outdir, f"{stem}.skip.json"), "w") as f:
        json.dump(stub, f, indent=2)
    return {
        "file": source.get("pdf"),
        "page": page,
        "chart": k,
        "status": "skipped",
        "reason": reason,
        "n_series": 0,
        "n_points": 0,
        "confidence": None,
    }


_MANIFEST_COLUMNS = [
    "file", "page", "chart", "status", "reason",
    "n_series", "n_points", "confidence",
]


def write_manifest(rows: list[dict], outdir: str) -> str:
    """Write ``manifest.csv`` (and ``manifest.parquet`` if pandas allows).

    Returns the path to the CSV manifest.
    """
    os.makedirs(outdir, exist_ok=True)
    csv_path = os.path.join(outdir, "manifest.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_MANIFEST_COLUMNS)
        w.writeheader()
        for row in rows:
            w.writerow({c: row.get(c) for c in _MANIFEST_COLUMNS})

    try:
        import pandas as pd

        pd.DataFrame(rows, columns=_MANIFEST_COLUMNS).to_parquet(
            os.path.join(outdir, "manifest.parquet")
        )
    except Exception:
        # parquet is best-effort (needs pyarrow/fastparquet); CSV is canonical.
        pass

    return csv_path
