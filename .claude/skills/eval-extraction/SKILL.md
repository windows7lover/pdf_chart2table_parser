---
name: eval-extraction
description: Use when validating parser output against a fixture's ground truth, or measuring extraction quality/precision on the corpus. Runs scripts/eval_extraction.py to match series and points, report per-series median/max error normalized by axis range (log-space for log axes), and pass/fail at a tolerance.
---

# Evaluate parser output against ground truth

This is the quality gate. The project optimizes **precision over recall**:
emitting wrong numbers is worse than skipping a chart, so eval treats both bad
values and series-count mismatches as failures.

## Run it

```bash
export UV_LINK_MODE=copy
uv run python scripts/eval_extraction.py --pred pred.json --truth tests/fixtures/<name>.json [--tol 0.01]
```

Exits **nonzero** on failure, so it works directly in test gates / loops.

### Self-test (no parser needed)

Synthesizes a perfect prediction from a fixture's truth and confirms ~0 error:

```bash
uv run python scripts/eval_extraction.py --self-test tests/fixtures/<name>.json
```

## Schemas

**pred.json** (parser output):
```json
{"x_axis": {"scale": "linear|log"}, "y_axis": {"scale": "..."},
 "series": [{"label","marker","color",
             "points": [{"x","y","x_px","y_px"}, ...]}]}
```

**truth.json** (fixture, from gen_fixtures.py):
```json
{"x_axis": {"scale","lim":[lo,hi]}, "y_axis": {"scale","lim":[lo,hi]},
 "series": [{"label","marker","color","x":[...],"y":[...]}]}
```

## How matching and error work

- **Series matching:** greedy nearest-centroid in data space (centroids scaled
  per-axis so x and y weigh evenly). `marker`/`color` are available as tie-aids.
- **Point matching:** nearest-neighbor in data space (per predicted point).
- **Error normalization:** error = max(|dx|/x_range, |dy|/y_range). For a `log`
  axis, values are compared in **log10 space** and the range is the log range.
- **Tolerance:** default 1% of axis range; a series PASSes only if its *max*
  point error is within tolerance.

## Metrics reported

- counts: #truth, #pred, #matched → **precision** (matched/pred) and **recall**
  (matched/truth). Precision is the headline metric for the corpus.
- per matched series: median and max relative error, point counts.
- overall PASS/FAIL (FAIL if any series exceeds tolerance OR counts mismatch).
