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

from __future__ import annotations

from .calibrate import calibrate_panels, to_data_array
from .lines import SeriesLine, classify_lines
from .marks import SeriesMarks, classify_marks, is_sparse_on_dense
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

# Marker class -> the matplotlib-style marker code reported on a Series.
_MARKER_CODE = {
    "circle": "o",
    "square": "s",
    "triangle": "^",
    "diamond": "D",
    "star": "*",
    "plus": "+",
    "cross": "x",
    "marker": None,
}


# A real chart yields more than a single isolated data point; <= this many
# total points across all series means an empty / noise region (e.g. one stray
# marker on a boxplot) -> skip rather than emit it as an "extraction".
_MIN_TOTAL_POINTS = 1

# A marker series of fewer than this many points is a degenerate tiny-n group
# (an annotation glyph, a "Peak" cross, a lone corner mark), not a real scatter
# series -> drop it.
_MIN_MARKS_PER_SERIES = 3


def _is_real_series(sm: SeriesMarks) -> bool:
    """Reject degenerate tiny-n marker groups (annotation glyphs) as series."""
    return len(sm.marks) >= _MIN_MARKS_PER_SERIES


def _confidence(x_axis: Axis, y_axis: Axis) -> float:
    """Confidence in [0, 1] for an extraction with both axes calibrated.

    extract_region only emits a table when BOTH axes are calibrated, so the base
    is high; we scale by the weaker axis fit quality (R^2) so a borderline
    calibration carries lower confidence than a clean one.
    """
    r2 = min(x_axis.calibration.get("r2", 1.0), y_axis.calibration.get("r2", 1.0))
    return round(max(0.0, min(1.0, r2)), 3)


def _round_color(c):
    return tuple(round(v, 2) for v in c) if c is not None else None


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
    xs_px = [v[0] for v in sl.points]
    ys_px = [v[1] for v in sl.points]
    return Series(
        label=None,
        marker=None,
        color=sl.color,
        points=_points_to_data(xs_px, ys_px, x_axis, y_axis),
    )


def extract_region(
    region: Region,
    axes: tuple[Axis, Axis],
    paths: list[Path],
    texts: list[TextSpan],
    source: dict | None = None,
) -> ChartResult:
    """Extract one region into a ``ChartResult`` (extracted or skipped)."""
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

    series_marks = classify_marks(region, paths, texts, plot_box)
    series_marks = [sm for sm in series_marks if _is_real_series(sm)]
    series = [_build_series(sm, x_axis, y_axis) for sm in series_marks]

    # Marker-less line curves: dedupe against colours already drawn as markers
    # (line+marker plots -> keep the markers), then add the clean ones.
    marker_colors = {_round_color(sm.fill or sm.stroke) for sm in series_marks}
    line_series, line_skips = classify_lines(region, paths, texts, marker_colors,
                                             plot_box)
    series += [_build_line_series(sl, x_axis, y_axis) for sl in line_series]

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
