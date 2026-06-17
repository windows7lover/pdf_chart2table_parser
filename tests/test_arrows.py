"""Annotation arrows: detection -> persist (pixel + data coords) -> render.

An annotation arrow (thin shaft + small filled arrowhead) is decoration, not
data. ``arrows.detect_arrows`` flags it and the caller drops its paths; the
record then carries the arrow in DATA coords so the renderer can re-draw it.
"""
from __future__ import annotations

from pdf_chart2table.arrows import detect_arrows
from pdf_chart2table.model import Path as VPath, Region


def _path(pts, *, fill=None, stroke=None):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return VPath(points=pts, stroke=stroke, fill=fill, width=1.0, dashes=None,
                 closed=fill is not None,
                 bbox=(min(xs), min(ys), max(xs), max(ys)))


def _arrowhead(tip, *, w=3.0, h=7.0):
    """Small filled downward-pointing triangle centred above ``tip``."""
    cx, ty = tip
    return _path([(cx - w / 2, ty - h), (cx + w / 2, ty - h), (cx, ty),
                  (cx - w / 2, ty - h)], fill=(0.0, 0.0, 0.0))


def _shaft(x, y0, y1):
    return _path([(x, y0), (x, y1)], stroke=(0.0, 0.0, 0.0))


def _region_with_arrow():
    """Plot region + a single shaft+head arrow pointing down to (200, 130)."""
    head = _arrowhead((200.0, 130.0))      # tip at y=130
    shaft = _shaft(200.0, 110.0, 123.0)    # above the head
    paths = [shaft, head]
    region = Region(bbox=(120.0, 100.0, 300.0, 300.0),
                    path_indices=[0, 1], text_indices=[])
    return region, paths


def test_detect_arrows_finds_shaft_and_head():
    region, paths = _region_with_arrow()
    idxs, recs = detect_arrows(region, paths)
    assert len(recs) == 1
    rec = recs[0]
    # tip (head centroid) sits near the data point; tail at the shaft's far end.
    assert "head_px" in rec and "tail_px" in rec
    assert rec["color"] == [0.0, 0.0, 0.0]
    # Both arrow paths are flagged for removal from the data.
    assert idxs == {0, 1}


def test_no_arrows_no_records():
    # A plain data polyline (no filled head) yields no arrows and drops nothing.
    region = Region(bbox=(120.0, 100.0, 300.0, 300.0),
                    path_indices=[0], text_indices=[])
    paths = [_path([(130, 250), (200, 200), (260, 240)], stroke=(0, 0, 1))]
    idxs, recs = detect_arrows(region, paths)
    assert recs == []
    assert idxs == set()


def _glyph(cx, cy, *, w=5.0, h=6.3, n=40):
    """A filled TEXT-GLYPH-like polygon: small, roughly square, MANY corners.

    Mimics a digit/letter outline (curved, so it flattens to ~30-80 distinct
    points). Such glyphs sit in the same size/aspect band as an arrowhead and
    used to be mis-counted as arrowheads, inflating the head count past the
    >3 bail and dropping a real arrow on the same chart (2006.13263)."""
    import math
    pts = [(cx + (w / 2) * math.cos(2 * math.pi * k / n),
            cy + (h / 2) * math.sin(2 * math.pi * k / n)) for k in range(n + 1)]
    return _path(pts, fill=(0.0, 0.0, 0.0))


def test_text_glyph_not_counted_as_arrowhead():
    """A many-cornered, roughly-square filled glyph is NOT an arrowhead, but a
    small few-cornered triangle wedge IS -- so glyphs cannot inflate the head
    count and trip the >3 bail that would drop a real arrow."""
    from pdf_chart2table.arrows import _is_head, _bmax

    region = Region(bbox=(120.0, 100.0, 300.0, 300.0))
    diag = _bmax(region.bbox)
    # Glyph: aspect ~0.79 (square-ish), ~40 corners -> rejected.
    glyph = _glyph(200.0, 150.0)
    assert not _is_head(glyph, diag)
    # Arrowhead: a small tight wedge, few corners -> accepted.
    head = _arrowhead((200.0, 130.0))
    assert _is_head(head, diag)


def test_glyphs_do_not_trip_head_bail_and_drop_arrow():
    """One real arrow plus four text glyphs on the same chart: the arrow is
    still detected (the glyphs are not heads, so the head count stays <=3)."""
    head = _arrowhead((200.0, 130.0))
    shaft = _shaft(200.0, 110.0, 123.0)
    glyphs = [_glyph(150.0 + 12 * i, 150.0) for i in range(4)]
    paths = [shaft, head] + glyphs
    region = Region(bbox=(120.0, 100.0, 300.0, 300.0),
                    path_indices=list(range(len(paths))), text_indices=[])
    idxs, recs = detect_arrows(region, paths)
    assert len(recs) == 1
    assert idxs == {0, 1}


