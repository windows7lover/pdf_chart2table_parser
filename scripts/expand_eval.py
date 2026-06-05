"""Sample + render ADDITIONAL eval charts to triple the frozen set (60 -> 180).

Samples N new stratified charts per corpus from the BASELINE batch outputs
(materials_out / astro_out, the locked-in code = same as Judge 3), EXCLUDING the
existing 60 frozen ids, and renders a reconstruction PNG for each. These then get
graded once (fan-out judges) to establish baseline verdicts; the union becomes the
expanded frozen set for the A/B.

Usage: uv run python scripts/expand_eval.py --per 60 --seed 11
"""
from __future__ import annotations

import argparse
import csv
import os

from judge_sample3 import load_extracted, stratified_sample
from make_examples import render_reconstruction

SCR = os.path.join(os.environ["SCRATCH"], "pdf_chart2table")
CORPORA = {"materials": f"{SCR}/materials_out", "astro": f"{SCR}/astro_out"}
VERD = os.path.join(os.path.dirname(__file__), "..", "docs", "judge3_verdicts.csv")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per", type=int, default=60, help="new charts per corpus")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--outdir", default=f"{SCR}/eval_expand")
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    existing = set()
    for r in csv.DictReader(open(VERD)):
        existing.add(r["sample_id"])

    rows = []
    for corpus, out_dir in CORPORA.items():
        items = [i for i in load_extracted(out_dir)
                 if f"{corpus}_{i['paper']}_{i['base']}" not in existing]
        sample = stratified_sample(items, args.per, args.seed)
        print(f"[{corpus}] {len(items)} candidate new charts -> sampled {len(sample)}",
              flush=True)
        for s in sample:
            sid = f"{corpus}_{s['paper']}_{s['base']}"
            png = os.path.join(args.outdir, sid + ".png")
            ok = True
            try:
                render_reconstruction(s["rec"]["source"]["pdf"],
                                      s["rec"]["source"]["page"], s["rec"], png)
            except Exception as e:
                ok = False
                print(f"  RENDER FAIL {sid}: {e}", flush=True)
            rows.append(dict(sample_id=sid, corpus=corpus, paper=s["paper"],
                             base=s["base"], json=s["json"],
                             png=png if ok else "", n_series=s["nser"],
                             n_points=s["npts"]))

    idx = os.path.join(args.outdir, "expand_index.csv")
    with open(idx, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\nwrote {idx} ({len(rows)} new charts) -> {args.outdir}")


if __name__ == "__main__":
    main()
