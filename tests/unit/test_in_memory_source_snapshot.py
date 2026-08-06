"""Reference-UoW evidence for immutable source snapshots and raw objects."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tradesieve.adapters.in_memory_source_snapshot import (
    InMemoryImmutableRawObjectStore,
    InMemorySourceSnapshotRepository,
)
from tradesieve.domain.source_registry import (
    SourceAvailability,
    SourceRuntimeObservation,
)
from tradesieve.domain.source_snapshot import (
    AUTHORIZATION_OPERATION_BY_EVENT_TYPE,
    MAX_QUERY_LIMIT,
    ArtifactWriteOutcome,
    LifecycleActorType,
    LifecycleWriteOutcome,
    ParsedAssertionInput,
    ParsedRecordInput,
    ParsedSourceSnapshot,
    RawObjectIntegrityError,
    RawObjectMetadata,
    SnapshotCommandAuditRecord,
    SnapshotCommandOutcome,
    SnapshotCommandReason,
    SourceSnapshotEventType,
    SourceSnapshotLifecycleEvent,
    SourceSnapshotState,
    ValidationReport,
    validate_snapshot,
)
from tradesieve.ports.source_snapshot import (
    FiniteParserOutput,
    SourceSnapshotPersistenceError,
)

NOW = datetime(2026, 8, 6, 12, tzinfo=UTC)
SCHEMA_ID = "synthetic-source-v1"

APPLIED_REASON = {
    SourceSnapshotEventType.RETRIEVED: SnapshotCommandReason.RETRIEVED,
    SourceSnapshotEventType.QUARANTINED: SnapshotCommandReason.QUARANTINED,
    SourceSnapshotEventType.PARSED: SnapshotCommandReason.PARSED,
    SourceSnapshotEventType.VALIDATION_FAILED: SnapshotCommandReason.VALIDATION_FAILED,
    SourceSnapshotEventType.VALIDATED: SnapshotCommandReason.VALIDATED,
    SourceSnapshotEventType.APPROVED: SnapshotCommandReason.APPROVED,
    SourceSnapshotEventType.ACTIVATED: SnapshotCommandReason.ACTIVATED,
    SourceSnapshotEventType.ROLLED_BACK: SnapshotCommandReason.ROLLED_BACK,
}

IDEMPOTENT_REASON = {
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


def raw_object(
    content: bytes = b'{"declared_count":1}',
    *,
    retrieved_at: datetime = NOW,
) -> tuple[RawObjectMetadata, bytes]:
    return (
        RawObjectMetadata.from_bytes(
            deployment_id="demo-deployment",
            source_id="synthetic-source",
            original_name="synthetic.json",
            media_type="application/json",
            charset="utf-8",
            retrieved_at=retrieved_at,
            effective_from=retrieved_at - timedelta(days=1),
            content=content,
        ),
        content,
    )


def parsed_snapshot(
    raw: RawObjectMetadata,
    *,
    value: str = "SYNTHETIC ENTITY",
    parsed_at: datetime | None = None,
) -> ParsedSourceSnapshot:
    return ParsedSourceSnapshot.create(
        raw_metadata=raw,
        parser_id="synthetic-json-v1",
        parser_version="1.0.0",
        schema_id=SCHEMA_ID,
        declared_record_count=1,
        parsed_at=parsed_at or raw.retrieved_at + timedelta(minutes=1),
        records=(
            ParsedRecordInput(
                source_record_id="record-1",
                native_locator="/records/0",
                effective_from="2026-08-01",
                effective_to=None,
                assertions=(
                    ParsedAssertionInput(
                        "entity.name",
                        "/records/0/name",
                        value,
                        value,
                    ),
                ),
            ),
        ),
    )


def report_for(
    snapshot: ParsedSourceSnapshot,
    previous: ParsedSourceSnapshot | None = None,
) -> ValidationReport:
    return validate_snapshot(
        snapshot,
        expected_schema_id=SCHEMA_ID,
        previous_accepted=previous,
        validated_at=snapshot.parsed_at + timedelta(minutes=1),
    )


def event(
    sequence: int,
    event_type: SourceSnapshotEventType,
    raw: RawObjectMetadata,
    snapshot: ParsedSourceSnapshot | None,
    *,
    previous_active: ParsedSourceSnapshot | None = None,
    report: ValidationReport | None = None,
    actor_id: str = "source-operator-1",
    actor_type: LifecycleActorType = LifecycleActorType.SERVICE,
    event_id: str | None = None,
) -> SourceSnapshotLifecycleEvent:
    return SourceSnapshotLifecycleEvent(
        sequence=sequence,
        event_id=event_id or f"snapshot-event-{sequence}",
        deployment_id=raw.deployment_id,
        source_id=raw.source_id,
        event_type=event_type,
        raw_object=raw.reference(),
        snapshot=snapshot.reference() if snapshot else None,
        previous_active_snapshot=(
            previous_active.reference() if previous_active else None
        ),
        actor_id=actor_id,
        actor_type=actor_type,
        reason=f"synthetic {event_type.value.lower()}",
        occurred_at=NOW + timedelta(minutes=10 + sequence),
        validation_report=report,
    )


def audit(
    lifecycle_event: SourceSnapshotLifecycleEvent,
    reason: SnapshotCommandReason,
    *,
    applied: bool,
    suffix: str = "",
) -> SnapshotCommandAuditRecord:
    snapshot = lifecycle_event.snapshot
    return SnapshotCommandAuditRecord(
        command_event_id=(
            f"command-{lifecycle_event.sequence}-{reason.value.lower()}{suffix}"
        ),
        authorization_event_id=f"authz-{lifecycle_event.sequence}{suffix}",
        lifecycle_event_id=lifecycle_event.event_id if applied else None,
        tenant_id="control-tenant",
        deployment_id=lifecycle_event.deployment_id,
        source_id=lifecycle_event.source_id,
        raw_object_id=lifecycle_event.raw_object.object_id,
        snapshot_id=snapshot.snapshot_id if snapshot else None,
        snapshot_content_hash=snapshot.content_hash if snapshot else None,
        actor_id=lifecycle_event.actor_id,
        actor_type=lifecycle_event.actor_type,
        operation=AUTHORIZATION_OPERATION_BY_EVENT_TYPE[lifecycle_event.event_type],
        outcome=SnapshotCommandOutcome.SUCCESS,
        reason=reason,
        occurred_at=lifecycle_event.occurred_at,
    )


def audit_pair(
    lifecycle_event: SourceSnapshotLifecycleEvent,
    *,
    suffix: str = "",
) -> tuple[SnapshotCommandAuditRecord, SnapshotCommandAuditRecord]:
    return (
        audit(
            lifecycle_event,
            APPLIED_REASON[lifecycle_event.event_type],
            applied=True,
            suffix=f"-applied{suffix}",
        ),
        audit(
            lifecycle_event,
            IDEMPOTENT_REASON[lifecycle_event.event_type],
            applied=False,
            suffix=f"-idempotent{suffix}",
        ),
    )


def failure_audit(
    lifecycle_event: SourceSnapshotLifecycleEvent,
    *,
    suffix: str = "",
) -> SnapshotCommandAuditRecord:
    return replace(
        audit(
            lifecycle_event,
            IDEMPOTENT_REASON[lifecycle_event.event_type],
            applied=False,
            suffix=suffix,
        ),
        command_event_id=f"command-failure{suffix}",
        outcome=SnapshotCommandOutcome.FAILURE,
        reason=SnapshotCommandReason.CONFLICT,
    )


def observation_for(
    lifecycle_event: SourceSnapshotLifecycleEvent,
    raw: RawObjectMetadata,
) -> SourceRuntimeObservation:
    assert lifecycle_event.snapshot is not None
    return SourceRuntimeObservation(
        deployment_id=raw.deployment_id,
        source_id=raw.source_id,
        availability=SourceAvailability.AVAILABLE,
        observed_at=lifecycle_event.occurred_at,
        active_snapshot_id=lifecycle_event.snapshot.snapshot_id,
        retrieved_at=raw.retrieved_at,
        effective_from=raw.effective_from,
    )


def persist_active(
    repository: InMemorySourceSnapshotRepository,
    raw: RawObjectMetadata,
    snapshot: ParsedSourceSnapshot,
    *,
    start: int = 1,
    previous: ParsedSourceSnapshot | None = None,
) -> tuple[SourceSnapshotLifecycleEvent, ...]:
    report = report_for(snapshot, previous)
    events = (
        event(start, SourceSnapshotEventType.RETRIEVED, raw, None),
        event(start + 1, SourceSnapshotEventType.QUARANTINED, raw, None),
        event(start + 2, SourceSnapshotEventType.PARSED, raw, snapshot),
        event(
            start + 3,
            SourceSnapshotEventType.VALIDATED,
            raw,
            snapshot,
            report=report,
        ),
        event(
            start + 4,
            SourceSnapshotEventType.APPROVED,
            raw,
            snapshot,
            actor_id="source-approver-1",
            actor_type=LifecycleActorType.HUMAN,
        ),
        event(
            start + 5,
            SourceSnapshotEventType.ACTIVATED,
            raw,
            snapshot,
            previous_active=previous,
            actor_id="source-approver-1",
            actor_type=LifecycleActorType.HUMAN,
        ),
    )
    assert (
        repository.save_retrieval_atomic(raw, events[0], *audit_pair(events[0]))
        is ArtifactWriteOutcome.APPLIED
    )
    assert (
        repository.append_lifecycle_atomic(events[1], *audit_pair(events[1]))
        is LifecycleWriteOutcome.APPLIED
    )
    assert (
        repository.save_parsed_snapshot_atomic(
            snapshot, events[2], *audit_pair(events[2])
        )
        is ArtifactWriteOutcome.APPLIED
    )
    assert (
        repository.append_lifecycle_atomic(events[3], *audit_pair(events[3]))
        is LifecycleWriteOutcome.APPLIED
    )
    assert (
        repository.append_lifecycle_atomic(events[4], *audit_pair(events[4]))
        is LifecycleWriteOutcome.APPLIED
    )
    assert (
        repository.activate_or_rollback_atomic(
            events[5], observation_for(events[5], raw), *audit_pair(events[5])
        )
        is LifecycleWriteOutcome.APPLIED
    )
    return events


def test_finite_parser_output_is_bounded_typed_and_has_closed_parser_inventory() -> (
    None
):
    output = FiniteParserOutput(SCHEMA_ID, 0, ())
    assert output.declared_record_count == 0
    assert output.schema_id == SCHEMA_ID
    with pytest.raises(ValueError, match="schema_id"):
        FiniteParserOutput("", 0, ())
    with pytest.raises(ValueError, match="record bound"):
        FiniteParserOutput(SCHEMA_ID, True, ())
    with pytest.raises(ValueError, match="typed immutable"):
        FiniteParserOutput(SCHEMA_ID, 0, [])  # type: ignore[arg-type]
    parsed_input = ParsedRecordInput("record-1", "/records/0", None, None, ())
    with pytest.raises(ValueError, match="record-count bound"):
        FiniteParserOutput(
            SCHEMA_ID,
            0,
            (parsed_input,) * 513,
        )


def test_object_store_is_exact_idempotent_verified_and_has_no_delete_export_api() -> (
    None
):
    store = InMemoryImmutableRawObjectStore()
    raw, content = raw_object()

    assert store.put_exact(raw, content) is ArtifactWriteOutcome.APPLIED
    assert store.put_exact(raw, content) is ArtifactWriteOutcome.IDEMPOTENT
    assert store.get_verified(raw.reference()) == content
    assert not hasattr(store, "delete")
    assert not hasattr(store, "export")


def test_object_store_fails_closed_on_missing_mismatch_corruption_and_injected_write() -> (
    None
):
    store = InMemoryImmutableRawObjectStore()
    raw, content = raw_object()
    with pytest.raises(RawObjectIntegrityError):
        store.get_verified(raw.reference())
    untyped: Any = "reference"
    with pytest.raises(ValueError, match="reference must be typed"):
        store.get_verified(untyped)
    with pytest.raises(RawObjectIntegrityError):
        store.put_exact(raw, content + b"changed")

    store.fail_next_write()
    with pytest.raises(SourceSnapshotPersistenceError):
        store.put_exact(raw, content)
    with pytest.raises(RawObjectIntegrityError):
        store.get_verified(raw.reference())

    assert store.put_exact(raw, content) is ArtifactWriteOutcome.APPLIED
    store.corrupt_bytes_for_test(raw.object_id, b"corrupt")
    with pytest.raises(RawObjectIntegrityError):
        store.get_verified(raw.reference())
    assert store.put_exact(raw, content) is ArtifactWriteOutcome.CONFLICT
    store.corrupt_bytes_for_test("missing", b"ignored")


def test_object_store_rejects_reference_metadata_mismatch() -> None:
    store = InMemoryImmutableRawObjectStore()
    raw, content = raw_object()
    other, _ = raw_object(b"other")
    store.put_exact(raw, content)
    mismatched = replace(other.reference(), object_id=raw.object_id)
    with pytest.raises(RawObjectIntegrityError):
        store.get_verified(mismatched)


def test_repository_happy_path_is_audited_bounded_and_observation_atomic() -> None:
    repository = InMemorySourceSnapshotRepository()
    raw, _ = raw_object()
    snapshot = parsed_snapshot(raw)
    events = persist_active(repository, raw, snapshot)

    stored_events, lifecycle = repository.get_lifecycle_snapshot(
        raw.deployment_id, raw.source_id
    )
    assert stored_events == events
    assert lifecycle.active_snapshot == snapshot.reference()
    assert lifecycle.state_for(snapshot.reference()) is SourceSnapshotState.ACTIVE
    assert (
        repository.get_raw_metadata(raw.deployment_id, raw.source_id, raw.object_id)
        == raw
    )
    assert (
        repository.get_snapshot(raw.deployment_id, raw.source_id, snapshot.snapshot_id)
        == snapshot
    )
    assert repository.list_snapshots(raw.deployment_id, raw.source_id, limit=1) == (
        snapshot,
    )
    assert (
        repository.list_lifecycle_events(
            raw.deployment_id, raw.source_id, limit=MAX_QUERY_LIMIT
        )
        == events
    )
    assert len(
        repository.list_command_audits(
            raw.deployment_id, raw.source_id, limit=MAX_QUERY_LIMIT
        )
    ) == len(events)
    assert repository.get_runtime_observation(
        raw.deployment_id, raw.source_id
    ) == observation_for(events[-1], raw)


def test_repository_exact_retries_append_only_idempotent_audits() -> None:
    repository = InMemorySourceSnapshotRepository()
    raw, _ = raw_object()
    snapshot = parsed_snapshot(raw)
    events = persist_active(repository, raw, snapshot)
    original_observation = repository.get_runtime_observation(
        raw.deployment_id, raw.source_id
    )

    retry_retrieve = event(7, SourceSnapshotEventType.RETRIEVED, raw, None)
    assert (
        repository.save_retrieval_atomic(
            raw, retry_retrieve, *audit_pair(retry_retrieve, suffix="-retry")
        )
        is ArtifactWriteOutcome.IDEMPOTENT
    )
    retry_parsed = event(7, SourceSnapshotEventType.PARSED, raw, snapshot)
    assert (
        repository.save_parsed_snapshot_atomic(
            snapshot, retry_parsed, *audit_pair(retry_parsed, suffix="-retry")
        )
        is ArtifactWriteOutcome.IDEMPOTENT
    )
    retry_approved = event(
        7,
        SourceSnapshotEventType.APPROVED,
        raw,
        snapshot,
        actor_id="source-approver-1",
        actor_type=LifecycleActorType.HUMAN,
    )
    assert (
        repository.append_lifecycle_atomic(
            retry_approved, *audit_pair(retry_approved, suffix="-retry")
        )
        is LifecycleWriteOutcome.IDEMPOTENT
    )
    retry_active = event(
        7,
        SourceSnapshotEventType.ACTIVATED,
        raw,
        snapshot,
        actor_id="source-approver-1",
        actor_type=LifecycleActorType.HUMAN,
    )
    assert (
        repository.activate_or_rollback_atomic(
            retry_active,
            observation_for(retry_active, raw),
            *audit_pair(retry_active, suffix="-retry"),
        )
        is LifecycleWriteOutcome.IDEMPOTENT
    )

    assert (
        repository.list_lifecycle_events(
            raw.deployment_id, raw.source_id, limit=MAX_QUERY_LIMIT
        )
        == events
    )
    assert (
        repository.get_runtime_observation(raw.deployment_id, raw.source_id)
        == original_observation
    )
    assert (
        len(
            repository.list_command_audits(
                raw.deployment_id, raw.source_id, limit=MAX_QUERY_LIMIT
            )
        )
        == len(events) + 4
    )


def test_two_versions_remain_immutable_and_rollback_updates_pointer_without_rewrite() -> (
    None
):
    repository = InMemorySourceSnapshotRepository()
    first_raw, _ = raw_object(b"first")
    first = parsed_snapshot(first_raw, value="FIRST")
    persist_active(repository, first_raw, first)
    second_raw, _ = raw_object(b"second", retrieved_at=NOW + timedelta(minutes=1))
    second = parsed_snapshot(second_raw, value="SECOND")
    second_events = persist_active(
        repository,
        second_raw,
        second,
        start=7,
        previous=first,
    )
    rollback = event(
        13,
        SourceSnapshotEventType.ROLLED_BACK,
        first_raw,
        first,
        previous_active=second,
        actor_id="source-approver-2",
        actor_type=LifecycleActorType.HUMAN,
    )

    assert (
        repository.activate_or_rollback_atomic(
            rollback,
            observation_for(rollback, first_raw),
            *audit_pair(rollback),
        )
        is LifecycleWriteOutcome.APPLIED
    )
    _, lifecycle = repository.get_lifecycle_snapshot(
        first_raw.deployment_id, first_raw.source_id
    )
    assert lifecycle.active_snapshot == first.reference()
    assert lifecycle.state_for(second.reference()) is SourceSnapshotState.ROLLED_BACK
    assert (
        repository.get_snapshot(
            first_raw.deployment_id, first_raw.source_id, first.snapshot_id
        )
        == first
    )
    assert (
        repository.get_snapshot(
            second_raw.deployment_id, second_raw.source_id, second.snapshot_id
        )
        == second
    )
    assert repository.get_runtime_observation(
        first_raw.deployment_id, first_raw.source_id
    ) == observation_for(rollback, first_raw)
    assert second_events[-1].snapshot == second.reference()


def test_atomic_failure_leaves_events_audits_and_observation_unchanged() -> None:
    repository = InMemorySourceSnapshotRepository()
    raw, _ = raw_object()
    retrieved = event(1, SourceSnapshotEventType.RETRIEVED, raw, None)
    repository.save_retrieval_atomic(raw, retrieved, *audit_pair(retrieved))
    before_events = repository.list_lifecycle_events(
        raw.deployment_id, raw.source_id, limit=MAX_QUERY_LIMIT
    )
    before_audits = repository.list_command_audits(
        raw.deployment_id, raw.source_id, limit=MAX_QUERY_LIMIT
    )
    quarantined = event(2, SourceSnapshotEventType.QUARANTINED, raw, None)

    repository.fail_next_atomic_write()
    with pytest.raises(SourceSnapshotPersistenceError):
        repository.append_lifecycle_atomic(quarantined, *audit_pair(quarantined))
    assert (
        repository.list_lifecycle_events(
            raw.deployment_id, raw.source_id, limit=MAX_QUERY_LIMIT
        )
        == before_events
    )
    assert (
        repository.list_command_audits(
            raw.deployment_id, raw.source_id, limit=MAX_QUERY_LIMIT
        )
        == before_audits
    )

    assert repository.get_runtime_observation(raw.deployment_id, raw.source_id) is None

    standalone = failure_audit(retrieved, suffix="-standalone")
    repository.fail_next_atomic_write()
    with pytest.raises(SourceSnapshotPersistenceError):
        repository.append_command_audit(standalone)
    assert (
        repository.list_command_audits(
            raw.deployment_id, raw.source_id, limit=MAX_QUERY_LIMIT
        )
        == before_audits
    )
    repository.append_command_audit(standalone)
    assert (
        repository.list_command_audits(
            raw.deployment_id, raw.source_id, limit=MAX_QUERY_LIMIT
        )[-1]
        == standalone
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        repository.append_command_audit(standalone)


@pytest.mark.parametrize("limit", [0, MAX_QUERY_LIMIT + 1])
def test_repository_read_limits_are_bounded(limit: int) -> None:
    repository = InMemorySourceSnapshotRepository()
    for read in (
        repository.list_snapshots,
        repository.list_lifecycle_events,
        repository.list_command_audits,
    ):
        with pytest.raises(ValueError, match="bounded query"):
            read("demo-deployment", "synthetic-source", limit=limit)


def test_missing_objects_return_none_but_corrupt_linked_state_fails_unavailable() -> (
    None
):
    repository = InMemorySourceSnapshotRepository()
    assert repository.get_raw_metadata("deployment", "source", "raw") is None
    assert repository.get_snapshot("deployment", "source", "snapshot") is None
    assert repository.get_runtime_observation("deployment", "source") is None

    raw, _ = raw_object()
    snapshot = parsed_snapshot(raw)
    persist_active(repository, raw, snapshot)
    repository.corrupt_remove_snapshot_for_test(
        raw.deployment_id, raw.source_id, snapshot.snapshot_id
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        repository.get_lifecycle_snapshot(raw.deployment_id, raw.source_id)

    repository = InMemorySourceSnapshotRepository()
    persist_active(repository, raw, snapshot)
    repository.corrupt_remove_raw_metadata_for_test(
        raw.deployment_id, raw.source_id, raw.object_id
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        repository.list_lifecycle_events(
            raw.deployment_id, raw.source_id, limit=MAX_QUERY_LIMIT
        )


def test_corrupt_history_or_observation_fails_verified_projection_reads() -> None:
    repository = InMemorySourceSnapshotRepository()
    raw, _ = raw_object()
    snapshot = parsed_snapshot(raw)
    events = persist_active(repository, raw, snapshot)

    repository.corrupt_replace_events_for_test(
        raw.deployment_id, raw.source_id, events[:-1]
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        repository.get_lifecycle_snapshot(raw.deployment_id, raw.source_id)

    repository = InMemorySourceSnapshotRepository()
    events = persist_active(repository, raw, snapshot)
    corrupted_observation = replace(
        observation_for(events[-1], raw), active_snapshot_id="snapshot-corrupt"
    )
    repository.corrupt_replace_observation_for_test(corrupted_observation)
    with pytest.raises(SourceSnapshotPersistenceError):
        repository.get_runtime_observation(raw.deployment_id, raw.source_id)


def test_repository_rejects_wrong_atomic_method_shapes_and_missing_raw_reference() -> (
    None
):
    repository = InMemorySourceSnapshotRepository()
    raw, _ = raw_object()
    snapshot = parsed_snapshot(raw)
    quarantine = event(1, SourceSnapshotEventType.QUARANTINED, raw, None)
    with pytest.raises(ValueError, match="retrieval event"):
        repository.save_retrieval_atomic(raw, quarantine, *audit_pair(quarantine))

    parsed = event(1, SourceSnapshotEventType.PARSED, raw, snapshot)
    with pytest.raises(SourceSnapshotPersistenceError):
        repository.save_parsed_snapshot_atomic(snapshot, parsed, *audit_pair(parsed))

    retrieved = event(1, SourceSnapshotEventType.RETRIEVED, raw, None)
    repository.save_retrieval_atomic(raw, retrieved, *audit_pair(retrieved))
    quarantined = event(2, SourceSnapshotEventType.QUARANTINED, raw, None)
    repository.append_lifecycle_atomic(quarantined, *audit_pair(quarantined))
    approved = event(
        3,
        SourceSnapshotEventType.APPROVED,
        raw,
        snapshot,
        actor_id="source-approver-1",
        actor_type=LifecycleActorType.HUMAN,
    )
    with pytest.raises(ValueError, match="parsed event"):
        repository.save_parsed_snapshot_atomic(
            snapshot, approved, *audit_pair(approved)
        )
    with pytest.raises(ValueError, match="explicit atomic"):
        repository.append_lifecycle_atomic(
            retrieved, *audit_pair(retrieved, suffix="-x")
        )
    with pytest.raises(ValueError, match="only activation or rollback"):
        repository.activate_or_rollback_atomic(
            quarantined,
            SourceRuntimeObservation(
                raw.deployment_id,
                raw.source_id,
                SourceAvailability.QUARANTINED,
                quarantined.occurred_at,
            ),
            *audit_pair(quarantined, suffix="-activation"),
        )


def test_repository_returns_conflict_for_invalid_new_artifact_or_lifecycle_order() -> (
    None
):
    repository = InMemorySourceSnapshotRepository()
    raw, _ = raw_object()
    first = event(1, SourceSnapshotEventType.RETRIEVED, raw, None)
    repository.save_retrieval_atomic(raw, first, *audit_pair(first))
    second_raw, _ = raw_object(b"second")
    wrong_sequence = event(
        1,
        SourceSnapshotEventType.RETRIEVED,
        second_raw,
        None,
        event_id="second-retrieval",
    )
    assert (
        repository.save_retrieval_atomic(
            second_raw, wrong_sequence, *audit_pair(wrong_sequence)
        )
        is ArtifactWriteOutcome.CONFLICT
    )

    quarantine = event(2, SourceSnapshotEventType.QUARANTINED, raw, None)
    repository.append_lifecycle_atomic(quarantine, *audit_pair(quarantine))
    snapshot = parsed_snapshot(raw)
    parsed = event(3, SourceSnapshotEventType.PARSED, raw, snapshot)
    repository.save_parsed_snapshot_atomic(snapshot, parsed, *audit_pair(parsed))
    approve_before_validation = event(
        4,
        SourceSnapshotEventType.APPROVED,
        raw,
        snapshot,
        actor_id="source-approver-1",
        actor_type=LifecycleActorType.HUMAN,
    )
    assert (
        repository.append_lifecycle_atomic(
            approve_before_validation,
            *audit_pair(approve_before_validation),
        )
        is LifecycleWriteOutcome.CONFLICT
    )


def test_existing_artifact_corruption_turns_retry_into_conflict_or_unavailable() -> (
    None
):
    repository = InMemorySourceSnapshotRepository()
    raw, _ = raw_object()
    retrieved = event(1, SourceSnapshotEventType.RETRIEVED, raw, None)
    repository.save_retrieval_atomic(raw, retrieved, *audit_pair(retrieved))
    other_raw, _ = raw_object(b"other")
    object.__setattr__(other_raw, "object_id", raw.object_id)
    conflicting = event(2, SourceSnapshotEventType.RETRIEVED, other_raw, None)
    assert (
        repository.save_retrieval_atomic(
            other_raw, conflicting, *audit_pair(conflicting)
        )
        is ArtifactWriteOutcome.CONFLICT
    )

    original_id = raw.object_id
    repository.corrupt_remove_raw_metadata_for_test(
        raw.deployment_id, raw.source_id, original_id
    )
    object.__setattr__(raw, "object_id", "raw-corrupt")
    repository.corrupt_store_raw_metadata_for_test(
        raw.deployment_id, raw.source_id, raw.object_id, raw
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        repository.get_raw_metadata(raw.deployment_id, raw.source_id, raw.object_id)


def test_corrupt_snapshot_hash_fails_direct_and_listing_reads() -> None:
    repository = InMemorySourceSnapshotRepository()
    raw, _ = raw_object()
    snapshot = parsed_snapshot(raw)
    persist_active(repository, raw, snapshot)
    object.__setattr__(snapshot, "content_hash", "sha256:" + "0" * 64)
    with pytest.raises(SourceSnapshotPersistenceError):
        repository.get_snapshot(raw.deployment_id, raw.source_id, snapshot.snapshot_id)
    with pytest.raises(SourceSnapshotPersistenceError):
        repository.list_snapshots(raw.deployment_id, raw.source_id, limit=1)


@pytest.mark.parametrize("corruption", ["record_key", "assertion_id"])
def test_snapshot_reads_reject_internal_identifier_corruption(
    corruption: str,
) -> None:
    repository = InMemorySourceSnapshotRepository()
    raw, _ = raw_object()
    snapshot = parsed_snapshot(raw)
    persist_active(repository, raw, snapshot)
    if corruption == "record_key":
        object.__setattr__(snapshot.records[0], "record_key", "record-corrupt")
    else:
        object.__setattr__(
            snapshot.records[0].assertions[0],
            "assertion_id",
            "assertion-corrupt",
        )

    with pytest.raises(SourceSnapshotPersistenceError):
        repository.get_snapshot(raw.deployment_id, raw.source_id, snapshot.snapshot_id)
    with pytest.raises(SourceSnapshotPersistenceError):
        repository.get_lifecycle_snapshot(raw.deployment_id, raw.source_id)


def test_verified_reads_reject_malformed_typed_and_untyped_artifact_rows() -> None:
    raw, _ = raw_object()
    repository = InMemorySourceSnapshotRepository()
    retrieved = event(1, SourceSnapshotEventType.RETRIEVED, raw, None)
    repository.save_retrieval_atomic(raw, retrieved, *audit_pair(retrieved))
    object.__setattr__(raw, "retrieved_at", "not-a-datetime")
    with pytest.raises(SourceSnapshotPersistenceError):
        repository.get_raw_metadata(raw.deployment_id, raw.source_id, raw.object_id)

    untyped: Any = "not-a-snapshot"
    repository.corrupt_store_snapshot_for_test(
        "empty-deployment", "empty-source", "snapshot-untyped", untyped
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        repository.get_snapshot("empty-deployment", "empty-source", "snapshot-untyped")


def test_observation_without_active_lifecycle_fails_verified_read() -> None:
    repository = InMemorySourceSnapshotRepository()
    raw, _ = raw_object()
    snapshot = parsed_snapshot(raw)
    events = persist_active(repository, raw, snapshot)
    audits = repository.list_command_audits(
        raw.deployment_id, raw.source_id, limit=MAX_QUERY_LIMIT
    )
    repository.corrupt_replace_events_for_test(
        raw.deployment_id, raw.source_id, events[:-1]
    )
    repository.corrupt_replace_audits_for_test(
        raw.deployment_id, raw.source_id, audits[:-1]
    )

    with pytest.raises(SourceSnapshotPersistenceError):
        repository.get_lifecycle_snapshot(raw.deployment_id, raw.source_id)


def test_verified_reads_fail_on_invalid_history_missing_audit_or_observation() -> None:
    raw, _ = raw_object()
    snapshot = parsed_snapshot(raw)

    repository = InMemorySourceSnapshotRepository()
    events = persist_active(repository, raw, snapshot)
    repository.corrupt_replace_events_for_test(
        raw.deployment_id,
        raw.source_id,
        (events[0], replace(events[1], sequence=3)),
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        repository.get_lifecycle_snapshot(raw.deployment_id, raw.source_id)

    repository = InMemorySourceSnapshotRepository()
    events = persist_active(repository, raw, snapshot)
    repository.corrupt_replace_audits_for_test(
        raw.deployment_id,
        raw.source_id,
        repository.list_command_audits(
            raw.deployment_id, raw.source_id, limit=MAX_QUERY_LIMIT
        )[1:],
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        repository.get_lifecycle_snapshot(raw.deployment_id, raw.source_id)

    repository = InMemorySourceSnapshotRepository()
    persist_active(repository, raw, snapshot)
    repository.corrupt_remove_observation_for_test(raw.deployment_id, raw.source_id)
    with pytest.raises(SourceSnapshotPersistenceError):
        repository.get_runtime_observation(raw.deployment_id, raw.source_id)


def test_verified_reads_fail_on_mismatched_applied_audit() -> None:
    repository = InMemorySourceSnapshotRepository()
    raw, _ = raw_object()
    snapshot = parsed_snapshot(raw)
    persist_active(repository, raw, snapshot)
    audits = repository.list_command_audits(
        raw.deployment_id, raw.source_id, limit=MAX_QUERY_LIMIT
    )
    repository.corrupt_replace_audits_for_test(
        raw.deployment_id,
        raw.source_id,
        (replace(audits[0], actor_id="other-actor"), *audits[1:]),
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        repository.get_lifecycle_snapshot(raw.deployment_id, raw.source_id)


def test_corrupt_artifact_history_makes_idempotent_retries_fail_or_conflict() -> None:
    raw, _ = raw_object()
    snapshot = parsed_snapshot(raw)

    repository = InMemorySourceSnapshotRepository()
    retrieved = event(1, SourceSnapshotEventType.RETRIEVED, raw, None)
    repository.save_retrieval_atomic(raw, retrieved, *audit_pair(retrieved))
    repository.corrupt_replace_events_for_test(raw.deployment_id, raw.source_id, ())
    retry_retrieved = event(2, SourceSnapshotEventType.RETRIEVED, raw, None)
    with pytest.raises(SourceSnapshotPersistenceError):
        repository.save_retrieval_atomic(
            raw, retry_retrieved, *audit_pair(retry_retrieved, suffix="-retry")
        )

    repository = InMemorySourceSnapshotRepository()
    retrieved = event(1, SourceSnapshotEventType.RETRIEVED, raw, None)
    quarantined = event(2, SourceSnapshotEventType.QUARANTINED, raw, None)
    parsed = event(3, SourceSnapshotEventType.PARSED, raw, snapshot)
    repository.save_retrieval_atomic(raw, retrieved, *audit_pair(retrieved))
    repository.append_lifecycle_atomic(quarantined, *audit_pair(quarantined))
    repository.save_parsed_snapshot_atomic(snapshot, parsed, *audit_pair(parsed))
    repository.corrupt_replace_events_for_test(
        raw.deployment_id, raw.source_id, (retrieved, quarantined)
    )
    retry_parsed = event(4, SourceSnapshotEventType.PARSED, raw, snapshot)
    with pytest.raises(SourceSnapshotPersistenceError):
        repository.save_parsed_snapshot_atomic(
            snapshot, retry_parsed, *audit_pair(retry_parsed, suffix="-retry")
        )

    repository.corrupt_replace_events_for_test(
        raw.deployment_id, raw.source_id, (retrieved, quarantined, parsed)
    )
    object.__setattr__(snapshot, "records", ())
    retry_conflict = event(4, SourceSnapshotEventType.PARSED, raw, snapshot)
    assert (
        repository.save_parsed_snapshot_atomic(
            snapshot,
            retry_conflict,
            *audit_pair(retry_conflict, suffix="-conflict"),
        )
        is ArtifactWriteOutcome.CONFLICT
    )


def test_new_snapshot_with_wrong_sequence_returns_conflict() -> None:
    repository = InMemorySourceSnapshotRepository()
    raw, _ = raw_object()
    retrieved = event(1, SourceSnapshotEventType.RETRIEVED, raw, None)
    quarantined = event(2, SourceSnapshotEventType.QUARANTINED, raw, None)
    repository.save_retrieval_atomic(raw, retrieved, *audit_pair(retrieved))
    repository.append_lifecycle_atomic(quarantined, *audit_pair(quarantined))
    snapshot = parsed_snapshot(raw)
    wrong_sequence = event(4, SourceSnapshotEventType.PARSED, raw, snapshot)

    assert (
        repository.save_parsed_snapshot_atomic(
            snapshot, wrong_sequence, *audit_pair(wrong_sequence)
        )
        is ArtifactWriteOutcome.CONFLICT
    )


def test_activation_conflict_and_duplicate_event_identity_are_explicit() -> None:
    repository = InMemorySourceSnapshotRepository()
    raw, _ = raw_object()
    snapshot = parsed_snapshot(raw)
    events = persist_active(repository, raw, snapshot)
    conflicting = event(
        7,
        SourceSnapshotEventType.ACTIVATED,
        raw,
        snapshot,
        previous_active=snapshot,
        actor_id="source-approver-1",
        actor_type=LifecycleActorType.HUMAN,
    )
    assert (
        repository.activate_or_rollback_atomic(
            conflicting,
            observation_for(conflicting, raw),
            *audit_pair(conflicting, suffix="-conflict"),
        )
        is LifecycleWriteOutcome.CONFLICT
    )

    duplicate_id = event(
        7,
        SourceSnapshotEventType.APPROVED,
        raw,
        snapshot,
        actor_id="source-approver-1",
        actor_type=LifecycleActorType.HUMAN,
        event_id=events[4].event_id,
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        repository.append_lifecycle_atomic(
            duplicate_id, *audit_pair(duplicate_id, suffix="-duplicate")
        )


def test_activation_observation_must_be_typed_and_strictly_monotonic() -> None:
    repository = InMemorySourceSnapshotRepository()
    first_raw, _ = raw_object(b"first")
    first = parsed_snapshot(first_raw, value="FIRST")
    first_events = persist_active(repository, first_raw, first)
    retry = event(
        7,
        SourceSnapshotEventType.ACTIVATED,
        first_raw,
        first,
        actor_id="source-approver-1",
        actor_type=LifecycleActorType.HUMAN,
    )
    untyped: Any = "observation"
    with pytest.raises(ValueError, match="observation must be typed"):
        repository.activate_or_rollback_atomic(
            retry, untyped, *audit_pair(retry, suffix="-untyped")
        )

    second_raw, _ = raw_object(b"second", retrieved_at=NOW + timedelta(minutes=1))
    second = parsed_snapshot(second_raw, value="SECOND")
    second_report = report_for(second, first)
    shared_time = first_events[-1].occurred_at
    second_events = tuple(
        replace(candidate, occurred_at=shared_time)
        for candidate in (
            event(7, SourceSnapshotEventType.RETRIEVED, second_raw, None),
            event(8, SourceSnapshotEventType.QUARANTINED, second_raw, None),
            event(9, SourceSnapshotEventType.PARSED, second_raw, second),
            event(
                10,
                SourceSnapshotEventType.VALIDATED,
                second_raw,
                second,
                report=second_report,
            ),
            event(
                11,
                SourceSnapshotEventType.APPROVED,
                second_raw,
                second,
                actor_id="source-approver-1",
                actor_type=LifecycleActorType.HUMAN,
            ),
            event(
                12,
                SourceSnapshotEventType.ACTIVATED,
                second_raw,
                second,
                previous_active=first,
                actor_id="source-approver-1",
                actor_type=LifecycleActorType.HUMAN,
            ),
        )
    )
    repository.save_retrieval_atomic(
        second_raw, second_events[0], *audit_pair(second_events[0])
    )
    repository.append_lifecycle_atomic(second_events[1], *audit_pair(second_events[1]))
    repository.save_parsed_snapshot_atomic(
        second, second_events[2], *audit_pair(second_events[2])
    )
    repository.append_lifecycle_atomic(second_events[3], *audit_pair(second_events[3]))
    repository.append_lifecycle_atomic(second_events[4], *audit_pair(second_events[4]))

    assert (
        repository.activate_or_rollback_atomic(
            second_events[5],
            observation_for(second_events[5], second_raw),
            *audit_pair(second_events[5]),
        )
        is LifecycleWriteOutcome.CONFLICT
    )


def test_artifact_reads_reject_scope_swaps_and_unreferenced_metadata_rows() -> None:
    repository = InMemorySourceSnapshotRepository()
    raw, _ = raw_object()
    snapshot = parsed_snapshot(raw)
    persist_active(repository, raw, snapshot)

    repository.corrupt_store_raw_metadata_for_test(
        "other-deployment", raw.source_id, raw.object_id, raw
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        repository.get_raw_metadata("other-deployment", raw.source_id, raw.object_id)
    with pytest.raises(SourceSnapshotPersistenceError):
        repository.get_lifecycle_snapshot("other-deployment", raw.source_id)

    repository.corrupt_store_snapshot_for_test(
        raw.deployment_id, "other-source", snapshot.snapshot_id, snapshot
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        repository.get_snapshot(raw.deployment_id, "other-source", snapshot.snapshot_id)
    with pytest.raises(SourceSnapshotPersistenceError):
        repository.list_snapshots(raw.deployment_id, "other-source", limit=1)

    orphan_raw, _ = raw_object(b"orphan")
    repository.corrupt_store_raw_metadata_for_test(
        raw.deployment_id, raw.source_id, orphan_raw.object_id, orphan_raw
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        repository.get_lifecycle_snapshot(raw.deployment_id, raw.source_id)

    repository = InMemorySourceSnapshotRepository()
    persist_active(repository, raw, snapshot)
    orphan_snapshot = parsed_snapshot(raw, value="ORPHAN")
    repository.corrupt_store_snapshot_for_test(
        raw.deployment_id,
        raw.source_id,
        orphan_snapshot.snapshot_id,
        orphan_snapshot,
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        repository.get_lifecycle_snapshot(raw.deployment_id, raw.source_id)


def test_verified_audit_listing_rejects_scope_duplicates_and_dangling_links() -> None:
    raw, _ = raw_object()
    snapshot = parsed_snapshot(raw)
    repository = InMemorySourceSnapshotRepository()
    persist_active(repository, raw, snapshot)
    audits = repository.list_command_audits(
        raw.deployment_id, raw.source_id, limit=MAX_QUERY_LIMIT
    )

    repository.corrupt_replace_audits_for_test(
        raw.deployment_id,
        raw.source_id,
        (replace(audits[0], deployment_id="other-deployment"), *audits[1:]),
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        repository.list_command_audits(
            raw.deployment_id, raw.source_id, limit=MAX_QUERY_LIMIT
        )

    repository.corrupt_replace_audits_for_test(
        raw.deployment_id,
        raw.source_id,
        (
            audits[0],
            replace(audits[1], command_event_id=audits[0].command_event_id),
            *audits[2:],
        ),
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        repository.list_command_audits(
            raw.deployment_id, raw.source_id, limit=MAX_QUERY_LIMIT
        )

    dangling = replace(
        audits[0],
        command_event_id="command-dangling",
        lifecycle_event_id="snapshot-event-missing",
    )
    repository.corrupt_replace_audits_for_test(
        raw.deployment_id, raw.source_id, (*audits, dangling)
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        repository.list_command_audits(
            raw.deployment_id, raw.source_id, limit=MAX_QUERY_LIMIT
        )


def test_corrupt_extra_audit_fails_lifecycle_read_and_idempotent_retry() -> None:
    repository = InMemorySourceSnapshotRepository()
    raw, _ = raw_object()
    retrieved = event(1, SourceSnapshotEventType.RETRIEVED, raw, None)
    repository.save_retrieval_atomic(raw, retrieved, *audit_pair(retrieved))
    standalone = failure_audit(retrieved, suffix="-corrupt")
    repository.append_command_audit(standalone)
    audits = repository.list_command_audits(
        raw.deployment_id, raw.source_id, limit=MAX_QUERY_LIMIT
    )
    repository.corrupt_replace_audits_for_test(
        raw.deployment_id,
        raw.source_id,
        (*audits[:-1], replace(standalone, deployment_id="other-deployment")),
    )

    with pytest.raises(SourceSnapshotPersistenceError):
        repository.get_lifecycle_snapshot(raw.deployment_id, raw.source_id)

    retry = event(2, SourceSnapshotEventType.RETRIEVED, raw, None)
    with pytest.raises(SourceSnapshotPersistenceError):
        repository.save_retrieval_atomic(
            raw,
            retry,
            *audit_pair(retry, suffix="-corrupt-retry"),
        )


def test_verified_idempotent_audit_rejects_wrong_authorized_operation() -> None:
    repository = InMemorySourceSnapshotRepository()
    raw, _ = raw_object()
    retrieved = event(1, SourceSnapshotEventType.RETRIEVED, raw, None)
    repository.save_retrieval_atomic(raw, retrieved, *audit_pair(retrieved))
    audits = repository.list_command_audits(
        raw.deployment_id, raw.source_id, limit=MAX_QUERY_LIMIT
    )
    corrupted = replace(
        audit(
            retrieved,
            SnapshotCommandReason.RETRIEVE_IDEMPOTENT,
            applied=False,
            suffix="-wrong-operation",
        ),
        operation="SOURCE_SNAPSHOT_PARSE",
    )
    repository.corrupt_replace_audits_for_test(
        raw.deployment_id, raw.source_id, (*audits, corrupted)
    )

    with pytest.raises(SourceSnapshotPersistenceError):
        repository.get_lifecycle_snapshot(raw.deployment_id, raw.source_id)


def test_only_standalone_unlinked_failure_audit_is_valid_for_empty_scope() -> None:
    repository = InMemorySourceSnapshotRepository()
    raw, _ = raw_object()
    retrieved = event(1, SourceSnapshotEventType.RETRIEVED, raw, None)
    fabricated_success = audit(
        retrieved, SnapshotCommandReason.RETRIEVE_IDEMPOTENT, applied=False
    )
    standalone = failure_audit(retrieved, suffix="-empty-scope")

    with pytest.raises(ValueError, match="unlinked failures"):
        repository.append_command_audit(fabricated_success)

    repository.append_command_audit(standalone)

    assert repository.list_command_audits(
        raw.deployment_id, raw.source_id, limit=1
    ) == (standalone,)

    assert (
        InMemorySourceSnapshotRepository().list_command_audits(
            "empty-deployment", "empty-source", limit=1
        )
        == ()
    )


def test_verified_empty_scope_rejects_corrupt_unlinked_success_audit() -> None:
    repository = InMemorySourceSnapshotRepository()
    raw, _ = raw_object()
    retrieved = event(1, SourceSnapshotEventType.RETRIEVED, raw, None)
    fabricated_success = audit(
        retrieved, SnapshotCommandReason.RETRIEVE_IDEMPOTENT, applied=False
    )
    repository.corrupt_replace_audits_for_test(
        raw.deployment_id, raw.source_id, (fabricated_success,)
    )

    with pytest.raises(SourceSnapshotPersistenceError):
        repository.get_lifecycle_snapshot(raw.deployment_id, raw.source_id)
    with pytest.raises(SourceSnapshotPersistenceError):
        repository.list_command_audits(
            raw.deployment_id, raw.source_id, limit=MAX_QUERY_LIMIT
        )


def test_lifecycle_append_rejects_event_whose_raw_metadata_is_missing() -> None:
    repository = InMemorySourceSnapshotRepository()
    raw, _ = raw_object()
    quarantine = event(1, SourceSnapshotEventType.QUARANTINED, raw, None)

    with pytest.raises(SourceSnapshotPersistenceError):
        repository.append_lifecycle_atomic(quarantine, *audit_pair(quarantine))
