# OnePrompt Discord Bot

Discord-native storefront for an AI faceless-video SaaS. Users `/subscribe`, `/setmodal` (their own Modal.com workspace), then `/create` — an external API server orchestrates generation against the user's deployed Modal app and pings the bot back when the video is ready.

## Quick start (local)

```bash
# 1. install Modal CLI on host (needed by /setmodal subprocess)
pip install modal

# 2. python deps
pip install -r requirements.txt

# 3. copy + fill envs
cp .env.example .env
# generate ENCRYPTION_KEY:
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# 4. start Postgres + bot
docker compose up -d db
python bot.py
```

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
| `/subscribe` | Suby checkout |
| `/setmodal` | Form → encrypts tokens → deploys `modal_app/app.py` |
| `/create` | Form → kicks off video generation |
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
- **Auto-DM on install** — `cogs/install_listener.py` hooks the gateway
  `APPLICATION_AUTHORIZED` event (integration_type=1, User App install) and
  DMs the welcoming `/start` message to the new user. Returning users get a
  "welcome back" variant. Users with DMs disabled are logged and skipped.
  discord.py 2.3 doesn't expose this event natively, so we install a parser
  on `bot._connection.parsers["APPLICATION_AUTHORIZED"]`.
