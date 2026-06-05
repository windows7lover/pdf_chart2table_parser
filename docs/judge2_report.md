# Judge 2: Precision of the fully-gated extraction pipeline (materials + astro)

Multimodal reconstruction-judge audit of the now fully-gated pipeline on two real-paper
corpora. Each sampled chart was rendered as a side-by-side reconstruction
(LEFT = original chart region with extracted marker pixels overlaid; RIGHT = re-plot of
the extracted x,y) and visually judged.

- Sampler: `scripts/judge_sample2.py` (adapted from `judge_sample.py`), seed 0, stratified
  across papers and strata (log/linear, single/many series, scatter/line), cap 2 charts/paper.
- Sample: 30 materials + 30 astro extracted charts (n_points>0), all rendered successfully.
- Renders + index: `$SCRATCH/pdf_chart2table/judge2/` (`sample_index.csv`, `skip_index.csv`).
- Verdicts: `$SCRATCH/pdf_chart2table/judge2/judge2_verdicts.csv`.

Verdict rubric: **correct** = extracted points lie on the real marks/curves AND the re-plot
matches shape & axis ranges; **partial** = mostly right (minor missing/extra series, slight
calib drift, label-only wrong, recoverable noise); **wrong** = data doesn't match (tick/grid
artifact, miscalibration, non-chart, panel-merge, garbage).

## Headline results

| Corpus | n | correct | partial | wrong | **Strict precision** | Lenient (c+p) |
|---|---|---|---|---|---|---|
| Materials | 30 | 20 | 7 | 3 | **66.7%** | 90.0% |
| Astro | 30 | 19 | 7 | 4 | **63.3%** | 86.7% |
| **Combined** | 60 | 39 | 14 | 7 | **65.0%** | 88.3% |

**The ≥95% precision bar is NOT met** — strict precision is 65% combined (67% materials,
63% astro), well short of 95%. Even the lenient metric (counting "partial" as acceptable)
is only 88%.

### Comparison to the 53% baseline
The prior judge measured **53% strict precision** on an earlier (pre-fix) ML batch. The
fully-gated pipeline on these real materials/astro corpora reaches **65% strict** — a
genuine ~12-point improvement, consistent with the gates doing useful work (they correctly
skip many non-charts; see skip audit). But the gains are far from sufficient: the pipeline
is still ~30 points below its own bar.

## Failure-mode histogram (ranked, over the 21 non-correct charts)

| Failure mode | count | example ids |
|---|---|---|
| extra-noise | 12 | astro_2606.05323_p10_c2, astro_2606.04232_p27_c3, materials_2606.01515_p3_c1 |
| miscalibrated | 4 | materials_2606.04724_p16_c2, materials_2606.00223_p6_c1, materials_2606.03278_p4_c1 |
| non-chart | 2 | astro_2606.04810_p12_c14 (2D hexbin), astro_2606.02190_p7_c2 (histogram) |
| label-only | 1 | materials_2606.04919_p25_c4 |
| missing-series | 1 | materials_2606.04675_p4_c1 |
| panel-merge | 1 | materials_2606.03016_p9_c1 |

The dominant problem by far is **extra-noise**: spurious points/series that contaminate an
otherwise-correct extraction. These come from a few recurring sources:

1. **Legend / annotation markers extracted as data** — legend swatches, arrow annotations,
   and corner labels get picked up as data points
   (materials_2606.03278_p2_c2 is the worst: ONLY arrow annotations were extracted, no real
   data; astro_2606.03881_p4_c4 has gross legend-corner outliers).
2. **Out-of-axis markers** — points detected outside the plotting box form spurious
   vertical columns or floor rows (astro_2606.03037_p12_c2, astro_2606.04140_p18_c1,
   materials_2606.00223_p6_c1).
3. **Axis / baseline lines sampled as a series** — a border or zero-baseline becomes a fake
   diagonal/flat "series" (astro_2606.04232_p27_c3 diagonal; astro_2606.03313_p5_c5 zero line).
4. **Connector / trend lines sampled as extra points** (astro_2606.03522_p12_c2).
5. **Inset / second-panel contamination** — markers from an inset plot or the inset image
   merge into the main series (materials_2606.03016_p9_c1, astro_2606.02688_p4_c1).

**Outright wrong (7)** break down as: 3 miscalibrated (structure lost / markers off-plot),
2 extra-noise so severe the data is meaningless (annotation-only n=2 extraction
astro_2606.04232_p50_c1; jumbled UVLF astro_2606.05323_p10_c2), and 2 non-charts that slipped
the gates (a **2D hexbin spatial map** and a **histogram** — neither is an xy line/scatter
chart and both should be rejected by type).

