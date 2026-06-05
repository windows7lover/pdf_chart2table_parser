"""Command-line interface: ``parse`` one PDF and ``batch`` a glob of PDFs.

The deterministic vector pipeline is wired here:
``pdf_vector.load_pdf`` -> ``plot_region.detect_regions`` ->
``calibrate.calibrate_panels`` -> per region build a canonical record (axes,
ticks, region bbox, lossless vector crop) and write it via ``io_store``.

Series extraction (``extract``) and textual labels (``labels``) are built in
parallel by other agents; both are imported guardedly. When unavailable, charts
are still emitted with ``series: []`` and null title/caption.

A region is *skipped* (skip stub written) when both axes are uncalibratable.

Outputs land under ``OUTDIR/<pdf_stem>/``; ``batch`` also writes a top-level
``manifest.csv`` indexing every chart across all PDFs.
"""

from __future__ import annotations

import argparse
import glob
import os
import sys

from . import io_store, pdf_vector
from .calibrate import calibrate_panels
from .plot_region import detect_regions

# Optional modules built in parallel; degrade gracefully if absent.
try:
    from . import extract as _extract
except ImportError:
    _extract = None
try:
    from . import labels as _labels
except ImportError:
    _labels = None


def _parse_pages(spec: str | None, n_pages: int) -> list[int]:
    """Parse a ``--pages A-B`` (1-based, inclusive) spec into 0-based indices."""
    if not spec:
        return list(range(n_pages))
    if "-" in spec:
        a, b = spec.split("-", 1)
        start, end = int(a), int(b)
    else:
        start = end = int(spec)
    return [i for i in range(start - 1, end) if 0 <= i < n_pages]


def _region_series(page, region, x_axis, y_axis, source):
    """Best-effort per-region series via the optional ``extract`` module.

    Uses ``extract.extract_region(region, (x_axis, y_axis), paths, texts,
    source) -> ChartResult`` and pulls ``.table.series``. Returns a list
    (possibly empty) of Series-like objects; never raises.
    """
    if _extract is None:
        return []
    fn = getattr(_extract, "extract_region", None)
    if not callable(fn):
        return []
    try:
        result = fn(region, (x_axis, y_axis), page.paths, page.texts, source)
    except Exception:
        return []
    table = getattr(result, "table", None)
    if table is None:
        return []
    return list(getattr(table, "series", []) or [])


def _region_labels(page, region):
    """Best-effort labels via the optional ``labels`` module.

    Uses ``labels.detect_labels(region, paths, texts, page) -> Labels`` and
    returns ``(title, caption, x_title, y_title, legend)`` (any may be None;
    legend is a list of ``(shape, color, label)`` or empty). Never raises.
    """
    if _labels is None:
        return None, None, None, None, []
    fn = getattr(_labels, "detect_labels", None)
    if not callable(fn):
        return None, None, None, None, []
    try:
        res = fn(region, page.paths, page.texts)
    except Exception:
        return None, None, None, None, []
    return (getattr(res, "title", None), getattr(res, "caption", None),
            getattr(res, "x_title", None), getattr(res, "y_title", None),
            list(getattr(res, "legend", []) or []))


_COLOR_TOL = 0.15  # max summed-RGB distance for a colour match


def _series_endpoints(s):
    """First and last (x_px, y_px) of a series, or None if unavailable."""
    pts = getattr(s, "points", None) or []
    if not pts:
        return None
    a, b = pts[0], pts[-1]
    if "x_px" not in a or "x_px" not in b:
        return None
    return (a["x_px"], a["y_px"]), (b["x_px"], b["y_px"])


