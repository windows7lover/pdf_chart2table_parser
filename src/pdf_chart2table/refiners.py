"""Post-extraction refiners: cross-check the extracted series and drop ink that
is decoration rather than data. This is the actionable side of the residual
method -- once we know the marker series (the data), a LINE series can be judged
spurious and removed.

Precision-first: only drop a line when its role is unambiguous, so genuine
line+marker data series are never touched.
"""
from __future__ import annotations

from .model import Axis, Path, Series
from .primitives import (
    is_saturated as _is_saturated,
    round_color as _round_color,
)

# --- residual step-2 refiner thresholds ------------------------------------
# A residual path is a candidate dropped curve only if it is LONG (spans this
# fraction of the region diagonal) and multi-vertex (mirrors the audit's
# ``missed`` criterion: span > 0.3*diag and npts >= 6, residual_audit.py).
_MISSED_SPAN_FRAC = 0.3
_MISSED_MIN_NPTS = 6
# A recovered curve must span at least this fraction of the plot WIDTH in x: a
# near-zero x-extent path is a vertical guide/threshold line, not data.
_MISSED_MIN_XSPAN_FRAC = 0.05
# ...AND at least this fraction of its vertices lie inside the calibrated plot
# box. A residual polyline overlapping the loose region_bbox but sitting mostly
# outside the plot box belongs to a neighbouring subplot/inset (overcapture) and
# must NOT be promoted (mirrors residual_audit._frac_in_box >= 0.5).
_INBOX_FRAC = 0.5
# A promoted curve's calibrated data must be sane: every vertex maps in-range of
# the axis data extent (with this slack beyond the spine-to-spine range). A path
# that calibrates wildly out of range is a mis-located residual, not a curve.
_CALIB_RANGE_SLACK = 0.15
# Dedup: a residual path is a DUPLICATE of an existing series when this fraction
# of its sampled vertices lie within _DEDUP_PX of that series' points (mirrors
# the audit's match test: hit >= 0.6 * len(pp), within 3px L1).
_DEDUP_FRAC = 0.6
_DEDUP_PX = 3.0

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
# Per-channel RGB gap above which two colours are "distinct". A straight line in a
# colour distinct from every marker series is a chromatic FIT / TREND line through
# the data (e.g. a red linear fit over black square markers) and is KEPT as a
# series; a straight line sharing a marker series' colour is decoration (a
# same-colour overlay / connector) and is dropped as before. Spines / gridlines /
# error-bars never reach here (they are filtered as non-data in classify_lines).
_DISTINCT_RGB_GAP = 0.2


def _colors_distinct(a, b) -> bool:
    """True when colours ``a`` and ``b`` differ on any channel by more than
    ``_DISTINCT_RGB_GAP`` (so red vs black is distinct, two near-identical greys
    are not). A missing colour is treated as not-distinct (cannot tell apart)."""
    if a is None or b is None:
        return False
    return any(abs(ai - bi) > _DISTINCT_RGB_GAP for ai, bi in zip(a, b))


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
    marker_colors = [s.color for s in markers]
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
            # A straight line in a SATURATED colour distinct from every marker
            # series is a chromatic fit / trend line through the data (e.g. a red
            # linear fit over black square markers) -- keep it as a series. Only a
            # straight line sharing a marker colour is decoration (same-colour
            # overlay) and is dropped.
            if (_is_saturated(s.color)
                    and all(_colors_distinct(s.color, mc) for mc in marker_colors)):
                kept.append(s)
                continue
            reasons.append("dropped straight reference/fit line (R^2>=0.997)")
            continue
        kept.append(s)
    return kept, reasons


# ===========================================================================
# Residual method -- step 2: promote genuinely-dropped curves from the residual
# ===========================================================================
# These passes operate ONLY on the residual (in-region paths the extractor did
# not account for) and are gated to NOT duplicate already-extracted geometry.
# The audit's ``explained%`` is the success metric. On a clean chart whose
# residual is all decoration (the 23-restyle set: candidate_missed_curves=0)
# every residual path is rejected and the record is returned UNCHANGED -- the
# precision guard the residual-method TODO warns about.


def _series_pixels(series: list[Series]) -> list[list[tuple[float, float]]]:
    return [_pixels(s) for s in series]


