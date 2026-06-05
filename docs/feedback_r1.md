# Feedback Round 1 — Overall-Precision Survey

Survey of 80 extracted charts (40 materials + 40 astro) from the current pipeline
(Round-1 2D-gate, Round-3 sparse-on-dense, legend fix all active).
Sample: `judge_survey.py --n 40 --seed 7`, stratified across papers and strata.
Index + verdicts: `$SCRATCH/pdf_chart2table/survey_r1/`.

---

## Headline results

| Corpus | n | correct | partial | wrong | **Strict precision** | Lenient (c+p) |
|---|---|---|---|---|---|---|
| Materials | 40 | 21 | 10 | 9 | **52.5%** | 77.5% |
| Astro | 40 | 23 | 10 | 7 | **57.5%** | 82.5% |
| **Combined** | **80** | **44** | **20** | **16** | **55.0%** | **80.0%** |

**The ≥95% precision bar is not met.** Strict precision is 55.0% combined, 40 points short.
Lenient (partial counted as acceptable) is 80.0%.

### Delta vs Judge 3 (68.3% strict / 85.0% lenient on n=60)

The strict precision dropped from 68.3% to 55.0%. This is a larger sample (80 vs 60 charts)
on fresh seed-7 data from the re-batched pipeline. The drop is real: extra-noise is the
dominant failure mode again — nearly identical in absolute count to Judge 2 before the noise-fix
iteration — suggesting that either (a) the noise-fix regressions occurred in the re-batch, or
(b) the seed-7 sample drew harder charts (more line-only series, more dual-y-axis charts,
more histograms). The non-chart and panel-merge rates are consistent with Judge 3.

---

## Failure-mode histogram (ranked, over the 36 non-correct charts)

| Failure mode | count | notes |
|---|---|---|
| **extra-noise** | **16** | 44% of all failures; dominant problem |
| panel-merge | 4 | dual-y-axis / adjacent panel merges |
| missing-series | 4 | dashed/dotted or faint series not extracted |
| non-chart | 4 | histograms, CMD/HR diagrams, stacked bars |
| legend | 3 | LaTeX label garbling only (data correct) |
| sparse | 3 | too few points extracted from richer chart |
| miscalibrated | 2 | axis-direction flip; wrong y-axis mapping |

---

## Detailed breakdown by failure mode

### 1. extra-noise (16 cases — most impactful)

Extra-noise is again the dominant problem. It spans several distinct sub-types:

**a) Slanted/diagonal fit/guide lines sampled as dense series (worst sub-type, 3 cases):**
- `materials_2606.05128_page6_chart2`: μ~n and μ~n^-0.5 power-law guide lines produce ~3 dense
  diagonal "series" drowning the actual scatter data. 12 series / 1504 pts, guide lines dominate.
- `materials_2606.02317_page14_chart1`: Dashed linear fit over a sparse scatter extracted as
  a dense orange series with ~25+ points; actual data markers underweighted.
- `materials_2606.00064_page2_chart1`: Vertical energy-level connectors (Grotrian-diagram style)
  have their endpoints extracted as marker pairs, producing spurious scatter.

**b) Model bands / shaded percentile regions over-sampled (4 cases):**
- `astro_2606.04762_page3_chart2`: 95%+68% PTA percentile bands extracted as huge dense blue
  series (4002 pts of band pixels); only the orange median is real data.
- `astro_2606.02032_page11_chart2`: GRB afterglow model error-band pixels included in "s" series
  (21639 pts).
- `astro_2606.00219_page16_chart10`: Shaded confidence bands fill re-plot with noisy scatter.
- `astro_2606.00569_page27_chart2`: Histogram steps bleed into frequency series.

**c) Histogram bar edges / step functions sampled (3 cases):**
- `astro_2606.00569_page9_chart5`: Black histogram step-tops sampled as extra dense series.
- `astro_2606.02353_page3_chart2`: Histogram top-edge creates extra orange series alongside
  the Gaussian fit.
- `astro_2606.01570_page10_chart12`: Error-bar end-caps at x=0.22 form a spurious vertical-column.

