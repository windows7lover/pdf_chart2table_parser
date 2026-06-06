# Legend / Label Fidelity — Baseline Judge Report

**Scope:** 100 charts assessed (20 user-flagged feedback_recheck + 80 survey_r2).
**Focus:** LaTeX/TeX-rendered legends — Greek letters, math operators, sub/superscripts.
**Method:** Visual comparison of LEFT original panel vs RIGHT re-plot legend in reconstruction PNGs, cross-referenced against chart.json `series[].label`.

---

## Summary Counts

| Set | Charts assessed | No-legend (skipped) | Legend-bearing |
|-----|----------------|---------------------|---------------|
| feedback_recheck | 20 | 7 | 13 |
| survey_r2 | 80 | 38 | 42 |
| **Total** | **100** | **45** | **55** |

### Verdict distribution (legend-bearing charts only, n=55)

| Verdict | Count | % |
|---------|-------|---|
| `missing` | 26 | 47.3% |
| `latex_garbled` | 15 | 27.3% |
| `wrong_entry` | 8 | 14.5% |
| `legend_correct` | 6 | **10.9%** |

**Baseline legend-correct: 10.9%** (6/55 legend-bearing charts).
**Baseline latex_garbled: 27.3%** (15/55).
Combined failure rate (missing + garbled + wrong): **89.1%**.

---

## Verdict Definitions

- **`legend_correct`** — all series labels match the original, including special chars.
- **`latex_garbled`** — labels partially extracted but special/LaTeX chars wrong or dropped.
- **`missing`** — legend present in original but all or most labels extracted as `None`.
- **`wrong_entry`** — wrong text used (panel label, annotation, subscript fragment, spurious entry).
- **`no_legend`** — chart has no discrete legend; skipped from scoring.

---

## Ranked Failure Modes (LaTeX Constructs)

Listed from most to least impactful (frequency of `latex_garbled` occurrence):

### 1. Subscript loss (6 occurrences)
Most pervasive LaTeX failure. Underscored subscripts are silently dropped or collapsed.
- `E_N(a_-:CR)` → `E (a :CR)` — subscript N and sign stripped
- `O_A2`, `V_A2` → `o`, `s` — subscript collapses entire label to single char
- `La z^2`, `Ni x^2-y^2` → `La z2`, `Ni x2- y2` — exponents rendered as plain digits
- `E_N(a_-,MP3:CR,MP1)` → `E (a ,MP3 :CR,MP1)` — partial survival

### 2. Superscript / exponent loss (3 occurrences)
Exponent carets dropped or mangled.
- `gamma_T^ind` → `T` (entire label collapses)
- `10^8 M_sun` → `10⊙8 M⊙` (solar symbol used as caret substitute)
- `La z^2` → `La z2` (caret dropped, digit survives)

### 3. Word-spacing artifacts (2 occurrences — systematic bug)
LaTeX character-spacing/kerning causes spurious whitespace injected mid-word.
- `Linear cavity(y)` → `Linear cavit y(y)` (space before 'y')
- `Without cavity` → `Wit ho ut cavit y` (multiple splits)
This is a consistent bug in the same paper family (`materials_2606.01373`).

### 4. Greek letter loss — δ (delta), ε (epsilon), α, λ̃ (1–2 each)
- `δt_4=0` → `dt4` (delta lost, subscript lost, feedback chart `materials__2606.01515`)
- `α=3 model` → `None` (α lost, `ml__2606.04212`)
- `λ̃=0.003` → `= 0.003` (lambda-tilde prefix dropped, `materials_2606.04724_page24`)
- `ε-PCA` → `ε-PCA` (epsilon survived — one of the few correct cases)

### 5. Math operators: ≠, ↑, ↓, fraction (1 each)
From `materials__2606.01515` feedback:
- `δt_4 ≠ 0 (↑)` → `dt4` (≠ dropped, ↑ dropped)
- `δt_4 ≠ 0 (↓)` → `None` (entire label lost)
- `bound ½log(1+Tκ²/σ²r)` → `bound 2` (fraction + all math dropped)

### 6. Missing entries (extraction completeness, not char-level)
Beyond garbling, 26/55 charts have most or all labels as `None`. Key patterns:
- Inline colored text (not inside a legend box) never extracted: `materials__2606.02317__page11_chart2` (Silica/Alumina/Hafnia/Silicon Nitride written directly on plot)
- Colorbar-style legends skipped entirely
- Multi-row or 6+ entry legends often truncated

---

## Correct Baseline Examples (6 charts)

| sample_id | true legend |
|-----------|-------------|
| `materials_2606.04724_page18_chart3` | DBC (peak); NBC (mean) |
| `materials_2606.02399_page2_chart2` | thr. em.; thr. abs. |
| `materials_2606.04842_page3_chart1` | Al; Ga |
| `astro_2606.01956_page13_chart1` | HOROLOGIUM-I exp; Fit |
| `astro_2606.01823_page4_chart4` | Power-law Fit |
| `astro_2606.04827_page5_chart2` | This work; THESAN (Yeh+23); Direct LyC; Wang+26a (const. SFE) |

Correct cases share: plain ASCII labels, no math, no subscripts, labels in a standard boxed legend.

---

## Key Example IDs for Regression Testing

| Failure type | Example chart | What to check |
|---|---|---|
| Subscript loss | `materials_2606.00165_page18_chart1` | E_N(a_-:CR) → E (a :CR) |
| Subscript collapses to char | `materials_2606.00681_page4_chart4` | O_A2→o, V_A2→s |
| Word-spacing split | `materials_2606.01373_page7_chart3` | "cavit y", "Wit ho ut" |
| Delta + neq + arrows | `materials__2606.01515__page7_chart4` | δt_4≠0(↑↓) → dt4/None |
| Greek α in label | `ml__2606.04212__page9_chart1` | α=3 model → None |
| Lambda-tilde prefix drop | `materials_2606.04724_page24_chart1` | λ̃=0.003 → =0.003 |
| Solar/exponent confusion | `astro_2606.01103_page13_chart1` | 10^8 M⊙ → 10⊙8 M⊙ |
| Superscript in gamma | `ml__2606.01457__page25_chart3` | γ_T^ind → T |

---

## Deliverables

- CSV: `/network/scratch/s/scieurda/pdf_chart2table/survey_r2/legend_verdicts.csv`
- This doc: `/network/projects/sail/damien/github/pdf_chart2table_parser/docs/legend_judge.md`
