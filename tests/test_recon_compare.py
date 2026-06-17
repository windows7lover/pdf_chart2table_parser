"""Unit tests for the pure image-diff core of the recon-vs-original comparison."""

from __future__ import annotations

import numpy as np
import pytest

from pdf_chart2table.recon_compare import ink_mask, dilate, compare


def _white(h, w):
    return np.full((h, w, 3), 255, np.uint8)


def test_ink_mask_marks_non_white():
    img = _white(5, 5)
    img[2, 2] = (0, 0, 0)
    img[1, 1] = (255, 0, 0)        # red is ink
    m = ink_mask(img)
    assert m[2, 2] and m[1, 1]
    assert not m[0, 0]
    assert m.sum() == 2


def test_dilate_grows_by_radius():
    m = np.zeros((7, 7), bool)
    m[3, 3] = True
    d1 = dilate(m, 1)
    assert d1[2:5, 2:5].all()      # 3x3 block around the point
    assert d1.sum() == 9
    assert dilate(m, 0).sum() == 1


def test_compare_identical_is_perfect():
    img = _white(20, 20)
    img[5:15, 10] = (0, 0, 0)      # a vertical stroke
    res = compare(img, img.copy(), tol=0)
    assert res.missing_frac == 0.0
    assert res.extra_frac == 0.0
    assert res.ink_iou == pytest.approx(1.0)


def test_compare_missing_ink():
    orig = _white(20, 20)
    orig[5:15, 10] = (0, 0, 0)     # original has a stroke
    recon = _white(20, 20)         # reconstruction drew nothing
    res = compare(orig, recon, tol=2)
    assert res.missing_frac == pytest.approx(1.0)
    assert res.extra_frac == 0.0
    assert res.ink_iou == 0.0
    assert res.orig_ink == 10 and res.recon_ink == 0


def test_compare_extra_ink():
    orig = _white(20, 20)
    recon = _white(20, 20)
    recon[5:15, 10] = (0, 0, 0)    # phantom stroke only in reconstruction
    res = compare(orig, recon, tol=2)
    assert res.extra_frac == pytest.approx(1.0)
    assert res.missing_frac == 0.0


def test_compare_tolerance_absorbs_small_shift():
    orig = _white(20, 20)
    orig[5:15, 10] = (0, 0, 0)
    recon = _white(20, 20)
    recon[5:15, 11] = (0, 0, 0)    # same stroke shifted 1px
    strict = compare(orig, recon, tol=0)
    lenient = compare(orig, recon, tol=2)
    assert strict.missing_frac > 0.0          # unmatched when strict
    assert lenient.missing_frac == 0.0        # absorbed by tolerance
    assert lenient.extra_frac == 0.0
    assert lenient.ink_iou == pytest.approx(1.0)


def test_compare_shape_mismatch_raises():
    with pytest.raises(ValueError):
        compare(_white(10, 10), _white(10, 12))


def test_ignore_mask_excludes_region():
    # Original has a stroke in a "text" region the recon never draws. Without a
    # mask it shows as missing; masking that region removes it from scoring.
    orig = _white(20, 20)
    orig[2:5, 2:8] = (0, 0, 0)     # "text" block (not reproduced)
    orig[10:18, 10] = (0, 0, 0)    # a data stroke (reproduced)
    recon = _white(20, 20)
    recon[10:18, 10] = (0, 0, 0)   # recon draws only the data stroke
    full = compare(orig, recon, tol=0)
    assert full.missing_frac > 0.0
    ign = np.zeros((20, 20), bool)
    ign[2:5, 2:8] = True
    data = compare(orig, recon, tol=0, ignore_mask=ign)
    assert data.missing_frac == 0.0
    assert data.ink_iou == pytest.approx(1.0)


def test_choose_best_picks_closest_candidate():
    from pdf_chart2table.recon_compare import choose_best
    orig = _white(20, 20)
    orig[5:15, 10] = (0, 0, 0)          # original stroke at column 10
    good = _white(20, 20); good[5:15, 10] = (0, 0, 0)     # exact match
    shifted = _white(20, 20); shifted[5:15, 14] = (0, 0, 0)  # far-off stroke
    empty = _white(20, 20)               # drew nothing
    best, results = choose_best(orig, [shifted, good, empty], tol=0)
    assert best == 1
    assert len(results) == 3


def test_choose_best_conservative_on_tie():
    from pdf_chart2table.recon_compare import choose_best
    orig = _white(20, 20); orig[5:15, 10] = (0, 0, 0)
    a = _white(20, 20); a[5:15, 10] = (0, 0, 0)
    b = _white(20, 20); b[5:15, 10] = (0, 0, 0)   # identical to a
    best, _ = choose_best(orig, [a, b], tol=0)
    assert best == 0                     # tie keeps the earliest (default) candidate


def test_order_xsort_and_nearest():
    from pdf_chart2table.recon_compare import order_xsort, order_nearest
    # points of y=x^2-ish sampled out of order
    coords = [(2, 4), (0, 0), (3, 9), (1, 1)]
    assert [coords[i] for i in order_xsort(coords)] == [(0, 0), (1, 1), (2, 4), (3, 9)]
    # nearest-neighbour from leftmost recovers the smooth left-to-right traversal
    nn = order_nearest(coords)
    assert nn[0] == 1                      # leftmost (x=0) first
    assert [coords[i] for i in nn] == [(0, 0), (1, 1), (2, 4), (3, 9)]


def test_order_nearest_collapses_scrambled_path():
    from pdf_chart2table.recon_compare import order_nearest
    import math
    # a smooth line sampled then shuffled: NN order has far smaller path length
    pts = [(x, x) for x in range(20)]
    scrambled = [pts[i] for i in (0, 10, 1, 11, 2, 12, 3, 13, 4, 14, 5, 15,
                                  6, 16, 7, 17, 8, 18, 9, 19)]
    def plen(seq):
        return sum(math.dist(seq[k], seq[k - 1]) for k in range(1, len(seq)))
    nn = [scrambled[i] for i in order_nearest(scrambled)]
    assert plen(nn) < plen(scrambled)
    assert plen(nn) == pytest.approx(plen(pts))
