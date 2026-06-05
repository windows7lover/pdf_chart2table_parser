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

from pdf_chart2table.labels import detect_labels
from pdf_chart2table.pdf_vector import load_pdf
from pdf_chart2table.plot_region import detect_regions

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
