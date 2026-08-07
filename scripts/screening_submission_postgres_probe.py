#!/usr/bin/env python3
"""Executable PostgreSQL 18.4 acceptance probe for TS-302 Slice C3."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from typing import Any, cast

import psycopg
from alembic import command
from alembic.config import Config
from psycopg import Connection
from psycopg.pq import TransactionStatus

from tradesieve.adapters.postgres_authorization import (
    PostgresAuthorizationAuditSink,
)
from tradesieve.adapters.postgres_screening_submission import (
    MAX_ATTEMPTS,
    PostgresScreeningSubmissionUnitOfWork,
)
from tradesieve.adapters.screening_submission_codec import encode_request_context
from tradesieve.application.auth import (
    ActorContext,
    ActorType,
    AuthorizationAuditEvent,
    AuthorizationRequest,
    AuthorizationService,
    Operation,
    RequestContext,
    ResolvedTargetFacts,
    Role,
    Scope,
)
from tradesieve.application.screening_intake import CanonicalScreeningIntake
from tradesieve.application.screening_submission import (
    ScreeningSubmissionAtomicWrite,
    ScreeningSubmissionService,
    ScreeningSubmissionServiceDisposition,
    bind_screening_submission,
    screening_authorization_target,
    screening_idempotency_scope,
)
from tradesieve.domain.screening_submission import (
    ScreeningAcceptedOutboxIntent,
    ScreeningAttemptOutcome,
    ScreeningIdempotencyResult,
    ScreeningIdentity,
    ScreeningOutboxEventType,
)
from tradesieve.manage import alembic_database_url

DATABASE_URL = os.environ["TRADESIEVE_DATABASE_URL"]
MIGRATION_ROOT = Path(os.environ.get("TRADESIEVE_MIGRATION_ROOT", "/app"))
BASE_TIME = datetime(2026, 8, 7, 8, 0, tzinfo=UTC)
PRIVATE_KEY = "ts302-c3-private-idempotency-key"  # pragma: allowlist secret
SCREENING_TABLES = (
    "screening_opaque_id_registry",
    "screening_accepted_intake",
    "screening_identity",
    "screening_idempotency_ledger",
    "screening_attempt_audit",
    "screening_accepted_outbox",
)


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


def admin_connect() -> Connection[Any]:
    return psycopg.connect(DATABASE_URL, autocommit=True)


def submission_connect() -> Connection[Any]:
    return psycopg.connect(DATABASE_URL, autocommit=False)


class DiagnosticConnection:
    """Expose only failing SQL category/state while the adapter stays redacted."""

    def __init__(self, connection: Connection[Any]) -> None:
        self.connection = connection
        self.info = connection.info

    @property
    def autocommit(self) -> bool:
        return self.connection.autocommit

    def execute(self, query: str, params: tuple[object, ...] | None = None) -> object:
        try:
            return self.connection.execute(query, params)
        except psycopg.Error as exc:
            category = " ".join(query.split()[:4])
            print(
                json.dumps(
                    {
                        "phase": "diagnostic-sql-failure",
                        "sql_category": category,
                        "sqlstate": exc.sqlstate,
                        "status": "FAIL",
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            raise

    def commit(self) -> None:
        try:
            self.connection.commit()
        except psycopg.Error as exc:
            print(
                json.dumps(
                    {
                        "phase": "diagnostic-commit-failure",
                        "sqlstate": exc.sqlstate,
                        "status": "FAIL",
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            raise

    def rollback(self) -> None:
        self.connection.rollback()

    def close(self) -> None:
        self.connection.close()


def diagnostic_submission_connect() -> Connection[Any]:
    return cast(Connection[Any], DiagnosticConnection(submission_connect()))


def scalar(
    connection: Connection[Any], query: str, params: tuple[object, ...] = ()
) -> Any:
    row = connection.execute(query, params).fetchone()
    assert row is not None and len(row) == 1
    return row[0]


def expect_transaction_error(
    action: Callable[[Connection[Any]], object],
    *,
    sqlstate: str,
) -> None:
    connection = submission_connect()
    try:
        action(connection)
        connection.commit()
    except psycopg.Error as exc:
        assert exc.sqlstate == sqlstate, (exc.sqlstate, sqlstate)
        connection.rollback()
    else:
        raise AssertionError("database operation unexpectedly succeeded")
    finally:
        connection.close()


def table_count(connection: Connection[Any], table: str) -> int:
    assert table in (*SCREENING_TABLES, "authorization_audit_event")
    return cast(int, scalar(connection, f"SELECT count(*) FROM {table}"))


def request_context(
    *,
    tenant_id: str = "ts302-tenant",
    client_id: str = "ts302-client",
    subject: str = "ts302-actor",
    correlation_id: str = "ts302-correlation",
) -> RequestContext:
    return RequestContext(
        actor=ActorContext(
            subject=subject,
            client_id=client_id,
            tenant_id=tenant_id,
            actor_type=ActorType.SERVICE,
            scopes=frozenset({Scope.SCREENING_SUBMIT}),
            roles=frozenset({Role.AUDITOR}),
            issuer="https://identity.example.test/",
            audience="urn:tradesieve:ts302-c3",
            issued_at=BASE_TIME - timedelta(minutes=1),
            expires_at=BASE_TIME + timedelta(hours=2),
            demo_identity=False,
        ),
        tenant_id=tenant_id,
        correlation_id=correlation_id,
    )


def intake(
    *,
    action: str = "CUSTOMER_ONBOARDING",
    subject: str = "ts302-actor",
    correlation_id: str = "ts302-correlation",
    indent: int | None = None,
) -> CanonicalScreeningIntake:
    context = request_context(
        subject=subject,
        correlation_id=correlation_id,
    )
    body: dict[str, object] = {
        "schema_version": "1.0.0",
        "tenant_id": context.tenant_id,
        "correlation_id": context.correlation_id,
        "data_classification": "SYNTHETIC",
        "external_object": {
            "system": "synthetic-crm",
            "object_type": "CUSTOMER",
            "object_id": "ts302-synthetic-customer",
            "object_version": "1",
        },
        "proposed_action": action,
        "activities": [],
        "action_due_at": None,
        "legal_nexus": [],
        "parties": [],
        "ownership_and_control": [],
        "goods": [],
        "route": None,
        "documents": [],
        "payment": None,
    }
    raw = json.dumps(
        body,
        ensure_ascii=False,
        indent=indent,
        separators=None if indent is not None else (",", ":"),
    ).encode()
    return CanonicalScreeningIntake.decode(
        raw,
        "application/json",
        context=context,
        received_at=BASE_TIME,
    )


class VisibleEntitlements:
    def resolve(
        self,
        *,
        actor_subject: str,
        actor_tenant_id: str,
        operation: str,
        target_tenant_id: str,
        target_type: str,
        target_id: str,
    ) -> ResolvedTargetFacts:
        del (
            actor_subject,
            actor_tenant_id,
            operation,
            target_tenant_id,
            target_type,
            target_id,
        )
        return ResolvedTargetFacts()


def persist_authorization(event: AuthorizationAuditEvent) -> None:
    connection = admin_connect()
    try:
        PostgresAuthorizationAuditSink(connection)(event)
    finally:
        connection.close()


def authorization_service(event_id: str) -> AuthorizationService:
    return AuthorizationService(
        persist_authorization,
        VisibleEntitlements(),
        event_id_factory=lambda: event_id,
    )


def atomic_request(
    item: CanonicalScreeningIntake,
    *,
    event_id: str,
    attempt_id: str,
    intake_id: str,
    screening_id: str,
    outbox_id: str,
    occurred_at: datetime,
    key: str = PRIVATE_KEY,
) -> ScreeningSubmissionAtomicWrite:
    scope = screening_idempotency_scope(item, key)
    authorized = authorization_service(event_id).require(
        AuthorizationRequest(
            context=item.context,
            operation=Operation.SCREENING_SUBMIT,
            target=screening_authorization_target(scope),
        ),
        now=occurred_at,
    )
    bound = bind_screening_submission(item, key, authorized)
    accepted = bound.accepted_intake_reference(intake_id)
    screening = ScreeningIdentity(screening_id, accepted, occurred_at)
    outbox = ScreeningAcceptedOutboxIntent(
        event_id=outbox_id,
        event_type=ScreeningOutboxEventType.SCREENING_ACCEPTED,
        schema_version=accepted.schema_version,
        occurred_at=occurred_at,
        intake_id=intake_id,
        screening_id=screening_id,
    )
    result = ScreeningIdempotencyResult(
        scope=scope,
        canonical_hash=accepted.canonical_hash,
        intake=accepted,
        screening=screening,
        outbox=outbox,
        committed_at=occurred_at,
    )
    return ScreeningSubmissionAtomicWrite(
        bound=bound,
        proposed_result=result,
        attempt_id=attempt_id,
        occurred_at=occurred_at,
    )


def legacy_upgrade_probe() -> None:
    migrate_up("20260806_0004")
    with admin_connect() as connection:
        assert scalar(connection, "SELECT version_num FROM alembic_version") == (
            "20260806_0004"
        )
        assert all(
            scalar(connection, "SELECT to_regclass(%s) IS NULL", (table,)) is True
            for table in SCREENING_TABLES
        )
        connection.execute(
            "INSERT INTO authorization_audit_event ("
            "event_id, occurred_at, actor_subject, client_id, actor_type, "
            "actor_tenant_id, request_tenant_id, target_tenant_id, operation, "
            "target_ref, correlation_id, outcome, reason) VALUES ("
            "'ts302-legacy-auth', %s, 'ts302-legacy-actor', 'ts302-legacy-client', "
            "'SERVICE', 'ts302-tenant', 'ts302-tenant', 'ts302-tenant', "
            "'SCREENING_SUBMIT', 'SCREENING_IDEMPOTENCY:ts302-legacy-target', "
            "'ts302-legacy-correlation', 'ALLOW', 'ALLOWED')",
            (BASE_TIME,),
        )
    migrate_up("20260806_0005")
    with admin_connect() as connection:
        assert scalar(connection, "SELECT version_num FROM alembic_version") == (
            "20260806_0005"
        )
        assert all(
            scalar(connection, "SELECT to_regclass(%s) IS NOT NULL", (table,)) is True
            for table in SCREENING_TABLES
        )
        assert (
            scalar(
                connection,
                "SELECT count(*) FROM authorization_audit_event "
                "WHERE event_id = 'ts302-legacy-auth'",
            )
            == 1
        )
    evidence(
        "legacy-0004-upgrade-0005",
        legacy_authorization_preserved=True,
        screening_objects=len(SCREENING_TABLES),
    )


def service_uow_probe() -> dict[str, object]:
    unit = PostgresScreeningSubmissionUnitOfWork(diagnostic_submission_connect)
    first = atomic_request(
        intake(),
        event_id="ts302-auth-apply",
        attempt_id="ts302-attempt-apply",
        intake_id="ts302-intake-apply",
        screening_id="ts302-screening-apply",
        outbox_id="ts302-outbox-apply",
        occurred_at=BASE_TIME + timedelta(seconds=1),
    )
    applied = unit.submit_atomic(first)
    assert applied.outcome is ScreeningAttemptOutcome.APPLIED
    assert unit.submit_atomic(first.reverify()) == applied
    stored = unit.read_result(first.bound.scope)
    assert stored == first.proposed_result

    replay_request = atomic_request(
        intake(subject="ts302-replay-actor", indent=2),
        event_id="ts302-auth-replay",
        attempt_id="ts302-attempt-replay",
        intake_id="ts302-discarded-intake-replay",
        screening_id="ts302-discarded-screening-replay",
        outbox_id="ts302-discarded-outbox-replay",
        occurred_at=BASE_TIME + timedelta(seconds=2),
    )
    replay = unit.submit_atomic(replay_request)
    assert replay.outcome is ScreeningAttemptOutcome.REPLAY
    assert replay.receipt == applied.receipt

    conflict_request = atomic_request(
        intake(
            action="QUOTE_RELEASE",
            subject="ts302-conflict-actor",
            correlation_id="ts302-conflict-correlation",
        ),
        event_id="ts302-auth-conflict",
        attempt_id="ts302-attempt-conflict",
        intake_id="ts302-discarded-intake-conflict",
        screening_id="ts302-discarded-screening-conflict",
        outbox_id="ts302-discarded-outbox-conflict",
        occurred_at=BASE_TIME + timedelta(seconds=3),
    )
    conflict = unit.submit_atomic(conflict_request)
    assert conflict.outcome is ScreeningAttemptOutcome.CONFLICT
    assert conflict.receipt == applied.receipt

    assert (
        unit.resolve_attempt(
            first.bound.scope,
            first.proposed_result.canonical_hash,
            first.attempt_id,
        )
        == applied
    )
    assert (
        unit.resolve_attempt(
            replay_request.bound.scope,
            replay_request.proposed_result.canonical_hash,
            replay_request.attempt_id,
        )
        == replay
    )
    assert (
        unit.resolve_attempt(
            conflict_request.bound.scope,
            conflict_request.proposed_result.canonical_hash,
            conflict_request.attempt_id,
        )
        == conflict
    )
    assert (
        unit.resolve_attempt(
            conflict_request.bound.scope,
            first.proposed_result.canonical_hash,
            conflict_request.attempt_id,
        )
        is None
    )

    with admin_connect() as connection:
        counts = {
            table: table_count(connection, table)
            for table in (*SCREENING_TABLES, "authorization_audit_event")
        }
        assert counts == {
            "screening_opaque_id_registry": 6,
            "screening_accepted_intake": 1,
            "screening_identity": 1,
            "screening_idempotency_ledger": 1,
            "screening_attempt_audit": 3,
            "screening_accepted_outbox": 1,
            "authorization_audit_event": 4,
        }
        attempt_shape = connection.execute(
            "SELECT sequence, outcome FROM screening_attempt_audit ORDER BY sequence"
        ).fetchall()
        assert attempt_shape == [(1, "APPLIED"), (2, "REPLAY"), (3, "CONFLICT")]
        discarded = cast(
            int,
            scalar(
                connection,
                "SELECT count(*) FROM screening_opaque_id_registry "
                "WHERE opaque_id LIKE %s",
                ("ts302-discarded-%",),
            ),
        )
        assert discarded == 0
    evidence(
        "real-authorization-and-uow",
        attempts=counts["screening_attempt_audit"],
        authorization_events=counts["authorization_audit_event"],
        business_graphs=counts["screening_idempotency_ledger"],
        dispositions=["APPLIED", "REPLAY", "CONFLICT"],
        exact_duplicate=True,
        exact_resolution=True,
    )
    return {
        "request": first,
        "scope": first.bound.scope,
        "result": first.proposed_result,
        "applied": applied,
    }


class _UnknownInfo:
    def __init__(self, owner: CommittedThenUnknownConnection) -> None:
        self._owner = owner

    @property
    def transaction_status(self) -> TransactionStatus:
        if self._owner.commit_ack_failed:
            return TransactionStatus.UNKNOWN
        return self._owner.connection.info.transaction_status


class CommittedThenUnknownConnection:
    def __init__(self, connection: Connection[Any]) -> None:
        self.connection = connection
        self.commit_ack_failed = False
        self.info = _UnknownInfo(self)

    @property
    def autocommit(self) -> bool:
        return self.connection.autocommit

    def execute(self, query: str, params: tuple[object, ...] | None = None) -> object:
        return self.connection.execute(query, params)

    def commit(self) -> None:
        self.connection.commit()
        self.commit_ack_failed = True
        raise psycopg.OperationalError("commit acknowledgement unavailable")

    def rollback(self) -> None:
        self.connection.rollback()

    def close(self) -> None:
        self.connection.close()


class KnownFailureConnection:
    def __init__(
        self,
        connection: Connection[Any],
        *,
        statement: bool,
    ) -> None:
        self.connection = connection
        self.statement = statement
        self.info = connection.info

    @property
    def autocommit(self) -> bool:
        return self.connection.autocommit

    def execute(self, query: str, params: tuple[object, ...] | None = None) -> object:
        if self.statement and query.startswith("INSERT INTO screening_accepted_intake"):
            raise psycopg.OperationalError("known statement failure")
        return self.connection.execute(query, params)

    def commit(self) -> None:
        if not self.statement:
            raise psycopg.OperationalError("known pre-commit failure")
        self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()

    def close(self) -> None:
        self.connection.close()


def submission_service(
    unit: PostgresScreeningSubmissionUnitOfWork,
    *,
    event_id: str,
    prefix: str,
    occurred_at: datetime,
) -> ScreeningSubmissionService:
    return ScreeningSubmissionService(
        authorization_service(event_id),
        unit,
        clock=lambda: occurred_at,
        attempt_id_factory=lambda: f"{prefix}-attempt",
        intake_id_factory=lambda: f"{prefix}-intake",
        screening_id_factory=lambda: f"{prefix}-screening",
        outbox_id_factory=lambda: f"{prefix}-outbox",
    )


def commit_outcome_probe() -> None:
    connections_opened = 0

    def unknown_then_fresh() -> Connection[Any]:
        nonlocal connections_opened
        connections_opened += 1
        connection = submission_connect()
        if connections_opened == 1:
            return cast(Connection[Any], CommittedThenUnknownConnection(connection))
        return connection

    unknown_unit = PostgresScreeningSubmissionUnitOfWork(unknown_then_fresh)
    unknown_service = submission_service(
        unknown_unit,
        event_id="ts302-auth-unknown",
        prefix="ts302-unknown",
        occurred_at=BASE_TIME + timedelta(seconds=10),
    )
    resolved = unknown_service.submit(intake(), "ts302-unknown-key")
    assert resolved.disposition is ScreeningSubmissionServiceDisposition.APPLIED
    assert connections_opened == 2

    for mode in ("statement", "commit"):
        prefix = f"ts302-known-{mode}"

        def known_factory(mode: str = mode) -> Connection[Any]:
            return cast(
                Connection[Any],
                KnownFailureConnection(
                    submission_connect(),
                    statement=mode == "statement",
                ),
            )

        unit = PostgresScreeningSubmissionUnitOfWork(known_factory)
        request = atomic_request(
            intake(correlation_id=f"{prefix}-correlation"),
            key=f"{prefix}-key",
            event_id=f"{prefix}-auth",
            attempt_id=f"{prefix}-attempt",
            intake_id=f"{prefix}-intake",
            screening_id=f"{prefix}-screening",
            outbox_id=f"{prefix}-outbox",
            occurred_at=BASE_TIME
            + timedelta(seconds=11 if mode == "statement" else 12),
        )
        try:
            unit.submit_atomic(request)
        except Exception as exc:
            assert str(exc) == "screening submission persistence failed"
        else:
            raise AssertionError("known persistence failure unexpectedly succeeded")
        with admin_connect() as connection:
            assert (
                scalar(
                    connection,
                    "SELECT count(*) FROM screening_opaque_id_registry "
                    "WHERE opaque_id LIKE %s",
                    (f"{prefix}%",),
                )
                == 0
            )
            assert (
                scalar(
                    connection,
                    "SELECT count(*) FROM screening_idempotency_ledger "
                    "WHERE key_digest = %s",
                    (request.bound.scope.key_digest,),
                )
                == 0
            )
    evidence(
        "commit-outcome-classification",
        committed_unknown_resolved=True,
        fresh_resolution_connection=True,
        known_commit_rolled_back=True,
        known_statement_rolled_back=True,
    )


def concurrency_probe() -> None:
    barrier = Barrier(2)

    def synchronized_factory() -> Connection[Any]:
        connection = submission_connect()
        try:
            barrier.wait(timeout=15)
        except Exception:
            connection.close()
            raise
        return connection

    race_item = intake(correlation_id="ts302-race-correlation")
    first = atomic_request(
        race_item,
        key="ts302-race-key",
        event_id="ts302-race-auth-1",
        attempt_id="ts302-race-attempt-1",
        intake_id="ts302-race-intake-1",
        screening_id="ts302-race-screening-1",
        outbox_id="ts302-race-outbox-1",
        occurred_at=BASE_TIME + timedelta(seconds=30),
    )
    second = atomic_request(
        race_item,
        key="ts302-race-key",
        event_id="ts302-race-auth-2",
        attempt_id="ts302-race-attempt-2",
        intake_id="ts302-race-intake-2",
        screening_id="ts302-race-screening-2",
        outbox_id="ts302-race-outbox-2",
        occurred_at=BASE_TIME + timedelta(seconds=31),
    )
    units = (
        PostgresScreeningSubmissionUnitOfWork(synchronized_factory),
        PostgresScreeningSubmissionUnitOfWork(synchronized_factory),
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(units[0].submit_atomic, first),
            executor.submit(units[1].submit_atomic, second),
        )
        results = tuple(future.result(timeout=30) for future in futures)
    assert {result.outcome for result in results} == {
        ScreeningAttemptOutcome.APPLIED,
        ScreeningAttemptOutcome.REPLAY,
    }
    assert results[0].receipt == results[1].receipt

    scope = first.bound.scope
    with admin_connect() as connection:
        scope_params = (
            scope.tenant_id,
            scope.client_id,
            scope.operation,
            scope.key_digest,
        )
        assert (
            scalar(
                connection,
                "SELECT count(*) FROM screening_idempotency_ledger WHERE "
                "tenant_id = %s AND client_id = %s AND operation = %s "
                "AND key_digest = %s",
                scope_params,
            )
            == 1
        )
        assert (
            scalar(
                connection,
                "SELECT count(*) FROM screening_attempt_audit WHERE "
                "tenant_id = %s AND client_id = %s AND operation = %s "
                "AND key_digest = %s",
                scope_params,
            )
            == 2
        )
        allocated = connection.execute(
            "SELECT object_kind, count(*) FROM screening_opaque_id_registry "
            "WHERE opaque_id LIKE %s GROUP BY object_kind ORDER BY object_kind",
            ("ts302-race-%",),
        ).fetchall()
        assert allocated == [
            ("ATTEMPT", 2),
            ("INTAKE", 1),
            ("OUTBOX", 1),
            ("SCREENING", 1),
        ]
    evidence(
        "two-connection-two-instance-race",
        attempts=2,
        business_graphs=1,
        dispositions=["APPLIED", "REPLAY"],
        registry_ids=5,
    )


def insert_attempt_row(
    connection: Connection[Any],
    request: ScreeningSubmissionAtomicWrite,
    *,
    sequence: int,
    outcome: str,
    authorization_event_id: str | None = None,
) -> None:
    bound = request.bound
    accepted = bound.accepted_intake_reference("ts302-forged-reference")
    connection.execute(
        "INSERT INTO screening_opaque_id_registry (opaque_id, object_kind) "
        "VALUES (%s, 'ATTEMPT')",
        (request.attempt_id,),
    )
    connection.execute(
        "INSERT INTO screening_attempt_audit ("
        "tenant_id, client_id, operation, key_digest, scope_fingerprint, "
        "sequence, attempt_id, id_kind, outcome, occurred_at, actor_type, "
        "actor_subject, correlation_id, authorization_event_id, byte_hash, "
        "canonical_hash) VALUES (" + ", ".join(["%s"] * 16) + ")",
        (
            bound.scope.tenant_id,
            bound.scope.client_id,
            bound.scope.operation,
            bound.scope.key_digest,
            bound.scope.fingerprint(),
            sequence,
            request.attempt_id,
            "ATTEMPT",
            outcome,
            request.occurred_at,
            accepted.actor_type.value,
            accepted.actor_subject,
            accepted.correlation_id,
            authorization_event_id or bound._authorization_event_id,
            accepted.byte_hash,
            accepted.canonical_hash,
        ),
    )


def insert_graph_without_attempt(
    connection: Connection[Any], request: ScreeningSubmissionAtomicWrite
) -> None:
    result = request.proposed_result
    accepted = result.intake
    screening = result.screening
    outbox = result.outbox
    canonical = request.bound._verified_intake_for_persistence()
    for opaque_id, kind in (
        (accepted.intake_id, "INTAKE"),
        (screening.screening_id, "SCREENING"),
        (outbox.event_id, "OUTBOX"),
    ):
        connection.execute(
            "INSERT INTO screening_opaque_id_registry "
            "(opaque_id, object_kind) VALUES (%s, %s)",
            (opaque_id, kind),
        )
    connection.execute(
        "INSERT INTO screening_accepted_intake ("
        "intake_id, id_kind, tenant_id, client_id, actor_subject, actor_type, "
        "correlation_id, scope_fingerprint, schema_version, media_type, raw_body, "
        "byte_length, byte_hash, canonical_hash, received_at, private_context) "
        "VALUES (" + ", ".join(["%s"] * 16) + ")",
        (
            accepted.intake_id,
            "INTAKE",
            accepted.tenant_id,
            accepted.client_id,
            accepted.actor_subject,
            accepted.actor_type.value,
            accepted.correlation_id,
            accepted.scope_fingerprint,
            accepted.schema_version,
            accepted.media_type,
            canonical._raw_bytes,
            accepted.byte_length,
            accepted.byte_hash,
            accepted.canonical_hash,
            accepted.received_at,
            encode_request_context(canonical.context),
        ),
    )
    connection.execute(
        "INSERT INTO screening_identity ("
        "screening_id, id_kind, intake_id, tenant_id, client_id, "
        "scope_fingerprint, canonical_hash, created_at) VALUES ("
        + ", ".join(["%s"] * 8)
        + ")",
        (
            screening.screening_id,
            "SCREENING",
            accepted.intake_id,
            accepted.tenant_id,
            accepted.client_id,
            accepted.scope_fingerprint,
            accepted.canonical_hash,
            screening.created_at,
        ),
    )
    connection.execute(
        "INSERT INTO screening_accepted_outbox ("
        "event_id, id_kind, event_type, schema_version, occurred_at, intake_id, "
        "screening_id) VALUES (" + ", ".join(["%s"] * 7) + ")",
        (
            outbox.event_id,
            "OUTBOX",
            outbox.event_type.value,
            outbox.schema_version,
            outbox.occurred_at,
            outbox.intake_id,
            outbox.screening_id,
        ),
    )
    scope = result.scope
    connection.execute(
        "INSERT INTO screening_idempotency_ledger ("
        "tenant_id, client_id, operation, key_digest, scope_fingerprint, "
        "canonical_hash, intake_id, screening_id, outbox_id, committed_at) "
        "VALUES (" + ", ".join(["%s"] * 10) + ")",
        (
            scope.tenant_id,
            scope.client_id,
            scope.operation,
            scope.key_digest,
            scope.fingerprint(),
            result.canonical_hash,
            accepted.intake_id,
            screening.screening_id,
            outbox.event_id,
            result.committed_at,
        ),
    )


def constraint_probe(context: dict[str, object]) -> None:
    base_request = cast(ScreeningSubmissionAtomicWrite, context["request"])

    expect_transaction_error(
        lambda connection: connection.execute(
            "INSERT INTO screening_opaque_id_registry "
            "(opaque_id, object_kind) VALUES "
            "('ts302-forged-orphan-registry', 'INTAKE')"
        ),
        sqlstate="23514",
    )
    expect_transaction_error(
        lambda connection: connection.execute(
            "INSERT INTO screening_opaque_id_registry "
            "(opaque_id, object_kind) VALUES (%s, 'OUTBOX')",
            (base_request.proposed_result.intake.intake_id,),
        ),
        sqlstate="23505",
    )
    expect_transaction_error(
        lambda connection: connection.execute(
            "INSERT INTO screening_idempotency_ledger ("
            "tenant_id, client_id, operation, key_digest, scope_fingerprint, "
            "canonical_hash, intake_id, screening_id, outbox_id, committed_at) "
            "VALUES ('ts302-forged-tenant', 'ts302-forged-client', "
            "'SCREENING_SUBMIT', %s, %s, %s, 'ts302-forged-intake', "
            "'ts302-forged-screening', 'ts302-forged-outbox', %s)",
            (
                "sha256:" + "a" * 64,
                "sha256:" + "b" * 64,
                "sha256:" + "c" * 64,
                BASE_TIME,
            ),
        ),
        sqlstate="23514",
    )
    expect_transaction_error(
        lambda connection: connection.execute(
            "INSERT INTO screening_idempotency_ledger "
            "SELECT * FROM screening_idempotency_ledger WHERE intake_id = %s",
            (base_request.proposed_result.intake.intake_id,),
        ),
        sqlstate="23505",
    )

    missing_applied = atomic_request(
        intake(correlation_id="ts302-deferred-correlation"),
        key="ts302-deferred-key",
        event_id="ts302-deferred-auth",
        attempt_id="ts302-deferred-attempt",
        intake_id="ts302-deferred-intake",
        screening_id="ts302-deferred-screening",
        outbox_id="ts302-deferred-outbox",
        occurred_at=BASE_TIME + timedelta(seconds=40),
    )
    expect_transaction_error(
        lambda connection: insert_graph_without_attempt(connection, missing_applied),
        sqlstate="23514",
    )

    gap = atomic_request(
        intake(),
        event_id="ts302-gap-auth",
        attempt_id="ts302-gap-attempt",
        intake_id="ts302-gap-intake",
        screening_id="ts302-gap-screening",
        outbox_id="ts302-gap-outbox",
        occurred_at=BASE_TIME + timedelta(seconds=41),
    )
    expect_transaction_error(
        lambda connection: insert_attempt_row(
            connection, gap, sequence=5, outcome="REPLAY"
        ),
        sqlstate="23514",
    )
    sole_applied = atomic_request(
        intake(),
        event_id="ts302-sole-applied-auth",
        attempt_id="ts302-sole-applied-attempt",
        intake_id="ts302-sole-applied-intake",
        screening_id="ts302-sole-applied-screening",
        outbox_id="ts302-sole-applied-outbox",
        occurred_at=BASE_TIME + timedelta(seconds=42),
    )
    expect_transaction_error(
        lambda connection: insert_attempt_row(
            connection, sole_applied, sequence=4, outcome="APPLIED"
        ),
        sqlstate="23514",
    )

    mismatch = atomic_request(
        intake(),
        event_id="ts302-mismatch-source-auth",
        attempt_id="ts302-mismatch-attempt",
        intake_id="ts302-mismatch-intake",
        screening_id="ts302-mismatch-screening",
        outbox_id="ts302-mismatch-outbox",
        occurred_at=BASE_TIME + timedelta(seconds=43),
    )
    mismatch_auth_id = "ts302-mismatch-deny-auth"
    mismatch_intake = mismatch.bound.accepted_intake_reference(
        "ts302-mismatch-reference"
    )
    with admin_connect() as connection:
        connection.execute(
            "INSERT INTO authorization_audit_event ("
            "event_id, occurred_at, actor_subject, client_id, actor_type, "
            "actor_tenant_id, request_tenant_id, target_tenant_id, operation, "
            "target_ref, correlation_id, outcome, reason) VALUES ("
            + ", ".join(["%s"] * 13)
            + ")",
            (
                mismatch_auth_id,
                mismatch.occurred_at,
                mismatch_intake.actor_subject,
                mismatch.bound.scope.client_id,
                mismatch_intake.actor_type.value,
                mismatch.bound.scope.tenant_id,
                mismatch.bound.scope.tenant_id,
                mismatch.bound.scope.tenant_id,
                mismatch.bound.scope.operation,
                "SCREENING_IDEMPOTENCY:idem-"
                + mismatch.bound.scope.fingerprint().removeprefix("sha256:"),
                mismatch_intake.correlation_id,
                "DENY",
                "POLICY_MISSING",
            ),
        )
    expect_transaction_error(
        lambda connection: insert_attempt_row(
            connection,
            mismatch,
            sequence=4,
            outcome="REPLAY",
            authorization_event_id=mismatch_auth_id,
        ),
        sqlstate="23514",
    )
    expect_transaction_error(
        lambda connection: connection.execute(
            "UPDATE screening_accepted_intake SET client_id = client_id "
            "WHERE intake_id = %s",
            (base_request.proposed_result.intake.intake_id,),
        ),
        sqlstate="55000",
    )
    evidence(
        "database-constraint-forgery",
        authorization_allow_fields="ENFORCED",
        deferred_graph="REJECTED",
        deferred_registry="REJECTED",
        global_ids="ENFORCED",
        immutable="ENFORCED",
        scope_fingerprint="ENFORCED",
        sequence_and_sole_applied="ENFORCED",
        uniqueness="ENFORCED",
    )


def attempt_capacity_probe(context: dict[str, object]) -> None:
    base_request = cast(ScreeningSubmissionAtomicWrite, context["request"])
    scope = base_request.bound.scope
    result = base_request.proposed_result
    attempt_time = BASE_TIME + timedelta(seconds=50)
    expected_target = "SCREENING_IDEMPOTENCY:idem-" + scope.fingerprint().removeprefix(
        "sha256:"
    )
    with admin_connect() as connection:
        connection.execute(
            "ALTER TABLE screening_attempt_audit "
            "DISABLE TRIGGER trg_screening_attempt_validate"
        )
    try:
        connection = submission_connect()
        try:
            connection.execute(
                "INSERT INTO authorization_audit_event ("
                "event_id, occurred_at, actor_subject, client_id, actor_type, "
                "actor_tenant_id, request_tenant_id, target_tenant_id, operation, "
                "target_ref, correlation_id, outcome, reason) "
                "SELECT 'ts302-cap-auth-' || series, %s, 'ts302-cap-actor', %s, "
                "'SERVICE', %s, %s, %s, %s, %s, 'ts302-cap-correlation', "
                "'ALLOW', 'ALLOWED' FROM generate_series(4, %s) AS series",
                (
                    attempt_time,
                    scope.client_id,
                    scope.tenant_id,
                    scope.tenant_id,
                    scope.tenant_id,
                    scope.operation,
                    expected_target,
                    MAX_ATTEMPTS,
                ),
            )
            connection.execute(
                "INSERT INTO screening_opaque_id_registry "
                "(opaque_id, object_kind) SELECT "
                "'ts302-cap-attempt-' || series, 'ATTEMPT' "
                "FROM generate_series(4, %s) AS series",
                (MAX_ATTEMPTS,),
            )
            connection.execute(
                "INSERT INTO screening_attempt_audit ("
                "tenant_id, client_id, operation, key_digest, scope_fingerprint, "
                "sequence, attempt_id, id_kind, outcome, occurred_at, actor_type, "
                "actor_subject, correlation_id, authorization_event_id, byte_hash, "
                "canonical_hash) SELECT %s, %s, %s, %s, %s, series, "
                "'ts302-cap-attempt-' || series, 'ATTEMPT', 'REPLAY', %s, "
                "'SERVICE', 'ts302-cap-actor', 'ts302-cap-correlation', "
                "'ts302-cap-auth-' || series, %s, %s "
                "FROM generate_series(4, %s) AS series",
                (
                    scope.tenant_id,
                    scope.client_id,
                    scope.operation,
                    scope.key_digest,
                    scope.fingerprint(),
                    attempt_time,
                    result.intake.byte_hash,
                    result.canonical_hash,
                    MAX_ATTEMPTS,
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
    finally:
        with admin_connect() as connection:
            connection.execute(
                "ALTER TABLE screening_attempt_audit "
                "ENABLE TRIGGER trg_screening_attempt_validate"
            )

    unit = PostgresScreeningSubmissionUnitOfWork(submission_connect)
    assert unit.read_result(scope) == result
    cap_retry = atomic_request(
        intake(),
        event_id="ts302-cap-retry-auth",
        attempt_id="ts302-cap-retry-attempt",
        intake_id="ts302-cap-retry-intake",
        screening_id="ts302-cap-retry-screening",
        outbox_id="ts302-cap-retry-outbox",
        occurred_at=BASE_TIME + timedelta(seconds=51),
    )
    try:
        unit.submit_atomic(cap_retry)
    except Exception as exc:
        assert str(exc) == "screening submission persistence failed"
    else:
        raise AssertionError("adapter accepted attempt above bounded history")
    with admin_connect() as connection:
        assert (
            scalar(
                connection,
                "SELECT count(*) FROM screening_attempt_audit WHERE "
                "tenant_id = %s AND client_id = %s AND operation = %s "
                "AND key_digest = %s",
                (
                    scope.tenant_id,
                    scope.client_id,
                    scope.operation,
                    scope.key_digest,
                ),
            )
            == MAX_ATTEMPTS
        )
        assert (
            scalar(
                connection,
                "SELECT count(*) FROM screening_opaque_id_registry "
                "WHERE opaque_id = 'ts302-cap-retry-attempt'",
            )
            == 0
        )
        connection.execute(
            "INSERT INTO authorization_audit_event ("
            "event_id, occurred_at, actor_subject, client_id, actor_type, "
            "actor_tenant_id, request_tenant_id, target_tenant_id, operation, "
            "target_ref, correlation_id, outcome, reason) VALUES ("
            "'ts302-cap-auth-4097', %s, 'ts302-cap-actor', %s, 'SERVICE', "
            "%s, %s, %s, %s, %s, 'ts302-cap-correlation', 'ALLOW', 'ALLOWED')",
            (
                attempt_time,
                scope.client_id,
                scope.tenant_id,
                scope.tenant_id,
                scope.tenant_id,
                scope.operation,
                expected_target,
            ),
        )

    def insert_4097(connection: Connection[Any]) -> None:
        connection.execute(
            "INSERT INTO screening_opaque_id_registry "
            "(opaque_id, object_kind) VALUES "
            "('ts302-cap-attempt-4097', 'ATTEMPT')"
        )
        connection.execute(
            "INSERT INTO screening_attempt_audit ("
            "tenant_id, client_id, operation, key_digest, scope_fingerprint, "
            "sequence, attempt_id, id_kind, outcome, occurred_at, actor_type, "
            "actor_subject, correlation_id, authorization_event_id, byte_hash, "
            "canonical_hash) VALUES (" + ", ".join(["%s"] * 16) + ")",
            (
                scope.tenant_id,
                scope.client_id,
                scope.operation,
                scope.key_digest,
                scope.fingerprint(),
                MAX_ATTEMPTS + 1,
                "ts302-cap-attempt-4097",
                "ATTEMPT",
                "REPLAY",
                attempt_time,
                "SERVICE",
                "ts302-cap-actor",
                "ts302-cap-correlation",
                "ts302-cap-auth-4097",
                result.intake.byte_hash,
                result.canonical_hash,
            ),
        )

    expect_transaction_error(insert_4097, sqlstate="54000")
    with admin_connect() as connection:
        assert (
            scalar(
                connection,
                "SELECT count(*) FROM screening_opaque_id_registry "
                "WHERE opaque_id = 'ts302-cap-attempt-4097'",
            )
            == 0
        )
        assert (
            scalar(
                connection,
                "SELECT count(*) FROM screening_attempt_audit "
                "WHERE attempt_id = 'ts302-cap-attempt-4097'",
            )
            == 0
        )
    evidence(
        "attempt-capacity",
        adapter_cap=MAX_ATTEMPTS,
        committed_attempts=MAX_ATTEMPTS,
        next_attempt_sqlstate="54000",
        partial_registry_rows=0,
    )


def expect_adapter_read_failure(
    unit: PostgresScreeningSubmissionUnitOfWork,
    request: ScreeningSubmissionAtomicWrite,
) -> None:
    try:
        unit.read_result(request.bound.scope)
    except Exception as exc:
        assert str(exc) == "screening submission persistence failed"
    else:
        raise AssertionError("adapter trusted corrupted screening state")


def corruption_repair_probe(context: dict[str, object]) -> None:
    request = cast(ScreeningSubmissionAtomicWrite, context["request"])
    result = request.proposed_result
    unit = PostgresScreeningSubmissionUnitOfWork(submission_connect)
    trigger_inventory_query = (
        "SELECT tgrelid::regclass::text, count(*), "
        "count(*) FILTER (WHERE tgenabled <> 'O') FROM pg_trigger "
        "WHERE tgrelid IN ('screening_attempt_audit'::regclass, "
        "'screening_opaque_id_registry'::regclass, "
        "'screening_accepted_outbox'::regclass, "
        "'authorization_audit_event'::regclass) "
        "GROUP BY tgrelid ORDER BY tgrelid::regclass::text"
    )
    with admin_connect() as connection:
        trigger_inventory_before = connection.execute(
            trigger_inventory_query
        ).fetchall()
        assert len(trigger_inventory_before) == 4
        assert all(disabled == 0 for _, _, disabled in trigger_inventory_before)

    with admin_connect() as connection:
        registry_row = connection.execute(
            "SELECT opaque_id, object_kind FROM screening_opaque_id_registry "
            "WHERE opaque_id = 'ts302-cap-attempt-4'"
        ).fetchone()
        assert registry_row == ("ts302-cap-attempt-4", "ATTEMPT")
        connection.execute(
            "ALTER TABLE screening_opaque_id_registry DISABLE TRIGGER ALL"
        )
        connection.execute(
            "DELETE FROM screening_opaque_id_registry "
            "WHERE opaque_id = 'ts302-cap-attempt-4'"
        )
        connection.execute(
            "ALTER TABLE screening_opaque_id_registry ENABLE TRIGGER ALL"
        )
    expect_adapter_read_failure(unit, request)
    with admin_connect() as connection:
        connection.execute(
            "INSERT INTO screening_opaque_id_registry (opaque_id, object_kind) "
            "VALUES (%s, %s)",
            cast(tuple[object, ...], registry_row),
        )
    assert unit.read_result(request.bound.scope) == result

    with admin_connect() as connection:
        authorization_row = connection.execute(
            "SELECT event_id, occurred_at, actor_subject, client_id, actor_type, "
            "actor_tenant_id, request_tenant_id, target_tenant_id, operation, "
            "target_ref, correlation_id, outcome, reason "
            "FROM authorization_audit_event WHERE event_id = 'ts302-cap-auth-4'"
        ).fetchone()
        assert authorization_row is not None and len(authorization_row) == 13
        connection.execute("ALTER TABLE authorization_audit_event DISABLE TRIGGER ALL")
        connection.execute(
            "DELETE FROM authorization_audit_event WHERE event_id = 'ts302-cap-auth-4'"
        )
        connection.execute("ALTER TABLE authorization_audit_event ENABLE TRIGGER ALL")
    expect_adapter_read_failure(unit, request)
    with admin_connect() as connection:
        connection.execute(
            "INSERT INTO authorization_audit_event ("
            "event_id, occurred_at, actor_subject, client_id, actor_type, "
            "actor_tenant_id, request_tenant_id, target_tenant_id, operation, "
            "target_ref, correlation_id, outcome, reason) VALUES ("
            + ", ".join(["%s"] * 13)
            + ")",
            authorization_row,
        )
    assert unit.read_result(request.bound.scope) == result

    with admin_connect() as connection:
        outbox_row = connection.execute(
            "SELECT event_id, id_kind, event_type, schema_version, occurred_at, "
            "intake_id, screening_id FROM screening_accepted_outbox "
            "WHERE event_id = %s",
            (result.outbox.event_id,),
        ).fetchone()
        assert outbox_row is not None and len(outbox_row) == 7
        connection.execute("ALTER TABLE screening_accepted_outbox DISABLE TRIGGER ALL")
        connection.execute(
            "DELETE FROM screening_accepted_outbox WHERE event_id = %s",
            (result.outbox.event_id,),
        )
        connection.execute("ALTER TABLE screening_accepted_outbox ENABLE TRIGGER ALL")
    expect_adapter_read_failure(unit, request)
    with admin_connect() as connection:
        connection.execute(
            "INSERT INTO screening_accepted_outbox ("
            "event_id, id_kind, event_type, schema_version, occurred_at, "
            "intake_id, screening_id) VALUES (" + ", ".join(["%s"] * 7) + ")",
            outbox_row,
        )
    assert unit.read_result(request.bound.scope) == result

    scope = request.bound.scope
    attempt_time = BASE_TIME + timedelta(seconds=50)
    with admin_connect() as connection:
        connection.execute(
            "ALTER TABLE screening_attempt_audit "
            "DISABLE TRIGGER trg_screening_attempt_validate"
        )
        connection.execute(
            "ALTER TABLE screening_attempt_audit "
            "DROP CONSTRAINT ck_screening_attempt_sequence"
        )
    connection = submission_connect()
    try:
        connection.execute(
            "INSERT INTO screening_opaque_id_registry "
            "(opaque_id, object_kind) VALUES "
            "('ts302-cap-attempt-4097', 'ATTEMPT')"
        )
        connection.execute(
            "INSERT INTO screening_attempt_audit ("
            "tenant_id, client_id, operation, key_digest, scope_fingerprint, "
            "sequence, attempt_id, id_kind, outcome, occurred_at, actor_type, "
            "actor_subject, correlation_id, authorization_event_id, byte_hash, "
            "canonical_hash) VALUES (" + ", ".join(["%s"] * 16) + ")",
            (
                scope.tenant_id,
                scope.client_id,
                scope.operation,
                scope.key_digest,
                scope.fingerprint(),
                MAX_ATTEMPTS + 1,
                "ts302-cap-attempt-4097",
                "ATTEMPT",
                "REPLAY",
                attempt_time,
                "SERVICE",
                "ts302-cap-actor",
                "ts302-cap-correlation",
                "ts302-cap-auth-4097",
                result.intake.byte_hash,
                result.canonical_hash,
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
        with admin_connect() as admin:
            admin.execute(
                "ALTER TABLE screening_attempt_audit "
                "ENABLE TRIGGER trg_screening_attempt_validate"
            )
    expect_adapter_read_failure(unit, request)
    with admin_connect() as connection:
        connection.execute(
            "ALTER TABLE screening_attempt_audit "
            "DISABLE TRIGGER trg_screening_attempt_immutable"
        )
        connection.execute(
            "DELETE FROM screening_attempt_audit "
            "WHERE attempt_id = 'ts302-cap-attempt-4097'"
        )
        connection.execute(
            "ALTER TABLE screening_attempt_audit "
            "ENABLE TRIGGER trg_screening_attempt_immutable"
        )
        connection.execute(
            "ALTER TABLE screening_opaque_id_registry "
            "DISABLE TRIGGER trg_screening_registry_immutable"
        )
        connection.execute(
            "DELETE FROM screening_opaque_id_registry "
            "WHERE opaque_id = 'ts302-cap-attempt-4097'"
        )
        connection.execute(
            "ALTER TABLE screening_opaque_id_registry "
            "ENABLE TRIGGER trg_screening_registry_immutable"
        )
        connection.execute(
            "ALTER TABLE screening_attempt_audit ADD CONSTRAINT "
            "ck_screening_attempt_sequence CHECK (sequence BETWEEN 1 AND 4096)"
        )
        assert (
            scalar(
                connection,
                "SELECT convalidated FROM pg_constraint WHERE "
                "conrelid = 'screening_attempt_audit'::regclass AND "
                "conname = 'ck_screening_attempt_sequence'",
            )
            is True
        )
        trigger_inventory_after = connection.execute(trigger_inventory_query).fetchall()
        assert trigger_inventory_after == trigger_inventory_before
        assert all(disabled == 0 for _, _, disabled in trigger_inventory_after)
    assert unit.read_result(request.bound.scope) == result
    evidence(
        "corruption-and-controlled-repair",
        graph="FAIL_CLOSED_REPAIRED",
        missing_authorization="FAIL_CLOSED_REPAIRED",
        missing_registry="FAIL_CLOSED_REPAIRED",
        overflow="FAIL_CLOSED_REPAIRED",
        restored_constraints_validated=True,
        restored_triggers_enabled=True,
    )


def downgrade_reupgrade_probe() -> None:
    migrate_down("20260806_0004")
    screening_functions = (
        "tradesieve_screening_scope_fingerprint(text,text,text,text)",
        "tradesieve_validate_screening_attempt()",
        "tradesieve_require_screening_complete_graph()",
        "tradesieve_require_screening_registry_child()",
    )
    with admin_connect() as connection:
        assert scalar(connection, "SELECT version_num FROM alembic_version") == (
            "20260806_0004"
        )
        assert all(
            scalar(connection, "SELECT to_regclass(%s) IS NULL", (table,)) is True
            for table in SCREENING_TABLES
        )
        assert all(
            scalar(connection, "SELECT to_regprocedure(%s) IS NULL", (function,))
            is True
            for function in screening_functions
        )
        assert (
            scalar(
                connection,
                "SELECT to_regclass('authorization_audit_event') IS NOT NULL",
            )
            is True
        )
        assert (
            scalar(
                connection,
                "SELECT count(*) FROM authorization_audit_event "
                "WHERE event_id = 'ts302-legacy-auth'",
            )
            == 1
        )
    migrate_up("head")
    with admin_connect() as connection:
        assert scalar(connection, "SELECT version_num FROM alembic_version") == (
            "20260806_0005"
        )
        assert all(
            scalar(connection, "SELECT to_regclass(%s) IS NOT NULL", (table,)) is True
            for table in SCREENING_TABLES
        )
        assert all(table_count(connection, table) == 0 for table in SCREENING_TABLES)
        assert all(
            scalar(
                connection,
                "SELECT to_regprocedure(%s) IS NOT NULL",
                (function,),
            )
            is True
            for function in screening_functions
        )
        assert (
            scalar(
                connection,
                "SELECT count(*) FROM authorization_audit_event "
                "WHERE event_id = 'ts302-legacy-auth'",
            )
            == 1
        )
    evidence(
        "downgrade-0005-to-0004-and-reupgrade-head",
        older_authorization_preserved=True,
        reupgraded_tables_empty=True,
        screening_functions=4,
        screening_tables=6,
    )


def main() -> None:
    legacy_upgrade_probe()
    context = service_uow_probe()
    commit_outcome_probe()
    concurrency_probe()
    constraint_probe(context)
    attempt_capacity_probe(context)
    corruption_repair_probe(context)
    downgrade_reupgrade_probe()
    evidence("ts302-c3-postgresql-18.4-acceptance", phases=8)


if __name__ == "__main__":
    main()
