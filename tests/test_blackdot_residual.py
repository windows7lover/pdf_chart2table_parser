"""Regression tests for the fix-2 residual black-dot mechanisms (marks.py).

Three residual classes of the downstream black_dot_chart rejects, each with a
general mechanism (geometry lifted from the concrete leaked charts):

1. Math annotation rows fragment: subscripts sit off the baseline (row rule
   was centroid-only) and the spacing around a flat '=' operator glyph exceeds
   the letter-tight gap (2512.13518_p83c4: "eps_2 = eps_1" survived as 5 black
   dots). Fix: bbox-overlap row membership + operator-gap bridging + operator
   glyphs counting as letter evidence like phantoms.
2. Vector-outline label blocks with NO anchor word (2003.03611: three stacked
   "rows of 3" glyph labels). Fix: stacked wordlet blocks -- short tight
   size-varying runs, left-aligned at text line pitch.
3. Glyph heads of partially extracted labels ("γ dominant" -> span "dominant";
   2405.10477_p20c5: 8 black dots left of two "dominant" spans). Fix:
   activated span head bands in _text_glyph_indices.

Plus the moderate-evidence ``suspect`` flag (policy: flag, don't guess).
"""
from __future__ import annotations

from pdf_chart2table.marks import (
    Mark,
    _head_band_indices,
    _suspect_small_group,
    _text_run_indices,
)
from pdf_chart2table.model import TextSpan


def _c(idx, cx, cy, w, h):
    return (idx, cx, cy, w, h)


def _span(text, bbox):
    return TextSpan(text=text, bbox=bbox)


# ---------------------------------------------------------------------------
# 1. math annotation rows ("eps_2 = eps_1", 2512.13518_p83c4 / p103c3)
# ---------------------------------------------------------------------------

# "eps_2 = eps_1": eps, subscript 2, '=', eps, subscript 1. The subscripts'
# centroids sit ~1 pt below the eps baseline; the gaps around '=' are 4.55 /
# 4.0 pt (letter-tight cap is 0.9 x medw = 3.87).
ROW_EPS = [
    _c(0, 344.4, 463.7, 4.3, 5.0),
    _c(1, 348.8, 464.6, 3.0, 5.1),
    _c(2, 358.4, 463.5, 7.1, 2.5),   # '=' (flat-wide operator glyph)
    _c(3, 368.1, 463.7, 4.3, 5.0),
    _c(4, 372.5, 464.7, 2.5, 5.1),
]

# "T = 2K" (2512.13518_p103c3 i311-314): only FOUR glyphs, tight, with an
# in-run '=': the operator counts as letter evidence (like a phantom), so the
# phantom-length bar applies.
ROW_T_2K = [
    _c(10, 184.9, 389.9, 7.3, 7.7),
    _c(11, 193.4, 392.1, 7.5, 2.6),  # '='
    _c(12, 200.4, 391.5, 4.5, 7.7),
    _c(13, 208.5, 391.3, 7.9, 7.7),
]


def test_math_operator_gap_bridged():
    assert _text_run_indices(ROW_EPS, []) == {0, 1, 2, 3, 4}


def test_operator_counts_as_letter_evidence():
    assert _text_run_indices(ROW_T_2K, []) == {10, 11, 12, 13}


def test_uniform_marker_row_with_flat_glyph_not_flagged():
    # A genuine row of uniform markers glued through a same-colour flat glyph
    # ('-' marker / dash fragment): the CV is over the PLAIN candidates only,
    # so the uniform row is never flagged even though the run bridges the gap.
    marks = [_c(i, 40.0 + 6.0 * i, 100.0, 4.0, 4.0) for i in range(4)]
    flat = [_c(9, 64.0, 100.0, 6.0, 1.0)]
    assert _text_run_indices(marks + flat, []) == set()


# ---------------------------------------------------------------------------
# 2. stacked wordlet blocks (2003.03611_p8c10: three rows of 3 glyph labels)
# ---------------------------------------------------------------------------

BLOCK_3X3 = [
    _c(0, 216.5, 369.5, 4.8, 6.6), _c(1, 220.5, 369.7, 2.2, 4.5),
    _c(2, 223.9, 369.7, 2.2, 4.5),
    _c(3, 216.5, 381.5, 4.8, 6.6), _c(4, 220.5, 381.6, 2.7, 4.5),
    _c(5, 223.9, 381.6, 2.7, 4.5),
    _c(6, 216.5, 393.5, 4.8, 6.6), _c(7, 220.4, 394.1, 2.8, 4.6),
    _c(8, 223.9, 394.1, 2.8, 4.6),
]


def test_stacked_wordlet_block_flagged():
    # No row reaches any word length on its own (3 glyphs each, no phantoms),
    # but three tight size-varying runs stacked at line pitch, left-aligned,
    # are a vector-outline label block.
    assert _text_run_indices(BLOCK_3X3, []) == set(range(9))


def test_uniform_marker_grid_not_stacked():
    # Same 3x3 layout but UNIFORM glyph sizes (a real dense marker lattice):
    # no size CV, no wordlets, nothing flagged.
    grid = [_c(3 * r + k, 216.0 + 4.0 * k, 370.0 + 12.0 * r, 3.0, 3.0)
            for r in range(3) for k in range(3)]
    assert _text_run_indices(grid, []) == set()


