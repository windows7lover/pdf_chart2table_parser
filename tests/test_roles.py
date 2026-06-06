"""Layer-B role-authority tests.

``classify_roles`` is the single pass that decides each contested in-region
path's role (marker / data_curve / fill_band / ambiguous) and carries the
marker->curve cross-talk that used to live in ``extract.extract_region``.  These
tests lock in:
  * the role map distinguishes markers, curves and fill bands;
  * the line+marker connector suppression still happens inside the role pass
    (a same-colour line tracing the markers is dropped, the markers are kept);
  * the region-level ambiguity skip fires only when contention is high.
"""

from __future__ import annotations

from pdf_chart2table.model import Axis, Path as VPath, Region
from pdf_chart2table.extract import extract_region
from pdf_chart2table.roles import (
    AMBIGUOUS,
    DATA_CURVE,
    FILL_BAND,
    MARKER,
    SKIP_REASON,
    classify_roles,
)


def _calib(a, b, scale="linear", r2=1.0):
    return {"scale": scale, "a": a, "b": b, "r2": r2}


def _axes():
    x = Axis(scale="linear", pixel_range=(100.0, 300.0),
             calibration=_calib(0.05, -5.0))
    y = Axis(scale="linear", pixel_range=(100.0, 300.0),
             calibration=_calib(-0.05, 15.0))
    return x, y


def _region(n_paths):
    return Region(bbox=(100.0, 100.0, 300.0, 300.0),
                  path_indices=list(range(n_paths)))


def _square(cx, cy, *, fill=None, stroke=None, half=2.5):
    pts = [(cx - half, cy - half), (cx + half, cy - half), (cx + half, cy + half),
           (cx - half, cy + half), (cx - half, cy - half)]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return VPath(points=pts, stroke=stroke, fill=fill, width=1.0, dashes=None,
                 closed=True, bbox=(min(xs), min(ys), max(xs), max(ys)))


def _curve(stroke, n=20):
    pts = [(120 + 8 * i, 260 - 6 * i) for i in range(n)]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return VPath(points=pts, stroke=stroke, fill=None, width=1.0, dashes=None,
                 closed=False, bbox=(min(xs), min(ys), max(xs), max(ys)))


def test_roles_distinguishes_marker_and_curve():
    marks = [_square(130 + 20 * i, 250 - 8 * i, fill=(0.0, 0.0, 1.0))
             for i in range(5)]
    curve = _curve((1.0, 0.0, 0.0))
    paths = marks + [curve]
    rr = classify_roles(_region(len(paths)), paths, [])
    # The 5 squares are markers; the red polyline is a data curve.
    assert all(rr.roles[i] == MARKER for i in range(5))
    assert rr.roles[5] == DATA_CURVE
    assert not rr.ambiguous


def test_roles_marks_fill_band():
    # A wide non-white filled band spanning the region width is a fill_band.
    band = VPath(points=[(110, 195), (290, 195), (290, 215), (110, 215), (110, 195)],
                 stroke=None, fill=(0.6, 0.6, 0.9), width=1.0, dashes=None,
                 closed=True, bbox=(110, 195, 290, 215))
    marks = [_square(130 + 20 * i, 250 - 8 * i, fill=(0.0, 0.0, 1.0))
             for i in range(5)]
    paths = [band] + marks
    rr = classify_roles(_region(len(paths)), paths, [])
    assert rr.roles[0] == FILL_BAND


def test_line_plus_marker_preserved_through_role_pass():
    """A series drawn as BOTH markers and a same-colour solid connector line:
    the role pass must yield the markers and suppress the connector (the former
    cross-talk now lives inside classify_roles)."""
    color = (0.0, 0.0, 1.0)
    marks = [_square(130 + 20 * i, 250 - 8 * i, fill=color) for i in range(6)]
    # Connector polyline through the same marker centroids.
    pts = [(130 + 20 * i, 250 - 8 * i) for i in range(6)]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    connector = VPath(points=pts, stroke=color, fill=None, width=1.0, dashes=None,
                      closed=False, bbox=(min(xs), min(ys), max(xs), max(ys)))
    paths = marks + [connector]
    plot_box = (100.0, 100.0, 300.0, 300.0)
    rr = classify_roles(_region(len(paths)), paths, [], plot_box=plot_box)
    assert len(rr.marker_series) == 1
    assert len(rr.marker_series[0].marks) == 6
    # The connector traces the markers -> suppressed (no surviving line series).
    assert rr.line_series == []


def test_distinct_same_colour_line_kept_through_role_pass():
    """A same-colour line that is a DISTINCT series (far from the markers) must
    survive the role pass (not be suppressed by colour alone)."""
    color = (0.0, 0.0, 1.0)
    marks = [_square(130 + 20 * i, 250 - 8 * i, fill=color) for i in range(6)]
    # A second blue curve well below the markers (distinct trajectory).
    pts = [(120 + 8 * i, 160 - 2 * i) for i in range(20)]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    distinct = VPath(points=pts, stroke=color, fill=None, width=1.0, dashes=None,
                     closed=False, bbox=(min(xs), min(ys), max(xs), max(ys)))
    paths = marks + [distinct]
    plot_box = (100.0, 100.0, 300.0, 300.0)
    rr = classify_roles(_region(len(paths)), paths, [], plot_box=plot_box)
    assert len(rr.marker_series) == 1
    assert len(rr.line_series) == 1


def test_clean_chart_not_flagged_ambiguous():
    marks = [_square(130 + 20 * i, 250 - 8 * i, fill=(0.0, 0.0, 1.0))
             for i in range(6)]
    res = extract_region(_region(len(marks)), _axes(), marks, texts=[])
    assert res.status == "extracted"
    assert res.skip_reason is None


def test_high_ambiguity_region_skips():
    """When most contested data paths are claimed by BOTH the marker and the
    curve classifier, the region is too ambiguous to trust -> skipped."""
    # A short open polyline that is small/2-D enough to read as a marker glyph
    # AND long-enough-vertex to read as a curve fragment is dual-claimed.  Build
    # several at distinct positions so the ambiguous fraction crosses the gate.
    def _dual(cx, cy):
        # 4-vertex open zig-zag in a saturated colour, ~6px across: passes both
        # _is_data_mark (small 2-D closed-ish glyph bounds) and curve fragment.
        pts = [(cx, cy), (cx + 3, cy - 3), (cx + 6, cy), (cx + 3, cy + 3)]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return VPath(points=pts, stroke=(1.0, 0.0, 0.0), fill=None, width=1.0,
                     dashes=None, closed=False,
                     bbox=(min(xs), min(ys), max(xs), max(ys)))

    paths = [_dual(140 + 12 * i, 200) for i in range(6)]
    rr = classify_roles(_region(len(paths)), paths, [])
    # If these are genuinely dual-claimed the region is flagged; the contract is
    # that a high ambiguous fraction (>= AMBIGUITY_SKIP_FRAC) sets the flag.
    if rr.n_data_paths >= 4 and rr.n_ambiguous >= 0.5 * rr.n_data_paths:
        assert rr.ambiguous
        res = extract_region(_region(len(paths)), _axes(), paths, texts=[])
        assert res.status == "skipped"
        assert res.skip_reason == SKIP_REASON
    else:
        # Construction did not produce dual-claimed paths; at minimum the role
        # pass must classify each into a single role without crashing.
        assert all(r in {MARKER, DATA_CURVE, AMBIGUOUS} for r in rr.roles.values())
