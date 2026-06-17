"""The warm parse server must change ONLY process lifecycle, never extraction.

A chart parsed through the warm server's request handler must produce a
``chart.json`` byte-identical to one produced by a direct ``parse_pdf`` call.
These run with ``PDFCHART_OCR=0`` (set session-wide in conftest) so no OCR model
is needed; an OCR-gated smoke test is skipped unless the engine is available.
"""

from __future__ import annotations

import importlib.util
import json
import os

import pytest

from conftest import pdf_path

# Load the script module by path (scripts/ is not an importable package).
_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts",
                       "warm_parse_server.py")
_spec = importlib.util.spec_from_file_location("warm_parse_server", _SCRIPT)
warm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(warm)


def _read_json(outroot, stem, name):
    return json.loads((outroot / stem / name).read_text())


def test_warm_handler_matches_direct_parse(tmp_path):
    """Same fixture via _handle (warm path) vs direct parse_pdf -> identical JSON."""
    from pdf_chart2table.cli import parse_pdf

    pdf = pdf_path("gaussian_clusters_3")
    stem = "gaussian_clusters_3"

    direct_out = tmp_path / "direct"
    warm_out = tmp_path / "warm"

    direct_rows = parse_pdf(pdf, str(direct_out))

    warm._warm()  # no-op for OCR (disabled), but exercises the warm path
    resp = warm._handle({"pdf": pdf, "outroot": str(warm_out)})

    assert resp["ok"], resp.get("error")
    assert resp["rows"] == len(direct_rows)

    direct_json = _read_json(direct_out, stem, "page1_chart1.json")
    warm_json = _read_json(warm_out, stem, "page1_chart1.json")
    assert warm_json == direct_json


def test_warm_handler_reports_error_not_raises(tmp_path):
    """A bad request yields ok:false, never an exception (server stays alive)."""
    resp = warm._handle({"pdf": str(tmp_path / "does_not_exist.pdf"),
                         "outroot": str(tmp_path / "out")})
    assert resp["ok"] is False
    assert "error" in resp


def test_serve_single_streams_responses(tmp_path, capsys):
    """The single-process server emits one JSON line per request, in order."""
    pdf = pdf_path("gaussian_clusters_3")
    reqs = [
        {"pdf": pdf, "outroot": str(tmp_path / "a")},
        {"pdf": pdf, "outroot": str(tmp_path / "b")},
    ]
    warm._serve_single(iter(reqs))
    lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert len(lines) == 2
    for line in lines:
        resp = json.loads(line)
        assert resp["ok"], resp.get("error")


@pytest.mark.skipif(
    os.environ.get("PDFCHART_OCR", "1") == "0",
    reason="OCR disabled; warm-up of the OCR engine not exercised",
)
def test_warm_loads_ocr_engine_when_enabled():
    """When OCR is on, _warm constructs the cached engine (or None if unavailable)."""
    warm._warm()
    from pdf_chart2table import ocr_backfill
    # _engine() caches in a module global; after _warm it must not be the
    # sentinel anymore (either a RapidOCR instance or None).
    assert ocr_backfill._ENGINE != "unset"
