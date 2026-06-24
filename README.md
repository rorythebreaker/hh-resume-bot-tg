# HH Applicant Bot

Automated mass-application system for **hh.ru** vacancies, fully managed from a **Telegram bot**.

Built on top of the [`s3rgeym/hh-applicant-tool`](https://github.com/s3rgeym/hh-applicant-tool) CLI (version pinned to **1.8.20**). Runs as three Docker containers, applies to vacancies on a cron schedule, and sends real-time Telegram notifications for every application. Supports **multiple HH accounts and multiple profiles**, each with its own search query, cover letter, region and resume.

---

## Features

- **Multi-profile / multi-account** — unlimited profiles in `profiles.json`; several profiles can share one HH account, and each account is authorized independently.
- **Per-profile cover letter** — every profile owns its own letter file (`letters/letter_<pid>.txt`); editing one profile never touches another.
- **Per-profile resume** — a resume is picked at profile creation and passed to the tool via `--resume-id`. Profiles created without an explicit choice fall back to the tool's default resume.
- **Scheduled applications** — every hour from 08:00 to 21:00 (MSK) for the active profile.
- **Manual applications** — start a run instantly from the bottom panel.
- **Authorization from Telegram** — full OAuth flow including captcha: the captcha image is relayed into the chat and the code is sent back as a message.
- **Real-time notifications** — a watcher daemon parses each account's log and notifies on every successful application and on token-expiry (with a re-auth hint).
- **HH daily limit tracking** — the rolling 200-applications-per-24h window is tracked; the bot shows the next run, the predicted number of free slots, and when the window resets.
- **Pause / resume** — pausing kills the currently running application process and makes scheduled runs skip until resumed.
- **Run summary** — after each batch the bot reports sent / attempts / errors for the active profile.
- **Maintenance jobs** — hourly token refresh, 4-hourly resume bump (`update-resumes`), daily cleanup of rejected negotiations, daily log rotation — all run per authorized account.
- **All times in MSK** (`TZ=Europe/Moscow`).

---

## Architecture

Three Docker containers built from a single image and sharing the project directory via the `.:/app` volume.

| Service | Purpose | Runs as |
|---|---|---|
| `hh_applicant_tool` | cron daemon (scheduled jobs + `@reboot`), the `hh-applicant-tool` CLI, Playwright/Chromium for captcha, and the `auth_helper.py` authorization daemon | root (cron switches to `docker` for user jobs) |
| `hh_tg_bot` | the Telegram control bot (`tg_bot.py`) | `docker` (UID 1000) |
| `hh_tg_watcher` | log watcher daemon (`tg_watcher.py`) sending real-time notifications | `docker` (UID 1000) |

Because all three share `.:/app`, edits to `.py` / `.sh` files are picked up at runtime — only the **bot/watcher need a restart** to reload, and **`run-apply.py` is re-read on every run**. Changing the **crontab requires an image rebuild** (it is baked into the image — see *Updating*).

### Data flow

```
cron (08–21, hourly)
   └─ apply_with_notify.sh ── checks pause flag
        ├─ apply-vacancies.sh ─ exec run-apply.py ─ builds & runs:
        │     hh-applicant-tool --profile <acct> apply-vacancies -L <letter> --search <q> --area <id> [--resume-id <id>] [--excluded-filter <...>]
        │        └─ writes config/<acct>/log.txt
        └─ _summary.py ── reads the run log ── sends batch summary to Telegram

tg_watcher.py (always on)
   └─ tails config/<acct>/log.txt for every account
        ├─ "201 POST /negotiations" ─ sends "applied" notification
        └─ "Refresh token required" ─ sends re-auth notification

tg_bot.py (always on)
   └─ Telegram updates ── bottom panel + inline menu ── controls everything above
```

---

## Project structure

```
.
├── docker-compose.yml        # 3 services, shared image, .env wiring
├── Dockerfile                # python:3.11-slim + hh-applicant-tool[playwright]==1.8.10 + cron
├── .env                      # secrets (NOT committed) — see Configuration
├── crontab                   # cron schedule (baked into the image)
├── startup.sh                # @reboot tasks: refresh-token, update-resumes, then one apply run
├── hh-run-all.sh             # runs an hh-applicant-tool subcommand for every authorized account
├── apply_with_notify.sh      # pause-aware apply wrapper; spawns apply + posts summary
├── apply-vacancies.sh        # thin wrapper -> exec run-apply.py
├── run-apply.py              # builds the apply-vacancies command from the active profile
├── tg_bot.py                 # Telegram control bot (panel + inline menu + auth relay)
├── tg_watcher.py             # real-time notification daemon
├── tg_notifier.py            # log-parsing notification helper
├── _summary.py               # per-run batch summary
├── auth_helper.py            # authorization daemon (drives hh-applicant-tool auth)
├── patch_captcha.py          # patches the tool's authorize.py so the captcha screenshot isn't blank
├── profiles_lib.py           # profile storage/management library (single source of truth)
├── profiles.example.json     # sample profiles.json
├── profiles.json             # YOUR profiles (NOT committed)
├── letters/
│   └── letter_default.txt    # template letter; per-profile letters become letter_<pid>.txt
└── config/                   # runtime state & tokens (NOT committed)
    ├── <hh_account>/config.json   # OAuth token per account
    ├── <hh_account>/log.txt       # tool log per account
    ├── .paused                    # pause flag (presence = paused)
    ├── .apply.pid                 # PID of the running apply process
    ├── .watcher_state.json        # watcher de-dup state
    └── .auth_*                    # auth exchange files (.auth_request/_output/_input/_status/_cancel/_captcha.png)
```

`config/` and `profiles.json` are git-ignored so updates never overwrite your tokens or configuration.

---

## Requirements

- A Linux host with **Docker** and **Docker Compose**.
- A **Telegram bot token** (from [@BotFather](https://t.me/BotFather)) and your **chat ID**.
- An **hh.ru OAuth app** — the same client credentials the `hh-applicant-tool` uses (configured during authorization).

---

## Configuration

### `.env`

Create `.env` in the project root (it is git-ignored, keep it `chmod 600`):

```
TG_BOT_TOKEN=123456:ABC-your-telegram-bot-token
TG_CHAT_ID=123456789
```

Docker Compose reads `.env` automatically and injects both variables into all three containers.

### `profiles.json`

Copy the example and edit:

```bash
cp profiles.example.json profiles.json
```

Schema:

```json
{
  "active": "profile_1",
  "profiles": {
    "profile_1": {
      "display_name": "Основной",
      "hh_account": "main",
      "search": "(devops OR sre) NOT helpdesk",
      "letter": "letter_default.txt",
      "area": 113,
      "excluded_filter": "1с,продажи,helpdesk,стажер",
      "resume_id": ""
    }
  }
}
```

| Field | Meaning |
|---|---|
| `display_name` | Human name shown in the bot |
| `hh_account` | Short id (`[a-z0-9_-]`) — the account whose token is at `config/<hh_account>/config.json`. Profiles with the same value share one HH login. |
| `search` | HH search query language (`AND`/`OR`/`NOT`, parentheses, double quotes; **no single quotes**) |
| `letter` | Cover letter filename inside `letters/` |
| `area` | HH region id (`113` = Russia) |
| `excluded_filter` | Comma-separated terms; vacancies matching them are skipped (passed as one `--excluded-filter "a,b,c"`) |
| `resume_id` | HH resume id, or empty for the tool's default. Set at profile creation; not editable later. |

> Existing profiles without a `resume_id` key keep their current behavior (no `--resume-id`, the tool picks the resume).

---

## Installation & first run

```bash
git clone <repo-url> hh && cd hh
cp profiles.example.json profiles.json   # then edit it
cp .env.example .env 2>/dev/null || true # or create .env manually (see above)
mkdir -p config && touch config/.gitkeep

docker compose build
docker compose up -d
```

Check that all three containers are healthy:

```bash
docker compose ps
docker logs --tail 50 hh_applicant_tool
```

Then open Telegram and send `/start` to the bot.

---

## Authorization

Each HH account must be authorized once.

1. In the bot: **🎛 Меню → 👤 Профиль → 🔑 Авторизовать HH** (authorizes the **active** profile's account).
2. Follow the link / prompts the bot sends. If HH shows a **captcha**, the bot relays the image into the chat — reply with the text from the picture.
3. On success the token is stored at `config/<hh_account>/config.json`. The bot restores the bottom panel.
4. Cancel an in-progress authorization with `/cancel`.

`patch_captcha.py` is applied at image build time so the captcha screenshot is captured only after the image has loaded (otherwise headless Chromium produces a blank picture). This is why the tool version is pinned — the patch targets `1.8.10`.

---

## Telegram interface

### Bottom panel (always present, 5 buttons)

| Button | Action |
|---|---|
| 🎛 Меню | Opens the full inline menu as a chat message |
| ▶️ Старт | Start an application run now (active profile) |
| ⏸ Пауза | Pause (kills a running apply, scheduled runs skip) |
| ▶️ Возобновить | Resume |
| ⏰ Лимит | Next run + remaining HH daily limit |

Taps on these buttons are auto-deleted to keep the chat clean.

### Inline menu (`🎛 Меню` / `/menu`)

Navigation edits the same message in place.

- **📊 Просмотр** — Статус · Лимит · Сегодня · За всё · Последние 10 · На HH · Лог
- **🎛 Управление** — Запустить · Пауза · Возобновить · Удалить отказы
- **👤 Профиль** — Письмо · Изменить письмо · Запрос · Изменить запрос · Переименовать · Регион · Авторизовать HH · Сменить профиль · Создать профиль · Удалить профиль · Список профилей
- **❓ Справка** — built-in help

### Typed commands

Only three are accepted (the Telegram slash-command menu is intentionally empty):

| Command | Action |
|---|---|
| `/start` | Show the bottom panel |
| `/menu` | Open the inline menu |
| `/cancel` | Cancel a text-input prompt (letter / query / name / region / profile creation) or an in-progress authorization |

Text-input flows (edit letter/query/name/region, create profile) work by the bot asking you to send a message; cancel with `/cancel`. Tapping any of the 5 bottom buttons during input cancels the input and runs that button's action.

---

## Profiles & HH accounts

- **Create:** Профиль → Создать профиль → send name, then search query, then HH account id. If that account is already authorized and has **2+ resumes**, the bot shows an inline resume picker; with exactly one resume it is auto-selected; with none (or an unauthorized account) the profile is created with the default resume.
- **Switch / rename / delete:** from the Профиль menu. Deleting removes the **active** profile and its letter file (confirmation required; the last profile cannot be deleted).
- **One account, many profiles:** give several profiles the same `hh_account` to run different searches/letters/resumes against one HH login.
- **Token storage:** `config/<hh_account>/config.json`. Authorization, apply, refresh and cleanup all select the account with the tool's `--profile <hh_account>` option.

---

## Cover letters

- Each profile has its own letter file `letters/letter_<pid>.txt`.
- On bot startup a one-time, idempotent migration gives every profile that still shares the default/template letter its own copy (`migrate_unique_letters()` in `profiles_lib.py`); `letters/letter_default.txt` stays on disk as the template.
- Edit via Профиль → Изменить письмо (send the new text). The letter is stored verbatim and passed to the tool with `--letter-file`; `--force-message` makes the tool always attach it.

---

## Resume selection

`apply-vacancies` is given `--resume-id <id>` only when the active profile has a non-empty `resume_id`. The resume is chosen **only at profile creation** (there is no "change resume" command by design). Resumes are listed live from `https://api.hh.ru/resumes/mine` using that account's token, so the account must be authorized at creation time for the picker to appear.

---

## Cron schedule

Defined in `crontab` (MSK). `CONFIG_DIR=/app/config` and `APP_DIR=/app` are set at the top so every job can find the tokens.

| When | Job |
|---|---|
| `@reboot` | `startup.sh` — refresh tokens, bump resumes, run one apply |
| Hourly, 08:00–21:00 | `apply_with_notify.sh` — application run + summary |
| Every hour | `hh-run-all.sh refresh-token` |
| Every 4 hours | `hh-run-all.sh update-resumes` |
| Daily 03:00 | `hh-run-all.sh clear-negotiations -d` |
| Daily 03:30 | rotate `config/log.txt` if larger than 5 MB |

`hh-run-all.sh <subcommand>` iterates all HH accounts from `profiles.json` and runs the subcommand with `--profile <account>` for each account that has a token, skipping unauthorized ones.

---

## Pause / resume

- **Pause** creates `config/.paused`. `apply_with_notify.sh` checks it before starting and a watcher thread inside it kills the running apply process tree if the flag appears mid-run.
- **Resume** removes the flag.
- Use the bottom panel or the Управление menu.

---

## Notifications

`tg_watcher.py` tails `config/<hh_account>/log.txt` for every account and sends:

- an **application notification** for each `201 POST /negotiations` (vacancy title, employer, link);
- a **re-authorization notification** when it sees `Refresh token required` / «Требуется авторизация» (de-duplicated for 6 hours).

---

## HH daily limit

HH allows **200 applications per rolling 24 hours** (`HH_DAILY_LIMIT = 200`). The bot computes the rolling window from the log and reports, via **⏰ Лимит** and the status views, how many were used, how many slots will be free at the next scheduled run, and when the window fully resets.

---

## Updating / deployment

Most files live in `/app` via the volume and are picked up at runtime:

```bash
# pull / copy new files into the project dir, then:
chown -R 1000:1000 .

# tg_bot.py / profiles_lib.py changed -> reload bot (and watcher if it changed):
docker compose restart hh_tg_bot hh_tg_watcher
# run-apply.py / *.sh changes apply on the next run automatically — no restart needed
```

**Crontab changes require a rebuild** (the crontab is copied into the image):

```bash
docker compose build hh_applicant_tool && docker compose up -d hh_applicant_tool
```

The rebuild is fast — the heavy layers (pip install, Playwright/Chromium) are cached; only the crontab layer is rebuilt. Editing the `Dockerfile` or `patch_captcha.py` invalidates the tool layers and forces a full rebuild.

---

## Logs & troubleshooting

```bash
docker logs --tail 100 -f hh_applicant_tool   # cron output (apply runs, maintenance jobs)
docker logs --tail 100 -f hh_tg_bot           # bot
docker logs --tail 100 -f hh_tg_watcher       # notifications daemon
```

- **`[E] Требуется авторизация` in cron output** — the account has no valid token. Authorize it from the bot (Профиль → 🔑 Авторизовать HH) or check `config/<account>/config.json`.
- **No scheduled applications** — verify `config/<account>/config.json` exists and that `CONFIG_DIR=/app/config` is present in the crontab (it must be, after the latest version).
- **Blank captcha / auth fails** — confirm the image was built with the pinned `1.8.10` (the `patch_captcha.py` step) and that Chromium installed during build.
- **Bot silent** — check `.env` (`TG_BOT_TOKEN`, `TG_CHAT_ID`) and that `hh_tg_bot` is running.

---

## Tech stack

- **Python 3.11** (slim) + standard library only for the bot/watcher (no extra Python deps beyond the tool).
- **[`hh-applicant-tool`](https://github.com/s3rgeym/hh-applicant-tool)** `==1.8.10` with the `playwright` extra.
- **Playwright + Chromium** for the authorization captcha.
- **cron** for scheduling, **SQLite** (shipped with the tool) for its state.
- **Docker Compose** for orchestration.
