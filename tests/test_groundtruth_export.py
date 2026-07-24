"""Tests for groundtruth_export.record_to_groundtruth (pure mapping, no I/O)."""

from __future__ import annotations

from pdf_chart2table.groundtruth_export import record_to_groundtruth


def _pts(xs, ys, errs=None):
    out = []
    for i, (x, y) in enumerate(zip(xs, ys)):
        p = {"x": x, "y": y, "x_px": 0.0, "y_px": 0.0}
        if errs is not None and errs[i] is not None:
            p["y_err"] = errs[i]
        out.append(p)
    return out


def _record(series, *, title=None, xtitle=None, ytitle=None, style=None):
    return {
        "title": title,
        "x_axis": {"title": xtitle},
        "y_axis": {"title": ytitle},
        "series": series,
        **({"style": style} if style is not None else {}),
    }


def test_basic_line_mapping_and_point_order():
    xs = [3.0, 1.0, 2.0]  # deliberately unsorted -> order must be preserved
    ys = [9.0, 1.0, 4.0]
    rec = _record(
        [{"label": "A", "marker": None, "color": [0, 0, 0], "points": _pts(xs, ys)}],
        title={"text": "T"}, xtitle="X (u)", ytitle="Y (v)",
        style={"series": [{"render_as": "line"}]},
    )
    gt = record_to_groundtruth(rec)
    assert gt["chart_type"] == "line"
    assert gt["title"] == "T"
    assert gt["x_label"] == "X (u)"
    assert gt["y_label"] == "Y (v)"
    assert gt["topic"] == ""
    assert gt["llm_data_mode"] == "extracted"
    assert gt["llm_plot_style"] == ""
    s = gt["series"][0]
    assert s["name"] == "A"
    assert s["x_raw"] == xs  # order preserved, NOT sorted
    assert s["y_raw"] == ys
    assert s["has_width"] is False and s["bbox_sizes"] == []
    assert s["has_error_band"] is False and s["yerr_raw"] == []


def test_scatter_inference_all_scatter():
    rec = _record(
        [{"label": "p", "points": _pts([1, 2], [1, 2])},
         {"label": "q", "points": _pts([1, 2], [3, 4])}],
        style={"series": [{"render_as": "scatter"}, {"render_as": "scatter"}]},
    )
    assert record_to_groundtruth(rec)["chart_type"] == "scatter"


def test_mixed_render_as_is_line():
    rec = _record(
        [{"label": "p", "points": _pts([1], [1])},
         {"label": "q", "points": _pts([1], [3])}],
        style={"series": [{"render_as": "scatter"}, {"render_as": "line"}]},
    )
    assert record_to_groundtruth(rec)["chart_type"] == "line"


def test_chart_type_fallback_without_style():
    # no style, all series have a marker -> scatter
    rec = _record([{"label": "p", "marker": "o", "points": _pts([1], [1])}])
    assert record_to_groundtruth(rec)["chart_type"] == "scatter"
    # no style, no markers -> line
    rec2 = _record([{"label": "p", "marker": None, "points": _pts([1], [1])}])
    assert record_to_groundtruth(rec2)["chart_type"] == "line"


def test_label_and_axis_fallbacks_to_empty_string():
    rec = _record([{"label": None, "points": _pts([1], [1])}],
                  title=None, xtitle=None, ytitle=None,
                  style={"series": [{"render_as": "line"}]})
    gt = record_to_groundtruth(rec)
    assert gt["title"] == "" and gt["x_label"] == "" and gt["y_label"] == ""
    assert gt["series"][0]["name"] == ""


def test_error_bars_map_to_yerr_aligned_and_zero_filled():
    # two of three points carry a whisker -> yerr_raw same length, 0.0 where absent
    pts = _pts([1, 2, 3], [10, 20, 30], errs=[0.5, None, 1.5])
    rec = _record([{"label": "E", "points": pts}],
                  style={"series": [{"render_as": "scatter"}]})
    s = record_to_groundtruth(rec)["series"][0]
    assert s["has_error_band"] is True
    assert s["yerr_raw"] == [0.5, 0.0, 1.5]
    assert len(s["yerr_raw"]) == len(s["x_raw"])


def test_multi_series_and_empty_record():
    rec = _record(
        [{"label": "A", "points": _pts([1, 2], [1, 2])},
         {"label": "B", "points": _pts([1, 2], [5, 6])}],
        style={"series": [{"render_as": "line"}, {"render_as": "line"}]},
    )
    gt = record_to_groundtruth(rec)
    assert len(gt["series"]) == 2 and gt["series"][1]["name"] == "B"
    # empty
    empty = record_to_groundtruth(_record([]))
    assert empty["series"] == [] and empty["chart_type"] == "line"