**d) Reference lines / dashed fit lines (3 cases):**
- `materials_2606.04608_page14_chart4`: Horizontal dashed reference line becomes an extra orange
  series at the top of the re-plot.
- `materials_2606.01408_page21_chart3`: Horizontal reference lines add spurious green dots.
- `materials_2606.01593_page4_chart1`: Solid + dashed line styles for same data treated as two
  separate offset series.

**e) Other / partial extra-noise (3 cases):**
- `materials_2606.02455_page8_chart1`: Theory dotted curves over-sampled.
- `materials_2606.04919_page24_chart1`: Small over-sampling near zero.
- `materials_2606.04544_page5_chart4`: Dual y-axis conductance trace (also panel-merge).

### 2. panel-merge (4 cases)

- `materials_2606.04608_page14_chart2`: Chart (a1) region includes a partial view of panel (a2)
  below it; extraction merges both panels, producing wrong x-axis range and series.
- `materials_2606.03497_page23_chart1`: Multi-panel figure with top and bottom panels merged
  into one bounding box; re-plot shows hybrid 4-series chart with no single correct shape.
- `materials_2606.01744_page21_chart1`: QHE fan diagram with dual y-axes (ρ_xx and ρ_xy);
  both are merged onto a single y-scale; re-plot is meaningless.
- `materials_2606.04544_page5_chart4`: Conductance staircase + Hall trace merged onto single axis.

### 3. missing-series (4 cases)

- `materials_2606.05050_page13_chart1`: 4 series on log-y expected; only 2 extracted (dashed
  intermediate lines not detected); orange series wildly displaced.
- `astro_2606.03667_page9_chart2`: Dense scatter with 42 series; re-plot only ~25 series; many
  faint/small markers not extracted.
- `astro_2606.05323_page9_chart1`: "W. Mean" reference level and scatter data partially absent.
- `astro_2606.00221_page11_chart2`: Second series (star-forming) absent; quiescent profile shape
  also incorrect.

### 4. non-chart (4 cases)

- `astro_2606.02190_page7_chart1`: Stacked-bar histogram (X-ray burst classification); top-edges
  of histogram bars extracted as line/scatter; not an xy line/scatter chart.
- `astro_2606.02688_page24_chart2`: Wrong extraction region — thumbnail of full page captured
  rather than the inset chart; data values completely unrelated to the actual chart.
- `astro_2606.03824_page3_chart1`: HR/CMD color-magnitude diagram with 49 series detected from
  a tiny region; mostly legend/colorbar pixels, not actual data.
- `astro_2606.04810_page8_chart3`: Step-function ionization phase chart with two y-axes;
  reconstruction shows wrong region.

### 5. legend (3 cases — data correct, labels wrong)

All three cases are **data-correct but legend-text garbled** due to LaTeX/math glyph extraction
limitations. Per the known limitation policy: flag only, do not chase.
- `materials_2606.00681_page4_chart4`, `materials_2606.05050_page18_chart1`,
  `materials_2606.02419_page9_chart1`.

### 6. sparse (3 cases)

- `materials_2606.02419_page3_chart1`: Only 4 points from a log-x chart.
- `astro_2606.02698_page11_chart14`, `astro_2606.02698_page11_chart2`: Both only 7 points from
  small panels (D4000 vs aperture axis plots).

### 7. miscalibrated (2 cases)

- `materials_2606.02317_page11_chart1`: X-axis reversed (original decreasing 290→282); re-plot
  shows 282→290 (axis-direction detection missing for reversed axes).
- `materials_2606.00403_page10_chart1`: y-axis mapping error — values mapped to wrong column.

---

## What is working well

The pipeline performs cleanly on single- and multi-series xy line/scatter charts with clear axis
ticks and no special decorations. Highlights from this survey:
- Dense smooth curves: `materials_2606.03897_page7_chart2` (sigmoid), `materials_2606.04147_page7_chart5`
  (V-shape), `materials_2606.01938_page7_chart2` (oscillation spectra), `materials_2606.04724_page18_chart4`.
