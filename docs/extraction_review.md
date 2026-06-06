# Extraction pipeline review — recovery-accuracy audit (2026-06-05)

Read-only audit of the vector→table recovery path. Focus per steer: **recovery
accuracy** (coordinates, calibration, series separation/completeness, marker/line
fidelity), NOT chart-type detection (handled downstream). Findings are grouped by
module; line numbers refer to the files as read in this session.

Skepticism note: I could not run the parser on the real feedback corpus in this
session, so impact estimates are reasoned from the code + the existing
docs/fixtures, not freshly measured. Items I am unsure about are flagged.

---

## TOP RECOVERY-ACCURACY LEVERS (ranked backlog)

| # | Lever | File / fn | One-line fix | Impact | Risk |
|---|---|---|---|---|---|
| 1 | **Dashed/dotted series split into phantom curves OR dropped** (B2–B5, the single biggest known cluster) | `lines.classify_lines` keying by `(color, dash_form)` + `_split_into_curves` | Stitch x-disjoint same-`(color,form)` fragments into ONE series instead of one-curve-per-x-cluster; only split when y-trajectories actually diverge | High (4 open fixtures + 4 feedback cases) | Med |
| 2 | **Solid lines dropped when same colour carries markers** (marker-color suppression) | `extract.py:145` + `lines.py:531` | Suppress the solid line only when its vertices actually coincide with the marker centroids; otherwise keep it | High (≥4 feedback misses: 01172, 01515, 04662, 01775) | Med |
| 3 | **Large-magnitude / non-`1eN` axis offsets mis-calibrated** (B1, exponent_ticks_large) | `axes._x_axis_multiplier` / no y-multiplier at all | Broaden offset detection to `\d+e\d+`, `×10^n`, SI words; ADD a y-axis multiplier (currently x-only) | High (calibration → every point wrong) | Low |
| 4 | **`_MIN_FIT_R2 = 0.97` rejects whole axes** (and thus charts) on slightly noisy real ticks | `calibrate.fit_calibration:217` | Lower floor and/or fall back to robust 2-tick fit before rejecting; log when an axis is dropped for R² | High (silent whole-chart loss) | Med |
| 5 | **Log-vs-linear decided by a 1e-3 R² tie on tick LABELS** → log axes read linear | `calibrate.fit_calibration:210-216` | When values span ≥1.5 decades and are all positive, prefer log unless linear is clearly better; use tick spacing, not just R² | High (4 feedback log-read-as-linear cases) | Med |
| 6 | **`_2d_map_skip_reason` + `legend_bbox` ignored by `extract.extract_pdf`** (two divergent entry points) | `extract.extract_region` vs `cli.parse_pdf` | Make `extract_pdf` honour `region.skip_reason` and pass `legend_bbox`, or delete `extract_pdf` | Med (correctness/consistency; the Judge-4 +6.2% gate is bypassed on that path) | Low |
| 7 | **Sparse-on-dense + 2D-map gates have brittle absolute thresholds** that can drop real sparse scatter on busy panels | `marks.is_sparse_on_dense`, `plot_region._is_uniform_density_map` | Make thresholds relative / add a "markers lie on a functional locus" recall escape hatch | Med (recall watch-item, already flagged in judge4) | Med |
| 8 | **`_MAX_ASPECT=3` + `_MIN_MARK_SIDE=1.5` drop thin/elongated markers** (h-line markers, thin error caps, small markers at low DPI) | `marks._is_data_mark` | Relax aspect for known marker shapes; key on shape classification not bbox aspect | Med (02617/02085/01457 missing-marker feedback) | Med |

---

## calibrate.py + axes.py — axis / tick recovery

