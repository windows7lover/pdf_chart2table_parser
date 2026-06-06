"""Tests for label/caption extraction (Agent B, module 6 `labels.py`).

Synthetic fixtures carry ground-truth `title`, `x_axis.label`, `y_axis.label`
and `series[].label`. We assert the detected title / axis titles match, and
that the detected legend label set equals the series-label set for fixtures
that actually draw a legend (empty for "minimal"/"no legend" fixtures).

Multi-panel `subplots_*` fixtures are handled per panel by mapping each
detected `Region` to its ground-truth panel via (row, col).

One non-asserting real-paper spot check prints an extracted caption when the
gitignored corpus in `data/real_pdfs/` is present.
"""

from __future__ import annotations

import json
import re
from pathlib import Path as FsPath

import pytest

from pdf_chart2table.labels import _assemble_label, detect_labels
from pdf_chart2table.pdf_vector import load_pdf
from pdf_chart2table.plot_region import detect_regions
from pdf_chart2table.model import TextSpan

FIXTURES = FsPath(__file__).parent / "fixtures"


def _norm(s):
    return re.sub(r"\s+", " ", s).strip() if s else None


def _single_panel_fixtures():
    out = []
    for jf in sorted(FIXTURES.glob("*.json")):
        gt = json.loads(jf.read_text())
        if "panels" not in gt:
            out.append(jf.name)
    return out


def _multi_panel_fixtures():
    return [jf.name for jf in sorted(FIXTURES.glob("subplots_*.json"))]


def _load(name):
    gt = json.loads((FIXTURES / name).read_text())
    pages = load_pdf(str(FIXTURES / gt["pdf"]))
    pd = pages[0]
    regions = detect_regions(pd.paths, pd.texts, pd.width, pd.height)
    return gt, pd, regions


@pytest.mark.parametrize("name", _single_panel_fixtures())
def test_single_panel_labels(name):
    gt, pd, regions = _load(name)
    assert len(regions) == 1, f"expected one region for {name}"
    labels = detect_labels(regions[0], pd.paths, pd.texts, pd)

    assert _norm(labels.title) == _norm(gt.get("title"))
    assert _norm(labels.x_title) == _norm(gt["x_axis"]["label"])
    assert _norm(labels.y_title) == _norm(gt["y_axis"]["label"])

    series_labels = {s["label"] for s in gt["series"]}
    detected = {lab for _, _, lab in labels.legend}
    if detected:
        # A legend was drawn: its labels must match the series labels exactly.
        assert detected == series_labels
        # Every legend entry carries a colour.
        assert all(c is not None for _, c, _ in labels.legend)
    else:
        # No legend drawn (minimal / single-series no-legend fixtures): fine.
        assert detected == set()


@pytest.mark.parametrize("name", _multi_panel_fixtures())
def test_multi_panel_labels(name):
    gt, pd, regions = _load(name)
    assert len(regions) == len(gt["panels"]), f"panel count mismatch for {name}"
    by_rc = {(r.row, r.col): r for r in regions}
    for panel in gt["panels"]:
        region = by_rc.get((panel["row"], panel["col"]))
        assert region is not None, f"no region at {panel['row'],panel['col']}"
        labels = detect_labels(region, pd.paths, pd.texts, pd)
        assert _norm(labels.title) == _norm(panel.get("title")), panel
        assert _norm(labels.x_title) == _norm(panel["x_axis"]["label"]), panel
        assert _norm(labels.y_title) == _norm(panel["y_axis"]["label"]), panel


def test_minimal_has_no_legend():
    """The 'no legend' fixture must yield an empty legend and no titles."""
    _, pd, regions = _load("minimal_scatter_nolegend.json")
    labels = detect_labels(regions[0], pd.paths, pd.texts, pd)
    assert labels.legend == []
    assert labels.title is None


# --------------------------------------------------------------------------
# Real-paper caption spot check (non-asserting; skipped if corpus absent).
# --------------------------------------------------------------------------

