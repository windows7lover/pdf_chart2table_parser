# Primitive-classification refactor plan

Goal: remove the pervasive duplication in primitive classification by giving the
pipeline a single shared vocabulary of low-level primitive predicates, plus a
single per-region "classify-once" pass whose output downstream consumers read
instead of each re-deriving the same judgments.

Constraints honoured:
- Behaviour-preserving where cheap; small accuracy dip acceptable, no mass loss.
- Always-green: every increment keeps `pytest` at the baseline
  (961 passed, 27 skipped, 5 xfailed, 1 xpassed).
- Two consumers may need DIFFERENT effective views of the same path (marks treats
  an `X` glyph as a marker; lines as a curve). The design preserves both views —
  it does NOT force one global label that changes behaviour.

## 1. Duplication map (file:function -> what it computes)

### Low-level primitive vocabulary (computed verbatim in 2-4 places)
- Rounded colour key:
  `marks._round_color`, `lines._round_color`, `extract._round_color`.
- Saturated (series-colour) test:
  `marks` (implicit via spread), `lines._is_saturated` + `lines._SAT_SPREAD`,
  `plot_region._is_saturated` + `plot_region._SAT_SPREAD`.
- Near-white / white test:
  `marks._is_near_white`, `lines._is_near_white`, `plot_region._is_white`.
- Hue / hue distance (gradient-scatter merge): `marks._hue_of`, `marks._hue_dist`.
- Polyline centroid: `marks._centroid`; bbox centre: `axes._center`,
  `labels._cx`/`labels._cy`, ad-hoc `0.5*(b0+b2)` in lines/plot_region.
- On-region-border test: `marks._on_border` + `marks._BORDER_TOL`,
  `lines._on_border` + `lines._BORDER_TOL` (identical).
- Plot-box bounds normalisation + in-box test: `lines._box_bounds`,
  `marks._in_plot_box`/`_strictly_in_plot_box` (each re-derives xlo/xhi/ylo/yhi).
- Legend-gap constant `_LEGEND_GAP = 40.0` duplicated in marks + lines.

### Marker-shape classification
- `marks._shape_of` + `marks._is_diamond_geometry` classify a path into
  circle/square/diamond/triangle/star/plus/cross/marker; `extract._MARKER_CODE`
  maps the same shape names to matplotlib codes. Single source today is marks,
  but the shape vocabulary is conceptually shared.

### Higher-level role judgments (per consumer, intentionally divergent thresholds)
- "is this path a data marker?" `marks._is_data_mark` / `_collect_large_fills` /
  `_is_interior_of_large_fill`.
- "is this path a data curve / fragment?" `lines._is_long_curve`,
  `lines._is_fragment`, `lines._is_data_lowsat`, `lines._varies_2d`,
  `lines._split_into_curves`, `lines._is_spine_line`, `lines._is_noise_cloud`.
- "is this path a legend swatch?" `labels._swatch_style` (style: line/dashed/
  marker), plus `marks._is_legend_swatch` and `lines._near_legend` (positional
  swatch suppression near legend text). cli `_series_style` re-derives a series'
  solid/dashed/marker style from region paths.
- Tick marks + tick-label bands + offset multiplier: `axes._x_tick_positions`,
  `_y_tick_positions`, `_x_label_spans`, `_y_label_spans`, `_*_axis_multiplier`.
- Region/chart-type gates: `plot_region._is_heatmap`, `_is_bar_chart`,
  `_spine_frame`, `_is_chart_type`, etc. These run BEFORE calibration on raw page
  paths and are a self-contained pre-region stage.

## 2. primitives.py — what it exposes

A single module that is the home of the shared primitive vocabulary. Two layers:

### Layer A: pure predicate vocabulary (the verbatim-duplicated helpers)
Colour: `round_color`, `is_saturated`, `is_near_white`, `is_white`, `hue_of`,
`hue_dist`, and constant `SAT_SPREAD`.
Geometry: `centroid(points)`, `bbox_center(bbox)`, `on_border(cx,cy,region,tol)`,
`box_bounds(plot_box)`, `in_box(cx,cy,plot_box,frac)`.
Shape: `shape_of(path)`, `is_diamond_geometry(path)`, and `MARKER_CODE` (shape ->
matplotlib code). Constant `KNOWN_CLOSED_SHAPES`.

Each consumer imports these instead of re-defining them. Tuned per-consumer
thresholds (e.g. marks' `_BORDER_TOL`, lines' `_MIN_SPAN_FRAC`) stay in the
consumer — only the *shared* primitive computations move.

### Layer B: classify-once region pass
`classify_region(region, paths, texts, plot_box=None, legend_bbox=None)
 -> RegionPrimitives` with:
