"""Render the FROZEN Judge-3 60-chart eval set from a given batch output.

For the agent-orchestrated precision loop: the 60 chart identities in
``docs/judge3_verdicts.csv`` are a fixed regression set. Given batch-output roots
(per-paper subdirs), this locates each frozen chart, records its current status
(extracted / skipped / missing), renders a reconstruction PNG for the extracted
ones, and writes an index CSV joined to the frozen verdict. Comparing two runs
(baseline vs post-change) over the SAME ids is a clean A/B; only ids whose status
or extraction changed need re-judging.

Usage:
    uv run python scripts/judge_frozen.py \
        --materials-out $SCRATCH/pdf_chart2table/materials_out \
        --astro-out     $SCRATCH/pdf_chart2table/astro_out \
        --outdir        $SCRATCH/pdf_chart2table/judge4_baseline
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re

from make_examples import render_reconstruction

VERDICTS = os.path.join(os.path.dirname(__file__), "..", "docs", "judge3_verdicts.csv")


def frozen_ids():
    out = []
    for r in csv.DictReader(open(VERDICTS)):
        sid = r["sample_id"]
        m = re.match(rf'{r["corpus"]}_(\d+\.\d+)_(page\d+_chart\d+)$', sid)
        out.append(dict(sample_id=sid, corpus=r["corpus"], paper=m.group(1),
                        base=m.group(2), frozen_verdict=r["verdict"],
                        frozen_mode=r["failure_mode"]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--materials-out", required=True)
    ap.add_argument("--astro-out", required=True)
    ap.add_argument("--outdir", required=True)
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)
    roots = {"materials": args.materials_out, "astro": args.astro_out}

    rows = []
    for it in frozen_ids():
        root = roots[it["corpus"]]
        stem = os.path.join(root, it["paper"], it["base"])
        jp, skip = stem + ".json", stem + ".skip.json"
        status, png = "missing", ""
        if os.path.exists(jp):
            rec = json.load(open(jp))
            npts = sum(len(s["points"]) for s in rec.get("series", []))
            status = "extracted" if npts > 0 else "empty"
            if npts > 0:
                png = os.path.join(args.outdir, it["sample_id"] + ".png")
                try:
                    render_reconstruction(rec["source"]["pdf"],
                                          rec["source"]["page"], rec, png)
                except Exception as e:
                    png = ""
                    print(f"  RENDER FAIL {it['sample_id']}: {e}")
        elif os.path.exists(skip):
            status = "skipped"
        rows.append({**it, "status": status, "png": png})
        print(f"{it['sample_id']:48s} {it['frozen_verdict']:8s} -> {status}")

    idx = os.path.join(args.outdir, "frozen_index.csv")
    with open(idx, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    n_ext = sum(r["status"] == "extracted" for r in rows)
    n_skip = sum(r["status"] == "skipped" for r in rows)
    print(f"\nwrote {idx}: {n_ext} extracted, {n_skip} skipped, "
          f"{len(rows) - n_ext - n_skip} missing/empty")


if __name__ == "__main__":
    main()
