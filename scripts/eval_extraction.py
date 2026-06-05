"""Compare parser output (pred.json) to fixture ground truth (truth.json).

Matches predicted series to truth series (greedy by nearest centroid), matches
points nearest-neighbor in data space, and reports per-series median/max error
normalized by axis range. Errors are measured in LOG space when an axis scale is
"log". Exits nonzero if any matched series exceeds the tolerance, or if series
counts do not line up (precision/recall).

Precision over recall is the project's headline metric: emitting wrong numbers
is worse than skipping. This script makes that measurable.

Schemas
-------
pred.json (parser output):
  {"x_axis": {"scale": "linear|log", ...}, "y_axis": {...},
   "series": [{"label","marker","color","points":[{"x","y","x_px","y_px"},...]}]}
truth.json (fixture ground truth):
  {"x_axis": {"scale","lim":[lo,hi]}, "y_axis": {...},
   "series": [{"label","marker","color","x":[...],"y":[...]}]}

Run:
  uv run python scripts/eval_extraction.py --pred pred.json --truth tests/fixtures/<name>.json
Self-test (synthesize a perfect pred from a fixture's truth, expect ~0 error):
  uv run python scripts/eval_extraction.py --self-test tests/fixtures/<name>.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np


def _axis_transform(scale):
    """Return a function mapping data values to the space errors are measured in."""
    if scale == "log":
        return lambda v: np.log10(np.asarray(v, float))
    return lambda v: np.asarray(v, float)


def _axis_range(values_t):
    """Range of transformed values; guard against zero."""
    lo, hi = float(np.min(values_t)), float(np.max(values_t))
    rng = hi - lo
    return rng if rng > 0 else 1.0


def _truth_arrays(s):
    return np.asarray(s["x"], float), np.asarray(s["y"], float)


def _pred_arrays(s):
    pts = s["points"]
    return (np.array([p["x"] for p in pts], float),
            np.array([p["y"] for p in pts], float))


def _centroid(x, y, fx, fy):
    return np.array([np.mean(fx(x)), np.mean(fy(y))])


def match_series(truth, pred, fx, fy):
    """Greedy nearest-centroid matching (in transformed space).

    Returns list of (truth_idx, pred_idx) pairs.
    """
    t_cent = [_centroid(*_truth_arrays(s), fx, fy) for s in truth]
    p_cent = [_centroid(*_pred_arrays(s), fx, fy) for s in pred]
    pairs = []
    used_pred = set()
    # Normalize centroid distances by per-axis spread so x/y weigh evenly.
    all_t = np.array(t_cent) if t_cent else np.zeros((0, 2))
    scale = all_t.std(axis=0)
    scale[scale == 0] = 1.0
    for ti, tc in enumerate(t_cent):
        best, best_d = None, np.inf
        for pi, pc in enumerate(p_cent):
            if pi in used_pred:
                continue
            d = np.linalg.norm((tc - pc) / scale)
            if d < best_d:
                best, best_d = pi, d
        if best is not None:
            used_pred.add(best)
            pairs.append((ti, best))
    return pairs


def series_errors(t_series, p_series, fx, fy, x_range, y_range):
    """Per-point relative error (max over the two axes), nearest-neighbor matched."""
    tx, ty = _truth_arrays(t_series)
    px, py = _pred_arrays(p_series)
    tX, tY = fx(tx), fy(ty)
    pX, pY = fx(px), fy(py)

    rel = []
    for j in range(len(pX)):
        d = np.sqrt(((tX - pX[j]) / x_range) ** 2 + ((tY - pY[j]) / y_range) ** 2)
        k = int(np.argmin(d))
        ex = abs(tX[k] - pX[j]) / x_range
        ey = abs(tY[k] - pY[j]) / y_range
        rel.append(max(ex, ey))
    return np.array(rel) if rel else np.array([np.nan])


def evaluate(pred, truth, tol):
    xscale = truth["x_axis"]["scale"]
    yscale = truth["y_axis"]["scale"]
    fx, fy = _axis_transform(xscale), _axis_transform(yscale)

    # Axis ranges from the union of all truth values (transformed).
    all_x = np.concatenate([_truth_arrays(s)[0] for s in truth["series"]])
    all_y = np.concatenate([_truth_arrays(s)[1] for s in truth["series"]])
    x_range = _axis_range(fx(all_x))
    y_range = _axis_range(fy(all_y))

    pairs = match_series(truth["series"], pred["series"], fx, fy)

    print(f"axes: x={xscale}  y={yscale}")
    print(f"series: truth={len(truth['series'])}  pred={len(pred['series'])}  "
          f"matched={len(pairs)}")
    n_t, n_p = len(truth["series"]), len(pred["series"])
    precision = len(pairs) / n_p if n_p else 0.0
    recall = len(pairs) / n_t if n_t else 0.0
    print(f"precision={precision:.3f}  recall={recall:.3f}")
    print(f"tolerance: {tol:.3%} of axis range (errors in log space for log axes)")
    print("-" * 60)

    all_ok = True
    for ti, pi in pairs:
        ts, ps = truth["series"][ti], pred["series"][pi]
        rel = series_errors(ts, ps, fx, fy, x_range, y_range)
        med, mx = float(np.nanmedian(rel)), float(np.nanmax(rel))
        ok = mx <= tol
        all_ok &= ok
        tag = "PASS" if ok else "FAIL"
        print(f"[{tag}] truth[{ti}] {ts.get('label','?')!r:<22} <- "
              f"pred[{pi}] {ps.get('label','?')!r:<22} "
              f"median={med:.4%} max={mx:.4%} npts={len(ps['points'])}/{len(ts['x'])}")

    # Unmatched series hurt precision/recall and count as failure.
    if len(pairs) != n_t or len(pairs) != n_p:
        print(f"[FAIL] series mismatch: {n_t} truth vs {n_p} pred, {len(pairs)} matched")
        all_ok = False

    print("-" * 60)
    print("RESULT:", "PASS" if all_ok else "FAIL")
    return all_ok


def synth_pred_from_truth(truth):
    """Build a 'perfect' pred.json from a fixture's truth (for the self-test)."""
    series = []
    for s in truth["series"]:
        pts = [{"x": float(x), "y": float(y), "x_px": 0.0, "y_px": 0.0}
               for x, y in zip(s["x"], s["y"])]
        series.append({"label": s["label"], "marker": s.get("marker"),
                       "color": s.get("color"), "points": pts})
    return {"x_axis": {"scale": truth["x_axis"]["scale"]},
            "y_axis": {"scale": truth["y_axis"]["scale"]},
            "series": series}


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                    formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pred", help="parser output JSON")
    parser.add_argument("--truth", help="ground-truth fixture JSON")
    parser.add_argument("--tol", type=float, default=0.01,
                        help="pass tolerance as fraction of axis range (default 0.01)")
    parser.add_argument("--self-test", dest="self_test",
                        help="synthesize a perfect pred from this fixture truth and eval")
    args = parser.parse_args()

    if args.self_test:
        truth = json.loads(Path(args.self_test).read_text())
        pred = synth_pred_from_truth(truth)
        print(f"[self-test] perfect pred synthesized from {args.self_test}\n")
        ok = evaluate(pred, truth, args.tol)
        sys.exit(0 if ok else 1)

    if not (args.pred and args.truth):
        parser.error("provide --pred and --truth, or --self-test FIXTURE.json")

    pred = json.loads(Path(args.pred).read_text())
    truth = json.loads(Path(args.truth).read_text())
    ok = evaluate(pred, truth, args.tol)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
