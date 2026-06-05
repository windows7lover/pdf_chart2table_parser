# Judge 3: Precision re-measurement after the noise-fix iteration (materials + astro)

Re-run of the multimodal reconstruction-judge audit after the noise-fix iteration
(plot-box clipping + baseline/connector-line rejection + tiny-n rejection). Method mirrors
Judge 2 for direct comparability: each sampled EXTRACTED chart (n_points>0) was rendered as a
side-by-side reconstruction (LEFT = original chart region with extracted marker pixels overlaid
in red; RIGHT = re-plot of the extracted x,y) and judged visually.

- Sampler: `scripts/judge_sample3.py` (copy of `judge_sample2.py`, output dir `judge3`), seed 0,
  stratified across papers and strata (log/linear, single/many series, scatter/line), cap 2 charts/paper.
- Sample: 30 materials + 30 astro extracted charts, all 60 rendered successfully.
- Corpus sizes (post-fix): materials 219 extracted-with-points charts across 46 papers;
  astro 1132 across 121 papers.
- Renders + index: `$SCRATCH/pdf_chart2table/judge3/` (`sample_index.csv`, `skip_index.csv`).
- Verdicts: `docs/judge3_verdicts.csv` (and `$SCRATCH/pdf_chart2table/judge3/judge3_verdicts.csv`).

Verdict rubric (identical to Judge 2): **correct** = extracted points lie on the real marks/curves
AND the re-plot matches shape & axis ranges; **partial** = mostly right (minor missing/extra series,
slight calib drift, recoverable noise); **wrong** = data doesn't match (contour/non-chart, miscalib,
panel-merge, near-empty extraction, garbage).

## Headline results

| Corpus | n | correct | partial | wrong | **Strict precision** | Lenient (c+p) |
|---|---|---|---|---|---|---|
| Materials | 30 | 21 | 4 | 5 | **70.0%** | 83.3% |
| Astro | 30 | 20 | 6 | 4 | **66.7%** | 86.7% |
| **Combined** | 60 | 41 | 10 | 9 | **68.3%** | 85.0% |

**The ≥95% precision bar is still NOT met.** Strict precision is 68.3% combined (70.0% materials,
66.7% astro), ~27 points short of 95%. Lenient (counting "partial" as acceptable) is 85.0%.

### Delta vs the prior judge (Judge 2: 65.0% strict / 88.3% lenient)

| Metric | Judge 2 | Judge 3 | Delta |
|---|---|---|---|
| Strict, materials | 66.7% | 70.0% | +3.3 |
| Strict, astro | 63.3% | 66.7% | +3.4 |
| **Strict, combined** | **65.0%** | **68.3%** | **+3.3** |
| Lenient, combined | 88.3% | 85.0% | -3.3 |

Strict precision improved modestly (**+3.3 points combined**). The lenient metric *dropped*
~3 points, and the **wrong** count rose from 7 to 9. This is the expected signature of the fix:
several charts that Judge 2 scored "partial" because of recoverable extra-noise are now cleaner
(some moving partial→correct, lifting strict), while the noise reduction did **not** rescue the
hard structural failures (contour maps, panel merges, near-empty extractions), which remain or
are now scored more harshly as wrong rather than partial. Note both samples are size-60 stratified
draws; a ±3-point shift is within sampling noise, so the honest read is: **the noise-fix produced a
small real improvement on extra-noise contamination but did not move the needle structurally.**

## The extra-noise fix: did it work? (quantified)

Yes, partially. **extra-noise dropped from the dominant failure mode (12/21 non-correct in Judge 2)
to 6/19 non-correct here — roughly halved (-50%).** Concrete evidence the specific fixes are firing:

- **Plot-box clipping / out-of-axis rejection:** out-of-axis "column"/"floor" artifacts that
  plagued Judge 2 are largely gone. Reconstructions no longer show spurious vertical columns or
  floor rows in the clean cases.
- **Baseline / reference-line rejection:** horizontal/vertical reference lines are now correctly
  NOT sampled as series in multiple charts — astro_2606.04894_p7_c2 and _p17_c1
  (Γmax/Γmin/tmin/tmax guides ignored), astro_2606.00221_p20_c4 (vertical line ignored),
  astro_2606.04140_p9_c1 (gridlines ignored), astro_2606.02732_p4_c4 (marginal side-panel ignored).
  In Judge 2 these were a recurring extra-noise source; here they are clean.
