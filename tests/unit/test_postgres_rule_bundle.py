"""PostgreSQL rule-bundle SQL, transaction, mapping, and corruption tests."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any, Self, cast

import psycopg
import pytest
from psycopg import Connection
from psycopg.types.json import Jsonb

import tradesieve.adapters.postgres_rule_bundle as postgres_rule_bundle
from tradesieve.adapters.postgres_rule_bundle import (
    MAX_AUDIT_QUERY,
    MAX_BUNDLE_QUERY,
    MAX_EVENT_QUERY,
    PostgresRuleBundleRepository,
)
from tradesieve.domain.rule_bundle import (
    DraftWriteOutcome,
    EffectiveWindow,
    InternalPolicyCitation,
    LegalNexusPresenceSpec,
    LifecycleActorType,
    LifecycleWriteOutcome,
    RuleAction,
    RuleActivity,
    RuleBundleCommandAuditRecord,
    RuleBundleCommandOutcome,
    RuleBundleCommandReason,
    RuleBundleEventType,
    RuleBundleLifecycleEvent,
    RuleBundleVersion,
    RuleEvaluatorKind,
    RuleScope,
    RuleVersion,
    calculate_rescreen_impact,
    fold_rule_bundle_events,
    rule_bundle_ref,
)
from tradesieve.ports.rule_bundle import RuleBundlePersistenceError

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


class FakeResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows


class FakeTransaction:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.snapshot: (
            tuple[
                dict[tuple[str, ...], tuple[Any, ...]],
                dict[tuple[str, str, str], tuple[Any, ...]],
                list[tuple[Any, ...]],
                list[tuple[Any, ...]],
            ]
            | None
        ) = None

    def __enter__(self) -> Self:
        self.connection.transaction_entries += 1
        self.snapshot = copy.deepcopy(
            (
                self.connection.bundles,
                self.connection.states,
                self.connection.events,
                self.connection.audits,
            )
        )
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
        assert self.snapshot is not None
        (
            self.connection.bundles,
            self.connection.states,
            self.connection.events,
            self.connection.audits,
        ) = copy.deepcopy(self.snapshot)
        self.connection.rollbacks += 1


class FakeConnection:
    def __init__(self) -> None:
        self.bundles: dict[tuple[str, ...], tuple[Any, ...]] = {}
        self.states: dict[tuple[str, str, str], tuple[Any, ...]] = {}
        self.events: list[tuple[Any, ...]] = []
        self.audits: list[tuple[Any, ...]] = []
        self.executed: list[tuple[str, object | None]] = []
        self.transaction_entries = 0
        self.commits = 0
        self.rollbacks = 0
        self.fail_once_query: str | None = None
        self.drop_state_insert = False

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)

    def execute(self, query: str, params: object | None = None) -> FakeResult:
        self.executed.append((query, params))
        if self.fail_once_query is not None and self.fail_once_query in query:
            self.fail_once_query = None
            raise psycopg.OperationalError("synthetic database failure")
        values = cast(tuple[Any, ...], params or ())
        if query.startswith("INSERT INTO rule_bundle_lifecycle_state"):
            scope = cast(tuple[str, str, str], values[:3])
            if not self.drop_state_insert:
                self.states.setdefault(scope, (0, None, None, None, None, None, None))
            return FakeResult([])
        if "FROM rule_bundle_lifecycle_state" in query:
            return FakeResult(
                [] if values[:3] not in self.states else [self.states[values[:3]]]
            )
        if query.startswith("UPDATE rule_bundle_lifecycle_state"):
            scope = cast(tuple[str, str, str], values[7:10])
            self.states[scope] = values[:7]
            return FakeResult([])
        if query.startswith("INSERT INTO rule_bundle_version"):
            key = cast(tuple[str, ...], values[:5])
            if key in self.bundles:
                raise psycopg.errors.UniqueViolation("duplicate bundle")
            payload = cast(Jsonb, values[6]).obj
            self.bundles[key] = (*values[:6], payload)
            return FakeResult([])
        if "FROM rule_bundle_version" in query:
            if "AND bundle_id = %s" in query:
                key = cast(tuple[str, ...], values[:5])
                row = self.bundles.get(key)
                return FakeResult([] if row is None else [row])
            scope = values[:3]
            rows = sorted(
                (row for key, row in self.bundles.items() if key[:3] == scope),
                key=lambda row: (row[3], row[4]),
            )
            return FakeResult(rows[: int(values[3])])
        if query.startswith("INSERT INTO rule_bundle_lifecycle_event"):
            if any(row[4] == values[4] for row in self.events):
                raise psycopg.errors.UniqueViolation("duplicate event")
            impact = None if values[22] is None else cast(Jsonb, values[22]).obj
            self.events.append((*values[:22], impact))
            return FakeResult([])
        if "FROM rule_bundle_lifecycle_event" in query:
            scope = values[:3]
            rows = sorted(
                (row for row in self.events if row[:3] == scope), key=lambda row: row[3]
            )
            return FakeResult(rows[: int(values[3])])
        if query.startswith("INSERT INTO rule_bundle_command_audit"):
            if any(row[0] == values[0] for row in self.audits):
                raise psycopg.errors.UniqueViolation("duplicate audit")
            self.audits.append(values)
            return FakeResult([])
        if "FROM rule_bundle_command_audit" in query:
            scope = values[:3]
            rows = sorted(
                (row for row in self.audits if row[3:6] == scope),
                key=lambda row: (row[16], row[0]),
            )
            return FakeResult(rows[: int(values[3])])
        raise AssertionError(f"unexpected SQL: {query}")


def repository(
    connection: FakeConnection,
) -> PostgresRuleBundleRepository:
    return PostgresRuleBundleRepository(cast(Connection[Any], connection))


def bundle(
    version: str = "1.0.0", *, note: str = "Private bundle note"
) -> RuleBundleVersion:
    rule = RuleVersion(
        rule_id="rule-1",
        version=version,
        kind=RuleEvaluatorKind.LEGAL_NEXUS_PRESENCE,
        owner="rule-owner",
        effective_window=EffectiveWindow(
            NOW - timedelta(days=1), NOW + timedelta(days=1)
        ),
        scope=RuleScope((RuleAction.QUOTE_RELEASE,), (RuleActivity.EXPORT,)),
        evaluator=LegalNexusPresenceSpec(),
        citations=(
            InternalPolicyCitation(
                "citation-1", "policy-1", "1.0.0", "section-1", "Private text"
            ),
        ),
        internal_notes="Private rule note",
    )
    return RuleBundleVersion(
        "tenant-1",
        "demo",
        "ruleset-1",
        "bundle-1",
        version,
        "bundle-owner",
        EffectiveWindow(NOW - timedelta(days=1), NOW + timedelta(days=1)),
        "author-1",
        (rule,),
        note,
    )


def command_audits(
    event: RuleBundleLifecycleEvent,
    applied_reason: RuleBundleCommandReason,
    idempotent_reason: RuleBundleCommandReason,
    prefix: str,
) -> tuple[RuleBundleCommandAuditRecord, RuleBundleCommandAuditRecord]:
    reference = event.new_bundle or event.previous_bundle
    assert reference is not None
    applied = RuleBundleCommandAuditRecord(
        f"{prefix}-applied",
        f"{prefix}-authorization",
        event.event_id,
        event.tenant_id,
        event.deployment_id,
        event.rule_set_id,
        reference.bundle_id,
        reference.version,
        reference.content_hash,
        "rule_bundle",
        "target",
        event.actor_id,
        event.actor_type,
        "POLICY_WRITE",
        RuleBundleCommandOutcome.SUCCESS,
        applied_reason,
        event.occurred_at,
    )
    return (
        applied,
        RuleBundleCommandAuditRecord(
            f"{prefix}-idempotent",
            f"{prefix}-authorization",
            None,
            event.tenant_id,
            event.deployment_id,
            event.rule_set_id,
            reference.bundle_id,
            reference.version,
            reference.content_hash,
            "rule_bundle",
            "target",
            "retry-actor",
            LifecycleActorType.HUMAN,
            "POLICY_WRITE",
            RuleBundleCommandOutcome.SUCCESS,
            idempotent_reason,
            NOW + timedelta(seconds=1),
        ),
    )


def drafted(
    item: RuleBundleVersion, sequence: int = 1, prefix: str = "draft"
) -> RuleBundleLifecycleEvent:
    return RuleBundleLifecycleEvent(
        sequence,
        f"{prefix}-event",
        item.tenant_id,
        item.deployment_id,
        item.rule_set_id,
        RuleBundleEventType.DRAFTED,
        None,
        rule_bundle_ref(item),
        "Immutable draft.",
        "author-1",
        LifecycleActorType.HUMAN,
        NOW,
    )


def save(
    repo: PostgresRuleBundleRepository,
    item: RuleBundleVersion,
    sequence: int,
    prefix: str,
) -> DraftWriteOutcome:
    event = drafted(item, sequence, prefix)
    applied, idempotent = command_audits(
        event,
        RuleBundleCommandReason.DRAFT_APPLIED,
        RuleBundleCommandReason.DRAFT_IDEMPOTENT,
        prefix,
    )
    return repo.save_draft_atomic(item, event, applied, idempotent)


def lifecycle_event(
    event_type: RuleBundleEventType,
    sequence: int,
    previous: RuleBundleVersion | None,
    new: RuleBundleVersion | None,
    *,
    prefix: str,
    reason: str,
) -> RuleBundleLifecycleEvent:
    impact = (
        None
        if event_type is RuleBundleEventType.APPROVED
        else calculate_rescreen_impact(previous, new)
    )
    return RuleBundleLifecycleEvent(
        sequence,
        f"{prefix}-event",
        "tenant-1",
        "demo",
        "ruleset-1",
        event_type,
        rule_bundle_ref(previous) if previous is not None else None,
        rule_bundle_ref(new) if new is not None else None,
        reason,
        "approver-1",
        LifecycleActorType.HUMAN,
        NOW,
        impact,
    )


def append(
    repo: PostgresRuleBundleRepository,
    event: RuleBundleLifecycleEvent,
    prefix: str,
) -> LifecycleWriteOutcome:
    reasons = {
        RuleBundleEventType.APPROVED: (
            RuleBundleCommandReason.APPROVED,
            RuleBundleCommandReason.APPROVE_IDEMPOTENT,
        ),
        RuleBundleEventType.ACTIVATED: (
            RuleBundleCommandReason.ACTIVATED,
            RuleBundleCommandReason.ACTIVATE_IDEMPOTENT,
        ),
        RuleBundleEventType.RETIRED: (
            RuleBundleCommandReason.RETIRED,
            RuleBundleCommandReason.RETIRE_IDEMPOTENT,
        ),
        RuleBundleEventType.ROLLED_BACK: (
            RuleBundleCommandReason.ROLLED_BACK,
            RuleBundleCommandReason.ROLLBACK_IDEMPOTENT,
        ),
    }[event.event_type]
    applied, idempotent = command_audits(event, *reasons, prefix)
    return repo.append_lifecycle_atomic(event, applied, idempotent)


def test_save_draft_uses_locked_transaction_and_exact_idempotence_or_conflict() -> None:
    connection = FakeConnection()
    repo = repository(connection)
    item = bundle()
    assert save(repo, item, 1, "draft-a") is DraftWriteOutcome.APPLIED
    assert connection.transaction_entries == 1
    assert connection.commits == 1
    queries = [query for query, _ in connection.executed]
    assert "ON CONFLICT DO NOTHING" in queries[0]
    assert queries[1].endswith("FOR UPDATE")
    assert "ORDER BY sequence LIMIT %s" in queries[2]
    assert queries[-1].startswith("UPDATE rule_bundle_lifecycle_state")
    insert_params = cast(tuple[Any, ...], connection.executed[4][1])
    assert cast(Jsonb, insert_params[-1]).obj["internal_notes"] == (
        "Private bundle note"
    )

    assert save(repo, item, 1, "draft-b") is DraftWriteOutcome.IDEMPOTENT
    assert len(connection.events) == 1
    assert len(connection.audits) == 2

    conflict = bundle(note="Different immutable content")
    assert save(repo, conflict, 2, "draft-c") is DraftWriteOutcome.CONFLICT
    assert connection.rollbacks == 1
    assert len(connection.bundles) == 1
    assert len(connection.events) == 1
    assert len(connection.audits) == 2


def test_draft_rejects_invalid_input_capacity_stale_sequence_and_corruption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = bundle()
    draft = drafted(item)
    applied, idempotent = command_audits(
        draft,
        RuleBundleCommandReason.DRAFT_APPLIED,
        RuleBundleCommandReason.DRAFT_IDEMPOTENT,
        "draft",
    )
    invalid = lifecycle_event(
        RuleBundleEventType.APPROVED,
        1,
        None,
        item,
        prefix="invalid",
        reason="Reviewed.",
    )
    with pytest.raises(ValueError, match="draft event"):
        repository(FakeConnection()).save_draft_atomic(
            item, invalid, applied, idempotent
        )

    connection = FakeConnection()
    repo = repository(connection)
    connection.fail_once_query = "INSERT INTO rule_bundle_lifecycle_event"
    with pytest.raises(RuleBundlePersistenceError):
        repo.save_draft_atomic(item, draft, applied, idempotent)
    assert connection.bundles == {}
    assert connection.events == []
    assert connection.audits == []

    assert save(repo, item, 1, "stored") is DraftWriteOutcome.APPLIED
    inconsistent = CappedRepository(cast(Connection[Any], connection), ())
    with pytest.raises(RuleBundlePersistenceError):
        save(inconsistent, item, 1, "retry-inconsistent")

    second = bundle("1.1.0")
    monkeypatch.setattr(postgres_rule_bundle, "MAX_PERSISTED_HISTORY", 1)
    capped = CappedRepository(cast(Connection[Any], connection), (draft,))
    with pytest.raises(RuleBundlePersistenceError):
        save(capped, second, 2, "capped")

    monkeypatch.setattr(postgres_rule_bundle, "MAX_PERSISTED_HISTORY", 4096)
    assert save(repo, second, 9, "stale") is DraftWriteOutcome.CONFLICT

    missing_bundle = CappedRepository(cast(Connection[Any], FakeConnection()), (draft,))
    assert (
        save(missing_bundle, item, 2, "duplicate-draft") is DraftWriteOutcome.CONFLICT
    )


def test_lifecycle_applies_idempotently_conflicts_and_tracks_active_rollback() -> None:
    connection = FakeConnection()
    repo = repository(connection)
    first = bundle()
    assert save(repo, first, 1, "draft-first") is DraftWriteOutcome.APPLIED
    approve_first = lifecycle_event(
        RuleBundleEventType.APPROVED,
        2,
        None,
        first,
        prefix="approve-first",
        reason="Reviewed first.",
    )
    assert append(repo, approve_first, "approve-first") is LifecycleWriteOutcome.APPLIED
    assert (
        append(
            repo,
            lifecycle_event(
                RuleBundleEventType.APPROVED,
                3,
                None,
                first,
                prefix="approve-first-retry",
                reason="Reviewed first.",
            ),
            "approve-first-retry",
        )
        is LifecycleWriteOutcome.IDEMPOTENT
    )
    assert (
        append(
            repo,
            lifecycle_event(
                RuleBundleEventType.APPROVED,
                3,
                None,
                first,
                prefix="approve-first-changed",
                reason="Different review intent.",
            ),
            "approve-first-changed",
        )
        is LifecycleWriteOutcome.CONFLICT
    )
    activate_first = lifecycle_event(
        RuleBundleEventType.ACTIVATED,
        3,
        None,
        first,
        prefix="activate-first",
        reason="Activate first.",
    )
    assert (
        append(repo, activate_first, "activate-first") is LifecycleWriteOutcome.APPLIED
    )

    second = bundle("1.1.0", note="Second private bundle")
    assert save(repo, second, 4, "draft-second") is DraftWriteOutcome.APPLIED
    approve_second = lifecycle_event(
        RuleBundleEventType.APPROVED,
        5,
        None,
        second,
        prefix="approve-second",
        reason="Reviewed second.",
    )
    assert (
        append(repo, approve_second, "approve-second") is LifecycleWriteOutcome.APPLIED
    )
    activate_second = lifecycle_event(
        RuleBundleEventType.ACTIVATED,
        6,
        first,
        second,
        prefix="activate-second",
        reason="Activate second.",
    )
    assert (
        append(repo, activate_second, "activate-second")
        is LifecycleWriteOutcome.APPLIED
    )
    rollback = lifecycle_event(
        RuleBundleEventType.ROLLED_BACK,
        7,
        second,
        first,
        prefix="rollback",
        reason="Rollback first.",
    )
    assert append(repo, rollback, "rollback") is LifecycleWriteOutcome.APPLIED
    retry = lifecycle_event(
        RuleBundleEventType.ROLLED_BACK,
        8,
        second,
        first,
        prefix="rollback-retry",
        reason="Rollback first.",
    )
    assert append(repo, retry, "rollback-retry") is LifecycleWriteOutcome.IDEMPOTENT
    state = connection.states[("tenant-1", "demo", "ruleset-1")]
    assert state[0] == 7
    assert state[1:] == (
        "tenant-1",
        "demo",
        "ruleset-1",
        "bundle-1",
        "1.0.0",
        rule_bundle_ref(first).content_hash,
    )
    assert fold_rule_bundle_events(
        repo.list_lifecycle_events(
            "tenant-1", "demo", "ruleset-1", limit=MAX_EVENT_QUERY
        )
    ).active_bundle == rule_bundle_ref(first)


def test_lifecycle_rejects_draft_entrypoint_and_stale_sequence() -> None:
    connection = FakeConnection()
    repo = repository(connection)
    item = bundle()
    draft = drafted(item)
    applied, idempotent = command_audits(
        draft,
        RuleBundleCommandReason.DRAFT_APPLIED,
        RuleBundleCommandReason.DRAFT_IDEMPOTENT,
        "draft",
    )
    with pytest.raises(ValueError, match="save_draft_atomic"):
        repo.append_lifecycle_atomic(draft, applied, idempotent)

    assert save(repo, item, 1, "stored") is DraftWriteOutcome.APPLIED
    stale = lifecycle_event(
        RuleBundleEventType.APPROVED,
        3,
        None,
        item,
        prefix="stale",
        reason="Reviewed.",
    )
    assert append(repo, stale, "stale") is LifecycleWriteOutcome.CONFLICT


def test_reads_are_bounded_ordered_strict_and_translate_corrupt_rows() -> None:
    connection = FakeConnection()
    repo = repository(connection)
    item = bundle()
    save(repo, item, 1, "draft")
    assert repo.get_bundle("tenant-1", "demo", "ruleset-1", "bundle-1", "1.0.0") == item
    assert repo.get_bundle("tenant-1", "demo", "ruleset-1", "missing", "1.0.0") is None
    assert repo.list_bundles("tenant-1", "demo", "ruleset-1", limit=1) == (item,)
    assert (
        len(repo.list_lifecycle_events("tenant-1", "demo", "ruleset-1", limit=1)) == 1
    )
    assert len(repo.list_command_audits("tenant-1", "demo", "ruleset-1", limit=1)) == 1
    for method, maximum in (
        (repo.list_bundles, MAX_BUNDLE_QUERY),
        (repo.list_lifecycle_events, MAX_EVENT_QUERY),
        (repo.list_command_audits, MAX_AUDIT_QUERY),
    ):
        for invalid in (0, True, maximum + 1):
            with pytest.raises(ValueError):
                method("tenant-1", "demo", "ruleset-1", limit=invalid)

    key = ("tenant-1", "demo", "ruleset-1", "bundle-1", "1.0.0")
    stored = connection.bundles[key]
    malformed = copy.deepcopy(stored[6])
    malformed["unknown"] = True
    connection.bundles[key] = (*stored[:6], malformed)
    with pytest.raises(RuleBundlePersistenceError):
        repo.get_bundle(*key)
    connection.bundles[key] = (*stored[:6], '{"schema_version":')
    with pytest.raises(RuleBundlePersistenceError):
        repo.get_bundle(*key)

    connection.fail_once_query = "FROM rule_bundle_command_audit"
    with pytest.raises(RuleBundlePersistenceError):
        repo.list_command_audits("tenant-1", "demo", "ruleset-1", limit=1)

    connection.fail_once_query = "FROM rule_bundle_version"
    with pytest.raises(RuleBundlePersistenceError):
        repo.list_bundles("tenant-1", "demo", "ruleset-1", limit=1)
    connection.fail_once_query = "FROM rule_bundle_lifecycle_event"
    with pytest.raises(RuleBundlePersistenceError):
        repo.list_lifecycle_events("tenant-1", "demo", "ruleset-1", limit=1)


def test_lifecycle_snapshot_is_transactional_bounded_and_state_validated() -> None:
    connection = FakeConnection()
    repo = repository(connection)
    events, lifecycle = repo.get_lifecycle_snapshot("tenant-1", "demo", "ruleset-1")
    assert events == ()
    assert lifecycle.active_bundle is None
    assert connection.executed[0][0].endswith("FOR SHARE")

    item = bundle()
    assert save(repo, item, 1, "draft") is DraftWriteOutcome.APPLIED
    events, lifecycle = repo.get_lifecycle_snapshot("tenant-1", "demo", "ruleset-1")
    assert len(events) == 1
    assert lifecycle.state_for(rule_bundle_ref(item)) is not None

    connection.states.clear()
    with pytest.raises(RuleBundlePersistenceError):
        repo.get_lifecycle_snapshot("tenant-1", "demo", "ruleset-1")
    connection.states[("tenant-1", "demo", "ruleset-1")] = (
        1,
        None,
        None,
        None,
        None,
        None,
        None,
    )
    connection.events.append(copy.deepcopy(connection.events[0]))
    with pytest.raises(RuleBundlePersistenceError):
        repo.get_lifecycle_snapshot("tenant-1", "demo", "ruleset-1")

    connection.events.pop()
    connection.fail_once_query = "FOR SHARE"
    with pytest.raises(RuleBundlePersistenceError):
        repo.get_lifecycle_snapshot("tenant-1", "demo", "ruleset-1")


def test_row_decoders_reject_malformed_shapes_scalars_and_references() -> None:
    connection = FakeConnection()
    repo = repository(connection)
    item = bundle()
    assert save(repo, item, 1, "draft") is DraftWriteOutcome.APPLIED
    scope = ("tenant-1", "demo", "ruleset-1")
    key = (*scope, "bundle-1", "1.0.0")

    bundle_row = connection.bundles[key]
    connection.bundles[key] = bundle_row[:-1]
    with pytest.raises(RuleBundlePersistenceError):
        repo.get_bundle(*key)
    connection.bundles[key] = bundle_row

    event_row = connection.events[0]
    invalid_events: list[tuple[Any, ...]] = [
        event_row[:-1],
        (*event_row[:6], "tenant-1", *event_row[7:]),
        (*event_row[:12], "tenant-other", *event_row[13:]),
        (*event_row[:4], 7, *event_row[5:]),
        (*event_row[:3], True, *event_row[4:]),
        (*event_row[:21], NOW.replace(tzinfo=None), event_row[22]),
    ]
    for invalid_event in invalid_events:
        connection.events = [invalid_event]
        with pytest.raises(RuleBundlePersistenceError):
            repo.list_lifecycle_events(*scope, limit=1)
    connection.events = [event_row]

    audit_row = connection.audits[0]
    connection.audits = [(*audit_row, "unexpected")]
    with pytest.raises(RuleBundlePersistenceError):
        repo.list_command_audits(*scope, limit=1)


def test_state_event_id_reference_and_database_failures_roll_back() -> None:
    connection = FakeConnection()
    repo = repository(connection)
    item = bundle()
    save(repo, item, 1, "draft")
    approve = lifecycle_event(
        RuleBundleEventType.APPROVED,
        2,
        None,
        item,
        prefix="approve",
        reason="Reviewed.",
    )
    applied, idempotent = command_audits(
        approve,
        RuleBundleCommandReason.APPROVED,
        RuleBundleCommandReason.APPROVE_IDEMPOTENT,
        "approve",
    )

    connection.states[("tenant-1", "demo", "ruleset-1")] = (
        99,
        None,
        None,
        None,
        None,
        None,
        None,
    )
    with pytest.raises(RuleBundlePersistenceError):
        repo.append_lifecycle_atomic(approve, applied, idempotent)
    assert connection.rollbacks == 1
    connection.states[("tenant-1", "demo", "ruleset-1")] = (
        1,
        None,
        None,
        None,
        None,
        None,
        None,
    )

    collision = RuleBundleLifecycleEvent(
        2,
        connection.events[0][4],
        approve.tenant_id,
        approve.deployment_id,
        approve.rule_set_id,
        approve.event_type,
        approve.previous_bundle,
        approve.new_bundle,
        approve.reason,
        approve.actor_id,
        approve.actor_type,
        approve.occurred_at,
    )
    collision_applied, collision_idempotent = command_audits(
        collision,
        RuleBundleCommandReason.APPROVED,
        RuleBundleCommandReason.APPROVE_IDEMPOTENT,
        "collision",
    )
    with pytest.raises(RuleBundlePersistenceError):
        repo.append_lifecycle_atomic(collision, collision_applied, collision_idempotent)

    connection.fail_once_query = "INSERT INTO rule_bundle_lifecycle_event"
    before = copy.deepcopy((connection.events, connection.audits, connection.states))
    with pytest.raises(RuleBundlePersistenceError):
        repo.append_lifecycle_atomic(approve, applied, idempotent)
    assert (connection.events, connection.audits, connection.states) == before

    missing = bundle("1.1.0")
    missing_approve = lifecycle_event(
        RuleBundleEventType.APPROVED,
        2,
        None,
        missing,
        prefix="missing",
        reason="Missing ref.",
    )
    missing_applied, missing_idempotent = command_audits(
        missing_approve,
        RuleBundleCommandReason.APPROVED,
        RuleBundleCommandReason.APPROVE_IDEMPOTENT,
        "missing",
    )
    with pytest.raises(RuleBundlePersistenceError):
        repo.append_lifecycle_atomic(
            missing_approve, missing_applied, missing_idempotent
        )


def test_locked_history_rejects_missing_malformed_invalid_and_over_cap_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = bundle()
    approve = lifecycle_event(
        RuleBundleEventType.APPROVED,
        2,
        None,
        item,
        prefix="approve",
        reason="Reviewed.",
    )

    missing_state = FakeConnection()
    missing_state.drop_state_insert = True
    with pytest.raises(RuleBundlePersistenceError):
        save(repository(missing_state), item, 1, "missing-state")

    malformed_state = FakeConnection()
    malformed_repo = repository(malformed_state)
    assert save(malformed_repo, item, 1, "draft") is DraftWriteOutcome.APPLIED
    malformed_state.states[("tenant-1", "demo", "ruleset-1")] = (1,)
    with pytest.raises(RuleBundlePersistenceError):
        append(malformed_repo, approve, "malformed-state")

    invalid_history = FakeConnection()
    invalid_repo = repository(invalid_history)
    assert save(invalid_repo, item, 1, "draft") is DraftWriteOutcome.APPLIED
    invalid_history.events.append(copy.deepcopy(invalid_history.events[0]))
    with pytest.raises(RuleBundlePersistenceError):
        append(invalid_repo, approve, "invalid-history")

    over_cap = FakeConnection()
    over_cap_repo = repository(over_cap)
    assert save(over_cap_repo, item, 1, "draft") is DraftWriteOutcome.APPLIED
    over_cap.events.append(copy.deepcopy(over_cap.events[0]))
    monkeypatch.setattr(postgres_rule_bundle, "MAX_PERSISTED_HISTORY", 1)
    with pytest.raises(RuleBundlePersistenceError):
        append(over_cap_repo, approve, "over-cap")


def test_standalone_audit_is_transactional_and_duplicate_rolls_back() -> None:
    connection = FakeConnection()
    repo = repository(connection)
    item = bundle()
    event = drafted(item)
    applied, _ = command_audits(
        event,
        RuleBundleCommandReason.DRAFT_APPLIED,
        RuleBundleCommandReason.DRAFT_IDEMPOTENT,
        "audit",
    )
    failure = RuleBundleCommandAuditRecord(
        "failure-command",
        "failure-authorization",
        None,
        "tenant-1",
        "demo",
        "ruleset-1",
        "bundle-1",
        "1.0.0",
        rule_bundle_ref(item).content_hash,
        "rule_bundle",
        "target",
        "actor-1",
        LifecycleActorType.HUMAN,
        "POLICY_READ",
        RuleBundleCommandOutcome.FAILURE,
        RuleBundleCommandReason.NOT_FOUND,
        NOW,
    )
    repo.append_command_audit(failure)
    assert connection.commits == 1
    with pytest.raises(RuleBundlePersistenceError):
        repo.append_command_audit(failure)
    assert connection.rollbacks == 1
    with pytest.raises(ValueError, match="typed"):
        repo.append_command_audit(cast(RuleBundleCommandAuditRecord, object()))
    assert applied.lifecycle_event_id == event.event_id


class CappedRepository(PostgresRuleBundleRepository):
    def __init__(
        self,
        connection: Connection[Any],
        events: tuple[RuleBundleLifecycleEvent, ...],
    ) -> None:
        super().__init__(connection)
        self.forced_events = events
        self.lifecycle = fold_rule_bundle_events(events)

    def _lock_and_validate_scope(
        self, scope: tuple[str, str, str]
    ) -> tuple[tuple[RuleBundleLifecycleEvent, ...], Any]:
        del scope
        return self.forced_events, self.lifecycle

    def _require_stored_references(
        self,
        scope: tuple[str, str, str],
        event: RuleBundleLifecycleEvent,
    ) -> None:
        del scope, event


def test_history_cap_allows_idempotent_retry_but_rejects_new_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection()
    item = bundle()
    draft = drafted(item)
    approve = lifecycle_event(
        RuleBundleEventType.APPROVED,
        2,
        None,
        item,
        prefix="approve",
        reason="Reviewed.",
    )
    activate = lifecycle_event(
        RuleBundleEventType.ACTIVATED,
        3,
        None,
        item,
        prefix="activate",
        reason="Activated.",
    )
    history = (draft, approve, activate)
    monkeypatch.setattr(postgres_rule_bundle, "MAX_PERSISTED_HISTORY", len(history))
    repo = CappedRepository(cast(Connection[Any], connection), history)
    retry = lifecycle_event(
        RuleBundleEventType.ACTIVATED,
        4,
        None,
        item,
        prefix="retry",
        reason="Activated.",
    )
    assert append(repo, retry, "retry") is LifecycleWriteOutcome.IDEMPOTENT
    audits_after_retry = copy.deepcopy(connection.audits)
    retire = lifecycle_event(
        RuleBundleEventType.RETIRED,
        4,
        item,
        None,
        prefix="retire",
        reason="Retired.",
    )
    with pytest.raises(RuleBundlePersistenceError):
        append(repo, retire, "retire")
    assert connection.events == []
    assert connection.audits == audits_after_retry
