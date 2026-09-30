"""
Batch Video Describer - Multi-Model Concurrent Transcription

Processes multiple videos simultaneously across Gemini 3.5, 3.1 (5 RPM each),
and unlimited Qwen. Each video is locked to one worker — no duplication.
Models race when assigned a video; winner takes the result.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.config import settings
from services.ai.llm.factory import get_client
from services.ai.llm.base import MediaFile, Message
from services.media.media_service import (
    collect_videos,
    probe_video_duration,
    sample_video_frames,
)

DEFAULT_MANIFEST_NAME = ".video_analysis_manifest.json"

DEFAULT_PROMPT = """You are analyzing a combat video of Clare from the anime Claymore.

The video filename is the absolute ground truth for what action is being performed. It is not a suggestion — it is a fact. Your job is to describe how that action plays out in the footage. Do not contradict the filename, do not hedge, and do not reinterpret it.

Clare's appearance: short light-blonde hair in an A-line cut, silver eyes, wearing silver Claymore armor, carrying a large single-edged broadsword. In her partially awakened form her eyes turn gold with slit pupils, her face distorts, her legs become hock-jointed, blades grow from her right arm, and her left arm becomes large and claw-like. Use these to identify her in the footage.

Other characters may appear in the scene. Only mention them if doing so meaningfully changes the description of what Clare is doing — for example, naming who she flashes behind, who she clashes with, or who she is responding to. When you reference them, keep the focus on Clare. They are context, not the subject.

Core Fighting Abilities & Techniques:
- Preemptive Yoki Sensing: reads subtle flows of Yoki in opponents, allowing her to predict enemy movements and dodge faster foes.
- Quicksword (Fast Sword): inherited from Irene; she releases Yoki into a single arm to deliver dozens of high-speed slashes per second while keeping the rest of her body calm.
- Windcutter: mastered from Flora's style; ultra-fast sword slash performed without releasing Yoki, bypassing enemies trained to detect energy signatures.
- Partial Awakening & Yoki Suppression: can conceal Yoki energy while keeping her sensing active, and selectively awaken parts of her body for explosive speed or weaponized limbs.
- Soul Link / Teresa Awakened Form: absorbs memories and energy, allowing a spiritual bond that can project Teresa's spirit/form for unmatched power.

For every clip, extract and describe the following in 2-3 sentences of plain prose — cover as many of these as are visible in the footage:
- What action Clare performed
- How she performed it (technique, body mechanics, speed, weapon use)
- Clare's own position or stance when she performed it (standing, crouching, mid-air, behind the opponent, etc.)
- Where the action was performed relative to the opponent (front, behind, above, flanking, etc.)
- Where on the opponent it landed, if applicable (head, torso, limb, etc.) and what the result was
- If Clare says anything, quote it exactly — mandatory if present
- Anything else relevant and visible that adds to understanding what happened in the clip

After covering those, her expression or demeanor. Scenery is last priority and only if it genuinely adds context.

