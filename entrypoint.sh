#!/bin/bash
set -euo pipefail
umask 077
mkdir -p /app/config /app/letters
chown 1000:1000 /app /app/config /app/letters
# Bind-mounted data must be writable by all three services.
find /app/config /app/letters -exec chown 1000:1000 {} +
[ ! -f /app/profiles.json ] || chown 1000:1000 /app/profiles.json
[ ! -f /app/profiles.lock ] || chown 1000:1000 /app/profiles.lock
python3 - <<'PY'
import json
import os
from storage import atomic_write

keys = (
    "TG_BOT_TOKEN", "TG_CHAT_ID", "TZ", "CONFIG_DIR", "APP_DIR",
    "BACKUP_PASSWORD", "HH_PROFILES_FILE",
    "APPLY_SEGMENTED", "APPLY_DISCOVERY_DAYS", "APPLY_DISCOVERY_REQUESTS",
)
atomic_write("/app/config/.cron_environment.json", json.dumps({key: os.environ[key] for key in keys if key in os.environ}))
PY
chown 1000:1000 /app/config/.cron_environment.json
touch /var/log/cron.log
chown 1000:1000 /var/log/cron.log
cron
tail -F /var/log/cron.log &
TAIL_PID=$!
su docker -s /bin/bash -c 'exec python3 /app/auth_helper.py' &
AUTH_PID=$!
shutdown() {
    kill -TERM "$AUTH_PID" 2>/dev/null || true
    kill -TERM "$TAIL_PID" 2>/dev/null || true
    pkill -TERM -x cron || true
    wait "$AUTH_PID" 2>/dev/null || true
}
trap 'shutdown; exit 0' TERM INT
while kill -0 "$AUTH_PID" 2>/dev/null && pgrep -x cron >/dev/null; do sleep 2 & wait $! || true; done
shutdown
exit 1
