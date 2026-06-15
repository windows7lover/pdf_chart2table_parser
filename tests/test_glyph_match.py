"""Deterministic shape matcher for font-less math glyph-PATHS (glyph_match.py).

These math symbols (ℓ, α, …) render in LaTeX/Computer-Modern PDFs as filled
vector outlines with NO char code, so text extraction and OCR both miss them.
We match each glyph's bitmap against templates rendered from matplotlib's CM
mathtext (same font family) by IoU -- it must be a confident, clear winner.
"""
from __future__ import annotations

import pytest

gm = pytest.importorskip("pdf_chart2table.glyph_match")


def test_templates_round_trip():
    # Each candidate, rendered then recognised, returns ITSELF at high overlap.
    for sym in [r"\alpha", r"\ell", r"\beta", r"\lambda", r"\pi", "x", "T", "5"]:
        t = gm._render_template(sym)
        hit = gm.recognize_bitmap(t)
        assert hit is not None, f"{sym} not recognised"
        assert hit[0] == sym, f"{sym} matched as {hit[0]}"
        assert hit[1] >= gm._MIN_IOU and hit[2] >= gm._MIN_MARGIN


def test_no_cross_confusion_alpha_vs_ell():
    # The two glyphs we recover most often must not be confused for each other.
    a = gm.recognize_bitmap(gm._render_template(r"\alpha"))
    l = gm.recognize_bitmap(gm._render_template(r"\ell"))
    assert a[0] == r"\alpha" and l[0] == r"\ell"


def test_blank_bitmap_is_none():
    import numpy as np
    assert gm.recognize_bitmap(None) is None
    # an all-ink (degenerate) block is not a confident single-glyph match
    solid = np.ones((gm._N, gm._N), dtype=bool)
    hit = gm.recognize_bitmap(solid)
    # either rejected, or if it matches it must still clear the margin gate
    assert hit is None or hit[2] >= gm._MIN_MARGIN
