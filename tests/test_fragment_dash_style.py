"""Regression: recover line STYLE from fragment-drawn dashing.

Bug (2001.01928_p5c1): a dashed / dotted / dash-dot curve can be rasterised as
MANY tiny solid sub-strokes (each path ``dashes=None``, ~2 vertices, sub-point
length) separated by real GAPS. There is no PDF dash array anywhere, so the
merged curve renders SOLID. ``style._classify_fragment_dash`` reconstructs the
on/off pattern from the fragment geometry and returns the matplotlib linestyle.

A SOLID curve merely drawn in many contiguous pieces (full coverage, no gaps)
must STAY solid, and a solid curve whose colour also has incidental short paths
(ticks/markers) must not be flipped.
"""
from __future__ import annotations

from pdf_chart2table.model import Path
from pdf_chart2table.style import _classify_fragment_dash, match_series_styles


def _seg(x0, y, length):
    """One short horizontal sub-stroke fragment of given drawn length at y."""
    pts = [(x0, y), (x0 + length, y)]
    return Path(points=pts, stroke=(1.0, 0.0, 0.0), fill=None, width=0.85,
                dashes=None, closed=False,
                bbox=(x0, y, x0 + length, y))


def _fragment_curve(on, off, *, n=60, y=50.0, x0=100.0):
    """Build a fragment-drawn line: ``n`` dash "on" runs of drawn length ``on``,
    each made of tiny touching sub-strokes, separated by ``off`` gaps. When
    ``on`` is a (long, short) pair the runs alternate -> dash-dot."""
    frags = []
    x = x0
    for i in range(n):
        run = on[i % len(on)] if isinstance(on, tuple) else on
        # tile the run with tiny sub-strokes (0.065pt, like the real PDF)
        step = 0.065
        t = 0.0
        while t < run - 1e-9:
            frags.append(_seg(x + t, y, min(step, run - t)))
            t += step
        x += run + off
    return frags


def test_classify_dashed():
    # uniform moderate on-runs (~3.8pt) with ~3.9pt gaps -> dashed
    frags = _fragment_curve(3.8, 3.9, n=60)
    assert _classify_fragment_dash(frags, 0.85) == "--"


def test_classify_dotted():
    # tiny on-runs (~0.59pt, ~ a stroke width) with gaps -> dotted
    frags = _fragment_curve(0.59, 3.5, n=80)
    assert _classify_fragment_dash(frags, 0.85) == ":"


def test_classify_dashdot():
    # alternating long dash (4.6pt) + short dot (0.6pt) -> dash-dot
    frags = _fragment_curve((4.6, 0.6), 3.0, n=60)
    assert _classify_fragment_dash(frags, 0.85) == "-."


def test_solid_in_contiguous_pieces_stays_solid():
    # A solid curve drawn in many touching pieces: no OFF gaps -> stays solid.
    frags = _fragment_curve(0.59, 0.0, n=200)
    assert _classify_fragment_dash(frags, 0.85) is None


def test_too_few_fragments_stays_solid():
    # A handful of short pieces is just a wiggly solid curve, not a dash signal:
    # below the fragment-count floor the classifier abstains (stays solid).
    frags = _fragment_curve(0.59, 3.5, n=2)
    assert len(frags) < 30
    assert _classify_fragment_dash(frags, 0.85) is None


def test_solid_long_polyline_with_decor_stays_solid():
    # A genuine solid curve (one long polyline) whose colour also carries
    # incidental short paths (ticks/markers) must NOT be flipped to dashed:
    # the long drawn length dominates the short fragments.
    red = (1.0, 0.0, 0.0)
    long_pts = [(100.0 + i, 50.0) for i in range(0, 121, 2)]   # 120pt solid line
    long_path = Path(points=long_pts, stroke=red, fill=None, width=0.85,
                     dashes=None, closed=False, bbox=(100.0, 50.0, 220.0, 50.0))
    # 40 incidental tiny tick fragments (short_len ~ 24pt << 120pt long_len)
    ticks = [_seg(100.0 + 3 * i, 40.0, 0.6) for i in range(40)]
    assert _classify_fragment_dash([long_path] + ticks, 0.85) is None


def test_match_series_styles_recovers_dashed_from_fragments():
    # End-to-end through match_series_styles: a fragment-drawn dashed curve with
    # no PDF dash array gets a non-solid linestyle.
    region = (90.0, 30.0, 320.0, 90.0)
    frags = _fragment_curve(3.8, 3.9, n=60)
    pts = [{"x_px": 0.5 * (p.bbox[0] + p.bbox[2]),
            "y_px": 0.5 * (p.bbox[1] + p.bbox[3])} for p in frags]
    series = [{"color": [1.0, 0.0, 0.0], "points": pts}]
    styles, _ = match_series_styles(frags, region, series)
    assert styles[0].get("linestyle") == "--"
