# Judge R2: Reconstruction audit — survey_r2 (80 charts)

Sample: 40 materials + 40 astro extracted charts, stratified draw, seed fixed by survey_index.csv.
Rubric: identical to Judge 3. Verdicts in `$SCRATCH/pdf_chart2table/survey_r2/survey_verdicts.csv`.

---

## Headline results

| Corpus | n | correct | partial | wrong | **Strict** | Lenient (c+p) |
|---|---|---|---|---|---|---|
| Materials | 40 | 20 | 11 | 9 | **50.0%** | 77.5% |
| Astro | 40 | 19 | 8 | 13 | **47.5%** | 67.5% |
| **Combined** | **80** | **39** | **19** | **22** | **48.8%** | **72.5%** |

**The ≥95% precision bar is still far from met.** Strict precision is 48.8% combined, ~46 points short of 95%. Lenient precision is 72.5%.

### Delta vs Judge 3 (60-sample baseline: 68.3% strict / 85.0% lenient)

The R2 sample is larger (80 vs 60) and independently drawn, so direct comparison reflects both sampling variation and real change. The drop from 68.3% → 48.8% strict is substantial; this is partly explained by the larger, more diverse sample — the R2 draw picks up more difficult charts (histograms, multi-panel figures, dense DOS spectra) that the smaller Judge 3 sample did not include. The underlying structural issues are the same, but R2 exposes them more broadly.

---

## Recovery-only precision (excluding detection-deferred bucket)

6 charts were flagged as **non-chart** (histogram/contour/LISA-band types that the parser should ideally reject before extraction). Excluding these from the denominator isolates recovery precision on the charts the parser did attempt to extract:

| Subset | n | Strict | Lenient |
|---|---|---|---|
| Recovery (excl. non-chart) | 74 | **52.7%** | **78.4%** |

---

## Failure-mode histogram (ranked, over 41 non-correct charts)

| Failure mode | count | % of non-correct | example IDs |
|---|---|---|---|
| **extra-noise** | **18** | **43.9%** | materials_2606.05128_page9_chart4, materials_2606.02317_page14_chart1, astro_2606.00218_page12_chart5 |
| missing-series | 9 | 22.0% | materials_2606.04724_page18_chart3, materials_2606.03278_page2_chart2, astro_2606.03881_page7_chart1 |
| panel-merge | 6 | 14.6% | materials_2606.04608_page18_chart12, materials_2606.04919_page25_chart4, astro_2606.04810_page18_chart11 |
| non-chart | 6 | 14.6% | materials_2606.02085_page4_chart1, astro_2606.00810_page12_chart11, astro_2606.04839_page11_chart1 |
| miscalibrated | 2 | 4.9% | astro_2606.02711_page57_chart1, astro_2606.00569_page25_chart4 |

### What changed vs Judge 3

Extra-noise has rebounded to 18/41 = 44% of failures (was 6/19 = 32% in Judge 3). The R2 sample exposes new extra-noise subtypes that Judge 3 did not sample heavily:

- **Connector/dashed-line sampling** (new dominant subtype): dashed vertical connectors between paired points (astro_2606.00218_page12_chart5 — pulsar timing), dashed reference lines in scatter plots, and dotted fit lines are sampled as intermediate x,y points, creating dozens of spurious series. This is now the single most common extra-noise pattern.
- **Filled-region/DOS sampling**: density-of-states charts with filled colored areas (materials_2606.00223_page4_chart2) and SED model confidence bands (astro_2606.01823_page4_chart4) produce dense spurious point clouds from the fill pixels.
- **Diagonal guide lines** (already known from Judge 3, still present): materials_2606.05128_page9_chart4, materials_2606.02317_page14_chart1.

Panel-merge (6) now appears more prominently. The sample includes several multi-panel figures where the bounding-box detection merges adjacent sub-charts.

Non-chart (6): histogram step-functions (astro_2606.04839_page11_chart1), spectral histograms (astro_2606.00810_page12_chart11, astro_2606.03313_page18_chart18), a corner-plot/joint-PDF (astro_2606.00234_page13_chart45), a GW detector sensitivity chart with band fills (astro_2606.01103_page13_chart1), and a broken-axis IR spectrum (materials_2606.02085_page4_chart1).

---

## What is working well

Clean single- and multi-series line/scatter charts remain strong. Concrete verified wins in this sample:

- Log-log and log-linear curves (power laws, SED fits, decay curves): near-perfect.
- Multi-series overlapping curves with distinct colors: correctly separated when series are thin-line or marker-only.
- Error bars are correctly ignored (not sampled as extra points) in most cases.
- Reference horizontal/vertical guide lines continue to be rejected in most (not all) cases.
- 52 of 80 charts (correct + partial) are usable, demonstrating the pipeline handles the majority of standard xy scatter/line charts.

---

## Top recovery-accuracy levers for the next coding round

Ranked by estimated impact (failure count × severity):

### 1. Reject dashed/dotted connector lines and thin-path segments — `marks.py` / `pdf_vector.py`
**Count: ~8 extra-noise cases**
**Pattern**: Dashed vertical connectors between paired scatter points (pulsar timing, comparison plots), dotted fit lines drawn over data, and dashed reference lines at fixed y-values are currently sampled as data series. The baseline/connector filter only catches long perfectly-horizontal/vertical strokes; it does not catch:
  - Short vertical dashes connecting two data points at the same x (e.g. astro_2606.00218_page12_chart5: IPTA_25 vs IPTA_30 connected by dashed lines; produces ~28 extra series).
  - Faint dotted trend lines (astro_2606.02617_page10_chart2: diagonal identity line sampled as series).
  - Slanted guide/power-law lines (materials_2606.05128_page9_chart4: diagonal fan lines between labeled clusters).