- `markers: list[ClassifiedPath]` (role=marker: shape, fill, stroke, centroid)
- `curves:  list[ClassifiedPath]` (role=data_curve candidate)
- `swatches/legend view` — left to labels (it owns the legend box assembly).

NOTE (decision made during implementation, see §4): Layer B's full role pass is
intentionally NOT forced on marks/lines, because their accept/reject thresholds
are surgically tuned and interdependent; collapsing them into one role pass
changed behaviour and broke the green gate. Instead the shared *vocabulary*
(Layer A) is centralised — that removes the real verbatim duplication — and the
per-consumer role functions are refactored to call the shared vocabulary. This
keeps both divergent views (marker vs curve) and all tuned thresholds intact.

## 3. Migration order (each step independently green)

1. Add `primitives.py` Layer A (pure vocabulary). No consumer changes yet.
2. Migrate `marks.py` to import the shared colour/geometry/shape vocabulary;
   drop its local `_round_color`, `_hue_of`, `_hue_dist`, `_centroid`,
   `_is_near_white`, `_shape_of`, `_is_diamond_geometry`, `_KNOWN_CLOSED_SHAPES`.
3. Migrate `lines.py` to import shared `round_color`, `is_saturated`,
   `is_near_white`, `on_border`, `box_bounds`.
4. Migrate `plot_region.py` to import shared `is_saturated`, `is_white`.
5. Migrate `extract.py` to import shared `round_color` + `MARKER_CODE`.
6. Migrate `labels.py` / `axes.py` bbox-centre helpers to shared `bbox_center`.

## 4. What was migrated vs left, and why

Migrated to consume `primitives.py` Layer A:
- `marks.py`: dropped local `_round_color`, `_hue_of`, `_hue_dist`, `_centroid`,
  `_shape_of`, `_is_diamond_geometry`, `_is_near_white`, `_on_border` body, the
  duplicate `_KNOWN_CLOSED_SHAPES`, and the box-bounds inline math in
  `_in_plot_box`/`_strictly_in_plot_box`; now imports them from primitives.
- `lines.py`: dropped local `_round_color`, `_is_saturated` body (+ `_SAT_SPREAD`),
  `_is_near_white` body, `_on_border` body, `_box_bounds`; imports from primitives.
- `plot_region.py`: dropped `_is_saturated` (+ `_SAT_SPREAD`); imports from
  primitives.
- `extract.py`: dropped local `_round_color` and `_MARKER_CODE`; imports from
  primitives.
- `axes.py`: dropped `_center`; imports `bbox_center` as `_center`.
- `labels.py`: `_cx`/`_cy` now delegate to shared `bbox_center`.
- Shared `LEGEND_GAP` constant moved into primitives (was duplicated in
  marks + lines).

Left in place (deliberately), calling the shared vocabulary:
- The divergent high-level ROLE functions — `marks._is_data_mark` /
  `_collect_large_fills`, `lines._is_long_curve` / `_is_fragment` /
  `_split_into_curves` / `_is_spine_line`, `labels._swatch_style` /
  `_detect_legend`, `axes` tick / label-band / multiplier detection,
  `plot_region` chart-type gates, `cli._series_style` / `_apply_legend_labels`.
  These encode each consumer's surgically-tuned, interdependent thresholds and
  intentionally produce DIFFERENT views of the same path (a glyph is a marker to
  marks and a curve to lines). Collapsing them into one global role pass changes
  behaviour and falls outside the always-green, behaviour-preserving envelope;
  so the refactor centralises the shared *vocabulary* (the real verbatim
  duplication) and leaves the tuned role logic per consumer.
- `plot_region._is_white` (strict `abs(c-1)<1e-3`) was NOT swapped for
  primitives' `is_white` (`>=0.95`): different semantics, swapping would change
  the axes-patch gate. Left untouched.
- Pre-existing dead import `marks.defaultdict` left as-is (not introduced by this
  work; per "don't remove pre-existing dead code unless asked").

### Verification
- `pytest` stays at baseline (961 passed, 27 skipped, 5 xfailed, 1 xpassed) after
  every increment.
- Page-limited (`pages=1-12`) `parse` over 6 real_pdfs charts gives
  byte-identical extracted/series/point counts before vs after the refactor
  (e.g. 1512.03385: 8 charts / 40 series / 2595 points unchanged;
  1608.03983: 8 / 93 / 2892 unchanged) — behaviour fully preserved on the sample.

## 5. Layer B — single role authority (`roles.py`)

