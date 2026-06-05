# Extraction Precision Judge Report

**Date:** 2026-06-04
**Method:** Multimodal visual judging of rendered reconstructions (LEFT = original chart region
with extracted marker pixels overlaid; RIGHT = re-plot of extracted (x, y) data).
**Sampler:** `scripts/judge_sample.py` (seed 0). Renders + index in `$SCRATCH/pdf_chart2table/judge/`.
Verdicts in `judge_verdicts.csv`; skip spot-checks in `skip_index.csv`.

## Corpus & sample

- Manifest: 1578 chart targets — **1295 extracted**, **283 skipped** (all "no axis calibration").
- Of the extracted, only **691 emit >0 data points** (across 77 papers); the other 604 are
  empty extractions (0 series, confidence 0.5) — they claim no data, so they are excluded from
  the precision sample but are flagged below.
- **Sample: 60 extracted-with-data charts across 41 papers**, stratified over log vs linear axes
  (16 log), single (14) vs many-series (27 with >=4), scatter-ish vs line-ish. All 60 rendered OK.

## Headline result

| Metric | Value |
|---|---|
| Sample size | 60 |
| **Precision (correct / sampled)** | **32 / 60 = 53.3%** |
| Lenient ((correct + partial) / sampled) | 44 / 60 = 73.3% |
| Wrong | 16 / 60 = 26.7% |

**The >=95% precision bar is NOT met — not even close.** Strict precision is ~53%, and even the
lenient figure (counting "mostly right but flawed" as acceptable) is ~73%. Roughly one in four
extracted-with-data charts is materially wrong.

## Failure-mode histogram (non-correct charts, ranked)

| Failure mode | Count (all non-correct) | Count (WRONG only) |
|---|---|---|
| miscalibrated | 8 | 8 |
| extra-noise-series | 6 | 3 |
| label-only | 5 | 0 |
| missing-series | 4 | 1 |
| non-chart-region | 3 | 3 |
| tick-or-grid-artifact | 2 | 1 |

The hard failures (the 16 WRONG) are dominated by **axis miscalibration** (8) and
**non-chart regions / noise** (6), plus a few missing-series and grid artifacts.

## Top 3 failure modes (drivers of low precision)

### 1. Axis miscalibration — 8 charts, ALL wrong (biggest single problem)
The most damaging mode: the shape is often preserved but values are off by 1-2 orders of
magnitude, or an axis explodes to absurd scales.
- **Wallclock-time log bug** (x blown up to ~1.8e34): `2606.05139_page34_chart3`,
  `2606.05139_page8_chart1`. Same paper; the log-x calibration on large wallclock axes detonates.
  Notably the marker-less twin of these charts was correctly *skipped* (`SKIP_2606.05139_page39`).
- **y off by ~100x** (decimal/exponent misread): `2606.04665_page6_chart3` (0.14 -> 14),
  `2606.04752_page8_chart2`, `2606.04603_page4_chart2` (~16x), `2606.04777_page13_chart5`
  (twin-axis confusion), `2606.01827_page8_chart2` (y inverted/wrong, flat series shown rising).
- **Histogram x exploded** to ~3e16: `2606.00934_page22_chart5`.

### 2. Extra-noise / spurious-series — 6 charts (3 wrong, 3 partial)
Clean line plots are shattered into multiple series plus a scattered noise cloud, or a series is
duplicated.
- Clean 2-line plots -> 4 series + red noise cloud: `2606.02081_page18_chart2`,
  `2606.02081_page18_chart5` (same paper/figure family).
- Wide scattered cloud from a clean line: `2606.04662_page6_chart3`.
- Duplicate/phantom series: `2606.04957_page13_chart1`, vertical smears of duplicate points
  `2606.00797_page12_chart5`.

### 3. Non-chart regions extracted as data — 3 wrong (plus grid artifacts)
The pipeline accepts region types it cannot handle and emits garbage.
- Boxplot -> single outlier marker: `2606.04574_page22_chart1`.
- Heatmap -> cell grid as "data": `2606.03217_page9_chart3`.
- Dense density/contour scatter -> only ~6 contour markers: `2606.05145_page16_chart1`.
- Related grid artifacts: `2606.02228_page16_chart1` (extracted background grid rows, not the
  step-line), `2606.05103_page8_chart1` (a couple of below-axis tick artifacts).

(Secondary: **label-only** issues — 5 charts where data/shape are correct but legend/axis-title
text is garbled or wrong; and **missing-series** — 4 charts where one or more real series/points
were dropped. These drag charts from correct to partial.)

## Skip spot-check (false-negative audit)

10 random skipped regions reviewed (all rendered). **0 look like false negatives.** Every skip is
a legitimately non-extractable region for a marker-based pipeline:
- Boxplots: `SKIP_2606.04866_page30`.
- Tables / number heatmaps: `SKIP_2606.05138_page17`.
- Marker-less smooth line plots (incl. date-axis financial series):
  `SKIP_2606.04866_page22`, `SKIP_2606.04574_page55`, `SKIP_2606.04574_page53`.
- Multi-panel grids of marker-less line plots (panel-merge):
  `SKIP_2606.04749_page5`, `SKIP_2606.04212_page7`, `SKIP_2606.04647_page21`,
  `SKIP_2606.04754_page41`, `SKIP_2606.05139_page39`.

Skipping is conservative and correct here. The problem is precision on what *is* extracted, not
over-skipping. (Caveat: skips are a small n=10 sample of 283, and the 604 empty extractions were
not separately audited.)

## Prioritized fix list for the next iteration

1. **Fix axis calibration (highest impact, 8/16 hard failures).**
   - Guard the log-axis fit against runaway exponents — the wallclock-time charts produce ~1e34
     ranges, which is physically impossible vs the visible tick labels. Sanity-check the fitted
     data_range against tick-label text and reject/clip wild extrapolations.
   - Catch the ~100x decimal/exponent misreads (likely tick-label OCR like "0.14" -> "14", or
     mixing a left and right twin y-axis). Cross-check first/last fitted values against parsed
     tick labels and refuse calibration on large residual mismatch.
2. **Add a chart-type gate before extraction (removes 3+ hard failures).**
   Detect and skip boxplots, heatmaps/imshow, and dense KDE/contour regions (they currently leak
   through as garbage). These should join the skip set, not the extracted set.
3. **Suppress spurious-series / noise clouds (6 charts).**
   When a clean line region yields a wide scattered cloud or duplicated series, prefer the line
   over phantom markers. De-duplicate stacked/near-identical points and merge over-split series.
4. **Drop tick/gridline and below-axis artifact points (2 charts).**
   Reject extracted markers that fall outside the plot bbox or sit exactly on gridlines/axes.
5. **Lower-priority quality (partials): label text and missing series.**
   Improve legend/axis-title OCR (5 label-only partials) and reduce dropped series/points
   (4 missing-series) — these don't break the value bar but cap the lenient score.

**Bottom line:** at ~53% strict precision the pipeline is far from the 95% bar. The cheapest path
to a large jump is (1) calibration sanity-checking and (2) a chart-type gate — together those
address ~11 of the 16 hard failures. The skip policy is healthy and should be extended, not
loosened.
