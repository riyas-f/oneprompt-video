"""
Explainer-video generator on Modal — optimized single-container build.

WHY THIS IS FAST (vs. the 3-class version):
  • Both models live in ONE container → no inter-container RPC, no extra cold starts.
  • Inside a single request:
        - TTS runs in a background thread (parallel with image gen)
        - ffmpeg per-clip runs in a thread pool (parallel with the next image gen)
        - image gen stays sequential on the GPU (diffusers pipelines are NOT thread-safe)
  • Final concat uses stream-copy (`-c copy`) — clips are already h264/aac,
    so there's nothing to re-encode. Near-instant.
  • libx264 preset=veryfast for per-clip encoding (still-image+audio is trivial).
  • scaledown_window=600 keeps the container warm between requests.

GPU CHOICE: L40S ($1.95/hr on Modal).
  • Z-Image-Turbo SDNQ uint4 uses ~4-6 GB VRAM — L40S's 48 GB is overkill, no swap.
  • L40S is Modal's most cost-effective inference GPU; H100 ($3.95/hr) is
    ~2x faster but 2x cost = neutral, with worse availability.

API CONTRACT IS UNCHANGED. Same endpoints, same payload, same response.
"""

from io import BytesIO
from pathlib import Path
from typing import Optional, List
from concurrent.futures import ThreadPoolExecutor
import base64
import re as _re
import shutil
import tempfile
import threading
import time

import modal

# ─── Image (unchanged from your working version) ──────────────────────────────
image = (
    modal.Image.from_registry(
        "nvidia/cuda:12.8.1-devel-ubuntu22.04",
        add_python="3.11",
    )
    .entrypoint([])
    .apt_install("git", "ffmpeg", "espeak-ng")
    .pip_install("uv")
    .run_commands(
        "uv pip install --system --compile-bytecode flask torch==2.7.1 "
        "--extra-index-url https://download.pytorch.org/whl/cu128"
    )
    .run_commands(
        "uv pip install --system --compile-bytecode "
        "git+https://github.com/huggingface/diffusers "
        "git+https://github.com/Disty0/sdnq"
    )
    .run_commands(
        "uv pip install --system --compile-bytecode "
        "'kokoro>=0.9.4' soundfile ffmpeg-python Pillow"
    )
    # Qwen-Edit's text encoder (Qwen2.5-VL) imports torchvision; peft is
    # diffusers' LoRA backend for the Lightning distill. Pinned torchvision
    # matches torch==2.7.1+cu128 so the torch layer stays cached.
    .run_commands(
        "uv pip install --system --compile-bytecode torchvision==0.22.1 "
        "--extra-index-url https://download.pytorch.org/whl/cu128"
    )
    .run_commands("uv pip install --system --compile-bytecode peft")
)

MODEL_NAME = "Disty0/Z-Image-Turbo-SDNQ-uint4-svd-r32"

# Character consistency: scenes tagged with a character_id are re-rendered from
# a reference sheet by Qwen-Image-Edit-2511 (same SDNQ quant stack), distilled
# to 4 steps by the Lightning LoRA (~15s/scene on L40S at QEDIT resolution).
QWEN_EDIT_MODEL = "Disty0/Qwen-Image-Edit-2511-SDNQ-uint4-svd-r32"
LIGHTNING_REPO = "lightx2v/Qwen-Image-Edit-2511-Lightning"
LIGHTNING_FILE = "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors"
# Character scenes render at reduced internal resolution (~15s vs ~30s at full
# 1080p); _make_clip upscales to the video's target size with Lanczos.
QEDIT_WIDTH, QEDIT_HEIGHT = 1344, 768

# NB: keep these as POSIX strings — str(Path("/cache")) is "\cache" when
# deploying from Windows, which corrupts the image definition.
CACHE_DIR = Path("/cache")
cache_volume = modal.Volume.from_name("hf-hub-cache", create_if_missing=True)
volumes = {"/cache": cache_volume}

image = image.env({"HF_HOME": "/cache"})

app = modal.App("explainer-videos")

# Cross-container job progress: job_key -> {stage, scene, total, error, updated_at}.
# Written by the GPU worker, read by the flask status endpoint.
progress_dict = modal.Dict.from_name("oneprompt-job-progress", create_if_missing=True)

