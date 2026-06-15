"""Regression tests for error-bar (whisker + cap) detection.

Reproduces the 2510.04789_p3c4 failure: vertical error-bar whiskers (and their
horizontal caps) co-located with square markers were traced as a jagged
marker-less polyline ("series"). ``detect_error_bars`` must flag those
decoration strokes so the caller drops them, WITHOUT removing genuine vertical
or horizontal data series.
"""

from __future__ import annotations

from pdf_chart2table.error_bars import detect_error_bars
from pdf_chart2table.model import Path as VPath, Region


def _square(cx, cy, *, fill, half=3.0):
    pts = [(cx - half, cy - half), (cx + half, cy - half), (cx + half, cy + half),
           (cx - half, cy + half), (cx - half, cy - half)]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return VPath(points=pts, stroke=fill, fill=fill, width=1.0, dashes=None,
                 closed=True, bbox=(min(xs), min(ys), max(xs), max(ys)))


def _vseg(x, y0, y1, *, stroke):
    pts = [(x, y0), (x, y1)]
    return VPath(points=pts, stroke=stroke, fill=None, width=1.0, dashes=None,
                 closed=False, bbox=(x, min(y0, y1), x, max(y0, y1)))


def _hseg(x0, x1, y, *, stroke):
    pts = [(x0, y), (x1, y)]
    return VPath(points=pts, stroke=stroke, fill=None, width=1.0, dashes=None,
                 closed=False, bbox=(min(x0, x1), y, max(x0, x1), y))


NAVY = (0.0, 0.0, 0.5)


def _region(n):
    return Region(bbox=(100.0, 100.0, 300.0, 300.0),
                  path_indices=list(range(n)), text_indices=[])


def test_whiskers_and_caps_flagged_squares_kept():
    # Squares (data) + a vertical whisker through each + horizontal caps at the
    # whisker ends. The whiskers/caps must be flagged; the squares must NOT be.
    marker_xs = [130.0, 180.0, 230.0]
    marker_ys = [250.0, 200.0, 160.0]
    paths = []
    square_idx = []
    for cx, cy in zip(marker_xs, marker_ys):
        square_idx.append(len(paths))
        paths.append(_square(cx, cy, fill=NAVY))
    whisker_idx = []
    cap_idx = []
    for cx, cy in zip(marker_xs, marker_ys):
        whisker_idx.append(len(paths))
        paths.append(_vseg(cx, cy - 15, cy + 15, stroke=NAVY))   # whisker
        cap_idx.append(len(paths))
        paths.append(_hseg(cx - 4, cx + 4, cy - 15, stroke=NAVY))  # bottom cap
        cap_idx.append(len(paths))
        paths.append(_hseg(cx - 4, cx + 4, cy + 15, stroke=NAVY))  # top cap

    drop = detect_error_bars(_region(len(paths)), paths)
    # Every whisker and cap is flagged.
    assert set(whisker_idx) <= drop
    assert set(cap_idx) <= drop
    # No square is flagged.
    assert drop.isdisjoint(set(square_idx))


def test_genuine_vertical_line_data_not_dropped():
    # A real vertical data series (a tall vertical line) with NO markers anywhere
    # must never be flagged: detect_error_bars only fires when markers exist.
    paths = [_vseg(200.0, 120.0, 280.0, stroke=NAVY)]
    drop = detect_error_bars(_region(len(paths)), paths)
    assert drop == set()


def test_vertical_segment_not_anchored_to_marker_kept():
    # A vertical stroke far in x from every marker is genuine data (e.g. a
    # vertical reference line), not a whisker -> must not be flagged.
    paths = [
        _square(130.0, 250.0, fill=NAVY),
        _square(180.0, 200.0, fill=NAVY),
        _square(230.0, 160.0, fill=NAVY),
        _vseg(280.0, 130.0, 270.0, stroke=NAVY),  # x=280, no marker near -> keep
    ]
    drop = detect_error_bars(_region(len(paths)), paths)
    assert 3 not in drop


def test_single_whisker_not_dropped():
    # Below the _MIN_WHISKERS threshold (one lone vertical near a marker): do not
    # fire, so a single genuine vertical near a datum is preserved.
    paths = [
        _square(130.0, 250.0, fill=NAVY),
        _vseg(130.0, 235.0, 265.0, stroke=NAVY),
    ]
    drop = detect_error_bars(_region(len(paths)), paths)
    assert drop == set()


def test_recover_error_bars_returns_whisker_geometry():
    # recover_error_bars returns the whisker (cx, top, bottom) so the caller can
    # attach a per-point y_err and the renderer can redraw the bars.
    from pdf_chart2table.error_bars import recover_error_bars
    marker_xs = [130.0, 180.0, 230.0]
    marker_ys = [250.0, 200.0, 160.0]
    paths = []
    for cx, cy in zip(marker_xs, marker_ys):
        paths.append(_square(cx, cy, fill=NAVY))
    for cx, cy in zip(marker_xs, marker_ys):
        paths.append(_vseg(cx, cy - 15, cy + 15, stroke=NAVY))
        paths.append(_hseg(cx - 4, cx + 4, cy - 15, stroke=NAVY))
        paths.append(_hseg(cx - 4, cx + 4, cy + 15, stroke=NAVY))
    idx, whiskers = recover_error_bars(_region(len(paths)), paths)
    assert idx  # decoration to remove
    assert len(whiskers) == 3
    for (cx, top, bot), mx in zip(sorted(whiskers), marker_xs):
        assert abs(cx - mx) < 1.0
        assert abs((bot - top) - 30.0) < 1.0  # whisker spans cy-15..cy+15


def test_attach_error_bars_sets_y_err():
    # _attach_error_bars maps a whisker to the marker point at its x and sets a
    # symmetric y_err = half the whisker height in DATA units (identity calib).
    from pdf_chart2table.cli import _attach_error_bars
    from pdf_chart2table.model import Axis, Series
    cal = {"scale": "linear", "a": 1.0, "b": 0.0, "r2": 1.0}
    y = Axis(scale="linear", pixel_range=(0.0, 100.0), data_range=(0.0, 100.0),
             calibration=cal)
    s = Series(label=None, marker="o", color=(0, 0, 0),
               points=[{"x": 130.0, "y": 250.0, "x_px": 130.0, "y_px": 250.0}])
    _attach_error_bars([s], [(130.0, 235.0, 265.0)], y)  # whisker height 30 -> err 15
    assert abs(s.points[0]["y_err"] - 15.0) < 1e-6
