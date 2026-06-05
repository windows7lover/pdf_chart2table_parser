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
# Bug-reproducer generators (failure modes from judge reports)
# ---------------------------------------------------------------------------

# Mode 1: Reversed / descending axis
def gen_reversed_xaxis(rng):
    """X ticks run high→low (e.g. binding energy / wavenumber convention)."""
    x = np.linspace(300, 280, 25)  # descending x
    y = 2.0 * (x - 280) + rng.normal(0, 1.0, x.size)
    return [_series("signal", "o", COLORS[0], x, y)]


def gen_reversed_yaxis(rng):
    """Y ticks run high→low (magnitude scale in astronomy)."""
    x = np.linspace(0, 10, 20)
    y = 20.0 - 1.5 * x + rng.normal(0, 0.3, x.size)
    return [_series("magnitude", "s", COLORS[1], x, y)]


def gen_reversed_xaxis_2series(rng):
    """Reversed x-axis with two series — stress-tests series matching."""
    x = np.linspace(10, 1, 20)  # descending
    y1 = x**2 * 0.5 + rng.normal(0, 0.5, x.size)
    y2 = x**2 * 0.2 + 5 + rng.normal(0, 0.5, x.size)
    return [
        _series("A", "o", COLORS[0], x, y1),
        _series("B", "^", COLORS[2], x, y2),
    ]


# Mode 3: Log axes — extra variants beyond the two already in SPECS
def gen_semilogx_decay(rng):
    """Semilog-x: x is log scale, y is linear — stress-tests x-log calibration."""
    x = np.logspace(-2, 2, 30)
    y = 1.0 / (1.0 + x) + rng.normal(0, 0.02, x.size)
    y = np.clip(y, 0, None)
    return [_series("1/(1+x)", "o", COLORS[0], x, y)]


def gen_loglog_multipower(rng):
    """Log-log with 4 series spanning 3 decades — tests series separation on log axes."""
    x = np.logspace(0, 3, 25)
    exponents = [0.25, 0.5, 1.0, 2.0]
    colors = [COLORS[0], COLORS[1], COLORS[2], COLORS[3]]
    markers = ["o", "s", "^", "D"]
    return [
        _series(f"x^{e}", mk, c, x, x**e)
        for e, mk, c in zip(exponents, markers, colors)
    ]


def gen_semilogy_negstart(rng):
    """Semilog-y with values crossing from small to large (1e-4 → 1e2)."""
    x = np.linspace(0, 10, 30)
    y1 = 1e-4 * np.exp(x * 0.9) + rng.uniform(0, 1e-5, x.size)
    y2 = 1e-3 * np.exp(x * 0.7) + rng.uniform(0, 1e-4, x.size)
    y1 = np.clip(y1, 1e-5, None)
    y2 = np.clip(y2, 1e-5, None)
    return [
        _series("fast", "o", COLORS[0], x, y1),
        _series("slow", "s", COLORS[1], x, y2),
    ]


# Mode 5: Negative tick values / minus sign
def gen_negative_x_ticks(rng):
    """Both positive and negative x values — minus sign in tick labels."""
    x = np.linspace(-5, 5, 30)
    y = np.sin(x) + rng.normal(0, 0.05, x.size)
    return [_series("sin(x)", "o", COLORS[0], x, y)]


def gen_negative_both_axes(rng):
    """Negative values on both x and y axes, including -0.10 style labels."""
    x = np.linspace(-0.20, 0.20, 25)
    y = -0.5 * x + rng.normal(0, 0.02, x.size)
    return [_series("−slope", "s", COLORS[1], x, y)]


def gen_negative_logy_series(rng):
    """Y values with log scale starting from negative exponents — calibration test."""
    x = np.linspace(-3, 3, 25)
    y = 1e-3 * np.exp(x) + rng.normal(0, 1e-4, x.size)
    y = np.clip(y, 1e-4, None)
    return [_series("exp(x)*1e-3", "^", COLORS[2], x, y)]


# Mode 6: Decimal & exponent tick labels
def gen_decimal_ticks(rng):
    """Values in [0, 1] — tick labels like 0.0, 0.2, 0.4 … 1.0."""
    x = np.linspace(0, 1, 20)
    y = x**2 + rng.normal(0, 0.02, x.size)
    return [_series("x^2", "o", COLORS[0], x, y)]


def gen_small_decimal_ticks(rng):
    """Values like 0.10, 0.12 — tight decimal ticks."""
    x = np.linspace(0.10, 0.30, 20)
    y = 0.5 * x + 0.05 + rng.normal(0, 0.005, x.size)
    return [_series("0.5x+0.05", "s", COLORS[1], x, y)]


def gen_exponent_ticks_1e3(rng):
    """Y values 0..5000 — matplotlib often formats as 1e3 / ×10³."""
    x = np.linspace(0, 10, 25)
    y = 500 * x + rng.normal(0, 100, x.size)
    return [_series("500x", "^", COLORS[2], x, y)]


