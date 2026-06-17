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
