"""Stratified sampler + reconstruction renderer for judging extraction precision.

Adapted from judge_sample.py to run over the TWO real-paper corpora
(materials_out, astro_out) with all extraction gates active.

For each corpus it draws a stratified random sample (fixed seed) of EXTRACTED
charts with n_points>0, spread across papers and strata (log/linear, single vs
many series, scatter vs line), renders a reconstruction PNG per sampled chart,
and writes a combined index CSV. Also renders a random sample of SKIPPED chart
regions per corpus for false-negative spot checks.

Usage:
    uv run python scripts/judge_sample3.py --n 30 --n-skip 10 --seed 0
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import random

import fitz
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from make_examples import render_reconstruction

ROOT = os.path.join(os.environ["SCRATCH"], "pdf_chart2table")
CORPORA = {
    "materials": os.path.join(ROOT, "materials_out"),
    "astro": os.path.join(ROOT, "astro_out"),
}
JUDGE = os.path.join(ROOT, "judge3")


def chart_features(rec):
    ser = rec.get("series", [])
    npts = sum(len(s["points"]) for s in ser)
    nser = len(ser)
    xlog = rec["x_axis"].get("scale") == "log"
    ylog = rec["y_axis"].get("scale") == "log"
    ppser = npts / max(nser, 1)
    return dict(npts=npts, nser=nser, log=(xlog or ylog), xlog=xlog, ylog=ylog,
                single=(nser == 1), many=(nser >= 4),
                scatter=(ppser <= 8), line=(ppser > 15))


def load_extracted(out_dir):
    out = []
    for jp in glob.glob(os.path.join(out_dir, "*", "page*_chart*.json")):
        if jp.endswith(".skip.json"):
            continue
        rec = json.load(open(jp))
        f = chart_features(rec)
        if f["npts"] == 0:
            continue
        paper = os.path.basename(os.path.dirname(jp))
        base = os.path.basename(jp)[:-5]  # page{n}_chart{k}
        out.append(dict(paper=paper, base=base, json=jp, rec=rec, **f))
    return out


def stratified_sample(items, n, seed):
    """Sample ~n charts: cover strata, spread across papers, fixed seed."""
    rng = random.Random(seed)
    buckets = {
        "log": [x for x in items if x["log"]],
        "single": [x for x in items if x["single"]],
        "many": [x for x in items if x["many"]],
        "scatter": [x for x in items if x["scatter"]],
        "line": [x for x in items if x["line"]],
        "mid": [x for x in items if not x["single"] and not x["many"]],
    }
    for b in buckets.values():
        rng.shuffle(b)
    order = ["log", "scatter", "single", "many", "mid", "line"]
    chosen = []
    chosen_keys = set()
    paper_count = {}
    cap = 2

    def key(x):
        return (x["paper"], x["base"])

    while len(chosen) < n:
        progressed = False
        for name in order:
            b = buckets[name]
            while b:
                cand = b.pop()
                k = key(cand)
                if k in chosen_keys:
                    continue
                if paper_count.get(cand["paper"], 0) >= cap:
                    continue
                chosen.append(cand)
                chosen_keys.add(k)
                paper_count[cand["paper"]] = paper_count.get(cand["paper"], 0) + 1
                progressed = True
                break
            if len(chosen) >= n:
                break
        if not progressed:
            break
    if len(chosen) < n:
        pool = [x for x in items if key(x) not in chosen_keys]
        rng.shuffle(pool)
        for cand in pool:
            chosen.append(cand)
            chosen_keys.add(key(cand))
            if len(chosen) >= n:
                break
    return chosen[:n]


def render_skip(src_pdf, page0, bbox, out_png):
    margin = 12.0
    clip = fitz.Rect(bbox[0] - margin, bbox[1] - margin,
                     bbox[2] + margin, bbox[3] + margin)
    doc = fitz.open(src_pdf)
    pix = doc[page0].get_pixmap(dpi=150, clip=clip)
    pix.save(out_png)
    doc.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--n-skip", type=int, default=10)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    os.makedirs(JUDGE, exist_ok=True)

    idx_rows = []
    skip_rows = []
    for corpus, out_dir in CORPORA.items():
        items = load_extracted(out_dir)
        print(f"[{corpus}] extracted-with-points charts: {len(items)} across "
              f"{len(set(i['paper'] for i in items))} papers")
        sample = stratified_sample(items, args.n, args.seed)
        print(f"[{corpus}] sampled {len(sample)} charts across "
              f"{len(set(s['paper'] for s in sample))} papers")

        for s in sample:
            sid = f"{corpus}_{s['paper']}_{s['base']}"
            out_png = os.path.join(JUDGE, sid + ".png")
            ok = True
            try:
                page0 = s["rec"]["source"]["page"]
                render_reconstruction(s["rec"]["source"]["pdf"], page0, s["rec"], out_png)
            except Exception as e:
                ok = False
                print(f"  RENDER FAIL {sid}: {e}")
            idx_rows.append(dict(
                sample_id=sid, corpus=corpus, paper=s["paper"], base=s["base"],
                json=s["json"], png=out_png if ok else "",
                n_series=s["nser"], n_points=s["npts"],
                x_scale=s["rec"]["x_axis"].get("scale"),
                y_scale=s["rec"]["y_axis"].get("scale"),
                rendered=ok))

        # ---- skipped sample for this corpus ----
        skips = glob.glob(os.path.join(out_dir, "*", "*.skip.json"))
        rng = random.Random(args.seed + 1)
        rng.shuffle(skips)
        for sp in skips[: args.n_skip]:
            rec = json.load(open(sp))
            paper = os.path.basename(os.path.dirname(sp))
            base = os.path.basename(sp).replace(".skip.json", "")
            sid = f"SKIP_{corpus}_{paper}_{base}"
            out_png = os.path.join(JUDGE, sid + ".png")
            try:
                render_skip(rec["source"]["pdf"], rec["source"]["page"],
                            rec["source"]["region_bbox"], out_png)
                okp = out_png
            except Exception as e:
                okp = ""
                print(f"  SKIP RENDER FAIL {sid}: {e}")
            skip_rows.append(dict(sample_id=sid, corpus=corpus, json=sp, png=okp,
                                  reason=rec.get("reason")))

    with open(os.path.join(JUDGE, "sample_index.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(idx_rows[0].keys()))
        w.writeheader()
        w.writerows(idx_rows)
    print(f"wrote {os.path.join(JUDGE, 'sample_index.csv')} ({len(idx_rows)} charts)")

    with open(os.path.join(JUDGE, "skip_index.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(skip_rows[0].keys()))
        w.writeheader()
        w.writerows(skip_rows)
    print(f"wrote {os.path.join(JUDGE, 'skip_index.csv')} ({len(skip_rows)} skips)")


if __name__ == "__main__":
    main()