def gen_large_exponent_ticks(rng):
    """X values in millions — tick labels like 5k/10M or 1e6 style."""
    x = np.array([1e6, 2e6, 5e6, 1e7, 2e7, 5e7, 1e8], dtype=float)
    y = np.sqrt(x / 1e6) + rng.normal(0, 0.1, x.size)
    return [_series("sqrt(x/1M)", "D", COLORS[3], x, y)]


# Mode 7: Missing series — dashed/dotted intermediates, many series
def gen_solid_dashed_same_color(rng):
    """Solid + dashed line of the SAME color — parser may treat as 2 series or miss one."""
    x = np.linspace(0, 10, 40)
    y1 = np.sin(x)
    y2 = np.sin(x) * 0.7 + 0.5
    return [
        _series("solid", "o", COLORS[0], x, y1),
        _series("dashed", "o", COLORS[0], x, y2),  # same color, different line style
    ]


def gen_four_series_log(rng):
    """4 series on semilog-y — intermediate dashed ones tend to be dropped."""
    it = np.arange(0, 40)
    rates = [0.95, 0.90, 0.84, 0.78]
    mks = ["o", "s", "^", "D"]
    return [
        _series(f"rate={r}", mk, COLORS[i], it, r**it + rng.uniform(0, 1e-4, it.size))
        for i, (r, mk) in enumerate(zip(rates, mks))
    ]


def gen_six_series_linear(rng):
    """6 series linear — dense multi-series separation test."""
    x = np.linspace(0, 5, 30)
    out = []
    for i in range(6):
        slope = 0.5 + i * 0.4
        y = slope * x + rng.normal(0, 0.1, x.size)
        c = COLORS[i % len(COLORS)]
        mk = MARKERS[i % len(MARKERS)]
        out.append(_series(f"slope={slope:.1f}", mk, c, x, y))
    return out


def gen_dotted_intermediates(rng):
    """3 series: solid, dashed, dotted — dotted tends to be missed."""
    x = np.linspace(0, 8, 35)
    y1 = np.exp(-0.3 * x)
    y2 = np.exp(-0.5 * x)
    y3 = np.exp(-0.7 * x)
    return [
        _series("solid",  "o", COLORS[0], x, y1),
        _series("dashed", "s", COLORS[1], x, y2),
        _series("dotted", "^", COLORS[2], x, y3),
    ]


# Mode 8: Dense / overlapping markers
def gen_dense_overlap_2series(rng):
    """Two series with very similar y values — heavy overlap, separation by marker shape."""
    x = np.linspace(0, 10, 50)
    y1 = 2.0 * x + rng.normal(0, 0.5, x.size)
    y2 = 2.0 * x + 1.0 + rng.normal(0, 0.5, x.size)
    return [
        _series("A", "o", COLORS[0], x, y1),
        _series("B", "s", COLORS[1], x, y2),
    ]


def gen_dense_4series_scatter(rng):
    """4 overlapping scatter series, different shapes — dense-marker separation."""
    x = np.linspace(0, 8, 40)
    out = []
    for i in range(4):
        y = (i + 1) * x * 0.25 + rng.normal(0, 0.3 * (i + 1), x.size)
        out.append(_series(f"s{i+1}", MARKERS[i], COLORS[i], x, y))
    return out


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
    # ---- Bug-reproducer specs (judge failure modes) ----
    # Mode 1: Reversed / descending axes
    Spec("reversed_xaxis_spectro", "line_markers", (6, 4), "linear", "linear",
         "Binding energy (reversed x)", "Binding energy (eV)", "counts", True,
         gen_reversed_xaxis),
    Spec("reversed_yaxis_magnitude", "scatter", (6, 4), "linear", "linear",
         "Magnitude scale (reversed y)", "distance (kpc)", "magnitude", True,
         gen_reversed_yaxis),
    Spec("reversed_xaxis_2series", "line_markers", (7, 4), "linear", "linear",
         "Reversed x, two series", "wavenumber (cm⁻¹)", "intensity", True,
         gen_reversed_xaxis_2series),
    # Mode 3: Log axes (extra variants)
    Spec("semilogx_decay", "line_markers", (6, 4), "log", "linear",
         "Semilog-x decay", "x", "y", True, gen_semilogx_decay),
    Spec("loglog_4series", "line_markers", (7, 5), "log", "log",
         "Log-log 4 power laws", "x", "y", True, gen_loglog_multipower),
    Spec("semilogy_wide_range", "line_markers", (7, 4.5), "linear", "log",
         "Semilog-y wide range 2 series", "x", "y", True, gen_semilogy_negstart),
    # Mode 5: Negative tick values
    Spec("negative_xticks_sin", "line_markers", (6, 4), "linear", "linear",
         "Negative x ticks", "x", "sin(x)", True, gen_negative_x_ticks),
    Spec("negative_both_axes", "scatter", (6, 4), "linear", "linear",
         "Negative both axes (±0.10 range)", "x", "y", True,
         gen_negative_both_axes),
    Spec("negative_small_logy", "scatter", (6, 4), "linear", "log",
         "Negative x, small log-y values", "x", "y", True,
         gen_negative_logy_series),
    # Mode 6: Decimal & exponent tick labels
    Spec("decimal_ticks_01range", "scatter", (6, 4), "linear", "linear",
         "Decimal ticks [0,1]", "x", "y", True, gen_decimal_ticks),
    Spec("decimal_ticks_tight", "scatter", (6, 4), "linear", "linear",
         "Tight decimal ticks 0.10–0.30", "x", "y", True,
         gen_small_decimal_ticks),
    Spec("exponent_ticks_1e3", "scatter", (6, 4), "linear", "linear",
         "Exponent ticks (×10³)", "x", "y (×10³)", True,
         gen_exponent_ticks_1e3),
    Spec("exponent_ticks_large", "scatter", (6, 4), "linear", "linear",
         "Large x (millions)", "size", "sqrt(size)", True,
         gen_large_exponent_ticks),
    # Mode 7: Missing series — dashed / many series
    Spec("solid_dashed_same_color", "line_markers", (7, 4), "linear", "linear",
         "Same color solid+dashed", "x", "y", True, gen_solid_dashed_same_color),
    Spec("four_series_semilogy", "line_markers", (7, 5), "linear", "log",
         "4 series semilog-y", "iteration", "loss", True, gen_four_series_log),
    Spec("six_series_linear", "line_markers", (8, 5), "linear", "linear",
         "6 series linear", "x", "y", True, gen_six_series_linear),
    Spec("dotted_intermediates_3", "line_markers", (7, 4), "linear", "linear",
         "Solid + dashed + dotted", "x", "y", True, gen_dotted_intermediates),
    # Mode 8: Dense / overlapping markers
    Spec("dense_overlap_2series", "scatter", (7, 5), "linear", "linear",
         "Dense overlapping 2 series", "x", "y", True, gen_dense_overlap_2series),
    Spec("dense_4series_scatter", "scatter", (7, 5), "linear", "linear",
         "Dense 4 series scatter", "x", "y", True, gen_dense_4series_scatter),
]


