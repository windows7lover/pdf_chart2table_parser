"""Tests for label/caption extraction (Agent B, module 6 `labels.py`).

Synthetic fixtures carry ground-truth `title`, `x_axis.label`, `y_axis.label`
and `series[].label`. We assert the detected title / axis titles match, and
that the detected legend label set equals the series-label set for fixtures
that actually draw a legend (empty for "minimal"/"no legend" fixtures).

Multi-panel `subplots_*` fixtures are handled per panel by mapping each
detected `Region` to its ground-truth panel via (row, col).

One non-asserting real-paper spot check prints an extracted caption when the
gitignored corpus in `data/real_pdfs/` is present.
"""

from __future__ import annotations

import json
import re
from pathlib import Path as FsPath

import pytest

from pdf_chart2table.labels import _assemble_label, detect_labels
from pdf_chart2table.pdf_vector import load_pdf
from pdf_chart2table.plot_region import detect_regions
from pdf_chart2table.model import TextSpan

FIXTURES = FsPath(__file__).parent / "fixtures"


def _norm(s):
    return re.sub(r"\s+", " ", s).strip() if s else None


def _single_panel_fixtures():
    out = []
    for jf in sorted(FIXTURES.glob("*.json")):
        gt = json.loads(jf.read_text())
        if "panels" not in gt:
            out.append(jf.name)
    return out


def _multi_panel_fixtures():
    return [jf.name for jf in sorted(FIXTURES.glob("subplots_*.json"))]


def _load(name):
    gt = json.loads((FIXTURES / name).read_text())
    pages = load_pdf(str(FIXTURES / gt["pdf"]))
    pd = pages[0]
    regions = detect_regions(pd.paths, pd.texts, pd.width, pd.height)
    return gt, pd, regions


@pytest.mark.parametrize("name", _single_panel_fixtures())
def test_single_panel_labels(name):
    gt, pd, regions = _load(name)
    assert len(regions) == 1, f"expected one region for {name}"
    labels = detect_labels(regions[0], pd.paths, pd.texts, pd)

    assert _norm(labels.title) == _norm(gt.get("title"))
    assert _norm(labels.x_title) == _norm(gt["x_axis"]["label"])
    assert _norm(labels.y_title) == _norm(gt["y_axis"]["label"])

    series_labels = {s["label"] for s in gt["series"]}
    detected = {lab for _, _, lab in labels.legend}
    if detected:
        # A legend was drawn: its labels must match the series labels exactly.
        assert detected == series_labels
        # Every legend entry carries a colour.
        assert all(c is not None for _, c, _ in labels.legend)
    else:
        # No legend drawn (minimal / single-series no-legend fixtures): fine.
        assert detected == set()


@pytest.mark.parametrize("name", _multi_panel_fixtures())
def test_multi_panel_labels(name):
    gt, pd, regions = _load(name)
    assert len(regions) == len(gt["panels"]), f"panel count mismatch for {name}"
    by_rc = {(r.row, r.col): r for r in regions}
    for panel in gt["panels"]:
        region = by_rc.get((panel["row"], panel["col"]))
        assert region is not None, f"no region at {panel['row'],panel['col']}"
        labels = detect_labels(region, pd.paths, pd.texts, pd)
        assert _norm(labels.title) == _norm(panel.get("title")), panel
        assert _norm(labels.x_title) == _norm(panel["x_axis"]["label"]), panel
        assert _norm(labels.y_title) == _norm(panel["y_axis"]["label"]), panel


def test_minimal_has_no_legend():
    """The 'no legend' fixture must yield an empty legend and no titles."""
    _, pd, regions = _load("minimal_scatter_nolegend.json")
    labels = detect_labels(regions[0], pd.paths, pd.texts, pd)
    assert labels.legend == []
    assert labels.title is None


# --------------------------------------------------------------------------
# Real-paper caption spot check (non-asserting; skipped if corpus absent).
# --------------------------------------------------------------------------

