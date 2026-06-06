# Legend / Label Fidelity — Judge 3 Report (post-iteration-2: legend-matching + legend-bbox fixes)

**Scope:** 100 charts assessed (20 feedback_recheck + 80 survey_r4).
**Method:** Visual comparison of LEFT original panel vs RIGHT re-plot legend in reconstruction PNGs,
cross-referenced against chart.json `series[].label`.
**Baseline (judge 2, survey_r3 + feedback_recheck):** legend_correct 16.2% (12/74), missing 71.6% (53/74), latex_garbled 12.2% (9/74), wrong_entry 0%.

---

## Summary Counts

| Set | Charts assessed | No-legend (skipped) | Legend-bearing |
|-----|----------------|---------------------|---------------|
| feedback_recheck | 20 | 8 | 12 |
| survey_r4 | 80 | 20 | 60 |
| **Total** | **100** | **28** | **72** |

### Verdict distribution (legend-bearing charts only, n=72)

| Verdict | Count | % | Delta vs judge-2 baseline |
|---------|-------|---|--------------------------|
| `missing` | 42 | **58.3%** | −13.3 pp (better) |
| `latex_garbled` | 12 | **16.7%** | +4.5 pp (worse) |
| `wrong_entry` | 9 | **12.5%** | +12.5 pp (worse) |
| `legend_correct` | 9 | **12.5%** | −3.7 pp (worse) |

**Now legend-correct: 12.5%** (9/72) vs baseline 16.2% — **−3.7 pp regression**.
**Now missing: 58.3%** (42/72) vs baseline 71.6% — **−13.3 pp improvement**.
**Now latex_garbled: 16.7%** (12/72) vs baseline 12.2% — **+4.5 pp increase**.
**Now wrong_entry: 12.5%** (9/72) vs baseline 0% — **+12.5 pp increase**.

---

## Verdict Definitions

- **`legend_correct`** — all labeled series match the original labels (minor spacing OK), including special chars.
- **`latex_garbled`** — labels partially extracted but special/LaTeX chars wrong, dropped, or word-split.
- **`missing`** — legend present in original but most/all labels extracted as None, OR only minority extracted.
- **`wrong_entry`** — wrong text used (Type3 marker-proxy 's'/'o'/'^' entries, or annotation fragment dominates).
- **`no_legend`** — chart has no discrete text legend (colorbar, inline labels, annotation-only, single series); skipped.

---

## Legend-Correct Charts (9)

| sample_id | true legend |
|-----------|-------------|
| `materials__2606.03553__page32_chart2` | PCA; ε-PCA; Sparse PCA; AdvPCA (Ours) |
| `materials__2606.04662__page6_chart1` | Muon; Adam |
| `materials_2606.01938_page8_chart1` | R = 30 nm; R = 50 nm; R = 70 nm; R = 100 nm |
| `astro_2606.03546_page3_chart4` | Experimental value; Linear fit |
| `astro_2606.04827_page5_chart2` | This work; THESAN (Yeh+23); Direct LyC; Wang+26a (const. SFE) |
| `astro_2606.01853_page7_chart2` | mχ = 10 TeV; mχ = 26.5 TeV; mχ = 1000 TeV |
| `astro_2606.03546_page4_chart1` | SO2 / Au; SO2 / H2O amorphous compact |
| `astro_2606.01823_page3_chart1` | S 2.25 GHz; X 8.42 GHz |
| `astro_2606.02700_page16_chart2` | Analytic (a* =0) |

---

## (a) / (b) / (c) Split of Remaining Failures (63 non-correct charts)

### (a) Missing/unmatched — legend-region detection or series↔legend matching failure — NOT LaTeX (42 charts)

These charts have ASCII-compatible labels or no special chars, but extractor returns all/most None.
Root causes:
- **Inline colored text** (labels printed directly on/beside curves, not in a legend box): ~15 charts
  (e.g. `materials__2606.02317__page11_chart2` Silica/Alumina, `astro_2606.02711_page25_chart2` LYDION/MC, `astro_2606.03016_page16_chart1` a/b/c/d).
- **Box legend present but all None** (region detection or matching miss): ~18 charts
  (e.g. `materials_2606.02858_page4_chart1` Py6/Py6-Nd8/16, `materials_2606.00681_page4_chart3` A2-like pole/electrical peak, `astro_2606.02691_page11_chart2` Simulation/Two-Zone/Blandford-McKee, `astro_2606.03776_page8_chart1` z_t labels).
