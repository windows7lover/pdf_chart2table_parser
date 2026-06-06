#!/usr/bin/env bash
# Regenerate the shared-folder example bundles on the CURRENT code (iter-6).
# Re-batches ONLY the curated papers already present in each example dir
# (their paper.pdf), rebuilds the bundles, and refreshes feedback_recheck.
set -uo pipefail
cd /network/projects/sail/damien/github/pdf_chart2table_parser
export UV_LINK_MODE=copy
SF="$HOME/shared_folder/chart2table_examples"
REGEN="$SCRATCH/pdf_chart2table/regen_iter6"
rm -rf "$REGEN" /tmp/regen
mkdir -p "$REGEN" /tmp/regen

# corpus example dir -> max-line-series cap (exclude many-line plots)
build_corpus () {
  local dir="$1"          # e.g. astro_examples
  echo "===== $dir ====="
  local src="$SF/$dir"
  local tmp="/tmp/regen/$dir"
  mkdir -p "$tmp"
  # copy each paper.pdf to <id>.pdf so parse_pdf names subdirs by arxiv id
  for pf in "$src"/*/paper.pdf; do
    [ -e "$pf" ] || continue
    id=$(basename "$(dirname "$pf")")
    cp "$pf" "$tmp/$id.pdf"
  done
  echo "$dir: $(ls "$tmp"/*.pdf 2>/dev/null | wc -l) papers to re-batch"
  # re-extract on current code, parallel across papers (one worker per paper)
  uv run python scripts/parallel_batch.py "$REGEN/$dir" "$tmp/*.pdf" "${JOBS:-16}"
  # clear old bundle, rebuild from fresh batch (all kept charts, <=2 line series)
  rm -rf "$src"
  uv run python scripts/build_examples_from_batch.py \
      --batch "$REGEN/$dir" --out "$src" --all --max-line-series 2
}

build_corpus pdf_chart2table_examples
build_corpus astro_examples
build_corpus semiconductor_examples

echo "===== feedback_recheck ====="
uv run python scripts/render_feedback_recheck.py

echo "===== FINAL COUNTS ====="
for d in pdf_chart2table_examples astro_examples semiconductor_examples feedback_recheck; do
  papers=$(ls -d "$SF/$d"/*/ 2>/dev/null | wc -l)
  pngs=$(find "$SF/$d" -name 'reconstruction.png' -o -name '*.png' 2>/dev/null | wc -l)
  echo "$d: $papers dirs, $pngs pngs"
done
echo "REGEN_DONE"
