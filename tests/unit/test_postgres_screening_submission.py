"""Exact PostgreSQL screening-submission UoW with deterministic fake connections."""

from __future__ import annotations

import copy
import json
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import psycopg
import pytest
from psycopg import Connection
from psycopg.pq import TransactionStatus

from tradesieve.adapters import postgres_screening_submission as postgres_submission
from tradesieve.adapters.postgres_screening_submission import (
    MAX_ATTEMPT_QUERY,
    MAX_ATTEMPTS,
    PostgresScreeningSubmissionUnitOfWork,
)
from tradesieve.application.auth import (
    ActorContext,
    ActorType,
    AuthorizationAuditEvent,
    AuthorizationService,
    AuthorizedRequest,
    Operation,
    RequestContext,
    ResolvedTargetFacts,
    Role,
    Scope,
)
from tradesieve.application.screening_intake import CanonicalScreeningIntake
from tradesieve.application.screening_submission import (
    ScreeningSubmissionAtomicWrite,
    ScreeningSubmissionCommitOutcomeUnknown,
    ScreeningSubmissionPersistenceFailure,
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
    ScreeningIdempotencyScope,
    ScreeningIdentity,
    ScreeningOutboxEventType,
)

NOW = datetime(2026, 8, 7, 10, 0, tzinfo=UTC)
COMMITTED_AT = NOW + timedelta(seconds=1)
PRIVATE_KEY = "private-postgres-idempotency-key"  # pragma: allowlist secret
PRIVATE_SENTINEL = "PRIVATE-POSTGRES-SCREENING-SENTINEL"

type ScopeKey = tuple[str, str, str, str]
type Row = tuple[Any, ...]


class StringSubclass(str):
    pass


class IntegerSubclass(int):
    pass


class FakeCursor:
    def __init__(self, rows: object = None, *, rowcount: object = -1) -> None:
        self.rows = [] if rows is None else rows
        self.rowcount = rowcount

    def fetchall(self) -> object:
        return self.rows


class FetchlessCursor:
    pass


@dataclass
class FakeDatabase:
    registry: dict[str, str]
    intakes: dict[str, Row]
    screenings: dict[str, Row]
    ledgers: dict[ScopeKey, Row]
    attempts: list[Row]
    outbox: dict[str, Row]
    authorization: dict[str, Row]

    @classmethod
    def create(cls) -> FakeDatabase:
        return cls({}, {}, {}, {}, [], {}, {})

    def snapshot(self) -> tuple[Any, ...]:
        return copy.deepcopy(
            (
                self.registry,
                self.intakes,
                self.screenings,
                self.ledgers,
                self.attempts,
                self.outbox,
                self.authorization,
            )
        )

    def restore(self, snapshot: tuple[Any, ...]) -> None:
        (
            self.registry,
            self.intakes,
            self.screenings,
            self.ledgers,
            self.attempts,
            self.outbox,
            self.authorization,
        ) = copy.deepcopy(snapshot)

    def graph_row(self, scope: ScopeKey) -> Row | None:
        ledger = self.ledgers.get(scope)
        if ledger is None:
            return None
        intake = self.intakes.get(cast(str, ledger[6]))
        screening = self.screenings.get(cast(str, ledger[7]))
        outbox = self.outbox.get(cast(str, ledger[8]))
        intake_values = intake if intake is not None else (None,) * 16
        screening_values = screening if screening is not None else (None,) * 8
        outbox_values = outbox if outbox is not None else (None,) * 7
        return (
            *intake_values,
            self.registry.get(cast(str, ledger[6])),
            *screening_values,
            self.registry.get(cast(str, ledger[7])),
            *outbox_values,
            self.registry.get(cast(str, ledger[8])),
        )

    def attempt_rows(self, scope: ScopeKey) -> list[Row]:
        rows: list[Row] = []
        for attempt in sorted(
            (item for item in self.attempts if item[:4] == scope),
            key=lambda item: cast(int, item[5]),
        ):
            authorization = self.authorization.get(cast(str, attempt[13]))
            rows.append(
                (
                    attempt[5],
                    attempt[6],
                    attempt[7],
                    attempt[8],
                    attempt[9],
                    attempt[10],
                    attempt[11],
                    attempt[12],
                    attempt[13],
                    attempt[14],
                    attempt[15],
                    attempt[4],
                    self.registry.get(cast(str, attempt[6])),
                    *(authorization if authorization is not None else (None,) * 13),
                )
            )
        return rows


class FakeInfo:
    def __init__(self, status: object = TransactionStatus.IDLE) -> None:
        self.status = status
        self.fail = False

    @property
    def transaction_status(self) -> object:
        if self.fail:
            raise RuntimeError(PRIVATE_SENTINEL)
        return self.status


