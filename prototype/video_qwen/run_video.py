from __future__ import annotations

import argparse
import base64
import json
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_MODEL = "qwen3.5:9b"
DEFAULT_OLLAMA_URL = "http://localhost:11434"


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        details = exc.stderr.strip() or exc.stdout.strip()
        raise RuntimeError(f"Command failed: {' '.join(command)}\n{details}") from exc


def require_binary(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(
            f"{name} was not found on PATH. Install ffmpeg, including ffprobe, and try again."
        )
    return path


def video_duration(video_path: Path) -> float:
    ffprobe = require_binary("ffprobe")
    result = run_command(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ]
    )
    return float(result.stdout.strip())


def extract_frames(
    video_path: Path,
    frames_dir: Path,
    max_frames: int,
    width: int,
) -> list[Path]:
    ffmpeg = require_binary("ffmpeg")
    duration = video_duration(video_path)
    if duration <= 0:
        raise RuntimeError("Could not determine video duration.")

    frames_dir.mkdir(parents=True, exist_ok=True)
    # Sample frame centers so the final request stays inside the video instead
    # of seeking exactly to its end, where ffmpeg may have no decodable frame.
    timestamps = [
        min(duration * (index + 0.5) / max_frames, max(duration - 0.05, 0.0))
        for index in range(max_frames)
    ]
    frame_paths: list[Path] = []

    for index, timestamp in enumerate(timestamps):
        frame_path = frames_dir / f"frame_{index:03d}.jpg"
        run_command(
            [
                ffmpeg,
                "-y",
                "-ss",
                f"{timestamp:.3f}",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-vf",
                f"scale={width}:-2",
                "-q:v",
                "5",
                str(frame_path),
            ]
        )
        frame_paths.append(frame_path)

    return frame_paths


def extract_audio(video_path: Path, audio_path: Path) -> bool:
    ffmpeg = require_binary("ffmpeg")
    try:
        run_command(
            [
                ffmpeg,
                "-y",
                "-i",
                str(video_path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(audio_path),
            ]
        )
    except subprocess.CalledProcessError:
        return False
    return audio_path.exists()


def transcribe_audio(audio_path: Path, model_name: str) -> str:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "faster-whisper is not installed. Run: "
            "pip install -r prototype/video_qwen/requirements.txt"
        ) from exc

    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(audio_path), vad_filter=True)
    return " ".join(segment.text.strip() for segment in segments).strip()


def encode_frames(frame_paths: list[Path]) -> list[str]:
    return [base64.b64encode(path.read_bytes()).decode("ascii") for path in frame_paths]


def ask_ollama(
    ollama_url: str,
    model: str,
    original_prompt: str,
    transcript: str,
    frame_images: list[str],
    context_size: int,
) -> dict:
    prompt = (
        "Use the following original battle prompt as the governing context. "
        "Follow its character identity, combat rules, opponent identity, and requested output behavior.\n\n"
        f"Original prompt:\n{original_prompt}\n\n"
        "Now analyze the attached video frames and speech-to-text transcript as the opponent video. "
        "Take both into consideration before deciding or describing anything.\n\n"
        "Analyze this video using both the sampled video frames and the speech transcript.\n\n"
        f"Speech transcript:\n{transcript or '[No speech detected]'}\n\n"
        "Return concise JSON with these keys: visible_action, spoken_words, tactical_summary, confidence. "
        "Separate what is visibly certain from interpretation. Do not invent details that are not visible or spoken."
    )
    payload = {
        "model": model,
        "stream": False,
        "think": False,
        "format": "json",
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": frame_images,
            }
        ],
        "options": {
            "temperature": 0.1,
            "num_predict": 1024,
            "num_ctx": context_size,
        },
    }
    request = urllib.request.Request(
        f"{ollama_url.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Ollama rejected the request with HTTP {exc.code}: {details}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Could not reach Ollama at {ollama_url}. Start Ollama and pull {model}."
        ) from exc

    return response_data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract frames and speech from a video, then analyze both with Ollama Qwen."
    )
    parser.add_argument("video", type=Path, help="Path to the input video")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model name")
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--whisper-model", default="base", help="faster-whisper model: tiny, base, small, ...")
    parser.add_argument("--frames", type=int, default=8, help="Number of evenly sampled frames")
    parser.add_argument("--width", type=int, default=768, help="Frame width sent to Ollama")
    parser.add_argument("--context-size", type=int, default=32768, help="Ollama context window")
    parser.add_argument("--prompt-file", type=Path, help="Path to the original battle prompt text file")
    parser.add_argument("--no-transcription", action="store_true")
    parser.add_argument("--output", type=Path, help="Output JSON path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    video_path = args.video.expanduser().resolve()
    if not video_path.is_file():
        raise SystemExit(f"Video does not exist: {video_path}")
    if args.frames < 1:
        raise SystemExit("--frames must be at least 1")

    output_path = args.output or video_path.with_suffix(".qwen.json")
    original_prompt = ""
    if args.prompt_file:
        prompt_path = args.prompt_file.expanduser().resolve()
        if not prompt_path.is_file():
            raise SystemExit(f"Prompt file does not exist: {prompt_path}")
        original_prompt = prompt_path.read_text(encoding="utf-8")

    with tempfile.TemporaryDirectory(prefix="video_qwen_") as temp_dir:
        temp_root = Path(temp_dir)
        frame_paths = extract_frames(video_path, temp_root / "frames", args.frames, args.width)
        audio_path = temp_root / "audio.wav"
        transcript = ""
        if not args.no_transcription and extract_audio(video_path, audio_path):
            transcript = transcribe_audio(audio_path, args.whisper_model)

        response = ask_ollama(
            ollama_url=args.ollama_url,
            model=args.model,
            original_prompt=original_prompt,
            transcript=transcript,
            frame_images=encode_frames(frame_paths),
            context_size=args.context_size,
        )

    result = {
        "video": str(video_path),
        "model": args.model,
        "whisper_model": None if args.no_transcription else args.whisper_model,
        "prompt_file": str(args.prompt_file) if args.prompt_file else None,
        "frame_count": len(frame_paths),
        "transcript": transcript,
        "ollama_response": response,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Saved analysis to {output_path}")


if __name__ == "__main__":
    main()