- **Inset non-contamination:** materials_2606.03016_p9_c2 (peak curve with an inset vector-field
  image) reconstructs cleanly — the inset did not bleed into the series (a panel-merge win).

What the fix did NOT solve (residual extra-noise, 6 cases):
- **Diagonal guide/trend lines still sampled as thick fake series** — materials_2606.05128_p6_c2
  is the worst (the red "μ~n" power-law guide lines become dense diagonal "series"). The
  baseline/connector filter catches axis-parallel lines but not slanted guide lines.
- **Model-band / floor over-sampling** — astro_2606.02698_p7_c4 (16-model SED band over-sampled),
  astro_2606.02726_p9_c1 (flat floor row of points along the bottom).
- **Connector lines in categorical/strip plots** — astro_2606.00218_p12_c3 (dashed connectors
  between 0/1 states add spurious mid-range points).

## Failure-mode histogram (ranked, over the 19 non-correct charts)

| Failure mode | count | example ids |
|---|---|---|
| extra-noise | 6 | materials_2606.05128_p6_c2, astro_2606.02698_p7_c4, astro_2606.02726_p9_c1 |
| non-chart | 4 | astro_2606.02593_p3_c1 & _p3_c2 (Bayesian-posterior contour maps), astro_2606.03707_p18_c1 (M-R credible band+ellipses), materials_2606.01744_p81_c1 (band-structure dispersion) |
| missing-series | 4 | astro_2606.00209_p2_c2, astro_2606.04712_p24_c1, materials_2606.05050_p13_c1, astro_2606.00221_p11_c2 |
| miscalibrated | 3 | materials_2606.04919_p21_c3, materials_2606.05057_p6_c1, materials_2606.02399_p2_c2 |
| panel-merge | 2 | materials_2606.03497_p23_c1, materials_2606.04219_p12_c1 |

The mix has **shifted**: in Judge 2, extra-noise was the overwhelming single problem (12, vs
4 miscalibrated and 2 non-chart). After the fix, extra-noise (6) is now only tied-largest with
**non-chart contour/2D-map types (4)** and **missing-series (4)** as the leading failures. The fix
worked on what it targeted; the remaining failures are dominated by chart-TYPE problems and
tiny-n/sparse extractions rather than spurious-point contamination.

### The 9 outright-wrong, broken down
- **4 non-chart types slipping the gates:** two Bayesian-posterior **contour maps**
  (astro_2606.02593 p3_c1 & p3_c2 — same paper, both extract contour isolines as meaningless
  scatter), one neutron-star **M-R credible-band + ellipse-contour** plot
  (astro_2606.03707_p18_c1), and one **band-structure dispersion lattice**
  (materials_2606.01744_p81_c1). None of these are xy line/scatter charts; all should be
  rejected by type.
- **2 panel-merges:** materials_2606.03497_p23_c1 (a peak panel merged with the flat panel below
  it) and materials_2606.04219_p12_c1 (a scatter panel merged with an adjacent violin-plot panel).
- **2 near-empty / sparse-on-dense extractions:** materials_2606.04919_p21_c3 (4 stray points
  that don't lie on the flat data line) and astro_2606.04712_p24_c1 (only 4 points extracted from
  a dense extinction-track plot full of markers). These survive the tiny-n rejection but are still
  too sparse/wrong to be usable — the tiny-n gate is not catching "sparse extraction from a dense
  chart."
- **1 extra-noise so severe data is meaningless:** materials_2606.05128_p6_c2 (guide lines
  dominate the reconstruction).

