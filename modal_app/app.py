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
import tempfile
import threading

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
)

MODEL_NAME = "Disty0/Z-Image-Turbo-SDNQ-uint4-svd-r32"

CACHE_DIR = Path("/cache")
cache_volume = modal.Volume.from_name("hf-hub-cache", create_if_missing=True)
volumes = {CACHE_DIR: cache_volume}

image = image.env({"HF_HOME": str(CACHE_DIR)})

app = modal.App("explainer-videos")

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

        # Defensive locks: even though we use max_inputs=1 by default,
        # these protect against any future change to concurrency settings.
        self._pipe_lock = threading.Lock()
        self._tts_lock = threading.Lock()

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

    def _generate_audio(self, text: str, voice: str = "af_heart",
                        speed: float = 1.0) -> bytes:
        with self._tts_lock:
            segs = [aud for _, _, aud in self.tts(text, voice=voice, speed=speed)]
        if not segs:
            raise ValueError("Kokoro produced no audio for the given text")
        buf = BytesIO()
        sf.write(buf, np.concatenate(segs), 24000, format="WAV")
        return buf.getvalue()

    @staticmethod
    def _make_clip(idx: int, image_bytes: bytes, audio_bytes: bytes,
                   target_w: int, target_h: int, temp_path: Path) -> str:
        """Encode one image+audio pair into an mp4 clip. CPU-bound; safe to run in a thread pool."""
        ipath = temp_path / f"scene_{idx}.png"
        apath = temp_path / f"scene_{idx}.wav"
        cpath = temp_path / f"scene_{idx}.mp4"
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
    ) -> bytes:
        """
        Pipelined generation.
            background thread  : TTS for all scenes (in order)
            main thread        : image gen on GPU (sequential, the bottleneck)
            ffmpeg thread pool : per-clip encode (3 workers, parallel)
            final concat       : stream-copy (instant)
        """
        valid = [(i, s) for i, s in enumerate(scenes)
                 if s.get("narration") and s.get("image_prompt")]
        if not valid:
            raise ValueError("No valid scenes provided")

        N = len(valid)
        print(f"→ Generating {N} scenes ({image_width}x{image_height})")

        with tempfile.TemporaryDirectory() as td:
            tp = Path(td)

            # --- TTS background worker ---------------------------------------
            audio_results: dict = {}
            audio_events = {i: threading.Event() for i, _ in valid}

            def tts_worker():
                for i, scene in valid:
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

                for i, scene in valid:
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
                        image_width, image_height, tp,
                    )

                tts_thread.join()
                # Block until all per-clip encodes finish.
                clip_paths = [clip_futures[i].result() for i, _ in valid]

            # --- Final concat: stream-copy, no re-encode --------------------
            final_path = tp / "final.mp4"
            if len(clip_paths) == 1:
                import shutil
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

    @web_app.route("/")
    def health():
        return jsonify({"status": "alive"})

    @web_app.route("/generate-video", methods=["POST"])
    def generate_video():
        try:
            data = request.get_json(force=True)
            first = data[0] if isinstance(data, list) and data else data
            if first is None or first == []:
                return jsonify({"error": "Empty input"}), 400

            scenes = first.get("scenes", [])
            voice = first.get("voice", "af_heart")
            tts_speed = first.get("tts_speed", 1.0)
            image_width = first.get("image_width", 1920)
            image_height = first.get("image_height", 1080)
            return_b64 = first.get("return_base64", False)

            if not scenes:
                return jsonify({"error": "'scenes' array is required"}), 400

            for i, s in enumerate(scenes):
                if not s.get("narration"):
                    return jsonify({"error": f"Scene {i + 1} missing 'narration'"}), 400
                if not s.get("image_prompt"):
                    return jsonify({"error": f"Scene {i + 1} missing 'image_prompt'"}), 400

            print(f"→ Request: {len(scenes)} scenes | "
                  f"voice={voice} | {image_width}x{image_height}")

            video_bytes = VideoGenerator().generate_video.remote(
                scenes=scenes,
                voice=voice,
                tts_speed=tts_speed,
                image_width=image_width,
                image_height=image_height,
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

    return web_app
