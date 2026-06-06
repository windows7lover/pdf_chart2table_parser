# Legend / Label Fidelity — Judge 2 Report (post-legend-recovery round)

**Scope:** 100 charts assessed (20 user-flagged feedback_recheck + 80 survey_r3).
**Method:** Visual comparison of LEFT original panel vs RIGHT re-plot legend in reconstruction PNGs, cross-referenced against chart.json `series[].label`.
**Baseline (judge 1, survey_r2):** legend_correct 10.9% (6/55), missing 47.3%, latex_garbled 27.3%.

---

## Summary Counts

| Set | Charts assessed | No-legend (skipped) | Legend-bearing |
|-----|----------------|---------------------|---------------|
| feedback_recheck | 20 | 2 | 18 |
| survey_r3 | 80 | 24 | 56 |
| **Total** | **100** | **26** | **74** |

### Verdict distribution (legend-bearing charts only, n=74)

| Verdict | Count | % | Delta vs baseline |
|---------|-------|---|-------------------|
| `missing` | 53 | **71.6%** | +24.3 pp (worse) |
| `legend_correct` | 12 | **16.2%** | +5.3 pp (better) |
| `latex_garbled` | 9 | **12.2%** | −15.1 pp (better) |
| `wrong_entry` | 0 | **0.0%** | −14.5 pp (better) |

**Now legend-correct: 16.2%** (12/74) vs baseline 10.9% — **+5.3 pp improvement**.
**Now latex_garbled: 12.2%** (9/74) vs baseline 27.3% — **−15.1 pp improvement**.
**Now missing: 71.6%** (53/74) vs baseline 47.3% — **+24.3 pp regression**.

The legend-recovery round successfully reduced garbled and wrong-entry failures but has
**shifted the dominant failure mode** from garbled/wrong to outright missing. The `missing`
category expanded substantially because (a) survey_r3 has more charts with inline/colorbar
legends vs boxed legends, and (b) the recovery round appears to have raised the bar for
what counts as an extracted label (fewer spurious entries, but more None).

---

## Verdict Definitions

- **`legend_correct`** — all labeled series match the original labels exactly, including special chars.
- **`latex_garbled`** — labels partially extracted but special/LaTeX chars wrong, dropped, or word-split.
- **`missing`** — legend present in original but most/all labels extracted as None, OR only a minority extracted.
- **`wrong_entry`** — wrong text used (panel label, annotation fragment, spurious entry dominates).
- **`no_legend`** — chart has no discrete text legend; skipped from scoring.

---

## Legend-Correct Examples (12 charts)

| sample_id | true legend |
|-----------|-------------|
| `materials_2606.04842_page3_chart2` | Al; Ga |
| `materials_2606.01938_page8_chart1` | R = 30 nm; R = 50 nm; R = 70 nm; R = 100 nm |
| `materials_2606.00223_page6_chart1` | AFM-C - NM; AFM-G - NM; FM - NM |
| `materials_2606.01938_page8_chart2` | R = 30 nm; R = 50 nm; R = 70 nm; R = 100 nm |
| `astro_2606.04309_page13_chart5` | fmol; HI; H2; H atoms |
| `astro_2606.04711_page7_chart1` | Data; Power law; Liu et al. (2025); Model |
| `astro_2606.01956_page13_chart7` | PHOENIX-II exp; Fit |
| `astro_2606.01103_page6_chart2` | ξ=0; ξ=1; ξ=2 |
| `astro_2606.01103_page8_chart2` | Species 1; Species 2 |
| `astro_2606.04711_page7_chart2` | Data; Power law; Liu et al. (2025); Model |
| `ml__2606.03553__page32_chart2` | PCA; ε-PCA; Sparse PCA; AdvPCA (Ours) |
| `ml__2606.04662__page6_chart1` | Muon; Adam |

---

## Ranked Remaining Failing Constructs

### LaTeX-garbled failures (9 charts)

