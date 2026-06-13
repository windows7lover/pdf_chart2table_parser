"""Tests for tick_ocr's spacing-outlier detector + fit (no OCR / no corpus).

The detector flags a tick whose VALUE breaks the regular pixel<->value spacing
(linear: equal step; log: equal log10 step) -- the signature of a mis-read /
merged tick label. OCR re-reading itself is integration-tested separately; here
we lock down the deterministic detection that gates it.
"""

from __future__ import annotations

from pdf_chart2table import tick_ocr
from pdf_chart2table.model import Tick


def _ticks(pairs):
    return [Tick(pixel=px, value=v, label=str(v)) for px, v in pairs]


class TestSpacingOutliers:
    def test_clean_linear_no_outlier(self):
        ticks = _ticks([(10, 0), (20, 50), (30, 100), (40, 150), (50, 200)])
        assert tick_ocr.spacing_outliers(ticks, "linear") == []

    def test_clean_log_no_outlier(self):
        ticks = _ticks([(10, 1), (20, 10), (30, 100), (40, 1000)])
        assert tick_ocr.spacing_outliers(ticks, "log") == []

    def test_merged_label_flagged_linear(self):
        # '250'+'280' merged into 250280 at the 250 tick (px=60).
        ticks = _ticks([(10, 0), (20, 50), (30, 100), (40, 150), (50, 200),
                        (60, 250280)])
        assert tick_ocr.spacing_outliers(ticks, "linear") == [5]

    def test_merged_minus_flagged_linear(self):
        # '-8'+'0' merged into -80 among 8,6,4,2,0,-2,-4,-6,(-80).
        vals = [8, 6, 4, 2, 0, -2, -4, -6, -80]
        ticks = _ticks([(10 * (i + 1), v) for i, v in enumerate(vals)])
        assert tick_ocr.spacing_outliers(ticks, "linear") == [8]

    def test_log_merged_decade_flagged(self):
        # a merged value far off the log line.
        ticks = _ticks([(10, 1), (20, 10), (30, 100), (40, 100010000)])
        assert tick_ocr.spacing_outliers(ticks, "log") == [3]

    def test_too_few_ticks(self):
        assert tick_ocr.spacing_outliers(_ticks([(10, 0), (20, 50)]), "linear") == []

    def test_mostly_outliers_bails(self):
        # If a majority of ticks disagree with any line, the fit is untrustworthy
        # -> flag nothing (don't "correct" most of the axis).
        ticks = _ticks([(10, 0), (20, 1000), (30, 2), (40, 1500), (50, 4)])
        assert tick_ocr.spacing_outliers(ticks, "linear") == []


class TestFits:
    def test_fit_accepts_inline_value_rejects_outlier(self):
        ticks = _ticks([(10, 0), (20, 50), (30, 100), (40, 150), (50, 200)])
        fit = tick_ocr._robust_fit(ticks, "linear")
        assert tick_ocr._fits(fit, 60, 250, "linear")          # the un-merged value
        assert not tick_ocr._fits(fit, 60, 250280, "linear")   # the merged value
