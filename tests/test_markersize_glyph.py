"""Regression: marker SIZE is measured from glyph paths, not stray segments.

Bug (2002.02623_p25c2): 4.85pt circle markers were recovered as 2.03pt because
``small_paths`` (all small same-colour paths) also contained stray 2-point black
segments (fit-line dashes / error-bar caps / ticks), dragging the median size
down. Marker size must come from paths that are actual marker GLYPHS (a detected
shape), not 1-2pt line segments.
"""
from __future__ import annotations

import math

from pdf_chart2table.model import Path
from pdf_chart2table.style import match_series_styles

REGION = (0.0, 0.0, 100.0, 100.0)
BLACK = (0.0, 0.0, 0.0)


def _circle(cx, cy, r=2.5, n=24):
    pts = [(cx + r * math.cos(2 * math.pi * i / n),
            cy + r * math.sin(2 * math.pi * i / n)) for i in range(n + 1)]
    return Path(points=pts, stroke=BLACK, fill=BLACK, width=0.4, dashes=None,
                closed=True, bbox=(cx - r, cy - r, cx + r, cy + r))


def _seg(x, y):  # a stray 2-point black segment (~2pt) that is NOT a marker
    return Path(points=[(x, y), (x + 2.0, y)], stroke=BLACK, fill=None, width=0.4,
                dashes=None, closed=False, bbox=(x, y, x + 2.0, y))


def _open_circle(cx, cy, r=2.5, n=24):  # stroked ring, NO fill (open marker)
    pts = [(cx + r * math.cos(2 * math.pi * i / n),
            cy + r * math.sin(2 * math.pi * i / n)) for i in range(n + 1)]
    return Path(points=pts, stroke=BLACK, fill=None, width=0.4, dashes=None,
                closed=True, bbox=(cx - r, cy - r, cx + r, cy + r))


def test_markersize_from_glyphs_not_stray_segments():
    centres = [(20.0, 50.0), (40.0, 50.0), (60.0, 50.0), (80.0, 50.0)]
    glyphs = [_circle(cx, cy) for cx, cy in centres]            # 5pt diameter
    segs = [_seg(10.0 + 5 * i, 12.0) for i in range(9)]         # 2pt stray
    series = [{"color": list(BLACK),
               "points": [{"x_px": cx, "y_px": cy} for cx, cy in centres]}]
    styles, _ = match_series_styles(glyphs + segs, REGION, series)
    ms = styles[0].get("markersize")
    assert ms is not None and ms >= 4.0, f"markersize dragged down: {ms}"


def _star(cx, cy, r_out=8.0, r_in=3.2, n=5):
    # A filled 5-point star glyph (10 alternating tips), ~16px across.
    verts = []
    for k in range(n * 2):
        ang = math.pi * k / n - math.pi / 2
        rad = r_out if k % 2 == 0 else r_in
        verts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
    verts.append(verts[0])
    xs = [x for x, _ in verts]
    ys = [y for _, y in verts]
    return Path(points=verts, stroke=(1.0, 0.0, 0.0), fill=(1.0, 0.0, 0.0),
                width=1.0, dashes=None, closed=True,
                bbox=(min(xs), min(ys), max(xs), max(ys)))


def test_large_star_marker_recovered_not_dropped():
    # 2203.02869_p24c1: red 5-point stars are ~15.7px across -- above the historical
    # 12px small_paths cap, so every star was excluded and the series rendered as
    # tiny dots (marker_shape=None). The cap scales with the region so genuine large
    # markers are admitted while staying below the marker-vs-curve threshold.
    region = (0.0, 0.0, 320.0, 320.0)  # large chart -> large diag
    centres = [(40.0 + 30 * i, 160.0) for i in range(8)]
    glyphs = [_star(cx, cy) for cx, cy in centres]
    series = [{"color": [1.0, 0.0, 0.0],
               "points": [{"x_px": cx, "y_px": cy} for cx, cy in centres]}]
    styles, _ = match_series_styles(glyphs, region, series)
    assert styles[0].get("marker_shape") == "*", styles[0].get("marker_shape")
    assert (styles[0].get("markersize") or 0) > 12.0


def test_open_marker_majority_stays_open():
    # 2503.07760_p4c1: a mostly-OPEN series (many fill=None rings) with a couple of
    # incidental FILLED glyphs (e.g. a filled legend sample) must stay open --
    # the face vote includes None so the majority (open) wins.
    centres = [(20.0 + 8 * i, 50.0) for i in range(8)]
    opens = [_open_circle(cx, cy) for cx, cy in centres]      # 8 open rings
    filled = [_circle(100.0, 60.0), _circle(108.0, 60.0)]     # 2 filled (minority)
    series = [{"color": list(BLACK),
               "points": [{"x_px": cx, "y_px": cy} for cx, cy in centres]}]
    styles, _ = match_series_styles(opens + filled, REGION, series)
    assert styles[0].get("marker_face") is None, "open marker flipped to filled"
