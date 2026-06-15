---
name: restyle-judge
description: Use to run one pass of the reconstruction QA / judge→feedback→fix loop on the restyle prototype. Samples a couple of rendered bundles, visually judges reconstruction vs original (verifying against the JSON/raw PDF, not just the thumbnail), records findings, fixes the most tractable precision-safe issue with a regression test, regenerates the shared folder, and commits.
---

# Reconstruction judge loop

The judge is **you** (vision): compare each reconstruction against its original
panel and decide what the extractor/renderer got wrong. This is the iteration
engine for improving fidelity. Run it as a **loop** — one pass per invocation,
then regenerate the shared folder and repeat.

Optimize **precision over recall**: a wrong number is worse than a skipped chart.
Never change pure data (coordinates) to make a picture look nicer. Style only
describes HOW to show data; it never overrides data. (See the project memory:
priority-recovery-over-detection, data-style-separation, verify-extraction-vs-render.)

## One pass

1. **Sample** a couple of bundles (fresh random each call):
   ```bash
   export UV_LINK_MODE=copy
   uv run python scripts/qa_sample.py --n 2   # prints the 3-panel PNG paths
   ```
   The shared-folder copy is `~/shared_folder/semiconductor_restyle_prototype/<cid>/`;
   the working copy is `<root>/restyle_prototype/<cid>/`. The PNG is
   `original | reconstruction | residual`.

2. **Judge each.** Read the PNG, then CONFIRM every suspected defect against the
   ground truth before acting — read `<cid>/chart.json` and/or inspect the raw
   vector crop (`scripts/inspect_pdf.py`, `<cid>/<cid>_original.pdf`). The side-by-
   side thumbnail mis-leads (crop misreads, decoration-depressed residual, marker
   mis-extraction). Decide for each flag: render bug, extraction bug, or faithful.

3. **Record** findings in `docs/qa_findings.md` (append; one short block per chart:
   cid, verdict, defect, render-vs-extraction, suggested fix). If a draw only hits
   faithful charts, log "clean" and stop the pass without committing.

4. **Fix** the single most tractable precision-safe issue. Every identified bug
   gets a regression test that reproduces it (parser bug → `tests/`; renderer bug →
   `tests/test_restyle_prototype.py`). Deep/structural bugs: note them, don't force
   a fragile fix. Out of scope (do NOT fix): multi-panel / inset / caption
   contamination (ill-defined — see memory no-multipanel).

5. **Verify** the whole suite on the compute node:
   ```bash
   export UV_LINK_MODE=copy PDFCHART_OCR=0
   uv run pytest -q          # must stay green
   ```

6. **Regenerate the shared folder** after the improvement (REQUIRED each pass),
   using the MONITORED launcher (detached + bounded poll, never hangs; ~5 min,
   extraction-dominated):
   ```bash
   bash scripts/run_regen.sh "iterN: <one-line label>"
   ```
   It launches `_regen_restyle.sh` (re-extract 32-way w/ per-paper progress →
   render parallel → audit → metrics → rsync), polls `scripts/jobstat.sh` until
   `DONE` / `STALLED` / `CRASHED`, and prints the final state + metrics table.
   Do NOT use the harness `run_in_background` (it has been throwing internal
   errors); the detached `nohup` + `jobstat` poll is the reliable path. To check a
   running regen at any time: `bash scripts/jobstat.sh
   /network/projects/sail/chart2table/arxiv_semicond/regen.log <pid>`
   (exit 0 done / 2 running / 3 crashed / 4 stalled).

7. **Show performance evolution.** The regen records one aggregate row per pass via
   `scripts/loop_metrics.py` (mean explained%, total residual/missed, charts fully
   explained). Each loop, DISPLAY the table — always iteration 1 plus the last 10 —
   so the trend is visible:
   ```bash
   uv run python scripts/loop_metrics.py --show
   ```

8. **Commit** the fix + test (the loop commits each verified improvement). Push
   only when asked.

## Notes
- Heavy work (extract / render / regen) runs on a **compute node** only, never a
  login node. The regen is multithreaded, sized by `os.sched_getaffinity(0)`.
- The 20 charts come from `<root>/restyle_cids.txt`. To judge a FRESH set, pick new
  random cids from `filter_verdicts.csv` (keep / line_scatter), excluding the prior
  `restyle_cids*.txt` lists, write them to `restyle_cids.txt` (back up the old one),
  then regenerate.
- To run this on a cadence, drive it with `/loop` (self-paced): one pass per wake.
