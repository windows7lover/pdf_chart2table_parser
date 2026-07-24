"""Convert our canonical chart record -> the ground-truth dataset schema.

The dataset (``semiconductor_groundtruth_v1``) pairs a rendered reconstruction
image with a *data-only* JSON label in the schema the user provided
(``shared_folder/example_json``):

    {chart_type, title, x_label, y_label, topic, llm_plot_style, llm_data_mode,
     series: [{name, x_raw, y_raw, has_width, bbox_sizes, has_error_band, yerr_raw}]}

This module holds the pure mapping (no I/O). Style is intentionally NOT carried
into the label -- only the data (series x/y, axis labels, title, chart type, and
error-bar magnitudes). Decisions baked in (per user, 2026-06-18):
  * ``topic`` is always "" (we don't have a generator topic seed).
  * ``llm_data_mode`` = "extracted", ``llm_plot_style`` = "" (honest provenance:
    these are extracted from real figures, not generated).
  * our data carries error *bars* (per-point ``y_err``); we surface them through
    the schema's ``has_error_band`` / ``yerr_raw`` fields (the schema has no
    separate error-bar field).
"""

from __future__ import annotations

from typing import Any


def _text(v: Any) -> str:
    """A label field (title / axis title) -> plain string, never None.

    Accepts our record forms: a {"text": ...} dict, a bare string, or None.
    """
    if v is None:
        return ""
    if isinstance(v, dict):
        return (v.get("text") or "").strip()
    return str(v).strip()


def _chart_type(record: dict) -> str:
    """"scatter" iff every series renders as scatter, else "line".

    Prefers the recovered ``style.series[].render_as`` (the authoritative
    line/scatter signal). Falls back to marker presence when no style is
    embedded: all-markered + no style -> "scatter", otherwise "line".
    """
    style = record.get("style") or {}
    st_series = style.get("series") or []
    if st_series:
        modes = [str(s.get("render_as") or "").lower() for s in st_series]
        if modes and all(m == "scatter" for m in modes):
            return "scatter"
        return "line"
    # No style: fall back to the data series' marker field.
    data_series = record.get("series") or []
    if data_series and all(s.get("marker") for s in data_series):
        return "scatter"
    return "line"


def _series_to_gt(s: dict) -> dict:
    """One data Series record -> one ground-truth series dict."""
    points = s.get("points") or []
    x_raw = [p.get("x") for p in points]
    y_raw = [p.get("y") for p in points]
    # Error BARS are stored per-point as ``y_err`` (data units); surface them via
    # the schema's error-band fields. Only emit yerr_raw when at least one point
    # actually carries an error, and then make it the same length as x/y (0.0 for
    # points without a recovered whisker).
    has_err = any(p.get("y_err") for p in points)
    yerr_raw = [float(p.get("y_err") or 0.0) for p in points] if has_err else []
    return {
        "name": (s.get("label") or "").strip(),
        "x_raw": x_raw,
        "y_raw": y_raw,
        "has_width": False,      # bar widths -> bars are out of scope
        "bbox_sizes": [],
        "has_error_band": bool(has_err),
        "yerr_raw": yerr_raw,
    }


def record_to_groundtruth(record: dict) -> dict:
    """Map a canonical chart record (our chart.json) to the dataset schema.

    ``record`` is the dict written by ``io_store.chart_to_record`` (optionally
    with an embedded ``style`` key). Returns a new dict in the target schema;
    does no I/O and does not mutate ``record``.
    """
    x_axis = record.get("x_axis") or {}
    y_axis = record.get("y_axis") or {}
    return {
        "chart_type": _chart_type(record),
        "title": _text(record.get("title")),
        "x_label": _text(x_axis.get("title")),
        "y_label": _text(y_axis.get("title")),
        "topic": "",
        "llm_plot_style": "",
        "llm_data_mode": "extracted",
        "series": [_series_to_gt(s) for s in (record.get("series") or [])],
    }
