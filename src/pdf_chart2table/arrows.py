"""Detect annotation arrows in a chart region and separate them from the data.

An annotation arrow is a thin shaft segment ending in a small filled arrowhead
(triangle/wedge). Arrows are decoration, not data, so the extractor would
otherwise trace them as fake curves or markers and corrupt the recovered series.
Identifying them lets the caller drop their paths from ``region.path_indices``
before series extraction and record the arrows separately.

Conservative by design: only the clear *shaft + arrowhead* pattern is flagged, so
triangle MARKERS (a glyph with no attached shaft) and genuine data are untouched.

Public API:
    detect_arrows(region, paths) -> (set[int] arrow_path_indices, list[dict])
"""
from __future__ import annotations

from .model import Path, Region


def _bmax(b):
    return max(b[2] - b[0], b[3] - b[1])


def _centroid(pts):
    n = len(pts)
    return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n)


def _distinct(pts):
    """Corner count: collapse points closer than 0.3 pt."""
    out = []
    for q in pts:
        if not out or abs(q[0] - out[-1][0]) + abs(q[1] - out[-1][1]) > 0.3:
            out.append(q)
    return out


def _aspect(b):
    lo, hi = sorted((b[2] - b[0], b[3] - b[1]))
    return lo / hi if hi else 1.0


def _is_head(p: Path, diag: float) -> bool:
    """A small filled arrowhead or single-polygon arrow patch.

    Elongated (aspect ~0.25-0.9) excludes circle/square markers (~1.0) and thin
    lines (<0.25); arrowheads and single-polygon arrows fall in between.

    A real arrowhead is a *tight triangle/wedge* with few corners (~3-9). A
    filled TEXT GLYPH (letter/digit drawn as a curved outline) is also a small
    elongated filled polygon, but its curved outline flattens to MANY corners
    (~30-80) -- so a head with many corners is only accepted when it is also
    thin/shaft-like (aspect <= 0.5), which a (roughly square) glyph never is.
    This keeps the single-polygon arrow branch (head+shaft as one >=10-corner
    patch) while rejecting glyphs that would otherwise inflate the head count."""
    if p.fill is None:
        return False
    sz = _bmax(p.bbox)
    if not (2.0 < sz < 0.18 * diag):
        return False
    if not (0.22 <= _aspect(p.bbox) <= 0.9):
        return False
    nc = len(_distinct(p.points))
    if nc < 3:
        return False
    if nc <= 9:
        return True  # simple triangle/wedge arrowhead
    # Many-cornered filled polygon: a genuine single-polygon arrow is thin
    # (the shaft dominates one dimension); a text glyph is roughly square.
    return _aspect(p.bbox) <= 0.5


def _line_thickness(pts) -> float:
    """Max perpendicular deviation of the points from the first->last chord.

    A straight shaft (a single segment, or a near-collinear polyline) has a
    near-zero deviation regardless of its ORIENTATION; the bbox min-dimension
    does not -- a diagonal segment has a large bbox in both axes yet is still a
    thin 1-D line. Measuring deviation from the chord catches diagonal shafts
    that the bbox test wrongly rejected."""
    a, b = pts[0], pts[-1]
    dx, dy = b[0] - a[0], b[1] - a[1]
    seg = (dx * dx + dy * dy) ** 0.5
    if seg == 0:
        return max((abs(q[0] - a[0]) + abs(q[1] - a[1])) for q in pts)
    return max(abs((q[0] - a[0]) * dy - (q[1] - a[1]) * dx) / seg for q in pts)


def _is_shaft(p: Path, headsz: float, diag: float) -> bool:
    """A thin, open, roughly-straight segment long enough to be an arrow shaft."""
    b = p.bbox
    if p.fill is not None or not (2 <= len(p.points) <= 8):
        return False
    if _line_thickness(p.points) >= 2.5:  # must be thin/near-straight (near-1D)
        return False
    return headsz * 0.8 < _bmax(b) < 0.6 * diag


def detect_arrows(region: Region, paths: list[Path]):
    """Return (set of arrow path indices to drop, list of arrow records)."""
    idxs = list(getattr(region, "path_indices", []))
    if not idxs:
        return set(), []
    diag = _bmax(region.bbox) or 1.0
    heads = [(i, paths[i]) for i in idxs if _is_head(paths[i], diag)]
    # A head BACKED BY A SEPARATE MATCHED SHAFT is a strong, unambiguous arrow
    # signal -- recovered with no upper limit. A head with NO shaft (a bare
    # triangle, or a single-polygon patch identified by its outline alone) is
    # ambiguous: it is the exact shape of a triangle MARKER. So we tally those
    # "unconfirmed" heads and, if there are many, treat them as a marker series
    # and drop them -- preserving the original guard against deleting data.
    used: set[int] = set()
    confirmed: list[tuple[set[int], dict]] = []
    unconfirmed: list[tuple[set[int], dict]] = []
    for hi, hp in heads:
        if hi in used:
            continue
        hc = _centroid(hp.points)
        hsz = _bmax(hp.bbox)
        best, best_d = None, hsz * 1.5
        for i in idxs:
            if i == hi or i in used:
                continue
            p = paths[i]
            if not _is_shaft(p, hsz, diag):
                continue
            for end in (p.points[0], p.points[-1]):
                d = abs(end[0] - hc[0]) + abs(end[1] - hc[1])
                if d < best_d:
                    best, best_d = i, d
        if best is not None:
            used.add(hi)
            used.add(best)
            sp = paths[best]
            tail = max((sp.points[0], sp.points[-1]),
                       key=lambda e: abs(e[0] - hc[0]) + abs(e[1] - hc[1]))
            bucket = confirmed
            owned = {hi, best}
        elif len(_distinct(hp.points)) >= 10:
            # single-polygon arrow (head+shaft drawn as one curved patch): the
            # complex outline alone identifies it -- no separate shaft to match.
            used.add(hi)
            tip = max(hp.points, key=lambda e: abs(e[0] - hc[0]) + abs(e[1] - hc[1]))
            tail = tip
            bucket = unconfirmed
            owned = {hi}
        else:
            # a bare simple triangle with no shaft -> almost certainly a marker;
            # never recorded, but it does count toward the marker-series guard.
            unconfirmed.append((set(), None))
            continue
        bucket.append((owned, {
            "tail_px": [round(tail[0], 2), round(tail[1], 2)],
            "head_px": [round(hc[0], 2), round(hc[1], 2)],
            "color": list(hp.fill) if hp.fill else None,
        }))
    # Arrows are rare (a handful per chart). Many UNCONFIRMED heads (no matched
    # shaft) are a triangle-MARKER series, not arrowheads -- drop them rather
    # than risk deleting data. Complete shaft+head arrows always survive.
    out_used: set[int] = set()
    records: list[dict] = []
    for owned, rec in confirmed:
        out_used |= owned
        records.append(rec)
    if len(unconfirmed) <= 3:
        for owned, rec in unconfirmed:
            if rec is None:
                continue
            out_used |= owned
            records.append(rec)
    return out_used, records
