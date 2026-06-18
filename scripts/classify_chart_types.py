"""Chart-type SIGNAL pass over the arxiv_semicond corpus.

This is the SCRIPT half of a script+judge pipeline. It scores each chart with a
set of geometric/vector signals and writes them to a CSV. A human/lead then
VISUALLY judges the surfaced candidates. Two design rules follow from that:

  * RELIABLE detectors (raster_image, dual_axis) are calibrated for LOW
    false-positive rate -- when they fire we want to be right.
  * CANDIDATE detectors (histogram_bar, violin, cartoon_inset, dense_noise,
    multipanel) deliberately OVER-FLAG: recall beats precision because the
    visual judge filters them. They are still bounded so they don't fire on
    everything.

No LLM/API calls. Pure PyMuPDF vector geometry + the existing parser
(load_pdf / detect_regions). Output is written INCREMENTALLY so a mid-run crash
can resume (already-scored chart_ids are skipped). Work is parallelised with a
ProcessPool over unique (arxiv_id, page) pages; every chart is wrapped in
try/except so one bad chart -> status=err, never aborting the run.

Usage:
    uv run python scripts/classify_chart_types.py [--limit N] [--workers K]
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import defaultdict

# Allow "import pdf_chart2table" when run from the repo root via `uv run`.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import fitz  # noqa: E402

from pdf_chart2table.pdf_vector import load_page  # noqa: E402
from pdf_chart2table.plot_region import detect_regions  # noqa: E402

CORPUS = "/network/projects/sail/chart2table/arxiv_semicond"
INDEX = os.path.join(CORPUS, "figures_index.csv")
PDF_DIR = os.path.join(CORPUS, "pdfs")
OUT_CSV = os.path.join(CORPUS, "chart_type_report.csv")

FIELDS = [
    "chart_id", "raster_image", "dual_axis", "multipanel", "histogram_bar",
    "violin", "cartoon_inset", "dense_noise", "out_of_scope_max",
    "primary_type", "status",
]

# Out-of-scope types contribute to out_of_scope_max / primary_type. dense_noise
# is a quality flag (in-scope line chart that is noisy), so it is NOT counted
# toward out_of_scope_max.
OUT_OF_SCOPE_TYPES = [
    "raster_image", "dual_axis", "multipanel", "histogram_bar", "violin",
    "cartoon_inset",
]


# ----------------------------------------------------------------------------
# geometry helpers
# ----------------------------------------------------------------------------

def _bbox_area(b):
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _intersect_area(a, b):
    x0 = max(a[0], b[0]); y0 = max(a[1], b[1])
    x1 = min(a[2], b[2]); y1 = min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def _is_numeric_label(txt):
    """A tick-label-looking token: mostly digits, with optional sign / dot /
    exponent / unit-ish trailers. Calibrated loose (recall side) but rejects
    pure words."""
    t = txt.strip()
    if not t:
        return False
    # strip common decorations
    core = t.replace("−", "-").replace("×", "x")
    digits = sum(c.isdigit() for c in core)
    if digits == 0:
        return False
    letters = sum(c.isalpha() for c in core)
    # allow a couple of letters (e.g. "10^3", "5k", "1e-3"); reject word labels
    return letters <= 2 and digits >= 1


# ----------------------------------------------------------------------------
# per-chart detectors  (region = chart plot box in PDF points; y grows DOWN)
# ----------------------------------------------------------------------------

def score_raster_image(region, image_rects):
    """Fraction of the chart region covered by embedded raster image bboxes.

    RELIABLE: a chart that is mostly a photo/colormap has high coverage. We use
    the union-ish via summed clipped intersection (capped at region area) so
    several tiles add up but cannot exceed 1.0."""
    ra = _bbox_area(region)
    if ra <= 0 or not image_rects:
        return 0.0
    covered = sum(_intersect_area(region, ir) for ir in image_rects)
    return max(0.0, min(1.0, covered / ra))


def score_dual_axis(region, texts):
    """Numeric tick-label columns on BOTH sides of the plot box.

    RELIABLE: a second numeric axis (right Y or top X) means a dual-axis chart.
    We bin numeric-looking text spans by which margin they sit in relative to
    the region box, then require numeric labels on the *opposite* margin too.

    NOTE: tick labels live OUTSIDE the plot box, so ``region.text_indices``
    (which holds only texts inside the box) is useless here -- we scan ALL page
    texts and keep those within a margin band just outside each edge."""
    x0, y0, x1, y1 = region
    w = x1 - x0; h = y1 - y0
    if w <= 0 or h <= 0:
        return 0.0
    mx = w * 0.18  # margin band width (just outside the plot box)
    my = h * 0.18
    left = right = top = bottom = 0
    lset = []; rset = []; tset = []; bset = []
    rys = []; tys = []  # y of right-axis labels / x of top-axis labels (span check)
    for t in texts:
        if not _is_numeric_label(t.text):
            continue
        bx0, by0, bx1, by1 = t.bbox
        cx = (bx0 + bx1) / 2.0
        cy = (by0 + by1) / 2.0
        lab = t.text.strip().replace("−", "-")
        # vertical center inside the box's y-span (so it is a Y tick, not a
        # title) for left/right; horizontal center inside x-span for top/bottom.
        in_y = (y0 - my) <= cy <= (y1 + my)
        in_x = (x0 - mx) <= cx <= (x1 + mx)
        # left/right margin band: just outside the vertical edge. Second-axis
        # ticks HUG the box edge; a neighbouring panel's labels bleeding into
        # the band sit farther out (their own box edge is elsewhere), so the
        # right/top side requires an edge-hugging gap (<8% of the box span).
        gap_r = bx0 - x1   # inner edge of a right label to the box's right side
        gap_t = y0 - by1   # bottom of a top label to the box's top side
        if in_y and (x0 - mx) <= cx < x0:
            left += 1; lset.append(lab)
        elif in_y and x1 < cx <= (x1 + mx) and gap_r <= 0.08 * w:
            right += 1; rset.append(lab); rys.append(cy)
        # top/bottom margin band: just outside the horizontal edge.
        if in_x and (y0 - my) <= cy < y0 and gap_t <= 0.08 * h:
            top += 1; tset.append(lab); tys.append(cx)
        elif in_x and y1 < cy <= (y1 + my):
            bottom += 1; bset.append(lab)

    def _mirrored(a, b):
        """Same numeric labels on both sides => one mirrored axis, NOT dual."""
        if not a or not b:
            return False
        sa, sb = set(a), set(b)
        inter = sa & sb
        return len(inter) >= 2 and len(inter) >= 0.7 * min(len(sa), len(sb))

    def _spans(coords, lo, hi):
        """Second-axis ticks distribute across the axis; a neighbouring panel's
        labels bleeding into the margin band cluster at one end. Require the
        labels to cover >=40% of the box span."""
        if len(coords) < 2 or hi <= lo:
            return False
        return (max(coords) - min(coords)) >= 0.4 * (hi - lo)

    conf = 0.0
    # right Y axis: need standard left ticks AND >=3 DISTINCT right labels
    # (a real axis has several ticks; a neighbour panel's 2-tick y-axis bleeding
    # in does not), a DIFFERENT scale (mirrored = decorative), and the right
    # labels spanning the box height.
    if (left >= 2 and len(set(rset)) >= 3 and not _mirrored(lset, rset)
            and _spans(rys, y0, y1)):
        conf = max(conf, min(1.0, 0.55 + 0.1 * min(right, 5)))
    # top X axis: bottom ticks AND >=3 distinct top labels, non-mirrored,
    # spanning width.
    if (bottom >= 2 and len(set(tset)) >= 3 and not _mirrored(bset, tset)
            and _spans(tys, x0, x1)):
        conf = max(conf, min(1.0, 0.55 + 0.1 * min(top, 5)))
    return conf


def score_multipanel(region, texts, tidx, n_regions_on_page):
    """Panel-letter labels (a)(b)(c)... inside the region OR the parser split
    the page into many sub-panels overlapping this chart.

    CANDIDATE: over-flag. We look for >=3 short '(x)' / 'x)' / 'x.' panel tags."""
    import re
    tag = re.compile(r"^\(?[a-h]\)?[\.\)]?$", re.IGNORECASE)
    n_tags = 0
    for ti in tidx:
        t = texts[ti].text.strip()
        if len(t) <= 3 and tag.match(t):
            n_tags += 1
    conf = 0.0
    if n_tags >= 3:
        conf = max(conf, min(1.0, 0.5 + 0.1 * n_tags))
    elif n_tags == 2:
        conf = max(conf, 0.4)
    # A single chart_id whose page exploded into many regions is multipanel-ish
    # even without letters (gridded subplots). Soft signal.
    if n_regions_on_page >= 4:
        conf = max(conf, 0.45)
    return conf


def _rects_in(region, paths, pidx):
    """Filled, axis-aligned rectangles inside the region. Returns list of
    (x0,y0,x1,y1,w,h)."""
    out = []
    for pi in pidx:
        p = paths[pi]
        if p.fill is None:
            continue
        pts = p.points
        if len(pts) < 4:
            continue
        bx0, by0, bx1, by1 = p.bbox
        w = bx1 - bx0; h = by1 - by0
        if w <= 0 or h <= 0:
            continue
        # axis-aligned-rectangle test: the path's points hug its own bbox
        # corners (a true rect spends all its vertices on the 4 sides).
        on_edge = 0
        for (px, py) in pts:
            near_x = (abs(px - bx0) < 0.5 or abs(px - bx1) < 0.5)
            near_y = (abs(py - by0) < 0.5 or abs(py - by1) < 0.5)
            if near_x or near_y:
                on_edge += 1
        if on_edge < len(pts) * 0.9:
            continue
        out.append((bx0, by0, bx1, by1, w, h))
    return out


def score_histogram_bar(region, paths, pidx):
    """>=4 filled rectangles that look like BARS (not scatter markers).

    CANDIDATE but TIGHTENED (prior run flagged square scatter markers): a real
    bar is TALL (h >> w, rising from a shared baseline) OR a wide tile, and is
    not marker-sized-square. We require rects to share a baseline AND be either
    tall or contiguously tiled in x."""
    x0, y0, x1, y1 = region
    rw = x1 - x0; rh = y1 - y0
    if rw <= 0 or rh <= 0:
        return 0.0
    rects = _rects_in(region, paths, pidx)
    if len(rects) < 4:
        return 0.0
    min_side = 0.02 * min(rw, rh)
    bars = []
    for (bx0, by0, bx1, by1, w, h) in rects:
        # reject marker-sized near-squares
        if w < min_side and h < min_side:
            continue
        aspect = h / w if w > 0 else 999
        near_square = 0.6 <= aspect <= 1.6
        small = max(w, h) < 0.06 * min(rw, rh)
        if near_square and small:
            continue  # scatter marker
        bars.append((bx0, by0, bx1, by1, w, h))
    if len(bars) < 4:
        return 0.0
    # vertical bars: share a bottom baseline (by1 ~ const) and are tall/tiled.
    bottoms = defaultdict(int)
    tops = defaultdict(int)
    lefts = sorted(b[0] for b in bars)
    for (bx0, by0, bx1, by1, w, h) in bars:
        bottoms[round(by1 / max(1.0, rh) * 40)] += 1
        tops[round(by0 / max(1.0, rh) * 40)] += 1
    share_bottom = max(bottoms.values()) if bottoms else 0
    share_top = max(tops.values()) if tops else 0  # horizontal bars
    baseline_n = max(share_bottom, share_top)
    if baseline_n < 4:
        return 0.0
    tall = sum(1 for b in bars if (b[5] / b[4] if b[4] > 0 else 9) >= 1.5)
    # contiguous tiling in x: gaps between successive bar lefts are regular
    gaps = [lefts[i + 1] - lefts[i] for i in range(len(lefts) - 1)]
    tiled = len(gaps) >= 3 and (max(gaps) - min(gaps)) < 0.5 * (sum(gaps) / len(gaps) + 1e-6)
    conf = 0.0
    if baseline_n >= 4 and (tall >= 4 or tiled):
        conf = min(1.0, 0.5 + 0.05 * baseline_n + (0.15 if tall >= 4 else 0))
    elif baseline_n >= 4:
        conf = 0.45  # shared baseline but ambiguous shape -> let judge decide
    return conf


def score_violin(region, paths, pidx):
    """Filled, non-rectangular, vertically-mirror-symmetric closed blobs in a
    column.

    CANDIDATE, acknowledged UNRELIABLE -- emit a best-effort signal. We look
    for filled closed paths that are NOT rectangles, are taller than wide, and
    whose left/right extent is roughly symmetric about a vertical axis."""
    x0, y0, x1, y1 = region
    rw = x1 - x0; rh = y1 - y0
    if rw <= 0 or rh <= 0:
        return 0.0
    blobs = 0
    for pi in pidx:
        p = paths[pi]
        if p.fill is None or len(p.points) < 8:
            continue
        bx0, by0, bx1, by1 = p.bbox
        w = bx1 - bx0; h = by1 - by0
        if w <= 0 or h <= 0:
            continue
        # vertical-ish blob, moderate size
        if h < 1.5 * w:
            continue
        if h < 0.08 * rh or h > 0.95 * rh:
            continue
        # not a rectangle (vertices do not all hug the bbox)
        cx = (bx0 + bx1) / 2.0
        on_edge = sum(1 for (px, py) in p.points
                      if abs(px - bx0) < 0.5 or abs(px - bx1) < 0.5)
        if on_edge > len(p.points) * 0.8:
            continue
        # vertical-mirror symmetry: sample widths at several heights, the
        # centroid x should stay near the bbox center.
        xs = [px for (px, py) in p.points]
        left_ext = cx - min(xs)
        right_ext = max(xs) - cx
        if min(left_ext, right_ext) <= 0:
            continue
        sym = min(left_ext, right_ext) / max(left_ext, right_ext)
        if sym < 0.55:
            continue
        blobs += 1
    if blobs >= 3:
        return min(1.0, 0.5 + 0.08 * blobs)
    if blobs == 2:
        return 0.45
    return 0.0


def score_cartoon_inset(region, paths, pidx):
    """A cluster of filled COLOURED shapes in a sub-rectangle that is NOT the
    main data area (an illustration / schematic inset).

    CANDIDATE: over-flag. We collect coloured (non-grey, non-white/black)
    filled shapes, cluster them into the densest quarter-region cell, and fire
    if that cell holds a tight cluster of several coloured shapes occupying a
    small fraction of the region (i.e. localised, not spread like data)."""
    x0, y0, x1, y1 = region
    rw = x1 - x0; rh = y1 - y0
    if rw <= 0 or rh <= 0:
        return 0.0

    def is_coloured(c):
        if c is None:
            return False
        r, g, b = c[0], c[1], c[2]
        mx = max(r, g, b); mn = min(r, g, b)
        # saturated (chromatic) and not near-white
        return (mx - mn) > 0.15 and mx > 0.2

    region_area = rw * rh
    marker_side = 0.025 * min(rw, rh)  # plot-marker scale
    shapes = []
    n_markerish = 0
    for pi in pidx:
        p = paths[pi]
        if p.fill is None or not is_coloured(p.fill):
            continue
        bx0, by0, bx1, by1 = p.bbox
        w = bx1 - bx0; h = by1 - by0
        if w <= 0 or h <= 0:
            continue
        # ignore region-spanning fills (backgrounds / big bars)
        if w > 0.6 * rw or h > 0.6 * rh:
            continue
        # marker-sized coloured shapes are DATA (scatter markers), not cartoon
        # illustration; count them but exclude from the illustration set.
        if w < marker_side and h < marker_side:
            n_markerish += 1
            continue
        shapes.append(((bx0 + bx1) / 2.0, (by0 + by1) / 2.0, w * h))

    # Many uniform coloured shapes overall == a scatter plot, not an inset.
    # If marker-sized shapes dominate, this is data; suppress.
    if n_markerish >= 8 and n_markerish > 2 * len(shapes):
        return 0.0
    if len(shapes) < 4:
        return 0.0
    # 4x4 grid: find the densest cell-cluster (a 2x2 block of cells)
    nx = ny = 4
    cell = defaultdict(list)
    for (cx, cy, a) in shapes:
        gx = min(nx - 1, int((cx - x0) / rw * nx))
        gy = min(ny - 1, int((cy - y0) / rh * ny))
        cell[(gx, gy)].append((cx, cy, a))
    best = 0
    best_areas = []
    for gx in range(nx - 1):
        for gy in range(ny - 1):
            members = [m for dx in (0, 1) for dy in (0, 1)
                       for m in cell.get((gx + dx, gy + dy), [])]
            if len(members) > best:
                best = len(members)
                best_areas = [m[2] for m in members]
    if best < 4:
        return 0.0
    # localisation: the cluster holds the bulk of the (non-marker) coloured
    # shapes, occupying a compact sub-rect.
    frac = best / len(shapes)
    # size heterogeneity: a real illustration mixes shape sizes; a residual
    # grid of identical glyphs (e.g. coloured tick markers that survived the
    # marker filter) has near-zero variance. CV = std/mean.
    if best_areas:
        mean_a = sum(best_areas) / len(best_areas)
        var_a = sum((a - mean_a) ** 2 for a in best_areas) / len(best_areas)
        cv = (var_a ** 0.5) / mean_a if mean_a > 0 else 0.0
        cluster_frac = sum(best_areas) / region_area
    else:
        cv = 0.0; cluster_frac = 0.0
    conf = 0.0
    if best >= 4 and frac >= 0.6:
        conf = min(1.0, 0.45 + 0.07 * best)
        # boost when the cluster is visually substantial and heterogeneous
        # (illustration), trim when it is uniform tiny glyphs.
        if cv < 0.15 and cluster_frac < 0.02:
            conf = min(conf, 0.4)
    elif best >= 5:
        conf = 0.4
    return conf


def score_dense_noise(region, paths, pidx, n_points):
    """High-n series with jagged (high 2nd-difference variance) geometry.

    CANDIDATE / quality flag (NOT out-of-scope). Gate on the index n_points
    (>250) AND measure jaggedness on the longest stroked polyline in the
    region: normalised mean-squared 2nd difference of the y trajectory."""
    if n_points <= 250:
        return 0.0
    x0, y0, x1, y1 = region
    rh = y1 - y0
    if rh <= 0:
        return 0.0
    # longest stroked (non-filled) polyline = the data curve
    best_pts = None
    for pi in pidx:
        p = paths[pi]
        if p.fill is not None:
            continue
        if p.stroke is None:
            continue
        if best_pts is None or len(p.points) > len(best_pts):
            best_pts = p.points
    if best_pts is None or len(best_pts) < 20:
        return 0.0
    ys = [py for (px, py) in best_pts]
    # second differences, normalised by region height
    d2 = []
    for i in range(1, len(ys) - 1):
        d2.append((ys[i + 1] - 2 * ys[i] + ys[i - 1]) / rh)
    if not d2:
        return 0.0
    rms = (sum(v * v for v in d2) / len(d2)) ** 0.5
    # rms ~0.0 smooth, ~0.02+ visibly jagged. Map to confidence.
    conf = max(0.0, min(1.0, (rms - 0.004) / 0.03))
    # require both the count gate (already passed) and some jaggedness
    if conf < 0.15:
        return 0.0
    return conf


# ----------------------------------------------------------------------------
# page-level worker
# ----------------------------------------------------------------------------

def process_page(task):
    """Score every chart on one (arxiv_id, page). Returns list of result dicts.

    Robust: page-level failure -> every chart on the page gets status=err;
    per-chart failure -> that chart gets status=err."""
    arxiv_id, page, charts = task  # charts: list of (chart_id, chart_idx, n_points)
    pdf_path = os.path.join(PDF_DIR, f"{arxiv_id}.pdf")
    results = []
    try:
        doc = fitz.open(pdf_path)
        pg = doc[page - 1]  # index csv 'page' is 1-based
        paths, texts = load_page(pg)
        image_rects = []
        try:
            for info in pg.get_image_info(xrefs=True):
                b = info.get("bbox")
                if b:
                    image_rects.append((b[0], b[1], b[2], b[3]))
        except Exception:
            pass
        regions = detect_regions(paths, texts, pg.rect.width, pg.rect.height,
                                 image_rects=image_rects)
        doc.close()
    except Exception as e:  # whole-page failure
        for (cid, _ci, _np) in charts:
            results.append(_err_row(cid, f"page:{type(e).__name__}"))
        return results

    n_regions = len(regions)
    page_box = (0.0, 0.0, pg.rect.width, pg.rect.height) if False else None

    for (cid, chart_idx, n_points) in charts:
        try:
            # chart index is 1-based in detection (row-major) order
            if regions and 1 <= chart_idx <= len(regions):
                reg = regions[chart_idx - 1]
            elif regions:
                # detection count mismatch -> use the nearest available region
                reg = regions[min(chart_idx - 1, len(regions) - 1)]
            else:
                results.append(_err_row(cid, "no_region"))
                continue
            region = reg.bbox
            pidx = reg.path_indices
            tidx = reg.text_indices

            raster = score_raster_image(region, image_rects)
            dual = score_dual_axis(region, texts)
            multi = score_multipanel(region, texts, tidx, n_regions)
            hist = score_histogram_bar(region, paths, pidx)
            viol = score_violin(region, paths, pidx)
            cart = score_cartoon_inset(region, paths, pidx)
            dense = score_dense_noise(region, paths, pidx, n_points)

            scores = {
                "raster_image": raster, "dual_axis": dual, "multipanel": multi,
                "histogram_bar": hist, "violin": viol, "cartoon_inset": cart,
            }
            oos_max = max(scores.values()) if scores else 0.0
            primary = max(scores, key=scores.get) if oos_max > 0.0 else "in_scope"
            if oos_max < 0.3:
                primary = "in_scope"

            results.append({
                "chart_id": cid,
                "raster_image": round(raster, 3),
                "dual_axis": round(dual, 3),
                "multipanel": round(multi, 3),
                "histogram_bar": round(hist, 3),
                "violin": round(viol, 3),
                "cartoon_inset": round(cart, 3),
                "dense_noise": round(dense, 3),
                "out_of_scope_max": round(oos_max, 3),
                "primary_type": primary,
                "status": "ok",
            })
        except Exception as e:
            results.append(_err_row(cid, f"chart:{type(e).__name__}"))
    return results


def _err_row(cid, reason):
    row = {f: 0.0 for f in FIELDS}
    row["chart_id"] = cid
    row["primary_type"] = ""
    row["status"] = f"err:{reason}"
    return row


# ----------------------------------------------------------------------------
# driver
# ----------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0,
                    help="limit number of pages (debug)")
    ap.add_argument("--workers", type=int, default=0,
                    help="0 = all available CPUs")
    args = ap.parse_args()

    # resume: chart_ids already in the CSV
    done = set()
    if os.path.exists(OUT_CSV):
        with open(OUT_CSV, newline="") as f:
            for r in csv.DictReader(f):
                done.add(r["chart_id"])
    print(f"already scored: {len(done)}")

    # group remaining charts by (arxiv_id, page)
    pages = defaultdict(list)
    with open(INDEX, newline="") as f:
        for r in csv.DictReader(f):
            if r["chart_id"] in done:
                continue
            pages[(r["arxiv_id"], int(r["page"]))].append(
                (r["chart_id"], int(r["chart"]), int(r["n_points"]))
            )
    tasks = [(aid, pg, ch) for (aid, pg), ch in pages.items()]
    if args.limit:
        tasks = tasks[:args.limit]
    print(f"pages to process: {len(tasks)}")
    if not tasks:
        print("nothing to do")
        return

    ncpu = args.workers or len(os.sched_getaffinity(0))
    print(f"workers: {ncpu}")

    write_header = not os.path.exists(OUT_CSV)
    import multiprocessing as mp
    ctx = mp.get_context("spawn")
    written = 0
    errs = 0
    with open(OUT_CSV, "a", newline="") as fout:
        w = csv.DictWriter(fout, fieldnames=FIELDS)
        if write_header:
            w.writeheader()
        with ctx.Pool(ncpu) as pool:
            for res in pool.imap_unordered(process_page, tasks, chunksize=4):
                for row in res:
                    w.writerow(row)
                    written += 1
                    if str(row["status"]).startswith("err"):
                        errs += 1
                fout.flush()
                if written % 2000 < len(res):
                    print(f"  written {written} (err {errs})", flush=True)
    print(f"DONE. wrote {written} rows, {errs} err.")


if __name__ == "__main__":
    main()
