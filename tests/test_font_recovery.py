"""Tests for font_recovery: recovering text mangled by broken ToUnicode maps.

These exercise the deterministic, corpus-independent pieces (glyph-name ->
Unicode, the Type1 cleartext-encoding parser, the broken-text gate, and the
Adobe Symbol table). The precision contract is the key property: the gate must
NOT fire on ordinary text, so normal spans are returned byte-for-byte unchanged.
"""

from __future__ import annotations

from pdf_chart2table.font_recovery import (
    _glyph_to_unicode,
    _type1_code_to_name,
    is_broken_text,
    _ADOBE_SYMBOL,
)


class TestIsBrokenText:
    def test_normal_text_not_broken(self):
        # Plain labels, Greek, combining diacritics, math signs are all FINE.
        for t in ("DC : [001]", "Energy (eV)", "ħω", "α + β", "[11̄0]", "10−3"):
            assert not is_broken_text(t, "AnyFont"), repr(t)

    def test_control_pua_replacement_are_broken(self):
        assert is_broken_text("\x02", "CMEX10")        # C0 control
        assert is_broken_text("", "SymbolMT")    # private use area
        assert is_broken_text("�", "Foo")         # replacement char

    def test_tabs_newlines_not_broken(self):
        assert not is_broken_text("a\tb\nc", "Foo")


class TestGlyphToUnicode:
    def test_tex_operators(self):
        assert _glyph_to_unicode("minus") == "−"
        assert _glyph_to_unicode("lessequal") == "≤"

    def test_big_delimiters(self):
        assert _glyph_to_unicode("parenleftbigg") == "("
        assert _glyph_to_unicode("bracketleftbigg") == "["
        assert _glyph_to_unicode("braceleftbigg") == "{"
        assert _glyph_to_unicode("bracerightBig") == "}"

    def test_agl_greek(self):
        assert _glyph_to_unicode("alpha") == "α"

    def test_s_decimal_name(self):
        # dvisvgm "s<decimal>" encodes the Unicode codepoint directly.
        assert _glyph_to_unicode("s8722") == "−"

    def test_unresolvable_returns_none(self):
        assert _glyph_to_unicode("glyph00001") is None
        assert _glyph_to_unicode(".notdef") is None
        assert _glyph_to_unicode("") is None


class TestType1ClearTextEncoding:
    def test_parses_dup_put_lines(self):
        buf = (
            b"%!FontType1\n/Encoding 256 array\n"
            b"dup 18 /parenleftbigg put\n"
            b"dup 20 /bracketleftbigg put\n"
            b"dup 26 /braceleftbigg put\n"
            b"readonly def\neexec\n\x00\x01\x02garbage"
        )
        enc = _type1_code_to_name(buf)
        assert enc == {18: "parenleftbigg", 20: "bracketleftbigg", 26: "braceleftbigg"}

    def test_skips_notdef_and_returns_none_when_empty(self):
        assert _type1_code_to_name(b"dup 0 /.notdef put\neexec") is None
        assert _type1_code_to_name(b"no encoding here") is None


class TestAdobeSymbol:
    def test_delta_and_greek_resolve(self):
        # SymbolMT byte 0x44 is uppercase Delta in the standard Adobe encoding.
        assert _glyph_to_unicode(_ADOBE_SYMBOL[0x44]) == "Δ"  # Delta
        assert _glyph_to_unicode(_ADOBE_SYMBOL[0x61]) == "α"  # alpha
        assert _glyph_to_unicode(_ADOBE_SYMBOL[0xA3]) == "≤"  # lessequal
