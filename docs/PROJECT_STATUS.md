# pdf_chart2table — project status (locked-in)

Automatic, **fully offline/deterministic** extraction of data from **vector**
line/scatter charts in PDFs → per-chart tables, with the original chart preserved
as a lossless vector crop. No LLM/agent at runtime (agents were build-time only).

## How to run
```bash
export UV_LINK_MODE=copy
uv run pdf-chart2table parse INPUT.pdf [--pages A-B] -o OUTDIR    # one PDF
uv run pdf-chart2table batch "GLOB/*.pdf" -o OUTDIR               # many PDFs
```
Per extracted chart, in `OUTDIR/<pdf_stem>/`: `pageN_chartK.json` (axes, ticks,
calibration, series with data+pixel coords, title, caption, legend labels),
`*.markers.csv` (long form), lossless vector crop `*.pdf`/`*.svg`, and a top-level
`manifest.csv`. Charts that can't be reliably extracted are **skipped** with a
reason (precision over recall) — `*.skip.json`.

## Pipeline (src/pdf_chart2table/)
`pdf_vector` (PyMuPDF paths+text+image rects) → `plot_region` (detect region(s),
split subplots, dedup, **gates**: chart-vs-not, bar/histogram, heatmap, raster-
image) → `axes`+`calibrate` (ticks, labels incl. decimals/units/log/glyph-minus,
linear/log auto, sanity rejection, shared-axis borrowing) → `marks`+`lines`
(markers + solid/dashed/dotted/black curves, clipped to the calibrated plot box,
baseline/connector/tiny-n rejection) → `labels` (title, axis titles, region-
confined legend, caption) → `extract` (orchestrate, skip rules) → `io_store`
(JSON/CSV/vector-crop/manifest) + `cli`.

## Quality (judge = multimodal agent over rendered reconstructions)
- **Strict precision 68% / lenient 85%** of EXTRACTED charts (Judge 3, 60-chart
  sample of real arXiv materials+astro). Trajectory: 53% → 65% → 68% across
  iterations. Detection on real papers ~P1.0 (locked earlier).
- Skips are conservative: skip audits found **no garbage admitted**; the precision
  shortfall is entirely within the extracted set (tightening gates trades recall
  for precision cleanly).
- Reports: `docs/judge2_report.md`, `docs/judge3_report.md` (+ verdicts CSVs).

## Datasets produced (in `$SCRATCH/pdf_chart2table/`)
| Corpus | dir | extracted | skipped |
|---|---|---|---|
| General ML (arXiv) | `out/` (pdfs/, 191) | 720 | 748 |
| Materials/semiconductor | `materials_out/` (199) | 219 | 369 |
| Astrophysics | `astro_out/` (223) | 1132 | 1258 |
| Feature-selection reports | `fs_out/` (3) | 202 | 3 |
| **Total** | | **2273** | **2378** |
Corpora fetched via `scripts/fetch_arxiv.py` (uses arxiv.org **listing pages**,
not the rate-limited API; `--cats`, `--months`). NOTE: `$SCRATCH` is cleaned
after 90 days — copy out anything to keep long-term.

## Example bundles (in `$HOME/shared_folder/`)
`pdf_chart2table_examples/` (general ML), `semiconductor_examples/`,
`astro_examples/`, `feature_selection_extracted/` (the 3 reports, 64 graphs,
many-line plots excluded). Layout: **one folder per paper** containing `paper.pdf`
(once), `metadata.json` (paper + per-graph metadata), and **one subfolder per
graph** (`chart.json`, `chart.markers.csv`, `chart_crop.pdf`/`.svg`,
`reconstruction.png`). Generator: `scripts/build_examples_from_batch.py`
(`--all`, `--max-line-series N`); renderer in `scripts/make_examples.py`.

## Tests
`uv run pytest -q` → **452 passed, 15 skipped** (synthetic fixtures + real-PDF
detection + per-module units). Synthetic fixtures: `tests/fixtures/` (18, via
`scripts/gen_fixtures.py`). Skills: `.claude/skills/{pdf-vector-inspect,
eval-extraction,chart-fixtures}`.

## Known limitations / next levers (to resume the loop)
Ranked by Judge 3 impact on the remaining ~32% strict gap:
1. **Non-chart 2D maps** still slip the gate: contour / 2D-density / Bayesian-
   posterior / M–R credible-band / band-structure dispersion → add a chart-type
   gate for these (biggest lever).
2. **Slanted guide/fit lines** extracted as series → reject diagonal reference
   lines.
3. **Sparse-on-dense** spurious series; strengthen tiny-n.
4. Miscalibration on cramped/twin axes; panel-merge on heavy overlap.
5. Deferred: **M8 `--llm` assist tier** for the hard tail; full arXiv `fetch`
   subcommand. Marker-less recall (dashed/date axes) is intentionally low.

Status: **locked in** at 68% strict / 85% lenient with conservative skipping;
uncommitted working tree (commit on request).