def _diag_arrow(head_tip, tail, *, w=3.0, h=4.0):
    """An arrow whose shaft is a DIAGONAL 2-point segment from ``tail`` to the
    arrowhead at ``head_tip``. The head is a small filled triangle; the shaft is
    a single straight segment whose bbox is large in BOTH axes (it is diagonal)
    yet is geometrically thin (a 1-D line)."""
    cx, cy = head_tip
    head = _path([(cx - w / 2, cy - h), (cx + w / 2, cy - h), (cx, cy),
                  (cx - w / 2, cy - h)], fill=(0.0, 0.0, 0.0))
    shaft = _path([tail, (cx, cy)], stroke=(0.0, 0.0, 0.0))
    return shaft, head


def test_five_diagonal_arrows_all_recovered():
    """Five complete shaft+head arrows with DIAGONAL shafts (inline labels
    pointing to curves, as on 2109.05382 p5c3) are ALL recovered -- the head
    count is no longer bailed once each head has a matched shaft, and a diagonal
    shaft is no longer rejected by a bbox-min-dimension thinness test."""
    paths = []
    tips = [(180.0, 180.0), (200.0, 155.0), (230.0, 140.0),
            (235.0, 112.0), (250.0, 88.0)]
    for cx, cy in tips:
        shaft, head = _diag_arrow((cx, cy), (cx - 45.0, cy - 28.0))
        paths += [shaft, head]
    region = Region(bbox=(84.0, 61.0, 285.0, 193.0),
                    path_indices=list(range(len(paths))), text_indices=[])
    idxs, recs = detect_arrows(region, paths)
    assert len(recs) == 5
    # every path (5 shafts + 5 heads) is flagged for removal from the data.
    assert idxs == set(range(len(paths)))


def test_many_bare_heads_still_bail_as_markers():
    """A triangle-MARKER series: many small filled triangles with NO shaft.
    Each is a bare head; more than three bare heads trips the marker guard, so
    NOTHING is recorded (the markers stay as data, not deleted as arrows)."""
    heads = [_arrowhead((150.0 + 20 * i, 200.0)) for i in range(6)]
    region = Region(bbox=(120.0, 100.0, 400.0, 300.0),
                    path_indices=list(range(len(heads))), text_indices=[])
    idxs, recs = detect_arrows(region, heads)
    assert recs == []
    assert idxs == set()


def test_many_complete_arrows_not_bailed_but_bare_heads_are():
    """Discriminator: five COMPLETE shaft+head arrows are recovered even with
    several extra BARE heads present, until the bare-head count alone exceeds
    the marker guard -- then only the bare (unconfirmed) heads are dropped while
    the complete arrows survive."""
    paths = []
    tips = [(180.0, 180.0), (200.0, 155.0), (230.0, 140.0),
            (235.0, 112.0), (250.0, 88.0)]
    for cx, cy in tips:
        shaft, head = _diag_arrow((cx, cy), (cx - 45.0, cy - 28.0))
        paths += [shaft, head]
    n_complete = len(paths)
    # three bare heads (no shaft): within the guard, so they do not bail the
    # complete arrows -- but being unconfirmed they are not recorded either.
    paths += [_arrowhead((120.0 + 15 * i, 250.0)) for i in range(3)]
    region = Region(bbox=(84.0, 61.0, 300.0, 300.0),
                    path_indices=list(range(len(paths))), text_indices=[])
    idxs, recs = detect_arrows(region, paths)
    assert len(recs) == 5
    assert idxs == set(range(n_complete))


def _bracket(cx, cy, *, w=1.8, h=7.2):
    """A filled '<'-bracket text glyph: a thin chevron (few corners, narrow
    aspect ~0.25). Mimics the leading angle bracket of a legend label like
    '<100>' -- small, elongated, ~7 corners, so it passes the arrowhead shape
    test by corner count and aspect. Vertex (apex) points LEFT."""
    return _path([(cx, cy), (cx + w, cy - h / 2), (cx + w * 1.3, cy - h / 2),
                  (cx + w * 0.3, cy), (cx + w * 1.3, cy + h / 2),
                  (cx + w, cy + h / 2), (cx, cy)], fill=(0.0, 0.0, 0.0))


def test_legend_handle_plus_bracket_glyph_not_an_arrow():
    """A legend LINE-SAMPLE (short horizontal handle) next to the label's
    leading '<'-bracket glyph must NOT be paired into an arrow (regression for
    2311.00787 p21c1). The glyph stands clear of the handle, so no head vertex
    touches the shaft end -- the contact guard rejects the pairing."""
    # Handle: short horizontal stroke ending at x=271.7 (like the blue sample).
    handle = _path([(255.7, 201.2), (271.7, 201.2)], stroke=(0.0, 0.0, 0.8))
    # Bracket glyph centred at x~279.8 -- ~7px clear of the handle end.
    glyph = _bracket(278.8, 200.6)
    region = Region(bbox=(230.0, 123.0, 416.0, 211.0),
                    path_indices=[0, 1], text_indices=[])
    idxs, recs = detect_arrows(region, [handle, glyph])
    assert recs == []
    assert idxs == set()


