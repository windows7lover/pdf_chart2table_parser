"""Broad stratified survey renderer for the Sonnet overall-feedback judge.

Unlike judge_frozen.py (fixed 60-chart A/B gate), this samples a WIDE, fresh
stratified set across one or more batch-output corpora and renders a
reconstruction PNG per chart, so a Sonnet judge can survey overall data-recovery
quality and emit ranked failure-mode feedback for the next coding round.

Run at the END of a round (after levers integrate + a broad re-batch).

Usage:
    uv run python scripts/judge_survey.py \
        --corpus materials:$SCRATCH/pdf_chart2table/materials_out \
        --corpus astro:$SCRATCH/pdf_chart2table/astro_out \
        --n 40 --seed 7 --outdir $SCRATCH/pdf_chart2table/survey_r1
"""
from __future__ import annotations

import argparse
import csv
import os

from judge_sample3 import load_extracted, stratified_sample  # same dir on path
from make_examples import render_reconstruction


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", action="append", required=True,
                    help="NAME:OUTDIR pair; repeatable")
    ap.add_argument("--n", type=int, default=40, help="charts per corpus")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--outdir", required=True)
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    rows = []
    for spec in args.corpus:
        name, out_dir = spec.split(":", 1)
        items = load_extracted(out_dir)
        sample = stratified_sample(items, args.n, args.seed)
        print(f"[{name}] {len(items)} extracted charts -> sampled {len(sample)}",
              flush=True)
        for s in sample:
            sid = f"{name}_{s['paper']}_{s['base']}"
            png = os.path.join(args.outdir, sid + ".png")
            ok = True
            try:
                render_reconstruction(s["rec"]["source"]["pdf"],
                                      s["rec"]["source"]["page"], s["rec"], png)
            except Exception as e:
                ok = False
                print(f"  RENDER FAIL {sid}: {e}", flush=True)
            rows.append(dict(sample_id=sid, corpus=name, paper=s["paper"],
                             base=s["base"], json=s["json"],
                             png=png if ok else "", n_series=s["nser"],
                             n_points=s["npts"],
                             x_scale=s["rec"]["x_axis"].get("scale"),
                             y_scale=s["rec"]["y_axis"].get("scale")))

    idx = os.path.join(args.outdir, "survey_index.csv")
    with open(idx, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\nwrote {idx} ({len(rows)} charts) -> render dir {args.outdir}")


if __name__ == "__main__":
    main()
