"""Build the ``semiconductor_groundtruth_v1`` VLM dataset.

For each IN-SCOPE chart (arxiv_semicond/figures/, the sorted set), this:
  1. RE-PARSES its PDF page with the CURRENT extractor (cli.parse_pdf) into a
     fresh _parsed/ tree (extraction is still changing, so we regenerate),
  2. maps the re-parsed regions back to the chart_id by REGION-BBOX IoU against
     the original detection (robust to region renumbering),
  3. gates on "did it extract to usable data" (status extracted + both axis
     calibrations + >=1 series with >=4 points) -- type-sorting is already done
     by the in-scope set, so this is only the technical extraction filter,
  4. CONVERTS the record -> ground-truth schema (groundtruth_export),
  5. RENDERS the reconstruction (render_restyle_prototype._recon_figure) to
     <chart_id>.eps (vector master) and <chart_id>.png (~200 dpi, VLM input).

Outputs under /network/projects/sail/chart2table/semiconductor_groundtruth_v1/:
  images/  eps/  annotations/  dataset.jsonl  manifest.csv  rejected.csv  README.md

Compute node only (re-parse + mathtext/usetex). Multithreaded by available CPUs.

Usage:
    uv run python scripts/build_groundtruth_dataset.py --sample 5      # preview
    uv run python scripts/build_groundtruth_dataset.py --all [--jobs N]
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import random
import sys

os.environ.setdefault("MPLBACKEND", "Agg")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))  # for render_restyle_prototype / recon_reprocess

CORPUS = "/network/projects/sail/chart2table/arxiv_semicond"
FIGURES = os.path.join(CORPUS, "figures")
FIGIDX = os.path.join(CORPUS, "figures_index.csv")
OLD_EXTRACT = os.path.join(CORPUS, "extract_out")
PDF_DIR = os.path.join(CORPUS, "pdfs")

DEST = "/network/projects/sail/chart2table/semiconductor_groundtruth_v1"
PARSED = os.path.join(DEST, "_parsed")

PNG_DPI = 200
MIN_PTS = 4
MIN_IOU = 0.5


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _cid_parts(cid: str):
    aid, rest = cid.rsplit("_p", 1)
    page, chart = rest.split("c")
    return aid, int(page), int(chart)


def _iou(a, b):
    if not a or not b:
        return 0.0
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    aa = (a[2] - a[0]) * (a[3] - a[1])
    bb = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (aa + bb - inter)


def _old_bbox(aid, page, chart):
    """Region bbox from the ORIGINAL extraction (skip json carries it too)."""
    for name in (f"page{page}_chart{chart}.json", f"page{page}_chart{chart}.skip.json"):
        p = os.path.join(OLD_EXTRACT, aid, name)
        if os.path.exists(p):
            try:
                d = json.load(open(p))
                return (d.get("source") or {}).get("region_bbox")
            except Exception:
                return None
    return None


def _eligible(rec: dict) -> bool:
    if not rec or rec.get("status") == "skipped" or "style" not in rec:
        return False
    if not (rec.get("x_axis") or {}).get("calibration"):
        return False
    if not (rec.get("y_axis") or {}).get("calibration"):
        return False
    ser = rec.get("series") or []
    return any(len(s.get("points") or []) >= MIN_PTS for s in ser)


def _match_record(aid, page, old_bbox):
    """Best re-parsed non-skip record on (aid,page) by bbox IoU vs old_bbox."""
    best, best_iou = None, 0.0
    for jp in glob.glob(os.path.join(PARSED, aid, f"page{page}_chart*.json")):
        if jp.endswith(".skip.json"):
            continue
        try:
            d = json.load(open(jp))
        except Exception:
            continue
        bb = (d.get("source") or {}).get("region_bbox")
        i = _iou(old_bbox, bb) if old_bbox else 0.0
        if i > best_iou:
            best, best_iou = d, i
    return best, best_iou


# --------------------------------------------------------------------------
# per-chart worker (one chart_id end-to-end)
# --------------------------------------------------------------------------

def _emit_original(rec, cid):
    """Write the ORIGINAL chart region as vector PDF + hi-dpi PNG (always) and a
    best-effort EPS (svglib+reportlab; lossy on clip-groups, large). Into
    DEST/originals/."""
    import fitz
    from pdf_chart2table import io_store
    src = rec["source"]
    odir = os.path.join(DEST, "originals")
    os.makedirs(odir, exist_ok=True)
    crop_pdf = os.path.join(odir, f"{cid}_original.pdf")
    io_store.crop_region_pdf(src["pdf"], src["page"], src["region_bbox"], crop_pdf)
    # accurate raster of the original (for easy visual compare)
    doc = fitz.open(crop_pdf)
    try:
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(PNG_DPI / 72.0, PNG_DPI / 72.0))
        pix.save(os.path.join(odir, f"{cid}_original.png"))
    finally:
        doc.close()
    # best-effort original EPS via SVG -> reportlab (skip silently if unavailable)
    if os.environ.get("GT_ORIG_EPS") == "1":
        try:
            crop_svg = os.path.join(odir, f"{cid}_original.svg")
            io_store._write_svg_from_pdf(crop_pdf, crop_svg)
            from svglib.svglib import svg2rlg
            from reportlab.graphics import renderPS
            renderPS.drawToFile(svg2rlg(crop_svg),
                                os.path.join(odir, f"{cid}_original.eps"))
        except Exception:
            pass


def _init_worker(dest, parsed):
    """Propagate the active output dirs to each spawned worker (spawn does NOT
    inherit main()'s global reassignment)."""
    global DEST, PARSED
    DEST, PARSED = dest, parsed


def _process(task):
    """task = (chart_id, aid, page, chart, old_bbox). Returns a result dict."""
    cid, aid, page, chart, old_bbox = task
    from pdf_chart2table.groundtruth_export import record_to_groundtruth
    res = {"chart_id": cid, "arxiv_id": aid, "page": page}

    pdf = os.path.join(PDF_DIR, f"{aid}.pdf")
    if not os.path.exists(pdf):
        res["status"] = "reject"; res["reason"] = "pdf_missing"; return res

    # 1. re-parse this page (idempotent: writes PARSED/<aid>/page{page}_chart*.json)
    parsed_dir = os.path.join(PARSED, aid)
    if not glob.glob(os.path.join(parsed_dir, f"page{page}_chart*.json")):
        if os.environ.get("GT_NO_LAZY_PARSE") == "1":
            res["status"] = "reject"; res["reason"] = "not_parsed"; return res
        try:
            from pdf_chart2table.cli import parse_pdf
            parse_pdf(pdf, PARSED, pages_spec=str(page))
        except Exception as e:
            res["status"] = "reject"; res["reason"] = f"parse:{type(e).__name__}"; return res

    # 2. map back by bbox IoU
    rec, iou = _match_record(aid, page, old_bbox)
    if rec is None or iou < MIN_IOU:
        res["status"] = "reject"; res["reason"] = f"no_region_match(iou={iou:.2f})"; return res

    # 3. eligibility gate
    if not _eligible(rec):
        res["status"] = "reject"; res["reason"] = "not_extracted_or_uncalibrated"; return res

    # Optional connection-order fix (analysis-by-synthesis; EXPENSIVE -- renders
    # candidate orderings). Off by default: extraction already emits points in
    # draw order. Enable with --reprocess for max line-connection fidelity.
    if os.environ.get("GT_REPROCESS") == "1":
        try:
            from recon_reprocess import reprocess_record
            reprocess_record(rec)
        except Exception:
            pass

    style = rec["style"]
    n_series = len(rec.get("series") or [])
    n_points = sum(len(s.get("points") or []) for s in (rec.get("series") or []))

    # 4. convert
    gt = record_to_groundtruth(rec)

    # 5. render eps + png
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from render_restyle_prototype import _recon_figure, _LATEX_OK
    txt = style.get("text") or {}
    out_eps = os.path.join(DEST, "eps", f"{cid}.eps")
    out_png = os.path.join(DEST, "images", f"{cid}.png")
    use_tex = bool(_LATEX_OK and txt.get("latex_like"))
    done = False
    if use_tex:
        try:
            with plt.rc_context({"text.usetex": True,
                                 "text.latex.preamble": r"\usepackage{type1cm}"}):
                fig = _recon_figure(rec, style, tex=True)
                fig.savefig(out_eps, format="eps"); fig.savefig(out_png, dpi=PNG_DPI)
                plt.close(fig)
            done = True
        except Exception:
            plt.close("all")
    if not done:
        try:
            fig = _recon_figure(rec, style)
            fig.savefig(out_eps, format="eps"); fig.savefig(out_png, dpi=PNG_DPI)
            plt.close(fig)
        except Exception as e:
            plt.close("all")
            res["status"] = "reject"; res["reason"] = f"render:{type(e).__name__}"; return res

    # annotation
    with open(os.path.join(DEST, "annotations", f"{cid}.json"), "w") as f:
        json.dump(gt, f)

    # optional ORIGINAL artifacts (vector PDF crop + hi-dpi PNG + best-effort EPS)
    if os.environ.get("GT_ORIGINALS") == "1":
        try:
            _emit_original(rec, cid)
        except Exception as e:
            res["orig_warn"] = f"{type(e).__name__}"

    res.update({"status": "ok", "chart_type": gt["chart_type"],
                "n_series": n_series, "n_points": n_points,
                "confidence": rec.get("confidence"), "match_iou": round(iou, 3),
                "annotation": gt})
    return res


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

def _parse_paper(task):
    """Parse ALL needed pages of one paper into PARSED (one paper per worker, so
    charts sharing a page never race on the same JSON). task = (aid, [pages])."""
    aid, pages = task
    pdf = os.path.join(PDF_DIR, f"{aid}.pdf")
    if not os.path.exists(pdf):
        return (aid, "no_pdf")
    from pdf_chart2table.cli import parse_pdf
    done = 0
    for page in sorted(set(pages)):
        if glob.glob(os.path.join(PARSED, aid, f"page{page}_chart*.json")):
            continue  # already parsed (resume)
        try:
            parse_pdf(pdf, PARSED, pages_spec=str(page))
            done += 1
        except Exception as e:
            return (aid, f"parse:{type(e).__name__}")
    return (aid, f"ok({done})")


def _in_scope_tasks():
    ids = {os.path.splitext(x)[0] for x in os.listdir(FIGURES) if x.endswith(".png")}
    tasks = []
    with open(FIGIDX) as f:
        for r in csv.DictReader(f):
            cid = r["chart_id"]
            if cid not in ids:
                continue
            aid, page, chart = _cid_parts(cid)
            tasks.append((cid, aid, page, chart, _old_bbox(aid, page, chart)))
    return tasks


def _write_readme():
    txt = f"""# semiconductor_groundtruth_v1

Ground-truth dataset for chart-understanding VLMs (e.g. Qwen-VL). Each example
pairs a rendered chart image with a data-only JSON label. The image is the
RECONSTRUCTION rendered from data we extracted from a real arXiv semiconductor
figure, so image and label are consistent by construction.

## Layout
- `images/<chart_id>.png` — VLM input (raster reconstruction, {PNG_DPI} dpi).
- `eps/<chart_id>.eps`     — vector master (same reconstruction; re-rasterize at any dpi).
- `annotations/<chart_id>.json` — label, schema below.
- `originals/<chart_id>_original.pdf` — the ORIGINAL chart region, unmodified vectors
  (faithful source); `originals/<chart_id>_original.png` — its {PNG_DPI} dpi raster (for compare).
- `dataset.jsonl` — one row per chart with the annotation inlined + provenance.
- `manifest.csv` — included charts + counts; `rejected.csv` — in-scope charts that
  did not extract to usable data (with reason).
- `render_from_groundtruth.py` — regenerate a chart FROM a label JSON (edit the JSON,
  re-render): `python render_from_groundtruth.py annotations/<id>.json -o out` (matplotlib only).

## Label schema (data only; plotting STYLE is intentionally dropped)
```
chart_type   : "line" | "scatter"
title, x_label, y_label : strings ("" when not recovered)
topic        : "" (not populated in v1)
llm_plot_style : ""          # provenance: not generated
llm_data_mode  : "extracted" # extracted from a real figure (vs "generator")
series[] : {{ name, x_raw[], y_raw[], has_width(false), bbox_sizes([]),
             has_error_band, yerr_raw[] }}
```
`x_raw`/`y_raw` are real data-coordinate values in draw order. Error BARS (per-point
uncertainty we recover) are surfaced via `has_error_band`/`yerr_raw` (the schema has
no separate error-bar field); `yerr_raw` is aligned to `x_raw` (0.0 where absent).
`has_width`/`bbox_sizes` are always false/[] (bar charts are out of scope).

## Source / license
Charts extracted from arXiv semiconductor preprints (see `arxiv_id`/`page` in the
manifest). Respect arXiv's licensing terms of the source papers.
"""
    with open(os.path.join(DEST, "README.md"), "w") as f:
        f.write(txt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0,
                    help="produce this many eligible charts (preview) into _samples/")
    ap.add_argument("--all", action="store_true", help="build the full dataset")
    ap.add_argument("--jobs", type=int, default=len(os.sched_getaffinity(0)))
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--reprocess", action="store_true",
                    help="run analysis-by-synthesis connection-order fix (slow)")
    ap.add_argument("--exclude-papers", default="",
                    help="comma-separated arxiv_ids to drop (e.g. parse stragglers)")
    ap.add_argument("--skip-parse", action="store_true",
                    help="skip the parse pass + lazy parse; render only over what is "
                         "already in _parsed/ (charts with no parse -> rejected). Use to "
                         "resume to render without re-hitting slow stragglers.")
    args = ap.parse_args()
    excl_papers = {a for a in args.exclude_papers.split(",") if a}
    if args.skip_parse:
        os.environ["GT_NO_LAZY_PARSE"] = "1"
    if args.reprocess:
        os.environ["GT_REPROCESS"] = "1"
    if not args.sample and not args.all:
        ap.error("pass --sample N or --all")

    global DEST, PARSED
    if args.sample:
        DEST = os.path.join("/network/projects/sail/chart2table/semiconductor_groundtruth_v1",
                            "_samples")
        PARSED = os.path.join(DEST, "_parsed")
    for sub in ("images", "eps", "annotations"):
        os.makedirs(os.path.join(DEST, sub), exist_ok=True)
    os.makedirs(PARSED, exist_ok=True)

    tasks = _in_scope_tasks()
    if excl_papers:
        before = len(tasks)
        tasks = [t for t in tasks if t[1] not in excl_papers]
        print(f"excluded papers {sorted(excl_papers)}: dropped {before - len(tasks)} charts",
              flush=True)
    print(f"in-scope charts: {len(tasks)}", flush=True)
    random.Random(args.seed).shuffle(tasks)

    jobs = max(1, min(args.jobs, len(tasks)))
    import multiprocessing as mp
    ctx = mp.get_context("spawn")

    # FULL run: dedicated PARSE PASS first (one paper per worker -> no intra-page
    # race on shared JSON). Sample mode keeps the lazy per-chart parse.
    if args.all and not args.skip_parse:
        pages_by_aid = {}
        for (_cid, aid, page, _chart, _bb) in tasks:
            pages_by_aid.setdefault(aid, set()).add(page)
        ptasks = [(aid, list(pgs)) for aid, pgs in pages_by_aid.items()]
        print(f"parse pass: {len(ptasks)} papers", flush=True)
        with ctx.Pool(jobs, initializer=_init_worker, initargs=(DEST, PARSED)) as ppool:
            pdone = 0
            for aid, msg in ppool.imap_unordered(_parse_paper, ptasks, chunksize=2):
                pdone += 1
                if pdone % 200 == 0:
                    print(f"  parsed {pdone}/{len(ptasks)} papers", flush=True)
        print(f"parse pass done ({len(ptasks)} papers)", flush=True)

    ok_rows, rej_rows = [], []
    target = args.sample if args.sample else len(tasks)

    # For --sample we oversample and stop once `target` succeed; for --all we run
    # everything. Chunked so sample mode can short-circuit.
    pool = ctx.Pool(jobs, initializer=_init_worker, initargs=(DEST, PARSED))
    try:
        for res in pool.imap_unordered(_process, tasks, chunksize=1):
            if res["status"] == "ok":
                ok_rows.append(res)
                print(f"  ok {res['chart_id']} ({res['chart_type']}, "
                      f"{res['n_series']}s/{res['n_points']}p, iou={res['match_iou']})",
                      flush=True)
            else:
                rej_rows.append(res)
            if args.sample and len(ok_rows) >= target:
                break
    finally:
        pool.terminate(); pool.join()

    # write dataset.jsonl + manifest + rejected + README
    with open(os.path.join(DEST, "dataset.jsonl"), "w") as f:
        for r in ok_rows:
            f.write(json.dumps({
                "chart_id": r["chart_id"],
                "image": f"images/{r['chart_id']}.png",
                "eps": f"eps/{r['chart_id']}.eps",
                "arxiv_id": r["arxiv_id"], "page": r["page"],
                "chart_type": r["chart_type"], "n_series": r["n_series"],
                "n_points": r["n_points"], "confidence": r["confidence"],
                "annotation": r["annotation"],
            }) + "\n")
    with open(os.path.join(DEST, "manifest.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["chart_id", "arxiv_id", "page", "chart_type", "n_series",
                    "n_points", "confidence", "match_iou"])
        for r in ok_rows:
            w.writerow([r["chart_id"], r["arxiv_id"], r["page"], r["chart_type"],
                        r["n_series"], r["n_points"], r["confidence"], r["match_iou"]])
    with open(os.path.join(DEST, "rejected.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["chart_id", "arxiv_id", "page", "reason"])
        for r in rej_rows:
            w.writerow([r["chart_id"], r["arxiv_id"], r["page"], r.get("reason", "")])
    _write_readme()
    # ship the standalone regenerator inside the dataset (edit a JSON -> re-render)
    try:
        import shutil as _sh
        _sh.copy(os.path.join(os.path.dirname(__file__), "render_from_groundtruth.py"),
                 os.path.join(DEST, "render_from_groundtruth.py"))
    except Exception:
        pass
    print(f"\nDONE: {len(ok_rows)} ok, {len(rej_rows)} rejected -> {DEST}", flush=True)


if __name__ == "__main__":
    main()