## What is working well
- **Single/multi-series line and scatter charts with clean axes reconstruct beautifully.**
  Sawtooth, decay, sigmoid, parabola, resonance-dip, RDF, periodogram-peak, and noisy
  spectra are all reproduced with correct shape and axis ranges (e.g.
  materials_2606.00479_p2_c1, materials_2606.04958_p6_c1, astro_2606.00721_p19_c3,
  astro_2606.05237_p163_c4/c5).
- Garbled series **labels/legends** (e.g. "^", "s") are common but usually do NOT corrupt the
  numeric data — they are cosmetic. Several "label-only-ish" charts were still scored correct
  because the data was right.
- Calibration (axis a/b fits) is generally accurate when axes are detected; most errors are
  detection/contamination problems, not linear-calibration problems.

## Skip audit (10 materials + 10 astro skipped charts spot-checked)
Skips are split between legitimate rejections and lost-good-charts:

- **Correctly skipped non-charts (~8/20):** 2D heatmaps/contour maps
  (astro_2606.04044_p11_c1), MCMC corner plots (astro_2606.03597_p20_c2 & p24_c3),
  radio image panels (astro_2606.02413_p10_c1), spin-wave 2D maps
  (materials_2606.00849_p5_c1), network/graph diagram (materials_2606.05050_p13_c4).
- **Lost good charts — false negatives (~7-8/20):** clean line charts skipped for
  "no axis calibration" or "no series extracted" that clearly contain extractable data:
  materials_2606.04724_p22_c2, materials_2606.00068_p15_c1 (PLA hardness),
  materials_2606.00824_p3_c2 (Lorentzian), materials_2606.00165_p3_c2 (Vacuum Correlations —
  same data the pipeline extracted correctly on another page), astro_2606.03881_p9_c4 &
  p10_c3 (clean LHAASO/HAWC data+fit), astro_2606.03261_p21_c1.
- **Conservative-but-defensible skips:** busy multi-dataset UVLF panels
  (astro_2606.02738_p4_c3) were skipped — and the *one* such panel that WAS extracted
  (astro_2606.05323_p10_c2) came out **wrong**. So the gate is correctly avoiding a known
  precision pitfall here, at the cost of recall.

**Conclusion on skips:** the gates are conservative — they protect precision somewhat (no
gross garbage was found among the skips that should clearly have been kept as correct) but
sacrifice substantial recall. This matches the project stance that recall may be low. None
of the skips were "lost good charts that would have extracted *cleanly*" in a way that
inflates the wrong-rate; the precision shortfall is entirely among the EXTRACTED set.

## Prioritized fix list (to move precision toward 95%)
1. **Reject non-data primitives before they become points (biggest lever — addresses the
   12 extra-noise cases).**
   - Drop any marker whose pixel lies **outside the calibrated axis box** (with a small
     margin). This alone kills the out-of-axis columns/floors and several "wrong"s.
   - Exclude legend regions, arrow/annotation glyphs, and text from marker detection
     (detect the legend bbox and the in-plot text spans and mask them).
2. **Don't let axis/baseline/border lines or connector lines become a series.** Filter
   perfectly-straight, full-width/height runs and dashed connectors; require a series to have
   non-degenerate 2D extent and to not coincide with the axis frame.
3. **Add a chart-type gate to reject 2D maps and histograms** (the 2 non-chart wrongs):
   detect filled hexbin/heatmap/contour fields and bar/histogram geometry, and skip them
   (they are not xy series).
4. **Inset / sub-panel separation.** When a region contains a nested inset or a second panel,
   either split into separate charts or restrict extraction to the primary axes
   (fixes panel-merge + several extra-noise contaminations).
5. **Sanity-check tiny n / degenerate extractions.** An extraction yielding only 2-5 points
   that coincide with annotation glyphs (astro_2606.04232_p50_c1) should be rejected.
6. **(Recall, secondary) loosen the calibration gate for clean charts** that are currently
   false-negatived — but only AFTER 1-5, since loosening now would admit more of the busy
   multi-dataset panels that extract as "wrong."

Net: the pipeline is solid on clean single/multi-series xy charts but is dragged from ~88%
(lenient) to 65% (strict) almost entirely by spurious points from legends, annotations,
out-of-axis detections, and axis/baseline lines, plus a small number of non-chart types
slipping through. Fixes 1-3 target the majority of the failures.
