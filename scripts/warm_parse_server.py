"""Warm worker for streaming single-chart parsing (interactive / QA sampling).

Each fresh ``python`` process that calls ``cli.parse_pdf`` pays ~8 s for Python
startup + heavy imports (numpy / pymupdf / matplotlib), and with OCR enabled
(``PDFCHART_OCR=1``) an additional ONE-TIME ~30-60 s RapidOCR model load. A
sampling loop that spawns a fresh process per chart re-pays all of that EVERY
chart.

This server pays the cost ONCE per worker, then parses many charts cheaply in
the same warm process(es). The batch pipeline (``scripts/pbatch.py``) already
amortizes startup across PDFs in one ProcessPool; this fills the gap for
*streaming* single-chart use, where the caller keeps one warm process alive and
feeds it charts over a pipe.

Protocol (newline-delimited JSON over stdin -> stdout):

    request : {"pdf": "...", "outroot": "...", "pages": "5"}   # pages optional
    response: {"pdf":..., "pages":..., "ok":true,  "rows":N, "secs":...}
              {"pdf":..., "pages":..., "ok":false, "error":"..."}

One response line per request, flushed immediately. Extraction is unchanged:
each request calls the existing ``pdf_chart2table.cli.parse_pdf`` verbatim, so
output is byte-for-byte identical to a direct call.

Usage:
    # stream charts into one warm process (OCR loaded once):
    printf '%s\n' \\
      '{"pdf":"a.pdf","outroot":"/tmp/out","pages":"13"}' \\
      '{"pdf":"b.pdf","outroot":"/tmp/out","pages":"4"}' \\
      | PDFCHART_OCR=1 uv run python scripts/warm_parse_server.py

    # pool of warm workers (default: all available CPUs):
    ... | uv run python scripts/warm_parse_server.py --workers 8

    # built-in self-test (no input needed):
    uv run python scripts/warm_parse_server.py --demo
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _warm() -> None:
    """Import the parser and pre-load the OCR engine ONCE in this process.

    Skips OCR warm-up when disabled (``PDFCHART_OCR=0``). The engine is lazy +
    cached inside ``ocr_backfill``; touching it here forces the one-time model
    load now (paying it before the first request) instead of on the first chart
    that needs a title backfilled.
    """
    import pdf_chart2table.cli  # noqa: F401  (heavy imports: numpy/fitz/mpl)

    if os.environ.get("PDFCHART_OCR", "1") != "0":
        from pdf_chart2table.ocr_backfill import _engine
        _engine()  # constructs + caches RapidOCR (or None if unavailable)


def _handle(req: dict) -> dict:
    """Run one parse request and build its response dict."""
    from pdf_chart2table.cli import parse_pdf

    pdf = req["pdf"]
    pages = req.get("pages")
    t0 = time.perf_counter()
    try:
        rows = parse_pdf(pdf, req["outroot"], pages)
        return {"pdf": pdf, "pages": pages, "ok": True,
                "rows": len(rows), "secs": round(time.perf_counter() - t0, 3)}
    except Exception as e:  # never let one bad chart kill the server
        return {"pdf": pdf, "pages": pages, "ok": False,
                "error": repr(e), "secs": round(time.perf_counter() - t0, 3)}


def _emit(resp: dict) -> None:
    sys.stdout.write(json.dumps(resp) + "\n")
    sys.stdout.flush()


def _serve_single(reqs) -> None:
    """One warm process: warm once, then handle requests in order."""
    _warm()
    for req in reqs:
        _emit(_handle(req))


def _serve_pool(reqs, workers: int) -> None:
    """Pool of warm workers: each warms once via the initializer, then charts
    are distributed across them. No ``maxtasksperchild`` so the OCR model stays
    resident for the worker's whole life. Responses stream out as soon as a
    chart finishes (order not preserved)."""
    with ProcessPoolExecutor(max_workers=workers, initializer=_warm) as ex:
        futs = [ex.submit(_handle, req) for req in reqs]
        for fut in futs:
            _emit(fut.result())


def _iter_stdin():
    for line in sys.stdin:
        line = line.strip()
        if line:
            yield json.loads(line)


def _demo() -> int:
    """Self-test: warm once, parse a tiny fixture twice in the SAME process,
    and show that the 2nd parse is cheap (no reload). Returns process exit code."""
    fixt = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures",
                        "gaussian_clusters_3.pdf")
    fixt = os.path.abspath(fixt)
    out = "/tmp/warm_demo_out"
    print(f"[demo] PDFCHART_OCR={os.environ.get('PDFCHART_OCR', '1')}", flush=True)

    t0 = time.perf_counter()
    _warm()
    print(f"[demo] warm-up (one-time): {time.perf_counter() - t0:.2f}s", flush=True)

    for i in (1, 2):
        resp = _handle({"pdf": fixt, "outroot": out})
        print(f"[demo] parse #{i}: ok={resp['ok']} rows={resp.get('rows')} "
              f"secs={resp['secs']}", flush=True)
        if not resp["ok"]:
            print(f"[demo] FAILED: {resp.get('error')}", flush=True)
            return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workers", type=int, default=len(os.sched_getaffinity(0)),
                    help="warm worker processes (default: available CPUs); "
                         "1 keeps everything in this process")
    ap.add_argument("--demo", action="store_true",
                    help="run the built-in self-test instead of reading stdin")
    args = ap.parse_args()

    if args.demo:
        return _demo()

    reqs = _iter_stdin()
    if args.workers <= 1:
        # Single-image / interactive mode: no worker pool to oversubscribe, so let
        # OCR use several intra-op threads to cut per-image latency (~20%). The
        # pool path leaves the default (1 thread per worker). Respect an explicit
        # user override. Thread count does not change OCR output.
        os.environ.setdefault(
            "PDFCHART_OCR_THREADS", str(min(8, len(os.sched_getaffinity(0)))))
        _serve_single(reqs)
    else:
        _serve_pool(reqs, args.workers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
