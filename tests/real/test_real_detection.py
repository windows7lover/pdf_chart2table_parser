"""Real-paper chart-detection tests (M3).

Runs ``detect_regions`` over every page of the downloaded real PDFs and checks
page-level detection against the hand-labelled ground truth in ``labels.json``.

The PDFs are NOT committed (``data/`` is gitignored), so this whole module is
skipped when the corpus is absent, keeping the core synthetic suite portable.

Metric: PRECISION over recall (the project mines many PDFs for a clean
ground-truth corpus, so a false chart is worse than a missed one). We assert a
hard precision floor and a softer recall floor; tighten as the detector
improves.
"""

from __future__ import annotations

import json
import os

import pytest

from pdf_chart2table.pdf_vector import load_pdf
from pdf_chart2table.plot_region import detect_regions

_HERE = os.path.dirname(__file__)
_PDF_DIR = os.path.join(_HERE, "..", "..", "data", "real_pdfs")
_LABELS = os.path.join(_HERE, "labels.json")


def _have_corpus() -> bool:
    if not os.path.isdir(_PDF_DIR):
        return False
    return any(f.endswith(".pdf") for f in os.listdir(_PDF_DIR))


pytestmark = pytest.mark.skipif(
    not _have_corpus(),
    reason="real-PDF corpus absent (data/real_pdfs/*.pdf); run M3 download first",
)


def _labels() -> dict:
    with open(_LABELS) as f:
        return json.load(f)


def _chart_pages() -> dict[str, list[int]]:
    return _labels()["chart_pages"]


def _detected_regions(stem: str) -> dict[int, list]:
    """Map page index -> list of detected region bboxes for one PDF."""
    path = os.path.join(_PDF_DIR, f"{stem}.pdf")
    out: dict[int, list] = {}
    for pg in load_pdf(path):
        regs = detect_regions(pg.paths, pg.texts, pg.width, pg.height)
        if regs:
            out[pg.page_index] = [r.bbox for r in regs]
    return out


def _detected_pages(stem: str) -> set[int]:
    return set(_detected_regions(stem))


def _iou(a, b) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


@pytest.mark.parametrize("stem", sorted(_chart_pages()) if _have_corpus() else [])
def test_no_false_charts_on_negative_pdf(stem):
    """PDFs whose only vector-dense pages are non-charts must yield nothing."""
    truth = _chart_pages()[stem]
    if truth:
        pytest.skip("not a pure-negative PDF")
    assert _detected_pages(stem) == set()


def test_corpus_precision_recall():
    """Aggregate page-level precision/recall over the whole corpus."""
    truth = _chart_pages()
    tp = fp = fn = 0
    for stem, gt in truth.items():
        det = _detected_pages(stem)
        gtset = set(gt)
        tp += len(det & gtset)
        fp += len(det - gtset)
        fn += len(gtset - det)

    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0

    # Precision is the headline metric: no false charts allowed on the corpus.
    assert fp == 0, f"false charts detected (precision={precision:.2f})"
    # Recall floor: detector must find most labelled charts (currently ~0.85).
    assert recall >= 0.80, f"recall regressed: {recall:.2f}"


def test_region_level_recall():
    """Each labelled chart region must be matched by a detected region (IoU>0.3).

    This is stronger than page-level: a page carrying both a table and a chart
    is not trivially "passed" by detecting the table, since the detected box
    must actually overlap the labelled chart panel.
    """
    regions = _labels().get("chart_regions", {})
    misses = []
    for stem, pages in regions.items():
        det = _detected_regions(stem)
        for pidx_s, gt_boxes in pages.items():
            dets = det.get(int(pidx_s), [])
            for gbox in gt_boxes:
                if not any(_iou(gbox, dbox) > 0.3 for dbox in dets):
                    misses.append((stem, pidx_s, gbox))
    # Allow a small slack: most labelled regions must be recovered.
    total = sum(len(boxes) for pages in regions.values() for boxes in pages.values())
    recovered = total - len(misses)
    rec = recovered / total if total else 0.0
    assert rec >= 0.85, f"region recall {rec:.2f}; missed {misses}"
