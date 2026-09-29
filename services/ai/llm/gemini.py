from __future__ import annotations

import mimetypes
import logging
import os
from pathlib import Path

import litellm

from .base import LLMClient, LLMResponse, MediaFile, Message
from .rate_limiter import RateLimiter, estimate_text_tokens

DEFAULT_MODEL = "gemini-3.5-flash-lite"
_GEMINI_PREFIX = "gemini/"
logger = logging.getLogger(__name__)


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
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = normalize_model_name(model)
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.rate_limiter = rate_limiter
        self.api_request_count = 0
        self._uploaded_image_cache: dict[tuple[str, int, int], tuple[str, str]] = {}
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
            if media_file.label:
                content.append({"type": "text", "text": media_file.label})
            file_id, mime_type = self._upload_file(media_file)
            content.append({
                "type": "file",
                "file": {
                    "file_id": file_id,
                    "format": mime_type,
                },
            })

        content.append({"type": "text", "text": message.text})

        self._wait_for_api_request(
            "generate classification",
            estimated_tokens=estimate_text_tokens(message.text),
        )

        response = litellm.completion(
            model=self.model,
            custom_llm_provider="gemini",
            messages=[{"role": "user", "content": content}],
            max_tokens=self.max_output_tokens,
            api_key=self.api_key,
            num_retries=0,
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
        cache_key = None
        if mime_type.startswith("image/"):
            stat = path.stat()
            cache_key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
            cached = self._uploaded_image_cache.get(cache_key)
            if cached is not None:
                return cached

        self._wait_for_api_request(f"upload {path.name}")

        with path.open("rb") as fh:
            uploaded = litellm.create_file(
                file=fh,
                purpose="user_data",
                custom_llm_provider="gemini",
                extra_headers={"custom-llm-provider": "gemini"},
                api_key=self.api_key,
                num_retries=0,
            )

        file_id = uploaded.id
        if not file_id:
            raise RuntimeError(f"LiteLLM file upload returned no ID: {uploaded}")

        result = (file_id, mime_type)
        if cache_key is not None:
            self._uploaded_image_cache[cache_key] = result
        return result

    def _wait_for_api_request(self, description: str, estimated_tokens: int = 0) -> None:
        if self.rate_limiter is not None:
            self.rate_limiter.wait(estimated_tokens=estimated_tokens)
        self.api_request_count += 1
        limit = self.rate_limiter.effective_rpm if self.rate_limiter is not None else "unlimited"
        logger.info(
            "Gemini API request #%d (%s; configured limit: %s requests/minute).",
            self.api_request_count,
            description,
            limit,
        )
