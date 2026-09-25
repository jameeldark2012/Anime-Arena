from __future__ import annotations

from .base import LLMClient
from .gemini import GeminiClient
from .ollama import OllamaClient
from .openai import OpenAIClient

_PROVIDERS = {"gemini", "openai", "ollama", "openrouter"}


def get_client(provider: str, **kwargs: object) -> LLMClient:
    """
    Return an LLMClient for the given provider name.

    Usage examples:
        get_client("gemini", api_key="...", model="gemini-3.5-flash-lite")
        get_client("openai", api_key="...", model="gpt-4o")
        get_client("ollama", model="llama3")
        get_client("openrouter", api_key="...", model="mistralai/mistral-7b-instruct")

    All extra kwargs are forwarded to the provider's constructor.
    """
    key = provider.strip().lower()

    if key == "gemini":
        return GeminiClient(**kwargs)  # type: ignore[arg-type]

    if key == "openai":
        return OpenAIClient(**kwargs)  # type: ignore[arg-type]

    if key in {"ollama", "openrouter"}:
        if key == "openrouter":
            kwargs.setdefault("base_url", "https://openrouter.ai/api/v1")
        return OllamaClient(**kwargs)  # type: ignore[arg-type]

    raise ValueError(
        f"Unknown provider '{provider}'. Available: {', '.join(sorted(_PROVIDERS))}"
    )