class FakeConnection:
    def __init__(self, database: FakeDatabase) -> None:
        self.database = database
        self.info = FakeInfo()
        self._autocommit: object = False
        self.autocommit_fail = False
        self.executed: list[tuple[str, object | None]] = []
        self.commits = 0
        self.rollbacks = 0
        self.close_calls = 0
        self.fail_once_query: str | None = None
        self.statement_failure_status: object = TransactionStatus.INERROR
        self.wrong_rowcount_query: str | None = None
        self.wrong_rowcount: object = 0
        self.row_override_query: str | None = None
        self.row_override: object = None
        self.row_override_occurrence: int | None = None
        self.row_override_rules: list[tuple[str, int | None, object]] = []
        self.query_occurrences: dict[str, int] = {}
        self.fetchless_query: str | None = None
        self.commit_failure = False
        self.commit_unknown = False
        self.commit_status_override: object | None = None
        self.status_failure_after_commit = False
        self.rollback_failure = False
        self.close_failure = False
        self.before: tuple[Any, ...] | None = None

    @property
    def autocommit(self) -> object:
        if self.autocommit_fail:
            raise RuntimeError(PRIVATE_SENTINEL)
        return self._autocommit

    def _begin(self) -> None:
        if self.info.status is TransactionStatus.IDLE:
            self.before = self.database.snapshot()
            self.info.status = TransactionStatus.INTRANS

    def _cursor(
        self, query: str, rows: object = None, *, rowcount: object = -1
    ) -> FakeCursor:
        if self.fetchless_query is not None and self.fetchless_query in query:
            return cast(FakeCursor, FetchlessCursor())
        if self.row_override_query is not None and self.row_override_query in query:
            occurrence = self.query_occurrences.get(self.row_override_query, 0) + 1
            self.query_occurrences[self.row_override_query] = occurrence
            if (
                self.row_override_occurrence is None
                or occurrence == self.row_override_occurrence
            ):
                rows = self.row_override
        for needle, expected_occurrence, replacement in self.row_override_rules:
            if needle not in query:
                continue
            occurrence = self.query_occurrences.get(needle, 0) + 1
            self.query_occurrences[needle] = occurrence
            if expected_occurrence is None or occurrence == expected_occurrence:
                rows = replacement
        if self.wrong_rowcount_query is not None and self.wrong_rowcount_query in query:
            rowcount = self.wrong_rowcount
        return FakeCursor(rows, rowcount=rowcount)

    def execute(self, query: str, params: object = None) -> FakeCursor:
        self.executed.append((query, params))
        self._begin()
        if self.fail_once_query is not None and self.fail_once_query in query:
            self.info.status = self.statement_failure_status
            raise psycopg.OperationalError(PRIVATE_SENTINEL)
        values = cast(tuple[Any, ...], params or ())
        database = self.database

        if query.startswith("SET TRANSACTION"):
            return self._cursor(query)
        if "pg_advisory_xact_lock" in query:
            return self._cursor(query, [(1,)])
        if query.startswith("SELECT scope_fingerprint"):
            ledger = database.ledgers.get(cast(ScopeKey, values[:4]))
            rows = [] if ledger is None else [ledger[4:10]]
            return self._cursor(query, rows)
        if "FROM screening_idempotency_ledger AS ledger" in query:
            row = database.graph_row(cast(ScopeKey, values[:4]))
            return self._cursor(query, [] if row is None else [row])
        if "FROM screening_attempt_audit AS attempt" in query:
            rows = database.attempt_rows(cast(ScopeKey, values[:4]))
            return self._cursor(query, rows[: int(values[4])])

        if query.startswith("INSERT INTO screening_opaque_id_registry"):
            opaque_id, kind = cast(tuple[str, str], values)
            if opaque_id in database.registry:
                raise psycopg.errors.UniqueViolation(PRIVATE_SENTINEL)
            database.registry[opaque_id] = kind
            return self._cursor(query, rowcount=1)
        if query.startswith("INSERT INTO screening_accepted_intake"):
            intake_id = cast(str, values[0])
            if intake_id in database.intakes:
                raise psycopg.errors.UniqueViolation(PRIVATE_SENTINEL)
            database.intakes[intake_id] = values
            return self._cursor(query, rowcount=1)
        if query.startswith("INSERT INTO screening_identity"):
            screening_id = cast(str, values[0])
            if screening_id in database.screenings:
                raise psycopg.errors.UniqueViolation(PRIVATE_SENTINEL)
            database.screenings[screening_id] = values
            return self._cursor(query, rowcount=1)
        if query.startswith("INSERT INTO screening_accepted_outbox"):
            event_id = cast(str, values[0])
            if event_id in database.outbox:
                raise psycopg.errors.UniqueViolation(PRIVATE_SENTINEL)
            database.outbox[event_id] = values
            return self._cursor(query, rowcount=1)
        if query.startswith("INSERT INTO screening_idempotency_ledger"):
            scope = cast(ScopeKey, values[:4])
            if scope in database.ledgers:
                raise psycopg.errors.UniqueViolation(PRIVATE_SENTINEL)
            database.ledgers[scope] = values
            return self._cursor(query, rowcount=1)
        if query.startswith("INSERT INTO screening_attempt_audit"):
            if any(item[6] == values[6] for item in database.attempts):
                raise psycopg.errors.UniqueViolation(PRIVATE_SENTINEL)
            database.attempts.append(values)
            return self._cursor(query, rowcount=1)
        raise AssertionError(f"unexpected SQL: {query}")

    def commit(self) -> None:
        self.commits += 1
        if self.commit_failure:
            if self.commit_status_override is not None:
                self.info.status = self.commit_status_override
            elif self.commit_unknown:
                self.before = None
                self.info.status = TransactionStatus.UNKNOWN
            else:
                self.info.status = TransactionStatus.INERROR
            if self.status_failure_after_commit:
                self.info.fail = True
            raise psycopg.OperationalError(PRIVATE_SENTINEL)
        self.before = None
        self.info.status = TransactionStatus.IDLE

    def rollback(self) -> None:
        self.rollbacks += 1
        if self.rollback_failure:
            raise psycopg.OperationalError(PRIVATE_SENTINEL)
        if self.before is not None:
            self.database.restore(self.before)
        self.before = None
        self.info.status = TransactionStatus.IDLE

    def close(self) -> None:
        self.close_calls += 1
        if self.close_failure:
            raise psycopg.OperationalError(PRIVATE_SENTINEL)
        if self.before is not None:
            self.database.restore(self.before)
        self.before = None
        if self.info.status is not TransactionStatus.UNKNOWN:
            self.info.status = TransactionStatus.IDLE


class FakeConnectionFactory:
    def __init__(self, database: FakeDatabase | None = None) -> None:
        self.database = database or FakeDatabase.create()
        self.connections: list[FakeConnection] = []
        self.configurations: list[Callable[[FakeConnection], None]] = []
        self.failure = False

    def __call__(self) -> Connection[Any]:
        if self.failure:
            raise psycopg.OperationalError(PRIVATE_SENTINEL)
        connection = FakeConnection(self.database)
        if self.configurations:
            self.configurations.pop(0)(connection)
        self.connections.append(connection)
        return cast(Connection[Any], connection)


class StructuralProbe:
    def __init__(self, raising_attribute: str, *, close_failure: bool = False) -> None:
        self.raising_attribute = raising_attribute
        self.close_failure = close_failure
        self.close_calls = 0
        self.info_value = FakeInfo()
        self.executed: list[tuple[str, object | None]] = []

    def __getattribute__(self, name: str) -> object:
        if name not in {
            "raising_attribute",
            "__dict__",
        } and name == object.__getattribute__(self, "raising_attribute"):
            raise RuntimeError(PRIVATE_SENTINEL)
        return object.__getattribute__(self, name)

    @property
    def info(self) -> FakeInfo:
        return self.info_value

    @property
    def autocommit(self) -> bool:
        return False

    def execute(self, query: str, params: object = None) -> FakeCursor:
        self.executed.append((query, params))
        return FakeCursor()

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        self.close_calls += 1
        if self.close_failure:
            raise RuntimeError(PRIVATE_SENTINEL)


def repository(
    factory: FakeConnectionFactory,
) -> PostgresScreeningSubmissionUnitOfWork:
    return PostgresScreeningSubmissionUnitOfWork(factory)


def request_payload(
    *,
    tenant_id: str = "tenant-1",
    correlation_id: str = "correlation-1",
    action: str = "CUSTOMER_ONBOARDING",
) -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "tenant_id": tenant_id,
        "correlation_id": correlation_id,
        "data_classification": "SYNTHETIC",
        "external_object": {
            "system": "synthetic-crm",
            "object_type": "CUSTOMER",
            "object_id": PRIVATE_SENTINEL,
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


def actor_context(
    *,
    tenant_id: str = "tenant-1",
    client_id: str = "client-1",
    subject: str = "actor-1",
) -> ActorContext:
    return ActorContext(
        subject=subject,
        client_id=client_id,
        tenant_id=tenant_id,
        actor_type=ActorType.SERVICE,
        scopes=frozenset({Scope.SCREENING_SUBMIT}),
        roles=frozenset({Role.AUDITOR}),
        issuer="https://identity.example.test/",
        audience="urn:tradesieve:test",
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=5),
        demo_identity=False,
    )


def intake(
    *,
    tenant_id: str = "tenant-1",
    client_id: str = "client-1",
    subject: str = "actor-1",
    correlation_id: str = "correlation-1",
    action: str = "CUSTOMER_ONBOARDING",
    indent: int | None = None,
) -> CanonicalScreeningIntake:
    context = RequestContext(
        actor=actor_context(
            tenant_id=tenant_id,
            client_id=client_id,
            subject=subject,
        ),
        tenant_id=tenant_id,
        correlation_id=correlation_id,
    )
    raw = json.dumps(
        request_payload(
            tenant_id=tenant_id,
            correlation_id=correlation_id,
            action=action,
        ),
        ensure_ascii=False,
        indent=indent,
    ).encode()
    return CanonicalScreeningIntake.decode(
        raw,
        "application/json",
        context=context,
        received_at=NOW,
    )


def authorization_row(
    item: CanonicalScreeningIntake,
    key: str,
    event_id: str,
    occurred_at: datetime,
) -> Row:
    scope = screening_idempotency_scope(item, key)
    target = screening_authorization_target(scope)
    context = item.context
    return (
        event_id,
        occurred_at,
        context.actor.subject,
        context.actor.client_id,
        context.actor.actor_type.value,
        context.actor.tenant_id,
        context.tenant_id,
        target.tenant_id,
        Operation.SCREENING_SUBMIT.value,
        target.opaque_ref,
        context.correlation_id,
        "ALLOW",
        "ALLOWED",
    )


