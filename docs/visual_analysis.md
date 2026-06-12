# Visual analysis — restyle prototype (20 charts)

Systematic visual comparison of the RECONSTRUCTED panel vs the ORIGINAL panel
for all 20 bundles in `restyle_prototype/`. Each chart lists every observed
mismatch with a category tag and severity:

- **DATA** = the recovered data differs (missing/extra/wrong series or points). High severity.
- **AXIS** = range / ticks / scale / multiplier / label-text wrong.
- **TEXT** = title / axis-label / annotation: presence, font, size, position.
- **LEGEND** = presence / placement / box / orientation / size.
- **MARKER / LINE / COLOR / LAYOUT** = cosmetic style fidelity.

Severity: **[D]** data-level (changes the numbers a reader takes away),
**[C]** cosmetic (style only).

Chart_ids analysed (from `/tmp/regen_cids.txt`): 2005.11717_p28c4, 2005.12088_p10c2,
2102.11637_p6c5, 2106.12703_p19c2, 2112.11900_p3c10, 2204.11743_p19c4,
2205.10303_p26c5, 2206.14073_p4c4, 2208.14630_p20c2, 2211.04615_p26c1,
2212.05730_p6c2, 2212.10848_p16c2, 2301.10421_p5c2, 2409.17350_p9c1,
2410.00955_p10c1, 2412.10191_p4c1, 2503.12775_p12c4, 2504.16333_p32c4,
2510.04789_p3c4, 2511.03205_p23c3.

---

## Per-chart mismatches

### 2005.11717_p28c4 (R_c vs V, two line+marker series)
- DATA: series shape recovered well; both R_c,large (red) and R_c,small (blue)
  tracks match the original. **[C]** minor — only the rightmost few points appear
  slightly truncated (x stops ~0.25 vs 0.3 in original).
- AXIS **[D]**: y-axis range wrong. Original tops at ~750 (labels 0/500/1000/1500/2000/2500
  but data lives 200–750); reconstruction shows 250–2500 so the data sits in the
  lower third. y data_range / tick interplay (calibration cluster).
- TEXT **[C]**: the "title" is the garbled string `p-dop 5x10 V MS [V] cm` — this
  is a mis-extracted in-plot annotation (`p-dop 5×10¹⁹ cm⁻²`) promoted to title;
  the original has NO title. Mangled superscript/subscript glyphs.
- LEGEND **[C]**: present and correctly placed (upper-right, vertical). Labels
  render as `Rclarge`/`Rcsmall` (lost the underscore subscript) — cosmetic glyph loss.

### 2005.12088_p10c2 (sawtooth G vs t/T, single magenta line)
- DATA **[C]**: excellent — 4 sawtooth periods, amplitude and shape faithfully
  reconstructed.
- COLOR **[D-ish]**: original line is magenta; reconstruction line is also
  magenta/pink ✓.
- AXIS **[C]**: y 0–0.15, x 0–1 match. Original had twin y-axes (left+right both
  0–0.15); reconstruction shows single y-axis — acceptable simplification.
- TEXT **[C]**: in-plot `(b)` panel label preserved at top-left ✓.

### 2102.11637_p6c5 (scatter + dashed fit, shaded band)
- DATA **[C]**: 7 black filled-circle data points recovered; positions match.
- LINE **[C]**: the dashed model curve is reconstructed (dotted) and tracks the
  original's dip-and-peak shape.
- MARKER **[C]**: data markers render much SMALLER than the original's bold dots.
- LAYOUT **[D]**: the grey SHADED confidence band present in the original is NOT
  reconstructed (no fill/band primitive). Loses visual context.
- AXIS **[C]**: y-range/ticks (10–70) reasonable; x 5–20 ok.

### 2106.12703_p19c2 (3 step-pulse traces, multi-color)
- DATA **[C]**: the three square-pulse traces (circuit input, GaN output,
  preamplifier output) are reconstructed with correct levels and timing.
- AXIS **[D]**: y-axis is INVERTED — original 20 (top) → −15 (bottom) with the
  blue trace dipping to −10; reconstruction y runs −15→20 but the layout is
  flipped relative to original (the negative excursions and the orientation of
  the orange 0-level differ). Calibration/orientation.
