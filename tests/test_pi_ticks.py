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


# --- Type3 mathtext glyph mis-decode (2001.01928_p5c1) -----------------------
# The π suffix on the "0.0π .. 3.0π" x-ticks is a matplotlib mathtext Type3 glyph
# (no BaseFont / no ToUnicode, /Differences names it ``s112`` = its Computer-Modern
# slot). PyMuPDF decodes that slot through Latin -> 'p', so the broken-text gate
# misses it. FontDecoder.is_mathtext_type3 recognises the font fingerprint so the
# glyph can then be confirmed by bitmap matching; here we assert the gate.

# A matplotlib-style Type3 font: no BaseFont/ToUnicode, /Differences = s<code>.
_T3_MATHTEXT_PDF = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Resources<</Font<</F1 5 0 R/F2 8 0 R/F3 10 0 R>>>>/Contents 4 0 R>>endobj
4 0 obj<</Length 6>>stream
BT ET
endstream endobj
5 0 obj<</Type/Font/Subtype/Type3/FontBBox[0 0 100 100]/FontMatrix[0.01 0 0 0.01 0 0]/CharProcs 6 0 R/Encoding 7 0 R/FirstChar 112/LastChar 114/Widths[10 0 10]>>endobj
6 0 obj<</s112 9 0 R/s114 9 0 R>>endobj
7 0 obj<</Type/Encoding/Differences[112/s112 114/s114]>>endobj
8 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
9 0 obj<</Length 5>>stream
0 d0
endstream endobj
10 0 obj<</Type/Font/Subtype/Type3/FontBBox[0 0 100 100]/FontMatrix[0.01 0 0 0.01 0 0]/CharProcs 6 0 R/Encoding 7 0 R/ToUnicode 9 0 R/FirstChar 112/LastChar 114/Widths[10 0 10]>>endobj
trailer<</Root 1 0 R/Size 11>>
%%EOF"""


def test_is_mathtext_type3_fingerprint():
    import fitz

    from pdf_chart2table.font_recovery import FontDecoder

    doc = fitz.open(stream=_T3_MATHTEXT_PDF, filetype="pdf")
    dec = FontDecoder(doc)
    # matplotlib mathtext Type3 (no BaseFont/ToUnicode, s<code> Differences)
    assert dec.is_mathtext_type3("Type3 (5 0 R)") is True
    # an ordinary font is never flagged
    assert dec.is_mathtext_type3("Helvetica") is False
    assert dec.is_mathtext_type3("") is False
    # a Type3 font WITH a ToUnicode map is trusted, not overridden
    assert dec.is_mathtext_type3("Type3 (10 0 R)") is False
