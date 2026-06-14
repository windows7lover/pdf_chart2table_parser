"""Tests for marker-less line-curve extraction (lines.classify_lines).

Builds synthetic ``Path`` objects in a region and asserts the precision rules:
saturated off-axis open polylines become line series; gridlines / spines /
dashed / border / legend-swatch / marker-colour curves are dropped; overlapping
same-colour curves are skipped (logged), never guessed. Also checks the
end-to-end line+marker dedupe stays at the correct series count on a fixture.
"""

from __future__ import annotations

from pathlib import Path as FsPath

from pdf_chart2table.lines import classify_lines
from pdf_chart2table.model import Path, Region, TextSpan
from pdf_chart2table.extract import extract_pdf

FIXTURES = FsPath(__file__).parent / "fixtures"

# A region spanning x in [100, 300], y in [100, 300].
REGION = Region(bbox=(100.0, 100.0, 300.0, 300.0), path_indices=[], text_indices=[])


def _poly(pts, stroke, *, closed=False, dashes=None, fill=None, width=1.0):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return Path(points=pts, stroke=stroke, fill=fill, width=width,
                dashes=dashes, closed=closed,
                bbox=(min(xs), min(ys), max(xs), max(ys)))


def _classify(paths, texts=None):
    texts = texts or []
    region = Region(bbox=REGION.bbox,
                    path_indices=list(range(len(paths))),
                    text_indices=list(range(len(texts))))
    return classify_lines(region, paths, texts, marker_colors=set())


def test_clean_saturated_curve_extracted():
    blue = _poly([(120, 250), (170, 200), (220, 170), (270, 160)], (0.0, 0.0, 1.0))
    series, reasons = _classify([blue])
    assert len(series) == 1 and not reasons
    assert series[0].color == (0.0, 0.0, 1.0)
    assert series[0].points[0][0] == 120  # sorted by x


def test_gridline_and_spine_rejected():
    # Axis-aligned, ~constant-in-one-axis strokes are gridlines/spines: rejected
    # whether gray or black, solid or multi-vertex (they don't vary in 2-D).
    gray_grid = _poly([(120, 200), (280, 200)], (0.8, 0.8, 0.8))   # gray, not saturated
    black_grid = _poly([(120, 220), (200, 220), (280, 220)], (0.0, 0.0, 0.0))  # black, flat
    series, _ = _classify([gray_grid, black_grid])
    assert series == []


def test_closed_rejected():
    closed = _poly([(120, 150), (180, 150), (180, 200), (120, 150)], (0.0, 1.0, 0.0), closed=True)
    series, _ = _classify([closed])
    assert series == []


def test_dashed_saturated_curve_extracted():
    # A long dashed saturated polyline IS a data curve (dashed series).
    dashed = _poly([(120, 250), (200, 200), (280, 170)], (1.0, 0.0, 0.0), dashes="[2 2] 0")
    series, reasons = _classify([dashed])
    assert len(series) == 1 and not reasons
    assert series[0].color == (1.0, 0.0, 0.0)
    assert series[0].dashes == "[2 2] 0"


def test_dash_recovered_from_gapped_fragments():
    # 2205.10303 pattern: a dashed fit is drawn as MANY short SOLID fragments
    # (each dashes=None), so the merged curve would look solid. classify_lines
    # must RECOVER a "dashed" signal onto it (so the renderer draws it dashed).
    blue = (0.0, 0.0, 1.0)
    frags = [_poly([(120 + 6 * i, 250 - 3 * i), (122 + 6 * i, 249 - 3 * i)], blue)
             for i in range(12)]  # 12 short gapped collinear solid fragments
    series, _ = _classify(frags)
    assert len(series) == 1
    assert series[0].dashes == "dashed", "gapped-fragment fit must be tagged dashed"


def test_continuous_solid_curve_stays_solid():
    # A single continuous solid polyline is NOT dash-recovered (no fragmentation).
    solid = _poly([(120 + 4 * i, 250 - 2 * i) for i in range(20)], (0.0, 0.0, 1.0))
    series, _ = _classify([solid])
    assert len(series) == 1
    assert series[0].dashes is None, "a continuous solid curve must stay solid"


def test_dashed_black_curve_extracted():
    # An unsaturated (black) curve is kept ONLY when dashed (not a solid grid).
    dashed = _poly([(120, 250), (200, 200), (280, 170)], (0.0, 0.0, 0.0), dashes="[3 3] 0")
    series, _ = _classify([dashed])
    assert len(series) == 1
    assert series[0].color == (0.0, 0.0, 0.0)


