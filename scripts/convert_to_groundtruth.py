"""CLI: convert our chart record JSON(s) -> ground-truth dataset schema.

Usage:
    # one record -> stdout (or -o file)
    uv run python scripts/convert_to_groundtruth.py path/to/chart.json [-o out.json]
    # a directory of bundles (each <dir>/chart.json) -> <outdir>/<name>.json
    uv run python scripts/convert_to_groundtruth.py --indir <bundles> --outdir <out>

Pure data conversion (style dropped). See pdf_chart2table.groundtruth_export.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pdf_chart2table.groundtruth_export import record_to_groundtruth  # noqa: E402


def _convert_file(path: str) -> dict:
    with open(path) as f:
        return record_to_groundtruth(json.load(f))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("record", nargs="?", help="single chart.json to convert")
    ap.add_argument("-o", "--out", help="output JSON path (default: stdout)")
    ap.add_argument("--indir", help="directory of <name>/chart.json bundles")
    ap.add_argument("--outdir", help="output dir for --indir mode")
    ap.add_argument("--pretty", action="store_true", help="indent the JSON")
    args = ap.parse_args()

    indent = 2 if args.pretty else None

    if args.indir:
        if not args.outdir:
            ap.error("--indir requires --outdir")
        os.makedirs(args.outdir, exist_ok=True)
        n = 0
        for name in sorted(os.listdir(args.indir)):
            jp = os.path.join(args.indir, name, "chart.json")
            if not os.path.exists(jp):
                continue
            gt = _convert_file(jp)
            with open(os.path.join(args.outdir, f"{name}.json"), "w") as f:
                json.dump(gt, f, indent=indent)
            n += 1
        print(f"converted {n} bundles -> {args.outdir}")
        return 0

    if not args.record:
        ap.error("provide a record path or --indir")
    gt = _convert_file(args.record)
    text = json.dumps(gt, indent=indent)
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