- TEXT **[D]**: "title" = garbled `ns; Repetition Frequency = MHz` (fragment of
  the real caption `Pulse = 60 ns; Repetition Frequency = 5 MHz`); not a title in
  original. The cyan/magenta annotations `Slew rate out:3557.9 V/µs` and
  `Slew rate in:4344.4 V/µs` are SPLIT into fragments (`Slew`, `rate`, `in:4344.4`,
  `V`, `µs`) placed separately, and rendered in black not their original cyan/magenta.
- LEGEND **[C]**: present (3 entries) but text is tiny/illegible vs original.

### 2112.11900_p3c10 (noisy FFT trace)
- DATA **[C]**: the noisy oscillating trace is well recovered (middle panel tracks it).
- AXIS **[D]**: y-axis range wrong — original effective range puts the trace
  mid-panel; reconstruction shows 22–62 with the trace compressed. Only ~2 y
  ticks recovered → poor calibration (documented in qa_findings as 2112 cluster).
- TEXT **[C]**: annotation `780 mK` is split (`780` and `mK` placed apart);
  x-label `B⊥(mT)` present.
- AXIS **[C]**: x tick labels 51/59 present but the 63/71 top-axis ticks dropped.

### 2204.11743_p19c4 (single exp-decay curve C_Min) — FIXED
- DATA **[C]**: the C_Min exponential-decay curve is recovered accurately.
- AXIS **[D] → FIXED**: was collapsed to a flat line because a spurious tick
  value `680.18` (mis-extracted) forced the y-view to [0.1, 680]. After the
  `_ticks_in_range` fix the reconstruction shows the correct 0.03–0.09 decay.
- TEXT **[C]**: legend `C_Min` rendered as split `C` / `Min` fragments (annotation
  splitting, deferred).

### 2205.10303_p26c5 (scatter + power-law dashed fit)
- DATA **[C]**: 8 black data points + dashed power-law fit recovered; shapes match.
- MARKER **[D]**: reconstruction markers are MUCH smaller (tiny dots) than the
  original's bold filled circles. The fit curve is correctly NOT connected to points.
- TEXT **[C]**: annotation `P^{1.41±0.14}` split into `P` and `1.41 ± 0.14`
  fragments; `|XX_D^-⟩` ket split into `|XX` / `D` fragments. Positions drift.
- AXIS **[C]**: y 0–100, x 0–40 ok.

### 2206.14073_p4c4 (single resistivity curve, log-ish)
- DATA **[C]**: the ln(ρ) curve with its peak at 1/T≈0.057 is reconstructed
  faithfully (shape, peak, tail all match).
- AXIS **[C]**: y −3..12, x 0.02–0.08 match well.
- TEXT **[C]**: annotation `1.52 GPa` preserved; `Δ = 56 meV` rendered as `= 56 meV`
  (the Δ glyph dropped). Minor.
- Overall one of the best reconstructions.

### 2208.14630_p20c2 (7 overlapping resistivity curves, multi-color)
- DATA **[C]**: all 7 pressure curves recovered with correct colors (red→brown
  ordering) and the characteristic hump near 100 K. Good.
- AXIS **[C]**: y 0–1, x 0–300 match.
- TEXT **[C]**: annotations `P = 0 kbar`, `sample a3`, `20.3 kbar` preserved and
  roughly placed. The arrow annotation (original has a diagonal arrow) is not drawn.
- LAYOUT **[C]**: aspect/box slightly taller than original. Minor.

### 2211.04615_p26c1 (3 stacked XPS peaks, offset)
- DATA **[C]**: the three stacked photoemission peaks (offset by 1.0) recovered
  with correct shapes; all rendered red (matches original).
- AXIS **[C]**: x-axis (binding energy 83–85) correct; the secondary top axis
  (kinetic energy 1397–1399) is dropped.
- TEXT **[D]**: the boxed labels `Au/WSe2 36Å`, `Au/WSe2 12Å`, `Au(111)` and the
  `Au 4f7/2 / Al Kα` label are reconstructed but as fragmented spans with leader
  lines drawn AS DATA-like strokes (the black diagonal lines in the recon are
  spurious — they connect split annotation fragments). The vertical tick marks
  (peak-position bars) and the dashed reference line are partially lost.