def atomic_request(
    database: FakeDatabase,
    *,
    item: CanonicalScreeningIntake | None = None,
    key: str = PRIVATE_KEY,
    event_id: str = "authz-1",
    attempt_id: str = "attempt-1",
    intake_id: str = "intake-1",
    screening_id: str = "screening-1",
    outbox_id: str = "outbox-1",
    occurred_at: datetime = COMMITTED_AT,
) -> ScreeningSubmissionAtomicWrite:
    current = item or intake()
    scope = screening_idempotency_scope(current, key)
    target = screening_authorization_target(scope)
    database.authorization[event_id] = authorization_row(
        current, key, event_id, occurred_at
    )
    bound = bind_screening_submission(
        current,
        key,
        AuthorizedRequest(
            context=current.context,
            operation=Operation.SCREENING_SUBMIT,
            target=target,
            audit_event_id=event_id,
        ),
    )
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


def assert_fixed_failure(operation: Callable[[], object]) -> None:
    with pytest.raises(ScreeningSubmissionPersistenceFailure) as exc_info:
        operation()
    error = exc_info.value
    assert str(error) == "screening submission persistence failed"
    assert repr(error) == (
        "ScreeningSubmissionPersistenceFailure("
        "'screening submission persistence failed')"
    )
    assert error.__cause__ is None
    assert error.__context__ is None
    rendered = "".join(traceback.format_exception(error))
    for private in (PRIVATE_KEY, PRIVATE_SENTINEL, "tenant-1", "actor-1"):
        assert private not in str(error)
        assert private not in repr(error)
        assert private not in rendered


def replace_row(row: Row, index: int, value: object) -> Row:
    values = list(row)
    values[index] = value
    return tuple(values)


def scope_key(scope: ScreeningIdempotencyScope) -> ScopeKey:
    return (scope.tenant_id, scope.client_id, scope.operation, scope.key_digest)


def seed_attempt_history(database: FakeDatabase, count: int) -> None:
    assert 1 <= count <= MAX_ATTEMPT_QUERY
    base = database.attempts[0]
    base_authorization = database.authorization[cast(str, base[13])]
    for sequence in range(2, count + 1):
        attempt_id = f"attempt-{sequence:04d}"
        authorization_id = f"authz-{sequence:04d}"
        attempt = list(base)
        attempt[5] = sequence
        attempt[6] = attempt_id
        attempt[8] = ScreeningAttemptOutcome.REPLAY.value
        attempt[13] = authorization_id
        database.attempts.append(tuple(attempt))
        database.registry[attempt_id] = "ATTEMPT"
        database.authorization[authorization_id] = replace_row(
            base_authorization, 0, authorization_id
        )


def configure_rows(
    factory: FakeConnectionFactory,
    *rules: tuple[str, int | None, object],
) -> None:
    def configure(connection: FakeConnection) -> None:
        connection.row_override_rules.extend(rules)

    factory.configurations.append(configure)


def test_apply_read_and_exact_resolution_use_fresh_closed_connections() -> None:
    factory = FakeConnectionFactory()
    unit = repository(factory)
    request = atomic_request(factory.database)
    applied = unit.submit_atomic(request)

    assert applied.outcome is ScreeningAttemptOutcome.APPLIED
    assert applied.audit.result == request.proposed_result
    assert len(factory.database.registry) == 4
    assert len(factory.database.intakes) == 1
    assert len(factory.database.screenings) == 1
    assert len(factory.database.ledgers) == 1
    assert len(factory.database.attempts) == 1
    assert len(factory.database.outbox) == 1
    write_connection = factory.connections[0]
    assert write_connection.commits == 1
    assert write_connection.rollbacks == 0
    assert write_connection.close_calls == 1
    assert any(
        "pg_advisory_xact_lock" in query for query, _ in write_connection.executed
    )
    assert any(
        "LEFT JOIN authorization_audit_event" in query
        for query, _ in write_connection.executed
    )
    attempt_queries = [
        query
        for query, _ in write_connection.executed
        if "FROM screening_attempt_audit AS attempt" in query
    ]
    assert attempt_queries
    assert all("AS auth_event" in query for query in attempt_queries)
    assert all("AS authorization" not in query for query in attempt_queries)
    assert any(
        params is not None and MAX_ATTEMPT_QUERY in cast(tuple[object, ...], params)
        for query, params in write_connection.executed
        if "FROM screening_attempt_audit AS attempt" in query
    )

    stored = unit.read_result(request.bound.scope)
    assert stored == request.proposed_result
    assert stored is not request.proposed_result
    object.__setattr__(stored.intake, "byte_hash", "bad")
    assert unit.read_result(request.bound.scope) == request.proposed_result

    resolved = unit.resolve_attempt(
        request.bound.scope,
        request.proposed_result.canonical_hash,
        request.attempt_id,
    )
    assert resolved == applied
    assert (
        unit.resolve_attempt(
            request.bound.scope,
            "sha256:" + "f" * 64,
            request.attempt_id,
        )
        is None
    )
    assert (
        unit.resolve_attempt(
            request.bound.scope,
            request.proposed_result.canonical_hash,
            "missing-attempt",
        )
        is None
    )
    assert len(factory.connections) == 6
    for connection in factory.connections[1:]:
        assert connection.commits == 0
        assert connection.rollbacks == 1
        assert connection.close_calls == 1
        assert connection.executed[0][0].startswith("SET TRANSACTION")
        assert not any(query.startswith("INSERT") for query, _ in connection.executed)


def test_replay_and_conflict_discard_proposed_graph_and_keep_current_attribution() -> (
    None
):
    factory = FakeConnectionFactory()
    unit = repository(factory)
    first = atomic_request(factory.database)
    applied = unit.submit_atomic(first)
    replay_item = intake(subject="actor-2", correlation_id="correlation-2", indent=2)
    replay_request = atomic_request(
        factory.database,
        item=replay_item,
        event_id="authz-2",
        attempt_id="attempt-2",
        intake_id="discarded-intake-2",
        screening_id="discarded-screening-2",
        outbox_id="discarded-outbox-2",
        occurred_at=COMMITTED_AT + timedelta(seconds=1),
    )
    replay = unit.submit_atomic(replay_request)
    changed_item = intake(action="QUOTE_RELEASE", correlation_id="correlation-3")
    conflict_request = atomic_request(
        factory.database,
        item=changed_item,
        event_id="authz-3",
        attempt_id="attempt-3",
        intake_id="discarded-intake-3",
        screening_id="discarded-screening-3",
        outbox_id="discarded-outbox-3",
        occurred_at=COMMITTED_AT + timedelta(seconds=2),
    )
    conflict = unit.submit_atomic(conflict_request)

    assert replay.outcome is ScreeningAttemptOutcome.REPLAY
    assert conflict.outcome is ScreeningAttemptOutcome.CONFLICT
    assert replay.receipt == conflict.receipt == applied.receipt
    assert replay.audit.actor_subject == "actor-2"
    assert replay.audit.correlation_id == "correlation-2"
    assert conflict.audit.correlation_id == "correlation-3"
    assert len(factory.database.intakes) == 1
    assert len(factory.database.screenings) == 1
    assert len(factory.database.outbox) == 1
    assert len(factory.database.ledgers) == 1
    assert len(factory.database.attempts) == 3
    assert len(factory.database.registry) == 6
    for discarded in (
        "discarded-intake-2",
        "discarded-screening-2",
        "discarded-outbox-2",
        "discarded-intake-3",
        "discarded-screening-3",
        "discarded-outbox-3",
    ):
        assert discarded not in factory.database.registry


