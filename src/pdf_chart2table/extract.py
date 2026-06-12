"""Orchestrate the vector pipeline: PDF -> per-chart ``ChartResult``.

For each page: ``load_pdf`` -> ``detect_regions`` -> ``calibrate_panels`` ->
``classify_marks``. Each series' marker centroids (pixel space) are converted to
data via the fitted axis calibration, yielding a :class:`Series` with both data
and pixel coordinates. A region becomes an ``extracted`` :class:`ChartResult`
when both axes are calibrated and at least one clean series is found; otherwise
it is ``skipped`` with a reason (precision over recall).

Series come from two sources: marker centroids (scatter / line-with-markers)
via ``classify_marks``, and marker-less data curves via ``classify_lines``
(polyline vertices of saturated off-axis curves). Line curves whose colour is
already a marker series are dropped (line+marker dedupe). A region with no clean
series is skipped (``"no clean series found"``), never emitting tick garbage.

Public API:
    extract_region(region, axes, paths, texts, source) -> ChartResult
    extract_pdf(path, pages=None) -> list[ChartResult]
"""
# Iteration-7 precision / recall improvements (marks.py + extract.py scope):
#
# Fix I  – Faint/short marker series recovery
#   A marker series of exactly 2 marks is retained when the region already
#   contains at least one "anchor" series (≥ _MIN_MARKS_PER_SERIES marks) or at
#   least one clean line series.  This rescues a faint or partially-visible
#   series (e.g. the third of three lines whose data points are sparse on that
#   page) without admitting lone 2-mark annotation glyphs in otherwise empty
#   regions.
#
# Fix II – Over-segmented same-color line series merge
#   When _split_into_curves (lines.py) produces multiple same-color line series
#   whose x-ranges form non-overlapping tiles (i.e. together they cover one
#   contiguous x-span), they are merged back into one series.  This corrects
#   resonance / spectral-peak charts where one physical curve is split across
#   many short same-color segments.

from __future__ import annotations

from .calibrate import calibrate_panels, to_data_array
from .lines import SeriesLine
from .marks import SeriesMarks, is_sparse_on_dense
from .plot_region import detect_regions
from .model import (
    Axis,
    ChartResult,
    ChartTable,
    Path,
    Region,
    Series,
    TextSpan,
)
from .pdf_vector import load_pdf
from .primitives import MARKER_CODE as _MARKER_CODE, round_color as _round_color
from .roles import SKIP_REASON as _AMBIGUOUS_REASON, classify_roles


# A real chart yields more than a single isolated data point; <= this many
# total points across all series means an empty / noise region (e.g. one stray
# marker on a boxplot) -> skip rather than emit it as an "extraction".
_MIN_TOTAL_POINTS = 1

# A marker series of fewer than this many points is a degenerate tiny-n group
# (an annotation glyph, a "Peak" cross, a lone corner mark), not a real scatter
# series -> drop it unconditionally.
_MIN_MARKS_PER_SERIES = 3
# A "small" series (exactly this many marks) is retained when the region has at
# least one strong anchor (Fix I: faint / partially-visible series recovery).
_MIN_MARKS_SMALL = 2

# Maximum pixel-space x-gap between two same-color line series (as a fraction of
# the combined x-span) to be considered x-tiled pieces of the same physical
# curve (Fix II: over-segmented line series merge).
_LINE_TILE_GAP_FRAC = 0.05


def _filter_mark_series(
    series_marks: list[SeriesMarks],
    has_line_anchor: bool,
) -> list[SeriesMarks]:
    """Filter marker groups by mark count.

    Strong series (≥ _MIN_MARKS_PER_SERIES marks) always pass.
    Small series (== _MIN_MARKS_SMALL marks) are accepted when at least one
    strong series is present OR ``has_line_anchor`` is True (the region already
    has a clean line series as independent evidence of a real chart).  This
    recovers faint / partially-visible series that happen to show only 2 data
    points on the page while keeping annotation glyphs out of empty regions.
    """
    strong = [sm for sm in series_marks if len(sm.marks) >= _MIN_MARKS_PER_SERIES]
    small = [sm for sm in series_marks if len(sm.marks) == _MIN_MARKS_SMALL]
    if not small:
        return strong
    if strong or has_line_anchor:
        return strong + small
    return strong


def _confidence(x_axis: Axis, y_axis: Axis) -> float:
    """Confidence in [0, 1] for an extraction with both axes calibrated.

    extract_region only emits a table when BOTH axes are calibrated, so the base
    is high; we scale by the weaker axis fit quality (R^2) so a borderline
    calibration carries lower confidence than a clean one.
    """
    r2 = min(x_axis.calibration.get("r2", 1.0), y_axis.calibration.get("r2", 1.0))
    return round(max(0.0, min(1.0, r2)), 3)


