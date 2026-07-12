"""OnePrompt as an embedded Whop app (Phase W2).

Whop loads our base URL in an iframe and proxies same-origin requests with an
`x-whop-user-token` JWT header. We verify it per request (JWKS, ES256, via the
official whop-sdk), map the Whop user onto our existing users table with a
synthetic negative discord_id, and everything downstream — metering, quotas,
refunds, the render pipeline — is unchanged.

Routes (mounted by webhooks/server.py when WHOP_API_KEY is set):
    GET  /whop            app shell (form + quota + videos, htmx)
    POST /whop/create     start a job
    GET  /whop/job/{id}   progress fragment (polled)
    GET  /whop/videos     library fragment
    POST /whop/checkout   create checkout config for in-app purchase
    POST /webhooks/whop   payment/membership webhook
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
from datetime import datetime, timedelta

from aiohttp import web

from config import cfg
from database import models as db
from pipeline import runner
from styles.presets import STYLE_PRESETS, VOICE_PRESETS, get_style_object

log = logging.getLogger(__name__)

DURATIONS = [(1, "1 minute"), (2, "2 minutes"), (3, "3 minutes"), (5, "5 minutes")]
FREE_MAX_MINUTES = 2

_whop_client = None


def _client():
    global _whop_client
    if _whop_client is None:
        from whop_sdk import AsyncWhop
        _whop_client = AsyncWhop(api_key=cfg.whop_api_key)
    return _whop_client


def synthetic_discord_id(whop_user_id: str) -> int:
    """Stable negative 63-bit id for Whop-only users. Negative so it can never
    collide with a real Discord snowflake."""
    h = int.from_bytes(hashlib.sha256(whop_user_id.encode()).digest()[:8], "big") >> 1
    return -(h or 1)


def _verify(request: web.Request):
    """Returns the Whop user id or None."""
    from whop_sdk.lib.verify_user_token import verify_user_token
    try:
        # app_id is a required kwarg; the SDK validates the token's aud
        # claim against it (None skips that check when WHOP_APP_ID is unset).
        payload = verify_user_token(
            dict(request.headers), app_id=cfg.whop_app_id or None
        )
        return payload.user_id
    except Exception as e:
        log.warning("Whop user-token verification failed: %s", e)
        return None


# ─── HTML ─────────────────────────────────────────────────────────────────────
_PAGE = """<!doctype html>
<html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>OnePrompt</title>
<script src="https://unpkg.com/htmx.org@1.9.12"></script>
<style>
:root {{ color-scheme: dark; }}
* {{ box-sizing: border-box; margin: 0; }}
body {{ background:#0B0B0C; color:#EDEAE4; font: 15px/1.55 ui-sans-serif, system-ui, sans-serif; padding: 20px; max-width: 760px; margin: 0 auto; }}
h1 {{ font-size: 20px; margin-bottom: 2px; }}
.gold {{ color:#D9A44C; }}
.muted {{ color:#8f8b84; font-size: 13px; }}
.card {{ background:#141416; border:1px solid #26262a; border-radius:10px; padding:16px; margin-top:16px; }}
label {{ display:block; font-size:12px; text-transform:uppercase; letter-spacing:.08em; color:#8f8b84; margin:10px 0 4px; }}
input, select, textarea {{ width:100%; background:#0B0B0C; color:#EDEAE4; border:1px solid #2e2e33; border-radius:7px; padding:9px 10px; font: inherit; }}
textarea {{ min-height:64px; resize:vertical; }}
button {{ background:#D9A44C; color:#0B0B0C; border:0; border-radius:7px; padding:10px 18px; font-weight:600; cursor:pointer; margin-top:14px; font:inherit; }}
button.secondary {{ background:#26262a; color:#EDEAE4; }}
.row {{ display:grid; grid-template-columns:1fr 1fr 1fr; gap:10px; }}
.quota {{ display:flex; justify-content:space-between; align-items:center; }}
.vid {{ border-top:1px solid #26262a; padding:10px 0; display:flex; justify-content:space-between; gap:10px; align-items:center; }}
.vid a {{ color:#D9A44C; }}
.err {{ color:#e07b6a; }} .ok {{ color:#7bc47f; }}
video {{ width:100%; border-radius:8px; margin-top:8px; }}
.htmx-indicator {{ opacity:0; transition:opacity .2s; }} .htmx-request .htmx-indicator {{ opacity:1; }}
</style>
</head><body>
<h1>One<span class="gold">Prompt</span></h1>
<div class="muted">Type a prompt → get a finished faceless video. Consistent characters, natural voices.</div>

<div class="card quota" id="quota" hx-get="/whop/quota" hx-trigger="load, quotaRefresh from:body" hx-swap="innerHTML">…</div>

<div class="card">
  <form hx-post="/whop/create" hx-target="#job" hx-swap="innerHTML">
    <label>Video topic</label>
    <input name="topic" required maxlength="200" placeholder="e.g. why your brain replays embarrassing moments">
    <label>Extra details (optional)</label>
    <textarea name="extra" maxlength="1000" placeholder="tone, angle, what to end with…"></textarea>
    <div class="row">
      <div><label>Style</label><select name="style">{style_options}</select></div>
      <div><label>Voice</label><select name="voice">{voice_options}</select></div>
      <div><label>Length</label><select name="duration">{duration_options}</select></div>
    </div>
    <button type="submit">Generate video <span class="htmx-indicator">…</span></button>
  </form>
  <div id="job"></div>
</div>

<div class="card">
  <div class="quota"><strong>Your videos</strong></div>
  <div id="videos" hx-get="/whop/videos" hx-trigger="load, quotaRefresh from:body" hx-swap="innerHTML">…</div>
</div>

<script type="module">
  import {{ createSdk }} from "https://unpkg.com/@whop/iframe@latest?module";
  const sdk = createSdk({{ appId: "{app_id}" }});
  window.buyPlan = async (planId) => {{
    try {{
      const r = await fetch("/whop/checkout", {{ method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ plan: planId }}) }});
      const cfg = await r.json();
      if (cfg.error) throw new Error(cfg.error);
      const res = await sdk.inAppPurchase({{ planId: cfg.plan_id, id: cfg.checkout_id }});
      if (res && res.status === "ok") document.body.dispatchEvent(new Event("quotaRefresh"));
    }} catch (e) {{
      alert("Purchase could not start: " + e.message);
    }}
  }};
</script>
</body></html>"""


def _style_options() -> str:
    return "".join(
        f'<option value="{k}">{html.escape(p["emoji"] + " " + p["label"])}</option>'
        for k, p in STYLE_PRESETS.items()
    )


def _voice_options() -> str:
    return "".join(
        f'<option value="{k}">{html.escape(v["emoji"] + " " + v["label"])}</option>'
        for k, v in VOICE_PRESETS.items()
    )


def _duration_options() -> str:
    return "".join(
        f'<option value="{v}"{" selected" if v == 2 else ""}>{label}</option>'
        for v, label in DURATIONS
    )


def _upgrade_buttons() -> str:
    # Plans not configured yet → no broken buy buttons.
    if not (cfg.whop_plan_pro and cfg.whop_plan_studio):
        return '<span class="muted">Paid plans coming soon.</span>'
    return (
        f'<button class="secondary" onclick="buyPlan(\'{cfg.whop_plan_pro}\')">'
        f'Pro — ${cfg.price_usd}/mo · {cfg.pro_monthly_minutes} min</button> '
        f'<button class="secondary" onclick="buyPlan(\'{cfg.whop_plan_studio}\')">'
        f'Studio — $79/mo · {cfg.studio_monthly_minutes} min</button>'
    )


# ─── handlers ─────────────────────────────────────────────────────────────────
async def page(request: web.Request) -> web.Response:
    if _verify(request) is None:
        return web.Response(
            text="<h3 style='font-family:sans-serif'>Open this app inside Whop.</h3>",
            content_type="text/html",
        )
    return web.Response(
        text=_PAGE.format(
            style_options=_style_options(),
            voice_options=_voice_options(),
            duration_options=_duration_options(),
            app_id=html.escape(cfg.whop_app_id),
        ),
        content_type="text/html",
    )


async def quota_fragment(request: web.Request) -> web.Response:
    whop_uid = _verify(request)
    if whop_uid is None:
        return web.Response(status=401)
    user = await db.get_or_create_whop_user(whop_uid, synthetic_discord_id(whop_uid))
    if user["subscription_status"] == "pro":
        used = await db.get_or_reset_period_usage(user["discord_id"])
        quota = (cfg.studio_monthly_minutes if user.get("tier") == "studio"
                 else cfg.pro_monthly_minutes)
        body = (f'<div><strong>{(user.get("tier") or "pro").capitalize()}</strong> · '
                f'{max(0, quota - used)} of {quota} render-minutes left</div>')
    else:
        free_left = max(0, cfg.free_generations - int(user.get("free_attempts_used") or 0))
        body = (f'<div><strong>Free</strong> · {free_left} of {cfg.free_generations} '
                f'free videos left (up to {FREE_MAX_MINUTES} min)</div>'
                f'<div>{_upgrade_buttons()}</div>')
    return web.Response(text=body, content_type="text/html")


async def create(request: web.Request) -> web.Response:
    whop_uid = _verify(request)
    if whop_uid is None:
        return web.Response(status=401)
    form = await request.post()
    topic = str(form.get("topic") or "").strip()[:200]
    extra = str(form.get("extra") or "").strip()[:1000]
    style_key = str(form.get("style") or "whiteboard")
    voice = str(form.get("voice") or "af_heart")
    try:
        duration = int(form.get("duration") or 2)
    except ValueError:
        duration = 2
    if duration not in [d for d, _ in DURATIONS]:
        duration = 2
    if not topic:
        return _frag('<p class="err">Please enter a topic.</p>')
    if voice not in VOICE_PRESETS:
        voice = "af_heart"

    user = await db.get_or_create_whop_user(whop_uid, synthetic_discord_id(whop_uid))
    uid = user["discord_id"]

    # Quota checks (mirrors the Discord cog).
    if user["subscription_status"] == "pro":
        quota = (cfg.studio_monthly_minutes if user.get("tier") == "studio"
                 else cfg.pro_monthly_minutes)
        used = await db.get_or_reset_period_usage(uid)
        if used + duration > quota:
            return _frag(f'<p class="err">Not enough render-minutes '
                         f'({max(0, quota - used)} of {quota} left).</p>')
        meter = "period"
    else:
        if int(user.get("free_attempts_used") or 0) >= cfg.free_generations:
            return _frag('<p class="err">Free videos used — pick a plan above '
                         'for more.</p>')
        if duration > FREE_MAX_MINUTES:
            return _frag(f'<p class="err">Free videos are up to '
                         f'{FREE_MAX_MINUTES} minutes.</p>')
        meter = "free"

    if await db.has_processing_job(uid):
        return _frag('<p class="err">You already have a video generating — '
                     'wait for it to finish.</p>')
    if await db.count_hosted_videos_today() >= cfg.max_hosted_videos_per_day:
        return _frag('<p class="err">We are at capacity right now — try again '
                     'in a few hours.</p>')

    job_id = await db.create_job(uid, topic, extra or None)
    style_obj = get_style_object(style_key, None)
    await runner.submit(
        discord_id=uid, job_id=job_id, topic=topic, extra_context=extra,
        style=style_obj, style_key=style_key, voice=voice,
        modal_url=cfg.admin_modal_url, duration_minutes=duration,
    )
    if meter == "free":
        await db.increment_free_attempt(uid)
    else:
        await db.increment_period_usage(uid, duration)

    return _frag(
        f'<div hx-get="/whop/job/{job_id}" hx-trigger="load delay:5s" '
        f'hx-swap="outerHTML"><p class="ok">🎬 Generating… usually 5–10 minutes. '
        f'This page updates itself.</p></div>',
        refresh=True,
    )


async def job_fragment(request: web.Request) -> web.Response:
    whop_uid = _verify(request)
    if whop_uid is None:
        return web.Response(status=401)
    try:
        job_id = int(request.match_info["job_id"])
    except ValueError:
        return web.Response(status=400)
    job = await db.get_job(job_id)
    if not job or job["discord_id"] != synthetic_discord_id(whop_uid):
        return web.Response(status=404)

    if job["status"] == "done":
        url = html.escape(job["video_url"] or "")
        return _frag(f'<p class="ok">✅ Done!</p><video controls src="{url}"></video>'
                     f'<p><a href="{url}" target="_blank">Download MP4</a></p>',
                     refresh=True)
    if job["status"] == "failed":
        return _frag('<p class="err">❌ Generation failed — your credit was '
                     'returned. Try again.</p>', refresh=True)
    stage = {"script": "writing the script", "rendering": "rendering scenes",
             "uploading": "uploading"}.get(job.get("stage") or "", "working")
    return _frag(
        f'<div hx-get="/whop/job/{job_id}" hx-trigger="load delay:7s" '
        f'hx-swap="outerHTML"><p>⏳ Still {stage}…</p></div>'
    )


async def videos_fragment(request: web.Request) -> web.Response:
    whop_uid = _verify(request)
    if whop_uid is None:
        return web.Response(status=401)
    vids = await db.list_recent_videos(synthetic_discord_id(whop_uid), limit=8)
    if not vids:
        return web.Response(text='<p class="muted">No videos yet.</p>',
                            content_type="text/html")
    rows = "".join(
        f'<div class="vid"><span>{html.escape((v["prompt"] or "")[:60])}</span>'
        f'<a href="{html.escape(v["video_url"] or "")}" target="_blank">watch</a></div>'
        for v in vids
    )
    return web.Response(text=rows, content_type="text/html")


async def checkout(request: web.Request) -> web.Response:
    whop_uid = _verify(request)
    if whop_uid is None:
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "bad json"}, status=400)
    plan_id = str(body.get("plan") or "")
    if not plan_id or plan_id not in (cfg.whop_plan_pro, cfg.whop_plan_studio):
        return web.json_response({"error": "unknown plan"}, status=400)
    try:
        conf = await _client().checkout_configurations.create(
            plan_id=plan_id,
            metadata={"whop_user_id": whop_uid},
        )
        return web.json_response({"checkout_id": conf.id, "plan_id": plan_id})
    except Exception as e:
        log.exception("Whop checkout config failed")
        return web.json_response({"error": str(e)[:200]}, status=500)


async def webhook(request: web.Request) -> web.Response:
    raw = await request.text()
    try:
        event = _client().webhooks.unwrap(
            raw, headers=dict(request.headers), key=cfg.whop_webhook_secret or None
        )
    except Exception:
        log.warning("Whop webhook signature/parse failure")
        return web.json_response({"error": "bad signature"}, status=401)

    etype = getattr(event, "type", "") or ""
    data = event.model_dump() if hasattr(event, "model_dump") else {}
    log.info("Whop webhook: %s", etype)

    d = data.get("data") or {}
    whop_uid = (
        (d.get("metadata") or {}).get("whop_user_id")
        or (d.get("user") or {}).get("id")
        or d.get("user_id")
    )
    plan_id = d.get("plan_id") or (d.get("plan") or {}).get("id") or ""
    if not whop_uid:
        return web.json_response({"ok": True, "noop": True})

    user = await db.get_or_create_whop_user(str(whop_uid), synthetic_discord_id(str(whop_uid)))

    if etype in ("payment.succeeded", "membership.went_valid", "membership.activated"):
        tier = "studio" if plan_id == cfg.whop_plan_studio else "pro"
        await db.mark_subscribed(user["discord_id"], f"whop:{whop_uid}", str(plan_id))
        await db.set_tier(user["discord_id"], tier,
                          datetime.utcnow() + timedelta(days=30), is_trialing=False)
        await db.set_billing_source(user["discord_id"], "whop")
    elif etype in ("membership.went_invalid", "membership.canceled",
                   "membership.deactivated", "payment.refunded"):
        await db.mark_cancelled(user["discord_id"])

    return web.json_response({"ok": True})


def _frag(inner: str, refresh: bool = False) -> web.Response:
    resp = web.Response(text=inner, content_type="text/html")
    if refresh:
        resp.headers["HX-Trigger"] = "quotaRefresh"
    return resp


def mount(app: web.Application) -> None:
    app.router.add_get("/whop", page)
    app.router.add_get("/whop/quota", quota_fragment)
    app.router.add_post("/whop/create", create)
    app.router.add_get("/whop/job/{job_id}", job_fragment)
    app.router.add_get("/whop/videos", videos_fragment)
    app.router.add_post("/whop/checkout", checkout)
    app.router.add_post("/webhooks/whop", webhook)
    log.info("Whop app mounted at /whop")