def test_exact_duplicate_attempt_is_read_only_and_changed_duplicate_fails() -> None:
    factory = FakeConnectionFactory()
    unit = repository(factory)
    request = atomic_request(factory.database)
    applied = unit.submit_atomic(request)
    before = factory.database.snapshot()
    duplicate = unit.submit_atomic(request.reverify())
    assert duplicate == applied
    assert factory.database.snapshot() == before
    duplicate_connection = factory.connections[-1]
    assert not any(
        query.startswith("INSERT") for query, _ in duplicate_connection.executed
    )

    changed = atomic_request(
        factory.database,
        event_id="authz-changed",
        attempt_id=request.attempt_id,
        intake_id="changed-intake",
        screening_id="changed-screening",
        outbox_id="changed-outbox",
    )
    before_changed = factory.database.snapshot()
    assert_fixed_failure(lambda: unit.submit_atomic(changed))
    assert factory.database.snapshot() == before_changed


@pytest.mark.parametrize(
    ("tenant_id", "client_id", "key"),
    [
        ("tenant-1", "client-1", "different-key"),
        ("tenant-1", "client-2", PRIVATE_KEY),
        ("tenant-2", "client-1", PRIVATE_KEY),
    ],
)
def test_every_scope_dimension_creates_an_independent_graph(
    tenant_id: str, client_id: str, key: str
) -> None:
    factory = FakeConnectionFactory()
    unit = repository(factory)
    unit.submit_atomic(atomic_request(factory.database))
    second = atomic_request(
        factory.database,
        item=intake(tenant_id=tenant_id, client_id=client_id),
        key=key,
        event_id="authz-second",
        attempt_id="attempt-second",
        intake_id="intake-second",
        screening_id="screening-second",
        outbox_id="outbox-second",
    )
    assert unit.submit_atomic(second).outcome is ScreeningAttemptOutcome.APPLIED
    assert len(factory.database.ledgers) == 2
    assert len(factory.database.registry) == 8


@pytest.mark.parametrize(
    "query",
    [
        "pg_advisory_xact_lock",
        "SELECT scope_fingerprint",
        "INSERT INTO screening_opaque_id_registry",
        "INSERT INTO screening_accepted_intake",
        "INSERT INTO screening_identity",
        "INSERT INTO screening_accepted_outbox",
        "INSERT INTO screening_idempotency_ledger",
        "INSERT INTO screening_attempt_audit",
        "FROM screening_idempotency_ledger AS ledger",
        "FROM screening_attempt_audit AS attempt",
    ],
)
def test_every_statement_stage_failure_rolls_back_without_partial_state(
    query: str,
) -> None:
    factory = FakeConnectionFactory()
    factory.configurations.append(
        lambda connection: setattr(connection, "fail_once_query", query)
    )
    request = atomic_request(factory.database)
    before = factory.database.snapshot()
    assert_fixed_failure(lambda: repository(factory).submit_atomic(request))
    assert factory.database.snapshot() == before
    connection = factory.connections[0]
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.close_calls == 1


@pytest.mark.parametrize(
    "query",
    [
        "INSERT INTO screening_opaque_id_registry",
        "INSERT INTO screening_accepted_intake",
        "INSERT INTO screening_identity",
        "INSERT INTO screening_accepted_outbox",
        "INSERT INTO screening_idempotency_ledger",
        "INSERT INTO screening_attempt_audit",
    ],
)
def test_every_insert_requires_exact_rowcount_one(query: str) -> None:
    factory = FakeConnectionFactory()
    factory.configurations.append(
        lambda connection: setattr(connection, "wrong_rowcount_query", query)
    )
    request = atomic_request(factory.database)
    before = factory.database.snapshot()
    assert_fixed_failure(lambda: repository(factory).submit_atomic(request))
    assert factory.database.snapshot() == before


@pytest.mark.parametrize(
    "initial_state",
    [
        "autocommit",
        "autocommit_type",
        "autocommit_property",
        "active",
        "intrans",
        "inerror",
        "unknown",
        "status_type",
        "status_property",
    ],
)
def test_non_idle_or_inexact_factory_connections_execute_zero_sql(
    initial_state: str,
) -> None:
    factory = FakeConnectionFactory()

    def configure(connection: FakeConnection) -> None:
        if initial_state == "autocommit":
            connection._autocommit = True
        elif initial_state == "autocommit_type":
            connection._autocommit = 0
        elif initial_state == "autocommit_property":
            connection.autocommit_fail = True
        elif initial_state == "status_property":
            connection.info.fail = True
        elif initial_state == "status_type":
            connection.info.status = 0
        else:
            connection.info.status = {
                "active": TransactionStatus.ACTIVE,
                "intrans": TransactionStatus.INTRANS,
                "inerror": TransactionStatus.INERROR,
                "unknown": TransactionStatus.UNKNOWN,
            }[initial_state]

    factory.configurations.append(configure)
    request = atomic_request(factory.database)
    before = factory.database.snapshot()
    assert_fixed_failure(lambda: repository(factory).submit_atomic(request))
    connection = factory.connections[0]
    assert connection.executed == []
    assert connection.commits == 0
    assert connection.rollbacks == 0
    assert connection.close_calls == 1
    assert factory.database.snapshot() == before


@pytest.mark.parametrize(
    ("behavior", "expected_unknown", "durable"),
    [
        ("statement_unknown", False, False),
        ("commit_known", False, False),
        ("commit_unknown", True, True),
        ("commit_status_failure", False, False),
        ("commit_close_failure", False, True),
    ],
)
def test_only_commit_ack_failure_with_exact_unknown_status_is_commit_unknown(
    behavior: str, expected_unknown: bool, durable: bool
) -> None:
    factory = FakeConnectionFactory()

    def configure(connection: FakeConnection) -> None:
        if behavior == "statement_unknown":
            connection.fail_once_query = "INSERT INTO screening_accepted_intake"
            connection.statement_failure_status = TransactionStatus.UNKNOWN
        else:
            connection.commit_failure = behavior != "commit_close_failure"
            connection.commit_unknown = behavior == "commit_unknown"
            connection.status_failure_after_commit = behavior == "commit_status_failure"
            connection.close_failure = behavior == "commit_close_failure"

    factory.configurations.append(configure)
    request = atomic_request(factory.database)
    before_authorization = copy.deepcopy(factory.database.authorization)
    if expected_unknown:
        with pytest.raises(ScreeningSubmissionCommitOutcomeUnknown) as exc_info:
            repository(factory).submit_atomic(request)
        assert str(exc_info.value) == "screening submission commit outcome unknown"
        assert exc_info.value.__cause__ is None
        assert exc_info.value.__context__ is None
    else:
        assert_fixed_failure(lambda: repository(factory).submit_atomic(request))
    assert bool(factory.database.ledgers) is durable
    assert factory.database.authorization == before_authorization
    connection = factory.connections[0]
    assert connection.close_calls == 1
    if behavior == "statement_unknown":
        assert connection.commits == 0
        assert connection.rollbacks == 1
    elif behavior in {"commit_known", "commit_status_failure"}:
        assert connection.commits == 1
        assert connection.rollbacks == 1
    elif behavior == "commit_unknown":
        assert connection.commits == 1
        assert connection.rollbacks == 0
    else:
        assert connection.commits == 1
        assert connection.rollbacks == 0


