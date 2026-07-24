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
- `series[].suspect` — bool (policy: flag, don't guess). True when a small marker group
  (≤6 points) survived every confident annotation-glyph test but is packed letter-tight or
  hugs annotation text — it MAY be an annotation/legend glyph cluster. The data is kept;
  gate R11 on this instead of blanket-dropping black-'o' series.
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

**Follow-up DONE (2026-07-24, commit 49cef9c + letter-aspect gate):** math-operator rows,
stacked wordlet blocks, span head bands, and the `suspect` flag are in. 40-sample re-parse:
14 -> 21 of 40 fully cleaned (`docs/blackdot_reparse_after.csv`); several of the remaining 19
are genuine data (e.g. 2012.10841 9-pt curve), 2 now flagged suspect. Precision guard: the
letter-aspect gate (a run with only ROUND members is markers/dots, never text) protects
converging marker tails interleaved with dotted-line dots — the 5 verified genuine
black-marker charts re-extract at 247/35/21/26/14 pts, none flagged suspect.

**Last-resort option (user policy):** for a section recovery can't confidently decode, do NOT
decode — keep the raw EPS/vector description of that section and reinject it verbatim into the
reconstruction, paired with a JSON flag (`raw_passthrough_region` + bbox). Recovery first;
passthrough+flag only where recovery would have to guess.

## Impact quantification — 2026-07-24 corpus measurements

Corpus: 56,821 raw files = **19,603 extracted charts** + ~37k skips
(15,534 dispersion-lattice, 11,551 no-series, 8,530 no-axis-calibration, ~1,600 2D-map rejects).

**Already-committed fixes, effect at next regen:**
| fix | affected |
|---|---:|
| role tags (fix 1) — downstream can keep markerless data curves | **7,647 charts + 6,549 series** |
| calibration self-check + borrow gate (fix 3) — incoherent axes repaired/dropped | **1,483 charts (7.6%)** |
| letterform suppression (fix 2, partial) — 40-sample re-parse: 35% of black-dot charts fully cleaned, 30% shrunk | ~**635 / 1,814** black-'o' charts cleaned |
| downstream-side filter fixes (their two-line changes) | ~466 scale_broken FPs + ~330 genuine black-marker charts |

**Remaining fixes, measured impact:**
| fix | affected | note |
|---|---:|---|
| fix 2 residual (letterform leaks + legend-handle/annotation dots) | ~**850 charts** | 65% of the 1,814 black-'o' universe still emits black-o after the partial fix; minus ~330 genuine-data charts |
| fix 4 markers-on-axis | ~**113 charts** (1.0% of 11,295 marker charts have a marker point on a spine) + unquantified share of the 11,551 no-series skips ('\|' bar-marker charts) | LOW priority; circles/closed glyphs on spines already extract correctly (probe-verified) |
| fix 5 multiplier metadata + spacing check | metadata: all charts with offset-text axes (not measurable from JSON); coherence flag: largely redundant with committed fix 3 (the 1,483) | cheap to add; mostly downstream-conditioning value |