def _dense_segment(x0, x1, y0, y1, stroke, *, n=60):
    """A dense OPEN sub-segment of a wiggly curve: ~n vertices wandering in y as
    x advances from x0 to x1 (endpoints far apart -> not a closed glyph loop).
    Mimics the 2208 case where a smooth curve is emitted as many small dense
    open polylines that each span a few px in x but carry ~100 vertices."""
    import math
    pts = []
    for k in range(n):
        t = k / (n - 1)
        x = x0 + (x1 - x0) * t
        # wiggle in y but trend from y0 to y1 so start != end
        y = y0 + (y1 - y0) * t + 1.5 * math.sin(8 * math.pi * t)
        pts.append((x, y))
    return _poly(pts, stroke)


def test_dense_tiling_segments_become_one_line_not_markers():
    # Regression for 2208.14630_p20c2: a wiggly curve emitted as MANY small dense
    # open segments tiling the x-axis must extract as ONE line series (each piece
    # is too dense to be a fragment and too short to be a long curve on its own).
    red = (1.0, 0.0, 0.0)
    seg_w = 12.0
    segs = [
        _dense_segment(120 + i * seg_w, 120 + (i + 1) * seg_w,
                       250 - i * 8, 250 - (i + 1) * 8, red)
        for i in range(12)  # tile x across most of the region width
    ]
    series, _ = _classify(segs)
    assert len(series) == 1, f"expected 1 merged line, got {len(series)}"
    assert series[0].color == red
    # the merged curve spans the tiled segments, not just one piece
    assert series[0].points[0][0] < 130 and series[0].points[-1][0] > 250


def test_few_dense_segments_do_not_fabricate_a_line():
    # Guard: a couple of stray dense open paths (not enough to tile a wide span)
    # must NOT be fabricated into a curve.
    red = (1.0, 0.0, 0.0)
    segs = [
        _dense_segment(120, 132, 250, 242, red),
        _dense_segment(132, 144, 242, 234, red),
    ]
    series, _ = _classify(segs)
    assert series == []


def test_solid_black_axis_aligned_rejected():
    # A solid black multi-vertex stroke that is FLAT (axis-aligned, ~constant y)
    # is a gridline/spine, not a curve: it does not vary in 2-D.
    black = _poly([(120, 220), (200, 220), (280, 220)], (0.0, 0.0, 0.0))
    series, _ = _classify([black])
    assert series == []


def _descending(x0, y0, color, *, n=10, dx=16, dy=-9):
    return _poly([(x0 + k * dx, y0 + k * dy) for k in range(n)], color)


def test_solid_black_interior_curve_extracted():
    # A SOLID black open polyline in the plot interior that varies in BOTH x and
    # y is a real data curve (e.g. a ResNet/optimizer black line) and is kept.
    black = _descending(120, 290, (0.0, 0.0, 0.0))
    series, reasons = _classify([black])
    assert len(series) == 1 and not reasons
    assert series[0].color == (0.0, 0.0, 0.0)
    assert series[0].points[0][0] == 120  # x-sorted


def test_solid_gray_interior_curve_extracted():
    # A SOLID gray open polyline that bends in 2-D is data too (gray ResNet line).
    gray = _descending(120, 285, (0.6, 0.6, 0.6))
    series, reasons = _classify([gray])
    assert len(series) == 1 and not reasons


def test_small_gray_box_not_emitted():
    # A small few-vertex gray box / legend frame (low span, few vertices) is NOT
    # a data curve: precision on the ambiguous low-saturation case.
    box = _poly([(150, 150), (180, 150), (180, 175), (150, 175), (150, 150)],
                (0.7, 0.7, 0.7))
    series, _ = _classify([box])
    assert series == []


def test_solid_gray_vertical_gridline_rejected():
    # A gray near-vertical full-height stroke (a gridline) must NOT be emitted as
    # data even with many vertices: it does not vary in x (axis-aligned).
    grid = _poly([(200, 120 + k * 18) for k in range(9)], (0.75, 0.75, 0.75))
    series, _ = _classify([grid])
    assert series == []


