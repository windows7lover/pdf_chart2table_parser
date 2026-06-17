"""Regression: a FILLED non-circular marker keeps its fill as the marker FACE.

Bug (2011.13523_p7c2): a solid red triangle series is emitted as TWO coincident
paths per data point -- a 3-vertex fill-only glyph and a 4-vertex stroke-only
outline at the same centre. The per-PATH face vote then tied fill-vs-None (11
filled + 11 outline paths) and collapsed the marker to OPEN (``marker_face=None``),
so the reconstruction drew hollow triangles. Voting ONCE per marker POSITION (a
position is filled if any coincident member carries a fill) recovers the face for
ALL shapes, while a genuinely-open marker (a lone stroke-only glyph, no coincident
fill twin) still votes None and stays open.
"""
from __future__ import annotations

from pdf_chart2table.model import Path
from pdf_chart2table.style import match_series_styles

REGION = (0.0, 0.0, 100.0, 100.0)
RED = (1.0, 0.0, 0.0)


def _triangle_pair(cx, cy, *, filled):
    """A '^' marker at (cx, cy): a 3-vertex fill glyph + a coincident 4-vertex
    stroke outline (filled) or just the stroke outline (open)."""
    half = 2.0
    apex = (cx, cy - half)
    bl = (cx - half, cy + half)
    br = (cx + half, cy + half)
    bbox = (cx - half, cy - half, cx + half, cy + half)
    outline = Path(points=[apex, bl, br, apex], stroke=RED, fill=None,
                   width=0.6, dashes=None, closed=False, bbox=bbox)
    if not filled:
        return [outline]
    fill = Path(points=[apex, bl, br], stroke=None, fill=RED,
                width=None, dashes=None, closed=True, bbox=bbox)
    return [fill, outline]


def _series(centres):
    return [{"color": list(RED),
             "points": [{"x_px": x, "y_px": y} for x, y in centres]}]


def test_filled_triangle_recovers_fill_as_face():
    # 8 solid red triangles along a diagonal, each a fill+outline coincident pair.
    centres = [(15.0 + 8 * i, 80.0 - 8 * i) for i in range(8)]
    paths = []
    for cx, cy in centres:
        paths.extend(_triangle_pair(cx, cy, filled=True))
    styles, _ = match_series_styles(paths, REGION, _series(centres))
    assert styles[0].get("marker_shape") == "^"
    face = styles[0].get("marker_face")
    assert face is not None, "filled triangle lost its face (read as OPEN)"
    assert face[0] > 0.9 and max(face[1:]) < 0.1, f"face should be red: {face!r}"


def test_open_triangle_stays_open():
    # 8 OPEN red triangles (stroke-only, no coincident fill) must stay face=None.
    centres = [(15.0 + 8 * i, 80.0 - 8 * i) for i in range(8)]
    paths = []
    for cx, cy in centres:
        paths.extend(_triangle_pair(cx, cy, filled=False))
    styles, _ = match_series_styles(paths, REGION, _series(centres))
    assert styles[0].get("marker_shape") == "^"
    assert styles[0].get("marker_face") is None, (
        "open triangle wrongly filled: marker_face="
        f"{styles[0].get('marker_face')!r}")
