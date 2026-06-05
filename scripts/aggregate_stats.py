"""Aggregate stats from a batch ``manifest.csv`` over the real-PDF corpus.

Reports: #papers processed, #charts detected (rows), extracted vs skipped with
skip-reason breakdown, n_series / n_points distributions, #papers with >=1
extracted chart, and the split of linear/log axes among extracted charts.

Run: uv run python scripts/aggregate_stats.py \
        --out "$SCRATCH/pdf_chart2table/out" \
        --pdfs "$SCRATCH/pdf_chart2table/pdfs"
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter


def _pct(a: int, b: int) -> str:
    return f"{100*a/b:.1f}%" if b else "n/a"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--pdfs", required=True)
    args = ap.parse_args()

    with open(os.path.join(args.out, "manifest.csv")) as f:
        rows = list(csv.DictReader(f))

    n_pdfs_corpus = len([x for x in os.listdir(args.pdfs)
                         if x.endswith(".pdf")])
    files = {r["file"] for r in rows}
    extracted = [r for r in rows if r["status"] == "extracted"]
    skipped = [r for r in rows if r["status"] == "skipped"]

    files_with_chart = {r["file"] for r in extracted}
    skip_reasons = Counter(r["reason"] for r in skipped)

    nser = [int(r["n_series"]) for r in extracted]
    npts = [int(r["n_points"]) for r in extracted]

    def dist(vals):
        if not vals:
            return "none"
        vals = sorted(vals)
        n = len(vals)
        return (f"min={vals[0]} p50={vals[n//2]} "
                f"p90={vals[int(0.9*n)]} max={vals[-1]} "
                f"mean={sum(vals)/n:.1f}")

    # Axis scale split among extracted charts (read JSON records).
    xs_scale, ys_scale = Counter(), Counter()
    for r in extracted:
        stem = os.path.splitext(os.path.basename(r["file"]))[0]
        p = os.path.join(args.out, stem,
                         f"page{r['page']}_chart{r['chart']}.json")
        try:
            with open(p) as f:
                rec = json.load(f)
            xs_scale[(rec.get("x_axis") or {}).get("scale")] += 1
            ys_scale[(rec.get("y_axis") or {}).get("scale")] += 1
        except Exception:
            pass

    print("=" * 60)
    print("AGGREGATE STATS")
    print("=" * 60)
    print(f"PDFs in corpus dir      : {n_pdfs_corpus}")
    print(f"PDFs appearing in manifest (>=1 detected chart): {len(files)}")
    print(f"PDFs with >=1 EXTRACTED chart: {len(files_with_chart)} "
          f"({_pct(len(files_with_chart), n_pdfs_corpus)} of corpus)")
    print()
    print(f"Charts detected (rows)  : {len(rows)}")
    print(f"  extracted             : {len(extracted)} "
          f"({_pct(len(extracted), len(rows))})")
    print(f"  skipped               : {len(skipped)} "
          f"({_pct(len(skipped), len(rows))})")
    print()
    print("Skip reasons:")
    for reason, c in skip_reasons.most_common():
        print(f"  {c:>5}  {reason}")
    print()
    print(f"n_series  (extracted): {dist(nser)}")
    print(f"n_points  (extracted): {dist(npts)}")
    print(f"  charts with 1 series : {sum(1 for v in nser if v==1)}")
    print(f"  charts with >1 series: {sum(1 for v in nser if v>1)}")
    print()
    print(f"x-axis scale: {dict(xs_scale)}")
    print(f"y-axis scale: {dict(ys_scale)}")


if __name__ == "__main__":
    main()
