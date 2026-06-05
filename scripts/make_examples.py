"""Build a few end-to-end example bundles for inspection.

For each (pdf, page) chart target it runs the CLI pipeline, then renders a
reconstruction image: the original chart region with the extracted marker
positions overlaid (pixel-space, the detection check) beside a re-plot of the
extracted (x, y) data (the calibration check). Each bundle (source PDF, vector
crop pdf/svg, json, markers.csv, reconstruction png) is copied to an output dir.

Usage:
    uv run python scripts/make_examples.py --out $HOME/shared_folder/pdf_chart2table_examples [--n 5]

Light enough for a login node: it only processes the given chart pages.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import tempfile

import fitz
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from pdf_chart2table import cli

CORPUS = "data/real_pdfs"
# (arxiv id, 0-based page) targets drawn from tests/real/labels.json chart_pages,
# ordered to prefer matplotlib papers likely to carry markers.
TARGETS = [
    ("1412.6980", 5), ("1412.6980", 6), ("1502.03167", 5), ("1502.03167", 6),
    ("1512.03385", 4), ("1512.03385", 7), ("1608.03983", 4), ("1608.03983", 9),
    ("1609.04836", 4), ("1609.04836", 5), ("1812.01097", 4), ("2010.11929", 5),
]


def render_reconstruction(src_pdf, page0, record, out_png):
    """Original region + overlaid extracted marker pixels | data re-plot."""
    bbox = record["source"]["region_bbox"]
    margin = 12.0
    clip = fitz.Rect(bbox[0] - margin, bbox[1] - margin,
                     bbox[2] + margin, bbox[3] + margin)
    doc = fitz.open(src_pdf)
    dpi = 150
    pix = doc[page0].get_pixmap(dpi=dpi, clip=clip)
    img = pix.tobytes("png")
    import io
    from PIL import Image
    arr = Image.open(io.BytesIO(img))

    fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(12, 5))
    ax0.imshow(arr)
    ax0.set_title("original + extracted marker pixels")
    ax0.axis("off")
    s = dpi / 72.0
    for ser in record["series"]:
        xs = [(p["x_px"] - clip.x0) * s for p in ser["points"]]
        ys = [(p["y_px"] - clip.y0) * s for p in ser["points"]]
        ax0.scatter(xs, ys, s=18, facecolors="none", edgecolors="red", linewidths=0.8)

    ax1.set_title("reconstructed data (extracted x,y)")
    for ser in record["series"]:
        xs = [p["x"] for p in ser["points"]]
        ys = [p["y"] for p in ser["points"]]
        ax1.scatter(xs, ys, s=18, label=ser.get("label") or ser.get("marker"))
    if record["x_axis"].get("scale") == "log":
        ax1.set_xscale("log")
    if record["y_axis"].get("scale") == "log":
        ax1.set_yscale("log")
    ax1.set_xlabel(record["x_axis"].get("title") or "")
    ax1.set_ylabel(record["y_axis"].get("title") or "")
    if any(s.get("label") for s in record["series"]):
        ax1.legend(fontsize=8)
    fig.suptitle(record.get("title", {}).get("text") if isinstance(record.get("title"), dict) else record.get("title"))
    fig.tight_layout()
    fig.savefig(out_png, dpi=110)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=5)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    tmp = tempfile.mkdtemp(prefix="examples_")
    chosen = []
    for arxiv_id, page0 in TARGETS:
        src = os.path.join(CORPUS, f"{arxiv_id}.pdf")
        if not os.path.exists(src):
            continue
        try:
            rows = cli.parse_pdf(src, tmp, str(page0 + 1))
        except Exception as e:
            print(f"  parse failed {arxiv_id} p{page0}: {e}")
            continue
        for r in rows:
            if r.get("status") != "extracted":
                continue
            jpath = r["json"] if "json" in r else None
            # locate the json written for this page
            stem = os.path.splitext(os.path.basename(src))[0]
            cand = sorted(glob.glob(os.path.join(tmp, stem, f"page{page0+1}_chart*.json")))
            cand = [p for p in cand if not p.endswith(".skip.json")]
            for jp in cand:
                rec = json.load(open(jp))
                if "series" not in rec:
                    continue
                nser = len(rec["series"])
                npts = sum(len(s["points"]) for s in rec["series"])
                tag = f"{arxiv_id}_p{page0}_{os.path.basename(jp).split('.')[0]}"
                if not any(c["tag"] == tag for c in chosen):
                    chosen.append({"tag": tag, "src": src, "page0": page0,
                                   "json": jp, "rec": rec, "nser": nser, "npts": npts})
            break
        # prefer ones with series; keep scanning
    # rank: series>0 first, more points first; then keep at most one per paper
    chosen.sort(key=lambda c: (c["nser"] == 0, -c["npts"]))
    seen_papers, deduped = set(), []
    for c in chosen:
        pid = c["tag"].split("_")[0]
        if pid in seen_papers:
            continue
        seen_papers.add(pid)
        deduped.append(c)
    chosen = deduped[:args.n]

    print(f"Selected {len(chosen)} examples:")
    for c in chosen:
        dest = os.path.join(args.out, c["tag"])
        os.makedirs(dest, exist_ok=True)
        base = c["json"][:-5]  # strip .json
        shutil.copy(c["src"], os.path.join(dest, "paper.pdf"))
        for ext, name in [(".json", "chart.json"), (".markers.csv", "chart.markers.csv"),
                          (".pdf", "chart_crop.pdf"), (".svg", "chart_crop.svg")]:
            p = base + ext
            if os.path.exists(p):
                shutil.copy(p, os.path.join(dest, name))
        try:
            render_reconstruction(c["src"], c["page0"], c["rec"],
                                  os.path.join(dest, "reconstruction.png"))
        except Exception as e:
            print(f"  reconstruction render failed for {c['tag']}: {e}")
        print(f"  {c['tag']}: n_series={c['nser']} n_points={c['npts']} -> {dest}")
    print(f"\nDone -> {args.out}")


if __name__ == "__main__":
    main()
