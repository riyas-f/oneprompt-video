# OnePrompt Discord Bot

Discord-native storefront for an AI faceless-video SaaS. Users `/subscribe`, `/setmodal` (their own Modal.com workspace), then `/create` — an external API server orchestrates generation against the user's deployed Modal app and pings the bot back when the video is ready.

## Quick start (local)

```bash
# 1. python deps (includes the modal CLI; /setmodal invokes it as
#    `python -m modal`, so it just needs to be in the same venv as the bot)
pip install -r requirements.txt

# 2. copy + fill envs
cp .env.example .env
# generate ENCRYPTION_KEY:
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# 3. start Postgres + bot
docker compose up -d db
python bot.py
```

When running under pm2/systemd, launch the bot with the same venv's
interpreter (e.g. `pm2 start bot.py --interpreter /path/to/.venv/bin/python`)
so `python -m modal` resolves to the installed package.

## Architecture

Three independent services — see [`build plan`](../../.claude/plans/build-oneprompt-bot-prompt-md-this-for-frolicking-sky.md) for full design:

1. **Discord bot** (this repo) — UX, subscription state, encrypted Modal creds, job records.
2. **API server** (separate) — receives `/generate` from this bot, expands prompt to scenes, calls user's Modal URL, uploads MP4 to S3, calls back `POST /webhooks/video-ready`.
3. **Modal app** (`modal_app/app.py`) — GPU video generator, deployed *per user* into their own Modal workspace.

## API contract (server side)

`POST $VIDEO_API_URL` — bot → API server, immediate ack.
```json
{
  "content": "...",
  "modal_url": "https://...modal.run",
  "extra_context": "...",
  "discord_id": "123",
  "job_id": 42,
  "callback_url": "https://bot.../webhooks/video-ready",
  "callback_secret": "..."
}
```

`POST /webhooks/video-ready` — API server → bot, when MP4 is uploaded.
```json
{ "discord_id": "123", "job_id": 42, "status": "done", "video_url": "https://cdn.../v.mp4" }
```
Header: `X-Signature: sha256=<HMAC-SHA256(callback_secret, raw_body)>`.

## Commands

| Command | Purpose |
|---|---|
| `/start` | Welcome + pricing |
| `/subscribe` | Pick a plan: Pro ($29/mo, hosted, monthly quota) or Studio ($79/mo, BYO Modal, unlimited) |
| `/setmodal` | Form → encrypts tokens → deploys `modal_app/app.py` |
| `/create` | Form → kicks off video generation. First `FREE_GENERATIONS` runs use the bot owner's Modal (no `/setmodal` needed); after that requires `/subscribe` + `/setmodal`. |
| `/status` | Subscription + Modal connection + current job |
| `/balance` | Modal account balance + month-to-date usage |
| `/videos` | Last 5 generated videos |
| `/support` | DMs the support user |
| `/help` | Command list |

## Notes

- **All commands are DM-only** — guild usage replies "use me in DMs" and short-circuits.
- **Single-job guard** — `/create` rejects while another job for the user is `processing`.
- **Idempotent webhook** — `/webhooks/video-ready` is safe to retry; second hit becomes a no-op.
- **`ENCRYPTION_KEY` is critical** — losing it bricks all stored Modal credentials.
- **Free-tier admin Modal** — `ADMIN_MODAL_URL` points at the bot owner's
  shared deployment of `modal_app/app.py`. New users get
  `FREE_GENERATIONS` (default 2) lifetime free runs against it before
  the paywall. Counter is stored in `users.free_attempts_used` and
  incremented only after a successful API dispatch (failed runs don't
  burn a credit). To deploy the admin app: `cd modal_app && modal deploy app.py`,
  then put the printed URL in `.env`.
- **Tiers**: Free (2 lifetime hosted), Pro ($29/mo hosted with
  `PRO_MONTHLY_QUOTA` videos/period), Studio ($79/mo BYO Modal,
  unlimited). Tier is set on `users.tier` by the Polar webhook based
  on `product_id` (`POLAR_PRODUCT_ID_PRO` vs `POLAR_PRODUCT_ID_STUDIO`).
  Pro period counter resets at `users.quota_reset_at`, which the
  webhook copies from Polar's `current_period_end`.
- **Cost circuit-breaker** — `MAX_HOSTED_VIDEOS_PER_DAY` (default 200)
  globally caps free + Pro hosted-Modal runs per 24h. Studio (BYO) is
  unaffected. Bump or zero it from `.env` if you spot abuse.
- **Auto-DM on install** — when a user installs OnePrompt as a User App,
  Discord sends `APPLICATION_AUTHORIZED` and we DM the welcome message
  (same content as `/start`; "welcome back" variant for returning users).
  Two delivery paths feed the same handler (`cogs/install_listener.handle_install`):
  1. **Gateway parser hook** — `cogs/install_listener.py` patches
     `bot._connection.parsers["APPLICATION_AUTHORIZED"]` because discord.py 2.3
     doesn't expose the event natively.
  2. **HTTP webhook** — `POST /webhooks/discord` (in `webhooks/server.py`)
     handles Discord's "Webhook Events" delivery. Configure in the Developer
     Portal → your app → **Webhook Events** → set URL to
     `https://bot.oneprompt.bot/webhooks/discord` and subscribe to
     `APPLICATION_AUTHORIZED`. Requires `DISCORD_PUBLIC_KEY` in `.env`
     (Developer Portal → General Information). The endpoint verifies the
     Ed25519 signature, acks the PING handshake, and dispatches events.

  The HTTP path is the reliable one — gateway delivery of APPLICATION_AUTHORIZED
  is inconsistent for User Apps. Users with DMs disabled are logged and skipped.
