"""Sample N random charts from the FULL ~19k-chart corpus, fresh-extract + render
to a scratch dir for judging. This is the loop's BUG-FINDING sampler -- it draws
from ALL detected charts (figures_index.csv, ~19k), NOT the 20 shared-folder
bundles (a fixed visualization + metric set) and NOT only the 1020 line_scatter
charts (that filter-classified subset is the METRIC set). Most "unknown"-verdict
charts were never filter-run but are line/scatter; judge them and skip any that
turn out to be an out-of-scope type (bar/heatmap/contour/multi-panel).

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

    # Draw from ALL detected charts (~19k), not the 1020 line_scatter metric set.
    # Require >=1 extracted series so we skip empty / non-data figures.
    rows = {}
    with open(os.path.join(ROOT, "figures_index.csv")) as f:
        for r in csv.DictReader(f):
            try:
                if int(r.get("n_series") or 0) >= 1:
                    rows[r["chart_id"]] = r
            except ValueError:
                continue

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
