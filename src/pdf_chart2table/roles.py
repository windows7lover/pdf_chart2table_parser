"""Single authority for a region's primitive ROLES (Layer B).

Background
----------
``marks.classify_marks`` (markers), ``lines.classify_lines`` (curves) and the
legend-swatch detection used to each INDEPENDENTLY decide what a path is, all
running over the same in-region paths.  They coordinated through a fragile hack:
``extract.extract_region`` re-derived ``marker_colors`` + ``marker_centroids``
from the marker output and passed them into ``classify_lines`` so a line in a
marker colour could be suppressed when it merely traced the markers.  Each
classifier re-deciding a path's nature (and the cross-talk wiring living in the
orchestrator) caused the top failure modes: double-count (a connector kept as an
extra series) and drop (a real solid line suppressed because its colour matched a
marker colour).

This module makes ONE pass decide each contested path's role, so marks, lines and
legend swatches AGREE on a single assignment instead of re-deriving it.  The
marker→line cross-talk now lives HERE (not in ``extract.py``): markers are
classified first, their colours/centroids are computed once, and the curve pass
consumes them — exactly the former behaviour, but encoded as a shared role
decision rather than orchestrator plumbing.

The intentional line+marker case is preserved: a series drawn as BOTH a line and
markers still yields the markers (role ``marker``) and the coincident connector
line is recorded as a suppressed curve (role ``data_curve`` + ``connector``
flag), exactly as before.

Public API
----------
    Role (str enum-like constants)
    RegionRoles (dataclass: per-path roles + marker/line series + ambiguity)
    classify_roles(region, paths, texts, plot_box, legend_bbox) -> RegionRoles
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .lines import (
    SeriesLine,
    _is_fragment,
    _is_long_curve,
    classify_lines,
)
from .marks import (
    SeriesMarks,
    _collect_large_fills,
    _is_data_mark,
    classify_marks,
)
from .model import Path, Region, TextSpan
from .primitives import round_color as _round_color

# --------------------------------------------------------------------------
# Role vocabulary
# --------------------------------------------------------------------------
# The single role assigned to each in-region path by classify_roles.  Only the
# CONTESTED set (paths the marker/curve/fill classifiers fight over) is resolved
# here; plain decorations (spines / ticks / gridlines / frames / background) are
# omitted from the map deliberately — they are uncontested and carry no signal.
MARKER = "marker"            # a data-marker glyph (scatter / line-with-markers)
DATA_CURVE = "data_curve"    # a marker-less data curve (or curve fragment)
FILL_BAND = "fill_band"      # a wide filled band (DOS / SED / confidence band)
AMBIGUOUS = "ambiguous"      # plausibly fits >1 role with similar evidence


# A region is low-confidence when this fraction of its DATA paths (markers +
# curves + ambiguous, i.e. the contested non-decoration set) are ambiguous: the
# marker and curve classifiers genuinely contend over the same paths, so the
# extraction cannot be trusted.  Precision over recall -> skip such a region.
AMBIGUITY_SKIP_FRAC = 0.5
# Only apply the ambiguity skip when there are at least this many contended data
# paths: a 1-of-2 fluke must not skip a tiny region.
AMBIGUITY_MIN_DATA_PATHS = 4

SKIP_REASON = "ambiguous primitive roles"

# A marker series with at least this many marks is a "strong" anchor whose colour
# participates in curve-connector suppression.  Mirrors extract's
# _MIN_MARKS_PER_SERIES (2-mark faint series rarely have a matching connector).
_MIN_STRONG_MARKS = 3


@dataclass
class RegionRoles:
    """The single role assignment for a region's contested paths.

    ``roles`` maps a path index (within ``paths``) to its resolved role.  Only
    in-region paths that are markers, data curves, fill bands or ambiguous appear
    (plain decorations are omitted to keep the map small and meaningful).

    ``marker_series`` / ``line_series`` are the SAME objects ``classify_marks`` /
    ``classify_lines`` produced — the curve pass already had the marker
    colours/centroids applied, so the line+marker connector suppression is baked
    in.  ``line_skips`` carries the curve pass's skip reasons.

    ``n_data_paths`` / ``n_ambiguous`` summarise the contention; ``ambiguous``
    is True when the region's role assignment is too contested to trust.
    """

    roles: dict[int, str] = field(default_factory=dict)
    marker_series: list[SeriesMarks] = field(default_factory=list)
    line_series: list[SeriesLine] = field(default_factory=list)
    line_skips: list[str] = field(default_factory=list)
    n_data_paths: int = 0
    n_ambiguous: int = 0
    ambiguous: bool = False


def classify_roles(
    region: Region,
    paths: list[Path],
    texts: list[TextSpan],
    plot_box: tuple | None = None,
    legend_bbox: tuple | None = None,
) -> RegionRoles:
    """Decide each contested in-region path's single role, once.

    Order of authority (markers win a contested glyph, then curves, then the
    marker→curve cross-talk decides connectors):

    1. ``classify_marks`` produces the marker series.  The colours and centroids
       of the STRONG marker series (the same subset ``extract`` used) are
       computed here and handed to the curve pass — this is the former
       ``marker_colors`` / ``marker_centroids`` cross-talk, now internal.
    2. ``classify_lines`` produces the curve series with connector suppression
       already applied (a same-colour line tracing the markers is suppressed; a
       distinct same-colour line is kept).
    3. A per-path role map is built from these outputs plus the shared predicate
       vocabulary, and an ambiguity metric flags paths claimed by BOTH the marker
       and the curve classifier.
    """
    region_texts = [texts[i] for i in region.text_indices]

    # --- 1. Markers (the marker authority) --------------------------------
    marker_series = classify_marks(region, paths, texts, plot_box, legend_bbox)
    strong_marks = [sm for sm in marker_series
                    if len(sm.marks) >= _MIN_STRONG_MARKS]

    # Former extract.py cross-talk, now owned here: a curve sharing a strong
    # marker colour is only suppressed when it geometrically coincides with the
    # markers (a connector), not by colour alone.  Accumulate ALL centroids per
    # colour so the multitrack-ratio guard sees 2x centroids when one colour
    # carries two marker trajectories.
    marker_colors = {_round_color(sm.fill or sm.stroke) for sm in strong_marks}
    marker_centroids: dict[tuple, list[tuple[float, float]]] = {}
    for sm in strong_marks:
        color = _round_color(sm.fill or sm.stroke)
        if color is None:
            continue
        pts = [(m.cx, m.cy) for m in sm.marks]
        marker_centroids.setdefault(color, []).extend(pts)

    # --- 2. Curves (consume the marker decision) --------------------------
    line_series, line_skips = classify_lines(
        region, paths, texts, marker_colors, plot_box,
        marker_centroids=marker_centroids,
    )

    # --- 3. Per-path role map + ambiguity ---------------------------------
    large_fills = _collect_large_fills(paths, region)
    large_fill_ids = {id(fp) for fp in large_fills}

    roles: dict[int, str] = {}
    n_data = 0
    n_ambiguous = 0
    for i in region.path_indices:
        p = paths[i]
        is_mark = _is_data_mark(p, region, large_fills)
        is_curve = _is_long_curve(p, region, region_texts) or _is_fragment(
            p, region, region_texts
        )
        if is_mark and is_curve:
            # A single path that BOTH the marker and the curve classifier accept
            # is genuinely ambiguous: the two authorities contend over it.
            roles[i] = AMBIGUOUS
            n_ambiguous += 1
            n_data += 1
        elif is_mark:
            roles[i] = MARKER
            n_data += 1
        elif is_curve:
            roles[i] = DATA_CURVE
            n_data += 1
        elif id(p) in large_fill_ids:
            roles[i] = FILL_BAND
        # plain decorations (spines / ticks / gridlines / background) are left
        # out of the map deliberately (precision over noise).

    # A region whose contested data paths are largely ambiguous cannot be trusted.
    ambiguous = (
        n_data >= AMBIGUITY_MIN_DATA_PATHS
        and n_ambiguous >= AMBIGUITY_SKIP_FRAC * n_data
    )

    return RegionRoles(
        roles=roles,
        marker_series=marker_series,
        line_series=line_series,
        line_skips=line_skips,
        n_data_paths=n_data,
        n_ambiguous=n_ambiguous,
        ambiguous=ambiguous,
    )