### C1. (TOP 5) Log-vs-linear tie decided purely on labeled-tick R² — log read as linear
`fit_calibration` (calibrate.py:204-216). Linear is the default `best`; log only
wins if `r2_log > r2_lin + 1e-3`, or on an exact tie *and* `_log_minor_consistent`
passes. On a real log axis with only 2–3 labeled decade ticks, BOTH fits hit
R²≈1.0 (two/three points), the tie branch needs unlabeled minor ticks to exist
and land on log positions — which frequently fails on papers that suppress minor
ticks. Result: a genuine log axis is reported linear (exactly the feedback cases
astro 2606.00212, ml 2606.04212/04662). **Fix:** add a decade-span heuristic —
when all values >0 and span ≥~1.5 decades, prefer log unless linear is *clearly*
better (e.g. evenly-spaced-in-value ticks). Impact: high; risk: medium (could
flip a legitimately-linear positive axis — gate on decade span + monotone log
spacing). Confidence: high this is the root of the log-as-linear misses.

### C2. (TOP 3) Axis multiplier is x-only and matches only `1eN`
`_x_axis_multiplier` (axes.py:410-437) uses `_OFFSET_RE = ^1[eE][+-]?\d+$`. Two
gaps: (a) there is **no y-axis multiplier function at all** — a `×10^n` offset on
the y axis is never applied, so y values come out off by orders of magnitude;
(b) the regex only matches the literal `1eN` form. matplotlib also renders the
offset as `×10^6` (mantissa "10" + superscript), and the failing fixture
`exponent_ticks_large` uses suffixed ticks "20M/40M…" which go through
`_split_unit` per-tick (works) but a bare "1e8"-style corner offset with plain
"0,20,40…" ticks does not. **Fix:** add `_y_axis_multiplier`; broaden detection
to `^\d+(\.\d+)?[eE][+-]?\d+$` and the `10`+superscript offset glyph. Impact:
high (whole-axis scale wrong); risk: low.

### C3. (TOP 4) `_MIN_FIT_R2 = 0.97` rejects entire axes silently
calibrate.py:87,217. A real paper's tick labels can be slightly mis-extracted
(LaTeX baseline jitter, a borrowed label) yet still give a usable linear fit at
R²≈0.95. The current floor drops the axis → `calibration=None` → the whole chart
is skipped with "no axis calibration". This is a *silent recall sink*: precision
guard with no diagnostic. `_drop_outlier` only runs with ≥5 ticks and only
rescues to R²≥0.999. **Fix:** lower the floor (≈0.90) or, before rejecting,
attempt a robust 2-best-tick fit; emit a diagnostic when an axis is dropped for
R². Impact: high; risk: medium (lower floor admits some bad fits — pair with the
existing `_values_sane` magnitude/gap guards). Flag: I have not measured how many
real charts hit exactly this floor.

### C4. Minus-sign glyph detection is geometry-fragile
`_is_minus_glyph` (axes.py:71-83) requires `fill is not None, stroke is None,
1.5≤w≤9.0, h≤2.5, ≤8 points`. A minus drawn as a *stroked* short segment (some
toolchains) has `fill=None` and is missed → positive value where negative
expected (sign error in calibration, and in every mapped point). `_has_minus`
(axes.py:256-269) additionally requires the glyph's right edge within
`-3.5..7.5` of the digit left edge; tight kerning or a wide minus fails. **Fix:**
also accept a thin stroked horizontal segment as a minus; widen the gap a touch.
Impact: medium (sign errors are catastrophic for affected ticks); risk: low-med
(a stroked dash could be a tiny gridline — gate on proximity to a digit, which is
already required).

### C5. `_primary_column` can drop the real tick row on twin/secondary-axis charts
`_primary_column` (axes.py:318-344) keeps the *largest* cluster of label groups
by alignment coordinate, tie → nearest spine. On a chart where a secondary
(twin) axis or a neighbouring panel happens to have *more* labels in-band than
the primary, the larger cluster wins and the primary ticks are discarded →
mis-calibration. This is the documented `twinx_log_linear-r0c0-y` xfail
("right-axis ticks contaminate"). **Fix:** prefer the cluster whose alignment
coordinate is nearest the spine over the merely-largest one, or restrict the
y-band more tightly to the left spine. Impact: medium (twin-axis charts);
risk: medium.

