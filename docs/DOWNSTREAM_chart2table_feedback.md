# Downstream feedback from chart2table (the VLM consumer of your GT)

**Written 2026-07-23 by the chart2table session, as a handoff for the next pdf_chart2table_parser session.**
Purpose: tell you (1) what chart2table had to do to your `semiconductor_groundtruth_v1` before it was
trainable, (2) which of your extractor failure modes forced that cleanup (so you can fix at source), and
(3) where the consolidated dataset + our cleaning code now live. Goal: you open a session here and do
cleanup / improvements to the extractor so downstream cleaning becomes unnecessary.

Context: chart2table is at `/network/projects/sail/damien/github/chart2table`. It fine-tunes a Qwen3.5-4B
VLM on chart→CSV. Your `semiconductor_groundtruth_v1` is our "eps" dataset (one of 4 domains). We consume
`images/<id>.png` + `annotations/<id>.json`.

---

## 1. Where everything is now (durable, consolidated)

**`/network/projects/sail/chart2table/eps_dataset/`** — a self-contained bundle we just built:
- `raw/extract_out/` — your raw extraction json (115,634 files) — verified copy
- `raw/semiconductor_groundtruth_v1/{images,originals}/` — your recon renders (16,104) + originals (32,208)
- `eps_v6_{train,test}/` — our CLEANED dataset (3648 / 483) used for training/eval
- `eps_v6m_{train,test}/` — same + axis bounds (x_bounds/y_bounds/bounds_source) for metadata conditioning
- `scripts/` — **our filtering code** (`build_eps_v2_fixed.py` + `clean_eps_gt.py`, self-contained) — READ THIS
- `README.md` — layout + provenance

eps_v6 was produced by `build_eps_v2_fixed.py` reading `raw/extract_out` (GT) paired with your `images/`
(recon) — it does NOT re-render, it reuses your renders (GT↔image consistent by construction).

### Versioning (matters for you when you regenerate the extraction)
The bundle now has a versioning tool — `version_control.py` (+ `VERSIONING.md`) — that content-hashes the
**raw annotations** (`raw/extract_out/**/*.json`, written to `raw/VERSION.json`) and each **filtered**
dataset (`eps_v6*/VERSION.json`, which records the raw version + filter-code version it was built from).
Current raw version: `raw:7f18cf3d0c92` (56,821 json files).

**Why you should care:** when you drop a NEW extraction into `raw/extract_out` (a re-run of your extractor),
we run `python version_control.py stamp-raw` → the raw version changes → `check` flags every filtered
dataset **STALE → regenerate**. So there's a clean, mechanical signal tying "you changed the extraction" to
"chart2table must rebuild eps_v6." When you hand off a new extraction, just note it so we re-stamp + rebuild.
If you want to drop a new extraction here yourself, put it under `raw/extract_out` and run `stamp-raw`.

---

## 2. What we found + what we do downstream (and the upstream cause)

**Headline: the raw eps GT was not trainable as-is.** A model trained on it caught our other domains on fire
(isolated eps full-FT crashed real-chart accuracy 0.79→0.42) because it learned to imitate broken GT. The
raw eps metric (~0.26–0.35) was a **GT artifact, not a model failure** — our reconstructions were fine, the
labels were wrong. After downstream cleaning the eps score roughly doubled.

Our cleaning lives in `build_eps_v2_fixed.py` (rules R1–R11). Mapping each to YOUR failure mode:

