"""Mechanical scene assembly — the style block is code, not LLM output.

The n8n workflow asked the LLM to copy the style block into every image
prompt (drift) and then wrapped it again with a hardcoded whiteboard style
(override bug). Here the user's resolved style is prepended verbatim by
code, so it is applied exactly once and can never be paraphrased or lost.
"""
from __future__ import annotations

BLANK_SUFFIX = (
    "All surfaces are completely blank. "
    "No text, no letters, no numbers anywhere in the image."
)


def style_prefix(style: dict) -> str:
    return (
        f"Render in this style: {style.get('art_style', '')}, "
        f"with {style.get('visual_style', '')}. "
        f"Characters: {style.get('character_design', '')}. "
        f"Backgrounds: {style.get('background_style', '')}. "
        f"Colors: {style.get('color_palette', '')}. "
        f"Overall mood: {style.get('mood', '')}."
    )


def assemble(scenes: list[dict], style: dict) -> list[dict]:
    """[{narration, image_prompt}] → same, with style prefix + no-text suffix applied."""
    prefix = style_prefix(style)
    out = []
    for s in scenes:
        prompt = s["image_prompt"].strip().rstrip(".") + "."
        out.append({
            "narration": s["narration"],
            "image_prompt": f"{prefix} {prompt} {BLANK_SUFFIX}",
        })
    return out
