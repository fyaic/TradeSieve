"""Atomic in-memory references for immutable source snapshot ports."""

from __future__ import annotations

from threading import RLock

from tradesieve.domain.source_registry import (
    SourceAvailability,
    SourceRuntimeObservation,
)
from tradesieve.domain.source_snapshot import (
    EVENT_TYPE_BY_SUCCESS_COMMAND_REASON,
    ArtifactWriteOutcome,
    InvalidSourceSnapshotTransition,
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
    fold_source_snapshot_events,
    require_query_limit,
    semantically_applied_lifecycle_transition,
    validate_atomic_command_audit,
    verify_raw_bytes,
)
from tradesieve.ports.source_snapshot import SourceSnapshotPersistenceError

type SourceKey = tuple[str, str]
type RawKey = tuple[str, str, str]
type SnapshotKey = tuple[str, str, str]


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


class InMemoryImmutableRawObjectStore:
    """Exact immutable bytes with verification on every read."""

    def __init__(self) -> None:
        self._objects: dict[str, tuple[RawObjectMetadata, bytes]] = {}
        self._fail_next_write = False
        self._lock = RLock()

    def fail_next_write(self) -> None:
        with self._lock:
            self._fail_next_write = True

    def corrupt_bytes_for_test(self, object_id: str, content: bytes) -> None:
        with self._lock:
            stored = self._objects.get(object_id)
            if stored is None:
                return
            self._objects[object_id] = (stored[0], content)

    def put_exact(
        self, metadata: RawObjectMetadata, content: bytes
    ) -> ArtifactWriteOutcome:
        verified = verify_raw_bytes(metadata, content)
        with self._lock:
            existing = self._objects.get(metadata.object_id)
            if existing is not None:
                return (
                    ArtifactWriteOutcome.IDEMPOTENT
                    if existing == (metadata, verified)
                    else ArtifactWriteOutcome.CONFLICT
                )
            objects = dict(self._objects)
            objects[metadata.object_id] = (metadata, verified)
            if self._fail_next_write:
                self._fail_next_write = False
                raise SourceSnapshotPersistenceError
            self._objects = objects
            return ArtifactWriteOutcome.APPLIED

    def get_verified(self, reference: RawObjectRef) -> bytes:
        if not isinstance(reference, RawObjectRef):
            raise ValueError("raw object reference must be typed")
        with self._lock:
            stored = self._objects.get(reference.object_id)
            if stored is None or stored[0].reference() != reference:
                raise RawObjectIntegrityError
            return verify_raw_bytes(stored[0], stored[1])


