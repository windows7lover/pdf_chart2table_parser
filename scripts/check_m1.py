"""M1 validation: load every fixture PDF, detect plot region(s), report.

Run: uv run python scripts/check_m1.py

For each fixture we read its ground-truth JSON sidecar and assert the number
of detected regions equals ``n_panels`` (default 1 for the flat single-panel
schema). For multi-panel fixtures we also check the detected shared-x / shared-y
grouping against the fixture's truth. Shared axes are detected from tick-label
text: under matplotlib ``sharex``/``sharey`` the inner panels carry no tick
labels, so a non-bottom-row panel missing x-tick text => shared x (likewise
non-left-col panel missing y-tick text => shared y).
"""

from __future__ import annotations

import glob
import json
import os

from pdf_chart2table.pdf_vector import load_pdf
from pdf_chart2table.plot_region import detect_regions

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures")

# Vertical band below / horizontal band left of a panel to scan for tick labels.
_TICK_BELOW = 18.0
_TICK_LEFT = 30.0


def _has_xtick_text(page, region) -> bool:
    x0, _, x1, y1 = region.bbox
    for t in page.texts:
        cx = 0.5 * (t.bbox[0] + t.bbox[2])
        cy = 0.5 * (t.bbox[1] + t.bbox[3])
        if x0 - 2 <= cx <= x1 + 2 and y1 < cy <= y1 + _TICK_BELOW:
            return True
    return False


def _has_ytick_text(page, region) -> bool:
    x0, y0, _, y1 = region.bbox
    for t in page.texts:
        cx = 0.5 * (t.bbox[0] + t.bbox[2])
        cy = 0.5 * (t.bbox[1] + t.bbox[3])
        if y0 - 2 <= cy <= y1 + 2 and x0 - _TICK_LEFT <= cx < x0:
            return True
    return False


def _detect_shared(page, regions) -> tuple[bool, bool]:
    """Infer figure-level shared-x / shared-y from missing tick labels."""
    if len(regions) <= 1:
        return False, False
    max_row = max(r.row for r in regions)
    min_col = min(r.col for r in regions)
    shared_x = any(r.row != max_row and not _has_xtick_text(page, r)
                   for r in regions)
    shared_y = any(r.col != min_col and not _has_ytick_text(page, r)
                   for r in regions)
    return shared_x, shared_y


def _load_truth(name: str) -> dict:
    path = os.path.join(FIXTURE_DIR, f"{name}.json")
    with open(path) as f:
        return json.load(f)


def main() -> None:
    pdfs = sorted(glob.glob(os.path.join(FIXTURE_DIR, "*.pdf")))
    n_pass = 0
    fails = []
    print(f"{'fixture':28s} {'exp':>3} {'det':>3} "
          f"{'sx d/t':>6} {'sy d/t':>6}  result")
    print("-" * 64)
    for pdf in pdfs:
        name = os.path.basename(pdf)[:-4]
        truth = _load_truth(name)
        n_panels = truth.get("n_panels", 1)
        true_sx = bool(truth.get("shared_x", False))
        true_sy = bool(truth.get("shared_y", False))

        page = load_pdf(pdf)[0]
        regions = detect_regions(page.paths, page.texts, page.width, page.height)
        det_sx, det_sy = _detect_shared(page, regions)

        problems = []
        if len(regions) != n_panels:
            problems.append(f"{len(regions)} regions != {n_panels} panels")
        if n_panels > 1:
            if det_sx != true_sx:
                problems.append(f"shared_x {det_sx} != {true_sx}")
            if det_sy != true_sy:
                problems.append(f"shared_y {det_sy} != {true_sy}")

        ok = not problems
        n_pass += ok
        if not ok:
            fails.append((name, "; ".join(problems)))
        sx = f"{str(det_sx)[0]}/{str(true_sx)[0]}"
        sy = f"{str(det_sy)[0]}/{str(true_sy)[0]}"
        print(f"{name:28s} {n_panels:>3} {len(regions):>3} "
              f"{sx:>6} {sy:>6}  {'PASS' if ok else 'FAIL'}")
    print("-" * 64)
    print(f"PASS {n_pass}/{len(pdfs)}")
    for name, why in fails:
        print(f"  FAIL {name}: {why}")


if __name__ == "__main__":
    main()
