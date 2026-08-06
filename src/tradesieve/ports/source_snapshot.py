"""Ports for finite parsing and immutable source-snapshot persistence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from tradesieve.domain.source_registry import SourceRuntimeObservation
from tradesieve.domain.source_snapshot import (
    MAX_RECORDS_PER_SNAPSHOT,
    ArtifactWriteOutcome,
    LifecycleWriteOutcome,
    ParsedRecordInput,
    ParsedSourceSnapshot,
    RawObjectMetadata,
    RawObjectRef,
    SnapshotCommandAuditRecord,
    SourceSnapshotLifecycle,
    SourceSnapshotLifecycleEvent,
)


class FiniteParserId(StrEnum):
    """Closed Phase 1 parser inventory; callers cannot select executable code."""

    SYNTHETIC_JSON_V1 = "synthetic-json-v1"


@dataclass(frozen=True, slots=True)
class FiniteParserOutput:
    schema_id: str
    declared_record_count: int
    records: tuple[ParsedRecordInput, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.schema_id, str)
            or not self.schema_id
            or len(self.schema_id) > 128
        ):
            raise ValueError("schema_id must be bounded")
        if (
            isinstance(self.declared_record_count, bool)
            or not isinstance(self.declared_record_count, int)
            or not 0 <= self.declared_record_count <= MAX_RECORDS_PER_SNAPSHOT
        ):
            raise ValueError("declared_record_count is outside the record bound")
        if not isinstance(self.records, tuple) or any(
            not isinstance(record, ParsedRecordInput) for record in self.records
        ):
            raise ValueError("records must be typed immutable parser output")
        if len(self.records) > MAX_RECORDS_PER_SNAPSHOT:
            raise ValueError("parser output exceeds the record-count bound")


class FiniteSourceParser(Protocol):
    """One configured built-in parser, never a source-selected plugin."""

    parser_id: FiniteParserId
    parser_version: str
    media_type: str
    charset: str

    def parse(self, content: bytes) -> FiniteParserOutput: ...


class ImmutableRawObjectStore(Protocol):
    """Content-verified immutable byte store; deliberately has no delete/export API."""

    def put_exact(
        self, metadata: RawObjectMetadata, content: bytes
    ) -> ArtifactWriteOutcome: ...

    def get_verified(self, reference: RawObjectRef) -> bytes: ...


class SourceSnapshotPersistenceError(Exception):
    def __init__(self) -> None:
        super().__init__("source snapshot persistence unavailable")


class SourceSnapshotRepository(Protocol):
    """Repository/UoW whose named methods define database transaction boundaries."""

    def save_retrieval_atomic(
        self,
        metadata: RawObjectMetadata,
        retrieved_event: SourceSnapshotLifecycleEvent,
        applied_audit: SnapshotCommandAuditRecord,
        idempotent_audit: SnapshotCommandAuditRecord,
    ) -> ArtifactWriteOutcome: ...

    def save_parsed_snapshot_atomic(
        self,
        snapshot: ParsedSourceSnapshot,
        parsed_event: SourceSnapshotLifecycleEvent,
        applied_audit: SnapshotCommandAuditRecord,
        idempotent_audit: SnapshotCommandAuditRecord,
    ) -> ArtifactWriteOutcome: ...

    def append_lifecycle_atomic(
        self,
        event: SourceSnapshotLifecycleEvent,
        applied_audit: SnapshotCommandAuditRecord,
        idempotent_audit: SnapshotCommandAuditRecord,
    ) -> LifecycleWriteOutcome: ...

    def activate_or_rollback_atomic(
        self,
        event: SourceSnapshotLifecycleEvent,
        observation: SourceRuntimeObservation,
        applied_audit: SnapshotCommandAuditRecord,
        idempotent_audit: SnapshotCommandAuditRecord,
    ) -> LifecycleWriteOutcome: ...

    def append_command_audit(self, audit: SnapshotCommandAuditRecord) -> None: ...

    def get_raw_metadata(
        self, deployment_id: str, source_id: str, object_id: str
    ) -> RawObjectMetadata | None: ...

    def get_snapshot(
        self, deployment_id: str, source_id: str, snapshot_id: str
    ) -> ParsedSourceSnapshot | None: ...

    def list_snapshots(
        self, deployment_id: str, source_id: str, *, limit: int
    ) -> tuple[ParsedSourceSnapshot, ...]: ...

    def get_lifecycle_snapshot(
        self, deployment_id: str, source_id: str
    ) -> tuple[tuple[SourceSnapshotLifecycleEvent, ...], SourceSnapshotLifecycle]: ...

    def list_lifecycle_events(
        self, deployment_id: str, source_id: str, *, limit: int
    ) -> tuple[SourceSnapshotLifecycleEvent, ...]: ...

    def list_command_audits(
        self, deployment_id: str, source_id: str, *, limit: int
    ) -> tuple[SnapshotCommandAuditRecord, ...]: ...

    def get_runtime_observation(
        self, deployment_id: str, source_id: str
    ) -> SourceRuntimeObservation | None: ...
