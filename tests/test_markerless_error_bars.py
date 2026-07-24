"""Regression tests for MARKER-LESS error-bar recovery.

When a chart draws its points only as error bars (matplotlib ``fmt='none'``)
there is no central marker, so ``detect_error_bars`` cannot fire and the
whisker+cap strokes were traced by ``lines.py`` as a phantom polyline of
whisker endpoints (10 fake points for 5 real data points, confirmed on a
synthetic probe). ``recover_markerless_error_bars`` finds the I-beams directly
and returns each bar's CENTRE (the datum) + half-length (the error), so the
caller strips the strokes and emits the real points instead.

Precision-first: only a whisker capped at BOTH ends counts, and at least
``_MIN_IBEAMS`` of one orientation are required -- a lone stroke, a capless
whisker, or a gridline is never recovered.
"""

from __future__ import annotations

from pdf_chart2table.error_bars import recover_markerless_error_bars
from pdf_chart2table.model import Path as VPath, Region

NAVY = (0.0, 0.0, 0.5)
RED = (0.55, 0.0, 0.0)


def _vseg(x, y0, y1, *, stroke=NAVY):
    return VPath(points=[(x, y0), (x, y1)], stroke=stroke, fill=None, width=1.0,
                 dashes=None, closed=False, bbox=(x, min(y0, y1), x, max(y0, y1)))


def _hseg(x0, x1, y, *, stroke=NAVY):
    return VPath(points=[(x0, y), (x1, y)], stroke=stroke, fill=None, width=1.0,
                 dashes=None, closed=False, bbox=(min(x0, x1), y, max(x0, x1), y))


def _region(n):
    return Region(bbox=(100.0, 100.0, 300.0, 300.0),
                  path_indices=list(range(n)), text_indices=[])


def _vbar(paths, cx, cy, half, *, stroke=NAVY, cap=4.0):
    """A vertical I-beam centred on (cx, cy): whisker + top & bottom caps."""
    paths.append(_vseg(cx, cy - half, cy + half, stroke=stroke))
    paths.append(_hseg(cx - cap, cx + cap, cy - half, stroke=stroke))
    paths.append(_hseg(cx - cap, cx + cap, cy + half, stroke=stroke))


def _hbar(paths, cx, cy, half, *, stroke=RED, cap=4.0):
    """A horizontal I-beam centred on (cx, cy): whisker + left & right caps."""
    paths.append(_hseg(cx - half, cx + half, cy, stroke=stroke))
    paths.append(_vseg(cx - half, cy - cap, cy + cap, stroke=stroke))
    paths.append(_vseg(cx + half, cy - cap, cy + cap, stroke=stroke))


def test_vertical_ibeams_recovered_at_centre():
    paths = []
    centres = [(130.0, 250.0), (180.0, 200.0), (230.0, 160.0)]
    for cx, cy in centres:
        _vbar(paths, cx, cy, 10.0)
    idx, pts = recover_markerless_error_bars(_region(len(paths)), paths)
    # every whisker + cap stroke stripped
    assert idx == set(range(len(paths)))
    assert len(pts) == 3
    got = sorted((round(cx), round(cy), round(err), o) for cx, cy, err, o in pts)
    assert got == [(130, 250, 10, "v"), (180, 200, 10, "v"), (230, 160, 10, "v")]


def test_horizontal_ibeams_recovered():
    paths = []
    for cx, cy in [(140.0, 250.0), (190.0, 210.0), (240.0, 170.0)]:
        _hbar(paths, cx, cy, 8.0)
    idx, pts = recover_markerless_error_bars(_region(len(paths)), paths)
    assert len(pts) == 3
    assert all(o == "h" and round(err) == 8 for _cx, _cy, err, o in pts)


def test_too_few_ibeams_not_recovered():
    # Only 2 I-beams: below _MIN_IBEAMS -> nothing recovered (precision).
    paths = []
    _vbar(paths, 130.0, 250.0, 10.0)
    _vbar(paths, 180.0, 200.0, 10.0)
    idx, pts = recover_markerless_error_bars(_region(len(paths)), paths)
    assert idx == set() and pts == []


def test_capless_whiskers_not_recovered():
    # Bare vertical strokes with NO caps are ambiguous (data spikes / bars /
    # annotation lines) -> never recovered, even when several are present.
    paths = [_vseg(x, 160.0, 250.0) for x in (130.0, 180.0, 230.0, 250.0)]
    idx, pts = recover_markerless_error_bars(_region(len(paths)), paths)
    assert idx == set() and pts == []


def test_gridlines_not_recovered_as_error_bars():
    # Full-height vertical lines (> 0.6 * region diagonal) are gridlines, never
    # whiskers; their crossing horizontals are not caps.
    paths = []
    for x in (130.0, 180.0, 230.0):
        paths.append(_vseg(x, 100.0, 300.0))          # spans the whole panel
    idx, pts = recover_markerless_error_bars(_region(len(paths)), paths)
    assert idx == set() and pts == []


def test_excluded_paths_not_reconsidered():
    # Paths already claimed by the marker-anchored pass are excluded.
    paths = []
    for cx, cy in [(130.0, 250.0), (180.0, 200.0), (230.0, 160.0)]:
        _vbar(paths, cx, cy, 10.0)
    idx, pts = recover_markerless_error_bars(
        _region(len(paths)), paths, exclude=set(range(len(paths))))
    assert idx == set() and pts == []


def test_wide_caps_rejected():
    # A "cap" wider than half the whisker length is a data segment / gridline,
    # not a cap -> the I-beam is not confirmed.
    paths = []
    for cx, cy in [(130.0, 250.0), (180.0, 200.0), (230.0, 160.0)]:
        paths.append(_vseg(cx, cy - 10.0, cy + 10.0))
        paths.append(_hseg(cx - 40.0, cx + 40.0, cy - 10.0))  # 80px wide, whisker 20px
        paths.append(_hseg(cx - 40.0, cx + 40.0, cy + 10.0))
    idx, pts = recover_markerless_error_bars(_region(len(paths)), paths)
    assert idx == set() and pts == []
