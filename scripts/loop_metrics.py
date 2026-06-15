"""Track reconstruction performance on the image set across judge-loop iterations.

Each loop pass, after the shared-folder regen produces ``restyle_prototype/
residual_audit.csv`` (per-chart explained%/residual/missed-curves), call this to
append one aggregate row to ``docs/loop_metrics.csv`` and print the evolution
table: ALWAYS the first recorded row plus up to the last 10.

The residual audit is the project's self-supervised completeness proxy: a chart is
well-reconstructed when (near) all of its in-region ink is explained and no long
curve is left unexplained ("missed"). Aggregate metrics:
  * mean_explained  -- mean per-chart explained fraction (higher is better)
  * total_missed    -- total candidate missed curves across the set (lower better)
  * charts_full     -- # charts with 0 residual (fully explained)

Usage:
    uv run python scripts/loop_metrics.py --root <root> --label "<note>"
    uv run python scripts/loop_metrics.py --show            # just print the table
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os

_HIST = os.path.join(os.path.dirname(__file__), "..", "docs", "loop_metrics.csv")
_FIELDS = ["iteration", "timestamp", "n_charts", "mean_explained",
           "total_residual", "total_missed", "charts_full", "label"]


def _read_hist():
    if not os.path.exists(_HIST):
        return []
    with open(_HIST) as f:
        return list(csv.DictReader(f))


def _aggregate(audit_csv):
    with open(audit_csv) as f:
        rows = list(csv.DictReader(f))
    n = len(rows)
    if not n:
        return None
    expl = [int(r["explained"]) / int(r["paths"]) if int(r["paths"]) else 1.0
            for r in rows]
    return {
        "n_charts": n,
        "mean_explained": round(sum(expl) / n, 4),
        "total_residual": sum(int(r["residual"]) for r in rows),
        "total_missed": sum(int(r["missed_curves"]) for r in rows),
        "charts_full": sum(1 for r in rows if int(r["residual"]) == 0),
    }


def _print_table(hist):
    if not hist:
        print("(no loop metrics recorded yet)")
        return
    # always the first row, then up to the last 10 (dedup if overlapping)
    shown = [hist[0]] + [r for r in hist[-10:] if r is not hist[0]]
    cols = ["iteration", "timestamp", "n_charts", "mean_explained",
            "total_residual", "total_missed", "charts_full", "label"]
    widths = {c: max(len(c), max(len(str(r.get(c, ""))) for r in shown)) for c in cols}
    line = "  ".join(c.ljust(widths[c]) for c in cols)
    print(line)
    print("  ".join("-" * widths[c] for c in cols))
    prev = None
    for i, r in enumerate(shown):
        if i == 1 and len(hist) > 11:
            print("  ".join(("...").ljust(widths[c]) for c in cols))
        print("  ".join(str(r.get(c, "")).ljust(widths[c]) for c in cols))
        prev = r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/network/projects/sail/chart2table/arxiv_semicond")
    ap.add_argument("--label", default="")
    ap.add_argument("--show", action="store_true", help="print the table only")
    args = ap.parse_args()
    hist = _read_hist()
    if not args.show:
        audit = os.path.join(args.root, "restyle_prototype", "residual_audit.csv")
        agg = _aggregate(audit)
        if agg is None:
            print(f"no audit rows in {audit}")
            return
        row = {"iteration": len(hist) + 1,
               "timestamp": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
               "label": args.label, **agg}
        write_header = not os.path.exists(_HIST)
        with open(_HIST, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=_FIELDS)
            if write_header:
                w.writeheader()
            w.writerow(row)
        hist.append({k: str(v) for k, v in row.items()})
    print(f"\n=== reconstruction performance on the image set (iter 1 + last 10) ===")
    _print_table(hist)


if __name__ == "__main__":
    main()
