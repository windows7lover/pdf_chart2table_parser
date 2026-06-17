"""Tests for primitives.join_scripts: sub/superscript -> inline mathtext.

A span SMALLER than the base size and raised/lowered from the baseline becomes a
super/subscript; consecutive same-script spans merge; non-script labels are
returned UNCHANGED (no '$', so existing behaviour is preserved)."""

from __future__ import annotations

from pdf_chart2table.primitives import join_scripts


def _it(text, size, cy, x0, x1):
    return (text, size, cy, x0, x1)


def test_superscript_exponent():
    # '10'(size 8, cy 68) + '5'(size 6, cy 64, raised) -> '10$^{5}$'.
    items = [_it("10", 8.0, 68.0, 0, 10), _it("5", 6.0, 64.0, 10, 14)]
    assert join_scripts(items) == "10$^{5}$"


def test_subscript():
    # 'P'(base) + 'in'(smaller, lowered) -> 'P$_{in}$'.
    items = [_it("P", 10.0, 50.0, 0, 8), _it("in", 7.0, 53.0, 8, 18)]
    assert join_scripts(items) == "P$_{in}$"


def test_near_full_size_subscript_with_large_offset():
    # 2001.01038 legend 'V_D = 1V': the 'D' subscript is rendered at ~0.90x base
    # (8.15 vs 9.04) -- above the 0.82x cutoff -- but is clearly lowered. Its big
    # vertical offset must still mark it as a subscript.
    items = [("V", 9.04, 657.8, 0, 6, False, True),
             ("D", 8.15, 663.3, 6, 11, False, False),
             ("= 1V", 8.20, 658.0, 14, 32, False, False)]
    assert join_scripts(items) == r"$\mathbf{V}$$_{D}$ = 1V"


def test_stacked_sub_and_superscript():
    # 'I' with subscript 'D' AND superscript '1/2' (I_D^{1/2}); both recovered.
    items = [("I", 8.99, 675.4, 0, 5, False, True),
             ("D", 8.04, 680.1, 5, 10, False, False),
             ("1/2", 4.47, 671.7, 6, 10, False, False)]
    assert join_scripts(items) == r"$\mathbf{I}$$_{D}$$^{1/2}$"


def test_near_full_size_inline_token_not_a_script():
    # A token only slightly smaller (0.91x) but ON the baseline must NOT become a
    # script (guards against descender-shifted full-size tokens).
    items = [("V", 9.0, 658.0, 0, 6), ("= 1V", 8.2, 658.0, 7, 25)]
    assert "$" not in join_scripts(items)


def test_chemical_formula_merges_runs():
    # 'SnO' + lowered '2' -> 'SnO$_{2}$'.
    items = [_it("SnO", 10.0, 50.0, 0, 20), _it("2", 7.0, 53.0, 20, 25)]
    assert join_scripts(items) == "SnO$_{2}$"


def test_plain_label_unchanged():
    # Same-size, same-baseline run -> plain concatenation, NO mathtext.
    items = [_it("100", 8.0, 50.0, 0, 12)]
    assert join_scripts(items) == "100"
    items2 = [_it("Energy", 8.0, 50.0, 0, 30), _it("(eV)", 8.0, 50.0, 40, 60)]
    assert "$" not in join_scripts(items2)


def test_word_gap_inserts_space():
    items = [_it("Input", 8.0, 50.0, 0, 20), _it("power", 8.0, 50.0, 30, 55)]
    assert join_scripts(items) == "Input power"


def test_italic_base_wrapped_in_mathtext():
    # 2003.11050: an italic variable 'M' (6th item field) + lowered 's' -> the M
    # is slanted: '$M$$_{s}$'. The subscript path is unchanged.
    items = [("M", 10.0, 50.0, 0, 8, True), ("s", 7.0, 53.0, 8, 12, False)]
    assert join_scripts(items) == "$M$$_{s}$"


def test_italic_only_on_safe_variable_tokens():
    # An italic run that is NOT a simple variable token (has punctuation/space)
    # stays roman -- never risk a mathtext parse error.
    items = [("v (m/s)", 10.0, 50.0, 0, 40, True)]
    assert join_scripts(items) == "v (m/s)"
    # A roman word after an italic variable: only the italic token is wrapped.
    items2 = [("M", 10.0, 50.0, 0, 8, True), ("ratio", 10.0, 50.0, 30, 55, False)]
    assert join_scripts(items2) == "$M$ ratio"


def test_five_tuple_items_still_supported():
    # Back-compat: items without the italic field behave exactly as before.
    assert join_scripts([_it("P", 10.0, 50.0, 0, 8), _it("in", 7.0, 53.0, 8, 18)]) == "P$_{in}$"


def test_bold_and_bold_italic_base_runs():
    # 2003.11050: a bold-italic variable 'M' -> '$\\boldsymbol{M}$'; bold-only ->
    # '$\\mathbf{M}$'. (text, size, cy, x0, x1, italic, bold)
    bi = [("M", 10.0, 50.0, 0, 8, True, True), ("s", 7.0, 53.0, 8, 12, False, False)]
    assert join_scripts(bi) == r"$\boldsymbol{M}$$_{s}$"
    bold_only = [("R", 10.0, 50.0, 0, 8, False, True), ("e", 10.0, 50.0, 8, 14, False, True)]
    assert join_scripts(bold_only) == r"$\mathbf{Re}$"
