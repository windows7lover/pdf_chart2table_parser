# Real-PDF chart detection findings (M3)

Corpus: 8 open-access arXiv PDFs (see `data/real_pdfs/SOURCES.md`). All pages
were rendered and hand-labelled; vector primitives characterized with
`scripts/inspect_pdf.py` and ad-hoc probes. Ground truth in
`tests/real/labels.json`.

## How real chart frames are drawn (vs the synthetic white-patch assumption)

The synthetic fixtures are single matplotlib charts that fill most of the page,
drawn with a large white unstroked axes-patch rectangle covering
`>= 10%` of the page area. **None of these assumptions hold on real pages:**

1. **Chart panels are SMALL relative to the page.** A real chart is a *figure*
   embedded in a text page (often 2-6 panels), so each panel's axes-patch
   covers only ~**0.04-0.07** of the page area — well under the synthetic
   `_MIN_AREA_FRAC = 0.10`. This single threshold caused most false negatives:
   matplotlib pages (Adam p5, SGDR p4) DO have white patches, but they were
   rejected as too small.

2. **Many toolchains draw NO white axes-patch at all.** pgfplots/TikZ
   (Super-Convergence, 1708.07120) draws no fill rectangle; the plot background
   is the page white. The white-patch path finds nothing there.

3. **Axis spines are rarely a single long path.**
   - matplotlib draws each spine as one segment, but the y-spine of a small
     panel is short in absolute points and < the synthetic `0.3*page_height`
     spine threshold (it can be < 25% of page height). At a 15% threshold
     matplotlib y-spines reappear, but pgfplots ones still do not...
   - pgfplots draws the frame as **many short collinear segments** (e.g. 9
     vertical segments of ~11pt each stacked at the same x), one per gridline
     gap. To recover a "spine" you must **merge collinear segments** sharing an
     x (vertical) or y (horizontal) coordinate. A raw "long single line" search
     finds `longV = 0` on every real chart page.

4. **The strongest, most portable signal is a dense vertical/horizontal line of
   axis-aligned segments**, i.e. project all near-vertical segment x-positions
   and all near-horizontal segment y-positions; a chart frame shows up as one
   x with a tall span of stacked vertical segments (left spine) and one y with a
   wide span of horizontal segments (bottom spine). This works for both
   matplotlib and pgfplots.

## Markers / lines representation

- Markers are small repeated paths (`<= ~10` vertices): circles flattened from
  beziers, squares/triangles as `l`/`re`. Consistent with synthetic. Dense
  scatter pages have hundreds of these.
- Data lines are long polylines (`> 10` vertices). Noisy real curves (SGDR) are
  very long polylines, often overlapping many colors.
- **Colored stroke/fill is a good chart signal**: real charts use multiple
  saturated series colors (blue/green/red/cyan). Body-text math and tables are
  almost entirely black + None.

## Density of non-chart vector content (false-positive sources)

Real pages are full of vector content that is NOT a line/scatter chart, and
each is a concrete false-positive trap:

- **Equations / math** (1412.6980 p12-13, 1609.04747 p5-p8): fraction bars,
  radicals and sqrt vincula register as dozens of short horizontal segments and
  small paths. No 2-D frame, all black -> must be rejected.
- **Tables** (1708.07120 p6/p13, 1803.09820 p9/p14, ViT p12/p21): long
  horizontal rules + many cell rectangles. A naive "long horizontal line"
  detector flags these. They have horizontal rules but no matching tall vertical
  spine and no colored data marks/lines.
- **Schematic / architecture diagrams** (ViT p2): boxes + arrows. Has a large
  bounding rectangle -> the OLD detector FALSE-POSITIVED here.
- **Raster image grids** (ViT p20 attention maps, p17 top): grids of embedded
  images become many rectangles. The OLD detector FALSE-POSITIVED on p20.
- **Boxplots** (1812.01097 p4 left): out of scope; only the scatter half counts.
- **Colored legend dots** (ViT p21 table): small colored marks WITHOUT a frame.

So a chart-vs-not filter must require BOTH an axis frame (perpendicular
spine pair) AND chart-like interior content (colored data marks/lines + nearby
numeric tick labels), and must reject regions that are dominated by a
text/table/image fill.

## Toolchains observed

- **matplotlib**: Adam (1412.6980), Batch-Norm (1502.03167), SGDR (1608.03983),
  ViT (2010.11929). Has white axes-patch + full stroked frame.
- **pgfplots / TikZ**: Super-Convergence (1708.07120). No patch; frame as
  stacked short segments; tick marks as tiny perpendicular segments.
- **raster only** (no extractable vector chart): 1609.04747, 1803.09820 figures.

## M2 (tick/label) assumptions that may break on real charts

- **Minus sign as a glyph**: real labels use the typographic minus / hyphen
  ("-3") and sometimes superscript exponents for log (`10^{-3}` rendered as
  separate "10" + superscript spans). M2's numeric parsing must handle the
  unicode minus and split exponent spans.
- **Log labels as `10^k`**: pgfplots/matplotlib log axes print `10^-1`,
  `10^1`... as a base span plus a superscript span (two TextSpans), not a single
  "0.1" string. Tick-label parsing must reassemble these.
- **Shared tick text far from the panel**: in subplot grids the tick labels sit
  only on the outer panels; inner panels have no nearby numeric text (the
  shared-axis borrowing in M2 is needed, and the tick-text scan bands are
  small relative to the inter-panel gaps).
- **Body text inside the scan band**: because panels are small and surrounded by
  prose, the tick-label scan band below/left of a panel can catch caption or
  paragraph text. Numeric-only filtering and tight bands are required.

## Implication for `plot_region`

The white-patch keying is one signal among several but is neither necessary
(pgfplots) nor sufficient (it under-fires due to area threshold, over-fires on
diagrams/images). The hardening (below) adds a **merged-spine frame detector**
gated by **chart-like interior content** and a **non-chart rejection filter**,
keeping the white-patch as a corroborating signal. See the report for
before/after precision-recall.

## Second pass (corpus broadening + stroked-rect frames)

Corpus broadened to 14 PDFs (+ResNet, +large-batch, +BERT positives; +Bengio,
+VGG, +Transformer pure-negatives). Key new finding:

- **Some axes frames are a single *stroked* rectangle** (no white fill), e.g.
  BERT (1810.04805) Fig 5 / p15. The white-patch path misses these because the
  rect is stroked, and the spine/merged-spine paths miss them because the frame
  is one short path, not a long line or stacked segments. Added a
  `_rect_frame_regions` candidate: any axis-aligned rectangle path in the size
  band. A *raw* rectangle, however, also bounds **schematic/architecture
  diagrams** (BERT p4/p12 embedding-box figures, whose arrow connectors are long
  polylines) - the lenient gate flagged those. The fix: rect-frame candidates
  use a **stricter content gate** requiring saturated-colour *stroked* data
  paths (real data series), not bare polylines or box fills. Schematics have
  saturated box *fills* and connector polylines but no saturated data strokes,
  so they are rejected; precision stays at FP=0.

Remaining FNs are charts drawn as **raster images** (Super-Convergence Fig 5/6,
LEAF p4 scatter) and one **tiny matplotlib panel** (BatchNorm p5, ~0.45% of the
page, below the area floor) - none recoverable without lowering the area gate
into FP territory or doing raster/OCR analysis (out of scope for M3).
