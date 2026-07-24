# Upstream response notes (for the next chart2table handoff)

Working notes accumulated while fixing the extractor at source (see
`docs/DOWNSTREAM_chart2table_feedback.md` for their ask). **No new dataset version has been
released yet** — per user policy, everything stays in this repo until inspected and approved.

## Schema additions (they must update their filter on the next drop)

- `series[].role` — `"data" | "fit" | "uncertain"`. Their `_MARKERLESS` drop should become:
  keep `marker=None` series with `role="data"` (recovers most of their 7,647 `nomarker` charts
  + 6,549 `markerless_line` series), drop `role="fit"`, policy call on `"uncertain"`.
- `series[].dashes` — raw PDF dash string or `"dashed"`; `None` = solid. The dash is a
  fit/guide signal.
- Ticks whose value the calibration could not reproduce are now serialized with `value: null`
  (honest unlabeled minor ticks) instead of a poisoned labeled value.

## scale_broken audit (their 524 chart-level drops) — 2026-07-24

Pure-JSON audit of the raw extraction for every scale_broken reject id
(`eps_v6_rejects.csv`); per-id tags in the session scratch `scale_broken_audit.csv`.

| class | count | whose bug | resolution |
|---|---:|---|---|
| genuine **log axes** with >1e4 dynamic range | ~234 | **their filter** | their `_scale_broken` ratio test has NO log exemption — it fires on any wide-range log-axis chart. Add `scale == "log"` exemption → ~234 valid charts recovered. |
| genuine **linear axes crossing near zero** (arith ticks, values inside tick range, min positive ≈ 0) | ~232 | **their filter** | `max(pos)/min(pos) > 1e4` explodes as values approach 0 on a perfectly calibrated linear axis. Replace ratio test with a values-within-tick-range test → ~232 valid charts recovered. |
| calibration inconsistent with own stored ticks | ~36 | **ours — FIXED** | two sources, both now gated: (1) `fit_calibration` excluded an outlier tick from the fit but the tick stayed serialized labeled → `_apply` self-check now strips such tick values or drops the axis; (2) shared-axis borrow overrode a panel's own fit with a same-pixel-column sibling's contradicting calibration → borrow now refuses siblings failing `_tick_misses` against the target's own labels. Verified on 6 sampled reject pages: all now emit tick-consistent calibrations. |
| log-as-linear tick pattern / off-tick-range | ~8 | mixed | tiny residue; the multiplier + tick-coherence work (fix 5) covers the remainder. |

**Net:** ~89% of their scale_broken drops are recoverable downstream with two small filter
changes; the ~7% that were our fault are fixed at source.

## black_dot_chart audit (their 1,167 chart drops) — 2026-07-24

Geometry triage of all 1,167 (session scratch `blackdot_audit.csv`) + 28-pair stratified visual
sample (originals vs recons; sheets judged in-session):

| class (of sample) | share | root cause | status |
|---|---|---|---|
| vector-outline TEXT read as marks | ~50% | TeX outline text (no font entry): round letterforms pass `_is_data_mark` and become a black-'o' series; angular letters rejected earlier were invisible to the run detector | **mechanism fixed** (commit c1a48e4): phantom-aware `_text_run_indices` + chained block extension; probe chart 10→5 marks. Residual: math-annotation rows ("ε₂ = ε₁"), some panel tags, and some panels' blocks still leak — follow-up below |
| legend-handle dots / annotation "•" dots / arrows | ~25% | legend bbox miss or dot outside box | open — follow-up |
| genuine black-marker data | **~18% (5/28)** | none — black filled circles are a normal marker | **downstream over-drops these**: R11 should not blanket-reject; suggest gating on our (new) suppression + a tight-cluster test instead |
| inset / colorbar contamination | ~7% | multipanel class | out of scope (their note acknowledges) |

**Follow-up (next session):** extend suppression to the residual letterform rows (math
annotation rows fragment because subscript glyphs sit off-baseline), audit remaining panels of
2512.13518, then the legend-handle/annotation-dot class; where confidence is low, FLAG
(`suspect`) instead of dropping per user policy.