## What is working well (unchanged from Judge 2, and a bit better)
Clean single/multi-series line and scatter charts with detectable axes reconstruct beautifully:
Lorentzians, parabolas, sawtooth (Dirichlet), decay curves, RDF g(r), XPS spectra, posterior PDFs,
shock-tube convergence, power-laws, P-Cygni/emission-line profiles, periodogram-style noisy decays
(e.g. materials_2606.00314_p5_c1, materials_2606.04724_p21_c1 & _p19_c1, materials_2606.00403_p8_c9,
materials_2606.02317_p11_c1, astro_2606.02711_p53_c4, astro_2606.01087_p16_c2,
astro_2606.00258_p13_c1, astro_2606.02413_p9_c2). Reference-line and side-panel non-contamination
(above) is a visible, concrete improvement over Judge 2.

## Skip audit (10 materials + 10 astro skipped charts spot-checked; 8 viewed)
Skip reasons split: 11 "no axis calibration", 9 "no series extracted". Spot-checks confirm the
gates remain **conservative** — they protect precision but sacrifice recall:

- **Correctly skipped non-charts:** astro_2606.01569_p1_c1 (schematic black-hole illustration +
  text), astro_2606.03346_p22_c1 (many overlaid noisy waveform strips), materials_2606.03586_p7_c3
  (rotated/transposed profile — non-standard orientation, defensible).
- **Lost good charts (false negatives):** materials_2606.04667_p9_c2 (RDF g(r) with sharp peaks),
  materials_2606.00068_p15_c1 (PLA hardness line chart — also FN in Judge 2),
  materials_2606.04919_p2_c2 (clean dispersion line chart with markers),
  astro_2606.02698_p8_c11 (UVJ track — the sibling chart12 WAS extracted),
  astro_2606.04090_p17_c3 (ionization-potential scatter — crowded categorical x, defensible).

**Conclusion on skips:** unchanged from Judge 2 — no gross garbage was found among the skips that
should obviously have been kept. The gates over-reject (substantial recall loss) but none of the
skips would have inflated the wrong-rate. The precision shortfall is entirely within the EXTRACTED
set, not hidden in the skips.

## Prioritized fix list (to move precision toward 95%)
1. **Chart-type gate to reject 2D maps / contour / credible-region plots (biggest remaining lever
   — addresses 3 of the 4 "non-chart" wrongs, including a same-paper double-failure).** Detect
   filled/contour isoline fields and credible-region bands/ellipses and skip them; they are not
   xy series. This is now the single highest-value fix.
2. **Reject slanted guide / trend / fit lines (residual extra-noise).** The baseline/connector
   filter handles axis-parallel lines; extend it to perfectly-straight diagonal runs (power-law
   guides, "μ~n" lines, linear fits drawn over markers) so they aren't sampled as dense series
   (materials_2606.05128_p6_c2, materials_2606.02317_p14_c2).
3. **Strengthen the tiny-n / sparse-extraction gate.** Reject extractions whose point count is far
   below the marker count visibly present (sparse-on-dense), and extractions whose points don't
   coincide with the dominant data locus (materials_2606.04919_p21_c3, astro_2606.04712_p24_c1).
   The current absolute tiny-n threshold misses these.
4. **Panel / sub-panel separation.** Split merged adjacent panels or restrict extraction to the
   primary axes (materials_2606.03497_p23_c1, materials_2606.04219_p12_c1).
5. **Mask model-band / floor regions.** Don't over-sample faded model bands or zero-floor rows
   (astro_2606.02698_p7_c4, astro_2606.02726_p9_c1).
6. **Broken-axis handling.** Detect "//" axis breaks so the post-break region isn't mis-mapped
   (materials_2606.05057_p6_c1).
7. **(Recall, secondary) loosen the calibration gate for clean charts** currently false-negatived
   (RDF/PLA/dispersion line charts) — but only AFTER 1-3.

## Net assessment (honest)
The noise-fix iteration delivered a **small, real improvement**: strict precision 65.0% → 68.3%
(+3.3, within sampling noise), and the targeted extra-noise failure mode was **roughly halved**
(12 → 6 non-correct cases), with concrete, verifiable wins on out-of-axis points, reference/baseline
lines, and inset contamination. It did **not** make a structural difference: the remaining failures
are now led by non-chart chart-TYPES (contour/2D/dispersion maps), panel merges, and sparse
extractions, none of which the noise fix addresses. Closing the gap to 95% now requires a
chart-type gate (item 1) and diagonal-guide-line rejection (item 2), not more point-level
de-noising.