def _points_to_data(xs_px, ys_px, x_axis: Axis, y_axis: Axis) -> list[dict]:
    xs = to_data_array(x_axis.calibration, xs_px)
    ys = to_data_array(y_axis.calibration, ys_px)
    return [
        {"x": float(x), "y": float(y), "x_px": float(xp), "y_px": float(yp)}
        for x, y, xp, yp in zip(xs, ys, xs_px, ys_px)
    ]


def _build_series(sm: SeriesMarks, x_axis: Axis, y_axis: Axis) -> Series:
    xs_px = [m.cx for m in sm.marks]
    ys_px = [m.cy for m in sm.marks]
    return Series(
        label=None,
        marker=_MARKER_CODE.get(sm.shape),
        color=sm.fill or sm.stroke,
        points=_points_to_data(xs_px, ys_px, x_axis, y_axis),
    )


def _build_line_series(sl: SeriesLine, x_axis: Axis, y_axis: Axis) -> Series:
    # Emit in TRUE draw order when known (sideways / folded curves are not
    # single-valued in x, so the x-sorted ``points`` would scramble them);
    # fall back to the x-sorted vertices for multi-path merges.
    verts = sl.raw_points or sl.points
    xs_px = [v[0] for v in verts]
    ys_px = [v[1] for v in verts]
    return Series(
        label=None,
        marker=None,
        color=sl.color,
        points=_points_to_data(xs_px, ys_px, x_axis, y_axis),
    )


def _merge_tiled_line_series(series: list[Series]) -> list[Series]:
    """Merge same-color line series whose x-pixel ranges form non-overlapping
    tiles (Fix II: over-segmented series from _split_into_curves).

    When ``classify_lines`` encounters x-overlapping paths of the same color it
    calls ``_split_into_curves``, which may produce multiple same-color line
    series each covering a disjoint sub-range of the x-axis.  These are pieces
    of ONE physical curve (e.g. a resonance spectrum or spectral envelope) and
    should be merged back into one series.

    Two same-color series are candidates to merge when:
      * their x-pixel ranges do NOT overlap (they tile the x-axis), AND
      * the gap between their x-ranges is at most ``_LINE_TILE_GAP_FRAC`` of the
        combined x-span (a small gap may arise from clipping or rendering).

    Merged series inherit the color of the first (widest) piece; their points are
    concatenated and sorted by x_px.  Series that do NOT share a tiling partner
    are returned unchanged.
    """
    if not series:
        return series

    # Group by rounded color.
    from collections import defaultdict
    color_groups: dict[tuple | None, list[int]] = defaultdict(list)
    for idx, s in enumerate(series):
        color_groups[_round_color(s.color)].append(idx)

    merged_indices: set[int] = set()
    result: list[Series] = []

    for color, idxs in color_groups.items():
        if len(idxs) == 1:
            continue  # nothing to merge

        # For each index compute its x_px range.
        def _xrange(s: Series) -> tuple[float, float] | None:
            xs = [p["x_px"] for p in s.points if "x_px" in p]
            return (min(xs), max(xs)) if xs else None

        candidates = [(i, _xrange(series[i])) for i in idxs]
        candidates = [(i, r) for i, r in candidates if r is not None]

        # Sort by x_lo.
        candidates.sort(key=lambda c: c[1][0])

        # Greedy tile merge: repeatedly merge adjacent non-overlapping series.
        groups: list[list[int]] = []
        for i, (idx, xr) in enumerate(candidates):
            placed = False
            for g in groups:
                # Check if idx's range tiles (doesn't overlap) with ALL members.
                g_ranges = [_xrange(series[gi]) for gi in g]
                g_ranges = [r for r in g_ranges if r is not None]
                all_lo = min(r[0] for r in g_ranges)
                all_hi = max(r[1] for r in g_ranges)
                combined_span = max(all_hi, xr[1]) - min(all_lo, xr[0])
                # Overlap check: two ranges overlap if lo1 < hi2 AND lo2 < hi1.
                overlaps_any = any(
                    r[0] < xr[1] and xr[0] < r[1] for r in g_ranges
                )
                if overlaps_any:
                    continue
                # Gap check: gap between this range and the group must be small.
                gap = max(0.0, max(xr[0] - all_hi, all_lo - xr[1]))
                if combined_span > 0 and gap / combined_span > _LINE_TILE_GAP_FRAC:
                    continue
                g.append(idx)
                placed = True
                break
            if not placed:
                groups.append([idx])

        # Only merge groups with ≥2 members.
        for g in groups:
            if len(g) < 2:
                continue
            # All members merge into one series.
            all_pts = []
            for gi in g:
                all_pts.extend(series[gi].points)
            all_pts.sort(key=lambda p: p.get("x_px", p["x"]))
            color_val = series[g[0]].color
            result.append(Series(label=None, marker=None, color=color_val,
                                 points=all_pts))
            merged_indices.update(g)

    # Add all unmerged series.
    for idx, s in enumerate(series):
        if idx not in merged_indices:
            result.append(s)
    return result


