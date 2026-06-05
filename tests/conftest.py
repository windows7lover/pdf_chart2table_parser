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

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


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