def test_service_uses_a_fresh_connection_to_resolve_real_commit_unknown() -> None:
    factory = FakeConnectionFactory()

    def configure_unknown_commit(connection: FakeConnection) -> None:
        connection.commit_failure = True
        connection.commit_unknown = True

    factory.configurations.append(configure_unknown_commit)

    class Entitlements:
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

    def audit_sink(event: AuthorizationAuditEvent) -> None:
        factory.database.authorization[event.event_id] = (
            event.event_id,
            event.occurred_at,
            event.actor_subject,
            event.client_id,
            event.actor_type.value,
            event.actor_tenant_id,
            event.request_tenant_id,
            event.target_tenant_id,
            event.operation,
            event.target_ref,
            event.correlation_id,
            event.outcome.value,
            event.reason.value,
        )

    authorization = AuthorizationService(
        audit_sink,
        Entitlements(),
        event_id_factory=lambda: "authz-service",
    )
    service = ScreeningSubmissionService(
        authorization,
        repository(factory),
        clock=lambda: COMMITTED_AT,
        attempt_id_factory=lambda: "attempt-service",
        intake_id_factory=lambda: "intake-service",
        screening_id_factory=lambda: "screening-service",
        outbox_id_factory=lambda: "outbox-service",
    )
    result = service.submit(intake(), PRIVATE_KEY)
    assert result.disposition is ScreeningSubmissionServiceDisposition.APPLIED
    assert len(factory.connections) == 2
    assert factory.connections[0].commits == 1
    assert factory.connections[0].info.status is TransactionStatus.UNKNOWN
    assert factory.connections[1].executed[0][0].startswith("SET TRANSACTION")
    assert factory.connections[1].rollbacks == 1
    assert not any(
        query.startswith("INSERT") for query, _ in factory.connections[1].executed
    )


def test_constructor_factory_and_public_input_validation_are_fail_closed() -> None:
    with pytest.raises(
        TypeError, match="invalid screening submission connection factory"
    ):
        PostgresScreeningSubmissionUnitOfWork(cast(Any, object()))

    factory = FakeConnectionFactory()
    unit = repository(factory)
    request = atomic_request(factory.database)
    assert_fixed_failure(
        lambda: unit.submit_atomic(cast(ScreeningSubmissionAtomicWrite, object()))
    )
    assert_fixed_failure(lambda: unit.read_result(cast(Any, object())))
    assert_fixed_failure(
        lambda: unit.resolve_attempt(request.bound.scope, "invalid-hash", "attempt-1")
    )
    assert_fixed_failure(
        lambda: unit.resolve_attempt(
            request.bound.scope,
            request.proposed_result.canonical_hash,
            "invalid attempt",
        )
    )
    assert factory.connections == []

    factory.failure = True
    assert_fixed_failure(lambda: unit.submit_atomic(request))
    assert factory.connections == []


def test_prepare_requires_exact_private_context_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = FakeConnectionFactory()
    request = atomic_request(factory.database)
    different_context = intake(subject="actor-2").context

    def decode_to_different_context(_payload: object) -> RequestContext:
        return different_context

    monkeypatch.setattr(
        postgres_submission, "decode_request_context", decode_to_different_context
    )
    assert_fixed_failure(lambda: repository(factory).submit_atomic(request))
    assert factory.connections == []


@pytest.mark.parametrize(
    ("raising_attribute", "close_failure"),
    [("execute", False), ("info", False), ("commit", True)],
)
def test_structural_probe_failure_attempts_exactly_one_close_without_sql(
    raising_attribute: str, close_failure: bool
) -> None:
    probe = StructuralProbe(raising_attribute, close_failure=close_failure)

    def candidate_factory() -> Connection[Any]:
        return cast(Connection[Any], probe)

    unit = PostgresScreeningSubmissionUnitOfWork(candidate_factory)
    request = atomic_request(FakeDatabase.create())
    assert_fixed_failure(lambda: unit.submit_atomic(request))
    assert probe.executed == []
    assert probe.close_calls == 1


@pytest.mark.parametrize(
    "behavior", ["set", "operation", "rollback", "close", "fetchless"]
)
def test_read_failures_rollback_close_and_never_mutate(
    behavior: str,
) -> None:
    factory = FakeConnectionFactory()
    unit = repository(factory)
    request = atomic_request(factory.database)
    unit.submit_atomic(request)
    before = factory.database.snapshot()

    def configure(connection: FakeConnection) -> None:
        if behavior == "set":
            connection.fail_once_query = "SET TRANSACTION"
        elif behavior == "operation":
            connection.fail_once_query = "SELECT scope_fingerprint"
        elif behavior == "rollback":
            connection.rollback_failure = True
        elif behavior == "close":
            connection.close_failure = True
        else:
            connection.fetchless_query = "SELECT scope_fingerprint"

    factory.configurations.append(configure)
    assert_fixed_failure(lambda: unit.read_result(request.bound.scope))
    connection = factory.connections[-1]
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.close_calls == 1
    assert factory.database.snapshot() == before


@pytest.mark.parametrize("row_count", [0, 2])
def test_graph_query_requires_exactly_one_row(row_count: int) -> None:
    factory = FakeConnectionFactory()
    unit = repository(factory)
    request = atomic_request(factory.database)
    unit.submit_atomic(request)
    graph = factory.database.graph_row(scope_key(request.bound.scope))
    assert graph is not None
    configure_rows(
        factory,
        (
            "FROM screening_idempotency_ledger AS ledger",
            None,
            [graph] * row_count,
        ),
    )
    assert_fixed_failure(lambda: unit.read_result(request.bound.scope))


def test_graph_must_match_every_ledger_business_reference() -> None:
    factory = FakeConnectionFactory()
    unit = repository(factory)
    request = atomic_request(factory.database)
    unit.submit_atomic(request)
    ledger = factory.database.ledgers[scope_key(request.bound.scope)]
    ledger_row = replace_row(ledger[4:10], 4, "different-outbox")
    configure_rows(factory, ("SELECT scope_fingerprint", None, [ledger_row]))
    assert_fixed_failure(lambda: unit.read_result(request.bound.scope))


@pytest.mark.parametrize(
    ("case", "index", "value"),
    [
        ("row-length", -1, None),
        ("sequence-gap", 0, 2),
        ("sequence-type", 0, IntegerSubclass(1)),
        ("attempt-id-type", 1, StringSubclass("attempt-1")),
        ("attempt-id-format", 1, "invalid attempt"),
        ("attempt-id-kind", 2, "OUTBOX"),
        ("outcome-enum", 3, "INVALID"),
        ("occurred-type", 4, "not-a-time"),
        ("occurred-naive", 4, COMMITTED_AT.replace(tzinfo=None)),
        ("actor-enum", 5, "ALIEN"),
        ("actor-id", 6, "invalid actor"),
        ("correlation-id", 7, "invalid correlation"),
        ("authorization-id", 8, "invalid authorization"),
        ("byte-hash-type", 9, StringSubclass("sha256:" + "f" * 64)),
        ("byte-hash-format", 9, "invalid-hash"),
        ("canonical-hash", 10, "invalid-hash"),
        ("scope-fingerprint", 11, "sha256:" + "f" * 64),
        ("registry-kind", 12, "INTAKE"),
    ],
)
def test_malformed_attempt_columns_fail_closed(
    case: str, index: int, value: object
) -> None:
    factory = FakeConnectionFactory()
    unit = repository(factory)
    request = atomic_request(factory.database)
    unit.submit_atomic(request)
    row = factory.database.attempt_rows(scope_key(request.bound.scope))[0]
    malformed = row[:-1] if case == "row-length" else replace_row(row, index, value)
    configure_rows(
        factory,
        ("FROM screening_attempt_audit AS attempt", None, [malformed]),
    )
    assert_fixed_failure(lambda: unit.read_result(request.bound.scope))


