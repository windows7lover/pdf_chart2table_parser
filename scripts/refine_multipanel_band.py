"""Refine the multipanel 0.4-0.6 confidence band.

The chart-type classifier (classify_chart_types.py) parks ~8.2k charts at
multipanel=0.45 from a single PAGE-LEVEL signal: ``n_regions_on_page >= 4``.
That fires on every chart that merely SHARES a page with >=4 figures, even when
the chart's own region is a clean single panel. The band is therefore monolithic
and low-information.

This pass re-reads each band chart's REGION and measures IN-REGION evidence of
multiple panels -- the thing that actually matters:

  * n_panel_tags  -- '(a)' '(b)' ... panel-letter labels strictly inside region.
  * n_plot_boxes  -- distinct large axis-aligned rectangular frames in region.
  * n_x_spines / n_y_spines -- distinct clusters of long vertical / horizontal
    axis-spine lines (catches OPEN L-shaped axes that draw no closed frame).
    A single panel has ~1-2 of each (its own L or box); a grid of panels has
    >=3 well-separated clusters along at least one direction.

refined_multipanel:
  0.85  strong  -- n_panel_tags>=2  OR  n_plot_boxes>=2  OR
                   (n_x_spines>=3 OR n_y_spines>=3)   [a real panel grid]
  0.45  weak    -- exactly one of the above signals at its threshold-1
  0.10  single  -- no in-region multi-panel evidence (the false positives)

Output: refine_multipanel_report.csv next to the main report. Pure vector
geometry, no LLM. Parallel over (arxiv_id, page).

Usage:
    uv run python scripts/refine_multipanel_band.py [--limit N] [--workers K]
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import fitz  # noqa: E402

from pdf_chart2table.pdf_vector import load_page  # noqa: E402
from pdf_chart2table.plot_region import detect_regions  # noqa: E402

CORPUS = "/network/projects/sail/chart2table/arxiv_semicond"
INDEX = os.path.join(CORPUS, "figures_index.csv")
PDF_DIR = os.path.join(CORPUS, "pdfs")
REPORT = os.path.join(CORPUS, "chart_type_report.csv")
OUT_CSV = os.path.join(CORPUS, "refine_multipanel_report.csv")

FIELDS = [
    "chart_id", "old_multipanel", "n_panel_tags", "n_plot_boxes",
    "n_x_spines", "n_y_spines", "refined_multipanel", "decision", "status",
]

_TAG = re.compile(r"^\(?[a-h]\)?[\.\)]?$", re.IGNORECASE)


def _cluster_1d(vals, tol):
    """Greedy 1-D clustering: sorted values within ``tol`` join one cluster.
    Returns the number of distinct clusters."""
    if not vals:
        return 0
    vals = sorted(vals)
    n = 1
    last = vals[0]
    for v in vals[1:]:
        if v - last > tol:
            n += 1
        last = v
    return n


def _long_spines(region, paths, pidx):
    """Distinct clusters of long axis-aligned segments inside the region.

    Returns (n_x_spines, n_y_spines): count of distinct x-positions carrying a
    long VERTICAL segment, and distinct y-positions carrying a long HORIZONTAL
    segment. A lone L/box -> ~1-2 each; a panel grid -> >=3 along a direction."""
    rx0, ry0, rx1, ry1 = region
    rw = rx1 - rx0
    rh = ry1 - ry0
    if rw <= 0 or rh <= 0:
        return 0, 0
    h_min = 0.30 * rw   # a spine spans a good fraction of its panel
    v_min = 0.30 * rh
    xs = []  # x of long vertical segments
    ys = []  # y of long horizontal segments
    for pi in pidx:
        pts = paths[pi].points
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            dx = abs(x1 - x0)
            dy = abs(y1 - y0)
            if dy < 1.0 and dx >= h_min:        # horizontal
                ys.append((y0 + y1) * 0.5)
            elif dx < 1.0 and dy >= v_min:      # vertical
                xs.append((x0 + x1) * 0.5)
    return _cluster_1d(xs, 0.10 * rw), _cluster_1d(ys, 0.10 * rh)


def _plot_boxes(region, paths, pidx):
    """Count distinct LARGE axis-aligned rectangular frames inside region."""
    rx0, ry0, rx1, ry1 = region
    rw = rx1 - rx0
    rh = ry1 - ry0
    if rw <= 0 or rh <= 0:
        return 0
    boxes = []  # (bx0,by0,bx1,by1)
    for pi in pidx:
        p = paths[pi]
        pts = p.points
        if len(pts) < 4:
            continue
        bx0, by0, bx1, by1 = p.bbox
        w = bx1 - bx0
        h = by1 - by0
        if w < 0.25 * rw or h < 0.25 * rh:      # must be a panel-sized frame
            continue
        on_edge = 0
        for (px, py) in pts:
            if (abs(px - bx0) < 0.6 or abs(px - bx1) < 0.6 or
                    abs(py - by0) < 0.6 or abs(py - by1) < 0.6):
                on_edge += 1
        if on_edge < len(pts) * 0.85:
            continue
        boxes.append((bx0, by0, bx1, by1))

    # Real panels TILE the region (disjoint, side by side); a legend / inset
    # frame is NESTED inside a larger panel frame. Drop any box that is mostly
    # contained within a larger box -> a nested legend no longer counts as a
    # second panel.
    def _area(b):
        return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])

    def _contained(inner, outer):
        ix0 = max(inner[0], outer[0]); iy0 = max(inner[1], outer[1])
        ix1 = min(inner[2], outer[2]); iy1 = min(inner[3], outer[3])
        inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
        ai = _area(inner)
        return ai > 0 and inter >= 0.80 * ai

    order = sorted(range(len(boxes)), key=lambda i: _area(boxes[i]), reverse=True)
    kept = []  # indices, largest first
    for i in order:
        if any(_contained(boxes[i], boxes[k]) for k in kept):
            continue                            # nested in a bigger frame
        kept.append(i)

    # merge near-coincident frames (overplotted strokes) -> distinct boxes
    centers = [((boxes[i][0] + boxes[i][2]) * 0.5,
                (boxes[i][1] + boxes[i][3]) * 0.5) for i in kept]
    distinct = 0
    used = [False] * len(centers)
    for i, c in enumerate(centers):
        if used[i]:
            continue
        distinct += 1
        for j in range(i + 1, len(centers)):
            if (abs(centers[j][0] - c[0]) < 0.08 * rw and
                    abs(centers[j][1] - c[1]) < 0.08 * rh):
                used[j] = True
    return distinct


def _panel_tags(region, texts, tidx):
    n = 0
    for ti in tidx:
        t = texts[ti].text.strip()
        if len(t) <= 3 and _TAG.match(t):
            n += 1
    return n


def _refine(n_tags, n_boxes, n_xs, n_ys):
    # STRONG, reliable multipanel evidence: >=2 panel-letter tags OR >=2 distinct
    # plot-box frames. These rarely fire on a single panel.
    if n_tags >= 2 or n_boxes >= 2:
        return 0.85, "multipanel"
    # The spine-cluster count ALONE over-fires: a single panel with a boxed
    # legend + frame + inner gridlines yields >=3 line clusters. So a spine-only
    # signal is NOT an auto-reject -> it goes to the judge as ambiguous.
    if n_xs >= 3 or n_ys >= 3 or (n_tags == 1 and n_boxes >= 1):
        return 0.45, "ambiguous"
    # No multi-panel evidence at all (its own L/box only) -> single panel.
    return 0.10, "single_panel"


def process_page(task):
    arxiv_id, page, charts = task  # charts: [(chart_id, chart_idx, old_mp)]
    pdf_path = os.path.join(PDF_DIR, f"{arxiv_id}.pdf")
    results = []
    try:
        doc = fitz.open(pdf_path)
        pg = doc[page - 1]
        paths, texts = load_page(pg)
        image_rects = []
        try:
            for info in pg.get_image_info(xrefs=True):
                b = info.get("bbox")
                if b:
                    image_rects.append((b[0], b[1], b[2], b[3]))
        except Exception:
            pass
        regions = detect_regions(paths, texts, pg.rect.width, pg.rect.height,
                                 image_rects=image_rects)
        doc.close()
    except Exception as e:
        for (cid, _ci, old) in charts:
            results.append(_err(cid, old, f"page:{type(e).__name__}"))
        return results

    for (cid, chart_idx, old) in charts:
        try:
            if regions and 1 <= chart_idx <= len(regions):
                reg = regions[chart_idx - 1]
            elif regions:
                reg = regions[min(chart_idx - 1, len(regions) - 1)]
            else:
                results.append(_err(cid, old, "no_region"))
                continue
            region = reg.bbox
            pidx = reg.path_indices
            tidx = reg.text_indices
            n_tags = _panel_tags(region, texts, tidx)
            n_boxes = _plot_boxes(region, paths, pidx)
            n_xs, n_ys = _long_spines(region, paths, pidx)
            ref, dec = _refine(n_tags, n_boxes, n_xs, n_ys)
            results.append({
                "chart_id": cid, "old_multipanel": old,
                "n_panel_tags": n_tags, "n_plot_boxes": n_boxes,
                "n_x_spines": n_xs, "n_y_spines": n_ys,
                "refined_multipanel": ref, "decision": dec, "status": "ok",
            })
        except Exception as e:
            results.append(_err(cid, old, f"chart:{type(e).__name__}"))
    return results


def _err(cid, old, reason):
    return {"chart_id": cid, "old_multipanel": old, "n_panel_tags": 0,
            "n_plot_boxes": 0, "n_x_spines": 0, "n_y_spines": 0,
            "refined_multipanel": 0.0, "decision": "", "status": f"err:{reason}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=0)
    args = ap.parse_args()

    # band charts: 0.4 <= out_of_scope_max < 0.6 AND primary_type == multipanel
    band = {}
    with open(REPORT, newline="") as f:
        for r in csv.DictReader(f):
            oos = float(r["out_of_scope_max"])
            if 0.4 <= oos < 0.6 and r["primary_type"] == "multipanel":
                band[r["chart_id"]] = float(r["multipanel"])
    print(f"band charts (multipanel, 0.4<=oos<0.6): {len(band)}")

    done = set()
    if os.path.exists(OUT_CSV):
        with open(OUT_CSV, newline="") as f:
            for r in csv.DictReader(f):
                done.add(r["chart_id"])
    print(f"already refined: {len(done)}")

    pages = defaultdict(list)
    with open(INDEX, newline="") as f:
        for r in csv.DictReader(f):
            cid = r["chart_id"]
            if cid in band and cid not in done:
                pages[(r["arxiv_id"], int(r["page"]))].append(
                    (cid, int(r["chart"]), band[cid]))
    tasks = [(aid, pg, ch) for (aid, pg), ch in pages.items()]
    if args.limit:
        tasks = tasks[:args.limit]
    print(f"pages to process: {len(tasks)}")
    if not tasks:
        print("nothing to do")
        return

    ncpu = args.workers or len(os.sched_getaffinity(0))
    print(f"workers: {ncpu}")

    import multiprocessing as mp
    ctx = mp.get_context("spawn")
    write_header = not os.path.exists(OUT_CSV)
    written = errs = 0
    with open(OUT_CSV, "a", newline="") as fout:
        w = csv.DictWriter(fout, fieldnames=FIELDS)
        if write_header:
            w.writeheader()
        with ctx.Pool(ncpu) as pool:
            for res in pool.imap_unordered(process_page, tasks, chunksize=4):
                for row in res:
                    w.writerow(row)
                    written += 1
                    if str(row["status"]).startswith("err"):
                        errs += 1
                fout.flush()
                if written % 2000 < len(res):
                    print(f"  written {written} (err {errs})", flush=True)
    print(f"DONE. wrote {written} rows, {errs} err.")


if __name__ == "__main__":
    main()
