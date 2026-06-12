"""Residual audit: a self-supervised completeness check for the extractor.

Idea (the "residual method"): everything we correctly understood about a chart
should be SUBTRACTED from the vector. Whatever ink is left unexplained is what we
failed to account for -- a long unexplained polyline is almost certainly a DROPPED
data curve; a cluster of unexplained glyphs is a missed marker series.

This step 1 is pure instrumentation (no change to extraction). For each chart it
tags every region path as explained (matched to an extracted series, or recovered
decoration: ticks/spines/frame/grid/near-white background) or UNEXPLAINED, and
reports the residual -- especially LONG unexplained paths (candidate missed data).

Usage:
    uv run python scripts/residual_audit.py --root <root> [--chart <id>] [--overlay]
"""
from __future__ import annotations

import argparse
import csv
import glob
import io
import json
import os

import fitz
from PIL import Image

from pdf_chart2table import pdf_vector
from pdf_chart2table.refiners import is_decoration_line


def _rc(c, q=0.08):
    return tuple(round(v / q) * q for v in c[:3]) if c else None


def _in_region(b, x0, y0, x1, y1):
    return not (b[2] < x0 or b[0] > x1 or b[3] < y0 or b[1] > y1)


def audit_chart(chart_json):
    d = json.load(open(chart_json))
    src = d["source"]
    x0, y0, x1, y1 = src["region_bbox"]
    w, h = (x1 - x0) or 1.0, (y1 - y0) or 1.0
    diag = max(w, h)
    # extracted series as (rounded colour, sampled pixel points)
    series = []
    marker_pts = []
    for s in d.get("series", []):
        pts = [(p["x_px"], p["y_px"]) for p in s.get("points", [])
               if p.get("x_px") is not None]
        series.append((_rc(s["color"]) if s.get("color") else None, pts))
        if s.get("marker"):
            marker_pts.extend(pts)

    page0 = src["page"]
    pg = pdf_vector.load_pdf(src["pdf"], [page0])[0]
    paths = pg.paths
    # legend region (paths inside it are decoration/swatches, not unexplained data)
    legend_bbox = None
    try:
        from pdf_chart2table.plot_region import detect_regions
        from pdf_chart2table import labels as _labels
        regs = detect_regions(pg.paths, pg.texts, pg.width, pg.height,
                              image_rects=pg.image_rects)
        if regs:
            reg = min(regs, key=lambda r: abs(r.bbox[0] - x0) + abs(r.bbox[1] - y0))
            legend_bbox = _labels.detect_labels(reg, pg.paths, pg.texts).legend_bbox
    except Exception:
        pass
    inreg = [(i, p) for i, p in enumerate(paths)
             if _in_region(p.bbox, x0, y0, x1, y1)]

    explained, residual = 0, []
    for i, p in inreg:
        b = p.bbox
        bw, bh = b[2] - b[0], b[3] - b[1]
        cx, cy = 0.5 * (b[0] + b[2]), 0.5 * (b[1] + b[3])
        col = (_rc(p.stroke) if p.stroke is not None
               else _rc(p.fill) if p.fill is not None else None)
        # near-white background / frame fill
        if p.stroke is not None and min(p.stroke) > 0.92:
            explained += 1
            continue
        # ticks / spines / frame: short or axis-aligned near the border
        near_border = (abs(cx - x0) < 3 or abs(cx - x1) < 3
                       or abs(cy - y0) < 3 or abs(cy - y1) < 3)
        if min(bw, bh) < 2.0 and (near_border or max(bw, bh) < 8.0):
            explained += 1
            continue
        # grey background grid line (long thin interior)
        if (col and max(col) - min(col) <= 0.15 and 0.4 <= max(col) <= 0.96
                and ((bw > 0.6 * w and bh < 2) or (bh > 0.6 * h and bw < 2))):
            explained += 1
            continue
        # legend region: swatches/connectors/box inside the legend are decoration,
        # not unexplained data (they were correctly identified and removed).
        if legend_bbox is not None and _in_region(b, *legend_bbox):
            explained += 1
            continue
        # matched to an extracted series: same colour AND most of the PATH's own
        # points are covered by the series' points (so the series genuinely
        # accounts for this path's geometry, not just its centroid).
        pp = p.points[::max(1, len(p.points) // 30)] or p.points
        matched = False
        for scol, spts in series:
            if not (scol and col and scol == col and spts):
                continue
            sp = spts[::max(1, len(spts) // 200)]
            hit = sum(1 for qx, qy in pp
                      if min(abs(qx - sx) + abs(qy - sy) for sx, sy in sp) < 3.0)
            if hit >= 0.6 * len(pp):
                matched = True
                break
        if matched:
            explained += 1
            continue
        # refiner-dropped decoration: a connector through markers or a straight
        # reference/fit line was correctly removed from the series by
        # drop_spurious_lines -> count as explained, not unexplained residual.
        if marker_pts and is_decoration_line(p.points, marker_pts, diag):
            explained += 1
            continue
        residual.append({"idx": i, "color": col, "npts": len(p.points),
                         "span": round(max(bw, bh), 1), "bbox": [round(v, 1) for v in b]})
    # a residual path is a "candidate missed curve" if it is long + multi-vertex
    missed = [r for r in residual if r["span"] > 0.3 * diag and r["npts"] >= 6]
    return d, len(inreg), explained, residual, missed


def overlay(d, residual, out_png):
    src = d["source"]
    x0, y0, x1, y1 = src["region_bbox"]
    m = 12.0
    clip = fitz.Rect(x0 - m, y0 - m, x1 + m, y1 + m)
    with fitz.open(src["pdf"]) as doc:
        pix = doc[src["page"]].get_pixmap(dpi=150, clip=clip)
        arr = Image.open(io.BytesIO(pix.tobytes("png")))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    paths = pdf_vector.load_pdf(src["pdf"], [src["page"]])[0].paths
    s = 150 / 72.0
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.imshow(arr)
    ax.set_title(f"unexplained residual (red): {len(residual)} paths")
    ax.axis("off")
    for r in residual:
        pts = paths[r["idx"]].points
        xs = [(px - clip.x0) * s for px, _ in pts]
        ys = [(py - clip.y0) * s for _, py in pts]
        ax.plot(xs, ys, color="red", linewidth=1.0)
    fig.tight_layout()
    fig.savefig(out_png, dpi=110)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--chart", default=None, help="single chart_id (else all bundles)")
    ap.add_argument("--overlay", action="store_true")
    args = ap.parse_args()
    proto = os.path.join(args.root, "restyle_prototype")
    if args.chart:
        jsons = [os.path.join(proto, args.chart, "chart.json")]
    else:
        jsons = sorted(glob.glob(os.path.join(proto, "*", "chart.json")))
    rows = []
    for jp in jsons:
        cid = os.path.basename(os.path.dirname(jp))
        try:
            d, n_in, n_exp, residual, missed = audit_chart(jp)
        except Exception as e:
            print(f"  ERR {cid}: {e}", flush=True)
            continue
        frac = n_exp / n_in if n_in else 1.0
        print(f"{cid}: paths={n_in} explained={n_exp} ({frac:.0%}) "
              f"residual={len(residual)} candidate_missed_curves={len(missed)}",
              flush=True)
        for mcurve in missed[:4]:
            print(f"    MISSED color={mcurve['color']} npts={mcurve['npts']} "
                  f"span={mcurve['span']} bbox={mcurve['bbox']}", flush=True)
        rows.append({"chart_id": cid, "paths": n_in, "explained": n_exp,
                     "residual": len(residual), "missed_curves": len(missed)})
        if args.overlay:
            overlay(d, residual, os.path.join(proto, cid, f"{cid}_residual.png"))
    if rows:
        out = os.path.join(proto, "residual_audit.csv")
        with open(out, "w", newline="") as f:
            wr = csv.DictWriter(f, fieldnames=list(rows[0]))
            wr.writeheader()
            wr.writerows(rows)
        print(f"\n-> {out}")


if __name__ == "__main__":
    main()
