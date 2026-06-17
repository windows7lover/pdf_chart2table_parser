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

import fitz

from . import io_store, ocr_backfill as _ocr_backfill, pdf_vector, style as _style
from . import tick_ocr as _tick_ocr
from .arrows import detect_arrows
from .calibrate import calibrate_panels
from .error_bars import detect_error_bars, recover_error_bars
from .grid import detect_grid
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


def _title_text(title):
    """Extract plain text from a title (TextSpan / dict / str / None)."""
    if title is None:
        return None
    if isinstance(title, str):
        return title
    if isinstance(title, dict):
        return title.get("text")
    return getattr(title, "text", None)


def _drop_title_if_axis_dup(title, x_axis, y_axis):
    """Return None if the chart title merely duplicates an axis title (a stacked
    sibling panel's axis label mis-detected as this panel's title)."""
    def norm(s):
        return "".join(c for c in (s or "").lower() if c.isalnum())
    tt = norm(_title_text(title))
    if tt and tt in {norm(getattr(x_axis, "title", None)),
                     norm(getattr(y_axis, "title", None))}:
        return None
    return title


def _region_series(page, region, x_axis, y_axis, source, legend_bbox=None):
    """Best-effort per-region series via the optional ``extract`` module.

    Uses ``extract.extract_region(region, (x_axis, y_axis), paths, texts,
    source, legend_bbox) -> ChartResult`` and pulls ``.table.series``. Returns
    a list (possibly empty) of Series-like objects; never raises.
    """
    if _extract is None:
        return []
    fn = getattr(_extract, "extract_region", None)
    if not callable(fn):
        return []
    try:
        result = fn(region, (x_axis, y_axis), page.paths, page.texts, source,
                    legend_bbox)
    except Exception:
        return []
    table = getattr(result, "table", None)
    if table is None:
        return []
    return list(getattr(table, "series", []) or [])


def _attach_error_bars(series, whiskers, y_axis):
    """Attach a per-point ``y_err`` (data units) from recovered error-bar whiskers.

    Each whisker is ``(cx, y_top_px, y_bottom_px)``. We find the marker point
    whose x coincides with the whisker and whose y lies within the whisker's
    vertical span (the datum the bar belongs to), and set its symmetric
    ``y_err`` = half the whisker's height in data units.
    """
    from .calibrate import to_data as _to_data
    cal = y_axis.calibration
    # An error bar is the uncertainty on a *plotted datum* (a marker). It is
    # attached to a MARKER series, never to a marker-less continuous curve: a
    # smooth fitted line does not carry per-point whiskers. Restricting the
    # candidate points to marker-bearing series stops a vertical decoration
    # (e.g. a dashed annotation arrow) whose x happens to fall on a dense
    # curve's vertices from being mis-recorded as that curve's error bars
    # (2005.03896_p4c1). Genuine error-bar series are scatter/marker series, so
    # they are unaffected.
    marker_series = [s for s in series if getattr(s, "marker", None) is not None]
    if not marker_series:
        return
    for cx, top, bot in whiskers:
        yerr = abs(_to_data(cal, top) - _to_data(cal, bot)) / 2.0
        if yerr <= 0:
            continue
        lo, hi = min(top, bot), max(top, bot)
        best, best_dx = None, 3.0
        for s in marker_series:
            for p in getattr(s, "points", []) or []:
                xp, yp = p.get("x_px"), p.get("y_px")
                if xp is None or yp is None:
                    continue
                if lo - 2.0 <= yp <= hi + 2.0 and abs(xp - cx) <= best_dx:
                    best, best_dx = p, abs(xp - cx)
        if best is not None:
            best["y_err"] = round(float(yerr), 6)