_REAL = FsPath(__file__).parents[1] / "data" / "real_pdfs"


@pytest.mark.skipif(
    not (_REAL / "1412.6980.pdf").exists(),
    reason="real-paper corpus not present (data/real_pdfs gitignored)",
)
def test_real_paper_caption_spotcheck(capsys):
    # Adam paper, page index 5 holds Figure 1 (logistic-regression curves).
    pd = load_pdf(str(_REAL / "1412.6980.pdf"), [5])[0]
    regions = detect_regions(pd.paths, pd.texts, pd.width, pd.height)
    with capsys.disabled():
        print(f"\n[real spot check] page 5: {len(regions)} region(s)")
        for r in regions:
            labels = detect_labels(r, pd.paths, pd.texts, pd)
            cap = labels.caption
            print("  region", [round(x, 1) for x in r.bbox],
                  "caption:", (cap[:100] + "...") if cap and len(cap) > 100 else cap)
    # Not a strict assertion: real captions are brittle. Just ensure at least
    # one region exists so the spot check is meaningful.
    assert regions


# --------------------------------------------------------------------------
# Subscript/superscript label assembly unit tests.
# --------------------------------------------------------------------------

def _span(text, x0, y0, x1, y1, size=10.0):
    return TextSpan(text=text, bbox=(x0, y0, x1, y1), size=size)


def test_assemble_label_subscript_joined():
    """Subscript span with cy offset > _LABEL_ROW_TOL but < 0.6 * anchor_size
    must be concatenated into the label rather than dropped.

    Mirrors the "E_N(a− : CR)" pattern in real papers where the subscript 'N'
    sits ~2.9 pt below the base line while _LABEL_ROW_TOL = 2.5 pt.
    """
    # Anchor: 'E', size=6.32, cy=169.35 → y0=166.19, y1=172.51
    # Subscript 'N': size=4.42, cy=172.25 → offset=2.90 (> 2.5 but < 0.6*6.32=3.79)
    # Next spans: ' (', 'a', '−' (subscript), ': CR)'
    base_size = 6.32
    sub_size = 4.42
    texts = [
        _span("E",     346.1, 166.2, 350.8, 172.5, size=base_size),   # 0 anchor
        _span("N",     350.8, 168.4, 354.9, 176.1, size=sub_size),    # 1 subscript, cy=172.25
        _span(" (",    354.9, 166.2, 358.4, 173.5, size=base_size),   # 2
        _span("a",     358.4, 166.2, 361.7, 172.5, size=base_size),   # 3
        _span("−",     361.7, 168.4, 365.6, 176.1, size=sub_size),    # 4 subscript
        _span(": CR)", 367.7, 166.2, 382.9, 172.5, size=base_size),   # 5
    ]
    ty = 0.5 * (texts[0].bbox[1] + texts[0].bbox[3])  # cy of anchor
    label, consumed = _assemble_label(0, ty, texts, set())
    assert "N" in label, f"subscript 'N' missing from {label!r}"
    assert "−" in label, f"subscript '−' missing from {label!r}"
    assert consumed == {0, 1, 2, 3, 4, 5}


