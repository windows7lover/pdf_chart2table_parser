"""Render an original chart region and its reconstruction onto a MATCHED pixel
grid and report image-space fidelity (analysis-by-synthesis).

The original plot box is exactly ``record['source']['region_bbox']`` (the
calibration places the spines at the region edges); chart.json stores each
axis' ``pixel_range`` (= region edges) and ``data_range``. We rasterise:

  * the ORIGINAL by clipping the source PDF page to region_bbox (fitz), and
  * the RECONSTRUCTION as a matplotlib axes that FILLS the figure with
    xlim/ylim set to the data range (aspect forced auto, frame off),

both at the same pixel dimensions — so the calibrated data->pixel mapping is
identical and the curves overlay pixel-aligned. The pure ink-diff metrics live
in ``pdf_chart2table.recon_compare``.

Usage:
    uv run python scripts/recon_compare.py CHART_JSON [--scale 2.0] [--tol 2]
        [--dump-prefix /tmp/cmp]   # write orig/recon/diff PNGs
Run corpus-wide passes via srun, not on a login node.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import fitz  # noqa: E402
from PIL import Image  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from render_restyle_prototype import _replot  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))
from pdf_chart2table.recon_compare import compare, ink_mask  # noqa: E402


def render_original(record: dict, scale: float) -> np.ndarray:
    """Rasterise the source PDF page clipped to the plot box (region_bbox)."""
    bb = record["source"]["region_bbox"]
    clip = fitz.Rect(bb[0], bb[1], bb[2], bb[3])
    with fitz.open(record["source"]["pdf"]) as doc:
        pix = doc[record["source"]["page"]].get_pixmap(
            matrix=fitz.Matrix(scale, scale), clip=clip)
        img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    return np.asarray(img)


def _apply_axis_range(ax, record: dict) -> None:
    """Set xlim/ylim/scale to the calibrated data range so the axes data->pixel
    mapping matches the original plot box; force aspect auto + frame off."""
    xa, ya = record["x_axis"], record["y_axis"]
    if xa.get("scale") == "log":
        ax.set_xscale("log")
    if ya.get("scale") == "log":
        ax.set_yscale("log")
    xr, yr = xa.get("data_range"), ya.get("data_range")
    if xr:
        ax.set_xlim(xr[0], xr[1])
    if yr:
        # data_range is stored top->bottom (pixel order); set_ylim wants
        # (bottom, top). region_bbox top edge = first pixel_range entry, so the
        # second data_range entry is the BOTTOM value.
        ax.set_ylim(yr[1], yr[0])
    ax.set_aspect("auto")
    ax.axis("off")


def render_reconstruction(record: dict, h_px: int, w_px: int,
                          dpi: int = 100) -> np.ndarray:
    """Render the reconstruction as an axes filling a w_px x h_px figure, with
    the data range mapped to the figure edges (matched to the original grid)."""
    fig = plt.figure(figsize=(w_px / dpi, h_px / dpi), dpi=dpi)
    ax = fig.add_axes([0.0, 0.0, 1.0, 1.0])
    try:
        _replot(ax, record, record["style"])
    except Exception:
        pass
    _apply_axis_range(ax, record)
    fig.canvas.draw()
    buf = np.asarray(fig.canvas.buffer_rgba())[..., :3].copy()
    plt.close(fig)
    # buffer may be off by a row/col from rounding; crop/pad to exact size.
    buf = buf[:h_px, :w_px]
    if buf.shape[0] != h_px or buf.shape[1] != w_px:
        out = np.full((h_px, w_px, 3), 255, np.uint8)
        out[:buf.shape[0], :buf.shape[1]] = buf
        buf = out
    return buf


def compare_chart(chart_json: str, scale: float = 2.0, tol: int = 2,
                  dump_prefix: str | None = None) -> dict:
    record = json.load(open(chart_json))
    orig = render_original(record, scale)
    h, w = orig.shape[:2]
    recon = render_reconstruction(record, h, w)
    res = compare(orig, recon, tol=tol)
    out = res.as_dict()
    out["chart_id"] = os.path.basename(os.path.dirname(chart_json)) or chart_json
    if dump_prefix:
        Image.fromarray(orig).save(dump_prefix + "_orig.png")
        Image.fromarray(recon).save(dump_prefix + "_recon.png")
        om, rm = ink_mask(orig), ink_mask(recon)
        diff = np.full((h, w, 3), 255, np.uint8)
        diff[om & ~rm] = (220, 20, 60)    # missing (in orig, not recon) = red
        diff[rm & ~om] = (30, 120, 220)   # extra (in recon, not orig) = blue
        diff[om & rm] = (200, 200, 200)   # matched = grey
        Image.fromarray(diff).save(dump_prefix + "_diff.png")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("chart_json")
    ap.add_argument("--scale", type=float, default=2.0)
    ap.add_argument("--tol", type=int, default=2)
    ap.add_argument("--dump-prefix", default=None)
    args = ap.parse_args()
    res = compare_chart(args.chart_json, scale=args.scale, tol=args.tol,
                        dump_prefix=args.dump_prefix)
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