| our downstream fix | your failure mode (many you already log in docs/TODO) | fix-at-source suggestion |
|---|---|---|
| **R11 black-dot drop** (`_is_black`, marker `'o'` pure-black): 1,814 charts had a black-'o' series; **509 removed** (dot was the ONLY series → whole chart junk) + 500 cleaned | legend/annotation "big black dot" glyphs extracted as a scatter data series | don't emit pure-black, tight-clustered `'o'` clusters that sit in the legend/annotation region |
| **drop tick-as-data series** | tick marks on the axis read as a fake point series (your [[resume-plan]] "#1 precision bug"; worst on marker-less lines) | your top lever — tick/grid suppression before mark grouping |
| **R7 scale-correction + `_scale_broken` drop** | merged / mis-OCR'd tick labels (`'250'+'280'→'250280'`) corrupt axis calibration (~8.5% of your axes) | your `tick_ocr.py` recovers some; the rest currently poison values — prefer DROP-axis over emit-bad-calibration |
| **downsample to ≤30 pts, round to 3 sig figs** | dense GT: median ~104 pts, up to ~19k pts/series, 16-decimal precision — a smooth curve becomes thousands of points no image model can match | for line charts, emit a sparse control-point set, not every path vertex |
| **R2/R6/R8 fuzzy series dedup + coincident-point collapse** | duplicate / ≥90%-overlapping series; doubled points | dedup at extraction |
| **R1/R5/R10 title & label sanitize; generic `series_N` fallback** | garbled subscript legends (`T₀=50,T_mult=1`→`T=50,T`), Symbol/PUA glyph junk, latexit base64 blobs leaked as labels, y-label repeated as every series name | your `font_recovery.py` / glyph recovery — for us, see note below (we mostly DON'T need names) |
| **(cannot fix downstream — we just drop/eat the error)** | non-x-monotone curves x-sorted → scrambled reconstruction; slanted fit/guide lines & dashed fits emitted as data; multi-panel/inset contamination (you abandoned this); marker-less line charts fabricating from ticks | all upstream-only — see ranked list below |

---

## 3. What most needs improvement (ranked by DOWNSTREAM impact)

1. **Tick-as-data phantom series + marker-less line recall.** The single biggest source of "empty or
   garbage GT" that forced us to delete charts. Marker-less line charts (very common — most loss curves)
   either fabricated data from ticks or were dropped. If you can extract polyline vertices cleanly for
   marker-less lines AND suppress ticks, our usable-chart yield jumps.
2. **Merged / mis-OCR'd tick labels (~8.5% of axes).** One bad tick → whole-axis calibration wrong → every
   value in the chart is off. This is a *value-correctness* bug (worse than a dropped chart). Bias toward
   dropping the axis over emitting a mis-calibrated one.
3. **Black-dot legend-glyph series (R11).** Concentrated, mechanical, high-yield to fix at source.
4. **Fit / guide / dashed reference lines extracted as data series.** Adds phantom series that look
   plausible → poisons training silently.
5. **Non-x-monotone / parametric / sideways (x=f(y)) curves.** Vertex order scrambled by x-sort → the
   reconstruction is noise. We can't detect these reliably downstream.
6. **Multi-panel / inset contamination.** You explicitly abandoned this (ill-defined) — noting it still
   costs us neighbor-panel ticks/insets, but understood if out of scope.

---

## 4. What you do NOT need to improve for us

- **Series / legend NAMES.** Our training *anonymizes* series (targets are `s1..sN` in a canonical order),
  and our eval metric is name-agnostic. So garbled subscript legends, missing legend text, y-label-as-name
  — **none of it affects us.** Don't spend extractor effort on legend-name fidelity for chart2table's sake.
  (Correct *number* of series and correct *values* are what matter.)
- **Style / colors / markers.** We drop style entirely. No need for style fidelity in the GT.

So for chart2table specifically, prioritize **(a) correct series COUNT** (no phantom tick/fit/dot series,
no dropped real line series) and **(b) correct VALUES** (axis calibration), over labels and style.

---

## 5. Suggested first move for the next session here

Read `/network/projects/sail/chart2table/eps_dataset/scripts/build_eps_v2_fixed.py` — it is the concrete,
per-rule list of everything we had to patch over. Every rule there is a candidate to eliminate at the
extractor. Cross-reference with your own `docs/TODO.md` (tick-as-data, marker-less recall, dual-axis, fit
lines) — they already overlap heavily; this doc just adds the downstream priority ordering and the
"names/style don't matter to us" simplification.
