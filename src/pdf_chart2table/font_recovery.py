"""Recover text mangled by broken LaTeX/private math-font ToUnicode maps.

LaTeX charts often embed Computer Modern / Latin Modern math fonts (cmmi, cmsy,
cmex, ...) whose ``/ToUnicode`` map is missing or wrong, so PyMuPDF returns
garbled or non-printable codepoints (C0 control chars, replacement char, or
private-use-area glyphs) for a span. The glyph *names*, however, are intact in
the embedded font program's encoding (the CFF built-in Encoding / Type1
``/Differences``). This module maps each character code of a *broken* span back
to a glyph name and then to Unicode, leaving every other span untouched.

Used ONLY as a fallback: ``is_broken_text`` gates engagement so normal spans
(working ToUnicode, ordinary fonts) are returned byte-for-byte unchanged. The
recovered string flows into ``TextSpan.text`` and thus into legend label /
title annotation text. It never touches numeric data, coordinates, or axes.

Tier 1 only (deterministic glyph-name -> Unicode). No rasterisation / shape
matching is performed.
"""

from __future__ import annotations

import io
import re

import fitz
from fontTools.agl import toUnicode as _agl_to_unicode

# Math / private TeX font families whose ToUnicode maps are frequently broken.
# Matched case-insensitively against the PostScript base font name (the
# 6-letter "ABCDEF+" subset tag is ignored).
_MATH_FONT_RE = re.compile(
    r"(cmmi|cmsy|cmex|cmmib|cmbsy|lmmi|lmsy|lasy|msam|msbm|rsfs|eufm|eufb"
    r"|stix|newcmmath|xits|texgyre.*math)",
    re.IGNORECASE,
)

# Standard TeX glyph names that the Adobe Glyph List does not resolve. Only
# names actually observed on the target corpus (cmmi/cmsy/cmex/NewCMMath) are
# listed; an unknown name is left unresolved rather than guessed.
_TEX_GLYPH_UNICODE: dict[str, str] = {
    # cmsy / cmex relational & binary operators.
    "minus": "−",
    "periodcentered": "·",
    "multiply": "×",
    "lessequal": "≤",
    "greaterequal": "≥",
    "similar": "∼",
    "approxequal": "≈",
    "arrowright": "→",
    "arrowleft": "←",
    "element": "∈",
    "negationslash": "̸",
    "plusminus": "±",
    "bullet": "•",
    # cmex large delimiters / operators (best-effort plain-text fallbacks).
    "summationdisplay": "∑",
    "summationtext": "∑",
    "integraldisplay": "∫",
    "integraltext": "∫",
    "producttext": "∏",
    "productdisplay": "∏",
    "parenleftbig": "(",
    "parenrightbig": ")",
    "parenleftBig": "(",
    "parenrightBig": ")",
    "parenleftbigg": "(",
    "parenrightbigg": ")",
    "parenleftBigg": "(",
    "parenrightBigg": ")",
    "bracketleftbig": "[",
    "bracketrightbig": "]",
    "bracketleftBig": "[",
    "bracketrightBig": "]",
    "bracketleftbigg": "[",
    "bracketrightbigg": "]",
    "bracketleftBigg": "[",
    "bracketrightBigg": "]",
    "braceleftbig": "{",
    "bracerightbig": "}",
    "braceleftBig": "{",
    "bracerightBig": "}",
    "braceleftbigg": "{",
    "bracerightbigg": "}",
    "braceleftBigg": "{",
    "bracerightBigg": "}",
    "angbracketleftbig": "⟨",
    "angbracketrightbig": "⟩",
    "angbracketleftBig": "⟨",
    "angbracketrightBig": "⟩",
    "vextendsingle": "|",
    # cmmi greek (most resolve via AGL, these are belt-and-braces).
    "alpha": "α",
    "beta": "β",
    "gamma": "γ",
    "delta": "δ",
    "epsilon": "ε",
    "varepsilon": "ε",
    "zeta": "ζ",
    "eta": "η",
    "theta": "θ",
    "vartheta": "ϑ",
    "iota": "ι",
    "kappa": "κ",
    "lambda": "λ",
    "mu": "μ",
    "nu": "ν",
    "xi": "ξ",
    "pi": "π",
    "varpi": "ϖ",
    "rho": "ρ",
    "varrho": "ϱ",
    "sigma": "σ",
    "varsigma": "ς",
    "tau": "τ",
    "upsilon": "υ",
    "phi": "φ",
    "varphi": "ϕ",
    "chi": "χ",
    "psi": "ψ",
    "omega": "ω",
}