def test_unaligned_wordlets_not_stacked():
    # Two size-varying tight pairs at line pitch but with NO left alignment
    # (x-staggered data clusters): not a text block.
    a = [_c(0, 100.0, 50.0, 5.0, 5.0), _c(1, 104.5, 50.0, 2.5, 3.0),
         _c(2, 108.0, 50.0, 5.0, 5.0)]
    b = [_c(3, 92.0, 60.0, 5.0, 5.0), _c(4, 96.5, 60.0, 2.5, 3.0)]
    assert _text_run_indices(a + b, []) == set()


def test_wordlet_on_word_row_anchors_block_extension():
    # 2512.13518_p98c2: the legend's lower line shares its y-band with the
    # wide "eps_2 = eps_1" annotation, so both land on ONE detected row. The
    # word flags the whole row, and the legend RUN on it (a wordlet) must
    # anchor the block extension so the "2K" row above is flagged too.
    eps = [
        _c(0, 378.2, 281.8, 4.7, 5.6), _c(1, 383.1, 282.8, 3.3, 5.5),
        _c(2, 393.8, 281.5, 7.9, 2.8),   # '='
        _c(3, 414.0, 281.8, 4.7, 5.6), _c(4, 418.9, 282.9, 2.8, 5.5),
    ]
    ph_eq = (None, 406.5, 281.5, 7.3, 0.5)   # second '=' (unclaimed ink)
    legend_low = [
        _c(5, 457.4, 283.3, 4.7, 7.9), _c(6, 463.6, 284.4, 5.0, 8.2),
        _c(7, 471.7, 284.5, 8.4, 8.1),
    ]
    legend_up = [
        _c(8, 457.2, 270.0, 4.7, 8.2), _c(9, 465.7, 269.9, 8.4, 8.1),
    ]
    flagged = _text_run_indices(eps + legend_low + legend_up, [ph_eq])
    assert flagged == set(range(10))


# ---------------------------------------------------------------------------
# 3. span head bands (2405.10477_p20c5: glyph head left of "dominant")
# ---------------------------------------------------------------------------

DOMINANT = _span("dominant", (408.3, 457.8, 443.2, 466.8))
HEAD_GLYPHS = [
    _c(0, 382.8, 462.8, 5.0, 4.3),
    _c(1, 391.9, 464.6, 14.6, 5.8),
    _c(2, 401.3, 466.4, 1.0, 1.6),
    _c(3, 403.9, 464.7, 2.7, 4.1),
]


def test_head_band_catches_partial_label_glyphs():
    assert _head_band_indices(HEAD_GLYPHS, [DOMINANT]) == {0, 1, 2, 3}


def test_head_band_not_activated_by_uniform_markers():
    # Two identical data markers grazing the band: no letterform size
    # variation, the band stays inactive.
    marks = [_c(0, 395.0, 462.0, 4.0, 4.0), _c(1, 402.0, 463.0, 4.0, 4.0)]
    assert _head_band_indices(marks, [DOMINANT]) == set()


def test_head_band_ignores_far_marks():
    # A varying pair OUTSIDE the 3-height band (or on another line) is ignored.
    far = [_c(0, 340.0, 462.0, 5.0, 4.3), _c(1, 346.0, 462.0, 2.0, 1.5)]
    below = [_c(2, 395.0, 480.0, 5.0, 4.3), _c(3, 400.0, 480.0, 2.0, 1.5)]
    assert _head_band_indices(far + below, [DOMINANT]) == set()


# ---------------------------------------------------------------------------
# suspect flag (moderate evidence: flag, don't guess)
# ---------------------------------------------------------------------------

def _m(cx, cy, size):
    return Mark(cx=cx, cy=cy, shape="circle", fill=(0, 0, 0), stroke=None,
                size=size)


def test_tight_cluster_is_suspect():
    # 2403.07251_p4c1: two 3-glyph clusters, marks packed at letter distance.
    marks = [_m(363.9, 140.1, 5.2), _m(366.7, 140.7, 3.5), _m(369.4, 140.8, 3.5),
             _m(362.1, 96.7, 5.2), _m(364.6, 98.6, 3.5), _m(367.1, 98.6, 3.5)]
    assert _suspect_small_group(marks, []) is True


def test_spread_small_series_not_suspect():
    # 2011.06321_p16c22: 4 evenly spaced dots, pitch ~5.5x the mark size --
    # genuine-looking data, never flagged.
    marks = [_m(252.1, 618.8, 2.4), _m(265.3, 619.5, 2.4),
             _m(278.5, 619.5, 2.4), _m(291.7, 616.4, 2.5)]
    assert _suspect_small_group(marks, []) is False


def test_text_adjacent_dots_are_suspect():
    # An annotation "• = 1": the dot hugs the text line band.
    span = _span("= 1", (250.0, 560.0, 270.0, 570.0))
    marks = [_m(243.0, 565.0, 3.0), _m(280.0, 565.0, 3.0)]
    assert _suspect_small_group(marks, [span]) is True


def test_large_group_never_suspect():
    marks = [_m(100.0 + 3.0 * i, 200.0, 3.0) for i in range(7)]
    assert _suspect_small_group(marks, []) is False


def test_series_record_emits_suspect():
    from pdf_chart2table.io_store import _series_record
    from pdf_chart2table.model import Series

    flagged, plain = _series_record(
        [Series(marker="o", suspect=True), Series(marker="o")])
    assert flagged["suspect"] is True
    assert plain["suspect"] is False
