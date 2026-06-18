"""Regression: a sparse marker series with ONE point far off the connector path
must NOT be reported as a connected line+marker series.

Bug (2504.21746_p6c1): a black scatter of 6 circles where 5 sit in a small cluster
joined by a real short line and the 6th is an ISOLATED circle ~25 px above. The
connect test accepted the 5-point line (5/6 >= the 0.8 fraction), but the renderer
draws ONE polyline through ALL points in draw order, so connecting the lone far
point fabricated a long spurious diagonal connector from the cluster up to it.

Fix: a series is connected only when some same-colour path BOTH threads >= frac of
the points AND leaves no point grossly off it -- so the rendered all-points polyline
never invents a segment. A genuine line+marker (every point on the line) stays
connected.
"""
from __future__ import annotations

from pdf_chart2table.model import Path
from pdf_chart2table.style import match_series_styles

REGION = (0.0, 0.0, 100.0, 100.0)
BLACK = (0.12, 0.12, 0.12)


def _circle(cx, cy):
    """A small filled black disk marker centred at (cx, cy)."""
    import math
    r = 1.5
    pts = [(cx + r * math.cos(2 * math.pi * k / 16),
            cy + r * math.sin(2 * math.pi * k / 16)) for k in range(17)]
    bbox = (cx - r, cy - r, cx + r, cy + r)
    return Path(points=pts, stroke=BLACK, fill=BLACK,
                width=0.6, dashes=None, closed=True, bbox=bbox)


def _polyline(centres):
    """A black stroked polyline through the given centres (a connector)."""
    xs = [x for x, _ in centres]
    ys = [y for _, y in centres]
    bbox = (min(xs), min(ys), max(xs), max(ys))
    return Path(points=list(centres), stroke=BLACK, fill=None,
                width=0.6, dashes=None, closed=False, bbox=bbox)


def _series(centres):
    return [{"color": list(BLACK),
             "points": [{"x_px": x, "y_px": y} for x, y in centres]}]


def test_sparse_scatter_with_off_line_point_not_connected():
    # 5 clustered circles joined by a real line, plus a 6th isolated circle far
    # above the line. The line threads 5/6, but the 6th is grossly off it.
    cluster = [(40.0, 80.0), (45.0, 78.0), (50.0, 76.0),
               (55.0, 76.0), (60.0, 75.0)]
    outlier = (43.0, 50.0)          # ~25 px above the line
    centres = cluster + [outlier]
    paths = [_circle(*c) for c in centres]
    paths.append(_polyline(cluster))  # connector through the 5, NOT the outlier
    styles, _ = match_series_styles(paths, REGION, _series(centres))
    assert styles[0].get("connect") is False, (
        "lone off-line point must keep the series UNCONNECTED (else the renderer "
        "draws a spurious diagonal to it)")


def test_all_points_on_line_is_connected():
    # Positive control: every marker sits ON the same-colour polyline -> connected.
    centres = [(20.0 + 8 * i, 80.0 - 5 * i) for i in range(6)]
    paths = [_circle(*c) for c in centres]
    paths.append(_polyline(centres))
    styles, _ = match_series_styles(paths, REGION, _series(centres))
    assert styles[0].get("connect") is True, (
        "a marker series lying on its own connector must be reported connected")


def test_steep_dip_curve_split_into_pieces_is_connected():
    # 2205.07176_p2c1: a smooth dense curve with a sharp full-height DIP is emitted
    # as SEVERAL long contiguous same-colour pieces (the steep feature broke the
    # stroke). No single piece threads >= frac of the markers, but every marker sits
    # ON some piece -> the series must be connected (line+marker), not bare scatter.
    import numpy as np
    xs = np.linspace(10.0, 90.0, 60)
    # near-flat curve with a narrow, near-full-height spike at the middle (the dip)
    ys = np.array([20.0 + 60.0 * np.exp(-((x - 50.0) / 4.0) ** 2) for x in xs]) + 15.0
    centres = list(zip(xs.tolist(), ys.tolist()))
    paths = [_circle(x, y) for x, y in centres]
    # split the connecting polyline into 3 contiguous pieces (each spans ~1/3)
    third = len(centres) // 3
    for a, b in [(0, third + 1), (third, 2 * third + 1), (2 * third, len(centres))]:
        paths.append(_polyline(centres[a:b]))
    styles, _ = match_series_styles(paths, REGION, _series(centres))
    assert styles[0].get("connect") is True, (
        "a dense curve drawn as several long contiguous same-colour pieces (incl. a "
        "steep dip) must be connected even though no single piece threads frac")


def test_sparse_scatter_with_multiple_off_paths_not_connected():
    # Guard: pooling pieces must NOT connect a pure scatter. Two same-colour long
    # paths (e.g. separate fit/model curves) that both MISS the markers leave the
    # markers far from every piece -> stay unconnected (#66 not regressed).
    centres = [(20.0, 30.0), (35.0, 70.0), (50.0, 25.0), (65.0, 65.0), (80.0, 40.0)]
    paths = [_circle(*c) for c in centres]
    paths.append(_polyline([(15.0, 85.0), (85.0, 85.0)]))   # fit line far above
    paths.append(_polyline([(15.0, 10.0), (85.0, 10.0)]))   # fit line far below
    styles, _ = match_series_styles(paths, REGION, _series(centres))
    assert styles[0].get("connect") is False, (
        "two long same-colour paths that both miss the markers must not pool into a "
        "spurious connection")


def test_union_connector_ignores_tiny_series_threaded_by_sibling():
    # 2005.03896_p4c1: a spurious 3-point group sits on a SAME-COLOUR sibling's
    # long curve. The union (multi-piece) connector must NOT connect such a tiny
    # group via the sibling's line (cross-talk) -- a split curve is dense, so the
    # union path only legitimately connects a many-point series.
    from pdf_chart2table.style import _union_connector_fits_all
    from pdf_chart2table.model import Path as _P

    def _line(p0, p1, n=200):
        return _P(points=[(p0[0] + (p1[0]-p0[0])*k/(n-1), p0[1] + (p1[1]-p0[1])*k/(n-1))
                          for k in range(n)],
                  stroke=(0, 0, 1), fill=None, width=1.0, dashes=None, closed=False,
                  bbox=(min(p0[0], p1[0]), min(p0[1], p1[1]), max(p0[0], p1[0]), max(p0[1], p1[1])))
    # two long pieces of one curve along y=x
    pieces = [_line((0, 0), (50, 50)), _line((50, 50), (100, 100))]
    # a tiny 3-point group lying ON that curve
    tiny = [(20.0, 20.0), (21.0, 21.0), (22.0, 22.0)]
    assert not _union_connector_fits_all(pieces, tiny, tol=2.0), \
        "a 3-point group threaded by a sibling curve must not union-connect"
    # a dense many-point series ON the pieces DOES union-connect
    dense = [(float(x), float(x)) for x in range(0, 100, 4)]  # 25 points
    assert _union_connector_fits_all(pieces, dense, tol=2.0)
