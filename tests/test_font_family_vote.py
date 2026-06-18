"""Regression: font family voted over the chart's OWN text, not leaked body text.

2001.08430_p4c1: recover_text_style classified the family by voting over every
span in the region+margin, which captured the surrounding paper's Computer
Modern body text (CMR, serif) -- outvoting the chart's ArialMT ticks -> an Arial
chart rendered serif. The vote now uses the chart's structural text (tick labels
+ legend), so the leaked body prose cannot flip it.
"""
from __future__ import annotations

from pdf_chart2table import style as S


def _span(text, x0, y0, x1, y1, font, size=7.4):
    return {"text": text, "bbox": (x0, y0, x1, y1), "size": size,
            "flags": 0, "font": font}


def test_body_text_cmr_does_not_flip_arial_chart_to_serif(monkeypatch):
    # Chart's own ticks in ArialMT (few chars) + a lot of leaked CMR body prose.
    spans = [
        _span("0", 20, 130, 26, 138, "ArialMT"),
        _span("50", 60, 130, 70, 138, "ArialMT"),
        _span("100", 100, 130, 116, 138, "ArialMT"),
        # surrounding body text (Computer Modern, serif) -- many chars
        _span("we observe that the quantity grows monotonically with",
              10, 5, 200, 13, "CMR10"),
        _span("for all measured samples in the considered regime here",
              10, 200, 200, 208, "CMR9"),
    ]
    monkeypatch.setattr(S, "_spans_in_region", lambda *a, **k: (spans, {
        "ArialMT": 5, "CMR10": 53, "CMR9": 54}))
    ts = S.recover_text_style(None, (0.0, 0.0, 220.0, 220.0),
                              {"x": None, "y": None}, [None],
                              None, ["0", "50", "100"])
    assert ts["font_family"] == "sans-serif", \
        "Arial chart must stay sans-serif despite leaked CMR body text"
    assert ts["latex_like"] is False


def test_genuine_cmr_chart_stays_serif(monkeypatch):
    # When the chart's OWN ticks are CMR, it is correctly serif/latex.
    spans = [
        _span("0", 20, 130, 26, 138, "CMR10"),
        _span("50", 60, 130, 70, 138, "CMR10"),
        _span("100", 100, 130, 116, 138, "CMR10"),
    ]
    monkeypatch.setattr(S, "_spans_in_region", lambda *a, **k: (spans, {"CMR10": 6}))
    ts = S.recover_text_style(None, (0.0, 0.0, 220.0, 220.0),
                              {"x": None, "y": None}, [None],
                              None, ["0", "50", "100"])
    assert ts["font_family"] == "serif"