def _overlaps_existing(path_pts, existing_pixels) -> bool:
    """True if ``path_pts`` duplicates an already-extracted series.

    Mirrors residual_audit's match test: a path is explained by a series when at
    least ``_DEDUP_FRAC`` of its sampled vertices lie within ``_DEDUP_PX`` (L1)
    of that series' points. This is the dedup guard -- never re-add a curve the
    extractor already has.
    """
    if not path_pts:
        return True  # nothing to add -> treat as already covered
    pp = path_pts[:: max(1, len(path_pts) // 30)] or path_pts
    for spts in existing_pixels:
        if not spts:
            continue
        sp = spts[:: max(1, len(spts) // 200)]
        hit = sum(1 for qx, qy in pp
                  if min(abs(qx - sx) + abs(qy - sy) for sx, sy in sp) < _DEDUP_PX)
        if hit >= _DEDUP_FRAC * len(pp):
            return True
    return False


def _frac_in_box(pts, box) -> float:
    """Fraction of ``pts`` inside the calibrated plot ``box`` (x0,y0,x1,y1).

    Mirrors residual_audit._frac_in_box: a residual path overlapping the loose
    region_bbox but sitting mostly outside the plot box belongs to a
    neighbouring subplot/inset and must not be promoted here.
    """
    if not pts or box is None:
        return 1.0
    bx0, by0, bx1, by1 = box
    lo_x, hi_x = (bx0, bx1) if bx0 <= bx1 else (bx1, bx0)
    lo_y, hi_y = (by0, by1) if by0 <= by1 else (by1, by0)
    n_in = sum(1 for x, y in pts if lo_x <= x <= hi_x and lo_y <= y <= hi_y)
    return n_in / len(pts)


def refine_region_overcapture(path_pts, plot_box) -> bool:
    """Guard: True when a residual block belongs to a DIFFERENT chart (an
    adjacent inset/subplot bled into the region) and so must NOT be promoted.

    A residual polyline that overlaps the loose region but lies mostly OUTSIDE
    the calibrated spine-to-spine plot box is foreign ink. (Nested-inset paths
    are already trimmed out of the region upstream by ``plot_region._inset_boxes``
    -- this is the residual-side guard for the leftover bleed that trimming did
    not catch.) Returns True = reject, False = inside this chart's plot box.
    """
    return _frac_in_box(path_pts, plot_box) < _INBOX_FRAC


def refine_decoration(path_pts, marker_pixels, diag: float) -> bool:
    """Guard: True when a residual path is DECORATION (legend swatch / arrow /
    frame / connector / straight reference-fit) and so must NOT be promoted.

    Reuses ``is_decoration_line`` (connector-through-markers / straight-fit) so
    refine_dropped_curve cannot over-promote a line the spurious-line refiner
    would have dropped. Frame/swatch geometry is already excluded as residual by
    the audit's near-border / legend-box tests; this is the line-shaped guard.
    """
    return bool(marker_pixels) and is_decoration_line(path_pts, marker_pixels, diag)


def _calibrates_in_range(path_pts, x_axis: Axis, y_axis: Axis) -> bool:
    """True if every vertex maps to a sane in-range data coord under the axes.

    A genuinely-dropped curve calibrates to coords within the axis data extent
    (plus slack). A residual that calibrates wildly out of range is mis-located
    ink, not a curve -> reject (precision over recall).
    """
    if x_axis.calibration is None or y_axis.calibration is None:
        return False
    if x_axis.pixel_range is None or y_axis.pixel_range is None:
        return False
    from .calibrate import to_data

    def _in_axis(px_lo, px_hi, calib, vals_px):
        d0 = to_data(calib, px_lo)
        d1 = to_data(calib, px_hi)
        lo, hi = (d0, d1) if d0 <= d1 else (d1, d0)
        span = (hi - lo) or 1.0
        lo -= _CALIB_RANGE_SLACK * abs(span)
        hi += _CALIB_RANGE_SLACK * abs(span)
        return all(lo <= to_data(calib, p) <= hi for p in vals_px)

    xs = [x for x, _ in path_pts]
    ys = [y for _, y in path_pts]
    return (_in_axis(x_axis.pixel_range[0], x_axis.pixel_range[1],
                     x_axis.calibration, xs)
            and _in_axis(y_axis.pixel_range[0], y_axis.pixel_range[1],
                         y_axis.calibration, ys))


def refine_dropped_curve(
    series: list[Series],
    residual_paths: list[Path],
    region,
    axes: tuple[Axis, Axis],
    band_colors: frozenset | None = None,
) -> tuple[list[Series], list[str]]:
    """Promote a residual polyline to a NEW data series, conservatively.

    A residual path is promoted ONLY IF ALL of:
      1. it is LONG + multi-vertex (the audit's candidate-missed-curve test);
      2. it does NOT overlap an existing series (dedup guard);
      3. it lies mostly inside the calibrated plot box (NOT adjacent-subplot
         bleed -- ``refine_region_overcapture``);
      4. it is NOT decoration (legend/connector/straight-fit --
         ``refine_decoration``);
      5. it calibrates to sane in-range data coords.

    This recovers genuinely-dropped curves without re-adding decoration or
    duplicating geometry. On a chart with no dropped curves (residual all
    decoration) it is a NO-OP. Returns ``(series, promote_reasons)``.
    """
    x_axis, y_axis = axes
    if x_axis.calibration is None or y_axis.calibration is None:
        return series, []

    plot_box = None
    if x_axis.pixel_range is not None and y_axis.pixel_range is not None:
        plot_box = (x_axis.pixel_range[0], y_axis.pixel_range[0],
                    x_axis.pixel_range[1], y_axis.pixel_range[1])

    bx0, by0, bx1, by1 = region.bbox
    diag = max(((bx1 - bx0) ** 2 + (by1 - by0) ** 2) ** 0.5, 1.0)
    existing_pixels = _series_pixels(series)
    marker_pixels = [p for s in series if s.marker is not None for p in _pixels(s)]

    from .calibrate import to_data

    promoted: list[Series] = []
    reasons: list[str] = []
    band_colors = band_colors or frozenset()
    for path in residual_paths:
        # A dropped data curve is an OPEN polyline; a closed path is a filled
        # glyph / shape / frame, never a recovered curve -> never promote.
        if path.closed:
            continue
        # Never resurrect a genuine shaded-band boundary outline: a stroke whose
        # colour matches a real fill band is the band's edge, not a data series.
        col = path.stroke or path.fill
        if col is not None and _round_color(col) in band_colors:
            continue
        # A LIGHT-GREY path is overwhelmingly a guide / reference / confidence-band
        # edge, not a data series (audit of missed-curve flags). Skip achromatic
        # light greys (near-equal RGB, bright); keep black/dark-grey, common data
        # colours.
        if col is not None and (max(col) - min(col)) < 0.10 and 0.55 < sum(col) / 3:
            continue
        pts = path.points
        if len(pts) < _MISSED_MIN_NPTS:
            continue
        xs = [x for x, _ in pts]
        ys = [y for _, y in pts]
        span = ((max(xs) - min(xs)) ** 2 + (max(ys) - min(ys)) ** 2) ** 0.5
        if span < _MISSED_SPAN_FRAC * diag:
            continue
        # A data curve y=f(x) spans a non-trivial x-range; a path with ~zero
        # x-extent is a VERTICAL reference / threshold / errorbar line (e.g. a
        # grey guide at constant x), not a series -- never promote it.
        if plot_box is not None:
            pw = abs(plot_box[2] - plot_box[0]) or 1.0
            if (max(xs) - min(xs)) < _MISSED_MIN_XSPAN_FRAC * pw:
                continue
        if refine_region_overcapture(pts, plot_box):
            continue
        if _overlaps_existing(pts, existing_pixels):
            continue
        if refine_decoration(pts, marker_pixels, diag):
            continue
        if not _calibrates_in_range(pts, x_axis, y_axis):
            continue
        # Promote: build a marker-less data series in pixel + data coords.
        color = path.stroke or path.fill
        points = [{"x": float(to_data(x_axis.calibration, px)),
                   "y": float(to_data(y_axis.calibration, py)),
                   "x_px": float(px), "y_px": float(py)} for px, py in pts]
        new_series = Series(label=None, marker=None, color=color, points=points)
        promoted.append(new_series)
        existing_pixels.append(_pixels(new_series))
        reasons.append(f"promoted dropped curve ({len(pts)} pts, span={span:.0f}px)")

    return series + promoted, reasons