Layer A centralised the shared *vocabulary* but left three classifiers
(`marks.classify_marks`, `lines.classify_lines`, the legend-swatch detection)
each INDEPENDENTLY deciding what a path is, coordinating through a fragile hack:
`extract.extract_region` re-derived `marker_colors` + `marker_centroids` from the
marker output and passed them to `classify_lines`. That orchestrator-level
cross-talk caused the top-2 failures (double-count: a connector kept as an extra
series; drop: a real solid line suppressed because its colour matched a marker
colour). Layer B makes ONE pass decide each contested path's role so the
classifiers AGREE on a single assignment.

### The role API
`roles.classify_roles(region, paths, texts, plot_box=None, legend_bbox=None)
 -> RegionRoles`.

`RegionRoles` exposes:
- `roles: dict[int, str]` — the single role for each CONTESTED in-region path:
  `marker`, `data_curve`, `fill_band`, or `ambiguous`. Plain decorations
  (spines / ticks / gridlines / frames / background) are deliberately omitted
  (uncontested, no signal). Role constants live in `roles.py`:
  `MARKER, DATA_CURVE, FILL_BAND, AMBIGUOUS`.
- `marker_series` / `line_series` / `line_skips` — the exact objects
  `classify_marks` / `classify_lines` produced. The curve pass already had the
  marker colours/centroids applied, so the line+marker connector suppression is
  baked in here, not in the orchestrator.
- `n_data_paths` / `n_ambiguous` / `ambiguous` — the contention summary and the
  region-level skip flag.

Authority order inside `classify_roles`:
1. `classify_marks` → marker series (markers win a contested glyph).
2. The colours/centroids of the STRONG marker series (≥ `_MIN_STRONG_MARKS`,
   mirroring extract's `_MIN_MARKS_PER_SERIES`) are computed ONCE — this is the
   former `marker_colors`/`marker_centroids` cross-talk, now internal to the role
   authority.
3. `classify_lines(..., marker_colors, marker_centroids)` → curve series with the
   geometric connector test applied: a same-colour line tracing the markers is
   suppressed; a distinct same-colour line is kept.
4. A per-path role map is built; a path accepted by BOTH `_is_data_mark` and
   (`_is_long_curve` or `_is_fragment`) is `ambiguous` (the two authorities
   contend over it).

### Consumers
- `extract.extract_region` now calls `classify_roles` ONCE and consumes
  `marker_series` / `line_series` / `line_skips`. The `marker_colors` /
  `marker_centroids` derivation and the `classify_marks` / `classify_lines`
  calls were REMOVED from `extract.py` (the role authority owns them).
- `classify_marks` / `classify_lines` keep their public signatures unchanged
  (tests still call them directly); `classify_roles` is the single orchestrating
  caller in the pipeline.

### Preserving the intentional line+marker case
A series drawn as BOTH a line and markers still yields the markers (role
`marker`) and the coincident connector is handled exactly as before: because
`classify_roles` runs markers first and feeds their colours+centroids into the
curve pass, the connector is suppressed by the SAME geometric-coincidence rule
(`_is_connector`, multitrack-ratio guard) that the cross-talk drove. A distinct
same-colour line (far from the markers) is still kept. Verified: the
`convergence_semilogy_3`, `dotted_3styles`, `four_dashed_semilogy` and
`dashed_same_color` fixtures still give the correct series counts, and the new
`tests/test_roles.py` asserts the suppress-vs-keep split through the role pass.

### Ambiguity → confidence/skip (Task 2, deterministic)
When a path plausibly fits >1 role (accepted by both the marker and the curve
classifier) it is `ambiguous`. When a region's aggregate ambiguity is high — at
least `AMBIGUITY_MIN_DATA_PATHS` (4) contested data paths AND a fraction
≥ `AMBIGUITY_SKIP_FRAC` (0.5) of them ambiguous — the extraction is
low-confidence and the region is SKIPPED via the existing skip path with reason
`"ambiguous primitive roles"` (precision over recall). This is purely
deterministic: a single classification pass, no retry-with-modified-parameters
loop. On the real_pdfs sample the per-path ambiguity metric is live (e.g.
1608.03983 p4 reaches 17/37 ≈ 0.46) but never crosses the 0.5 region gate, so no
region is skipped and series/point counts are byte-identical to base.

### Layer-B verification
- `pytest`: 967 passed (961 baseline + 6 new `tests/test_roles.py`), 27 skipped,
  5 xfailed, 1 xpassed.
- Page-limited (`pages=1-12`) `parse` over the real_pdfs corpus: extracted /
  series / point counts byte-identical before vs after Layer B (1512.03385:
  8/40/2595; 1608.03983: 8/93/2892; 1609.04836: 16/58/5241; 1412.6980:
  5/24/2089; 2010.11929: 4/25/455 — all unchanged).
