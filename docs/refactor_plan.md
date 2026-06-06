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
