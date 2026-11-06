# Release 2.5.4-bot.1

Telegram wrapper for hh-applicant-tool 2.5.4, Python 3.12.

Includes sources, deployment, tests, migration instructions and an item-by-item audit. Source archive excludes tokens, .env, runtime profiles, virtual environments and old ZIP files.

Automated validation covers tests and CI Docker smoke checks. Real HH authorization, CAPTCHA and AI calls require operator acceptance and are not represented as validated by CI. Read AUDIT.md and MIGRATION_2_5.md before upgrading.

Prerelease: live HH/AI/CAPTCHA acceptance is pending.

- 47 automated tests pass locally and on Python 3.12 in CI.
- Docker build and healthy startup of all three services passed.
- Source credential scan passed.
- Full audit: https://github.com/rorythebreaker/hh-resume-bot-tg/blob/main/AUDIT.md
- CI: https://github.com/rorythebreaker/hh-resume-bot-tg/actions/runs/42242936205
