# QA findings — random reconstruction spot-checks

A QA loop samples 3 random reconstructions (`scripts/qa_sample.py`) and logs any
extraction/reconstruction problems here. Newest round on top. Each item: what's
wrong → likely cause → owner. Fixed items get struck through with the commit.

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
