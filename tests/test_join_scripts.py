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
