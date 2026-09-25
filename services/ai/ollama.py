from __future__ import annotations

import litellm

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
        return False

    def generate(self, message: Message) -> LLMResponse:
        if message.media:
            raise NotImplementedError("Ollama/OpenRouter media input is not supported.")

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
