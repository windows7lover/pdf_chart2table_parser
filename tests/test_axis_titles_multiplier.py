"""Regression tests for multi-panel y-title mis-assignment and x10^n offsets.

Reproduces bugs found on 2002.04278_p15c3 (a dense multi-panel figure):
* the y-title detector grabbed a NEIGHBOUR panel's "Energy" (far left / above)
  instead of this panel's own "Integrated ... (Counts)";
* an Origin-style "x10^5" multiplier (separate x/10/5 spans at the axis top) was
  not detected, so y values were off by 1e5.
"""
from __future__ import annotations

from pdf_chart2table.axes import _y_axis_multiplier, _y_title
from pdf_chart2table.model import Region, TextSpan

# region: x in [347,477], y in [344,422]  (panel top=344, bottom=422)
REGION = Region(bbox=(347.0, 344.0, 477.0, 422.0), path_indices=[], text_indices=[])


def _vspan(text, cx, cy):
    return TextSpan(text=text, bbox=(cx - 3, cy - 4, cx + 3, cy + 4),
                    size=6.0, dir=(0.0, -1.0))


def _hspan(text, x0, y0, x1, y1):
    return TextSpan(text=text, bbox=(x0, y0, x1, y1), size=5.0, dir=(1.0, 0.0))


def test_y_title_ignores_neighbour_panel_and_assembles_multispan():
    texts = [
        # THIS panel's title (vertical column just left of the spine, x=334).
        # Spans carry trailing spaces -> the join must collapse them, not double.
        _vspan("Integrated ", 334, 417),
        _vspan("PL ", 334, 396),
        _vspan("intensity ", 334, 375),
        _vspan("(Counts)", 334, 347),
        # neighbour panel labels: far left (x=134) and above the region (y=272)
        _vspan("Energy", 134, 335),
        _vspan("Energy", 319, 272),
    ]
    title = _y_title(texts, REGION)
    assert title is not None and "Energy" not in title
    assert "Integrated" in title and "(Counts)" in title
    assert "  " not in title  # no double spaces
    assert title == "Integrated PL intensity (Counts)"


def test_superscript_x10n_multiplier_detected():
    # "x 10^5" drawn as separate spans near the top-left of the y-axis.
    texts = [
        _hspan("×", 346, 340, 352, 346),   # x, baseline
        _hspan("10", 353, 338, 359, 344),
        _hspan("5", 359, 335, 363, 341),         # exponent: right of "10", raised
    ]
    assert _y_axis_multiplier(texts, REGION, []) == 100000.0


def test_superscript_times10_glued_mantissa_negative_exponent():
    """MATLAB ×10^-4 offset: mantissa is one glued '×10' span, exponent '−4'.

    Reproduces 2503.12775_p12c4, whose y-axis offset is rendered as a single
    span '×10' plus a raised '−4' (unicode minus). The old detector required an
    exact '10' mantissa and a bare-digit exponent, so the multiplier was dropped
    and y values came out ~1e4x too large.
    """
    texts = [
        _hspan("×10", 346, 338, 358, 345),       # glued mantissa
        _hspan("−4", 358, 334, 365, 340),         # raised, unicode-minus exponent
    ]
    assert _y_axis_multiplier(texts, REGION, []) == 1e-4


def test_superscript_separate_minus_and_digit_spans():
    """×10^-2 offset where the SIGN and DIGIT are SEPARATE raised spans.

    Reproduces 2002.06092_p6c1: the offset is '×','10','−','2' as four distinct
    spans. The old scan tried int() per span, int('−') failed, so it kept '2' and
    returned ×10^2 -- y values came out 1e4x too large. The exponent must be read
    as the JOINED contiguous raised run '−2'."""
    texts = [
        _hspan("×", 346, 340, 352, 346),
        _hspan("10", 353, 338, 359, 344),
        _hspan("−", 359, 335, 363, 341),         # raised, separate sign glyph
        _hspan("2", 363, 335, 366, 341),         # raised, separate digit
    ]
    assert _y_axis_multiplier(texts, REGION, []) == 1e-2


def test_superscript_missing_minus_glyph_yields_no_multiplier():
    """×10^-5 whose minus glyph was NOT extracted leaves a GAP between '10' and
    the raised '5' (2003.13245_p8c2). Rather than guess ×10^5 (catastrophic, data
    1e10x off), the contiguity break -> no multiplier (1.0)."""
    texts = [
        _hspan("×", 346, 340, 352, 346),
        _hspan("10", 353, 338, 359, 344),
        _hspan("5", 363.5, 335, 366.5, 341),     # 4.5pt gap where '−' should be
    ]
    assert _y_axis_multiplier(texts, REGION, []) == 1.0


def test_log_decade_label_not_read_as_multiplier():
    """A log y-axis labelled with decades '10^5..10^0' (each = a '10' mantissa +
    a raised exponent span) must NOT have its topmost '10^5' tick mistaken for a
    'x10^5' axis multiplier (the 2003.03611_p8c3 bug). With the decade labels
    passed as the label-band spans, the multiplier must come back 1.0."""
    x0, y0 = 347.0, 344.0
    spans = []
    for exp, yc in [("5", 350), ("3", 372), ("2", 394), ("0", 410)]:
        spans.append(TextSpan(text="10", bbox=(x0 - 22, yc - 3, x0 - 12, yc + 3)))
        spans.append(TextSpan(text=exp, bbox=(x0 - 11, yc - 6, x0 - 6, yc)))  # raised
    assert _y_axis_multiplier(spans, REGION, spans) == 1.0
