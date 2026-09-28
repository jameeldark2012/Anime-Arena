from __future__ import annotations

import litellm

from .base import LLMClient, LLMResponse, Message

DEFAULT_MODEL = "gpt-4o"


class OpenAIClient(LLMClient):
    """OpenAI client backed by LiteLLM."""

    def __init__(self, *, api_key: str, model: str = DEFAULT_MODEL) -> None:
        self.api_key = api_key
        self.model = model

    def supports_media(self) -> bool:
        return True  # GPT-4o supports image inputs via base64 url

    def generate(self, message: Message) -> LLMResponse:
        if message.media:
            raise NotImplementedError(
                "OpenAI media (image) support not yet wired up. "
                "Encode images as base64 data URLs and pass them in message.text for now."
            )

        response = litellm.completion(
            model=self.model,
            messages=[{"role": "user", "content": message.text}],
            api_key=self.api_key,
        )

        text = response.choices[0].message.content or ""
        return LLMResponse(text=text.strip(), raw=response.model_dump())