def test_black_dotted_fragments_recovered():
    # Many short black 2-D segments (a black dotted curve) join into one curve,
    # even though they are unsaturated -- they vary in 2-D, so not gridlines.
    frags = []
    for k in range(10):
        x = 120 + k * 16
        y = 250 - k * 8
        frags.append(_poly([(x, y), (x + 6, y - 4)], (0.0, 0.0, 0.0)))
    series, reasons = _classify(frags)
    assert len(series) == 1 and not reasons
    assert len(series[0].points) >= 8


def test_black_axis_aligned_fragments_not_a_curve():
    # Short black HORIZONTAL segments (dashed gridline pieces) are axis-aligned,
    # so they never join into a data curve.
    frags = [_poly([(120 + k * 16, 200), (120 + k * 16 + 6, 200)], (0.0, 0.0, 0.0))
             for k in range(10)]
    series, _ = _classify(frags)
    assert series == []


def test_noise_cloud_candidate_skipped():
    # One saturated open path whose vertices are a multivalued scatter cloud (no
    # x-ordering) is not a coherent curve and is dropped.
    pts = [(120, 250), (130, 130), (140, 240), (150, 140), (160, 250),
           (170, 135), (180, 245), (190, 150), (200, 250), (210, 130)]
    cloud = _poly(pts, (1.0, 0.0, 0.0))
    series, _ = _classify([cloud])
    assert series == []


def test_fragments_joined_into_one_curve():
    # Many short saturated segments (a dash-dot curve) join into one polyline.
    frags = []
    for k in range(10):
        x = 120 + k * 16
        y = 250 - k * 8
        frags.append(_poly([(x, y), (x + 6, y - 4)], (1.0, 0.0, 1.0)))
    series, reasons = _classify(frags)
    assert len(series) == 1 and not reasons
    assert len(series[0].points) >= 8
    xs = [p[0] for p in series[0].points]
    assert xs == sorted(xs)  # x-ordered


def test_scattered_fragments_skipped():
    # Saturated 2-point segments scattered with no single-valued structure are
    # NOT joined into a curve (clean-or-skip).
    frags = [
        _poly([(120, 250), (122, 130)], (1.0, 0.0, 1.0)),
        _poly([(140, 130), (142, 250)], (1.0, 0.0, 1.0)),
        _poly([(160, 250), (162, 130)], (1.0, 0.0, 1.0)),
        _poly([(180, 130), (182, 250)], (1.0, 0.0, 1.0)),
        _poly([(200, 250), (202, 130)], (1.0, 0.0, 1.0)),
    ]
    series, _ = _classify(frags)
    assert series == []


def test_border_segment_rejected():
    # A saturated segment riding the bottom spine (y == region y1) is a tick/frame.
    on_axis = _poly([(120, 300), (200, 300), (280, 300)], (1.0, 0.0, 0.0))
    series, _ = _classify([on_axis])
    assert series == []


def test_legend_swatch_rejected():
    text = TextSpan(text="series A", bbox=(250.0, 195.0, 290.0, 205.0))
    swatch = _poly([(215, 200), (235, 200), (245, 200)], (1.0, 0.0, 0.0))
    series, _ = _classify([swatch], [text])
    assert series == []


def test_overlapping_same_color_split_into_two():
    # Two same-colour paths that overlap in x but have DISTINCT y-trajectories
    # are split into separate single-path groups; each forms its own curve.
    a = _poly([(120, 250), (160, 220), (200, 200)], (1.0, 0.0, 0.0))
    b = _poly([(150, 180), (190, 200), (230, 230)], (1.0, 0.0, 0.0))  # overlaps a in x
    series, reasons = _classify([a, b])
    # Both are valid curves with distinct y-trajectories -> both kept.
    assert len(series) == 2 and not reasons


def test_overlapping_same_color_truly_ambiguous_skipped():
    # Three same-colour paths where all three overlap in x: the greedy split
    # still places each in its own group (each single-path sub-group is valid).
    # But if a fourth overlapping path cannot be placed without conflicting an
    # already-placed group member, the residual group gets rejected.
    # This minimal case has two overlapping paths: each ends up its own group.
    a = _poly([(120, 250), (160, 220), (200, 200)], (1.0, 0.0, 0.0))
    b = _poly([(120, 180), (160, 200), (200, 220)], (1.0, 0.0, 0.0))
    series, _ = _classify([a, b])
    # Each path forms its own x-compatible group -> 2 distinct curves kept.
    assert len(series) == 2


