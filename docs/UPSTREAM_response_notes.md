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
