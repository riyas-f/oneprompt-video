"""Job state machine for the internal pipeline.

Stages (persisted on video_jobs.stage):
    script → rendering → uploading → delivered   (or → failed at any point)

Design goals:
  * Every stage writes to Postgres before advancing → a bot restart resumes
    mid-render jobs (artifacts holds modal_url/voice/scenes) instead of
    losing them.
  * Any exception marks the job failed and DMs the user → the one-job-at-a-
    time rule can never permanently lock someone out.
  * A periodic sweep also fails ancient 'processing' jobs from the legacy
    n8n path (which has no failure callback of its own).
"""
from __future__ import annotations

import asyncio
import logging
import time

import discord

from config import cfg
from database import models as db
from pipeline import assembly, llm, storage
from pipeline.modal_client import ModalClient
from services import embeds

log = logging.getLogger(__name__)

_POLL_SECONDS = 20
_UNKNOWN_STRIKES = 4          # consecutive 'unknown' statuses before we give up
_SWEEP_SECONDS = 300

_bot: discord.Client | None = None
_sem: asyncio.Semaphore | None = None
_started = False
_active: set[int] = set()     # job ids owned by a live task (sweep skips these)


class JobFailed(RuntimeError):
    pass


def _job_key(discord_id: int, job_id: int) -> str:
    return f"{discord_id}-{job_id}"


