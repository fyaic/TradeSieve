"""Append-only PostgreSQL sink for authorization decisions."""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg import Connection

from tradesieve.application.auth import AuthorizationAuditEvent


class AuthorizationAuditPersistenceError(Exception):
    def __init__(self) -> None:
        super().__init__("authorization audit persistence unavailable")


class PostgresAuthorizationAuditSink:
    """Persist every evaluated authorization decision before it is returned."""

    def __init__(self, connection: Connection[Any]) -> None:
        self._connection = connection

    def __call__(self, event: AuthorizationAuditEvent) -> None:
        if not isinstance(event, AuthorizationAuditEvent):
            raise ValueError("authorization audit event must be typed")
        try:
            with self._connection.transaction():
                self._connection.execute(
                    "INSERT INTO authorization_audit_event ("
                    "event_id, occurred_at, actor_subject, client_id, actor_type, "
                    "actor_tenant_id, request_tenant_id, target_tenant_id, "
                    "operation, target_ref, correlation_id, outcome, reason"
                    ") VALUES (" + ", ".join(["%s"] * 13) + ")",
                    (
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
                    ),
                )
        except (psycopg.Error, TypeError, ValueError, IndexError) as exc:
            raise AuthorizationAuditPersistenceError from exc