# ---------------------------------------------------------------------------
# Custom-render fixtures: modes that need axis manipulation or special styles
# Each entry is a callable render_<name>(rng, outdir) -> dict following the
# same ground-truth schema as render().
# ---------------------------------------------------------------------------

@dataclass
class SpecCustom:
    name: str
    render_fn: Callable  # (rng, outdir) -> dict


def _make_gt(name, outdir, ax, series_list, chart_type, xscale, yscale,
             title, xlabel, ylabel):
    """Build a flat ground-truth dict from a live matplotlib Axes + series list."""
    xlim = ax.get_xlim()
    ylim = ax.get_ylim()

    def visible_ticks(ticks, lim):
        lo, hi = min(lim), max(lim)
        return [float(t) for t in ticks if lo <= t <= hi]

    pdf_path = outdir / f"{name}.pdf"
    eps_path = outdir / f"{name}.eps"
    return {
        "name": name,
        "pdf": pdf_path.name,
        "eps": eps_path.name,
        "chart_type": chart_type,
        "figsize_in": list(ax.get_figure().get_size_inches()),
        "title": title,
        "x_axis": {"label": xlabel, "scale": xscale,
                   "lim": [float(xlim[0]), float(xlim[1])]},
        "y_axis": {"label": ylabel, "scale": yscale,
                   "lim": [float(ylim[0]), float(ylim[1])]},
        "xticks": visible_ticks(ax.get_xticks(), xlim),
        "yticks": visible_ticks(ax.get_yticks(), ylim),
        "series": [
            {"label": s.label, "marker": s.marker,
             "color": [float(c) for c in s.color],
             "x": s.x.tolist(), "y": s.y.tolist()}
            for s in series_list
        ],
    }


def _save_and_close(fig, outdir, name, gt):
    fig.savefig(outdir / f"{name}.pdf")
    fig.savefig(outdir / f"{name}.eps")
    json_path = outdir / f"{name}.json"
    json_path.write_text(json.dumps(gt, indent=2))
    plt.close(fig)
    return gt


# ---- Mode 1 (custom): reversed x-axis drawn with inverted limits ----

def render_reversed_xaxis_inverted(rng, outdir):
    """Reversed x: tick VALUES also run 300→280 (inverted axis via set_xlim)."""
    name = "reversed_xaxis_invlim"
    x = np.linspace(300, 280, 25)
    y = 2.0 * (x - 280) + rng.normal(0, 1.0, x.size)
    series = [_series("signal", "o", COLORS[0], x, y)]

    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(x, y, marker="o", color=COLORS[0], label="signal")
    ax.set_xlim(305, 275)  # explicitly inverted limits
    ax.set_xlabel("Binding energy (eV)")
    ax.set_ylabel("counts")
    ax.set_title("Reversed x (inverted xlim)")
    ax.legend()
    fig.tight_layout()
    fig.canvas.draw()

    gt = _make_gt(name, outdir, ax, series, "line_markers",
                  "linear", "linear",
                  "Reversed x (inverted xlim)", "Binding energy (eV)", "counts")
    return _save_and_close(fig, outdir, name, gt)