### 2212.05730_p6c2 (S-curve + two fit lines, sideways)
- DATA **[C]**: the blue sigmoid (drawn sideways/folded), the yellow steep line,
  and the red dashed shallow line are all recovered with correct orientation
  (draw-order preserved, not x-sorted). Good.
- COLOR **[C]**: blue/yellow/red preserved ✓.
- AXIS **[C]**: x −10..10, y −5..5 match; original had a secondary right axis
  (−2..2) dropped.
- MARKER **[C]**: the red ✗ marker at origin in the original is not reconstructed.
- LAYOUT/AXIS **[C]**: original grid (dashed) reconstructed ✓.

### 2212.10848_p16c2 (pDOS, 3 thin curves, horizontal orientation)
- DATA **[C]**: Ni/Hf/Pb partial-DOS curves recovered with correct colors and the
  sharp peak near y≈6.
- AXIS **[D]**: the original is a HORIZONTAL-orientation plot (energy on y, DOS on
  x) with x running 0..>10 and a spike to ~15. Reconstruction keeps orientation
  but the x-axis upper bound / spike scaling looks compressed.
- LEGEND **[C]**: present (Ni/Hf/Pb) but rendered LARGE and low-right, overlapping
  the plot, vs the original's compact lower-right legend. Legend-size fitting
  under-shrank.
- TEXT **[C]**: title `pDOS` duplicated (suptitle + panel title). Minor.

### 2301.10421_p5c2 (8 scatter points, colored, labeled 1–8)
- DATA **[D]**: SEVERAL data points are MISSING in the reconstruction. Original
  has 8 colored points plus dashed guide lines; reconstruction shows only ~3
  points (two teal near (0.5,0.85)/(1,0) and labels 5/6/7/8 with no markers).
  Most colored markers dropped — likely color/series-splitting at extraction
  (each point is its own color → treated as near-white/!merged). High severity.
- TEXT **[C]**: numeric point labels 1–8 partly preserved as annotations but
  drift from their points.
- LINE **[D]**: the two dashed guide lines (diagonal + horizontal at y=0) are lost.

### 2409.17350_p9c1 — (NOTE: bundle shows a DIFFERENT chart than id suggests)
- This rendered as the 8-point colored scatter (same as 2301.10421 visual). See
  2301.10421 notes; the missing-markers DATA issue applies. (The two share the
  triangular-lattice scatter layout.)

### 2410.00955_p10c1 (two sigmoid scatter series, blue + green)
- DATA **[C]**: both N_iris=1 (blue) and N_iris=19 (green) sigmoid point-clouds
  recovered with correct shape and color.
- MARKER **[C]**: markers slightly larger than the original's tiny dots, but ok.
- LINE **[C]**: a faint connect-through-scatter line is drawn in the recon that is
  not in the original (known render-side connect artifact, documented in qa_findings).
- LEGEND **[D]**: legend entries `N_iris = 1` / `N_iris = 19` are reconstructed as
  TEXT annotations top-left WITHOUT color swatches (labels were None at extraction;
  the text is in-plot, not a real legend). Documented in qa_findings (legend glyphs
  need OCR). The original's framed legend with colored dots is lost.
- AXIS **[C]**: x 0.8–1.8, y 0–1 match; x-label `Perturbation power (*0.1W)` ✓.

### 2412.10191_p4c1 (6 transistor I_D curves, multi-color)
- DATA **[C]**: 5 of 6 curves recovered with correct colors (red/green/blue/cyan/
  magenta) and saturation shapes. The flat **0 V black curve is dropped** (lies on
  the x-axis → near-zero, treated as baseline). Known/acceptable.
- LEGEND **[C]**: legend recovered (5V..40V) but missing the 0V entry (matches the
  dropped series). Color swatches correct.
- AXIS **[C]**: y 0–8000 (data tops ~6400), x 0–50 — reasonable.
- TEXT **[C]**: `V_G` legend title fragmented (`V` / `G`). x-label/y-label present.

### 2503.12775_p12c4 (two curves, x10^-4 multiplier) — calibration FIXED earlier
- DATA **[C]**: the decaying A_100 curve and flat R_100 baseline recovered.
- AXIS **[C]**: y multiplier ×10⁻⁴ now handled (recon shows 0.00000–0.00018);
  x 0–1 correct. This was the 2503 calibration fix (done).
