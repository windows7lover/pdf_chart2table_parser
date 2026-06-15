"""Regression test for legend swatch-SIDE detection.

Bug (2006.03604_p4c1): a 2-column legend drawn as ``Np [—] Tc [—]`` / ``Ac [—]
Pc [—]`` puts the colour sample to the RIGHT of each label. The detector assumed
the swatch sits to the LEFT, so it dropped the left-column labels (Np, Ac) AND
mis-coloured the right-column ones (it grabbed the neighbouring column's swatch:
Tc->green instead of blue). The detector now picks the side that pairs MORE labels
(defaulting to left on a tie), recovering all four entries with the right colours.
"""
from __future__ import annotations

from pdf_chart2table.labels import _detect_legend
from pdf_chart2table.model import Path, Region, TextSpan

REGION = Region(bbox=(180.0, 60.0, 260.0, 100.0), path_indices=[], text_indices=[])
GREEN, BLUE, RED, BLACK = ((0.0, 0.67, 0.0), (0.0, 0.5, 1.0),
                           (1.0, 0.0, 0.0), (0.0, 0.0, 0.0))


def _lab(s, x0, cy):
    return TextSpan(text=s, bbox=(x0, cy - 3, x0 + 7, cy + 3), size=6.0,
                    dir=(1.0, 0.0))


def _line(x0, x1, cy, color):
    return Path(points=[(x0, cy), (x1, cy)], stroke=color, fill=None, width=0.8,
                dashes=None, closed=False, bbox=(x0, cy - 0.4, x1, cy + 0.4))


def test_swatch_on_right_two_column_legend():
    # Np [green]  Tc [blue]   /   Ac [red]  Pc [black]  (swatch to the RIGHT)
    texts = [_lab("Np", 193, 74), _lab("Tc", 227, 74),
             _lab("Ac", 193, 83), _lab("Pc", 227, 83)]
    paths = [_line(207, 223, 74, GREEN), _line(237, 253, 74, BLUE),
             _line(207, 223, 83, RED), _line(237, 253, 83, BLACK)]
    entries, _ = _detect_legend(REGION, paths, texts)
    got = {label: tuple(round(c, 2) for c in color) for _, color, label in entries}
    assert got == {"Np": GREEN, "Tc": BLUE, "Ac": RED, "Pc": BLACK}


def test_swatch_abutting_or_overlapping_label_still_pairs():
    # 2002.02623_p23c1: the colored swatch line ends right at (or a hair past) the
    # label's space-padded left edge -> gap ~ -0.001..-1. The strict 0<=gap test
    # missed it (legend lost, series unlabeled). A small overlap tolerance pairs it.
    texts = [_lab("298K", 252, 79), _lab("77K", 252, 90)]
    paths = [_line(230, 253, 79, RED), _line(230, 253, 90, BLUE)]  # end 1pt PAST label
    entries, _ = _detect_legend(REGION, paths, texts)
    got = {label: tuple(round(c, 2) for c in color) for _, color, label in entries}
    assert got == {"298K": RED, "77K": BLUE}


def test_tight_rows_pair_with_closest_swatch():
    # 2502.18732_p6c3: 4 legend rows stacked ~5.6pt apart (< _ROW_TOL=6), so each
    # label's tolerance band overlaps several swatch rows. Taking the first swatch
    # grabbed the row above (H=30->black, H=60->red, ...). Closest-cy pairing fixes
    # it: each label gets its OWN-row colour.
    cys = [70.0, 75.6, 81.2, 86.8]
    cols = [BLACK, RED, GREEN, BLUE]
    names = ["H10", "H30", "H60", "H70"]
    texts = [_lab(n, 240, cy) for n, cy in zip(names, cys)]
    paths = [_line(222, 238, cy, c) for c, cy in zip(cols, cys)]
    entries, _ = _detect_legend(REGION, paths, texts)
    got = {label: tuple(round(c, 2) for c in color) for _, color, label in entries}
    assert got == {"H10": BLACK, "H30": RED, "H60": GREEN, "H70": BLUE}


def test_swatch_on_left_still_default():
    # [green] Tc   [blue] Pc  (the common case: swatch to the LEFT) -> unchanged.
    texts = [_lab("Tc", 227, 74), _lab("Pc", 227, 83)]
    paths = [_line(207, 223, 74, GREEN), _line(207, 223, 83, BLUE)]
    entries, _ = _detect_legend(REGION, paths, texts)
    got = {label: tuple(round(c, 2) for c in color) for _, color, label in entries}
    assert got == {"Tc": GREEN, "Pc": BLUE}
