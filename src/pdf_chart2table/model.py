"""Dataclasses for the chart->table pipeline.

M1 uses ``Path``, ``TextSpan`` and ``Region``. The remaining types
(``Tick``, ``Axis``, ``Series``, ``ChartTable``, ``ChartResult``) are
lightweight forward-looking stubs for later milestones; they are not
populated yet.

Coordinates are raw PDF points (origin top-left, y increasing downward,
matching PyMuPDF's convention).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# A bounding box as (x0, y0, x1, y1) in PDF points.
BBox = tuple[float, float, float, float]
Point = tuple[float, float]
Color = tuple[float, float, float]


# --------------------------------------------------------------------------
# M1 primitives
# --------------------------------------------------------------------------

@dataclass
class Path:
    """A single drawn path, flattened to a polyline (beziers subdivided)."""

    points: list[Point]
    stroke: Color | None
    fill: Color | None
    width: float | None
    dashes: str | None
    closed: bool
    bbox: BBox
    # Transparency (1.0 = opaque). Recovered from the PDF so semi-transparent
    # shaded regions / CI bands / markers re-render with the right alpha.
    stroke_alpha: float | None = None
    fill_alpha: float | None = None
    # True when the source stroked this path with ROUND line caps (PDF lineCap 1).
    # Recovered so thick lines / ticks re-render with rounded ends, not butt caps.
    round_cap: bool = False


@dataclass
class TextSpan:
    """A run of text with its bounding box."""

    text: str
    bbox: BBox
    size: float | None = None
    # Writing direction unit vector (dx, dy); (1, 0) is horizontal.
    dir: tuple[float, float] = (1.0, 0.0)
    # Text fill color as an (R, G, B) float tuple (0–1 each), or None when the
    # color is black / unavailable.  Used by the inline-label detector to match
    # colored annotation text against curve colors.
    color: Color | None = None
    # True when the span is drawn in an italic/oblique (non-symbol) font, so a
    # variable in a label can be re-rendered italic (e.g. legend 'M$_{s}$' -> a
    # slanted M). Symbol fonts carry the italic flag yet are not italic text, so
    # they are excluded when this is set.
    italic: bool = False
    # True when the span is drawn in a bold font -- so a bold (or bold-italic)
    # label variable is re-rendered bold (legend 'M_s' in Arial-BoldItalic).
    bold: bool = False


@dataclass
class Region:
    """A detected chart plotting area and the primitives it contains.

    For multi-panel figures, ``row``/``col`` give the panel's position in the
    row-major grid, and ``shares_x_with``/``shares_y_with`` list the indices of
    sibling regions that align into the same column (shared x axis) or row
    (shared y axis). Single-panel pages have row=col=0 and empty sibling lists.

    ``skip_reason`` is set (non-None) when the chart-type gate identifies the
    region as a non-line/scatter chart type (e.g. contour map, dispersion
    lattice, credible band) that must not be extracted. The region is returned
    by ``detect_regions`` so that the caller can emit a skip stub for it rather
    than silently discarding it.
    """

    bbox: BBox
    path_indices: list[int] = field(default_factory=list)
    text_indices: list[int] = field(default_factory=list)
    row: int = 0
    col: int = 0
    shares_x_with: list[int] = field(default_factory=list)
    shares_y_with: list[int] = field(default_factory=list)
    skip_reason: str | None = None


@dataclass
class PageData:
    """Normalized primitives for one PDF page."""

    page_index: int
    width: float
    height: float
    paths: list[Path]
    texts: list[TextSpan]
    # Bounding boxes of embedded raster images placed on the page. Used to reject
    # "markers on a photo" regions (a raster image with vector markers drawn over
    # it is not a vector chart).
    image_rects: list[BBox] = field(default_factory=list)


# --------------------------------------------------------------------------
# Forward-looking stubs (populated in later milestones)
# --------------------------------------------------------------------------

@dataclass
class Tick:
    pixel: float
    value: float | None = None
    label: str | None = None


@dataclass
class Axis:
    title: str | None = None
    scale: str = "linear"  # "linear" | "log"
    pixel_range: tuple[float, float] | None = None
    data_range: tuple[float, float] | None = None
    ticks: list[Tick] = field(default_factory=list)
    calibration: dict | None = None
    # Which way the tick marks point relative to the spine: "in" | "out" | None.
    tick_direction: str | None = None
    # Median tick-mark length in PDF points (perpendicular extent), or None.
    tick_length: float | None = None


@dataclass
class Series:
    label: str | None = None
    marker: str | None = None
    color: Color | None = None
    points: list[dict] = field(default_factory=list)
    # Dash form of the source line: the raw PDF dash string, or "dashed" when a
    # dash was RECOVERED from a curve drawn as many gapped collinear fragments
    # (a dashed fit rasterised into short solid sub-strokes -- see
    # lines._recovered_dashes). None for a continuous solid curve / a marker
    # series. Carried for style-faithful reconstruction AND serialized to the
    # JSON record (downstream consumers use the dash as a fit/guide signal).
    dashes: str | None = None
    # Role of the series' ink: "data" (read-off data points / a data curve),
    # "fit" (a fit / trend / guide line drawn through or over the data), or
    # "uncertain" (a line whose role cannot be decided from geometry alone --
    # e.g. a lone solid straight line, which may be genuine linear data or a
    # reference line). Marker series are always "data"; line series are tagged
    # by refiners.drop_spurious_lines (marker-present charts) and
    # refiners.classify_line_roles (final pass, pure-line charts). Serialized so
    # downstream dataset builders can keep marker-less DATA curves instead of
    # dropping every marker=None series as a suspected fit line.
    role: str | None = None


@dataclass
class ChartTable:
    source: dict | None = None
    region_bbox: BBox | None = None
    title: str | None = None
    caption: str | None = None
    x_axis: Axis | None = None
    y_axis: Axis | None = None
    series: list[Series] = field(default_factory=list)
    confidence: float = 0.0


@dataclass
class ChartResult:
    status: str = "skipped"  # "extracted" | "skipped"
    table: ChartTable | None = None
    skip_reason: str | None = None
    diagnostics: dict = field(default_factory=dict)
