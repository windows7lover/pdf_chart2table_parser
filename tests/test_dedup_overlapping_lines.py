"""Tests for dedup_overlapping_line_series (over-segmentation collapse).

Some PDFs draw one curve as several overlapping strokes, so classify_lines
emits it as many same-colour marker-less line series covering the same box --
inflating the series COUNT (observed: one curve emitted 8x; a chart ballooning
to ~200 series). The dedup drops a line whose vertices coincide with a LONGER
same-colour line (a redundant trace), while never merging a FAMILY of distinct
same-colour curves (they diverge, so coincidence stays low) and never touching
marker series.
"""
from __future__ import annotations

import math

from pdf_chart2table.model import Series
from pdf_chart2table.refiners import dedup_overlapping_line_series


def _line(pix, color=(0, 0, 0), marker=None):
    return Series(label=None, marker=marker, color=color,
                  points=[{"x": x, "y": y, "x_px": x, "y_px": y} for x, y in pix])


def _sine(n=60, x0=0.0, x1=100.0, amp=30.0, yc=50.0, phase=0.0, jitter=0.0):
    return [(x0 + (x1 - x0) * i / (n - 1),
             yc + amp * math.sin(6.28 * i / (n - 1) + phase) + jitter)
            for i in range(n)]


def test_eight_duplicate_traces_collapse_to_one():
    # One curve drawn as 8 overlapping strokes (tiny sub-pixel jitter), same colour.
    curves = [_line(_sine(jitter=0.3 * (k - 4)), color=(0.97, 0.67, 0.56))
              for k in range(8)]
    out = dedup_overlapping_line_series(curves)
    assert len(out) == 1, f"8 duplicate traces should collapse to 1, got {len(out)}"


def test_keeps_the_longest_trace():
    long = _line(_sine(n=90), color=(0, 0, 1.0))
    short = _line(_sine(n=30), color=(0, 0, 1.0))
    out = dedup_overlapping_line_series([short, long])
    assert out == [long] and len(out[0].points) == 90


def test_distinct_same_colour_family_untouched():
    # Three distinct black curves (different phase -> they diverge): a real
    # multi-series family, must all survive.
    fam = [_line(_sine(phase=0.0)), _line(_sine(phase=2.0)), _line(_sine(phase=4.0))]
    out = dedup_overlapping_line_series(fam)
    assert len(out) == 3


def test_different_colours_never_merged():
    a = _line(_sine(), color=(1.0, 0, 0))
    b = _line(_sine(), color=(0, 0, 1.0))   # identical geometry, different colour
    out = dedup_overlapping_line_series([a, b])
    assert len(out) == 2


def test_marker_series_untouched():
    marks = _line([(10, 10), (20, 25), (30, 15)], marker="o")
    dupe_line = _line(_sine(), color=(0, 0, 0))
    dupe_line2 = _line(_sine(jitter=0.2), color=(0, 0, 0))
    out = dedup_overlapping_line_series([marks, dupe_line, dupe_line2])
    assert marks in out           # marker series never dropped
    assert len(out) == 2          # the two duplicate lines -> one


def test_single_line_noop():
    one = _line(_sine())
    assert dedup_overlapping_line_series([one]) == [one]
