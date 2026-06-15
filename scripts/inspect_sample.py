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
import json
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
    # Cheap pre-filter on the index (no extraction): a line/scatter chart has a
    # series AND enough extracted points (>= _MIN_PTS). This keeps the validity
    # hit-rate high so we don't extract dozens of bars/degenerate figures to find
    # a few valid ones (extraction is the cost).
    _MIN_PTS = 12
    rows = {}
    with open(os.path.join(ROOT, "figures_index.csv")) as f:
        for r in csv.DictReader(f):
            try:
                if int(r.get("n_series") or 0) >= 1 and \
                        int(r.get("n_points") or 0) >= _MIN_PTS:
                    rows[r["chart_id"]] = r
            except ValueError:
                continue
    # Known non-line/scatter types from the filter -> never sample.
    nonls = {"histogram", "contour", "colormap", "bar_chart", "stem_bar"}
    bad_type = set()
    with open(os.path.join(ROOT, "filter_verdicts.csv")) as f:
        for r in csv.DictReader(f):
            if r.get("type") in nonls:
                bad_type.add(r["chart_id"])

    extract_out = os.path.join(args.outdir, "_extract")
    os.makedirs(extract_out, exist_ok=True)
    rng = random.Random(args.seed)
    # Oversample: extract candidates and keep only VALID line/scatter charts (>=1
    # series, >=6 points spanning BOTH axes) so the judge never wastes a slot on a
    # bar/heatmap/degenerate figure.
    candidates = rng.sample(sorted(rows), len(rows))
    kept = 0
    attempts = 0
    max_attempts = args.n * 5  # bound extraction cost even if validity is rare
    for cid in candidates:
        if kept >= args.n or attempts >= max_attempts:
            break
        if cid in bad_type:
            continue
        attempts += 1
        r = rows[cid]
        pdf = os.path.join(ROOT, "pdfs", f"{r['arxiv_id']}.pdf")
        jp = os.path.join(extract_out, r["arxiv_id"],
                          f"page{r['page']}_chart{r['chart']}.json")
        try:
            parse_pdf(pdf, extract_out, str(r["page"]))  # only this chart's page
            d = json.load(open(jp))
        except Exception:
            continue
        if not _looks_line_scatter(d):
            continue
        _cid, msg = _render_one((r, extract_out, args.outdir))
        png = os.path.join(args.outdir, cid, f"{cid}.png")
        if os.path.exists(png):
            print(png, flush=True)
            kept += 1
        else:
            print(f"  skip {cid}: {msg}", flush=True)


def _looks_line_scatter(d):
    """A valid line/scatter chart: >=1 series whose points span BOTH axes with
    enough vertices (a curve/scatter), not a bar/heatmap/degenerate extraction."""
    xs, ys = [], []
    for s in d.get("series") or []:
        for p in s.get("points", []):
            if p.get("x") is not None and p.get("y") is not None:
                xs.append(p["x"])
                ys.append(p["y"])
    return len(xs) >= 6 and (max(xs) - min(xs)) > 0 and (max(ys) - min(ys)) > 0


if __name__ == "__main__":
    main()
