"""Ports for the fixed official OFAC Sanctions List Service boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class OfacSlsHttpDocument:
    final_url: str
    content_type: str
    content: bytes
    last_modified: str | None
    digest: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.final_url, str) or not self.final_url:
            raise ValueError("final_url must be non-empty")
        if not isinstance(self.content_type, str) or not self.content_type:
            raise ValueError("content_type must be non-empty")
        if not isinstance(self.content, bytes):
            raise ValueError("content must be bytes")
        for value, field in (
            (self.last_modified, "last_modified"),
            (self.digest, "digest"),
        ):
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{field} must be a non-empty string or absent")


class OfacSlsHttpTransport(Protocol):
    """Bounded GET-only transport; callers cannot provide arbitrary source URLs."""

    def get(self, url: str, *, maximum_bytes: int) -> OfacSlsHttpDocument: ...