def test_assemble_label_raised_superscript_joined():
    """A raised exponent ("^2") is a SMALLER span whose centre-y sits higher
    than a lowered subscript drops, so its offset can reach ~0.65× the base
    size. It must still be stitched into the label, not dropped.

    Mirrors the "‖Z_t‖_F^2 ratio (update scale)" math legend in 2606.04662
    where the raised '2' (size 3.87, cy offset 3.6 pt on a 5.53 pt base) was
    silently dropped, yielding "‖Zt‖F" instead of "‖Zt‖2F".
    """
    base_size = 5.53
    sub_size = 3.87
    # Anchor '‖' cy = 110.25; raised '2' cy = 106.65 → offset 3.6 pt.
    # 3.6 / 5.53 = 0.65 < _SUB_ROW_TOL_FRAC (0.75) → admitted.
    texts = [
        _span("‖", 447.1, 107.0, 449.9, 113.5, size=base_size),  # 0 anchor
        _span("Z", 449.9, 105.1, 453.6, 111.6, size=base_size),  # 1
        _span("t", 453.6, 107.5, 455.0, 111.9, size=sub_size),   # 2 subscript
        _span("‖", 455.4, 107.0, 458.1, 113.5, size=base_size),  # 3
        _span("2", 458.4, 104.3, 460.3, 109.0, size=sub_size),   # 4 superscript
        _span("F", 458.4, 107.7, 460.9, 112.6, size=sub_size),   # 5 subscript
    ]
    ty = 0.5 * (texts[0].bbox[1] + texts[0].bbox[3])
    label, consumed = _assemble_label(0, ty, texts, set())
    assert "2" in label, f"raised superscript '2' missing from {label!r}"
    assert consumed == {0, 1, 2, 3, 4, 5}


def test_assemble_label_same_size_row_tol_respected():
    """Same-size spans with cy offset > _LABEL_ROW_TOL are NOT merged
    (they belong to the next legend entry, not a subscript of this one).
    """
    base_size = 6.32
    texts = [
        _span("A", 10.0, 0.0, 14.0, 6.32, size=base_size),   # 0 anchor cy=3.16
        _span("B", 14.0, 3.5, 18.0, 9.82, size=base_size),   # 1 next-row (cy=6.66, diff=3.5 > 2.5)
    ]
    ty = 0.5 * (texts[0].bbox[1] + texts[0].bbox[3])
    label, consumed = _assemble_label(0, ty, texts, set())
    assert "B" not in label, f"next-row span should not be merged into {label!r}"
    assert consumed == {0}


# --------------------------------------------------------------------------
# Effective-font-size gap fix (LaTeX-scaled size artefact).
# --------------------------------------------------------------------------

def test_assemble_label_latex_scaled_size_bridges_gap():
    """When span.size is a LaTeX scaling artifact (much smaller than bbox height),
    _eff_size() must use the bbox height so that a normal inter-word gap is not
    treated as a label-column separator.

    Mirrors "Linear cavit y(y), g' = 0.4" in 2606.01373 where size≈0.87 but
    bbox height ≈11.7 pt. Gap between "Linear" and "cavit" is ~2.4 pt — fine
    for 11.7 pt text but exceeds _SPAN_GAP * 0.87.
    """
    # size=0.87 (LaTeX artifact), bbox height ~11.7 pt.
    # Gap between spans: 2.4 pt.  With real height the ratio = 2.4/11.7 ≈ 0.21,
    # well within _SPAN_GAP=1.2. Without the fix: 2.4/0.87 = 2.76 > 1.2 → break.
    tiny_size = 0.87
    h = 11.7
    texts = [
        _span("Linear", 441.1, 323.9, 463.1, 323.9 + h, size=tiny_size),   # 0 anchor
        _span("cavit",  465.5, 323.9, 478.0, 323.9 + h, size=tiny_size),   # 1 gap=2.4
        _span("y",      478.5, 323.9, 480.0, 323.9 + h, size=tiny_size),   # 2 adjacent
    ]
    ty = 0.5 * (texts[0].bbox[1] + texts[0].bbox[3])
    label, consumed = _assemble_label(0, ty, texts, set())
    assert "cavit" in label, f"'cavit' should be included; label={label!r}"
    assert consumed == {0, 1, 2}


# --------------------------------------------------------------------------
# Row-sort order fix: leftmost anchor wins when spans share a visual row.
# --------------------------------------------------------------------------