def test_connected_shaft_and_head_still_an_arrow():
    """Positive control for the contact guard: a genuine in-plot arrow whose
    head BASE touches the shaft tip is still recovered."""
    region, paths = _region_with_arrow()
    idxs, recs = detect_arrows(region, paths)
    assert len(recs) == 1
    assert idxs == {0, 1}


def _tri_marker(cx, cy, *, w=9.0, h=8.0):
    """A filled upward triangle data MARKER (arrowhead-shaped) centred at
    ``(cx, cy)`` -- the exact glyph a connected triangle data-series is drawn with."""
    return _path([(cx - w / 2, cy + h / 2), (cx + w / 2, cy + h / 2),
                  (cx, cy - h / 2), (cx - w / 2, cy + h / 2)],
                 fill=(0.0, 0.0, 1.0))


def test_connected_triangle_marker_series_yields_no_arrows():
    """A blue filled-triangle data curve (markers JOINED by a line) must NOT be
    decomposed into arrows (regression for 2003.11050 p20c2). Each triangle is
    arrowhead-shaped and each connecting segment looks like a shaft, but the
    segment bridges TWO same-colour markers -> a connected same-colour head-chain
    -> a marker series, not arrows. Nothing is recorded; no path is dropped."""
    markers = [_tri_marker(120.0 + 40 * i, 200.0 - 10 * i) for i in range(5)]
    # connecting line drawn as separate 2-point segments between adjacent markers.
    segs = [_path([(120.0 + 40 * i, 200.0 - 10 * i),
                   (120.0 + 40 * (i + 1), 200.0 - 10 * (i + 1))],
                  stroke=(0.0, 0.0, 1.0)) for i in range(4)]
    paths = markers + segs
    region = Region(bbox=(80.0, 100.0, 360.0, 280.0),
                    path_indices=list(range(len(paths))), text_indices=[])
    idxs, recs = detect_arrows(region, paths)
    assert recs == []
    assert idxs == set()


def test_marker_series_and_genuine_arrows_coexist():
    """The 2003.11050 case in full: a connected blue triangle marker-series PLUS
    two genuine distinct annotation arrows (a blue + a red, each a smaller/narrower
    head with its own free-tailed shaft). The marker series is dropped while BOTH
    genuine arrows are still recovered."""
    markers = [_tri_marker(120.0 + 40 * i, 200.0 - 10 * i) for i in range(5)]
    segs = [_path([(120.0 + 40 * i, 200.0 - 10 * i),
                   (120.0 + 40 * (i + 1), 200.0 - 10 * (i + 1))],
                  stroke=(0.0, 0.0, 1.0)) for i in range(4)]
    # Two genuine arrows: smaller, narrower heads (sz/aspect differ from markers),
    # each with a free-tailed shaft pointing away (not toward another head).
    blue_head = _path([(301.5, 53.0), (304.5, 53.0), (303.0, 60.0),
                       (301.5, 53.0)], fill=(0.0, 0.0, 1.0))
    blue_shaft = _path([(303.0, 56.0), (303.0, 90.0)], stroke=(0.0, 0.0, 1.0))
    red_head = _path([(331.5, 53.0), (334.5, 53.0), (333.0, 60.0),
                      (331.5, 53.0)], fill=(1.0, 0.0, 0.0))
    red_shaft = _path([(333.0, 56.0), (333.0, 90.0)], stroke=(1.0, 0.0, 0.0))
    paths = markers + segs + [blue_shaft, blue_head, red_shaft, red_head]
    region = Region(bbox=(80.0, 40.0, 360.0, 280.0),
                    path_indices=list(range(len(paths))), text_indices=[])
    idxs, recs = detect_arrows(region, paths)
    assert len(recs) == 2
    colors = sorted(tuple(r["color"]) for r in recs)
    assert colors == [(0.0, 0.0, 1.0), (1.0, 0.0, 0.0)]
    # only the two genuine arrows' paths are dropped; the marker series stays.
    bh, bs, rh, rs = (len(markers) + len(segs) + j for j in range(4))
    assert idxs == {bh, bs, rh, rs}


def test_cli_persists_arrows_in_data_coords(tmp_path):
    """parse_pdf must store each detected arrow's tip/tail in DATA coords."""
    import json
    import os
    import pytest

    pdf = ("/network/projects/sail/chart2table/arxiv_semicond/pdfs/"
           "2006.13263.pdf")
    if not os.path.exists(pdf):
        pytest.skip("corpus PDF not available")
    from pdf_chart2table.cli import parse_pdf
    parse_pdf(pdf, str(tmp_path), "12-12")
    out = tmp_path / "2006.13263" / "page12_chart1.json"
    assert out.exists()
    d = json.loads(out.read_text())
    # arrows is always present (possibly empty -- no spurious noise expected).
    assert "arrows" in d
    for a in d["arrows"]:
        # Persisted arrows carry renderer-usable DATA coords alongside pixels.
        assert "tail" in a and "head" in a
        assert len(a["tail"]) == 2 and len(a["head"]) == 2