# ---- Mode 2: Twin / dual-y axis ----
# dual-y produces two separate y-axes; ground truth stores BOTH series with
# their correct data-space y values (parser needs to detect and split them).

def render_twinx_linear(rng, outdir):
    """Dual y-axis: left axis [0,1] temperature, right axis [0,100] pressure."""
    name = "twinx_linear"
    x = np.linspace(0, 10, 20)
    y_temp = np.sin(x * 0.5) + rng.normal(0, 0.05, x.size)
    y_pres = 50 + 40 * np.cos(x * 0.4) + rng.normal(0, 1.0, x.size)

    series_left  = _series("temperature", "o", COLORS[0], x, y_temp)
    series_right = _series("pressure",    "s", COLORS[1], x, y_pres)

    fig, ax1 = plt.subplots(figsize=(7, 4))
    ax2 = ax1.twinx()

    ax1.plot(x, y_temp, marker="o", color=COLORS[0], label="temperature")
    ax2.plot(x, y_pres, marker="s", color=COLORS[1], label="pressure")
    ax1.set_xlabel("time (s)")
    ax1.set_ylabel("temperature (°C)")
    ax2.set_ylabel("pressure (Pa)")
    ax1.set_title("Dual y-axis (linear)")
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2)
    fig.tight_layout()
    fig.canvas.draw()

    # Ground truth: record both series with their own y values (data space).
    # We embed both in a single "series" list; a correct parser would split by axis.
    gt = {
        "name": name,
        "pdf": f"{name}.pdf",
        "eps": f"{name}.eps",
        "chart_type": "line_markers",
        "figsize_in": list(fig.get_size_inches()),
        "title": "Dual y-axis (linear)",
        "x_axis": {"label": "time (s)", "scale": "linear",
                   "lim": list(ax1.get_xlim())},
        "y_axis": {"label": "temperature (°C)", "scale": "linear",
                   "lim": list(ax1.get_ylim())},
        "y2_axis": {"label": "pressure (Pa)", "scale": "linear",
                    "lim": list(ax2.get_ylim())},
        "xticks": [float(t) for t in ax1.get_xticks()
                   if min(ax1.get_xlim()) <= t <= max(ax1.get_xlim())],
        "yticks": [float(t) for t in ax1.get_yticks()
                   if min(ax1.get_ylim()) <= t <= max(ax1.get_ylim())],
        "series": [
            {"label": "temperature", "marker": "o",
             "color": list(COLORS[0]),
             "x": x.tolist(), "y": y_temp.tolist(), "axis": "left"},
            {"label": "pressure", "marker": "s",
             "color": list(COLORS[1]),
             "x": x.tolist(), "y": y_pres.tolist(), "axis": "right"},
        ],
    }
    return _save_and_close(fig, outdir, name, gt)


def render_twinx_loglinear(rng, outdir):
    """Dual y-axis: left axis log-scale, right axis linear — harder calibration."""
    name = "twinx_log_linear"
    x = np.linspace(0, 8, 20)
    y_loss = 1.0 * 0.88**x + rng.uniform(0, 5e-3, x.size)
    y_acc  = 50 + 45 * (1 - np.exp(-0.5 * x)) + rng.normal(0, 0.5, x.size)
    y_loss = np.clip(y_loss, 1e-4, None)

    series_left  = _series("loss", "o", COLORS[0], x, y_loss)
    series_right = _series("accuracy", "^", COLORS[2], x, y_acc)

    fig, ax1 = plt.subplots(figsize=(7, 4.5))
    ax2 = ax1.twinx()
    ax1.semilogy(x, y_loss, marker="o", color=COLORS[0], label="loss")
    ax2.plot(x, y_acc, marker="^", color=COLORS[2], label="accuracy (%)")
    ax1.set_xlabel("epoch")
    ax1.set_ylabel("loss (log)")
    ax2.set_ylabel("accuracy (%)")
    ax1.set_title("Dual y-axis: log-loss + linear accuracy")
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="center right")
    fig.tight_layout()
    fig.canvas.draw()

    gt = {
        "name": name,
        "pdf": f"{name}.pdf",
        "eps": f"{name}.eps",
        "chart_type": "line_markers",
        "figsize_in": list(fig.get_size_inches()),
        "title": "Dual y-axis: log-loss + linear accuracy",
        "x_axis": {"label": "epoch", "scale": "linear",
                   "lim": list(ax1.get_xlim())},
        "y_axis": {"label": "loss (log)", "scale": "log",
                   "lim": list(ax1.get_ylim())},
        "y2_axis": {"label": "accuracy (%)", "scale": "linear",
                    "lim": list(ax2.get_ylim())},
        "xticks": [float(t) for t in ax1.get_xticks()
                   if min(ax1.get_xlim()) <= t <= max(ax1.get_xlim())],
        "yticks": [float(t) for t in ax1.get_yticks()
                   if min(ax1.get_ylim()) <= t <= max(ax1.get_ylim())],
        "series": [
            {"label": "loss", "marker": "o",
             "color": list(COLORS[0]),
             "x": x.tolist(), "y": y_loss.tolist(), "axis": "left"},
            {"label": "accuracy", "marker": "^",
             "color": list(COLORS[2]),
             "x": x.tolist(), "y": y_acc.tolist(), "axis": "right"},
        ],
    }
    return _save_and_close(fig, outdir, name, gt)


