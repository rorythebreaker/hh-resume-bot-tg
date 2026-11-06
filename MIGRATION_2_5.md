# Migrate 1.8.x to 2.5.4 and roll back

Stop services. Make a private backup OUTSIDE the project of .env, profiles.json, letters/, config/, old sources and image. Never publish this backup.

```bash
docker compose down
# Install new sources, then:
docker compose build
docker compose run --rm --no-deps --user 1000:1000 hh_applicant_tool python migrate_config.py --config-dir /app/config
docker compose run --rm --no-deps --user 1000:1000 hh_applicant_tool python migrate_config.py --config-dir /app/config --write
docker compose up -d --wait
```

Inspect dry-run output first. Migration retains config.json, creates token.json and config.toml, converts legacy AI sections to openai.cover_letter, openai.vacancy_filter and openai.captcha, and removes /chat/completions from base URLs. Existing targets are never overwritten.

For a legacy root configuration, copy it into config/main/ before migration. Only main can read legacy root tokens; other accounts never inherit them. The new core always uses its account subdirectory.

Validate whoami per account, resume selection, letter and AI dry-run. Reauthorization may be needed. Old Playwright patches are not applied.

Rollback: stop new containers, restore old sources/image and backed-up runtime data, start old Compose. Never run both versions with one Telegram token. Validate rollback with live credentials: token refresh can invalidate the backed-up refresh token and require a fresh login.
