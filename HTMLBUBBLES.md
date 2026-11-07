# HTMLBubbles UI

This integration targets Telegram HTMLBubbles (widget-sandbox, layer 233), not ordinary parse_mode=HTML messages or a separate Mini App.

## Availability

As checked on 2026-11-06, the official widget-sandbox repository documents sendWidget, editMessageWidgetData, answerWidgetQuery and widget_query for beta Bot API 10.4. These methods are not yet listed in the public Bot API reference. Client version 13.2 alone does not establish that a bot's configured server accepts them.

Official contracts used:

- https://github.com/TelegramMessenger/widget-sandbox/blob/main/docs/tgw-format.md
- https://github.com/TelegramMessenger/widget-sandbox/blob/main/docs/widget-runtime-profile.md
- https://github.com/TelegramMessenger/widget-sandbox/blob/main/docs/sdk-v1.md
- https://github.com/TelegramMessenger/widget-sandbox/blob/main/scripts/conformance/test-bot.ts

## Usage

TG_HTML_BUBBLES=auto is the default. A new menu tries sendWidget on the configured Telegram server; HTTP 400/404 falls back to existing inline menus and disables further probes until restart or /bubbles. It never switches API servers automatically.

- /ui_status reports the actual last widget method and sanitized API result, enabled/disabled mode and classic chat preference. It makes no API requests. Run /bubbles first to probe, then /ui_status. HTTP 404 means the method or configured route was not found; HTTP 400 alone is inconclusive, so inspect its description.
- /menu opens the current menu.
- /classic selects the standard interface for this chat until restart or /bubbles.
- /bubbles retries widget support and opens a new menu.
- TG_HTML_BUBBLES=off disables the feature entirely.
- TG_HTML_BUBBLES=on explicitly enables trying the same widget transport, with the same compatibility fallback.

If the server accepts widgets but an older client cannot display them, the caption includes /classic. The bot cannot reliably determine a viewer's client version. Long text/help screens retain the complete classic view; widget media is never incorrectly edited as plain text.

Experimental test-server setup must be explicit: TG_API_ROOT selects the root and TG_API_TEST_ENV=1 adds /test to the bot UI endpoint. Use the appropriate test-server bot account. TG_API_TEST_ENV applies to the polling UI process, not the independent watcher/backup services. Production accounts are not automatically moved to beta/test endpoints.

## Behavior

One self-contained HTML/CSS/JavaScript asset follows Telegram theme variables. Menus have five actions per page, pagination and refresh; text entry remains in ordinary chat. No external files, network requests, libraries or form controls are required.

HTML never contains user information. Menu data goes only into private chat_data, capped at 1024 UTF-8 bytes. No public_data or shared widgets are used; forwarded copies carry no private menu state. Payload size is checked against the documented 32 KiB source / 4 KiB zlib limits.

Widget actions use existing callback handlers and their confirmations. The server verifies the private chat, sender, message, active profile, expiring nonce and visible action index; repeated update IDs are ignored. Restarted/expired sessions cannot perform actions. A delivery timeout is not automatically resent as another message.

## Validation

Python tests cover transport, fallback, permissions, replay, state limits, pagination and bot integration. Node tests execute the client JavaScript with a simulated DOM/TelegramWidget bridge. CI also builds Docker and starts the services with a local Telegram API stub. These checks do not substitute for a live test in Telegram 13.2 or prove rendering conformance of the native sandbox. No real bot messages were sent during development.