- Multi-series log-log SEDs: `astro_2606.03881_page7_chart2`, `astro_2606.03881_page4_chart4`,
  `astro_2606.04140_page4_chart2`, `astro_2606.01775_page4_chart1`.
- Complex multi-series materials: `materials_2606.03452_page6_chart1` (4 phonon dispersion curves),
  `materials_2606.02317_page11_chart1` was only partially wrong (axis direction); the points themselves
  were correctly found.
- Molecular/emission line spectra: `astro_2606.03546_page5_chart8`, `astro_2606.05237_page163_chart4`.
- CDF/sigmoid astro charts: `astro_2606.00218_page14_chart2`, `astro_2606.04827_page5_chart2`.

---

## Actionable, module-level suggestions for coders

Ranked by expected impact on precision (fixing each would eliminate the tagged cases):

### #1 — Band/fill region rejection (extra-noise subtype b; ~4-5 cases → +5-6 pts strict)
**File: `marks.py` / `plot_region.py`**

The pipeline is sampling pixels from shaded/hatched confidence bands and model bands as if they
were data markers. These filled regions have characteristic properties:
- Their pixel color is a **lighter tint** (alpha-blended) of the main series color — significantly
  less saturated than the actual markers.
- They span a **contiguous 2D fill** (every column between two curves has pixels), unlike true
  scatter/line data which has gaps.

**Proposed rule:** In `marks.py`, after the color-cluster step, compute the **pixel-density
profile** of each candidate series: for each x-column within the plot box, count how many pixels
of that series color are present. A filled band has density ≥ 1 pixel for nearly every x-column.
A scatter series has density 0 for most columns. Reject any color cluster whose per-column
occupancy rate (fraction of x-columns with ≥1 pixel) exceeds 0.85 AND whose pixel count per
column averages > 2 (i.e., it's a thick band, not a thin line trace).

Alternative simpler rule: if a cluster has n_points > k * plot_width_px (i.e., more points
than would be expected for a sampled line, say k=3), and the points form a 2D blob rather than
a 1D locus (check aspect ratio of bounding box vs convex hull), flag as a fill region.

### #2 — Histogram step-edge rejection (extra-noise subtype c; ~3-4 cases → +4 pts strict)
**File: `marks.py` or `lines.py`**

Histograms produce characteristic step-function paths in the vector PDF (horizontal segment,
vertical segment, horizontal segment...). The pipeline currently samples these step edges as
"markers" or as a thick line series.

**Proposed rule:** Detect step-function paths: in the extracted path list, look for alternating
horizontal+vertical segments with no diagonal moves and a fixed step height (i.e., the
horizontal runs share the same y-values as the adjacent bin boundaries). If ≥6 such
right-angle step segments are found forming a monotone or peaked histogram shape,
classify the path as a histogram and skip series extraction from it.
Implementation: in `lines.py` after line-segment collection, flag paths where consecutive
segments alternate between near-zero slope and near-infinite slope (|slope| < 0.05 then
|slope| > 20), which is the step-function signature. These should not produce scatter series.

### #3 — Diagonal straight-line rejection (extra-noise subtype a; ~3 cases → +3 pts strict)
**File: `marks.py` or a new `filter_guides.py`**

Power-law guide lines (μ~n, linear fits drawn over data) are perfectly straight diagonal paths
in the PDF vector layer. The baseline/connector filter already handles axis-parallel
(horizontal/vertical) lines. Extend it to catch diagonal straight lines:

**Proposed rule:** After extracting candidate series, for each series with n_points > 15,
fit a straight line to the (x, y) coordinates. If the residuals from a linear fit are very
small (median absolute residual < 2% of y-range) AND the slope is neither 0 nor ∞ (i.e.,
|slope_normalized| between 0.05 and 20 in axis units), reject the series as a guide/fit line.

Note: This will also reject true data that happens to be exactly linear. Mitigation: only
apply when the series density (points-per-unit-x) is much higher than the other series in
the same chart (e.g., ≥3x more points per x-unit), since guide lines are typically sampled
much more densely than actual data.

### #4 — Reversed-axis detection (miscalibrated; 2 cases → +2 pts strict)
**File: `calibrate.py` or `axes.py`**

Some charts have a reversed x-axis (tick values decrease left-to-right, common in spectroscopy
where binding energy or wavenumber conventionally runs right-to-left).

**Proposed rule:** During axis calibration, after reading tick values from left-to-right,
check whether the tick-value sequence is monotonically decreasing. If yes, set a `reversed=True`
flag on the axis object and invert the linear mapping so that pixel positions near the right
edge get low tick values. This is a 2-line fix in the calibration mapping step.

### #5 — Non-chart type gating (non-chart; 4 cases → +3-4 pts strict)
**File: `plot_region.py`**

Stacked-bar histograms, HR/CMD diagrams, and step-function ionization charts slip through the
existing gates. Several heuristics could reject them:

**a) Stacked bar / histogram gate:** If the extracted path structure consists of many
axis-aligned rectangles filled with distinct colors (bar chart signature), reject the chart
region as a histogram/bar chart. In the vector layer, filled rectangles have 4-vertex closed
paths; count them. If filled-rectangle count > 5, likely a bar chart.

