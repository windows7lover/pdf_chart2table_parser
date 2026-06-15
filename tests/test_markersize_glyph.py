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


def test_markersize_from_glyphs_not_stray_segments():
    centres = [(20.0, 50.0), (40.0, 50.0), (60.0, 50.0), (80.0, 50.0)]
    glyphs = [_circle(cx, cy) for cx, cy in centres]            # 5pt diameter
    segs = [_seg(10.0 + 5 * i, 12.0) for i in range(9)]         # 2pt stray
    series = [{"color": list(BLACK),
               "points": [{"x_px": cx, "y_px": cy} for cx, cy in centres]}]
    styles, _ = match_series_styles(glyphs + segs, REGION, series)
    ms = styles[0].get("markersize")
    assert ms is not None and ms >= 4.0, f"markersize dragged down: {ms}"
