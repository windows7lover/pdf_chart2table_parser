# QA findings — random reconstruction spot-checks

A QA loop samples 3 random reconstructions (`scripts/qa_sample.py`) and logs any
extraction/reconstruction problems here. Newest round on top. Each item: what's
wrong → likely cause → owner. Fixed items get struck through with the commit.

## Round 9 (2026-06-12) — post-regen spot-check (clean 23-set)
Draw: 2302.01559_p38c3, 2110.09149_p10c1, 2108.13102_p83c1.
- **2110.09149_p10c1** [~clean]: scatter faithful. JSON: blue [182pts] + orange [36] are correctly `marker=x shape=x` (my thumbnail "blue squares" read was WRONG — verify-vs-JSON caught it). Minor: two tiny 3-pt series have marker/shape mismatch (s vs x). Dashed ref-lines (x=32, y=283) not drawn (decoration). Not worth a fix.
- **2108.13102_p83c1** [defer]: known dual-Y-axis limitation (point 3 / Round 6), unchanged.
- **2302.01559_p38c3** [D → FIXED `c9171c6`]: I mis-called the red 3-pt series "spurious" in this round — it is REAL (verify-vs-PDF, by the agent, corrected me): the zig-zag is ONE data series drawn as red-filled / black-edged SQUARE markers connected by a zig-zag line. Three compounding bugs mangled it: (1) `marks.py` fill-blob merge kept the red disc and dropped the square outline → `marker='o'`; (2) **`labels.py` legend-box recovery swept data markers in the "r ẑ" x-column into the legend and snapped onto the densest data row → dropped 7 of 10 markers (the visible 3-pt result)**; (3) `style.py` read the render shape from the red fill blob → disc. Fix (surgical, 3 files): coalesce reconciles only SHAPE (blob→outline) leaving fill/stroke for the cross-series merge; x-column legend recovery now requires vertical contiguity so scattered data markers aren't swept; style prefers the recognised marker over generic disc. New series[0]: red, 7 pts, `marker='s'`, full ±0.55 zig-zag (was bogus 3-pt line); series[1]/[2] untouched. Suite 1064→1066 (+2 regression tests: `test_marks::test_filled_blob_plus_square_outline_merges_to_square`, `test_legend_box::test_data_markers_in_label_column_not_swept_into_legend`). No-regress on 9 cids (incl 2110). Caveat: 3/10 markers (central peak + 2 spine-coincident) still dropped by pre-existing conservative guards (`_is_legend_swatch`/`_on_border`) — unchanged, protects other charts.

## Round 8b (2026-06-12) — full regen + axis cleanup + original-crop landed
Regen (re-extract 23 + render + audit + rsync) propagating style→JSON (af9191b), axis data/style cleanup (d5d018d), original-crop-in-bundle (ac487c0). **23/24 rendered**; axis style-bleed = **0** on all real charts (the lone flagged one was a stale bundle). Each bundle now ships `<cid>_original.pdf`/`.svg` + reconstruction + chart.json. Audit n=24 median **96%** mean 91% min 44%, **missed_curves total = 0**.
- **2308.10009_p16c8 — DROPPED from set (correct skip, NOT a regression)**: page 16 is a corner-plot grid; charts 1–6 hit the 2D credible-band/contour gate, 7–10 (incl. c8) → "no series extracted". Current parser correctly declines; old parser extracted junk. Removed from restyle_cids.txt (now 23) + stale bundle deleted.
- **Regen-hygiene note (latent)**: re-extraction writes a `.skip.json` but leaves the OLD non-skip `pageNcM.json` in place → when a chart newly skips, the renderer reads the stale json and `KeyError 'style'`. Fix later: re-extraction should clear the paper's out dir, or the renderer should prefer a `.skip.json` sibling. (Only bit 2308 here; surfaced as the 1 ERR.)

