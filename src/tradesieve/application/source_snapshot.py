"""Authorized ingest, parse, and validation of immutable source snapshots."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Never
from uuid import uuid4

from pydantic import ConfigDict, Field, model_validator

from tradesieve.application.auth import (
    ActorType,
    AuthorizedRequest,
    Operation,
    Role,
    Scope,
)
from tradesieve.application.contracts import (
    CanonicalDatetime,
    ContentHash,
    ContractModel,
    Reference,
    SemanticVersion,
)
from tradesieve.domain.source_registry import (
    SourceAvailability,
    SourceRegistration,
    SourceRuntimeObservation,
)
from tradesieve.domain.source_snapshot import (
    MAX_LIFECYCLE_EVENTS,
    MAX_REASON_LENGTH,
    SAFE_ID,
    ArtifactWriteOutcome,
    LifecycleActorType,
    LifecycleWriteOutcome,
    ParsedSourceSnapshot,
    RawObjectMetadata,
    RawObjectRef,
    RawObjectState,
    SnapshotCommandAuditRecord,
    SnapshotCommandOutcome,
    SnapshotCommandReason,
    SourceSnapshotEventType,
    SourceSnapshotLifecycle,
    SourceSnapshotLifecycleEvent,
    SourceSnapshotRef,
    SourceSnapshotState,
    ValidationReasonCode,
    ValidationReport,
    canonical_sha256,
    validate_lifecycle_projection,
    validate_snapshot,
)
from tradesieve.ports.source_registry import SourceRegistryRepository
from tradesieve.ports.source_snapshot import (
    SYNTHETIC_CHARSET,
    SYNTHETIC_MEDIA_TYPE,
    SYNTHETIC_PARSER_VERSION,
    SYNTHETIC_SCHEMA_ID,
    FiniteParserError,
    FiniteParserErrorCode,
    FiniteParserId,
    FiniteParserOutput,
    FiniteSourceParser,
    ImmutableRawObjectStore,
    SourceSnapshotRepository,
)

SOURCE_TARGET_TYPE = "source"
SOURCE_SNAPSHOT_TARGET_TYPE = "source_snapshot"


class _ValidationRetryLifecycleConflict:
    pass


_VALIDATION_RETRY_LIFECYCLE_CONFLICT = _ValidationRetryLifecycleConflict()


class SourceSnapshotApplicationError(Exception):
    pass


class SourceSnapshotAuthorizationBindingDenied(SourceSnapshotApplicationError):
    def __init__(self) -> None:
        super().__init__("source snapshot authorization binding denied")


class SourceSnapshotNotFound(SourceSnapshotApplicationError):
    def __init__(self) -> None:
        super().__init__("source snapshot not found")


class SourceSnapshotConflict(SourceSnapshotApplicationError):
    def __init__(self) -> None:
        super().__init__("source snapshot command conflict")


class SourceSnapshotUnavailable(SourceSnapshotApplicationError):
    def __init__(self) -> None:
        super().__init__("source snapshot state unavailable")


class SourceSnapshotRequestInvalid(SourceSnapshotApplicationError):
    def __init__(self) -> None:
        super().__init__("source snapshot request is invalid")


class SourceSnapshotParseRejected(SourceSnapshotApplicationError):
    def __init__(self, code: FiniteParserErrorCode) -> None:
        if not isinstance(code, FiniteParserErrorCode):
            raise ValueError("parse rejection code must be typed")
        self.code = code
        super().__init__("source snapshot parsing rejected")


class SourceSnapshotCreatorSeparationDenied(SourceSnapshotApplicationError):
    def __init__(self) -> None:
        super().__init__("source snapshot creator separation denied")


class SourceSnapshotValidationBlocked(SourceSnapshotApplicationError):
    def __init__(self) -> None:
        super().__init__("source snapshot validation blocks governance")


class RawObjectCommandResult(ContractModel):
    deployment_id: Reference
    source_id: Reference
    object_id: Reference
    content_hash: ContentHash
    byte_length: Annotated[int, Field(ge=0, le=1_048_576)]
    media_type: str
    charset: str
    retrieved_at: CanonicalDatetime
    effective_from: CanonicalDatetime | None
    state: RawObjectState


class ParsedSnapshotCommandResult(ContractModel):
    deployment_id: Reference
    source_id: Reference
    object_id: Reference
    raw_content_hash: ContentHash
    snapshot_id: Reference
    snapshot_content_hash: ContentHash
    parser_id: FiniteParserId
    parser_version: SemanticVersion
    schema_id: Reference
    declared_record_count: Annotated[int, Field(ge=0, le=512)]
    record_count: Annotated[int, Field(ge=0, le=512)]
    parsed_at: CanonicalDatetime
    state: SourceSnapshotState


class SnapshotValidationResult(ContractModel):
    deployment_id: Reference
    source_id: Reference
    snapshot_id: Reference
    snapshot_content_hash: ContentHash
    passed: bool
    reason_codes: Annotated[
        list[ValidationReasonCode], Field(max_length=len(ValidationReasonCode))
    ]
    previous_snapshot_id: Reference | None
    previous_snapshot_content_hash: ContentHash | None
    diff_content_hash: ContentHash
    added_record_count: Annotated[int, Field(ge=0, le=512)]
    removed_record_count: Annotated[int, Field(ge=0, le=512)]
    changed_record_count: Annotated[int, Field(ge=0, le=512)]
    validated_at: CanonicalDatetime
    state: SourceSnapshotState


class ImmutableSourceSnapshotIdentity(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_default=True,
        hide_input_in_errors=True,
    )

    snapshot_id: Reference
    content_hash: ContentHash

    @model_validator(mode="after")
    def require_derived_identity(self) -> ImmutableSourceSnapshotIdentity:
        try:
            SourceSnapshotRef(self.snapshot_id, self.content_hash)
        except (TypeError, ValueError):
            raise ValueError("source snapshot identity is invalid") from None
        return self


class SnapshotGovernanceCommandResult(ContractModel):
    deployment_id: Reference
    source_id: Reference
    snapshot_id: Reference
    snapshot_content_hash: ContentHash
    event_type: SourceSnapshotEventType
    state: SourceSnapshotState
    active_snapshot_id: Reference | None
    active_snapshot_content_hash: ContentHash | None
    previous_active_snapshot_id: Reference | None
    previous_active_snapshot_content_hash: ContentHash | None
    occurred_at: CanonicalDatetime


def source_authorization_target_id(
    deployment_id: str, source_set_id: str, source_id: str
) -> str:
    """Bind authorization to one configured source without concatenation collisions."""

    for value in (deployment_id, source_set_id, source_id):
        if not isinstance(value, str) or SAFE_ID.fullmatch(value) is None:
            raise ValueError("source authorization target components must be safe IDs")
    digest = canonical_sha256(
        {
            "deployment_id": deployment_id,
            "source_id": source_id,
            "source_set_id": source_set_id,
        }
    )
    return f"source-{digest.removeprefix('sha256:')}"


def source_snapshot_authorization_target_id(
    deployment_id: str,
    source_set_id: str,
    source_id: str,
    snapshot_id: str,
    content_hash: str,
) -> str:
    """Bind governance authorization to one exact immutable source snapshot."""

    for value in (deployment_id, source_set_id, source_id):
        if not isinstance(value, str) or SAFE_ID.fullmatch(value) is None:
            raise ValueError("source snapshot target components must be safe IDs")
    try:
        reference = SourceSnapshotRef(snapshot_id, content_hash)
    except (TypeError, ValueError):
        raise ValueError("source snapshot target identity is invalid") from None
    digest = canonical_sha256(
        {
            "content_hash": reference.content_hash,
            "deployment_id": deployment_id,
            "snapshot_id": reference.snapshot_id,
            "source_id": source_id,
            "source_set_id": source_set_id,
        }
    )
    return f"source-snapshot-{digest.removeprefix('sha256:')}"


class SourceSnapshotService:
    """Execute bounded source commands against exact authorization bindings."""

    def __init__(
        self,
        repository: SourceSnapshotRepository,
        object_store: ImmutableRawObjectStore,
        source_registry: SourceRegistryRepository,
        parser: FiniteSourceParser,
        *,
        deployment_id: str,
        source_set_id: str,
        control_tenant_id: str,
        expected_schema_id: str = SYNTHETIC_SCHEMA_ID,
        clock: Callable[[], datetime] | None = None,
        event_id_factory: Callable[[], str] | None = None,
        command_id_factory: Callable[[], str] | None = None,
    ) -> None:
        for value in (
            deployment_id,
            source_set_id,
            control_tenant_id,
            expected_schema_id,
        ):
            if not isinstance(value, str) or SAFE_ID.fullmatch(value) is None:
                raise ValueError("source snapshot configuration contains an invalid ID")
        try:
            parser_binding = (
                parser.parser_id,
                parser.parser_version,
                parser.media_type,
                parser.charset,
            )
        except Exception as exc:
            raise ValueError("source parser binding is invalid") from exc
        if (
            parser_binding
            != (
                FiniteParserId.SYNTHETIC_JSON_V1,
                SYNTHETIC_PARSER_VERSION,
                SYNTHETIC_MEDIA_TYPE,
                SYNTHETIC_CHARSET,
            )
            or expected_schema_id != SYNTHETIC_SCHEMA_ID
        ):
            raise ValueError("source parser binding is outside the finite inventory")
        self._repository = repository
        self._object_store = object_store
        self._source_registry = source_registry
        self._parser = parser
        self._deployment_id = deployment_id
        self._source_set_id = source_set_id
        self._control_tenant_id = control_tenant_id
        self._expected_schema_id = expected_schema_id
        self._clock = clock or (lambda: datetime.now(UTC))
        self._event_id_factory = event_id_factory or (
            lambda: f"source-event-{uuid4().hex}"
        )
        self._command_id_factory = command_id_factory or (
            lambda: f"source-command-{uuid4().hex}"
        )

    def ingest(
        self,
        authorized: AuthorizedRequest,
        *,
        source_id: str,
        original_name: str,
        media_type: str,
        charset: str,
        retrieved_at: datetime,
        effective_from: datetime | None,
        content: bytes,
    ) -> RawObjectCommandResult:
        self._require_authorized(
            authorized, Operation.SOURCE_SNAPSHOT_INGEST, source_id
        )
        self._require_registration(source_id)
        try:
            metadata = RawObjectMetadata.from_bytes(
                deployment_id=self._deployment_id,
                source_id=source_id,
                original_name=original_name,
                media_type=media_type,
                charset=charset,
                retrieved_at=retrieved_at,
                effective_from=effective_from,
                content=content,
            )
        except (TypeError, ValueError):
            raise SourceSnapshotRequestInvalid from None
        now = self._now()
        if metadata.retrieved_at > now:
            raise SourceSnapshotRequestInvalid from None
        reference = metadata.reference()
        try:
            store_outcome = self._object_store.put_exact(metadata, content)
        except Exception:
            raise SourceSnapshotUnavailable from None
        if store_outcome is ArtifactWriteOutcome.CONFLICT:
            self._record_failure(
                authorized,
                source_id,
                reference,
                None,
                SnapshotCommandReason.CONFLICT,
                now,
            )
            raise SourceSnapshotConflict from None
        if store_outcome not in {
            ArtifactWriteOutcome.APPLIED,
            ArtifactWriteOutcome.IDEMPOTENT,
        }:
            raise SourceSnapshotUnavailable from None
        events, lifecycle = self._snapshot(source_id)
        self._require_command_time(now, events)
        retrieved = self._event(
            authorized,
            source_id=source_id,
            sequence=self._next_sequence(events),
            event_type=SourceSnapshotEventType.RETRIEVED,
            raw_object=reference,
            snapshot=None,
            report=None,
            now=now,
        )
        try:
            outcome = self._repository.save_retrieval_atomic(
                metadata,
                retrieved,
                self._audit_pair(authorized, retrieved),
                self._audit_pair(authorized, retrieved, idempotent=True),
            )
        except Exception:
            raise SourceSnapshotUnavailable from None
        if outcome is ArtifactWriteOutcome.CONFLICT:
            self._record_failure(
                authorized,
                source_id,
                reference,
                None,
                SnapshotCommandReason.CONFLICT,
                now,
            )
            raise SourceSnapshotConflict from None
        if outcome not in {
            ArtifactWriteOutcome.APPLIED,
            ArtifactWriteOutcome.IDEMPOTENT,
        }:
            raise SourceSnapshotUnavailable from None
        events, lifecycle = self._snapshot(source_id)
        self._require_command_time(now, events)
        state = self._raw_state(lifecycle, reference)
        if state in {
            RawObjectState.RETRIEVED,
            RawObjectState.QUARANTINED,
            RawObjectState.PARSED,
        }:
            quarantined = self._event(
                authorized,
                source_id=source_id,
                sequence=self._next_sequence(events),
                event_type=SourceSnapshotEventType.QUARANTINED,
                raw_object=reference,
                snapshot=None,
                report=None,
                now=now,
            )
            try:
                quarantine_outcome = self._repository.append_lifecycle_atomic(
                    quarantined,
                    self._audit_pair(authorized, quarantined),
                    self._audit_pair(authorized, quarantined, idempotent=True),
                )
            except Exception:
                raise SourceSnapshotUnavailable from None
            if quarantine_outcome is LifecycleWriteOutcome.CONFLICT:
                self._record_failure(
                    authorized,
                    source_id,
                    reference,
                    None,
                    SnapshotCommandReason.CONFLICT,
                    now,
                )
                raise SourceSnapshotConflict from None
            if quarantine_outcome not in {
                LifecycleWriteOutcome.APPLIED,
                LifecycleWriteOutcome.IDEMPOTENT,
            }:
                raise SourceSnapshotUnavailable from None
            _, lifecycle = self._snapshot(source_id)
            state = self._raw_state(lifecycle, reference)
        if state not in {RawObjectState.QUARANTINED, RawObjectState.PARSED}:
            raise SourceSnapshotUnavailable from None
        return self._raw_result(metadata, state)

    def parse(
        self,
        authorized: AuthorizedRequest,
        *,
        source_id: str,
        object_id: str,
    ) -> ParsedSnapshotCommandResult:
        self._require_authorized(authorized, Operation.SOURCE_SNAPSHOT_PARSE, source_id)
        self._require_registration(source_id)
        metadata = self._raw_metadata(source_id, object_id)
        events, lifecycle = self._snapshot(source_id)
        retry = self._parsed_retry(metadata, events, lifecycle)
        if retry is not None:
            snapshot, state = retry
            now = self._now()
            self._require_command_time(now, events)
            parsed = self._event(
                authorized,
                source_id=source_id,
                sequence=self._next_sequence(events),
                event_type=SourceSnapshotEventType.PARSED,
                raw_object=metadata.reference(),
                snapshot=snapshot.reference(),
                report=None,
                now=now,
            )
            try:
                outcome = self._repository.save_parsed_snapshot_atomic(
                    snapshot,
                    parsed,
                    self._audit_pair(authorized, parsed),
                    self._audit_pair(authorized, parsed, idempotent=True),
                )
            except Exception:
                raise SourceSnapshotUnavailable from None
            if outcome is ArtifactWriteOutcome.CONFLICT:
                self._record_failure(
                    authorized,
                    source_id,
                    metadata.reference(),
                    snapshot.reference(),
                    SnapshotCommandReason.CONFLICT,
                    now,
                )
                raise SourceSnapshotConflict from None
            if outcome is not ArtifactWriteOutcome.IDEMPOTENT:
                raise SourceSnapshotUnavailable from None
            return self._parsed_result(snapshot, state)
        raw_state = self._raw_state(lifecycle, metadata.reference())
        now = self._now()
        self._require_command_time(now, events)
        if raw_state is not RawObjectState.QUARANTINED:
            self._record_failure(
                authorized,
                source_id,
                metadata.reference(),
                None,
                SnapshotCommandReason.INVALID_LIFECYCLE,
                now,
            )
            raise SourceSnapshotConflict from None
        if (
            metadata.media_type != self._parser.media_type
            or metadata.charset != self._parser.charset
        ):
            self._record_failure(
                authorized,
                source_id,
                metadata.reference(),
                None,
                SnapshotCommandReason.MEDIA_BINDING_DENIED,
                now,
            )
            raise SourceSnapshotParseRejected(
                FiniteParserErrorCode.INVALID_INPUT
            ) from None
        try:
            content = self._object_store.get_verified(metadata.reference())
        except Exception:
            raise SourceSnapshotUnavailable from None
        try:
            output = self._parser.parse(content)
        except FiniteParserError as exc:
            self._record_failure(
                authorized,
                source_id,
                metadata.reference(),
                None,
                SnapshotCommandReason.PARSER_REJECTED,
                now,
            )
            raise SourceSnapshotParseRejected(exc.code) from None
        except Exception:
            raise SourceSnapshotUnavailable from None
        if not isinstance(output, FiniteParserOutput):
            raise SourceSnapshotUnavailable from None
        if output.schema_id != self._expected_schema_id:
            self._record_failure(
                authorized,
                source_id,
                metadata.reference(),
                None,
                SnapshotCommandReason.PARSER_REJECTED,
                now,
            )
            raise SourceSnapshotParseRejected(
                FiniteParserErrorCode.SCHEMA_MISMATCH
            ) from None
        try:
            snapshot = ParsedSourceSnapshot.create(
                raw_metadata=metadata,
                parser_id=self._parser.parser_id,
                parser_version=self._parser.parser_version,
                schema_id=output.schema_id,
                declared_record_count=output.declared_record_count,
                parsed_at=now,
                records=output.records,
            )
        except (TypeError, ValueError):
            raise SourceSnapshotUnavailable from None
        parsed = self._event(
            authorized,
            source_id=source_id,
            sequence=self._next_sequence(events),
            event_type=SourceSnapshotEventType.PARSED,
            raw_object=metadata.reference(),
            snapshot=snapshot.reference(),
            report=None,
            now=now,
        )
        try:
            outcome = self._repository.save_parsed_snapshot_atomic(
                snapshot,
                parsed,
                self._audit_pair(authorized, parsed),
                self._audit_pair(authorized, parsed, idempotent=True),
            )
        except Exception:
            raise SourceSnapshotUnavailable from None
        if outcome is ArtifactWriteOutcome.CONFLICT:
            self._record_failure(
                authorized,
                source_id,
                metadata.reference(),
                snapshot.reference(),
                SnapshotCommandReason.CONFLICT,
                now,
            )
            raise SourceSnapshotConflict from None
        if outcome not in {
            ArtifactWriteOutcome.APPLIED,
            ArtifactWriteOutcome.IDEMPOTENT,
        }:
            raise SourceSnapshotUnavailable from None
        return self._parsed_result(snapshot, SourceSnapshotState.PARSED)

    def validate(
        self,
        authorized: AuthorizedRequest,
        *,
        source_id: str,
        snapshot_id: str,
    ) -> SnapshotValidationResult:
        self._require_authorized(
            authorized, Operation.SOURCE_SNAPSHOT_VALIDATE, source_id
        )
        self._require_registration(source_id)
        snapshot = self._parsed_snapshot(source_id, snapshot_id)
        events, lifecycle = self._snapshot(source_id)
        retry = self._validation_retry(snapshot, events, lifecycle)
        if isinstance(retry, _ValidationRetryLifecycleConflict):
            now = self._now()
            self._require_command_time(now, events)
            self._record_failure(
                authorized,
                source_id,
                snapshot.raw_object,
                snapshot.reference(),
                SnapshotCommandReason.CONFLICT,
                now,
            )
            raise SourceSnapshotConflict from None
        if retry is not None:
            report, retry_state, event_type = retry
            now = self._now()
            self._require_command_time(now, events)
            event = self._event(
                authorized,
                source_id=source_id,
                sequence=self._next_sequence(events),
                event_type=event_type,
                raw_object=snapshot.raw_object,
                snapshot=snapshot.reference(),
                report=report,
                now=now,
            )
            try:
                outcome = self._repository.append_lifecycle_atomic(
                    event,
                    self._audit_pair(authorized, event),
                    self._audit_pair(authorized, event, idempotent=True),
                )
            except Exception:
                raise SourceSnapshotUnavailable from None
            if outcome is LifecycleWriteOutcome.CONFLICT:
                self._record_failure(
                    authorized,
                    source_id,
                    snapshot.raw_object,
                    snapshot.reference(),
                    SnapshotCommandReason.CONFLICT,
                    now,
                )
                raise SourceSnapshotConflict from None
            if outcome is not LifecycleWriteOutcome.IDEMPOTENT:
                raise SourceSnapshotUnavailable from None
            return self._validation_result(snapshot, report, retry_state)
        state = lifecycle.state_for(snapshot.reference())
        now = self._now()
        self._require_command_time(now, events)
        if state is not SourceSnapshotState.PARSED:
            self._record_failure(
                authorized,
                source_id,
                snapshot.raw_object,
                snapshot.reference(),
                SnapshotCommandReason.INVALID_LIFECYCLE,
                now,
            )
            raise SourceSnapshotConflict from None
        previous = self._active_snapshot(source_id, lifecycle)
        try:
            report = validate_snapshot(
                snapshot,
                expected_schema_id=self._expected_schema_id,
                previous_accepted=previous,
                validated_at=now,
            )
        except (TypeError, ValueError):
            raise SourceSnapshotUnavailable from None
        event_type = (
            SourceSnapshotEventType.VALIDATED
            if report.passed
            else SourceSnapshotEventType.VALIDATION_FAILED
        )
        event = self._event(
            authorized,
            source_id=source_id,
            sequence=self._next_sequence(events),
            event_type=event_type,
            raw_object=snapshot.raw_object,
            snapshot=snapshot.reference(),
            report=report,
            now=now,
        )
        try:
            outcome = self._repository.append_lifecycle_atomic(
                event,
                self._audit_pair(authorized, event),
                self._audit_pair(authorized, event, idempotent=True),
            )
        except Exception:
            raise SourceSnapshotUnavailable from None
        if outcome is LifecycleWriteOutcome.CONFLICT:
            self._record_failure(
                authorized,
                source_id,
                snapshot.raw_object,
                snapshot.reference(),
                SnapshotCommandReason.CONFLICT,
                now,
            )
            raise SourceSnapshotConflict from None
        if outcome not in {
            LifecycleWriteOutcome.APPLIED,
            LifecycleWriteOutcome.IDEMPOTENT,
        }:
            raise SourceSnapshotUnavailable from None
        result_state = (
            SourceSnapshotState.VALIDATED
            if report.passed
            else SourceSnapshotState.QUARANTINED
        )
        return self._validation_result(snapshot, report, result_state)

    def approve(
        self,
        authorized: AuthorizedRequest,
        *,
        source_id: str,
        identity: ImmutableSourceSnapshotIdentity,
        reason: str,
    ) -> SnapshotGovernanceCommandResult:
        self._require_authorized(
            authorized,
            Operation.SOURCE_SNAPSHOT_APPROVE,
            source_id,
            identity=identity,
        )
        checked_reason = self._require_reason(reason)
        self._require_registration(source_id)
        snapshot = self._snapshot_for_identity(source_id, identity)
        events, lifecycle = self._snapshot(source_id)
        now = self._now()
        self._require_command_time(now, events)
        self._require_creator_separation(authorized, snapshot, events, lifecycle, now)
        existing = self._unique_governance_event(
            events, snapshot.reference(), SourceSnapshotEventType.APPROVED
        )
        if existing is not None:
            if existing.reason != checked_reason:
                self._governance_conflict(authorized, snapshot, now)
            return self._retry_governance_event(
                authorized, snapshot, existing, events, lifecycle, now
            )
        if lifecycle.state_for(snapshot.reference()) is SourceSnapshotState.PARSED:
            self._governance_conflict(authorized, snapshot, now)
        validation = self._verified_validation_event(snapshot, events)
        if validation.event_type is SourceSnapshotEventType.VALIDATION_FAILED:
            self._record_failure(
                authorized,
                source_id,
                snapshot.raw_object,
                snapshot.reference(),
                SnapshotCommandReason.VALIDATION_BLOCKED,
                now,
            )
            raise SourceSnapshotValidationBlocked from None
        if (
            lifecycle.state_for(snapshot.reference())
            is not SourceSnapshotState.VALIDATED
        ):
            self._governance_conflict(authorized, snapshot, now)
        event = self._event(
            authorized,
            source_id=source_id,
            sequence=self._next_sequence(events),
            event_type=SourceSnapshotEventType.APPROVED,
            raw_object=snapshot.raw_object,
            snapshot=snapshot.reference(),
            report=None,
            now=now,
            reason=checked_reason,
        )
        try:
            outcome = self._repository.append_lifecycle_atomic(
                event,
                self._audit_pair(authorized, event),
                self._audit_pair(authorized, event, idempotent=True),
            )
        except Exception:
            raise SourceSnapshotUnavailable from None
        if outcome is LifecycleWriteOutcome.CONFLICT:
            self._governance_conflict(authorized, snapshot, now)
        if outcome is LifecycleWriteOutcome.IDEMPOTENT:
            updated_events, updated_lifecycle = self._snapshot(source_id)
            responsible = self._responsible_event(event, updated_events)
            return self._governance_result(snapshot, responsible, updated_lifecycle)
        if outcome is not LifecycleWriteOutcome.APPLIED:
            raise SourceSnapshotUnavailable from None
        _, updated = self._snapshot(source_id)
        return self._governance_result(snapshot, event, updated)

    def activate(
        self,
        authorized: AuthorizedRequest,
        *,
        source_id: str,
        identity: ImmutableSourceSnapshotIdentity,
        reason: str,
    ) -> SnapshotGovernanceCommandResult:
        self._require_authorized(
            authorized,
            Operation.SOURCE_SNAPSHOT_ACTIVATE,
            source_id,
            identity=identity,
        )
        checked_reason = self._require_reason(reason)
        self._require_registration(source_id)
        snapshot = self._snapshot_for_identity(source_id, identity)
        events, lifecycle = self._snapshot(source_id)
        now = self._now()
        self._require_command_time(now, events)
        self._require_creator_separation(authorized, snapshot, events, lifecycle, now)
        existing = self._unique_governance_event(
            events, snapshot.reference(), SourceSnapshotEventType.ACTIVATED
        )
        if existing is not None:
            last_pointer_event = next(
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
            if (
                existing.reason != checked_reason
                or lifecycle.active_snapshot != snapshot.reference()
                or last_pointer_event != existing
            ):
                self._governance_conflict(authorized, snapshot, now)
            return self._retry_governance_event(
                authorized, snapshot, existing, events, lifecycle, now
            )
        if lifecycle.state_for(snapshot.reference()) is SourceSnapshotState.PARSED:
            self._governance_conflict(authorized, snapshot, now)
        validation = self._verified_validation_event(snapshot, events)
        if validation.event_type is SourceSnapshotEventType.VALIDATION_FAILED:
            self._record_failure(
                authorized,
                source_id,
                snapshot.raw_object,
                snapshot.reference(),
                SnapshotCommandReason.VALIDATION_BLOCKED,
                now,
            )
            raise SourceSnapshotValidationBlocked from None
        report = validation.validation_report
        if report is None:
            raise SourceSnapshotUnavailable from None
        if (
            lifecycle.state_for(snapshot.reference())
            is not SourceSnapshotState.APPROVED
            or report.diff.previous_snapshot != lifecycle.active_snapshot
        ):
            self._governance_conflict(authorized, snapshot, now)
        event = self._event(
            authorized,
            source_id=source_id,
            sequence=self._next_sequence(events),
            event_type=SourceSnapshotEventType.ACTIVATED,
            raw_object=snapshot.raw_object,
            snapshot=snapshot.reference(),
            report=None,
            now=now,
            reason=checked_reason,
            previous_active_snapshot=lifecycle.active_snapshot,
        )
        return self._write_pointer_transition(authorized, snapshot, event)

    def rollback(
        self,
        authorized: AuthorizedRequest,
        *,
        source_id: str,
        identity: ImmutableSourceSnapshotIdentity,
        reason: str,
    ) -> SnapshotGovernanceCommandResult:
        self._require_authorized(
            authorized,
            Operation.SOURCE_SNAPSHOT_ROLLBACK,
            source_id,
            identity=identity,
        )
        checked_reason = self._require_reason(reason)
        self._require_registration(source_id)
        snapshot = self._snapshot_for_identity(source_id, identity)
        events, lifecycle = self._snapshot(source_id)
        now = self._now()
        self._require_command_time(now, events)
        self._require_creator_separation(authorized, snapshot, events, lifecycle, now)
        validation = self._verified_validation_event(snapshot, events)
        if validation.event_type is not SourceSnapshotEventType.VALIDATED:
            raise SourceSnapshotUnavailable from None
        last_pointer_event = next(
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
        if lifecycle.active_snapshot == snapshot.reference():
            if (
                last_pointer_event is None
                or last_pointer_event.event_type
                is not SourceSnapshotEventType.ROLLED_BACK
                or last_pointer_event.snapshot != snapshot.reference()
                or last_pointer_event.reason != checked_reason
            ):
                self._governance_conflict(authorized, snapshot, now)
            return self._retry_governance_event(
                authorized,
                snapshot,
                last_pointer_event,
                events,
                lifecycle,
                now,
            )
        if (
            lifecycle.active_snapshot is None
            or snapshot.reference() not in lifecycle.activated_snapshots
            or lifecycle.state_for(snapshot.reference())
            not in {SourceSnapshotState.SUPERSEDED, SourceSnapshotState.ROLLED_BACK}
        ):
            self._governance_conflict(authorized, snapshot, now)
        event = self._event(
            authorized,
            source_id=source_id,
            sequence=self._next_sequence(events),
            event_type=SourceSnapshotEventType.ROLLED_BACK,
            raw_object=snapshot.raw_object,
            snapshot=snapshot.reference(),
            report=None,
            now=now,
            reason=checked_reason,
            previous_active_snapshot=lifecycle.active_snapshot,
        )
        return self._write_pointer_transition(authorized, snapshot, event)

    def _require_authorized(
        self,
        authorized: AuthorizedRequest,
        operation: Operation,
        source_id: str,
        *,
        identity: ImmutableSourceSnapshotIdentity | None = None,
    ) -> None:
        try:
            actor = authorized.context.actor
            governance = operation in {
                Operation.SOURCE_SNAPSHOT_APPROVE,
                Operation.SOURCE_SNAPSHOT_ACTIVATE,
                Operation.SOURCE_SNAPSHOT_ROLLBACK,
            }
            if governance:
                if not isinstance(identity, ImmutableSourceSnapshotIdentity):
                    raise ValueError("immutable snapshot identity is required")
                expected_target = source_snapshot_authorization_target_id(
                    self._deployment_id,
                    self._source_set_id,
                    source_id,
                    identity.snapshot_id,
                    identity.content_hash,
                )
                expected_target_type = SOURCE_SNAPSHOT_TARGET_TYPE
                actor_types = {ActorType.HUMAN}
                required_scope = Scope.SOURCE_APPROVE
                allowed_roles = {Role.SOURCE_APPROVER, Role.COMPLIANCE_OWNER}
            else:
                if identity is not None:
                    raise ValueError("immutable snapshot identity is unexpected")
                expected_target = source_authorization_target_id(
                    self._deployment_id, self._source_set_id, source_id
                )
                expected_target_type = SOURCE_TARGET_TYPE
                actor_types = {ActorType.HUMAN, ActorType.SERVICE}
                required_scope = Scope.SOURCE_OPERATE
                allowed_roles = {Role.SOURCE_OPERATOR}
            valid = (
                isinstance(authorized, AuthorizedRequest)
                and authorized.operation is operation
                and authorized.context.tenant_id == self._control_tenant_id
                and actor.tenant_id == self._control_tenant_id
                and authorized.target.tenant_id == self._control_tenant_id
                and authorized.target.object_type == expected_target_type
                and authorized.target.object_id == expected_target
                and actor.actor_type in actor_types
                and required_scope in actor.scopes
                and bool(actor.roles & allowed_roles)
                and isinstance(actor.subject, str)
                and SAFE_ID.fullmatch(actor.subject) is not None
                and isinstance(authorized.audit_event_id, str)
                and SAFE_ID.fullmatch(authorized.audit_event_id) is not None
            )
        except (AttributeError, TypeError, ValueError):
            valid = False
        if not valid:
            raise SourceSnapshotAuthorizationBindingDenied from None

    @staticmethod
    def _require_reason(reason: str) -> str:
        if (
            not isinstance(reason, str)
            or not reason
            or len(reason) > MAX_REASON_LENGTH
            or reason != reason.strip()
            or not reason.isprintable()
        ):
            raise SourceSnapshotRequestInvalid from None
        return reason

    def _snapshot_for_identity(
        self, source_id: str, identity: ImmutableSourceSnapshotIdentity
    ) -> ParsedSourceSnapshot:
        if not isinstance(identity, ImmutableSourceSnapshotIdentity):
            raise SourceSnapshotNotFound from None
        snapshot = self._parsed_snapshot(source_id, identity.snapshot_id)
        try:
            expected = SourceSnapshotRef(identity.snapshot_id, identity.content_hash)
            valid = snapshot.reference() == expected
        except (AttributeError, TypeError, ValueError):
            valid = False
        if not valid:
            raise SourceSnapshotNotFound from None
        return snapshot

    def _require_creator_separation(
        self,
        authorized: AuthorizedRequest,
        snapshot: ParsedSourceSnapshot,
        events: tuple[SourceSnapshotLifecycleEvent, ...],
        lifecycle: SourceSnapshotLifecycle,
        now: datetime,
    ) -> None:
        reference = snapshot.reference()
        parsed = tuple(
            event
            for event in events
            if event.event_type is SourceSnapshotEventType.PARSED
            and event.snapshot == reference
        )
        creator = lifecycle.creator_for(reference)
        if (
            len(parsed) != 1
            or parsed[0].raw_object != snapshot.raw_object
            or parsed[0].snapshot != reference
            or creator is None
            or parsed[0].actor_id != creator
        ):
            raise SourceSnapshotUnavailable from None
        if authorized.context.actor.subject == creator:
            self._record_failure(
                authorized,
                snapshot.source_id,
                snapshot.raw_object,
                reference,
                SnapshotCommandReason.CREATOR_SEPARATION_DENIED,
                now,
            )
            raise SourceSnapshotCreatorSeparationDenied from None

    def _verified_validation_event(
        self,
        snapshot: ParsedSourceSnapshot,
        events: tuple[SourceSnapshotLifecycleEvent, ...],
    ) -> SourceSnapshotLifecycleEvent:
        reference = snapshot.reference()
        linked = tuple(
            event
            for event in events
            if event.event_type
            in {
                SourceSnapshotEventType.VALIDATED,
                SourceSnapshotEventType.VALIDATION_FAILED,
            }
            and event.snapshot == reference
        )
        if len(linked) != 1:
            raise SourceSnapshotUnavailable from None
        event = linked[0]
        report = event.validation_report
        try:
            valid = (
                report is not None
                and event.raw_object == snapshot.raw_object
                and report.snapshot == reference
                and report.diff.new_snapshot == reference
                and report.expected_schema_id == self._expected_schema_id
                and report.recomputed_content_hash() == report.content_hash
                and report.diff.recomputed_content_hash() == report.diff.content_hash
                and report.passed
                == (event.event_type is SourceSnapshotEventType.VALIDATED)
            )
        except (AttributeError, TypeError, ValueError):
            valid = False
        if not valid:
            raise SourceSnapshotUnavailable from None
        return event

    @staticmethod
    def _unique_governance_event(
        events: tuple[SourceSnapshotLifecycleEvent, ...],
        reference: SourceSnapshotRef,
        event_type: SourceSnapshotEventType,
    ) -> SourceSnapshotLifecycleEvent | None:
        linked = tuple(
            event
            for event in events
            if event.event_type is event_type and event.snapshot == reference
        )
        if len(linked) > 1:
            raise SourceSnapshotUnavailable from None
        return linked[0] if linked else None

    @staticmethod
    def _responsible_event(
        attempted: SourceSnapshotLifecycleEvent,
        events: tuple[SourceSnapshotLifecycleEvent, ...],
    ) -> SourceSnapshotLifecycleEvent:
        linked = tuple(
            event
            for event in events
            if event.event_type is attempted.event_type
            and event.raw_object == attempted.raw_object
            and event.snapshot == attempted.snapshot
            and event.previous_active_snapshot == attempted.previous_active_snapshot
            and event.validation_report == attempted.validation_report
            and event.reason == attempted.reason
        )
        if not linked:
            raise SourceSnapshotUnavailable from None
        return linked[-1]

    def _retry_governance_event(
        self,
        authorized: AuthorizedRequest,
        snapshot: ParsedSourceSnapshot,
        responsible: SourceSnapshotLifecycleEvent,
        events: tuple[SourceSnapshotLifecycleEvent, ...],
        lifecycle: SourceSnapshotLifecycle,
        now: datetime,
    ) -> SnapshotGovernanceCommandResult:
        attempted = self._event(
            authorized,
            source_id=snapshot.source_id,
            sequence=self._next_sequence(events),
            event_type=responsible.event_type,
            raw_object=snapshot.raw_object,
            snapshot=snapshot.reference(),
            report=None,
            now=now,
            reason=responsible.reason,
            previous_active_snapshot=responsible.previous_active_snapshot,
        )
        try:
            if responsible.event_type is SourceSnapshotEventType.APPROVED:
                outcome = self._repository.append_lifecycle_atomic(
                    attempted,
                    self._audit_pair(authorized, attempted),
                    self._audit_pair(authorized, attempted, idempotent=True),
                )
            else:
                outcome = self._repository.activate_or_rollback_atomic(
                    attempted,
                    self._observation(snapshot, attempted),
                    self._audit_pair(authorized, attempted),
                    self._audit_pair(authorized, attempted, idempotent=True),
                )
        except Exception:
            raise SourceSnapshotUnavailable from None
        if outcome is LifecycleWriteOutcome.CONFLICT:
            self._governance_conflict(authorized, snapshot, now)
        if outcome is not LifecycleWriteOutcome.IDEMPOTENT:
            raise SourceSnapshotUnavailable from None
        updated_events, updated_lifecycle = self._snapshot(snapshot.source_id)
        original = self._responsible_event(attempted, updated_events)
        return self._governance_result(snapshot, original, updated_lifecycle)

    def _write_pointer_transition(
        self,
        authorized: AuthorizedRequest,
        snapshot: ParsedSourceSnapshot,
        event: SourceSnapshotLifecycleEvent,
    ) -> SnapshotGovernanceCommandResult:
        try:
            outcome = self._repository.activate_or_rollback_atomic(
                event,
                self._observation(snapshot, event),
                self._audit_pair(authorized, event),
                self._audit_pair(authorized, event, idempotent=True),
            )
        except Exception:
            raise SourceSnapshotUnavailable from None
        if outcome is LifecycleWriteOutcome.CONFLICT:
            self._governance_conflict(authorized, snapshot, event.occurred_at)
        if outcome is LifecycleWriteOutcome.IDEMPOTENT:
            updated_events, updated_lifecycle = self._snapshot(snapshot.source_id)
            responsible = self._responsible_event(event, updated_events)
            return self._governance_result(snapshot, responsible, updated_lifecycle)
        if outcome is not LifecycleWriteOutcome.APPLIED:
            raise SourceSnapshotUnavailable from None
        _, updated = self._snapshot(snapshot.source_id)
        return self._governance_result(snapshot, event, updated)

    def _observation(
        self,
        snapshot: ParsedSourceSnapshot,
        event: SourceSnapshotLifecycleEvent,
    ) -> SourceRuntimeObservation:
        metadata = self._raw_metadata(snapshot.source_id, snapshot.raw_object.object_id)
        if metadata.reference() != snapshot.raw_object:
            raise SourceSnapshotUnavailable from None
        try:
            return SourceRuntimeObservation(
                deployment_id=self._deployment_id,
                source_id=snapshot.source_id,
                availability=SourceAvailability.AVAILABLE,
                observed_at=event.occurred_at,
                active_snapshot_id=snapshot.snapshot_id,
                retrieved_at=metadata.retrieved_at,
                effective_from=metadata.effective_from,
            )
        except (TypeError, ValueError):
            raise SourceSnapshotUnavailable from None

    def _governance_conflict(
        self,
        authorized: AuthorizedRequest,
        snapshot: ParsedSourceSnapshot,
        now: datetime,
    ) -> Never:
        self._record_failure(
            authorized,
            snapshot.source_id,
            snapshot.raw_object,
            snapshot.reference(),
            SnapshotCommandReason.CONFLICT,
            now,
        )
        raise SourceSnapshotConflict from None

    @staticmethod
    def _governance_result(
        snapshot: ParsedSourceSnapshot,
        event: SourceSnapshotLifecycleEvent,
        lifecycle: SourceSnapshotLifecycle,
    ) -> SnapshotGovernanceCommandResult:
        reference = snapshot.reference()
        if event.snapshot != reference:
            raise SourceSnapshotUnavailable from None
        state = lifecycle.state_for(reference)
        if state is None:
            raise SourceSnapshotUnavailable from None
        active = lifecycle.active_snapshot
        previous = event.previous_active_snapshot
        try:
            return SnapshotGovernanceCommandResult(
                deployment_id=snapshot.deployment_id,
                source_id=snapshot.source_id,
                snapshot_id=snapshot.snapshot_id,
                snapshot_content_hash=snapshot.content_hash,
                event_type=event.event_type,
                state=state,
                active_snapshot_id=active.snapshot_id if active else None,
                active_snapshot_content_hash=active.content_hash if active else None,
                previous_active_snapshot_id=(
                    previous.snapshot_id if previous else None
                ),
                previous_active_snapshot_content_hash=(
                    previous.content_hash if previous else None
                ),
                occurred_at=event.occurred_at,
            )
        except (TypeError, ValueError):
            raise SourceSnapshotUnavailable from None

    def _require_registration(self, source_id: str) -> SourceRegistration:
        if not isinstance(source_id, str) or SAFE_ID.fullmatch(source_id) is None:
            raise SourceSnapshotNotFound from None
        try:
            registration = self._source_registry.get_registration(
                self._deployment_id, source_id
            )
        except Exception:
            raise SourceSnapshotUnavailable from None
        if not isinstance(registration, SourceRegistration) or (
            registration.deployment_id,
            registration.source_set_id,
            registration.source_id,
            registration.active,
        ) != (self._deployment_id, self._source_set_id, source_id, True):
            raise SourceSnapshotNotFound from None
        return registration

    def _raw_metadata(self, source_id: str, object_id: str) -> RawObjectMetadata:
        if not isinstance(object_id, str) or SAFE_ID.fullmatch(object_id) is None:
            raise SourceSnapshotNotFound from None
        try:
            metadata = self._repository.get_raw_metadata(
                self._deployment_id, source_id, object_id
            )
        except Exception:
            raise SourceSnapshotUnavailable from None
        try:
            valid = (
                isinstance(metadata, RawObjectMetadata)
                and metadata.deployment_id == self._deployment_id
                and metadata.source_id == source_id
                and metadata.object_id == object_id
                and metadata.recomputed_object_id() == object_id
            )
        except (AttributeError, TypeError, ValueError):
            raise SourceSnapshotUnavailable from None
        if metadata is None:
            raise SourceSnapshotNotFound from None
        if not valid:
            raise SourceSnapshotUnavailable from None
        return metadata

    def _parsed_snapshot(
        self, source_id: str, snapshot_id: str
    ) -> ParsedSourceSnapshot:
        if not isinstance(snapshot_id, str) or SAFE_ID.fullmatch(snapshot_id) is None:
            raise SourceSnapshotNotFound from None
        try:
            snapshot = self._repository.get_snapshot(
                self._deployment_id, source_id, snapshot_id
            )
        except Exception:
            raise SourceSnapshotUnavailable from None
        if snapshot is None:
            raise SourceSnapshotNotFound from None
        try:
            valid = (
                isinstance(snapshot, ParsedSourceSnapshot)
                and snapshot.deployment_id == self._deployment_id
                and snapshot.source_id == source_id
                and snapshot.snapshot_id == snapshot_id
                and snapshot.reference().snapshot_id == snapshot_id
                and snapshot.parser_id == self._parser.parser_id
                and snapshot.parser_version == self._parser.parser_version
                and snapshot.schema_id == self._expected_schema_id
            )
            snapshot.verify_integrity()
        except Exception:
            raise SourceSnapshotUnavailable from None
        if not valid:
            raise SourceSnapshotUnavailable from None
        return snapshot

    def _snapshot(
        self, source_id: str
    ) -> tuple[tuple[SourceSnapshotLifecycleEvent, ...], SourceSnapshotLifecycle]:
        try:
            events, lifecycle = self._repository.get_lifecycle_snapshot(
                self._deployment_id, source_id
            )
            if len(events) > MAX_LIFECYCLE_EVENTS:
                raise ValueError("source lifecycle exceeds its application bound")
            validate_lifecycle_projection(events, lifecycle)
        except Exception:
            raise SourceSnapshotUnavailable from None
        return events, lifecycle

    def _parsed_retry(
        self,
        metadata: RawObjectMetadata,
        events: tuple[SourceSnapshotLifecycleEvent, ...],
        lifecycle: SourceSnapshotLifecycle,
    ) -> tuple[ParsedSourceSnapshot, SourceSnapshotState] | None:
        linked = tuple(
            event
            for event in events
            if event.event_type is SourceSnapshotEventType.PARSED
            and event.raw_object == metadata.reference()
        )
        if not linked:
            return None
        if len(linked) != 1 or linked[0].snapshot is None:
            raise SourceSnapshotUnavailable from None
        reference = linked[0].snapshot
        snapshot = self._parsed_snapshot(metadata.source_id, reference.snapshot_id)
        state = lifecycle.state_for(reference)
        if (
            snapshot.reference() != reference
            or snapshot.raw_object != metadata.reference()
            or snapshot.parser_id != self._parser.parser_id
            or snapshot.parser_version != self._parser.parser_version
            or state is None
        ):
            raise SourceSnapshotUnavailable from None
        return snapshot, state

    def _validation_retry(
        self,
        snapshot: ParsedSourceSnapshot,
        events: tuple[SourceSnapshotLifecycleEvent, ...],
        lifecycle: SourceSnapshotLifecycle,
    ) -> (
        tuple[
            ValidationReport,
            SourceSnapshotState,
            SourceSnapshotEventType,
        ]
        | _ValidationRetryLifecycleConflict
        | None
    ):
        linked = tuple(
            event
            for event in events
            if event.event_type
            in {
                SourceSnapshotEventType.VALIDATED,
                SourceSnapshotEventType.VALIDATION_FAILED,
            }
            and event.snapshot == snapshot.reference()
        )
        if not linked:
            return None
        if len(linked) != 1:
            raise SourceSnapshotUnavailable from None
        event = linked[0]
        report = event.validation_report
        if report is None:
            raise SourceSnapshotUnavailable from None
        try:
            report_integrity_valid = (
                report.recomputed_content_hash() == report.content_hash
                and report.diff.recomputed_content_hash() == report.diff.content_hash
            )
        except Exception:
            raise SourceSnapshotUnavailable from None
        if not report_integrity_valid:
            raise SourceSnapshotUnavailable from None
        expected_state = (
            SourceSnapshotState.VALIDATED
            if event.event_type is SourceSnapshotEventType.VALIDATED
            else SourceSnapshotState.QUARANTINED
        )
        if (
            event.raw_object != snapshot.raw_object
            or report.snapshot != snapshot.reference()
            or report.passed != (event.event_type is SourceSnapshotEventType.VALIDATED)
        ):
            raise SourceSnapshotUnavailable from None
        if lifecycle.state_for(snapshot.reference()) is not expected_state:
            return _VALIDATION_RETRY_LIFECYCLE_CONFLICT
        if lifecycle.active_snapshot != report.diff.previous_snapshot:
            return _VALIDATION_RETRY_LIFECYCLE_CONFLICT
        return report, expected_state, event.event_type

    def _active_snapshot(
        self, source_id: str, lifecycle: SourceSnapshotLifecycle
    ) -> ParsedSourceSnapshot | None:
        reference = lifecycle.active_snapshot
        if reference is None:
            return None
        if lifecycle.state_for(reference) is not SourceSnapshotState.ACTIVE:
            raise SourceSnapshotUnavailable from None
        snapshot = self._parsed_snapshot(source_id, reference.snapshot_id)
        if snapshot.reference() != reference:
            raise SourceSnapshotUnavailable from None
        return snapshot

    @staticmethod
    def _raw_state(
        lifecycle: SourceSnapshotLifecycle, reference: RawObjectRef
    ) -> RawObjectState | None:
        matches = tuple(
            item.state for item in lifecycle.raw_objects if item.raw_object == reference
        )
        if len(matches) > 1:
            raise SourceSnapshotUnavailable from None
        return matches[0] if matches else None

    @staticmethod
    def _next_sequence(events: tuple[SourceSnapshotLifecycleEvent, ...]) -> int:
        return events[-1].sequence + 1 if events else 1

    @staticmethod
    def _require_command_time(
        now: datetime, events: tuple[SourceSnapshotLifecycleEvent, ...]
    ) -> None:
        if events and now < events[-1].occurred_at:
            raise SourceSnapshotUnavailable from None

    def _event(
        self,
        authorized: AuthorizedRequest,
        *,
        source_id: str,
        sequence: int,
        event_type: SourceSnapshotEventType,
        raw_object: RawObjectRef,
        snapshot: SourceSnapshotRef | None,
        report: ValidationReport | None,
        now: datetime,
        reason: str | None = None,
        previous_active_snapshot: SourceSnapshotRef | None = None,
    ) -> SourceSnapshotLifecycleEvent:
        reasons = {
            SourceSnapshotEventType.RETRIEVED: "source object retrieved",
            SourceSnapshotEventType.QUARANTINED: "source object quarantined",
            SourceSnapshotEventType.PARSED: "source object parsed",
            SourceSnapshotEventType.VALIDATION_FAILED: "source validation failed",
            SourceSnapshotEventType.VALIDATED: "source validation passed",
        }
        try:
            event_reason = reason if reason is not None else reasons[event_type]
            return SourceSnapshotLifecycleEvent(
                sequence=sequence,
                event_id=self._new_event_id(),
                deployment_id=self._deployment_id,
                source_id=source_id,
                event_type=event_type,
                raw_object=raw_object,
                snapshot=snapshot,
                previous_active_snapshot=previous_active_snapshot,
                actor_id=authorized.context.actor.subject,
                actor_type=LifecycleActorType(
                    authorized.context.actor.actor_type.value
                ),
                reason=event_reason,
                occurred_at=now,
                validation_report=report,
            )
        except SourceSnapshotUnavailable:
            raise
        except Exception:
            raise SourceSnapshotUnavailable from None

    def _audit_pair(
        self,
        authorized: AuthorizedRequest,
        event: SourceSnapshotLifecycleEvent,
        *,
        idempotent: bool = False,
    ) -> SnapshotCommandAuditRecord:
        applied, replay = {
            SourceSnapshotEventType.RETRIEVED: (
                SnapshotCommandReason.RETRIEVED,
                SnapshotCommandReason.RETRIEVE_IDEMPOTENT,
            ),
            SourceSnapshotEventType.QUARANTINED: (
                SnapshotCommandReason.QUARANTINED,
                SnapshotCommandReason.QUARANTINE_IDEMPOTENT,
            ),
            SourceSnapshotEventType.PARSED: (
                SnapshotCommandReason.PARSED,
                SnapshotCommandReason.PARSE_IDEMPOTENT,
            ),
            SourceSnapshotEventType.VALIDATION_FAILED: (
                SnapshotCommandReason.VALIDATION_FAILED,
                SnapshotCommandReason.VALIDATION_FAILURE_IDEMPOTENT,
            ),
            SourceSnapshotEventType.VALIDATED: (
                SnapshotCommandReason.VALIDATED,
                SnapshotCommandReason.VALIDATE_IDEMPOTENT,
            ),
            SourceSnapshotEventType.APPROVED: (
                SnapshotCommandReason.APPROVED,
                SnapshotCommandReason.APPROVE_IDEMPOTENT,
            ),
            SourceSnapshotEventType.ACTIVATED: (
                SnapshotCommandReason.ACTIVATED,
                SnapshotCommandReason.ACTIVATE_IDEMPOTENT,
            ),
            SourceSnapshotEventType.ROLLED_BACK: (
                SnapshotCommandReason.ROLLED_BACK,
                SnapshotCommandReason.ROLLBACK_IDEMPOTENT,
            ),
        }[event.event_type]
        return self._audit(
            authorized,
            event.source_id,
            event.raw_object,
            event.snapshot,
            SnapshotCommandOutcome.SUCCESS,
            replay if idempotent else applied,
            event.occurred_at,
            lifecycle_event_id=None if idempotent else event.event_id,
        )

    def _record_failure(
        self,
        authorized: AuthorizedRequest,
        source_id: str,
        raw_object: RawObjectRef,
        snapshot: SourceSnapshotRef | None,
        reason: SnapshotCommandReason,
        now: datetime,
    ) -> None:
        try:
            self._repository.append_command_audit(
                self._audit(
                    authorized,
                    source_id,
                    raw_object,
                    snapshot,
                    SnapshotCommandOutcome.FAILURE,
                    reason,
                    now,
                )
            )
        except Exception:
            raise SourceSnapshotUnavailable from None

    def _audit(
        self,
        authorized: AuthorizedRequest,
        source_id: str,
        raw_object: RawObjectRef,
        snapshot: SourceSnapshotRef | None,
        outcome: SnapshotCommandOutcome,
        reason: SnapshotCommandReason,
        now: datetime,
        *,
        lifecycle_event_id: str | None = None,
    ) -> SnapshotCommandAuditRecord:
        try:
            return SnapshotCommandAuditRecord(
                command_event_id=self._new_command_id(),
                authorization_event_id=authorized.audit_event_id,
                lifecycle_event_id=lifecycle_event_id,
                tenant_id=self._control_tenant_id,
                deployment_id=self._deployment_id,
                source_id=source_id,
                raw_object_id=raw_object.object_id,
                snapshot_id=snapshot.snapshot_id if snapshot else None,
                snapshot_content_hash=snapshot.content_hash if snapshot else None,
                actor_id=authorized.context.actor.subject,
                actor_type=LifecycleActorType(
                    authorized.context.actor.actor_type.value
                ),
                operation=authorized.operation.value,
                outcome=outcome,
                reason=reason,
                occurred_at=now,
            )
        except SourceSnapshotUnavailable:
            raise
        except Exception:
            raise SourceSnapshotUnavailable from None

    def _now(self) -> datetime:
        try:
            now = self._clock()
            if (
                not isinstance(now, datetime)
                or now.tzinfo is None
                or now.utcoffset() is None
            ):
                raise ValueError("source snapshot clock must be timezone aware")
            return now.astimezone(UTC)
        except Exception:
            raise SourceSnapshotUnavailable from None

    def _new_event_id(self) -> str:
        return self._new_id(self._event_id_factory)

    def _new_command_id(self) -> str:
        return self._new_id(self._command_id_factory)

    @staticmethod
    def _new_id(factory: Callable[[], str]) -> str:
        try:
            value = factory()
        except Exception:
            raise SourceSnapshotUnavailable from None
        if not isinstance(value, str) or SAFE_ID.fullmatch(value) is None:
            raise SourceSnapshotUnavailable from None
        return value

    @staticmethod
    def _raw_result(
        metadata: RawObjectMetadata, state: RawObjectState
    ) -> RawObjectCommandResult:
        return RawObjectCommandResult(
            deployment_id=metadata.deployment_id,
            source_id=metadata.source_id,
            object_id=metadata.object_id,
            content_hash=metadata.content_hash,
            byte_length=metadata.byte_length,
            media_type=metadata.media_type,
            charset=metadata.charset,
            retrieved_at=metadata.retrieved_at,
            effective_from=metadata.effective_from,
            state=state,
        )

    @staticmethod
    def _parsed_result(
        snapshot: ParsedSourceSnapshot, state: SourceSnapshotState
    ) -> ParsedSnapshotCommandResult:
        return ParsedSnapshotCommandResult(
            deployment_id=snapshot.deployment_id,
            source_id=snapshot.source_id,
            object_id=snapshot.raw_object.object_id,
            raw_content_hash=snapshot.raw_object.content_hash,
            snapshot_id=snapshot.snapshot_id,
            snapshot_content_hash=snapshot.content_hash,
            parser_id=FiniteParserId(snapshot.parser_id),
            parser_version=snapshot.parser_version,
            schema_id=snapshot.schema_id,
            declared_record_count=snapshot.declared_record_count,
            record_count=len(snapshot.records),
            parsed_at=snapshot.parsed_at,
            state=state,
        )

    @staticmethod
    def _validation_result(
        snapshot: ParsedSourceSnapshot,
        report: ValidationReport,
        state: SourceSnapshotState,
    ) -> SnapshotValidationResult:
        previous = report.diff.previous_snapshot
        return SnapshotValidationResult(
            deployment_id=snapshot.deployment_id,
            source_id=snapshot.source_id,
            snapshot_id=snapshot.snapshot_id,
            snapshot_content_hash=snapshot.content_hash,
            passed=report.passed,
            reason_codes=sorted({reason.code for reason in report.reasons}),
            previous_snapshot_id=previous.snapshot_id if previous else None,
            previous_snapshot_content_hash=(
                previous.content_hash if previous else None
            ),
            diff_content_hash=report.diff.content_hash,
            added_record_count=len(report.diff.added_record_ids),
            removed_record_count=len(report.diff.removed_record_ids),
            changed_record_count=len(report.diff.changed_records),
            validated_at=report.validated_at,
            state=state,
        )
