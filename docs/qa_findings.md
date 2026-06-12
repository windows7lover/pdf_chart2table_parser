# QA findings — random reconstruction spot-checks

A QA loop samples 3 random reconstructions (`scripts/qa_sample.py`) and logs any
extraction/reconstruction problems here. Newest round on top. Each item: what's
wrong → likely cause → owner. Fixed items get struck through with the commit.

## Round 2 (2026-06-12)
- **2005.12088_p10c2** — sawtooth curve reconstructed faithfully ✓. Only the dashed
  horizontal reference line (~0.115) is missing (its residual). Good.
- **2211.04615_p26c1** — three stacked XPS peaks reconstructed faithfully ✓.
  Residual is decoration (dashed vertical line at 84 eV, peak tick-marks, dotted
  guide lines). Annotation leader-lines a bit off. Data is right.
- **2510.04789_p3c4** — known: orange straight fit now dropped ✓; navy 38-pt
  error-bar polyline + dashed fit remain (documented follow-ups).
- **2208.14630_p20c2** (user-flagged: "lines transformed into stars") — ROOT CAUSE
  found: each colored curve is drawn as ~21 SHORT, OPEN, ~125-vertex fragments
  (max span ~10px), NOT marker glyphs. `classify_lines._merge_long` can't join
  them (they overlap in x), so `marks._is_data_mark` accepts each fragment as an
  'o' glyph and the recon's `_marker_shape` then renders them as '*' stars.
  Tried: a marks guard rejecting open high-vertex paths — it removes the stars but
  the curves then VANISH (7 of 8 lost), because `classify_lines` still can't
  recover them. So that guard is data-lossy and was reverted.
  REAL FIX (open, non-trivial): **fragment endpoint / draw-order stitching** in
  `classify_lines` — chain same-colour open fragments that don't x-tile by
  endpoint proximity (or PyMuPDF draw order) into one polyline, THEN the marks
  guard can safely reject them. Needs care (mis-stitching risk) + tests.

## ~~Audit confound — refiner-dropped lines counted as residual~~ FIXED
Resolved: `residual_audit.py` now calls `refiners.is_decoration_line` and scores a
connector-through-markers / straight-fit path as explained decoration (like legend
swatches), so correct refining no longer lowers explained%.

## (history) Audit confound — refiner-dropped lines counted as residual (TOP TARGET)
After `drop_spurious_lines` shipped, the residual audit's explained% *fell* on
charts where it correctly removed a connector/fit (2410 82%→77%, 2409 88%→79%,
2510 79%→78%): the dropped line's paths are now "unexplained residual." Same
confound as the legend-swatch case (fixed earlier by treating legend-region paths
as explained). Fix: `residual_audit.py` should treat paths matching a
refiner-dropped line (connector through markers / straight fit) as explained
decoration, so the metric rewards correct refining instead of punishing it.

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
