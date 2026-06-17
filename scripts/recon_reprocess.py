"""Analysis-by-synthesis reprocessing of a chart.json: fix scrambled connection
order by rendering candidate orderings and keeping the one that best overlays
the original.

A merged multi-path curve is sometimes emitted with a bad point order (e.g.
x-sorted when the true curve is multi-valued, or no usable order at all), so the
rendered polyline self-crosses into a dense zigzag — visible as a large
over-draw (extra ink) in the recon-vs-original comparison. For each line series
we try alternative orderings (stored, x-sorted, nearest-neighbour) by RENDERING
the whole chart with that series reordered and measuring data-ink ink_iou vs the
original; we keep an alternative ONLY when it strictly improves the match (the
parser's stored order wins ties — conservative, never changes a good series).

This is decoupled from the extraction library: it operates on a written
chart.json and rewrites the point order in place (the order is mis-extracted
DATA the synthesis recovers; values are untouched). Run via srun for batches.

Usage:
    uv run python scripts/recon_reprocess.py CHART_JSON [--write] [--scale S]
        [--tol T] [--min-gain G]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from recon_compare import (  # noqa: E402
    render_original, render_reconstruction, text_ignore_mask,
)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))
from pdf_chart2table.recon_compare import (  # noqa: E402
    compare, order_xsort, order_nearest,
)

# A reordering is adopted only when it lifts data-ink ink_iou by at least this,
# so render noise / sub-pixel jitter never flips a series. Conservative.
_MIN_GAIN = 0.01


def _is_line_series(record: dict, i: int) -> bool:
    """True when series ``i`` is drawn with a connecting line, so its point ORDER
    affects the rendered ink (scatter-only series are order-invariant)."""
    st = (record.get("style", {}).get("series") or [])
    s = st[i] if i < len(st) else {}
    if s.get("connect"):
        return True
    return s.get("marker") in (None, "", "none") and s.get("linestyle") not in (
        None, "", "None", "none")


def _reordered(points: list[dict], order: list[int]) -> list[dict]:
    return [points[i] for i in order]


def reprocess_chart(chart_json: str, scale: float = 2.0, tol: int = 2,
                    min_gain: float = _MIN_GAIN, write: bool = False) -> dict:
    record = json.load(open(chart_json))
    orig = render_original(record, scale)
    h, w = orig.shape[:2]
    ign = text_ignore_mask(record, h, w, scale)

    def iou() -> float:
        recon = render_reconstruction(record, h, w)
        return compare(orig, recon, tol=tol, ignore_mask=ign).ink_iou

    base = iou()
    cur = base
    changes: list[dict] = []

    for i, ser in enumerate(record["series"]):
        pts = ser.get("points") or []
        if len(pts) < 4 or not _is_line_series(record, i):
            continue
        coords = [(p["x_px"], p["y_px"]) for p in pts]
        candidates = {
            "xsort": order_xsort(coords),
            "nearest": order_nearest(coords),
        }
        best_key, best_iou, best_pts = "stored", cur, pts
        for key, order in candidates.items():
            new_pts = _reordered(pts, order)
            record["series"][i]["points"] = new_pts          # try
            s = iou()
            if s > best_iou + min_gain:
                best_key, best_iou, best_pts = key, s, new_pts
        record["series"][i]["points"] = best_pts              # commit best/restore
        if best_key != "stored":
            changes.append({"series": i, "order": best_key,
                            "iou_before": round(cur, 4), "iou_after": round(best_iou, 4)})
            cur = best_iou

    out = {
        "chart_id": os.path.basename(os.path.dirname(chart_json)) or chart_json,
        "iou_before": round(base, 4),
        "iou_after": round(cur, 4),
        "changes": changes,
    }
    if changes and write:
        with open(chart_json, "w") as f:
            json.dump(record, f, indent=2)
        out["written"] = True
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("chart_json")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--scale", type=float, default=2.0)
    ap.add_argument("--tol", type=int, default=2)
    ap.add_argument("--min-gain", type=float, default=_MIN_GAIN)
    args = ap.parse_args()
    res = reprocess_chart(args.chart_json, scale=args.scale, tol=args.tol,
                          min_gain=args.min_gain, write=args.write)
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