**Rule**: In `pdf_vector.py`, flag dashed/dotted path segments (dash-gap pattern detected from PDF stroke state) and exclude them from marker candidate pixels. Alternatively, in `marks.py`, after clustering, reject any "series" that is perfectly collinear (R² > 0.999 on a straight line) across its full extent — it is almost certainly a fit or guide line, not data.
**Expected impact**: ~8 extra-noise failures fixed → +10 pp strict precision.

### 2. Filter filled-region pixels before series extraction — `extract.py` / `marks.py`
**Count: ~5 extra-noise cases**
**Pattern**: Filled colored areas (DOS shading: materials_2606.00223_page4_chart2, model confidence bands: astro_2606.01823_page4_chart4, SED model envelopes: astro_2606.02687_page7_chart2) have many interior pixels that are sampled as if they were markers. Interior pixels of a filled region are NOT on the data boundary; only the top (or boundary) edge is data.
**Rule**: In `marks.py` or `extract.py`, after candidate pixel detection, apply a flood-fill connectivity test: if a cluster of same-color pixels has a large interior (area >> perimeter²/4π), flag it as a filled region and keep only the boundary pixels (top edge per x-column). This is the fill-vs-line distinction.
**Expected impact**: ~5 extra-noise failures fixed → +6 pp strict precision.

### 3. Detect and reject histogram/bar-chart chart types before extraction — LLM gate or heuristic in `plot_region.py`
**Count: 6 non-chart cases**
**Pattern**: Step-function histograms (astro_2606.04839_page11_chart1, astro_2606.00810_page12_chart11), spectral bar-histogram overlays (astro_2606.03313_page18_chart18), and corner/joint-PDF plots (astro_2606.00234_page13_chart45) all slip the non-chart gate and produce garbage extractions.
**Rule**: In `plot_region.py` (chart-type classifier), detect: (a) paths that consist of axis-aligned step patterns (histogram staircase) — recognizable as many short horizontal + vertical segments at equal y-increments; (b) filled rectangular bars. If the dominant path pattern is step-staircase, skip. The broken-axis IR spectrum (materials_2606.02085_page4_chart1) is harder (requires "//" break detection).
**Expected impact**: 5-6 non-chart failures removed from output (precision improvement ~6 pp; recall loss on these is intentional and already acceptable).

### 4. Multi-panel bounding-box splitting — `plot_region.py`
**Count: 6 panel-merge cases**
**Pattern**: The region detector merges adjacent sub-panels sharing a common outer frame (e.g. (d) and (f) in materials_2606.04919_page25_chart4, the 3×3 panel in astro_2606.04810_page18_chart11, astro_2606.00810_page9_chart3). The merged extraction produces an uninterpretable mix of two coordinate systems.
**Rule**: In `plot_region.py`, after finding an axis bounding box, look for internal horizontal or vertical dividing lines at equal spacing (sub-panel separators). If found, split into sub-regions and extract each independently. At minimum, reject regions whose interior has more than one pair of x-tick clusters (a proxy for multiple sub-axes stacked vertically).
**Expected impact**: ~4-5 panel-merge failures fixed → +5-6 pp strict precision.

### 5. Missing-series: improve dark/faint series detection — `marks.py`
**Count: 9 missing-series cases**
**Pattern**: Series drawn in near-black or gray (e.g. materials_2606.01938_page7_chart2: B=0.001T nearly flat at y≈0, drawn in blue so faint it blends with axis; materials_2606.03278_page2_chart2: sharp resonance peaks on a log-y chart missing almost entirely; astro_2606.03881_page7_chart1: Fermi-LAT gray errorbars missing). Near-zero flat lines are currently below the tiny-n rejection threshold or the color-clustering misses them.
**Rule**: (a) In `marks.py`, lower the minimum brightness distance from axis color for near-flat lines — a flat line at y≈0 may be confused with the x-axis itself. (b) For log-y charts, check whether any significant pixel cluster exists in the bottom 20% of the plot box that wasn't claimed by any series — a missed flat/near-zero series. (c) Review the color-clustering threshold: gray series close to the axis color should still be detected.
**Note**: This is harder to fix cleanly than 1-4; may need per-case tuning.

### 6. Miscalibration: y-axis range errors on log scale (minor, 2 cases)
Both miscalibrated cases (astro_2606.02711_page57_chart1, astro_2606.00569_page25_chart4) are log-axis calibration failures. The x-axis of the spectrum in _00569 is calibrated in GHz × 10^-n notation, mapping absolute values wrong. The y-axis of _02711 is off by ~10 decades. These appear to be tick-label mis-parsing edge cases (superscript exponents not correctly captured). No clean rule without addressing the LaTeX text extraction (already documented as a known issue in MEMORY).

---

## Net assessment

R2 strict precision is 48.8% combined (52.7% recovery-only). This is lower than Judge 3's 68.3%, primarily because the larger and more diverse R2 sample surfaces histogram, multi-panel, and filled-region chart types that were underrepresented in Judge 3's 60-chart draw. The structural failure hierarchy is now clear:

1. **Extra-noise (43.9% of failures)**: connector/dashed-line sampling and filled-region pixel bleeding are now the #1 bottleneck. The prior noise-fix addressed axis-parallel straight baselines; it left connector dashes and fill interiors untouched.
2. **Missing-series (22.0%)**: faint/gray series and near-zero flat lines are systematically missed.
3. **Panel-merge + non-chart (14.6% each)**: multi-panel region detection and histogram/bar type rejection.

Reaching 70% strict requires fixing levers 1+2 (extra-noise + non-chart rejection). Reaching 80%+ requires also addressing panel-merge and missing-series. The 95% bar remains distant without addressing all five categories.
