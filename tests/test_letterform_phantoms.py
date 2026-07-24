"""Regression tests for phantom-aware glyph-text run detection (marks.py).

Vector-outline text (TeX outlines, no font entries) splits per glyph: round
letterforms pass _is_data_mark and became a black-'o' "scatter series"
(2512.13518: legend "5K/20K/No EPI" emitted as 15 black dots; ~20/28 of the
downstream black_dot_chart sample were this class). The angular letters are
rejected earlier, so the old run detector never saw them: their gaps broke
the runs below the word length. Now small unclaimed same-colour ink joins the
run-building as PHANTOM members, short runs with interleaved phantoms count
as words (size-CV over candidates only), word spans are capped (dashed-line
protection), and the block extension CHAINS across stacked legend lines.
"""
from __future__ import annotations

from pdf_chart2table.marks import _text_run_indices


def _c(idx, cx, cy, w, h):
    return (idx, cx, cy, w, h)


def _ph(cx, cy, w, h):
    return (None, cx, cy, w, h)


# Geometry lifted from 2512.13518_p81c4 ("No EPI" row + "20K"/"5K" above).
ROW_NO_EPI = [
    _c(0, 208.1, 418.4, 7.1, 7.1),   # N
    _c(1, 214.3, 419.1, 4.6, 4.8),   # o
    _c(2, 224.1, 418.0, 6.5, 7.1),   # E
    _c(3, 236.0, 418.1, 3.2, 7.1),   # I
]
PH_P = _ph(231.2, 416.9, 6.1, 7.1)   # 'P' (rejected as a mark; phantom)
ROW_20K = [
    _c(4, 206.5, 404.6, 4.2, 6.9),   # 2
    _c(5, 211.9, 405.5, 4.4, 7.2),   # 0
    _c(6, 218.8, 405.7, 7.3, 7.1),   # K
]
ROW_5K = [
    _c(7, 206.4, 393.2, 4.2, 7.2),   # 5
    _c(8, 213.7, 393.0, 7.3, 7.1),   # K
]


def test_phantom_completes_word_run():
    # Without the phantom the 'No EPI' row fragments below the word length;
    # with it the run reaches 5 members and is flagged.
    assert _text_run_indices(ROW_NO_EPI, []) == set()
    flagged = _text_run_indices(ROW_NO_EPI, [PH_P])
    assert flagged == {0, 1, 2, 3}


def test_block_extension_chains_across_stacked_legend_lines():
    # '20K' (3 glyphs) and '5K' (2 glyphs) can never be words alone; they are
    # reached from the confirmed 'No EPI' word row via the CHAINED block
    # extension ('5K' is two line pitches away, through '20K').
    cands = ROW_NO_EPI + ROW_20K + ROW_5K
    flagged = _text_run_indices(cands, [PH_P])
    assert flagged == {0, 1, 2, 3, 4, 5, 6, 7, 8}


def test_uniform_marker_row_on_dashed_line_not_flagged():
    # A genuine series: uniform-size markers at one y, sitting on a dashed
    # line whose dash segments are phantom-sized but span the whole plot.
    # Candidate-only CV is ~0 and the run span exceeds the word cap -> kept.
    marks = [_c(i, 40.0 + 28.0 * i, 100.0, 4.0, 4.0) for i in range(6)]
    dashes = [_ph(30.0 + 7.0 * k, 100.0, 3.5, 0.6) for k in range(28)]
    assert _text_run_indices(marks, dashes) == set()


def test_scatter_far_from_text_rows_untouched():
    # Marks on other rows, x-disjoint from any word box, are never flagged
    # (the 'epsilon' annotation cluster at x~143-174 stays out of the legend
    # block at x~204-240).
    scatter = [_c(20, 143.7, 427.0, 4.0, 4.0), _c(21, 169.0, 427.0, 4.0, 4.0)]
    flagged = _text_run_indices(ROW_NO_EPI + scatter, [PH_P])
    assert flagged == {0, 1, 2, 3}