def _series_style(s, region, paths):
    """Style signature of a series, comparable to a legend swatch style.

    A marker series ("o"/"s"/... shape) is "marker". A marker-less line series
    is "line" (solid) or "dashed", recovered from the specific region path the
    series was traced from: among same-colour multi-vertex paths we pick the one
    whose endpoints coincide with the series' (so a solid "Testing" and a dashed
    "Training" curve in one colour are told apart even though both paths exist in
    the region). None when no path matches.
    """
    if getattr(s, "marker", None) is not None:
        return "marker"
    sc = getattr(s, "color", None)
    ends = _series_endpoints(s)
    if sc is None or ends is None or region is None or not paths:
        return None
    (sx0, sy0), (sx1, sy1) = ends
    best, best_d = None, 4.0  # px tolerance on matched endpoints
    for i in getattr(region, "path_indices", []):
        p = paths[i]
        if p.stroke is None or len(p.points) < 4:
            continue
        if sum(abs(a - b) for a, b in zip(sc, p.stroke)) >= _COLOR_TOL:
            continue
        px0, py0 = p.points[0]
        px1, py1 = p.points[-1]
        d = abs(px0 - sx0) + abs(py0 - sy0) + abs(px1 - sx1) + abs(py1 - sy1)
        if d < best_d:
            best, best_d = p, d
    if best is None:
        return None
    return "dashed" if best.dashes else "line"


def _apply_legend_labels(series, legend, region=None, paths=None):
    """Name each series from the legend by matching (colour, style).

    Colour alone is ambiguous when several series share a colour but differ in
    style (a solid "Train" and a dashed "Test" curve, or a line vs a marker
    series). We match on colour first, then disambiguate equal-colour candidates
    by style ("marker" / "line" / "dashed"). When the choice stays ambiguous we
    leave the label as ``None`` (precision over recall). Each legend entry is
    used at most once. Mutates ``series`` in place.
    """
    if not series or not legend:
        return
    used: set[int] = set()
    for s in series:
        sc = getattr(s, "color", None)
        if sc is None:
            continue
        # Colour-matching, still-unused legend entries.
        cands = [i for i, e in enumerate(legend)
                 if i not in used and e[1] is not None
                 and sum(abs(a - b) for a, b in zip(sc, e[1])) < _COLOR_TOL]
        if not cands:
            continue
        if len(cands) > 1:
            # Disambiguate by style; bail if still not a unique match.
            style = _series_style(s, region, paths)
            cands = [i for i in cands if legend[i][0] == style]
            if len(cands) != 1:
                continue
        best = cands[0]
        used.add(best)
        s.label = legend[best][2]


def _sibling_groups(regions) -> list[list[int]]:
    """Connected components of regions joined by shared-axis sibling links.

    Two regions are siblings when one lists the other in ``shares_x_with`` or
    ``shares_y_with`` (panels of the same split figure). Returns the region
    indices grouped per figure (single-panel pages yield singletons).
    """
    n = len(regions)
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i, r in enumerate(regions):
        for j in list(getattr(r, "shares_x_with", [])) + list(getattr(r, "shares_y_with", [])):
            if 0 <= j < n:
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def _resolve_legends(legends, regions):
    """Per-region legend after shared-legend propagation across split panels.

    A multi-panel figure often draws ONE legend (in a corner of a single panel);
    after the figure is split into sibling regions only that panel carries it.
    For each sibling group: if exactly one panel has a legend and the rest have
    none, copy it to the empty panels. When several panels carry their own
    legend we leave each as-is (no shared legend to propagate).
    """
    resolved = list(legends)
    for group in _sibling_groups(regions):
        if len(group) < 2:
            continue
        with_leg = [i for i in group if legends[i]]
        if len(with_leg) == 1:
            shared = legends[with_leg[0]]
            for i in group:
                if not resolved[i]:
                    resolved[i] = shared
    return resolved