def extract_region(
    region: Region,
    axes: tuple[Axis, Axis],
    paths: list[Path],
    texts: list[TextSpan],
    source: dict | None = None,
    legend_bbox: tuple | None = None,
) -> ChartResult:
    """Extract one region into a ``ChartResult`` (extracted or skipped).

    ``legend_bbox`` is the bounding box of the detected legend (from
    ``labels.detect_labels``), used to exclude legend mini-curve markers from
    data extraction. When None, no legend-box filtering is applied.
    """
    x_axis, y_axis = axes
    if x_axis.calibration is None or y_axis.calibration is None:
        return ChartResult(status="skipped", skip_reason="axis not calibrated")

    # The true plotting box is the calibrated spine-to-spine extent of each axis
    # (NOT region.bbox, which includes axis-label margins, the legend, annotations
    # and insets). Marks/curves outside it are off-plot glyphs, not data.
    plot_box = None
    if x_axis.pixel_range is not None and y_axis.pixel_range is not None:
        plot_box = (x_axis.pixel_range[0], y_axis.pixel_range[0],
                    x_axis.pixel_range[1], y_axis.pixel_range[1])

    # Single authority decides every contested path's role (marker / curve /
    # swatch) ONCE; the marker→curve cross-talk (former marker_colors /
    # marker_centroids plumbing) now lives inside classify_roles, so a connector
    # line tracing a marker series is already suppressed and a distinct same-
    # colour line is already kept.
    region_roles = classify_roles(region, paths, texts, plot_box, legend_bbox)

    # Task 2: a region whose contested data paths are largely ambiguous (markers
    # and curves genuinely contend over the same paths) is low-confidence -> skip
    # for precision rather than emit an untrustworthy table.
    if region_roles.ambiguous:
        return ChartResult(status="skipped", skip_reason=_AMBIGUOUS_REASON)

    all_marks = region_roles.marker_series
    # Strong anchor series (≥ _MIN_MARKS_PER_SERIES): always kept.
    strong_marks = [sm for sm in all_marks if len(sm.marks) >= _MIN_MARKS_PER_SERIES]
    line_series = region_roles.line_series
    line_skips = region_roles.line_skips

    # Fix II: merge same-color line series that are x-tiled pieces of one curve.
    merged_line_series = _merge_tiled_line_series(
        [_build_line_series(sl, x_axis, y_axis) for sl in line_series]
    )

    # Fix I: accept 2-mark series when the region has an anchor (strong marker
    # or line series), avoiding lone annotation glyphs in empty regions.
    has_anchor = bool(strong_marks) or bool(merged_line_series)
    series_marks = _filter_mark_series(all_marks, has_line_anchor=has_anchor)
    series = [_build_series(sm, x_axis, y_axis) for sm in series_marks]
    series += merged_line_series

    if not series:
        reason = "no clean series found"
        if line_skips:
            reason += ": " + "; ".join(line_skips)
        return ChartResult(status="skipped", skip_reason=reason)

    # A region that yields no usable data points (or only a single isolated
    # marker) is not a real extraction -- it is an empty / noise region that
    # would otherwise be marked "extracted" with nothing in it. Skip it.
    # Precision over recall.
    n_points = sum(len(s.points) for s in series)
    if n_points <= _MIN_TOTAL_POINTS:
        return ChartResult(status="skipped", skip_reason="no data points")

    if is_sparse_on_dense(region, paths, n_points):
        return ChartResult(status="skipped", skip_reason="sparse markers on dense chart")

    table = ChartTable(
        source=source,
        region_bbox=region.bbox,
        x_axis=x_axis,
        y_axis=y_axis,
        series=series,
        confidence=_confidence(x_axis, y_axis),
    )
    diagnostics = {"line_skips": line_skips} if line_skips else {}
    return ChartResult(status="extracted", table=table, diagnostics=diagnostics)


def extract_pdf(path: str, pages: list[int] | None = None) -> list[ChartResult]:
    """Extract every detected chart in ``path`` into a list of ``ChartResult``."""
    results: list[ChartResult] = []
    for page in load_pdf(path, pages):
        regions = detect_regions(page.paths, page.texts, page.width, page.height,
                                 image_rects=page.image_rects)
        if not regions:
            continue
        panel_axes = calibrate_panels(regions, page.paths, page.texts)
        for region, axes in zip(regions, panel_axes):
            source = {
                "pdf": path,
                "page": page.page_index,
                "region_bbox": region.bbox,
            }
            results.append(
                extract_region(region, axes, page.paths, page.texts, source)
            )
    return results
