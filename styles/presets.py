"""Style + voice preset catalogs.

Single source of truth — used by /style, /voice, /status, /help, and the
payload sent to the API server. Built-ins live here as code (no DB rows);
user-defined custom styles live in user_preferences.style_custom (JSONB).
"""
from __future__ import annotations

# ─── Style presets ────────────────────────────────────────────────────────────
# Each value is the JSON object the API server pastes into the image-prompt
# style block, verbatim. Each preset also has a label + emoji + tagline for UI.

STYLE_PRESETS: dict[str, dict] = {
    "whiteboard": {
        "label": "Whiteboard Doodle",
        "emoji": "✏️",
        "tagline": "Hand-drawn explainer — playful, tutorial energy",
        "best_for": "Tutorials, life advice, comedy explainers",
        "style": {
            "art_style": "2D hand-drawn whiteboard animation",
            "visual_style": "flat colors, minimal shading, imperfect children-like sketch lines, single-stroke outlines",
            "character_design": "stick figure with white circular head and simple facial features, consistent across all scenes",
            "background_style": "plain off-white background with single hand-drawn props relevant to the scene",
            "color_palette": "soft pastels and muted primaries, max 4 colors per frame",
            "mood": "light, playful, slightly comedic",
        },
    },
    "anime": {
        "label": "Anime / Manga",
        "emoji": "🌸",
        "tagline": "Studio-quality anime — dramatic and expressive",
        "best_for": "Stories, mysteries, motivation",
        "style": {
            "art_style": "modern Japanese anime, cel-shaded with crisp linework",
            "visual_style": "dynamic poses, expressive eyes, motion lines, dramatic camera angles, speed lines for action",
            "character_design": "stylized anime character with consistent hairstyle, outfit, and color palette across all scenes",
            "background_style": "detailed anime backgrounds with depth, soft bokeh, atmospheric perspective",
            "color_palette": "saturated jewel tones with high contrast highlights and deep shadows",
            "mood": "emotional, cinematic, intense",
        },
    },
    "cinematic": {
        "label": "Cinematic Photoreal",
        "emoji": "🎬",
        "tagline": "Hollywood-grade realism — looks like a film",
        "best_for": "History, true crime, science docs",
        "style": {
            "art_style": "cinematic photorealistic still",
            "visual_style": "shot on 35mm film, shallow depth of field, volumetric lighting, motion blur, subtle film grain",
            "character_design": "realistic adult of varied demographics, expressive face, consistent wardrobe across scenes",
            "background_style": "real-world environment matching the topic, atmospheric haze, golden-hour or moody-blue lighting",
            "color_palette": "teal-and-orange cinematic grade, deep blacks, lifted shadows",
            "mood": "epic, suspenseful, documentary-grade",
        },
    },
    "pixar3d": {
        "label": "3D Pixar",
        "emoji": "🧸",
        "tagline": "Cute 3D animation — family-friendly fun",
        "best_for": "Kids, family content, fun facts",
        "style": {
            "art_style": "high-quality 3D Pixar-style animation render",
            "visual_style": "soft global illumination, subsurface scattering on skin, slightly exaggerated proportions, big expressive eyes",
            "character_design": "rounded, friendly 3D character with consistent design across scenes — same outfit, hair, and color scheme",
            "background_style": "stylized 3D environment with depth, warm ambient lighting, gentle bokeh",
            "color_palette": "warm saturated colors, hero lighting on the subject, complementary background tones",
            "mood": "wholesome, charming, uplifting",
        },
    },
    "comic": {
        "label": "Comic Book",
        "emoji": "💥",
        "tagline": "Bold panels — action and impact",
        "best_for": "Action, conspiracy, did-you-know",
        "style": {
            "art_style": "American comic book illustration, inked and color-flatted",
            "visual_style": "bold black ink outlines, halftone dot shading, dynamic angles, dramatic foreshortening",
            "character_design": "muscular or stylized comic character with consistent costume and hair across scenes",
            "background_style": "stylized comic backgrounds with hatching, motion lines, and impact frames",
            "color_palette": "high-saturation primaries with strong red/blue/yellow contrast, deep blacks",
            "mood": "intense, kinetic, larger-than-life",
        },
    },
    "minimalist": {
        "label": "Minimalist Line Art",
        "emoji": "◻️",
        "tagline": "Clean modern lines — confident and pro",
        "best_for": "Tech, business, productivity",
        "style": {
            "art_style": "minimalist flat vector illustration",
            "visual_style": "clean geometric shapes, single-weight line work, generous negative space, subtle gradients",
            "character_design": "abstracted human figure with simplified features, consistent silhouette across scenes",
            "background_style": "solid color or single subtle gradient background, no extraneous detail",
            "color_palette": "two or three accent colors maximum on a neutral base, modern editorial palette",
            "mood": "calm, confident, expert",
        },
    },
    "synthwave": {
        "label": "Retro Synthwave",
        "emoji": "🌆",
        "tagline": "80s neon nostalgia — cool and futuristic",
        "best_for": "Gaming, 80s nostalgia, futurism",
        "style": {
            "art_style": "1980s synthwave / retrowave digital illustration",
            "visual_style": "neon grid horizons, chrome reflections, scan lines, VHS glitch artifacts, sun-with-stripes motif",
            "character_design": "stylized silhouette or chrome-suited figure, consistent across scenes",
            "background_style": "purple-to-pink gradient sky, distant geometric mountains, neon grid floor stretching to horizon",
            "color_palette": "electric pink, cyan, deep purple, neon orange, on near-black",
            "mood": "energetic, nostalgic, slightly cocky",
        },
    },
    "watercolor": {
        "label": "Watercolor Storybook",
        "emoji": "🎨",
        "tagline": "Soft painted scenes — gentle and reflective",
        "best_for": "Lifestyle, mindfulness, history",
        "style": {
            "art_style": "traditional watercolor children's-book illustration",
            "visual_style": "soft wet-on-wet color bleeds, visible paper texture, loose pencil under-sketch, gentle ink details",
            "character_design": "warm storybook character with consistent clothing and proportions across scenes",
            "background_style": "soft painted environments with negative-space whites, atmospheric color washes",
            "color_palette": "muted earth tones with one accent color per scene, warm and inviting",
            "mood": "gentle, reflective, almost whispered",
        },
    },
}