class InMemorySourceSnapshotRepository:
    """Copy-on-write repository proving the future PostgreSQL UoW semantics."""

    def __init__(self) -> None:
        self._raw_metadata: dict[RawKey, RawObjectMetadata] = {}
        self._snapshots: dict[SnapshotKey, ParsedSourceSnapshot] = {}
        self._events: dict[SourceKey, tuple[SourceSnapshotLifecycleEvent, ...]] = {}
        self._audits: dict[SourceKey, tuple[SnapshotCommandAuditRecord, ...]] = {}
        self._observations: dict[SourceKey, SourceRuntimeObservation] = {}
        self._fail_next_atomic = False
        self._lock = RLock()

    def fail_next_atomic_write(self) -> None:
        with self._lock:
            self._fail_next_atomic = True

    def corrupt_remove_raw_metadata_for_test(
        self, deployment_id: str, source_id: str, object_id: str
    ) -> None:
        with self._lock:
            self._raw_metadata.pop((deployment_id, source_id, object_id), None)

    def corrupt_remove_snapshot_for_test(
        self, deployment_id: str, source_id: str, snapshot_id: str
    ) -> None:
        with self._lock:
            self._snapshots.pop((deployment_id, source_id, snapshot_id), None)

    def corrupt_store_raw_metadata_for_test(
        self,
        deployment_id: str,
        source_id: str,
        object_id: str,
        metadata: RawObjectMetadata,
    ) -> None:
        with self._lock:
            self._raw_metadata[(deployment_id, source_id, object_id)] = metadata

    def corrupt_store_snapshot_for_test(
        self,
        deployment_id: str,
        source_id: str,
        snapshot_id: str,
        snapshot: ParsedSourceSnapshot,
    ) -> None:
        with self._lock:
            self._snapshots[(deployment_id, source_id, snapshot_id)] = snapshot

    def corrupt_replace_events_for_test(
        self,
        deployment_id: str,
        source_id: str,
        events: tuple[SourceSnapshotLifecycleEvent, ...],
    ) -> None:
        with self._lock:
            self._events[(deployment_id, source_id)] = events

    def corrupt_replace_observation_for_test(
        self, observation: SourceRuntimeObservation
    ) -> None:
        with self._lock:
            self._observations[(observation.deployment_id, observation.source_id)] = (
                observation
            )

    def corrupt_remove_observation_for_test(
        self, deployment_id: str, source_id: str
    ) -> None:
        with self._lock:
            self._observations.pop((deployment_id, source_id), None)

    def corrupt_replace_audits_for_test(
        self,
        deployment_id: str,
        source_id: str,
        audits: tuple[SnapshotCommandAuditRecord, ...],
    ) -> None:
        with self._lock:
            self._audits[(deployment_id, source_id)] = audits

    def save_retrieval_atomic(
        self,
        metadata: RawObjectMetadata,
        retrieved_event: SourceSnapshotLifecycleEvent,
        applied_audit: SnapshotCommandAuditRecord,
        idempotent_audit: SnapshotCommandAuditRecord,
    ) -> ArtifactWriteOutcome:
        with self._lock:
            scope = (metadata.deployment_id, metadata.source_id)
            key = (*scope, metadata.object_id)
            if (
                retrieved_event.event_type is not SourceSnapshotEventType.RETRIEVED
                or retrieved_event.raw_object != metadata.reference()
                or retrieved_event.snapshot is not None
                or self._event_scope(retrieved_event) != scope
            ):
                raise ValueError("retrieval event must exactly identify raw metadata")
            self._validate_audit_pair(retrieved_event, applied_audit, idempotent_audit)
            existing = self._raw_metadata.get(key)
            if existing is not None:
                if existing != metadata:
                    return ArtifactWriteOutcome.CONFLICT
                # Verified replay plus the artifact-reference check proves that the
                # immutable metadata has exactly one RETRIEVED transition.
                self._verified_lifecycle(scope)
                audits = self._audits_with(scope, idempotent_audit)
                self._raise_if_atomic_failure()
                self._audits[scope] = audits
                return ArtifactWriteOutcome.IDEMPOTENT
            self._require_unused_event_id(scope, retrieved_event.event_id)
            events = self._events.get(scope, ()) + (retrieved_event,)
            try:
                fold_source_snapshot_events(events)
            except InvalidSourceSnapshotTransition:
                return ArtifactWriteOutcome.CONFLICT
            raw_metadata = dict(self._raw_metadata)
            raw_metadata[key] = metadata
            audits = self._audits_with(scope, applied_audit)
            self._raise_if_atomic_failure()
            self._raw_metadata = raw_metadata
            self._events[scope] = events
            self._audits[scope] = audits
            return ArtifactWriteOutcome.APPLIED

    def save_parsed_snapshot_atomic(
        self,
        snapshot: ParsedSourceSnapshot,
        parsed_event: SourceSnapshotLifecycleEvent,
        applied_audit: SnapshotCommandAuditRecord,
        idempotent_audit: SnapshotCommandAuditRecord,
    ) -> ArtifactWriteOutcome:
        with self._lock:
            scope = (snapshot.deployment_id, snapshot.source_id)
            key = (*scope, snapshot.snapshot_id)
            if (
                parsed_event.event_type is not SourceSnapshotEventType.PARSED
                or parsed_event.raw_object != snapshot.raw_object
                or parsed_event.snapshot != snapshot.reference()
                or self._event_scope(parsed_event) != scope
            ):
                raise ValueError(
                    "parsed event must exactly identify immutable snapshot"
                )
            self._require_raw(scope, snapshot.raw_object)
            self._validate_audit_pair(parsed_event, applied_audit, idempotent_audit)
            existing = self._snapshots.get(key)
            if existing is not None:
                if (
                    existing != snapshot
                    or existing.recomputed_content_hash() != snapshot.content_hash
                ):
                    return ArtifactWriteOutcome.CONFLICT
                # Verified replay plus the artifact-reference check proves that the
                # immutable snapshot has exactly one PARSED transition.
                self._verified_lifecycle(scope)
                audits = self._audits_with(scope, idempotent_audit)
                self._raise_if_atomic_failure()
                self._audits[scope] = audits
                return ArtifactWriteOutcome.IDEMPOTENT
            self._require_unused_event_id(scope, parsed_event.event_id)
            events = self._events.get(scope, ()) + (parsed_event,)
            try:
                fold_source_snapshot_events(events)
            except InvalidSourceSnapshotTransition:
                return ArtifactWriteOutcome.CONFLICT
            snapshots = dict(self._snapshots)
            snapshots[key] = snapshot
            audits = self._audits_with(scope, applied_audit)
            self._raise_if_atomic_failure()
            self._snapshots = snapshots
            self._events[scope] = events
            self._audits[scope] = audits
            return ArtifactWriteOutcome.APPLIED

    def append_lifecycle_atomic(
        self,
        event: SourceSnapshotLifecycleEvent,
        applied_audit: SnapshotCommandAuditRecord,
        idempotent_audit: SnapshotCommandAuditRecord,
    ) -> LifecycleWriteOutcome:
        with self._lock:
            allowed = {
                SourceSnapshotEventType.QUARANTINED,
                SourceSnapshotEventType.VALIDATION_FAILED,
                SourceSnapshotEventType.VALIDATED,
                SourceSnapshotEventType.APPROVED,
            }
            if event.event_type not in allowed:
                raise ValueError("event requires its explicit atomic repository method")
            scope = self._event_scope(event)
            self._require_event_references(scope, event)
            self._validate_audit_pair(event, applied_audit, idempotent_audit)
            return self._append_event_atomic(
                scope, event, applied_audit, idempotent_audit
            )

    def activate_or_rollback_atomic(
        self,
        event: SourceSnapshotLifecycleEvent,
        observation: SourceRuntimeObservation,
        applied_audit: SnapshotCommandAuditRecord,
        idempotent_audit: SnapshotCommandAuditRecord,
    ) -> LifecycleWriteOutcome:
        with self._lock:
            if event.event_type not in {
                SourceSnapshotEventType.ACTIVATED,
                SourceSnapshotEventType.ROLLED_BACK,
            }:
                raise ValueError("activation UoW accepts only activation or rollback")
            scope = self._event_scope(event)
            self._require_event_references(scope, event)
            self._validate_observation(scope, event, observation)
            self._validate_audit_pair(event, applied_audit, idempotent_audit)
            self._require_unused_event_id(scope, event.event_id)
            current_events, lifecycle = self._verified_lifecycle(scope)
            responsible = semantically_applied_lifecycle_transition(
                event, lifecycle, current_events
            )
            if responsible is not None:
                audits = self._audits_with(scope, idempotent_audit)
                self._raise_if_atomic_failure()
                self._audits[scope] = audits
                return LifecycleWriteOutcome.IDEMPOTENT
            if event.event_type is SourceSnapshotEventType.ACTIVATED:
                validation_events = tuple(
                    candidate
                    for candidate in current_events
                    if candidate.event_type is SourceSnapshotEventType.VALIDATED
                    and candidate.snapshot == event.snapshot
                )
                if len(validation_events) != 1:
                    raise SourceSnapshotPersistenceError
                validation = validation_events[0]
                report = validation.validation_report
                if report is None:
                    raise SourceSnapshotPersistenceError
                try:
                    valid_report = (
                        report.passed
                        and validation.raw_object == event.raw_object
                        and report.snapshot == event.snapshot
                        and report.diff.new_snapshot == event.snapshot
                        and report.recomputed_content_hash() == report.content_hash
                        and report.diff.recomputed_content_hash()
                        == report.diff.content_hash
                    )
                except (AttributeError, TypeError, ValueError):
                    valid_report = False
                if not valid_report:
                    raise SourceSnapshotPersistenceError
                if report.diff.previous_snapshot != lifecycle.active_snapshot:
                    return LifecycleWriteOutcome.CONFLICT
            events = current_events + (event,)
            try:
                fold_source_snapshot_events(events)
            except InvalidSourceSnapshotTransition:
                return LifecycleWriteOutcome.CONFLICT
            current_observation = self._observations.get(scope)
            if (
                current_observation is not None
                and observation.observed_at <= current_observation.observed_at
            ):
                return LifecycleWriteOutcome.CONFLICT
            audits = self._audits_with(scope, applied_audit)
            observations = dict(self._observations)
            observations[scope] = observation
            self._raise_if_atomic_failure()
            self._events[scope] = events
            self._audits[scope] = audits
            self._observations = observations
            return LifecycleWriteOutcome.APPLIED

    def append_command_audit(self, audit: SnapshotCommandAuditRecord) -> None:
        with self._lock:
            if (
                audit.outcome is not SnapshotCommandOutcome.FAILURE
                or audit.lifecycle_event_id is not None
            ):
                raise ValueError("standalone command audits must be unlinked failures")
            scope = (audit.deployment_id, audit.source_id)
            audits = self._audits_with(scope, audit)
            self._raise_if_atomic_failure()
            self._audits[scope] = audits

    def get_raw_metadata(
        self, deployment_id: str, source_id: str, object_id: str
    ) -> RawObjectMetadata | None:
        with self._lock:
            return self._validated_raw_at((deployment_id, source_id, object_id))

    def get_snapshot(
        self, deployment_id: str, source_id: str, snapshot_id: str
    ) -> ParsedSourceSnapshot | None:
        with self._lock:
            return self._validated_snapshot_at((deployment_id, source_id, snapshot_id))

    def list_snapshots(
        self, deployment_id: str, source_id: str, *, limit: int
    ) -> tuple[ParsedSourceSnapshot, ...]:
        require_query_limit(limit)
        with self._lock:
            scope = (deployment_id, source_id)
            self._verify_all_scope_artifacts(scope)
            values = tuple(
                sorted(
                    (
                        snapshot
                        for key, snapshot in self._snapshots.items()
                        if key[:2] == scope
                    ),
                    key=lambda item: item.snapshot_id,
                )
            )
            return values[:limit]

    def get_lifecycle_snapshot(
        self, deployment_id: str, source_id: str
    ) -> tuple[tuple[SourceSnapshotLifecycleEvent, ...], SourceSnapshotLifecycle]:
        with self._lock:
            return self._verified_lifecycle((deployment_id, source_id))

    def list_lifecycle_events(
        self, deployment_id: str, source_id: str, *, limit: int
    ) -> tuple[SourceSnapshotLifecycleEvent, ...]:
        require_query_limit(limit)
        with self._lock:
            events, _ = self._verified_lifecycle((deployment_id, source_id))
            return events[:limit]

    def list_command_audits(
        self, deployment_id: str, source_id: str, *, limit: int
    ) -> tuple[SnapshotCommandAuditRecord, ...]:
        require_query_limit(limit)
        with self._lock:
            scope = (deployment_id, source_id)
            if self._scope_has_repository_state(scope):
                self._verified_lifecycle(scope)
            audits = self._verified_audits(scope)
            return audits[:limit]

    def get_runtime_observation(
        self, deployment_id: str, source_id: str
    ) -> SourceRuntimeObservation | None:
        with self._lock:
            scope = (deployment_id, source_id)
            observation = self._observations.get(scope)
            if self._scope_has_repository_state(scope):
                self._verified_lifecycle(scope)
            return observation

    def _append_event_atomic(
        self,
        scope: SourceKey,
        event: SourceSnapshotLifecycleEvent,
        applied_audit: SnapshotCommandAuditRecord,
        idempotent_audit: SnapshotCommandAuditRecord,
    ) -> LifecycleWriteOutcome:
        self._require_unused_event_id(scope, event.event_id)
        current_events, lifecycle = self._verified_lifecycle(scope)
        if event.event_type in {
            SourceSnapshotEventType.VALIDATION_FAILED,
            SourceSnapshotEventType.VALIDATED,
        }:
            report = event.validation_report
            if (
                report is None
                or report.diff.previous_snapshot != lifecycle.active_snapshot
            ):
                return LifecycleWriteOutcome.CONFLICT
        responsible = semantically_applied_lifecycle_transition(
            event, lifecycle, current_events
        )
        if responsible is not None:
            audits = self._audits_with(scope, idempotent_audit)
            self._raise_if_atomic_failure()
            self._audits[scope] = audits
            return LifecycleWriteOutcome.IDEMPOTENT
        events = current_events + (event,)
        try:
            fold_source_snapshot_events(events)
        except InvalidSourceSnapshotTransition:
            return LifecycleWriteOutcome.CONFLICT
        audits = self._audits_with(scope, applied_audit)
        self._raise_if_atomic_failure()
        self._events[scope] = events
        self._audits[scope] = audits
        return LifecycleWriteOutcome.APPLIED

    def _verified_lifecycle(
        self, scope: SourceKey
    ) -> tuple[tuple[SourceSnapshotLifecycleEvent, ...], SourceSnapshotLifecycle]:
        events = self._events.get(scope, ())
        try:
            for event in events:
                self._verify_lifecycle_event(event)
            lifecycle = fold_source_snapshot_events(events)
        except (
            AttributeError,
            InvalidSourceSnapshotTransition,
            TypeError,
            ValueError,
        ) as exc:
            raise SourceSnapshotPersistenceError from exc
        self._verify_all_scope_artifacts(scope)
        self._verified_audits(scope)
        for event in events:
            self._require_event_references(scope, event)
            matching_audits = tuple(
                audit
                for audit in self._audits.get(scope, ())
                if audit.lifecycle_event_id == event.event_id
            )
            if len(matching_audits) != 1:
                raise SourceSnapshotPersistenceError
            try:
                validate_atomic_command_audit(
                    matching_audits[0],
                    event.raw_object,
                    event.snapshot,
                    event,
                    APPLIED_REASONS[event.event_type],
                )
            except ValueError as exc:
                raise SourceSnapshotPersistenceError from exc
        observation = self._observations.get(scope)
        if lifecycle.active_snapshot is None:
            if observation is not None:
                raise SourceSnapshotPersistenceError
        else:
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
                self._validate_observation(scope, transition, observation)
            except ValueError as exc:
                raise SourceSnapshotPersistenceError from exc
        return events, lifecycle

    def _require_event_references(
        self, scope: SourceKey, event: SourceSnapshotLifecycleEvent
    ) -> None:
        raw = self._validated_raw_at((*scope, event.raw_object.object_id))
        if raw is None or raw.reference() != event.raw_object:
            raise SourceSnapshotPersistenceError
        if event.snapshot is not None:
            snapshot = self._validated_snapshot_at((*scope, event.snapshot.snapshot_id))
            if (
                snapshot is None
                or snapshot.reference() != event.snapshot
                or snapshot.raw_object != event.raw_object
                or snapshot.recomputed_content_hash() != snapshot.content_hash
            ):
                raise SourceSnapshotPersistenceError

    def _require_raw(self, scope: SourceKey, reference: RawObjectRef) -> None:
        stored = self._validated_raw_at((*scope, reference.object_id))
        if stored is None or stored.reference() != reference:
            raise SourceSnapshotPersistenceError

    def _validated_raw_at(self, key: RawKey) -> RawObjectMetadata | None:
        stored = self._raw_metadata.get(key)
        if stored is not None:
            try:
                valid = (
                    isinstance(stored, RawObjectMetadata)
                    and (stored.deployment_id, stored.source_id, stored.object_id)
                    == key
                    and stored.recomputed_object_id() == stored.object_id
                )
            except (AttributeError, TypeError, ValueError):
                valid = False
            if not valid:
                raise SourceSnapshotPersistenceError
        return stored

    def _validated_snapshot_at(self, key: SnapshotKey) -> ParsedSourceSnapshot | None:
        stored = self._snapshots.get(key)
        if stored is not None:
            try:
                if not isinstance(stored, ParsedSourceSnapshot):
                    raise SourceSnapshotIntegrityError
                stored.verify_integrity()
                valid = (
                    stored.deployment_id,
                    stored.source_id,
                    stored.snapshot_id,
                ) == key
            except (SourceSnapshotIntegrityError, TypeError, ValueError):
                valid = False
            if not valid:
                raise SourceSnapshotPersistenceError
        return stored

    def _verify_all_scope_artifacts(self, scope: SourceKey) -> None:
        events = self._events.get(scope, ())
        referenced_raw_ids = {event.raw_object.object_id for event in events}
        referenced_snapshot_ids = {
            event.snapshot.snapshot_id for event in events if event.snapshot is not None
        }
        for key in tuple(self._raw_metadata):
            if key[:2] != scope:
                continue
            self._validated_raw_at(key)
            if key[2] not in referenced_raw_ids:
                raise SourceSnapshotPersistenceError
        for key in tuple(self._snapshots):
            if key[:2] != scope:
                continue
            snapshot = self._validated_snapshot_at(key)
            if snapshot is None or key[2] not in referenced_snapshot_ids:
                raise SourceSnapshotPersistenceError
            self._require_raw(scope, snapshot.raw_object)

    def _verified_audits(
        self, scope: SourceKey
    ) -> tuple[SnapshotCommandAuditRecord, ...]:
        audits = self._audits.get(scope, ())
        command_ids: set[str] = set()
        events = self._events.get(scope, ())
        event_ids = {event.event_id for event in events}
        for audit in audits:
            try:
                self._verify_command_audit(audit)
            except (AttributeError, TypeError, ValueError) as exc:
                raise SourceSnapshotPersistenceError from exc
            if (
                (audit.deployment_id, audit.source_id) != scope
                or audit.command_event_id in command_ids
                or (
                    audit.lifecycle_event_id is not None
                    and audit.lifecycle_event_id not in event_ids
                )
            ):
                raise SourceSnapshotPersistenceError
            if (
                audit.outcome is SnapshotCommandOutcome.SUCCESS
                and audit.lifecycle_event_id is None
            ):
                event_type = EVENT_TYPE_BY_SUCCESS_COMMAND_REASON.get(audit.reason)
                responsible = next(
                    (
                        event
                        for event in reversed(events)
                        if event.event_type is event_type
                        and event.raw_object.object_id == audit.raw_object_id
                        and (
                            None
                            if event.snapshot is None
                            else (
                                event.snapshot.snapshot_id,
                                event.snapshot.content_hash,
                            )
                        )
                        == (
                            None
                            if audit.snapshot_id is None
                            else (audit.snapshot_id, audit.snapshot_content_hash)
                        )
                        and event.occurred_at <= audit.occurred_at
                    ),
                    None,
                )
                if responsible is None:
                    raise SourceSnapshotPersistenceError
                try:
                    validate_atomic_command_audit(
                        audit,
                        responsible.raw_object,
                        responsible.snapshot,
                        None,
                        audit.reason,
                    )
                except ValueError as exc:
                    raise SourceSnapshotPersistenceError from exc
            command_ids.add(audit.command_event_id)
        return audits

    @staticmethod
    def _verify_lifecycle_event(event: SourceSnapshotLifecycleEvent) -> None:
        if not isinstance(event, SourceSnapshotLifecycleEvent):
            raise TypeError("lifecycle event must be typed")
        rebuilt = SourceSnapshotLifecycleEvent(
            sequence=event.sequence,
            event_id=event.event_id,
            deployment_id=event.deployment_id,
            source_id=event.source_id,
            event_type=event.event_type,
            raw_object=event.raw_object,
            snapshot=event.snapshot,
            previous_active_snapshot=event.previous_active_snapshot,
            actor_id=event.actor_id,
            actor_type=event.actor_type,
            reason=event.reason,
            occurred_at=event.occurred_at,
            validation_report=event.validation_report,
        )
        if rebuilt != event:
            raise ValueError("lifecycle event failed reconstruction")

    @staticmethod
    def _verify_command_audit(audit: SnapshotCommandAuditRecord) -> None:
        if not isinstance(audit, SnapshotCommandAuditRecord):
            raise TypeError("command audit must be typed")
        rebuilt = SnapshotCommandAuditRecord(
            command_event_id=audit.command_event_id,
            authorization_event_id=audit.authorization_event_id,
            lifecycle_event_id=audit.lifecycle_event_id,
            tenant_id=audit.tenant_id,
            deployment_id=audit.deployment_id,
            source_id=audit.source_id,
            raw_object_id=audit.raw_object_id,
            snapshot_id=audit.snapshot_id,
            snapshot_content_hash=audit.snapshot_content_hash,
            actor_id=audit.actor_id,
            actor_type=audit.actor_type,
            operation=audit.operation,
            outcome=audit.outcome,
            reason=audit.reason,
            occurred_at=audit.occurred_at,
        )
        if rebuilt != audit:
            raise ValueError("command audit failed reconstruction")

    def _scope_has_repository_state(self, scope: SourceKey) -> bool:
        return (
            scope in self._events
            or scope in self._audits
            or scope in self._observations
            or any(key[:2] == scope for key in self._raw_metadata)
            or any(key[:2] == scope for key in self._snapshots)
        )

    def _validate_audit_pair(
        self,
        event: SourceSnapshotLifecycleEvent,
        applied: SnapshotCommandAuditRecord,
        idempotent: SnapshotCommandAuditRecord,
    ) -> None:
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

    def _validate_observation(
        self,
        scope: SourceKey,
        event: SourceSnapshotLifecycleEvent,
        observation: SourceRuntimeObservation,
    ) -> None:
        if not isinstance(observation, SourceRuntimeObservation):
            raise ValueError("runtime observation must be typed")
        assert event.snapshot is not None
        raw = self._raw_metadata.get((*scope, event.raw_object.object_id))
        if (
            raw is None
            or (observation.deployment_id, observation.source_id) != scope
            or observation.availability is not SourceAvailability.AVAILABLE
            or observation.active_snapshot_id != event.snapshot.snapshot_id
            or observation.observed_at != event.occurred_at
            or observation.retrieved_at != raw.retrieved_at
            or observation.effective_from != raw.effective_from
        ):
            raise ValueError("runtime observation does not match atomic activation")

    def _audits_with(
        self, scope: SourceKey, audit: SnapshotCommandAuditRecord
    ) -> tuple[SnapshotCommandAuditRecord, ...]:
        current = self._audits.get(scope, ())
        if any(item.command_event_id == audit.command_event_id for item in current):
            raise SourceSnapshotPersistenceError
        return current + (audit,)

    def _require_unused_event_id(self, scope: SourceKey, event_id: str) -> None:
        if any(event.event_id == event_id for event in self._events.get(scope, ())):
            raise SourceSnapshotPersistenceError

    def _raise_if_atomic_failure(self) -> None:
        if self._fail_next_atomic:
            self._fail_next_atomic = False
            raise SourceSnapshotPersistenceError

    @staticmethod
    def _event_scope(event: SourceSnapshotLifecycleEvent) -> SourceKey:
        return (event.deployment_id, event.source_id)
