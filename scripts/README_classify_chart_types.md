# classify_chart_types.py — chart-type signal pass

Geometric/vector signal pass over the `arxiv_semicond` corpus. This is the
**script** half of a script+judge pipeline: it scores every chart with cheap,
reliable vector signals; a human/lead then **visually judges** the surfaced
candidates. No LLM/API calls — pure PyMuPDF geometry + the existing parser
(`load_page` / `detect_regions`).

## Run

```bash
export UV_LINK_MODE=copy
uv run python scripts/classify_chart_types.py            # all CPUs, full corpus
uv run python scripts/classify_chart_types.py --limit 50 # debug (first 50 pages)
uv run python scripts/classify_chart_types.py --workers 16
```

- Output: `/network/projects/sail/chart2table/arxiv_semicond/chart_type_report.csv`
- **Incremental + resumable**: chart_ids already in the CSV are skipped, so a
  crashed/interrupted run just re-launches and continues.
- Parallel: `ProcessPool` over unique `(arxiv_id, page)` pages, all available
  CPUs (`os.sched_getaffinity`). Per-chart `try/except` → `status=err`; a bad
  chart never aborts the run. The benign `MuPDF error: ...` lines on stderr are
  PyMuPDF parse warnings and are handled gracefully.

## Columns

`chart_id, raster_image, dual_axis, multipanel, histogram_bar, violin,
cartoon_inset, dense_noise, out_of_scope_max, primary_type, status`

Each detector is a confidence in `[0,1]`. `out_of_scope_max` is the max over the
six out-of-scope types (dense_noise is a *quality* flag, not out-of-scope, so it
is excluded). `primary_type` is the arg-max (or `in_scope` if all < 0.3).

## Detectors

**RELIABLE (tuned for low false-positive rate):**

- `raster_image` — fraction of the chart region covered by embedded raster
  image bboxes (`get_image_info` ∩ region) / region area.
- `dual_axis` — numeric tick-label columns on BOTH sides (left&right or
  top&bottom) of the plot box. Tick labels live *outside* the plot box, so it
  scans **all** page texts in a margin band around the region (not
  `region.text_indices`, which only holds texts inside the box). Mirrored
  identical-scale labels on both sides are suppressed (decorative, not dual).

**CANDIDATE (deliberately OVER-FLAG — recall > precision; the judge filters):**

- `histogram_bar` — ≥4 filled axis-aligned rectangles that look like BARS, not
  scatter markers: marker-sized near-squares are rejected; rects must share a
  baseline AND be tall (h≥1.5·w) or contiguously tiled in x.
- `violin` — filled, non-rectangular, vertically-mirror-symmetric closed blobs
  in a column. Acknowledged unreliable; emits a best-effort signal.
- `cartoon_inset` — a localised cluster of filled COLOURED shapes that are
  bigger than plot markers and size-heterogeneous (an illustration), not the
  uniform tiny marker grid of a scatter plot (those are suppressed).
- `dense_noise` — index `n_points > 250` AND high normalised 2nd-difference
  variance on the longest stroked polyline in the region (jagged/noisy).
- `multipanel` — ≥3 panel-letter tags `(a)(b)(c)…` inside the region, OR the
  parser split the page into ≥4 sub-panels.

## Notes / known over-flag

The candidate detectors are expected to over-flag; the visual judge confirms.
`multipanel` in particular fires a soft 0.45 whenever a page has ≥4 detected
regions, and `cartoon_inset` still catches some colored-marker scatter plots.
That is by design (recall side). The reliable detectors (`raster_image`,
`dual_axis`) are the ones to trust when they fire.