DEFAULT_STYLE_PRESET = "whiteboard"


def get_style_object(preset_key: str | None, custom: dict | None) -> dict:
    """Resolve a (preset_key, custom) pair into the JSON style object the API
    server should embed in the LLM prompt. Custom wins; otherwise look up the
    preset; otherwise fall back to default."""
    if custom:
        return custom
    if preset_key and preset_key in STYLE_PRESETS:
        return STYLE_PRESETS[preset_key]["style"]
    return STYLE_PRESETS[DEFAULT_STYLE_PRESET]["style"]


def get_style_label(preset_key: str | None, custom: dict | None) -> str:
    """Human-readable label for /status and /help."""
    if custom:
        return "✨ Custom"
    if preset_key and preset_key in STYLE_PRESETS:
        p = STYLE_PRESETS[preset_key]
        return f"{p['emoji']} {p['label']}"
    return f"{STYLE_PRESETS[DEFAULT_STYLE_PRESET]['emoji']} {STYLE_PRESETS[DEFAULT_STYLE_PRESET]['label']}"


# ─── Voice presets ────────────────────────────────────────────────────────────
# Keys starting with cb_ route to Chatterbox Turbo on the render side (more
# natural prosody, supports [chuckle]/[sigh] tags); the rest are Kokoro.
VOICE_PRESETS: dict[str, dict] = {
    "cb_aria": {
        "label": "Aria — Natural Female",
        "emoji": "✨",
        "tagline": "NEW · warm and lifelike — storytelling",
    },
    "cb_atlas": {
        "label": "Atlas — Natural Male",
        "emoji": "✨",
        "tagline": "NEW · steady and lifelike — explainers",
    },
    "cb_ember": {
        "label": "Ember — Bright Female",
        "emoji": "✨",
        "tagline": "NEW · crisp and energetic — facts, lists",
    },
    "cb_oak": {
        "label": "Oak — Deep Male",
        "emoji": "✨",
        "tagline": "NEW · low and deliberate — mystery, docs",
    },
    "af_heart": {
        "label": "Friendly Female",
        "emoji": "🎙️",
        "tagline": "Warm, conversational — tutorial energy",
    },
    "af_sky": {
        "label": "News Female",
        "emoji": "📰",
        "tagline": "Crisp, factual — documentary",
    },
    "am_michael": {
        "label": "Confident Male",
        "emoji": "🎬",
        "tagline": "Strong, deliberate — action / business",
    },
    "am_eric": {
        "label": "Narrator Male",
        "emoji": "🎙️",
        "tagline": "Mid-pitch, smooth — storytelling",
    },
    "bf_alice": {
        "label": "British Female",
        "emoji": "🇬🇧",
        "tagline": "Posh, articulate — lifestyle / history",
    },
    "bm_lewis": {
        "label": "British Male",
        "emoji": "🇬🇧",
        "tagline": "Deep, gravelly — mystery / true crime",
    },
}

DEFAULT_VOICE = "af_heart"


def get_voice_label(voice_key: str | None) -> str:
    if voice_key and voice_key in VOICE_PRESETS:
        v = VOICE_PRESETS[voice_key]
        return f"{v['emoji']} {v['label']}"
    v = VOICE_PRESETS[DEFAULT_VOICE]
    return f"{v['emoji']} {v['label']}"


# ─── Custom-style field schema (used by the modal in /style) ─────────────────
# Discord modals support max 5 TextInput components. We collapse art_style +
# visual_style into one "Art Style" field on the form, then split into the
# canonical 6-key JSON shape on save.
CUSTOM_STYLE_FIELDS = [
    ("art_style",        "Art Style",       "e.g. 2D hand-drawn animation, flat colors, minimal shading"),
    ("character_design", "Character",       "e.g. stick figure with white circular head"),
    ("background_style", "Background",      "e.g. cartoon city street with plain wall"),
    ("color_palette",    "Color Palette",   "e.g. soft pastels and muted primaries"),
    ("mood",             "Mood",            "e.g. light, playful, slightly comedic"),
]


def custom_style_from_form(values: dict[str, str]) -> dict:
    """Build the canonical 6-key style object from the 5-field modal form."""
    art = values.get("art_style", "").strip()
    return {
        "art_style": art.split(",")[0].strip() if "," in art else art,
        "visual_style": art,
        "character_design": values.get("character_design", "").strip(),
        "background_style": values.get("background_style", "").strip(),
        "color_palette": values.get("color_palette", "").strip(),
        "mood": values.get("mood", "").strip() or "neutral",
    }
