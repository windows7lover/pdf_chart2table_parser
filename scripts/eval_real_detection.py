"""Evaluate chart detection on the real-PDF corpus.

Runs ``detect_regions`` over every page of every corpus PDF and prints
page-level precision/recall vs ``tests/real/labels.json``, plus the per-PDF
FN/FP page lists. If labels carry region-level bboxes ("chart_regions"), also
reports region-level precision/recall via IoU matching.

Run: uv run python scripts/eval_real_detection.py
"""

from __future__ import annotations

import json
import os

from pdf_chart2table.pdf_vector import load_pdf
from pdf_chart2table.plot_region import detect_regions

_HERE = os.path.dirname(__file__)
_PDF_DIR = os.path.join(_HERE, "..", "data", "real_pdfs")
_LABELS = os.path.join(_HERE, "..", "tests", "real", "labels.json")


def _iou(a, b) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    ua = (a[2] - a[0]) * (a[3] - a[1])
    ub = (b[2] - b[0]) * (b[3] - b[1])
    union = ua + ub - inter
    return inter / union if union > 0 else 0.0


def main():
    with open(_LABELS) as f:
        labels = json.load(f)
    chart_pages = labels["chart_pages"]
    chart_regions = labels.get("chart_regions", {})

    tp = fp = fn = 0
    rtp = rfp = rfn = 0
    print(f"{'PDF':<14} {'TP':>3} {'FP':>3} {'FN':>3}  FN_pages / FP_pages")
    for stem in sorted(chart_pages):
        path = os.path.join(_PDF_DIR, f"{stem}.pdf")
        if not os.path.exists(path):
            print(f"{stem:<14} (missing)")
            continue
        gt = set(chart_pages[stem])
        det = {}
        for pg in load_pdf(path):
            regs = detect_regions(pg.paths, pg.texts, pg.width, pg.height)
            if regs:
                det[pg.page_index] = regs

        detset = set(det)
        ptp = len(detset & gt)
        pfp = len(detset - gt)
        pfn = len(gt - detset)
        tp += ptp
        fp += pfp
        fn += pfn
        notes = f"FN={sorted(gt - detset)} FP={sorted(detset - gt)}"
        print(f"{stem:<14} {ptp:>3} {pfp:>3} {pfn:>3}  {notes}")

        # Region-level IoU matching (only on pages that have labelled regions).
        gt_regions = chart_regions.get(stem, {})
        for pidx_s, boxes in gt_regions.items():
            pidx = int(pidx_s)
            dets = [r.bbox for r in det.get(pidx, [])]
            matched_d = set()
            for gbox in boxes:
                hit = None
                for k, dbox in enumerate(dets):
                    if k in matched_d:
                        continue
                    if _iou(gbox, dbox) > 0.3:
                        hit = k
                        break
                if hit is not None:
                    matched_d.add(hit)
                    rtp += 1
                else:
                    rfn += 1
            rfp += len(dets) - len(matched_d)

    prec = tp / (tp + fp) if (tp + fp) else 1.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    print("\n=== PAGE-LEVEL ===")
    print(f"TP={tp} FP={fp} FN={fn}")
    print(f"precision={prec:.3f}  recall={rec:.3f}")

    if rtp or rfp or rfn:
        rprec = rtp / (rtp + rfp) if (rtp + rfp) else 1.0
        rrec = rtp / (rtp + rfn) if (rtp + rfn) else 0.0
        print("\n=== REGION-LEVEL (IoU>0.3, labelled pages only) ===")
        print(f"TP={rtp} FP={rfp} FN={rfn}")
        print(f"precision={rprec:.3f}  recall={rrec:.3f}")


if __name__ == "__main__":
    main()