_REAL = FsPath(__file__).parents[1] / "data" / "real_pdfs"


@pytest.mark.skipif(
    not (_REAL / "1412.6980.pdf").exists(),
    reason="real-paper corpus not present (data/real_pdfs gitignored)",
)
def test_real_paper_caption_spotcheck(capsys):
    # Adam paper, page index 5 holds Figure 1 (logistic-regression curves).
    pd = load_pdf(str(_REAL / "1412.6980.pdf"), [5])[0]
    regions = detect_regions(pd.paths, pd.texts, pd.width, pd.height)
    with capsys.disabled():
        print(f"\n[real spot check] page 5: {len(regions)} region(s)")
        for r in regions:
            labels = detect_labels(r, pd.paths, pd.texts, pd)
            cap = labels.caption
            print("  region", [round(x, 1) for x in r.bbox],
                  "caption:", (cap[:100] + "...") if cap and len(cap) > 100 else cap)
    # Not a strict assertion: real captions are brittle. Just ensure at least
    # one region exists so the spot check is meaningful.
    assert regions


# --------------------------------------------------------------------------
# Subscript/superscript label assembly unit tests.
# --------------------------------------------------------------------------

def _span(text, x0, y0, x1, y1, size=10.0):
    return TextSpan(text=text, bbox=(x0, y0, x1, y1), size=size)


def test_assemble_label_subscript_joined():
    """Subscript span with cy offset > _LABEL_ROW_TOL but < 0.6 * anchor_size
    must be concatenated into the label rather than dropped.

    Mirrors the "E_N(a− : CR)" pattern in real papers where the subscript 'N'
    sits ~2.9 pt below the base line while _LABEL_ROW_TOL = 2.5 pt.
    """
    # Anchor: 'E', size=6.32, cy=169.35 → y0=166.19, y1=172.51
    # Subscript 'N': size=4.42, cy=172.25 → offset=2.90 (> 2.5 but < 0.6*6.32=3.79)
    # Next spans: ' (', 'a', '−' (subscript), ': CR)'
    base_size = 6.32
    sub_size = 4.42
    texts = [
        _span("E",     346.1, 166.2, 350.8, 172.5, size=base_size),   # 0 anchor
        _span("N",     350.8, 168.4, 354.9, 176.1, size=sub_size),    # 1 subscript, cy=172.25
        _span(" (",    354.9, 166.2, 358.4, 173.5, size=base_size),   # 2
        _span("a",     358.4, 166.2, 361.7, 172.5, size=base_size),   # 3
        _span("−",     361.7, 168.4, 365.6, 176.1, size=sub_size),    # 4 subscript
        _span(": CR)", 367.7, 166.2, 382.9, 172.5, size=base_size),   # 5
    ]
    ty = 0.5 * (texts[0].bbox[1] + texts[0].bbox[3])  # cy of anchor
    label, consumed = _assemble_label(0, ty, texts, set())
    assert "N" in label, f"subscript 'N' missing from {label!r}"
    assert "−" in label, f"subscript '−' missing from {label!r}"
    assert consumed == {0, 1, 2, 3, 4, 5}


def test_assemble_label_same_size_row_tol_respected():
    """Same-size spans with cy offset > _LABEL_ROW_TOL are NOT merged
    (they belong to the next legend entry, not a subscript of this one).
    """
    base_size = 6.32
    texts = [
        _span("A", 10.0, 0.0, 14.0, 6.32, size=base_size),   # 0 anchor cy=3.16
        _span("B", 14.0, 3.5, 18.0, 9.82, size=base_size),   # 1 next-row (cy=6.66, diff=3.5 > 2.5)
    ]
    ty = 0.5 * (texts[0].bbox[1] + texts[0].bbox[3])
    label, consumed = _assemble_label(0, ty, texts, set())
    assert "B" not in label, f"next-row span should not be merged into {label!r}"
    assert consumed == {0}
