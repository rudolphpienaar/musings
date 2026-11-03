#!/usr/bin/env bash
# rush_executor_supervise.sh: keep a rush executor attached, and say why it died.
#
# An executor that dies leaves rush with nothing to talk to until it is
# started again, so the size of that gap is the size of the restart
# delay. This script restarts musings-rush-executor the moment it exits,
# backing off only when it dies again immediately, and logs one line per
# death with the exit status, because the status is the diagnosis: 137
# is SIGKILL (the OOM killer or the platform), 143 is SIGTERM, anything
# else came from Python and will have a traceback above it.
#
# Usage:
#   scripts/rush_executor_supervise.sh [executor args...]
#
# Environment:
#   MUSINGS_URL, MUSINGS_TOKEN      as for the executor itself
#   SUPERVISE_MAX_BACKOFF_SECONDS   cap on the restart delay (default 60)
#   SUPERVISE_HEALTHY_SECONDS       a run at least this long resets the backoff (default 60)
#   SUPERVISE_COMMAND               command to supervise (default musings-rush-executor)
#   SUPERVISE_MAX_RESTARTS          stop after this many restarts (default unlimited; for tests)

set -u

command="${SUPERVISE_COMMAND:-musings-rush-executor}"
maxBackoff="${SUPERVISE_MAX_BACKOFF_SECONDS:-60}"
healthySeconds="${SUPERVISE_HEALTHY_SECONDS:-60}"
maxRestarts="${SUPERVISE_MAX_RESTARTS:-}"
backoff=2
restarts=0

stamp() {
    date -u +%Y-%m-%dT%H:%M:%SZ
}

signal_name() {
    # Map an exit status above 128 to the signal that produced it.
    local code="$1"
    if [ "$code" -gt 128 ]; then
        kill -l $((code - 128)) 2>/dev/null || echo "signal $((code - 128))"
    fi
}

trap 'echo "$(stamp) supervisor stopped"; exit 0' INT TERM

while :; do
    started=$(date +%s)
    echo "$(stamp) supervisor starting: $command $*"
    $command "$@"
    code=$?
    ran=$(( $(date +%s) - started ))
    reason="exit $code"
    name=$(signal_name "$code")
    [ -n "$name" ] && reason="$reason ($name)"
    echo "$(stamp) executor died after ${ran}s: $reason" >&2
    restarts=$((restarts + 1))
    if [ -n "$maxRestarts" ] && [ "$restarts" -ge "$maxRestarts" ]; then
        echo "$(stamp) supervisor giving up after $restarts restarts" >&2
        exit 1
    fi
    if [ "$ran" -ge "$healthySeconds" ]; then
        backoff=2
    fi
    echo "$(stamp) restarting in ${backoff}s" >&2
    sleep "$backoff"
    backoff=$(( backoff * 2 ))
    [ "$backoff" -gt "$maxBackoff" ] && backoff="$maxBackoff"
done