## Round 8 (2026-06-12) — style→JSON refactor landed; 1 recall regression found
Draw: 2307.07124_p3c1, 2508.02902_p7c3, 2003.03611_p8c3. (Bundles are PRE-refactor; style→JSON refactor `af9191b` is behavior-preserving so PNGs still representative.)
- **2307.07124_p3c1** [clean]: faithful — green CNT/WSe₂ PLE (E₂₂/Eₐ peaks) + grey WSe₂ PL both reconstructed, correct peaks/shapes, red overlay on data, labels present.
- **2508.02902_p7c3** [clean]: faithful (Round-6 false-alarm confirmed) — DLR-averaged/DRAG oscillatory log-decay, dips matched. Minor label-style: original y uses bare exponents `0,-5,-10,-15`; recon uses `10^0…10^-15` on log axis (same data, arguably cleaner). Not a bug.
- **2003.03611_p8c3** [~RETRACTED regression / region-overcapture stands]: my "0 series → recall regression" was a FALSE ALARM — a buggy probe filtered `parse_pdf`'s *return value* (manifest summary rows, which have no `series` key) instead of the written JSON. Correct re-probe: the paper extracts **21 charts** (e.g. page8_chart11 = 1 series / 878 pts). No regression; do NOT bisect. The region-overcapture observation stands as a visual flag (left crop bleeds into a lower subplot, leading y-tick digit clipped `05/03/02/00`=`10^5/10^3/10^2/10^0`) — verify against JSON before acting; candidate for `refine_region_overcapture` (point 2).
- **DATA/STYLE BLEED (real, pre-existing, all charts)**: `style.x_axis`/`y_axis` (from `_axis_style`) duplicates DATA into the style block — `data_range` + per-tick `pixel`/`value` — alongside genuine style (`tick_direction`, `tick_length`, label text, `scale`). Series-level separation is clean (style.series carries only color/marker/linewidth/linestyle/markersize/marker_shape/connect/render_as/label, positionally linked, zero coords). Fix per the data/style principle: style.axis keeps only render-how; renderer reads data_range + tick pixel/value from the DATA section (`d['x_axis']`, `d['xticks']`). Pairs with point 1 (both renderer-side) → one dedicated agent.

## Round 7 (2026-06-12) — NEW real bug: y-tick leaked into x-ticks (2507.19945)
- **2507.19945_p22c1** [D]: reconstruction squashes both noisy curves (ε=1e-4 blue, ε=1e-8 orange) into a thin sliver — x-axis distorted. Data is CORRECT (x_axis.data_range 0.0002–0.1997, series x-extent 0.0006–0.2, matching original t=0–0.2). Root cause: `xticks` wrongly contains `(50.0,'50')` — a Y-AXIS tick value (y runs 5–55) mis-assigned to the X-axis tick list → distorts x rendering. EXTRACTION (tick→axis assignment in `axes.py`); the render `_ticks_in_range` guard may also not be dropping it for the x-limit. NEXT: dedicated agent — prevent a y-axis tick value from being classified as an x-tick (and/or have the render reject the cross-axis outlier). Deferred to post-session-limit (resets 9:10pm).
  - **RESOLVED** (`axes.py`): the bottom-most y-tick label "5" straddled the bottom-left corner, fell into the x-label band, and merged with the x-origin label "0" → bogus "50". `_x_label_spans` now drops a span at/left of the left spine that aligns (within 2.5pt) with a detected y-tick mark — that span is a corner y-axis label, not an x-tick. `xticks` now `0,0.02..0.2` (no 50); reconstruction fills x∈0–0.2. Side benefit: recovers the previously-clobbered leftmost x-tick on several other charts (2001.01709 p5c1 "1565", 2001.00255 p21c1/c2 "0", p12c2 "0"). Suite 1064 passed; regression test `test_x_label_band_excludes_corner_ytick_label`.