def test_disjoint_same_color_tiles_one_curve():
    a = _poly([(120, 250), (160, 220), (200, 200)], (0.0, 0.0, 1.0))
    b = _poly([(205, 190), (245, 180), (285, 175)], (0.0, 0.0, 1.0))  # disjoint in x
    series, reasons = _classify([a, b])
    assert len(series) == 1 and not reasons
    assert len(series[0].points) == 6


def test_same_color_solid_and_dashed_distinct_trajectories_both_kept():
    # A solid Testing curve and a dashed Training curve in the SAME colour but on
    # different y-trajectories: both are genuine curves and must be kept.
    solid = _poly([(120, 250), (170, 240), (220, 235), (270, 233)], (0.0, 0.0, 1.0))
    dashed = _poly([(120, 180), (170, 160), (220, 150), (270, 145)], (0.0, 0.0, 1.0),
                   dashes="[2 2] 0")
    series, reasons = _classify([solid, dashed])
    assert len(series) == 2 and not reasons
    forms = {s.dashes for s in series}
    assert forms == {None, "[2 2] 0"}


def test_same_color_solid_and_dashed_overlapping_one_kept():
    # The SAME curve drawn once solid and once dashed (overlapping geometry):
    # dedup to a single series.
    pts = [(120, 250), (170, 200), (220, 170), (270, 160)]
    solid = _poly(pts, (1.0, 0.0, 0.0))
    dashed = _poly([(x, y + 0.5) for x, y in pts], (1.0, 0.0, 0.0), dashes="[2 2] 0")
    series, reasons = _classify([solid, dashed])
    assert len(series) == 1 and not reasons


def test_out_of_box_curve_tail_clipped_and_kept():
    # A curve that lies in-box across the plot but trails OUT of the box on the
    # right keeps its in-box vertices and drops the out-of-box tail.
    plot_box = (110.0, 110.0, 290.0, 290.0)
    pts = [(120, 250), (170, 220), (220, 200), (270, 180),  # in box
           (360, 120), (420, 90)]                            # out of box
    blue = _poly(pts, (0.0, 0.0, 1.0))
    region = Region(bbox=REGION.bbox, path_indices=[0], text_indices=[])
    series, _ = classify_lines(region, [blue], [], plot_box=plot_box)
    assert len(series) == 1
    assert all(110 <= x <= 295 for x, _ in series[0].points)


def test_mostly_out_of_box_connector_dropped():
    # A connector/axis diagonal lying mostly OUTSIDE the box is dropped entirely.
    plot_box = (110.0, 110.0, 290.0, 290.0)
    pts = [(120, 280), (300, 100), (400, 60), (500, 40), (600, 20)]  # 1 in box
    diag = _poly(pts, (1.0, 0.0, 0.0))
    region = Region(bbox=REGION.bbox, path_indices=[0], text_indices=[])
    series, _ = classify_lines(region, [diag], [], plot_box=plot_box)
    assert series == []


def test_baseline_spine_line_rejected():
    # A horizontal curve hugging the bottom edge of the plot box (a zero/baseline)
    # is an axis line, not data.
    plot_box = (110.0, 110.0, 290.0, 290.0)
    base = _poly([(120, 289), (180, 290), (240, 289), (285, 290)], (0.0, 0.0, 1.0))
    region = Region(bbox=REGION.bbox, path_indices=[0], text_indices=[])
    series, _ = classify_lines(region, [base], [], plot_box=plot_box)
    assert series == []


def test_plot_box_frame_rejected():
    # The rectangular plot frame -- or the four spines joined as one path with
    # corner jumps -- has every vertex on a box edge and spans the whole box; it
    # is not data (2003.00176: the frame leaked in as black "series").
    plot_box = (110.0, 110.0, 290.0, 290.0)
    frame = _poly([(110, 110), (110, 290), (290, 110), (290, 290), (110, 110)],
                  (0.0, 0.0, 0.0))
    region = Region(bbox=REGION.bbox, path_indices=[0], text_indices=[])
    series, _ = classify_lines(region, [frame], [], plot_box=plot_box)
    assert series == []


def test_full_box_diagonal_data_line_kept():
    # A genuine curve spanning the box but with INTERIOR vertices (off every edge)
    # must NOT be mistaken for a frame.
    plot_box = (110.0, 110.0, 290.0, 290.0)
    curve = _poly([(115, 285), (160, 210), (210, 170), (285, 150)],
                  (0.0, 0.0, 1.0))
    region = Region(bbox=REGION.bbox, path_indices=[0], text_indices=[])
    series, _ = classify_lines(region, [curve], [], plot_box=plot_box)
    assert len(series) == 1


