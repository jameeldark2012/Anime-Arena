from __future__ import annotations

import base64
import json
import litellm
import mimetypes
import os
import urllib.error
import urllib.request

from .base import LLMClient, LLMResponse, Message

OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "llama3"


class OllamaClient(LLMClient):
    """
    Ollama / OpenRouter client backed by LiteLLM.

    Both expose an OpenAI-compatible endpoint, so the same implementation covers both.
    Pass base_url to switch:

        OllamaClient(model="llama3")  # local Ollama
        OllamaClient(base_url="https://openrouter.ai/api/v1", api_key="...", model="mistralai/mistral-7b-instruct")
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        base_url: str = OLLAMA_DEFAULT_BASE_URL,
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

        # LiteLLM uses "ollama/" prefix for local Ollama models
        if not model.startswith("ollama/") and base_url == OLLAMA_DEFAULT_BASE_URL:
            self.model = f"ollama/{model}"

    def supports_media(self) -> bool:
        return self.base_url == OLLAMA_DEFAULT_BASE_URL

    def generate(self, message: Message) -> LLMResponse:
        # Always use Ollama's native API for format: "json" support
        if self.base_url == OLLAMA_DEFAULT_BASE_URL:
            return self._generate_native_ollama(message)

        # Use LiteLLM for OpenRouter or other compatible endpoints
        if message.media:
            raise NotImplementedError("OpenRouter media input is not supported by this client.")

        kwargs: dict = {
            "model": self.model,
            "messages": [{"role": "user", "content": message.text}],
            "api_base": self.base_url,
        }
        if self.api_key:
            kwargs["api_key"] = self.api_key

        response = litellm.completion(**kwargs)
        text = response.choices[0].message.content or ""
        return LLMResponse(text=text.strip(), raw=response.model_dump())

    def _generate_native_ollama(self, message: Message) -> LLMResponse:
        """Send request through Ollama's native API with format: "json" support."""
        user_message = {"role": "user", "content": message.text}
        if message.media:
            user_message["images"] = [
                base64.b64encode(media.path.read_bytes()).decode("ascii")
                for media in message.media
            ]

        think_enabled = os.environ.get("OLLAMA_THINK", "false").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        payload = {
            "model": self.model.removeprefix("ollama/"),
            "stream": False,
            "think": think_enabled,
            "format": "json",
            "messages": [user_message],
            "options": {
                "num_ctx": int(os.environ.get("OLLAMA_CONTEXT_SIZE", "32768")),
                "num_predict": int(os.environ.get("OLLAMA_MAX_OUTPUT_TOKENS", "8192")),
                "temperature": 0.1,
            },
        }

        request = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Ollama returned HTTP {exc.code}: {details}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Could not reach Ollama at {self.base_url}.") from exc

        response_message = raw.get("message") or {}
        text = str(response_message.get("content") or "").strip()
        if not text:
            thinking = str(response_message.get("thinking") or "")
            raise RuntimeError(
                "Ollama returned no final message.content"
                f" (thinking_chars={len(thinking)},"
                f" done_reason={raw.get('done_reason')},"
                f" eval_count={raw.get('eval_count')})."
                " Increase OLLAMA_MAX_OUTPUT_TOKENS or disable OLLAMA_THINK if the response was truncated."
            )
        return LLMResponse(text=text, raw=raw)