## Round 6 (2026-06-12) — fresh-set low-explained charts are FALSE ALARMS (no data loss)
Checked the fresh-set's lowest explained%-charts against their reconstructions; all faithful, low% is decoration/geometry the audit undercounts (total candidate-missed-curves across all 24 = 0):
- **2503.10490_p8c2 (44%)**: 5 overlapping oscillating traces + legend + 2 dotted reference lines — all reconstructed; low% = overlapping-curve fragments + decoration.
- **2508.02902_p7c3 (70%)**: two log-scale oscillating decay curves (DLR-averaged / DRAG) with sharp dips to 1e-15 — both reconstructed with correct colors + legend; low% = complex oscillation geometry + gridlines.
- **2309.15777 / 2308.10009**: neighbour-subplot bleed (fixed in the audit, Round 5).
Conclusion: the fresh 24 extract well; explained% is depressed by decoration/complex geometry, not real flaws.
- **2108.13102_p83c1 (81%)**: DUAL-Y-AXIS chart (left Δ|Z_SUT|, right Δ∠Z_SUT). Data extracted correctly (4+4 points) but the single-axis reconstruction can't render the secondary axis → the right-axis (blue) series maps onto the left scale, and the line+marker connectors aren't drawn. Known limitation (theme #6, secondary axes out of scope), NOT data loss.
ALL fresh-set low-explained charts now accounted for: faithful false-alarms or deferred-feature (twin-axis) limitations; no data-loss bug remains on the set.

## Round 5 (2026-06-12) — residual-audit false alarm (NOT data loss) ✅ FIXED (audit gating)
- **2309.15777_p18c4** [audit artifact, NOT extraction]: the residual audit flagged 3 dark-red (0.545,0,0) "candidate missed curves" (87–90 verts, bboxes ~[53–309, 396–537]) and a low 77% explained. Investigated against the RAW PDF: `_p18c4` is the **(a) ρMZM bar subplot** (plot box y=[415–466]) — its real data is a green bar + an orange bar, **no dark-red curve at all**. The 3 dark-red paths (idx 231/372/373) are the **band-structure parabolas of subplot (b) below it**; they OVERLAP this region's loose `region_bbox` but lie mostly BELOW the calibrated plot box, so the extractor correctly clips them away (`lines._box_ok`) and emits the single in-box fragment. `lines._same_curve` did NOT over-merge: it returns False for all three pairs (distinct y-trajectories) — no dedup bug, no data loss. Root cause of the false alarm: the audit's candidate-missed filter used the loose region_bbox `_in_region` OVERLAP test, pulling in neighbouring-subplot ink.
  FIX (audit only, no extraction change): `residual_audit._frac_in_box` + a plot-box gate — a residual path is a "candidate missed curve" only when ≥50% of its vertices fall inside the calibrated plot box (x/y spine ranges), mirroring the extractor's own clip. 2309.15777_p18c4: 3→0 missed. Same phantom on **2308.10009_p16c8** (3 tall vertical lines from an adjacent panel spanning y[170–435] vs box y[344–382]): 3→0. No other chart's missed count changed. Suite 1046→1049 (3 new regression tests in `tests/test_residual_audit.py`).

## Round 4 (2026-06-12) — 1 new finding ✅ FIXED (2503 x10^n axis multiplier)
- ~~**2503.12775_p12c4**~~ FIXED [C, render]: calibration is correct (x 0–1, y 0–1.8×10⁻⁴, decay matches) but the y-axis NUMBER DISPLAY differs from the original. Reconstruction shows full/scientific tick values (`2e-05, 4e-05, … 0.00018`); the original factors out the scale and shows a `×10⁻⁴` axis multiplier header with small mantissa labels (`.2 .4 .6 .8`). Data unaffected — purely the multiplier-axis label STYLE.
  FIXED: `_plain_linear` now uses `_use_axis_multiplier` → ScalarFormatter (factored `×10ⁿ` header + mantissa ticks) for extreme-magnitude axes; verified 2503 shows `×10⁻⁴` header. STYLE-ONLY (no coordinate change).
- Other draws this round (2412, 2510) already verified/documented; no regression from the number-formatting fix.

## Round 3 (2026-06-12)
- **2301.10421_p5c2** — 5 transistor I-D curves reconstructed faithfully ✓ (only
  the flat 0 V black curve dropped; it lies on the x-axis). 100% explained.
- **2112.11900_p3c10** — noisy FFT trace extracted well (middle panel tracks it)
  but the **reconstruction squashes it flat**: y-axis renders 22–62 while the
  curve sits at the bottom → y-calibration/range bug (only ~2 y-ticks; mismatch
  between tick values and data). Same family as 2503 (x 0–14 vs 0–1, dropped
  y ×10⁻⁴). → **CALIBRATION cluster = next focused target** (`axes.py`/`calibrate.py`).
- **2410.00955_p10c1** — connector fix holding (88% explained, up from 77%); the
  faint render-side connect-through-scatter line remains (render-only).

## 2410.00955_p10c1 legend problem (user-flagged) — diagnosed
The reconstruction shows NO legend. Root cause: all series are `label=None`, so
`_replot` draws no legend. Two compounding extraction issues:
1. **Over-segmentation**: the 2 real series (ED = green ○, METTS = blue ◇) are
   split into 6 marker series with mixed/incorrect shapes (`*`,`D`,`o`,`*`,`D`),
   so there is no clean 2-series set to label.
2. **No legend→series label matching**: the legend text "ED"/"METTS" is detected
   but never attached to the marker series (labels stay None).
Connect-fix already removed the spurious connecting line here (88% explained).
FIX (dedicated): stabilise marker shape/colour grouping (one series per
shape+colour, fewer spurious splits) in `marks.py`, then match legend entries to
series by colour/shape in `labels.py` so labels propagate → legend renders.
Both are deep; not safe quick fixes. Needs a fixture + careful work.

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
  PRECISE ROOT CAUSE (diagnosed): per colour there is ONE long path = the real
  curve (172 verts, ~104px → `_is_long_curve` True) PLUS ~47 thin OPEN slivers
  (h<1px, w~6px, ~125 verts, aspect 5-9). The slivers pass `marks._is_data_mark`
  (some via `_shape_of` mis-reading a 125-vertex sliver as a circle/star → relaxed
  `known_closed` bounds; some via strict bounds), so the colour gets a fake MARKER
  series → (a) rendered as scattered stars AND (b) the colour enters
  `marker_colors`, so the REAL long curve is suppressed as a "connector" by
  `_is_connector`. Net: curve lost, slivers shown as stars.
  ATTEMPTS (all reverted): (1) `_stitch_fragments` in long-branch — slivers aren't
  in long_groups, no-op + regressed; (2) blanket marks `_OPEN_MARK_MAX_VERTS=16`
  guard — regressed 8 test_marks fixtures (valid open many-vertex glyphs exist);
  (3) require `p.closed` for relaxed `known_closed` bounds — SAFE (suite green) but
  ineffective (enough slivers pass STRICT bounds h≥1.5/aspect≤3). NEXT (dedicated
  session): when a colour already has a strong long curve, treat its small
  same-colour slivers as curve artefacts (don't form a marker series / don't add
  the colour to marker_colors) — so the long line survives. Needs a fixture +
  careful guard so true line+marker plots (sparse real markers on a line) keep
  their markers.

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

## Error bars (2026-06-12) — 2510.04789_p3c4 / p3c2

- **FIXED (extract): error-bar whiskers traced as a fake series.** On
  `2510.04789_p3c4` the vertical error-bar whiskers + horizontal caps (navy,
  same colour as the square markers) were collected by `lines.py` into a 38-pt
  marker-less polyline that zig-zagged through the data squares. New module
  `error_bars.py` (`detect_error_bars`, wired into `cli.parse_pdf` right after
  `detect_arrows`) flags short near-vertical strokes whose x coincides with a
  marker centroid (the whiskers) plus their short near-horizontal caps, drawn in
  the marker colour, and only when ≥2 such whiskers exist and markers are
  present. The caller drops those path indices before extraction. Precision-safe:
  identical series/point output on 4 unrelated PDFs (2001.00255/01038/01709/01928).
  Side benefit: sibling panel `p3c2` (dark-red squares) flipped skip→extracted —
  its whisker artifact had previously suppressed the whole series ("no series
  extracted"); now 8 clean squares are recovered.
- **Orange "fit" line — correctly dropped, NOT a flaw.** Path 29 is a perfectly
  STRAIGHT segment (max perpendicular deviation 0.00 px over a 154-px chord; the
  662 vertices are exactly collinear). It only *looks* curved at high zoom
  because of the thick stroke against the gridlines. `refiners.drop_spurious_lines`
  removes it as a straight reference/fit line (R²=1.0) — the intended behaviour.
  Left as-is (forcing it back would weaken the straight-line rule corpus-wide).
- **DEFERRED (legend swatch FP): 6-of-8 squares on p3c4.** Only 6 of the 8 navy
  squares survive `classify_marks`. The two lower-left squares (cx≈461/463) are
  rejected by `marks._is_legend_swatch`: the embedded annotation box (φ/δ/|X|²,
  contains letters, sits in the lower-left within `_LEGEND_BORDER_FRAC` of the
  plot edges) is mistaken for legend text, and a marker just to its left is read
  as a swatch. This is a legend-heuristic false positive, NOT an error-bar issue.
  Fixing it touches the corpus-wide legend/swatch logic (high regression risk for
  the 1033-test baseline), so it is deferred rather than patched with a risky
  guard. (p3c2 has the same annotation box but its squares sit clear of the text,
  so all 8 survive there.)
  REVISIT (2026-06-12): attempted to tighten `_is_legend_swatch` (require a tight
  swatch→label gap + label-row-leading geometry) to recover the 2 squares.
  REVERTED — NOT precision-safe. The annotation's leading math glyph (δ → "d",
  single char, gap ~8pt, begins its text row) is geometrically INDISTINGUISHABLE
  from a genuine single-letter legend label (e.g. 2001.01769_p17 "D"/"I" legends,
  gap ≤6pt, also row-leading): any gap/abutment threshold that drops the 2510 "d"
  also leaks real legend swatches (confirmed: 2001.01769 1→2 series, 7 fixture
  test_marks failures with `npts` +1 = leaked swatch). Separating them needs
  SWATCH-COLUMN-STACK awareness (a legend swatch belongs to a stacked key column;
  a data square belongs to a series spread across the plot) — a larger refactor
  than per-mark text matching. Left at 6/8.

## 2409.17350_p9c1 glyph-legend false positive (extract) — FIXED
`labels._detect_glyph_legend_box` fired on the VERTICAL DATA COLUMN of the
dispersion lattice (distinctly-coloured markers stacked at one x, ZERO label
text, ZERO label-character glyphs to the right) and returned a legend_bbox
sitting ON the data → 7 points eaten. Root cause: the detector accepted any
swatch column with ≥2 distinct colours as a legend without requiring LABEL
EVIDENCE. FIX: `_detect_glyph_legend_box` now requires either ≥1 alphabetic
TextSpan label or ≥1 label-character GLYPH path just to the right of the swatch
column (a real legend always labels its swatches; a bare colour column is data).
The 2410.00955 glyph-path legend (labels rendered as vector outlines → label
glyphs to the right) is still detected; the 2409 data column is now rejected at
the labels level (the `marks._looks_like_colormap_scatter` workaround stays as
redundant defense-in-depth). Corpus-wide spot-check (102 charts) unchanged;
suite green (1037→1039 with 2 new regression tests in test_labels.py).

---

## Round 11 — fresh 20-chart set (2001–2006 arxiv_semicond), 2026-06-13

QA pass over a brand-new 20-chart draw (disjoint from the prior curated set).

**Faithful (8):** 2001.02216 (5 series log-y), 2001.06601 (5 strain curves),
2003.07825 (hysteresis ×10⁻⁶), 2006.04478 (6 temp curves log-y), 2006.05363
(5 curves+grid), 2006.16872 (multi-peak spectra), 2006.04979 (PL peak),
2001.07556 (sigmoid; ref-lines dropped).

**Data-affecting bugs:**
- **Y-multiplier exponent sign/glyph lost** (×10⁻ⁿ → ×10⁺ⁿ): 2002.06092 (×10⁻²,
  data ×10⁴ too big), 2003.13245 (×10⁻⁵, data ×10¹⁰ too big). FIXED for the
  separate-minus-span case (join contiguous raised spans); the missing-minus-glyph
  case (2003.13245) now degrades to no-multiplier (sane magnitude) instead of a
  wrong positive exponent. Recovering an UNEXTRACTED minus glyph (drawn as a path)
  is still open.
- **Mis-bounded region / wrong-tick calibration:** 2006.08318 (y came out
  −1.10…−1.30; inset present). Multi-panel/inset family.
- **Dropped curve:** 2001.03975 (Nonlocal Re(ε), steep red solid) — residual panel
  now flags it (missed-curve:1).
- **Legend extracted as a data series:** 2001.11305 (legend "Experiment — Fit"
  glyphs became a black scatter row at y≈1.5); 2006.08318 (spurious "/T_C" series).

**Inset contamination (leaks into main plot):** 2001.04704 (scatter+vertical line),
2006.08318 (inset curve as series + garbled inset axis text), 2003.09804
("Pressure"/"(GPa)" text + vertical line).

**Style bugs:**
- **Marker-shape misclassification (recurring):** circle→star (2005.00241,
  2002.09528), triangle→square (2003.07592), open→filled (2002.05277).
- **Panel enumerator promoted to giant suptitle:** 2003.09804 ("(b)").
- **Spurious frame/diagonal lines:** 2003.00176 (two diagonals).
- **Dropped connecting line / reference guide-lines:** 2002.09528 (connect line),
  2002.05277 / 2005.00241 / 2001.07556 / 2003.07592 (ref/fit guide-lines).

**Also fixed this round:** residual PNG panel no longer draws a faded full-chart
"ghost" when residual≈0 (now reads "no unexplained ink (fully explained)"); with
residual it keeps a lighter backdrop + bolder leftover ink.

**Next tractable levers (by frequency):** (1) marker-shape classifier (≥4 charts);
(2) inset detection+isolation / mis-bounded multi-panel calibration (≥3 charts,
the big one); (3) legend-region masking from data extraction (2 charts).

---

## QA pass 2026-06-15 (fresh 20-chart set; restyle-judge loop)

Set: 20 NEW random charts (`restyle_cids_set3.txt` = prior list; new list in
`restyle_cids.txt`). Judged 3 (verified against chart.json / raw PDF, not the PNG).

- **2002.01952_p4c1** — Faithful main plot (5 temp curves 110–150 °C, y 0–0.20
  correct). Spurious black series (npts 34/99) are the **top-right INSET scatter**
  captured as data → inset contamination (out of scope, [[no-multipanel]]).
- **2006.04979_p31c1** — 3 spectra captured well; calibration correct. Gaps:
  (a) the **secondary top x-axis** "Photon energy / eV" is not reconstructed
  (dual-axis feature); (b) annotation `X⁻` lost its superscript minus (`X`).
- **2004.08077_p7c2 — DROPPED SERIES (high-value target).** Original has 3 curves
  (14.4 / 19.2 / 24.0 µW/cm²); only 2 extracted (magenta 19.2, teal 24.0). The
  **blue (0,0,1) = 14.4 series is dropped entirely.** Diagnostic: all THREE colors
  are present and assigned to the region symmetrically (blue/magenta/teal each = 7
  paths, ~3850 pts), so the drop happens DOWNSTREAM of region assignment (marks /
  series assembly / a refiner), not at detection. The asymmetry (why blue and not
  the other two identical-looking colors) is the open question — NOT force-fixed
  this pass (a wrong recovery would violate precision-over-recall). This is a
  concrete [[stage1-peeling-targets]] reproduction: re-extract `2004.08077` page 7
  chart 2 and assert 3 series.

**Improvement committed this loop:** legend-box style recovery (edge/fill/linewidth/
rounded), verified on 2005.09264_p27c1 + regression tests; shared folder regenerated.

**Next target:** trace the blue-series drop in 2004.08077_p7c2 (marks/refiners) —
why one of three symmetric colors is dropped.

### QA pass 2026-06-15 (cont.)

- **2002.01912_p12c1** — Fully explained, data exact (double-peak resonance).
  Only cosmetic: the 4 QA-PNG panel titles overlap (wide/short figure). Skip.
- **2006.03604_p4c1 — legend swatch on the RIGHT (FIXED).** 2-column legend
  `Np [—] Tc [—]` / `Ac [—] Pc [—]` draws the sample to the right of each label;
  the detector assumed left, dropping Np/Ac and mis-colouring Tc/Pc. Now the
  swatch side is chosen by which side pairs more labels (default left). All 4
  entries recovered with correct colours. Test: test_legend_swatch_side.py.

### QA pass 2026-06-15 (cont. 2)

- **2002.04278_p15c1** — Main spectrum captured; top-right INSET contamination
  (out of scope, [[no-multipanel]]).
- **2005.13306_p22c2** — DUAL Y-AXIS (left 0–1, right −1…1); both curves captured
  & fully explained but rendered on ONE axis, so the secondary-axis series may be
  mis-scaled. Known un-implemented feature (dual-axis), larger effort. Not forced.

Pass verdict: only out-of-scope / known-feature items -> logged clean, no commit.
Open larger targets seen this loop: dual-axis (2005.13306, 2006.04979 top axis),
inset isolation, and the 2001.07029 dense-page (~29k pts) parse cost (~200s).

### QA pass 2026-06-15 (cont. 3)

- **2002.00630_p26c2** — convergence curves; top-right DOS INSET contamination (out of scope).
- **2001.06104_p6c3** — main decay curve + FPI-transmission inset both drawn; inset (out of scope) + minor green tail spike.
- **2003.13245_p11c1** — FAITHFUL (3 temp series, markers + lines, data exact).
- **2005.11717_p17c2 — endpoint markers dropped (FIXED).** First/last points at
  x=±0.2 sit on the spine; `_is_data_mark` dropped on-border marks as ticks.
  Recognised 2-D marker glyphs at the axis extreme are now kept (ticks are 1-D,
  still rejected). Series now spans the full ±0.2. Test: test_marker_on_axis_extreme.py.

### QA pass 2026-06-15 (cont. 4) — 5-chart batch
- 2004.06765_p9c4, 2005.03851_p3c2 — FAITHFUL (both series captured, explained).
- 2003.13327_p3c3 — MULTI-PANEL crop (J_H panel bleeds into (d) panel); out of scope.
- 2001.11728_p5c2 — faithful points; minor: connecting lines/triangle shape understated (style).
Current 20-set swept: 4 fixes landed (legend-style, dropped-series, swatch-side,
axis-extreme markers); remainder faithful or out-of-scope. Rotating to a fresh 20.

### QA pass 2026-06-15 (set5) — detail fidelity: TEXT ORIENTATION
- **2006.14257_p10c1 — diagonal labels rendered horizontal (FIXED).** 5 curve
  labels ("r_tL = 20%"...) drawn diagonally (~24°) along each curve were re-drawn
  HORIZONTAL. Text rotation was never captured/rendered. Now recover the baseline
  angle from span `dir` (`_text_rotation`, PDF-y negated; near-horizontal->0,
  diagonal/vertical preserved) and apply `rotation` in the renderer.
  Tests: test_text_rotation_* in test_restyle_prototype.py.
  (User detail-fidelity targets: text orientation ✓; legend placement/size,
  color/linewidth/transparency = continuing focus.)

### QA pass 2026-06-15 (set5 cont.) — DETAIL: marker/line proportion in PNG
- **2002.02623_p25c2** — markers rendered as tiny dots vs original's large spheres.
  Cause: the magnified QA-PNG scales fonts by font_scale but NOT markersize /
  linewidths, so they look disproportionately small (deliverables at font_scale=1.0
  are correct). Fix: scale marker diameter + line/edge widths by font_scale in the
  PNG path. Also FLAGGED (per "don't dismiss"): the red linear FIT LINE is dropped
  by drop_spurious_lines — intentional, but the user may want fit lines kept; needs
  a decision.
- Backlog from "investigate minor diffs" (set5): 2003.07592 legend swatch-handles
  faint; 2004.06773 inline curve labels became a legend box; 2001.11728 connecting
  lines/triangle markers understated.

### QA pass 2026-06-15 (set5) — DETAIL: markersize under-recovery (FIXED)
- **2002.02623_p25c2** — circle markers 4.85pt recovered as 2.03pt (2.4x small).
  Cause: markersize = median of ALL small same-colour paths, which included stray
  2-point black segments (fit-line dashes / caps / ticks). Fix: measure marker
  size from glyph paths only (those with a detected `_marker_shape`). Now 4.86pt.
  Test: test_markersize_glyph.py. (This is the "don't dismiss minor diffs" win —
  the tiny-marker look was a real recovery bug, not just PNG scaling.)

### QA pass 2026-06-15 (set5 cont. 2) — backlog triage
- **2003.07592_p12c1** — investigated the "LA/TA legend missing / faint handles":
  the region spans TWO panels (right panel has "Free-standing NW", "1D ph [Eq.(26)]"
  on the same rows as LA/TA). The legend detector picked the right panel's "1D 3D"
  and the LA/TA series stayed unlabeled -> text fell through to annotations. Root
  cause = MULTI-PANEL contamination -> OUT OF SCOPE ([[no-multipanel]]), not a
  simple legend bug. (Lesson: triage legend/label diffs for multi-panel first.)

- **2002.05277_p21c2** — single panel (aspect 1.28); a spurious BLACK 74-pt scatter
  series sits in a compact top-left cluster (38%x29%) where the legend is = legend
  glyph-text captured as data (undetected legend -> text leaks in; cf. commit
  0018f44). TARGET: legend-region detection/exclusion here. Also all 9 series are
  render_as=scatter+connect (some are dense lines) -> possible scatter/line
  misclass. Deeper legend-recovery work; logged, not force-fixed.

NOTE (recurring): several set5 "legend/label" diffs trace to legend DETECTION
(missed legend -> unlabeled series + glyph text leaks). A focused legend-detection
hardening pass is the next high-value lever (distinct from per-chart fixes).

### QA pass 2026-06-15 (set5) — DETAIL: abutting swatch legend missed (FIXED)
- **2002.02623_p23c1** — clean single-panel swatch legend (colored lines + "298 K"
  ..."77 K") was NOT detected: each swatch ends right at the label's left edge
  (gap ~ -0.001), so the strict 0<=gap row test failed -> swatch-side flipped to
  "right" -> 0 entries, series unlabeled, labels fell through to black annotations.
  Fix: allow a small swatch/label overlap (_SWATCH_OVERLAP=2.5). Now all 4 series
  labeled; legend renders with colored handles. Test: test_legend_swatch_side.py.

### QA pass 2026-06-15 (set5) — DETAIL: discrete multi-colour series merged (FIXED)
- **2001.01769_p17c3** — 5 power-law series (each own SOLID colour, ~16 circles)
  were merged into ONE teal series (n_series=1) by _merge_colormap_scatter, which
  fired on "≥4 same-shape colour groups, wide hue span" without checking marks-per-
  group. A real colormap is point-per-colour (~1 mark/group); these are dense
  discrete series. Added a sparsity guard (median marks-per-group ≤ 2). Now 5
  series with correct colours. Test: test_colormap_merge_guard.py.

### QA pass 2026-06-15 (corpus) — DETAIL: triangle marker orientation (FIXED)
- **2504.02903_p11c3** — CdTe down-triangles (▽) rendered as up (△); _marker_shape
  returned '^' for ALL triangles. Now distinguish via centroid position (filled △
  centroid in lower half -> '^'; ▽ -> 'v'). CdTe now 'v', CZT stays '^'.
  Test: test_triangle_orientation_up_vs_down. (Same chart: GaAs circles ● mis-read
  as 'x' -- separate shape_of bug, logged.)
- Corpus pass also saw: 2209.00927_p8c1 faithful (6 colored curves); 2509.11037 fit/
  ref lines dropped (by-design, user decision pending); 2302.04967 red series split
  ^/o + connector dropped (deep marker-consistency, logged).

- **2504.02903_p11c3 GaAs ●->x (DEEP, logged):** the red GaAs glyphs near the
  markers are 2-point vertical segments classified shape_of='cross' (error-bar /
  stroke fragments), so the series picks 'x' not 'o'. Not a simple circle-misread;
  needs disentangling the circle glyph from red stroke fragments. Logged, not forced.

### QA pass 2026-06-15 (corpus) — DETAIL: open-marker edge colour (FIXED)
- **2505.19730_p6c2** — blue OPEN markers (white-fill blob + separate blue-edge
  outline, same positions) rendered WHITE/invisible: _coalesce_duplicate kept the
  white-fill group's colour. Now the kept white/open group adopts the duplicate's
  visible edge stroke -> series colour blue. Test: test_open_marker_edge_color.py.
- Corpus draw also: 2107.08282 faithful (curve+ZBD/ZBP segments; bottom highlight
  axvspan bands not reproduced = niche); 2111.09242 / 2211.04130 / 2512.00603
  faithful; 2210.14881 / 2506.22329 inset (out of scope).

### QA pass 2026-06-15 (corpus, 2 draws) — mostly faithful; sampler guard added
10 corpus charts judged: faithful (2003.07825 hysteresis loops, 2202.10860 scatter+
exp-fit, 2506.01464 / 2010.09299 / 2405.09792 / 2111.09242 / 2211.04130 / 2512.00603),
out-of-scope (2106.12703 inset, 2306.08643 cluttered multi-peak, 2404.01379 HISTOGRAM,
2309.12776 HEATMAP), nuanced (2203.00695 same-colour 2020/2020-open legend split).
No clean tractable parser fix -> parser is healthy on typical line/scatter.
ADDED sampler guard: reject heatmap/colormap lattices (>12 series & mean <6 pts/series)
so they stop slipping the valid-line/scatter filter (2309.12776). Logged target:
2203.00695 same-colour legend entries differing by linestyle.

### QA pass 2026-06-15 (corpus) — DETAIL: math-alphanumeric unicode labels (FIXED)
- **2202.11139_p39c1** (+ recurring): annotation '2D−𝑅$_{ℎ}$$^{𝑋}$' uses Mathematical
  Alphanumeric unicode (𝑅 U+1D445, 𝑋 U+1D44B, ℎ U+210E) that matplotlib can't render
  -> dummy boxes (the earlier 'U+1d43b' warnings). Extraction was CORRECT (sub/super
  captured); the renderer now NFKC-normalizes the U+1D400–1D7FF math blocks +
  letterlike chars to plain letters (render italic in $...$). Fixes V_DS, R_h^X, etc.
  across many physics charts. Test: test_demath_alnum_normalizes_math_unicode.
- Draw also: 2104.08998 / 2501.06936 / 2202.11139(data) faithful; 2008.09881 inset;
  2005.00735 LaTeX bra-ket mangle + spurious dots (deep).

### QA pass 2026-06-15 (corpus) — DETAIL: tight-stacked legend mis-pairing (FIXED)
- **2502.18732_p6c3** — 4 ADMR curves; legend rows stacked ~5.6pt apart (< _ROW_TOL
  6). Each label matched 3 swatch rows; pick=row[0] grabbed the row ABOVE, so
  H=30->black, H=60->red, H=70->green (every entry mis-coloured; H=10/H=70 series
  also lost labels). Fix: pair each label with the CLOSEST-cy swatch (marker
  tie-break). Now black->H=10, red->H=30, green->H=60, blue->H=70. Test:
  test_tight_rows_pair_with_closest_swatch.
- Draw also: (others pending view) — this was the tractable fix.

### QA pass 2026-06-15 (corpus) — DATA: π-axis tick calibration (FIXED)
- **2405.15494_p38c5** — a sinusoid on a 0..2π axis (ticks '0','π','2π') was
  miscalibrated to 0..2 -> points plotted over the wrong x-range (looked scattered).
  The π glyph (U+03C0) survives extraction but _is_numeric_span filtered it and
  _parse_plain couldn't read it, so 'π'->no tick and '2π' ('2'+'π' spans)->'2'.
  Added _parse_pi (π, 2π, π/2, 3π/2, 0.5π, -π) wired into _is_numeric_span +
  _parse_plain; _label_value already joins the spans. x now calibrates 0..2π.
  Test: test_pi_ticks.py.

### QA pass 2026-06-15 (corpus, 2 draws) — clean; new logged targets
10 corpus charts: faithful (2211.05763 ECT, 2108.00345 density-scatter, 2506.19640,
2512.10786, 2407.01134 [×10⁻³ y is CORRECT — misread cut crop], 2202.11139 data),
out-of-scope (2404.10293 DUAL-X-AXIS μrad+μm merged ticks/title; 2506.18139 complex
multi-series residual 14). No clean in-scope fix -> parser healthy on line/scatter.
NEW logged target: 2101.01714_p11c1 — CATEGORICAL scatter (markers styled by anion
COLOUR × polymer SHAPE, dual legends); most points dropped (residual 27) because each
(colour,shape) group has <_MIN_MARKS_PER_SERIES marks. Hard: the per-series model
doesn't fit per-point styling. Logged.

### QA pass 2026-06-15 (corpus) — DETAIL: numeric (year) legend labels (FIXED)
- **2203.00695_p24c1** — legend entries '2016'/'2020' (years) were dropped: the emit
  loop's `_is_numeric(label)` filter treated them as ticks, so only '2020 open'
  (non-numeric) survived. Now a numeric label WITH a swatch is admitted, but a LONE
  numeric entry is dropped post-hoc (a tick that picked up a swatch). Year/numeric
  legends with >=2 entries are recovered. Tests: test_multi_row_numeric_legend_admitted
  (+ existing test_pure_numeric_anchor_still_filtered still green).
- Draw also faithful: 2406.05032 log-scatter+fit, 2311.09347 (solid->dashed branch
  nuance), 2305.00719 curve family. Parser healthy on line/scatter.

### QA pass 2026-06-15 (corpus) — DETAIL: open marker flipped to filled (FIXED)
- **2503.07760_p4c1** — two OPEN-circle series (∥c black, ⊥c red); black rendered
  FILLED, red open. Both glyphs identical (fill=None rings). The face vote (_modal)
  considered only paths WITH a fill, so 2 incidental filled-black glyphs (legend
  sample) outvoted 20 open rings (fill=None excluded) -> black face=[0,0,0]. Now the
  face vote INCLUDES None (open) and uses glyph paths only, so the open majority
  wins -> face=None. Also scoped marker face/edge/width to glyph paths (like
  markersize). Test: test_open_marker_majority_stays_open.
- Draw also: 2207.03135 / 2107.11117(data) faithful; 2107.11117 legend ℓx/α are
  glyph-PATH math symbols (deep glyph-OCR); 2509.11041 phase-diagram (OOS).
