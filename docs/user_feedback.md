# User feedback on data recovery (2026-06-05)

20 charts the user hand-flagged with ground-truth corrections, applying to the
**current** reconstructions in `$HOME/shared_folder/chart2table_examples/<folder>/<paper>/<base>/`
(each folder has `reconstruction.png` + `chart.json` + `feedback.txt`). Eval set +
symlinked PDFs: `$SCRATCH/pdf_chart2table/feedback_eval/` (`manifest.csv`).
IMPORTANT: never `rm -rf` these example folders — it deletes the user's feedback.txt.

Corpora: semiconductor→materials_pdfs, astro→astro_pdfs, pdf_chart2table→pdfs (ML), fs→user PDFs.

## By owning module (= the coder that should fix it)

### calibrate.py + axes.py — axis/tick recovery
- astro 2606.00212 p12c5: y is LOG 10¹²–10¹⁶ increasing upward; read as LINEAR (tick 10^12→"102"=100, data_range garbage). Exponent-tick + log-detection.
- ml 2606.04212 p9c1: y is log, reported linear. Also x label should be `alpha_test`.
- ml 2606.04662 p6c1: y is log, reported linear; y label garbled (LaTeX).
(Quantitative synthetic reproducer already exists: `exponent_ticks_large`.)

### lines.py — line recovery
- ml 2606.01172 p34c11: solid blue line not detected.
- materials 2606.01515 p7c4: 6 lines expected (2 solid red, 2 solid blue, 1 dotted green); only the dotted green reported → solid lines dropped.
- ml 2606.04662 p6c1: solid blue line not reported; variance SHADE reported instead of the solid orange line.
- astro 2606.01775 p6c1: green/orange/blue lines missing.
- fs batch-finetune-ctx-input-grid_04-06 p7c4: reported the limits of a shaded green BOX — should not (band/fill, not data).
- materials 2606.04147 p3c2: report only the markers, NOT the solid (fit/guide) line.
(Synthetic reproducers: solid_dashed_same_color, dashed_same_color, dotted_3styles, four_dashed_semilogy.)

### marks.py — marker recovery
- astro 2606.02617 p10c1: blue markers in the middle missing.
- materials 2606.02085 p5c1: light-green inverted-triangle + light-orange markers missing.
- ml 2606.01457 p25c3: one marker missing on the solid blue line at ~(80,21).
- materials 2606.02858 p2c1: reported two series but there is only one marker type (orange circles) → over-split.

### labels.py — legend recovery (user gave the correct legend text)
- astro 2606.02687 p7c2: should NOT emit legends for the big markers.
- materials 2606.01373 p7c9: legend swatch points reported as data; legend should be: "linear cavity(y), g'=0.4" / "without cavity".
- materials 2606.01515 p7c4: legend missing: δt_r=0 / δt_r≠0 (↑) / δt_r≠0 (↓).
- materials 2606.02085 p5c1: legend missing: 310 / 580 / 930 / 3200 / 5200 / 7200.
- materials 2606.02317 p11c2 (hard): legend = Silicon Nitride (green) / Silica (black) / Alumina (red) / Hafnia (blue).
- materials 2606.03405 p12c2: legend missing: P=8 (dashed) / P=32 (solid).
- ml 2606.03553 p32c2: legend "epsilon" bug — LaTeX related.
- ml 2606.04662 p6c1: legend missing (also see lines/calibrate items for this chart).

### plot_region.py — panel split
- ml 2606.04777 p13c1: this is TWO graphs, reported as one → split.

### Detection (DEFERRED to downstream LLM discard — do NOT prioritize per steer)
- astro 2606.04810 p12c9 & p12c15: heatmaps that slipped through; the multimodal LLM discard pass will drop these.

### Cosmetic (rendering/crop)
- astro 2606.01775: crop margin too tight (y ticks not visible) — `make_examples` crop margin.
