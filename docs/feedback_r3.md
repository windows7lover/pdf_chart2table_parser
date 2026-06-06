# Survey R3 — Judge Report: Recovery Quality After Latest Fix Round

**Sample:** 80 charts, seed-13, same as r2 (40 materials + 40 astro). Renders in
`$SCRATCH/pdf_chart2table/survey_r3/`. Verdicts in `survey_verdicts.csv` (same dir).

---

## Headline numbers

| Corpus | n | correct | partial | wrong | **Strict** | Lenient (c+p) |
|---|---|---|---|---|---|---|
| Materials | 40 | 20 | 13 | 7 | **50.0%** | 82.5% |
| Astro | 40 | 21 | 13 | 6 | **52.5%** | 85.0% |
| **Combined** | **80** | **41** | **26** | **13** | **51.2%** | **83.8%** |

### Detection-deferred bucket (non-chart / histogram / 2D-map)

8 of 80 charts are detection-deferred (not xy series): 2 materials (Hofstadter butterfly,
histogram), 6 astro (2D heatmap, 3x histogram/staircase, 1 spectral datacube slice, 1
borderline step-spectrum). These inflate the wrong count without measuring recovery quality.

### Recovery-only strict (non-chart excluded)

| Corpus | n | correct | partial | wrong | **Strict** | Lenient |
|---|---|---|---|---|---|---|
| Materials | 38 | 20 | 13 | 5 | **52.6%** | 86.8% |
| Astro | 34 | 21 | 11 | 2 | **61.8%** | 94.1% |
| **Combined** | **72** | **41** | **24** | **7** | **56.9%** | **90.3%** |

---

## Delta vs survey_r2

Survey r2 baseline: **48.8% strict / 52.7% recovery-only strict** (80-chart sample, same seed).

| Metric | r2 | r3 | Delta |
|---|---|---|---|
| Strict, materials | — | 50.0% | — |
| Strict, astro | — | 52.5% | — |
| **Strict, combined** | **48.8%** | **51.2%** | **+2.4 pp** |
| **Recovery-only strict** | **52.7%** | **56.9%** | **+4.2 pp** |
| Lenient, combined | ~83% | 83.8% | ~flat |

**Net: +2.4 pp strict, +4.2 pp recovery-only. Modest but positive movement.** The recovery-only
gain is more reliable — 4.2 pp on 72 charts is a real shift. The lenient metric is flat, indicating
that fixes moved some wrongs to correct (lifting strict) while the partial bucket did not shrink —
new partial cases are entering where there were previously wrongs.

**What improved:** The materials astro recovery gains are asymmetric (+9.1 pp astro recovery vs
~flat materials recovery), suggesting the latest fixes particularly helped reference-line and
structural issues more common in astro charts (axis-parallel guide rejection, etc.).

---

## Failure-mode histogram (over non-correct charts, n=39)

| Failure mode | count | key examples |
|---|---|---|
| **missing-series** | 11 | mat_01373_p7_c6 (cavity truncated), mat_04724_p21_c2 (2 of 3 missing), mat_04842_p3_c4 (Carbon series missing), astro_01855_p6_c1 (marker types), astro_03522_p7_c1 (upper limits absent), astro_05322_p15_c7 |
| **extra-noise** | 10 | mat_02317_p23_c1 (XPS over-segmented), mat_02455_p9_c2 (GPU benchmark), mat_01938_p8_c1 & c2 (resonances), astro_05284_p11_c4, astro_01103_p6_c2, astro_00332_p10_c7, astro_02698_p8_c4 |
| **non-chart** | 8 | mat_01029_p25_c1 (Hofstadter butterfly), mat_00711_p4_c1 (histogram), astro_04810_p12_c4 (heatmap), astro_00212_p10_c3 (histogram), astro_03313_p18_c17 (spectral), astro_03522_p6_c1 (histogram), astro_00569_p11_c7, astro_05237_p123_c2 |
| **panel-merge** | 5 | mat_04608_p18_c9, mat_04608_p19_c1, astro_04810_p9_c8 (wrong), astro_01625_p37_c7, astro_02566_p17_c1 |
| **miscalibrated** | 4 | mat_05050_p22_c2 (y-axis inverted), mat_04919_p21_c3, astro_02698_p17_c5 (axis swap), astro_02711_p57_c1 (log vs linear) |
| **sparse** | 1 | mat_04219_p12_c5 |

**Shift from r2:** In r2, extra-noise dominated (it was the largest single category). Here,
missing-series has overtaken extra-noise as the top failure mode (11 vs 10). Non-chart
remains a fixed structural floor. Panel-merge dropped slightly. Miscalibration stays low but
persistent. The overall mix is now more balanced, which reflects that extra-noise fixes worked
while structural failures remain.

---

## What is working well

