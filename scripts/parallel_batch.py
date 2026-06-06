"""Parallel re-extract of many PDFs (one process per paper).

parse_pdf writes per-paper subdirs into OUTDIR, so concurrent workers on
distinct PDFs never collide.  Far faster than the sequential `batch` CLI on a
many-core node.  No manifest is written (build_examples reads the per-paper
JSONs directly); that's all the example bundles need.

Usage:
    uv run python scripts/parallel_batch.py OUTDIR "GLOB" [JOBS]
"""
from __future__ import annotations

import glob
import os
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from pdf_chart2table.cli import parse_pdf  # noqa: E402


def _work(args):
    pdf, outdir = args
    try:
        rows = parse_pdf(pdf, outdir, None)
        n = sum(1 for r in rows if r.get("status") == "extracted")
        return (pdf, f"ok ({n} extracted)")
    except Exception as e:  # one bad PDF must not abort the batch
        return (pdf, f"ERROR {e}")


def _available_cpus() -> int:
    """CPUs actually available to this process (respects SLURM/cgroup affinity)."""
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:  # non-Linux fallback
        return os.cpu_count() or 4


def main() -> int:
    outdir = sys.argv[1]
    pdfs = sorted(glob.glob(sys.argv[2]))
    jobs = int(sys.argv[3]) if len(sys.argv) > 3 else _available_cpus()
    if not pdfs:
        print(f"no PDFs matched: {sys.argv[2]}", file=sys.stderr)
        return 1
    os.makedirs(outdir, exist_ok=True)
    jobs = min(jobs, len(pdfs))
    print(f"{len(pdfs)} PDFs on {jobs} workers -> {outdir}", flush=True)
    with Pool(jobs) as pool:
        for pdf, st in pool.imap_unordered(_work, [(p, outdir) for p in pdfs]):
            print(f"  {os.path.basename(pdf)}: {st}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
