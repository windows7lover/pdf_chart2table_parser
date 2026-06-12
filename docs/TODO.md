# pdf_chart2table — TODO / what's left to do

_Snapshot: locked in at **68% strict / 85% lenient** extraction precision on real
arXiv charts (detection ~P1.0). Suite 452 passed, 15 skipped. Committed at
`69fbfe8`. Full context: `docs/PROJECT_STATUS.md`._

## Resume in 30 seconds
```bash
cd /network/projects/sail/damien/github/pdf_chart2table_parser
export UV_LINK_MODE=copy
uv run pytest -q                                   # 452 passed
uv run pdf-chart2table batch "DIR/*.pdf" -o OUT    # extract
uv run python scripts/build_examples_from_batch.py --batch OUT --out EXDIR --all   # per-paper bundles
```
Quality is graded by an LLM-as-judge **agent** over rendered reconstructions
(`scripts/judge_sample3.py` + view the PNGs); reports in `docs/judge*_report.md`.

## Precision backlog (ranked by judge impact on the ~32% strict gap)
1. **Chart-type gate for 2D/contour maps** — biggest lever. Reject contour /
   2D-density / Bayesian-posterior / M–R credible-band / band-structure
   dispersion plots (they still slip through and emit junk). Add to
   `plot_region._is_chart_type`.
2. **Reject slanted guide/fit lines** — diagonal reference/fit lines get
   extracted as a series (`lines.py`); only `_is_spine_line` (axis-parallel) is
   caught today.
3. **Strengthen tiny-n / sparse-on-dense** — a few stray points on a dense
   background still pass; tighten in `marks.py`/`extract.py`.
4. **Twin-axis & panel-merge calibration** — heavy region overlap still leaks
   secondary-axis ticks / merges panels (`plot_region.py` region split, `axes.py`).
5. **Legend: deeply-subscripted math** — labels like `T₀ = 50, T_mult = 1`
   truncate to `T = 50, T` (`labels.py`; row-tolerance trade-off).
6. **Marker-less recall** (optional) — pure dashed/smooth or date-axis line
   charts are skipped by design; only pursue if recall matters more than the
   clean corpus.

## Residual method — step 2 via specialized refiners (not by re-running lines.py)
`scripts/residual_audit.py` is step 1: it subtracts everything we understood and
reports the unexplained residual (a long unexplained polyline = candidate dropped
curve; a cluster = missed series). Step 2 is "re-analyse the residual and update
the JSON extraction" — but it should **NOT** be a blanket re-run / gate-loosening
of `lines.py`/`marks.py`. That was tried and reverted: it was a no-op on the
flagged charts (2007, 2011 unchanged) while raising duplication/noise risk on the
clean ones.

Instead, route residual recovery into **dedicated post-extraction refiner passes**,
one per failure cause, each operating only on the residual paths and gated to NOT
duplicate already-extracted geometry (honours the "complete *without duplicating*"
constraint):
- **`refine_region_overcapture`** — 2007: region grabbed an adjacent inset panel,
  so part of the residual is a *different* chart. Split/trim the region, then the
  residual recalibrates correctly. (Owner of the real fix: `plot_region.py`.)
- **`refine_dropped_curve`** — promote a residual polyline to a series *only if*
  (a) its point set doesn't overlap an existing series (dedup guard) AND (b) it
  calibrates against the recovered axes. Targets: 2007, 2110, 2011 candidates.
- **`refine_decoration`** — confirm residual that is legend swatches / arrows /
  frame is correctly *excluded* (not promoted). 2011's residual is mostly this.
- **`refine_spurious_line`** (NEW) — use the residual / cross-check to drop LINE
  series (`marker is None`) that should not be there:
  * a **fit / guide line** drawn through scatter data (2205.10303 dashed power-law
    fit captured as a 48-pt series; 2510.04789 orange straight fit, 662 pts + a
    38-pt dashed line) — typically dashed, smooth/monotonic, and passing near the
    marker series; NOT data.
  * a **spurious connector** through points that are really independent scatter
    (2410.00955 is pure scatter — ED ○ / METTS ◇ — yet a 42-pt `marker=None`
    series is emitted and the recon draws a zig-zag line through it).
  Heuristic (precision-first, dedup-safe): drop a line series when its vertices
  lie (mostly) on an existing MARKER series' points (it just connects them) OR it
  is dashed + smooth + lies near markers (a fit). Guard against killing genuine
  line+marker data series (where the original really drew the connecting line).
  Owner: a refiner pass + possibly `marks.py`/`lines.py` line-vs-scatter typing.