# Symbol fonts (Adobe Symbol / SymbolMT) embed only a private-use cmap, so the
# glyph *identity* survives nowhere in the subset -- it is only knowable through
# the fixed standard Adobe Symbol encoding (byte -> glyph name). Recovering it is
# therefore deterministic, not a guess. Only the well-defined Greek letters and
# the common math operators are listed; an unlisted byte stays unresolved.
_SYMBOL_FONT_RE = re.compile(r"symbol", re.IGNORECASE)

# Adobe Symbol encoding: byte code -> glyph name (AGL/TeX-table resolves to char).
_ADOBE_SYMBOL: dict[int, str] = {
    # Uppercase Greek (ASCII letter positions).
    0x41: "Alpha", 0x42: "Beta", 0x43: "Chi", 0x44: "Delta", 0x45: "Epsilon",
    0x46: "Phi", 0x47: "Gamma", 0x48: "Eta", 0x49: "Iota", 0x4A: "theta1",
    0x4B: "Kappa", 0x4C: "Lambda", 0x4D: "Mu", 0x4E: "Nu", 0x4F: "Omicron",
    0x50: "Pi", 0x51: "Theta", 0x52: "Rho", 0x53: "Sigma", 0x54: "Tau",
    0x55: "Upsilon", 0x57: "Omega", 0x58: "Xi", 0x59: "Psi", 0x5A: "Zeta",
    # Lowercase Greek.
    0x61: "alpha", 0x62: "beta", 0x63: "chi", 0x64: "delta", 0x65: "epsilon",
    0x66: "phi", 0x67: "gamma", 0x68: "eta", 0x69: "iota", 0x6A: "phi1",
    0x6B: "kappa", 0x6C: "lambda", 0x6D: "mu", 0x6E: "nu", 0x6F: "omicron",
    0x70: "pi", 0x71: "theta", 0x72: "rho", 0x73: "sigma", 0x74: "tau",
    0x75: "upsilon", 0x76: "omega1", 0x77: "omega", 0x78: "xi", 0x79: "psi",
    0x7A: "zeta",
    # Common operators / relations.
    0xA3: "lessequal", 0xB3: "greaterequal", 0xB1: "plusminus",
    0xB4: "multiply", 0xB8: "divide", 0xA5: "infinity", 0xB6: "partialdiff",
    0xD1: "gradient", 0xB5: "proportional", 0x40: "congruent",
    0xBB: "arrowright", 0xAC: "arrowleft", 0xCE: "element",
    0xB7: "bullet", 0x22: "forall", 0x24: "existential",
}
# Map TeX-style variant glyph names the Symbol table uses onto Unicode.
_TEX_GLYPH_UNICODE.update(
    {
        "Alpha": "Α", "Beta": "Β", "Chi": "Χ", "Delta": "Δ", "Epsilon": "Ε",
        "Phi": "Φ", "Gamma": "Γ", "Eta": "Η", "Iota": "Ι", "theta1": "ϑ",
        "Kappa": "Κ", "Lambda": "Λ", "Mu": "Μ", "Nu": "Ν", "Omicron": "Ο",
        "Pi": "Π", "Theta": "Θ", "Rho": "Ρ", "Sigma": "Σ", "Tau": "Τ",
        "Upsilon": "Υ", "Omega": "Ω", "Xi": "Ξ", "Psi": "Ψ", "Zeta": "Ζ",
        "phi1": "φ", "omega1": "ϖ", "omicron": "ο",
        "infinity": "∞", "partialdiff": "∂", "gradient": "∇",
        "proportional": "∝", "congruent": "≅", "divide": "÷",
        "forall": "∀", "existential": "∃",
    }
)


def is_broken_text(text: str, font: str) -> bool:
    """True when ``text`` carries an unrenderable codepoint (broken ToUnicode).

    A span engages recovery only when the extracted text contains a codepoint
    that cannot be a real label character: a C0 control char, the Unicode
    replacement char, or a private-use-area glyph. This is the precision gate --
    ordinary text and cleanly-decoded math fonts (e.g. a cmmi span that already
    reads ``"βE"``) never contain such codepoints, so they are left untouched.
    The ``font`` argument is unused here (the codepoint test alone is the gate);
    it is accepted for call-site symmetry / future tightening.
    """
    for ch in text:
        o = ord(ch)
        if o == 0xFFFD:  # replacement char
            return True
        if 0xE000 <= o <= 0xF8FF:  # private use area
            return True
        if o < 0x20 and ch not in "\t\n\r":  # C0 control
            return True
    return False


# dvisvgm / TeX Type3 glyph-name convention: "s<decimal-codepoint>" (e.g.
# ``s8722`` -> U+2212 minus, ``s8776`` -> U+2248 approx). These names carry the
# Unicode value directly; decoding them is exact, not a guess.
_S_DECIMAL_RE = re.compile(r"^s(\d{2,6})$")


