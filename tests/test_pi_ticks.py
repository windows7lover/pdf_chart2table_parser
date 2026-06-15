"""Regression: π-axis tick labels parse to numeric values.

Bug (2405.15494_p38c5): a sinusoid on a 0..2π axis (ticks '0', 'π', '2π') was
miscalibrated to 0..2 -- the π glyph (U+03C0) survives extraction but
``_is_numeric_span`` filtered it out and ``_parse_plain`` couldn't read it, so 'π'
became no tick and '2π' (spans '2'+'π') parsed as just '2'. The points then plotted
over the wrong x-range (looked scattered, not sinusoidal). π-expressions are now
recognised and valued.
"""
from __future__ import annotations

import math

from pdf_chart2table.axes import _is_numeric_span, _parse_pi, _parse_plain


def test_parse_pi_expressions():
    assert _parse_pi("π") == math.pi
    assert _parse_pi("2π") == 2 * math.pi
    assert abs(_parse_pi("π/2") - math.pi / 2) < 1e-12
    assert abs(_parse_pi("3π/2") - 3 * math.pi / 2) < 1e-12
    assert _parse_pi("-π") == -math.pi
    assert _parse_pi("0.5π") == 0.5 * math.pi
    assert _parse_pi("2") is None        # plain number is not a π expression
    assert _parse_pi("xπ") is None       # not a clean coefficient


def test_pi_spans_are_numeric_and_parsed():
    # so the π glyph survives the numeric-span filter and calibrates
    assert _is_numeric_span("π")
    assert _is_numeric_span("2π")
    assert _parse_plain("2π") == 2 * math.pi
    assert _parse_plain("π") == math.pi
    # ordinary numbers still parse normally
    assert _parse_plain("2") == 2.0
    assert not _is_numeric_span("time")
