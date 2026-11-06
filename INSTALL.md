# Installation and operation

Requires Linux, Docker Engine, Docker Compose v2 supporting up --wait. Follow README to create .env and profiles.json, then build and start. All three services must be healthy in docker compose ps.

The main service runs cron and auth_helper; bot and watcher use UID 1000. The entrypoint prepares writable runtime directories. Rebuild and recreate all services after updates: dependencies and crontab are baked into the image.

Authorize each account from Telegram, select a published resume, configure search and letter. Start with dry-run, then preview one allowed manual vacancy before confirming a real response.

HTTP 401: reauthorize if token refresh fails. HTTP 403/CAPTCHA: complete the HH check. HTTP 429: wait for restrictions to clear. Resume after resolving the cause. An ambiguous POST must be checked in HH history before trying again.

Pause blocks new runs and terminates the automatic process group. Manual processing checks pause between vacancies. Required manual tests are completed on HH.

Runtime files: config/run_history.json (last 100 automatic runs), config/schedule.json, config/.watcher_state.json. Statistics are limited by retained account logs. docker compose down preserves bind-mounted data; keep config/, letters/, profiles.json and .env when moving installations.

Backups are sent only on explicit request. If BACKUP_PASSWORD is configured and encryption fails, sending is cancelled. No automatic credential upload runs in cron. Decrypt with:

```bash
openssl enc -d -aes-256-cbc -pbkdf2 -in backup.tar.gz.enc -out backup.tar.gz
```

The password is prompted interactively. Stop the old bot instance before starting a new one with the same token. Intended for one trusted operator in a private chat.
