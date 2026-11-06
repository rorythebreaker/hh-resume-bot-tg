#!/bin/bash
set -uo pipefail
APP_DIR="${APP_DIR:-/app}"
CONFIG_DIR="${CONFIG_DIR:-/app/config}"
PAUSE_FLAG="$CONFIG_DIR/.paused"
PID_FILE="$CONFIG_DIR/.apply.pid"
if [ -f "$PAUSE_FLAG" ]; then echo 'Paused; skipping apply'; exit 0; fi
if ! printenv HH_BOT_PROFILE >/dev/null; then
    HH_BOT_PROFILE=$(python3 -c 'import json,profiles_lib; print(json.dumps(profiles_lib.get_active_profile()))')
    export HH_BOT_PROFILE
fi
LOG=$(mktemp)
RUN_START=$(date '+%Y-%m-%d %H:%M:%S')
cleanup() {
    [ -z "${WATCHER_PID:-}" ] || kill "$WATCHER_PID" 2>/dev/null || true
    if [ -n "${APPLY_PID:-}" ]; then kill -TERM -- "-$APPLY_PID" 2>/dev/null || true; fi
    rm -f "$LOG"
    if [ -f "$PID_FILE" ] && [ "$(cat "$PID_FILE")" = "${APPLY_PID:-}" ]; then rm -f "$PID_FILE"; fi
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
setsid python3 "$APP_DIR/run-apply.py" > "$LOG" 2>&1 &
APPLY_PID=$!
echo "$APPLY_PID" > "$PID_FILE"
(
    while kill -0 "$APPLY_PID" 2>/dev/null; do
        if [ -f "$PAUSE_FLAG" ]; then
            kill -TERM -- "-$APPLY_PID" 2>/dev/null || true
            sleep 3
            kill -KILL -- "-$APPLY_PID" 2>/dev/null || true
            break
        fi
        sleep 1
    done
) &
WATCHER_PID=$!
wait "$APPLY_PID"
RC=$?
cat "$LOG"
LOG_FILE="$LOG" RC="$RC" RUN_START="$RUN_START" python3 "$APP_DIR/_summary.py" || true
exit "$RC"
