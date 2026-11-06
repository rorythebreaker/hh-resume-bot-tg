# Changelog

## Unreleased - HTMLBubbles UI

- Experimental sendWidget menus with Telegram theme colors, pagination and live state updates.
- Existing callback/confirmation flows remain authoritative.
- Owner/profile/message/nonce checks, bounded private state and replay protection.
- Automatic classic fallback on unsupported servers; /classic and /bubbles controls.
- Dedicated transport and JavaScript bridge tests; availability documented in HTMLBUBBLES.md.

## 2.5.4-bot.1 - 2026-11-06

- Python 3.12 and pinned hh-applicant-tool 2.5.4; removed Playwright dependencies.
- CLI adapter with per-profile letter/prompt overlays and current AI sections.
- Account isolation, atomic profile transactions and cross-process application lock.
- Manual preview and single-use bulk/manual confirmations; pinned sender.
- Distinct API errors, bounded GET retries, no ambiguous POST replay.
- Per-run maximum, persistent hourly schedule, run history and log normalization.
- Three Docker services, process healthchecks, cron environment and shutdown handling.
- Regression tests, Docker CI, migration and item-by-item requirements audit.
- Watcher no longer labels every HTTP 4xx as an expired token.
- Disabled automatic backup uploads; configured encryption now fails closed.

Live HH/AI/CAPTCHA acceptance is separate; see AUDIT.md.
