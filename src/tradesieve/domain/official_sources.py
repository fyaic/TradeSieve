"""Atomic identity for the complete official source set used by one screening."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from tradesieve.domain.eu_dual_use import EuDualUseControlList
from tradesieve.domain.eu_fsf import EuFsfSnapshot
from tradesieve.domain.ofac_sls import OfacSlsListKind, OfacSnapshot


class OfficialSourceWriteOutcome(StrEnum):
    APPLIED = "APPLIED"
    IDEMPOTENT = "IDEMPOTENT"


def _bundle_content_hash(
    fsf_snapshot: EuFsfSnapshot,
    dual_use_control_list: EuDualUseControlList,
    ofac_sdn_snapshot: OfacSnapshot,
    ofac_consolidated_snapshot: OfacSnapshot,
) -> str:
    encoded = json.dumps(
        {
            "dual_use_snapshot_content_hash": dual_use_control_list.content_hash,
            "dual_use_snapshot_id": dual_use_control_list.snapshot_id,
            "fsf_snapshot_content_hash": fsf_snapshot.content_hash,
            "fsf_snapshot_id": fsf_snapshot.snapshot_id,
            "ofac_consolidated_snapshot_content_hash": (
                ofac_consolidated_snapshot.content_hash
            ),
            "ofac_consolidated_snapshot_id": ofac_consolidated_snapshot.snapshot_id,
            "ofac_sdn_snapshot_content_hash": ofac_sdn_snapshot.content_hash,
            "ofac_sdn_snapshot_id": ofac_sdn_snapshot.snapshot_id,
        },
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def official_source_bundle_id(
    fsf_snapshot: EuFsfSnapshot,
    dual_use_control_list: EuDualUseControlList,
    ofac_sdn_snapshot: OfacSnapshot,
    ofac_consolidated_snapshot: OfacSnapshot,
) -> str:
    if (
        not isinstance(fsf_snapshot, EuFsfSnapshot)
        or not isinstance(dual_use_control_list, EuDualUseControlList)
        or not isinstance(ofac_sdn_snapshot, OfacSnapshot)
        or not isinstance(ofac_consolidated_snapshot, OfacSnapshot)
    ):
        raise ValueError("official source bundle members must be typed")
    content_hash = _bundle_content_hash(
        fsf_snapshot,
        dual_use_control_list,
        ofac_sdn_snapshot,
        ofac_consolidated_snapshot,
    )
    return f"official-bundle-{content_hash[7:]}"


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


def _require_raw(
    content: bytes,
    expected_length: int,
    expected_hash: str,
    field: str,
) -> None:
    if not isinstance(content, bytes) or not content:
        raise ValueError(f"{field} must be non-empty bytes")
    if (
        len(content) != expected_length
        or f"sha256:{hashlib.sha256(content).hexdigest()}" != expected_hash
    ):
        raise ValueError(f"{field} does not match its projection")


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
    ofac_sdn_raw_content: bytes
    ofac_sdn_content_type: str
    ofac_sdn_snapshot: OfacSnapshot
    ofac_consolidated_raw_content: bytes
    ofac_consolidated_content_type: str
    ofac_consolidated_snapshot: OfacSnapshot
    activated_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.fsf_snapshot, EuFsfSnapshot):
            raise ValueError("FSF snapshot must be typed")
        if not isinstance(self.dual_use_control_list, EuDualUseControlList):
            raise ValueError("dual-use control list must be typed")
        if not isinstance(self.ofac_sdn_snapshot, OfacSnapshot) or not isinstance(
            self.ofac_consolidated_snapshot, OfacSnapshot
        ):
            raise ValueError("OFAC snapshots must be typed")
        if self.ofac_sdn_snapshot.list_kind is not OfacSlsListKind.SDN or (
            self.ofac_consolidated_snapshot.list_kind
            is not OfacSlsListKind.CONSOLIDATED
        ):
            raise ValueError("OFAC snapshots must bind the required list kinds")
        _require_aware(self.fsf_retrieved_at, "FSF retrieval time")
        _require_aware(self.activated_at, "bundle activation time")
        _require_content_type(self.fsf_content_type, "FSF content type")
        _require_content_type(self.dual_use_content_type, "dual-use content type")
        _require_content_type(self.ofac_sdn_content_type, "OFAC SDN content type")
        _require_content_type(
            self.ofac_consolidated_content_type, "OFAC consolidated content type"
        )
        _require_raw(
            self.fsf_raw_content,
            self.fsf_snapshot.raw_byte_length,
            self.fsf_snapshot.raw_content_hash,
            "FSF raw content",
        )
        _require_raw(
            self.dual_use_raw_content,
            self.dual_use_control_list.source_archive_bytes,
            self.dual_use_control_list.source_archive_hash,
            "dual-use raw content",
        )
        _require_raw(
            self.ofac_sdn_raw_content,
            self.ofac_sdn_snapshot.raw_byte_length,
            self.ofac_sdn_snapshot.raw_content_hash,
            "OFAC SDN raw content",
        )
        _require_raw(
            self.ofac_consolidated_raw_content,
            self.ofac_consolidated_snapshot.raw_byte_length,
            self.ofac_consolidated_snapshot.raw_content_hash,
            "OFAC consolidated raw content",
        )
        if any(
            retrieved_at > self.activated_at
            for retrieved_at in (
                self.fsf_retrieved_at,
                self.dual_use_control_list.retrieved_at,
                self.ofac_sdn_snapshot.retrieved_at,
                self.ofac_consolidated_snapshot.retrieved_at,
            )
        ):
            raise ValueError("bundle activation precedes source retrieval")

    @property
    def content_hash(self) -> str:
        return _bundle_content_hash(
            self.fsf_snapshot,
            self.dual_use_control_list,
            self.ofac_sdn_snapshot,
            self.ofac_consolidated_snapshot,
        )

    @property
    def bundle_id(self) -> str:
        return official_source_bundle_id(
            self.fsf_snapshot,
            self.dual_use_control_list,
            self.ofac_sdn_snapshot,
            self.ofac_consolidated_snapshot,
        )


@dataclass(frozen=True, slots=True)
class ActiveOfficialSources:
    """Verified active projections loaded without returning private raw bytes."""

    bundle_id: str
    bundle_content_hash: str
    fsf_retrieved_at: datetime
    fsf_snapshot: EuFsfSnapshot
    dual_use_control_list: EuDualUseControlList
    ofac_sdn_snapshot: OfacSnapshot
    ofac_consolidated_snapshot: OfacSnapshot
    activated_at: datetime

    def __post_init__(self) -> None:
        if (
            not isinstance(self.fsf_snapshot, EuFsfSnapshot)
            or not isinstance(self.dual_use_control_list, EuDualUseControlList)
            or not isinstance(self.ofac_sdn_snapshot, OfacSnapshot)
            or not isinstance(self.ofac_consolidated_snapshot, OfacSnapshot)
        ):
            raise ValueError("active official projections must be typed")
        if self.ofac_sdn_snapshot.list_kind is not OfacSlsListKind.SDN or (
            self.ofac_consolidated_snapshot.list_kind
            is not OfacSlsListKind.CONSOLIDATED
        ):
            raise ValueError("active OFAC projections have invalid list kinds")
        _require_aware(self.fsf_retrieved_at, "FSF retrieval time")
        _require_aware(self.activated_at, "bundle activation time")
        expected_hash = _bundle_content_hash(
            self.fsf_snapshot,
            self.dual_use_control_list,
            self.ofac_sdn_snapshot,
            self.ofac_consolidated_snapshot,
        )
        if self.bundle_content_hash != expected_hash:
            raise ValueError("active official bundle hash is invalid")
        if self.bundle_id != official_source_bundle_id(
            self.fsf_snapshot,
            self.dual_use_control_list,
            self.ofac_sdn_snapshot,
            self.ofac_consolidated_snapshot,
        ):
            raise ValueError("active official bundle identity is invalid")
        if any(
            retrieved_at > self.activated_at
            for retrieved_at in (
                self.fsf_retrieved_at,
                self.dual_use_control_list.retrieved_at,
                self.ofac_sdn_snapshot.retrieved_at,
                self.ofac_consolidated_snapshot.retrieved_at,
            )
        ):
            raise ValueError("active official bundle precedes retrieval")


__all__ = [
    "ActiveOfficialSources",
    "OfficialSourceBundle",
    "OfficialSourceWriteOutcome",
    "official_source_bundle_id",
]
