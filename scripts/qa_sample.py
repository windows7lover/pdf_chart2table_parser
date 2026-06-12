"""QA sampler: print N random reconstruction bundles for visual inspection.

Each call returns a fresh random sample (chart_id + the 3-panel PNG path) so a QA
loop can spot-check the restyle prototype for extraction/reconstruction problems
without biasing which charts get looked at.

Usage:
    python scripts/qa_sample.py [--root <root>] [--n 3] [--seed <int>]
"""
from __future__ import annotations

import argparse
import glob
import os
import random


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/network/projects/sail/chart2table/arxiv_semicond")
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    proto = os.path.join(args.root, "restyle_prototype")
    bundles = sorted(d for d in glob.glob(os.path.join(proto, "*"))
                     if os.path.isdir(d))
    rng = random.Random(args.seed)
    for d in rng.sample(bundles, min(args.n, len(bundles))):
        cid = os.path.basename(d)
        png = os.path.join(d, f"{cid}.png")
        print(png if os.path.exists(png) else f"{cid}: NO PNG")


if __name__ == "__main__":
    main()
