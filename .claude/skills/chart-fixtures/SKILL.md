---
name: chart-fixtures
description: Use when you need to (re)generate or extend the synthetic test charts the parser is verified against. Documents scripts/gen_fixtures.py, the ground-truth JSON schema, that each fixture is saved as BOTH .pdf (parser input) and .eps, and how to add a new fixture spec.
---

# Generate / extend synthetic chart fixtures

Fixtures are matplotlib charts drawn from *known* arrays, so extraction can be
checked against exact ground truth. They live in `tests/fixtures/` as
`<name>.pdf` + `<name>.eps` + `<name>.json`.

## Regenerate all fixtures

```bash
export UV_LINK_MODE=copy
uv run python scripts/gen_fixtures.py [--outdir tests/fixtures]
```

Deterministic (`SEED = 1234`), so re-running reproduces identical data. Each
spec writes:
- **`<name>.pdf`** — the real parser input (fitz/PyMuPDF can open it).
- **`<name>.eps`** — same chart, vector EPS (fitz **cannot** open EPS; this is
  for reference/other tools only — never feed it to the parser).
- **`<name>.json`** — ground truth (schema below).

## Ground-truth JSON schema

```json
{
  "name": "...", "pdf": "...", "eps": "...",
  "chart_type": "scatter|line_markers",
  "figsize_in": [w, h],
  "title": "..." | null,
  "x_axis": {"label": "..."|null, "scale": "linear|log", "lim": [lo, hi]},
  "y_axis": {"label": "..."|null, "scale": "linear|log", "lim": [lo, hi]},
  "xticks": [..visible tick values..],
  "yticks": [..],
  "series": [
    {"label": "...", "marker": "o|s|^|D|v|*|x|+",
     "color": [r, g, b], "x": [...], "y": [...]}
  ]
}
```

(The eval script consumes `x_axis.scale`, `y_axis.scale`, and `series[].x/y`.)

## Add a new fixture

In `scripts/gen_fixtures.py`:

1. Write a generator `def gen_myfixture(rng) -> list[Series]:` returning
   `_series(label, marker, color, x, y)` entries. Use the `COLORS`/`MARKERS`
   palettes for distinct, separable series.
2. Append a `Spec(...)` to the `SPECS` list: name, `chart_type`
   ("scatter" or "line_markers"), figsize, x/y scale, title, x/y labels,
   `legend` bool, and your generator.
3. Re-run `gen_fixtures.py`. The `.pdf`/`.eps`/`.json` are written and the spec
   appears in the printed summary table.

Cover varied cases: linear/log axes, multiple series, marker shapes, with/without
legend/title, and deliberately-hard charts (no labels, dense overlap) to exercise
the parser's skip-and-log behavior.
