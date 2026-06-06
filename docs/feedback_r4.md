# Feedback r4: Judge measurement after iteration 2

Same 80-chart seed-13 sample as r3 (the 51.2% / 56.9% baseline). Direct before/after comparison.

## Headline results

| Corpus | n | correct | partial | wrong | Strict | Lenient (c+p) |
|---|---|---|---|---|---|---|
| Materials | 40 | 22 | 16 | 2 | **55.0%** | 95.0% |
| Astro | 40 | 19 | 16 | 5 | **47.5%** | 87.5% |
| **Combined** | **80** | **41** | **32** | **7** | **51.2%** | **91.2%** |

Recovery-only strict (non-chart/histogram/2D removed from denominator, n=77): **53.2%**
(materials 55.0%, astro 51.4%).

## Delta vs r3 (51.2% strict / 56.9% recovery-only / 83.8% lenient)

| Metric | r3 | r4 | Delta |
|---|---|---|---|
| Strict, materials | 50.0% | 55.0% | **+5.0** |
| Strict, astro | 52.5% | 47.5% | **-5.0** |
| **Strict, combined** | **51.2%** | **51.2%** | **0.0** |
| Lenient, combined | 83.8% | 91.2% | **+7.4** |
| Recovery-only strict | 56.9% | 53.2% | -3.7 |

**Net: strict precision is flat at 51.2%.** Wrong count dropped from 13 to 7 (large improvement
in hard failures), but many of those cases moved from wrong → partial rather than wrong → correct,
so lenient precision rose sharply (+7.4 pts) while strict stayed flat. The iteration converted
catastrophic failures to recoverable-but-imperfect cases — useful progress in quality but not
reflected in the strict metric.

## Failure-mode histogram (r4, ranked over 39 non-correct charts)

| Failure mode | Count | Representative cases |
|---|---|---|
| **extra-noise** | **20** | mat_2606.02858, mat_2606.04219_p5_c7, mat_2606.03886, mat_2606.02317_p22_c11, astr_2606.05298, astr_2606.03776 |
| **miscalibrated** | **6** | mat_2606.02419_p3_c2, mat_2606.05128_p9_c3, astr_2606.02711_p57_c1, astr_2606.00218_p12_c1, astr_2606.00218_p12_c3 |
| **missing-series** | **5** | mat_2606.00403_p10_c1, mat_2606.02419_p3_c1, mat_2606.00165_p17_c1, astr_2606.05146_p19_c4, astr_2606.04712_p22_c1 |
| **panel-merge** | **4** | mat_2606.04919_p24_c1, mat_2606.04608_p14_c2, astr_2606.02691_p11_c2, astr_2606.00219_p18_c2 |
| **non-chart** | **3** | astr_2606.03313_p5_c11 (spec+hist), astr_2606.00258_p10_c5 (mol spec+hist), astr_2606.02353_p3_c5 (step-fcn hist) |
| **sparse** | **1** | astr_2606.02566_p7_c4 |

**Extra-noise is now the dominant failure by a large margin (20/39 = 51%).** This is a regression
vs r3's 6 extra-noise cases — the iteration appears to have traded hard misses/miscalibs for
softer over-segmentation / extra-series noise.

## Notable changes on 16 shared sample IDs

Improved (partial/wrong → correct):
- `materials_2606.01373_page7_chart6`: partial (missing series) → **correct** — "Without cavity"
  series now fully recovered over full range
- `materials_2606.01938_page8_chart1`: partial (extra-noise) → **correct** — R=70nm series now
  visible; resonance spikes damped

Regressed (correct/partial → wrong or partial):
- `materials_2606.02858_page4_chart1`: correct → **partial** (y-axis scale wrong, extra-noise)
- `materials_2606.03405_page6_chart6`: correct → **partial** (minor calibration drift on y-axis)
- `materials_2606.02317_page23_chart2`: correct → **partial** (extra series over-split from fits)
- `astro_2606.02711_page57_chart1`: partial → **wrong** (log-vs-linear y-axis miscalib persists)
- `astro_2606.04712_page22_chart1`: correct → **partial** (upper-limit markers missing)

## Summary of change character

The r4 iteration reduced outright failures (wrong: 13→7) but the mode signature shifted:
- Wrong-count drop of 6 is real and meaningful — fewer catastrophic extractions
- Those 6 cases landed in partial not correct, hence 0 net strict gain
- Extra-noise surged from 6 (r3) to 20 (r4), indicating a parameter or code change that
  is over-segmenting, sampling fill regions, or failing to suppress auxiliary graphic elements
  at a higher rate than before — this is the biggest structural regression

## Top-3 recovery levers for iteration 4

**1. Suppress over-segmentation / extra-noise (critical — 20/39 failures = 51%)**
The extra-noise rate more than tripled vs r3 (6→20). Audit which code change in iteration 2
reduced the baseline/connector-line filter effectiveness or introduced new sources of spurious
series. Key patterns: fill-region edges sampled as extra series (absorption profiles, shaded
bands), Gaussian-fit component curves split as extra series (XPS spectra), and over-splitting
of near-coincident multi-series into more segments than exist in the original. Restoring or
tightening the r3 noise filter should recover 10+ correct verdicts.

**2. Axis calibration robustness (6 miscalibrated, including 3 axis-type errors)**
Three cases show log-vs-linear axis type confusion on new charts; two show y-axis scale offsets.
The broken-axis / dual-y-axis case (mat_2606.04958) worked; the issue is elsewhere. Focus on:
(a) the persistent shock-tube step-function miscalib (astr_2606.02711_p57_c1) — log wrongly
inferred on a linear-integer y-axis; (b) mat_2606.05128_p9_c3 where x-axis was changed from
linear to log; (c) the multi-marker strip charts where y-values are systematically wrong.

**3. Panel / sub-panel separation (4 panel-merge cases)**
Panel-merge is back at 4 failures (same as r3's worst). Two materials and two astro charts
have adjacent sub-panels bleeding into the main extraction. Tightening the bounding-box logic
to restrict extraction to the primary axes rectangle (and not adjacent panels) would address
these without hurting good charts.

## Lenient metric commentary

Lenient precision rose to 91.2% (+7.4 pts), meaning nearly all wrong-class charts moved to
recoverable. This is genuine quality improvement — the data topology is mostly present, even
where series count or axis scale is imperfect. The lenient → strict gap of 40 points is now
mainly driven by extra-noise contamination (20 partial cases), which is reversible.