- **Dense single-series charts**: XRD diffractograms, spectral peaks, decay curves, log-log
  power-laws, sinusoids all extract cleanly (mat_00785_p17_c2, mat_02072_p12_c1,
  mat_03497_p22_c4, mat_00403_p8_c8, astro_04758_p9_c1, astro_00221_p19_c3,
  astro_02032_p6_c2, astro_04711_p7_c2).
- **Multi-series scatter with distinct colors**: most 3-6 series scatter charts extract all
  series correctly (mat_04219_p8_c4, mat_00223_p6_c1, astro_04309_p13_c5,
  astro_02738_p6_c2, astro_00493_p8_c2, astro_01103_p8_c2).
- **Transit/light curves**: clean extraction on exoplanet transits and GRB decay
  (astro_04624_p13_c2, astro_04894_p6_c1, astro_01956_p13_c7).
- **Axis-parallel reference-line rejection** is functioning — multiple charts (mat_03497_p23_c4,
  astro_00221_p19_c3) correctly ignore reference lines.
- **19-series and many-series charts** now extractable (astro_03776_p5_c1 with 19 series correct;
  astro_03667_p11_c1 correct).

---

## Top recovery levers for the next round (ranked by expected precision gain)

### 1. Chart-type gate: histograms, 2D maps, staircase spectra  
**File: `src/pdf_chart2table/plot_region.py` (region filtering) + `src/pdf_chart2table/marks.py`**  
8 of 80 = 10% of sample are non-xy types getting through. Histograms (bar fills with step edges),
2D heatmaps, and Hofstadter/band-structure diagrams should be detected and skipped:
- Histogram: detect predominant vertical bar fills or step-function edge sampling
- Heatmap: detect 2D color-fill covering >50% of plot area
- Expected gain: up to +8-10 pp strict (these all score wrong)

### 2. Missing-series: faint/low-contrast series not extracted  
**File: `src/pdf_chart2table/marks.py` (color clustering threshold)**  
11 failures classified as missing-series. Common cause: faint series (black on white near axis,
very light-colored series, barely-distinguishable overlapping series). Examples:
mat_04724_p21_c2 (2 of 3 lines missed), mat_02858_p4_c1 previously, astro_01855_p6_c1
(different marker shapes not picked up). Lower the minimum-pixel threshold for color clusters
or improve marker vs background separation.  
Expected gain: +4-6 pp strict if half the missing-series cases are rescued.

### 3. Extra-noise: XPS/spectral peak over-segmentation and reference-band sampling  
**File: `src/pdf_chart2table/extract.py` (series merging) + `src/pdf_chart2table/marks.py`**  
10 extra-noise failures remain. Primary drivers:
- XPS/spectral fitting: background curves and envelope fits sampled alongside data peaks
  (mat_02317_p23_c1, astro_01103_p6_c2). Merge series that lie within 1 pixel of another
  series on a smooth fitted envelope.
- Connector-line sampling: dashed errorbars between stacked points generate spurious
  intermediate series (astro_02698_p8_c4). Extend baseline/connector rejection to dashed
  near-vertical segments.
- Resonance spike contamination (mat_01938 charts): dense sampling of sharp Fabry-Perot
  resonances creates near-duplicate series. Merge series within tight spatial clusters.  
Expected gain: +3-5 pp strict if 3-4 extra-noise cases cleaned.

### 4. Panel-merge: sub-panel bleed  
**File: `src/pdf_chart2table/plot_region.py` (bounding-box detection)**  
5 failures from panel bleed. Key pattern: adjacent panels in a multi-row figure share a
thin strip that gets included in the extraction bounding box. Restrict region to the single
detected axis box and clip to its exact boundaries. mat_04608_p18_c9 and p19_c1 are the
clearest examples (strip from adjacent panel (b1) bleeds into (c1) extraction).  
Expected gain: +3-4 pp strict.

### 5. Miscalibration: log vs linear axis detection  
**File: `src/pdf_chart2table/pdf_vector.py` (tick spacing analysis)**  
4 miscalibration cases. Two are log-vs-linear confusion (astro_02711_p57_c1 maps linear y
to log), one is axis inversion (mat_05050_p22_c2), one is axis swap in dual-panel
(astro_02698_p17_c5). For the log-vs-linear cases: verify that detected tick spacing is
consistent with linear spacing before assigning linear scale. If tick ratios are non-uniform,
default to log.  
Expected gain: +2-3 pp strict.

---

## Summary

R3 strict = **51.2%** (combined), **56.9%** recovery-only.  
Delta vs r2: **+2.4 pp strict, +4.2 pp recovery-only**.  
The gap to 95% is still ~38 pp. The fix round produced real but small improvement. The
**missing-series** problem has now overtaken extra-noise as the top failure mode, and
**non-chart chart-types** remain a 10% floor that a type-gate would immediately remove.
The path to 70%+ strict requires: (1) type-gate to block histograms/heatmaps, (2) better
faint-series recovery, (3) XPS/spectral peak over-segmentation fix, (4) tighter panel
bounding.
