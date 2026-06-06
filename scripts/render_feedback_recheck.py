"""Re-render the user's 20 flagged feedback charts with the CURRENT code.

Writes, per flagged chart, a fresh reconstruction into
``$HOME/shared_folder/chart2table_examples/feedback_recheck/<id>/`` alongside the
user's original feedback.txt (copied from the archive) so the latest extraction
can be compared against the feedback. Re-parses each PDF directly (independent of
the corpus batch). Run after any code change since the feedback was given.

Usage: uv run python scripts/render_feedback_recheck.py
"""
from __future__ import annotations

import csv
import glob
import json
import os
import shutil

from make_examples import render_reconstruction
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from pdf_chart2table.cli import parse_pdf  # noqa: E402

SCR = os.path.join(os.environ["SCRATCH"], "pdf_chart2table")
SF = os.path.join(os.environ["HOME"], "shared_folder", "chart2table_examples")
ARC = os.path.join(os.environ["HOME"], "shared_folder", "archive_feedback")
OUT = os.path.join(SF, "feedback_recheck")
# corpus -> the example-folder name the archive uses
ARCDIR = {"materials": "semiconductor_examples", "astro": "astro_examples",
          "ml": "pdf_chart2table_examples",
          "fs": "feature_selection_extracted"}


def main():
    os.makedirs(OUT, exist_ok=True)
    rows = list(csv.DictReader(open(f"{SCR}/feedback_eval/manifest.csv")))
    for r in rows:
        corpus, paper, base, page = r["corpus"], r["paper"], r["base"], r["page"]
        pdf = f"{SCR}/feedback_eval/pdfs/{corpus}__{paper}.pdf"
        tmp = f"/tmp/recheck/{corpus}_{paper}"
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            parse_pdf(pdf, tmp, f"{page}-{page}")
        except Exception as e:
            print(f"  parse fail {corpus}/{paper} p{page}: {e}")
        dest = os.path.join(OUT, f"{corpus}__{paper}__{base}")
        os.makedirs(dest, exist_ok=True)
        # copy the user's original feedback from the archive
        arc = os.path.join(ARC, ARCDIR[corpus], paper, base, "feedback.txt")
        if os.path.exists(arc):
            shutil.copy(arc, os.path.join(dest, "feedback.txt"))
        # find the (possibly renumbered) chart on that page; render the matching
        # base if present, else all charts on the page so nothing is hidden.
        stem_dir = os.path.join(tmp, f"{corpus}__{paper}")
        jp = os.path.join(stem_dir, f"{base}.json")
        targets = [jp] if os.path.exists(jp) else sorted(
            p for p in glob.glob(os.path.join(stem_dir, "page*_chart*.json"))
            if not p.endswith(".skip.json"))
        if not targets:
            sk = os.path.join(stem_dir, f"{base}.skip.json")
            reason = (json.load(open(sk)).get("reason") if os.path.exists(sk)
                      else "no chart extracted on page")
            open(os.path.join(dest, "STATUS.txt"), "w").write(f"SKIPPED: {reason}\n")
            print(f"{corpus}/{paper}/{base}: skipped ({reason})")
            continue
        for t in targets:
            rec = json.load(open(t))
            name = os.path.basename(t)[:-5]
            shutil.copy(t, os.path.join(dest, name + ".json"))
            try:
                render_reconstruction(rec["source"]["pdf"], rec["source"]["page"],
                                      rec, os.path.join(dest, name + ".png"))
            except Exception as e:
                print(f"  render fail {name}: {e}")
        print(f"{corpus}/{paper}/{base}: rendered {len(targets)} chart(s)")
    print(f"\nDone -> {OUT}")


if __name__ == "__main__":
    main()
