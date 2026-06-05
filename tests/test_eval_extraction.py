"""Tests for scripts/eval_extraction.py self-test path.

Synthesizing a perfect prediction from a fixture's truth must yield ~0 error and
report PASS. We import the script's functions directly (adding scripts/ to the
path); the subprocess invocation is exercised once as a smoke test of the CLI.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from conftest import load_truth, truth_path

_SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, os.path.abspath(_SCRIPTS))

import eval_extraction as ee  # noqa: E402

# One linear, one semilog-y, one log-log fixture.
REPRESENTATIVE = [
    "linear_scatter_1series",
    "exp_decay_semilogy",
    "power_law_loglog",
]


@pytest.mark.parametrize("name", REPRESENTATIVE)
def test_self_test_perfect_pred_passes(name):
    truth = load_truth(name)
    pred = ee.synth_pred_from_truth(truth)
    assert ee.evaluate(pred, truth, tol=0.01) is True


@pytest.mark.parametrize("name", REPRESENTATIVE)
def test_self_test_zero_error(name):
    """Perfect prediction => max per-point error is ~0 for every series."""
    truth = load_truth(name)
    pred = ee.synth_pred_from_truth(truth)
    fx = ee._axis_transform(truth["x_axis"]["scale"])
    fy = ee._axis_transform(truth["y_axis"]["scale"])
    import numpy as np

    all_x = np.concatenate([ee._truth_arrays(s)[0] for s in truth["series"]])
    all_y = np.concatenate([ee._truth_arrays(s)[1] for s in truth["series"]])
    x_range = ee._axis_range(fx(all_x))
    y_range = ee._axis_range(fy(all_y))
    for ts, ps in zip(truth["series"], pred["series"]):
        rel = ee.series_errors(ts, ps, fx, fy, x_range, y_range)
        assert float(np.nanmax(rel)) < 1e-9


def test_self_test_cli_exit_zero():
    """The --self-test CLI exits 0 on a perfect prediction."""
    proc = subprocess.run(
        [sys.executable, os.path.join(_SCRIPTS, "eval_extraction.py"),
         "--self-test", truth_path("linear_scatter_1series")],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "RESULT: PASS" in proc.stdout
