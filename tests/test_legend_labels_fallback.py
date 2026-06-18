"""Regression for the legend_labels fallback in style.recover_text_style.

2004.01004_p6c3: a colour-ambiguous legend (4 entries, colours black,black,red,
red over 6 series) left every series.label == None, so the text legend matcher
(which keys off series labels) found nothing -> the OCR-glyph fallback fired,
LOSING the frame bbox and double-drawing the labels as annotations.

The fix lets build_chart_style pass the legend's OWN recovered labels
(labels.detect_labels) as ``legend_labels``; recover_text_style uses them when no
series carries a label, so the readable text legend recovers normally (frame
bbox + entries) and its spans are consumed (not re-drawn as annotations).
"""
from __future__ import annotations

from pdf_chart2table import style as S


def _span(text, x0, y0, x1, y1, size=8.0):
    return {"text": text, "bbox": (x0, y0, x1, y1), "size": size,
            "flags": 0, "font": "Helvetica"}


def _legend_spans():
    # three stacked legend labels on the left of a 200x200 region
    return [_span("alpha", 20, 40, 60, 48),
            _span("beta", 20, 55, 60, 63),
            _span("gamma", 20, 70, 60, 78)]


def test_no_legend_when_series_unlabelled_and_no_legend_labels(monkeypatch):
    monkeypatch.setattr(S, "_spans_in_region", lambda *a, **k: (_legend_spans(), {}))
    ts = S.recover_text_style(None, (0.0, 0.0, 200.0, 200.0),
                              {"x": None, "y": None}, [None, None, None], None, [])
    # No series labels and no legend_labels -> matcher has nothing -> no legend,
    # and the unconsumed label spans fall through to annotations (the old bug).
    assert not (ts.get("legend") or {}).get("entries")
    annots = {a["text"] for a in ts.get("annotations", [])}
    assert {"alpha", "beta", "gamma"} <= annots


def test_legend_labels_recover_box_and_consume_spans(monkeypatch):
    monkeypatch.setattr(S, "_spans_in_region", lambda *a, **k: (_legend_spans(), {}))
    ts = S.recover_text_style(None, (0.0, 0.0, 200.0, 200.0),
                              {"x": None, "y": None}, [None, None, None], None, [],
                              legend_labels=["alpha", "beta", "gamma"])
    leg = ts.get("legend") or {}
    assert len(leg.get("entries", [])) == 3, "legend should recover from legend_labels"
    assert leg.get("bbox_frac") is not None, "frame bbox must be recovered"
    # consumed as legend -> NOT double-drawn as annotations
    annots = {a["text"] for a in ts.get("annotations", [])}
    assert not ({"alpha", "beta", "gamma"} & annots), "legend labels must not leak to annotations"


def test_series_labels_take_priority_over_legend_labels(monkeypatch):
    # When series ARE labelled, legend_labels is ignored (normal path unchanged).
    monkeypatch.setattr(S, "_spans_in_region", lambda *a, **k: (_legend_spans(), {}))
    ts = S.recover_text_style(None, (0.0, 0.0, 200.0, 200.0),
                              {"x": None, "y": None},
                              ["alpha", "beta", "gamma"], None, [],
                              legend_labels=["WRONG1", "WRONG2"])
    leg = ts.get("legend") or {}
    labels = {e["label"] for e in leg.get("entries", [])}
    assert labels == {"alpha", "beta", "gamma"}
