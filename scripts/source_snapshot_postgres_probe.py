#!/usr/bin/env python3
"""Executable PostgreSQL 18.4 acceptance probe for TS-202 Slice C3c."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from typing import Any

import psycopg
from alembic import command
from alembic.config import Config
from psycopg import Connection

from tradesieve.adapters.postgres_source_snapshot import (
    APPLIED_REASONS,
    IDEMPOTENT_REASONS,
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
    validate_snapshot,
)
from tradesieve.manage import alembic_database_url
from tradesieve.ports.source_snapshot import (
    SYNTHETIC_CHARSET,
    SYNTHETIC_MEDIA_TYPE,
    SYNTHETIC_PARSER_VERSION,
    SYNTHETIC_SCHEMA_ID,
    SourceSnapshotPersistenceError,
)

DATABASE_URL = os.environ["TRADESIEVE_DATABASE_URL"]
MIGRATION_ROOT = Path(os.environ.get("TRADESIEVE_MIGRATION_ROOT", "/app"))
DEPLOYMENT_ID = "c3c-deployment"
SOURCE_SET_ID = "c3c-source-set"
SOURCE_ID = "c3c-source"
TENANT_ID = "c3c-tenant"
BASE_TIME = datetime(2026, 8, 7, 6, 0, tzinfo=UTC)


def evidence(phase: str, **details: object) -> None:
    print(
        json.dumps(
            {"phase": phase, "status": "PASS", **details},
            sort_keys=True,
        ),
        flush=True,
    )


def migration_config() -> Config:
    config = Config(MIGRATION_ROOT / "alembic.ini")
    config.set_main_option("script_location", str(MIGRATION_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", alembic_database_url(DATABASE_URL))
    return config


def migrate_up(target: str) -> None:
    command.upgrade(migration_config(), target)


def migrate_down(target: str) -> None:
    command.downgrade(migration_config(), target)


def connect() -> Connection[Any]:
    return psycopg.connect(DATABASE_URL, autocommit=True)


def scalar(
    connection: Connection[Any], query: str, params: tuple[object, ...] = ()
) -> Any:
    row = connection.execute(query, params).fetchone()
    assert row is not None and len(row) == 1
    return row[0]


def expect_database_error(
    action: Callable[[], object], *, sqlstate: str | None = None
) -> str:
    try:
        action()
    except psycopg.Error as exc:
        if sqlstate is not None:
            assert exc.sqlstate == sqlstate, (exc.sqlstate, sqlstate)
        assert exc.sqlstate is not None
        return exc.sqlstate
    raise AssertionError("database operation unexpectedly succeeded")


def register_source(connection: Connection[Any], source_id: str) -> None:
    connection.execute(
        "INSERT INTO source_registry ("
        "deployment_id, source_set_id, source_id, name, owner, "
        "responsible_operator, jurisdiction, legal_scope, data_scope, "
        "access_method, credential_secret_ref, licence_summary, "
        "contractual_constraints, refresh_expectation_seconds, "
        "stale_after_seconds, active) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'INTERNAL', NULL, "
        "%s, NULL, 3600, 7200, TRUE)",
        (
            DEPLOYMENT_ID,
            SOURCE_SET_ID,
            source_id,
            f"Synthetic {source_id}",
            "TradeSieve C3c",
            "c3c-operator",
            "SYNTHETIC",
            "Synthetic executable database acceptance only",
            "Synthetic records only",
            "Synthetic fixture; no production use",
        ),
    )


def raw_metadata(
    source_id: str,
    name: str,
    *,
    retrieved_at: datetime,
) -> RawObjectMetadata:
    return RawObjectMetadata.from_bytes(
        deployment_id=DEPLOYMENT_ID,
        source_id=source_id,
        original_name=f"{name}.json",
        media_type=SYNTHETIC_MEDIA_TYPE,
        charset=SYNTHETIC_CHARSET,
        retrieved_at=retrieved_at,
        effective_from=retrieved_at + timedelta(hours=1),
        content=json.dumps(
            {"fixture": name}, separators=(",", ":"), sort_keys=True
        ).encode(),
    )


def parsed_snapshot(
    metadata: RawObjectMetadata,
    name: str,
    *,
    parsed_at: datetime,
) -> ParsedSourceSnapshot:
    return ParsedSourceSnapshot.create(
        raw_metadata=metadata,
        parser_id="synthetic-json-v1",
        parser_version=SYNTHETIC_PARSER_VERSION,
        schema_id=SYNTHETIC_SCHEMA_ID,
        declared_record_count=1,
        parsed_at=parsed_at,
        records=(
            ParsedRecordInput(
                source_record_id="record-1",
                native_locator="/records/0",
                effective_from="2026-08-01",
                effective_to=None,
                assertions=(
                    ParsedAssertionInput(
                        "name",
                        "/records/0/name",
                        f"Synthetic {name}",
                        f"synthetic {name}",
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
    reason: str | None = None,
    occurred_at: datetime | None = None,
) -> SourceSnapshotLifecycleEvent:
    governance = event_type in {
        SourceSnapshotEventType.APPROVED,
        SourceSnapshotEventType.ACTIVATED,
        SourceSnapshotEventType.ROLLED_BACK,
    }
    return SourceSnapshotLifecycleEvent(
        sequence=sequence,
        event_id=event_id or f"c3c-event-{sequence}-{event_type.value.lower()}",
        deployment_id=metadata.deployment_id,
        source_id=metadata.source_id,
        event_type=event_type,
        raw_object=metadata.reference(),
        snapshot=snapshot.reference() if snapshot is not None else None,
        previous_active_snapshot=previous,
        actor_id="c3c-approver" if governance else "c3c-operator",
        actor_type=(
            LifecycleActorType.HUMAN if governance else LifecycleActorType.SERVICE
        ),
        reason=reason or f"C3c {event_type.value.lower()} acceptance.",
        occurred_at=occurred_at or BASE_TIME + timedelta(seconds=sequence),
        validation_report=report,
    )


def target_for_event(event: SourceSnapshotLifecycleEvent) -> tuple[str, str]:
    if event.event_type in {
        SourceSnapshotEventType.APPROVED,
        SourceSnapshotEventType.ACTIVATED,
        SourceSnapshotEventType.ROLLED_BACK,
    }:
        assert event.snapshot is not None
        return (
            SOURCE_SNAPSHOT_TARGET_TYPE,
            source_snapshot_authorization_target_id(
                event.deployment_id,
                SOURCE_SET_ID,
                event.source_id,
                event.snapshot.snapshot_id,
                event.snapshot.content_hash,
            ),
        )
    return (
        SOURCE_TARGET_TYPE,
        source_authorization_target_id(
            event.deployment_id, SOURCE_SET_ID, event.source_id
        ),
    )


def insert_authorization(
    connection: Connection[Any],
    *,
    authorization_event_id: str,
    actor_id: str,
    actor_type: LifecycleActorType,
    operation: str,
    target_type: str,
    target_id: str,
    occurred_at: datetime,
) -> None:
    connection.execute(
        "INSERT INTO authorization_audit_event ("
        "event_id, occurred_at, actor_subject, client_id, actor_type, "
        "actor_tenant_id, request_tenant_id, target_tenant_id, operation, "
        "target_ref, correlation_id, outcome, reason) "
        "VALUES (%s, %s, %s, 'c3c-probe', %s, %s, %s, %s, %s, %s, %s, "
        "'ALLOW', 'ALLOWED')",
        (
            authorization_event_id,
            occurred_at,
            actor_id,
            actor_type.value,
            TENANT_ID,
            TENANT_ID,
            TENANT_ID,
            operation,
            f"{target_type}:{target_id}",
            authorization_event_id,
        ),
    )


def audit_pair(
    connection: Connection[Any],
    event: SourceSnapshotLifecycleEvent,
    prefix: str,
) -> tuple[SnapshotCommandAuditRecord, SnapshotCommandAuditRecord]:
    authorization_event_id = f"c3c-auth-{prefix}"
    operation = AUTHORIZATION_OPERATION_BY_EVENT_TYPE[event.event_type]
    target_type, target_id = target_for_event(event)
    insert_authorization(
        connection,
        authorization_event_id=authorization_event_id,
        actor_id=event.actor_id,
        actor_type=event.actor_type,
        operation=operation,
        target_type=target_type,
        target_id=target_id,
        occurred_at=event.occurred_at,
    )
    snapshot = event.snapshot
    common = {
        "authorization_event_id": authorization_event_id,
        "tenant_id": TENANT_ID,
        "deployment_id": event.deployment_id,
        "source_id": event.source_id,
        "raw_object_id": event.raw_object.object_id,
        "snapshot_id": snapshot.snapshot_id if snapshot is not None else None,
        "snapshot_content_hash": (
            snapshot.content_hash if snapshot is not None else None
        ),
        "actor_id": event.actor_id,
        "actor_type": event.actor_type,
        "operation": operation,
    }
    applied = SnapshotCommandAuditRecord(
        command_event_id=f"c3c-command-{prefix}-applied",
        lifecycle_event_id=event.event_id,
        outcome=SnapshotCommandOutcome.SUCCESS,
        reason=APPLIED_REASONS[event.event_type],
        occurred_at=event.occurred_at,
        **common,
    )
    idempotent = SnapshotCommandAuditRecord(
        command_event_id=f"c3c-command-{prefix}-idempotent",
        lifecycle_event_id=None,
        outcome=SnapshotCommandOutcome.SUCCESS,
        reason=IDEMPOTENT_REASONS[event.event_type],
        occurred_at=event.occurred_at + timedelta(microseconds=1),
        **common,
    )
    return applied, idempotent


def failure_audit(
    event: SourceSnapshotLifecycleEvent,
    *,
    prefix: str,
    authorization_event_id: str,
    reason: SnapshotCommandReason = SnapshotCommandReason.CONFLICT,
) -> SnapshotCommandAuditRecord:
    snapshot = event.snapshot
    return SnapshotCommandAuditRecord(
        command_event_id=f"c3c-command-{prefix}-failure",
        authorization_event_id=authorization_event_id,
        lifecycle_event_id=None,
        tenant_id=TENANT_ID,
        deployment_id=event.deployment_id,
        source_id=event.source_id,
        raw_object_id=event.raw_object.object_id,
        snapshot_id=snapshot.snapshot_id if snapshot is not None else None,
        snapshot_content_hash=(snapshot.content_hash if snapshot is not None else None),
        actor_id=event.actor_id,
        actor_type=event.actor_type,
        operation=AUTHORIZATION_OPERATION_BY_EVENT_TYPE[event.event_type],
        outcome=SnapshotCommandOutcome.FAILURE,
        reason=reason,
        occurred_at=event.occurred_at,
    )


def observation_for(
    event: SourceSnapshotLifecycleEvent,
    metadata: RawObjectMetadata,
) -> SourceRuntimeObservation:
    assert event.snapshot is not None
    return SourceRuntimeObservation(
        event.deployment_id,
        event.source_id,
        SourceAvailability.AVAILABLE,
        event.occurred_at,
        event.snapshot.snapshot_id,
        metadata.retrieved_at,
        metadata.effective_from,
    )


def assert_audit_row(
    connection: Connection[Any], audit: SnapshotCommandAuditRecord
) -> None:
    row = connection.execute(
        "SELECT authorization_event_id, lifecycle_event_id, tenant_id, "
        "deployment_id, source_id, raw_object_id, snapshot_id, "
        "snapshot_content_hash, actor_id, actor_type, operation, outcome, "
        "reason, occurred_at FROM source_snapshot_command_audit "
        "WHERE command_event_id = %s",
        (audit.command_event_id,),
    ).fetchone()
    assert row == (
        audit.authorization_event_id,
        audit.lifecycle_event_id,
        audit.tenant_id,
        audit.deployment_id,
        audit.source_id,
        audit.raw_object_id,
        audit.snapshot_id,
        audit.snapshot_content_hash,
        audit.actor_id,
        audit.actor_type.value,
        audit.operation,
        audit.outcome.value,
        audit.reason.value,
        audit.occurred_at,
    )


def write_retrieval(
    repository: PostgresSourceSnapshotRepository,
    connection: Connection[Any],
    metadata: RawObjectMetadata,
    event: SourceSnapshotLifecycleEvent,
    prefix: str,
) -> tuple[ArtifactWriteOutcome, SnapshotCommandAuditRecord]:
    applied, idempotent = audit_pair(connection, event, prefix)
    outcome = repository.save_retrieval_atomic(metadata, event, applied, idempotent)
    selected = applied if outcome is ArtifactWriteOutcome.APPLIED else idempotent
    return outcome, selected


def append_event(
    repository: PostgresSourceSnapshotRepository,
    connection: Connection[Any],
    event: SourceSnapshotLifecycleEvent,
    prefix: str,
) -> tuple[LifecycleWriteOutcome, SnapshotCommandAuditRecord]:
    applied, idempotent = audit_pair(connection, event, prefix)
    outcome = repository.append_lifecycle_atomic(event, applied, idempotent)
    selected = applied if outcome is LifecycleWriteOutcome.APPLIED else idempotent
    return outcome, selected


def write_pointer(
    repository: PostgresSourceSnapshotRepository,
    connection: Connection[Any],
    event: SourceSnapshotLifecycleEvent,
    metadata: RawObjectMetadata,
    prefix: str,
) -> tuple[LifecycleWriteOutcome, SnapshotCommandAuditRecord]:
    applied, idempotent = audit_pair(connection, event, prefix)
    outcome = repository.activate_or_rollback_atomic(
        event, observation_for(event, metadata), applied, idempotent
    )
    selected = applied if outcome is LifecycleWriteOutcome.APPLIED else idempotent
    return outcome, selected


def legacy_upgrade_probe() -> None:
    migrate_up("20260806_0003")
    with connect() as connection:
        register_source(connection, SOURCE_ID)
        connection.execute(
            "INSERT INTO source_runtime_observation ("
            "deployment_id, source_id, availability, observed_at, "
            "active_snapshot_id, retrieved_at, effective_from) "
            "VALUES (%s, %s, 'AVAILABLE', %s, 'legacy-c3c-marker', %s, %s)",
            (
                DEPLOYMENT_ID,
                SOURCE_ID,
                BASE_TIME,
                BASE_TIME,
                BASE_TIME,
            ),
        )
    migrate_up("20260806_0004")
    with connect() as connection:
        marker = connection.execute(
            "SELECT active_snapshot_id FROM source_runtime_observation "
            "WHERE deployment_id = %s AND source_id = %s",
            (DEPLOYMENT_ID, SOURCE_ID),
        ).fetchone()
        assert marker == ("legacy-c3c-marker",)
        constraint = connection.execute(
            "SELECT convalidated FROM pg_constraint "
            "WHERE conrelid = 'source_runtime_observation'::regclass "
            "AND conname = 'fk_source_observation_active_snapshot'"
        ).fetchone()
        assert constraint == (False,)
        repository = PostgresSourceSnapshotRepository(connection)
        try:
            repository.get_runtime_observation(DEPLOYMENT_ID, SOURCE_ID)
        except SourceSnapshotPersistenceError:
            repository_readiness = "FAIL_CLOSED"
        else:
            raise AssertionError("legacy marker was trusted by repository")

        register_source(connection, "c3c-illegal-observation")

        def insert_illegal_observation() -> object:
            return connection.execute(
                "INSERT INTO source_runtime_observation ("
                "deployment_id, source_id, availability, observed_at, "
                "active_snapshot_id, retrieved_at, effective_from) "
                "VALUES (%s, 'c3c-illegal-observation', 'AVAILABLE', %s, "
                "'illegal-post-0004-marker', %s, %s)",
                (DEPLOYMENT_ID, BASE_TIME, BASE_TIME, BASE_TIME),
            )

        assert expect_database_error(insert_illegal_observation) == "23503"
        connection.execute(
            "DELETE FROM source_runtime_observation "
            "WHERE deployment_id = %s AND source_id = %s "
            "AND active_snapshot_id = 'legacy-c3c-marker'",
            (DEPLOYMENT_ID, SOURCE_ID),
        )
        connection.execute(
            "DELETE FROM source_registry WHERE deployment_id = %s "
            "AND source_id = 'c3c-illegal-observation'",
            (DEPLOYMENT_ID,),
        )
    evidence(
        "legacy-0003-upgrade-0004",
        marker_preserved=True,
        foreign_key="NOT VALID",
        new_illegal_observation="REJECTED",
        repository_integrity_readiness=repository_readiness,
    )


def full_lifecycle_probe() -> dict[str, object]:
    persisted_audits: list[SnapshotCommandAuditRecord] = []
    with connect() as connection:
        repository = PostgresSourceSnapshotRepository(connection)
        first_raw = raw_metadata(
            SOURCE_ID, "first", retrieved_at=BASE_TIME + timedelta(seconds=1)
        )
        first_snapshot = parsed_snapshot(
            first_raw, "first", parsed_at=BASE_TIME + timedelta(seconds=3)
        )
        retrieved = lifecycle_event(SourceSnapshotEventType.RETRIEVED, 1, first_raw)
        outcome, audit = write_retrieval(
            repository, connection, first_raw, retrieved, "first-retrieval"
        )
        assert outcome is ArtifactWriteOutcome.APPLIED
        persisted_audits.append(audit)

        retry = lifecycle_event(
            SourceSnapshotEventType.RETRIEVED,
            2,
            first_raw,
            event_id="c3c-event-retrieval-retry",
            reason=retrieved.reason,
            occurred_at=BASE_TIME + timedelta(seconds=2),
        )
        outcome, audit = write_retrieval(
            repository, connection, first_raw, retry, "first-retrieval-retry"
        )
        assert outcome is ArtifactWriteOutcome.IDEMPOTENT
        persisted_audits.append(audit)

        changed = lifecycle_event(
            SourceSnapshotEventType.RETRIEVED,
            2,
            first_raw,
            event_id="c3c-event-retrieval-conflict",
            reason="C3c changed retrieval attempt.",
            occurred_at=BASE_TIME + timedelta(seconds=2),
        )
        applied, idempotent = audit_pair(connection, changed, "retrieval-conflict")
        assert (
            repository.save_retrieval_atomic(first_raw, changed, applied, idempotent)
            is ArtifactWriteOutcome.CONFLICT
        )
        conflict = failure_audit(
            changed,
            prefix="retrieval-conflict",
            authorization_event_id=applied.authorization_event_id,
        )
        repository.append_command_audit(conflict)
        persisted_audits.append(conflict)

        quarantined = lifecycle_event(SourceSnapshotEventType.QUARANTINED, 2, first_raw)
        outcome, audit = append_event(
            repository, connection, quarantined, "first-quarantine"
        )
        assert outcome is LifecycleWriteOutcome.APPLIED
        persisted_audits.append(audit)

        parsed = lifecycle_event(
            SourceSnapshotEventType.PARSED,
            3,
            first_raw,
            snapshot=first_snapshot,
        )
        applied, idempotent = audit_pair(connection, parsed, "first-parse")
        assert (
            repository.save_parsed_snapshot_atomic(
                first_snapshot, parsed, applied, idempotent
            )
            is ArtifactWriteOutcome.APPLIED
        )
        persisted_audits.append(applied)

        first_report = validate_snapshot(
            first_snapshot,
            expected_schema_id=SYNTHETIC_SCHEMA_ID,
            previous_accepted=None,
            validated_at=BASE_TIME + timedelta(seconds=4),
        )
        validated = lifecycle_event(
            SourceSnapshotEventType.VALIDATED,
            4,
            first_raw,
            snapshot=first_snapshot,
            report=first_report,
        )
        outcome, audit = append_event(
            repository, connection, validated, "first-validation"
        )
        assert outcome is LifecycleWriteOutcome.APPLIED
        persisted_audits.append(audit)

        approved = lifecycle_event(
            SourceSnapshotEventType.APPROVED,
            5,
            first_raw,
            snapshot=first_snapshot,
        )
        outcome, audit = append_event(
            repository, connection, approved, "first-approval"
        )
        assert outcome is LifecycleWriteOutcome.APPLIED
        persisted_audits.append(audit)

        activated = lifecycle_event(
            SourceSnapshotEventType.ACTIVATED,
            6,
            first_raw,
            snapshot=first_snapshot,
        )
        outcome, audit = write_pointer(
            repository, connection, activated, first_raw, "first-activation"
        )
        assert outcome is LifecycleWriteOutcome.APPLIED
        persisted_audits.append(audit)

        second_raw = raw_metadata(
            SOURCE_ID, "second", retrieved_at=BASE_TIME + timedelta(seconds=7)
        )
        second_snapshot = parsed_snapshot(
            second_raw, "second", parsed_at=BASE_TIME + timedelta(seconds=9)
        )
        second_retrieved = lifecycle_event(
            SourceSnapshotEventType.RETRIEVED, 7, second_raw
        )
        outcome, audit = write_retrieval(
            repository,
            connection,
            second_raw,
            second_retrieved,
            "second-retrieval",
        )
        assert outcome is ArtifactWriteOutcome.APPLIED
        persisted_audits.append(audit)
        second_quarantined = lifecycle_event(
            SourceSnapshotEventType.QUARANTINED, 8, second_raw
        )
        outcome, audit = append_event(
            repository, connection, second_quarantined, "second-quarantine"
        )
        assert outcome is LifecycleWriteOutcome.APPLIED
        persisted_audits.append(audit)
        second_parsed = lifecycle_event(
            SourceSnapshotEventType.PARSED,
            9,
            second_raw,
            snapshot=second_snapshot,
        )
        applied, idempotent = audit_pair(connection, second_parsed, "second-parse")
        assert (
            repository.save_parsed_snapshot_atomic(
                second_snapshot, second_parsed, applied, idempotent
            )
            is ArtifactWriteOutcome.APPLIED
        )
        persisted_audits.append(applied)

        second_report = validate_snapshot(
            second_snapshot,
            expected_schema_id=SYNTHETIC_SCHEMA_ID,
            previous_accepted=first_snapshot,
            validated_at=BASE_TIME + timedelta(seconds=10),
        )
        second_validated = lifecycle_event(
            SourceSnapshotEventType.VALIDATED,
            10,
            second_raw,
            snapshot=second_snapshot,
            report=second_report,
        )
        outcome, audit = append_event(
            repository, connection, second_validated, "second-validation"
        )
        assert outcome is LifecycleWriteOutcome.APPLIED
        persisted_audits.append(audit)
        second_approved = lifecycle_event(
            SourceSnapshotEventType.APPROVED,
            11,
            second_raw,
            snapshot=second_snapshot,
        )
        outcome, audit = append_event(
            repository, connection, second_approved, "second-approval"
        )
        assert outcome is LifecycleWriteOutcome.APPLIED
        persisted_audits.append(audit)
        replacement = lifecycle_event(
            SourceSnapshotEventType.ACTIVATED,
            12,
            second_raw,
            snapshot=second_snapshot,
            previous=first_snapshot.reference(),
        )
        outcome, audit = write_pointer(
            repository, connection, replacement, second_raw, "replacement"
        )
        assert outcome is LifecycleWriteOutcome.APPLIED
        persisted_audits.append(audit)
        rollback = lifecycle_event(
            SourceSnapshotEventType.ROLLED_BACK,
            13,
            first_raw,
            snapshot=first_snapshot,
            previous=second_snapshot.reference(),
        )
        outcome, audit = write_pointer(
            repository, connection, rollback, first_raw, "rollback"
        )
        assert outcome is LifecycleWriteOutcome.APPLIED
        persisted_audits.append(audit)

        events, lifecycle = repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
        assert len(events) == 13
        assert lifecycle.active_snapshot == first_snapshot.reference()
        assert (
            repository.get_snapshot(
                DEPLOYMENT_ID, SOURCE_ID, first_snapshot.snapshot_id
            )
            == first_snapshot
        )
        assert (
            repository.get_snapshot(
                DEPLOYMENT_ID, SOURCE_ID, second_snapshot.snapshot_id
            )
            == second_snapshot
        )
        assert repository.get_runtime_observation(
            DEPLOYMENT_ID, SOURCE_ID
        ) == observation_for(rollback, first_raw)
        for persisted in persisted_audits:
            assert_audit_row(connection, persisted)

        absent = raw_metadata(
            SOURCE_ID, "absent", retrieved_at=BASE_TIME + timedelta(seconds=14)
        )
        absent_event = lifecycle_event(
            SourceSnapshotEventType.RETRIEVED,
            14,
            absent,
            event_id="c3c-event-absent-failure",
            occurred_at=BASE_TIME + timedelta(seconds=14),
        )
        absent_auth = "c3c-auth-absent-failure"
        target_type, target_id = target_for_event(absent_event)
        insert_authorization(
            connection,
            authorization_event_id=absent_auth,
            actor_id=absent_event.actor_id,
            actor_type=absent_event.actor_type,
            operation=AUTHORIZATION_OPERATION_BY_EVENT_TYPE[absent_event.event_type],
            target_type=target_type,
            target_id=target_id,
            occurred_at=absent_event.occurred_at,
        )
        absent_failure = failure_audit(
            absent_event,
            prefix="absent-artifact",
            authorization_event_id=absent_auth,
        )
        repository.append_command_audit(absent_failure)
        assert (
            repository.get_raw_metadata(DEPLOYMENT_ID, SOURCE_ID, absent.object_id)
            is None
        )
        assert_audit_row(connection, absent_failure)

    evidence(
        "repository-full-lifecycle",
        retrieval=["APPLIED", "IDEMPOTENT", "CONFLICT"],
        lifecycle_events=13,
        active_snapshot=first_snapshot.snapshot_id,
        current_attempt_audits=len(persisted_audits) + 1,
        absent_artifact_failure_audit=True,
    )
    return {
        "first_raw": first_raw,
        "first_snapshot": first_snapshot,
        "second_snapshot": second_snapshot,
        "rollback": rollback,
    }


def constraint_probe() -> None:
    with connect() as connection:
        immutable_updates = {
            "source_raw_object_metadata": "original_name = original_name",
            "source_parsed_snapshot": "parser_id = parser_id",
            "source_snapshot_validation_evidence": "passed = passed",
            "source_snapshot_lifecycle_event": "reason = reason",
            "source_snapshot_command_audit": "reason = reason",
        }
        for table, assignment in immutable_updates.items():
            assert (
                expect_database_error(
                    lambda table=table, assignment=assignment: connection.execute(
                        f"UPDATE {table} SET {assignment}"
                    ),
                    sqlstate="55000",
                )
                == "55000"
            )
        assert (
            expect_database_error(
                lambda: connection.execute(
                    "DELETE FROM source_snapshot_command_audit "
                    "WHERE command_event_id = ("
                    "SELECT command_event_id FROM source_snapshot_command_audit LIMIT 1)"
                ),
                sqlstate="55000",
            )
            == "55000"
        )

        assert (
            scalar(
                connection,
                "SELECT tradesieve_valid_source_snapshot_payload(convert_to('{}', 'UTF8'))",
            )
            is False
        )
        assert (
            scalar(
                connection,
                "SELECT tradesieve_valid_source_snapshot_payload("
                "convert_to(repeat('x', 4194305), 'UTF8'))",
            )
            is False
        )
        assert (
            scalar(
                connection,
                "SELECT tradesieve_valid_source_validation_payload("
                "convert_to('{}', 'UTF8'))",
            )
            is False
        )
        assert (
            scalar(
                connection,
                "SELECT tradesieve_valid_source_validation_payload("
                "convert_to(repeat('x', 2097153), 'UTF8'))",
            )
            is False
        )

        def insert_bad_payload(payload_expression: str, digit: str) -> object:
            return connection.execute(
                "INSERT INTO source_parsed_snapshot ("
                "deployment_id, source_id, snapshot_id, content_hash, "
                "raw_object_id, raw_content_hash, raw_byte_length, parser_id, "
                "parser_version, schema_id, declared_record_count, parsed_at, "
                "private_payload) SELECT deployment_id, source_id, %s, %s, "
                "raw_object_id, raw_content_hash, raw_byte_length, parser_id, "
                "parser_version, schema_id, declared_record_count, parsed_at, "
                f"{payload_expression} FROM source_parsed_snapshot LIMIT 1",
                (f"snapshot-{digit * 64}", f"sha256:{digit * 64}"),
            )

        assert (
            expect_database_error(
                lambda: insert_bad_payload("convert_to('{}', 'UTF8')", "a"),
                sqlstate="23514",
            )
            == "23514"
        )
        assert (
            expect_database_error(
                lambda: insert_bad_payload(
                    "convert_to(repeat('x', 4194305), 'UTF8')", "b"
                ),
                sqlstate="23514",
            )
            == "23514"
        )

        sample = connection.execute(
            "SELECT * FROM source_snapshot_command_audit ORDER BY occurred_at LIMIT 1"
        ).fetchone()
        assert sample is not None

        def insert_missing_auth() -> object:
            return connection.execute(
                "INSERT INTO source_snapshot_command_audit ("
                "command_event_id, authorization_event_id, lifecycle_event_id, "
                "tenant_id, deployment_id, source_id, raw_object_id, snapshot_id, "
                "snapshot_content_hash, target_type, target_id, actor_id, "
                "actor_type, operation, outcome, reason, occurred_at) "
                "SELECT 'c3c-constraint-missing-auth', 'c3c-missing-auth', NULL, "
                "tenant_id, deployment_id, source_id, raw_object_id, NULL, NULL, "
                "'source', target_id, actor_id, actor_type, "
                "'SOURCE_SNAPSHOT_INGEST', 'FAILURE', 'CONFLICT', CURRENT_TIMESTAMP "
                "FROM source_snapshot_command_audit LIMIT 1"
            )

        assert expect_database_error(insert_missing_auth, sqlstate="23503") == "23503"

        def event_without_audit() -> None:
            with connection.transaction():
                connection.execute(
                    "INSERT INTO source_snapshot_lifecycle_event ("
                    "deployment_id, source_id, sequence, event_id, event_type, "
                    "raw_object_id, raw_content_hash, raw_byte_length, snapshot_id, "
                    "snapshot_content_hash, previous_active_snapshot_id, "
                    "previous_active_snapshot_content_hash, validation_report_hash, "
                    "validation_diff_hash, validation_passed, actor_id, actor_type, "
                    "reason, occurred_at) SELECT raw.deployment_id, raw.source_id, 14, "
                    "'c3c-constraint-event-no-audit', 'QUARANTINED', raw.object_id, "
                    "raw.content_hash, raw.byte_length, NULL, NULL, NULL, NULL, NULL, "
                    "NULL, NULL, 'c3c-operator', 'SERVICE', "
                    "'C3c missing audit constraint.', CURRENT_TIMESTAMP "
                    "FROM source_raw_object_metadata AS raw "
                    "WHERE raw.deployment_id = %s AND raw.source_id = %s "
                    "ORDER BY raw.retrieved_at LIMIT 1",
                    (DEPLOYMENT_ID, SOURCE_ID),
                )

        assert expect_database_error(event_without_audit, sqlstate="23514") == "23514"
        assert (
            scalar(
                connection,
                "SELECT count(*) FROM source_snapshot_lifecycle_event "
                "WHERE event_id = 'c3c-constraint-event-no-audit'",
            )
            == 0
        )

        source_target_id = source_authorization_target_id(
            DEPLOYMENT_ID, SOURCE_SET_ID, SOURCE_ID
        )
        first_raw_row = connection.execute(
            "SELECT object_id, content_hash, byte_length "
            "FROM source_raw_object_metadata WHERE deployment_id = %s "
            "AND source_id = %s ORDER BY retrieved_at LIMIT 1",
            (DEPLOYMENT_ID, SOURCE_ID),
        ).fetchone()
        assert first_raw_row is not None
        first_raw_object_id, first_raw_content_hash, first_raw_byte_length = (
            first_raw_row
        )

        def mismatched_authorization_target() -> None:
            with connection.transaction():
                insert_authorization(
                    connection,
                    authorization_event_id="c3c-auth-wrong-target",
                    actor_id="c3c-operator",
                    actor_type=LifecycleActorType.SERVICE,
                    operation=Operation.SOURCE_SNAPSHOT_INGEST.value,
                    target_type=SOURCE_TARGET_TYPE,
                    target_id=f"source-{'e' * 64}",
                    occurred_at=BASE_TIME + timedelta(minutes=1),
                )
                connection.execute(
                    "INSERT INTO source_snapshot_command_audit ("
                    "command_event_id, authorization_event_id, lifecycle_event_id, "
                    "tenant_id, deployment_id, source_id, raw_object_id, snapshot_id, "
                    "snapshot_content_hash, target_type, target_id, actor_id, "
                    "actor_type, operation, outcome, reason, occurred_at) "
                    "VALUES ('c3c-command-wrong-target', 'c3c-auth-wrong-target', "
                    "NULL, %s, %s, %s, %s, NULL, NULL, %s, %s, 'c3c-operator', "
                    "'SERVICE', 'SOURCE_SNAPSHOT_INGEST', 'FAILURE', 'CONFLICT', %s)",
                    (
                        TENANT_ID,
                        DEPLOYMENT_ID,
                        SOURCE_ID,
                        first_raw_object_id,
                        SOURCE_TARGET_TYPE,
                        source_target_id,
                        BASE_TIME + timedelta(minutes=1),
                    ),
                )

        assert (
            expect_database_error(mismatched_authorization_target, sqlstate="23514")
            == "23514"
        )
        assert connection.execute(
            "SELECT "
            "(SELECT count(*) FROM authorization_audit_event "
            " WHERE event_id = 'c3c-auth-wrong-target'), "
            "(SELECT count(*) FROM source_snapshot_command_audit "
            " WHERE command_event_id = 'c3c-command-wrong-target')"
        ).fetchone() == (0, 0)

        def forged_lifecycle_link() -> None:
            with connection.transaction():
                connection.execute(
                    "INSERT INTO source_snapshot_lifecycle_event ("
                    "deployment_id, source_id, sequence, event_id, event_type, "
                    "raw_object_id, raw_content_hash, raw_byte_length, snapshot_id, "
                    "snapshot_content_hash, previous_active_snapshot_id, "
                    "previous_active_snapshot_content_hash, validation_report_hash, "
                    "validation_diff_hash, validation_passed, actor_id, actor_type, "
                    "reason, occurred_at) VALUES ("
                    "%s, %s, 14, 'c3c-event-forged-link', 'QUARANTINED', %s, %s, "
                    "%s, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 'c3c-operator', "
                    "'SERVICE', 'C3c forged lifecycle linkage.', %s)",
                    (
                        DEPLOYMENT_ID,
                        SOURCE_ID,
                        first_raw_object_id,
                        first_raw_content_hash,
                        first_raw_byte_length,
                        BASE_TIME + timedelta(minutes=1, seconds=1),
                    ),
                )
                insert_authorization(
                    connection,
                    authorization_event_id="c3c-auth-forged-link",
                    actor_id="c3c-forger",
                    actor_type=LifecycleActorType.SERVICE,
                    operation=Operation.SOURCE_SNAPSHOT_INGEST.value,
                    target_type=SOURCE_TARGET_TYPE,
                    target_id=source_target_id,
                    occurred_at=BASE_TIME + timedelta(minutes=1, seconds=1),
                )
                connection.execute(
                    "INSERT INTO source_snapshot_command_audit ("
                    "command_event_id, authorization_event_id, lifecycle_event_id, "
                    "tenant_id, deployment_id, source_id, raw_object_id, snapshot_id, "
                    "snapshot_content_hash, target_type, target_id, actor_id, "
                    "actor_type, operation, outcome, reason, occurred_at) "
                    "VALUES ('c3c-command-forged-link', 'c3c-auth-forged-link', "
                    "'c3c-event-forged-link', %s, %s, %s, %s, NULL, NULL, %s, %s, "
                    "'c3c-forger', 'SERVICE', 'SOURCE_SNAPSHOT_INGEST', 'SUCCESS', "
                    "'QUARANTINED', %s)",
                    (
                        TENANT_ID,
                        DEPLOYMENT_ID,
                        SOURCE_ID,
                        first_raw_object_id,
                        SOURCE_TARGET_TYPE,
                        source_target_id,
                        BASE_TIME + timedelta(minutes=1, seconds=1),
                    ),
                )

        assert expect_database_error(forged_lifecycle_link, sqlstate="23514") == "23514"
        assert connection.execute(
            "SELECT "
            "(SELECT count(*) FROM authorization_audit_event "
            " WHERE event_id = 'c3c-auth-forged-link'), "
            "(SELECT count(*) FROM source_snapshot_lifecycle_event "
            " WHERE event_id = 'c3c-event-forged-link'), "
            "(SELECT count(*) FROM source_snapshot_command_audit "
            " WHERE command_event_id = 'c3c-command-forged-link')"
        ).fetchone() == (0, 0, 0)

        atomic_source = "c3c-atomic-source"
        register_source(connection, atomic_source)
        metadata = raw_metadata(
            atomic_source,
            "atomic",
            retrieved_at=BASE_TIME + timedelta(minutes=1),
        )
        event = lifecycle_event(
            SourceSnapshotEventType.RETRIEVED,
            1,
            metadata,
            event_id="c3c-event-atomic-failure",
            occurred_at=BASE_TIME + timedelta(minutes=1),
        )
        missing_auth = "c3c-auth-never-inserted"
        applied = SnapshotCommandAuditRecord(
            "c3c-command-atomic-applied",
            missing_auth,
            event.event_id,
            TENANT_ID,
            DEPLOYMENT_ID,
            atomic_source,
            metadata.object_id,
            None,
            None,
            event.actor_id,
            event.actor_type,
            Operation.SOURCE_SNAPSHOT_INGEST.value,
            SnapshotCommandOutcome.SUCCESS,
            SnapshotCommandReason.RETRIEVED,
            event.occurred_at,
        )
        idempotent = SnapshotCommandAuditRecord(
            "c3c-command-atomic-idempotent",
            missing_auth,
            None,
            TENANT_ID,
            DEPLOYMENT_ID,
            atomic_source,
            metadata.object_id,
            None,
            None,
            event.actor_id,
            event.actor_type,
            Operation.SOURCE_SNAPSHOT_INGEST.value,
            SnapshotCommandOutcome.SUCCESS,
            SnapshotCommandReason.RETRIEVE_IDEMPOTENT,
            event.occurred_at,
        )
        repository = PostgresSourceSnapshotRepository(connection)
        try:
            repository.save_retrieval_atomic(metadata, event, applied, idempotent)
        except SourceSnapshotPersistenceError:
            pass
        else:
            raise AssertionError("missing authorization did not fail atomically")
        assert connection.execute(
            "SELECT "
            "(SELECT count(*) FROM source_raw_object_metadata WHERE source_id = %s), "
            "(SELECT count(*) FROM source_snapshot_lifecycle_event WHERE source_id = %s), "
            "(SELECT count(*) FROM source_snapshot_command_audit WHERE source_id = %s), "
            "(SELECT count(*) FROM source_snapshot_lifecycle_state WHERE source_id = %s)",
            (atomic_source, atomic_source, atomic_source, atomic_source),
        ).fetchone() == (0, 0, 0, 0)

    evidence(
        "database-constraints-and-atomicity",
        append_only_tables=5,
        snapshot_payload_shape_and_4mib="REJECTED",
        validation_payload_shape_and_2mib="REJECTED",
        missing_authorization="REJECTED",
        authorization_target_mismatch="REJECTED_NO_RESIDUE",
        forged_event_linkage="REJECTED_NO_RESIDUE",
        event_without_audit="REJECTED_AT_COMMIT",
        partial_write_rows=0,
    )


def concurrency_probe() -> None:
    race_source = "c3c-race-source"
    cas_source = "c3c-cas-source"
    with connect() as setup:
        register_source(setup, race_source)
        register_source(setup, cas_source)

    metadata = raw_metadata(
        race_source, "race", retrieved_at=BASE_TIME + timedelta(minutes=2)
    )
    barrier = Barrier(2)

    def contender(index: int) -> str:
        with connect() as connection:
            repository = PostgresSourceSnapshotRepository(connection)
            event = lifecycle_event(
                SourceSnapshotEventType.RETRIEVED,
                1,
                metadata,
                event_id=f"c3c-event-race-{index}",
                reason="C3c retrieval race.",
                occurred_at=BASE_TIME + timedelta(minutes=2, microseconds=index),
            )
            applied, idempotent = audit_pair(connection, event, f"race-{index}")
            barrier.wait(timeout=10)
            return repository.save_retrieval_atomic(
                metadata, event, applied, idempotent
            ).value

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = sorted(executor.map(contender, (1, 2)))
    assert outcomes == ["APPLIED", "IDEMPOTENT"]

    with connect() as connection:
        assert connection.execute(
            "SELECT count(*), count(DISTINCT event_id) "
            "FROM source_snapshot_lifecycle_event WHERE source_id = %s",
            (race_source,),
        ).fetchone() == (1, 1)
        assert connection.execute(
            "SELECT count(*), count(*) FILTER (WHERE lifecycle_event_id IS NOT NULL), "
            "count(*) FILTER (WHERE reason = 'RETRIEVE_IDEMPOTENT') "
            "FROM source_snapshot_command_audit WHERE source_id = %s",
            (race_source,),
        ).fetchone() == (2, 1, 1)
        events, _lifecycle = PostgresSourceSnapshotRepository(
            connection
        ).get_lifecycle_snapshot(DEPLOYMENT_ID, race_source)
        assert len(events) == 1

        old_time = BASE_TIME + timedelta(minutes=3)
        connection.execute(
            "INSERT INTO source_runtime_observation ("
            "deployment_id, source_id, availability, observed_at, "
            "active_snapshot_id, retrieved_at, effective_from) "
            "VALUES (%s, %s, 'UNAVAILABLE', %s, NULL, NULL, NULL)",
            (DEPLOYMENT_ID, cas_source, old_time),
        )

    cas_barrier = Barrier(2)

    def cas_contender(index: int) -> int:
        with connect() as connection:
            cas_barrier.wait(timeout=10)
            result = connection.execute(
                "UPDATE source_runtime_observation SET observed_at = %s "
                "WHERE deployment_id = %s AND source_id = %s AND observed_at = %s",
                (
                    old_time + timedelta(microseconds=index),
                    DEPLOYMENT_ID,
                    cas_source,
                    old_time,
                ),
            )
            assert result.rowcount is not None
            return result.rowcount

    with ThreadPoolExecutor(max_workers=2) as executor:
        cas_results = sorted(executor.map(cas_contender, (1, 2)))
    assert cas_results == [0, 1]
    with connect() as cleanup:
        cleanup.execute(
            "DELETE FROM source_runtime_observation "
            "WHERE deployment_id = %s AND source_id = %s",
            (DEPLOYMENT_ID, cas_source),
        )
    evidence(
        "two-connection-concurrency",
        retrieval_outcomes=outcomes,
        responsible_events=1,
        command_audits=2,
        cas_rowcounts=cas_results,
    )


def corruption_repair_probe(context: dict[str, object]) -> None:
    first_raw = context["first_raw"]
    first_snapshot = context["first_snapshot"]
    rollback = context["rollback"]
    assert isinstance(first_raw, RawObjectMetadata)
    assert isinstance(first_snapshot, ParsedSourceSnapshot)
    assert isinstance(rollback, SourceSnapshotLifecycleEvent)
    with connect() as connection:
        repository = PostgresSourceSnapshotRepository(connection)
        connection.execute(
            "UPDATE source_snapshot_lifecycle_state SET last_sequence = 12 "
            "WHERE deployment_id = %s AND source_id = %s",
            (DEPLOYMENT_ID, SOURCE_ID),
        )
        try:
            repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
        except SourceSnapshotPersistenceError:
            state_readiness = "FAIL_CLOSED"
        else:
            raise AssertionError("corrupt lifecycle projection was trusted")
        connection.execute(
            "UPDATE source_snapshot_lifecycle_state SET last_sequence = 13, "
            "active_snapshot_id = %s, active_snapshot_content_hash = %s "
            "WHERE deployment_id = %s AND source_id = %s",
            (
                first_snapshot.snapshot_id,
                first_snapshot.content_hash,
                DEPLOYMENT_ID,
                SOURCE_ID,
            ),
        )
        assert len(repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)[0]) == 13

        connection.execute(
            "UPDATE source_runtime_observation SET availability = 'UNAVAILABLE' "
            "WHERE deployment_id = %s AND source_id = %s",
            (DEPLOYMENT_ID, SOURCE_ID),
        )
        try:
            repository.get_runtime_observation(DEPLOYMENT_ID, SOURCE_ID)
        except SourceSnapshotPersistenceError:
            observation_readiness = "FAIL_CLOSED"
        else:
            raise AssertionError("corrupt runtime observation was trusted")
        expected = observation_for(rollback, first_raw)
        connection.execute(
            "UPDATE source_runtime_observation SET availability = %s, "
            "observed_at = %s, active_snapshot_id = %s, retrieved_at = %s, "
            "effective_from = %s WHERE deployment_id = %s AND source_id = %s",
            (
                expected.availability.value,
                expected.observed_at,
                expected.active_snapshot_id,
                expected.retrieved_at,
                expected.effective_from,
                DEPLOYMENT_ID,
                SOURCE_ID,
            ),
        )
        assert repository.get_runtime_observation(DEPLOYMENT_ID, SOURCE_ID) == expected
    evidence(
        "corruption-and-controlled-repair",
        lifecycle_state=state_readiness,
        observation=observation_readiness,
        repaired=True,
    )


def downgrade_reupgrade_probe() -> None:
    with connect() as connection:
        before = connection.execute(
            "SELECT active_snapshot_id FROM source_runtime_observation "
            "WHERE deployment_id = %s AND source_id = %s",
            (DEPLOYMENT_ID, SOURCE_ID),
        ).fetchone()
        assert before is not None
    migrate_down("20260806_0003")
    with connect() as connection:
        assert (
            scalar(
                connection,
                "SELECT version_num FROM alembic_version",
            )
            == "20260806_0003"
        )
        assert (
            scalar(
                connection,
                "SELECT to_regclass('source_registry') IS NOT NULL",
            )
            is True
        )
        assert (
            scalar(
                connection,
                "SELECT to_regclass('source_runtime_observation') IS NOT NULL",
            )
            is True
        )
        preserved = connection.execute(
            "SELECT active_snapshot_id FROM source_runtime_observation "
            "WHERE deployment_id = %s AND source_id = %s",
            (DEPLOYMENT_ID, SOURCE_ID),
        ).fetchone()
        assert preserved == before
        connection.execute(
            "UPDATE source_runtime_observation "
            "SET active_snapshot_id = 'legacy-c3c-downgrade-marker' "
            "WHERE deployment_id = %s AND source_id = %s",
            (DEPLOYMENT_ID, SOURCE_ID),
        )
    migrate_up("20260806_0004")
    with connect() as connection:
        assert (
            scalar(
                connection,
                "SELECT version_num FROM alembic_version",
            )
            == "20260806_0004"
        )
        assert connection.execute(
            "SELECT active_snapshot_id FROM source_runtime_observation "
            "WHERE deployment_id = %s AND source_id = %s",
            (DEPLOYMENT_ID, SOURCE_ID),
        ).fetchone() == ("legacy-c3c-downgrade-marker",)
        assert (
            scalar(
                connection,
                "SELECT NOT convalidated FROM pg_constraint "
                "WHERE conrelid = 'source_runtime_observation'::regclass "
                "AND conname = 'fk_source_observation_active_snapshot'",
            )
            is True
        )
        try:
            PostgresSourceSnapshotRepository(connection).get_runtime_observation(
                DEPLOYMENT_ID, SOURCE_ID
            )
        except SourceSnapshotPersistenceError:
            pass
        else:
            raise AssertionError("re-upgraded legacy marker was trusted")
    evidence(
        "downgrade-0004-to-0003-and-reupgrade",
        old_tables_preserved=True,
        observation_preserved=True,
        legacy_marker_reupgrade="FAIL_CLOSED",
    )


def main() -> None:
    legacy_upgrade_probe()
    context = full_lifecycle_probe()
    constraint_probe()
    concurrency_probe()
    corruption_repair_probe(context)
    downgrade_reupgrade_probe()
    evidence("c3c-postgresql-18.4-acceptance", phases=6)


if __name__ == "__main__":
    main()