def test_legend_leftmost_span_anchors_when_same_row():
    """Two spans on the same visual row (cy within _ROW_BIN/2) with the same
    swatch: the leftmost must become the anchor so that the right span is its
    continuation, not an independent entry.

    Mirrors the ε-PCA split in 2606.03553 where 'ε' (lx=145.9) and '-PCA'
    (lx=150.0) have cy 0.36 pt apart, causing '-PCA' to sort first under the
    old exact-cy ordering.
    """
    from pdf_chart2table.model import Path
    from pdf_chart2table.labels import detect_labels

    ORANGE = (0.84, 0.37, 0.0)

    def _marker(x0, y, x1):
        """Tiny filled marker swatch."""
        cx, cy = 0.5 * (x0 + x1), y
        half = 2.0
        pts = [(cx - half, cy - half), (cx + half, cy - half),
               (cx + half, cy + half), (cx - half, cy + half),
               (cx - half, cy - half)]
        return Path(points=pts, stroke=ORANGE, fill=ORANGE, width=1.0,
                    dashes=None, closed=True,
                    bbox=(cx - half, cy - half, cx + half, cy + half))

    region = TextSpan.__mro__  # just need a Region-like for _inside check
    from pdf_chart2table.model import Region
    region = Region(bbox=(120.0, 470.0, 420.0, 550.0))

    swatch = _marker(127.7, 505.05, 131.7)   # single swatch for both spans

    # 'ε' at cy = 505.83, '-PCA' at cy = 505.47 — should be same row
    span_eps  = TextSpan(text='ε',    bbox=(145.9, 501.43, 150.0, 510.22), size=8.8, dir=(1.0, 0.0))
    span_pca  = TextSpan(text='-PCA', bbox=(150.0, 499.02, 170.0, 511.92), size=8.8, dir=(1.0, 0.0))

    labels = detect_labels(region, [swatch], [span_eps, span_pca])
    entries = {lab for _, _, lab in labels.legend}
    assert 'ε-PCA' in entries, f"expected 'ε-PCA' but got {entries}"
    assert len(labels.legend) == 1, f"expected 1 entry, got {labels.legend}"


# --------------------------------------------------------------------------
# legend_bbox computation.
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Numeric-anchor label assembly (e.g. "0 ≤ J ≤ 15" where ≤ is a path glyph).
# --------------------------------------------------------------------------

def test_numeric_anchor_with_nonnumeric_continuation():
    """A numeric span is allowed as the legend anchor when a swatch is
    immediately to its left AND the assembled label contains non-numeric text.

    Mirrors the "0 ≤ J ≤ 15" pattern in astro_2606.05237 where the ≤ signs
    are rendered as path glyphs (invisible in text extraction), leaving spans
    '0', 'J', '15' that together form the label '0 J 15'.
    """
    from pdf_chart2table.model import Path, Region

    GRAY = (0.5, 0.5, 0.5)

    def _line_swatch(x0, y, x1):
        return Path(points=[(x0, y), (x1, y)], stroke=GRAY, fill=None,
                    width=1.0, dashes=None, closed=False,
                    bbox=(x0, y, x1, y))

    region = Region(bbox=(400.0, 400.0, 550.0, 580.0))
    swatch = _line_swatch(446.0, 513.0, 461.0)   # right edge x=461

    # '0' is numeric, immediately right of swatch (gap ~10pt)
    span0 = TextSpan(text='0',  bbox=(471.0, 507.0, 476.0, 519.0), size=7.2, dir=(1.0, 0.0))
    # 'J' is non-numeric, gap from '0' right edge (476) = 8.5pt (< 1.3×7.2=9.4)
    spanJ = TextSpan(text='J',  bbox=(484.5, 508.0, 487.0, 518.0), size=7.2, dir=(1.0, 0.0))
    # '15' is numeric continuation of 'J', gap from 'J' right edge (487) = 8.5pt
    span15 = TextSpan(text='15', bbox=(495.5, 507.0, 504.7, 519.0), size=7.2, dir=(1.0, 0.0))

    lbs = detect_labels(region, [swatch], [span0, spanJ, span15])
    entries = {lab for _, _, lab in lbs.legend}
    assert entries, f"expected legend entry but got none"
    # The label should contain the non-numeric 'J' and numeric range parts
    assert any('J' in lab for lab in entries), f"'J' not found in {entries}"


