"""Build example bundles from an existing batch-output dir for human inspection.

Samples extracted charts (a mix that may or may not be correct — honest) across
distinct papers, renders a reconstruction for each, and copies a bundle
(paper.pdf, vector crop, json, markers.csv, reconstruction.png) to --out.

Usage:
    uv run python scripts/build_examples_from_batch.py \
        --batch $SCRATCH/pdf_chart2table/mat_out \
        --out $HOME/shared_folder/semiconductor_examples --n 10
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import shutil

from make_examples import render_reconstruction  # same dir on sys.path[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", required=True, help="batch OUTDIR (per-paper subdirs)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--per-paper", type=int, default=2)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    jsons = [p for p in glob.glob(os.path.join(args.batch, "*", "page*_chart*.json"))
             if not p.endswith(".skip.json")]
    # keep only records with data points
    cand = []
    for jp in jsons:
        try:
            rec = json.load(open(jp))
        except Exception:
            continue
        if "series" not in rec:
            continue
        npts = sum(len(s["points"]) for s in rec["series"])
        if npts > 0:
            cand.append((jp, rec, npts))
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

    print(f"{len(cand)} extracted-with-data charts; building {len(chosen)} examples")
    for jp, rec, npts in chosen:
        pid = os.path.basename(os.path.dirname(jp))
        base = jp[:-5]  # strip .json
        tag = f"{pid}_{os.path.basename(base)}"
        dest = os.path.join(args.out, tag)
        os.makedirs(dest, exist_ok=True)
        src_pdf = rec["source"]["pdf"]
        if os.path.exists(src_pdf):
            shutil.copy(src_pdf, os.path.join(dest, "paper.pdf"))
        for ext, name in [(".json", "chart.json"), (".markers.csv", "chart.markers.csv"),
                          (".pdf", "chart_crop.pdf"), (".svg", "chart_crop.svg")]:
            if os.path.exists(base + ext):
                shutil.copy(base + ext, os.path.join(dest, name))
        try:
            render_reconstruction(src_pdf, rec["source"]["page"], rec,
                                  os.path.join(dest, "reconstruction.png"))
        except Exception as e:
            print(f"  render fail {tag}: {e}")
        print(f"  {tag}: series={len(rec['series'])} pts={npts}")
    print(f"\nDone -> {args.out}")


if __name__ == "__main__":
    main()
