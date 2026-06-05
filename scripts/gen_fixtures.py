"""Generate synthetic vector chart fixtures for the chart->table parser tests.

Each fixture is rendered with matplotlib and saved as BOTH a .pdf (the real
parser input -- PyMuPDF can open it) and a .eps, plus a ground-truth JSON
sidecar capturing the exact data, axes, ticks and styling.

Run: uv run python scripts/gen_fixtures.py [--outdir tests/fixtures]
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

SEED = 1234


@dataclass
class Series:
    label: str
    marker: str
    color: tuple[float, float, float]
    x: np.ndarray
    y: np.ndarray


@dataclass
class Spec:
    name: str
    chart_type: str  # "scatter" | "line_markers"
    figsize: tuple[float, float]
    xscale: str  # "linear" | "log"
    yscale: str
    title: str | None
    xlabel: str | None
    ylabel: str | None
    legend: bool
    make: Callable[[np.random.Generator], list[Series]]


# ---------------------------------------------------------------------------
# Data generators -- each returns a list of Series with meaningful synthetic data
# ---------------------------------------------------------------------------

# A small palette of distinct RGB colors and markers to spread across series.
COLORS = [
    (0.12, 0.47, 0.71),  # blue
    (0.84, 0.15, 0.16),  # red
    (0.17, 0.63, 0.17),  # green
    (0.58, 0.40, 0.74),  # purple
    (1.00, 0.50, 0.05),  # orange
]
MARKERS = ["o", "s", "^", "D", "v", "*", "x", "+"]


def _series(label, marker, color, x, y):
    return Series(label, marker, color, np.asarray(x, float), np.asarray(y, float))


def gen_linear_scatter(rng):
    x = np.linspace(0, 10, 30)
    y = 2.5 * x + 3.0 + rng.normal(0, 2.0, x.size)
    return [_series("y = 2.5x + 3 + noise", "o", COLORS[0], x, y)]


def gen_two_linear_scatter(rng):
    x = np.linspace(0, 10, 25)
    y1 = 1.0 * x + 1.0 + rng.normal(0, 1.0, x.size)
    y2 = -0.8 * x + 9.0 + rng.normal(0, 1.0, x.size)
    return [
        _series("slope +1.0", "o", COLORS[0], x, y1),
        _series("slope -0.8", "s", COLORS[1], x, y2),
    ]


def gen_gaussian_clusters(rng):
    centers = [(2, 2), (6, 7), (9, 2)]
    out = []
    for i, (cx, cy) in enumerate(centers):
        x = rng.normal(cx, 0.6, 40)
        y = rng.normal(cy, 0.6, 40)
        out.append(_series(f"cluster {i + 1}", MARKERS[i], COLORS[i], x, y))
    return out


def gen_exp_decay_logy(rng):
    x = np.linspace(0, 5, 25)
    y = 100.0 * np.exp(-0.8 * x) + rng.normal(0, 0.5, x.size).clip(min=-0.4)
    y = np.clip(y, 1e-2, None)
    return [_series("100 * exp(-0.8x)", "^", COLORS[2], x, y)]


def gen_power_law_loglog(rng):
    x = np.logspace(0, 3, 20)
    y1 = 5.0 * x**0.5
    y2 = 2.0 * x**1.0
    return [
        _series("5 * x^0.5", "o", COLORS[0], x, y1),
        _series("2 * x^1.0", "D", COLORS[3], x, y2),
    ]


def gen_damped_sine(rng):
    x = np.linspace(0, 12, 60)
    y = np.exp(-0.2 * x) * np.cos(2.0 * x)
    return [_series("exp(-0.2t) cos(2t)", "o", COLORS[0], x, y)]


def gen_convergence_logy(rng):
    it = np.arange(0, 50)
    methods = [
        ("GD", 0.92, COLORS[0], "o"),
        ("Momentum", 0.85, COLORS[1], "s"),
        ("Adam", 0.78, COLORS[2], "^"),
    ]
    out = []
    for name, rate, color, mk in methods:
        loss = 1.0 * rate**it + rng.uniform(0, 1e-3, it.size)
        out.append(_series(name, mk, color, it, loss))
    return out


def gen_two_lines_linear(rng):
    x = np.linspace(0, 2 * np.pi, 40)
    y1 = np.sin(x)
    y2 = np.cos(x)
    return [
        _series("sin(x)", "o", COLORS[0], x, y1),
        _series("cos(x)", "v", COLORS[4], x, y2),
    ]


def gen_sqrt_growth(rng):
    x = np.linspace(0, 100, 30)
    y = np.sqrt(x) + rng.normal(0, 0.15, x.size)
    return [_series("sqrt(x) + noise", "*", COLORS[1], x, y)]


def gen_logistic_lines(rng):
    x = np.linspace(-6, 6, 45)
    out = []
    for i, k in enumerate([0.7, 1.5, 3.0]):
        y = 1.0 / (1.0 + np.exp(-k * x))
        out.append(_series(f"k={k}", MARKERS[i], COLORS[i], x, y))
    return out


def gen_exp_growth_logy(rng):
    it = np.arange(0, 20)
    y = 2.0**it.astype(float)
    return [_series("2^n", "x", COLORS[3], it, y)]


def gen_single_minimal_scatter(rng):
    # No title, no labels, no legend -- robustness fixture.
    x = rng.uniform(0, 1, 50)
    y = 0.5 * x + rng.normal(0, 0.05, x.size) + 0.2
    return [_series("data", "+", COLORS[0], x, y)]


def gen_noisy_quadratic(rng):
    x = np.linspace(-5, 5, 35)
    y = 0.5 * x**2 - 1.0 * x + 2.0 + rng.normal(0, 1.5, x.size)
    return [_series("0.5x^2 - x + 2 + noise", "s", COLORS[2], x, y)]


# ---------------------------------------------------------------------------
# Multi-panel (subplot) fixtures
# ---------------------------------------------------------------------------

@dataclass
class Panel:
    chart_type: str  # "scatter" | "line_markers"
    title: str | None
    xlabel: str | None
    ylabel: str | None
    xscale: str
    yscale: str
    make: Callable[[np.random.Generator], list[Series]]


@dataclass
class MultiSpec:
    name: str
    figsize: tuple[float, float]
    grid: tuple[int, int]  # (rows, cols)
    sharex: bool
    sharey: bool
    panels: list[Panel]  # row-major


def _panel_scatter_line(rng):
    x = np.linspace(0, 8, 24)
    y = 1.3 * x + rng.normal(0, 1.2, x.size)
    return [_series("trend", "o", COLORS[0], x, y)]


def _panel_quadratic(rng):
    x = np.linspace(-4, 4, 28)
    y = 0.4 * x**2 + rng.normal(0, 0.8, x.size)
    return [_series("quadratic", "s", COLORS[1], x, y)]


def _panel_sine(rng):
    x = np.linspace(0, 2 * np.pi, 36)
    return [_series("sin", "^", COLORS[2], x, np.sin(x))]


def _panel_cosine(rng):
    x = np.linspace(0, 2 * np.pi, 36)
    return [_series("cos", "D", COLORS[3], x, np.cos(x))]


def _panel_decay(rng):
    x = np.linspace(0, 5, 25)
    y = np.clip(50.0 * np.exp(-0.7 * x) + rng.normal(0, 0.3, x.size), 1e-2, None)
    return [_series("decay", "v", COLORS[4], x, y)]


def _panel_cluster(rng):
    x = rng.normal(5, 0.8, 35)
    y = rng.normal(5, 0.8, 35)
    return [_series("cluster", "o", COLORS[3], x, y)]


MULTI_SPECS = [
    MultiSpec(
        "subplots_1x2_independent", (10, 4), (1, 2), False, False,
        [
            Panel("scatter", "Left", "x", "y", "linear", "linear",
                  _panel_scatter_line),
            Panel("line_markers", "Right", "t", "f(t)", "linear", "linear",
                  _panel_sine),
        ],
    ),
    MultiSpec(
        "subplots_2x2_grid", (9, 7), (2, 2), False, False,
        [
            Panel("scatter", "A", "x", "y", "linear", "linear",
                  _panel_scatter_line),
            Panel("scatter", "B", "x", "y", "linear", "linear",
                  _panel_quadratic),
            Panel("line_markers", "C", "t", "y", "linear", "linear",
                  _panel_sine),
            Panel("line_markers", "D", "t", "y", "linear", "linear",
                  _panel_cosine),
        ],
    ),
    MultiSpec(
        "subplots_2x1_sharex", (6, 7), (2, 1), True, False,
        [
            Panel("line_markers", "Top", None, "sin", "linear", "linear",
                  _panel_sine),
            Panel("line_markers", None, "t", "cos", "linear", "linear",
                  _panel_cosine),
        ],
    ),
    MultiSpec(
        "subplots_1x2_sharey", (10, 4), (1, 2), False, True,
        [
            Panel("scatter", "Left", "x", "y", "linear", "linear",
                  _panel_scatter_line),
            Panel("scatter", "Right", "x", None, "linear", "linear",
                  _panel_cluster),
        ],
    ),
    MultiSpec(
        "subplots_2x2_mixed_scales", (9, 7), (2, 2), False, False,
        [
            Panel("scatter", "linear", "x", "y", "linear", "linear",
                  _panel_scatter_line),
            Panel("line_markers", "semilogy", "x", "y", "linear", "log",
                  _panel_decay),
            Panel("scatter", "cluster", "x", "y", "linear", "linear",
                  _panel_cluster),
            Panel("line_markers", "cosine", "t", "y", "linear", "linear",
                  _panel_cosine),
        ],
    ),
]


# ---------------------------------------------------------------------------
# Fixture specs -- vary type, series count, scales, size, decorations, markers.
# ---------------------------------------------------------------------------

SPECS = [
    Spec("linear_scatter_1series", "scatter", (6, 4), "linear", "linear",
         "Linear fit with noise", "x", "y", True, gen_linear_scatter),
    Spec("two_linear_scatter", "scatter", (6, 4), "linear", "linear",
         "Two trends", "input", "output", True, gen_two_linear_scatter),
    Spec("gaussian_clusters_3", "scatter", (5, 5), "linear", "linear",
         "Gaussian clusters", "feature 1", "feature 2", True, gen_gaussian_clusters),
    Spec("exp_decay_semilogy", "line_markers", (6, 4), "linear", "log",
         "Exponential decay", "time", "amplitude", True, gen_exp_decay_logy),
    Spec("power_law_loglog", "line_markers", (6, 4.5), "log", "log",
         "Power laws", "x", "y", True, gen_power_law_loglog),
    Spec("damped_sine_small", "line_markers", (3, 2.5), "linear", "linear",
         None, None, None, False, gen_damped_sine),
    Spec("convergence_semilogy_3", "line_markers", (7, 4.5), "linear", "log",
         "Optimizer convergence", "iteration", "loss", True, gen_convergence_logy),
    Spec("trig_two_lines", "line_markers", (9, 4), "linear", "linear",
         "Trig functions", "x (rad)", "value", True, gen_two_lines_linear),
    Spec("sqrt_scatter_large", "scatter", (9, 6), "linear", "linear",
         "Square-root growth", "x", "sqrt(x)", True, gen_sqrt_growth),
    Spec("logistic_lines_3", "line_markers", (6, 4), "linear", "linear",
         "Logistic curves", "x", "sigma(kx)", True, gen_logistic_lines),
    Spec("exp_growth_semilogy", "line_markers", (5, 4), "linear", "log",
         "Exponential growth", "n", "2^n", False, gen_exp_growth_logy),
    Spec("minimal_scatter_nolegend", "scatter", (3, 2.5), "linear", "linear",
         None, None, None, False, gen_single_minimal_scatter),
    Spec("noisy_quadratic_scatter", "scatter", (6, 4), "linear", "linear",
         "Noisy quadratic", "x", "y", True, gen_noisy_quadratic),
]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render(spec: Spec, rng: np.random.Generator, outdir: Path) -> dict:
    series = spec.make(rng)

    fig, ax = plt.subplots(figsize=spec.figsize)
    ax.set_xscale(spec.xscale)
    ax.set_yscale(spec.yscale)

    for s in series:
        if spec.chart_type == "scatter":
            ax.scatter(s.x, s.y, marker=s.marker, color=s.color, label=s.label)
        else:
            ax.plot(s.x, s.y, marker=s.marker, color=s.color, label=s.label,
                    linestyle="-")

    if spec.title:
        ax.set_title(spec.title)
    if spec.xlabel:
        ax.set_xlabel(spec.xlabel)
    if spec.ylabel:
        ax.set_ylabel(spec.ylabel)
    if spec.legend:
        ax.legend()

    fig.tight_layout()
    # Draw so that limits/ticks reflect what will be rendered.
    fig.canvas.draw()

    pdf_path = outdir / f"{spec.name}.pdf"
    eps_path = outdir / f"{spec.name}.eps"
    fig.savefig(pdf_path)
    fig.savefig(eps_path)

    xlim = ax.get_xlim()
    ylim = ax.get_ylim()

    def visible_ticks(ticks, lim):
        lo, hi = min(lim), max(lim)
        return [float(t) for t in ticks if lo <= t <= hi]

    gt = {
        "name": spec.name,
        "pdf": pdf_path.name,
        "eps": eps_path.name,
        "chart_type": spec.chart_type,
        "figsize_in": [float(spec.figsize[0]), float(spec.figsize[1])],
        "title": spec.title,
        "x_axis": {
            "label": spec.xlabel,
            "scale": spec.xscale,
            "lim": [float(xlim[0]), float(xlim[1])],
        },
        "y_axis": {
            "label": spec.ylabel,
            "scale": spec.yscale,
            "lim": [float(ylim[0]), float(ylim[1])],
        },
        "xticks": visible_ticks(ax.get_xticks(), xlim),
        "yticks": visible_ticks(ax.get_yticks(), ylim),
        "series": [
            {
                "label": s.label,
                "marker": s.marker,
                "color": [float(c) for c in s.color],
                "x": s.x.tolist(),
                "y": s.y.tolist(),
            }
            for s in series
        ],
    }

    json_path = outdir / f"{spec.name}.json"
    json_path.write_text(json.dumps(gt, indent=2))

    plt.close(fig)
    return gt


def render_multi(spec: MultiSpec, rng: np.random.Generator, outdir: Path) -> dict:
    rows, cols = spec.grid
    fig, axes = plt.subplots(rows, cols, figsize=spec.figsize,
                             sharex=spec.sharex, sharey=spec.sharey,
                             squeeze=False)

    panels_gt = []
    for idx, panel in enumerate(spec.panels):
        r, c = divmod(idx, cols)
        ax = axes[r][c]
        ax.set_xscale(panel.xscale)
        ax.set_yscale(panel.yscale)
        series = panel.make(rng)
        for s in series:
            if panel.chart_type == "scatter":
                ax.scatter(s.x, s.y, marker=s.marker, color=s.color, label=s.label)
            else:
                ax.plot(s.x, s.y, marker=s.marker, color=s.color, label=s.label,
                        linestyle="-")
        if panel.title:
            ax.set_title(panel.title)
        if panel.xlabel:
            ax.set_xlabel(panel.xlabel)
        if panel.ylabel:
            ax.set_ylabel(panel.ylabel)
        panels_gt.append((idx, r, c, panel, ax, series))

    fig.tight_layout()
    fig.canvas.draw()

    pdf_path = outdir / f"{spec.name}.pdf"
    eps_path = outdir / f"{spec.name}.eps"
    fig.savefig(pdf_path)
    fig.savefig(eps_path)

    def visible_ticks(ticks, lim):
        lo, hi = min(lim), max(lim)
        return [float(t) for t in ticks if lo <= t <= hi]

    panels = []
    for idx, r, c, panel, ax, series in panels_gt:
        xlim = ax.get_xlim()
        ylim = ax.get_ylim()
        panels.append({
            "index": idx,
            "row": r,
            "col": c,
            "chart_type": panel.chart_type,
            "title": panel.title,
            "x_axis": {
                "label": panel.xlabel,
                "scale": panel.xscale,
                "lim": [float(xlim[0]), float(xlim[1])],
            },
            "y_axis": {
                "label": panel.ylabel,
                "scale": panel.yscale,
                "lim": [float(ylim[0]), float(ylim[1])],
            },
            "xticks": visible_ticks(ax.get_xticks(), xlim),
            "yticks": visible_ticks(ax.get_yticks(), ylim),
            "series": [
                {
                    "label": s.label,
                    "marker": s.marker,
                    "color": [float(cc) for cc in s.color],
                    "x": s.x.tolist(),
                    "y": s.y.tolist(),
                }
                for s in series
            ],
        })

    gt = {
        "name": spec.name,
        "pdf": pdf_path.name,
        "eps": eps_path.name,
        "figsize_in": [float(spec.figsize[0]), float(spec.figsize[1])],
        "n_panels": len(spec.panels),
        "grid": [rows, cols],
        "shared_x": bool(spec.sharex),
        "shared_y": bool(spec.sharey),
        "panels": panels,
    }

    json_path = outdir / f"{spec.name}.json"
    json_path.write_text(json.dumps(gt, indent=2))

    plt.close(fig)
    return gt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outdir", default="tests/fixtures")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(SEED)

    rows = []
    for spec in SPECS:
        gt = render(spec, rng, outdir)
        rows.append(gt)

    # Summary table
    header = f"{'name':<28} {'type':<13} {'x/y scale':<14} {'series':>6}  size"
    print(header)
    print("-" * len(header))
    for gt in rows:
        scales = f"{gt['x_axis']['scale']}/{gt['y_axis']['scale']}"
        size = f"{gt['figsize_in'][0]}x{gt['figsize_in'][1]}"
        print(f"{gt['name']:<28} {gt['chart_type']:<13} {scales:<14} "
              f"{len(gt['series']):>6}  {size}")
    print(f"\nWrote {len(rows)} single-panel fixtures (.pdf + .eps + .json) "
          f"to {outdir}/")

    multi_rows = []
    for spec in MULTI_SPECS:
        gt = render_multi(spec, rng, outdir)
        multi_rows.append(gt)

    header = f"{'name':<28} {'grid':>6} {'panels':>6} {'sharex':>7} {'sharey':>7}"
    print()
    print(header)
    print("-" * len(header))
    for gt in multi_rows:
        grid = f"{gt['grid'][0]}x{gt['grid'][1]}"
        print(f"{gt['name']:<28} {grid:>6} {gt['n_panels']:>6} "
              f"{str(gt['shared_x']):>7} {str(gt['shared_y']):>7}")
    print(f"\nWrote {len(multi_rows)} multi-panel fixtures (.pdf + .eps + .json) "
          f"to {outdir}/")


if __name__ == "__main__":
    main()