- **Colorbar-style or 99-series parameter maps** (gradient bar, not text entries): already counted as no_legend; ~4 boundary.
- **Partial extraction** (some series labeled, majority None): ~9 charts
  (e.g. `astro_2606.05298_page7_chart1` SL B+23/SL B+21/Dynamics partial, `astro_2606.04827_page9_chart1` z=6/8/10 vs dashed THESAN).

### (b) LaTeX-glyph recoverable via improved PDF text parsing (12 charts)

These have correct structure but specific Unicode/LaTeX chars fail:
- **Word-spacing / kerning splits** (2 charts, same paper `materials_2606.01373`): "Linear cavit y(y)" systematic kerning.
- **Greek letter drop** (4 charts): α → dropped, δ → d, γ_T^{ind} → T, χ subscript drops.
- **Subscript/superscript loss** (3 charts): _N, ^ind, _4 numeric subscripts.
- **Tilde/accent on variable** (2 charts): b̃ → b =, value truncated.
- **Math operators** (3 charts): ≠ dropped, ½ → 2, → sign dropped, ⁻¹ → 1.
- **Angle-bracket → pipe substitution** (1 chart): `|⟨a₁a₂⟩|` → `| a1a2 |`.
- **Chemical superscript** (1 chart): `0.2 e⁻` → `0.2 e`.

### (c) Type3 / glyph-path rendering — needs vision-based OCR (9 charts)

Labels render as glyph paths rather than Unicode text. Extractor returns marker-shape proxy
strings like 'o', 's', '^' instead of actual label text, polluting the wrong_entry count:
- `materials_2606.00403_page10_chart1`: 9R → 'o' proxy.
- `materials_2606.02489_page9_chart3`: C1/−C2 correct but 2 'o' proxies added.
- `materials_2606.05050_page18_chart4`: Case bank/Reuse fraction correct but 4 's'/'o' proxies from bar glyphs.
- `materials_2606.00681_page4_chart2`: S1/A1/ST/S2/A2/AT/electrical peak; AT/ST correct, 5 others 'o'/'^' proxies.
- `astro_2606.04712_page22_chart1`: LRDs correct but 4 '^'/'s'/'o' proxies added.
- `astro_2606.00787_page9_chart2`: MINERVA-A correct; 4 instrument names → 'o' proxies.
- `astro_2606.03667_page10_chart2`: SDSS-eRosita/WISSH/JWST all None (99 series all Type3).
- `materials__2606.02687__page7_chart2`: All/Disk/Bulge partially correct; 2 'o'/'s' proxies.
- `materials__2606.04147__page3_chart2`: T_fit correct but first series '*' spurious marker label.

---

## Analysis vs Judge-2 Baseline

The `missing` rate dropped substantially (71.6% → 58.3%, −13.3 pp), suggesting the legend-bbox
and legend-matching fixes land some improvements. However:

1. **`legend_correct` regressed** (16.2% → 12.5%, −3.7 pp): survey_r4 has more challenging
   astro papers with complex Type3 fonts and multi-series inline legends vs survey_r3.

2. **`wrong_entry` appeared** (0% → 12.5%): the iteration-2 fixes appear to have introduced or
   exposed more Type3 marker-proxy contamination. Matching now finds a legend region and assigns
   the glyph-path proxy strings rather than returning None.

3. **`latex_garbled` increased slightly** (12.2% → 16.7%): more papers with sub/superscript and
   Greek chars in this batch vs r3.

---

## Key Remaining Failure Modes (priority order)

1. **Type3 glyph proxy contamination** (9 wrong_entry charts): legend entries filled with
   's'/'o'/'^' from marker glyphs. Fix: filter out pure single-char marker-proxy labels.

2. **Box legend extraction miss** (~18 charts): clear box legends with ASCII text returning all
   None — region detection or series matching still failing for ~25% of legend-bearing charts.

3. **Inline label extraction** (~15 charts): labels printed directly on curves not in a box;
   requires different extraction strategy.

4. **LaTeX kerning/spacing splits** (2 charts, `materials_2606.01373` series): systematic
   inter-character spacing in rendered glyphs splitting single words.

---

## Deliverables

- CSV: `/network/scratch/s/scieurda/pdf_chart2table/survey_r4/legend_verdicts.csv`
- This doc: `/network/projects/sail/damien/github/pdf_chart2table_parser/docs/legend_judge3.md`
