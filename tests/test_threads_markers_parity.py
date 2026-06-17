"""Parity guard for the vectorised :func:`style._threads_markers` (task #64).

The vectorised implementation MUST return EXACTLY the same boolean as the original
scalar double-loop on every input. The scalar logic is kept inline here as the
reference oracle so this test stays meaningful even if the production scalar helper
is ever refactored away.
"""
import math

from pdf_chart2table import style


def _seg_dist_sq_ref(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    dd = dx * dx + dy * dy
    if dd == 0.0:
        return (px - ax) ** 2 + (py - ay) ** 2
    t = ((px - ax) * dx + (py - ay) * dy) / dd
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    qx, qy = ax + t * dx, ay + t * dy
    return (px - qx) ** 2 + (py - qy) ** 2


def _threads_markers_ref(paths, pts, tol, frac=0.8):
    if not paths or not pts:
        return False
    need = max(2, int(round(frac * len(pts))))
    tol2 = tol * tol
    for p in paths:
        pp = p.points
        if len(pp) < 2:
            continue
        near = 0
        for sx, sy in pts:
            d2 = min(_seg_dist_sq_ref(sx, sy, pp[i][0], pp[i][1],
                                      pp[i + 1][0], pp[i + 1][1])
                     for i in range(len(pp) - 1))
            if d2 <= tol2:
                near += 1
        if near >= need:
            return True
    return False


class _P:
    """Minimal stand-in for ``model.Path`` -- only ``.points`` is used."""

    def __init__(self, points):
        self.points = points


def _cases():
    """A spread of (paths, pts, tol) scenarios incl. boundary / degenerate ones."""
    line = _P([(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)])
    diag = _P([(0.0, 0.0), (10.0, 10.0)])
    degenerate = _P([(5.0, 5.0), (5.0, 5.0)])           # zero-length segment
    single_vertex = _P([(1.0, 1.0)])                    # len < 2 -> skipped
    multi = [_P([(0.0, 0.0), (10.0, 0.0)]),
             _P([(0.0, 50.0), (10.0, 50.0)])]

    threading = [(0.0, 0.0), (5.0, 0.0), (10.0, 0.0), (15.0, 0.0), (20.0, 0.0)]
    near_miss = [(0.0, 1.5), (5.0, 1.5), (10.0, 1.5), (15.0, 1.5), (20.0, 1.5)]
    grazing = [(0.0, 0.0), (5.0, 0.0), (10.0, 9.0), (15.0, 9.0), (20.0, 9.0)]

    cases = []
    # threading line, tight tol
    cases.append(([line], threading, 1.0))
    # near miss just outside / inside tol
    cases.append(([line], near_miss, 1.0))
    cases.append(([line], near_miss, 2.0))
    # tol boundary: point exactly at tol (dist 1.5 == tol 1.5 -> within)
    cases.append(([line], near_miss, 1.5))
    # grazing only a few points
    cases.append(([line], grazing, 0.5))
    # diagonal segment, mid-segment projection
    cases.append(([diag], [(5.0, 5.0), (4.0, 6.0), (6.0, 4.0)], 1.6))
    # degenerate zero-length segment (distance to the endpoint)
    cases.append(([degenerate], [(5.0, 5.0), (6.0, 5.0), (5.0, 7.0)], 1.0))
    cases.append(([degenerate], [(5.0, 5.0), (6.0, 5.0), (5.0, 7.0)], 2.0))
    # multi-path: only the second threads (at y=50)
    cases.append((multi, [(0.0, 50.0), (5.0, 50.0), (10.0, 50.0)], 1.0))
    # path with a sub-2-vertex member (must be skipped, others still tested)
    cases.append(([single_vertex, line], threading, 1.0))
    # empty inputs
    cases.append(([], threading, 1.0))
    cases.append(([line], [], 1.0))
    cases.append(([single_vertex], threading, 1.0))   # all paths skipped
    # single point (need clamps to 2 -> can never reach -> False)
    cases.append(([line], [(0.0, 0.0)], 1.0))
    # exactly frac boundary: 4/5 = 0.8 == need=4
    cases.append(([line],
                  [(0.0, 0.0), (5.0, 0.0), (10.0, 0.0), (15.0, 0.0), (50.0, 0.0)],
                  1.0))
    return cases


def test_parity_handcrafted():
    for paths, pts, tol in _cases():
        assert style._threads_markers(paths, pts, tol) == \
            _threads_markers_ref(paths, pts, tol), (pts, tol)


def test_parity_boundary_exact_tol():
    # A point whose nearest-segment distance is EXACTLY tol must count as within.
    line = _P([(0.0, 0.0), (10.0, 0.0)])
    pts = [(2.0, 3.0), (4.0, 3.0), (6.0, 3.0), (8.0, 3.0)]   # all dist 3.0
    assert style._threads_markers([line], pts, 3.0) is \
        _threads_markers_ref([line], pts, 3.0)
    assert style._threads_markers([line], pts, 3.0) is True
    assert style._threads_markers([line], pts, 2.999) is False


def test_parity_randomised():
    import random
    rng = random.Random(0xC0FFEE)
    for _ in range(400):
        npath = rng.randint(0, 3)
        paths = []
        for _ in range(npath):
            nv = rng.randint(1, 6)
            paths.append(_P([(rng.uniform(-5, 25), rng.uniform(-5, 25))
                             for _ in range(nv)]))
        npts = rng.randint(0, 12)
        pts = [(rng.uniform(-5, 25), rng.uniform(-5, 25)) for _ in range(npts)]
        tol = rng.choice([0.0, 0.5, 1.0, 1.7320508, 3.0, 7.5])
        assert style._threads_markers(paths, pts, tol) == \
            _threads_markers_ref(paths, pts, tol), (paths, pts, tol)
