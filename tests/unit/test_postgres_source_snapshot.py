"""PostgreSQL source-snapshot UoW, SQL, corruption, and concurrency tests."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any, Self, cast

import psycopg
import pytest
from psycopg import Connection

import tradesieve.adapters.postgres_source_snapshot as postgres_source_snapshot
from tradesieve.adapters.postgres_source_snapshot import (
    APPLIED_REASONS,
    IDEMPOTENT_REASONS,
    MAX_PERSISTED_AUDITS,
    PostgresSourceSnapshotRepository,
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
    AUTHORIZATION_OPERATION_BY_EVENT_TYPE,
    ArtifactWriteOutcome,
    LifecycleActorType,
    LifecycleWriteOutcome,
    ParsedAssertionInput,
    ParsedRecordInput,
    ParsedSourceSnapshot,
    RawObjectMetadata,
    SnapshotCommandAuditRecord,
    SnapshotCommandOutcome,
    SnapshotCommandReason,
    SourceSnapshotEventType,
    SourceSnapshotLifecycleEvent,
    SourceSnapshotRef,
    ValidationReport,
    fold_source_snapshot_events,
    validate_snapshot,
)
from tradesieve.ports.source_snapshot import (
    SYNTHETIC_CHARSET,
    SYNTHETIC_MEDIA_TYPE,
    SYNTHETIC_PARSER_VERSION,
    SYNTHETIC_SCHEMA_ID,
    SourceSnapshotPersistenceError,
)

DEPLOYMENT_ID = "demo"
SOURCE_SET_ID = "synthetic-screening"
SOURCE_ID = "synthetic-source"
TENANT_ID = "tenant-1"
NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


class FakeResult:
    def __init__(self, rows: object = None, *, rowcount: int = -1) -> None:
        self.rows = [] if rows is None else rows
        self.rowcount = rowcount

    def fetchall(self) -> object:
        return self.rows


@dataclass
class FakeDatabase:
    registrations: dict[tuple[str, str], tuple[Any, ...]]
    raw: dict[tuple[str, str, str], tuple[Any, ...]]
    snapshots: dict[tuple[str, str, str], tuple[Any, ...]]
    validation: dict[tuple[str, str, str], tuple[Any, ...]]
    states: dict[tuple[str, str], tuple[Any, ...]]
    events: list[tuple[Any, ...]]
    audits: list[tuple[Any, ...]]
    authorization: dict[str, tuple[Any, ...]]
    observations: dict[tuple[str, str], tuple[Any, ...]]

    @classmethod
    def create(cls) -> FakeDatabase:
        return cls(
            registrations={
                (DEPLOYMENT_ID, SOURCE_ID): (SOURCE_SET_ID, True),
            },
            raw={},
            snapshots={},
            validation={},
            states={},
            events=[],
            audits=[],
            authorization={},
            observations={},
        )

    def snapshot(self) -> tuple[Any, ...]:
        return copy.deepcopy(
            (
                self.registrations,
                self.raw,
                self.snapshots,
                self.validation,
                self.states,
                self.events,
                self.audits,
                self.authorization,
                self.observations,
            )
        )

    def restore(self, snapshot: tuple[Any, ...]) -> None:
        (
            self.registrations,
            self.raw,
            self.snapshots,
            self.validation,
            self.states,
            self.events,
            self.audits,
            self.authorization,
            self.observations,
        ) = copy.deepcopy(snapshot)


class FakeTransaction:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.before: tuple[Any, ...] | None = None

    def __enter__(self) -> Self:
        self.connection.transaction_entries += 1
        self.before = self.connection.database.snapshot()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_value, traceback
        if exc_type is None:
            self.connection.commits += 1
            return
        assert self.before is not None
        self.connection.database.restore(self.before)
        self.connection.rollbacks += 1


class FakeConnection:
    def __init__(self, database: FakeDatabase | None = None) -> None:
        self.database = database or FakeDatabase.create()
        self.executed: list[tuple[str, object | None]] = []
        self.transaction_entries = 0
        self.commits = 0
        self.rollbacks = 0
        self.fail_once_query: str | None = None
        self.wrong_rowcount_query: str | None = None
        self.non_list_query: str | None = None
        self.drop_state_insert = False

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)

    def _result(
        self,
        query: str,
        rows: object = None,
        *,
        rowcount: int = -1,
    ) -> FakeResult:
        if self.wrong_rowcount_query is not None and self.wrong_rowcount_query in query:
            self.wrong_rowcount_query = None
            rowcount = 0
        if self.non_list_query is not None and self.non_list_query in query:
            self.non_list_query = None
            rows = tuple(cast(list[tuple[Any, ...]], rows or []))
        return FakeResult(rows, rowcount=rowcount)

    def execute(self, query: str, params: object | None = None) -> FakeResult:
        self.executed.append((query, params))
        if self.fail_once_query is not None and self.fail_once_query in query:
            self.fail_once_query = None
            raise psycopg.OperationalError("private database detail sentinel")
        values = cast(tuple[Any, ...], params or ())
        database = self.database

        if "FROM source_registry" in query:
            row = database.registrations.get(cast(tuple[str, str], values[:2]))
            return self._result(query, [] if row is None else [row])
        if query.startswith("INSERT INTO source_snapshot_lifecycle_state"):
            scope = cast(tuple[str, str], values[:2])
            inserted = scope not in database.states and not self.drop_state_insert
            if inserted:
                database.states[scope] = (0, None, None)
            return self._result(query, rowcount=1 if inserted else 0)
        if "FROM source_snapshot_lifecycle_state" in query:
            row = database.states.get(cast(tuple[str, str], values[:2]))
            return self._result(query, [] if row is None else [row])
        if query.startswith("UPDATE source_snapshot_lifecycle_state"):
            scope = cast(tuple[str, str], values[3:5])
            if scope not in database.states:
                return self._result(query, rowcount=0)
            database.states[scope] = values[:3]
            return self._result(query, rowcount=1)

        if query.startswith("INSERT INTO source_raw_object_metadata"):
            key = cast(tuple[str, str, str], values[:3])
            if key in database.raw:
                raise psycopg.errors.UniqueViolation("synthetic raw collision")
            database.raw[key] = values
            return self._result(query, rowcount=1)
        if "FROM source_raw_object_metadata" in query:
            scope = values[:2]
            rows = sorted(
                (row for key, row in database.raw.items() if key[:2] == scope),
                key=lambda row: row[2],
            )
            return self._result(query, rows[: int(values[2])])

        if query.startswith("INSERT INTO source_parsed_snapshot"):
            key = cast(tuple[str, str, str], values[:3])
            if key in database.snapshots:
                raise psycopg.errors.UniqueViolation("synthetic snapshot collision")
            database.snapshots[key] = values
            return self._result(query, rowcount=1)
        if "FROM source_parsed_snapshot" in query:
            scope = values[:2]
            rows = sorted(
                (row for key, row in database.snapshots.items() if key[:2] == scope),
                key=lambda row: row[2],
            )
            return self._result(query, rows[: int(values[2])])

        if query.startswith("INSERT INTO source_snapshot_validation_evidence"):
            key = cast(tuple[str, str, str], values[:3])
            if key in database.validation:
                raise psycopg.errors.UniqueViolation("synthetic validation collision")
            database.validation[key] = values
            return self._result(query, rowcount=1)
        if "FROM source_snapshot_validation_evidence" in query:
            scope = values[:2]
            rows = sorted(
                (row for key, row in database.validation.items() if key[:2] == scope),
                key=lambda row: row[2],
            )
            return self._result(query, rows[: int(values[2])])

        if query.startswith("INSERT INTO source_snapshot_lifecycle_event"):
            if any(row[3] == values[3] for row in database.events):
                raise psycopg.errors.UniqueViolation("synthetic event collision")
            database.events.append(values)
            return self._result(query, rowcount=1)
        if query.startswith("SELECT event_id FROM source_snapshot_lifecycle_event"):
            rows = [(row[3],) for row in database.events if row[3] == values[0]][:2]
            return self._result(query, rows)
        if "FROM source_snapshot_lifecycle_event" in query:
            scope = values[:2]
            rows = sorted(
                (row for row in database.events if row[:2] == scope),
                key=lambda row: row[2],
            )
            return self._result(query, rows[: int(values[2])])

        if query.startswith("INSERT INTO source_snapshot_command_audit"):
            if any(row[0] == values[0] for row in database.audits):
                raise psycopg.errors.UniqueViolation("synthetic audit collision")
            if values[2] is not None and any(
                row[2] == values[2] for row in database.audits
            ):
                raise psycopg.errors.UniqueViolation(
                    "synthetic lifecycle audit collision"
                )
            database.audits.append(values)
            return self._result(query, rowcount=1)
        if "FROM source_snapshot_command_audit" in query:
            scope = values[:2]
            rows = sorted(
                (row for row in database.audits if row[4:6] == scope),
                key=lambda row: (row[16], row[0]),
            )
            return self._result(query, rows[: int(values[2])])

        if "FROM authorization_audit_event" in query:
            row = database.authorization.get(cast(str, values[0]))
            return self._result(query, [] if row is None else [row])

        if query.startswith("INSERT INTO source_runtime_observation"):
            scope = cast(tuple[str, str], values[:2])
            if scope in database.observations:
                raise psycopg.errors.UniqueViolation("synthetic observation collision")
            database.observations[scope] = values[2:]
            return self._result(query, rowcount=1)
        if query.startswith("UPDATE source_runtime_observation"):
            scope = cast(tuple[str, str], values[5:7])
            current = database.observations.get(scope)
            if current is None or current[1] != values[7]:
                return self._result(query, rowcount=0)
            database.observations[scope] = values[:5]
            return self._result(query, rowcount=1)
        if "FROM source_runtime_observation" in query:
            row = database.observations.get(cast(tuple[str, str], values[:2]))
            return self._result(query, [] if row is None else [row])

        raise AssertionError(f"unexpected SQL: {query}")


def repository(connection: FakeConnection) -> PostgresSourceSnapshotRepository:
    return PostgresSourceSnapshotRepository(cast(Connection[Any], connection))


def raw_metadata(
    name: str = "first", *, retrieved_at: datetime = NOW
) -> RawObjectMetadata:
    return RawObjectMetadata.from_bytes(
        deployment_id=DEPLOYMENT_ID,
        source_id=SOURCE_ID,
        original_name=f"{name}.json",
        media_type=SYNTHETIC_MEDIA_TYPE,
        charset=SYNTHETIC_CHARSET,
        retrieved_at=retrieved_at,
        effective_from=retrieved_at + timedelta(days=1),
        content=f'{{"fixture":"{name}"}}'.encode(),
    )


def parsed_snapshot(
    metadata: RawObjectMetadata,
    *,
    record_id: str = "record-1",
    parsed_at: datetime | None = None,
) -> ParsedSourceSnapshot:
    return ParsedSourceSnapshot.create(
        raw_metadata=metadata,
        parser_id="synthetic-json-v1",
        parser_version=SYNTHETIC_PARSER_VERSION,
        schema_id=SYNTHETIC_SCHEMA_ID,
        declared_record_count=1,
        parsed_at=parsed_at or metadata.retrieved_at + timedelta(seconds=2),
        records=(
            ParsedRecordInput(
                source_record_id=record_id,
                native_locator="/records/0",
                effective_from="2026-08-01",
                effective_to=None,
                assertions=(
                    ParsedAssertionInput(
                        "name",
                        "/records/0/name",
                        f"Synthetic {record_id}",
                        f"synthetic {record_id}",
                    ),
                ),
            ),
        ),
    )


def lifecycle_event(
    event_type: SourceSnapshotEventType,
    sequence: int,
    metadata: RawObjectMetadata,
    *,
    snapshot: ParsedSourceSnapshot | None = None,
    previous: SourceSnapshotRef | None = None,
    report: ValidationReport | None = None,
    event_id: str | None = None,
    occurred_at: datetime | None = None,
    reason: str | None = None,
) -> SourceSnapshotLifecycleEvent:
    governance = event_type in {
        SourceSnapshotEventType.APPROVED,
        SourceSnapshotEventType.ACTIVATED,
        SourceSnapshotEventType.ROLLED_BACK,
    }
    return SourceSnapshotLifecycleEvent(
        sequence=sequence,
        event_id=event_id or f"event-{sequence}-{event_type.value.lower()}",
        deployment_id=DEPLOYMENT_ID,
        source_id=SOURCE_ID,
        event_type=event_type,
        raw_object=metadata.reference(),
        snapshot=snapshot.reference() if snapshot is not None else None,
        previous_active_snapshot=previous,
        actor_id="approver-1" if governance else "operator-1",
        actor_type=LifecycleActorType.HUMAN
        if governance
        else LifecycleActorType.SERVICE,
        reason=reason or f"Synthetic {event_type.value.lower()}.",
        occurred_at=occurred_at or NOW + timedelta(seconds=sequence),
        validation_report=report,
    )


def target_for_audit(
    audit: SnapshotCommandAuditRecord,
) -> tuple[str, str]:
    if audit.operation in {
        Operation.SOURCE_SNAPSHOT_INGEST.value,
        Operation.SOURCE_SNAPSHOT_PARSE.value,
        Operation.SOURCE_SNAPSHOT_VALIDATE.value,
    }:
        return (
            SOURCE_TARGET_TYPE,
            source_authorization_target_id(
                audit.deployment_id, SOURCE_SET_ID, audit.source_id
            ),
        )
    assert audit.snapshot_id is not None and audit.snapshot_content_hash is not None
    return (
        SOURCE_SNAPSHOT_TARGET_TYPE,
        source_snapshot_authorization_target_id(
            audit.deployment_id,
            SOURCE_SET_ID,
            audit.source_id,
            audit.snapshot_id,
            audit.snapshot_content_hash,
        ),
    )


def add_authorization(
    database: FakeDatabase,
    audit: SnapshotCommandAuditRecord,
    *,
    target: tuple[str, str] | None = None,
    outcome: str = "ALLOW",
) -> None:
    target_type, target_id = target or target_for_audit(audit)
    database.authorization[audit.authorization_event_id] = (
        audit.authorization_event_id,
        audit.actor_id,
        audit.actor_type.value,
        audit.tenant_id,
        audit.tenant_id,
        audit.tenant_id,
        audit.operation,
        f"{target_type}:{target_id}",
        outcome,
    )


def audit_pair(
    database: FakeDatabase,
    event: SourceSnapshotLifecycleEvent,
    prefix: str,
) -> tuple[SnapshotCommandAuditRecord, SnapshotCommandAuditRecord]:
    operation = AUTHORIZATION_OPERATION_BY_EVENT_TYPE[event.event_type]
    snapshot = event.snapshot
    applied = SnapshotCommandAuditRecord(
        command_event_id=f"command-{prefix}-applied",
        authorization_event_id=f"authorization-{prefix}-applied",
        lifecycle_event_id=event.event_id,
        tenant_id=TENANT_ID,
        deployment_id=event.deployment_id,
        source_id=event.source_id,
        raw_object_id=event.raw_object.object_id,
        snapshot_id=snapshot.snapshot_id if snapshot is not None else None,
        snapshot_content_hash=snapshot.content_hash if snapshot is not None else None,
        actor_id=event.actor_id,
        actor_type=event.actor_type,
        operation=operation,
        outcome=SnapshotCommandOutcome.SUCCESS,
        reason=APPLIED_REASONS[event.event_type],
        occurred_at=event.occurred_at,
    )
    idempotent = SnapshotCommandAuditRecord(
        command_event_id=f"command-{prefix}-idempotent",
        authorization_event_id=f"authorization-{prefix}-idempotent",
        lifecycle_event_id=None,
        tenant_id=TENANT_ID,
        deployment_id=event.deployment_id,
        source_id=event.source_id,
        raw_object_id=event.raw_object.object_id,
        snapshot_id=snapshot.snapshot_id if snapshot is not None else None,
        snapshot_content_hash=snapshot.content_hash if snapshot is not None else None,
        actor_id=event.actor_id,
        actor_type=event.actor_type,
        operation=operation,
        outcome=SnapshotCommandOutcome.SUCCESS,
        reason=IDEMPOTENT_REASONS[event.event_type],
        occurred_at=event.occurred_at + timedelta(microseconds=1),
    )
    add_authorization(database, applied)
    add_authorization(database, idempotent)
    return applied, idempotent


def save_retrieval(
    repo: PostgresSourceSnapshotRepository,
    database: FakeDatabase,
    metadata: RawObjectMetadata,
    *,
    sequence: int = 1,
    prefix: str = "retrieve",
    event_id: str | None = None,
) -> ArtifactWriteOutcome:
    event = lifecycle_event(
        SourceSnapshotEventType.RETRIEVED,
        sequence,
        metadata,
        event_id=event_id,
    )
    applied, idempotent = audit_pair(database, event, prefix)
    return repo.save_retrieval_atomic(metadata, event, applied, idempotent)


def append_event(
    repo: PostgresSourceSnapshotRepository,
    database: FakeDatabase,
    event: SourceSnapshotLifecycleEvent,
    prefix: str,
) -> LifecycleWriteOutcome:
    applied, idempotent = audit_pair(database, event, prefix)
    return repo.append_lifecycle_atomic(event, applied, idempotent)


def save_parsed(
    repo: PostgresSourceSnapshotRepository,
    database: FakeDatabase,
    snapshot: ParsedSourceSnapshot,
    *,
    sequence: int,
    prefix: str,
) -> ArtifactWriteOutcome:
    event = lifecycle_event(
        SourceSnapshotEventType.PARSED,
        sequence,
        raw_metadata_for_snapshot(database, snapshot),
        snapshot=snapshot,
    )
    applied, idempotent = audit_pair(database, event, prefix)
    return repo.save_parsed_snapshot_atomic(snapshot, event, applied, idempotent)


def raw_metadata_for_snapshot(
    database: FakeDatabase, snapshot: ParsedSourceSnapshot
) -> RawObjectMetadata:
    row = database.raw[
        (
            snapshot.deployment_id,
            snapshot.source_id,
            snapshot.raw_object.object_id,
        )
    ]
    return RawObjectMetadata.reconstitute(
        deployment_id=cast(str, row[0]),
        source_id=cast(str, row[1]),
        object_id=cast(str, row[2]),
        original_name=cast(str, row[3]),
        media_type=cast(str, row[4]),
        charset=cast(str, row[5]),
        retrieved_at=cast(datetime, row[6]),
        effective_from=cast(datetime | None, row[7]),
        byte_length=cast(int, row[8]),
        content_hash=cast(str, row[9]),
    )


def persist_parsed(
    repo: PostgresSourceSnapshotRepository,
    database: FakeDatabase,
    metadata: RawObjectMetadata,
    snapshot: ParsedSourceSnapshot,
    *,
    start_sequence: int = 1,
    prefix: str = "first",
) -> None:
    assert (
        save_retrieval(
            repo,
            database,
            metadata,
            sequence=start_sequence,
            prefix=f"{prefix}-retrieve",
        )
        is ArtifactWriteOutcome.APPLIED
    )
    quarantined = lifecycle_event(
        SourceSnapshotEventType.QUARANTINED,
        start_sequence + 1,
        metadata,
    )
    assert (
        append_event(repo, database, quarantined, f"{prefix}-quarantine")
        is LifecycleWriteOutcome.APPLIED
    )
    assert (
        save_parsed(
            repo,
            database,
            snapshot,
            sequence=start_sequence + 2,
            prefix=f"{prefix}-parse",
        )
        is ArtifactWriteOutcome.APPLIED
    )


def validate_and_approve(
    repo: PostgresSourceSnapshotRepository,
    database: FakeDatabase,
    metadata: RawObjectMetadata,
    snapshot: ParsedSourceSnapshot,
    *,
    sequence: int,
    previous: ParsedSourceSnapshot | None,
    prefix: str,
) -> ValidationReport:
    report = validate_snapshot(
        snapshot,
        expected_schema_id=SYNTHETIC_SCHEMA_ID,
        previous_accepted=previous,
        validated_at=NOW + timedelta(seconds=sequence),
    )
    validated = lifecycle_event(
        SourceSnapshotEventType.VALIDATED,
        sequence,
        metadata,
        snapshot=snapshot,
        report=report,
    )
    assert (
        append_event(repo, database, validated, f"{prefix}-validate")
        is LifecycleWriteOutcome.APPLIED
    )
    approved = lifecycle_event(
        SourceSnapshotEventType.APPROVED,
        sequence + 1,
        metadata,
        snapshot=snapshot,
    )
    assert (
        append_event(repo, database, approved, f"{prefix}-approve")
        is LifecycleWriteOutcome.APPLIED
    )
    return report


def pointer_write(
    repo: PostgresSourceSnapshotRepository,
    database: FakeDatabase,
    event: SourceSnapshotLifecycleEvent,
    metadata: RawObjectMetadata,
    prefix: str,
) -> LifecycleWriteOutcome:
    assert event.snapshot is not None
    observation = SourceRuntimeObservation(
        DEPLOYMENT_ID,
        SOURCE_ID,
        SourceAvailability.AVAILABLE,
        event.occurred_at,
        event.snapshot.snapshot_id,
        metadata.retrieved_at,
        metadata.effective_from,
    )
    applied, idempotent = audit_pair(database, event, prefix)
    return repo.activate_or_rollback_atomic(event, observation, applied, idempotent)


def activated_fixture() -> tuple[
    FakeConnection,
    PostgresSourceSnapshotRepository,
    RawObjectMetadata,
    ParsedSourceSnapshot,
    ValidationReport,
    SourceSnapshotLifecycleEvent,
]:
    connection = FakeConnection()
    repo = repository(connection)
    metadata = raw_metadata()
    snapshot = parsed_snapshot(metadata)
    persist_parsed(repo, connection.database, metadata, snapshot)
    report = validate_and_approve(
        repo,
        connection.database,
        metadata,
        snapshot,
        sequence=4,
        previous=None,
        prefix="fixture",
    )
    activation = lifecycle_event(
        SourceSnapshotEventType.ACTIVATED, 6, metadata, snapshot=snapshot
    )
    assert (
        pointer_write(
            repo, connection.database, activation, metadata, "fixture-activate"
        )
        is LifecycleWriteOutcome.APPLIED
    )
    return connection, repo, metadata, snapshot, report, activation


def test_retrieval_applies_with_exact_transaction_sql_target_and_reads() -> None:
    connection = FakeConnection()
    repo = repository(connection)
    metadata = raw_metadata()

    assert (
        save_retrieval(repo, connection.database, metadata)
        is ArtifactWriteOutcome.APPLIED
    )
    assert connection.transaction_entries == 1
    assert connection.commits == 1
    assert connection.rollbacks == 0
    queries = [query for query, _ in connection.executed]
    assert queries[0].endswith("LIMIT 2 FOR SHARE")
    assert "ON CONFLICT DO NOTHING" in queries[1]
    assert queries[2].endswith("LIMIT 2 FOR UPDATE")
    assert any(
        query.startswith("INSERT INTO source_raw_object_metadata") for query in queries
    )
    assert queries[-1].startswith("UPDATE source_snapshot_lifecycle_state")
    stored_audit = connection.database.audits[0]
    assert stored_audit[9:11] == (
        SOURCE_TARGET_TYPE,
        source_authorization_target_id(DEPLOYMENT_ID, SOURCE_SET_ID, SOURCE_ID),
    )
    assert (
        repo.get_raw_metadata(DEPLOYMENT_ID, SOURCE_ID, metadata.object_id) == metadata
    )
    assert repo.get_raw_metadata(DEPLOYMENT_ID, SOURCE_ID, "raw-missing") is None
    events, lifecycle = repo.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    assert len(events) == 1
    assert lifecycle.active_snapshot is None
    assert repo.list_lifecycle_events(DEPLOYMENT_ID, SOURCE_ID, limit=1) == events
    audits = repo.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=1)
    assert len(audits) == 1
    assert audits[0].reason is SnapshotCommandReason.RETRIEVED
    assert repo.get_runtime_observation(DEPLOYMENT_ID, SOURCE_ID) is None


def test_complete_first_snapshot_workflow_round_trips_private_documents() -> None:
    connection = FakeConnection()
    repo = repository(connection)
    metadata = raw_metadata()
    snapshot = parsed_snapshot(metadata)
    persist_parsed(repo, connection.database, metadata, snapshot)
    report = validate_and_approve(
        repo,
        connection.database,
        metadata,
        snapshot,
        sequence=4,
        previous=None,
        prefix="first",
    )
    activated = lifecycle_event(
        SourceSnapshotEventType.ACTIVATED,
        6,
        metadata,
        snapshot=snapshot,
    )
    assert (
        pointer_write(repo, connection.database, activated, metadata, "first-activate")
        is LifecycleWriteOutcome.APPLIED
    )

    assert repo.get_snapshot(DEPLOYMENT_ID, SOURCE_ID, snapshot.snapshot_id) == snapshot
    assert repo.get_snapshot(DEPLOYMENT_ID, SOURCE_ID, "snapshot-missing") is None
    assert repo.list_snapshots(DEPLOYMENT_ID, SOURCE_ID, limit=1) == (snapshot,)
    assert type(next(iter(connection.database.snapshots.values()))[12]) is bytes
    validation_row = next(iter(connection.database.validation.values()))
    assert type(validation_row[13]) is bytes
    assert validation_row[4:8] == (None, None, None, None)
    assert validation_row[11:13] == (
        report.content_hash,
        report.diff.content_hash,
    )
    observation = repo.get_runtime_observation(DEPLOYMENT_ID, SOURCE_ID)
    assert observation is not None
    assert observation.active_snapshot_id == snapshot.snapshot_id
    assert connection.database.states[(DEPLOYMENT_ID, SOURCE_ID)] == (
        6,
        snapshot.snapshot_id,
        snapshot.content_hash,
    )
    applied_target = connection.database.audits[-1][9:11]
    assert applied_target == (
        SOURCE_SNAPSHOT_TARGET_TYPE,
        source_snapshot_authorization_target_id(
            DEPLOYMENT_ID,
            SOURCE_SET_ID,
            SOURCE_ID,
            snapshot.snapshot_id,
            snapshot.content_hash,
        ),
    )


def test_exact_retries_append_only_current_idempotent_audits() -> None:
    connection = FakeConnection()
    first = repository(connection)
    second = repository(connection)
    metadata = raw_metadata()
    assert (
        save_retrieval(first, connection.database, metadata)
        is ArtifactWriteOutcome.APPLIED
    )
    before_event = copy.deepcopy(connection.database.events)
    assert (
        save_retrieval(
            second,
            connection.database,
            metadata,
            sequence=2,
            prefix="retrieve-retry",
            event_id="event-retrieve-retry",
        )
        is ArtifactWriteOutcome.IDEMPOTENT
    )
    assert connection.database.events == before_event
    assert len(connection.database.audits) == 2
    assert connection.database.audits[-1][15] == "RETRIEVE_IDEMPOTENT"

    quarantined = lifecycle_event(SourceSnapshotEventType.QUARANTINED, 2, metadata)
    assert (
        append_event(first, connection.database, quarantined, "quarantine")
        is LifecycleWriteOutcome.APPLIED
    )
    retry_quarantine = lifecycle_event(
        SourceSnapshotEventType.QUARANTINED,
        3,
        metadata,
        event_id="event-quarantine-retry",
        reason=quarantined.reason,
        occurred_at=quarantined.occurred_at + timedelta(seconds=1),
    )
    assert (
        append_event(second, connection.database, retry_quarantine, "quarantine-retry")
        is LifecycleWriteOutcome.IDEMPOTENT
    )
    assert len(connection.database.events) == 2
    assert connection.database.audits[-1][15] == "QUARANTINE_IDEMPOTENT"


def test_parsed_validation_activation_and_rollback_exact_retries() -> None:
    connection = FakeConnection()
    repo = repository(connection)
    first_raw = raw_metadata()
    first_snapshot = parsed_snapshot(first_raw)
    persist_parsed(repo, connection.database, first_raw, first_snapshot)
    assert (
        save_parsed(
            repo,
            connection.database,
            first_snapshot,
            sequence=4,
            prefix="parse-retry",
        )
        is ArtifactWriteOutcome.IDEMPOTENT
    )

    report = validate_and_approve(
        repo,
        connection.database,
        first_raw,
        first_snapshot,
        sequence=4,
        previous=None,
        prefix="first",
    )
    validation_retry = lifecycle_event(
        SourceSnapshotEventType.VALIDATED,
        6,
        first_raw,
        snapshot=first_snapshot,
        report=report,
        event_id="event-validation-retry",
        occurred_at=NOW + timedelta(seconds=6),
    )
    assert (
        append_event(
            repo,
            connection.database,
            validation_retry,
            "validation-retry",
        )
        is LifecycleWriteOutcome.IDEMPOTENT
    )

    activation = lifecycle_event(
        SourceSnapshotEventType.ACTIVATED,
        6,
        first_raw,
        snapshot=first_snapshot,
    )
    assert (
        pointer_write(repo, connection.database, activation, first_raw, "activation")
        is LifecycleWriteOutcome.APPLIED
    )
    activation_retry = lifecycle_event(
        SourceSnapshotEventType.ACTIVATED,
        7,
        first_raw,
        snapshot=first_snapshot,
        event_id="event-activation-retry",
        occurred_at=NOW + timedelta(seconds=7),
    )
    assert (
        pointer_write(
            repo,
            connection.database,
            activation_retry,
            first_raw,
            "activation-retry",
        )
        is LifecycleWriteOutcome.IDEMPOTENT
    )


def test_inactive_registration_preserves_historical_reads_but_rejects_writes() -> None:
    connection, repo, metadata, snapshot, _report, _activation = activated_fixture()
    connection.database.registrations[(DEPLOYMENT_ID, SOURCE_ID)] = (
        SOURCE_SET_ID,
        False,
    )

    assert (
        repo.get_raw_metadata(DEPLOYMENT_ID, SOURCE_ID, metadata.object_id) == metadata
    )
    assert repo.get_raw_metadata(DEPLOYMENT_ID, SOURCE_ID, "missing") is None
    reconstructed = repo.get_snapshot(DEPLOYMENT_ID, SOURCE_ID, snapshot.snapshot_id)
    assert reconstructed == snapshot
    assert reconstructed is not None
    assert (
        reconstructed.records[0].assertions[0].normalized_value == "synthetic record-1"
    )
    assert repo.get_snapshot(DEPLOYMENT_ID, SOURCE_ID, "missing") is None
    assert repo.list_snapshots(DEPLOYMENT_ID, SOURCE_ID, limit=10) == (snapshot,)
    events, lifecycle = repo.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    assert repo.list_lifecycle_events(DEPLOYMENT_ID, SOURCE_ID, limit=10) == events
    assert len(events) == 6
    assert lifecycle.active_snapshot == snapshot.reference()
    audits = repo.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=10)
    assert len(audits) == 6
    observation = repo.get_runtime_observation(DEPLOYMENT_ID, SOURCE_ID)
    assert observation is not None
    assert observation.active_snapshot_id == snapshot.snapshot_id

    retry = lifecycle_event(
        SourceSnapshotEventType.RETRIEVED,
        7,
        metadata,
        event_id="event-inactive-write",
    )
    applied, idempotent = audit_pair(connection.database, retry, "inactive-write")
    before = connection.database.snapshot()
    rollbacks = connection.rollbacks
    with pytest.raises(SourceSnapshotPersistenceError):
        repo.save_retrieval_atomic(metadata, retry, applied, idempotent)
    assert connection.database.snapshot() == before
    assert connection.rollbacks == rollbacks + 1


def test_retrieval_and_parse_retries_require_exact_event_body() -> None:
    connection = FakeConnection()
    repo = repository(connection)
    metadata = raw_metadata()
    assert (
        save_retrieval(repo, connection.database, metadata)
        is ArtifactWriteOutcome.APPLIED
    )
    changed_retrieval = lifecycle_event(
        SourceSnapshotEventType.RETRIEVED,
        2,
        metadata,
        event_id="event-retrieval-changed-body",
        reason="Changed retrieval body.",
    )
    applied, idempotent = audit_pair(
        connection.database, changed_retrieval, "retrieval-changed-body"
    )
    before = copy.deepcopy(
        (
            connection.database.events,
            connection.database.audits,
            connection.database.states,
        )
    )
    assert (
        repo.save_retrieval_atomic(metadata, changed_retrieval, applied, idempotent)
        is ArtifactWriteOutcome.CONFLICT
    )
    assert (
        connection.database.events,
        connection.database.audits,
        connection.database.states,
    ) == before
    assert (
        save_retrieval(
            repo,
            connection.database,
            metadata,
            sequence=2,
            prefix="retrieval-exact-body",
            event_id="event-retrieval-exact-body",
        )
        is ArtifactWriteOutcome.IDEMPOTENT
    )

    quarantined = lifecycle_event(SourceSnapshotEventType.QUARANTINED, 2, metadata)
    assert (
        append_event(repo, connection.database, quarantined, "exact-body-quarantine")
        is LifecycleWriteOutcome.APPLIED
    )
    snapshot = parsed_snapshot(metadata)
    assert (
        save_parsed(
            repo,
            connection.database,
            snapshot,
            sequence=3,
            prefix="exact-body-parse",
        )
        is ArtifactWriteOutcome.APPLIED
    )
    changed_parse = lifecycle_event(
        SourceSnapshotEventType.PARSED,
        4,
        metadata,
        snapshot=snapshot,
        event_id="event-parse-changed-body",
        reason="Changed parse body.",
    )
    applied, idempotent = audit_pair(
        connection.database, changed_parse, "parse-changed-body"
    )
    before = copy.deepcopy(
        (
            connection.database.events,
            connection.database.audits,
            connection.database.states,
        )
    )
    assert (
        repo.save_parsed_snapshot_atomic(snapshot, changed_parse, applied, idempotent)
        is ArtifactWriteOutcome.CONFLICT
    )
    assert (
        connection.database.events,
        connection.database.audits,
        connection.database.states,
    ) == before
    assert (
        save_parsed(
            repo,
            connection.database,
            snapshot,
            sequence=4,
            prefix="parse-exact-body",
        )
        is ArtifactWriteOutcome.IDEMPOTENT
    )
    reused_parse = lifecycle_event(
        SourceSnapshotEventType.PARSED,
        4,
        metadata,
        snapshot=snapshot,
        event_id=cast(str, connection.database.events[0][3]),
    )
    reused_applied, reused_idempotent = audit_pair(
        connection.database, reused_parse, "parse-reused-event-id"
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        repo.save_parsed_snapshot_atomic(
            snapshot, reused_parse, reused_applied, reused_idempotent
        )


def test_only_latest_pointer_transition_can_be_an_exact_retry() -> None:
    connection = FakeConnection()
    repo = repository(connection)
    first_raw = raw_metadata()
    first_snapshot = parsed_snapshot(first_raw)
    persist_parsed(repo, connection.database, first_raw, first_snapshot)
    validate_and_approve(
        repo,
        connection.database,
        first_raw,
        first_snapshot,
        sequence=4,
        previous=None,
        prefix="latest-first",
    )
    first_activation = lifecycle_event(
        SourceSnapshotEventType.ACTIVATED,
        6,
        first_raw,
        snapshot=first_snapshot,
    )
    assert (
        pointer_write(
            repo,
            connection.database,
            first_activation,
            first_raw,
            "latest-first-activate",
        )
        is LifecycleWriteOutcome.APPLIED
    )

    second_raw = raw_metadata("second", retrieved_at=NOW + timedelta(seconds=7))
    second_snapshot = parsed_snapshot(second_raw, parsed_at=NOW + timedelta(seconds=9))
    persist_parsed(
        repo,
        connection.database,
        second_raw,
        second_snapshot,
        start_sequence=7,
        prefix="latest-second",
    )
    validate_and_approve(
        repo,
        connection.database,
        second_raw,
        second_snapshot,
        sequence=10,
        previous=first_snapshot,
        prefix="latest-second",
    )
    second_activation = lifecycle_event(
        SourceSnapshotEventType.ACTIVATED,
        12,
        second_raw,
        snapshot=second_snapshot,
        previous=first_snapshot.reference(),
    )
    assert (
        pointer_write(
            repo,
            connection.database,
            second_activation,
            second_raw,
            "latest-second-activate",
        )
        is LifecycleWriteOutcome.APPLIED
    )
    old_rollback = lifecycle_event(
        SourceSnapshotEventType.ROLLED_BACK,
        13,
        first_raw,
        snapshot=first_snapshot,
        previous=second_snapshot.reference(),
        reason="Original rollback body.",
    )
    assert (
        pointer_write(
            repo,
            connection.database,
            old_rollback,
            first_raw,
            "latest-first-rollback",
        )
        is LifecycleWriteOutcome.APPLIED
    )

    stale_activation = lifecycle_event(
        SourceSnapshotEventType.ACTIVATED,
        14,
        first_raw,
        snapshot=first_snapshot,
        event_id="event-stale-old-activation",
        reason=first_activation.reason,
    )
    before = copy.deepcopy(
        (
            connection.database.events,
            connection.database.audits,
            connection.database.states,
            connection.database.observations,
        )
    )
    assert (
        pointer_write(
            repo,
            connection.database,
            stale_activation,
            first_raw,
            "stale-old-activation",
        )
        is LifecycleWriteOutcome.CONFLICT
    )
    assert (
        connection.database.events,
        connection.database.audits,
        connection.database.states,
        connection.database.observations,
    ) == before

    rollback_second = lifecycle_event(
        SourceSnapshotEventType.ROLLED_BACK,
        14,
        second_raw,
        snapshot=second_snapshot,
        previous=first_snapshot.reference(),
        reason="Move away before later restoration.",
    )
    assert (
        pointer_write(
            repo,
            connection.database,
            rollback_second,
            second_raw,
            "latest-second-rollback",
        )
        is LifecycleWriteOutcome.APPLIED
    )
    later_restore = lifecycle_event(
        SourceSnapshotEventType.ROLLED_BACK,
        15,
        first_raw,
        snapshot=first_snapshot,
        previous=second_snapshot.reference(),
        reason="Later restoration body.",
    )
    assert (
        pointer_write(
            repo,
            connection.database,
            later_restore,
            first_raw,
            "latest-first-restored",
        )
        is LifecycleWriteOutcome.APPLIED
    )
    stale_rollback = lifecycle_event(
        SourceSnapshotEventType.ROLLED_BACK,
        16,
        first_raw,
        snapshot=first_snapshot,
        previous=second_snapshot.reference(),
        event_id="event-stale-old-rollback",
        reason=old_rollback.reason,
    )
    before = copy.deepcopy(
        (
            connection.database.events,
            connection.database.audits,
            connection.database.states,
            connection.database.observations,
        )
    )
    assert (
        pointer_write(
            repo,
            connection.database,
            stale_rollback,
            first_raw,
            "stale-old-rollback",
        )
        is LifecycleWriteOutcome.CONFLICT
    )
    assert (
        connection.database.events,
        connection.database.audits,
        connection.database.states,
        connection.database.observations,
    ) == before

    exact_latest = lifecycle_event(
        SourceSnapshotEventType.ROLLED_BACK,
        16,
        first_raw,
        snapshot=first_snapshot,
        previous=second_snapshot.reference(),
        event_id="event-exact-latest-rollback",
        reason=later_restore.reason,
    )
    assert (
        pointer_write(
            repo,
            connection.database,
            exact_latest,
            first_raw,
            "exact-latest-rollback",
        )
        is LifecycleWriteOutcome.IDEMPOTENT
    )


def test_stale_sequence_predecessor_and_observation_conflicts_have_no_partial_write() -> (
    None
):
    connection = FakeConnection()
    repo = repository(connection)
    first_raw = raw_metadata()
    first_snapshot = parsed_snapshot(first_raw)
    persist_parsed(repo, connection.database, first_raw, first_snapshot)
    validate_and_approve(
        repo,
        connection.database,
        first_raw,
        first_snapshot,
        sequence=4,
        previous=None,
        prefix="first",
    )
    first_activation = lifecycle_event(
        SourceSnapshotEventType.ACTIVATED, 6, first_raw, snapshot=first_snapshot
    )
    assert (
        pointer_write(
            repo, connection.database, first_activation, first_raw, "activate"
        )
        is LifecycleWriteOutcome.APPLIED
    )

    second_raw = raw_metadata("second", retrieved_at=NOW + timedelta(seconds=7))
    second_snapshot = parsed_snapshot(
        second_raw,
        record_id="record-1",
        parsed_at=NOW + timedelta(seconds=9),
    )
    persist_parsed(
        repo,
        connection.database,
        second_raw,
        second_snapshot,
        start_sequence=7,
        prefix="second",
    )
    stale_report = validate_snapshot(
        second_snapshot,
        expected_schema_id=SYNTHETIC_SCHEMA_ID,
        previous_accepted=None,
        validated_at=NOW + timedelta(seconds=10),
    )
    stale_validation = lifecycle_event(
        SourceSnapshotEventType.VALIDATED,
        10,
        second_raw,
        snapshot=second_snapshot,
        report=stale_report,
    )
    before_stale = copy.deepcopy(
        (
            connection.database.validation,
            connection.database.states,
            connection.database.events,
            connection.database.audits,
            connection.database.observations,
        )
    )
    assert (
        append_event(repo, connection.database, stale_validation, "stale-validation")
        is LifecycleWriteOutcome.CONFLICT
    )
    assert (
        connection.database.validation,
        connection.database.states,
        connection.database.events,
        connection.database.audits,
        connection.database.observations,
    ) == before_stale
    assert connection.rollbacks >= 1

    validate_and_approve(
        repo,
        connection.database,
        second_raw,
        second_snapshot,
        sequence=10,
        previous=first_snapshot,
        prefix="second",
    )
    replacement = lifecycle_event(
        SourceSnapshotEventType.ACTIVATED,
        12,
        second_raw,
        snapshot=second_snapshot,
        previous=first_snapshot.reference(),
    )
    assert (
        pointer_write(repo, connection.database, replacement, second_raw, "replace")
        is LifecycleWriteOutcome.APPLIED
    )
    rollback_equal = lifecycle_event(
        SourceSnapshotEventType.ROLLED_BACK,
        13,
        first_raw,
        snapshot=first_snapshot,
        previous=second_snapshot.reference(),
        event_id="event-rollback-equal",
        occurred_at=replacement.occurred_at,
    )
    equal_time = SourceRuntimeObservation(
        DEPLOYMENT_ID,
        SOURCE_ID,
        SourceAvailability.AVAILABLE,
        rollback_equal.occurred_at,
        first_snapshot.snapshot_id,
        first_raw.retrieved_at,
        first_raw.effective_from,
    )
    applied, idempotent = audit_pair(
        connection.database, rollback_equal, "rollback-equal"
    )
    before_observation = copy.deepcopy(
        (
            connection.database.states,
            connection.database.events,
            connection.database.audits,
            connection.database.observations,
        )
    )
    assert (
        repo.activate_or_rollback_atomic(
            rollback_equal, equal_time, applied, idempotent
        )
        is LifecycleWriteOutcome.CONFLICT
    )
    assert (
        connection.database.states,
        connection.database.events,
        connection.database.audits,
        connection.database.observations,
    ) == before_observation
    rollback = lifecycle_event(
        SourceSnapshotEventType.ROLLED_BACK,
        13,
        first_raw,
        snapshot=first_snapshot,
        previous=second_snapshot.reference(),
    )
    assert (
        pointer_write(repo, connection.database, rollback, first_raw, "rollback")
        is LifecycleWriteOutcome.APPLIED
    )


def test_standalone_failure_audit_accepts_absent_artifact_and_derives_target() -> None:
    connection = FakeConnection()
    repo = repository(connection)
    absent = raw_metadata("absent").reference()
    failure = SnapshotCommandAuditRecord(
        "command-absent-failure",
        "authorization-absent-failure",
        None,
        TENANT_ID,
        DEPLOYMENT_ID,
        SOURCE_ID,
        absent.object_id,
        None,
        None,
        "operator-1",
        LifecycleActorType.SERVICE,
        Operation.SOURCE_SNAPSHOT_INGEST.value,
        SnapshotCommandOutcome.FAILURE,
        SnapshotCommandReason.CONFLICT,
        NOW,
    )
    add_authorization(connection.database, failure)
    repo.append_command_audit(failure)
    assert connection.database.raw == {}
    assert connection.database.audits[0][6] == absent.object_id
    assert connection.database.audits[0][9:11] == target_for_audit(failure)
    assert repo.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=1) == (failure,)


def test_database_failure_wrong_rowcount_and_forged_authorization_roll_back_safely() -> (
    None
):
    connection = FakeConnection()
    repo = repository(connection)
    metadata = raw_metadata()
    connection.fail_once_query = "INSERT INTO source_raw_object_metadata"
    with pytest.raises(SourceSnapshotPersistenceError) as captured:
        save_retrieval(repo, connection.database, metadata)
    assert str(captured.value) == "source snapshot persistence unavailable"
    assert "sentinel" not in repr(captured.value)
    assert connection.database.raw == {}
    assert connection.database.events == []
    assert connection.database.audits == []
    assert connection.rollbacks == 1

    connection.wrong_rowcount_query = "INSERT INTO source_raw_object_metadata"
    with pytest.raises(SourceSnapshotPersistenceError):
        save_retrieval(repo, connection.database, metadata, prefix="wrong-rowcount")
    assert connection.database.raw == {}

    event = lifecycle_event(SourceSnapshotEventType.RETRIEVED, 1, metadata)
    applied, idempotent = audit_pair(connection.database, event, "forged")
    add_authorization(
        connection.database,
        applied,
        target=(SOURCE_TARGET_TYPE, "source-" + "0" * 64),
    )
    before = connection.database.snapshot()
    with pytest.raises(SourceSnapshotPersistenceError):
        repo.save_retrieval_atomic(metadata, event, applied, idempotent)
    assert connection.database.snapshot() == before


def test_unknown_inactive_legacy_and_projection_corruption_fail_closed() -> None:
    unknown_connection = FakeConnection()
    unknown_connection.database.registrations.clear()
    with pytest.raises(SourceSnapshotPersistenceError):
        repository(unknown_connection).get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    unknown_connection.database.registrations[(DEPLOYMENT_ID, SOURCE_ID)] = (
        SOURCE_SET_ID,
        False,
    )
    assert repository(unknown_connection).get_lifecycle_snapshot(
        DEPLOYMENT_ID, SOURCE_ID
    ) == ((), fold_source_snapshot_events(()))

    legacy = FakeConnection()
    legacy.database.observations[(DEPLOYMENT_ID, SOURCE_ID)] = (
        "AVAILABLE",
        NOW,
        "legacy-demo-marker",
        NOW,
        NOW,
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        repository(legacy).get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)

    connection = FakeConnection()
    repo = repository(connection)
    metadata = raw_metadata()
    save_retrieval(repo, connection.database, metadata)
    connection.database.states[(DEPLOYMENT_ID, SOURCE_ID)] = (99, None, None)
    with pytest.raises(SourceSnapshotPersistenceError):
        repo.get_raw_metadata(DEPLOYMENT_ID, SOURCE_ID, metadata.object_id)


def test_orphan_corrupt_and_overbound_rows_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection()
    repo = repository(connection)
    metadata = raw_metadata()
    save_retrieval(repo, connection.database, metadata)
    raw_key = (DEPLOYMENT_ID, SOURCE_ID, metadata.object_id)
    valid_raw = connection.database.raw[raw_key]
    connection.database.raw[raw_key] = (*valid_raw[:8], True, valid_raw[9])
    with pytest.raises(SourceSnapshotPersistenceError):
        repo.get_raw_metadata(*raw_key)
    connection.database.raw[raw_key] = valid_raw

    orphan = raw_metadata("orphan")
    connection.database.raw[(DEPLOYMENT_ID, SOURCE_ID, orphan.object_id)] = (
        orphan.deployment_id,
        orphan.source_id,
        orphan.object_id,
        orphan.original_name,
        orphan.media_type,
        orphan.charset,
        orphan.retrieved_at,
        orphan.effective_from,
        orphan.byte_length,
        orphan.content_hash,
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        repo.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    connection.database.raw.pop((DEPLOYMENT_ID, SOURCE_ID, orphan.object_id))

    monkeypatch.setattr(postgres_source_snapshot, "MAX_PERSISTED_AUDITS", 0)
    with pytest.raises(SourceSnapshotPersistenceError):
        repo.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=1)
    monkeypatch.setattr(
        postgres_source_snapshot, "MAX_PERSISTED_AUDITS", MAX_PERSISTED_AUDITS
    )
    monkeypatch.setattr(postgres_source_snapshot, "MAX_PERSISTED_ARTIFACTS", 0)
    with pytest.raises(SourceSnapshotPersistenceError):
        repo.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)


def test_projection_audit_and_observation_verifiers_fail_closed() -> None:
    module = postgres_source_snapshot
    metadata = raw_metadata()
    snapshot = parsed_snapshot(metadata)
    retrieved = lifecycle_event(SourceSnapshotEventType.RETRIEVED, 1, metadata)
    parsed = lifecycle_event(
        SourceSnapshotEventType.PARSED, 2, metadata, snapshot=snapshot
    )
    applied, idempotent = audit_pair(FakeDatabase.create(), retrieved, "verify")
    scope = (DEPLOYMENT_ID, SOURCE_ID)
    raw_map = {metadata.object_id: metadata}
    snapshot_map = {snapshot.snapshot_id: snapshot}

    with pytest.raises(SourceSnapshotPersistenceError):
        module.PostgresSourceSnapshotRepository._verify_artifacts(
            scope, {}, {}, {}, (retrieved,)
        )
    with pytest.raises(SourceSnapshotPersistenceError):
        module.PostgresSourceSnapshotRepository._verify_artifacts(
            scope, raw_map, snapshot_map, {}, (retrieved,)
        )
    evidence_report = validate_snapshot(
        snapshot,
        expected_schema_id=SYNTHETIC_SCHEMA_ID,
        previous_accepted=None,
        validated_at=NOW + timedelta(seconds=3),
    )
    evidence = module._ValidationEvidence(snapshot.reference(), None, evidence_report)
    with pytest.raises(SourceSnapshotPersistenceError):
        module.PostgresSourceSnapshotRepository._verify_artifacts(
            scope,
            raw_map,
            snapshot_map,
            {snapshot.snapshot_id: evidence},
            (retrieved, parsed),
        )

    with pytest.raises(SourceSnapshotPersistenceError):
        module.PostgresSourceSnapshotRepository._verify_audits(
            (retrieved,), (applied, applied)
        )
    with pytest.raises(SourceSnapshotPersistenceError):
        module.PostgresSourceSnapshotRepository._verify_audits((retrieved,), ())
    invalid_applied = copy.deepcopy(applied)
    object.__setattr__(invalid_applied, "actor_id", "other")
    with pytest.raises(SourceSnapshotPersistenceError):
        module.PostgresSourceSnapshotRepository._verify_audits(
            (retrieved,), (invalid_applied,)
        )
    with pytest.raises(SourceSnapshotPersistenceError):
        module.PostgresSourceSnapshotRepository._verify_audits((), (idempotent,))
    invalid_idempotent = copy.deepcopy(idempotent)
    object.__setattr__(invalid_idempotent, "operation", "wrong-operation")
    with pytest.raises(SourceSnapshotPersistenceError):
        module.PostgresSourceSnapshotRepository._verify_audits(
            (retrieved,), (applied, invalid_idempotent)
        )

    inactive = fold_source_snapshot_events(())
    observation = SourceRuntimeObservation(
        DEPLOYMENT_ID, SOURCE_ID, SourceAvailability.UNAVAILABLE, NOW
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        module.PostgresSourceSnapshotRepository._verify_observation(
            scope, {}, (), inactive, observation
        )

    _connection, repo, metadata, _snapshot, _report, activation = activated_fixture()
    events, active = repo.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    with pytest.raises(SourceSnapshotPersistenceError):
        module.PostgresSourceSnapshotRepository._verify_observation(
            scope, {metadata.object_id: metadata}, events, active, None
        )
    bad_observation = SourceRuntimeObservation(
        DEPLOYMENT_ID,
        SOURCE_ID,
        SourceAvailability.UNAVAILABLE,
        activation.occurred_at,
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        module.PostgresSourceSnapshotRepository._verify_observation(
            scope,
            {metadata.object_id: metadata},
            events,
            active,
            bad_observation,
        )
    with pytest.raises(ValueError, match="typed"):
        module.PostgresSourceSnapshotRepository._validate_observation(
            scope, {}, activation, cast(Any, object())
        )
    with pytest.raises(ValueError, match="identify a snapshot"):
        module.PostgresSourceSnapshotRepository._validate_observation(
            scope, {}, retrieved, observation
        )
    with pytest.raises(ValueError, match="does not match"):
        module.PostgresSourceSnapshotRepository._validate_observation(
            scope, {metadata.object_id: metadata}, activation, bad_observation
        )


def test_target_and_reconstruction_equality_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = postgres_source_snapshot
    metadata = raw_metadata()
    retrieved = lifecycle_event(SourceSnapshotEventType.RETRIEVED, 1, metadata)
    applied, _ = audit_pair(FakeDatabase.create(), retrieved, "target")
    object.__setattr__(applied, "operation", "unknown-operation")
    with pytest.raises(SourceSnapshotPersistenceError):
        module._target_for_audit(applied, SOURCE_SET_ID)

    snapshot = parsed_snapshot(metadata)
    activation = lifecycle_event(
        SourceSnapshotEventType.ACTIVATED, 1, metadata, snapshot=snapshot
    )
    activation_audit, _ = audit_pair(
        FakeDatabase.create(), activation, "target-governance"
    )
    object.__setattr__(activation_audit, "snapshot_id", None)
    object.__setattr__(activation_audit, "snapshot_content_hash", None)
    with pytest.raises(SourceSnapshotPersistenceError):
        module._target_for_audit(activation_audit, SOURCE_SET_ID)

    valid_event = lifecycle_event(SourceSnapshotEventType.RETRIEVED, 1, metadata)
    valid_audit, _ = audit_pair(FakeDatabase.create(), valid_event, "equality")
    valid_observation = SourceRuntimeObservation(
        DEPLOYMENT_ID, SOURCE_ID, SourceAvailability.UNAVAILABLE, NOW
    )
    for value_type, function, value in (
        (RawObjectMetadata, module._checked_raw_metadata, metadata),
        (SourceSnapshotLifecycleEvent, module._checked_event, valid_event),
        (SnapshotCommandAuditRecord, module._checked_audit, valid_audit),
        (SourceRuntimeObservation, module._checked_observation, valid_observation),
    ):
        with monkeypatch.context() as context:
            context.setattr(value_type, "__eq__", lambda _self, _other: False)
            with pytest.raises(ValueError, match="reconstruction"):
                function(cast(Any, value))


def test_invalid_limits_entrypoints_and_reused_event_ids_are_rejected() -> None:
    connection = FakeConnection()
    repo = repository(connection)
    for method in (
        repo.list_snapshots,
        repo.list_lifecycle_events,
        repo.list_command_audits,
    ):
        for invalid in (0, True, 513):
            with pytest.raises(ValueError):
                method(DEPLOYMENT_ID, SOURCE_ID, limit=invalid)

    metadata = raw_metadata()
    save_retrieval(repo, connection.database, metadata)
    second = raw_metadata("second")
    with pytest.raises(SourceSnapshotPersistenceError):
        save_retrieval(
            repo,
            connection.database,
            second,
            sequence=2,
            prefix="reused",
            event_id=cast(str, connection.database.events[0][3]),
        )

    event = lifecycle_event(SourceSnapshotEventType.RETRIEVED, 2, second)
    applied, idempotent = audit_pair(connection.database, event, "invalid-entrypoint")
    with pytest.raises(ValueError, match="explicit"):
        repo.append_lifecycle_atomic(event, applied, idempotent)
    with pytest.raises(ValueError, match="only activation"):
        repo.activate_or_rollback_atomic(
            event,
            SourceRuntimeObservation(
                DEPLOYMENT_ID,
                SOURCE_ID,
                SourceAvailability.AVAILABLE,
                event.occurred_at,
            ),
            applied,
            idempotent,
        )
    with pytest.raises(ValueError, match="unlinked failures"):
        repo.append_command_audit(applied)


def invoke_read(repo: PostgresSourceSnapshotRepository, name: str) -> object:
    if name == "snapshot":
        return repo.get_snapshot(DEPLOYMENT_ID, SOURCE_ID, "missing")
    if name == "snapshots":
        return repo.list_snapshots(DEPLOYMENT_ID, SOURCE_ID, limit=1)
    if name == "lifecycle":
        return repo.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    if name == "events":
        return repo.list_lifecycle_events(DEPLOYMENT_ID, SOURCE_ID, limit=1)
    if name == "audits":
        return repo.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=1)
    assert name == "observation"
    return repo.get_runtime_observation(DEPLOYMENT_ID, SOURCE_ID)


@pytest.mark.parametrize(
    "name", ("snapshot", "snapshots", "lifecycle", "events", "audits", "observation")
)
@pytest.mark.parametrize("failure", ("persistence", "database"))
def test_public_reads_preserve_safe_exception_boundary(name: str, failure: str) -> None:
    connection = FakeConnection()
    if failure == "persistence":
        connection.database.registrations[(DEPLOYMENT_ID, SOURCE_ID)] = (SOURCE_SET_ID,)
    else:
        connection.fail_once_query = "FROM source_registry"
    with pytest.raises(SourceSnapshotPersistenceError):
        invoke_read(repository(connection), name)


def test_strict_scalar_and_relational_row_codecs_reject_malformed_values() -> None:
    module = postgres_source_snapshot
    with pytest.raises(SourceSnapshotPersistenceError):
        module._raw_from_row(())
    with pytest.raises(SourceSnapshotPersistenceError):
        module._relational_previous_snapshot((), (DEPLOYMENT_ID, SOURCE_ID), {})
    with pytest.raises(SourceSnapshotPersistenceError):
        module._relational_previous_snapshot(
            (None, SOURCE_ID, None, None), (DEPLOYMENT_ID, SOURCE_ID), {}
        )
    with pytest.raises(SourceSnapshotPersistenceError):
        module._relational_previous_snapshot(
            ("other", SOURCE_ID, "snapshot", "a" * 64),
            (DEPLOYMENT_ID, SOURCE_ID),
            {},
        )
    with pytest.raises(SourceSnapshotPersistenceError):
        reference = parsed_snapshot(raw_metadata()).reference()
        module._relational_previous_snapshot(
            (
                DEPLOYMENT_ID,
                SOURCE_ID,
                reference.snapshot_id,
                reference.content_hash,
            ),
            (DEPLOYMENT_ID, SOURCE_ID),
            {},
        )
    with pytest.raises(SourceSnapshotPersistenceError):
        module._optional_snapshot_reference((), "snapshot")
    with pytest.raises(SourceSnapshotPersistenceError):
        module._optional_snapshot_reference((None, "hash"), "snapshot")
    with pytest.raises(SourceSnapshotPersistenceError):
        module._event_validation_report((), None, {})
    with pytest.raises(SourceSnapshotPersistenceError):
        module._event_validation_report(("hash", "hash", True), None, {})
    with pytest.raises(SourceSnapshotPersistenceError):
        module._event_validation_report(
            ("report", "diff", True),
            reference,
            {},
        )

    for function, value in (
        (module._string, 1),
        (module._boolean, 1),
        (module._datetime, NOW.replace(tzinfo=None)),
        (module._bytes, bytearray()),
    ):
        with pytest.raises(ValueError):
            function(value, "field")
    with pytest.raises(ValueError):
        module._integer(True, "field")
    assert module._optional_datetime(None, "field") is None


def test_checked_input_guards_reject_wrong_types_and_reconstruction_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = postgres_source_snapshot
    for function in (
        module._checked_raw_metadata,
        module._checked_snapshot,
        module._checked_event,
        module._checked_audit,
        module._checked_observation,
    ):
        with pytest.raises(ValueError):
            function(cast(Any, object()))

    metadata = raw_metadata()
    monkeypatch.setattr(
        RawObjectMetadata,
        "reconstitute",
        staticmethod(lambda **_values: (_ for _ in ()).throw(ValueError("bad"))),
    )
    with pytest.raises(ValueError, match="failed reconstruction"):
        module._checked_raw_metadata(metadata)
    monkeypatch.undo()

    snapshot = parsed_snapshot(metadata)
    object.__setattr__(snapshot, "content_hash", "f" * 64)
    with pytest.raises(ValueError, match="integrity"):
        module._checked_snapshot(snapshot)

    event = lifecycle_event(SourceSnapshotEventType.RETRIEVED, 1, metadata)
    object.__setattr__(event, "sequence", 0)
    with pytest.raises(ValueError, match="reconstruction"):
        module._checked_event(event)

    applied, _ = audit_pair(
        FakeDatabase.create(),
        lifecycle_event(SourceSnapshotEventType.RETRIEVED, 1, metadata),
        "guard",
    )
    object.__setattr__(applied, "command_event_id", "")
    with pytest.raises(ValueError, match="reconstruction"):
        module._checked_audit(applied)

    observation = SourceRuntimeObservation(
        DEPLOYMENT_ID,
        SOURCE_ID,
        SourceAvailability.AVAILABLE,
        NOW,
        "snapshot",
        NOW,
        NOW,
    )
    object.__setattr__(observation, "deployment_id", "")
    with pytest.raises(ValueError, match="reconstruction"):
        module._checked_observation(observation)


def test_write_entrypoint_conflicts_and_exception_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection()
    repo = repository(connection)
    metadata = raw_metadata()
    invalid_retrieval = lifecycle_event(
        SourceSnapshotEventType.QUARANTINED, 1, metadata
    )
    applied, idempotent = audit_pair(
        connection.database, invalid_retrieval, "invalid-retrieval"
    )
    with pytest.raises(ValueError, match="exact raw metadata"):
        repo.save_retrieval_atomic(metadata, invalid_retrieval, applied, idempotent)

    event = lifecycle_event(SourceSnapshotEventType.RETRIEVED, 1, metadata)
    applied, idempotent = audit_pair(connection.database, event, "view-conflict")
    altered_metadata = raw_metadata("altered")
    lifecycle = fold_source_snapshot_events(())
    conflict_view = postgres_source_snapshot._ScopeView(
        SOURCE_SET_ID,
        {metadata.object_id: altered_metadata},
        {},
        {},
        (),
        lifecycle,
        (),
        None,
    )
    monkeypatch.setattr(
        PostgresSourceSnapshotRepository,
        "_lock_and_validate_scope",
        lambda _self, _scope: conflict_view,
    )
    assert (
        repo.save_retrieval_atomic(metadata, event, applied, idempotent)
        is ArtifactWriteOutcome.CONFLICT
    )

    orphan_view = postgres_source_snapshot._ScopeView(
        SOURCE_SET_ID,
        {metadata.object_id: metadata},
        {},
        {},
        (),
        lifecycle,
        (),
        None,
    )
    monkeypatch.setattr(
        PostgresSourceSnapshotRepository,
        "_lock_and_validate_scope",
        lambda _self, _scope: orphan_view,
    )
    assert (
        repo.save_retrieval_atomic(metadata, event, applied, idempotent)
        is ArtifactWriteOutcome.CONFLICT
    )
    monkeypatch.undo()

    assert (
        save_retrieval(repo, connection.database, metadata)
        is ArtifactWriteOutcome.APPLIED
    )
    snapshot = parsed_snapshot(metadata)
    wrong_parsed = lifecycle_event(SourceSnapshotEventType.QUARANTINED, 2, metadata)
    applied, idempotent = audit_pair(connection.database, wrong_parsed, "wrong-parsed")
    with pytest.raises(ValueError, match="exact immutable snapshot"):
        repo.save_parsed_snapshot_atomic(snapshot, wrong_parsed, applied, idempotent)

    parsed_event = lifecycle_event(
        SourceSnapshotEventType.PARSED, 2, metadata, snapshot=snapshot
    )
    applied, idempotent = audit_pair(connection.database, parsed_event, "parsed-view")
    other_snapshot = parsed_snapshot(metadata, record_id="other")
    parsed_conflict_view = postgres_source_snapshot._ScopeView(
        SOURCE_SET_ID,
        {metadata.object_id: metadata},
        {snapshot.snapshot_id: other_snapshot},
        {},
        (),
        lifecycle,
        (),
        None,
    )
    monkeypatch.setattr(
        PostgresSourceSnapshotRepository,
        "_lock_and_validate_scope",
        lambda _self, _scope: parsed_conflict_view,
    )
    assert (
        repo.save_parsed_snapshot_atomic(snapshot, parsed_event, applied, idempotent)
        is ArtifactWriteOutcome.CONFLICT
    )
    parsed_orphan_view = postgres_source_snapshot._ScopeView(
        SOURCE_SET_ID,
        {metadata.object_id: metadata},
        {snapshot.snapshot_id: snapshot},
        {},
        (),
        lifecycle,
        (),
        None,
    )
    monkeypatch.setattr(
        PostgresSourceSnapshotRepository,
        "_lock_and_validate_scope",
        lambda _self, _scope: parsed_orphan_view,
    )
    assert (
        repo.save_parsed_snapshot_atomic(snapshot, parsed_event, applied, idempotent)
        is ArtifactWriteOutcome.CONFLICT
    )
    monkeypatch.undo()

    quarantined = lifecycle_event(SourceSnapshotEventType.QUARANTINED, 2, metadata)
    applied, idempotent = audit_pair(connection.database, quarantined, "append-db")
    connection.fail_once_query = "INSERT INTO source_snapshot_lifecycle_event"
    with pytest.raises(SourceSnapshotPersistenceError):
        repo.append_lifecycle_atomic(quarantined, applied, idempotent)
    connection.database.events[0] = (
        *connection.database.events[0][:3],
        quarantined.event_id,
        *connection.database.events[0][4:],
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        repo.append_lifecycle_atomic(quarantined, applied, idempotent)


def test_low_level_transition_and_reference_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = postgres_source_snapshot
    metadata = raw_metadata()
    snapshot = parsed_snapshot(metadata)
    lifecycle = fold_source_snapshot_events(())
    empty = module._ScopeView(SOURCE_SET_ID, {}, {}, {}, (), lifecycle, (), None)
    with pytest.raises(SourceSnapshotPersistenceError):
        module.PostgresSourceSnapshotRepository._require_raw(
            empty, metadata.reference()
        )
    with pytest.raises(SourceSnapshotPersistenceError):
        module.PostgresSourceSnapshotRepository._require_snapshot(
            empty, snapshot.reference()
        )

    mismatched_snapshot = parsed_snapshot(metadata, record_id="other")
    mismatched = module._ScopeView(
        SOURCE_SET_ID,
        {metadata.object_id: metadata},
        {snapshot.snapshot_id: mismatched_snapshot},
        {},
        (),
        lifecycle,
        (),
        None,
    )
    parsed = lifecycle_event(
        SourceSnapshotEventType.PARSED, 1, metadata, snapshot=snapshot
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        module.PostgresSourceSnapshotRepository._require_event_references(
            mismatched, parsed
        )

    other_metadata = raw_metadata("other")
    other_snapshot = parsed_snapshot(other_metadata)
    cross_raw_view = module._ScopeView(
        SOURCE_SET_ID,
        {metadata.object_id: metadata},
        {other_snapshot.snapshot_id: other_snapshot},
        {},
        (),
        lifecycle,
        (),
        None,
    )
    cross_raw_event = lifecycle_event(
        SourceSnapshotEventType.PARSED, 1, metadata, snapshot=other_snapshot
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        module.PostgresSourceSnapshotRepository._require_event_references(
            cross_raw_view, cross_raw_event
        )

    with pytest.raises(postgres_source_snapshot._WriteConflict):
        module.PostgresSourceSnapshotRepository._fold_new_event(
            (), lifecycle_event(SourceSnapshotEventType.QUARANTINED, 1, metadata)
        )
    with pytest.raises(postgres_source_snapshot._WriteConflict):
        module.PostgresSourceSnapshotRepository._fold_new_event(
            (), lifecycle_event(SourceSnapshotEventType.RETRIEVED, 2, metadata)
        )

    retrieved = lifecycle_event(SourceSnapshotEventType.RETRIEVED, 1, metadata)
    early_database = FakeDatabase.create()
    _, early = audit_pair(early_database, retrieved, "early")
    object.__setattr__(
        early, "occurred_at", retrieved.occurred_at - timedelta(seconds=1)
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        module.PostgresSourceSnapshotRepository._require_idempotent_audit(
            early, retrieved
        )
    _, invalid = audit_pair(early_database, retrieved, "invalid")
    object.__setattr__(invalid, "raw_object_id", "other")
    with pytest.raises(SourceSnapshotPersistenceError):
        module.PostgresSourceSnapshotRepository._require_idempotent_audit(
            invalid, retrieved
        )

    applied, idempotent = audit_pair(early_database, retrieved, "pair")
    object.__setattr__(idempotent, "tenant_id", "other")
    with pytest.raises(ValueError, match="scope and time"):
        module.PostgresSourceSnapshotRepository._validate_audit_pair(
            retrieved, applied, idempotent
        )
    monkeypatch.setattr(module, "MAX_LIFECYCLE_EVENTS", 0)
    with pytest.raises(SourceSnapshotPersistenceError):
        module.PostgresSourceSnapshotRepository._fold_new_event((), retrieved)


def test_all_persisted_row_codecs_reject_structural_corruption() -> None:
    connection, repo, metadata, snapshot, _report, _activation = activated_fixture()
    module = postgres_source_snapshot
    scope = (DEPLOYMENT_ID, SOURCE_ID)
    raw_row = connection.database.raw[(DEPLOYMENT_ID, SOURCE_ID, metadata.object_id)]
    raw_map = {metadata.object_id: metadata}
    snapshot_row = connection.database.snapshots[
        (DEPLOYMENT_ID, SOURCE_ID, snapshot.snapshot_id)
    ]
    snapshot_map = {snapshot.snapshot_id: snapshot}
    validation_row = connection.database.validation[
        (DEPLOYMENT_ID, SOURCE_ID, snapshot.snapshot_id)
    ]
    validation_map = module.PostgresSourceSnapshotRepository._validation_rows(
        scope, (validation_row,), snapshot_map
    )
    event_row = next(row for row in connection.database.events if row[4] == "PARSED")
    audit_row = connection.database.audits[0]

    with pytest.raises(SourceSnapshotPersistenceError):
        module.PostgresSourceSnapshotRepository._raw_rows(scope, (raw_row, raw_row))
    for corrupt_snapshot_row, corrupt_raw in (
        (snapshot_row[:-1], raw_map),
        (("other", *snapshot_row[1:]), raw_map),
        (snapshot_row, {}),
        (
            (
                *snapshot_row[:5],
                raw_metadata("different").content_hash,
                *snapshot_row[6:],
            ),
            raw_map,
        ),
        (
            (*snapshot_row[:7], "different-parser", *snapshot_row[8:]),
            raw_map,
        ),
    ):
        with pytest.raises(SourceSnapshotPersistenceError):
            module.PostgresSourceSnapshotRepository._snapshot_rows(
                scope, (corrupt_snapshot_row,), corrupt_raw
            )
    with pytest.raises(SourceSnapshotPersistenceError):
        module.PostgresSourceSnapshotRepository._snapshot_rows(
            scope, (snapshot_row, snapshot_row), raw_map
        )

    for corrupt_validation_row, corrupt_snapshots in (
        (validation_row[:-1], snapshot_map),
        (("other", *validation_row[1:]), snapshot_map),
        (validation_row, {}),
        (
            (
                *validation_row[:9],
                not cast(bool, validation_row[9]),
                *validation_row[10:],
            ),
            snapshot_map,
        ),
    ):
        with pytest.raises(SourceSnapshotPersistenceError):
            module.PostgresSourceSnapshotRepository._validation_rows(
                scope, (corrupt_validation_row,), corrupt_snapshots
            )
    with pytest.raises(SourceSnapshotPersistenceError):
        module.PostgresSourceSnapshotRepository._validation_rows(
            scope, (validation_row, validation_row), snapshot_map
        )

    for corrupt_event_row, corrupt_raw, corrupt_snapshots in (
        (event_row[:-1], raw_map, snapshot_map),
        (("other", *event_row[1:]), raw_map, snapshot_map),
        (event_row, {}, snapshot_map),
        (event_row, raw_map, {}),
    ):
        with pytest.raises(SourceSnapshotPersistenceError):
            module.PostgresSourceSnapshotRepository._event_from_row(
                corrupt_event_row,
                scope,
                corrupt_raw,
                corrupt_snapshots,
                validation_map,
            )
    other_snapshot = parsed_snapshot(raw_metadata("other"))
    pointer_row = next(
        row for row in connection.database.events if row[4] == "ACTIVATED"
    )
    previous_corrupt = (
        *pointer_row[:10],
        other_snapshot.snapshot_id,
        other_snapshot.content_hash,
        *pointer_row[12:],
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        module.PostgresSourceSnapshotRepository._event_from_row(
            previous_corrupt, scope, raw_map, snapshot_map, validation_map
        )

    with pytest.raises(SourceSnapshotPersistenceError):
        repo._audit_from_row(audit_row[:-1], scope, SOURCE_SET_ID)
    with pytest.raises(SourceSnapshotPersistenceError):
        repo._audit_from_row(
            (*audit_row[:7], snapshot.snapshot_id, None, *audit_row[9:]),
            scope,
            SOURCE_SET_ID,
        )
    with pytest.raises(SourceSnapshotPersistenceError):
        repo._audit_from_row(
            (*audit_row[:4], "other", *audit_row[5:]), scope, SOURCE_SET_ID
        )
    with pytest.raises(SourceSnapshotPersistenceError):
        repo._audit_from_row(
            (*audit_row[:10], "forged-target", *audit_row[11:]),
            scope,
            SOURCE_SET_ID,
        )
    authorization = connection.database.authorization.pop(cast(str, audit_row[1]))
    with pytest.raises(SourceSnapshotPersistenceError):
        repo._audit_from_row(audit_row, scope, SOURCE_SET_ID)
    connection.database.authorization[cast(str, audit_row[1])] = authorization
    with pytest.raises(SourceSnapshotPersistenceError):
        repo._observation_from_row(scope, ())
    with pytest.raises(SourceSnapshotPersistenceError):
        repo._validate_state(
            (),
            events=(),
            lifecycle=fold_source_snapshot_events(()),
            has_scope_data=False,
        )


def test_scope_loader_rejects_missing_lock_row_invalid_fold_and_non_tuple_rows() -> (
    None
):
    missing_state = FakeConnection()
    missing_state.drop_state_insert = True
    metadata = raw_metadata()
    with pytest.raises(SourceSnapshotPersistenceError):
        save_retrieval(repository(missing_state), missing_state.database, metadata)

    empty = FakeConnection()
    assert repository(empty).get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID) == (
        (),
        fold_source_snapshot_events(()),
    )

    non_tuple = FakeConnection()
    non_tuple.database.registrations[(DEPLOYMENT_ID, SOURCE_ID)] = cast(
        tuple[Any, ...], [SOURCE_SET_ID, True]
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        repository(non_tuple).get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)

    connection, repo, _metadata, _snapshot, _report, _activation = activated_fixture()
    duplicate = list(connection.database.events[0])
    duplicate[2] = 7
    duplicate[3] = "event-invalid-fold"
    connection.database.events.append(tuple(duplicate))
    connection.database.states[(DEPLOYMENT_ID, SOURCE_ID)] = (
        7,
        connection.database.states[(DEPLOYMENT_ID, SOURCE_ID)][1],
        connection.database.states[(DEPLOYMENT_ID, SOURCE_ID)][2],
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        repo.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)


def test_parsed_pointer_and_failure_audit_exception_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed_connection = FakeConnection()
    parsed_repo = repository(parsed_connection)
    metadata = raw_metadata()
    snapshot = parsed_snapshot(metadata)
    assert (
        save_retrieval(parsed_repo, parsed_connection.database, metadata)
        is ArtifactWriteOutcome.APPLIED
    )
    quarantine = lifecycle_event(SourceSnapshotEventType.QUARANTINED, 2, metadata)
    assert (
        append_event(parsed_repo, parsed_connection.database, quarantine, "db-parse-q")
        is LifecycleWriteOutcome.APPLIED
    )
    parsed_connection.fail_once_query = "INSERT INTO source_parsed_snapshot"
    with pytest.raises(SourceSnapshotPersistenceError):
        save_parsed(
            parsed_repo,
            parsed_connection.database,
            snapshot,
            sequence=3,
            prefix="db-parse",
        )

    connection, repo, metadata, snapshot, _report, activation = activated_fixture()
    with connection.transaction():
        view = repo._read_and_validate_scope((DEPLOYMENT_ID, SOURCE_ID))
    retry = lifecycle_event(
        SourceSnapshotEventType.ACTIVATED,
        7,
        metadata,
        snapshot=snapshot,
        event_id="event-pointer-guard",
        reason=activation.reason,
        occurred_at=NOW + timedelta(seconds=7),
    )
    observation = SourceRuntimeObservation(
        DEPLOYMENT_ID,
        SOURCE_ID,
        SourceAvailability.AVAILABLE,
        retry.occurred_at,
        snapshot.snapshot_id,
        metadata.retrieved_at,
        metadata.effective_from,
    )

    no_validation = postgres_source_snapshot._ScopeView(
        view.source_set_id,
        view.raw,
        view.snapshots,
        view.validation,
        tuple(
            event
            for event in view.events
            if event.event_type is not SourceSnapshotEventType.VALIDATED
        ),
        view.lifecycle,
        view.audits,
        view.observation,
    )
    applied, idempotent = audit_pair(connection.database, retry, "pointer-missing")
    with monkeypatch.context() as context:
        context.setattr(
            PostgresSourceSnapshotRepository,
            "_lock_and_validate_scope",
            lambda _self, _scope: no_validation,
        )
        with pytest.raises(SourceSnapshotPersistenceError):
            repo.activate_or_rollback_atomic(retry, observation, applied, idempotent)

    validation = copy.deepcopy(
        next(
            event
            for event in view.events
            if event.event_type is SourceSnapshotEventType.VALIDATED
        )
    )
    object.__setattr__(validation, "raw_object", raw_metadata("wrong").reference())
    mismatched_events = tuple(
        validation if event.event_id == validation.event_id else event
        for event in view.events
    )
    mismatched_validation = postgres_source_snapshot._ScopeView(
        view.source_set_id,
        view.raw,
        view.snapshots,
        view.validation,
        mismatched_events,
        view.lifecycle,
        view.audits,
        view.observation,
    )
    with monkeypatch.context() as context:
        context.setattr(
            PostgresSourceSnapshotRepository,
            "_lock_and_validate_scope",
            lambda _self, _scope: mismatched_validation,
        )
        with pytest.raises(SourceSnapshotPersistenceError):
            repo.activate_or_rollback_atomic(retry, observation, applied, idempotent)

    other_metadata = raw_metadata("pointer-other")
    other_snapshot = parsed_snapshot(other_metadata)
    responsible = copy.deepcopy(activation)
    object.__setattr__(
        responsible, "previous_active_snapshot", other_snapshot.reference()
    )
    responsible_events = tuple(
        responsible if event.event_id == activation.event_id else event
        for event in view.events
    )
    predecessor_view = postgres_source_snapshot._ScopeView(
        view.source_set_id,
        view.raw,
        {**view.snapshots, other_snapshot.snapshot_id: other_snapshot},
        view.validation,
        responsible_events,
        view.lifecycle,
        view.audits,
        view.observation,
    )
    predecessor_retry = lifecycle_event(
        SourceSnapshotEventType.ACTIVATED,
        7,
        metadata,
        snapshot=snapshot,
        previous=other_snapshot.reference(),
        event_id="event-pointer-predecessor",
        reason=activation.reason,
        occurred_at=NOW + timedelta(seconds=7),
    )
    predecessor_observation = SourceRuntimeObservation(
        DEPLOYMENT_ID,
        SOURCE_ID,
        SourceAvailability.AVAILABLE,
        predecessor_retry.occurred_at,
        snapshot.snapshot_id,
        metadata.retrieved_at,
        metadata.effective_from,
    )
    predecessor_applied, predecessor_idempotent = audit_pair(
        connection.database, predecessor_retry, "pointer-predecessor"
    )
    with monkeypatch.context() as context:
        context.setattr(
            PostgresSourceSnapshotRepository,
            "_lock_and_validate_scope",
            lambda _self, _scope: predecessor_view,
        )
        with pytest.raises(SourceSnapshotPersistenceError):
            repo.activate_or_rollback_atomic(
                predecessor_retry,
                predecessor_observation,
                predecessor_applied,
                predecessor_idempotent,
            )

    second_report = validate_snapshot(
        other_snapshot,
        expected_schema_id=SYNTHETIC_SCHEMA_ID,
        previous_accepted=None,
        validated_at=NOW + timedelta(seconds=7),
    )
    second_validation = lifecycle_event(
        SourceSnapshotEventType.VALIDATED,
        7,
        other_metadata,
        snapshot=other_snapshot,
        report=second_report,
    )
    stale_view = postgres_source_snapshot._ScopeView(
        view.source_set_id,
        {**view.raw, other_metadata.object_id: other_metadata},
        {**view.snapshots, other_snapshot.snapshot_id: other_snapshot},
        view.validation,
        view.events + (second_validation,),
        view.lifecycle,
        view.audits,
        view.observation,
    )
    stale_activation = lifecycle_event(
        SourceSnapshotEventType.ACTIVATED,
        8,
        other_metadata,
        snapshot=other_snapshot,
        previous=snapshot.reference(),
    )
    stale_observation = SourceRuntimeObservation(
        DEPLOYMENT_ID,
        SOURCE_ID,
        SourceAvailability.AVAILABLE,
        stale_activation.occurred_at,
        other_snapshot.snapshot_id,
        other_metadata.retrieved_at,
        other_metadata.effective_from,
    )
    stale_applied, stale_idempotent = audit_pair(
        connection.database, stale_activation, "pointer-stale"
    )
    with monkeypatch.context() as context:
        context.setattr(
            PostgresSourceSnapshotRepository,
            "_lock_and_validate_scope",
            lambda _self, _scope: stale_view,
        )
        assert (
            repo.activate_or_rollback_atomic(
                stale_activation,
                stale_observation,
                stale_applied,
                stale_idempotent,
            )
            is LifecycleWriteOutcome.CONFLICT
        )

    database_failure = FakeConnection()
    database_repo = repository(database_failure)
    database_metadata = raw_metadata()
    database_snapshot = parsed_snapshot(database_metadata)
    persist_parsed(
        database_repo,
        database_failure.database,
        database_metadata,
        database_snapshot,
    )
    validate_and_approve(
        database_repo,
        database_failure.database,
        database_metadata,
        database_snapshot,
        sequence=4,
        previous=None,
        prefix="pointer-db",
    )
    database_activation = lifecycle_event(
        SourceSnapshotEventType.ACTIVATED,
        6,
        database_metadata,
        snapshot=database_snapshot,
    )
    database_failure.fail_once_query = "INSERT INTO source_snapshot_lifecycle_event"
    with pytest.raises(SourceSnapshotPersistenceError):
        pointer_write(
            database_repo,
            database_failure.database,
            database_activation,
            database_metadata,
            "pointer-db",
        )

    audit_connection = FakeConnection()
    audit_repo = repository(audit_connection)
    absent = raw_metadata("audit-absent").reference()
    failure = SnapshotCommandAuditRecord(
        "command-audit-handler",
        "authorization-audit-handler",
        None,
        TENANT_ID,
        DEPLOYMENT_ID,
        SOURCE_ID,
        absent.object_id,
        None,
        None,
        "operator-1",
        LifecycleActorType.SERVICE,
        Operation.SOURCE_SNAPSHOT_INGEST.value,
        SnapshotCommandOutcome.FAILURE,
        SnapshotCommandReason.CONFLICT,
        NOW,
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        audit_repo.append_command_audit(failure)
    add_authorization(audit_connection.database, failure)
    audit_connection.fail_once_query = "INSERT INTO source_snapshot_command_audit"
    with pytest.raises(SourceSnapshotPersistenceError):
        audit_repo.append_command_audit(failure)