@pytest.mark.parametrize(
    ("index", "value"),
    [
        (13, "different-authz"),
        (14, COMMITTED_AT + timedelta(seconds=1)),
        (15, "actor-2"),
        (16, "client-2"),
        (17, "HUMAN"),
        (18, "tenant-2"),
        (19, "tenant-2"),
        (20, "tenant-2"),
        (21, "SCREENING_READ"),
        (22, "SCREENING_IDEMPOTENCY:different"),
        (23, "correlation-2"),
        (24, "DENY"),
        (25, "POLICY_MISSING"),
        (13, None),
        (14, COMMITTED_AT.replace(tzinfo=None)),
        (17, 1),
    ],
)
def test_every_authorization_join_field_is_exact(index: int, value: object) -> None:
    factory = FakeConnectionFactory()
    unit = repository(factory)
    request = atomic_request(factory.database)
    unit.submit_atomic(request)
    row = factory.database.attempt_rows(scope_key(request.bound.scope))[0]
    configure_rows(
        factory,
        (
            "FROM screening_attempt_audit AS attempt",
            None,
            [replace_row(row, index, value)],
        ),
    )
    assert_fixed_failure(lambda: unit.read_result(request.bound.scope))


@pytest.mark.parametrize("missing", ["attempt-registry", "authorization"])
def test_attempt_left_joins_expose_missing_registry_or_authorization(
    missing: str,
) -> None:
    factory = FakeConnectionFactory()
    unit = repository(factory)
    request = atomic_request(factory.database)
    unit.submit_atomic(request)
    if missing == "attempt-registry":
        del factory.database.registry[request.attempt_id]
    else:
        authorization_id = cast(str, factory.database.attempts[0][13])
        del factory.database.authorization[authorization_id]
    assert_fixed_failure(lambda: unit.read_result(request.bound.scope))


@pytest.mark.parametrize("duplicate", ["attempt-id", "authorization-id"])
def test_attempt_and_authorization_ids_must_be_unique_within_scope(
    duplicate: str,
) -> None:
    factory = FakeConnectionFactory()
    unit = repository(factory)
    request = atomic_request(factory.database)
    unit.submit_atomic(request)
    seed_attempt_history(factory.database, 2)
    first, second = factory.database.attempts
    index = 6 if duplicate == "attempt-id" else 13
    factory.database.attempts[1] = replace_row(second, index, first[index])
    assert_fixed_failure(lambda: unit.read_result(request.bound.scope))


def test_attempt_id_must_not_collide_with_graph_ids() -> None:
    factory = FakeConnectionFactory()
    unit = repository(factory)
    request = atomic_request(factory.database)
    unit.submit_atomic(request)
    row = factory.database.attempt_rows(scope_key(request.bound.scope))[0]
    collision = replace_row(row, 1, request.proposed_result.intake.intake_id)
    collision = replace_row(collision, 12, "ATTEMPT")
    configure_rows(
        factory,
        ("FROM screening_attempt_audit AS attempt", None, [collision]),
    )
    assert_fixed_failure(lambda: unit.read_result(request.bound.scope))


def test_graph_business_ids_must_be_distinct() -> None:
    factory = FakeConnectionFactory()
    unit = repository(factory)
    request = atomic_request(factory.database)
    unit.submit_atomic(request)
    scope = scope_key(request.bound.scope)
    ledger = factory.database.ledgers[scope]
    graph = factory.database.graph_row(scope)
    assert graph is not None
    collided_ledger = replace_row(
        ledger[4:10], 4, request.proposed_result.intake.intake_id
    )
    collided_graph = replace_row(graph, 26, request.proposed_result.intake.intake_id)
    collided_graph = replace_row(collided_graph, 33, "OUTBOX")
    configure_rows(
        factory,
        ("SELECT scope_fingerprint", None, [collided_ledger]),
        ("FROM screening_idempotency_ledger AS ledger", None, [collided_graph]),
    )
    assert_fixed_failure(lambda: unit.read_result(request.bound.scope))


def test_zero_attempts_and_4097_attempt_overflow_fail_closed() -> None:
    empty_factory = FakeConnectionFactory()
    empty_unit = repository(empty_factory)
    empty_request = atomic_request(empty_factory.database)
    empty_unit.submit_atomic(empty_request)
    empty_factory.database.attempts.clear()
    assert_fixed_failure(lambda: empty_unit.read_result(empty_request.bound.scope))

    overflow_factory = FakeConnectionFactory()
    overflow_unit = repository(overflow_factory)
    overflow_request = atomic_request(overflow_factory.database)
    overflow_unit.submit_atomic(overflow_request)
    seed_attempt_history(overflow_factory.database, MAX_ATTEMPT_QUERY)
    assert len(overflow_factory.database.attempts) == MAX_ATTEMPTS + 1
    assert_fixed_failure(
        lambda: overflow_unit.read_result(overflow_request.bound.scope)
    )
    query, params = next(
        (query, params)
        for query, params in overflow_factory.connections[-1].executed
        if "FROM screening_attempt_audit AS attempt" in query
    )
    assert "LIMIT %s" in query
    assert cast(tuple[object, ...], params)[-1] == MAX_ATTEMPT_QUERY


def test_4096_attempt_cap_rejects_new_retry_without_mutation() -> None:
    factory = FakeConnectionFactory()
    unit = repository(factory)
    request = atomic_request(factory.database)
    unit.submit_atomic(request)
    seed_attempt_history(factory.database, MAX_ATTEMPTS)
    retry = atomic_request(
        factory.database,
        event_id="authz-cap-retry",
        attempt_id="attempt-cap-retry",
        intake_id="discarded-intake-cap",
        screening_id="discarded-screening-cap",
        outbox_id="discarded-outbox-cap",
        occurred_at=COMMITTED_AT + timedelta(seconds=2),
    )
    before = factory.database.snapshot()
    assert_fixed_failure(lambda: unit.submit_atomic(retry))
    assert factory.database.snapshot() == before
    assert "attempt-cap-retry" not in factory.database.registry
    connection = factory.connections[-1]
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.close_calls == 1
    assert factory.database.snapshot() == before


@pytest.mark.parametrize(
    ("rollback_failure", "close_failure"), [(True, False), (False, True)]
)
def test_statement_failure_teardown_never_commits_partial_state(
    rollback_failure: bool, close_failure: bool
) -> None:
    factory = FakeConnectionFactory()

    def configure(connection: FakeConnection) -> None:
        connection.fail_once_query = "INSERT INTO screening_identity"
        connection.rollback_failure = rollback_failure
        connection.close_failure = close_failure

    factory.configurations.append(configure)
    request = atomic_request(factory.database)
    before = factory.database.snapshot()
    assert_fixed_failure(lambda: repository(factory).submit_atomic(request))
    connection = factory.connections[0]
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.close_calls == 1
    assert factory.database.snapshot() == before


@pytest.mark.parametrize("status", [StringSubclass("UNKNOWN"), 4, IntegerSubclass(4)])
def test_commit_status_must_be_exact_transaction_status_unknown(status: object) -> None:
    factory = FakeConnectionFactory()

    def configure(connection: FakeConnection) -> None:
        connection.commit_failure = True
        connection.commit_status_override = status

    factory.configurations.append(configure)
    request = atomic_request(factory.database)
    before = factory.database.snapshot()
    assert_fixed_failure(lambda: repository(factory).submit_atomic(request))
    connection = factory.connections[0]
    assert connection.commits == 1
    assert connection.rollbacks == 1
    assert connection.close_calls == 1
    assert factory.database.snapshot() == before


