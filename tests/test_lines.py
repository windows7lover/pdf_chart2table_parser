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


def test_dashed_black_curve_extracted():
    # An unsaturated (black) curve is kept ONLY when dashed (not a solid grid).
    dashed = _poly([(120, 250), (200, 200), (280, 170)], (0.0, 0.0, 0.0), dashes="[3 3] 0")
    series, _ = _classify([dashed])
    assert len(series) == 1
    assert series[0].color == (0.0, 0.0, 0.0)


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


def test_overlapping_same_color_skipped():
    a = _poly([(120, 250), (160, 220), (200, 200)], (1.0, 0.0, 0.0))
    b = _poly([(150, 180), (190, 200), (230, 230)], (1.0, 0.0, 0.0))  # overlaps a in x
    series, reasons = _classify([a, b])
    assert series == []
    assert reasons and "overlapping" in reasons[0]


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


def test_marker_color_deduped():
    region = Region(bbox=REGION.bbox, path_indices=[0], text_indices=[])
    blue = _poly([(120, 250), (170, 200), (220, 170)], (0.0, 0.0, 1.0))
    series, _ = classify_lines(region, [blue], [], marker_colors={(0.0, 0.0, 1.0)})
    assert series == []


def test_line_plus_marker_fixture_no_duplicate_series():
    # convergence_semilogy_3 is a line+marker chart with 3 series; the lines
    # share the markers' colours, so dedupe must keep exactly 3 series.
    results = [r for r in extract_pdf(str(FIXTURES / "convergence_semilogy_3.pdf"))
               if r.status == "extracted"]
    assert len(results) == 1
    assert len(results[0].table.series) == 3
    assert all(s.marker is not None for s in results[0].table.series)
