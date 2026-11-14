#!/bin/bash
# Restart loop for a helper daemon. Stops when the script exits 3 ("already running").
#   run-forever.sh LOG -- python3 script.py args...
# Start detached:  setsid nohup ./run-forever.sh ~/.local/state/agent-helpers/x.log -- python3 x.py ... >/dev/null 2>&1 &
set -u
log=$1; shift; [ "${1:-}" = -- ] && shift
mkdir -p "$(dirname "$log")"
while true; do
    "$@" >>"$log" 2>&1
    rc=$?
    [ "$rc" = 3 ] && { echo "$(date +%T) exit 3 (already running); loop stops" >>"$log"; exit 0; }
    echo "$(date +%T) exited $rc; restarting in 5 s" >>"$log"
    sleep "${RESTART_DELAY:-5}"
done