# ---- Mode 4: Broken axis ("//") ----
# Uses matplotlib's broken-axis pattern (two subplots with spines hidden).
# The ground-truth JSON uses the MULTI-panel schema (one panel per segment).

def render_broken_yaxis(rng, outdir):
    """Broken y-axis: lower segment [0,5], upper segment [20,25], '//' gap."""
    name = "broken_yaxis"
    x = np.linspace(0, 10, 25)
    y_low  = 0.4 * x + rng.normal(0, 0.15, x.size)          # fits in [0,5]
    y_high = 20.5 + 0.35 * x + rng.normal(0, 0.1, x.size)   # fits in [20,25]

    fig, (ax_top, ax_bot) = plt.subplots(2, 1, figsize=(6, 5),
                                          sharex=True, gridspec_kw={"hspace": 0.05})

    ax_top.plot(x, y_high, marker="o", color=COLORS[0], label="high range")
    ax_bot.plot(x, y_low,  marker="o", color=COLORS[0])

    # broken-axis convention: limit each panel to its range
    ax_top.set_ylim(19.8, 26.0)
    ax_bot.set_ylim(-0.5, 5.5)

    # hide spines between the panels
    ax_top.spines["bottom"].set_visible(False)
    ax_bot.spines["top"].set_visible(False)
    ax_top.tick_params(labelbottom=False)

    # diagonal '//' marks
    d = 0.015
    kwargs = dict(transform=ax_top.transAxes, color="k", clip_on=False, linewidth=1)
    ax_top.plot((-d, +d), (-d, +d), **kwargs)
    ax_top.plot((1 - d, 1 + d), (-d, +d), **kwargs)
    kwargs.update(transform=ax_bot.transAxes)
    ax_bot.plot((-d, +d), (1 - d, 1 + d), **kwargs)
    ax_bot.plot((1 - d, 1 + d), (1 - d, 1 + d), **kwargs)

    ax_top.set_title("Broken y-axis")
    ax_bot.set_xlabel("x")
    ax_top.legend()
    fig.tight_layout()
    fig.canvas.draw()

    def vticks(ax):
        xlim = ax.get_xlim(); ylim = ax.get_ylim()
        return ([float(t) for t in ax.get_xticks()
                 if min(xlim) <= t <= max(xlim)],
                [float(t) for t in ax.get_yticks()
                 if min(ylim) <= t <= max(ylim)])

    xt_top, yt_top = vticks(ax_top)
    xt_bot, yt_bot = vticks(ax_bot)

    gt = {
        "name": name,
        "pdf": f"{name}.pdf",
        "eps": f"{name}.eps",
        "figsize_in": list(fig.get_size_inches()),
        "n_panels": 2,
        "grid": [2, 1],
        "shared_x": True,
        "shared_y": False,
        "broken_axis": "y",
        "panels": [
            {
                "index": 0, "row": 0, "col": 0,
                "chart_type": "line_markers",
                "title": "high segment",
                "x_axis": {"label": None, "scale": "linear",
                           "lim": list(ax_top.get_xlim())},
                "y_axis": {"label": None, "scale": "linear",
                           "lim": list(ax_top.get_ylim())},
                "xticks": xt_top, "yticks": yt_top,
                "series": [{"label": "high range", "marker": "o",
                             "color": list(COLORS[0]),
                             "x": x.tolist(), "y": y_high.tolist()}],
            },
            {
                "index": 1, "row": 1, "col": 0,
                "chart_type": "line_markers",
                "title": None,
                "x_axis": {"label": "x", "scale": "linear",
                           "lim": list(ax_bot.get_xlim())},
                "y_axis": {"label": None, "scale": "linear",
                           "lim": list(ax_bot.get_ylim())},
                "xticks": xt_bot, "yticks": yt_bot,
                "series": [{"label": "low range", "marker": "o",
                             "color": list(COLORS[0]),
                             "x": x.tolist(), "y": y_low.tolist()}],
            },
        ],
    }
    return _save_and_close(fig, outdir, name, gt)


