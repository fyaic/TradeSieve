"""Transport boundary for the fixed official EU Annex I source."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class EuDualUseHttpDocument:
    final_url: str
    content_type: str
    content: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.final_url, str) or not self.final_url:
            raise ValueError("final_url must be non-empty")
        if not isinstance(self.content_type, str) or not self.content_type:
            raise ValueError("content_type must be non-empty")
        if not isinstance(self.content, bytes):
            raise ValueError("content must be bytes")


class EuDualUseHttpTransport(Protocol):
    def get(self, url: str, *, maximum_bytes: int) -> EuDualUseHttpDocument: ...
