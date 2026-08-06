"""PostgreSQL unit-of-work for immutable rule bundles and lifecycle history."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import psycopg
from psycopg import Connection
from psycopg.types.json import Jsonb

from tradesieve.adapters.rule_bundle_codec import (
    decode_bundle_payload,
    decode_rescreen_impact,
    encode_bundle_payload,
    encode_rescreen_impact,
)
from tradesieve.domain.rule_bundle import (
    DraftWriteOutcome,
    InvalidLifecycleTransition,
    LifecycleActorType,
    LifecycleWriteOutcome,
    RuleBundleCommandAuditRecord,
    RuleBundleCommandOutcome,
    RuleBundleCommandReason,
    RuleBundleEventType,
    RuleBundleLifecycleEvent,
    RuleBundleRef,
    RuleBundleVersion,
    RuleSetLifecycle,
    fold_rule_bundle_events,
    rule_bundle_ref,
    semantically_applied_lifecycle_transition,
    validate_atomic_command_audit,
)
from tradesieve.ports.rule_bundle import RuleBundlePersistenceError

MAX_PERSISTED_HISTORY = 4096
MAX_BUNDLE_QUERY = 257
MAX_EVENT_QUERY = MAX_PERSISTED_HISTORY + 1
MAX_AUDIT_QUERY = 4096

BUNDLE_COLUMNS = (
    "tenant_id, deployment_id, rule_set_id, bundle_id, version, content_hash, payload"
)
STATE_COLUMNS = (
    "last_sequence, active_tenant_id, active_deployment_id, active_rule_set_id, "
    "active_bundle_id, active_version, active_content_hash"
)
EVENT_COLUMNS = (
    "tenant_id, deployment_id, rule_set_id, sequence, event_id, event_type, "
    "previous_tenant_id, previous_deployment_id, previous_rule_set_id, "
    "previous_bundle_id, previous_version, previous_content_hash, "
    "new_tenant_id, new_deployment_id, new_rule_set_id, new_bundle_id, "
    "new_version, new_content_hash, reason, actor_id, actor_type, occurred_at, "
    "rescreen_impact"
)
AUDIT_COLUMNS = (
    "command_event_id, authorization_event_id, lifecycle_event_id, tenant_id, "
    "deployment_id, rule_set_id, bundle_id, bundle_version, bundle_content_hash, "
    "target_type, target_id, actor_id, actor_type, operation, outcome, reason, "
    "occurred_at"
)

type RuleSetKey = tuple[str, str, str]
type Row = tuple[Any, ...]


class _WriteConflict(Exception):
    pass


class PostgresRuleBundleRepository:
    """Persist rule-bundle commands through explicit serialized transactions."""

    def __init__(self, connection: Connection[Any]) -> None:
        self._connection = connection

    def save_draft_atomic(
        self,
        bundle: RuleBundleVersion,
        drafted_event: RuleBundleLifecycleEvent,
        applied_audit: RuleBundleCommandAuditRecord,
        idempotent_audit: RuleBundleCommandAuditRecord,
    ) -> DraftWriteOutcome:
        scope = self._bundle_scope(bundle)
        reference = rule_bundle_ref(bundle)
        if (
            drafted_event.event_type is not RuleBundleEventType.DRAFTED
            or drafted_event.previous_bundle is not None
            or drafted_event.new_bundle != reference
            or self._event_scope(drafted_event) != scope
        ):
            raise ValueError("draft event must exactly identify the immutable bundle")
        validate_atomic_command_audit(
            applied_audit,
            scope,
            reference,
            RuleBundleCommandReason.DRAFT_APPLIED,
            drafted_event,
        )
        validate_atomic_command_audit(
            idempotent_audit,
            scope,
            reference,
            RuleBundleCommandReason.DRAFT_IDEMPOTENT,
            None,
        )
        payload = encode_bundle_payload(bundle)
        try:
            with self._connection.transaction():
                events, _ = self._lock_and_validate_scope(scope)
                existing = self._select_bundle(*scope, bundle.bundle_id, bundle.version)
                if existing is not None:
                    if existing != bundle:
                        raise _WriteConflict
                    matching_drafts = tuple(
                        event
                        for event in events
                        if event.event_type is RuleBundleEventType.DRAFTED
                        and event.new_bundle == reference
                    )
                    if len(matching_drafts) != 1:
                        raise RuleBundlePersistenceError
                    self._insert_audit(idempotent_audit)
                    return DraftWriteOutcome.IDEMPOTENT
                if len(events) >= MAX_PERSISTED_HISTORY:
                    raise RuleBundlePersistenceError
                expected_sequence = events[-1].sequence + 1 if events else 1
                if drafted_event.sequence != expected_sequence:
                    raise _WriteConflict
                try:
                    lifecycle = fold_rule_bundle_events(events + (drafted_event,))
                except InvalidLifecycleTransition as exc:
                    raise _WriteConflict from exc
                self._insert_bundle(bundle, reference, payload)
                self._insert_event(drafted_event)
                self._insert_audit(applied_audit)
                self._update_state(scope, drafted_event.sequence, lifecycle)
                return DraftWriteOutcome.APPLIED
        except _WriteConflict:
            return DraftWriteOutcome.CONFLICT
        except RuleBundlePersistenceError:
            raise
        except (psycopg.Error, TypeError, ValueError, IndexError) as exc:
            raise RuleBundlePersistenceError from exc

    def append_lifecycle_atomic(
        self,
        event: RuleBundleLifecycleEvent,
        applied_audit: RuleBundleCommandAuditRecord,
        idempotent_audit: RuleBundleCommandAuditRecord,
    ) -> LifecycleWriteOutcome:
        if event.event_type is RuleBundleEventType.DRAFTED:
            raise ValueError("DRAFTED must use save_draft_atomic")
        scope = self._event_scope(event)
        target = event.new_bundle or event.previous_bundle
        assert target is not None
        validate_atomic_command_audit(
            applied_audit,
            scope,
            target,
            self._applied_reason(event.event_type),
            event,
        )
        validate_atomic_command_audit(
            idempotent_audit,
            scope,
            target,
            self._idempotent_reason(event.event_type),
            None,
        )
        try:
            with self._connection.transaction():
                events, lifecycle = self._lock_and_validate_scope(scope)
                self._require_stored_references(scope, event)
                if any(stored.event_id == event.event_id for stored in events):
                    raise RuleBundlePersistenceError
                responsible = semantically_applied_lifecycle_transition(
                    event, lifecycle, events
                )
                if responsible is not None:
                    self._insert_audit(idempotent_audit)
                    return LifecycleWriteOutcome.IDEMPOTENT
                if len(events) >= MAX_PERSISTED_HISTORY:
                    raise RuleBundlePersistenceError
                expected_sequence = events[-1].sequence + 1 if events else 1
                if event.sequence != expected_sequence:
                    raise _WriteConflict
                try:
                    updated = fold_rule_bundle_events(events + (event,))
                except InvalidLifecycleTransition as exc:
                    raise _WriteConflict from exc
                self._insert_event(event)
                self._insert_audit(applied_audit)
                self._update_state(scope, event.sequence, updated)
                return LifecycleWriteOutcome.APPLIED
        except _WriteConflict:
            return LifecycleWriteOutcome.CONFLICT
        except RuleBundlePersistenceError:
            raise
        except (psycopg.Error, TypeError, ValueError, IndexError) as exc:
            raise RuleBundlePersistenceError from exc

    def append_command_audit(self, audit: RuleBundleCommandAuditRecord) -> None:
        if not isinstance(audit, RuleBundleCommandAuditRecord):
            raise ValueError("command audit must be typed")
        try:
            with self._connection.transaction():
                self._insert_audit(audit)
        except (psycopg.Error, TypeError, ValueError, IndexError) as exc:
            raise RuleBundlePersistenceError from exc

    def get_bundle(
        self,
        tenant_id: str,
        deployment_id: str,
        rule_set_id: str,
        bundle_id: str,
        version: str,
    ) -> RuleBundleVersion | None:
        try:
            return self._select_bundle(
                tenant_id, deployment_id, rule_set_id, bundle_id, version
            )
        except (psycopg.Error, TypeError, ValueError, IndexError) as exc:
            raise RuleBundlePersistenceError from exc

    def list_bundles(
        self,
        tenant_id: str,
        deployment_id: str,
        rule_set_id: str,
        *,
        limit: int,
    ) -> tuple[RuleBundleVersion, ...]:
        self._require_limit(limit, maximum=MAX_BUNDLE_QUERY)
        try:
            rows = self._connection.execute(
                f"SELECT {BUNDLE_COLUMNS} FROM rule_bundle_version "
                "WHERE tenant_id = %s AND deployment_id = %s AND rule_set_id = %s "
                "ORDER BY bundle_id, version LIMIT %s",
                (tenant_id, deployment_id, rule_set_id, limit),
            ).fetchall()
            return tuple(self._bundle_from_row(row) for row in rows)
        except (psycopg.Error, TypeError, ValueError, IndexError) as exc:
            raise RuleBundlePersistenceError from exc

    def list_lifecycle_events(
        self,
        tenant_id: str,
        deployment_id: str,
        rule_set_id: str,
        *,
        limit: int,
    ) -> tuple[RuleBundleLifecycleEvent, ...]:
        self._require_limit(limit, maximum=MAX_EVENT_QUERY)
        try:
            rows = self._connection.execute(
                f"SELECT {EVENT_COLUMNS} FROM rule_bundle_lifecycle_event "
                "WHERE tenant_id = %s AND deployment_id = %s AND rule_set_id = %s "
                "ORDER BY sequence LIMIT %s",
                (tenant_id, deployment_id, rule_set_id, limit),
            ).fetchall()
            return tuple(self._event_from_row(row) for row in rows)
        except (psycopg.Error, TypeError, ValueError, IndexError) as exc:
            raise RuleBundlePersistenceError from exc

    def get_lifecycle_snapshot(
        self,
        tenant_id: str,
        deployment_id: str,
        rule_set_id: str,
    ) -> tuple[tuple[RuleBundleLifecycleEvent, ...], RuleSetLifecycle]:
        scope = (tenant_id, deployment_id, rule_set_id)
        try:
            with self._connection.transaction():
                row = self._connection.execute(
                    f"SELECT {STATE_COLUMNS} FROM rule_bundle_lifecycle_state "
                    "WHERE tenant_id = %s AND deployment_id = %s "
                    "AND rule_set_id = %s FOR SHARE",
                    scope,
                ).fetchone()
                events = self._load_history(scope)
                lifecycle = fold_rule_bundle_events(events)
                if row is None:
                    if events:
                        raise RuleBundlePersistenceError
                else:
                    self._validate_state_row(scope, row, events, lifecycle)
                return events, lifecycle
        except RuleBundlePersistenceError:
            raise
        except (
            psycopg.Error,
            InvalidLifecycleTransition,
            TypeError,
            ValueError,
            IndexError,
        ) as exc:
            raise RuleBundlePersistenceError from exc

    def list_command_audits(
        self,
        tenant_id: str,
        deployment_id: str,
        rule_set_id: str,
        *,
        limit: int,
    ) -> tuple[RuleBundleCommandAuditRecord, ...]:
        self._require_limit(limit, maximum=MAX_AUDIT_QUERY)
        try:
            rows = self._connection.execute(
                f"SELECT {AUDIT_COLUMNS} FROM rule_bundle_command_audit "
                "WHERE tenant_id = %s AND deployment_id = %s AND rule_set_id = %s "
                "ORDER BY occurred_at, command_event_id LIMIT %s",
                (tenant_id, deployment_id, rule_set_id, limit),
            ).fetchall()
            return tuple(self._audit_from_row(row) for row in rows)
        except (psycopg.Error, TypeError, ValueError, IndexError) as exc:
            raise RuleBundlePersistenceError from exc

    def _lock_and_validate_scope(
        self, scope: RuleSetKey
    ) -> tuple[tuple[RuleBundleLifecycleEvent, ...], RuleSetLifecycle]:
        self._connection.execute(
            "INSERT INTO rule_bundle_lifecycle_state "
            "(tenant_id, deployment_id, rule_set_id, last_sequence) "
            "VALUES (%s, %s, %s, 0) ON CONFLICT DO NOTHING",
            scope,
        )
        row = self._connection.execute(
            f"SELECT {STATE_COLUMNS} FROM rule_bundle_lifecycle_state "
            "WHERE tenant_id = %s AND deployment_id = %s AND rule_set_id = %s "
            "FOR UPDATE",
            scope,
        ).fetchone()
        if row is None:
            raise RuleBundlePersistenceError
        events = self._load_history(scope)
        try:
            lifecycle = fold_rule_bundle_events(events)
        except (InvalidLifecycleTransition, ValueError) as exc:
            raise RuleBundlePersistenceError from exc
        self._validate_state_row(scope, row, events, lifecycle)
        return events, lifecycle

    def _load_history(self, scope: RuleSetKey) -> tuple[RuleBundleLifecycleEvent, ...]:
        rows = self._connection.execute(
            f"SELECT {EVENT_COLUMNS} FROM rule_bundle_lifecycle_event "
            "WHERE tenant_id = %s AND deployment_id = %s AND rule_set_id = %s "
            "ORDER BY sequence LIMIT %s",
            (*scope, MAX_PERSISTED_HISTORY + 1),
        ).fetchall()
        if len(rows) > MAX_PERSISTED_HISTORY:
            raise RuleBundlePersistenceError
        return tuple(self._event_from_row(row) for row in rows)

    def _select_bundle(
        self,
        tenant_id: str,
        deployment_id: str,
        rule_set_id: str,
        bundle_id: str,
        version: str,
    ) -> RuleBundleVersion | None:
        row = self._connection.execute(
            f"SELECT {BUNDLE_COLUMNS} FROM rule_bundle_version "
            "WHERE tenant_id = %s AND deployment_id = %s AND rule_set_id = %s "
            "AND bundle_id = %s AND version = %s LIMIT 1",
            (tenant_id, deployment_id, rule_set_id, bundle_id, version),
        ).fetchone()
        return self._bundle_from_row(row) if row is not None else None

    @staticmethod
    def _bundle_from_row(row: Row) -> RuleBundleVersion:
        if len(row) != 7:
            raise ValueError("malformed rule bundle row")
        return decode_bundle_payload(
            row[6],
            tenant_id=_string(row[0], "tenant_id"),
            deployment_id=_string(row[1], "deployment_id"),
            rule_set_id=_string(row[2], "rule_set_id"),
            bundle_id=_string(row[3], "bundle_id"),
            version=_string(row[4], "version"),
            content_hash=_string(row[5], "content_hash"),
        )

    @staticmethod
    def _event_from_row(row: Row) -> RuleBundleLifecycleEvent:
        if len(row) != 23:
            raise ValueError("malformed lifecycle event row")
        scope = (
            _string(row[0], "tenant_id"),
            _string(row[1], "deployment_id"),
            _string(row[2], "rule_set_id"),
        )
        previous = _scoped_reference(row[6:12], scope, "previous")
        new = _scoped_reference(row[12:18], scope, "new")
        impact = None if row[22] is None else decode_rescreen_impact(row[22])
        return RuleBundleLifecycleEvent(
            sequence=_integer(row[3], "sequence"),
            event_id=_string(row[4], "event_id"),
            tenant_id=scope[0],
            deployment_id=scope[1],
            rule_set_id=scope[2],
            event_type=RuleBundleEventType(_string(row[5], "event_type")),
            previous_bundle=previous,
            new_bundle=new,
            reason=_string(row[18], "reason"),
            actor_id=_string(row[19], "actor_id"),
            actor_type=LifecycleActorType(_string(row[20], "actor_type")),
            occurred_at=_datetime(row[21], "occurred_at"),
            rescreen_impact=impact,
        )

    @staticmethod
    def _audit_from_row(row: Row) -> RuleBundleCommandAuditRecord:
        if len(row) != 17:
            raise ValueError("malformed command audit row")
        lifecycle_event_id = (
            None if row[2] is None else _string(row[2], "lifecycle_event_id")
        )
        return RuleBundleCommandAuditRecord(
            command_event_id=_string(row[0], "command_event_id"),
            authorization_event_id=_string(row[1], "authorization_event_id"),
            lifecycle_event_id=lifecycle_event_id,
            tenant_id=_string(row[3], "tenant_id"),
            deployment_id=_string(row[4], "deployment_id"),
            rule_set_id=_string(row[5], "rule_set_id"),
            bundle_id=_string(row[6], "bundle_id"),
            bundle_version=_string(row[7], "bundle_version"),
            bundle_content_hash=_string(row[8], "bundle_content_hash"),
            target_type=_string(row[9], "target_type"),
            target_id=_string(row[10], "target_id"),
            actor_id=_string(row[11], "actor_id"),
            actor_type=LifecycleActorType(_string(row[12], "actor_type")),
            operation=_string(row[13], "operation"),
            outcome=RuleBundleCommandOutcome(_string(row[14], "outcome")),
            reason=RuleBundleCommandReason(_string(row[15], "reason")),
            occurred_at=_datetime(row[16], "occurred_at"),
        )

    def _insert_bundle(
        self,
        bundle: RuleBundleVersion,
        reference: RuleBundleRef,
        payload: dict[str, Any],
    ) -> None:
        self._connection.execute(
            "INSERT INTO rule_bundle_version "
            "(tenant_id, deployment_id, rule_set_id, bundle_id, version, "
            "content_hash, payload) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                bundle.tenant_id,
                bundle.deployment_id,
                bundle.rule_set_id,
                bundle.bundle_id,
                bundle.version,
                reference.content_hash,
                Jsonb(payload),
            ),
        )

    def _insert_event(self, event: RuleBundleLifecycleEvent) -> None:
        previous = _reference_values(self._event_scope(event), event.previous_bundle)
        new = _reference_values(self._event_scope(event), event.new_bundle)
        impact = encode_rescreen_impact(event.rescreen_impact)
        self._connection.execute(
            "INSERT INTO rule_bundle_lifecycle_event ("
            + EVENT_COLUMNS
            + ") VALUES ("
            + ", ".join(["%s"] * 23)
            + ")",
            (
                event.tenant_id,
                event.deployment_id,
                event.rule_set_id,
                event.sequence,
                event.event_id,
                event.event_type.value,
                *previous,
                *new,
                event.reason,
                event.actor_id,
                event.actor_type.value,
                event.occurred_at,
                Jsonb(impact) if impact is not None else None,
            ),
        )

    def _insert_audit(self, audit: RuleBundleCommandAuditRecord) -> None:
        self._connection.execute(
            "INSERT INTO rule_bundle_command_audit ("
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
                audit.rule_set_id,
                audit.bundle_id,
                audit.bundle_version,
                audit.bundle_content_hash,
                audit.target_type,
                audit.target_id,
                audit.actor_id,
                audit.actor_type.value,
                audit.operation,
                audit.outcome.value,
                audit.reason.value,
                audit.occurred_at,
            ),
        )

    def _update_state(
        self, scope: RuleSetKey, sequence: int, lifecycle: RuleSetLifecycle
    ) -> None:
        active = _reference_values(scope, lifecycle.active_bundle)
        self._connection.execute(
            "UPDATE rule_bundle_lifecycle_state SET last_sequence = %s, "
            "active_tenant_id = %s, active_deployment_id = %s, "
            "active_rule_set_id = %s, active_bundle_id = %s, active_version = %s, "
            "active_content_hash = %s, updated_at = CURRENT_TIMESTAMP "
            "WHERE tenant_id = %s AND deployment_id = %s AND rule_set_id = %s",
            (sequence, *active, *scope),
        )

    def _require_stored_references(
        self, scope: RuleSetKey, event: RuleBundleLifecycleEvent
    ) -> None:
        for reference in (event.previous_bundle, event.new_bundle):
            if reference is None:
                continue
            stored = self._select_bundle(*scope, reference.bundle_id, reference.version)
            if stored is None or rule_bundle_ref(stored) != reference:
                raise RuleBundlePersistenceError

    @staticmethod
    def _validate_state_row(
        scope: RuleSetKey,
        row: Row,
        events: tuple[RuleBundleLifecycleEvent, ...],
        lifecycle: RuleSetLifecycle,
    ) -> None:
        if len(row) != 7:
            raise RuleBundlePersistenceError
        last_sequence = _integer(row[0], "last_sequence", allow_zero=True)
        active = _scoped_reference(row[1:7], scope, "active")
        expected_sequence = events[-1].sequence if events else 0
        if last_sequence != expected_sequence or active != lifecycle.active_bundle:
            raise RuleBundlePersistenceError

    @staticmethod
    def _require_limit(limit: int, *, maximum: int) -> None:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= maximum
        ):
            raise ValueError(f"limit must be an integer between 1 and {maximum}")

    @staticmethod
    def _bundle_scope(bundle: RuleBundleVersion) -> RuleSetKey:
        return (bundle.tenant_id, bundle.deployment_id, bundle.rule_set_id)

    @staticmethod
    def _event_scope(event: RuleBundleLifecycleEvent) -> RuleSetKey:
        return (event.tenant_id, event.deployment_id, event.rule_set_id)

    @staticmethod
    def _applied_reason(event_type: RuleBundleEventType) -> RuleBundleCommandReason:
        return {
            RuleBundleEventType.APPROVED: RuleBundleCommandReason.APPROVED,
            RuleBundleEventType.ACTIVATED: RuleBundleCommandReason.ACTIVATED,
            RuleBundleEventType.RETIRED: RuleBundleCommandReason.RETIRED,
            RuleBundleEventType.ROLLED_BACK: RuleBundleCommandReason.ROLLED_BACK,
        }[event_type]

    @staticmethod
    def _idempotent_reason(event_type: RuleBundleEventType) -> RuleBundleCommandReason:
        return {
            RuleBundleEventType.APPROVED: RuleBundleCommandReason.APPROVE_IDEMPOTENT,
            RuleBundleEventType.ACTIVATED: RuleBundleCommandReason.ACTIVATE_IDEMPOTENT,
            RuleBundleEventType.RETIRED: RuleBundleCommandReason.RETIRE_IDEMPOTENT,
            RuleBundleEventType.ROLLED_BACK: RuleBundleCommandReason.ROLLBACK_IDEMPOTENT,
        }[event_type]


def _reference_values(
    scope: RuleSetKey, reference: RuleBundleRef | None
) -> tuple[str | None, ...]:
    if reference is None:
        return (None, None, None, None, None, None)
    return (*scope, reference.bundle_id, reference.version, reference.content_hash)


def _scoped_reference(
    values: Row, scope: RuleSetKey, field: str
) -> RuleBundleRef | None:
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise ValueError(f"partial {field} bundle reference")
    stored_scope = tuple(_string(value, field) for value in values[:3])
    if stored_scope != scope:
        raise ValueError(f"cross-scope {field} bundle reference")
    return RuleBundleRef(
        _string(values[3], field),
        _string(values[4], field),
        _string(values[5], field),
    )


def _string(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def _integer(value: object, field: str, *, allow_zero: bool = False) -> int:
    minimum = 0 if allow_zero else 1
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{field} must be an integer at least {minimum}")
    return value


def _datetime(value: object, field: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{field} must be timezone-aware")
    return value