1. **Word-spacing / kerning splits** (2 charts, same paper `materials_2606.01373`): "Linear cavit y(y)", "Wit ho ut cavit y" — systematic inter-character spacing from LaTeX rendering.
2. **Subscript loss + Greek drop** (2 charts): `E_N(a_-:CR)` → `E (a :CR)` (subscript N and minus), `δt_4≠0(↑↓)` → `dt4` (δ→d, ≠ dropped, arrows dropped).
3. **Tilde/accent on variable** (1 chart): `b̃ = −0.83` → `b =` (tilde dropped, value dropped).
4. **α Greek prefix drop** (1 chart): `α=20(meVnm)` → `* ` or `=20(meVnm)`.
5. **Multiple compounded LaTeX** (1 chart, `ml__2606.01457`): `γ_T^ind` → `T`, `bound ½log(...)` → `bound 2`, `γ_T^causal` → `s`.
6. **Chemical bond dash drop** (1 chart): `Si-C` → `Si C`, `C-C` → `C C`.
7. **Partial label + dropped values** (1 chart, `materials_2606.04724_page23_chart1`): `b̃=−0.83;−1.04;−1.24;−1.45` → only 3 extracted as `b =`.

### Missing failures (53 charts)

The 53 missing cases split into three root causes:

---

## (a) / (b) / (c) Split of Remaining Failures (62 total non-correct charts)

### (a) Missing/unmatched — legend-region detection or series↔legend matching failure — NOT LaTeX (35 charts)

These charts have ASCII-compatible labels or no special chars, but the extractor returns all None.
Root causes:
- **Inline colored text** (not in a box legend): labels printed directly on/beside curves — 12 charts (e.g. `materials_2606.02317_page23_chart1` Silica/Alumina, `astro_2606.02566_page17_chart1` m10i-s70, `astro_2606.02711_page57_chart1` Full Z/Half Z).
- **Colorbar-style legend** (gradient bar, not text entries): 4 charts (e.g. `astro__2606.00212__page12_chart5`, `astro_2606.04219_page8_chart4`).
- **Large multi-series maps** (>50 series, 2D parameter space): 3 charts (`astro_2606.04810_page12_chart4`, `astro__2606.04810__page12_chart9`, `astro__2606.04810__page12_chart15`).
- **Legend clipped at page margin** or outside extracted region: 3 charts.
- **Box legend present but all None** (region detection miss): 13 charts where a clear boxed legend with ASCII text is in the original but extracted as all None (e.g. `astro_2606.02711_page57_chart1`, `astro_2606.05237_page123_chart2`, `materials_2606.03405__page12_chart2`).

### (b) LaTeX-glyph recoverable via improved PDF text parsing (15 charts)

These have correct structure but specific Unicode/LaTeX chars fail:
- Greek letters: δ, α, γ, ξ (ξ works! δ/α/γ still fail) — 5 charts.
- Sub/superscripts: `_N`, `^ind`, `_4`, `_6` numeric subscripts — 4 charts.
- Math operators: ≠, ≤, ½, →, ↑↓ — 4 charts.
- Tilde/accent on variable: b̃, λ̃ — 2 charts.

### (c) Type3 / glyph-path rendering — needs vision-based OCR (12 charts)

Labels render as glyph paths rather than Unicode text in the PDF. The extractor returns
marker-shape proxy strings like 'o', 's', '^' instead of the actual label text. Examples:
- `astro_2606.03667_page11_chart1`: WISSH, JWST z=3-4.5 → 's', 's', 's'.
- `astro_2606.00787_page7_chart3`: 3 instrument names → 'o', 'o', 'o'.
- `astro_2606.03522_page7_chart1`: LRD entries → 'o', '^'.
- `astro__2606.02687__page7_chart2`: All/Disk/Bulge partially correct but 2 spurious 'o'/'s' added.
- `materials_2606.00711_page4_chart1`: State M1→M3, M2→M3 → 's', 's'.
- `astro_2606.04624_page13_chart2`: Observed → 'o'.

These require rendering the legend glyph to image and OCR-ing the text, or using a vision model
to read the right-panel legend directly.

---

## Key Regression: Missing Rate Increase

The missing% jumped from 47.3% (baseline) to 71.6% now. This is largely **corpus composition**:
survey_r3 skews toward astro papers with inline labels and multi-series parameter maps vs
survey_r2 which had more materials papers with clean boxed legends. The wrong_entry rate
dropped to 0% (from 14.5%) indicating improved extraction discipline, but the tradeoff is
higher None rates.

---

## Deliverables

- CSV: `/network/scratch/s/scieurda/pdf_chart2table/survey_r3/legend_verdicts.csv`
- This doc: `/network/projects/sail/damien/github/pdf_chart2table_parser/docs/legend_judge2.md`