def test_unknown_commit_stays_unknown_when_close_also_fails() -> None:
    factory = FakeConnectionFactory()

    def configure(connection: FakeConnection) -> None:
        connection.commit_failure = True
        connection.commit_unknown = True
        connection.close_failure = True

    factory.configurations.append(configure)
    request = atomic_request(factory.database)
    with pytest.raises(ScreeningSubmissionCommitOutcomeUnknown):
        repository(factory).submit_atomic(request)
    assert factory.database.ledgers
    connection = factory.connections[0]
    assert connection.rollbacks == 0
    assert connection.close_calls == 1


@pytest.mark.parametrize(
    "rows",
    [
        ((1,),),
        [[1]],
        [(1,), (1,)],
        None,
    ],
)
def test_fetch_rows_requires_bounded_list_of_exact_tuples(rows: object) -> None:
    factory = FakeConnectionFactory()
    configure_rows(factory, ("pg_advisory_xact_lock", None, rows))
    request = atomic_request(factory.database)
    before = factory.database.snapshot()
    assert_fixed_failure(lambda: repository(factory).submit_atomic(request))
    assert factory.database.snapshot() == before


def test_advisory_lock_must_return_exact_success_row() -> None:
    factory = FakeConnectionFactory()
    configure_rows(factory, ("pg_advisory_xact_lock", None, [(2,)]))
    request = atomic_request(factory.database)
    assert_fixed_failure(lambda: repository(factory).submit_atomic(request))
    assert factory.database.ledgers == {}


@pytest.mark.parametrize("rowcount", [None, True, IntegerSubclass(1), 2])
def test_insert_rowcount_must_be_exact_integer_one(rowcount: object) -> None:
    factory = FakeConnectionFactory()

    def configure(connection: FakeConnection) -> None:
        connection.wrong_rowcount_query = "INSERT INTO screening_opaque_id_registry"
        connection.wrong_rowcount = rowcount

    factory.configurations.append(configure)
    request = atomic_request(factory.database)
    before = factory.database.snapshot()
    assert_fixed_failure(lambda: repository(factory).submit_atomic(request))
    assert factory.database.snapshot() == before


@pytest.mark.parametrize(
    "case",
    [
        "length",
        "fingerprint-type",
        "fingerprint-format",
        "fingerprint-mismatch",
        "canonical-type",
        "canonical-format",
        "intake-type",
        "intake-format",
        "screening-type",
        "outbox-type",
        "committed-type",
        "committed-naive",
    ],
)
def test_malformed_ledger_rows_fail_closed(case: str) -> None:
    factory = FakeConnectionFactory()
    unit = repository(factory)
    request = atomic_request(factory.database)
    unit.submit_atomic(request)
    scope = scope_key(request.bound.scope)
    ledger = factory.database.ledgers[scope]
    other_hash = "sha256:" + "f" * 64
    if case == "length":
        configure_rows(factory, ("SELECT scope_fingerprint", None, [ledger[4:9]]))
    else:
        index, value = {
            "fingerprint-type": (4, StringSubclass(cast(str, ledger[4]))),
            "fingerprint-format": (4, "invalid-hash"),
            "fingerprint-mismatch": (4, other_hash),
            "canonical-type": (5, StringSubclass(cast(str, ledger[5]))),
            "canonical-format": (5, "invalid-hash"),
            "intake-type": (6, StringSubclass(cast(str, ledger[6]))),
            "intake-format": (6, "invalid intake"),
            "screening-type": (7, 1),
            "outbox-type": (8, None),
            "committed-type": (9, "not-a-time"),
            "committed-naive": (9, COMMITTED_AT.replace(tzinfo=None)),
        }[case]
        factory.database.ledgers[scope] = replace_row(ledger, index, value)
    before = factory.database.snapshot()
    assert_fixed_failure(lambda: unit.read_result(request.bound.scope))
    assert factory.database.snapshot() == before


@pytest.mark.parametrize(
    ("case", "index", "value"),
    [
        ("row-length", -1, None),
        ("intake-id-kind", 1, "SCREENING"),
        ("intake-registry-kind", 16, "OUTBOX"),
        ("tenant-mismatch", 2, "tenant-2"),
        ("client-mismatch", 3, "client-2"),
        ("actor-mismatch", 4, "actor-2"),
        ("actor-enum", 5, "ALIEN"),
        ("correlation-mismatch", 6, "correlation-2"),
        ("scope-mismatch", 7, "sha256:" + "f" * 64),
        ("schema-mismatch", 8, "2.0.0"),
        ("media-mismatch", 9, "text/plain"),
        ("raw-type", 10, bytearray(b"{}")),
        ("raw-decode", 10, b"{}"),
        ("length-type", 11, IntegerSubclass(1)),
        ("length-mismatch", 11, 1),
        ("byte-hash-mismatch", 12, "sha256:" + "f" * 64),
        ("canonical-mismatch", 13, "sha256:" + "f" * 64),
        ("received-type", 14, "not-a-time"),
        ("received-naive", 14, NOW.replace(tzinfo=None)),
        ("context-type", 15, bytearray(b"{}")),
        ("context-decode", 15, b"{}"),
        ("screening-id-kind", 18, "INTAKE"),
        ("screening-registry-kind", 25, "ATTEMPT"),
        ("screening-intake-ref", 19, "different-intake"),
        ("screening-tenant", 20, "tenant-2"),
        ("screening-client", 21, "client-2"),
        ("screening-scope", 22, "sha256:" + "f" * 64),
        ("screening-canonical", 23, "sha256:" + "f" * 64),
        ("screening-time", 24, COMMITTED_AT.replace(tzinfo=None)),
        ("outbox-id-kind", 27, "INTAKE"),
        ("outbox-enum", 28, "screening.invalid"),
        ("outbox-schema", 29, "2.0.0"),
        ("outbox-time", 30, COMMITTED_AT.replace(tzinfo=None)),
        ("outbox-intake-ref", 31, "different-intake"),
        ("outbox-screening-ref", 32, "different-screening"),
        ("outbox-registry-kind", 33, "SCREENING"),
    ],
)
def test_malformed_graph_columns_fail_closed(
    case: str, index: int, value: object
) -> None:
    factory = FakeConnectionFactory()
    unit = repository(factory)
    request = atomic_request(factory.database)
    unit.submit_atomic(request)
    scope = scope_key(request.bound.scope)
    graph = factory.database.graph_row(scope)
    assert graph is not None
    malformed = graph[:-1] if case == "row-length" else replace_row(graph, index, value)
    configure_rows(
        factory,
        ("FROM screening_idempotency_ledger AS ledger", None, [malformed]),
    )
    before = factory.database.snapshot()
    assert_fixed_failure(lambda: unit.read_result(request.bound.scope))
    assert factory.database.snapshot() == before


@pytest.mark.parametrize(
    "missing",
    [
        "intake",
        "screening",
        "outbox",
        "intake-registry",
        "screening-registry",
        "outbox-registry",
    ],
)
def test_graph_left_joins_expose_missing_records_and_registries(missing: str) -> None:
    factory = FakeConnectionFactory()
    unit = repository(factory)
    request = atomic_request(factory.database)
    unit.submit_atomic(request)
    result = request.proposed_result
    if missing == "intake":
        del factory.database.intakes[result.intake.intake_id]
    elif missing == "screening":
        del factory.database.screenings[result.screening.screening_id]
    elif missing == "outbox":
        del factory.database.outbox[result.outbox.event_id]
    else:
        opaque_id = {
            "intake-registry": result.intake.intake_id,
            "screening-registry": result.screening.screening_id,
            "outbox-registry": result.outbox.event_id,
        }[missing]
        del factory.database.registry[opaque_id]
    before = factory.database.snapshot()
    assert_fixed_failure(lambda: unit.read_result(request.bound.scope))
    assert factory.database.snapshot() == before