Return plain text only — no JSON, no bullet points, no formatting."""


def _gemini_api_key() -> str:
    key = settings.GOOGLE_API_KEY or os.getenv("GOOGLE_API_KEY", "").strip()
    if not key:
        raise SystemExit(
            "GOOGLE_API_KEY is not set. Add it to config.env or set it as an environment variable."
        )
    return key


def load_manifest(manifest_path: str | Path) -> dict[str, Any]:
    manifest_file = Path(manifest_path).expanduser().resolve()
    if manifest_file.is_dir():
        manifest_file = manifest_file / DEFAULT_MANIFEST_NAME
    if not manifest_file.exists():
        return {}
    try:
        return json.loads(manifest_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


class AsyncRateLimiter:
    """Per-model RPM limiter using sliding 60-second window."""
    
    def __init__(self, rpm_limit: int = 5):
        self.rpm_limit = max(1, int(rpm_limit))
        self._request_times: list[float] = []
        self._lock = asyncio.Lock()
    
    async def acquire(self) -> None:
        """Wait until RPM budget allows the next request."""
        async with self._lock:
            while True:
                now = time.monotonic()
                
                # Remove requests older than 60 seconds (sliding window)
                self._request_times = [t for t in self._request_times if now - t < 60.0]
                
                if len(self._request_times) < self.rpm_limit:
                    # Budget available - claim slot immediately
                    self._request_times.append(now)
                    return
                
                # Need to wait until oldest request exits the 60s window
                oldest = self._request_times[0]
                wait_time = 60.0 - (now - oldest) + 0.1
                
                # Release lock, sleep, then retry
                self._lock.release()
                await asyncio.sleep(wait_time)
                await self._lock.acquire()


class ModelClient:
    """Wrapper for a single LLM client with metadata."""
    
    def __init__(self, name: str, client: Any, rate_limiter: AsyncRateLimiter | None = None, is_qwen: bool = False):
        self.name = name
        self.client = client
        self.rate_limiter = rate_limiter
        self.is_qwen = is_qwen
    
    async def generate(self, message: Message) -> str | None:
        """Generate response with optional rate limiting."""
        try:
            if self.rate_limiter:
                # Log BEFORE acquiring to show we're waiting
                async with self.rate_limiter._lock:
                    active = len([t for t in self.rate_limiter._request_times if time.monotonic() - t < 60.0])
                    if active >= self.rate_limiter.rpm_limit:
                        print(f"[{self.name}] Waiting for slot ({active}/{self.rate_limiter.rpm_limit} active)...", file=sys.stderr, flush=True)
                
                await self.rate_limiter.acquire()
            
            # For Qwen, preprocess video into frames (non-blocking)
            if self.is_qwen and message.media:
                try:
                    video_path = message.media[0].path
                    
                    # Run frame extraction in thread pool to avoid blocking event loop
                    loop = asyncio.get_event_loop()
                    frames = await loop.run_in_executor(
                        None,
                        self._sample_frames_for_qwen,
                        video_path
                    )
                    
                    if not frames:
                        print(f"[{self.name}] Frame extraction returned empty", file=sys.stderr, flush=True)
                        return None
                    
                    frame_media = [MediaFile(path=frame) for frame in frames]
                    qwen_msg = Message(text=message.text, media=frame_media)
                    
                    # Run model inference in thread pool too
                    response = await loop.run_in_executor(None, self.client.generate, qwen_msg)
                    
                    # Cleanup frames
                    for frame in frames:
                        try:
                            frame.unlink()
                        except:
                            pass
                    try:
                        frames[0].parent.rmdir()
                    except:
                        pass
                    
                    return response.text.strip() if response else None
                except Exception as e:
                    print(f"[{self.name}] Frame processing error: {e}", file=sys.stderr, flush=True)
                    return None
            
            response = self.client.generate(message)
            return response.text.strip() if response else None
        except Exception as e:
            print(f"[{self.name}] Generation error: {e}", file=sys.stderr, flush=True)
            return None
    
    def _sample_frames_for_qwen(self, video_path: Path) -> list[Path]:
        """Sample frames distributed across entire video (max 60 frames, 5 fps)."""
        duration = probe_video_duration(video_path)
        if duration <= 0:
            return []
        
        # Calculate how many frames at 5 fps
        total_frames_at_5fps = int(duration * 5.0)
        
        if total_frames_at_5fps <= 60:
            # Video is short enough; sample at 5 fps
            return sample_video_frames(
                video_path,
                min_frames=1,
                max_frames=60,
                fps=5.0,
                max_dimension=512
            )
        else:
            # Video is longer; distribute 60 frames evenly across entire duration
            effective_fps = 60.0 / duration
            return sample_video_frames(
                video_path,
                min_frames=1,
                max_frames=60,
                fps=effective_fps,
                max_dimension=512
            )


class MultiModelTranscriber:
    """Manages multiple models. Each model works on separate videos (no racing)."""
    
    def __init__(self, gemini_api_key: str, rpm_limit: int = 5):
        self.gemini_api_key = gemini_api_key
        self.rpm_limit = rpm_limit
        self.models: list[ModelClient] = []
        self._init_models()
    
    def _init_models(self) -> None:
        """Initialize all available models."""
        # Gemini 3.5 Flash Lite with rate limiting
        try:
            client_3_5 = get_client(
                "gemini",
                api_key=self.gemini_api_key,
                model="gemini-3.5-flash-lite",
                temperature=0.1,
                max_output_tokens=2048
            )
            limiter_3_5 = AsyncRateLimiter(rpm_limit=self.rpm_limit)
            self.models.append(ModelClient("GEMINI-3.5", client_3_5, limiter_3_5))
            print(f"[INFO] Initialized Gemini 3.5 Flash Lite ({self.rpm_limit} RPM limit)", file=sys.stderr)
        except Exception as e:
            print(f"[WARN] Gemini 3.5 init failed: {e}", file=sys.stderr)
        
        # Gemini 3.1 Flash Lite with rate limiting
        try:
            client_3_1 = get_client(
                "gemini",
                api_key=self.gemini_api_key,
                model="gemini-3.1-flash-lite",
                temperature=0.1,
                max_output_tokens=2048
            )
            limiter_3_1 = AsyncRateLimiter(rpm_limit=self.rpm_limit)
            self.models.append(ModelClient("GEMINI-3.1", client_3_1, limiter_3_1))
            print(f"[INFO] Initialized Gemini 3.1 Flash Lite ({self.rpm_limit} RPM limit)", file=sys.stderr)
        except Exception as e:
            print(f"[WARN] Gemini 3.1 init failed: {e}", file=sys.stderr)
        
        # Qwen (no rate limit)
        try:
            qwen_model = os.environ.get("OLLAMA_FALLBACK_MODEL", "fredrezones55/Qwen3.5-APEX:latest")
            client_qwen = get_client("ollama", model=qwen_model)
            self.models.append(ModelClient("QWEN", client_qwen, rate_limiter=None, is_qwen=True))
            print("[INFO] Initialized Qwen (no rate limit, frame preprocessing)", file=sys.stderr)
        except Exception as e:
            print(f"[WARN] Qwen init failed: {e}", file=sys.stderr)
        
        if not self.models:
            raise RuntimeError("No models initialized successfully")
    
    async def transcribe(self, message: Message, model: ModelClient) -> tuple[str, str]:
        """Transcribe a single video with the assigned model."""
        result = await model.generate(message)
        if not result:
            raise RuntimeError(f"{model.name} failed to transcribe")
        return result, model.name


class BatchTranscriber:
    """
    Batch video transcriber with round-robin model assignment.
    
    Workflow:
    1. Get list of pending videos
    2. Each model is assigned videos in round-robin fashion
    3. Models work concurrently (Gemini models respect RPM limits, Qwen has none)
    4. No duplicate work: each video processed by exactly one model
    5. Results persisted to manifest after each successful transcription
    """
    
    def __init__(
        self,
        root: str | Path,
        *,
        output_dir: str | Path | None = None,
        manifest_path: str | Path | None = None,
        api_key: str | None = None,
        prompt: str | None = None,
        gemini_rpm_limit: int = 5,
    ):
        self.root = Path(root).expanduser().resolve()
        self.output_dir = Path(output_dir).expanduser().resolve() if output_dir else self.root
        self.manifest_path = (
            Path(manifest_path).expanduser().resolve() if manifest_path else self.root / DEFAULT_MANIFEST_NAME
        )
        self.prompt = (prompt or DEFAULT_PROMPT).strip()
        self.manifest = load_manifest(self.manifest_path)
        
        self.transcriber = MultiModelTranscriber(
            api_key or _gemini_api_key(),
            rpm_limit=gemini_rpm_limit
        )
        
        self._manifest_lock = asyncio.Lock()
    
    def _relative_path(self, video_path: Path) -> str:
        """Get relative path key for manifest storage."""
        return video_path.relative_to(self.root).as_posix()
    
    def _is_processed(self, video_path: Path) -> bool:
        """Check if video already has successful result."""
        relative_key = self._relative_path(video_path)
        return self.manifest.get(relative_key, {}).get("status") == "done"
    
    def _get_pending_videos(self) -> list[Path]:
        """Collect all unprocessed videos (dynamic check)."""
        all_videos = collect_videos(self.root)
        return [v for v in all_videos if not self._is_processed(v)]
    
    async def _save_result(self, video_path: Path, description: str, model_name: str) -> None:
        """Persist transcription result and update manifest."""
        relative_key = self._relative_path(video_path)
        output_path = self.output_dir / Path(relative_key).parent / f"{video_path.stem}.analysis.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        result = {
            "video_name": video_path.name,
            "video_path": str(video_path),
            "description": description,
            "model": model_name,
        }
        output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        
        async with self._manifest_lock:
            self.manifest[relative_key] = {
                "status": "done",
                "video_path": str(video_path),
                "output_path": str(output_path),
                "model": model_name,
                "processed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
            self.manifest_path.write_text(json.dumps(self.manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    
    async def _save_failure(self, video_path: Path, error: str) -> None:
        """Persist failure and update manifest."""
        relative_key = self._relative_path(video_path)
        async with self._manifest_lock:
            self.manifest[relative_key] = {
                "status": "failed",
                "error": error,
                "processed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
            self.manifest_path.write_text(json.dumps(self.manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    
    async def _process_video(self, video_path: Path, model: ModelClient, progress_counter: dict) -> None:
        """Process a single video using assigned model."""
        video_name = video_path.name
        
        try:
            # Check duration
            duration = probe_video_duration(video_path)
            if 0 < duration < 1.0:
                await self._save_result(video_path, video_path.stem, "skipped")
                async with self._manifest_lock:
                    progress_counter['completed'] += 1
                    print(f"[{progress_counter['completed']}/{progress_counter['total']}] [{model.name}] SKIP {video_name} (too short)", file=sys.stderr, flush=True)
                return
            
            # Build message
            full_prompt = f'{self.prompt}\n\nFilename: "{video_path.name}"'
            message = Message(text=full_prompt, media=[MediaFile(path=video_path)])
            
            # Transcribe with assigned model
            description, model_name = await self.transcriber.transcribe(message, model)
            
            await self._save_result(video_path, description, model_name)
            async with self._manifest_lock:
                progress_counter['completed'] += 1
                print(f"[{progress_counter['completed']}/{progress_counter['total']}] [{model.name}] ✓ {video_name}", file=sys.stderr, flush=True)
            
        except asyncio.CancelledError:
            raise
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            await self._save_failure(video_path, error_msg)
            async with self._manifest_lock:
                progress_counter['completed'] += 1
                print(f"[{progress_counter['completed']}/{progress_counter['total']}] [{model.name}] ✗ {video_name}: {error_msg}", file=sys.stderr, flush=True)
    
    async def run(self) -> None:
        """
        Run the batch transcriber with round-robin model assignment.
        
        Each model is assigned videos in sequence. All models work concurrently.
        """
        pending = self._get_pending_videos()
        if not pending:
            print("[OK] All videos already processed", file=sys.stderr)
            return
        
        models = self.transcriber.models
        if not models:
            print("[ERROR] No models available", file=sys.stderr)
            return
        
        print(f"[INFO] Found {len(pending)} videos to transcribe", file=sys.stderr)
        print(f"[INFO] {len(models)} model(s) available:", file=sys.stderr)
        for model in models:
            limit_str = f"{model.rate_limiter.rpm_limit} RPM" if model.rate_limiter else "unlimited"
            print(f"  - {model.name}: {limit_str}", file=sys.stderr)
        print(f"[START] Processing {len(pending)} videos...\n", file=sys.stderr)
        
        # Progress counter shared across tasks
        progress_counter = {'completed': 0, 'total': len(pending)}
        
        # Assign videos to models in round-robin
        tasks = []
        for idx, video_path in enumerate(pending):
            assigned_model = models[idx % len(models)]
            tasks.append(self._process_video(video_path, assigned_model, progress_counter))
        
        # Run all tasks concurrently
        await asyncio.gather(*tasks, return_exceptions=True)
        
        print(f"\n[DONE] Completed {progress_counter['completed']}/{progress_counter['total']} videos", file=sys.stderr)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch multi-model video transcriber with round-robin assignment"
    )
    parser.add_argument(
        "--root",
        type=str,
        default=str(Path(__file__).resolve().parents[2] / "assets" / "boss_clips"),
        help="Root folder to scan for videos"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Output directory for .analysis.json files (default: same as root)"
    )
    parser.add_argument(
        "--manifest-path",
        type=str,
        default=None,
        help="Path to manifest file (default: .video_analysis_manifest.json in root)"
    )
    parser.add_argument(
        "--prompt",
        type=str,
        default=DEFAULT_PROMPT,
        help="Custom prompt (inline)"
    )
    parser.add_argument(
        "--prompt-file",
        type=str,
        default=None,
        help="Load prompt from file"
    )
    parser.add_argument(
        "--gemini-rpm-limit",
        type=int,
        default=5,
        help="Requests-per-minute limit for Gemini models (default: 5)"
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Clear manifest and restart from scratch"
    )
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    
    root = Path(args.root).expanduser().resolve()
    if not root.exists():
        print(f"[ERROR] Root folder does not exist: {root}", file=sys.stderr)
        return 1
    
    prompt = args.prompt
    if args.prompt_file:
        prompt_file = Path(args.prompt_file).expanduser().resolve()
        if not prompt_file.exists():
            print(f"[ERROR] Prompt file not found: {prompt_file}", file=sys.stderr)
            return 1
        prompt = prompt_file.read_text(encoding="utf-8")
    
    manifest_path = (
        Path(args.manifest_path).expanduser().resolve()
        if args.manifest_path
        else root / DEFAULT_MANIFEST_NAME
    )
    if args.reset and manifest_path.exists():
        manifest_path.unlink()
        print(f"[INFO] Manifest reset", file=sys.stderr)
    
    transcriber = BatchTranscriber(
        root,
        output_dir=args.output_dir,
        manifest_path=manifest_path,
        api_key=_gemini_api_key(),
        prompt=prompt,
        gemini_rpm_limit=args.gemini_rpm_limit,
    )
    
    try:
        await transcriber.run()
        return 0
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
