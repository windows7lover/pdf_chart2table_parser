"""Regression test for legend-BOX STYLE recovery.

Bug (2005.09264_p27c1): the legend frame is drawn as a thin DARK-GREY square box
with a white fill (border stroke 0.149 grey, linewidth 0.43, sharp corners), but
the extractor only emitted ``legend_box: True`` -- the renderer then drew
matplotlib's DEFAULT light-grey rounded fancybox, so the style was wrong.

The extractor now recovers the frame's full style into ``style.legend_frame``:
edge colour, fill colour, border linewidth, and square-vs-rounded corners. Papers
commonly draw the box as a white FILL rect plus a SEPARATE border-stroke rect, so
neither single path has both -- the coincident pair must be merged.
"""
from __future__ import annotations

from pdf_chart2table.model import Path
from pdf_chart2table.style import _box_like, match_series_styles

REGION = (0.0, 0.0, 200.0, 200.0)


def _rect(bbox, stroke, fill, width, npts=5):
    """A rectangle outline path. ``npts`` controls corner sharpness: ~5 points is
    a sharp box; many points (corner arcs) is a rounded fancybox."""
    x0, y0, x1, y1 = bbox
    if npts <= 5:
        pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    else:
        # many points hugging the perimeter (as a rounded/fancybox frame is drawn)
        pts = []
        for i in range(npts):
            t = i / (npts - 1)
            # walk the perimeter; all points sit on the bbox edges
            if t < 0.25:
                pts.append((x0 + (x1 - x0) * (t / 0.25), y0))
            elif t < 0.5:
                pts.append((x1, y0 + (y1 - y0) * ((t - 0.25) / 0.25)))
            elif t < 0.75:
                pts.append((x1 - (x1 - x0) * ((t - 0.5) / 0.25), y1))
            else:
                pts.append((x0, y1 - (y1 - y0) * ((t - 0.75) / 0.25)))
    return Path(points=pts, stroke=stroke, fill=fill, width=width, dashes=None,
                closed=True, bbox=bbox)


# --- _box_like: rectangle-outline detector (sharp vs rounded vs not-a-box) -----
def test_box_like_sharp_rectangle():
    assert _box_like(_rect((110, 20, 190, 60), (0, 0, 0), None, 0.5)) is False


def test_box_like_rounded_rectangle():
    # a frame drawn with many perimeter-hugging points = rounded (fancybox) corners
    assert _box_like(_rect((110, 20, 190, 60), (0, 0, 0), None, 0.5, npts=40)) is True


def test_box_like_rejects_data_curve():
    # a diagonal polyline whose points fill the bbox interior is NOT a box
    pts = [(10.0 + i, 10.0 + i) for i in range(40)]
    curve = Path(points=pts, stroke=(1, 0, 0), fill=None, width=1.0, dashes=None,
                 closed=False, bbox=(10.0, 10.0, 49.0, 49.0))
    assert _box_like(curve) is None


def test_box_like_rejects_tiny():
    assert _box_like(_rect((10, 10, 15, 15), (0, 0, 0), None, 0.5)) is None


# --- frame-style recovery via match_series_styles -----------------------------
def test_legend_frame_style_recovered_from_separate_fill_and_border():
    # the 2005.09264 pattern: a white FILL rect + a COINCIDENT grey BORDER rect.
    bbox = (110.0, 20.0, 190.0, 60.0)
    paths = [_rect(bbox, None, (1.0, 1.0, 1.0), None),            # white fill, no edge
             _rect(bbox, (0.149, 0.149, 0.149), None, 0.432)]     # grey border, no fill
    _, meta = match_series_styles(paths, REGION, [])
    fr = meta["legend_frame"]
    assert meta["legend_box"] is True
    assert fr is not None
    assert fr["edge_color"] == [0.149, 0.149, 0.149]
    assert fr["face_color"] == [1.0, 1.0, 1.0]
    assert fr["linewidth"] == 0.432
    assert fr["rounded"] is False


def test_legend_frame_rounded_corners_recovered():
    bbox = (110.0, 20.0, 190.0, 60.0)
    paths = [_rect(bbox, None, (1.0, 1.0, 1.0), None, npts=40),
             _rect(bbox, (0.5, 0.5, 0.5), None, 0.8, npts=40)]
    _, meta = match_series_styles(paths, REGION, [])
    assert meta["legend_frame"]["rounded"] is True


def test_no_legend_frame_without_box():
    # a lone data curve in the region -> no frame recovered
    pts = [(20.0 + i, 100.0) for i in range(30)]
    curve = Path(points=pts, stroke=(1, 0, 0), fill=None, width=1.0, dashes=None,
                 closed=False, bbox=(20.0, 100.0, 49.0, 100.0))
    _, meta = match_series_styles([curve], REGION, [])
    assert meta["legend_box"] is False
    assert meta["legend_frame"] is None