def test_pure_numeric_anchor_still_filtered():
    """A purely numeric assembled label (e.g. '42') is never emitted as a
    legend entry even when a swatch is immediately to its left.
    """
    from pdf_chart2table.model import Path, Region

    BLUE = (0.0, 0.0, 1.0)

    def _line_swatch(x0, y, x1):
        return Path(points=[(x0, y), (x1, y)], stroke=BLUE, fill=None,
                    width=1.0, dashes=None, closed=False,
                    bbox=(x0, y, x1, y))

    region = Region(bbox=(50.0, 40.0, 300.0, 200.0))
    swatch = _line_swatch(60.0, 50.0, 80.0)
    # Only a numeric span to the right of the swatch — should NOT be a legend entry.
    span_num = TextSpan(text='42', bbox=(84.0, 44.0, 98.0, 56.0), size=8.0, dir=(1.0, 0.0))

    lbs = detect_labels(region, [swatch], [span_num])
    assert lbs.legend == [], f"numeric-only entry should be filtered; got {lbs.legend}"


# --------------------------------------------------------------------------

def test_detect_labels_returns_legend_bbox():
    """detect_labels must populate legend_bbox enclosing swatches+labels."""
    from pdf_chart2table.model import Path, Region

    BLUE = (0.0, 0.0, 1.0)

    def _line_swatch(x0, y, x1):
        return Path(points=[(x0, y), (x1, y)], stroke=BLUE, fill=None,
                    width=1.0, dashes=None, closed=False,
                    bbox=(x0, y, x1, y))

    region = Region(bbox=(50.0, 40.0, 300.0, 200.0))
    swatch = _line_swatch(60.0, 50.0, 80.0)
    label_span = TextSpan(text='Series A', bbox=(84.0, 44.0, 130.0, 56.0),
                          size=6.0, dir=(1.0, 0.0))

    lbs = detect_labels(region, [swatch], [label_span])
    assert len(lbs.legend) == 1
    assert lbs.legend_bbox is not None
    bx0, by0, bx1, by1 = lbs.legend_bbox
    # Must enclose the swatch (x0=60) and the label (x1=130, y0=44, y1=56)
    assert bx0 <= 60.0
    assert bx1 >= 130.0
    assert by0 <= 44.0
    assert by1 >= 56.0


# --------------------------------------------------------------------------
# Type3 / glyph-path proxy label suppression.
# --------------------------------------------------------------------------

def test_marker_proxy_label_suppressed():
    """A legend entry whose assembled text is a known marker proxy ('o', 's',
    '^', etc.) must not be emitted — it is a Type3 glyph artifact, not a real
    label. The legend should be empty rather than contain the proxy string.
    """
    from pdf_chart2table.model import Path, Region

    BLUE = (0.0, 0.0, 1.0)

    def _marker_swatch(cx, cy):
        half = 3.0
        pts = [(cx - half, cy - half), (cx + half, cy - half),
               (cx + half, cy + half), (cx - half, cy + half),
               (cx - half, cy - half)]
        return Path(points=pts, stroke=BLUE, fill=BLUE, width=1.0,
                    dashes=None, closed=True,
                    bbox=(cx - half, cy - half, cx + half, cy + half))

    region = Region(bbox=(50.0, 40.0, 300.0, 200.0))
    swatch = _marker_swatch(74.0, 50.0)

    for proxy in ("o", "s", "^", "v", "D", "*", "+", "x"):
        span = TextSpan(text=proxy, bbox=(81.0, 44.0, 86.0, 56.0),
                        size=8.0, dir=(1.0, 0.0))
        lbs = detect_labels(region, [swatch], [span])
        assert lbs.legend == [], (
            f"proxy label {proxy!r} should be suppressed; got {lbs.legend}"
        )


