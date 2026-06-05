"""Re-extract ONLY the frozen-eval pages, in one process (fast A/B re-batch).

For each frozen chart id (docs/judge3_verdicts.csv) we know its paper + page.
This parses just those pages (via the page-limited parse_pdf) into
``$SCRATCH/pdf_chart2table/<outroot>/<corpus>/<paper>/`` so judge_frozen.py can
render the same 60 identities. Avoids loading whole 89-page papers and avoids
per-call `uv run` startup.

Usage:  uv run python scripts/rebatch_frozen.py judge4_r1
"""
from __future__ import annotations

import csv
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from pdf_chart2table.cli import parse_pdf  # noqa: E402

SCR = os.path.join(os.environ["SCRATCH"], "pdf_chart2table")
VERD = os.path.join(os.path.dirname(__file__), "..", "docs", "judge3_verdicts.csv")


def main():
    outroot = sys.argv[1]
    pages: dict[tuple[str, str], set[int]] = defaultdict(set)
    for r in csv.DictReader(open(VERD)):
        m = re.match(rf'{r["corpus"]}_(\d+\.\d+)_page(\d+)_chart\d+$', r["sample_id"])
        pages[(r["corpus"], m.group(1))].add(int(m.group(2)))

    for (corpus, paper), pgs in sorted(pages.items()):
        pdf = f"{SCR}/judge4_pdfs/{corpus}/{paper}.pdf"
        outdir = f"{SCR}/{outroot}/{corpus}"
        for p in sorted(pgs):
            parse_pdf(pdf, outdir, f"{p}-{p}")
        print(f"{corpus}/{paper}: pages {sorted(pgs)} done", flush=True)
    print(f"DONE -> {SCR}/{outroot}")


if __name__ == "__main__":
    main()
