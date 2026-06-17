"""Run the image-space recon-vs-original comparison over many charts.

Globs ``chart.json`` files (e.g. a shared-folder bundle dir or an extract_out
tree), renders + compares each, writes a fidelity CSV, and prints the charts
ranked worst-first (highest missing_frac). This is the measurement backbone for
the analysis-by-synthesis loop: track fidelity across iterations and find the
charts whose reconstruction diverges most from the original.

Usage:
    uv run python scripts/recon_compare_batch.py GLOB [-o CSV] [--scale S]
        [--tol T] [--jobs N]
e.g. uv run python scripts/recon_compare_batch.py \
        '~/shared_folder/semiconductor_restyle_prototype/*/chart.json' \
        -o docs/recon_fidelity.csv
Run via srun for large corpora, not on a login node.
"""

from __future__ import annotations

import argparse
import csv
import glob
import os
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _one(args) -> dict | None:
    chart_json, scale, tol = args
    # Import inside the worker so each process initialises matplotlib/fitz once.
    from recon_compare import compare_chart
    try:
        res = compare_chart(chart_json, scale=scale, tol=tol)
        res["path"] = chart_json
        return res
    except Exception as e:  # a malformed / skipped chart is reported, not fatal
        return {"chart_id": os.path.basename(os.path.dirname(chart_json)),
                "path": chart_json, "error": str(e)[:200]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("glob_pat", help="glob for chart.json files")
    ap.add_argument("-o", "--out", default="docs/recon_fidelity.csv")
    ap.add_argument("--scale", type=float, default=2.0)
    ap.add_argument("--tol", type=int, default=2)
    ap.add_argument("--jobs", type=int, default=len(os.sched_getaffinity(0)))
    args = ap.parse_args()

    pat = os.path.expanduser(args.glob_pat)
    files = sorted(glob.glob(pat))
    if not files:
        print(f"no chart.json matched: {pat}")
        return 1
    print(f"comparing {len(files)} charts (jobs={args.jobs}) ...")

    tasks = [(f, args.scale, args.tol) for f in files]
    rows: list[dict] = []
    with ProcessPoolExecutor(max_workers=args.jobs) as ex:
        for r in ex.map(_one, tasks):
            if r is not None:
                rows.append(r)

    ok = [r for r in rows if "error" not in r]
    cols = ["chart_id", "missing_frac", "extra_frac", "ink_iou",
            "orig_ink", "recon_ink", "path", "error"]
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})

    if ok:
        mean_missing = sum(r["missing_frac"] for r in ok) / len(ok)
        mean_extra = sum(r["extra_frac"] for r in ok) / len(ok)
        mean_iou = sum(r["ink_iou"] for r in ok) / len(ok)
        print(f"\n{len(ok)} compared | mean missing={mean_missing:.3f} "
              f"extra={mean_extra:.3f} iou={mean_iou:.3f}")
        print("\nworst (highest missing_frac):")
        for r in sorted(ok, key=lambda r: -r["missing_frac"])[:15]:
            print(f"  {r['chart_id']:24} missing={r['missing_frac']:.3f} "
                  f"extra={r['extra_frac']:.3f} iou={r['ink_iou']:.3f}")
    errs = [r for r in rows if "error" in r]
    if errs:
        print(f"\n{len(errs)} errored:")
        for r in errs[:10]:
            print(f"  {r['chart_id']}: {r['error']}")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