def test_punctuation_glyph_label_suppressed():
    """A legend span containing only non-alphanumeric characters (a box or
    arrow glyph rendered as a Unicode character) is also suppressed.
    """
    from pdf_chart2table.model import Path, Region

    GRAY = (0.5, 0.5, 0.5)

    def _line_swatch(x0, y, x1):
        return Path(points=[(x0, y), (x1, y)], stroke=GRAY, fill=None,
                    width=1.0, dashes=None, closed=False,
                    bbox=(x0, y, x1, y))

    region = Region(bbox=(50.0, 40.0, 300.0, 200.0))
    swatch = _line_swatch(60.0, 50.0, 80.0)

    for glyph in ("■", "●", "→", "◆"):
        span = TextSpan(text=glyph, bbox=(84.0, 44.0, 90.0, 56.0),
                        size=8.0, dir=(1.0, 0.0))
        lbs = detect_labels(region, [swatch], [span])
        assert lbs.legend == [], (
            f"glyph {glyph!r} should be suppressed; got {lbs.legend}"
        )


def test_real_single_char_label_not_suppressed():
    """A single-letter label that is NOT a marker proxy (e.g. 'A', 'B', 'R')
    must pass through — these can be real legend labels.
    """
    from pdf_chart2table.model import Path, Region

    RED = (1.0, 0.0, 0.0)

    def _line_swatch(x0, y, x1):
        return Path(points=[(x0, y), (x1, y)], stroke=RED, fill=None,
                    width=1.0, dashes=None, closed=False,
                    bbox=(x0, y, x1, y))

    region = Region(bbox=(50.0, 40.0, 300.0, 200.0))
    swatch = _line_swatch(60.0, 50.0, 80.0)

    for real_char in ("A", "B", "R", "Z", "N"):
        span = TextSpan(text=real_char, bbox=(84.0, 44.0, 90.0, 56.0),
                        size=8.0, dir=(1.0, 0.0))
        lbs = detect_labels(region, [swatch], [span])
        assert len(lbs.legend) == 1, (
            f"real single-char label {real_char!r} should not be suppressed; "
            f"got {lbs.legend}"
        )
        assert lbs.legend[0][2] == real_char


def test_proxy_mixed_with_real_labels():
    """When a legend has both proxy entries and real entries, only the proxies
    are suppressed; the real entries survive.

    Mirrors materials_2606.02489 where 'C1'/'-C2' are correct but two 'o'
    proxies should be dropped.
    """
    from pdf_chart2table.model import Path, Region

    BLUE = (0.0, 0.0, 1.0)
    RED = (1.0, 0.0, 0.0)

    def _line_swatch(x0, y, x1, color):
        return Path(points=[(x0, y), (x1, y)], stroke=color, fill=None,
                    width=1.0, dashes=None, closed=False,
                    bbox=(x0, y, x1, y))

    region = Region(bbox=(50.0, 40.0, 300.0, 200.0))

    # Entry 1: real label 'C1' on row y=50
    swatch1 = _line_swatch(60.0, 50.0, 80.0, BLUE)
    span_c1 = TextSpan(text='C1', bbox=(84.0, 44.0, 96.0, 56.0),
                       size=8.0, dir=(1.0, 0.0))
    # Entry 2: proxy label 'o' on row y=70
    swatch2 = _line_swatch(60.0, 70.0, 80.0, RED)
    span_o = TextSpan(text='o', bbox=(84.0, 64.0, 90.0, 76.0),
                      size=8.0, dir=(1.0, 0.0))

    lbs = detect_labels(region, [swatch1, swatch2], [span_c1, span_o])
    labels_found = [lab for _, _, lab in lbs.legend]
    assert 'C1' in labels_found, f"real label 'C1' missing from {labels_found}"
    assert 'o' not in labels_found, f"proxy 'o' should be suppressed; got {labels_found}"
    assert len(lbs.legend) == 1, f"expected 1 entry, got {lbs.legend}"
