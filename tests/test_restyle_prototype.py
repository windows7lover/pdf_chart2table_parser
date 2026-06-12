"""Regression tests for the restyle-reconstruction helpers (scripts/).

Each test reproduces a specific bug found by inspecting reconstructions:
* 2010.12950: a LINEAR axis (700..860) was tagged 'log' by the extractor, so it
  rendered '7x10^2' -- _effective_scale must demote a sub-decade log axis.
* 2002.04278: tick labels like '10'/'x' matched the legend entry 'X (x100)'
  (norm 'xx100' contains '10') -- _label_match needs a length floor.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from render_restyle_prototype import (  # noqa: E402
    _effective_scale, _is_italic, _label_match, _norm)


def test_log_axis_under_one_decade_demoted_to_linear():
    ax = {"scale": "log",
          "ticks": [{"value": v} for v in (700, 720, 740, 760, 780, 800)]}
    assert _effective_scale(ax) == "linear"


def test_genuine_log_axis_kept():
    ax = {"scale": "log", "ticks": [{"value": v} for v in (0.1, 1, 10, 100, 1000)]}
    assert _effective_scale(ax) == "log"


def test_linear_axis_unchanged():
    ax = {"scale": "linear", "ticks": [{"value": v} for v in (0, 10, 20)]}
    assert _effective_scale(ax) == "linear"


def test_short_tick_label_does_not_match_long_legend_entry():
    label = _norm("X (x100)")          # -> 'xx100'
    assert not _label_match(_norm("10"), label)
    assert not _label_match(_norm("x"), label)
    assert not _label_match(_norm("0"), label)


def test_real_legend_chunks_match():
    label = _norm("X (x100)")
    assert _label_match(_norm("(x100)"), label)      # substantial chunk
    assert _label_match(_norm("Sideband"), _norm("Sideband"))


def test_italic_detected_from_flag_and_font_name():
    assert _is_italic({"flags": 2, "font": "Times"})            # italic flag bit
    assert _is_italic({"flags": 0, "font": "NimbusRomNo9L-Ital"})
    assert _is_italic({"flags": 0, "font": "CMMI10-Oblique"})
    assert not _is_italic({"flags": 0, "font": "Helvetica"})
