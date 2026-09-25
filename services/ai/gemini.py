from __future__ import annotations

import mimetypes
import os
from pathlib import Path

import litellm

from .base import LLMClient, LLMResponse, MediaFile, Message

DEFAULT_MODEL = "gemini-3.5-flash-lite"
_GEMINI_PREFIX = "gemini/"


def normalize_model_name(model: str | None) -> str:
    """Ensure the model name has the gemini/ prefix LiteLLM requires for API-key auth."""
    cleaned = (model or DEFAULT_MODEL).strip()
    lowered = cleaned.lower()
    if "live" in lowered or "extended-thinking" in lowered:
        raise ValueError(
            "The live/extended-thinking model names use a different API path. "
            "Use a standard Gemini model like 'gemini-2.0-flash' or 'gemini-1.5-flash'."
        )
    if not lowered.startswith(_GEMINI_PREFIX):
        cleaned = f"{_GEMINI_PREFIX}{cleaned}"
    return cleaned


def _detect_mime(path: Path, override: str | None) -> str:
    if override:
        return override
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


class GeminiClient(LLMClient):
    """
    Google Gemini client backed by LiteLLM.
    Uses the Files API for large media (videos) and passes the file_id in the message.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        temperature: float = 0.2,
        max_output_tokens: int = 2048,
    ) -> None:
        self.api_key = api_key
        self.model = normalize_model_name(model)
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        # LiteLLM reads GEMINI_API_KEY from env — set it from our config
        os.environ["GEMINI_API_KEY"] = api_key

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def supports_media(self) -> bool:
        return True

    def generate(self, message: Message) -> LLMResponse:
        content: list[dict] = []

        # Upload each media file via the Files API and reference by file_id
        for media_file in message.media:
            file_id, mime_type = self._upload_file(media_file)
            content.append({
                "type": "file",
                "file": {
                    "file_id": file_id,
                    "format": mime_type,
                },
            })

        content.append({"type": "text", "text": message.text})

        response = litellm.completion(
            model=self.model,
            messages=[{"role": "user", "content": content}],
            temperature=self.temperature,
            max_tokens=self.max_output_tokens,
            api_key=self.api_key,
        )

        text = response.choices[0].message.content or ""
        return LLMResponse(text=text.strip(), raw=response.model_dump())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _upload_file(self, media_file: MediaFile) -> tuple[str, str]:
        """Upload a file via LiteLLM's Files API wrapper. Returns (file_id, mime_type)."""
        path = media_file.path
        if not path.exists():
            raise FileNotFoundError(f"Media file not found: {path}")

        mime_type = _detect_mime(path, media_file.mime_type)

        with path.open("rb") as fh:
            uploaded = litellm.create_file(
                file=fh,
                purpose="user_data",
                extra_headers={"custom-llm-provider": "gemini"},
                api_key=self.api_key,
            )

        file_id = uploaded.id
        if not file_id:
            raise RuntimeError(f"LiteLLM file upload returned no ID: {uploaded}")

        return file_id, mime_type
