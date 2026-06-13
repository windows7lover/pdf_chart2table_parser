"""Tests for ocr_backfill's detection-side logic (no OCR engine required).

The OCR engine itself is integration-tested separately; here we lock down the
deterministic gating that keeps it safe: tick-label subtraction, the numeric
filter, the "needs real letters" guard, and the disabled / no-engine no-op.
"""

from __future__ import annotations

import os

from pdf_chart2table import ocr_backfill as ob


class TestAssembleTitle:
    def _boxes(self, *pairs):
        # pairs: (text, conf, x)
        return [{"text": t, "conf": c, "x": x} for t, c, x in pairs]

    def test_subtracts_known_tick_labels(self):
        boxes = self._boxes(
            ("700", 1.0, 0), ("720", 1.0, 10), ("Wavelength", 1.0, 20),
            ("(nm)", 1.0, 40),
        )
        title = ob._assemble_title(boxes, tick_labels=["700", "720", "740"])
        assert title == "Wavelength (nm)"

    def test_drops_bare_numbers_even_if_not_a_known_tick(self):
        boxes = self._boxes(("125", 1.0, 0), ("Input power", 1.0, 10))
        assert ob._assemble_title(boxes, tick_labels=[]) == "Input power"

    def test_low_confidence_boxes_dropped(self):
        boxes = self._boxes(("xxxx", 0.3, 0), ("density", 0.95, 10))
        assert ob._assemble_title(boxes, tick_labels=[]) == "density"

    def test_cjk_hallucinations_dropped(self):
        # RapidOCR's Chinese model invents CJK on noise -> must be discarded.
        boxes = self._boxes(("旧邮", 0.9, 0), ("intensity", 0.95, 10))
        assert ob._assemble_title(boxes, tick_labels=[]) == "intensity"

    def test_fragmented_assembly_rejected(self):
        # Mostly tiny / symbol tokens (rotated-band mis-segmentation) -> None,
        # never emitted as a garbage title.
        boxes = self._boxes(("ce", 0.9, 0), ("-1/6/", 0.9, 5), ("K", 0.9, 10),
                            ("nc", 0.9, 15))
        assert ob._assemble_title(boxes, tick_labels=[]) is None

    def test_ordered_left_to_right(self):
        boxes = self._boxes(("(W)", 1.0, 50), ("Input", 1.0, 0), ("power", 1.0, 25))
        assert ob._assemble_title(boxes, tick_labels=[]) == "Input power (W)"

    def test_requires_letters_returns_none(self):
        # Only punctuation/number debris -> no title (never emit junk).
        assert ob._assemble_title(self._boxes(("-", 0.9, 0), ("5", 0.9, 10)), []) is None

    def test_empty_returns_none(self):
        assert ob._assemble_title([], ["1", "2"]) is None


class TestNumericFilter:
    def test_matches_numbers(self):
        for s in ("0", "-4", "1.5", "100", "1e-3", "2.0%", "+5"):
            assert ob._NUMERIC_RE.match(s), s

    def test_not_match_words(self):
        for s in ("nm", "B", "Pin", "10x", "k1"):
            assert not ob._NUMERIC_RE.match(s), s


class TestDisabledNoOp:
    def test_disabled_engine_is_none(self, monkeypatch):
        monkeypatch.setenv("PDFCHART_OCR", "0")
        monkeypatch.setattr(ob, "_ENGINE", "unset")
        assert ob._engine() is None

    def test_backfill_noop_without_engine(self, monkeypatch):
        monkeypatch.setattr(ob, "_ENGINE", None)  # force "no engine"
        rec = {"x_axis": {"pixel_range": [0, 10], "title": None},
               "y_axis": {"pixel_range": [0, 10], "title": None},
               "xticks": [], "yticks": []}
        assert ob.backfill(rec, fitz_page=None) == {}
        assert rec["x_axis"]["title"] is None  # untouched