### C6. Shared-axis borrow can overwrite a correct 2-point fit with a sibling's
`calibrate_panels.borrow` (calibrate.py:291-335). When the target has a 2-point
fit (always R²=1.0) and a sibling has ≥3 labeled ticks with a *different scale*,
the sibling supersedes. That is the intended log/linear rescue, but it also fires
when the 2-point target was actually correct and the sibling is a different
physical axis that merely shares a pixel range within `_RANGE_TOL=3.0`. Pixel-
range match is the only safeguard and 3pt is loose for tightly-packed panels.
**Fix:** require the sibling to also share the same tick *positions*, not just
the spine extent, before overriding. Impact: medium; risk: medium. Flag: unsure
how often this misfires in practice — needs a real-corpus check.

### C7. `to_data` for reversed axes is fine, but `data_range` ordering is not normalised
`_apply` (calibrate.py:245-248) sets `data_range = (to_data(px0), to_data(px1))`
in pixel order, so a reversed axis yields `data_range` high→low. Downstream
consumers (eval/judge) that assume `data_range[0] < data_range[1]` would compute
a negative span. The reversed-axis fixtures PASS, so the eval path tolerates it,
but any consumer normalising by `data_range[1]-data_range[0]` is at risk. **Fix:**
document or normalise. Impact: low (currently passing); risk: low. Flag: verify
no consumer divides by an unsorted range.

---

## lines.py — curve recovery (the biggest open cluster)

### L1. (TOP 1) Dashed/dotted series fragmented into phantom curves, or dropped
This is B2–B5 (synthetic) and four feedback cases (01515, 04662, 01775, plus the
"only dotted reported" 01515). Root cause is the candidate keying + splitting:

- `classify_lines` keys both long-paths and fragments by `(color, dash_form)`
  (lines.py:507-509). A single dashed series drawn as N short dash segments of
  the *same* colour/form goes into one `frag_groups` bucket — good — and
  `_merge_fragments` joins them (lines.py:409-424). BUT a dashed series drawn as
  a few *longer* dashes lands in `long_groups`; if those dashes x-overlap at all,
  `_merge_long` returns None and `_split_into_curves` (lines.py:385-406)
  greedily splits them into **separate curves by x-cluster**, emitting one
  `SeriesLine` per cluster → the "4 truth → 8 pred" / "3 → 5" phantom-series
  signature exactly.
- Conversely, `_MIN_FRAG_POINTS = 8` (lines.py:56) and `_is_noise_cloud`
  (lines.py:348-361) can reject a sparse dashed curve entirely → "dashed series
  dropped" (B2).

