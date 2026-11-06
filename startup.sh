#!/bin/bash
echo "[$(date)] Running startup tasks..."

# echo "Current user: $(whoami)"
# echo "$CONFIG_DIR"

# Выполняем цепочку
# Resume refresh is handled by cron; startup only prepares the daemon.
# Scheduled applies run via cron; no automatic bulk apply on every restart.

echo "[$(date)] Startup tasks finished."
