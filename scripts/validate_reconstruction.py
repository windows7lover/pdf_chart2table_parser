"""Visual validation of chart reconstruction on the real-PDF corpus.

Reads the batch ``manifest.csv`` + per-chart JSON records, picks a stratified
sample of extracted charts (scatter vs line+markers, linear vs log, single vs
multi-series), and for each emits two side-by-side PNGs:

  a. PIXEL OVERLAY: source page rendered at DPI with extracted (x_px,y_px)
     overlaid (point*dpi/72, top-left origin, no y-flip) -- detection check.
  b. RECONSTRUCTION: re-plot extracted (x,y) with matplotlib using the axis
     scale, beside the rendered region crop -- calibration check.

Both panels for a chart are stacked into one ``<stem>.png`` under the report
dir, so a single Read shows detection + reconstruction together.

Run: uv run python scripts/validate_reconstruction.py \
        --pdfs "$SCRATCH/pdf_chart2table/pdfs" \
        --out  "$SCRATCH/pdf_chart2table/out" \
        --report docs/real_validation --n 28
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random

import fitz
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def _read_manifest(out: str) -> list[dict]:
    with open(os.path.join(out, "manifest.csv")) as f:
        return list(csv.DictReader(f))


def _record_path(out: str, row: dict) -> str:
    stem = os.path.splitext(os.path.basename(row["file"]))[0]
    return os.path.join(out, stem, f"page{row['page']}_chart{row['chart']}.json")


def _load_record(path: str) -> dict | None:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _stratum(rec: dict) -> str:
    """Coarse strata key for sampling diversity."""
    xs = rec.get("x_axis") or {}
    ys = rec.get("y_axis") or {}
    log = "log" if "log" in (xs.get("scale"), ys.get("scale")) else "lin"
    n = len(rec.get("series", []))
    multi = "multi" if n > 1 else "single"
    markers = {s.get("marker") for s in rec.get("series", [])}
    kind = "scatter" if markers and None not in markers else "line"
    return f"{kind}-{log}-{multi}"


def _render_page(pdf: str, page_index: int, dpi: int):
    doc = fitz.open(pdf)
    try:
        pix = doc[page_index].get_pixmap(dpi=dpi)
        import numpy as np
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n)
        return img[:, :, :3].copy()
    finally:
        doc.close()


def _render_crop(out: str, row: dict, dpi: int):
    stem = os.path.splitext(os.path.basename(row["file"]))[0]
    crop = os.path.join(out, stem, f"page{row['page']}_chart{row['chart']}.pdf")
    if not os.path.exists(crop):
        return None
    doc = fitz.open(crop)
    try:
        pix = doc[0].get_pixmap(dpi=dpi)
        import numpy as np
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n)
        return img[:, :, :3].copy()
    finally:
        doc.close()


def _make_figure(rec: dict, row: dict, pdfs: str, out: str, dst: str,
                 dpi: int = 150) -> None:
    pdf = os.path.join(pdfs, os.path.basename(rec["source"]["pdf"]))
    if not os.path.exists(pdf):
        pdf = rec["source"]["pdf"]
    page_idx = rec["source"]["page"]
    bbox = rec["source"]["region_bbox"]
    scale = dpi / 72.0

    page_img = _render_page(pdf, page_idx, dpi)
    crop_img = _render_crop(out, row, dpi)

    fig, axs = plt.subplots(1, 3, figsize=(18, 6))

    # (a) Pixel overlay on the cropped region of the page.
    x0, y0, x1, y1 = [c * scale for c in bbox]
    m = 18 * scale
    axs[0].imshow(page_img)
    colors = plt.cm.tab10.colors
    for i, s in enumerate(rec.get("series", [])):
        px = [p["x_px"] * scale for p in s["points"]]
        py = [p["y_px"] * scale for p in s["points"]]
        axs[0].scatter(px, py, s=30, facecolors="none",
                       edgecolors=colors[i % 10], linewidths=1.2)
    axs[0].set_xlim(x0 - m, x1 + m)
    axs[0].set_ylim(y1 + m, y0 - m)  # top-left origin -> invert y display
    npts = sum(len(s["points"]) for s in rec.get("series", []))
    axs[0].set_title(f"(a) overlay: {len(rec.get('series', []))} series, "
                     f"{npts} pts")

    # (b) Original rendered crop.
    if crop_img is not None:
        axs[1].imshow(crop_img)
    axs[1].set_title("(b) original crop")
    axs[1].axis("off")

    # (c) Reconstruction from extracted (x,y).
    for i, s in enumerate(rec.get("series", [])):
        xs = [p["x"] for p in s["points"]]
        ys = [p["y"] for p in s["points"]]
        order = sorted(range(len(xs)), key=lambda j: xs[j])
        xs = [xs[j] for j in order]
        ys = [ys[j] for j in order]
        lbl = s.get("label") or f"series {i+1}"
        axs[2].plot(xs, ys, marker="o", ms=3, color=colors[i % 10], label=lbl)
    xa = rec.get("x_axis") or {}
    ya = rec.get("y_axis") or {}
    if xa.get("scale") == "log":
        axs[2].set_xscale("log")
    if ya.get("scale") == "log":
        axs[2].set_yscale("log")
    axs[2].set_xlabel((xa.get("title") or "x") + f"  [{xa.get('scale')}]")
    axs[2].set_ylabel((ya.get("title") or "y") + f"  [{ya.get('scale')}]")
    axs[2].set_title("(c) reconstruction")
    if any(s.get("label") for s in rec.get("series", [])):
        axs[2].legend(fontsize=7)

    title = rec.get("title") or {}
    fig.suptitle(f"{os.path.basename(pdf)} p{page_idx+1} "
                 f"[{_stratum(rec)}]  title={title.get('text')!r}",
                 fontsize=10)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(dst, dpi=90)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdfs", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--n", type=int, default=28)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    os.makedirs(args.report, exist_ok=True)
    rng = random.Random(args.seed)

    rows = [r for r in _read_manifest(args.out) if r["status"] == "extracted"]
    # Group by stratum.
    strata: dict[str, list[tuple[dict, dict]]] = {}
    for r in rows:
        rec = _load_record(_record_path(args.out, r))
        if rec is None or not rec.get("series"):
            continue
        strata.setdefault(_stratum(rec), []).append((r, rec))

    # Round-robin sample across strata for diversity.
    for v in strata.values():
        rng.shuffle(v)
    keys = sorted(strata)
    sample: list[tuple[dict, dict]] = []
    i = 0
    while len(sample) < args.n and any(strata.values()):
        k = keys[i % len(keys)]
        if strata[k]:
            sample.append(strata[k].pop())
        i += 1
        if i > 10000:
            break

    print("strata sizes:", {k: len(v) for k, v in strata.items()})
    index = []
    for j, (row, rec) in enumerate(sample, 1):
        stem = (os.path.splitext(os.path.basename(rec['source']['pdf']))[0]
                + f"_p{rec['source']['page']+1}_c{row['chart']}")
        dst = os.path.join(args.report, f"{j:02d}_{stem}.png")
        try:
            _make_figure(rec, row, args.pdfs, args.out, dst)
            index.append((dst, _stratum(rec), rec))
            print(f"[{j}/{len(sample)}] {dst}")
        except Exception as e:
            print(f"[{j}] FAILED {stem}: {e}")

    with open(os.path.join(args.report, "sample_index.csv"), "w",
              newline="") as f:
        w = csv.writer(f)
        w.writerow(["image", "stratum", "pdf", "page", "n_series", "n_points",
                    "x_scale", "y_scale", "title"])
        for dst, strat, rec in index:
            npts = sum(len(s["points"]) for s in rec.get("series", []))
            t = (rec.get("title") or {}).get("text")
            w.writerow([os.path.basename(dst), strat,
                        os.path.basename(rec["source"]["pdf"]),
                        rec["source"]["page"] + 1,
                        len(rec.get("series", [])), npts,
                        (rec.get("x_axis") or {}).get("scale"),
                        (rec.get("y_axis") or {}).get("scale"), t])
    print(f"wrote {len(index)} sample figures to {args.report}")


if __name__ == "__main__":
    main()
