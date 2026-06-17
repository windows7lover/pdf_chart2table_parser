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