def test_marker_color_deduped():
    region = Region(bbox=REGION.bbox, path_indices=[0], text_indices=[])
    blue = _poly([(120, 250), (170, 200), (220, 170)], (0.0, 0.0, 1.0))
    series, _ = classify_lines(region, [blue], [], marker_colors={(0.0, 0.0, 1.0)})
    assert series == []


def test_lowsat_filled_fragment_not_merged_as_line():
    """Small fragments whose stroke is low-saturation (black/gray) AND whose fill
    is non-None are marker glyph outlines, NOT curve segments.  Many such fragments
    must NOT be merged into a spurious line series."""
    # Simulate 12 black-stroked, colored-fill square marker outlines (5 pts each, 4x4 px)
    # spread across x: [120..280].  Without the guard they would be collected as
    # fragments and merged into a 60-pt "black line".
    frags = []
    for k in range(12):
        x = 120 + k * 14
        y = 250 - k * 6
        frags.append(_poly([(x, y), (x+4, y), (x+4, y+4), (x, y+4), (x, y)],
                           (0.0, 0.0, 0.0), fill=(1.0, 0.0, 0.0)))
    series, _ = _classify(frags)
    assert series == [], "Black-stroke colored-fill marker outlines must not become a line"


def test_flat_near_bottom_edge_curve_rejected():
    """A long, nearly-flat (y-span < 2 % of chart height) curve that also sits
    within 3 % of the bottom edge is a zero-floor artefact and must be dropped.
    A real data curve with similar x-span but proper vertical variation is kept."""
    plot_box = (110.0, 110.0, 290.0, 290.0)  # height = 180 px

    # Zero-floor artefact: 213 pts, all at y ≈ 286 (2.2 % from bottom at y=290,
    # within the 3 % edge band = 5.4 px).
    # y-span = 0.15 px  (<< 2 % * 180 = 3.6 px) -- essentially horizontal.
    flat_pts = [(110 + k * 0.85, 286.0 + (k % 3) * 0.05) for k in range(213)]
    flat = _poly(flat_pts, (0.8, 0.47, 0.65))
    region = Region(bbox=REGION.bbox, path_indices=[0, 1], text_indices=[])

    # Real data curve with similar extent but genuine y variation:
    real_pts = [(120 + k * 1.5, 250 - k * 5) for k in range(50)]
    real = _poly(real_pts, (0.0, 0.45, 0.7))

    series, _ = classify_lines(region, [flat, real], [], plot_box=plot_box)
    colors = [s.color for s in series]
    assert (0.8, 0.47, 0.65) not in colors, "Flat near-bottom artefact must be rejected"
    assert any(abs(c[2] - 0.7) < 0.01 for c in colors if c), "Real data curve must be kept"


def test_flat_data_series_not_at_spine_kept():
    """A genuinely flat data series (like a Thomson-opacity plateau) that sits
    close to the bottom of the chart but NOT within the 3 % spine-edge band must
    NOT be suppressed.  This is the regression guard for 2606.02726 chart 1 where
    the pink flat line (y ~4.6 % from the bottom spine) was incorrectly dropped.

    Two guards must pass:
      1. _is_noise_cloud: a flat curve with yspan < 2 px must not be flagged as
         a scatter cloud even if adjacent-y jumps exceed 0.5 * yspan.
      2. _is_spine_line: the flat-and-near check only fires within 3 % of an
         edge; 4.6 % is outside that band, so the series must survive."""
    plot_box = (110.0, 110.0, 290.0, 290.0)  # height = 180 px
    # Flat plateau at y ≈ 281.7, bottom of box at y=290; distance = 8.3 px = 4.6 %
    # of height.  Use cyclic y (matches the 2606.02726 rendering) so _is_noise_cloud
    # sub-pixel jumps are covered -- the fix skips the cloud check when yspan < 2 px.
    flat_data_pts = [(110 + k * 0.85, 281.7 + (k % 3) * 0.05) for k in range(213)]
    flat_data = _poly(flat_data_pts, (0.8, 0.47, 0.65))
    region = Region(bbox=REGION.bbox, path_indices=[0], text_indices=[])
    series, _ = classify_lines(region, [flat_data], [], plot_box=plot_box)
    colors = [s.color for s in series]
    assert (0.8, 0.47, 0.65) in colors, "Flat data plateau outside 3 % edge band must be kept"