def _region_labels(page, region, glyph_recognize=None):
    """Best-effort labels via the optional ``labels`` module.

    Uses ``labels.detect_labels(region, paths, texts, page) -> Labels`` and
    returns ``(title, caption, x_title, y_title, legend, legend_bbox)`` (any
    may be None; legend is a list of ``(shape, color, label)`` or empty;
    legend_bbox is a ``(x0, y0, x1, y1)`` tuple or None). Never raises.

    ``glyph_recognize`` is an optional ``bbox -> (latex, unicode, conf) | None``
    callback passed through to recover leading math-symbol glyph-paths on legend
    labels (font-less Greek/symbol characters dropped from the text stream).
    """
    if _labels is None:
        return None, None, None, None, [], None
    fn = getattr(_labels, "detect_labels", None)
    if not callable(fn):
        return None, None, None, None, [], None
    try:
        res = fn(region, page.paths, page.texts, glyph_recognize=glyph_recognize)
    except Exception:
        return None, None, None, None, [], None
    return (getattr(res, "title", None), getattr(res, "caption", None),
            getattr(res, "x_title", None), getattr(res, "y_title", None),
            list(getattr(res, "legend", []) or []),
            getattr(res, "legend_bbox", None))


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
        d_start = abs(px0 - sx0) + abs(py0 - sy0)
        # The series' last point may be an interior point of the path (some
        # PDF paths close back to the axis after the last data vertex, so
        # p.points[-1] diverges from the series endpoint). Match against the
        # nearest point in the path instead of only the last point.
        d_end = min(abs(px - sx1) + abs(py - sy1) for px, py in p.points)
        d = d_start + d_end
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

    Order-based fallback (applied after colour matching):
    - Case 1: NO colour match was made at all AND the number of unmatched series
      equals the number of unused entries -> assign by vertical position (i-th
      series top-to-bottom <-> i-th entry top-to-bottom). This covers the
      black/neutral-swatch pattern where legend swatches carry no hue.
    - Case 2: Exactly ONE series and ONE entry remain unmatched after partial
      colour matching -> assign unconditionally (N=1 is unambiguous).
    - Excluded: some colour matches were made AND N>1 remain unmatched ->
      NO fallback (order is uncertain; a wrong label is worse than missing).
    """
    if not series or not legend:
        return
    used: set[int] = set()
    color_matched = 0
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
        color_matched += 1

    # Order-based fallback for series still missing a label.
    unmatched_series = [s for s in series if getattr(s, "label", None) is None]
    unused_entries = [i for i in range(len(legend)) if i not in used]
    n_unmatched = len(unmatched_series)
    n_unused = len(unused_entries)

    if n_unmatched == 0 or n_unused == 0:
        return  # nothing left to assign

    apply_fallback = False
    if color_matched == 0 and n_unmatched == n_unused:
        # Case 1: no colour match at all and counts match -> order-assign.
        apply_fallback = True
    elif n_unmatched == 1 and n_unused == 1:
        # Case 2: single remainder after partial matching -> unambiguous.
        apply_fallback = True
    # Excluded: color_matched > 0 and n_unmatched > 1 -> too uncertain.

    if not apply_fallback:
        return

    # Sort series top-to-bottom by their first point's y_px (or by index if
    # unavailable). Sort entries by the vertical midpoint of their swatch row
    # (legend[i][1] is colour; we use entry index as a stable proxy for
    # top-to-bottom order since legend entries are already emitted in reading
    # order by _detect_legend).
    def _series_top_y(s):
        pts = getattr(s, "points", None) or []
        if pts and "y_px" in pts[0]:
            return pts[0]["y_px"]
        return 0.0

    ordered_series = sorted(unmatched_series, key=_series_top_y)
    # Legend entries are already in top-to-bottom reading order (index is a
    # reliable proxy). Sort unused_entries by index to preserve that order.
    ordered_entries = sorted(unused_entries)

    for s, ei in zip(ordered_series, ordered_entries):
        s.label = legend[ei][2]


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


def _legend_color_overlap(legend_entries, region, paths) -> int:
    """Count how many distinct legend entry colors are present in a region's paths.

    Returns the number of legend entry colors (non-None) that have at least one
    matching stroke color among the region's data paths (those referenced by
    ``region.path_indices``).  Used to identify which legend panel belongs to
    which data panel in multi-legend sibling groups.
    """
    if paths is None:
        return 0
    # Collect non-None legend colors.
    leg_colors = [e[1] for e in legend_entries if e[1] is not None]
    if not leg_colors:
        return 0
    # Collect stroke colors of paths inside this region.
    data_colors: list = []
    for idx in getattr(region, "path_indices", []):
        if idx < len(paths):
            c = paths[idx].stroke
            if c is not None:
                data_colors.append(c)
    if not data_colors:
        return 0
    matched = 0
    for lc in leg_colors:
        if any(sum(abs(a - b) for a, b in zip(lc, dc)) < _COLOR_TOL
               for dc in data_colors):
            matched += 1
    return matched


def _resolve_legends(legends, regions, paths=None):
    """Per-region legend after shared-legend propagation across split panels.

    A multi-panel figure often draws ONE legend (in a corner of a single panel);
    after the figure is split into sibling regions only that panel carries it.

    Single-legend case: if exactly one panel in a sibling group has a legend,
    copy it to all empty sibling panels.

    Multi-legend case: when several panels carry legends (e.g. a 2-row × 3-col
    figure where each row has its own legend in the first panel), propagate each
    legend to the empty panels whose data-path colors best match that legend's
    entry colors.  Propagation is blocked when the colour match is ambiguous
    (two legend panels score equally well for the same empty panel).
    """
    resolved = list(legends)
    for group in _sibling_groups(regions):
        if len(group) < 2:
            continue
        with_leg = [i for i in group if legends[i]]
        no_leg = [i for i in group if not legends[i]]
        if not with_leg or not no_leg:
            continue

        if len(with_leg) == 1:
            # Unambiguous: single legend panel — copy to all empty siblings.
            shared = legends[with_leg[0]]
            for i in no_leg:
                resolved[i] = shared
        else:
            # Multiple legend panels: match each empty panel to the legend whose
            # entry colors best overlap with that panel's data-path colors.
            for i in no_leg:
                scores = {
                    L: _legend_color_overlap(legends[L], regions[i], paths)
                    for L in with_leg
                }
                best_score = max(scores.values())
                if best_score == 0:
                    # No colour evidence: fall back to order-match if the
                    # sibling group has a simple row-based layout (each legend
                    # panel has direct shares_y_with / shares_x_with links to
                    # the empty panel).
                    direct_donors = [
                        L for L in with_leg
                        if i in list(getattr(regions[L], "shares_y_with", []))
                        + list(getattr(regions[L], "shares_x_with", []))
                    ]
                    if len(direct_donors) == 1:
                        resolved[i] = legends[direct_donors[0]]
                    # else: ambiguous with no colour evidence — leave empty.
                    continue
                # Check for a unique winner.
                winners = [L for L, s in scores.items() if s == best_score]
                if len(winners) == 1:
                    resolved[i] = legends[winners[0]]
                # else: tied — leave empty (wrong_entry is worse than missing).
    return resolved


def parse_pdf(pdf: str, outroot: str, pages_spec: str | None = None) -> list[dict]:
    """Parse one PDF; write artifacts under ``outroot/<stem>/``.

    Returns the manifest rows for every chart (extracted or skipped).
    """
    stem = os.path.splitext(os.path.basename(pdf))[0]
    outdir = os.path.join(outroot, stem)

    # Resolve wanted pages cheaply (page_count loads no drawings) and load only
    # those — so ``--pages`` doesn't pay to parse every page of a long paper.
    wanted = set(_parse_pages(pages_spec, pdf_vector.page_count(pdf)))
    page_list = pdf_vector.load_pdf(pdf, pages=sorted(wanted))

    # Source fitz doc, kept open for per-span font/flags during style recovery
    # (the only data the normalized TextSpan does not carry). Closed at the end.
    fitz_doc = fitz.open(pdf)
    rows: list[dict] = []
    for page in page_list:
        if page.page_index not in wanted:
            continue
        regions = detect_regions(page.paths, page.texts, page.width, page.height,
                                 image_rects=page.image_rects)
        if not regions:
            continue
        axes = calibrate_panels(regions, page.paths, page.texts)
        # Recover leading math-symbol glyph-paths on legend labels by matching
        # them against Computer-Modern mathtext templates (font-less Greek/symbol
        # characters that the text stream drops). Bound to this page's fitz page;
        # a no-op unless such a glyph-path abuts a legend label.
        _fitz_page = fitz_doc[page.page_index]

        def _glyph_recognize(bbox, _fp=_fitz_page):
            try:
                from . import glyph_match
                return glyph_match.recognize_region(_fp, bbox)
            except Exception:
                return None

        # Labels per region, then propagate a shared legend to split panels that
        # have none of their own (one-legend multi-panel figures).
        region_labels = [_region_labels(page, r, _glyph_recognize)
                         for r in regions]
        legends = _resolve_legends([rl[4] for rl in region_labels], regions,
                                   paths=page.paths)
        page_no = page.page_index + 1  # 1-based in output names
        for k, (region, (x_axis, y_axis)) in enumerate(zip(regions, axes), start=1):
            source = {
                "pdf": pdf,
                "page": page.page_index,
                "region_bbox": list(region.bbox),
            }
            # Skip regions identified as 2D non-linear chart types (contour
            # maps, dispersion lattices, credible bands) by the chart-type
            # gate in detect_regions. These cannot be represented as series
            # tables and would emit junk if extracted.
            if region.skip_reason is not None:
                rows.append(io_store.write_skip(
                    region.skip_reason, source, outdir, page_no, k))
                continue
            # A tick whose value breaks the regular pixel<->value spacing was
            # mis-read (merged adjacent labels); OCR re-reads its label region to
            # recover the true value (or drops it), then the axis is re-fitted.
            # No-op without an outlier / when OCR is disabled.
            x_axis, y_axis = _tick_ocr.validate_and_correct(
                x_axis, y_axis, region, fitz_doc[page.page_index])
            # Skip when neither axis could be calibrated.
            if x_axis.calibration is None and y_axis.calibration is None:
                rows.append(io_store.write_skip(
                    "no axis calibration", source, outdir, page_no, k))
                continue

            # Identify annotation arrows and drop their paths before extraction so
            # they are not traced as fake curves/markers (they are recorded
            # separately, not as data).
            arrow_idx, arrow_recs = detect_arrows(region, page.paths)
            if arrow_idx:
                region.path_indices = [i for i in region.path_indices
                                       if i not in arrow_idx]
            # Error-bar whiskers + caps are decoration anchored to the markers,
            # not a data series; drop their paths so they are not traced as a
            # jagged marker-less polyline through the data points.
            errbar_idx, errbar_whiskers = recover_error_bars(region, page.paths)
            if errbar_idx:
                region.path_indices = [i for i in region.path_indices
                                       if i not in errbar_idx]
            # Background grid (axis-aligned lines on the ticks, any colour/dash):
            # recorded as style, not traced as data. Tick positions both confirm
            # real grid lines and recover faint/fragmented ones at a tick.
            grid = detect_grid(region, page.paths,
                               x_ticks=[t.pixel for t in x_axis.ticks],
                               y_ticks=[t.pixel for t in y_axis.ticks])
            if grid:
                # Convert recovered line PIXEL positions to data coords so the
                # renderer can draw each grid / reference line exactly where it is
                # (axvline/axhline), not just at matplotlib's auto major ticks.
                from .calibrate import to_data as _to_data
                if x_axis.calibration is not None:
                    grid["x_lines"] = [float(_to_data(x_axis.calibration, px))
                                       for px in grid.pop("x_px", [])]
                else:
                    grid.pop("x_px", None)
                if y_axis.calibration is not None:
                    grid["y_lines"] = [float(_to_data(y_axis.calibration, px))
                                       for px in grid.pop("y_px", [])]
                else:
                    grid.pop("y_px", None)

            rl = region_labels[k - 1]
            legend_bbox = rl[5] if len(rl) > 5 else None
            series = _region_series(page, region, x_axis, y_axis, source,
                                    legend_bbox)
            # An extraction with no clean series / no data points is a skip, not
            # an empty "extracted" record (precision over recall).
            if not series:
                rows.append(io_store.write_skip(
                    "no series extracted", source, outdir, page_no, k))
                continue
            # Recovered error bars: attach a per-point y_err (data units) to the
            # series point whose x coincides with each whisker, so the renderer can
            # re-draw the uncertainty bars instead of dropping them.
            if errbar_whiskers and y_axis.calibration is not None:
                _attach_error_bars(series, errbar_whiskers, y_axis)
            title, caption, _x_title, _y_title, _ = rl[:5]
            # In stacked multi-panel figures the title detector can grab the
            # neighbouring panel's axis LABEL (which sits just above this panel).
            # Drop a "title" that merely duplicates this panel's own axis title.
            title = _drop_title_if_axis_dup(title, x_axis, y_axis)
            legend = legends[k - 1]
            _apply_legend_labels(series, legend, region, page.paths)
            # Axis titles come from axes.py (robust: rejects sub-captions/figure
            # captions, returns None when there is no real title). We deliberately
            # do NOT fall back to labels.py's x/y title, which can grab caption
            # body text below the figure.
            record = io_store.chart_to_record(
                pdf=pdf,
                page=page.page_index,
                region_bbox=region.bbox,
                x_axis=x_axis,
                y_axis=y_axis,
                series=series,
                title=title,
                caption=caption,
                arrows=arrow_recs,
                grid=grid,
                confidence=1.0 if (x_axis.calibration and y_axis.calibration) else 0.5,
            )
            # OCR backfill: fill EMPTY axis-title slots from zone-targeted OCR
            # (text drawn outside the plot crop / as outlines that get_text misses).
            # Detection-gated, never overrides vector text; no-op if OCR disabled.
            _ocr_backfill.backfill(record, fitz_doc[page.page_index])
            # Self-contained STYLE block: rendering attributes recovered at parse
            # time so a downstream renderer never re-opens the source PDF for style.
            record["style"] = _style.build_chart_style(
                record, page, fitz_doc[page.page_index])
            rows.append(io_store.write_chart(record, outdir, page_no, k))
    fitz_doc.close()
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
