#!/usr/bin/env bash
# Monitored shared-folder regen. Launches the regen DETACHED (so a harness
# background glitch can't kill it), then polls jobstat until DONE / STALLED /
# CRASHED, bounded by max_min so it never hangs. Prints live state + the metrics
# table at the end.
#
#   scripts/run_regen.sh "<loop label>" [max_min=10] [stall_secs=180]
set -u
ROOT=/network/projects/sail/chart2table/arxiv_semicond
REPO=/network/projects/sail/damien/github/pdf_chart2table_parser
log="$ROOT/regen.log"
label="${1:-}"; maxmin="${2:-10}"; stall="${3:-180}"
cd "$REPO"
export PDFCHART_OCR=1 OMP_NUM_THREADS=1 UV_LINK_MODE=copy LOOP_LABEL="$label"

rm -rf "$ROOT/restyle_prototype"/*
: > "$log"
# Detached; drop the noisy (benign) MuPDF recoverable-xref warnings, keep live.
nohup bash -c "bash '$ROOT/_regen_restyle.sh' 2>&1 | grep --line-buffered -v 'MuPDF error'" >> "$log" 2>&1 &
pid=$!
echo "regen launched pid=$pid log=$log label='$label'"

for i in $(seq 1 $(( maxmin * 2 )) ); do
    sleep 30
    bash "$REPO/scripts/jobstat.sh" "$log" "$pid" "$stall" > /tmp/regen_stat 2>&1
    rc=$?
    head -1 /tmp/regen_stat
    [[ $rc -ne 2 ]] && break          # 0 done / 3 crashed / 4 stalled -> stop polling
done

echo "=== final regen status ==="
cat /tmp/regen_stat
echo "=== metrics ==="
grep -A 40 "performance on the image set" "$log" || echo "(no metrics table in log)"
