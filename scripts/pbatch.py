"""Parallel batch extraction: ProcessPool over parse_pdf (one process per PDF).

The CLI `batch` is single-process; this saturates a many-core node. Each PDF
writes its own ``<outroot>/<stem>/`` tree, so workers don't collide.

Usage:
    uv run python scripts/pbatch.py --glob "$SCRATCH/.../materials_pdfs/*.pdf" \
        --out $SCRATCH/.../materials_out --workers 30
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from pdf_chart2table.cli import parse_pdf  # noqa: E402


def _work(pdf: str, outroot: str):
    try:
        rows = parse_pdf(pdf, outroot)
        ext = sum(1 for r in rows if r.get("status") == "extracted")
        return (pdf, ext, len(rows), None)
    except Exception as e:  # never let one PDF kill the run
        return (pdf, 0, 0, repr(e))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--glob", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    args = ap.parse_args()
    pdfs = sorted(glob.glob(args.glob))
    os.makedirs(args.out, exist_ok=True)
    print(f"[pbatch] {len(pdfs)} PDFs -> {args.out} with {args.workers} workers",
          flush=True)
    t0 = time.perf_counter()
    ext_tot = chart_tot = errs = done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_work, p, args.out): p for p in pdfs}
        for f in as_completed(futs):
            pdf, ext, n, err = f.result()
            done += 1
            if err:
                errs += 1
                print(f"  ERR {os.path.basename(pdf)}: {err}", flush=True)
            else:
                ext_tot += ext; chart_tot += n
            if done % 25 == 0:
                print(f"  {done}/{len(pdfs)}  extracted={ext_tot} errors={errs} "
                      f"({time.perf_counter()-t0:.0f}s)", flush=True)
    print(f"[pbatch] DONE {done} PDFs, {ext_tot} charts extracted, {errs} errors, "
          f"{time.perf_counter()-t0:.0f}s -> {args.out}", flush=True)


if __name__ == "__main__":
    main()