**b) HR diagram / dense scatter with colorbar:** If the number of detected series > 30 and
the bounding box of the legend/colorbar occupies > 20% of the chart region width, the chart
likely has a continuous colorscale rather than discrete series — reject or skip.

**c) Wrong region (tiny inset) detection:** If the extracted chart region has pixel area
< 10% of the page area but the "extraction" sources from a full-page thumbnail, detect by
comparing the PDF crop box dimensions to the extracted marker pixel spread.

### #6 — Panel / dual-y-axis split (panel-merge; 4 cases → +4 pts strict)
**File: `plot_region.py`**

This is the hardest structural fix. Two sub-cases:

**a) Adjacent sub-panels:** If the detected plot bounding box contains two distinct tick-axis
sets (e.g., the left half has axis ticks for 0–1 and the right half has ticks for 0–5),
split the region into sub-panels at the dividing x coordinate. Currently there is no detection
of this.

**b) Dual y-axis:** If there are tick labels on both the left and right y-axis with different
ranges, two separate calibration mappings exist. Assign series to the axis with matching
tick-label color (if colored axes) or nearest tick marks. This requires detecting right-side
y-axis ticks and treating them as a second y-calibration.

Both are significant development effort. Impact: 4 cases, moderate priority.

### #7 — Missing dashed/dotted series detection (missing-series; 4 cases → +2-3 pts strict)
**File: `lines.py`**

Dashed and dotted line series are currently missed when the gap-to-segment ratio is large.
The path decomposition in `lines.py` likely drops short segments below a pixel-count threshold.

**Proposed rule:** Reduce the minimum segment length threshold for series that are the only
path of a given color (i.e., no solid counterpart exists). A dashed line's individual
segments are short but consistently spaced; detect them by checking for evenly-spaced short
collinear segments of the same color, and stitch them into a single series.

---

## Modes with no clean fix (do not pursue)

- **Legend-text garbling (3 cases):** LaTeX/Unicode glyph extraction is a known fundamental
  limitation. The glyphs are not stored as text in the PDF; they cannot be extracted accurately
  by any PDF text-extraction library. The data itself is correct; only the legend name is wrong.
  Do not chase.
- **Very small panels / sparse charts (3 cases):** When a chart physically occupies <1 cm² on
  the page, pixel resolution is too low for reliable point extraction. These should be skipped
  by a size gate rather than fixed.

---

## Net assessment

Strict precision is 55.0% combined (down from 68.3% in Judge 3, but on a larger/harder sample
with seed 7 vs seed 0). The dominant failure is **extra-noise from filled bands, histogram
steps, and diagonal guide lines** — these 16 cases alone account for 44% of all non-correct
outcomes. Fixing just the top-3 levers (band rejection, histogram-step rejection, diagonal
guide rejection) would directly address ~10 of the 36 failures, adding approximately **+12-13
strict precision points** if successful, bringing the estimate to ~67-68% strict. Getting to 95%
would additionally require panel-merge fixes and more robust line-style detection.
