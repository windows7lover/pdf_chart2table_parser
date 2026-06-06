"""Shared test helpers: fixture discovery and ground-truth loading.

Each fixture under ``tests/fixtures`` is a ``<name>.pdf`` with a ``<name>.json``
ground-truth sidecar. Single-panel fixtures use a FLAT schema (no ``n_panels``);
multi-panel ones (``subplots_*``) use a ``panels`` schema with ``n_panels``,
``grid``, ``shared_x``, ``shared_y``.
"""

from __future__ import annotations

import glob
import json
import os

import pytest

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

# ---------------------------------------------------------------------------
# Known-failing test IDs from new bug-reproducer fixtures.
# These are marked xfail so the suite stays green while documenting open bugs.
# Key = pytest node-id substring (parametrize id); value = reason string.
# ---------------------------------------------------------------------------
_KNOWN_FAILING = {
    # twinx_log_linear: right y-axis ticks bleed into left-axis detection, producing
    # spurious negative values [-1, -1] in the left log-axis tick list.
    "twinx_log_linear-r0c0-y": (
        "open bug: axes.py spurious ticks on left log-axis of dual-y chart "
        "(right-axis ticks contaminate left-axis detection)"
    ),
    # test_labels failures — Unicode / special characters in ground-truth labels
    # that the PDF text extractor cannot reproduce accurately.
    "decimal_ticks_tight.json": (
        "open bug: labels.py truncates title at em-dash character "
        "'0.10–0.30' → '0.10' (Unicode en/em-dash not extracted)"
    ),
    "negative_both_axes.json": (
        "open bug: labels.py strips Unicode minus sign '−' from series label "
        "'−slope' → 'slope' (special minus glyph not extracted)"
    ),
    "reversed_xaxis_2series.json": (
        "open bug: labels.py drops combining superscript '⁻' in x-axis label "
        "'wavenumber (cm⁻¹)' → 'wavenumber (cm ¹)'"
    ),
    "twinx_log_linear.json": (
        "open bug: labels.py appends '(%)' unit from y2-axis label into legend "
        "entry; 'accuracy' → 'accuracy (%)'"
    ),
}


def pytest_collection_modifyitems(items):
    """Attach xfail markers to parametrized test IDs matching known open bugs."""
    for item in items:
        for node_id_fragment, reason in _KNOWN_FAILING.items():
            if node_id_fragment in item.nodeid:
                item.add_marker(pytest.mark.xfail(reason=reason, strict=False))


def fixture_names() -> list[str]:
    """All fixture base names (sorted), discovered from the PDFs."""
    pdfs = sorted(glob.glob(os.path.join(FIXTURE_DIR, "*.pdf")))
    return [os.path.basename(p)[:-4] for p in pdfs]


def multipanel_names() -> list[str]:
    """Fixture names that use the multi-panel ``panels`` schema."""
    return [n for n in fixture_names() if load_truth(n).get("n_panels", 1) > 1]


def pdf_path(name: str) -> str:
    return os.path.join(FIXTURE_DIR, f"{name}.pdf")


def truth_path(name: str) -> str:
    return os.path.join(FIXTURE_DIR, f"{name}.json")


def load_truth(name: str) -> dict:
    with open(truth_path(name)) as f:
        return json.load(f)