Each refiner: takes `(record, residual_paths, region, axes)`, returns an updated
record; lives outside `lines.py` (e.g. `src/pdf_chart2table/refiners/`); ships with
a regression test that reproduces the specific chart's residual (per the
bug→test rule). The audit's `explained%` is the loop's success metric.

## Connection order of line vertices (not always left-to-right)
A line series is currently emitted **x-sorted** (`lines.py::_dedupe_points` sorts by
x; `_merge_long` joins x-monotone; the recon `_replot` also re-sorts by x). That is
wrong whenever the curve is not single-valued in x:
- **sideways plots** — independent variable on the Y axis, so the curve is `x=f(y)`
  and must be ordered by y (2212.10848_p16c2: pDOS-vs-Freq, x≈0 with y spanning →
  x-sort scrambles it; recon is noise).
- **folded / parametric curves** — multivalued in x (2212.05730_p6c2: an S-shaped
  curve that folds back; x-sort destroys the fold).
**Why x-sort is load-bearing:** `_is_noise_cloud`, `_curve_at_x` (interp), and
`_merge_long` all assume x-monotone vertices to detect scatter clouds / dedupe
overlapping redraws. So we can't simply stop sorting — a folded curve in polyline
order would be falsely rejected as a noise cloud.
**Plan (additive, low risk):** keep `SeriesLine.points` x-sorted for ALL internal
analysis, but ALSO retain the source path's original vertex order (`raw_points`,
the PyMuPDF draw order = true connection order) for SINGLE-path lines; emit data
points in `raw_points` order when present, else fall back to x-sorted (merged
multi-path). Then drop the x-sort in `_replot`. Re-extract + re-render the two 2212
charts to verify; full suite must stay green (eval matches points order-independent,
but any exact-order unit test may need updating). A later refinement could *detect*
the independent axis (sideways → order by y) for the merged-fallback case.

## Deferred features (seams exist)
- **M8 `--llm` assist tier**: escalate low-confidence charts to a Claude agent
  for interpretation (coords still from `calibrate`). `llm_assist.py` is a stub.
- **Full arXiv `fetch` subcommand**: wrap `scripts/fetch_arxiv.py` into the CLI.

## Dataset housekeeping
- Datasets live in `$SCRATCH/pdf_chart2table/{out,materials_out,astro_out,fs_out}/`
  (2273 extracted charts total). **$SCRATCH is auto-cleaned after 90 days** →
  copy anything to keep into `/network/projects/sail/...` or elsewhere.
- Example bundles: `$HOME/shared_folder/chart2table_examples/` (per-paper layout;
  see its `pdf_chart2table_SUMMARY.md`).
- Corpora PDFs in `$SCRATCH/pdf_chart2table/{pdfs,materials_pdfs,astro_pdfs}/`;
  re-fetch with `scripts/fetch_arxiv.py --cats ... --months N`.

## Open decisions (need your call)
- **Push to a remote?** No git remote configured yet; history is local
  (`first commit`, then `69fbfe8`).
- **feature_selection "many line plot" threshold**: currently excludes charts
  with ≥3 line series (`build_examples_from_batch.py --max-line-series 2`) —
  loosen/tighten if wanted.
- **Bar/box/pie/contour**: intentionally NOT extracted (out of scope: line/
  scatter only). Say if you want any of these supported.

## How to push precision (workflow)
Pick the top backlog item → make a surgical change in the owning module →
`uv run pytest -q` stays green → re-batch a corpus → re-run the judge agent →
compare precision/failure-histogram. Repeat. (Each past iteration moved ~3–12 pts;
strict ≥95% on arbitrary real PDFs is likely unrealistic — tightening gates
trades recall for precision cleanly.)
