"""Atomic identity for the official source pair used by one screening run."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from tradesieve.domain.eu_dual_use import EuDualUseControlList
from tradesieve.domain.eu_fsf import EuFsfSnapshot


class OfficialSourceWriteOutcome(StrEnum):
    APPLIED = "APPLIED"
    IDEMPOTENT = "IDEMPOTENT"


def _bundle_content_hash(
    fsf_snapshot: EuFsfSnapshot, dual_use_control_list: EuDualUseControlList
) -> str:
    encoded = json.dumps(
        {
            "dual_use_snapshot_content_hash": dual_use_control_list.content_hash,
            "dual_use_snapshot_id": dual_use_control_list.snapshot_id,
            "fsf_snapshot_content_hash": fsf_snapshot.content_hash,
            "fsf_snapshot_id": fsf_snapshot.snapshot_id,
        },
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def official_source_bundle_id(
    fsf_snapshot: EuFsfSnapshot, dual_use_control_list: EuDualUseControlList
) -> str:
    if not isinstance(fsf_snapshot, EuFsfSnapshot) or not isinstance(
        dual_use_control_list, EuDualUseControlList
    ):
        raise ValueError("official source bundle members must be typed")
    return f"official-bundle-{_bundle_content_hash(fsf_snapshot, dual_use_control_list)[7:]}"


def _require_aware(value: datetime, field: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{field} must be timezone-aware")


def _require_content_type(value: str, field: str) -> None:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 128:
        raise ValueError(f"{field} is invalid")


@dataclass(frozen=True, slots=True)
class OfficialSourceBundle:
    """Verified raw bytes and projections to persist and activate atomically."""

    fsf_raw_content: bytes
    fsf_content_type: str
    fsf_retrieved_at: datetime
    fsf_snapshot: EuFsfSnapshot
    dual_use_raw_content: bytes
    dual_use_content_type: str
    dual_use_control_list: EuDualUseControlList
    activated_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.fsf_snapshot, EuFsfSnapshot):
            raise ValueError("FSF snapshot must be typed")
        if not isinstance(self.dual_use_control_list, EuDualUseControlList):
            raise ValueError("dual-use control list must be typed")
        _require_aware(self.fsf_retrieved_at, "FSF retrieval time")
        _require_aware(self.activated_at, "bundle activation time")
        _require_content_type(self.fsf_content_type, "FSF content type")
        _require_content_type(self.dual_use_content_type, "dual-use content type")
        if not isinstance(self.fsf_raw_content, bytes) or not self.fsf_raw_content:
            raise ValueError("FSF raw content must be non-empty bytes")
        if (
            len(self.fsf_raw_content) != self.fsf_snapshot.raw_byte_length
            or f"sha256:{hashlib.sha256(self.fsf_raw_content).hexdigest()}"
            != self.fsf_snapshot.raw_content_hash
        ):
            raise ValueError("FSF raw content does not match its projection")
        if (
            not isinstance(self.dual_use_raw_content, bytes)
            or not self.dual_use_raw_content
        ):
            raise ValueError("dual-use raw content must be non-empty bytes")
        if (
            len(self.dual_use_raw_content)
            != self.dual_use_control_list.source_archive_bytes
            or f"sha256:{hashlib.sha256(self.dual_use_raw_content).hexdigest()}"
            != self.dual_use_control_list.source_archive_hash
        ):
            raise ValueError("dual-use raw content does not match its projection")
        if self.dual_use_control_list.retrieved_at > self.activated_at:
            raise ValueError("bundle activation precedes dual-use retrieval")
        if self.fsf_retrieved_at > self.activated_at:
            raise ValueError("bundle activation precedes FSF retrieval")

    @property
    def content_hash(self) -> str:
        return _bundle_content_hash(self.fsf_snapshot, self.dual_use_control_list)

    @property
    def bundle_id(self) -> str:
        return official_source_bundle_id(self.fsf_snapshot, self.dual_use_control_list)


@dataclass(frozen=True, slots=True)
class ActiveOfficialSources:
    """Verified active projections loaded without returning private raw bytes."""

    bundle_id: str
    bundle_content_hash: str
    fsf_retrieved_at: datetime
    fsf_snapshot: EuFsfSnapshot
    dual_use_control_list: EuDualUseControlList
    activated_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.fsf_snapshot, EuFsfSnapshot) or not isinstance(
            self.dual_use_control_list, EuDualUseControlList
        ):
            raise ValueError("active official projections must be typed")
        _require_aware(self.fsf_retrieved_at, "FSF retrieval time")
        _require_aware(self.activated_at, "bundle activation time")
        expected_hash = _bundle_content_hash(
            self.fsf_snapshot, self.dual_use_control_list
        )
        if self.bundle_content_hash != expected_hash:
            raise ValueError("active official bundle hash is invalid")
        if self.bundle_id != official_source_bundle_id(
            self.fsf_snapshot, self.dual_use_control_list
        ):
            raise ValueError("active official bundle identity is invalid")
        if self.fsf_retrieved_at > self.activated_at or (
            self.dual_use_control_list.retrieved_at > self.activated_at
        ):
            raise ValueError("active official bundle precedes retrieval")


__all__ = [
    "ActiveOfficialSources",
    "OfficialSourceBundle",
    "OfficialSourceWriteOutcome",
    "official_source_bundle_id",
]
