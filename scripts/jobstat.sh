#!/usr/bin/env bash
# Robust status for a detached, log-writing job. Detects crash vs stall vs done.
#
#   scripts/jobstat.sh <logfile> [pid] [stall_secs]
#
# Prints a one-line STATE plus the log tail, and EXITS with a state code so a
# poll loop can branch:
#   0 DONE     log contains the completion sentinel (REGEN_EXIT 0 / DONE_REGEN)
#   3 CRASHED  sentinel present with nonzero code, OR pid given and not alive
#              and no completion sentinel
#   4 STALLED  no completion, log not written for > stall_secs (default 180)
#   2 RUNNING  none of the above (log advancing)
set -u
log="${1:?usage: jobstat.sh <logfile> [pid] [stall_secs]}"
pid="${2:-}"
stall="${3:-180}"

if [[ ! -f "$log" ]]; then
    echo "MISSING  log not found: $log"; exit 4
fi
now=$(date +%s)
mtime=$(stat -c %Y "$log")
age=$((now - mtime))
last=$(grep -E "REGEN_EXIT|DONE_REGEN" "$log" | tail -1)

state="RUNNING"; code=2
if echo "$last" | grep -q "REGEN_EXIT 0\|DONE_REGEN"; then
    state="DONE"; code=0
elif echo "$last" | grep -q "REGEN_EXIT [1-9]"; then
    state="CRASHED ($last)"; code=3
elif [[ -n "$pid" ]] && ! kill -0 "$pid" 2>/dev/null; then
    state="CRASHED (pid $pid gone, no completion sentinel)"; code=3
elif (( age > stall )); then
    state="STALLED (no log write for ${age}s > ${stall}s)"; code=4
fi
echo "STATE=$state  log_age=${age}s  pid=${pid:-n/a}"
echo "--- tail ---"; tail -4 "$log"
exit $code