def _glyph_to_unicode(name: str) -> str | None:
    """Map a glyph name to a Unicode string, or None if unresolvable."""
    if not name or name == ".notdef":
        return None
    # TeX supplement first (covers names AGL gets wrong or misses, e.g. "minus").
    u = _TEX_GLYPH_UNICODE.get(name)
    if u is not None:
        return u
    # dvisvgm "s<decimal>" name encodes the Unicode codepoint directly.
    m = _S_DECIMAL_RE.match(name)
    if m:
        cp = int(m.group(1))
        if 0x20 <= cp <= 0x10FFFF:
            return chr(cp)
    # Adobe Glyph List (handles "alpha", "uniXXXX", "Beta", etc.).
    s = _agl_to_unicode(name)
    if s:
        return s
    return None


def _cff_code_to_name(buf: bytes) -> dict[int, str] | None:
    """char code -> glyph name from a bare CFF (Type1C) font program."""
    try:
        from fontTools.cffLib import CFFFontSet

        cff = CFFFontSet()
        cff.decompile(io.BytesIO(buf), None)
        font = cff[cff.fontNames[0]]
        enc = font.Encoding  # 256-entry list: code -> glyph name (".notdef" gaps)
        out: dict[int, str] = {}
        for code in range(len(enc)):
            gname = enc[code]
            if gname and gname != ".notdef":
                out[code] = gname
        return out or None
    except Exception:
        return None


# A Type1 font program carries its built-in /Encoding in the *cleartext* header
# (before the ``eexec`` blob) as PostScript ``dup <code> /<name> put`` lines.
_T1_DUP_RE = re.compile(rb"dup\s+(\d+)\s*/([^\s/]+)\s+put")


def _type1_code_to_name(buf: bytes) -> dict[int, str] | None:
    """char code -> glyph name from a Type1 (pfa/pfb) font program's Encoding."""
    head = buf[: buf.find(b"eexec")] if b"eexec" in buf else buf
    out: dict[int, str] = {}
    for m in _T1_DUP_RE.finditer(head):
        gname = m.group(2).decode("latin-1")
        if gname != ".notdef":
            out[int(m.group(1))] = gname
    return out or None


# A PDF font's /Encoding /Differences array overrides the program encoding:
# ``[ code /name /name code /name ... ]`` -- a code resets the running index.
_DIFF_TOKEN_RE = re.compile(r"\d+|/[^\s/\]]+")

# Matplotlib emits math symbols (\pi, \rho, ...) as embedded Type3 fonts with NO
# BaseFont and NO ToUnicode, whose /Differences names each glyph ``s<code>`` where
# the number equals the char code (e.g. ``[112/s112 114/s114]``). The code is the
# glyph's Computer-Modern slot, NOT a Unicode value, so PyMuPDF decodes it via the
# standard Latin encoding -- e.g. slot 112 (CM ``\pi``) reads as 'p', slot 114
# (CM ``\rho``) as 'r'. These spans look like plain ASCII letters, so the broken-
# text gate misses them. We recognise the font fingerprint here; the actual glyph
# is then confirmed by bitmap matching (glyph_match), which is deterministic.
_TYPE3_XREF_RE = re.compile(r"Type3 \((\d+) 0 R\)")
_S_CODE_GLYPH_RE = re.compile(r"^s\d+$")


