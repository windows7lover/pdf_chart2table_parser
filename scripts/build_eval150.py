"""Build a LARGE current-code eval set (>=150 charts) for fan-out judging.

Stratified-samples ~PER charts/corpus from the existing batch outputs (only to
pick paper+page targets), RE-EXTRACTS those pages on the CURRENT code (parallel,
one worker per paper), renders a reconstruction PNG per resulting chart, and
writes an index CSV. Unlike judge_frozen (fixed 60-id A/B), this is a fresh wide
survey on the live code.

Usage:  uv run python scripts/build_eval150.py [PER] [SEED]
"""
from __future__ import annotations

import csv
import glob
import json
import os
import re
import sys
from collections import defaultdict
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(__file__))            # judge_sample3, make_examples
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from judge_sample3 import load_extracted, stratified_sample  # noqa: E402
from make_examples import render_reconstruction               # noqa: E402
from pdf_chart2table.cli import parse_pdf                      # noqa: E402

SCR = os.path.join(os.environ["SCRATCH"], "pdf_chart2table")
CORPORA = {"materials": f"{SCR}/materials_out",
           "astro": f"{SCR}/astro_out",
           "optml": f"{SCR}/optml_out"}
OUT = f"{SCR}/eval150"
IDX = f"{SCR}/eval150_idx"


def _cpus() -> int:
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:
        return os.cpu_count() or 4


def _page_of(base: str) -> int:
    m = re.match(r"page(\d+)_chart\d+", base)
    return int(m.group(1))


def _extract_paper(task):
    """Re-extract the sampled pages of one paper on current code."""
    corpus, paper, pdf, pages = task
    outdir = os.path.join(OUT, corpus)
    if not os.path.exists(pdf):
        return (corpus, paper, "pdf-missing")
    for p in sorted(set(pages)):
        try:
            parse_pdf(pdf, outdir, f"{p}-{p}")
        except Exception as e:
            return (corpus, paper, f"err p{p}: {e}")
    return (corpus, paper, "ok")


def _render(job):
    src_pdf, page, rec, out_png = job
    try:
        render_reconstruction(src_pdf, page, rec, out_png)
        return None
    except Exception as e:
        return f"render fail {out_png}: {e}"


def main():
    per = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 13
    os.makedirs(IDX, exist_ok=True)

    # 1. sample paper+page targets per corpus (from existing extractions)
    tasks = []
    for corpus, out_dir in CORPORA.items():
        items = load_extracted(out_dir)
        chosen = stratified_sample(items, per, seed)
        by_paper = defaultdict(list)
        for c in chosen:
            by_paper[c["paper"]].append((c["rec"]["source"]["pdf"], _page_of(c["base"])))
        for paper, pp in by_paper.items():
            pdf = pp[0][0]
            tasks.append((corpus, paper, pdf, [p for _, p in pp]))
        print(f"{corpus}: sampled {len(chosen)} charts across {len(by_paper)} papers",
              flush=True)

    # 2. re-extract on current code, parallel (one worker per paper)
    jobs = min(_cpus(), len(tasks))
    print(f"re-extracting {len(tasks)} papers on {jobs} workers -> {OUT}", flush=True)
    with Pool(jobs) as pool:
        for corpus, paper, st in pool.imap_unordered(_extract_paper, tasks):
            if st != "ok":
                print(f"  {corpus}/{paper}: {st}", flush=True)

    # 3. gather resulting charts, render reconstructions (parallel), write index
    rows, render_jobs = [], []
    for corpus in CORPORA:
        for jp in glob.glob(os.path.join(OUT, corpus, "*", "page*_chart*.json")):
            if jp.endswith(".skip.json"):
                continue
            rec = json.load(open(jp))
            if sum(len(s["points"]) for s in rec.get("series", [])) == 0:
                continue
            paper = os.path.basename(os.path.dirname(jp))
            base = os.path.basename(jp)[:-5]
            png = os.path.join(IDX, f"{corpus}_{paper}_{base}.png")
            rows.append(dict(sample_id=f"{corpus}_{paper}_{base}", corpus=corpus,
                             paper=paper, page=_page_of(base), chart=base,
                             status="extracted", png=png))
            render_jobs.append((rec["source"]["pdf"], rec["source"]["page"], rec, png))

    print(f"rendering {len(render_jobs)} reconstructions on {jobs} workers", flush=True)
    with Pool(jobs) as pool:
        for err in pool.imap_unordered(_render, render_jobs):
            if err:
                print("  " + err, flush=True)

    idx_csv = os.path.join(IDX, "eval150_index.csv")
    with open(idx_csv, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["sample_id", "corpus", "paper", "page",
                                           "chart", "status", "png"])
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: r["sample_id"]))
    by_c = defaultdict(int)
    for r in rows:
        by_c[r["corpus"]] += 1
    print(f"\nEVAL150 DONE: {len(rows)} extracted charts -> {idx_csv}")
    print("by corpus:", dict(by_c))


if __name__ == "__main__":
    main()
