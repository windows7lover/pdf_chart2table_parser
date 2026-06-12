"""Post-extraction refiners: cross-check the extracted series and drop ink that
is decoration rather than data. This is the actionable side of the residual
method -- once we know the marker series (the data), a LINE series can be judged
spurious and removed.

Precision-first: only drop a line when its role is unambiguous, so genuine
line+marker data series are never touched.
"""
from __future__ import annotations

from .model import Series

# A line vertex within this many pixels of a marker counts as "on" that marker.
_COINCIDE_PX = 3.0
# A line is a redundant CONNECTOR when at least this fraction of its vertices sit
# on markers (a real data curve is dense BETWEEN markers, so its fraction is low).
_CONNECTOR_FRAC = 0.9
# ...AND the markers are not far more numerous than the line's vertices. This is
# the "1:1 connector" case (markers ~= vertices); when markers ~= 2x vertices the
# colour carries two marker trajectories (multitrack) and the line is a distinct
# series -- mirrors lines._is_connector's multitrack guard so we don't drop it.
_CONNECTOR_MULTITRACK = 1.3
# A line is a straight reference/fit line when its linear fit R^2 is at least this
# (a perfectly straight dense polyline among scatter markers is a guide, not data).
_STRAIGHT_R2 = 0.999
# ...and spans at least this fraction of the plot diagonal (ignore tiny segments).
_STRAIGHT_MIN_SPAN = 0.3


def _pixels(s: Series) -> list[tuple[float, float]]:
    return [(p["x_px"], p["y_px"]) for p in s.points
            if p.get("x_px") is not None and p.get("y_px") is not None]


def _linear_r2(pts: list[tuple[float, float]]) -> float:
    n = len(pts)
    if n < 3:
        return 1.0
    mx = sum(x for x, _ in pts) / n
    my = sum(y for _, y in pts) / n
    sxx = sum((x - mx) ** 2 for x, _ in pts)
    syy = sum((y - my) ** 2 for _, y in pts)
    sxy = sum((x - mx) * (y - my) for x, y in pts)
    if sxx == 0 or syy == 0:  # a perfectly vertical/horizontal line is straight
        return 1.0
    return (sxy * sxy) / (sxx * syy)


def _connector_frac(line_pts, marker_pts, tol) -> float:
    if not line_pts or not marker_pts:
        return 0.0
    t2 = tol * tol
    hit = sum(1 for lx, ly in line_pts
              if min((lx - mx) ** 2 + (ly - my) ** 2 for mx, my in marker_pts) <= t2)
    return hit / len(line_pts)


def is_decoration_line(pts, marker_pts, diag: float) -> bool:
    """True if a point-list is the kind of LINE the spurious-line refiner drops --
    a connector through markers, or a straight reference/fit line. Used by the
    residual audit so a correctly-dropped line is scored as explained decoration
    (not unexplained residual), mirroring ``drop_spurious_lines``."""
    if len(pts) < 3 or not marker_pts:
        return False
    if _connector_frac(pts, marker_pts, _COINCIDE_PX) >= _CONNECTOR_FRAC:
        return True
    xs = [x for x, _ in pts]
    ys = [y for _, y in pts]
    span = ((max(xs) - min(xs)) ** 2 + (max(ys) - min(ys)) ** 2) ** 0.5
    return _linear_r2(pts) >= _STRAIGHT_R2 and span >= _STRAIGHT_MIN_SPAN * diag


def drop_spurious_lines(series: list[Series]) -> tuple[list[Series], list[str]]:
    """Drop LINE series (``marker is None``) that are decoration, not data:

    * a **connector** whose vertices coincide with an existing marker series
      (the markers are the data; the line just joins them -- e.g. a scatter plot
      whose points should not be connected);
    * a **straight reference/fit line** (near-perfect linear fit) drawn through
      scatter data.

    Both require a marker series to exist (so we never strip a line from a pure
    line chart). Returns ``(kept_series, drop_reasons)``.
    """
    markers = [s for s in series if s.marker is not None]
    if not markers:
        return series, []
    marker_pts = [p for s in markers for p in _pixels(s)]
    if not marker_pts:
        return series, []
    mxs = [x for x, _ in marker_pts]
    mys = [y for _, y in marker_pts]
    diag = max(((max(mxs) - min(mxs)) ** 2 + (max(mys) - min(mys)) ** 2) ** 0.5, 1.0)

    kept: list[Series] = []
    reasons: list[str] = []
    for s in series:
        if s.marker is not None:
            kept.append(s)
            continue
        line_pts = _pixels(s)
        if len(line_pts) < 3:
            kept.append(s)
            continue
        xs = [x for x, _ in line_pts]
        ys = [y for _, y in line_pts]
        span = ((max(xs) - min(xs)) ** 2 + (max(ys) - min(ys)) ** 2) ** 0.5
        frac = _connector_frac(line_pts, marker_pts, _COINCIDE_PX)
        if (frac >= _CONNECTOR_FRAC
                and len(marker_pts) <= _CONNECTOR_MULTITRACK * len(line_pts)):
            reasons.append(f"dropped connector line ({frac:.0%} of vertices on markers)")
            continue
        if _linear_r2(line_pts) >= _STRAIGHT_R2 and span >= _STRAIGHT_MIN_SPAN * diag:
            reasons.append("dropped straight reference/fit line (R^2>=0.997)")
            continue
        kept.append(s)
    return kept, reasons
