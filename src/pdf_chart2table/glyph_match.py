"""Recover math symbols drawn as Type3 glyph-PATHS (filled vector outlines that
PyMuPDF exposes as drawings, not text -- so they're invisible to text extraction).

These appear in LaTeX/Computer-Modern PDFs as labels like ``ℓ_x=10 nm, α=1`` whose
``ℓ``/``α``/subscripts have NO char code anywhere (rawdict/texttrace are empty), so
font_recovery can't help and OCR (RapidOCR/pix2tex) is unreliable on the tiny
glyphs. Instead we MATCH each glyph-path's normalized bitmap against a template
library rendered from matplotlib's Computer-Modern mathtext -- the SAME font family
the PDFs use -- which is deterministic and precision-safe (no new dependency).

Public API:
    recognize_region(fitz_page, bbox) -> (latex, unicode, confidence) | None
    recognize_bitmap(bool48) -> (latex, conf, margin) | None
"""
from __future__ import annotations

import io

# Candidate symbols: common physics Greek + letterlike, latin (math-italic) and
# digits. latex -> the unicode char to splice into the label.
_CANDIDATES = {
    r"\alpha": "α", r"\beta": "β", r"\gamma": "γ", r"\delta": "δ",
    r"\epsilon": "ε", r"\zeta": "ζ", r"\eta": "η", r"\theta": "θ",
    r"\iota": "ι", r"\kappa": "κ", r"\lambda": "λ", r"\mu": "μ", r"\nu": "ν",
    r"\xi": "ξ", r"\pi": "π", r"\rho": "ρ", r"\sigma": "σ", r"\tau": "τ",
    r"\phi": "φ", r"\chi": "χ", r"\psi": "ψ", r"\omega": "ω",
    r"\Gamma": "Γ", r"\Delta": "Δ", r"\Theta": "Θ", r"\Lambda": "Λ",
    r"\Sigma": "Σ", r"\Phi": "Φ", r"\Psi": "Ψ", r"\Omega": "Ω",
    r"\ell": "ℓ", r"\hbar": "ℏ", r"\infty": "∞",
}
for _c in "abcdefghijklmnopqrstuvwxyz":
    _CANDIDATES[_c] = _c
for _c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    _CANDIDATES[_c] = _c
for _c in "0123456789":
    _CANDIDATES[_c] = _c

_N = 48  # normalized template/glyph size (NxN)
# Accept a match only when it is BOTH a good overlap AND a clear winner over the
# runner-up (so an ambiguous glyph stays unrecognised rather than being guessed).
_MIN_IOU = 0.55
_MIN_MARGIN = 0.06

_TEMPLATES = None  # lazy {latex: bool NxN}


def _normalize_ink(gray):
    """Crop a grayscale array to its dark (ink) bbox and resize to N×N bool."""
    import numpy as np
    from PIL import Image

    ink = gray < 128
    ys, xs = np.where(ink)
    if len(xs) == 0:
        return None
    crop = ink[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    im = Image.fromarray((crop * 255).astype("uint8")).resize((_N, _N),
                                                              Image.BILINEAR)
    return np.asarray(im) > 128


def _render_template(latex):
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    from PIL import Image
    import matplotlib.pyplot as plt

    with matplotlib.rc_context({"mathtext.fontset": "cm"}):
        fig = plt.figure(figsize=(1, 1), dpi=200)
        fig.text(0.5, 0.5, "$%s$" % latex, ha="center", va="center", fontsize=40)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=200)
        plt.close(fig)
    buf.seek(0)
    return _normalize_ink(np.asarray(Image.open(buf).convert("RGB"))[..., :3].mean(2))


def _templates():
    global _TEMPLATES
    if _TEMPLATES is None:
        _TEMPLATES = {}
        for latex in _CANDIDATES:
            t = _render_template(latex)
            if t is not None:
                _TEMPLATES[latex] = t
    return _TEMPLATES


def _iou(a, b):
    inter = int((a & b).sum())
    uni = int((a | b).sum())
    return inter / uni if uni else 0.0


def recognize_bitmap(glyph):
    """Best (latex, iou, margin) for a normalized N×N bool glyph, or None when no
    template is a confident, clear winner."""
    if glyph is None:
        return None
    scored = sorted(((_iou(glyph, t), latex) for latex, t in _templates().items()),
                    reverse=True)
    if not scored:
        return None
    best_iou, best = scored[0]
    second = scored[1][0] if len(scored) > 1 else 0.0
    if best_iou < _MIN_IOU or (best_iou - second) < _MIN_MARGIN:
        return None
    return best, best_iou, best_iou - second


def recognize_region(fitz_page, bbox, scale=20.0):
    """Rasterize ``bbox`` of ``fitz_page`` and recognise the glyph it contains.
    Returns ``(latex, unicode, confidence)`` or None."""
    import fitz
    import numpy as np

    pix = fitz_page.get_pixmap(matrix=fitz.Matrix(scale, scale),
                               clip=fitz.Rect(*bbox))
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width,
                                                             pix.n)
    glyph = _normalize_ink(arr[..., :3].mean(2))
    hit = recognize_bitmap(glyph)
    if hit is None:
        return None
    latex, conf, _margin = hit
    return latex, _CANDIDATES[latex], conf