def _require_internal_config() -> None:
    required = {
        "OPENROUTER_API_KEY": cfg.openrouter_api_key,
        "R2_ENDPOINT": cfg.r2_endpoint,
        "R2_ACCESS_KEY_ID": cfg.r2_access_key_id,
        "R2_SECRET_ACCESS_KEY": cfg.r2_secret_access_key,
        "R2_BUCKET": cfg.r2_bucket,
        "R2_PUBLIC_BASE_URL": cfg.r2_public_base_url,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise RuntimeError(
            f"PIPELINE=internal but missing env vars: {', '.join(missing)}"
        )


def setup(bot: discord.Client) -> None:
    """Call once from bot.py after the DB pool is up."""
    global _bot, _sem, _started
    if _started:
        return
    _started = True
    _bot = bot
    _sem = asyncio.Semaphore(cfg.pipeline_max_concurrent)
    if cfg.pipeline == "internal":
        _require_internal_config()
    asyncio.create_task(_background())


async def submit(
    *,
    discord_id: int,
    job_id: int,
    topic: str,
    extra_context: str,
    style: dict,
    style_key: str | None,
    voice: str,
    modal_url: str,
) -> None:
    """Fire-and-forget from the /create cog. Returns immediately."""
    _active.add(job_id)
    asyncio.create_task(
        _run_job(discord_id, job_id, topic, extra_context, style, style_key, voice, modal_url)
    )


# ─── the job task ─────────────────────────────────────────────────────────────
async def _run_job(
    discord_id: int,
    job_id: int,
    topic: str,
    extra_context: str,
    style: dict,
    style_key: str | None,
    voice: str,
    modal_url: str,
) -> None:
    assert _sem is not None
    async with _sem:
        progress_msg = None
        try:
            progress_msg = await _dm(
                discord_id,
                embeds.processing("Writing your script",
                                  f"**Topic:** {topic[:150]}\n\nStep 1 of 3 — scripting…"),
            )

            # ── script ────────────────────────────────────────────────────
            await db.set_job_stage(job_id, "script")
            raw_scenes = await llm.write_script(topic, extra_context, style, style_key)
            scenes = assembly.assemble(raw_scenes, style)
            await db.save_job_artifacts(job_id, {
                "modal_url": modal_url, "voice": voice, "scenes": scenes,
            })

            # ── render ────────────────────────────────────────────────────
            video = await _render(discord_id, job_id, scenes, voice, modal_url, progress_msg)

            # ── upload + deliver ──────────────────────────────────────────
            await _deliver(discord_id, job_id, video, progress_msg)

        except Exception as e:
            log.exception("Job %s failed", job_id)
            await _fail(discord_id, job_id, str(e), progress_msg)
        finally:
            _active.discard(job_id)


async def _render(
    discord_id: int,
    job_id: int,
    scenes: list[dict],
    voice: str,
    modal_url: str,
    progress_msg,
) -> bytes:
    client = ModalClient(modal_url)
    key = _job_key(discord_id, job_id)
    total = len(scenes)

    mode = await client.submit(scenes, voice, key)
    await db.set_job_stage(job_id, "rendering")
    await _edit(progress_msg, embeds.processing(
        "Rendering your video",
        f"Step 2 of 3 — rendering **{total} scenes**. I'll update this message as it goes.",
    ))

    if mode == "sync":
        # Old Modal deploy without the async API: block on the legacy endpoint.
        return await client.generate_sync(scenes, voice)
    return await _poll(client, key, total, progress_msg)


async def _poll(client: ModalClient, key: str, total: int, progress_msg) -> bytes:
    deadline = time.monotonic() + cfg.job_timeout_minutes * 60
    strikes = 0
    last_scene = -1

    while True:
        await asyncio.sleep(_POLL_SECONDS)
        if time.monotonic() > deadline:
            raise JobFailed(f"render exceeded {cfg.job_timeout_minutes} minutes")

        try:
            st = await client.status(key)
        except Exception as e:
            log.warning("Status poll error for %s: %s", key, e)
            continue

        status = st.get("status")
        if status == "done":
            return await client.fetch_result(key)
        if status == "failed":
            raise JobFailed(f"render failed: {st.get('error', 'unknown error')}")
        if status == "unknown":
            strikes += 1
            if strikes >= _UNKNOWN_STRIKES:
                raise JobFailed("render job disappeared on the Modal side")
            continue

        strikes = 0
        scene = st.get("scene")
        if isinstance(scene, int) and scene != last_scene:
            last_scene = scene
            await _edit(progress_msg, embeds.processing(
                "Rendering your video",
                f"Step 2 of 3 — scene **{scene}/{st.get('total', total)}** rendered…",
            ))


async def _deliver(discord_id: int, job_id: int, video: bytes, progress_msg) -> None:
    await db.set_job_stage(job_id, "uploading")
    await _edit(progress_msg, embeds.processing(
        "Almost there", "Step 3 of 3 — uploading your video…",
    ))

    url = await storage.upload_video(video, storage.video_key(discord_id, job_id))
    row = await db.complete_job(job_id, url)
    await db.set_job_stage(job_id, "delivered")

    if row is not None:
        await _edit(progress_msg, embeds.success(
            "Your video is ready",
            f"**Prompt:** {row['prompt']}\n\n[▶ Watch / download]({url})",
        ))

    # Free the checkpoint space on Modal (best-effort).
    job = row or await db.get_job(job_id)
    artifacts = (job or {}).get("artifacts")
    modal_url = None
    if isinstance(artifacts, dict):
        modal_url = artifacts.get("modal_url")
    elif isinstance(artifacts, str):
        import json
        try:
            modal_url = json.loads(artifacts).get("modal_url")
        except Exception:
            pass
    if modal_url:
        await ModalClient(modal_url).cleanup(_job_key(discord_id, job_id))


async def _fail(discord_id: int, job_id: int, error: str, progress_msg) -> None:
    row = await db.fail_job(job_id, error[:500])
    if row is None:
        return  # already terminal (e.g. sweep got there first)
    embed = embeds.error(
        "Video generation failed",
        "Something went wrong on our side — you haven't lost anything.\n\n"
        "Try `/create` again, or `/support` if it keeps happening.",
    )
    if not await _edit(progress_msg, embed):
        await _dm(discord_id, embed)


# ─── background: resume + sweep ───────────────────────────────────────────────
async def _background() -> None:
    assert _bot is not None
    await _bot.wait_until_ready()

    if cfg.pipeline == "internal":
        await _resume_all()

    while True:
        try:
            await _sweep()
        except Exception:
            log.exception("Job sweep failed")
        await asyncio.sleep(_SWEEP_SECONDS)


async def _resume_all() -> None:
    """Re-attach to jobs that were mid-render when the bot last stopped."""
    for job in await db.resumable_jobs():
        art = job.get("artifacts") or {}
        if not isinstance(art, dict) or not art.get("modal_url") or not art.get("scenes"):
            await _fail(job["discord_id"], job["id"], "restart lost job context", None)
            continue
        _active.add(job["id"])
        asyncio.create_task(_resume_job(job, art))
        log.info("Resuming job %s (stage=%s)", job["id"], job["stage"])

    # Jobs that died before rendering: cheap to redo, so just unlock the user.
    for job in await db.orphaned_pipeline_jobs():
        await _fail(
            job["discord_id"], job["id"],
            "bot restarted before rendering began",
            None,
        )


async def _resume_job(job: dict, art: dict) -> None:
    assert _sem is not None
    discord_id, job_id = job["discord_id"], job["id"]
    scenes, voice, modal_url = art["scenes"], art.get("voice", "af_heart"), art["modal_url"]
    key = _job_key(discord_id, job_id)
    client = ModalClient(modal_url)

    async with _sem:
        try:
            st = await client.status(key)
            if st.get("status") == "unknown":
                # Modal never saw it (or GC'd it) — resubmit; volume checkpoints
                # make a resubmit resume from the last finished scene anyway.
                mode = await client.submit(scenes, voice, key)
                if mode == "sync":
                    video = await client.generate_sync(scenes, voice)
                    await _deliver(discord_id, job_id, video, None)
                    return
            video = await _poll(client, key, len(scenes), None)
            await _deliver(discord_id, job_id, video, None)
        except Exception as e:
            log.exception("Resumed job %s failed", job_id)
            await _fail(discord_id, job_id, str(e), None)
        finally:
            _active.discard(job_id)


async def _sweep() -> None:
    """Hard-timeout failsafe for jobs nothing is watching (n8n path, crashed tasks)."""
    for job in await db.stale_processing_jobs(cfg.job_timeout_minutes):
        if job["id"] in _active:
            continue
        log.warning("Sweeping stale job %s (started %s)", job["id"], job["started_at"])
        await _fail(
            job["discord_id"], job["id"],
            f"timed out after {cfg.job_timeout_minutes} minutes",
            None,
        )


# ─── discord helpers ──────────────────────────────────────────────────────────
async def _dm(discord_id: int, embed: discord.Embed):
    assert _bot is not None
    try:
        user = await _bot.fetch_user(discord_id)
        return await user.send(embed=embed)
    except discord.Forbidden:
        log.warning("Cannot DM user %s (DMs disabled)", discord_id)
    except Exception:
        log.exception("DM failed for %s", discord_id)
    return None


async def _edit(message, embed: discord.Embed) -> bool:
    if message is None:
        return False
    try:
        await message.edit(embed=embed)
        return True
    except Exception:
        return False
