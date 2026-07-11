"""Scene-script generation via OpenRouter. One call, validated JSON out.

The prompt forbids the model from describing art style — that gets injected
mechanically in assembly.py, so user style presets can never be overridden
by the LLM (the bug the old n8n workflow had).
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import httpx

from config import cfg

log = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "scene_writer.md"
_MAX_ATTEMPTS = 3

# ≈6 scenes and ≈145 narration words per minute of video.
_SCENES_PER_MIN = 6
_WORDS_PER_MIN = 145


def _runtime_tokens(minutes: int) -> dict:
    """Compute the prompt's runtime targets + 4-act scene boundaries."""
    minutes = max(1, min(int(minutes), 15))
    n = minutes * _SCENES_PER_MIN
    lo, hi = max(4, n - 2), n + 2
    a1 = max(2, round(n * 0.11))
    a2 = max(a1 + 1, round(n * 0.33))
    a3 = max(a2 + 1, round(n * 0.63))
    return {
        "<<RUNTIME_RANGE>>": f"about {minutes} minute{'s' if minutes > 1 else ''}",
        "<<WORD_RANGE>>": f"{(minutes * _WORDS_PER_MIN) - 40}–{(minutes * _WORDS_PER_MIN) + 40}",
        "<<SCENE_RANGE>>": f"{lo}–{hi}",
        "<<A1_END>>": str(a1),
        "<<A2_START>>": str(a1 + 1),
        "<<A2_END>>": str(a2),
        "<<A3_START>>": str(a2 + 1),
        "<<A3_END>>": str(a3),
        "<<A4_START>>": str(a3 + 1),
        "<<REHOOK>>": str(max(a2 + 1, round(n * 0.45))),
        "_target_scenes": n,
    }


class ScriptError(RuntimeError):
    """LLM failed to produce a usable script after all retries."""


def _style_summary(style: dict) -> str:
    parts = [
        style.get("art_style", ""),
        style.get("visual_style", ""),
        f"characters: {style.get('character_design', '')}",
        f"mood: {style.get('mood', '')}",
    ]
    return "; ".join(p for p in parts if p and not p.endswith(": "))


def _build_prompt(topic: str, extra_context: str, style: dict, style_key: str | None,
                  target_minutes: int) -> str:
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    out = (
        template
        .replace("<<TOPIC>>", topic.strip())
        .replace("<<EXTRA_CONTEXT>>", (extra_context or "").strip() or "(none)")
        .replace("<<STYLE_KEY>>", style_key or "custom")
        .replace("<<STYLE_SUMMARY>>", _style_summary(style))
    )
    for token, value in _runtime_tokens(target_minutes).items():
        if token.startswith("<<"):
            out = out.replace(token, value)
    return out


def _extract_json(text: str) -> dict:
    """Parse the model output into a dict, tolerating markdown fences / preamble."""
    s = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", s, re.DOTALL)
    if fence:
        s = fence.group(1).strip()
    if not s.startswith("{"):
        start = s.find("{")
        end = s.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object found in model output")
        s = s[start:end + 1]
    return json.loads(s)


def _validate_script(payload: dict, target_scenes: int) -> dict:
    """Returns {"characters": {id: desc}, "scenes": [...]} — both validated."""
    characters = payload.get("characters")
    if not isinstance(characters, dict):
        characters = {}
    characters = {
        str(k): v.strip() for k, v in characters.items()
        if isinstance(v, str) and v.strip()
    }
    if len(characters) > 3:
        raise ValueError(f"too many characters: {len(characters)}")

    scenes = payload.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("'scenes' missing or empty")
    lo, hi = max(3, int(target_scenes * 0.6)), int(target_scenes * 1.6) + 2
    if not (lo <= len(scenes) <= hi):
        raise ValueError(
            f"scene count {len(scenes)} is far from the ~{target_scenes} requested"
        )
    out = []
    for i, s in enumerate(scenes):
        if not isinstance(s, dict):
            raise ValueError(f"scene {i + 1} is not an object")
        narration = s.get("narration")
        image_prompt = s.get("image_prompt")
        if not isinstance(narration, str) or not narration.strip():
            raise ValueError(f"scene {i + 1} missing narration")
        # Defensive: if the model ignored the plain-string rule, flatten it.
        if isinstance(image_prompt, dict):
            image_prompt = ", ".join(str(v) for v in image_prompt.values() if v)
        if not isinstance(image_prompt, str) or not image_prompt.strip():
            raise ValueError(f"scene {i + 1} missing image_prompt")
        cid = s.get("character_id")
        if cid is not None and str(cid) not in characters:
            cid = None  # unknown id → treat as a character-free scene
        out.append({
            "narration": narration.strip(),
            "image_prompt": image_prompt.strip(),
            "character_id": str(cid) if cid is not None else None,
        })
    return {"characters": characters, "scenes": out}


async def write_script(
    topic: str,
    extra_context: str,
    style: dict,
    style_key: str | None,
    target_minutes: int = 3,
) -> dict:
    """Returns validated {"characters": {id: desc}, "scenes": [{narration,
    image_prompt, character_id}, ...]} (style NOT yet applied)."""
    prompt = _build_prompt(topic, extra_context, style, style_key, target_minutes)
    target_scenes = _runtime_tokens(target_minutes)["_target_scenes"]
    messages = [{"role": "user", "content": prompt}]

    last_err: Exception | None = None
    async with httpx.AsyncClient(timeout=300.0) as client:
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            content = ""
            try:
                r = await client.post(
                    f"{cfg.openrouter_base.rstrip('/')}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {cfg.openrouter_api_key}",
                        "HTTP-Referer": "https://oneprompt.bot",
                        "X-Title": "OnePrompt",
                    },
                    json={
                        "model": cfg.openrouter_model,
                        "messages": messages,
                        "temperature": 0.8,
                        "max_tokens": 16000,
                    },
                )
                r.raise_for_status()
                content = r.json()["choices"][0]["message"]["content"]
                return _validate_script(_extract_json(content), target_scenes)
            except (json.JSONDecodeError, ValueError, KeyError) as e:
                # Bad output → tell the model what was wrong and retry.
                last_err = e
                log.warning("Script attempt %d/%d invalid: %s", attempt, _MAX_ATTEMPTS, e)
                messages.append({"role": "assistant", "content": content})
                messages.append({
                    "role": "user",
                    "content": (
                        f"Your previous output was invalid: {e}. "
                        "Return ONLY the JSON object in the exact format specified — "
                        "no prose, no markdown fences, image_prompt as a plain string."
                    ),
                })
            except httpx.HTTPError as e:
                last_err = e
                log.warning("OpenRouter call failed (attempt %d/%d): %s", attempt, _MAX_ATTEMPTS, e)

    raise ScriptError(f"script generation failed after {_MAX_ATTEMPTS} attempts: {last_err}")
