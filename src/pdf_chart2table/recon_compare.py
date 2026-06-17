"""Image-space original-vs-reconstruction comparison (analysis-by-synthesis).

The residual audit (``scripts/residual_audit.py``) measures fidelity in VECTOR
space and only credits paths matched to a data-series polyline — so correctly
handled arrows / spans / grids / marker glyphs still count as "residual". This
module measures fidelity in RENDERED-IMAGE space instead: rasterize the original
chart region and the reconstruction onto the SAME pixel grid (both spanning the
calibrated plot box) and diff the ink. That covers EVERYTHING that is drawn, with
no per-primitive blind spot, and is the foundation for:

  * a true per-chart fidelity score (missing / extra ink),
  * ambiguity resolution (render candidate interpretations, keep the best match),
  * residual reprocessing (hypothesise the unexplained ink, render, keep if it
    lowers the diff).

This module is the PURE, testable core: it operates on numpy RGB arrays and knows
nothing about PDFs or matplotlib. The rendering glue (rasterising the original
crop with fitz and the reconstruction with the prototype renderer onto a matched
grid) lives in ``scripts/recon_compare.py``.

Public API:
    ink_mask(rgb, white_thresh=0.9) -> bool array
    dilate(mask, radius) -> bool array
    compare(orig_rgb, recon_rgb, tol=2) -> ComparisonResult
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np


# A pixel is "ink" when it is not near-white: its brightest channel is below this
# fraction of full white. Anti-aliased curve edges fade toward white; a moderate
# threshold keeps the solid core of every stroke while ignoring the faint halo.
_WHITE_THRESH = 0.9


def ink_mask(rgb: np.ndarray, white_thresh: float = _WHITE_THRESH) -> np.ndarray:
    """Boolean ink mask of an HxWx3 RGB array (uint8 or float in [0,1]).

    A pixel is ink when it is not (near-)white background. White has ALL channels
    high, so the test is on the DARKEST channel: a pixel is ink when its minimum
    channel falls below ``white_thresh``. This counts any non-white colour
    (saturated red/blue curves included) while ignoring the near-white
    anti-aliased halo around strokes.
    """
    a = np.asarray(rgb)
    if a.dtype == np.uint8:
        a = a.astype(np.float32) / 255.0
    if a.ndim == 3 and a.shape[2] >= 3:
        darkest = a[..., :3].min(axis=2)
    else:
        darkest = a
    return darkest < white_thresh


def dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    """Binary dilation by a square structuring element of the given radius.

    Implemented as a separable max-filter via shifted ORs (no scipy dependency).
    ``radius`` 0 returns the mask unchanged. Used to grant a small alignment /
    anti-aliasing tolerance so a stroke 1-2px off is still counted as a match.
    """
    if radius <= 0:
        return mask.astype(bool)
    out = mask.astype(bool)
    # Horizontal then vertical shifts (separable square dilation).
    for axis in (0, 1):
        acc = out.copy()
        for s in range(1, radius + 1):
            acc |= np.roll(out, s, axis=axis)
            acc |= np.roll(out, -s, axis=axis)
        out = acc
    return out


@dataclass
class ComparisonResult:
    """Ink-overlap metrics between an original and a reconstruction raster.

    All fractions are in [0, 1].

    ``missing_frac``  original ink NOT covered by the (dilated) reconstruction —
                      under-drawing: a dropped series / curve / marker / arrow.
    ``extra_frac``    reconstruction ink NOT present in the (dilated) original —
                      over-drawing: a phantom series, a spurious connector link.
    ``ink_iou``       intersection-over-union of the two ink masks (dilated),
                      a single symmetric fidelity number (1.0 = perfect overlap).
    ``orig_ink`` / ``recon_ink`` are the raw ink-pixel counts (context for the
    fractions: a tiny orig_ink makes the fractions noisy).
    """

    missing_frac: float
    extra_frac: float
    ink_iou: float
    orig_ink: int
    recon_ink: int

    def as_dict(self) -> dict:
        return asdict(self)


def compare(orig_rgb: np.ndarray, recon_rgb: np.ndarray,
            tol: int = 2, white_thresh: float = _WHITE_THRESH,
            ignore_mask: np.ndarray | None = None) -> ComparisonResult:
    """Compare two equally-sized RGB rasters of the same plot box.

    ``tol`` is the dilation radius (px) granting alignment / anti-aliasing slack:
    original ink is "covered" if any reconstruction ink lies within ``tol`` px,
    and vice-versa. Both rasters MUST already be aligned to the same grid (same
    shape, same data->pixel mapping); alignment is the caller's responsibility.

    ``ignore_mask`` (bool, same HxW) marks pixels to EXCLUDE from both ink masks
    before scoring — used to drop the original's text regions (axis/tick labels,
    titles, legend text rendered as math glyphs we don't reproduce) so the
    metrics reflect DATA-ink fidelity (curves / markers / fills / arrows).
    """
    o = np.asarray(orig_rgb)
    r = np.asarray(recon_rgb)
    if o.shape[:2] != r.shape[:2]:
        raise ValueError(
            f"raster shapes differ: {o.shape[:2]} vs {r.shape[:2]} — "
            "the original and reconstruction must be on the same pixel grid"
        )
    om = ink_mask(o, white_thresh)
    rm = ink_mask(r, white_thresh)
    if ignore_mask is not None:
        keep = ~np.asarray(ignore_mask, bool)
        om = om & keep
        rm = rm & keep
    om_d = dilate(om, tol)
    rm_d = dilate(rm, tol)

    orig_ink = int(om.sum())
    recon_ink = int(rm.sum())

    # Missing: original ink with no reconstruction ink within tol.
    missing = om & ~rm_d
    # Extra: reconstruction ink with no original ink within tol.
    extra = rm & ~om_d
    missing_frac = (missing.sum() / orig_ink) if orig_ink else 0.0
    extra_frac = (extra.sum() / recon_ink) if recon_ink else 0.0

    # Tolerant IoU: union of the two raw masks; intersection is the part of each
    # raw mask matched within tol by the other (symmetric).
    matched = (om & rm_d) | (rm & om_d)
    union = om | rm
    ink_iou = (matched.sum() / union.sum()) if union.any() else 1.0

    return ComparisonResult(
        missing_frac=float(missing_frac),
        extra_frac=float(extra_frac),
        ink_iou=float(ink_iou),
        orig_ink=orig_ink,
        recon_ink=recon_ink,
    )


def score(res: ComparisonResult) -> float:
    """Scalar fidelity score in [0, 1] for ranking candidate reconstructions.

    The tolerant ink_iou already balances missing (under-draw) against extra
    (over-draw) symmetrically, so it is the score: 1.0 is a perfect overlay, and
    both a dropped series (missing) and a phantom one (extra) push it down.
    """
    return res.ink_iou


def order_xsort(coords) -> list[int]:
    """Index permutation that orders points left-to-right by x (a function-of-x
    curve). ``coords`` is a sequence of (x, y)."""
    return sorted(range(len(coords)), key=lambda i: coords[i][0])


def order_nearest(coords) -> list[int]:
    """Greedy nearest-neighbour index permutation starting from the leftmost
    point. Recovers a smooth curve whose draw order the extractor scrambled (a
    merged multi-path curve emitted with no usable order): a high path-length /
    x-span ratio collapses to a short, smooth traversal. Candidate only — the
    synthesis selector keeps it solely when it overlays the original better."""
    n = len(coords)
    if n <= 2:
        return list(range(n))
    remaining = set(range(n))
    cur = min(remaining, key=lambda i: coords[i][0])
    remaining.discard(cur)
    order = [cur]
    while remaining:
        cx, cy = coords[cur]
        nxt = min(remaining,
                  key=lambda i: (coords[i][0] - cx) ** 2 + (coords[i][1] - cy) ** 2)
        remaining.discard(nxt)
        order.append(nxt)
        cur = nxt
    return order


def choose_best(orig_rgb: np.ndarray, candidate_recons,
                tol: int = 2, white_thresh: float = _WHITE_THRESH,
                ignore_mask: np.ndarray | None = None):
    """Pick the candidate reconstruction that best matches the original.

    The analysis-by-synthesis selector: when the parser is uncertain (connection
    order, marker-vs-line, legend-or-data, a residual hypothesis), render each
    interpretation and keep the one whose rendered ink best overlays the original.

    ``candidate_recons`` is a sequence of RGB rasters, all on the original's grid.
    Returns ``(best_index, results)`` where ``results`` is the per-candidate list
    of :class:`ComparisonResult` (same order as input). Ties keep the earliest
    candidate, so callers should order candidates by prior preference (the
    parser's default interpretation first) — a trial only wins if it is strictly
    better, never on a tie, keeping the selector conservative.
    """
    results = [compare(orig_rgb, rec, tol=tol, white_thresh=white_thresh,
                       ignore_mask=ignore_mask) for rec in candidate_recons]
    if not results:
        raise ValueError("choose_best needs at least one candidate")
    best_i, best_s = 0, score(results[0])
    for i in range(1, len(results)):
        s = score(results[i])
        if s > best_s:
            best_i, best_s = i, s
    return best_i, results
