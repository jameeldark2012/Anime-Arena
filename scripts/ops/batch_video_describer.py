from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# Allow running as a script from the project root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.config import settings
from services.ai import GeminiClient, MediaFile, Message, RateLimiter, normalize_model_name
from services.ai.gemini import DEFAULT_MODEL
from services.ai.rate_limiter import DEFAULT_RPM_LIMIT
from services.media.media_service import (
    collect_videos,
    estimate_video_tokens,
    probe_video_duration,
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


def _api_key() -> str:
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


def _analysis_path_for(video_path: Path) -> Path:
    return video_path.with_name(f"{video_path.stem}.analysis.json")


def estimate_processing_schedule(
    videos: list[Path], *, tpm_limit: int, safety_margin: float
) -> dict[str, float | int]:
    if not videos:
        return {
            "average_duration_seconds": 0.0,
            "estimated_tokens_per_video": 0,
            "effective_tpm_budget": 0,
            "target_interval_seconds": 0.0,
        }
    durations = [d for v in videos if (d := probe_video_duration(v)) > 0]
    average_duration = sum(durations) / len(durations) if durations else 0.0
    tokens_per_video = estimate_video_tokens(average_duration)
    effective_budget = max(1, int(tpm_limit * safety_margin))
    target_interval = (
        60.0 / max(1, effective_budget // max(1, tokens_per_video))
        if tokens_per_video else 0.0
    )
    return {
        "average_duration_seconds": average_duration,
        "estimated_tokens_per_video": tokens_per_video,
        "effective_tpm_budget": effective_budget,
        "target_interval_seconds": target_interval,
    }


class BatchAnalyzer:
    MIN_DURATION_SECONDS = 1.0

    def __init__(
        self,
        root: str | Path,
        *,
        output_dir: str | Path | None = None,
        manifest_path: str | Path | None = None,
        api_key: str | None = None,
        model: str | None = None,
        prompt: str | None = None,
        max_retries: int = 4,
        rate_limiter: RateLimiter | None = None,
    ):
        self.root = Path(root).expanduser().resolve()
        self.output_dir = Path(output_dir).expanduser().resolve() if output_dir else self.root
        resolved_manifest = (
            Path(manifest_path).expanduser().resolve()
            if manifest_path
            else self.root / DEFAULT_MANIFEST_NAME
        )
        if resolved_manifest.is_dir():
            resolved_manifest = resolved_manifest / DEFAULT_MANIFEST_NAME
        self.manifest_path = resolved_manifest
        self.prompt = (prompt or DEFAULT_PROMPT).strip()
        self.max_retries = max(0, int(max_retries))
        self.rate_limiter = rate_limiter or RateLimiter(
            tpm_limit=65000, rpm_limit=DEFAULT_RPM_LIMIT, safety_margin=0.8
        )
        self.manifest = load_manifest(self.manifest_path)

        resolved_key = api_key or _api_key()
        resolved_model = normalize_model_name(model or settings.GOOGLE_MODEL or os.getenv("GOOGLE_MODEL", DEFAULT_MODEL))
        self.client = GeminiClient(api_key=resolved_key, model=resolved_model)

    # ------------------------------------------------------------------
    # Manifest / path helpers
    # ------------------------------------------------------------------

    def _relative_path(self, video_path: Path) -> str:
        return video_path.relative_to(self.root).as_posix()

    def is_processed(self, video_path: Path) -> bool:
        if not video_path.is_file():
            return False
        relative_key = self._relative_path(video_path)
        if self.manifest.get(relative_key, {}).get("status") == "done":
            return True
        if self.output_dir == self.root:
            return _analysis_path_for(video_path).exists()
        rel = self._relative_path(video_path)
        base = self.output_dir / rel
        stem = Path(rel).stem
        return any(
            (self.output_dir / Path(rel).parent / f"{stem}{ext}").exists()
            for ext in (".analysis.json", ".json", ".txt")
        )

    def get_pending_files(self) -> list[Path]:
        return [v for v in collect_videos(self.root) if not self.is_processed(v)]

    def _result_path_for(self, video_path: Path) -> Path:
        if self.output_dir == self.root:
            return _analysis_path_for(video_path)
        rel = self._relative_path(video_path)
        return self.output_dir / Path(rel).parent / f"{video_path.stem}.analysis.json"

    def _save_manifest(self) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(
            json.dumps(self.manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def _record_success(self, video_path: Path, result: dict[str, Any], output_path: Path) -> None:
        relative_key = self._relative_path(video_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        self.manifest[relative_key] = {
            "status": "done",
            "video_path": str(video_path),
            "relative_path": relative_key,
            "output_path": str(output_path),
            "processed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self._save_manifest()

    def _record_failure(self, video_path: Path, error: Exception | str) -> None:
        relative_key = self._relative_path(video_path)
        self.manifest[relative_key] = {
            "status": "failed",
            "video_path": str(video_path),
            "relative_path": relative_key,
            "error": str(error),
            "processed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self._save_manifest()

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------

    def _process_single_video(self, video_path: Path) -> dict[str, Any]:
        duration = probe_video_duration(video_path)
        if 0 < duration < self.MIN_DURATION_SECONDS:
            result = {
                "video_name": video_path.name,
                "video_path": str(video_path),
                "description": video_path.stem,
            }
            self._record_success(video_path, result, self._result_path_for(video_path))
            print(
                f"[SKIP] {video_path.name} — too short ({duration:.2f}s), description set to filename.",
                file=sys.stderr,
            )
            return result

        # Build the prompt with the filename injected at the end as the strongest signal
        full_prompt = (
            f'{self.prompt}\n\n'
            f'Filename: "{video_path.name}" — numbers in the filename are sequence IDs only, not part of the action. Ignore them.'
        )

        message = Message(
            text=full_prompt,
            media=[MediaFile(path=video_path)],
        )
        response = self.client.generate(message)

        return {
            "video_name": video_path.name,
            "video_path": str(video_path),
            "description": response.text,
        }

    def process_file(self, video_path: Path) -> dict[str, Any]:
        attempts = 0
        while attempts <= self.max_retries:
            try:
                result = self._process_single_video(video_path)
                self._record_success(video_path, result, self._result_path_for(video_path))
                return result
            except ValueError as exc:
                # Non-retriable (e.g. file too short)
                self._record_failure(video_path, exc)
                raise
            except Exception as exc:
                attempts += 1
                if attempts > self.max_retries:
                    self._record_failure(video_path, exc)
                    raise
                delay = min(60, 5 * (2 ** (attempts - 1)))
                print(
                    f"[RETRY] {video_path.name} failed ({exc}). Sleeping {delay}s before retry {attempts}/{self.max_retries}",
                    file=sys.stderr,
                )
                time.sleep(delay)
        raise RuntimeError(f"Processing failed for {video_path}")

    def run(self, *, quiet: bool = False) -> list[dict[str, Any]]:
        pending = self.get_pending_files()
        if not pending:
            print("[OK] Nothing pending. Already processed all supported videos.")
            return []

        schedule = estimate_processing_schedule(
            pending,
            tpm_limit=self.rate_limiter.tpm_limit,
            safety_margin=self.rate_limiter.safety_margin,
        )
        print(f"[INFO] Found {len(pending)} videos to analyze.")
        print(
            f"[INFO] Avg duration: {schedule['average_duration_seconds']:.1f}s | "
            f"estimated tokens/video: {schedule['estimated_tokens_per_video']} | "
            f"effective TPM budget: {schedule['effective_tpm_budget']}"
        )
        if schedule["target_interval_seconds"]:
            print(f"[INFO] Recommended pacing: ~{schedule['target_interval_seconds']:.1f}s between model calls.")

        results: list[dict[str, Any]] = []
        failed: list[Path] = []

        for index, video_path in enumerate(pending, start=1):
            start_time = time.monotonic()
            if not quiet:
                print(f"[RUN] ({index}/{len(pending)}) {video_path}")
            self.rate_limiter.wait(estimate_video_tokens(probe_video_duration(video_path)))
            try:
                result = self.process_file(video_path)
            except Exception as exc:
                print(
                    f"[SKIP] ({index}/{len(pending)}) {video_path.name} — permanently failed: {exc}",
                    file=sys.stderr,
                )
                failed.append(video_path)
                continue
            results.append(result)
            if not quiet:
                print(f"[DONE] ({index}/{len(pending)}) {video_path.name} in {time.monotonic() - start_time:.1f}s")

        if failed:
            print(f"\n[WARN] {len(failed)} file(s) permanently failed and were skipped:", file=sys.stderr)
            for f in failed:
                print(f"  - {f}", file=sys.stderr)

        return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Recursively analyze all videos under a folder with a Gemini model and save structured descriptions."
    )
    parser.add_argument(
        "--root",
        type=str,
        default=str(Path(__file__).resolve().parents[2] / "assets" / "boss_clips"),
        help="Folder to scan recursively for videos.",
    )
    parser.add_argument("--output-dir", type=str, default=None)
    parser.add_argument("--manifest-path", type=str, default=None)
    parser.add_argument("--model", type=str, default=os.getenv("GOOGLE_MODEL", DEFAULT_MODEL))
    parser.add_argument("--prompt", type=str, default=DEFAULT_PROMPT)
    parser.add_argument("--prompt-file", type=str, default=None)
    parser.add_argument("--tpm-limit", type=int, default=65000)
    parser.add_argument("--rpm-limit", type=int, default=DEFAULT_RPM_LIMIT)
    parser.add_argument("--safety-margin", type=float, default=0.8)
    parser.add_argument("--max-retries", type=int, default=4)
    parser.add_argument("--reset", action="store_true", help="Discard manifest and reprocess every file.")
    return parser.parse_args()


def main() -> int:
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
        print(f"[INFO] Manifest reset: {manifest_path}")

    rate_limiter = RateLimiter(
        tpm_limit=args.tpm_limit, rpm_limit=args.rpm_limit, safety_margin=args.safety_margin
    )
    analyzer = BatchAnalyzer(
        root,
        output_dir=args.output_dir,
        manifest_path=manifest_path,
        api_key=_api_key(),
        model=args.model,
        prompt=prompt,
        max_retries=args.max_retries,
        rate_limiter=rate_limiter,
    )

    try:
        analyzer.run()
        return 0
    except Exception as exc:  # pragma: no cover
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