def test_dashed_near_vertical_connector_rejected():
    """A dashed path whose height is much larger than its width (near-vertical,
    e.g. an errorbar or state-transition connector between stacked data points)
    must NOT be emitted as a data series.  Real dashed data series are roughly
    horizontal or gently diagonal, not near-vertical."""
    plot_box = (110.0, 110.0, 290.0, 290.0)
    # Near-vertical dashed connector: bw=15, bh=50 -> bh/bw = 3.3 > _NEAR_VERT_RATIO=2.0
    near_vert_pts = [(120, 200), (123, 210), (121, 220), (122, 230),
                     (120, 240), (121, 250), (122, 260)]
    connector = _poly(near_vert_pts, (1.0, 0.27, 0.0), dashes="[2 2] 0")
    region = Region(bbox=REGION.bbox, path_indices=[0], text_indices=[])
    series, _ = classify_lines(region, [connector], [], plot_box=plot_box)
    assert series == [], "Near-vertical dashed connector must be rejected"


def test_dashed_diagonal_curve_kept():
    """A dashed curve that is diagonal (roughly equal x and y extents) must be
    kept — it is a real data series, not a near-vertical connector."""
    plot_box = (110.0, 110.0, 290.0, 290.0)
    # Diagonal dashed series: bw≈90, bh≈50 -> bh/bw ≈ 0.56 < 2.0 -> keep
    diag_pts = [(120, 150), (140, 160), (160, 175), (180, 185),
                (200, 195), (220, 200), (240, 205)]
    diag = _poly(diag_pts, (1.0, 0.27, 0.0), dashes="[2 2] 0")
    region = Region(bbox=REGION.bbox, path_indices=[0], text_indices=[])
    series, _ = classify_lines(region, [diag], [], plot_box=plot_box)
    assert len(series) == 1, "Diagonal dashed curve must be kept"


def test_dashed_steep_peak_kept_not_connector():
    """Regression (2504.16333_p32c4 blue series): a sharp, tall, narrow DASHED
    data curve (a peak rising then falling) is near-vertical in bbox
    (bh/bw > _NEAR_VERT_RATIO) but reverses direction in y, so it is NOT a
    connector and must be kept. Previously the near-vertical guard mis-rejected
    it, leaving the dominant peak unextracted."""
    plot_box = (110.0, 110.0, 290.0, 290.0)
    # Peak: x 150..186 (bw=36), y from 280 up to 130 and back down (bh=150);
    # bh/bw ≈ 4.2 >> 2.0, but the y-step reverses at the apex (non-monotone).
    peak_pts = [(150, 280), (156, 240), (162, 180), (168, 130),
                (174, 175), (180, 235), (186, 280)]
    peak = _poly(peak_pts, (0.0, 0.0, 0.8), dashes="[1 1] 0")
    region = Region(bbox=REGION.bbox, path_indices=[0], text_indices=[])
    series, _ = classify_lines(region, [peak], [], plot_box=plot_box)
    assert len(series) == 1, "Tall narrow dashed peak must be kept, not dropped"


def test_saturated_full_span_dashed_gridline_rejected():
    """Regression (2504.16333_p32c4 blue grid): full-width axis-aligned DASHED
    segments in a saturated colour are GRIDLINES, not dash fragments. Many of
    them must NOT merge into a fake horizontal/diagonal 'series'. Previously the
    fragment path accepted them because they are saturated, fabricating a blue
    series from the grid-line endpoints."""
    # Five blue dashed horizontal rules spanning ~the full region width (200px).
    grid = [_poly([(105, 140 + k * 30), (295, 140 + k * 30)],
                  (0.0, 0.0, 0.8), dashes="[1 1] 0", width=0.25)
            for k in range(5)]
    series, _ = _classify(grid)
    assert series == [], "Full-span saturated dashed gridlines must be rejected"


def test_line_plus_marker_fixture_no_duplicate_series():
    # convergence_semilogy_3 is a line+marker chart with 3 series; the lines
    # share the markers' colours, so dedupe must keep exactly 3 series.
    results = [r for r in extract_pdf(str(FIXTURES / "convergence_semilogy_3.pdf"))
               if r.status == "extracted"]
    assert len(results) == 1
    assert len(results[0].table.series) == 3
    assert all(s.marker is not None for s in results[0].table.series)


