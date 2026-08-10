"""Ports for the fixed official EU FSF source boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class EuFsfHttpDocument:
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


class EuFsfHttpTransport(Protocol):
    """Bounded GET-only transport; callers cannot provide source URLs."""

    def get(self, url: str, *, maximum_bytes: int) -> EuFsfHttpDocument: ...
