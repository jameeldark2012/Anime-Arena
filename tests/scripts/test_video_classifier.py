from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from scripts.video_classifier import (
    CLASSIFICATION_PROMPT_VERSION,
    CharacterForms,
    ClassificationResult,
    VideoClassifier,
)


def test_character_forms_can_load_from_json(tmp_path: Path) -> None:
    profile_path = tmp_path / "character_profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "character": "Clare",
                "forms": [
                    {"name": "Normal Form", "description": "Base state."},
                    {"name": "Half-Awakened", "description": "Partial transformation."},
                    {"name": "Fully Transformed", "description": "Full transformation."},
                ],
            }
        ),
        encoding="utf-8",
    )

    forms = CharacterForms.from_profile_file(profile_path)

    assert forms.name == "Clare"
    assert list(forms.forms) == ["Normal Form", "Half-Awakened", "Fully Transformed"]
    assert "Base state." in forms.forms["Normal Form"]


def test_classifier_builds_prompt_from_profile_path(tmp_path: Path) -> None:
    profile_path = tmp_path / "character_profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "character": "Clare",
                "forms": {
                    "Normal Form": "Base state.",
                    "Half-Awakened": "Partial transformation.",
                    "Fully Transformed": "Full transformation.",
                },
            }
        ),
        encoding="utf-8",
    )

    classifier = VideoClassifier(
        character="Clare",
        gemini_api_key=None,
        fallback_to_local=False,
        profile_path=profile_path,
    )

    prompt = classifier.build_classification_prompt()
    assert "Normal Form" in prompt
    assert "Half-Awakened" in prompt
    assert "Fully Transformed" in prompt
    assert "character_profile.json" not in prompt


def test_default_profile_is_written_to_source_directory(tmp_path: Path) -> None:
    source_dir = tmp_path / "clare_source"
    source_dir.mkdir()

    classifier = VideoClassifier(
        character="Clare",
        gemini_api_key=None,
        fallback_to_local=False,
        source_dir=source_dir,
    )

    profile_path = source_dir / "character_profile.json"
    assert profile_path.exists()
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    assert profile["character"] == "Clare"
    assert "Normal Form" in profile["forms"]


def test_classifier_supports_larger_frame_budgets() -> None:
    classifier = VideoClassifier(
        character="Clare",
        gemini_api_key=None,
        fallback_to_local=False,
        min_frames=12,
        max_frames=60,
        fps=10.0,
    )

    assert classifier.min_frames == 12
    assert classifier.max_frames == 60
    assert classifier.fps == 10.0


def test_classify_directory_resumes_from_existing_output(tmp_path: Path) -> None:
    source_dir = tmp_path / "clare_source"
    source_dir.mkdir()
    video_dir = source_dir / "clips"
    video_dir.mkdir()
    first = video_dir / "clip1.mp4"
    first.write_bytes(b"fake-video-1")
    second = video_dir / "clip2.mp4"
    second.write_bytes(b"fake-video-2")

    output_path = source_dir / "classifications.json"
    output_path.write_text(
        json.dumps(
            {
                "character": "Clare",
                "categorized_files": {"clips/clip1.mp4": "Normal Form"},
                "classifications": [
                    {
                        "video_path": str(first),
                        "character_name": "Clare",
                        "form": "Normal Form",
                        "confidence": 1.0,
                        "reasoning": "already processed",
                        "error": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    classifier = VideoClassifier(
        character="Clare",
        gemini_api_key=None,
        fallback_to_local=False,
        source_dir=source_dir,
    )

    resume_set = classifier._load_resume_state(output_path)
    first_key = str(first.relative_to(source_dir)).replace('\\', '/')
    second_key = str(second.relative_to(source_dir)).replace('\\', '/')
    assert first_key in resume_set
    assert second_key not in resume_set


def test_gemini_receives_native_video_and_reference_images(tmp_path: Path) -> None:
    source_dir = tmp_path / "clare_source"
    source_dir.mkdir()
    video_path = source_dir / "clip.mp4"
    video_path.write_bytes(b"video")
    reference_names = [
        "Full Transformation form first example.png",
        "Full transformation form 2nd example.png",
        "Half transformation example 1 legs.png",
        "Half transfomation example 2 legs.png",
        "Half transformation face example 3 must have transformed legs.png",
    ]
    for name in reference_names:
        (source_dir / name).write_bytes(b"image")

    class RecordingClient:
        message = None

        def generate(self, message):
            self.message = message
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "form": "Fully Transformed",
                        "confidence": 0.9,
                        "reasoning": "Metal-like appendages are visible on the back.",
                    }
                )
            )

    classifier = VideoClassifier(
        character="Clare",
        fallback_to_local=False,
        source_dir=source_dir,
    )

    client = RecordingClient()
    classifier.gemini_client = client

    result = asyncio.run(classifier.classify_video(video_path))

    media_paths = [media.path for media in client.message.media]
    assert result.form == "Fully Transformed"
    assert media_paths[0] == video_path
    assert set(media_paths[1:]) == {source_dir / name for name in reference_names}
    assert client.message.media[0].label.startswith("TARGET VIDEO TO CLASSIFY:")
    assert all(media.label.startswith("REFERENCE STILL ONLY") for media in client.message.media[1:])
    assert classifier.rate_limiter.effective_rpm == 12
    assert "metal-like appendages" in client.message.text
    assert "never enough to assign an awakened form" in client.message.text
    assert all(name in client.message.text for name in reference_names)


def test_classifier_shares_its_rate_limiter_with_gemini_client(tmp_path: Path) -> None:
    classifier = VideoClassifier(
        character="Clare",
        gemini_api_key="test-key",
        fallback_to_local=False,
        source_dir=tmp_path,
    )

    assert classifier.gemini_client.rate_limiter is classifier.rate_limiter
    assert classifier.rate_limiter.effective_rpm == 12


def test_resume_reclassifies_old_prompt_and_preserves_previous_output(tmp_path: Path) -> None:
    source_dir = tmp_path / "clare_source"
    source_dir.mkdir()
    video_path = source_dir / "clip.mp4"
    video_path.write_bytes(b"video")
    output_path = source_dir / "classifications.json"
    output_path.write_text(
        json.dumps(
            {
                "classifications": [
                    {
                        "video_path": str(video_path),
                        "character_name": "Clare",
                        "form": "Normal Form",
                        "confidence": 0.5,
                        "reasoning": "old prompt",
                        "error": None,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    classifier = VideoClassifier(
        character="Clare",
        fallback_to_local=False,
        source_dir=source_dir,
    )

    async def classify_video(path: Path) -> ClassificationResult:
        return ClassificationResult(
            video_path=str(path),
            character_name="Clare",
            form="Half-Awakened",
            confidence=0.9,
            reasoning="Transformed legs are visible.",
        )

    classifier.classify_video = classify_video
    results = asyncio.run(classifier.classify_directory(source_dir, output_path, resume=True))

    backup_path = source_dir / "classifications.previous.json"
    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert backup_path.exists()
    assert results[0].form == "Half-Awakened"
    assert saved["classification_prompt_version"] == CLASSIFICATION_PROMPT_VERSION
    assert saved["categorized_files"]["clip.mp4"] == "Half-Awakened"