def render_broken_xaxis(rng, outdir):
    """Broken x-axis: left segment [0,2], right segment [8,10], '//' gap."""
    name = "broken_xaxis"
    x_left  = np.linspace(0, 2, 12)
    x_right = np.linspace(8, 10, 12)
    y_left  = np.exp(-x_left)  + rng.normal(0, 0.01, x_left.size)
    y_right = np.exp(-x_right) + rng.normal(0, 0.005, x_right.size)

    fig, (ax_l, ax_r) = plt.subplots(1, 2, figsize=(8, 4),
                                      sharey=True, gridspec_kw={"wspace": 0.05})
    for ax, xv, yv in [(ax_l, x_left, y_left), (ax_r, x_right, y_right)]:
        ax.plot(xv, yv, marker="o", color=COLORS[0], label="exp(-x)")

    ax_l.spines["right"].set_visible(False)
    ax_r.spines["left"].set_visible(False)
    ax_r.tick_params(labelleft=False)
    ax_r.yaxis.set_tick_params(which="both", left=False)

    d = 0.025
    kwargs = dict(transform=ax_l.transAxes, color="k", clip_on=False, linewidth=1)
    ax_l.plot((1 - d, 1 + d), (-d, +d), **kwargs)
    ax_l.plot((1 - d, 1 + d), (1 - d, 1 + d), **kwargs)
    kwargs.update(transform=ax_r.transAxes)
    ax_r.plot((-d, +d), (-d, +d), **kwargs)
    ax_r.plot((-d, +d), (1 - d, 1 + d), **kwargs)

    ax_l.set_title("Broken x-axis")
    ax_l.set_xlabel("x")
    ax_l.set_ylabel("exp(-x)")
    ax_l.legend()
    fig.tight_layout()
    fig.canvas.draw()

    def vticks(ax):
        xlim = ax.get_xlim(); ylim = ax.get_ylim()
        return ([float(t) for t in ax.get_xticks()
                 if min(xlim) <= t <= max(xlim)],
                [float(t) for t in ax.get_yticks()
                 if min(ylim) <= t <= max(ylim)])

    xt_l, yt_l = vticks(ax_l)
    xt_r, yt_r = vticks(ax_r)

    gt = {
        "name": name,
        "pdf": f"{name}.pdf",
        "eps": f"{name}.eps",
        "figsize_in": list(fig.get_size_inches()),
        "n_panels": 2,
        "grid": [1, 2],
        "shared_x": False,
        "shared_y": True,
        "broken_axis": "x",
        "panels": [
            {
                "index": 0, "row": 0, "col": 0,
                "chart_type": "line_markers",
                "title": "left segment",
                "x_axis": {"label": "x", "scale": "linear",
                           "lim": list(ax_l.get_xlim())},
                "y_axis": {"label": "exp(-x)", "scale": "linear",
                           "lim": list(ax_l.get_ylim())},
                "xticks": xt_l, "yticks": yt_l,
                "series": [{"label": "left", "marker": "o",
                             "color": list(COLORS[0]),
                             "x": x_left.tolist(), "y": y_left.tolist()}],
            },
            {
                "index": 1, "row": 0, "col": 1,
                "chart_type": "line_markers",
                "title": "right segment",
                "x_axis": {"label": "x", "scale": "linear",
                           "lim": list(ax_r.get_xlim())},
                "y_axis": {"label": None, "scale": "linear",
                           "lim": list(ax_r.get_ylim())},
                "xticks": xt_r, "yticks": yt_r,
                "series": [{"label": "right", "marker": "o",
                             "color": list(COLORS[0]),
                             "x": x_right.tolist(), "y": y_right.tolist()}],
            },
        ],
    }
    return _save_and_close(fig, outdir, name, gt)


# ---- Mode 7 (custom): dashed / dotted line styles ----
# Standard render() only uses linestyle="-". These use explicit dashes/dots.

def render_dashed_series(rng, outdir):
    """Explicit dashed-line series alongside a solid one — same color."""
    name = "dashed_same_color"
    x = np.linspace(0, 10, 40)
    y1 = np.sin(x)
    y2 = np.sin(x) * 0.7 + 0.5

    s1 = _series("solid",  "o", COLORS[0], x, y1)
    s2 = _series("dashed", "o", COLORS[0], x, y2)

    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(x, y1, marker="o", color=COLORS[0], linestyle="-",  label="solid")
    ax.plot(x, y2, marker="o", color=COLORS[0], linestyle="--", label="dashed")
    ax.set_title("Same color: solid + dashed")
    ax.set_xlabel("x"); ax.set_ylabel("y")
    ax.legend()
    fig.tight_layout(); fig.canvas.draw()

    gt = _make_gt(name, outdir, ax, [s1, s2], "line_markers",
                  "linear", "linear",
                  "Same color: solid + dashed", "x", "y")
    return _save_and_close(fig, outdir, name, gt)


