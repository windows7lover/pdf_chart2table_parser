"""Tests for the output store + CLI (Agent C).

These are robust to the optional ``extract`` / ``labels`` modules being absent:
series may be empty and titles/captions null, which the assertions allow for.
"""

from __future__ import annotations

import csv
import json
import os

import fitz
import pytest

from conftest import pdf_path
from pdf_chart2table import io_store, pdf_vector
from pdf_chart2table.calibrate import calibrate_panels
from pdf_chart2table.cli import parse_pdf
from pdf_chart2table.model import Axis, Series, Tick


# --------------------------------------------------------------------------
# Hand-built record: JSON round-trip + markers.csv columns
# --------------------------------------------------------------------------

def _hand_record():
    x_axis = Axis(
        title="time",
        scale="linear",
        pixel_range=(10.0, 100.0),
        data_range=(0.0, 9.0),
        ticks=[Tick(10.0, 0.0, "0"), Tick(100.0, 9.0, "9")],
        calibration={"scale": "linear", "a": 0.1, "b": -1.0, "r2": 1.0},
    )
    y_axis = Axis(
        title="value",
        scale="log",
        pixel_range=(5.0, 95.0),
        data_range=(1.0, 1000.0),
        ticks=[Tick(5.0, 1.0, "1"), Tick(95.0, 1000.0, "1000")],
        calibration={"scale": "log", "a": 0.03, "b": 0.0, "r2": 1.0},
    )
    series = [Series(
        label="A", marker="circle", color=(0.1, 0.2, 0.3),
        points=[{"x": 1.0, "y": 10.0, "x_px": 20.0, "y_px": 50.0},
                {"x": 2.0, "y": 100.0, "x_px": 30.0, "y_px": 80.0}],
    )]
    return io_store.chart_to_record(
        pdf="some.pdf", page=2, region_bbox=(1, 2, 3, 4),
        x_axis=x_axis, y_axis=y_axis, series=series,
        title=None, caption=None, confidence=0.9,
    )


def test_record_json_roundtrips():
    rec = _hand_record()
    s = json.dumps(rec)
    back = json.loads(s)
    assert back["source"]["region_bbox"] == [1, 2, 3, 4]
    assert back["x_axis"]["scale"] == "linear"
    assert back["y_axis"]["scale"] == "log"
    assert [t["value"] for t in back["xticks"]] == [0.0, 9.0]
    assert back["series"][0]["label"] == "A"
    assert back["series"][0]["points"][0]["x_px"] == 20.0


def test_axis_record_multiplier_and_tick_consistency():
    # Axis-scale multiplier (offset text) + spacing-pattern flag are serialized;
    # both default to their neutral values on a plain Axis.
    ax = Axis(scale="linear", multiplier=1e5, tick_consistency="suspect")
    rec = io_store._axis_record(ax)
    assert rec["multiplier"] == 1e5
    assert rec["tick_consistency"] == "suspect"
    rec_default = io_store._axis_record(Axis())
    assert rec_default["multiplier"] == 1.0
    assert rec_default["tick_consistency"] is None


def test_markers_csv_columns(tmp_path):
    rec = _hand_record()
    csv_path = tmp_path / "m.csv"
    io_store._write_markers_csv(rec, str(csv_path))
    with open(csv_path) as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["series", "x", "y", "x_px", "y_px"]
    assert len(rows) == 1 + 2  # header + 2 points
    assert rows[1][0] == "A"


# --------------------------------------------------------------------------
# Lossless vector crop of a real fixture: opens in fitz with drawings > 0
# --------------------------------------------------------------------------

def test_vector_crop_has_drawings(tmp_path):
    pdf = pdf_path("gaussian_clusters_3")
    pages = pdf_vector.load_pdf(pdf)
    regions = __import__(
        "pdf_chart2table.plot_region", fromlist=["detect_regions"]
    ).detect_regions(pages[0].paths, pages[0].texts,
                     pages[0].width, pages[0].height)
    assert regions, "fixture should yield a region"
    out_pdf = tmp_path / "crop.pdf"
    io_store.crop_region_pdf(pdf, 0, regions[0].bbox, str(out_pdf))

    doc = fitz.open(str(out_pdf))
    page = doc[0]
    assert len(page.get_drawings()) > 0
    pix = page.get_pixmap()
    assert pix.width > 0 and pix.height > 0
    doc.close()


# --------------------------------------------------------------------------
# Skip stub for an uncalibratable case
# --------------------------------------------------------------------------

def test_write_skip_stub(tmp_path):
    source = {"pdf": "x.pdf", "page": 0, "region_bbox": [0, 0, 1, 1]}
    row = io_store.write_skip("no axis calibration", source, str(tmp_path), 1, 1)
    stub_file = tmp_path / "page1_chart1.skip.json"
    assert stub_file.exists()
    stub = json.loads(stub_file.read_text())
    assert stub["status"] == "skipped"
    assert stub["reason"] == "no axis calibration"
    assert row["status"] == "skipped" and row["n_series"] == 0


# --------------------------------------------------------------------------
# End-to-end parse of a fixture + manifest
# --------------------------------------------------------------------------

def test_parse_pdf_end_to_end(tmp_path):
    pdf = pdf_path("gaussian_clusters_3")
    rows = parse_pdf(pdf, str(tmp_path))
    assert rows, "should produce at least one chart row"
    io_store.write_manifest(rows, str(tmp_path / "gaussian_clusters_3"))

    outdir = tmp_path / "gaussian_clusters_3"
    # An extracted chart writes json + markers.csv + crop pdf + svg.
    extracted = [r for r in rows if r["status"] == "extracted"]
    assert extracted, "calibratable fixture should extract"
    assert (outdir / "page1_chart1.json").exists()
    assert (outdir / "page1_chart1.markers.csv").exists()
    assert (outdir / "page1_chart1.pdf").exists()
    assert (outdir / "page1_chart1.svg").exists()

    rec = json.loads((outdir / "page1_chart1.json").read_text())
    assert rec["x_axis"] is not None and rec["x_axis"]["calibration"] is not None
    assert rec["y_axis"] is not None
    assert len(rec["xticks"]) >= 2
    assert rec["source"]["region_bbox"]
    assert rec["vector_file"] == "page1_chart1.svg"

    manifest = outdir / "manifest.csv"
    assert manifest.exists()
    with open(manifest) as f:
        header = next(csv.reader(f))
    assert header == ["file", "page", "chart", "status", "reason",
                      "n_series", "n_points", "confidence"]


def test_write_manifest_columns(tmp_path):
    rows = [
        {"file": "a.pdf", "page": 0, "chart": 1, "status": "extracted",
         "reason": None, "n_series": 2, "n_points": 50, "confidence": 1.0},
        {"file": "a.pdf", "page": 1, "chart": 1, "status": "skipped",
         "reason": "no axis calibration", "n_series": 0, "n_points": 0,
         "confidence": None},
    ]
    path = io_store.write_manifest(rows, str(tmp_path))
    with open(path) as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == [
            "file", "page", "chart", "status", "reason",
            "n_series", "n_points", "confidence"]
        data = list(reader)
    assert len(data) == 2
    assert data[1]["status"] == "skipped"
