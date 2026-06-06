"""Build example bundles from an existing batch-output dir for human inspection.

Layout: ONE folder per paper, each containing the paper PDF once, a
``metadata.json`` describing the paper and its graphs, and ONE subfolder per
graph (chart.json, chart.markers.csv, chart_crop.pdf/.svg, reconstruction.png):

    OUT/<paper>/
        paper.pdf
        metadata.json
        <pageN_chartK>/ chart.json chart.markers.csv chart_crop.pdf chart_crop.svg reconstruction.png

Usage:
    uv run python scripts/build_examples_from_batch.py \
        --batch $SCRATCH/pdf_chart2table/mat_out \
        --out $HOME/shared_folder/semiconductor_examples --all --max-line-series 2
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import shutil
from collections import defaultdict
from multiprocessing import Pool

from make_examples import render_reconstruction  # same dir on sys.path[0]


def _available_cpus() -> int:
    """CPUs actually available to this process (respects SLURM/cgroup affinity)."""
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:  # non-Linux fallback
        return os.cpu_count() or 4


def _render_job(job):
    """Render one reconstruction PNG (worker for the parallel render pass)."""
    src_pdf, page, rec, out_png = job
    try:
        render_reconstruction(src_pdf, page, rec, out_png)
        return None
    except Exception as e:
        return f"render fail {out_png}: {e}"


def _title_text(rec):
    t = rec.get("title")
    return t.get("text") if isinstance(t, dict) else t


def _axis_meta(rec, key):
    ax = rec.get(key) or {}
    return {"title": ax.get("title"), "scale": ax.get("scale"),
            "data_range": ax.get("data_range")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", required=True, help="batch OUTDIR (per-paper subdirs)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--per-paper", type=int, default=2)
    ap.add_argument("--all", action="store_true",
                    help="build every kept chart (ignore --n / --per-paper / sampling)")
    ap.add_argument("--max-line-series", type=int, default=None,
                    help="skip charts with more than this many LINE series "
                         "(marker-less) -- excludes 'many line plot' figures")
    ap.add_argument("--jobs", type=int, default=_available_cpus(),
                    help="parallel workers for the reconstruction render pass "
                         "(default: CPUs available to this process)")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    jsons = [p for p in glob.glob(os.path.join(args.batch, "*", "page*_chart*.json"))
             if not p.endswith(".skip.json")]
    # keep only records with data points (and within the line-series cap)
    cand = []
    for jp in jsons:
        try:
            rec = json.load(open(jp))
        except Exception:
            continue
        if "series" not in rec:
            continue
        npts = sum(len(s["points"]) for s in rec["series"])
        n_lines = sum(1 for s in rec["series"] if not s.get("marker"))
        if npts <= 0:
            continue
        if args.max_line_series is not None and n_lines > args.max_line_series:
            continue  # 'many line plot' -> excluded
        cand.append((jp, rec, npts))

    if args.all:
        chosen = sorted(cand, key=lambda c: c[0])
    else:
        random.Random(args.seed).shuffle(cand)
        chosen, per = [], {}
        for jp, rec, npts in cand:
            pid = os.path.basename(os.path.dirname(jp))
            if per.get(pid, 0) >= args.per_paper:
                continue
            per[pid] = per.get(pid, 0) + 1
            chosen.append((jp, rec, npts))
            if len(chosen) >= args.n:
                break

    by_paper = defaultdict(list)
    for jp, rec, npts in chosen:
        by_paper[os.path.basename(os.path.dirname(jp))].append((jp, rec, npts))

    print(f"{len(cand)} kept charts; building {len(chosen)} graphs "
          f"in {len(by_paper)} paper folders")
    render_jobs = []  # (src_pdf, page, rec, out_png) -- rendered in parallel below
    for pid, items in sorted(by_paper.items()):
        pdir = os.path.join(args.out, pid)
        os.makedirs(pdir, exist_ok=True)
        src_pdf = items[0][1]["source"]["pdf"]
        if os.path.exists(src_pdf):
            shutil.copy(src_pdf, os.path.join(pdir, "paper.pdf"))  # once per paper

        graphs_meta = []
        for jp, rec, npts in sorted(items, key=lambda x: x[0]):
            base = jp[:-5]
            gname = os.path.basename(base)  # pageN_chartK
            gdir = os.path.join(pdir, gname)
            os.makedirs(gdir, exist_ok=True)
            for ext, name in [(".json", "chart.json"),
                              (".markers.csv", "chart.markers.csv"),
                              (".pdf", "chart_crop.pdf"), (".svg", "chart_crop.svg")]:
                if os.path.exists(base + ext):
                    shutil.copy(base + ext, os.path.join(gdir, name))
            render_jobs.append((src_pdf, rec["source"]["page"], rec,
                                 os.path.join(gdir, "reconstruction.png")))
            graphs_meta.append({
                "folder": gname,
                "page": rec["source"]["page"],
                "n_series": len(rec["series"]),
                "n_marker_series": sum(1 for s in rec["series"] if s.get("marker")),
                "n_line_series": sum(1 for s in rec["series"] if not s.get("marker")),
                "n_points": npts,
                "title": _title_text(rec),
                "caption": rec.get("caption"),
                "x_axis": _axis_meta(rec, "x_axis"),
                "y_axis": _axis_meta(rec, "y_axis"),
                "series_labels": [s.get("label") for s in rec["series"]],
            })

        meta = {
            "paper": pid,
            "source_pdf": src_pdf,
            "n_graphs": len(graphs_meta),
            "graphs": graphs_meta,
        }
        with open(os.path.join(pdir, "metadata.json"), "w") as fh:
            json.dump(meta, fh, indent=2)
        print(f"  {pid}: {len(graphs_meta)} graphs")

    # Parallel render pass (matplotlib Agg is process-safe; the dominant cost).
    jobs = min(args.jobs, len(render_jobs)) or 1
    print(f"rendering {len(render_jobs)} reconstructions on {jobs} workers")
    with Pool(jobs) as pool:
        for err in pool.imap_unordered(_render_job, render_jobs):
            if err:
                print("  " + err)
    print(f"\nDone -> {args.out}")


if __name__ == "__main__":
    main()