def render_dotted_series(rng, outdir):
    """Three curves: solid / dashed / dotted — dotted tends to be dropped."""
    name = "dotted_3styles"
    x = np.linspace(0, 8, 35)
    y1 = np.exp(-0.3 * x)
    y2 = np.exp(-0.5 * x)
    y3 = np.exp(-0.7 * x)
    s1 = _series("solid",  "o", COLORS[0], x, y1)
    s2 = _series("dashed", "s", COLORS[1], x, y2)
    s3 = _series("dotted", "^", COLORS[2], x, y3)

    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(x, y1, marker="o", color=COLORS[0], linestyle="-",  label="solid")
    ax.plot(x, y2, marker="s", color=COLORS[1], linestyle="--", label="dashed")
    ax.plot(x, y3, marker="^", color=COLORS[2], linestyle=":",  label="dotted")
    ax.set_title("Three line styles: solid / dashed / dotted")
    ax.set_xlabel("x"); ax.set_ylabel("y")
    ax.legend()
    fig.tight_layout(); fig.canvas.draw()

    gt = _make_gt(name, outdir, ax, [s1, s2, s3], "line_markers",
                  "linear", "linear",
                  "Three line styles: solid / dashed / dotted", "x", "y")
    return _save_and_close(fig, outdir, name, gt)


def render_many_dashed_series(rng, outdir):
    """4 dashed series on semilog-y — many missed in judge reports."""
    name = "four_dashed_semilogy"
    it = np.arange(0, 40)
    rates = [0.95, 0.90, 0.84, 0.78]
    styles = ["--", "--", "--", "--"]
    mks = ["o", "s", "^", "D"]

    series_list = []
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.set_yscale("log")
    for i, (r, ls, mk) in enumerate(zip(rates, styles, mks)):
        y = r**it + rng.uniform(0, 1e-4, it.size)
        ax.plot(it, y, marker=mk, color=COLORS[i], linestyle=ls,
                label=f"r={r}")
        series_list.append(_series(f"r={r}", mk, COLORS[i], it.astype(float), y))
    ax.set_title("4 dashed series (semilog-y)")
    ax.set_xlabel("iteration"); ax.set_ylabel("loss")
    ax.legend(); fig.tight_layout(); fig.canvas.draw()

    gt = _make_gt(name, outdir, ax, series_list, "line_markers",
                  "linear", "log",
                  "4 dashed series (semilog-y)", "iteration", "loss")
    return _save_and_close(fig, outdir, name, gt)


# ---- Mode 9: Multi-panel shared-axis (calibration borrowing) ----

def render_shared_xaxis_logy(rng, outdir):
    """2×1 subplots sharing x-axis; bottom panel is semilog-y."""
    name = "shared_xaxis_logy_bottom"
    x = np.linspace(0, 6, 30)
    y_lin = np.sin(x) + rng.normal(0, 0.05, x.size)
    y_log = 1.0 * 0.7**x + rng.uniform(0, 1e-3, x.size)
    y_log = np.clip(y_log, 1e-4, None)

    fig, (ax_top, ax_bot) = plt.subplots(2, 1, figsize=(6, 6),
                                          sharex=True,
                                          gridspec_kw={"hspace": 0.1})
    ax_top.plot(x, y_lin, marker="o", color=COLORS[0], label="linear")
    ax_bot.semilogy(x, y_log, marker="s", color=COLORS[1], label="log decay")
    ax_top.set_ylabel("sin(x)")
    ax_bot.set_ylabel("decay (log)")
    ax_bot.set_xlabel("x")
    ax_top.set_title("Shared x: linear top / semilog-y bottom")
    ax_top.legend(); ax_bot.legend()
    fig.tight_layout(); fig.canvas.draw()

    def vticks(ax):
        xlim = ax.get_xlim(); ylim = ax.get_ylim()
        return ([float(t) for t in ax.get_xticks()
                 if min(xlim) <= t <= max(xlim)],
                [float(t) for t in ax.get_yticks()
                 if min(ylim) <= t <= max(ylim)])

    xt_top, yt_top = vticks(ax_top)
    xt_bot, yt_bot = vticks(ax_bot)
    gt = {
        "name": name,
        "pdf": f"{name}.pdf",
        "eps": f"{name}.eps",
        "figsize_in": list(fig.get_size_inches()),
        "n_panels": 2,
        "grid": [2, 1],
        "shared_x": True,
        "shared_y": False,
        "panels": [
            {
                "index": 0, "row": 0, "col": 0,
                "chart_type": "line_markers",
                "title": "linear panel",
                "x_axis": {"label": None, "scale": "linear",
                           "lim": list(ax_top.get_xlim())},
                "y_axis": {"label": "sin(x)", "scale": "linear",
                           "lim": list(ax_top.get_ylim())},
                "xticks": xt_top, "yticks": yt_top,
                "series": [{"label": "linear", "marker": "o",
                             "color": list(COLORS[0]),
                             "x": x.tolist(), "y": y_lin.tolist()}],
            },
            {
                "index": 1, "row": 1, "col": 0,
                "chart_type": "line_markers",
                "title": None,
                "x_axis": {"label": "x", "scale": "linear",
                           "lim": list(ax_bot.get_xlim())},
                "y_axis": {"label": "decay (log)", "scale": "log",
                           "lim": list(ax_bot.get_ylim())},
                "xticks": xt_bot, "yticks": yt_bot,
                "series": [{"label": "log decay", "marker": "s",
                             "color": list(COLORS[1]),
                             "x": x.tolist(), "y": y_log.tolist()}],
            },
        ],
    }
    return _save_and_close(fig, outdir, name, gt)


