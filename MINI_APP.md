# Telegram Mini App control panel

The control UI is a normal Telegram Mini App. HTMLBubbles/beta APIs are not required.

## What moves into the app

Profiles/accounts, resume selection, cover letters, search/filter settings, AI configuration and prompts, schedule, pause/resume, manual vacancy previews, bulk launch confirmations, statistics, history, authorization input and CAPTCHA relay use the same bot handlers as before.

All current menu buttons are rendered from the bot's existing menu data; the app does not implement an independent application engine. Text forms have single-use submission tokens. Background menus (such as manual-response confirmation) return into the app; background results and the existing summary/watcher notifications still go to Telegram chat. Backups requested from the app are delivered in chat.

## Deployment

A public HTTPS origin pointing to your bot server is required. A GitHub Pages-only deployment cannot run the Python API. The Mini App and API must be on the same origin.

1. Create an HTTPS subdomain for the server, e.g. https://hh.example.com.
2. Configure its reverse proxy to forward all requests to http://127.0.0.1:8080.
3. Add TG_MINI_APP_URL=https://hh.example.com to .env.
4. Update sources and run docker compose up -d --build.
5. Open the bot's private chat and use its native menu button or the inline Open panel button. /start and /menu also provide the app link.

The bot installs the native menu button with setChatMenuButton on startup and removes the old reply keyboard. With TG_MINI_APP_URL blank, the original chat UI continues working. /classic remains an escape hatch for the classic interface.

The HTTP listener runs in the bot process so prompts, active profile and confirmations stay consistent. Inside Docker it listens on port 8080; Compose exposes it only on 127.0.0.1:8080. Set TG_MINI_APP_HOST/TG_MINI_APP_PORT only if your deployment needs different internal binding; update the Compose port mapping accordingly.

Example Nginx location inside your existing HTTPS server block:

~~~nginx
location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 90s;
    client_max_body_size 64k;
}
~~~

Use your real domain and valid certificate. The URL must currently be an HTTPS origin without a path, query or fragment. TLS terminates at your reverse proxy. Do not expose the raw HTTP port publicly.

## Authorization and operation

The frontend sends Telegram.WebApp.initData in an Authorization header. The backend verifies the HMAC signature, authentication time and exact owner ID from TG_CHAT_ID. It does not trust initDataUnsafe. Sessions expire after an hour; close and reopen the Mini App to obtain fresh Telegram credentials. Only the configured owner in a private bot chat is supported.

State and API replies use no-store. Tokens, user inputs and initData are not written to HTTP logs. API requests do not use cookies or permissive CORS. Mutating actions must use short-lived server-issued IDs; changing the profile invalidates old controls, and forms reject changed prompts/profiles.

There is no API authentication bypass for preview. A browser on localhost with ?preview=1 may display static sample data, but this mode never calls the bot API or performs actions. The deployed app requires Telegram identity.

Health endpoint: /health (no account information). Protected state endpoint: /api/state. Static frontend code contains no bot token. No Node server or frontend build step is needed in production.

## Validation limits

Automated tests cover owner/signature checks, forged/expired sessions, shared business handlers, launch confirmation, single-use forms, profile changes, asynchronous menus, background chat reports and the HTTP boundary. JavaScript tests cover Telegram-only access, local demo isolation, authorization header handling and server-issued commands.

Live Telegram rendering, domain/TLS routing and HH authorization must be checked after deployment. The development browser host was unavailable, so visual/browser QA was not completed. The server must be reachable at the configured HTTPS URL before the app can be considered enabled for your bot.

Official Telegram contract: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