class FontDecoder:
    """Per-page cache of char-code -> Unicode maps for broken math fonts.

    Built from the embedded font programs (CFF Encoding -> glyph name -> Unicode)
    extracted via ``doc.extract_font``. Looked up by the PostScript base font
    name (subset tag stripped), which is what ``page.get_text`` reports as the
    span ``font``.
    """

    def __init__(self, doc: fitz.Document):
        self._doc = doc
        # base font name (no subset tag) -> {code: unicode str}
        self._maps: dict[str, dict[int, str]] = {}
        self._scanned: set[int] = set()
        # font xref -> is matplotlib mathtext Type3 (cached fingerprint probe)
        self._type3_mathtext: dict[int, bool] = {}

    @staticmethod
    def _strip_tag(name: str) -> str:
        # Subset tags are exactly "ABCDEF+" (6 uppercase letters + plus).
        if len(name) > 7 and name[6] == "+" and name[:6].isalpha():
            return name[7:]
        return name

    def _ensure_page(self, page: fitz.Page) -> None:
        """Build code->unicode maps for every math font referenced on ``page``."""
        try:
            fonts = page.get_fonts(full=True)
        except Exception:
            return
        for f in fonts:
            xref = f[0]
            base = f[3] or ""
            if xref in self._scanned:
                continue
            self._scanned.add(xref)
            if _SYMBOL_FONT_RE.search(base):
                # Standard Adobe Symbol encoding -- no font program needed; the
                # broken span returns the byte directly or as a PUA alias F0xx.
                code_to_name = dict(_ADOBE_SYMBOL)
                code_to_name.update({0xF000 + c: n for c, n in _ADOBE_SYMBOL.items()})
            elif _MATH_FONT_RE.search(base):
                code_to_name = self._extract_code_to_name(xref) or {}
                # PDF /Differences overrides the font program's own encoding.
                code_to_name.update(self._differences_map(xref))
            else:
                continue
            if not code_to_name:
                continue
            code_to_uni: dict[int, str] = {}
            for code, gname in code_to_name.items():
                u = _glyph_to_unicode(gname)
                if u is not None:
                    code_to_uni[code] = u
            if code_to_uni:
                self._maps[self._strip_tag(base)] = code_to_uni

    def _extract_code_to_name(self, xref: int) -> dict[int, str] | None:
        try:
            info = self._doc.extract_font(xref)
        except Exception:
            return None
        ext, buf = info[1], info[3]
        if not buf:
            return None
        if ext == "cff":
            return _cff_code_to_name(buf)
        if ext in ("pfa", "pfb", "type1"):
            return _type1_code_to_name(buf)
        # cid/ttf/otf subsets keep no glyph identity (Identity-H, stripped cmap,
        # or PUA-only) -> not decodable deterministically; left to OCR fallback.
        return None

    def _differences_map(self, xref: int) -> dict[int, str]:
        """Parse the PDF font's /Encoding /Differences -> {code: glyph name}."""
        try:
            enc = self._doc.xref_get_key(xref, "Encoding")
        except Exception:
            return {}
        if not enc or enc[0] != "xref":
            return {}
        try:
            enc_xref = int(enc[1].split()[0])
            diffs = self._doc.xref_get_key(enc_xref, "Differences")
        except Exception:
            return {}
        if not diffs or diffs[0] != "array":
            return {}
        out: dict[int, str] = {}
        cur = 0
        for tok in _DIFF_TOKEN_RE.findall(diffs[1]):
            if tok.startswith("/"):
                if tok != "/.notdef":
                    out[cur] = tok[1:]
                cur += 1
            else:
                cur = int(tok)
        return out

    def is_mathtext_type3(self, font: str) -> bool:
        """True when ``font`` is a matplotlib mathtext Type3 font (no BaseFont, no
        ToUnicode, ``s<code>`` /Differences names) whose Latin-decoded text may be
        a mis-read Computer-Modern math glyph (e.g. 'p'->π, 'r'->ρ).

        ``font`` is the name PyMuPDF reports, e.g. ``"Type3 (276 0 R)"``. Result is
        cached per xref. A regular (BaseFont-bearing, ToUnicode-mapped) font never
        matches, so this is a no-op for ordinary documents.
        """
        m = _TYPE3_XREF_RE.search(font or "")
        if not m:
            return False
        xref = int(m.group(1))
        cached = self._type3_mathtext.get(xref)
        if cached is not None:
            return cached
        result = self._probe_mathtext_type3(xref)
        self._type3_mathtext[xref] = result
        return result

    def _probe_mathtext_type3(self, xref: int) -> bool:
        try:
            if self._doc.xref_get_key(xref, "Subtype")[1] != "/Type3":
                return False
            if self._doc.xref_get_key(xref, "ToUnicode")[0] != "null":
                return False  # a working ToUnicode map -> trust the decoded text
        except Exception:
            return False
        diffs = self._differences_map(xref)
        if not diffs:
            return False
        # All glyph names follow the matplotlib ``s<code>`` convention.
        return all(_S_CODE_GLYPH_RE.match(name) for name in diffs.values())

    def recover(self, page: fitz.Page, codes: list[int], font: str) -> str | None:
        """Recover a span's text from its raw char ``codes`` using ``font``'s map.

        Returns the reconstructed string (glyph order preserved), or None when no
        map is available for the font (caller then keeps the original text).
        """
        self._ensure_page(page)
        cmap = self._maps.get(self._strip_tag(font or ""))
        if not cmap:
            return None
        out: list[str] = []
        for c in codes:
            u = cmap.get(c)
            if u is not None:
                out.append(u)
            elif c == 0x20:
                out.append(" ")
            # Unknown code with no glyph mapping: drop it (a broken control byte
            # we cannot resolve) rather than emit garbage.
        recovered = "".join(out)
        return recovered if recovered else None