def test_dotted_3styles_three_series_not_five():
    """dotted_3styles has 3 series (solid+o, dashed+s, dotted+^); the dashed and
    dotted connector lines coincide with their respective marker trajectories and
    must be suppressed, yielding exactly 3 marker series (not 5)."""
    results = [r for r in extract_pdf(str(FIXTURES / "dotted_3styles.pdf"))
               if r.status == "extracted"]
    assert len(results) == 1
    assert len(results[0].table.series) == 3
    # All 3 series must carry markers (they are line+marker series).
    assert all(s.marker is not None for s in results[0].table.series)


def test_four_dashed_semilogy_four_series_not_eight():
    """four_dashed_semilogy has 4 dashed+marker series; dashed connectors
    coincide with their markers and must be suppressed, yielding exactly 4
    series (not 8)."""
    results = [r for r in extract_pdf(str(FIXTURES / "four_dashed_semilogy.pdf"))
               if r.status == "extracted"]
    assert len(results) == 1
    assert len(results[0].table.series) == 4
    # All 4 series carry markers.
    assert all(s.marker is not None for s in results[0].table.series)


def test_dashed_same_color_two_series_kept():
    """dashed_same_color has 2 same-colour series (solid+o and dashed+o).
    Both marker trajectories differ in y; the solid connector is suppressed
    (geometric coincidence, 1:1 marker:vertex ratio in the solid trajectory),
    while the dashed connector is kept as a distinct series because the combined
    marker centroid count is 2× the vertex count (multitrack guard).
    Result: 2 series (one combined marker group + one dashed line)."""
    results = [r for r in extract_pdf(str(FIXTURES / "dashed_same_color.pdf"))
               if r.status == "extracted"]
    assert len(results) == 1
    assert len(results[0].table.series) == 2


def test_connector_suppressed_when_one_of_three_markers_missed():
    """Connector suppression must succeed even when 1 of 3 marker positions was
    missed by mark detection (frac = 2/3 ≈ 0.667).

    Scenario: an orange line+marker chart with 3 data points.  Mark detection
    finds only 2 of the 3 orange markers (the 3rd is missed).  The line
    connecting those 3 points is still a connector (2/3 vertices within
    _COINCIDE_TOL of a centroid), so it must be suppressed.

    A separate real blue line (different colour, no marker proximity) is
    checked to ensure the looser threshold does not cause over-suppression.
    """
    orange = (1.0, 0.5, 0.0)
    blue = (0.0, 0.0, 1.0)

    # The connecting line passes through 3 data points.
    connector = _poly([(120, 250), (200, 200), (280, 170)], orange)
    # Only 2 of the 3 orange marker centroids detected (3rd at x=280 was missed).
    centroids_orange = [(120, 250), (200, 200)]   # frac = 2/3 ≈ 0.667

    # A genuine blue line with no proximity to the orange markers.
    real_line = _poly([(120, 180), (200, 160), (280, 150)], blue)

    region = Region(
        bbox=REGION.bbox,
        path_indices=[0, 1],
        text_indices=[],
    )
    series, _ = classify_lines(
        region,
        [connector, real_line],
        [],
        marker_colors={orange},
        marker_centroids={orange: centroids_orange},
    )
    colors = {s.color for s in series}
    assert orange not in colors, "Connector with 2/3 marker proximity must be suppressed"
    assert blue in colors, "Genuine blue line must be kept"


def test_interleaved_diff_width_curves_separated_by_style_key():
    """Two same-colour curves of DIFFERENT WIDTH, each drawn as x-disjoint
    segments that interleave along x. Keyed only by (colour, dash) their segments
    merge into one zig-zag (rejected as a cloud) -> both lost; keyed by
    (colour, dash, WIDTH) each weight groups into its own clean curve -> both kept.
    """
    blue = (0.0, 0.0, 1.0)
    thin1 = _poly([(105, 250), (115, 246), (135, 240), (145, 234)], blue, width=1.0)
    thin2 = _poly([(205, 218), (215, 214), (235, 208), (245, 202)], blue, width=1.0)
    thick1 = _poly([(155, 140), (165, 144), (185, 152), (195, 156)], blue, width=2.6)
    thick2 = _poly([(255, 168), (265, 172), (285, 180), (295, 184)], blue, width=2.6)
    series, _ = _classify([thin1, thick1, thin2, thick2])
    assert len(series) == 2, [s.width for s in series]
    assert {round(s.width, 1) for s in series} == {1.0, 2.6}


