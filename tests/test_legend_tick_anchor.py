"""Regression: axis TICK numbers must not be mistaken for legend entries.

Bug (2006.03681_p4c3): the real legend ("Experimental [10]" with a red marker,
"This Work" with a black line) has its swatches to the LEFT of the labels, all
inside the plot. But the y-axis tick numbers ('2000','1800',…) sit just left of
the left spine — within _LEGEND_MARGIN of the region — and paired with the
legend's own line samples sitting to their RIGHT. That flipped the swatch-side
vote to "right", which dropped the two real (left-swatched) entries and emitted
the tick numbers as bogus black-line legend entries instead.

A purely-numeric anchor whose centre is OUTSIDE the plotting area is an axis
tick label, not a legend entry. Numeric legend entries (years/temps) sit inside.
"""
from __future__ import annotations

from pdf_chart2table.model import Path, Region, TextSpan
from pdf_chart2table.labels import _detect_legend

REGION = Region(bbox=(18.0, 18.0, 229.0, 211.0))


def _line(x0, y, x1, color=(0.0, 0.0, 0.0)):
    return Path(points=[(x0, y), (x1, y)], stroke=color, fill=None, width=0.5,
                dashes=None, closed=False, bbox=(x0, y, x1, y))


def _marker(cx, cy, color, r=1.7):
    pts = [(cx - r, cy - r), (cx + r, cy - r), (cx + r, cy + r), (cx - r, cy + r)]
    return Path(points=pts, stroke=color, fill=None, width=0.4, dashes=None,
                closed=True, bbox=(cx - r, cy - r, cx + r, cy + r))


def _txt(s, x0, y0, x1, y1):
    return TextSpan(text=s, bbox=(x0, y0, x1, y1), size=5.0, dir=(1.0, 0.0),
                    color=None)


def test_y_tick_numbers_not_taken_as_legend_entries():
    # Real legend (top-left, swatches LEFT of labels, inside the plot).
    red = (1.0, 0.0, 0.0)
    paths = [
        _marker(37.0, 31.5, red),          # "Experimental [10]" swatch (marker)
        _line(29.3, 41.5, 44.3),           # "This Work" swatch (line)
    ]
    texts = [
        _txt("Experimental [10]", 46.1, 29.7, 90.1, 35.0),
        _txt("This Work", 46.1, 40.1, 71.6, 45.5),
        # y-axis tick numbers just LEFT of the left spine (x0=18) -> in margin.
        _txt("2000", 4.7, 16.2, 16.6, 21.6),
        _txt("1800", 4.7, 35.4, 16.6, 40.8),
        _txt("1600", 4.7, 54.6, 16.6, 60.0),
    ]
    entries, _box = _detect_legend(REGION, paths, texts)
    labels = [e[2] for e in entries]
    assert labels == ["Experimental [10]", "This Work"], labels
    # The "Experimental" entry keeps its red marker; "This Work" its black line.
    by_label = {e[2]: e for e in entries}
    assert by_label["Experimental [10]"][0] == "marker"
    assert by_label["Experimental [10]"][1] == red
    assert by_label["This Work"][0] == "line"


def test_numeric_legend_entry_inside_plot_kept():
    # A numeric legend (years) sitting INSIDE the plot must still be recovered.
    paths = [_line(60.0, 40.0, 78.0), _line(60.0, 52.0, 78.0)]
    texts = [
        _txt("2016", 82.0, 37.0, 100.0, 43.0),
        _txt("2020", 82.0, 49.0, 100.0, 55.0),
    ]
    entries, _box = _detect_legend(REGION, paths, texts)
    assert sorted(e[2] for e in entries) == ["2016", "2020"]
