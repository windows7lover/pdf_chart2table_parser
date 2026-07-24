"""Render a chart FROM a ground-truth-schema JSON (the dataset label).

The dataset label is data-only (no plotting style), so this is a CLEAN canonical
re-plot: edit the JSON (change data, labels, add/remove a series) and re-render.
Standalone -- only needs matplotlib; ships inside the dataset so it runs anywhere.

Schema consumed (see dataset README):
  {chart_type: "line"|"scatter", title, x_label, y_label,
   series: [{name, x_raw[], y_raw[], has_error_band, yerr_raw[]}], ...}

Usage:
    python render_from_groundtruth.py annotations/<id>.json -o out          # out.png + out.eps
    python render_from_groundtruth.py annotations/<id>.json --png out.png    # one format
    python render_from_groundtruth.py --jsonl dataset.jsonl --row 3 -o out   # a dataset.jsonl row
"""

from __future__ import annotations

import argparse
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def render_groundtruth(gt: dict, figsize=(6.0, 4.5)):
    """Return a matplotlib Figure plotting the ground-truth JSON."""
    fig, ax = plt.subplots(figsize=figsize)
    scatter = (gt.get("chart_type") == "scatter")
    any_name = False
    for s in gt.get("series", []):
        x, y = s.get("x_raw", []), s.get("y_raw", [])
        name = s.get("name") or None
        any_name = any_name or bool(name)
        yerr = s.get("yerr_raw") if s.get("has_error_band") else None
        if scatter:
            ax.scatter(x, y, s=14, label=name)
            if yerr:
                ax.errorbar(x, y, yerr=yerr, fmt="none", elinewidth=0.8, capsize=2)
        else:
            line, = ax.plot(x, y, label=name)
            if yerr:
                ax.errorbar(x, y, yerr=yerr, fmt="none", elinewidth=0.8,
                            capsize=2, ecolor=line.get_color())
    if gt.get("title"):
        ax.set_title(gt["title"])
    if gt.get("x_label"):
        ax.set_xlabel(gt["x_label"])
    if gt.get("y_label"):
        ax.set_ylabel(gt["y_label"])
    if any_name:
        ax.legend(fontsize=8)
    fig.tight_layout()
    return fig


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("json", nargs="?", help="ground-truth JSON file")
    ap.add_argument("--jsonl", help="read from a dataset.jsonl instead")
    ap.add_argument("--row", type=int, default=0, help="row index for --jsonl")
    ap.add_argument("-o", "--out", help="output prefix -> <prefix>.png and .eps")
    ap.add_argument("--png", help="write PNG to this path")
    ap.add_argument("--eps", help="write EPS to this path")
    ap.add_argument("--dpi", type=int, default=200)
    args = ap.parse_args()

    if args.jsonl:
        with open(args.jsonl) as f:
            row = [json.loads(l) for l in f][args.row]
        gt = row.get("annotation", row)
    elif args.json:
        gt = json.load(open(args.json))
    else:
        ap.error("provide a JSON file or --jsonl")

    fig = render_groundtruth(gt)
    outs = []
    if args.out:
        outs += [(args.out + ".png", args.dpi), (args.out + ".eps", None)]
    if args.png:
        outs.append((args.png, args.dpi))
    if args.eps:
        outs.append((args.eps, None))
    if not outs:
        outs = [("groundtruth_render.png", args.dpi)]
    for path, dpi in outs:
        if path.endswith(".eps"):
            fig.savefig(path, format="eps")
        else:
            fig.savefig(path, dpi=dpi or 200)
        print(f"wrote {path}")
    plt.close(fig)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
