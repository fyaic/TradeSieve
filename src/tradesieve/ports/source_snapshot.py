"""Ports for finite parsing and immutable source-snapshot persistence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
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


SYNTHETIC_SCHEMA_ID = "tradesieve-synthetic-source-v1"
SYNTHETIC_PARSER_VERSION = "1.0.0"
SYNTHETIC_MEDIA_TYPE = "application/json"
SYNTHETIC_CHARSET = "utf-8"


class FiniteParserErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    CONTENT_TOO_LARGE = "CONTENT_TOO_LARGE"
    BOM_NOT_ALLOWED = "BOM_NOT_ALLOWED"
    INVALID_ENCODING = "INVALID_ENCODING"
    INVALID_JSON = "INVALID_JSON"
    DUPLICATE_KEY = "DUPLICATE_KEY"
    FLOAT_NOT_ALLOWED = "FLOAT_NOT_ALLOWED"
    NON_FINITE_NUMBER = "NON_FINITE_NUMBER"
    UNSAFE_NESTING = "UNSAFE_NESTING"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    INVALID_SHAPE = "INVALID_SHAPE"
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
    VALUE_OUT_OF_BOUNDS = "VALUE_OUT_OF_BOUNDS"


FINITE_PARSER_ERROR_MESSAGES = MappingProxyType(
    {
        FiniteParserErrorCode.INVALID_INPUT: "synthetic source input is invalid",
        FiniteParserErrorCode.CONTENT_TOO_LARGE: "synthetic source exceeds byte limit",
        FiniteParserErrorCode.BOM_NOT_ALLOWED: "synthetic source must not contain a BOM",
        FiniteParserErrorCode.INVALID_ENCODING: "synthetic source must be strict UTF-8",
        FiniteParserErrorCode.INVALID_JSON: "synthetic source is not one JSON document",
        FiniteParserErrorCode.DUPLICATE_KEY: "synthetic source contains a duplicate key",
        FiniteParserErrorCode.FLOAT_NOT_ALLOWED: "synthetic source floats are not allowed",
        FiniteParserErrorCode.NON_FINITE_NUMBER: (
            "synthetic source non-finite numbers are not allowed"
        ),
        FiniteParserErrorCode.UNSAFE_NESTING: "synthetic source nesting is unsafe",
        FiniteParserErrorCode.UNKNOWN_FIELD: "synthetic source contains an unknown field",
        FiniteParserErrorCode.INVALID_SHAPE: "synthetic source shape is invalid",
        FiniteParserErrorCode.SCHEMA_MISMATCH: "synthetic source schema is unsupported",
        FiniteParserErrorCode.VALUE_OUT_OF_BOUNDS: (
            "synthetic source value exceeds a safe bound"
        ),
    }
)


class FiniteParserError(Exception):
    """Stable, bounded parse failure that never includes source content."""

    def __init__(self, code: FiniteParserErrorCode) -> None:
        if not isinstance(code, FiniteParserErrorCode):
            raise ValueError("finite parser error code must be typed")
        self.code = code
        super().__init__(FINITE_PARSER_ERROR_MESSAGES[code])


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
