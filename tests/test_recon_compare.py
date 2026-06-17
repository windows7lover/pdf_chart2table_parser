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
