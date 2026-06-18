"""Axes background fill: a non-white rectangle covering ~the full plot box on
BOTH axes is recovered as ``axes_facecolor`` (renderer ax.set_facecolor), while
a partial highlight band, a data-bounded fill, and a white patch are NOT.

Motivated by 2310.06834_p4c1: a gray (0.83) panel covered the full plot interior
behind the grid + data; the reconstruction lost it (white background), with the
gray panel as the bulk of the missing ink (data_iou 0.409).
"""
from __future__ import annotations

from pdf_chart2table.model import Path
from pdf_chart2table.style import recover_axes_facecolor

# Plot box (PDF points), padded ~8pt vertically with tick-label margin so a
# recovered facecolor rect sits slightly inside it (as in the real 2310.06834).
PLOT = (100.0, 50.0, 300.0, 250.0)  # x0, y0, x1, y1  (W=200, H=200)


def _rect(x0, y0, x1, y1, fill, alpha=None):
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    return Path(points=pts, stroke=None, fill=fill, width=0.0, dashes=None,
                closed=True, bbox=(x0, y0, x1, y1), fill_alpha=alpha)


def test_full_box_gray_fill_is_axes_facecolor():
    # Gray panel, full width, ~0.85 height (8pt margin each side), centred.
    bg = _rect(100.0, 58.0, 300.0, 242.0, (0.83, 0.83, 0.83))
    fc = recover_axes_facecolor([bg], PLOT)
    assert fc is not None
    assert fc["color"] == [0.83, 0.83, 0.83]
    assert fc["alpha"] is None


def test_full_box_fill_carries_alpha():
    bg = _rect(100.0, 58.0, 300.0, 242.0, (0.9, 0.85, 0.7), alpha=0.5)
    fc = recover_axes_facecolor([bg], PLOT)
    assert fc is not None and fc["alpha"] == 0.5


def test_full_width_partial_height_band_not_background():
    # axhspan-style: full width but a narrow (0.15 height) horizontal band.
    band = _rect(100.0, 120.0, 300.0, 150.0, (1.0, 0.9, 0.2))
    assert recover_axes_facecolor([band], PLOT) is None


def test_full_height_partial_width_band_not_background():
    # axvspan-style: full height but a narrow (0.25 width) vertical band.
    band = _rect(240.0, 50.0, 290.0, 250.0, (0.0, 1.0, 1.0))
    assert recover_axes_facecolor([band], PLOT) is None


def test_offcentre_tall_wide_fill_not_background():
    # Full width and tall (0.85 height) but shoved against the bottom edge: a
    # band, not a centred background -> rejected by the centring guard.
    band = _rect(100.0, 80.0, 300.0, 250.0, (0.6, 0.8, 0.95))
    assert recover_axes_facecolor([band], PLOT) is None


def test_white_fill_ignored():
    bg = _rect(100.0, 58.0, 300.0, 242.0, (1.0, 1.0, 1.0))
    assert recover_axes_facecolor([bg], PLOT) is None


def test_curve_following_fill_not_background():
    # A data-bounded fill (many distinct vertices) covering most of the box is
    # not a simple rectangle -> excluded by the point-count guard.
    fwd = [(100.0 + 4.0 * i, 60.0 + 0.2 * i) for i in range(50)]
    back = [(x, y + 170.0) for x, y in reversed(fwd)]
    pts = fwd + back + [fwd[0]]
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    fill = Path(points=pts, stroke=None, fill=(0.6, 0.7, 0.95), width=0.0,
                dashes=None, closed=True,
                bbox=(min(xs), min(ys), max(xs), max(ys)))
    assert recover_axes_facecolor([fill], PLOT) is None
