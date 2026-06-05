"""Tests for pdf_vector: loading PDFs into Path / TextSpan primitives."""

from __future__ import annotations

import fitz
import pytest

from conftest import fixture_names, pdf_path
from pdf_chart2table.pdf_vector import load_page, load_pdf

ALL_FIXTURES = fixture_names()


def _valid_bbox(b) -> bool:
    return (
        len(b) == 4
        and b[0] <= b[2]
        and b[1] <= b[3]
    )


def _valid_color(c) -> bool:
    return c is None or (
        isinstance(c, tuple)
        and len(c) == 3
        and all(isinstance(v, float) for v in c)
    )


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_load_pdf_single_page(name):
    pages = load_pdf(pdf_path(name))
    assert len(pages) == 1
    page = pages[0]
    assert page.page_index == 0
    assert page.width > 0 and page.height > 0


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_paths_and_texts_nonempty(name):
    page = load_pdf(pdf_path(name))[0]
    assert page.paths, f"{name}: no paths"
    assert page.texts, f"{name}: no text spans"


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_path_invariants(name):
    page = load_pdf(pdf_path(name))[0]
    for p in page.paths:
        assert len(p.points) >= 1
        assert _valid_bbox(p.bbox), f"{name}: bad bbox {p.bbox}"
        assert _valid_color(p.stroke), f"{name}: bad stroke {p.stroke}"
        assert _valid_color(p.fill), f"{name}: bad fill {p.fill}"


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_textspan_invariants(name):
    page = load_pdf(pdf_path(name))[0]
    for t in page.texts:
        assert t.text != ""
        assert _valid_bbox(t.bbox), f"{name}: bad text bbox {t.bbox}"


def test_bezier_flattened_to_polyline():
    """A cubic bezier path must be subdivided into a multi-point polyline."""
    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    shape = page.new_shape()
    shape.draw_bezier(
        fitz.Point(20, 100),
        fitz.Point(60, 20),
        fitz.Point(140, 180),
        fitz.Point(180, 100),
    )
    shape.finish(color=(0, 0, 0), width=1.0)
    shape.commit()

    paths, _ = load_page(page)
    assert paths, "no path emitted for the bezier"
    # A flattened cubic should yield many points, not just the 2 endpoints.
    longest = max(paths, key=lambda p: len(p.points))
    pts = longest.points
    assert len(pts) > 2
    # The polyline must be a genuine curve, not collinear points: pick the two
    # farthest-apart points as a baseline and require some point to deviate.
    import itertools

    (x0, y0), (x1, y1) = max(
        itertools.combinations(pts, 2),
        key=lambda pair: (pair[0][0] - pair[1][0]) ** 2
        + (pair[0][1] - pair[1][1]) ** 2,
    )
    deviations = [
        abs((x1 - x0) * (y0 - py) - (x0 - px) * (y1 - y0))
        for px, py in pts
    ]
    assert max(deviations) > 1.0
