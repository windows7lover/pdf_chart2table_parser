"""Sample N random charts from the FULL corpus, fresh-extract + render to a scratch
dir for judging. This is the loop's BUG-FINDING sampler -- it draws from the whole
eligible pool (keep / line_scatter in filter_verdicts.csv), NOT the 20 shared-folder
bundles (those are a fixed visualization + metric set for the user).

Each chart is re-extracted with the CURRENT parser (only its own page) and rendered
to ``<scratch>/<cid>/<cid>.png`` so the judge sees up-to-date behaviour.

Usage:
    uv run python scripts/inspect_sample.py [--n 5] [--seed S] [--outdir DIR]
"""
from __future__ import annotations

import argparse
import csv
import os
import random
import sys

sys.path.insert(0, os.path.dirname(__file__))
from pdf_chart2table.cli import parse_pdf  # noqa: E402
from render_restyle_prototype import _render_one  # noqa: E402

ROOT = "/network/projects/sail/chart2table/arxiv_semicond"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--outdir", default="/tmp/inspect_bundles")
    args = ap.parse_args()

    keep = set()
    with open(os.path.join(ROOT, "filter_verdicts.csv")) as f:
        for r in csv.DictReader(f):
            if r["verdict"] == "keep" and r["type"] == "line_scatter":
                keep.add(r["chart_id"])
    rows = {}
    with open(os.path.join(ROOT, "figures_index.csv")) as f:
        for r in csv.DictReader(f):
            if r["chart_id"] in keep:
                rows[r["chart_id"]] = r

    rng = random.Random(args.seed)
    picked = rng.sample(sorted(rows), min(args.n, len(rows)))

    extract_out = os.path.join(args.outdir, "_extract")
    os.makedirs(extract_out, exist_ok=True)
    for cid in picked:
        r = rows[cid]
        pdf = os.path.join(ROOT, "pdfs", f"{r['arxiv_id']}.pdf")
        try:
            parse_pdf(pdf, extract_out, str(r["page"]))  # only this chart's page
        except Exception as e:
            print(f"  EXTRACT-ERR {cid}: {e}", flush=True)
            continue
        _cid, msg = _render_one((r, extract_out, args.outdir))
        png = os.path.join(args.outdir, cid, f"{cid}.png")
        print(png if os.path.exists(png) else f"{cid}: {msg}", flush=True)


if __name__ == "__main__":
    main()
