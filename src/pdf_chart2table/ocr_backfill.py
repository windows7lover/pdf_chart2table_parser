"""OCR backfill of EMPTY text slots, using detection to aim OCR precisely.

The vector parser sometimes leaves an axis title empty -- the text sits just
outside the plot crop, or is drawn as glyph outlines with no text layer, so
``get_text`` returns nothing. This module fills ONLY those empty slots:

  detection localizes the title band (just below the x-axis / left of the
  y-axis); OCR reads that band; the known tick labels are SUBTRACTED so only the
  residual title text remains.

Because OCR is pointed at the title bands (never the plot interior), data
markers cannot be hallucinated into text -- the failure mode of naive full-chart
OCR. OCR also only *fills empty* slots; it never overrides text the vector layer
already recovered (which is more reliable for math/Greek/sub-superscripts).

On by default. If the OCR engine (``rapidocr-onnxruntime``) is not importable, or
``PDFCHART_OCR=0`` is set, every entry point degrades to a no-op so extraction
still succeeds. The engine is lazy + cached, so charts whose slots are already
filled never construct it.
"""

from __future__ import annotations

import os
import re

import fitz
import numpy as np

_RENDER_SCALE = 4.0  # rasterize zones at 4x so small title text is crisp
_BAND_X_PTS = 30.0   # title-band depth below the x-axis (PDF pts)
_BAND_Y_PTS = 52.0   # title-band width left of the y-axis (wider: ticks sit here)
_MIN_CONF = 0.6

_ENGINE: object = "unset"


def _enabled() -> bool:
    return os.environ.get("PDFCHART_OCR", "1") != "0"


def _engine():
    """Lazy, cached RapidOCR instance (or None if unavailable)."""
    global _ENGINE
    if _ENGINE == "unset":
        if not _enabled():
            _ENGINE = None
            return None
        try:
            from rapidocr_onnxruntime import RapidOCR

            # ONNX intra-op threads. Default 1: extraction normally runs many
            # parallel worker processes, and letting each ONNX session grab every
            # core causes severe thread oversubscription (and noisy affinity
            # errors). A SINGLE-image parse (interactive / QA sampling) has no
            # such pool, so it sets PDFCHART_OCR_THREADS > 1 to use several cores
            # and cut per-image OCR latency (~20%). Thread count does NOT change
            # OCR output. Unknown kwargs are absorbed by RapidOCR(**kwargs).
            try:
                _nthreads = max(1, int(os.environ.get("PDFCHART_OCR_THREADS", "1")))
            except ValueError:
                _nthreads = 1
            _ENGINE = RapidOCR(intra_op_num_threads=_nthreads)
        except Exception:
            _ENGINE = None
    return _ENGINE


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").lower())


_NUMERIC_RE = re.compile(r"^[-+]?\d[\d.,eE+\-]*%?$")
# RapidOCR ships a Chinese recognition model, so on noise it hallucinates CJK /
# fullwidth glyphs. Real axis titles here are Latin/Greek/math -> drop any token
# carrying an ideographic or fullwidth character.
_CJK_RE = re.compile(r"[　-鿿豈-﫿＀-￯]")


def _ocr_band(page: fitz.Page, clip, rotate: int = 0) -> list[dict]:
    """Rasterize a clip band and OCR it -> [{text, conf, x}] (x = left edge)."""
    eng = _engine()
    if eng is None:
        return []
    try:
        pix = page.get_pixmap(matrix=fitz.Matrix(_RENDER_SCALE, _RENDER_SCALE),
                              clip=fitz.Rect(*clip))
    except Exception:
        return []
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        img = img[:, :, :3]
    if rotate:
        img = np.rot90(img, k=rotate)
    img = np.ascontiguousarray(img)
    try:
        res, _ = eng(img)
    except Exception:
        return []
    out = []
    for box, text, conf in res or []:
        xs = [p[0] for p in box]
        out.append({"text": text, "conf": float(conf), "x": min(xs)})
    return out


def _assemble_title(boxes: list[dict], tick_labels) -> str | None:
    """Drop tick numbers + low-confidence boxes; join the residual into a title."""
    ticks = {_norm(t) for t in (tick_labels or []) if t}
    kept = []
    for b in boxes:
        if b["conf"] < _MIN_CONF:
            continue
        t = b["text"].strip()
        nt = _norm(t)
        if not nt or nt in ticks:
            continue
        if _NUMERIC_RE.match(t):  # a bare number = a tick we already know
            continue
        if _CJK_RE.search(t):  # CJK/fullwidth = a model hallucination on noise
            continue
        # Single-char tokens are almost always misread tick/axis debris, never a
        # title word -- dropping them removes the dominant noise in the rotated
        # y-axis band (where misread numbers survive exact tick subtraction).
        if len(nt) < 2:
            continue
        kept.append(b)
    if not kept:
        return None
    kept.sort(key=lambda b: b["x"])
    tokens = [b["text"].strip() for b in kept]
    title = " ".join(tokens)
    # Require real letters so we never emit punctuation/number debris as a title.
    if sum(c.isalpha() for c in title) < 2:
        return None
    # Quality gate: a clean title is mostly real words. Rotated-band OCR that
    # mis-segments (e.g. "ce -11/6/+:/ pth K' nce") yields many tiny / symbol
    # tokens -- reject those rather than emit garbage. A token is "wordy" if it
    # holds a >=3-letter alphabetic run; require at least half the tokens wordy.
    wordy = sum(1 for t in tokens if re.search(r"[A-Za-z]{3}", t))
    if wordy < max(1, (len(tokens) + 1) // 2):
        return None
    return title


def _plot_box(record):
    xr = (record.get("x_axis") or {}).get("pixel_range")
    yr = (record.get("y_axis") or {}).get("pixel_range")
    if not xr or not yr:
        return None
    return min(xr), min(yr), max(xr), max(yr)


def backfill(record: dict, fitz_page: fitz.Page) -> dict:
    """Fill empty axis-title slots from zone OCR. Mutates ``record`` in place;
    returns a dict of {slot: recovered_text} for what was filled (may be empty)."""
    if _engine() is None:
        return {}
    box = _plot_box(record)
    if not box:
        return {}
    px0, py0, px1, py1 = box
    filled = {}

    xa = record.get("x_axis") or {}
    if not (xa.get("title") and str(xa["title"]).strip()):
        labels = [t.get("label") for t in record.get("xticks", [])]
        boxes = _ocr_band(fitz_page, (px0 - 6, py1 + 1, px1 + 6, py1 + _BAND_X_PTS))
        t = _assemble_title(boxes, labels)
        if t:
            xa["title"] = t
            filled["x_title"] = t

    ya = record.get("y_axis") or {}
    if not (ya.get("title") and str(ya["title"]).strip()):
        labels = [t.get("label") for t in record.get("yticks", [])]
        clip = (px0 - _BAND_Y_PTS, py0 - 6, px0 - 1, py1 + 6)
        # y-title is rotated; try both 90deg senses, keep the better assembly.
        best = None
        for rot in (1, 3):
            t = _assemble_title(_ocr_band(fitz_page, clip, rotate=rot), labels)
            if t and (best is None or len(t) > len(best)):
                best = t
        if best:
            ya["title"] = best
            filled["y_title"] = best

    return filled