def test_graph_requires_private_context_canonical_reencoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = FakeConnectionFactory()
    unit = repository(factory)
    request = atomic_request(factory.database)
    unit.submit_atomic(request)
    scope = scope_key(request.bound.scope)
    graph = factory.database.graph_row(scope)
    assert graph is not None
    malformed = replace_row(graph, 15, b"non-canonical-private-context")
    configure_rows(
        factory,
        ("FROM screening_idempotency_ledger AS ledger", None, [malformed]),
    )

    def decode_as_original(_payload: object) -> RequestContext:
        return request.bound._verified_intake_for_persistence().context

    monkeypatch.setattr(
        postgres_submission, "decode_request_context", decode_as_original
    )
    assert_fixed_failure(lambda: unit.read_result(request.bound.scope))


def test_missing_scope_read_and_resolution_return_none_on_fresh_connections() -> None:
    factory = FakeConnectionFactory()
    unit = repository(factory)
    request = atomic_request(factory.database)
    assert unit.read_result(request.bound.scope) is None
    assert (
        unit.resolve_attempt(
            request.bound.scope,
            request.proposed_result.canonical_hash,
            request.attempt_id,
        )
        is None
    )
    assert len(factory.connections) == 2
    assert all(connection.rollbacks == 1 for connection in factory.connections)


@pytest.mark.parametrize(
    "collision", ["intake-screening", "intake-attempt", "intake-outbox"]
)
def test_new_graph_rejects_any_collision_among_four_allocated_ids(
    collision: str,
) -> None:
    factory = FakeConnectionFactory()
    values = {
        "intake-screening": ("same-id", "same-id", "attempt-1", "outbox-1"),
        "intake-attempt": ("same-id", "screening-1", "same-id", "outbox-1"),
        "intake-outbox": ("same-id", "screening-1", "attempt-1", "same-id"),
    }[collision]
    request = atomic_request(
        factory.database,
        intake_id=values[0],
        screening_id=values[1],
        attempt_id=values[2],
        outbox_id=values[3],
    )
    before = factory.database.snapshot()
    assert_fixed_failure(lambda: repository(factory).submit_atomic(request))
    assert factory.database.snapshot() == before
    assert factory.database.ledgers == {}


def test_retry_attempt_registry_collision_rolls_back_and_discards_business_ids() -> (
    None
):
    factory = FakeConnectionFactory()
    unit = repository(factory)
    first = atomic_request(factory.database)
    unit.submit_atomic(first)
    retry = atomic_request(
        factory.database,
        event_id="authz-collision",
        attempt_id=first.proposed_result.intake.intake_id,
        intake_id="discarded-intake-collision",
        screening_id="discarded-screening-collision",
        outbox_id="discarded-outbox-collision",
        occurred_at=COMMITTED_AT + timedelta(seconds=1),
    )
    before = factory.database.snapshot()
    assert_fixed_failure(lambda: unit.submit_atomic(retry))
    assert factory.database.snapshot() == before
    for opaque_id in (
        "discarded-intake-collision",
        "discarded-screening-collision",
        "discarded-outbox-collision",
    ):
        assert opaque_id not in factory.database.registry


@pytest.mark.parametrize("action", ["CUSTOMER_ONBOARDING", "QUOTE_RELEASE"])
def test_duplicate_replay_or_conflict_requires_exact_stored_attribution(
    action: str,
) -> None:
    factory = FakeConnectionFactory()
    unit = repository(factory)
    unit.submit_atomic(atomic_request(factory.database))
    item = intake(action=action, subject="actor-2", correlation_id="correlation-2")
    retry = atomic_request(
        factory.database,
        item=item,
        event_id="authz-retry",
        attempt_id="attempt-retry",
        intake_id="discarded-intake-retry",
        screening_id="discarded-screening-retry",
        outbox_id="discarded-outbox-retry",
        occurred_at=COMMITTED_AT + timedelta(seconds=1),
    )
    stored = unit.submit_atomic(retry)
    before = factory.database.snapshot()
    assert unit.submit_atomic(retry.reverify()) == stored
    assert factory.database.snapshot() == before

    changed = atomic_request(
        factory.database,
        item=intake(
            action=action,
            subject="actor-3",
            correlation_id="correlation-3",
        ),
        event_id="authz-retry-changed",
        attempt_id=retry.attempt_id,
        intake_id="discarded-intake-changed",
        screening_id="discarded-screening-changed",
        outbox_id="discarded-outbox-changed",
        occurred_at=COMMITTED_AT + timedelta(seconds=2),
    )
    before_changed = factory.database.snapshot()
    assert_fixed_failure(lambda: unit.submit_atomic(changed))
    assert factory.database.snapshot() == before_changed


@pytest.mark.parametrize("mismatch", ["none", "result", "count", "last"])
def test_post_insert_refresh_must_exactly_match_expected_state(
    mismatch: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = FakeConnectionFactory()
    unit = repository(factory)
    request = atomic_request(factory.database)
    expected = request.bound.attempt_audit(
        attempt_id=request.attempt_id,
        outcome=ScreeningAttemptOutcome.APPLIED,
        occurred_at=request.occurred_at,
        result=request.proposed_result,
    )
    if mismatch == "none":
        refreshed: object = None
    elif mismatch == "result":
        alternate = atomic_request(
            factory.database,
            event_id="authz-alternate",
            attempt_id="attempt-alternate",
            intake_id="intake-alternate",
            screening_id="screening-alternate",
            outbox_id="outbox-alternate",
        )
        refreshed = postgres_submission._StoredScope(
            result=alternate.proposed_result,
            attempts=(expected,),
        )
    elif mismatch == "count":
        refreshed = postgres_submission._StoredScope(
            result=expected.result,
            attempts=(expected, expected),
        )
    else:
        different = request.bound.attempt_audit(
            attempt_id="different-attempt",
            outcome=ScreeningAttemptOutcome.REPLAY,
            occurred_at=request.occurred_at,
            result=request.proposed_result,
        )
        refreshed = postgres_submission._StoredScope(
            result=expected.result,
            attempts=(different,),
        )
    calls = 0

    def load_scope(
        _connection: object,
        _scope: ScreeningIdempotencyScope,
        *,
        lock: bool = False,
    ) -> object:
        nonlocal calls
        del lock
        calls += 1
        return None if calls == 1 else refreshed

    monkeypatch.setattr(unit, "_load_scope", load_scope)
    before = factory.database.snapshot()
    assert_fixed_failure(lambda: unit.submit_atomic(request))
    assert calls == 2
    assert factory.database.snapshot() == before


def test_exact_duplicate_rejects_audit_from_another_scope() -> None:
    first_factory = FakeConnectionFactory()
    first_unit = repository(first_factory)
    first = atomic_request(first_factory.database)
    applied = first_unit.submit_atomic(first)
    other_factory = FakeConnectionFactory()
    other = atomic_request(
        other_factory.database,
        item=intake(tenant_id="tenant-2"),
    )
    assert_fixed_failure(lambda: first_unit._exact_duplicate(other, applied.audit))


def test_invalid_candidate_with_noncallable_close_fails_closed() -> None:
    candidate = cast(Connection[Any], object())
    unit = PostgresScreeningSubmissionUnitOfWork(lambda: candidate)
    request = atomic_request(FakeDatabase.create())
    assert_fixed_failure(lambda: unit.submit_atomic(request))
