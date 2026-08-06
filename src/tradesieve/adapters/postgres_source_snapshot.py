"""Transactional PostgreSQL persistence for immutable source snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg import Connection

from tradesieve.adapters.source_snapshot_codec import (
    decode_snapshot_document,
    decode_validation_document,
    encode_snapshot_document,
    encode_validation_document,
)
from tradesieve.application.auth import Operation
from tradesieve.application.source_snapshot import (
    SOURCE_SNAPSHOT_TARGET_TYPE,
    SOURCE_TARGET_TYPE,
    source_authorization_target_id,
    source_snapshot_authorization_target_id,
)
from tradesieve.domain.source_registry import (
    SourceAvailability,
    SourceRuntimeObservation,
)
from tradesieve.domain.source_snapshot import (
    EVENT_TYPE_BY_SUCCESS_COMMAND_REASON,
    MAX_LIFECYCLE_EVENTS,
    ArtifactWriteOutcome,
    InvalidSourceSnapshotTransition,
    LifecycleActorType,
    LifecycleWriteOutcome,
    ParsedSourceSnapshot,
    RawObjectIntegrityError,
    RawObjectMetadata,
    RawObjectRef,
    SnapshotCommandAuditRecord,
    SnapshotCommandOutcome,
    SnapshotCommandReason,
    SourceSnapshotEventType,
    SourceSnapshotIntegrityError,
    SourceSnapshotLifecycle,
    SourceSnapshotLifecycleEvent,
    SourceSnapshotRef,
    ValidationReport,
    fold_source_snapshot_events,
    require_query_limit,
    semantically_applied_lifecycle_transition,
    validate_atomic_command_audit,
)
from tradesieve.ports.source_snapshot import SourceSnapshotPersistenceError

MAX_PERSISTED_ARTIFACTS = MAX_LIFECYCLE_EVENTS
MAX_PERSISTED_AUDITS = MAX_LIFECYCLE_EVENTS

RAW_COLUMNS = (
    "deployment_id, source_id, object_id, original_name, media_type, charset, "
    "retrieved_at, effective_from, byte_length, content_hash"
)
SNAPSHOT_COLUMNS = (
    "deployment_id, source_id, snapshot_id, content_hash, raw_object_id, "
    "raw_content_hash, raw_byte_length, parser_id, parser_version, schema_id, "
    "declared_record_count, parsed_at, private_payload"
)
VALIDATION_COLUMNS = (
    "deployment_id, source_id, snapshot_id, snapshot_content_hash, "
    "previous_deployment_id, previous_source_id, previous_snapshot_id, "
    "previous_snapshot_content_hash, expected_schema_id, passed, validated_at, "
    "report_content_hash, diff_content_hash, private_payload"
)
STATE_COLUMNS = "last_sequence, active_snapshot_id, active_snapshot_content_hash"
EVENT_COLUMNS = (
    "deployment_id, source_id, sequence, event_id, event_type, raw_object_id, "
    "raw_content_hash, raw_byte_length, snapshot_id, snapshot_content_hash, "
    "previous_active_snapshot_id, previous_active_snapshot_content_hash, "
    "validation_report_hash, validation_diff_hash, validation_passed, actor_id, "
    "actor_type, reason, occurred_at"
)
AUDIT_COLUMNS = (
    "command_event_id, authorization_event_id, lifecycle_event_id, tenant_id, "
    "deployment_id, source_id, raw_object_id, snapshot_id, "
    "snapshot_content_hash, target_type, target_id, actor_id, actor_type, "
    "operation, outcome, reason, occurred_at"
)
OBSERVATION_COLUMNS = (
    "availability, observed_at, active_snapshot_id, retrieved_at, effective_from"
)
AUTHORIZATION_COLUMNS = (
    "event_id, actor_subject, actor_type, actor_tenant_id, request_tenant_id, "
    "target_tenant_id, operation, target_ref, outcome"
)

APPLIED_REASONS = {
    SourceSnapshotEventType.RETRIEVED: SnapshotCommandReason.RETRIEVED,
    SourceSnapshotEventType.QUARANTINED: SnapshotCommandReason.QUARANTINED,
    SourceSnapshotEventType.PARSED: SnapshotCommandReason.PARSED,
    SourceSnapshotEventType.VALIDATION_FAILED: (
        SnapshotCommandReason.VALIDATION_FAILED
    ),
    SourceSnapshotEventType.VALIDATED: SnapshotCommandReason.VALIDATED,
    SourceSnapshotEventType.APPROVED: SnapshotCommandReason.APPROVED,
    SourceSnapshotEventType.ACTIVATED: SnapshotCommandReason.ACTIVATED,
    SourceSnapshotEventType.ROLLED_BACK: SnapshotCommandReason.ROLLED_BACK,
}

IDEMPOTENT_REASONS = {
    SourceSnapshotEventType.RETRIEVED: SnapshotCommandReason.RETRIEVE_IDEMPOTENT,
    SourceSnapshotEventType.QUARANTINED: SnapshotCommandReason.QUARANTINE_IDEMPOTENT,
    SourceSnapshotEventType.PARSED: SnapshotCommandReason.PARSE_IDEMPOTENT,
    SourceSnapshotEventType.VALIDATION_FAILED: (
        SnapshotCommandReason.VALIDATION_FAILURE_IDEMPOTENT
    ),
    SourceSnapshotEventType.VALIDATED: SnapshotCommandReason.VALIDATE_IDEMPOTENT,
    SourceSnapshotEventType.APPROVED: SnapshotCommandReason.APPROVE_IDEMPOTENT,
    SourceSnapshotEventType.ACTIVATED: SnapshotCommandReason.ACTIVATE_IDEMPOTENT,
    SourceSnapshotEventType.ROLLED_BACK: SnapshotCommandReason.ROLLBACK_IDEMPOTENT,
}

_SOURCE_OPERATIONS = frozenset(
    {
        Operation.SOURCE_SNAPSHOT_INGEST.value,
        Operation.SOURCE_SNAPSHOT_PARSE.value,
        Operation.SOURCE_SNAPSHOT_VALIDATE.value,
    }
)
_GOVERNANCE_OPERATIONS = frozenset(
    {
        Operation.SOURCE_SNAPSHOT_APPROVE.value,
        Operation.SOURCE_SNAPSHOT_ACTIVATE.value,
        Operation.SOURCE_SNAPSHOT_ROLLBACK.value,
    }
)

type SourceKey = tuple[str, str]
type RawKey = tuple[str, str, str]
type SnapshotKey = tuple[str, str, str]
type Row = tuple[Any, ...]

_DATABASE_OR_CORRUPTION_ERRORS = (
    psycopg.Error,
    RawObjectIntegrityError,
    SourceSnapshotIntegrityError,
    InvalidSourceSnapshotTransition,
    AttributeError,
    IndexError,
    KeyError,
    OverflowError,
    TypeError,
    ValueError,
)


class _WriteConflict(Exception):
    pass


@dataclass(frozen=True, slots=True)
class _ValidationEvidence:
    snapshot: SourceSnapshotRef
    previous_snapshot: SourceSnapshotRef | None
    report: ValidationReport


@dataclass(frozen=True, slots=True)
class _StoredAudit:
    audit: SnapshotCommandAuditRecord
    target_type: str
    target_id: str


@dataclass(frozen=True, slots=True)
class _ScopeView:
    source_set_id: str
    raw: dict[str, RawObjectMetadata]
    snapshots: dict[str, ParsedSourceSnapshot]
    validation: dict[str, _ValidationEvidence]
    events: tuple[SourceSnapshotLifecycleEvent, ...]
    lifecycle: SourceSnapshotLifecycle
    audits: tuple[SnapshotCommandAuditRecord, ...]
    observation: SourceRuntimeObservation | None


class PostgresSourceSnapshotRepository:
    """Verify complete bounded scope state inside every PostgreSQL transaction."""

    def __init__(self, connection: Connection[Any]) -> None:
        self._connection = connection

    def save_retrieval_atomic(
        self,
        metadata: RawObjectMetadata,
        retrieved_event: SourceSnapshotLifecycleEvent,
        applied_audit: SnapshotCommandAuditRecord,
        idempotent_audit: SnapshotCommandAuditRecord,
    ) -> ArtifactWriteOutcome:
        checked_metadata = _checked_raw_metadata(metadata)
        event = _checked_event(retrieved_event)
        applied = _checked_audit(applied_audit)
        idempotent = _checked_audit(idempotent_audit)
        scope = (checked_metadata.deployment_id, checked_metadata.source_id)
        if (
            event.event_type is not SourceSnapshotEventType.RETRIEVED
            or (event.deployment_id, event.source_id) != scope
            or event.raw_object != checked_metadata.reference()
            or event.snapshot is not None
        ):
            raise ValueError("retrieval event must identify exact raw metadata")
        self._validate_audit_pair(event, applied, idempotent)
        try:
            with self._connection.transaction():
                view = self._lock_and_validate_scope(scope)
                self._require_unused_event_id(event.event_id)
                existing = view.raw.get(checked_metadata.object_id)
                if existing is not None:
                    if existing != checked_metadata:
                        raise _WriteConflict
                    responsible = semantically_applied_lifecycle_transition(
                        event, view.lifecycle, view.events
                    )
                    if responsible is None:
                        raise _WriteConflict
                    self._require_idempotent_audit(idempotent, responsible)
                    self._insert_audit(idempotent, view.source_set_id)
                    return ArtifactWriteOutcome.IDEMPOTENT
                updated = self._fold_new_event(view.events, event)
                self._insert_raw(checked_metadata)
                self._insert_event(event)
                self._insert_audit(applied, view.source_set_id)
                self._update_state(scope, event.sequence, updated)
                return ArtifactWriteOutcome.APPLIED
        except _WriteConflict:
            return ArtifactWriteOutcome.CONFLICT
        except SourceSnapshotPersistenceError:
            raise
        except _DATABASE_OR_CORRUPTION_ERRORS:
            raise SourceSnapshotPersistenceError from None

    def save_parsed_snapshot_atomic(
        self,
        snapshot: ParsedSourceSnapshot,
        parsed_event: SourceSnapshotLifecycleEvent,
        applied_audit: SnapshotCommandAuditRecord,
        idempotent_audit: SnapshotCommandAuditRecord,
    ) -> ArtifactWriteOutcome:
        checked_snapshot = _checked_snapshot(snapshot)
        event = _checked_event(parsed_event)
        applied = _checked_audit(applied_audit)
        idempotent = _checked_audit(idempotent_audit)
        scope = (checked_snapshot.deployment_id, checked_snapshot.source_id)
        if (
            event.event_type is not SourceSnapshotEventType.PARSED
            or (event.deployment_id, event.source_id) != scope
            or event.raw_object != checked_snapshot.raw_object
            or event.snapshot != checked_snapshot.reference()
        ):
            raise ValueError("parsed event must identify exact immutable snapshot")
        self._validate_audit_pair(event, applied, idempotent)
        try:
            with self._connection.transaction():
                view = self._lock_and_validate_scope(scope)
                self._require_unused_event_id(event.event_id)
                metadata = self._require_raw(view, checked_snapshot.raw_object)
                payload = encode_snapshot_document(
                    checked_snapshot, raw_metadata=metadata
                )
                existing = view.snapshots.get(checked_snapshot.snapshot_id)
                if existing is not None:
                    if existing != checked_snapshot:
                        raise _WriteConflict
                    responsible = semantically_applied_lifecycle_transition(
                        event, view.lifecycle, view.events
                    )
                    if responsible is None:
                        raise _WriteConflict
                    self._require_idempotent_audit(idempotent, responsible)
                    self._insert_audit(idempotent, view.source_set_id)
                    return ArtifactWriteOutcome.IDEMPOTENT
                updated = self._fold_new_event(view.events, event)
                self._insert_snapshot(checked_snapshot, payload)
                self._insert_event(event)
                self._insert_audit(applied, view.source_set_id)
                self._update_state(scope, event.sequence, updated)
                return ArtifactWriteOutcome.APPLIED
        except _WriteConflict:
            return ArtifactWriteOutcome.CONFLICT
        except SourceSnapshotPersistenceError:
            raise
        except _DATABASE_OR_CORRUPTION_ERRORS:
            raise SourceSnapshotPersistenceError from None

    def append_lifecycle_atomic(
        self,
        event: SourceSnapshotLifecycleEvent,
        applied_audit: SnapshotCommandAuditRecord,
        idempotent_audit: SnapshotCommandAuditRecord,
    ) -> LifecycleWriteOutcome:
        checked_event = _checked_event(event)
        applied = _checked_audit(applied_audit)
        idempotent = _checked_audit(idempotent_audit)
        if checked_event.event_type not in {
            SourceSnapshotEventType.QUARANTINED,
            SourceSnapshotEventType.VALIDATION_FAILED,
            SourceSnapshotEventType.VALIDATED,
            SourceSnapshotEventType.APPROVED,
        }:
            raise ValueError("event requires its explicit atomic repository method")
        self._validate_audit_pair(checked_event, applied, idempotent)
        scope = (checked_event.deployment_id, checked_event.source_id)
        try:
            with self._connection.transaction():
                view = self._lock_and_validate_scope(scope)
                self._require_unused_event_id(checked_event.event_id)
                self._require_event_references(view, checked_event)
                validation_payload: bytes | None = None
                if checked_event.event_type in {
                    SourceSnapshotEventType.VALIDATION_FAILED,
                    SourceSnapshotEventType.VALIDATED,
                }:
                    report = checked_event.validation_report
                    assert report is not None and checked_event.snapshot is not None
                    if report.diff.previous_snapshot != view.lifecycle.active_snapshot:
                        raise _WriteConflict
                    snapshot = self._require_snapshot(view, checked_event.snapshot)
                    previous = (
                        None
                        if view.lifecycle.active_snapshot is None
                        else self._require_snapshot(
                            view, view.lifecycle.active_snapshot
                        )
                    )
                    validation_payload = encode_validation_document(
                        report,
                        snapshot=snapshot,
                        previous_accepted=previous,
                    )
                responsible = semantically_applied_lifecycle_transition(
                    checked_event, view.lifecycle, view.events
                )
                if responsible is not None:
                    self._require_idempotent_audit(idempotent, responsible)
                    self._insert_audit(idempotent, view.source_set_id)
                    return LifecycleWriteOutcome.IDEMPOTENT
                updated = self._fold_new_event(view.events, checked_event)
                if validation_payload is not None:
                    self._insert_validation(checked_event, validation_payload)
                self._insert_event(checked_event)
                self._insert_audit(applied, view.source_set_id)
                self._update_state(scope, checked_event.sequence, updated)
                return LifecycleWriteOutcome.APPLIED
        except _WriteConflict:
            return LifecycleWriteOutcome.CONFLICT
        except SourceSnapshotPersistenceError:
            raise
        except _DATABASE_OR_CORRUPTION_ERRORS:
            raise SourceSnapshotPersistenceError from None

    def activate_or_rollback_atomic(
        self,
        event: SourceSnapshotLifecycleEvent,
        observation: SourceRuntimeObservation,
        applied_audit: SnapshotCommandAuditRecord,
        idempotent_audit: SnapshotCommandAuditRecord,
    ) -> LifecycleWriteOutcome:
        checked_event = _checked_event(event)
        checked_observation = _checked_observation(observation)
        applied = _checked_audit(applied_audit)
        idempotent = _checked_audit(idempotent_audit)
        if checked_event.event_type not in {
            SourceSnapshotEventType.ACTIVATED,
            SourceSnapshotEventType.ROLLED_BACK,
        }:
            raise ValueError("pointer UoW accepts only activation or rollback")
        self._validate_audit_pair(checked_event, applied, idempotent)
        scope = (checked_event.deployment_id, checked_event.source_id)
        try:
            with self._connection.transaction():
                view = self._lock_and_validate_scope(scope)
                self._require_unused_event_id(checked_event.event_id)
                self._require_event_references(view, checked_event)
                self._validate_observation(
                    scope, view.raw, checked_event, checked_observation
                )
                validation: SourceSnapshotLifecycleEvent | None = None
                if checked_event.event_type is SourceSnapshotEventType.ACTIVATED:
                    validation_events = tuple(
                        item
                        for item in view.events
                        if item.event_type is SourceSnapshotEventType.VALIDATED
                        and item.snapshot == checked_event.snapshot
                    )
                    if len(validation_events) != 1:
                        raise SourceSnapshotPersistenceError
                    validation = validation_events[0]
                    report = validation.validation_report
                    if (
                        report is None
                        or not report.passed
                        or validation.raw_object != checked_event.raw_object
                        or report.snapshot != checked_event.snapshot
                        or report.diff.new_snapshot != checked_event.snapshot
                    ):
                        raise SourceSnapshotPersistenceError
                responsible = semantically_applied_lifecycle_transition(
                    checked_event, view.lifecycle, view.events
                )
                if responsible is not None:
                    last_pointer = next(
                        item
                        for item in reversed(view.events)
                        if item.event_type
                        in {
                            SourceSnapshotEventType.ACTIVATED,
                            SourceSnapshotEventType.ROLLED_BACK,
                        }
                    )
                    if responsible != last_pointer:
                        raise _WriteConflict
                    if (
                        validation is not None
                        and validation.validation_report is not None
                        and validation.validation_report.diff.previous_snapshot
                        != responsible.previous_active_snapshot
                    ):
                        raise SourceSnapshotPersistenceError
                    self._require_idempotent_audit(idempotent, responsible)
                    self._insert_audit(idempotent, view.source_set_id)
                    return LifecycleWriteOutcome.IDEMPOTENT
                if (
                    validation is not None
                    and validation.validation_report is not None
                    and validation.validation_report.diff.previous_snapshot
                    != view.lifecycle.active_snapshot
                ):
                    raise _WriteConflict
                updated = self._fold_new_event(view.events, checked_event)
                if (
                    view.observation is not None
                    and checked_observation.observed_at <= view.observation.observed_at
                ):
                    raise _WriteConflict
                self._insert_event(checked_event)
                self._insert_audit(applied, view.source_set_id)
                self._update_state(scope, checked_event.sequence, updated)
                self._write_observation(view.observation, checked_observation)
                return LifecycleWriteOutcome.APPLIED
        except _WriteConflict:
            return LifecycleWriteOutcome.CONFLICT
        except SourceSnapshotPersistenceError:
            raise
        except _DATABASE_OR_CORRUPTION_ERRORS:
            raise SourceSnapshotPersistenceError from None

    def append_command_audit(self, audit: SnapshotCommandAuditRecord) -> None:
        checked = _checked_audit(audit)
        if (
            checked.outcome is not SnapshotCommandOutcome.FAILURE
            or checked.lifecycle_event_id is not None
        ):
            raise ValueError("standalone command audits must be unlinked failures")
        scope = (checked.deployment_id, checked.source_id)
        try:
            with self._connection.transaction():
                view = self._lock_and_validate_scope(scope)
                self._insert_audit(checked, view.source_set_id)
        except SourceSnapshotPersistenceError:
            raise
        except _DATABASE_OR_CORRUPTION_ERRORS:
            raise SourceSnapshotPersistenceError from None

    def get_raw_metadata(
        self, deployment_id: str, source_id: str, object_id: str
    ) -> RawObjectMetadata | None:
        try:
            with self._connection.transaction():
                view = self._read_and_validate_scope((deployment_id, source_id))
                return view.raw.get(object_id)
        except SourceSnapshotPersistenceError:
            raise
        except _DATABASE_OR_CORRUPTION_ERRORS:
            raise SourceSnapshotPersistenceError from None

    def get_snapshot(
        self, deployment_id: str, source_id: str, snapshot_id: str
    ) -> ParsedSourceSnapshot | None:
        try:
            with self._connection.transaction():
                view = self._read_and_validate_scope((deployment_id, source_id))
                return view.snapshots.get(snapshot_id)
        except SourceSnapshotPersistenceError:
            raise
        except _DATABASE_OR_CORRUPTION_ERRORS:
            raise SourceSnapshotPersistenceError from None

    def list_snapshots(
        self, deployment_id: str, source_id: str, *, limit: int
    ) -> tuple[ParsedSourceSnapshot, ...]:
        require_query_limit(limit)
        try:
            with self._connection.transaction():
                view = self._read_and_validate_scope((deployment_id, source_id))
                return tuple(
                    sorted(view.snapshots.values(), key=lambda item: item.snapshot_id)
                )[:limit]
        except SourceSnapshotPersistenceError:
            raise
        except _DATABASE_OR_CORRUPTION_ERRORS:
            raise SourceSnapshotPersistenceError from None

    def get_lifecycle_snapshot(
        self, deployment_id: str, source_id: str
    ) -> tuple[tuple[SourceSnapshotLifecycleEvent, ...], SourceSnapshotLifecycle]:
        try:
            with self._connection.transaction():
                view = self._read_and_validate_scope((deployment_id, source_id))
                return view.events, view.lifecycle
        except SourceSnapshotPersistenceError:
            raise
        except _DATABASE_OR_CORRUPTION_ERRORS:
            raise SourceSnapshotPersistenceError from None

    def list_lifecycle_events(
        self, deployment_id: str, source_id: str, *, limit: int
    ) -> tuple[SourceSnapshotLifecycleEvent, ...]:
        require_query_limit(limit)
        try:
            with self._connection.transaction():
                view = self._read_and_validate_scope((deployment_id, source_id))
                return view.events[:limit]
        except SourceSnapshotPersistenceError:
            raise
        except _DATABASE_OR_CORRUPTION_ERRORS:
            raise SourceSnapshotPersistenceError from None

    def list_command_audits(
        self, deployment_id: str, source_id: str, *, limit: int
    ) -> tuple[SnapshotCommandAuditRecord, ...]:
        require_query_limit(limit)
        try:
            with self._connection.transaction():
                view = self._read_and_validate_scope((deployment_id, source_id))
                return view.audits[:limit]
        except SourceSnapshotPersistenceError:
            raise
        except _DATABASE_OR_CORRUPTION_ERRORS:
            raise SourceSnapshotPersistenceError from None

    def get_runtime_observation(
        self, deployment_id: str, source_id: str
    ) -> SourceRuntimeObservation | None:
        try:
            with self._connection.transaction():
                view = self._read_and_validate_scope((deployment_id, source_id))
                return view.observation
        except SourceSnapshotPersistenceError:
            raise
        except _DATABASE_OR_CORRUPTION_ERRORS:
            raise SourceSnapshotPersistenceError from None

    @staticmethod
    def _validate_audit_pair(
        event: SourceSnapshotLifecycleEvent,
        applied: SnapshotCommandAuditRecord,
        idempotent: SnapshotCommandAuditRecord,
    ) -> None:
        scope = (event.deployment_id, event.source_id)
        if (
            (applied.deployment_id, applied.source_id) != scope
            or (idempotent.deployment_id, idempotent.source_id) != scope
            or applied.tenant_id != idempotent.tenant_id
            or idempotent.occurred_at < event.occurred_at
        ):
            raise ValueError("command audit pair must match event scope and time")
        validate_atomic_command_audit(
            applied,
            event.raw_object,
            event.snapshot,
            event,
            APPLIED_REASONS[event.event_type],
        )
        validate_atomic_command_audit(
            idempotent,
            event.raw_object,
            event.snapshot,
            None,
            IDEMPOTENT_REASONS[event.event_type],
        )

    def _require_unused_event_id(self, event_id: str) -> None:
        rows = self._fetch_bounded(
            "SELECT event_id FROM source_snapshot_lifecycle_event "
            "WHERE event_id = %s LIMIT 2",
            (event_id,),
            maximum=1,
        )
        if rows:
            raise SourceSnapshotPersistenceError

    @staticmethod
    def _require_idempotent_audit(
        audit: SnapshotCommandAuditRecord,
        responsible: SourceSnapshotLifecycleEvent,
    ) -> None:
        expected_reason = IDEMPOTENT_REASONS[responsible.event_type]
        if audit.occurred_at < responsible.occurred_at:
            raise SourceSnapshotPersistenceError
        try:
            validate_atomic_command_audit(
                audit,
                responsible.raw_object,
                responsible.snapshot,
                None,
                expected_reason,
            )
        except ValueError as exc:
            raise SourceSnapshotPersistenceError from exc

    @staticmethod
    def _fold_new_event(
        events: tuple[SourceSnapshotLifecycleEvent, ...],
        event: SourceSnapshotLifecycleEvent,
    ) -> SourceSnapshotLifecycle:
        if len(events) >= MAX_LIFECYCLE_EVENTS:
            raise SourceSnapshotPersistenceError
        expected_sequence = events[-1].sequence + 1 if events else 1
        if event.sequence != expected_sequence:
            raise _WriteConflict
        try:
            return fold_source_snapshot_events(events + (event,))
        except InvalidSourceSnapshotTransition as exc:
            raise _WriteConflict from exc

    @staticmethod
    def _require_raw(view: _ScopeView, reference: RawObjectRef) -> RawObjectMetadata:
        metadata = view.raw.get(reference.object_id)
        if metadata is None or metadata.reference() != reference:
            raise SourceSnapshotPersistenceError
        return metadata

    @staticmethod
    def _require_snapshot(
        view: _ScopeView, reference: SourceSnapshotRef
    ) -> ParsedSourceSnapshot:
        snapshot = view.snapshots.get(reference.snapshot_id)
        if snapshot is None or snapshot.reference() != reference:
            raise SourceSnapshotPersistenceError
        return snapshot

    @classmethod
    def _require_event_references(
        cls, view: _ScopeView, event: SourceSnapshotLifecycleEvent
    ) -> None:
        cls._require_raw(view, event.raw_object)
        if event.snapshot is not None:
            snapshot = cls._require_snapshot(view, event.snapshot)
            if snapshot.raw_object != event.raw_object:
                raise SourceSnapshotPersistenceError
        if event.previous_active_snapshot is not None:
            cls._require_snapshot(view, event.previous_active_snapshot)

    def _insert_raw(self, metadata: RawObjectMetadata) -> None:
        self._execute_one(
            "INSERT INTO source_raw_object_metadata ("
            + RAW_COLUMNS
            + ") VALUES ("
            + ", ".join(["%s"] * 10)
            + ")",
            (
                metadata.deployment_id,
                metadata.source_id,
                metadata.object_id,
                metadata.original_name,
                metadata.media_type,
                metadata.charset,
                metadata.retrieved_at,
                metadata.effective_from,
                metadata.byte_length,
                metadata.content_hash,
            ),
        )

    def _insert_snapshot(self, snapshot: ParsedSourceSnapshot, payload: bytes) -> None:
        self._execute_one(
            "INSERT INTO source_parsed_snapshot ("
            + SNAPSHOT_COLUMNS
            + ") VALUES ("
            + ", ".join(["%s"] * 13)
            + ")",
            (
                snapshot.deployment_id,
                snapshot.source_id,
                snapshot.snapshot_id,
                snapshot.content_hash,
                snapshot.raw_object.object_id,
                snapshot.raw_object.content_hash,
                snapshot.raw_object.byte_length,
                snapshot.parser_id,
                snapshot.parser_version,
                snapshot.schema_id,
                snapshot.declared_record_count,
                snapshot.parsed_at,
                payload,
            ),
        )

    def _insert_validation(
        self, event: SourceSnapshotLifecycleEvent, payload: bytes
    ) -> None:
        report = event.validation_report
        snapshot = event.snapshot
        assert report is not None and snapshot is not None
        previous = report.diff.previous_snapshot
        previous_values: tuple[str | None, ...] = (
            (None, None, None, None)
            if previous is None
            else (
                event.deployment_id,
                event.source_id,
                previous.snapshot_id,
                previous.content_hash,
            )
        )
        self._execute_one(
            "INSERT INTO source_snapshot_validation_evidence ("
            + VALIDATION_COLUMNS
            + ") VALUES ("
            + ", ".join(["%s"] * 14)
            + ")",
            (
                event.deployment_id,
                event.source_id,
                snapshot.snapshot_id,
                snapshot.content_hash,
                *previous_values,
                report.expected_schema_id,
                report.passed,
                report.validated_at,
                report.content_hash,
                report.diff.content_hash,
                payload,
            ),
        )

    def _insert_event(self, event: SourceSnapshotLifecycleEvent) -> None:
        snapshot_values = _reference_values(event.snapshot)
        previous_values = _reference_values(event.previous_active_snapshot)
        report = event.validation_report
        validation_values: tuple[object | None, ...] = (
            (None, None, None)
            if report is None
            else (report.content_hash, report.diff.content_hash, report.passed)
        )
        self._execute_one(
            "INSERT INTO source_snapshot_lifecycle_event ("
            + EVENT_COLUMNS
            + ") VALUES ("
            + ", ".join(["%s"] * 19)
            + ")",
            (
                event.deployment_id,
                event.source_id,
                event.sequence,
                event.event_id,
                event.event_type.value,
                event.raw_object.object_id,
                event.raw_object.content_hash,
                event.raw_object.byte_length,
                *snapshot_values,
                *previous_values,
                *validation_values,
                event.actor_id,
                event.actor_type.value,
                event.reason,
                event.occurred_at,
            ),
        )

    def _insert_audit(
        self, audit: SnapshotCommandAuditRecord, source_set_id: str
    ) -> None:
        target_type, target_id = _target_for_audit(audit, source_set_id)
        self._verify_authorization(audit, target_type, target_id)
        self._execute_one(
            "INSERT INTO source_snapshot_command_audit ("
            + AUDIT_COLUMNS
            + ") VALUES ("
            + ", ".join(["%s"] * 17)
            + ")",
            (
                audit.command_event_id,
                audit.authorization_event_id,
                audit.lifecycle_event_id,
                audit.tenant_id,
                audit.deployment_id,
                audit.source_id,
                audit.raw_object_id,
                audit.snapshot_id,
                audit.snapshot_content_hash,
                target_type,
                target_id,
                audit.actor_id,
                audit.actor_type.value,
                audit.operation,
                audit.outcome.value,
                audit.reason.value,
                audit.occurred_at,
            ),
        )

    def _update_state(
        self,
        scope: SourceKey,
        sequence: int,
        lifecycle: SourceSnapshotLifecycle,
    ) -> None:
        active = _reference_values(lifecycle.active_snapshot)
        self._execute_one(
            "UPDATE source_snapshot_lifecycle_state SET last_sequence = %s, "
            "active_snapshot_id = %s, active_snapshot_content_hash = %s, "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE deployment_id = %s AND source_id = %s",
            (sequence, *active, *scope),
        )

    def _write_observation(
        self,
        current: SourceRuntimeObservation | None,
        observation: SourceRuntimeObservation,
    ) -> None:
        values = (
            observation.deployment_id,
            observation.source_id,
            observation.availability.value,
            observation.observed_at,
            observation.active_snapshot_id,
            observation.retrieved_at,
            observation.effective_from,
        )
        if current is None:
            self._execute_one(
                "INSERT INTO source_runtime_observation "
                "(deployment_id, source_id, availability, observed_at, "
                "active_snapshot_id, retrieved_at, effective_from) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                values,
            )
            return
        self._execute_one(
            "UPDATE source_runtime_observation SET availability = %s, "
            "observed_at = %s, active_snapshot_id = %s, retrieved_at = %s, "
            "effective_from = %s WHERE deployment_id = %s AND source_id = %s "
            "AND observed_at = %s",
            (
                observation.availability.value,
                observation.observed_at,
                observation.active_snapshot_id,
                observation.retrieved_at,
                observation.effective_from,
                observation.deployment_id,
                observation.source_id,
                current.observed_at,
            ),
        )

    def _execute_one(self, query: str, params: tuple[object, ...]) -> None:
        result = self._connection.execute(query, params)
        if type(result.rowcount) is not int or result.rowcount != 1:
            raise SourceSnapshotPersistenceError

    def _lock_and_validate_scope(self, scope: SourceKey) -> _ScopeView:
        source_set_id = self._registration(scope, lock="FOR SHARE", require_active=True)
        self._connection.execute(
            "INSERT INTO source_snapshot_lifecycle_state "
            "(deployment_id, source_id, last_sequence) VALUES (%s, %s, 0) "
            "ON CONFLICT DO NOTHING",
            scope,
        )
        state_rows = self._fetch_bounded(
            f"SELECT {STATE_COLUMNS} FROM source_snapshot_lifecycle_state "
            "WHERE deployment_id = %s AND source_id = %s LIMIT 2 FOR UPDATE",
            scope,
            maximum=1,
        )
        if len(state_rows) != 1:
            raise SourceSnapshotPersistenceError
        return self._load_and_validate_scope(
            scope,
            source_set_id=source_set_id,
            state_row=state_rows[0],
            observation_lock="FOR UPDATE",
        )

    def _read_and_validate_scope(self, scope: SourceKey) -> _ScopeView:
        source_set_id = self._registration(
            scope, lock="FOR SHARE", require_active=False
        )
        state_rows = self._fetch_bounded(
            f"SELECT {STATE_COLUMNS} FROM source_snapshot_lifecycle_state "
            "WHERE deployment_id = %s AND source_id = %s LIMIT 2 FOR SHARE",
            scope,
            maximum=1,
        )
        state_row = state_rows[0] if state_rows else None
        return self._load_and_validate_scope(
            scope,
            source_set_id=source_set_id,
            state_row=state_row,
            observation_lock="FOR SHARE",
        )

    def _load_and_validate_scope(
        self,
        scope: SourceKey,
        *,
        source_set_id: str,
        state_row: Row | None,
        observation_lock: str,
    ) -> _ScopeView:
        raw_rows = self._fetch_bounded(
            f"SELECT {RAW_COLUMNS} FROM source_raw_object_metadata "
            "WHERE deployment_id = %s AND source_id = %s "
            "ORDER BY object_id LIMIT %s",
            (*scope, MAX_PERSISTED_ARTIFACTS + 1),
            maximum=MAX_PERSISTED_ARTIFACTS,
        )
        raw = self._raw_rows(scope, raw_rows)
        snapshot_rows = self._fetch_bounded(
            f"SELECT {SNAPSHOT_COLUMNS} FROM source_parsed_snapshot "
            "WHERE deployment_id = %s AND source_id = %s "
            "ORDER BY snapshot_id LIMIT %s",
            (*scope, MAX_PERSISTED_ARTIFACTS + 1),
            maximum=MAX_PERSISTED_ARTIFACTS,
        )
        snapshots = self._snapshot_rows(scope, snapshot_rows, raw)
        validation_rows = self._fetch_bounded(
            f"SELECT {VALIDATION_COLUMNS} "
            "FROM source_snapshot_validation_evidence "
            "WHERE deployment_id = %s AND source_id = %s "
            "ORDER BY snapshot_id LIMIT %s",
            (*scope, MAX_PERSISTED_ARTIFACTS + 1),
            maximum=MAX_PERSISTED_ARTIFACTS,
        )
        validation = self._validation_rows(scope, validation_rows, snapshots)
        event_rows = self._fetch_bounded(
            f"SELECT {EVENT_COLUMNS} FROM source_snapshot_lifecycle_event "
            "WHERE deployment_id = %s AND source_id = %s "
            "ORDER BY sequence LIMIT %s",
            (*scope, MAX_LIFECYCLE_EVENTS + 1),
            maximum=MAX_LIFECYCLE_EVENTS,
        )
        events = tuple(
            self._event_from_row(row, scope, raw, snapshots, validation)
            for row in event_rows
        )
        audit_rows = self._fetch_bounded(
            f"SELECT {AUDIT_COLUMNS} FROM source_snapshot_command_audit "
            "WHERE deployment_id = %s AND source_id = %s "
            "ORDER BY occurred_at, command_event_id LIMIT %s",
            (*scope, MAX_PERSISTED_AUDITS + 1),
            maximum=MAX_PERSISTED_AUDITS,
        )
        stored_audits = tuple(
            self._audit_from_row(row, scope, source_set_id) for row in audit_rows
        )
        observation_rows = self._fetch_bounded(
            f"SELECT {OBSERVATION_COLUMNS} FROM source_runtime_observation "
            "WHERE deployment_id = %s AND source_id = %s LIMIT 2 " + observation_lock,
            scope,
            maximum=1,
        )
        observation = (
            self._observation_from_row(scope, observation_rows[0])
            if observation_rows
            else None
        )
        try:
            lifecycle = fold_source_snapshot_events(events)
        except (InvalidSourceSnapshotTransition, TypeError, ValueError) as exc:
            raise SourceSnapshotPersistenceError from exc
        audits = tuple(item.audit for item in stored_audits)
        self._validate_state(
            state_row,
            events=events,
            lifecycle=lifecycle,
            has_scope_data=bool(
                raw or snapshots or validation or audits or observation
            ),
        )
        self._verify_artifacts(scope, raw, snapshots, validation, events)
        self._verify_audits(events, audits)
        self._verify_observation(scope, raw, events, lifecycle, observation)
        return _ScopeView(
            source_set_id,
            raw,
            snapshots,
            validation,
            events,
            lifecycle,
            audits,
            observation,
        )

    def _registration(
        self, scope: SourceKey, *, lock: str, require_active: bool
    ) -> str:
        rows = self._fetch_bounded(
            "SELECT source_set_id, active FROM source_registry "
            "WHERE deployment_id = %s AND source_id = %s LIMIT 2 " + lock,
            scope,
            maximum=1,
        )
        if len(rows) != 1 or len(rows[0]) != 2:
            raise SourceSnapshotPersistenceError
        source_set_id = _string(rows[0][0], "source_set_id")
        active = _boolean(rows[0][1], "active")
        if require_active and not active:
            raise SourceSnapshotPersistenceError
        return source_set_id

    @staticmethod
    def _raw_rows(
        scope: SourceKey, rows: tuple[Row, ...]
    ) -> dict[str, RawObjectMetadata]:
        result: dict[str, RawObjectMetadata] = {}
        for row in rows:
            metadata = _raw_from_row(row)
            if (
                metadata.deployment_id,
                metadata.source_id,
            ) != scope or metadata.object_id in result:
                raise SourceSnapshotPersistenceError
            result[metadata.object_id] = metadata
        return result

    @staticmethod
    def _snapshot_rows(
        scope: SourceKey,
        rows: tuple[Row, ...],
        raw: dict[str, RawObjectMetadata],
    ) -> dict[str, ParsedSourceSnapshot]:
        result: dict[str, ParsedSourceSnapshot] = {}
        for row in rows:
            if len(row) != 13:
                raise SourceSnapshotPersistenceError
            row_scope = (
                _string(row[0], "deployment_id"),
                _string(row[1], "source_id"),
            )
            if row_scope != scope:
                raise SourceSnapshotPersistenceError
            raw_object_id = _string(row[4], "raw_object_id")
            metadata = raw.get(raw_object_id)
            if metadata is None:
                raise SourceSnapshotPersistenceError
            raw_reference = RawObjectRef(
                raw_object_id,
                _string(row[5], "raw_content_hash"),
                _integer(row[6], "raw_byte_length", allow_zero=True),
            )
            if metadata.reference() != raw_reference:
                raise SourceSnapshotPersistenceError
            snapshot_id = _string(row[2], "snapshot_id")
            content_hash = _string(row[3], "content_hash")
            parsed_at = _datetime(row[11], "parsed_at")
            snapshot = decode_snapshot_document(
                _bytes(row[12], "private_payload"),
                deployment_id=scope[0],
                source_id=scope[1],
                snapshot_id=snapshot_id,
                content_hash=content_hash,
                raw_metadata=metadata,
            )
            if (
                snapshot.raw_object != raw_reference
                or snapshot.parser_id != _string(row[7], "parser_id")
                or snapshot.parser_version != _string(row[8], "parser_version")
                or snapshot.schema_id != _string(row[9], "schema_id")
                or snapshot.declared_record_count
                != _integer(row[10], "declared_record_count", allow_zero=True)
                or snapshot.parsed_at != parsed_at
                or snapshot_id in result
            ):
                raise SourceSnapshotPersistenceError
            result[snapshot_id] = snapshot
        return result

    @staticmethod
    def _validation_rows(
        scope: SourceKey,
        rows: tuple[Row, ...],
        snapshots: dict[str, ParsedSourceSnapshot],
    ) -> dict[str, _ValidationEvidence]:
        result: dict[str, _ValidationEvidence] = {}
        for row in rows:
            if len(row) != 14:
                raise SourceSnapshotPersistenceError
            row_scope = (
                _string(row[0], "deployment_id"),
                _string(row[1], "source_id"),
            )
            if row_scope != scope:
                raise SourceSnapshotPersistenceError
            snapshot_id = _string(row[2], "snapshot_id")
            snapshot = snapshots.get(snapshot_id)
            snapshot_hash = _string(row[3], "snapshot_content_hash")
            if snapshot is None or snapshot.content_hash != snapshot_hash:
                raise SourceSnapshotPersistenceError
            previous, previous_snapshot = _relational_previous_snapshot(
                row[4:8], scope, snapshots
            )
            expected_schema_id = _string(row[8], "expected_schema_id")
            passed = _boolean(row[9], "passed")
            validated_at = _datetime(row[10], "validated_at")
            report_hash = _string(row[11], "report_content_hash")
            diff_hash = _string(row[12], "diff_content_hash")
            report = decode_validation_document(
                _bytes(row[13], "private_payload"),
                deployment_id=scope[0],
                source_id=scope[1],
                snapshot_id=snapshot_id,
                report_content_hash=report_hash,
                expected_schema_id=expected_schema_id,
                validated_at=validated_at,
                snapshot=snapshot,
                previous_accepted=previous_snapshot,
            )
            if (
                report.passed is not passed
                or report.diff.content_hash != diff_hash
                or report.diff.previous_snapshot != previous
                or snapshot_id in result
            ):
                raise SourceSnapshotPersistenceError
            result[snapshot_id] = _ValidationEvidence(
                snapshot.reference(), previous, report
            )
        return result

    @staticmethod
    def _event_from_row(
        row: Row,
        scope: SourceKey,
        raw: dict[str, RawObjectMetadata],
        snapshots: dict[str, ParsedSourceSnapshot],
        validation: dict[str, _ValidationEvidence],
    ) -> SourceSnapshotLifecycleEvent:
        if len(row) != 19:
            raise SourceSnapshotPersistenceError
        row_scope = (
            _string(row[0], "deployment_id"),
            _string(row[1], "source_id"),
        )
        if row_scope != scope:
            raise SourceSnapshotPersistenceError
        raw_reference = RawObjectRef(
            _string(row[5], "raw_object_id"),
            _string(row[6], "raw_content_hash"),
            _integer(row[7], "raw_byte_length", allow_zero=True),
        )
        metadata = raw.get(raw_reference.object_id)
        if metadata is None or metadata.reference() != raw_reference:
            raise SourceSnapshotPersistenceError
        snapshot = _optional_snapshot_reference(row[8:10], "snapshot")
        previous = _optional_snapshot_reference(row[10:12], "previous")
        report = _event_validation_report(row[12:15], snapshot, validation)
        event = SourceSnapshotLifecycleEvent(
            sequence=_integer(row[2], "sequence"),
            event_id=_string(row[3], "event_id"),
            deployment_id=scope[0],
            source_id=scope[1],
            event_type=SourceSnapshotEventType(_string(row[4], "event_type")),
            raw_object=raw_reference,
            snapshot=snapshot,
            previous_active_snapshot=previous,
            actor_id=_string(row[15], "actor_id"),
            actor_type=LifecycleActorType(_string(row[16], "actor_type")),
            reason=_string(row[17], "reason"),
            occurred_at=_datetime(row[18], "occurred_at"),
            validation_report=report,
        )
        if snapshot is not None:
            stored = snapshots.get(snapshot.snapshot_id)
            if (
                stored is None
                or stored.reference() != snapshot
                or stored.raw_object != raw_reference
            ):
                raise SourceSnapshotPersistenceError
        if previous is not None:
            stored_previous = snapshots.get(previous.snapshot_id)
            if stored_previous is None or stored_previous.reference() != previous:
                raise SourceSnapshotPersistenceError
        return event

    def _audit_from_row(
        self, row: Row, scope: SourceKey, source_set_id: str
    ) -> _StoredAudit:
        if len(row) != 17:
            raise SourceSnapshotPersistenceError
        lifecycle_event_id = _optional_string(row[2], "lifecycle_event_id")
        snapshot_id = _optional_string(row[7], "snapshot_id")
        snapshot_hash = _optional_string(row[8], "snapshot_content_hash")
        if (snapshot_id is None) != (snapshot_hash is None):
            raise SourceSnapshotPersistenceError
        audit = SnapshotCommandAuditRecord(
            command_event_id=_string(row[0], "command_event_id"),
            authorization_event_id=_string(row[1], "authorization_event_id"),
            lifecycle_event_id=lifecycle_event_id,
            tenant_id=_string(row[3], "tenant_id"),
            deployment_id=_string(row[4], "deployment_id"),
            source_id=_string(row[5], "source_id"),
            raw_object_id=_string(row[6], "raw_object_id"),
            snapshot_id=snapshot_id,
            snapshot_content_hash=snapshot_hash,
            actor_id=_string(row[11], "actor_id"),
            actor_type=LifecycleActorType(_string(row[12], "actor_type")),
            operation=_string(row[13], "operation"),
            outcome=SnapshotCommandOutcome(_string(row[14], "outcome")),
            reason=SnapshotCommandReason(_string(row[15], "reason")),
            occurred_at=_datetime(row[16], "occurred_at"),
        )
        if (audit.deployment_id, audit.source_id) != scope:
            raise SourceSnapshotPersistenceError
        target_type = _string(row[9], "target_type")
        target_id = _string(row[10], "target_id")
        expected_target = _target_for_audit(audit, source_set_id)
        if (target_type, target_id) != expected_target:
            raise SourceSnapshotPersistenceError
        self._verify_authorization(audit, target_type, target_id)
        return _StoredAudit(audit, target_type, target_id)

    def _verify_authorization(
        self, audit: SnapshotCommandAuditRecord, target_type: str, target_id: str
    ) -> None:
        rows = self._fetch_bounded(
            f"SELECT {AUTHORIZATION_COLUMNS} FROM authorization_audit_event "
            "WHERE event_id = %s LIMIT 2",
            (audit.authorization_event_id,),
            maximum=1,
        )
        if len(rows) != 1 or len(rows[0]) != 9:
            raise SourceSnapshotPersistenceError
        row = rows[0]
        expected = (
            audit.authorization_event_id,
            audit.actor_id,
            audit.actor_type.value,
            audit.tenant_id,
            audit.tenant_id,
            audit.tenant_id,
            audit.operation,
            f"{target_type}:{target_id}",
            "ALLOW",
        )
        if tuple(_string(value, "authorization") for value in row) != expected:
            raise SourceSnapshotPersistenceError

    @staticmethod
    def _observation_from_row(scope: SourceKey, row: Row) -> SourceRuntimeObservation:
        if len(row) != 5:
            raise SourceSnapshotPersistenceError
        return SourceRuntimeObservation(
            deployment_id=scope[0],
            source_id=scope[1],
            availability=SourceAvailability(_string(row[0], "availability")),
            observed_at=_datetime(row[1], "observed_at"),
            active_snapshot_id=_optional_string(row[2], "active_snapshot_id"),
            retrieved_at=_optional_datetime(row[3], "retrieved_at"),
            effective_from=_optional_datetime(row[4], "effective_from"),
        )

    @staticmethod
    def _validate_state(
        row: Row | None,
        *,
        events: tuple[SourceSnapshotLifecycleEvent, ...],
        lifecycle: SourceSnapshotLifecycle,
        has_scope_data: bool,
    ) -> None:
        if row is None:
            if has_scope_data or events:
                raise SourceSnapshotPersistenceError
            return
        if len(row) != 3:
            raise SourceSnapshotPersistenceError
        last_sequence = _integer(row[0], "last_sequence", allow_zero=True)
        active = _optional_snapshot_reference(row[1:3], "active")
        expected_sequence = events[-1].sequence if events else 0
        if last_sequence != expected_sequence or active != lifecycle.active_snapshot:
            raise SourceSnapshotPersistenceError

    @staticmethod
    def _verify_artifacts(
        scope: SourceKey,
        raw: dict[str, RawObjectMetadata],
        snapshots: dict[str, ParsedSourceSnapshot],
        validation: dict[str, _ValidationEvidence],
        events: tuple[SourceSnapshotLifecycleEvent, ...],
    ) -> None:
        del scope
        for metadata in raw.values():
            retrievals = tuple(
                event
                for event in events
                if event.event_type is SourceSnapshotEventType.RETRIEVED
                and event.raw_object == metadata.reference()
            )
            if len(retrievals) != 1:
                raise SourceSnapshotPersistenceError
        for event in events:
            event_metadata = raw.get(event.raw_object.object_id)
            if event_metadata is None or event_metadata.reference() != event.raw_object:
                raise SourceSnapshotPersistenceError
        for snapshot in snapshots.values():
            parsed = tuple(
                event
                for event in events
                if event.event_type is SourceSnapshotEventType.PARSED
                and event.snapshot == snapshot.reference()
                and event.raw_object == snapshot.raw_object
            )
            if len(parsed) != 1:
                raise SourceSnapshotPersistenceError
        for evidence in validation.values():
            linked = tuple(
                event
                for event in events
                if event.event_type
                in {
                    SourceSnapshotEventType.VALIDATED,
                    SourceSnapshotEventType.VALIDATION_FAILED,
                }
                and event.snapshot == evidence.snapshot
                and event.validation_report == evidence.report
            )
            if len(linked) != 1:
                raise SourceSnapshotPersistenceError

    @staticmethod
    def _verify_audits(
        events: tuple[SourceSnapshotLifecycleEvent, ...],
        audits: tuple[SnapshotCommandAuditRecord, ...],
    ) -> None:
        command_ids: set[str] = set()
        event_ids = {event.event_id for event in events}
        for audit in audits:
            if audit.command_event_id in command_ids:
                raise SourceSnapshotPersistenceError
            if (
                audit.lifecycle_event_id is not None
                and audit.lifecycle_event_id not in event_ids
            ):
                raise SourceSnapshotPersistenceError
            command_ids.add(audit.command_event_id)
        for event in events:
            linked = tuple(
                audit for audit in audits if audit.lifecycle_event_id == event.event_id
            )
            if len(linked) != 1:
                raise SourceSnapshotPersistenceError
            try:
                validate_atomic_command_audit(
                    linked[0],
                    event.raw_object,
                    event.snapshot,
                    event,
                    APPLIED_REASONS[event.event_type],
                )
            except ValueError as exc:
                raise SourceSnapshotPersistenceError from exc
        for audit in audits:
            if (
                audit.outcome is not SnapshotCommandOutcome.SUCCESS
                or audit.lifecycle_event_id is not None
            ):
                continue
            event_type = EVENT_TYPE_BY_SUCCESS_COMMAND_REASON.get(audit.reason)
            responsible = tuple(
                event
                for event in events
                if event.event_type is event_type
                and event.raw_object.object_id == audit.raw_object_id
                and _event_snapshot_values(event) == _audit_snapshot_values(audit)
                and event.occurred_at <= audit.occurred_at
            )
            if len(responsible) != 1:
                raise SourceSnapshotPersistenceError
            try:
                validate_atomic_command_audit(
                    audit,
                    responsible[0].raw_object,
                    responsible[0].snapshot,
                    None,
                    audit.reason,
                )
            except ValueError as exc:
                raise SourceSnapshotPersistenceError from exc

    @classmethod
    def _verify_observation(
        cls,
        scope: SourceKey,
        raw: dict[str, RawObjectMetadata],
        events: tuple[SourceSnapshotLifecycleEvent, ...],
        lifecycle: SourceSnapshotLifecycle,
        observation: SourceRuntimeObservation | None,
    ) -> None:
        if lifecycle.active_snapshot is None:
            if observation is not None:
                raise SourceSnapshotPersistenceError
            return
        transition = next(
            (
                event
                for event in reversed(events)
                if event.event_type
                in {
                    SourceSnapshotEventType.ACTIVATED,
                    SourceSnapshotEventType.ROLLED_BACK,
                }
            ),
            None,
        )
        if transition is None or observation is None:
            raise SourceSnapshotPersistenceError
        try:
            cls._validate_observation(scope, raw, transition, observation)
        except ValueError as exc:
            raise SourceSnapshotPersistenceError from exc

    @staticmethod
    def _validate_observation(
        scope: SourceKey,
        raw: dict[str, RawObjectMetadata],
        event: SourceSnapshotLifecycleEvent,
        observation: SourceRuntimeObservation,
    ) -> None:
        if not isinstance(observation, SourceRuntimeObservation):
            raise ValueError("runtime observation must be typed")
        if event.snapshot is None:
            raise ValueError("pointer event must identify a snapshot")
        metadata = raw.get(event.raw_object.object_id)
        if (
            metadata is None
            or metadata.reference() != event.raw_object
            or (observation.deployment_id, observation.source_id) != scope
            or observation.availability is not SourceAvailability.AVAILABLE
            or observation.active_snapshot_id != event.snapshot.snapshot_id
            or observation.observed_at != event.occurred_at
            or observation.retrieved_at != metadata.retrieved_at
            or observation.effective_from != metadata.effective_from
        ):
            raise ValueError("runtime observation does not match pointer event")

    def _fetch_bounded(
        self, query: str, params: tuple[object, ...], *, maximum: int
    ) -> tuple[Row, ...]:
        rows = self._connection.execute(query, params).fetchall()
        if type(rows) is not list or len(rows) > maximum:
            raise SourceSnapshotPersistenceError
        result: list[Row] = []
        for row in rows:
            if type(row) is not tuple:
                raise SourceSnapshotPersistenceError
            result.append(row)
        return tuple(result)


def _raw_from_row(row: Row) -> RawObjectMetadata:
    if len(row) != 10:
        raise SourceSnapshotPersistenceError
    return RawObjectMetadata.reconstitute(
        deployment_id=_string(row[0], "deployment_id"),
        source_id=_string(row[1], "source_id"),
        object_id=_string(row[2], "object_id"),
        original_name=_string(row[3], "original_name"),
        media_type=_string(row[4], "media_type"),
        charset=_string(row[5], "charset"),
        retrieved_at=_datetime(row[6], "retrieved_at"),
        effective_from=_optional_datetime(row[7], "effective_from"),
        byte_length=_integer(row[8], "byte_length", allow_zero=True),
        content_hash=_string(row[9], "content_hash"),
    )


def _relational_previous_snapshot(
    values: Row,
    scope: SourceKey,
    snapshots: dict[str, ParsedSourceSnapshot],
) -> tuple[SourceSnapshotRef | None, ParsedSourceSnapshot | None]:
    if len(values) != 4:
        raise SourceSnapshotPersistenceError
    if all(value is None for value in values):
        return None, None
    if any(value is None for value in values):
        raise SourceSnapshotPersistenceError
    previous_scope = (
        _string(values[0], "previous_deployment_id"),
        _string(values[1], "previous_source_id"),
    )
    if previous_scope != scope:
        raise SourceSnapshotPersistenceError
    reference = SourceSnapshotRef(
        _string(values[2], "previous_snapshot_id"),
        _string(values[3], "previous_snapshot_content_hash"),
    )
    snapshot = snapshots.get(reference.snapshot_id)
    if snapshot is None or snapshot.reference() != reference:
        raise SourceSnapshotPersistenceError
    return reference, snapshot


def _optional_snapshot_reference(values: Row, field: str) -> SourceSnapshotRef | None:
    if len(values) != 2:
        raise SourceSnapshotPersistenceError
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise SourceSnapshotPersistenceError
    return SourceSnapshotRef(
        _string(values[0], field),
        _string(values[1], field),
    )


def _event_validation_report(
    values: Row,
    snapshot: SourceSnapshotRef | None,
    validation: dict[str, _ValidationEvidence],
) -> ValidationReport | None:
    if len(values) != 3:
        raise SourceSnapshotPersistenceError
    if all(value is None for value in values):
        return None
    if any(value is None for value in values) or snapshot is None:
        raise SourceSnapshotPersistenceError
    report_hash = _string(values[0], "validation_report_hash")
    diff_hash = _string(values[1], "validation_diff_hash")
    passed = _boolean(values[2], "validation_passed")
    evidence = validation.get(snapshot.snapshot_id)
    if (
        evidence is None
        or evidence.snapshot != snapshot
        or evidence.report.content_hash != report_hash
        or evidence.report.diff.content_hash != diff_hash
        or evidence.report.passed is not passed
    ):
        raise SourceSnapshotPersistenceError
    return evidence.report


def _target_for_audit(
    audit: SnapshotCommandAuditRecord, source_set_id: str
) -> tuple[str, str]:
    if audit.operation in _SOURCE_OPERATIONS:
        return (
            SOURCE_TARGET_TYPE,
            source_authorization_target_id(
                audit.deployment_id, source_set_id, audit.source_id
            ),
        )
    if audit.operation not in _GOVERNANCE_OPERATIONS:
        raise SourceSnapshotPersistenceError
    if audit.snapshot_id is None or audit.snapshot_content_hash is None:
        raise SourceSnapshotPersistenceError
    return (
        SOURCE_SNAPSHOT_TARGET_TYPE,
        source_snapshot_authorization_target_id(
            audit.deployment_id,
            source_set_id,
            audit.source_id,
            audit.snapshot_id,
            audit.snapshot_content_hash,
        ),
    )


def _checked_raw_metadata(value: RawObjectMetadata) -> RawObjectMetadata:
    if type(value) is not RawObjectMetadata:
        raise ValueError("raw metadata must be typed")
    try:
        rebuilt = RawObjectMetadata.reconstitute(
            deployment_id=value.deployment_id,
            source_id=value.source_id,
            object_id=value.object_id,
            original_name=value.original_name,
            media_type=value.media_type,
            charset=value.charset,
            retrieved_at=value.retrieved_at,
            effective_from=value.effective_from,
            byte_length=value.byte_length,
            content_hash=value.content_hash,
        )
    except _DATABASE_OR_CORRUPTION_ERRORS:
        raise ValueError("raw metadata failed reconstruction") from None
    if rebuilt != value:
        raise ValueError("raw metadata failed reconstruction")
    return rebuilt


def _checked_snapshot(value: ParsedSourceSnapshot) -> ParsedSourceSnapshot:
    if type(value) is not ParsedSourceSnapshot:
        raise ValueError("snapshot must be typed")
    try:
        value.verify_integrity()
    except _DATABASE_OR_CORRUPTION_ERRORS:
        raise ValueError("snapshot failed integrity verification") from None
    return value


def _checked_event(value: SourceSnapshotLifecycleEvent) -> SourceSnapshotLifecycleEvent:
    if type(value) is not SourceSnapshotLifecycleEvent:
        raise ValueError("lifecycle event must be typed")
    try:
        rebuilt = SourceSnapshotLifecycleEvent(
            sequence=value.sequence,
            event_id=value.event_id,
            deployment_id=value.deployment_id,
            source_id=value.source_id,
            event_type=value.event_type,
            raw_object=value.raw_object,
            snapshot=value.snapshot,
            previous_active_snapshot=value.previous_active_snapshot,
            actor_id=value.actor_id,
            actor_type=value.actor_type,
            reason=value.reason,
            occurred_at=value.occurred_at,
            validation_report=value.validation_report,
        )
    except _DATABASE_OR_CORRUPTION_ERRORS:
        raise ValueError("lifecycle event failed reconstruction") from None
    if rebuilt != value:
        raise ValueError("lifecycle event failed reconstruction")
    return rebuilt


def _checked_audit(value: SnapshotCommandAuditRecord) -> SnapshotCommandAuditRecord:
    if type(value) is not SnapshotCommandAuditRecord:
        raise ValueError("command audit must be typed")
    try:
        rebuilt = SnapshotCommandAuditRecord(
            command_event_id=value.command_event_id,
            authorization_event_id=value.authorization_event_id,
            lifecycle_event_id=value.lifecycle_event_id,
            tenant_id=value.tenant_id,
            deployment_id=value.deployment_id,
            source_id=value.source_id,
            raw_object_id=value.raw_object_id,
            snapshot_id=value.snapshot_id,
            snapshot_content_hash=value.snapshot_content_hash,
            actor_id=value.actor_id,
            actor_type=value.actor_type,
            operation=value.operation,
            outcome=value.outcome,
            reason=value.reason,
            occurred_at=value.occurred_at,
        )
    except _DATABASE_OR_CORRUPTION_ERRORS:
        raise ValueError("command audit failed reconstruction") from None
    if rebuilt != value:
        raise ValueError("command audit failed reconstruction")
    return rebuilt


def _checked_observation(value: SourceRuntimeObservation) -> SourceRuntimeObservation:
    if type(value) is not SourceRuntimeObservation:
        raise ValueError("runtime observation must be typed")
    try:
        rebuilt = SourceRuntimeObservation(
            deployment_id=value.deployment_id,
            source_id=value.source_id,
            availability=value.availability,
            observed_at=value.observed_at,
            active_snapshot_id=value.active_snapshot_id,
            retrieved_at=value.retrieved_at,
            effective_from=value.effective_from,
        )
    except _DATABASE_OR_CORRUPTION_ERRORS:
        raise ValueError("runtime observation failed reconstruction") from None
    if rebuilt != value:
        raise ValueError("runtime observation failed reconstruction")
    return rebuilt


def _reference_values(
    reference: SourceSnapshotRef | None,
) -> tuple[str | None, str | None]:
    if reference is None:
        return None, None
    return reference.snapshot_id, reference.content_hash


def _event_snapshot_values(
    event: SourceSnapshotLifecycleEvent,
) -> tuple[str, str] | None:
    if event.snapshot is None:
        return None
    return event.snapshot.snapshot_id, event.snapshot.content_hash


def _audit_snapshot_values(
    audit: SnapshotCommandAuditRecord,
) -> tuple[str, str] | None:
    if audit.snapshot_id is None:
        return None
    assert audit.snapshot_content_hash is not None
    return audit.snapshot_id, audit.snapshot_content_hash


def _string(value: object, field: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{field} must be a string")
    return value


def _optional_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _string(value, field)


def _boolean(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{field} must be a boolean")
    return value


def _integer(value: object, field: str, *, allow_zero: bool = False) -> int:
    minimum = 0 if allow_zero else 1
    if type(value) is not int or value < minimum:
        raise ValueError(f"{field} must be an integer at least {minimum}")
    return value


def _datetime(value: object, field: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _optional_datetime(value: object, field: str) -> datetime | None:
    if value is None:
        return None
    return _datetime(value, field)


def _bytes(value: object, field: str) -> bytes:
    if type(value) is not bytes:
        raise ValueError(f"{field} must be bytes")
    return value