- TEXT **[D]**: the legend entries `U = A_100^(α)` / `U = R_100` render as garbled
  boxed glyphs (`□=□` with subscripts) top-right — mangled LaTeX legend text
  (deferred, OCR). y-label `CvM Distance` partly garbled.
- COLOR **[C]**: the R_100 dashed orange baseline is reconstructed but very faint.

### 2504.16333_p32c4 (3 force curves -4/0/+4, line styles)
- DATA **[D]**: the three F_z curves (red solid, green dashed, blue dotted) — the
  blue dotted dominant peak (rising to ~4.4) is REPLACED in the recon by a straight
  diagonal line; only the red and green small humps near the bottom survive.
  The blue curve's extraction/scaling is wrong (rendered as a line spanning the
  whole y-range). High severity.
- AXIS **[D]**: y-range wrong — original 2.0–5.0; recon shows 12.0–15.0 (offset/
  multiplier mis-calibration). x 7–8 ok.
- TEXT **[C]**: legend `F_z, Gap x(µm) -4/0/4` fragmented into scattered spans;
  `+8%`/`-4%` measurement annotations and the vertical double-arrow lost.

### 2510.04789_p3c4 (square markers + error bars + fit line, dark blue)
- DATA **[D]**: the dark-blue square data points are recovered, but the recon draws
  spurious vertical line segments / a jagged connecting polyline through them
  (the error bars were extracted as connectable strokes and the points got
  connected). Original = discrete squares with vertical error bars + a smooth
  orange fit line. The orange fit line is missing in the recon.
- MARKER **[C]**: square markers preserved (filled dark blue) ✓.
- TEXT **[C]**: annotations `φ = 3.7 mrad`, `δ = -0.98 meV`, `|X|² = 0.34` partly
  recovered/fragmented; `(c)` panel label preserved.
- LEGEND **[C]**: a legend is drawn (`= 3.7 mrad`, `dCph-X0 = -0.98 meV`) that
  conflates annotation text with legend — not present as a legend in original.

### 2511.03205_p23c3 (3 points + 1/(M-1) dotted fit)
- DATA **[C]**: 3 blue data points + the dotted ∝1/(M−1) curve recovered; shapes
  and positions match.
- MARKER **[D]**: marker SIZE is wrong — the recon's three dots shrink with x
  (large→medium→tiny) whereas the original's are all the same size. Marker-size
  recovery picked up per-point size variation incorrectly.
- TEXT **[C]**: annotation `∝ 1/(M−1)` fragmented into `1`/`M`/`1` placed in the
  middle of the plot (lost the ∝ and fraction structure).
- AXIS **[C]**: x 0–250, y 0–0.5 match; grid present ✓.

---

## Cross-chart themes (ranked by frequency / impact)

### 1. Mis-extracted / fragmented in-plot TEXT (annotations, legend text, titles) — ~14/20  [mostly C, some D]
By far the most pervasive issue. Multi-span labels in the source PDF (LaTeX
math, sub/superscripts, kets, units) are broken into single-token spans that are
then placed individually (`Slew` `rate` `in:4344.4`; `P` `1.41±0.14`; `|XX` `D`;
`1` `M` `1`; `C` `Min`). Two failure modes:
  - **(a) annotation splitting** — pieces scattered across the plot, losing
    structure and original color (e.g. cyan/magenta slew-rate labels rendered black).
  - **(b) fake titles** — a fragment of an in-plot annotation or caption is promoted
    to the chart TITLE (2005.11717 `p-dop 5x10 V MS [V] cm`; 2106.12703
    `ns; Repetition Frequency = MHz`). The original has no such title.
  - **(c) legend text as glyphs** — legend entries that are LaTeX (2503 `U=A_100`,
    2510 `dCph-X0`) render as boxes/garbage.
**Disposition: DEFERRED.** Robust fixing needs OCR + span-grouping of the source
text (already flagged for 2410 legend glyphs in qa_findings). Not precision-safe
to patch render-side. Highest-value future work.

