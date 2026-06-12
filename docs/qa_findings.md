# QA findings — random reconstruction spot-checks

A QA loop samples 3 random reconstructions (`scripts/qa_sample.py`) and logs any
extraction/reconstruction problems here. Newest round on top. Each item: what's
wrong → likely cause → owner. Fixed items get struck through with the commit.

## Round 1 (2026-06-12)
- **2503.12775_p12c4** — curve extracted well, but the **x-axis is miscalibrated**
  (recon spans 0–14, original is 0–1) and the **y ×10⁻⁴ multiplier is dropped**
  (recon 0–1.8 vs original 0–0.8e-4). The flat `U=R₁₀₀` baseline isn't extracted.
  → calibration (`axes.py`/`calibrate.py`): tick-value read + axis multiplier.
- **2102.11637_p6c5** — dashed model curve + markers both extracted (good), but the
  **scatter markers are connected by a dotted line** (crossing lines). Grey shaded
  band not reconstructed (fill, out of scope).
  → render-side `match_series_styles` sets `connect=True` on scatter; only connect
  when the original truly drew a line. (Recurring; also seen on 2410.00955.)
- **2208.14630_p20c2** — 7–8 smooth colored **line** curves reconstructed as
  **scattered star markers** (no connecting lines, points scrambled). The
  extracted pixels (middle panel) track the curves, so detection is OK but the
  series are **mis-typed as markers** and/or marker-shape detection turns curve
  samples into stars.
  → line-vs-marker typing (`marks.py`/`lines.py`); a dense multi-curve chart.

### Recurring themes so far
1. **connect-through-scatter (render):** scatter series drawn with a connecting
   line. Affects 2410, 2102. Highest-frequency, render-only fix.
2. **line-vs-marker mis-typing (extract):** smooth curves emitted as marker
   series (2208).
3. **calibration:** axis multiplier / tick-value read (2503).
