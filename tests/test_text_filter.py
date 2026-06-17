"""Text-sanity filters: spurious / non-chart text must never be drawn.

Covers task #67:
  * hidden LaTeXit/SVG metadata ('</latexit>' blob) dropped from annotations.
  * an encoded base64-ish blob dropped.
  * a body-text prose sentence rejected as a chart title.
Conservative: real labels / titles must SURVIVE.
"""
from pdf_chart2table.style import _is_junk_text, _is_prose_title


def test_latexit_marker_is_junk():
    assert _is_junk_text("hS6Bm9ojcHnBfn3flYtBacfOY/YHz+QOHp5Fs</latexit>")
    assert _is_junk_text("<latexit sha1_base64=\"abc\">")


def test_base64_blob_is_junk():
    assert _is_junk_text("aGVsbG8gd29ybGQgdGhpcyBpcyBhIGJsb2I=")


def test_prose_title_rejected():
    assert _is_prose_title(
        "/ impact ionization) can always occur for electronic states "
        "with high excitation energy.")


def test_real_labels_survive_junk_filter():
    for s in ("1560.3", "GaAs", r"$\rho_{00}$", "2.25 V",
              "REGIME - II", "SHG power [µW]", r"V$_{BG}$ = 2.25 V"):
        assert not _is_junk_text(s), s


def test_real_titles_survive_prose_filter():
    for s in ("REGIME - II", "SHG power [µW]", r"$\rho_{00}$", "GaAs",
              "2.25 V", r"V$_{BG}$ = 2.25 V"):
        assert not _is_prose_title(s), s
