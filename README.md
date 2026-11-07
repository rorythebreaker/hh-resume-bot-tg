# HH Resume Bot TG

Telegram control for **hh-applicant-tool 2.5.4**, **Python 3.12**, Docker Compose.

## Install

```bash
git clone https://github.com/rorythebreaker/hh-resume-bot-tg.git
cd hh-resume-bot-tg
cp .env.example .env
cp profiles.example.json profiles.json
# Set TG_BOT_TOKEN and TG_CHAT_ID in .env
chmod 600 .env profiles.json
docker compose up -d --build --wait
```

Open a private chat and send /start. Create/select a profile, authorize its HH account, and select a published resume. Each account has its own config/ACCOUNT directory.

## Features

- Inline menus: accounts, profiles, resume selection, letters and search filters.
- Region, salary, experience, work format, schedule and employer exclusions.
- Single-use confirmation before bulk applications; default maximum 50 per run.
- Manual applications: links, vacancy preview, confirmation, AI or template letter.
- Pause/resume; editable hourly Moscow schedule, default 08-21, with 1-5 minute jitter.
- AI provider, model, prompts, request pacing; optional AI disable and dry-run.
- Account statistics, bounded run history, watcher notifications.
- Cross-process application lock, pinned sender profile, atomic profile storage.
- Upstream authorization and CAPTCHA; Kitty image relay, no Playwright patches.

Automatic questionnaires use upstream capabilities. Manual vacancies requiring tests are handed off to HH. Ambiguous POST results are reported and never automatically replayed. AI filtering enables upstream's global AI mode, which can also generate letters and solve tests. Review generated text: factual accuracy cannot be guaranteed.

## Configuration

| Environment | Purpose |
|---|---|
| TG_BOT_TOKEN | Telegram bot token |
| TG_CHAT_ID | Allowed private chat |
| TZ | Container timezone, Europe/Moscow by default |
| TG_INTERACTIVE_BUTTONS | 1 enables supported colored inline buttons |
| BACKUP_PASSWORD | Encryption password for explicitly requested backups |
| APP_DIR, CONFIG_DIR | Fixed to /app and /app/config in Compose |
| TG_API_ROOT | Optional Telegram API root; local stub in CI |

Data persists in the project bind mount. Never run two bot instances with one Telegram token. Runtime tokens, cookies, profiles and custom letters are excluded from source publication.

Profile fields: hh_account, resume_id, search, letter, area, excluded_filter, filters, ai. excluded_filter is a comma-separated list converted to a regex. filters.max_responses sets the per-run maximum in the profile filters menu.

Edit schedule in the control menu, or use CLI:

```bash
docker compose exec --user 1000:1000 hh_applicant_tool python schedule_config.py --hours 8-21
docker compose exec --user 1000:1000 hh_applicant_tool python hh_cli.py --profile main --no-use-ai whoami
docker compose exec --user 1000:1000 hh_applicant_tool python hh_cli.py --profile main --manual-captcha --use-kitty auth
docker compose exec --user 1000:1000 hh_applicant_tool python hh_cli.py --profile main config openai.default.base_url https://api.openai.com/v1
docker compose exec --user 1000:1000 hh_applicant_tool python hh_cli.py --profile main config openai.default.model MODEL_NAME
```

Set API keys in the private AI menu; input messages are deleted. The core reads config/ACCOUNT/config.toml and token.json. AI sections: openai.default, openai.cover_letter, openai.vacancy_filter, openai.captcha. Supply base URLs without /chat/completions. Profile letters and prompts are overlaid in memory, without rewriting shared account settings.

## Validate

```bash
pip install -r requirements-dev.txt
pytest -q
docker compose ps
docker compose logs --tail 100
docker compose stop
```

CI tests Python 3.12, shell syntax, Docker build and all three services against a local Telegram stub. Process healthchecks do not establish external API availability. Real HH authentication, CAPTCHA and AI require separate acceptance in an authorized account.

[Requirements audit](AUDIT.md) lists every requirement with evidence and limitations. See [installation](INSTALL.md), [migration and rollback](MIGRATION_2_5.md), and [changelog](CHANGELOG.md).

Upstream: [hh-applicant-tool](https://github.com/s3rgeym/hh-applicant-tool), a separate dependency under its own license. Upstream sources are not copied here.

## HTMLBubbles menus

Experimental HTMLBubbles support is enabled in auto mode: native HTML/CSS/JS menu cards when the configured Bot API supports sendWidget, otherwise standard inline menus. Use /classic for the old interface or /bubbles to retry widgets. Client version 13.2 alone is insufficient: server support is also required. See [availability, settings and limits](HTMLBUBBLES.md).

## Mini App control panel

Set TG_MINI_APP_URL to your own HTTPS origin to move the control buttons into a Telegram Mini App while keeping run summaries and watcher notifications in chat. The app uses the same handlers and confirmations, with Telegram-signed owner authorization. See [deployment and limits](MINI_APP.md). Without a configured HTTPS origin the existing bot UI stays enabled.