The split logic conflates "two genuinely different curves of the same colour"
with "one dashed curve whose dashes happen to overlap in x". **Fix direction
(matches the doc's stated direction):** before splitting, test whether the
x-clusters share a common y-trajectory (interpolate, compare like `_same_curve`);
only emit multiple series when y actually diverges. For dashes, treat all
same-`(color,form)` short segments as fragments of one curve (collect endpoints,
order by x) rather than as competing long curves. Impact: high; risk: medium
(must not merge two truly distinct same-colour curves — use the y-divergence
test as the discriminator).

### L2. (TOP 2) Marker-colour suppression drops solid data lines that share a series colour
`extract.py:145` builds `marker_colors` from every marker series, and
`classify_lines` (lines.py:531, 558) drops any **solid** line whose colour is in
that set, on the theory it is the connector through the markers. But a real chart
can legitimately have BOTH a marker series AND a separate solid line of the same
colour (e.g. data markers + a fit/guide line, or two solid lines one of which
also carries markers). The current rule unconditionally removes the solid line.
This matches feedback: ml 2606.01172 (solid blue not detected), materials
2606.01515 (solid red/blue dropped, only dotted kept), ml 2606.04662 (solid blue
not reported), astro 2606.01775 (green/orange/blue missing). **Fix:** suppress the
solid line only when its vertices actually coincide with the marker centroids
(it IS the connector); if the line departs from the markers, keep it. Impact:
high; risk: medium (over-keeping re-introduces connector duplication — gate on
geometric coincidence). Note the *opposite* feedback case (materials 2606.04147:
report only markers, not the guide line) means the discriminator must be
geometric coincidence, not a blanket keep-or-drop.

### L3. Plot-box clipping + `_MIN_KEPT_FRAC=0.5` can drop a real curve
`_box_ok` (lines.py:514-520) drops a curve if <50% of its vertices survive the
`_CLIP_FRAC=0.03` plot-box clip. The plot_box comes from `axis.pixel_range`
(spine-to-spine). If calibration put the spines slightly inside the data extent
(common when the outermost data point sits on the axis), a curve that legitimately
grazes/exceeds the box loses >50% of vertices and is dropped entirely. **Fix:**
clip vertices but keep the curve if the *retained* span still covers the plot
width; don't require 50% vertex retention. Impact: medium; risk: low-med.

### L4. `_is_spine_line` flat-and-near guard can drop legitimate flat data
`_is_spine_line` (lines.py:191-231). The "flat-and-near" branch rejects a curve
with y-extent <2% of plot height that hugs the top/bottom edge within 5%. A real
near-zero baseline series (e.g. an accuracy curve pinned at ~1.0 near the top, or
a loss curve at ~0 near the bottom) satisfies both conditions and is dropped.
The comment claims legitimate near-flat curves "are not near any edge", but a
saturated/floored metric IS near an edge. **Fix:** only apply when the stroke is
unsaturated (gray/black baseline) OR when there is no calibrated tick at that
edge value. Impact: medium; risk: medium. Flag: needs a real example to confirm
frequency.

### L5. `_is_data_lowsat` thresholds drop short black/gray data curves
lines.py:143-161: requires `≥8 vertices` AND `max side ≥0.4·min(region dims)`. A
short solid black data curve (a fit line over part of the range, a zoomed inset
trace) below either threshold is rejected as a "box/legend frame". Combined with
L2, black solid lines are doubly fragile. Impact: medium; risk: medium (lowering
re-admits gridlines — pair with the `_varies_2d` 1-D rejection which is the real
gridline discriminator).

### L6. `_split_into_curves` greedy assignment is order-dependent
lines.py:385-406: each path joins the *first* cluster with no x-overlap. For
three same-colour curves drawn as interleaved segments the greedy first-fit can
misassign segments to the wrong curve, producing curves that zig-zag between
series. There is no y-continuity check during assignment. **Fix:** assign by
nearest-y-continuation, not first-no-overlap. Impact: medium (only multi-curve
same-colour cases); risk: medium. Subsumed by L1's redesign.

---

## marks.py — marker recovery

### M1. (TOP 8) `_MAX_ASPECT=3` / `_MIN_MARK_SIDE=1.5` reject thin or small markers
`_is_data_mark` (marks.py:219-241). A horizontal-line marker ("_"), a thin
diamond, a small marker at low render resolution, or a marker partly clipped by
an axis can have min-side <1.5pt or aspect >3 and is silently dropped. Feedback
missing-marker cases (astro 2606.02617 blue markers, materials 2606.02085
light-green inverted-triangle + light-orange, ml 2606.01457 one marker on a blue
line) plausibly involve markers that fail these bbox gates. **Fix:** classify
shape first (via `_shape_of` vertex count) and relax the bbox aspect for
recognised marker shapes; only apply the strict aspect to *unclassified* paths.
Impact: medium; risk: medium (relaxing admits short segments — but `_shape_of`
already distinguishes cross/marker). Flag: I could not confirm these specific
markers fail here vs. fail the plot-box clip.

### M2. (TOP 7) `is_sparse_on_dense` absolute thresholds can drop real sparse scatter
marks.py:387-417 + extract.py:164. Thresholds `≤8 points`, `≥60 region paths`,
`ratio ≥12`, `≥2 dense paths` are all absolute. A legitimate sparse scatter (say
6 points) overlaid on a busy panel with gridlines/error bars/a fit curve
(≥60 paths, ≥2 dense) is skipped as "sparse markers on dense chart". Judge-4
itself flags this as a recall watch-item. **Fix:** add an escape hatch — if the
few markers lie on a clean functional locus (monotone-ish, low residual to a
line/curve fit) keep them. Impact: medium; risk: medium.

### M3. Hue-merge only merges SINGLE-mark groups → gradient scatter with ≥2 per hue over-splits
`_merge_hue_gradient_singles` (marks.py:272-319) merges only groups of exactly
one mark. A sequential-colourmap scatter where each hue bucket has 2–3 marks is
NOT merged, so it stays as many tiny groups, each ≥3? no — each below
`_MIN_MARKS_PER_SERIES=3` is dropped by `_is_real_series` (extract.py:64). Net:
a gradient scatter with 2 marks/hue is entirely lost (groups too small to keep,
not single enough to merge). **Fix:** extend the hue merge to small groups
(len < `_MIN_MARKS_PER_SERIES`), not just singletons. Impact: medium (gradient
scatter / colour-encoded third variable); risk: medium (could merge two real
close-hue series — gate on "no group has enough marks to stand alone").

### M4. Hue-merge collapses two legitimately different series of similar hue → over-merge
The mirror of M3: `_HUE_MERGE_DEG=20°` with same shape merges singles into the
nearest existing multi-mark group. Two distinct series in similar blues (e.g.
"navy" vs "royal blue", both circles) within 20° hue could be merged. Feedback
materials 2606.02858 reports the *over-split* direction (one orange-circle series
read as two), which is the duplicate-merge path (`_merge_duplicate_series`) not
firing — see M5. Impact: low-medium; risk: noted as a coupling concern.

### M5. `_merge_duplicate_series` requires EXACT 1-to-1 position match within 1.5pt
marks.py:244-258. A filled glyph + its stroke outline drawn with sub-pixel offset
>1.5pt, or different point counts (one path missing/extra), won't merge → the
same series appears twice (over-split), the likely cause of materials 2606.02858
("two series but only one marker type"). **Fix:** match on centroid-set overlap
fraction (e.g. ≥80% of marks pair within tol) rather than strict equality of
count + every mark. Impact: medium (over-split = wrong series count); risk: low.

### M6. `_is_legend_swatch` border-proximity guard can still eat data markers
marks.py:152-193. When `plot_box` is given, the swatch check requires the legend
text to be within `_LEGEND_BORDER_FRAC=0.20` of a plot edge. A legend drawn in
the *interior* (matplotlib `loc='center'`, or an inset legend) is >20% from every
edge, so its swatches are NOT filtered → swatch points leak in as data
(materials 2606.01373 "legend swatch points reported as data"). Conversely a data
marker that happens to sit left of an annotation near an edge gets dropped. The
0.20 constant is doing two opposing jobs. **Fix:** prefer the `legend_bbox`
exclusion (L from labels.py) as the primary mechanism and demote the heuristic
swatch test. Impact: medium; risk: medium. See X1 (legend_bbox not always wired).

---

## extract.py / cli.py — orchestration & coupling

### X1. (TOP 6) `extract.extract_pdf` is a divergent entry point: ignores skip_reason AND legend_bbox
`extract_pdf` (extract.py:179-197) calls `extract_region` without `legend_bbox`
and never inspects `region.skip_reason`. So through this path: (a) the Judge-4
Round-1 2D-map gate (the measured +6.2% strict win) is **bypassed** — density
maps/credible bands ARE extracted as junk; (b) legend mini-curve decorations are
NOT excluded from marks. The CLI path (`cli.parse_pdf`) does both correctly, and
all current eval/batch scripts use `parse_pdf`. So `extract_pdf` is effectively
dead but dangerous: any future caller (or a test) using it gets materially worse,
inconsistent results. **Fix:** either make `extract_pdf` honour `skip_reason`
(return a skip) and run `detect_labels`→pass `legend_bbox`, or delete
`extract_pdf` and route everything through `parse_pdf`. Impact: medium
(correctness/consistency, latent regression risk); risk: low.

### X2. `marker_colors` coupling between marks and lines is rounded-colour exact-match
extract.py:145 rounds marker fill/stroke to 2 decimals; lines.py compares against
that rounded set. A marker series and its connector line whose stroke differs by
>0.005 in any channel (anti-aliasing, alpha compositing) won't match → connector
duplicates the markers (over-count). The inverse of L2 — here the suppression
*fails to fire*. So the same colour-equality mechanism is both too aggressive
(L2, drops real lines) and too brittle (here, fails to dedupe). **Fix:** use a
tolerance-based colour match (like cli.py's `_COLOR_TOL=0.15`) AND the geometric
coincidence test from L2. Impact: medium; risk: low.

### X3. `_MIN_TOTAL_POINTS = 1` lets a 2-point "series" through as an extraction
extract.py:54,161. After dropping tiny marker groups (`_MIN_MARKS_PER_SERIES=3`),
a line series of just 2 vertices survives (`_MIN_VERTS=3` in lines.py is the
floor for a *long curve* but `_merge_*` can yield exactly 3, and clipping can cut
to 2). A 2-point "curve" is almost always an axis/connector fragment. Low impact
but a precision leak. **Fix:** align the floor with a meaningful minimum. Impact:
low; risk: low.

### X4. `_confidence` uses min axis R²; with the 0.97 floor it is always ≥0.97 → uninformative
extract.py:67-75. Because `fit_calibration` rejects anything below R²=0.97,
emitted charts always have confidence ∈ [0.97, 1.0]. The CLI ignores it anyway
(`confidence=1.0 if both calibrated`, cli.py:301). The confidence signal is dead.
**Fix:** either feed a real quality signal (series count, clip loss, tick count)
or drop the field. Impact: low; risk: low.

---

## plot_region.py — region detection / panel split

### P1. Panel-split relies on white axes-patch rects; pgfplots/stroked-frame multi-panel won't split
`_split_enclosing_frames` / `_split_multi_row_boxes` (plot_region.py:230-265,
562-630) both look for **white unstroked rectangle patches** as inner panels.
Toolchains that draw frames as stroked rects or merged spines with NO white patch
(pgfplots, TikZ) have no inner patches to find, so a 2-panel figure stays merged
→ two panels' data concatenated along one axis (feedback ml 2606.04777 "two
graphs reported as one"). **Fix:** add a spine-gap-based inner-panel detector
(reuse `_sub_edges` gap logic) to split when no white patches exist. Impact:
medium (panel merge corrupts both panels' coordinates); risk: medium.

### P2. Shared-axis grouping by row/col is purely geometric → wrong borrow on irregular grids
plot_region.py:1006-1012 sets `shares_x_with = same col`, `shares_y_with = same
row` from `_assign_grid`, which clusters by left-edge / top-edge within
`_ALIGN_TOL=3pt`. An irregular subplot layout (mosaic, differing panel widths)
mis-clusters, and `calibrate_panels.borrow` then borrows a calibration across
panels that don't actually share an axis → silently wrong calibration on the
borrowing panel. The borrow guard only checks pixel-range match (C6). **Fix:**
require both the grid relationship AND tick-position agreement before borrowing.
Impact: medium; risk: medium. Flag: severity depends on corpus layout variety.

### P3. 2D-map gates (`_is_uniform_density_map`, `_is_dense_fill_lattice`,
`_has_tall_fill_band`) use absolute, corpus-tuned thresholds
plot_region.py:139-162, 754-847. `_DENSITY_MIN_GRAY_FILLS=200`,
`_DISPERSION_DENSITY=0.15 fills/pt²`, `_BAND_HEIGHT_FRAC=0.85` were fit to a
handful of examples. `_has_tall_fill_band` fires on ANY single fill-only polygon
≥85% panel height with non-white colour — a legitimate full-height shaded
confidence band (very common in ML/astro) is geometrically identical and would be
skipped, dropping a real chart. Judge-4's own note (Lever 2) found
filled-twin/closed-contour gates fire on legitimate spectra/bands. **Fix:** for
the tall-band gate, additionally require the *absence* of an open data line
inside (like the density gate already does via `_has_open_data_line`). Impact:
medium (recall); risk: medium. This is per-steer NOT a detection concern but a
*recovery* concern because it silently discards extractable line charts.

### P4. `_has_open_data_line` requires ≥100 points and x-monotone — misses sparse/dashed real lines
plot_region.py:717-751, `_DENSITY_LINE_MIN_PTS=100`. The escape hatch that
*saves* a line+marker chart from the density-map gate needs a ≥100-vertex,
near-monotone, ≥0.5-width line. A sparse line (20 markers + thin connector) or a
dashed line (broken into fragments, none ≥100 pts) does NOT satisfy it, so a real
sparse scatter sitting on a slightly-gray-heavy panel can be gated out. **Fix:**
also treat "many same-colour markers forming a functional locus" as the
escape signal. Impact: medium; risk: medium. Couples with M2.

### P5. `_num_tick_labels` counts "10" as a tick → log axes with only "10^n" labels can over/under-count
plot_region.py:434-436 treats bare "10" as numeric (precedes a log exponent).
But the count is raw span occurrences, so a log axis showing 10², 10³, 10⁴ counts
each "10" mantissa → fine for passing the gate, but a chart whose only x-labels
are two "10"s from one 10^n group could pass `_MIN_NUM_TICKS=2` with a single
real tick. Minor precision concern. Impact: low; risk: low.

---

## Dead code / inconsistencies (from rapid multi-agent edits)

- **D1.** `extract.extract_pdf` — divergent/likely-dead entry point (see X1).
  No script imports it; all use `cli.parse_pdf`.
- **D2.** `_confidence` (extract.py) is effectively constant ≥0.97 and unused by
  the CLI output (see X4).
- **D3.** `labels._detect_x_title` / `_detect_y_title` are computed in
  `detect_labels` but the CLI **deliberately ignores them** (cli.py:285,
  "we do NOT fall back to labels.py's x/y title") in favour of `axes._x_title`/
  `_y_title`. So two title implementations coexist; `labels`'s are dead in
  production. Mention, do not delete (per instructions).
- **D4.** `_group_labels` / `_align_coord` / `_title_from_rows` take an `axis`
  param that is unused in some branches (`_group_labels` ignores `axis`
  entirely). Cosmetic.
- **D5.** `marks._merge_hue_gradient_singles` returns `others + new_groups`,
  silently dropping the *ordering* relative to original single positions; series
  order is then non-deterministic w.r.t. input for the merged singles. The
  module docstring promises "order by first appearance" — mild inconsistency.
- **D6.** `_BREAK_MARKERS` / `_has_break_marker` (calibrate.py) only uncalibrate
  the broken axis but the chart is still emitted with the *other* axis — a
  broken-axis chart yields a half-calibrated, then skipped, result. The synthetic
  broken-axis fixtures are multi-panel/no-self-test, so this is untested in
  practice. Flag for verification.

---

## Cross-cutting recommendation

The dominant recovery losses cluster on **two coupled mechanisms**:
1. the `(color, dash_form)` line-candidate model + greedy x-split (L1, L6), and
2. the colour-equality coupling between marks and lines (L2, X2, M5).

Both would benefit from a single change: replace exact/rounded colour-equality +
x-overlap heuristics with **geometric coincidence tests** (do these vertices/
centroids actually lie on the same locus?). That one discriminator resolves
"is this solid line the marker connector?" (L2), "are these two same-colour
x-clusters one curve or two?" (L1), and "are these two marker groups the same
series drawn twice?" (M5) — the three highest-impact open issues.