def test_raw_points_preserve_draw_order_when_x_sort_would_scramble():
    """A single-path curve drawn right-to-left (or folded) must keep its TRUE
    draw order in ``raw_points`` even though ``points`` stays x-sorted for the
    internal geometry checks. Reproduces the sideways/folded-curve scramble
    (2212.10848 pDOS-vs-Freq, 2212.05730 S-curve) where x-sorting the vertices
    destroys the real connection order.
    """
    # same geometry as test_clean_saturated_curve_extracted but drawn R->L:
    red = _poly([(270, 160), (220, 170), (170, 200), (120, 250)], (1.0, 0.0, 0.0))
    series, reasons = _classify([red])
    assert len(series) == 1 and not reasons
    s = series[0]
    # points: x-sorted (unchanged behaviour the internal analysis relies on)
    assert s.points[0][0] == 120 and s.points[-1][0] == 270
    # raw_points: the source polyline's true order, NOT x-sorted
    assert s.raw_points, "single-path curve must carry raw draw order"
    assert s.raw_points[0][0] == 270 and s.raw_points[-1][0] == 120
    assert [p[0] for p in s.raw_points] != [p[0] for p in s.points]


# ---------------------------------------------------------------------------
# Regression: small open marker glyphs (×, +, open □, △) must NOT be collected
# into a fake line series; a real data line is still extracted.
# Repro: 2202.08374_p4c4 (× glyphs -> 40-pt zig-zag) and 2302.01559_p38c3
# (open squares -> 35-pt staircase).
# ---------------------------------------------------------------------------

def _cross_glyph(cx, cy, *, stroke, half=0.7):
    """A small ``×`` marker: two diagonal strokes -> 4 corner-anchored vertices."""
    pts = [(cx - half, cy - half), (cx + half, cy + half),
           (cx - half, cy + half), (cx + half, cy - half)]
    return _poly(pts, stroke)


def _open_square_glyph(cx, cy, *, stroke, half=0.8):
    pts = [(cx - half, cy - half), (cx + half, cy - half), (cx + half, cy + half),
           (cx - half, cy + half), (cx - half, cy - half)]
    return _poly(pts, stroke)


def test_cross_glyphs_not_collected_into_line():
    """A row of small ``×`` cross glyphs in one colour must NOT become a line
    series (they are markers, owned by marks.py)."""
    gray = (0.25, 0.25, 0.25)
    glyphs = [_cross_glyph(120 + 12 * i, 200 - 3 * i, stroke=gray) for i in range(10)]
    series, _ = _classify(glyphs)
    assert series == [], f"cross glyphs must not form a line, got {len(series)}"


def test_open_square_glyphs_not_collected_into_line():
    """A row of small open square glyphs (fill=None, black) must NOT become a
    line series (repro of the 35-pt black staircase in 2302.01559_p38c3)."""
    black = (0.0, 0.0, 0.0)
    # Alternate y (peaks/troughs) so a fake merge would look like a zig-zag.
    glyphs = [_open_square_glyph(120 + 15 * i, 200 if i % 2 else 150, stroke=black)
              for i in range(8)]
    series, _ = _classify(glyphs)
    assert series == [], f"open squares must not form a line, got {len(series)}"


def test_real_data_line_still_extracted_alongside_glyphs():
    """A genuine solid data polyline IS still extracted even when small marker
    glyphs of another colour are present (the glyph exclusion is shape-targeted,
    not a blanket short-path drop)."""
    blue = (0.0, 0.0, 1.0)
    line = _poly([(120, 250), (160, 230), (200, 200), (240, 175), (280, 160)], blue)
    gray = (0.25, 0.25, 0.25)
    glyphs = [_cross_glyph(120 + 12 * i, 210 - 2 * i, stroke=gray) for i in range(8)]
    series, _ = _classify([line] + glyphs)
    assert len(series) == 1, f"expected only the real line, got {len(series)}"
    assert series[0].color == blue


def test_short_dash_fragment_still_a_line():
    """A genuine dash-fragment curve (short elongated saturated segments tiling
    the x-axis) is still merged into a line — the glyph exclusion must not catch
    elongated (monotone) dash fragments."""
    red = (0.9, 0.1, 0.1)
    # Short, elongated, x-monotone dash segments tiling left->right.
    frags = [_poly([(120 + 20 * i, 250 - 4 * i), (134 + 20 * i, 247 - 4 * i)], red)
             for i in range(8)]
    series, _ = _classify(frags)
    assert len(series) == 1, f"dash fragments should merge into 1 line, got {len(series)}"
    assert series[0].color == red
