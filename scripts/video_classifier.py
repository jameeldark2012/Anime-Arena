#!/usr/bin/env python3
"""Video Classification Script for Anime Arena.

This script classifies clips by their character transformation form, using a
character profile JSON in the source directory. It can use Gemini 3.1 when an
API key is available and falls back to the local Qwen model by sampling frames
from each video when Gemini or the Ollama endpoint is unavailable.

Usage:
    python scripts/video_classifier.py /path/to/videos [--character Clare] [--output-dir /path/to/output]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

# Add project root to path to import internal modules
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
load_dotenv(project_root / "config.env")

from services.ai.llm.base import MediaFile, Message
from services.ai.llm.factory import get_client
from services.ai.llm.rate_limiter import RateLimiter, estimate_text_tokens
from services.media.media_service import sample_video_frames

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)
CLASSIFICATION_PROMPT_VERSION = 4

DEFAULT_CHARACTER_FORMS: Dict[str, Dict[str, str]] = {
    "clare": {
        "Normal Form": (
            "Natural human legs and face, with no prominent metal-like appendages projecting from the back. "
            "Eye color alone does not indicate a transformation."
        ),
        "Half-Awakened": (
            "The legs are visibly transformed, with a bent, hock-jointed shape; this is the primary cue. "
            "Her face is subtly altered. Glowing or slit-pupil eyes can support this reading, but eyes alone "
            "are not enough. The large metal-like appendages on her back seen in the full form are absent."
        ),
        "Fully Transformed": (
            "Prominent metal-like appendages, plates, or blade-like structures project from her back. Their "
            "presence is the clearest cue for this form; do not infer it from glowing eyes or a dark aura alone."
        ),
    }
}


@dataclass
class ClassificationResult:
    """Result of a single video classification."""

    video_path: str
    character_name: str
    form: str
    confidence: float
    reasoning: str
    error: Optional[str] = None


@dataclass
class CharacterForms:
    """Definition of character forms and descriptions."""

    name: str
    forms: Dict[str, str]

    @classmethod
    def default_for_character(cls, character: str) -> "CharacterForms":
        normalized = character.strip().lower()
        if normalized not in DEFAULT_CHARACTER_FORMS:
            raise ValueError(
                f"Character '{character}' is not configured. Add its forms to the default profile or provide a character_profile.json file."
            )
        return cls(name=character, forms=DEFAULT_CHARACTER_FORMS[normalized].copy())

    @classmethod
    def from_profile_data(cls, data: Dict[str, Any]) -> "CharacterForms":
        if not isinstance(data, dict):
            raise ValueError("Character profile JSON must be an object.")

        name = str(data.get("character") or data.get("name") or "Unknown Character")
        raw_forms = data.get("forms") or data.get("transformations") or {}
        normalized: Dict[str, str] = {}

        if isinstance(raw_forms, list):
            for entry in raw_forms:
                if not isinstance(entry, dict):
                    continue
                label = str(entry.get("name") or entry.get("form") or entry.get("label") or "Unnamed Form")
                description = str(entry.get("description") or entry.get("prompt") or "")
                normalized[label] = description
        elif isinstance(raw_forms, dict):
            for key, value in raw_forms.items():
                if isinstance(value, dict):
                    normalized[str(key)] = str(value.get("description") or value.get("prompt") or "")
                else:
                    normalized[str(key)] = str(value)

        if not normalized:
            raise ValueError("The character profile JSON does not define any forms.")

        return cls(name=name, forms=normalized)

    @classmethod
    def from_profile_file(cls, profile_path: str | Path) -> "CharacterForms":
        path = Path(profile_path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"Character profile not found: {path}")

        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_profile_data(payload)

    def write_profile_file(self, target_path: str | Path) -> Path:
        path = Path(target_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "character": self.name,
            "forms": self.forms,
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def as_prompt_text(self) -> str:
        return "\n".join(f"- {form_name}: {description}" for form_name, description in self.forms.items())


class VideoClassifier:
    """Classify videos into character forms using AI models."""

    def __init__(
        self,
        character: str = "Clare",
        gemini_api_key: Optional[str] = None,
        fallback_to_local: bool = True,
        use_gemini_3_1: bool = True,
        source_dir: Optional[str | Path] = None,
        profile_path: Optional[str | Path] = None,
        min_frames: int = 12,
        max_frames: int = 60,
        fps: float = 10.0,
    ):
        self.character = character
        self.fallback_to_local = fallback_to_local
        self.use_gemini_3_1 = use_gemini_3_1
        self.source_dir = Path(source_dir).expanduser().resolve() if source_dir else None
        self.min_frames = max(1, int(min_frames))
        self.max_frames = max(self.min_frames, int(max_frames))
        self.fps = float(fps)
        self.rate_limiter = RateLimiter(rpm_limit=15, safety_margin=0.8)
        logger.info(
            "Gemini request rate limit: %d requests per minute.",
            self.rate_limiter.effective_rpm,
        )

        resolved_profile = self._resolve_profile_path(profile_path)
        self.profile_path = resolved_profile
        self.forms = self._load_forms(resolved_profile)

        self.gemini_client = None
        self.local_client = None
        self.fallback_reason: Optional[str] = None

        if gemini_api_key:
            try:
                model_name = "gemini-3.1-flash-lite"
                self.gemini_client = get_client(
                    "gemini",
                    api_key=gemini_api_key,
                    model=model_name,
                    temperature=0.1,
                    max_output_tokens=1024,
                    rate_limiter=self.rate_limiter,
                )
                logger.info("Initialized Gemini client with model: %s", model_name)
            except Exception as exc:
                self.fallback_reason = f"Gemini initialization failed: {exc}"
                logger.warning("Failed to initialize Gemini client: %s", exc)
                self.gemini_client = None
        else:
            self.fallback_reason = "No GEMINI_API_KEY was provided."

        if fallback_to_local and self.gemini_client is None:
            logger.warning("Local fallback triggered because: %s", self.fallback_reason or "Gemini client unavailable.")
            for candidate in [
                "fredrezones55/Qwen3.5-APEX:latest",
                "qwen3.5:9b",
                "qwen2.5:7b",
            ]:
                try:
                    self.local_client = get_client("ollama", model=candidate)
                    logger.info("Initialized local client with %s", candidate)
                    break
                except Exception as exc:
                    logger.warning("Failed to initialize local client %s: %s", candidate, exc)
            if self.local_client is None:
                logger.warning("No local Qwen fallback client was available.")

    def _resolve_profile_path(self, profile_path: Optional[str | Path]) -> Optional[Path]:
        if profile_path is not None:
            return Path(profile_path).expanduser().resolve()

        if self.source_dir is not None:
            candidate = self.source_dir / "character_profile.json"
            if candidate.exists():
                return candidate
            default_profile = self.source_dir / "character-profile.json"
            if default_profile.exists():
                return default_profile
            CharacterForms.default_for_character(self.character).write_profile_file(candidate)
            return candidate

        return None

    def _load_forms(self, profile_path: Optional[Path]) -> CharacterForms:
        if profile_path is not None and profile_path.exists():
            return CharacterForms.from_profile_file(profile_path)
        return CharacterForms.default_for_character(self.character)

    def build_classification_prompt(self, video_info: Optional[str] = None) -> str:
        """Build the prompt for video classification."""
        forms_text = self.forms.as_prompt_text()
        reference_images = self._reference_images()
        reference_text = ""
        if reference_images:
            reference_text = (
                "\nReference stills are attached after the video in this order:\n"
                + "\n".join(
                    f"{index}. {path.name} ({'full transformation' if 'full' in path.stem.lower() else 'half-awakened'})"
                    for index, path in enumerate(reference_images, start=1)
                )
                + "\nCompare the entire video with these reference stills. "
            )

        base_prompt = f"""You are an anime video classifier. Analyze the attached video clip and determine which transformation form the character {self.character} is in.

