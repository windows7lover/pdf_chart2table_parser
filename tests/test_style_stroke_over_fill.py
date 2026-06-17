"""Regression: a LINE series' style is read from its STROKE, not a coinciding FILL.

Bug (2002.05937_p5c2): a red dot-dash boundary curve had a same-colour pink BAND
FILL whose outline runs ALONG the boundary. Both the fill polygon and the dashed
stroke tied at ~0 geometric distance to the extracted series points, and the fill
(listed first, ``dashes=None``) won the ``min(..., key=score)`` tie -- so the
recovered ``linestyle`` was solid ("-") and the dot-dash pattern was lost. The
matcher must tie-break a stroked path ABOVE a fill-only one.
"""
from __future__ import annotations

from pdf_chart2table.model import Path
from pdf_chart2table.style import match_series_styles

REGION = (0.0, 0.0, 100.0, 100.0)
RED = (1.0, 0.0, 0.0)
# fitz-style dash string: a dash-dot pattern (long dash, gap, dot, gap).
DASHDOT = "[ 6.0 4.0 1.0 4.0 ] 0"


def test_line_style_from_stroke_not_coinciding_fill():
    # The boundary curve: a stroked dash-dot line from (10,90) to (90,10).
    curve_pts = [(10.0 + i, 90.0 - i) for i in range(0, 81, 4)]
    stroke = Path(points=curve_pts, stroke=RED, fill=None, width=0.6,
                  dashes=DASHDOT, closed=False,
                  bbox=(10.0, 10.0, 90.0, 90.0))
    # A same-colour band FILL polygon whose outline runs ALONG that same curve
    # (so it ties the stroke at ~0 distance to the series points). No dash, no
    # width. Listed FIRST so a naive min() tie picks it.
    fill = Path(points=curve_pts + [(90.0, 90.0), (10.0, 90.0)], stroke=None,
                fill=RED, width=None, dashes=None, closed=True,
                bbox=(10.0, 10.0, 90.0, 90.0))
    series = [{"color": list(RED),
               "points": [{"x_px": x, "y_px": y} for x, y in curve_pts]}]

    styles, _ = match_series_styles([fill, stroke], REGION, series)
    ls = styles[0].get("linestyle")
    assert ls != "-", f"dash pattern lost to coinciding fill: linestyle={ls!r}"
    assert styles[0].get("width") == 0.6, (
        f"width must come from the stroke, not the fill: {styles[0].get('width')!r}")
