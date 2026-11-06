#!/bin/bash
set -uo pipefail
APP_DIR="${APP_DIR:-/app}"
CONFIG_DIR="${CONFIG_DIR:-/app/config}"
accounts=$(python3 -c 'import profiles_lib; print("\n".join(profiles_lib.get_hh_accounts()))')
for acc in $accounts; do
    if [ -f "$CONFIG_DIR/$acc/token.json" ]; then
        python3 "$APP_DIR/hh_cli.py" --config-dir "$CONFIG_DIR" --profile "$acc" --no-use-ai --no-handle-captcha "$@"
    fi
done
