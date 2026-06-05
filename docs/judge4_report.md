# Judge 4: agent-orchestrated precision loop (frozen-set A/B)

Method: the Judge-3 60-chart sample is frozen as a regression set
(`docs/judge3_verdicts.csv`). Each round applies ONE lever, re-extracts only the
frozen charts' pages (`scripts/rebatch_frozen.py`, page-limited), re-renders the
same 60 identities (`scripts/judge_frozen.py`), and diffs vs the prior state. Pure
skips of non-correct charts are deterministic wins (no judge needed); content
changes are graded by a fresh Opus judge. KEEP if strict precision ≥ prior AND no
**correct** chart is lost; else REVERT.

Baseline (Judge 3): 41 correct / 10 partial / 9 wrong = 60 extracted →
**68.3% strict, 85.0% lenient.**

## Round 1 — chart-type gate for 2D/contour/credible-region maps (`plot_region.py`)
Coder added `_2d_map_skip_reason` (3 sub-gates: uniform grayscale density cloud /
dense fill lattice / tall credible-band) wired via `Region.skip_reason`; +5 tests.
pytest in main: **457 passed, 15 skipped**.

Frozen-set diff (5 charts flipped extracted→skipped, 0 correct lost):

| chart | frozen verdict | skip reason |
|---|---|---|
| astro_2606.02593 p3_c1 | wrong | uniform grayscale fill cloud |
| astro_2606.02593 p3_c2 | wrong | uniform grayscale fill cloud |
| astro_2606.03707 p18_c1 | wrong | tall colored fill spans panel height |
| materials_2606.01744 p81_c1 | wrong | dispersion lattice: fill density too high |
| astro_2606.00209 p2_c2 | partial | uniform grayscale fill cloud (dense gray cloud; only 1/4 datasets were recovered — defensible skip) |

Result: 41 correct / 9 partial / 5 wrong = **55 extracted**.
**Strict 68.3% → 74.5% (+6.2). Lenient 85.0% → 90.9% (+5.9).** Removed 4 of 9 wrongs
+ 1 weak partial; no correct lost. **KEPT.**

Note: the 00209 case shows Gate A treats dense gray scatter clouds as density maps —
acceptable here (poor extraction anyway) but a recall watch-item on cleaner scatter.

## Round 2 — sparse-on-dense guard (`marks.is_sparse_on_dense`, wired in `extract.py`)
Coder added `is_sparse_on_dense(region, paths, n_points)`: skip a near-empty
extraction (≤8 pts) when the region is non-trivial (≥60 paths), the path/point
sparsity ratio is high (≥12), AND the region contains ≥2 dense (>8-vertex)
line/curve paths — i.e. the real data is encoded as curves and the few markers
are annotation/peak glyphs, not a scatter series. Skip reason
`"sparse markers on dense chart"`. +2 tests. pytest in main: **459 passed,
15 skipped**.

Direct verification on the two backlog targets + the legitimate-sparse control
(`extract_pdf` on the specific page, gate active):

| chart | result | reason |
|---|---|---|
| astro_2606.04712 p24_c1 (dense extinction track, 4 stray pts) | skipped | sparse markers on dense chart |
| materials_2606.04919 p21_c3 (flat line, 4 off-locus pts) | skipped | sparse markers on dense chart |
| astro_2606.02688 p18_c2 (genuine 3-marker line, ~42 paths) | extracted | spared (region below 60-path floor) |

Both frozen-set "near-empty/sparse" wrongs are now skipped; the legitimate sparse
control is preserved (it falls below the 60-path floor and has no dense curves).
Independent rendering confirmed 04712/04919 were unusable (4 points not on the
data locus); the gate is a clean wrong→skip. Awaiting frozen-set A/B grade.

## Investigation note — Levers 2 (slanted guide/fit lines) deferred
A broad audit (full-corpus scans + visual rendering) found NO clean vector-level
discriminator for slanted guide/fit lines that does not also drop real data:
near-perfectly-straight diagonal line-series spanning the plot are, in the real
corpus, dominated by **legitimate** data (surrogate-model fits lying on the
markers, e.g. materials_2606.01444 p17 "anisotropic stiffness surrogate";
genuine linear relationships, e.g. materials_2606.02455 p8). The one clearly-bad
case (materials_2606.05128 red "μ~n" guides) is drawn as fragmented dashed
*marks*, not a clean line-series, so a straight-line gate in `lines.py` would not
catch it anyway. Likewise a "filled-twin/closed-contour" gate fired on legitimate
filled-area spectra and confidence-band curves (astro_2606.00258, 2606.00219) —
geometrically identical to credible-region boundaries at the primitive level.
Conclusion: Lever 2 has no clean precision/recall trade with the available
primitives (consistent with the original `_is_chart_type` design rationale);
deferred rather than shipped as a lossy gate. Lever 1 (2D-maps) and Lever 3
(sparse-on-dense) carry the measurable gains.