JOBS_DIR = CACHE_DIR / "jobs"
_JOB_KEY_RE = r"[A-Za-z0-9_\-]{1,64}"


def _report_progress(job_key: Optional[str], **fields) -> None:
    """Best-effort progress write; never lets telemetry break a render."""
    if not job_key:
        return
    try:
        progress_dict[job_key] = {"updated_at": time.time(), **fields}
    except Exception:
        pass

with image.imports():
    import torch
    import diffusers
    from sdnq.loader import apply_sdnq_options_to_model
    from flask import Flask, request, jsonify, send_file
    import soundfile as sf
    import ffmpeg
    import numpy as np
    from PIL import Image as PILImage


# ─── Single GPU class — image gen + TTS in one container ─────────────────────
@app.cls(
    image=image,
    gpu="L40S",
    volumes=volumes,
    scaledown_window=30,    # 30s warm — single-job guard means no back-to-back bursts
    timeout=20 * 60,
    cpu=4.0,                # for parallel ffmpeg encodes + TTS thread
    memory=16384,           # 16 GiB — plenty of headroom for ffmpeg buffers
)
class VideoGenerator:

    @modal.enter()
    def enter(self):
        """One-time model load. Runs only on cold start, then stays warm."""
        torch.set_float32_matmul_precision("high")
        self.device = "cuda"

        print("→ Loading Z-Image-Turbo (SDNQ uint4)...")
        self.pipe = diffusers.ZImagePipeline.from_pretrained(
            MODEL_NAME,
            torch_dtype=torch.float32,   # unchanged — preserves original output quality
            device_map="cuda",
            cache_dir=CACHE_DIR,
        )
        self.pipe.transformer = apply_sdnq_options_to_model(
            self.pipe.transformer, use_quantized_matmul=True
        )
        self.pipe.text_encoder = apply_sdnq_options_to_model(
            self.pipe.text_encoder, use_quantized_matmul=True
        )

        print("→ Loading Kokoro TTS...")
        from kokoro import KPipeline
        # Kokoro auto-detects CUDA. It's tiny (~82M params, <1 GB VRAM) and
        # CUDA's stream scheduler interleaves it cleanly with diffusion kernels.
        self.tts = KPipeline(lang_code="a")

        print("→ Loading Qwen-Image-Edit-2511 (character scenes)...")
        # Both models fit together on L40S (~27 GB of 48). Any failure here
        # falls back to text-only rendering rather than blocking video gen.
        self.qedit = None
        try:
            qedit = diffusers.QwenImageEditPlusPipeline.from_pretrained(
                QWEN_EDIT_MODEL,
                torch_dtype=torch.bfloat16,
                cache_dir=CACHE_DIR,
            )
            qedit.transformer = apply_sdnq_options_to_model(
                qedit.transformer, use_quantized_matmul=True
            )
            qedit.text_encoder = apply_sdnq_options_to_model(
                qedit.text_encoder, use_quantized_matmul=True
            )
            qedit.to("cuda")
            # Without the 4-step Lightning distill this model needs 40 CFG
            # steps (~2 min/scene) — too slow for production, so all-or-nothing.
            qedit.load_lora_weights(
                LIGHTNING_REPO, weight_name=LIGHTNING_FILE, cache_dir=CACHE_DIR
            )
            self.qedit = qedit
            print("✓ Qwen-Edit + Lightning ready")
        except Exception as e:
            print(f"⚠ Qwen-Edit unavailable ({e}) — character scenes render text-only")

        # Defensive locks: even though we use max_inputs=1 by default,
        # these protect against any future change to concurrency settings.
        self._pipe_lock = threading.Lock()
        self._tts_lock = threading.Lock()
        self._qedit_lock = threading.Lock()
        # Volume commits happen from the ffmpeg thread pool; serialize them.
        self._commit_lock = threading.Lock()

        print("✓ Container ready (image + TTS both loaded)")

    # ───── helpers ────────────────────────────────────────────────────────────
    @staticmethod
    def _adjust_dims(w: int, h: int, divisor: int = 16, min_size: int = 256):
        """Round (w, h) to nearest divisor while preserving aspect ratio."""
        aspect = w / h
        for offset in range(0, max(w, 1000), divisor):
            for tw in (w + offset, w - offset):
                if tw < min_size or tw % divisor != 0:
                    continue
                th = round(tw / aspect)
                if th >= min_size and th % divisor == 0:
                    return int(tw), int(th)
        return (w // divisor) * divisor, (h // divisor) * divisor

    def _generate_image(self, prompt: str, width: int, height: int,
                        seed: Optional[int] = None) -> bytes:
        import random
        width, height = self._adjust_dims(width, height)
        if seed is None:
            seed = random.randint(0, 2**32 - 1)
        gen = torch.manual_seed(seed)

        with self._pipe_lock, torch.inference_mode():
            img = self.pipe(
                prompt=prompt,
                height=height,
                width=width,
                num_inference_steps=9,
                guidance_scale=0.0,
                generator=gen,
            ).images[0]

        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def _generate_character_scene(self, ref_image, scene_prompt: str,
                                  seed: Optional[int] = None) -> bytes:
        """Render a scene featuring a known character, conditioned on its sheet."""
        import random
        if seed is None:
            seed = random.randint(0, 2**32 - 1)
        gen = torch.manual_seed(seed)
        instruction = (
            "Redraw the exact character from the reference image into a completely "
            "new scene, keeping the character's design, proportions, line style and "
            f"colors identical. New scene: {scene_prompt}"
        )
        with self._qedit_lock, torch.inference_mode():
            img = self.qedit(
                image=[ref_image],
                prompt=instruction,
                width=QEDIT_WIDTH,
                height=QEDIT_HEIGHT,
                num_inference_steps=4,
                true_cfg_scale=1.0,
                guidance_scale=1.0,
                negative_prompt=" ",
                num_images_per_prompt=1,
                generator=gen,
            ).images[0]
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def _generate_audio(self, text: str, voice: str = "af_heart",
                        speed: float = 1.0) -> bytes:
        with self._tts_lock:
            segs = [aud for _, _, aud in self.tts(text, voice=voice, speed=speed)]
        if not segs:
            raise ValueError("Kokoro produced no audio for the given text")
        buf = BytesIO()
        sf.write(buf, np.concatenate(segs), 24000, format="WAV")
        return buf.getvalue()

    def _make_clip(self, idx: int, image_bytes: bytes, audio_bytes: bytes,
                   target_w: int, target_h: int, temp_path: Path,
                   out_dir: Optional[Path] = None) -> str:
        """Encode one image+audio pair into an mp4 clip. CPU-bound; safe to run in a thread pool.

        When out_dir points at the volume (checkpointing on), the finished clip
        is written there and committed, so a retried run can skip this scene."""
        ipath = temp_path / f"scene_{idx}.png"
        apath = temp_path / f"scene_{idx}.wav"
        cpath = (out_dir or temp_path) / f"scene_{idx}.mp4"
        ipath.write_bytes(image_bytes)
        apath.write_bytes(audio_bytes)

        # Resize to exact target dims if the model returned an adjusted size.
        img = PILImage.open(ipath)
        if img.size != (target_w, target_h):
            img = img.resize((target_w, target_h), PILImage.Resampling.LANCZOS)
            img.save(ipath)

        duration = float(ffmpeg.probe(str(apath))["streams"][0]["duration"])

        (
            ffmpeg.input(str(ipath), loop=1, t=duration, framerate=30)
            .output(
                ffmpeg.input(str(apath)),
                str(cpath),
                vcodec="libx264",
                acodec="aac",
                pix_fmt="yuv420p",
                shortest=None,
                af="loudnorm=I=-16:TP=-1.5:LRA=11",
                **{
                    "b:a": "192k",
                    "preset": "veryfast",   # static image: quality is dominated by source, not preset
                    "tune": "stillimage",
                },
            )
            .overwrite_output()
            .run(quiet=True)
        )
        if out_dir is not None:
            with self._commit_lock:
                cache_volume.commit()
        return str(cpath)

    # ───── public entrypoint ──────────────────────────────────────────────────
    @modal.method()
    def generate_video(
        self,
        scenes: List[dict],
        voice: str = "af_heart",
        tts_speed: float = 1.0,
        image_width: int = 1920,
        image_height: int = 1080,
        job_key: Optional[str] = None,
        characters: Optional[dict] = None,
    ) -> bytes:
        """
        Pipelined generation.
            background thread  : TTS for all scenes (in order)
            main thread        : image gen on GPU (sequential, the bottleneck)
            ffmpeg thread pool : per-clip encode (3 workers, parallel)
            final concat       : stream-copy (instant)

        With a job_key (async path): per-scene clips are checkpointed to the
        volume so a retried/preempted run resumes instead of restarting, the
        final mp4 is left at /cache/jobs/{job_key}/final.mp4 for the status
        server to serve, and progress is reported to progress_dict.
        """
        try:
            return self._generate_video_impl(
                scenes, voice, tts_speed, image_width, image_height, job_key,
                characters,
            )
        except Exception as e:
            _report_progress(job_key, stage="failed", error=str(e)[:500])
            raise

    @staticmethod
    def _safe_char_id(cid: str) -> str:
        return "".join(c for c in str(cid) if c.isalnum() or c in "-_")[:32] or "char"

    def _prepare_character_sheets(self, characters: Optional[dict],
                                  job_dir: Optional[Path],
                                  job_key: Optional[str]) -> dict:
        """Generate (or restore from checkpoint) one reference image per character."""
        sheets: dict = {}
        if not characters or self.qedit is None:
            return sheets
        _report_progress(job_key, stage="sheets", total=len(characters))
        for cid, sheet_prompt in characters.items():
            if not isinstance(sheet_prompt, str) or not sheet_prompt.strip():
                continue
            ck = (job_dir / f"char_{self._safe_char_id(cid)}.png") if job_dir else None
            if ck is not None and ck.exists():
                sheets[cid] = PILImage.open(ck).convert("RGB")
                continue
            png = self._generate_image(sheet_prompt, 1024, 1024)
            if ck is not None:
                ck.write_bytes(png)
                with self._commit_lock:
                    cache_volume.commit()
            sheets[cid] = PILImage.open(BytesIO(png)).convert("RGB")
            print(f"  character sheet ready: {cid}")
        return sheets

    def _generate_video_impl(
        self,
        scenes: List[dict],
        voice: str,
        tts_speed: float,
        image_width: int,
        image_height: int,
        job_key: Optional[str],
        characters: Optional[dict] = None,
    ) -> bytes:
        valid = [(i, s) for i, s in enumerate(scenes)
                 if s.get("narration") and s.get("image_prompt")]
        if not valid:
            raise ValueError("No valid scenes provided")

        N = len(valid)
        print(f"→ Generating {N} scenes ({image_width}x{image_height})")

        # Checkpoint dir on the volume (async path only).
        job_dir: Optional[Path] = None
        done_clips: set = set()
        if job_key:
            try:
                cache_volume.reload()
            except Exception:
                pass
            job_dir = JOBS_DIR / job_key
            job_dir.mkdir(parents=True, exist_ok=True)
            done_clips = {
                i for i, _ in valid if (job_dir / f"scene_{i}.mp4").exists()
            }
            if done_clips:
                print(f"→ Resuming: {len(done_clips)}/{N} clips already checkpointed")

        todo = [(i, s) for i, s in valid if i not in done_clips]

        # Character reference sheets (only when the payload declares characters
        # and Qwen-Edit loaded; otherwise falls through to text-only rendering).
        sheets = self._prepare_character_sheets(characters, job_dir, job_key)

        _report_progress(job_key, stage="rendering", scene=len(done_clips), total=N)

        with tempfile.TemporaryDirectory() as td:
            tp = Path(td)

            # --- TTS background worker (only scenes still to render) ---------
            audio_results: dict = {}
            audio_events = {i: threading.Event() for i, _ in todo}

            def tts_worker():
                for i, scene in todo:
                    try:
                        audio_results[i] = self._generate_audio(
                            scene["narration"], voice=voice, speed=tts_speed
                        )
                    except Exception as e:
                        audio_results[i] = e
                    finally:
                        audio_events[i].set()

            tts_thread = threading.Thread(target=tts_worker, daemon=True)
            tts_thread.start()

            # --- Image gen (sequential, GPU) + ffmpeg pool (parallel, CPU) ---
            with ThreadPoolExecutor(max_workers=3) as ffmpeg_pool:
                clip_futures: dict = {}

                for n, (i, scene) in enumerate(todo):
                    cid = scene.get("character_id")
                    if cid and cid in sheets:
                        print(f"  [{i + 1}/{N}] character scene ({cid})…")
                        image_bytes = self._generate_character_scene(
                            sheets[cid], scene["image_prompt"]
                        )
                    else:
                        print(f"  [{i + 1}/{N}] image…")
                        image_bytes = self._generate_image(
                            scene["image_prompt"], image_width, image_height
                        )

                    # By the time the image is done, the corresponding audio
                    # is almost always already finished (TTS is faster than
                    # diffusion at ~real-time-factor < 1).
                    audio_events[i].wait()
                    aud = audio_results[i]
                    if isinstance(aud, Exception):
                        raise aud

                    print(f"  [{i + 1}/{N}] queueing ffmpeg encode…")
                    clip_futures[i] = ffmpeg_pool.submit(
                        self._make_clip, i, image_bytes, aud,
                        image_width, image_height, tp, job_dir,
                    )
                    _report_progress(
                        job_key, stage="rendering",
                        scene=len(done_clips) + n + 1, total=N,
                    )

                tts_thread.join()
                # Block until all per-clip encodes finish.
                for i, _ in todo:
                    clip_futures[i].result()

            # All clips now exist — in job_dir (async) or tp (sync legacy).
            clip_dir = job_dir or tp
            clip_paths = [str(clip_dir / f"scene_{i}.mp4") for i, _ in valid]

            # --- Final concat: stream-copy, no re-encode --------------------
            final_path = (job_dir or tp) / "final.mp4"
            if len(clip_paths) == 1:
                shutil.copy(clip_paths[0], final_path)
            else:
                concat_file = tp / "concat.txt"
                concat_file.write_text(
                    "\n".join(f"file '{p}'" for p in clip_paths) + "\n"
                )
                (
                    ffmpeg.input(str(concat_file), format="concat", safe=0)
                    .output(str(final_path), c="copy")   # ← stream copy = instant
                    .overwrite_output()
                    .run(quiet=True)
                )

            video_bytes = final_path.read_bytes()

            if job_dir is not None:
                # Keep final.mp4 for the result endpoint; scene clips are done.
                for i, _ in valid:
                    try:
                        (job_dir / f"scene_{i}.mp4").unlink(missing_ok=True)
                    except Exception:
                        pass
                for ck in job_dir.glob("char_*.png"):
                    try:
                        ck.unlink()
                    except Exception:
                        pass
                with self._commit_lock:
                    cache_volume.commit()

            _report_progress(job_key, stage="done", scene=N, total=N,
                             size_bytes=len(video_bytes))
            print(f"✓ Done: {len(video_bytes) / (1024 * 1024):.1f} MB")
            return video_bytes


# ─── Flask web endpoint — same API contract as your existing clients ─────────
@app.function(
    image=image,
    volumes=volumes,
    cpu=0.5,
    memory=2048,
    timeout=25 * 60,
    scaledown_window=120,
)
@modal.wsgi_app()
def flask_app():
    web_app = Flask(__name__)

    def _parse_payload():
        """Shared request validation. Returns (params_dict, None) or (None, flask_error)."""
        data = request.get_json(force=True)
        first = data[0] if isinstance(data, list) and data else data
        if first is None or first == []:
            return None, (jsonify({"error": "Empty input"}), 400)

        scenes = first.get("scenes", [])
        if not scenes:
            return None, (jsonify({"error": "'scenes' array is required"}), 400)
        for i, s in enumerate(scenes):
            if not s.get("narration"):
                return None, (jsonify({"error": f"Scene {i + 1} missing 'narration'"}), 400)
            if not s.get("image_prompt"):
                return None, (jsonify({"error": f"Scene {i + 1} missing 'image_prompt'"}), 400)

        characters = first.get("characters")
        if not isinstance(characters, dict):
            characters = None

        return {
            "scenes": scenes,
            "voice": first.get("voice", "af_heart"),
            "tts_speed": first.get("tts_speed", 1.0),
            "image_width": first.get("image_width", 1920),
            "image_height": first.get("image_height", 1080),
            "return_b64": first.get("return_base64", False),
            "job_key": str(first.get("job_key") or "").strip(),
            "characters": characters,
        }, None

    def _valid_job_key(job_key: str) -> bool:
        return bool(_re.fullmatch(_JOB_KEY_RE, job_key))

    @web_app.route("/")
    def health():
        return jsonify({"status": "alive"})

    @web_app.route("/generate-video", methods=["POST"])
    def generate_video():
        try:
            p, err = _parse_payload()
            if err:
                return err
            scenes = p["scenes"]
            voice, tts_speed = p["voice"], p["tts_speed"]
            image_width, image_height = p["image_width"], p["image_height"]
            return_b64 = p["return_b64"]

            print(f"→ Request: {len(scenes)} scenes | "
                  f"voice={voice} | {image_width}x{image_height}")

            video_bytes = VideoGenerator().generate_video.remote(
                scenes=scenes,
                voice=voice,
                tts_speed=tts_speed,
                image_width=image_width,
                image_height=image_height,
                characters=p["characters"],
            )

            if return_b64:
                return jsonify({
                    "video": base64.b64encode(video_bytes).decode("utf-8"),
                    "format": "mp4",
                    "size_bytes": len(video_bytes),
                })

            return send_file(
                BytesIO(video_bytes),
                mimetype="video/mp4",
                as_attachment=True,
                download_name="explainer_video.mp4",
            )

        except Exception as e:
            import traceback
            print(f"Error: {e}\n{traceback.format_exc()}")
            return jsonify({"error": str(e)}), 500

    # ─── Async job API (used by the OnePrompt bot's internal pipeline) ───────
    @web_app.route("/generate-video-async", methods=["POST"])
    def generate_video_async():
        try:
            p, err = _parse_payload()
            if err:
                return err
            job_key = p["job_key"]
            if not _valid_job_key(job_key):
                return jsonify({"error": "'job_key' is required ([A-Za-z0-9_-], max 64)"}), 400

            # Mark queued BEFORE spawning so a fast poll never sees 404.
            progress_dict[job_key] = {"stage": "queued", "updated_at": time.time()}

            call = VideoGenerator().generate_video.spawn(
                scenes=p["scenes"],
                voice=p["voice"],
                tts_speed=p["tts_speed"],
                image_width=p["image_width"],
                image_height=p["image_height"],
                job_key=job_key,
                characters=p["characters"],
            )
            print(f"→ Async job {job_key}: {len(p['scenes'])} scenes spawned "
                  f"(call {call.object_id})")
            return jsonify({"job_key": job_key, "call_id": call.object_id}), 202

        except Exception as e:
            import traceback
            print(f"Error: {e}\n{traceback.format_exc()}")
            return jsonify({"error": str(e)}), 500

    @web_app.route("/job/<job_key>/status")
    def job_status(job_key: str):
        if not _valid_job_key(job_key):
            return jsonify({"error": "bad job_key"}), 400
        try:
            rec = dict(progress_dict[job_key])
        except KeyError:
            return jsonify({"status": "unknown"}), 404
        stage = rec.get("stage", "queued")
        status = "done" if stage == "done" else "failed" if stage == "failed" else "running"
        return jsonify({"status": status, **rec})

    @web_app.route("/job/<job_key>/result")
    def job_result(job_key: str):
        if not _valid_job_key(job_key):
            return jsonify({"error": "bad job_key"}), 400
        try:
            cache_volume.reload()
        except Exception:
            pass
        path = JOBS_DIR / job_key / "final.mp4"
        if not path.exists():
            return jsonify({"error": "result not ready"}), 404
        return send_file(
            BytesIO(path.read_bytes()),
            mimetype="video/mp4",
            as_attachment=True,
            download_name=f"{job_key}.mp4",
        )

    @web_app.route("/job/<job_key>", methods=["DELETE"])
    def job_cleanup(job_key: str):
        if not _valid_job_key(job_key):
            return jsonify({"error": "bad job_key"}), 400
        try:
            cache_volume.reload()
        except Exception:
            pass
        shutil.rmtree(JOBS_DIR / job_key, ignore_errors=True)
        # Opportunistic GC: drop job dirs older than 48h whose caller vanished.
        try:
            cutoff = time.time() - 48 * 3600
            if JOBS_DIR.exists():
                for d in JOBS_DIR.iterdir():
                    if d.is_dir() and d.stat().st_mtime < cutoff:
                        shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass
        try:
            cache_volume.commit()
        except Exception:
            pass
        try:
            del progress_dict[job_key]
        except KeyError:
            pass
        return jsonify({"ok": True})

    return web_app
