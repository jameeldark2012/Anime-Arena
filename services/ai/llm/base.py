from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class MediaFile:
    """A local file to be passed alongside a prompt."""
    path: Path
    mime_type: str | None = None  # auto-detected if None


@dataclass
class Message:
    """A single prompt turn sent to the model."""
    text: str
    media: list[MediaFile] = field(default_factory=list)


@dataclass
class LLMResponse:
    """Normalised response from any provider."""
    text: str
    raw: dict[str, Any] = field(default_factory=dict)  # original provider payload


class LLMClient(ABC):
    """Abstract interface every AI provider must implement."""

    @abstractmethod
    def generate(self, message: Message) -> LLMResponse:
        """Send a prompt (with optional media) and return the model's response."""
        ...

    @abstractmethod
    def supports_media(self) -> bool:
        """Whether this client can accept media files alongside a prompt."""
        ...