def parse_pdf(pdf: str, outroot: str, pages_spec: str | None = None) -> list[dict]:
    """Parse one PDF; write artifacts under ``outroot/<stem>/``.

    Returns the manifest rows for every chart (extracted or skipped).
    """
    stem = os.path.splitext(os.path.basename(pdf))[0]
    outdir = os.path.join(outroot, stem)

    page_list = pdf_vector.load_pdf(pdf)
    n_pages = max((p.page_index for p in page_list), default=-1) + 1
    wanted = set(_parse_pages(pages_spec, n_pages))

    rows: list[dict] = []
    for page in page_list:
        if page.page_index not in wanted:
            continue
        regions = detect_regions(page.paths, page.texts, page.width, page.height)
        if not regions:
            continue
        axes = calibrate_panels(regions, page.paths, page.texts)
        # Labels per region, then propagate a shared legend to split panels that
        # have none of their own (one-legend multi-panel figures).
        region_labels = [_region_labels(page, r) for r in regions]
        legends = _resolve_legends([rl[4] for rl in region_labels], regions)
        page_no = page.page_index + 1  # 1-based in output names
        for k, (region, (x_axis, y_axis)) in enumerate(zip(regions, axes), start=1):
            source = {
                "pdf": pdf,
                "page": page.page_index,
                "region_bbox": list(region.bbox),
            }
            # Skip when neither axis could be calibrated.
            if x_axis.calibration is None and y_axis.calibration is None:
                rows.append(io_store.write_skip(
                    "no axis calibration", source, outdir, page_no, k))
                continue

            series = _region_series(page, region, x_axis, y_axis, source)
            # An extraction with no clean series / no data points is a skip, not
            # an empty "extracted" record (precision over recall).
            if not series:
                rows.append(io_store.write_skip(
                    "no series extracted", source, outdir, page_no, k))
                continue
            title, caption, x_title, y_title, _ = region_labels[k - 1]
            legend = legends[k - 1]
            _apply_legend_labels(series, legend, region, page.paths)
            if x_axis.title is None and x_title:
                x_axis.title = x_title
            if y_axis.title is None and y_title:
                y_axis.title = y_title
            record = io_store.chart_to_record(
                pdf=pdf,
                page=page.page_index,
                region_bbox=region.bbox,
                x_axis=x_axis,
                y_axis=y_axis,
                series=series,
                title=title,
                caption=caption,
                confidence=1.0 if (x_axis.calibration and y_axis.calibration) else 0.5,
            )
            rows.append(io_store.write_chart(record, outdir, page_no, k))
    return rows


def _cmd_parse(args: argparse.Namespace) -> int:
    rows = parse_pdf(args.input, args.outdir, args.pages)
    io_store.write_manifest(rows, os.path.join(
        args.outdir, os.path.splitext(os.path.basename(args.input))[0]))
    n_ext = sum(1 for r in rows if r["status"] == "extracted")
    print(f"{args.input}: {n_ext} extracted, {len(rows) - n_ext} skipped")
    return 0


def _cmd_batch(args: argparse.Namespace) -> int:
    pdfs = sorted(glob.glob(args.glob))
    if not pdfs:
        print(f"no PDFs matched: {args.glob}", file=sys.stderr)
        return 1
    all_rows: list[dict] = []
    for pdf in pdfs:
        try:
            all_rows.extend(parse_pdf(pdf, args.outdir, None))
        except Exception as e:  # one bad PDF must not abort the batch
            print(f"error on {pdf}: {e}", file=sys.stderr)
    manifest = io_store.write_manifest(all_rows, args.outdir)
    n_ext = sum(1 for r in all_rows if r["status"] == "extracted")
    print(f"{len(pdfs)} PDFs, {n_ext} charts extracted, "
          f"{len(all_rows) - n_ext} skipped -> {manifest}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pdf-chart2table")
    sub = p.add_subparsers(dest="command", required=True)

    pp = sub.add_parser("parse", help="parse one PDF into chart records")
    pp.add_argument("input", help="input PDF path")
    pp.add_argument("--pages", default=None, help="page range, e.g. 1-3 (1-based)")
    pp.add_argument("-o", "--outdir", default="out", help="output directory")
    pp.set_defaults(func=_cmd_parse)

    pb = sub.add_parser("batch", help="parse many PDFs and write a manifest")
    pb.add_argument("glob", help="glob of input PDFs, e.g. 'papers/*.pdf'")
    pb.add_argument("-o", "--outdir", default="out", help="output directory")
    pb.add_argument("--jobs", type=int, default=1, help="(reserved) parallelism")
    pb.set_defaults(func=_cmd_batch)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
