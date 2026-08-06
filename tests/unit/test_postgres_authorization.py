"""PostgreSQL authorization-audit sink tests."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime
from typing import Any, cast

import psycopg
import pytest
from psycopg import Connection

from tradesieve.adapters.postgres_authorization import (
    AuthorizationAuditPersistenceError,
    PostgresAuthorizationAuditSink,
)
from tradesieve.application.auth import (
    ActorType,
    AuthorizationAuditEvent,
    AuthorizationOutcome,
    AuthorizationReason,
)


class FakeConnection:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.executed: list[tuple[str, object | None]] = []

    def transaction(self) -> nullcontext[None]:
        return nullcontext()

    def execute(self, query: str, params: object | None = None) -> None:
        self.executed.append((query, params))
        if self.failure is not None:
            raise self.failure


def audit_event() -> AuthorizationAuditEvent:
    return AuthorizationAuditEvent(
        event_id="authz-1",
        occurred_at=datetime(2026, 8, 6, 12, 0, tzinfo=UTC),
        actor_subject="actor-1",
        client_id="client-1",
        actor_type=ActorType.HUMAN,
        actor_tenant_id="tenant-1",
        request_tenant_id="tenant-1",
        target_tenant_id="tenant-1",
        operation="POLICY_READ",
        target_ref="rule_set:target-1",
        correlation_id="correlation-1",
        outcome=AuthorizationOutcome.ALLOW,
        reason=AuthorizationReason.ALLOWED,
    )


def sink(connection: FakeConnection) -> PostgresAuthorizationAuditSink:
    return PostgresAuthorizationAuditSink(cast(Connection[Any], connection))


def test_authorization_audit_sink_persists_full_typed_event() -> None:
    connection = FakeConnection()
    sink(connection)(audit_event())
    query, params = connection.executed[0]
    assert query.startswith("INSERT INTO authorization_audit_event")
    assert params == (
        "authz-1",
        datetime(2026, 8, 6, 12, 0, tzinfo=UTC),
        "actor-1",
        "client-1",
        "HUMAN",
        "tenant-1",
        "tenant-1",
        "tenant-1",
        "POLICY_READ",
        "rule_set:target-1",
        "correlation-1",
        "ALLOW",
        "ALLOWED",
    )


def test_authorization_audit_sink_rejects_untyped_or_database_failure() -> None:
    with pytest.raises(ValueError, match="typed"):
        sink(FakeConnection())(cast(AuthorizationAuditEvent, object()))
    unavailable = sink(FakeConnection(psycopg.OperationalError("synthetic")))
    with pytest.raises(AuthorizationAuditPersistenceError):
        unavailable(audit_event())