### 2. Y-axis range / multiplier / tick mis-calibration — ~7/20  [D]
The recurring "calibration cluster": 2005.11717 (y 250–2500 vs data ~750),
2112.11900 (22–62 squashed), 2204.11743 (FIXED — 680 outlier), 2206/2503 (×10
multiplier — DONE), 2412 (headroom), 2504.16333 (12–15 offset), 2106.12703
(inverted/flipped). Symptoms: data compressed into a fraction of the panel, or
an offset/multiplier dropped, or a single bad tick dominating the view.
**Disposition: ONE fix shipped** (`_ticks_in_range` removes wild-outlier ticks —
fixes 2204). The rest are upstream calibration (`axes.py`/`calibrate.py`),
DEFERRED to the calibration cluster work.

### 3. Marker SIZE wrong (too small, or spuriously varying) — ~5/20  [C]
2102.11637, 2205.10303 (markers far too small vs bold original dots);
2511.03205 (sizes shrink left→right instead of constant); 2410 (slightly large).
Recovered glyph diameter is noisy. **Disposition: DEFERRED** — tractable but the
median-diameter recovery interacts with the marker-shape classifier; a careless
bump would over-size some charts. Worth a focused follow-up.

### 4. Spurious connecting lines / lost fit or guide lines — ~4/20  [D]
2510.04789 (error-bar strokes connected through square markers; orange fit lost),
2504.16333 (blue curve → straight diagonal), 2301.10421/2409.17350 (dashed guide
lines lost), 2410 (faint connect-through-scatter — known render artifact).
**Disposition: DEFERRED** — `connect`/threading heuristic plus extraction; deep.

### 5. Dropped series / points — ~3/20  [D]
2301.10421/2409.17350 (most colored scatter points missing — each-point-own-color
splitting), 2412.10191 & 2301 (flat baseline curve on the x-axis dropped — known
acceptable). **Disposition: DEFERRED** (extraction-side color/series handling).

### 6. Lost secondary axes, shaded bands, arrows, error bars — ~6/20  [C/D]
Twin/right axes (2005.12088, 2212.05730, 2211.04615, 2112.11900), grey
confidence band (2102.11637), annotation arrows (2208.14630, 2504.16333), error
bars (2510.04789). The reconstruction has no primitive for these.
**Disposition: DEFERRED** — out of scope for a style re-plot; documented for
completeness.

### 7. Legend size / placement off — ~2/20  [C]
2212.10848 (legend too large, overlaps plot — size-fitting under-shrank);
2510.04789 (annotation conflated into a legend). Minor. **Disposition: DEFERRED.**

---

## Fixes applied this round

- **`_ticks_in_range` (render-side, `scripts/render_restyle_prototype.py`)** —
  drops tick values that fall more than one full axis-span outside the parser's
  calibrated `data_range`. A single mis-extracted tick (`680.18` on 2204.11743's
  0.03–0.10 axis) was forcing matplotlib's view to [0.1, 680] and collapsing the
  curve to a flat line. After the fix the curve renders correctly at 0.03–0.09.
  Verified by re-render. Regression tests added in
  `tests/test_restyle_prototype.py` (`test_outlier_tick_dropped_from_range`,
  `test_legitimate_edge_ticks_kept`, `test_range_filter_noop_without_range`,
  `test_range_filter_keeps_all_when_too_few_survive`). Full suite green
  (1025 passed, 27 skipped, 5 xfailed, 1 xpassed).

## Deferred (documented, not patched — too deep / ambiguous / precision-risk)

- Annotation/legend/title text fragmentation & LaTeX glyph garbling (theme 1) →
  needs OCR + span grouping.
- Calibration-cluster y-range/multiplier/orientation errors (theme 2, beyond the
  outlier-tick fix) → `axes.py`/`calibrate.py`.
- Marker-size recovery noise (theme 3) → focused tuning, interacts with shape classifier.
- Spurious/lost lines, dropped series, secondary axes/bands/arrows/error bars
  (themes 4–6) → extraction-side, out of scope for a style re-plot.

## Needs the parent to regenerate the shared folder

The only re-render performed was `2204.11743_p19c4` (to verify the tick fix). The
`_ticks_in_range` change is general and may slightly adjust tick placement on any
chart whose extraction produced an out-of-range tick. **Parent should regenerate
all 20 bundles** so every reconstruction reflects the fix (none should regress —
the filter only removes ticks already outside the trusted data_range).