def render_shared_xaxis_reversed(rng, outdir):
    """2×1 subplots sharing a REVERSED x-axis — hardest calibration case."""
    name = "shared_xaxis_reversed"
    x = np.linspace(300, 280, 20)  # descending
    y_top = 1.5 * (x - 280) + rng.normal(0, 0.5, x.size)
    y_bot = 0.5 * (x - 280) + rng.normal(0, 0.3, x.size)

    fig, (ax_top, ax_bot) = plt.subplots(2, 1, figsize=(6, 6),
                                          sharex=True,
                                          gridspec_kw={"hspace": 0.1})
    ax_top.plot(x, y_top, marker="o", color=COLORS[0], label="series A")
    ax_bot.plot(x, y_bot, marker="s", color=COLORS[1], label="series B")
    for ax in (ax_top, ax_bot):
        ax.set_xlim(305, 275)  # inverted
    ax_top.set_ylabel("A"); ax_bot.set_ylabel("B")
    ax_bot.set_xlabel("wavenumber (cm⁻¹)")
    ax_top.set_title("Shared reversed x-axis")
    ax_top.legend(); ax_bot.legend()
    fig.tight_layout(); fig.canvas.draw()

    def vticks(ax):
        xlim = ax.get_xlim(); ylim = ax.get_ylim()
        return ([float(t) for t in ax.get_xticks()
                 if min(xlim) <= t <= max(xlim)],
                [float(t) for t in ax.get_yticks()
                 if min(ylim) <= t <= max(ylim)])

    xt_top, yt_top = vticks(ax_top)
    xt_bot, yt_bot = vticks(ax_bot)
    gt = {
        "name": name,
        "pdf": f"{name}.pdf",
        "eps": f"{name}.eps",
        "figsize_in": list(fig.get_size_inches()),
        "n_panels": 2,
        "grid": [2, 1],
        "shared_x": True,
        "shared_y": False,
        "panels": [
            {
                "index": 0, "row": 0, "col": 0,
                "chart_type": "line_markers",
                "title": "top",
                "x_axis": {"label": None, "scale": "linear",
                           "lim": list(ax_top.get_xlim())},
                "y_axis": {"label": "A", "scale": "linear",
                           "lim": list(ax_top.get_ylim())},
                "xticks": xt_top, "yticks": yt_top,
                "series": [{"label": "series A", "marker": "o",
                             "color": list(COLORS[0]),
                             "x": x.tolist(), "y": y_top.tolist()}],
            },
            {
                "index": 1, "row": 1, "col": 0,
                "chart_type": "line_markers",
                "title": None,
                "x_axis": {"label": "wavenumber (cm⁻¹)", "scale": "linear",
                           "lim": list(ax_bot.get_xlim())},
                "y_axis": {"label": "B", "scale": "linear",
                           "lim": list(ax_bot.get_ylim())},
                "xticks": xt_bot, "yticks": yt_bot,
                "series": [{"label": "series B", "marker": "s",
                             "color": list(COLORS[1]),
                             "x": x.tolist(), "y": y_bot.tolist()}],
            },
        ],
    }
    return _save_and_close(fig, outdir, name, gt)


# Registry of all custom-render specs
CUSTOM_SPECS = [
    SpecCustom("reversed_xaxis_invlim",    render_reversed_xaxis_inverted),
    SpecCustom("twinx_linear",             render_twinx_linear),
    SpecCustom("twinx_log_linear",         render_twinx_loglinear),
    SpecCustom("broken_yaxis",             render_broken_yaxis),
    SpecCustom("broken_xaxis",             render_broken_xaxis),
    SpecCustom("dashed_same_color",        render_dashed_series),
    SpecCustom("dotted_3styles",           render_dotted_series),
    SpecCustom("four_dashed_semilogy",     render_many_dashed_series),
    SpecCustom("shared_xaxis_logy_bottom", render_shared_xaxis_logy),
    SpecCustom("shared_xaxis_reversed",    render_shared_xaxis_reversed),
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

    # Custom-render fixtures (special axis modes, dual-y, broken, dashed styles)
    custom_rows = []
    for spec in CUSTOM_SPECS:
        gt = spec.render_fn(rng, outdir)
        custom_rows.append(gt)

    print()
    header = f"{'name':<32} {'type':<14}"
    print(header)
    print("-" * (len(header) + 10))
    for gt in custom_rows:
        panels = f"{gt['n_panels']} panels" if "n_panels" in gt else gt.get("chart_type", "custom")
        print(f"{gt['name']:<32} {panels}")
    print(f"\nWrote {len(custom_rows)} custom-render fixtures (.pdf + .eps + .json) "
          f"to {outdir}/")


if __name__ == "__main__":
    main()