Available forms for {self.character}:
{forms_text}

IMPORTANT INSTRUCTIONS:
1. Carefully inspect the clip and focus on the visible transformation state.
2. Use the form descriptions as the reference for classification.
3. Output only a valid JSON object with this exact structure:
{{
    "form": "One of the form names exactly as listed above",
    "confidence": 0.95,
    "reasoning": "Brief explanation of what visual features led to this classification"
}}

4. Do not include any extra text outside the JSON.
5. If the clip does not show a distinct transformation, use the closest match from the form list.
"""

        if self.character.strip().lower() == "clare":
            base_prompt += """\nCLARE MEDIA ROLES AND CLASSIFICATION:
There is exactly one target video. Its attachment is explicitly labeled TARGET VIDEO. Classify only what is visibly present in that video.
Every attachment labeled REFERENCE STILL is an example image for comparison only. It is not part of the target video. Never use a feature visible only in a reference still as evidence that the feature appears in the target video.
Inspect the entire target video and choose the most transformed form clearly visible at any point.
Fully Transformed: choose this only when metal-like plates or blade-like appendages projecting from Clare's back are clearly visible in the target video itself.
Half-Awakened: choose this when the target video itself clearly shows the transformed, bent or hock-jointed legs, while the full-form back appendages are not visible.
Normal Form: choose this when neither transformed legs nor full-form back appendages are clearly visible in the target video.
Eye color, lighting, aura, pose, and the reference stills alone are never enough to assign an awakened form. If a feature is uncertain or appears only in a reference, treat it as absent from the target video.
In the reasoning, cite an approximate timestamp from the target video and describe the actual visible evidence there. Do not repeat a form definition as if it were an observation.
"""

        base_prompt += reference_text

        if video_info:
            base_prompt += f"\nVideo context: {video_info}\n"

        return base_prompt

    def parse_model_response(self, response_text: str) -> Dict[str, Any]:
        """Parse the model response and extract valid JSON."""
        try:
            match = re.search(r"\{.*\}", response_text, re.DOTALL)
            json_text = match.group(0) if match else response_text
            result = json.loads(json_text)

            required = ["form", "confidence", "reasoning"]
            if not all(field in result for field in required):
                raise ValueError(f"Missing required fields. Got: {list(result.keys())}")

            if result["form"] not in self.forms.forms:
                raise ValueError(f"Unknown form: {result['form']}; must be one of {list(self.forms.forms.keys())}")

            confidence = float(result["confidence"])
            if not 0.0 <= confidence <= 1.0:
                raise ValueError(f"Confidence must be between 0.0 and 1.0, got: {confidence}")

            return result
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON response: {exc}\nResponse: {response_text}") from exc
        except Exception as exc:
            raise ValueError(f"Failed to parse response: {exc}\nResponse: {response_text}") from exc

    def _prepare_local_media(self, video_path: Path) -> List[MediaFile]:
        frame_paths = sample_video_frames(
            video_path,
            min_frames=self.min_frames,
            max_frames=self.max_frames,
            fps=self.fps,
            max_dimension=448,
        )
        if not frame_paths:
            raise RuntimeError(f"Could not extract sample frames from {video_path}. Local fallback requires valid video frames.")

        for frame in frame_paths:
            if not frame.exists():
                raise RuntimeError(f"Sampled local frame missing: {frame}")

        frame_media = [
            MediaFile(
                path=frame_path,
                mime_type="image/jpeg",
                label=f"TARGET VIDEO FRAME {index}/{len(frame_paths)}; frames are sampled across the full clip.",
            )
            for index, frame_path in enumerate(frame_paths, start=1)
        ]
        reference_media = [
            MediaFile(
                path=path,
                label=(
                    f"REFERENCE STILL ONLY ({'FULL TRANSFORMATION' if 'full' in path.stem.lower() else 'HALF-AWAKENED'}): "
                    f"{path.name}. This is a comparison example, not part of the target video."
                ),
            )
            for path in self._reference_images()
        ]
        return frame_media + reference_media

    def _reference_images(self) -> List[Path]:
        if self.source_dir is None or not self.source_dir.is_dir():
            return []

        image_extensions = {".png", ".jpg", ".jpeg", ".webp"}
        references = [
            path
            for path in self.source_dir.iterdir()
            if path.is_file()
            and path.suffix.lower() in image_extensions
            and ("full" in path.stem.lower() or "half" in path.stem.lower())
        ]
        return sorted(
            references,
            key=lambda path: (0 if "full" in path.stem.lower() else 1, path.name.casefold()),
        )

    def _normalize_path_key(self, candidate: str | Path) -> str:
        path = Path(candidate).expanduser()
        if self.source_dir is not None and path.is_absolute() and path.is_relative_to(self.source_dir):
            return str(path.relative_to(self.source_dir)).replace('\\', '/')
        return str(path).replace('\\', '/')

    def _load_resume_state(self, output_path: Path) -> set[str]:
        output_path = Path(output_path).expanduser().resolve()
        if not output_path.exists():
            return set()

        try:
            payload = json.loads(output_path.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError):
            return set()

        seen: set[str] = set()
        for key in payload.get('categorized_files', {}):
            seen.add(str(key).replace('\\', '/'))
        for item in payload.get('classifications', []):
            if not isinstance(item, dict):
                continue
            video_path = item.get('video_path')
            if video_path:
                seen.add(self._normalize_path_key(video_path))
        return seen

    def _load_existing_results(self, output_path: Path) -> List[ClassificationResult]:
        output_path = Path(output_path).expanduser().resolve()
        if not output_path.exists():
            return []

        try:
            payload = json.loads(output_path.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError):
            return []

        results: List[ClassificationResult] = []
        for item in payload.get('classifications', []):
            if not isinstance(item, dict):
                continue
            try:
                results.append(
                    ClassificationResult(
                        video_path=str(item.get('video_path', '')),
                        character_name=str(item.get('character_name', self.character)),
                        form=str(item.get('form', '')),
                        confidence=float(item.get('confidence', 0.0)),
                        reasoning=str(item.get('reasoning', '')),
                        error=item.get('error'),
                    )
                )
            except (TypeError, ValueError):
                continue
        return results

    async def classify_video(self, video_path: Path, model: str = "gemini") -> ClassificationResult:
        """Classify a single video file."""
        if not video_path.exists():
            return ClassificationResult(
                video_path=str(video_path),
                character_name=self.character,
                form="",
                confidence=0.0,
                reasoning="",
                error=f"File not found: {video_path}",
            )

        relative_parts = video_path.parts[-3:]
        video_context = f"Video location: {'/'.join(relative_parts)}"
        prompt = self.build_classification_prompt(video_context)

        try:
            if model == "gemini" and self.gemini_client is not None:
                client = self.gemini_client
                reference_images = self._reference_images()
                logger.info(
                    "Using Gemini to classify %s with native video and %d reference images.",
                    video_path.name,
                    len(reference_images),
                )
                media = [
                    MediaFile(
                        path=video_path,
                        label=(
                            f"TARGET VIDEO TO CLASSIFY: {video_path.name}. "
                            "All requested visual evidence must come from this video."
                        ),
                    )
                ] + [
                    MediaFile(
                        path=reference_path,
                        label=(
                            f"REFERENCE STILL ONLY ({'FULL TRANSFORMATION' if 'full' in reference_path.stem.lower() else 'HALF-AWAKENED'}): "
                            f"{reference_path.name}. This is a comparison example, not part of the target video."
                        ),
                    )
                    for reference_path in reference_images
                ]
            elif self.local_client is not None:
                client = self.local_client
                logger.info("Using local Qwen fallback to classify: %s", video_path.name)
                media = self._prepare_local_media(video_path)
            else:
                return ClassificationResult(
                    video_path=str(video_path),
                    character_name=self.character,
                    form="",
                    confidence=0.0,
                    reasoning="",
                    error="No available AI client",
                )

            response = client.generate(Message(text=prompt, media=media))
            result_data = self.parse_model_response(response.text)

            return ClassificationResult(
                video_path=str(video_path),
                character_name=self.character,
                form=result_data["form"],
                confidence=result_data["confidence"],
                reasoning=result_data["reasoning"],
            )
        except Exception as exc:
            error_msg = f"Classification failed: {exc}"
            logger.error("Error classifying %s: %s", video_path.name, error_msg)

            if model == "gemini" and self.fallback_to_local and self.local_client is not None:
                logger.warning(
                    "Gemini classification failed for %s; falling back to local Qwen because: %s",
                    video_path.name,
                    self.fallback_reason or "Gemini returned an error.",
                )
                return await self.classify_video(video_path, model="local")

            return ClassificationResult(
                video_path=str(video_path),
                character_name=self.character,
                form="",
                confidence=0.0,
                reasoning="",
                error=error_msg,
            )

    async def classify_directory(self, directory_path: Path, output_path: Optional[Path] = None, resume: bool = True) -> List[ClassificationResult]:
        """Classify all video files in a directory (recursive)."""
        if not directory_path.exists():
            raise FileNotFoundError(f"Directory not found: {directory_path}")

        if self.source_dir is None:
            self.source_dir = directory_path.resolve()
            resolved_profile = self._resolve_profile_path(None)
            self.profile_path = resolved_profile
            self.forms = self._load_forms(resolved_profile)

        if output_path is None:
            output_path = directory_path / "classifications.json"
        output_path = Path(output_path).expanduser().resolve()

        existing_results: List[ClassificationResult] = []
        if resume and output_path.exists():
            try:
                existing_payload = json.loads(output_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                existing_payload = {}
            if existing_payload.get("classification_prompt_version") == CLASSIFICATION_PROMPT_VERSION:
                existing_results = self._load_existing_results(output_path)
                logger.info("Resuming from existing classifications file: %s", output_path)
            else:
                backup_path = output_path.with_name(f"{output_path.stem}.previous{output_path.suffix}")
                suffix = 2
                while backup_path.exists():
                    backup_path = output_path.with_name(
                        f"{output_path.stem}.previous-{suffix}{output_path.suffix}"
                    )
                    suffix += 1
                if output_path.exists():
                    shutil.copy2(output_path, backup_path)
                    logger.warning(
                        "Existing classifications use an older prompt; preserving them at %s and reclassifying.",
                        backup_path,
                    )
        else:
            logger.info("No existing classifications file found at %s; starting a fresh run.", output_path)
        results: List[ClassificationResult] = [result for result in existing_results if not result.error]
        processed_keys = {self._normalize_path_key(result.video_path) for result in results if result.video_path}

        video_extensions = {'.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv', '.wmv', '.m4v'}
        video_files: List[Path] = []
        for ext in sorted(video_extensions):
            video_files.extend(directory_path.rglob(f"*{ext}"))
            video_files.extend(directory_path.rglob(f"*{ext.upper()}"))

        unique_files = sorted({path.resolve() for path in video_files})
        logger.info("Found %d video files in %s", len(unique_files), directory_path)

        total_to_process = 0
        for video_path in unique_files:
            if self._normalize_path_key(video_path) in processed_keys:
                continue
            total_to_process += 1

        logger.info("Skipping %d already-processed files and processing %d remaining files.", len(processed_keys), total_to_process)
        self.save_results(results, output_path, print_summary=False)

        for index, video_path in enumerate(unique_files, start=1):
            key = self._normalize_path_key(video_path)
            if key in processed_keys:
                logger.info("Skipping already processed file [%d/%d]: %s", index, len(unique_files), video_path.name)
                continue

            logger.info("Processing [%d/%d]: %s", index, len(unique_files), video_path.name)
            result = await self.classify_video(video_path)
            results.append(result)
            self.save_results(results, output_path, print_summary=False)

            if result.error:
                logger.warning("  Result: ERROR - %s", result.error)
            else:
                logger.info("  Result: %s (confidence: %.2f)", result.form, result.confidence)

        self.save_results(results, output_path)
        return results

    def save_results(
        self,
        results: List[ClassificationResult],
        output_path: Path,
        print_summary: bool = True,
    ) -> None:
        """Save classification results to JSON files inside the source directory."""
        output_path = Path(output_path).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        categorized_files: Dict[str, str] = {}
        for result in results:
            if result.error:
                continue
            try:
                path_obj = Path(result.video_path)
                if self.source_dir is not None and path_obj.is_absolute() and path_obj.is_relative_to(self.source_dir):
                    relative_name = str(path_obj.relative_to(self.source_dir))
                else:
                    relative_name = path_obj.name
                categorized_files[relative_name] = result.form
            except Exception:
                categorized_files[Path(result.video_path).name] = result.form

        data = {
            "character": self.character,
            "classification_prompt_version": CLASSIFICATION_PROMPT_VERSION,
            "profile_path": str(self.profile_path) if self.profile_path else None,
            "total_videos": len(results),
            "successful_classifications": len([r for r in results if not r.error]),
            "failed_classifications": len([r for r in results if r.error]),
            "categorized_files": categorized_files,
            "classifications": [asdict(r) for r in results],
        }

        temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
        with temporary_path.open('w', encoding='utf-8') as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        temporary_path.replace(output_path)

        logger.info("Saved results to %s", output_path)

        if not print_summary:
            return

        successful = data["successful_classifications"]
        failed = data["failed_classifications"]
        print("\n=== Classification Summary ===")
        print(f"Total videos processed: {len(results)}")
        print(f"Successful: {successful}")
        print(f"Failed: {failed}")

        if successful > 0:
            form_counts: Dict[str, int] = {}
            for result in results:
                if not result.error and result.form:
                    form_counts[result.form] = form_counts.get(result.form, 0) + 1

            print("\nForm distribution:")
            for form, count in sorted(form_counts.items()):
                percentage = (count / successful) * 100
                print(f"  {form}: {count} videos ({percentage:.1f}%)")


async def main() -> None:
    """Main entry point for CLI."""
    parser = argparse.ArgumentParser(description="Classify anime video clips into character forms using AI")
    parser.add_argument("directory", help="Path to the directory containing video files")
    parser.add_argument("--character", "-c", default="Clare", help="Character name (for example Clare)")
    parser.add_argument("--output-dir", "-o", help="Directory to save classification results (defaults to the source directory)")
    parser.add_argument("--gemini-key", "-k", help="Google Gemini API key (or set GEMINI_API_KEY)")
    parser.add_argument("--no-fallback", action="store_true", help="Disable the local fallback model")
    parser.add_argument("--gemini-2", action="store_true", help="Legacy alias; default LiteLLM path is gemini-3.1-flash-lite")
    parser.add_argument("--profile", "-p", help="Path to a JSON profile file with character forms")
    parser.add_argument("--min-frames", type=int, default=12, help="Minimum evenly spaced frames across the full video for local fallback (default: 12)")
    parser.add_argument("--max-frames", type=int, default=60, help="Maximum evenly spaced frames across the full video for local fallback (default: 60)")
    parser.add_argument("--fps", type=float, default=10.0, help="Desired local frame count per second before the max-frame cap is applied (default: 10.0)")
    parser.add_argument("--resume", action="store_true", help="Resume from an existing classifications.json file instead of restarting from the beginning")

    args = parser.parse_args()

    if args.gemini_key:
        api_key = args.gemini_key.strip()
        key_source = "--gemini-key"
    elif os.environ.get("GEMINI_API_KEY", "").strip():
        api_key = os.environ["GEMINI_API_KEY"].strip()
        key_source = "GEMINI_API_KEY"
    elif os.environ.get("GOOGLE_API_KEY", "").strip():
        api_key = os.environ["GOOGLE_API_KEY"].strip()
        key_source = "GOOGLE_API_KEY in config.env/environment"
    else:
        api_key = None
        key_source = None

    if api_key:
        logger.info("Gemini API key loaded from %s; key value is not displayed.", key_source)
    else:
        reason = "No key found in --gemini-key, GEMINI_API_KEY, or GOOGLE_API_KEY (config.env is loaded)."
        if args.no_fallback:
            logger.error("Cannot start Gemini-only run: %s", reason)
            sys.exit(1)
        logger.warning("Gemini unavailable: %s Local fallback may be used.", reason)

    input_dir = Path(args.directory).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else input_dir
    output_path = output_dir / "classifications.json"

    classifier = VideoClassifier(
        character=args.character,
        gemini_api_key=api_key,
        fallback_to_local=not args.no_fallback,
        use_gemini_3_1=not args.gemini_2,
        source_dir=input_dir,
        profile_path=args.profile,
        min_frames=args.min_frames,
        max_frames=args.max_frames,
        fps=args.fps,
    )

    print(f"Starting video classification for {args.character}...")
    print(f"Input directory: {input_dir}")
    print(f"Output file: {output_path}")

    try:
        should_resume = args.resume or output_path.exists()
        results = await classifier.classify_directory(input_dir, output_path, resume=should_resume)
        successful = len([result for result in results if not result.error])
        if successful == 0:
            print("\n❌ No videos were successfully classified.")
            sys.exit(1)
        print(f"\n✅ Successfully classified {successful} videos.")
        sys.exit(0)
    except Exception as exc:
        logger.error("Classification failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
