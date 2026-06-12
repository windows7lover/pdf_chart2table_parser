"""Regression test for the residual audit's plot-box gate.

Bug (2309.15777_p18c4 / 2308.10009_p16c8): a thin subplot's region_bbox
OVERLAPS curves that actually belong to a neighbouring subplot (their ink sits
mostly above/below this plot box). The audit's _in_region overlap test pulled
those curves into the candidate list and reported them as "missed data curves"
(3 phantom dark-red parabolas), inflating the unexplained residual even though
the extractor correctly clips them away. The plot-box gate (_frac_in_box >= 0.5)
fixes this: a candidate missed curve must lie mostly inside the calibrated data
box, not merely overlap the loose region_bbox.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from residual_audit import _frac_in_box  # noqa: E402


# Calibrated plot box (spine-to-spine): a thin horizontal subplot strip.
PLOT_BOX = (90.0, 415.0, 309.0, 466.0)


def test_neighbour_subplot_curve_mostly_outside_box():
    # A parabola from the subplot BELOW: spans the same x range but its y values
    # sit far below this plot box (y ~ 470..540). Almost no vertex is inside.
    pts = [(90.0 + i, 470.0 + 0.5 * i) for i in range(80)]
    assert _frac_in_box(pts, PLOT_BOX) < 0.5


def test_genuine_in_box_curve_kept():
    # A curve that actually lives inside the plot box stays a candidate.
    pts = [(100.0 + i, 420.0 + 0.2 * i) for i in range(80)]
    assert _frac_in_box(pts, PLOT_BOX) >= 0.5


def test_no_box_falls_back_to_keep():
    # Without a calibrated box we cannot gate; default to keeping the candidate.
    pts = [(100.0, 9999.0), (200.0, 9999.0)]
    assert _frac_in_box(pts, None) == 1.0
