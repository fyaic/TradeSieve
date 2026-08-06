"""Application evidence for immutable source ingest, parsing, and validation."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest

import tradesieve.application.source_snapshot as source_app
from tradesieve.adapters.in_memory_source_snapshot import (
    InMemoryImmutableRawObjectStore,
    InMemorySourceSnapshotRepository,
)
from tradesieve.adapters.synthetic_source_parser import SyntheticJsonSourceParser
from tradesieve.application.auth import (
    ActorContext,
    ActorType,
    AuthorizationAuditEvent,
    AuthorizationDenied,
    AuthorizationReason,
    AuthorizationRequest,
    AuthorizationService,
    AuthorizedRequest,
    Operation,
    RequestContext,
    ResolvedTargetFacts,
    Role,
    Scope,
    TargetObject,
)
from tradesieve.application.source_snapshot import (
    SOURCE_SNAPSHOT_TARGET_TYPE,
    SOURCE_TARGET_TYPE,
    ImmutableSourceSnapshotIdentity,
    SourceSnapshotAuthorizationBindingDenied,
    SourceSnapshotConflict,
    SourceSnapshotNotFound,
    SourceSnapshotParseRejected,
    SourceSnapshotRequestInvalid,
    SourceSnapshotService,
    SourceSnapshotUnavailable,
    source_authorization_target_id,
    source_snapshot_authorization_target_id,
)
from tradesieve.domain.source_registry import (
    ObservationWriteOutcome,
    SourceAccessMethod,
    SourceAvailability,
    SourceRegistration,
    SourceRuntimeObservation,
    SourceSetManifest,
)
from tradesieve.domain.source_snapshot import (
    AUTHORIZATION_OPERATION_BY_EVENT_TYPE,
    ArtifactWriteOutcome,
    LifecycleActorType,
    LifecycleWriteOutcome,
    ParsedSourceSnapshot,
    RawObjectMetadata,
    RawObjectState,
    SnapshotCommandAuditRecord,
    SnapshotCommandOutcome,
    SnapshotCommandReason,
    SourceSnapshotEventType,
    SourceSnapshotLifecycleEvent,
    SourceSnapshotState,
    ValidationReasonCode,
)
from tradesieve.ports.source_snapshot import (
    SYNTHETIC_CHARSET,
    SYNTHETIC_MEDIA_TYPE,
    SYNTHETIC_PARSER_VERSION,
    SYNTHETIC_SCHEMA_ID,
    FiniteParserErrorCode,
    FiniteParserId,
    FiniteParserOutput,
    SourceSnapshotPersistenceError,
)

NOW = datetime(2026, 8, 6, 12, tzinfo=UTC)
DEPLOYMENT_ID = "demo-deployment"
SOURCE_SET_ID = "official-source-set"
SOURCE_ID = "synthetic-source"
TENANT_ID = "control-tenant"


def content(
    *,
    record_id: str | None = "record-1",
    assertions: list[dict[str, object]] | None = None,
    declared_count: int = 1,
) -> bytes:
    return json.dumps(
        {
            "schema_id": SYNTHETIC_SCHEMA_ID,
            "declared_record_count": declared_count,
            "records": [
                {
                    "record_id": record_id,
                    "effective_from": "2026-08-01",
                    "effective_to": None,
                    "assertions": assertions
                    if assertions is not None
                    else [{"field": "entity.name", "value": "Synthetic Entity"}],
                }
            ],
        },
        separators=(",", ":"),
    ).encode()


def records_content(records: list[tuple[str, str]]) -> bytes:
    return json.dumps(
        {
            "schema_id": SYNTHETIC_SCHEMA_ID,
            "declared_record_count": len(records),
            "records": [
                {
                    "record_id": record_id,
                    "effective_from": "2026-08-01",
                    "effective_to": None,
                    "assertions": [{"field": "entity.name", "value": value}],
                }
                for record_id, value in records
            ],
        },
        separators=(",", ":"),
    ).encode()


def registration(
    *, source_set_id: str = SOURCE_SET_ID, active: bool = True
) -> SourceRegistration:
    return SourceRegistration(
        deployment_id=DEPLOYMENT_ID,
        source_set_id=source_set_id,
        source_id=SOURCE_ID,
        name="Synthetic official source",
        owner="Compliance",
        responsible_operator="Source operations",
        jurisdiction="EU",
        legal_scope="Synthetic testing",
        data_scope="Synthetic records",
        access_method=SourceAccessMethod.INTERNAL,
        licence_summary="Internal synthetic data",
        refresh_expectation=timedelta(days=1),
        stale_after=timedelta(days=2),
        active=active,
    )


class Registry:
    def __init__(self, current: SourceRegistration | None = None) -> None:
        self.current: SourceRegistration | None = (
            current if current is not None else registration()
        )
        self.failure: Exception | None = None

    def save_manifest(self, manifest: SourceSetManifest) -> None:
        del manifest

    def get_manifest(
        self, deployment_id: str, source_set_id: str
    ) -> SourceSetManifest | None:
        del deployment_id, source_set_id
        return None

    def save_registration(self, value: SourceRegistration) -> None:
        self.current = value

    def get_registration(
        self, deployment_id: str, source_id: str
    ) -> SourceRegistration | None:
        del deployment_id, source_id
        if self.failure is not None:
            raise self.failure
        return self.current

    def save_observation(
        self, observation: SourceRuntimeObservation
    ) -> ObservationWriteOutcome:
        del observation
        return ObservationWriteOutcome.APPLIED

    def list_entries(
        self, deployment_id: str, source_set_id: str, *, limit: int
    ) -> tuple[tuple[SourceRegistration, SourceRuntimeObservation | None], ...]:
        del deployment_id, source_set_id, limit
        return ()


class Ids:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return f"{self.prefix}-{self.value}"


class Clock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value
        self.calls = 0

    def __call__(self) -> datetime:
        self.calls += 1
        return self.value


@dataclass
class Environment:
    repository: InMemorySourceSnapshotRepository
    store: InMemoryImmutableRawObjectStore
    registry: Registry
    parser: SyntheticJsonSourceParser
    clock: Clock
    event_ids: Ids
    command_ids: Ids
    service: SourceSnapshotService


@pytest.fixture
def env() -> Environment:
    repository = InMemorySourceSnapshotRepository()
    store = InMemoryImmutableRawObjectStore()
    source_registry = Registry()
    parser = SyntheticJsonSourceParser()
    clock = Clock()
    event_ids = Ids("event")
    command_ids = Ids("command")
    service = SourceSnapshotService(
        repository,
        store,
        source_registry,
        parser,
        deployment_id=DEPLOYMENT_ID,
        source_set_id=SOURCE_SET_ID,
        control_tenant_id=TENANT_ID,
        clock=clock,
        event_id_factory=event_ids,
        command_id_factory=command_ids,
    )
    return Environment(
        repository,
        store,
        source_registry,
        parser,
        clock,
        event_ids,
        command_ids,
        service,
    )


def actor(
    *,
    subject: str = "source-operator-1",
    actor_type: ActorType = ActorType.SERVICE,
    tenant_id: str = TENANT_ID,
    scopes: frozenset[Scope] = frozenset({Scope.SOURCE_OPERATE}),
    roles: frozenset[Role] = frozenset({Role.SOURCE_OPERATOR}),
) -> ActorContext:
    return ActorContext(
        subject=subject,
        client_id="source-worker",
        tenant_id=tenant_id,
        actor_type=actor_type,
        scopes=scopes,
        roles=roles,
        issuer="https://identity.example.test/",
        audience="tradesieve-api",
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=5),
        demo_identity=False,
    )


def authorized(
    operation: Operation,
    *,
    principal: ActorContext | None = None,
    context_tenant: str = TENANT_ID,
    target_tenant: str = TENANT_ID,
    target_type: str = SOURCE_TARGET_TYPE,
    target_id: str | None = None,
    audit_event_id: str | None = None,
) -> AuthorizedRequest:
    return AuthorizedRequest(
        RequestContext(principal or actor(), context_tenant, "correlation-1"),
        operation,
        TargetObject(
            target_tenant,
            target_type,
            target_id
            or source_authorization_target_id(DEPLOYMENT_ID, SOURCE_SET_ID, SOURCE_ID),
        ),
        audit_event_id or f"authorization-{operation.value.lower()}",
    )


def ingest(
    env: Environment,
    *,
    payload: bytes | None = None,
    media_type: str = SYNTHETIC_MEDIA_TYPE,
    charset: str = SYNTHETIC_CHARSET,
    retrieved_at: datetime = NOW - timedelta(minutes=1),
    effective_from: datetime | None = NOW + timedelta(days=1),
) -> str:
    result = env.service.ingest(
        authorized(Operation.SOURCE_SNAPSHOT_INGEST),
        source_id=SOURCE_ID,
        original_name="synthetic.json",
        media_type=media_type,
        charset=charset,
        retrieved_at=retrieved_at,
        effective_from=effective_from,
        content=payload if payload is not None else content(),
    )
    return result.object_id


def parse(env: Environment, object_id: str) -> str:
    return env.service.parse(
        authorized(Operation.SOURCE_SNAPSHOT_PARSE),
        source_id=SOURCE_ID,
        object_id=object_id,
    ).snapshot_id


def immutable_identity(
    env: Environment, snapshot_id: str
) -> ImmutableSourceSnapshotIdentity:
    snapshot = env.repository.get_snapshot(DEPLOYMENT_ID, SOURCE_ID, snapshot_id)
    assert snapshot is not None
    return ImmutableSourceSnapshotIdentity(
        snapshot_id=snapshot.snapshot_id, content_hash=snapshot.content_hash
    )


def governance_authorized(
    operation: Operation,
    identity: ImmutableSourceSnapshotIdentity,
    *,
    subject: str = "source-approver-2",
    role: Role = Role.SOURCE_APPROVER,
    audit_event_id: str | None = None,
) -> AuthorizedRequest:
    return authorized(
        operation,
        principal=actor(
            subject=subject,
            actor_type=ActorType.HUMAN,
            scopes=frozenset({Scope.SOURCE_APPROVE}),
            roles=frozenset({role}),
        ),
        target_type=SOURCE_SNAPSHOT_TARGET_TYPE,
        target_id=source_snapshot_authorization_target_id(
            DEPLOYMENT_ID,
            SOURCE_SET_ID,
            SOURCE_ID,
            identity.snapshot_id,
            identity.content_hash,
        ),
        audit_event_id=audit_event_id,
    )


def validated_snapshot(
    env: Environment, *, payload: bytes | None = None
) -> tuple[str, str, ImmutableSourceSnapshotIdentity]:
    object_id = ingest(env, payload=payload)
    snapshot_id = parse(env, object_id)
    result = env.service.validate(
        authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
        source_id=SOURCE_ID,
        snapshot_id=snapshot_id,
    )
    assert result.passed
    return object_id, snapshot_id, immutable_identity(env, snapshot_id)


def approve(
    env: Environment,
    identity: ImmutableSourceSnapshotIdentity,
    *,
    reason: str = "approve verified snapshot",
    role: Role = Role.SOURCE_APPROVER,
    subject: str = "source-approver-2",
    audit_event_id: str | None = None,
) -> source_app.SnapshotGovernanceCommandResult:
    return env.service.approve(
        governance_authorized(
            Operation.SOURCE_SNAPSHOT_APPROVE,
            identity,
            subject=subject,
            role=role,
            audit_event_id=audit_event_id,
        ),
        source_id=SOURCE_ID,
        identity=identity,
        reason=reason,
    )


def activate(
    env: Environment,
    identity: ImmutableSourceSnapshotIdentity,
    *,
    reason: str = "activate approved snapshot",
    audit_event_id: str | None = None,
) -> source_app.SnapshotGovernanceCommandResult:
    return env.service.activate(
        governance_authorized(
            Operation.SOURCE_SNAPSHOT_ACTIVATE,
            identity,
            audit_event_id=audit_event_id,
        ),
        source_id=SOURCE_ID,
        identity=identity,
        reason=reason,
    )


def rollback(
    env: Environment,
    identity: ImmutableSourceSnapshotIdentity,
    *,
    reason: str = "rollback to accepted snapshot",
    audit_event_id: str | None = None,
) -> source_app.SnapshotGovernanceCommandResult:
    return env.service.rollback(
        governance_authorized(
            Operation.SOURCE_SNAPSHOT_ROLLBACK,
            identity,
            audit_event_id=audit_event_id,
        ),
        source_id=SOURCE_ID,
        identity=identity,
        reason=reason,
    )


def active_snapshot(
    env: Environment,
    *,
    payload: bytes | None = None,
    approval_reason: str = "approve verified snapshot",
    activation_reason: str = "activate approved snapshot",
) -> tuple[str, str, ImmutableSourceSnapshotIdentity]:
    object_id, snapshot_id, identity = validated_snapshot(env, payload=payload)
    approve(env, identity, reason=approval_reason)
    activate(env, identity, reason=activation_reason)
    return object_id, snapshot_id, identity


def active_pair(
    env: Environment,
) -> tuple[
    tuple[str, str, ImmutableSourceSnapshotIdentity],
    tuple[str, str, ImmutableSourceSnapshotIdentity],
]:
    first = active_snapshot(
        env,
        approval_reason="approve first snapshot",
        activation_reason="activate first snapshot",
    )
    second = validated_snapshot(
        env, payload=records_content([("record-1", "Replacement")])
    )
    approve(env, second[2], reason="approve second snapshot")
    env.clock.value += timedelta(seconds=1)
    activate(env, second[2], reason="activate second snapshot")
    return first, second


def atomic_audit(
    event: SourceSnapshotLifecycleEvent,
    reason: SnapshotCommandReason,
    *,
    applied: bool,
) -> SnapshotCommandAuditRecord:
    reference = event.snapshot
    return SnapshotCommandAuditRecord(
        command_event_id=f"test-command-{event.sequence}-{reason.value.lower()}",
        authorization_event_id=f"test-authorization-{event.sequence}",
        lifecycle_event_id=event.event_id if applied else None,
        tenant_id=TENANT_ID,
        deployment_id=event.deployment_id,
        source_id=event.source_id,
        raw_object_id=event.raw_object.object_id,
        snapshot_id=reference.snapshot_id if reference else None,
        snapshot_content_hash=reference.content_hash if reference else None,
        actor_id=event.actor_id,
        actor_type=event.actor_type,
        operation=AUTHORIZATION_OPERATION_BY_EVENT_TYPE[event.event_type],
        outcome=SnapshotCommandOutcome.SUCCESS,
        reason=reason,
        occurred_at=event.occurred_at,
    )


def approve_and_activate(env: Environment, *, object_id: str, snapshot_id: str) -> None:
    snapshot = env.repository.get_snapshot(DEPLOYMENT_ID, SOURCE_ID, snapshot_id)
    raw = env.repository.get_raw_metadata(DEPLOYMENT_ID, SOURCE_ID, object_id)
    events, lifecycle = env.repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    assert snapshot is not None
    assert raw is not None
    approved = SourceSnapshotLifecycleEvent(
        sequence=events[-1].sequence + 1,
        event_id=f"test-approved-{snapshot_id[-8:]}",
        deployment_id=DEPLOYMENT_ID,
        source_id=SOURCE_ID,
        event_type=SourceSnapshotEventType.APPROVED,
        raw_object=snapshot.raw_object,
        snapshot=snapshot.reference(),
        previous_active_snapshot=None,
        actor_id="source-approver-2",
        actor_type=LifecycleActorType.HUMAN,
        reason="source snapshot approved",
        occurred_at=env.clock.value,
    )
    assert (
        env.repository.append_lifecycle_atomic(
            approved,
            atomic_audit(approved, SnapshotCommandReason.APPROVED, applied=True),
            atomic_audit(
                approved, SnapshotCommandReason.APPROVE_IDEMPOTENT, applied=False
            ),
        )
        is LifecycleWriteOutcome.APPLIED
    )
    activated = replace(
        approved,
        sequence=approved.sequence + 1,
        event_id=f"test-activated-{snapshot_id[-8:]}",
        event_type=SourceSnapshotEventType.ACTIVATED,
        previous_active_snapshot=lifecycle.active_snapshot,
        reason="source snapshot activated",
    )
    assert (
        env.repository.activate_or_rollback_atomic(
            activated,
            SourceRuntimeObservation(
                deployment_id=DEPLOYMENT_ID,
                source_id=SOURCE_ID,
                availability=SourceAvailability.AVAILABLE,
                observed_at=env.clock.value,
                active_snapshot_id=snapshot_id,
                retrieved_at=raw.retrieved_at,
                effective_from=raw.effective_from,
            ),
            atomic_audit(activated, SnapshotCommandReason.ACTIVATED, applied=True),
            atomic_audit(
                activated, SnapshotCommandReason.ACTIVATE_IDEMPOTENT, applied=False
            ),
        )
        is LifecycleWriteOutcome.APPLIED
    )


def test_happy_path_is_atomic_audited_safe_and_retry_stable(
    env: Environment,
) -> None:
    object_id = ingest(env)
    events, lifecycle = env.repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    assert [event.event_type for event in events] == [
        SourceSnapshotEventType.RETRIEVED,
        SourceSnapshotEventType.QUARANTINED,
    ]
    assert lifecycle.raw_objects[0].state is RawObjectState.QUARANTINED

    snapshot_id = parse(env, object_id)
    stored_snapshot = env.repository.get_snapshot(DEPLOYMENT_ID, SOURCE_ID, snapshot_id)
    assert stored_snapshot is not None
    original_parsed_at = stored_snapshot.parsed_at
    parse_clock_calls = env.clock.calls
    parsed_retry = env.service.parse(
        authorized(Operation.SOURCE_SNAPSHOT_PARSE),
        source_id=SOURCE_ID,
        object_id=object_id,
    )
    assert parsed_retry.snapshot_id == snapshot_id
    assert parsed_retry.state is SourceSnapshotState.PARSED
    assert parsed_retry.parsed_at == original_parsed_at
    assert env.clock.calls == parse_clock_calls + 1

    validated = env.service.validate(
        authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
        source_id=SOURCE_ID,
        snapshot_id=snapshot_id,
    )
    validate_clock_calls = env.clock.calls
    retried = env.service.validate(
        authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
        source_id=SOURCE_ID,
        snapshot_id=snapshot_id,
    )
    assert retried == validated
    assert retried.passed
    assert retried.reason_codes == []
    assert retried.state is SourceSnapshotState.VALIDATED
    assert retried.added_record_count == 1
    assert retried.validated_at == validated.validated_at
    assert env.clock.calls == validate_clock_calls + 1

    audits = env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=32)
    assert [audit.authorization_event_id for audit in audits] == [
        "authorization-source_snapshot_ingest",
        "authorization-source_snapshot_ingest",
        "authorization-source_snapshot_parse",
        "authorization-source_snapshot_parse",
        "authorization-source_snapshot_validate",
        "authorization-source_snapshot_validate",
    ]
    assert [audit.operation for audit in audits] == [
        Operation.SOURCE_SNAPSHOT_INGEST.value,
        Operation.SOURCE_SNAPSHOT_INGEST.value,
        Operation.SOURCE_SNAPSHOT_PARSE.value,
        Operation.SOURCE_SNAPSHOT_PARSE.value,
        Operation.SOURCE_SNAPSHOT_VALIDATE.value,
        Operation.SOURCE_SNAPSHOT_VALIDATE.value,
    ]
    assert [audit.reason for audit in audits] == [
        SnapshotCommandReason.RETRIEVED,
        SnapshotCommandReason.QUARANTINED,
        SnapshotCommandReason.PARSED,
        SnapshotCommandReason.PARSE_IDEMPOTENT,
        SnapshotCommandReason.VALIDATED,
        SnapshotCommandReason.VALIDATE_IDEMPOTENT,
    ]
    assert all(audit.tenant_id == TENANT_ID for audit in audits)


def test_governance_happy_path_activates_two_snapshots_then_rolls_back_exactly(
    env: Environment,
) -> None:
    first_object_id = ingest(env)
    first_snapshot_id = parse(env, first_object_id)
    env.service.validate(
        authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
        source_id=SOURCE_ID,
        snapshot_id=first_snapshot_id,
    )
    first_identity = immutable_identity(env, first_snapshot_id)
    approved = env.service.approve(
        governance_authorized(Operation.SOURCE_SNAPSHOT_APPROVE, first_identity),
        source_id=SOURCE_ID,
        identity=first_identity,
        reason="approved for controlled release",
    )
    assert approved.event_type is SourceSnapshotEventType.APPROVED
    assert approved.state is SourceSnapshotState.APPROVED
    assert approved.active_snapshot_id is None

    first_activated = env.service.activate(
        governance_authorized(Operation.SOURCE_SNAPSHOT_ACTIVATE, first_identity),
        source_id=SOURCE_ID,
        identity=first_identity,
        reason="activate initial verified source",
    )
    assert first_activated.event_type is SourceSnapshotEventType.ACTIVATED
    assert first_activated.state is SourceSnapshotState.ACTIVE
    assert first_activated.active_snapshot_id == first_snapshot_id
    assert first_activated.previous_active_snapshot_id is None

    second_object_id = ingest(
        env,
        payload=records_content([("record-1", "Updated Synthetic Entity")]),
    )
    second_snapshot_id = parse(env, second_object_id)
    validated = env.service.validate(
        authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
        source_id=SOURCE_ID,
        snapshot_id=second_snapshot_id,
    )
    assert validated.previous_snapshot_id == first_snapshot_id
    second_identity = immutable_identity(env, second_snapshot_id)
    env.service.approve(
        governance_authorized(Operation.SOURCE_SNAPSHOT_APPROVE, second_identity),
        source_id=SOURCE_ID,
        identity=second_identity,
        reason="approved replacement source",
    )
    env.clock.value += timedelta(seconds=1)
    second_activated = env.service.activate(
        governance_authorized(Operation.SOURCE_SNAPSHOT_ACTIVATE, second_identity),
        source_id=SOURCE_ID,
        identity=second_identity,
        reason="activate verified replacement",
    )
    assert second_activated.active_snapshot_id == second_snapshot_id
    assert second_activated.previous_active_snapshot_id == first_snapshot_id

    env.clock.value += timedelta(seconds=1)
    rolled_back = env.service.rollback(
        governance_authorized(Operation.SOURCE_SNAPSHOT_ROLLBACK, first_identity),
        source_id=SOURCE_ID,
        identity=first_identity,
        reason="rollback after replacement review",
    )
    assert rolled_back.event_type is SourceSnapshotEventType.ROLLED_BACK
    assert rolled_back.state is SourceSnapshotState.ACTIVE
    assert rolled_back.active_snapshot_id == first_snapshot_id
    assert rolled_back.previous_active_snapshot_id == second_snapshot_id
    observation = env.repository.get_runtime_observation(DEPLOYMENT_ID, SOURCE_ID)
    assert observation is not None
    assert observation.availability is SourceAvailability.AVAILABLE
    assert observation.active_snapshot_id == first_snapshot_id
    first_raw = env.repository.get_raw_metadata(
        DEPLOYMENT_ID, SOURCE_ID, first_object_id
    )
    assert first_raw is not None
    assert observation.retrieved_at == first_raw.retrieved_at
    assert observation.effective_from == first_raw.effective_from
    _, lifecycle = env.repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    second_snapshot = env.repository.get_snapshot(
        DEPLOYMENT_ID, SOURCE_ID, second_snapshot_id
    )
    assert second_snapshot is not None
    assert (
        lifecycle.state_for(second_snapshot.reference())
        is SourceSnapshotState.ROLLED_BACK
    )
    governance_audits = [
        audit.reason
        for audit in env.repository.list_command_audits(
            DEPLOYMENT_ID, SOURCE_ID, limit=32
        )
        if audit.operation
        in {
            Operation.SOURCE_SNAPSHOT_APPROVE.value,
            Operation.SOURCE_SNAPSHOT_ACTIVATE.value,
            Operation.SOURCE_SNAPSHOT_ROLLBACK.value,
        }
    ]
    assert governance_audits == [
        SnapshotCommandReason.APPROVED,
        SnapshotCommandReason.ACTIVATED,
        SnapshotCommandReason.APPROVED,
        SnapshotCommandReason.ACTIVATED,
        SnapshotCommandReason.ROLLED_BACK,
    ]


class ExactGovernanceEntitlements:
    def __init__(
        self,
        creator_actor_id: str,
        expected_call: tuple[str, str, str, str, str, str],
    ) -> None:
        self.creator_actor_id = creator_actor_id
        self.expected_call = expected_call
        self.calls: list[tuple[str, str, str, str, str, str]] = []

    def resolve(
        self,
        *,
        actor_subject: str,
        actor_tenant_id: str,
        operation: str,
        target_tenant_id: str,
        target_type: str,
        target_id: str,
    ) -> ResolvedTargetFacts | None:
        call = (
            actor_subject,
            actor_tenant_id,
            operation,
            target_tenant_id,
            target_type,
            target_id,
        )
        self.calls.append(call)
        return (
            ResolvedTargetFacts(author_actor_id=self.creator_actor_id)
            if call == self.expected_call
            else None
        )


@pytest.mark.parametrize("role", [Role.SOURCE_APPROVER, Role.COMPLIANCE_OWNER])
def test_real_authorization_binds_exact_snapshot_and_trusted_creator_fact(
    role: Role,
) -> None:
    content_hash = f"sha256:{'a' * 64}"
    identity = ImmutableSourceSnapshotIdentity(
        snapshot_id=f"snapshot-{'a' * 64}", content_hash=content_hash
    )
    target_id = source_snapshot_authorization_target_id(
        DEPLOYMENT_ID,
        SOURCE_SET_ID,
        SOURCE_ID,
        identity.snapshot_id,
        identity.content_hash,
    )
    principal = actor(
        subject="source-approver-2",
        actor_type=ActorType.HUMAN,
        scopes=frozenset({Scope.SOURCE_APPROVE}),
        roles=frozenset({role}),
    )
    request = AuthorizationRequest(
        RequestContext(principal, TENANT_ID, "correlation-governance"),
        Operation.SOURCE_SNAPSHOT_APPROVE,
        TargetObject(TENANT_ID, SOURCE_SNAPSHOT_TARGET_TYPE, target_id),
    )
    events: list[AuthorizationAuditEvent] = []
    expected_call = (
        "source-approver-2",
        TENANT_ID,
        Operation.SOURCE_SNAPSHOT_APPROVE,
        TENANT_ID,
        SOURCE_SNAPSHOT_TARGET_TYPE,
        target_id,
    )
    allowed_resolver = ExactGovernanceEntitlements("source-operator-1", expected_call)
    allowed_service = AuthorizationService(
        events.append,
        allowed_resolver,
        event_id_factory=lambda: "authz-governance-allowed",
    )
    allowed = allowed_service.require(request, now=NOW)
    assert allowed.target.object_id == target_id
    assert allowed_resolver.calls == [expected_call]
    assert str(events[-1].reason) == AuthorizationReason.ALLOWED.value

    denied_resolver = ExactGovernanceEntitlements("source-approver-2", expected_call)
    denied_service = AuthorizationService(
        events.append,
        denied_resolver,
        event_id_factory=lambda: "authz-governance-denied",
    )
    with pytest.raises(AuthorizationDenied):
        denied_service.require(request, now=NOW)
    assert denied_resolver.calls == [expected_call]
    assert (
        str(events[-1].reason)
        == AuthorizationReason.AUTHOR_APPROVER_SEPARATION_DENIED.value
    )

    wrong_identity = ImmutableSourceSnapshotIdentity(
        snapshot_id=f"snapshot-{'b' * 64}",
        content_hash=f"sha256:{'b' * 64}",
    )
    wrong_target_id = source_snapshot_authorization_target_id(
        DEPLOYMENT_ID,
        SOURCE_SET_ID,
        SOURCE_ID,
        wrong_identity.snapshot_id,
        wrong_identity.content_hash,
    )
    wrong_request = replace(
        request,
        target=TargetObject(TENANT_ID, SOURCE_SNAPSHOT_TARGET_TYPE, wrong_target_id),
    )
    with pytest.raises(AuthorizationDenied):
        allowed_service.require(wrong_request, now=NOW)
    assert allowed_resolver.calls[-1][-1] == wrong_target_id
    assert str(events[-1].reason) == AuthorizationReason.OBJECT_NOT_VISIBLE.value


def test_concurrent_same_reason_approval_recovers_responsible_event(
    env: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_id = parse(env, ingest(env))
    env.service.validate(
        authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
        source_id=SOURCE_ID,
        snapshot_id=snapshot_id,
    )
    identity = immutable_identity(env, snapshot_id)
    original_append = env.repository.append_lifecycle_atomic

    def concurrent_append(
        event: SourceSnapshotLifecycleEvent,
        applied_audit: SnapshotCommandAuditRecord,
        idempotent_audit: SnapshotCommandAuditRecord,
    ) -> LifecycleWriteOutcome:
        concurrent = replace(event, event_id="concurrent-approval-event")
        assert (
            original_append(
                concurrent,
                atomic_audit(concurrent, SnapshotCommandReason.APPROVED, applied=True),
                atomic_audit(
                    concurrent,
                    SnapshotCommandReason.APPROVE_IDEMPOTENT,
                    applied=False,
                ),
            )
            is LifecycleWriteOutcome.APPLIED
        )
        return original_append(event, applied_audit, idempotent_audit)

    monkeypatch.setattr(env.repository, "append_lifecycle_atomic", concurrent_append)
    result = env.service.approve(
        governance_authorized(Operation.SOURCE_SNAPSHOT_APPROVE, identity),
        source_id=SOURCE_ID,
        identity=identity,
        reason="approve concurrently",
    )
    assert result.event_type is SourceSnapshotEventType.APPROVED
    events, _ = env.repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    responsible = next(
        event
        for event in events
        if event.event_type is SourceSnapshotEventType.APPROVED
    )
    assert responsible.event_id == "concurrent-approval-event"
    assert result.occurred_at == responsible.occurred_at
    assert (
        env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=16)[
            -1
        ].reason
        is SnapshotCommandReason.APPROVE_IDEMPOTENT
    )


def test_old_activation_is_not_a_retry_after_rollback_restores_target(
    env: Environment,
) -> None:
    first_object_id = ingest(env)
    first_snapshot_id = parse(env, first_object_id)
    env.service.validate(
        authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
        source_id=SOURCE_ID,
        snapshot_id=first_snapshot_id,
    )
    approve_and_activate(env, object_id=first_object_id, snapshot_id=first_snapshot_id)
    second_object_id = ingest(
        env,
        payload=records_content([("record-1", "Replacement")]),
    )
    second_snapshot_id = parse(env, second_object_id)
    env.service.validate(
        authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
        source_id=SOURCE_ID,
        snapshot_id=second_snapshot_id,
    )
    env.clock.value += timedelta(seconds=1)
    approve_and_activate(
        env, object_id=second_object_id, snapshot_id=second_snapshot_id
    )
    first_identity = immutable_identity(env, first_snapshot_id)
    env.clock.value += timedelta(seconds=1)
    env.service.rollback(
        governance_authorized(Operation.SOURCE_SNAPSHOT_ROLLBACK, first_identity),
        source_id=SOURCE_ID,
        identity=first_identity,
        reason="restore first snapshot",
    )
    with pytest.raises(SourceSnapshotConflict):
        env.service.activate(
            governance_authorized(Operation.SOURCE_SNAPSHOT_ACTIVATE, first_identity),
            source_id=SOURCE_ID,
            identity=first_identity,
            reason="source snapshot activated",
        )
    assert (
        env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=32)[
            -1
        ].reason
        is SnapshotCommandReason.CONFLICT
    )


@pytest.mark.parametrize("role", [Role.SOURCE_APPROVER, Role.COMPLIANCE_OWNER])
def test_application_accepts_each_named_human_governance_role(
    env: Environment, role: Role
) -> None:
    _, _, identity = validated_snapshot(env)
    result = approve(env, identity, role=role)
    assert result.state is SourceSnapshotState.APPROVED


@pytest.mark.parametrize(
    "operation",
    [
        Operation.SOURCE_SNAPSHOT_APPROVE,
        Operation.SOURCE_SNAPSHOT_ACTIVATE,
        Operation.SOURCE_SNAPSHOT_ROLLBACK,
    ],
)
@pytest.mark.parametrize(
    "forgery",
    [
        "service",
        "agent",
        "scope",
        "role",
        "actor_tenant",
        "context_tenant",
        "target_tenant",
        "target_type",
        "target_id",
        "audit_id",
        "operation",
    ],
)
def test_forged_governance_authorization_is_rejected_before_storage(
    env: Environment, operation: Operation, forgery: str
) -> None:
    _, _, identity = validated_snapshot(env)
    request = governance_authorized(operation, identity)
    principal = request.context.actor
    if forgery == "service":
        principal = actor(
            actor_type=ActorType.SERVICE,
            scopes=frozenset({Scope.SOURCE_APPROVE}),
            roles=frozenset({Role.SOURCE_APPROVER}),
        )
    elif forgery == "agent":
        principal = actor(
            actor_type=ActorType.AGENT,
            scopes=frozenset({Scope.SOURCE_APPROVE}),
            roles=frozenset({Role.SOURCE_APPROVER}),
        )
    elif forgery == "scope":
        principal = actor(
            subject="source-approver-2",
            actor_type=ActorType.HUMAN,
            scopes=frozenset({Scope.SOURCE_OPERATE}),
            roles=frozenset({Role.SOURCE_APPROVER}),
        )
    elif forgery == "role":
        principal = actor(
            subject="source-approver-2",
            actor_type=ActorType.HUMAN,
            scopes=frozenset({Scope.SOURCE_APPROVE}),
            roles=frozenset({Role.SOURCE_OPERATOR}),
        )
    elif forgery == "actor_tenant":
        principal = replace(principal, tenant_id="other-tenant")
    request = replace(
        request,
        context=RequestContext(
            principal,
            "other-tenant" if forgery == "context_tenant" else TENANT_ID,
            "correlation-1",
        ),
        operation=(
            Operation.SOURCE_SNAPSHOT_PARSE
            if forgery == "operation"
            else request.operation
        ),
        target=TargetObject(
            "other-tenant" if forgery == "target_tenant" else TENANT_ID,
            SOURCE_TARGET_TYPE
            if forgery == "target_type"
            else SOURCE_SNAPSHOT_TARGET_TYPE,
            "source-snapshot-wrong"
            if forgery == "target_id"
            else request.target.object_id,
        ),
        audit_event_id="not safe" if forgery == "audit_id" else request.audit_event_id,
    )
    before = env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=16)
    command = {
        Operation.SOURCE_SNAPSHOT_APPROVE: env.service.approve,
        Operation.SOURCE_SNAPSHOT_ACTIVATE: env.service.activate,
        Operation.SOURCE_SNAPSHOT_ROLLBACK: env.service.rollback,
    }[operation]
    with pytest.raises(SourceSnapshotAuthorizationBindingDenied):
        command(
            request,
            source_id=SOURCE_ID,
            identity=identity,
            reason="forged governance request",
        )
    assert (
        env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=16) == before
    )


@pytest.mark.parametrize(
    "reason",
    ["", " ", " leading", "trailing ", "line\nbreak", "x" * 2001],
)
def test_governance_reason_is_bounded_nonblank_and_printable(
    env: Environment, reason: str
) -> None:
    _, _, identity = validated_snapshot(env)
    before = env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=16)
    with pytest.raises(SourceSnapshotRequestInvalid):
        env.service.approve(
            governance_authorized(Operation.SOURCE_SNAPSHOT_APPROVE, identity),
            source_id=SOURCE_ID,
            identity=identity,
            reason=reason,
        )
    assert (
        env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=16) == before
    )


def test_immutable_identity_and_target_helpers_fail_closed_without_input_echo() -> None:
    secret = "snapshot-secret"  # pragma: allowlist secret
    with pytest.raises(ValueError) as caught:
        ImmutableSourceSnapshotIdentity(
            snapshot_id=secret,
            content_hash=f"sha256:{'a' * 64}",
        )
    assert secret not in str(caught.value)
    with pytest.raises(ValueError):
        source_snapshot_authorization_target_id(
            "bad deployment", SOURCE_SET_ID, SOURCE_ID, "snapshot-a", "sha256:a"
        )
    with pytest.raises(ValueError):
        source_snapshot_authorization_target_id(
            DEPLOYMENT_ID,
            SOURCE_SET_ID,
            SOURCE_ID,
            "snapshot-a",
            f"sha256:{'a' * 64}",
        )


def test_exact_authorized_but_unknown_immutable_identity_is_generic_not_found(
    env: Environment,
) -> None:
    identity = ImmutableSourceSnapshotIdentity(
        snapshot_id=f"snapshot-{'b' * 64}",
        content_hash=f"sha256:{'b' * 64}",
    )
    with pytest.raises(SourceSnapshotNotFound):
        approve(env, identity)


def test_application_reproves_creator_separation_and_audits_denial(
    env: Environment,
) -> None:
    _, _, identity = validated_snapshot(env)
    with pytest.raises(source_app.SourceSnapshotCreatorSeparationDenied):
        approve(env, identity, subject="source-operator-1")
    audit = env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=16)[-1]
    assert audit.reason is SnapshotCommandReason.CREATOR_SEPARATION_DENIED
    assert audit.actor_id == "source-operator-1"


@pytest.mark.parametrize(
    "operation",
    [Operation.SOURCE_SNAPSHOT_APPROVE, Operation.SOURCE_SNAPSHOT_ACTIVATE],
)
def test_failed_validation_is_audited_block_not_a_governance_waiver(
    env: Environment, operation: Operation
) -> None:
    snapshot_id = parse(env, ingest(env, payload=content(declared_count=2)))
    result = env.service.validate(
        authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
        source_id=SOURCE_ID,
        snapshot_id=snapshot_id,
    )
    assert not result.passed
    identity = immutable_identity(env, snapshot_id)
    request = governance_authorized(operation, identity)
    command = (
        env.service.approve
        if operation is Operation.SOURCE_SNAPSHOT_APPROVE
        else env.service.activate
    )
    with pytest.raises(source_app.SourceSnapshotValidationBlocked):
        command(
            request,
            source_id=SOURCE_ID,
            identity=identity,
            reason="attempted waiver",
        )
    audit = env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=16)[-1]
    assert audit.reason is SnapshotCommandReason.VALIDATION_BLOCKED
    events, lifecycle = env.repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    assert SourceSnapshotEventType.APPROVED not in {
        event.event_type for event in events
    }
    snapshot = env.repository.get_snapshot(DEPLOYMENT_ID, SOURCE_ID, snapshot_id)
    assert snapshot is not None
    assert lifecycle.state_for(snapshot.reference()) is SourceSnapshotState.QUARANTINED


@pytest.mark.parametrize(
    "operation",
    [Operation.SOURCE_SNAPSHOT_APPROVE, Operation.SOURCE_SNAPSHOT_ACTIVATE],
)
def test_unvalidated_parsed_snapshot_is_an_audited_lifecycle_conflict(
    env: Environment, operation: Operation
) -> None:
    snapshot_id = parse(env, ingest(env))
    identity = immutable_identity(env, snapshot_id)
    command = (
        env.service.approve
        if operation is Operation.SOURCE_SNAPSHOT_APPROVE
        else env.service.activate
    )
    with pytest.raises(SourceSnapshotConflict):
        command(
            governance_authorized(operation, identity),
            source_id=SOURCE_ID,
            identity=identity,
            reason="premature governance command",
        )
    audit = env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=16)[-1]
    assert audit.reason is SnapshotCommandReason.CONFLICT
    assert audit.authorization_event_id == f"authorization-{operation.value.lower()}"


def test_approval_retry_after_activation_preserves_event_and_audits_current_command(
    env: Environment,
) -> None:
    _, _, identity = validated_snapshot(env)
    first = approve(env, identity, reason="same approval reason")
    activate(env, identity)
    before_events, _ = env.repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    env.clock.value += timedelta(seconds=1)
    retried = approve(
        env,
        identity,
        reason="same approval reason",
        audit_event_id="authz-current-approval-retry",
    )
    after_events, _ = env.repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    assert after_events == before_events
    assert retried.occurred_at == first.occurred_at
    assert retried.state is SourceSnapshotState.ACTIVE
    audit = env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=16)[-1]
    assert audit.reason is SnapshotCommandReason.APPROVE_IDEMPOTENT
    assert audit.authorization_event_id == "authz-current-approval-retry"
    assert audit.occurred_at == env.clock.value
    with pytest.raises(SourceSnapshotConflict):
        approve(env, identity, reason="changed approval reason")
    assert (
        env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=20)[
            -1
        ].reason
        is SnapshotCommandReason.CONFLICT
    )


def test_activation_retry_preserves_observation_event_and_current_audit_identity(
    env: Environment,
) -> None:
    _, _, identity = validated_snapshot(env)
    approve(env, identity)
    first = activate(env, identity, reason="same activation reason")
    before_events, _ = env.repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    before_observation = env.repository.get_runtime_observation(
        DEPLOYMENT_ID, SOURCE_ID
    )
    env.clock.value += timedelta(seconds=1)
    retried = activate(
        env,
        identity,
        reason="same activation reason",
        audit_event_id="authz-current-activation-retry",
    )
    after_events, _ = env.repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    assert after_events == before_events
    assert (
        env.repository.get_runtime_observation(DEPLOYMENT_ID, SOURCE_ID)
        == before_observation
    )
    assert retried.occurred_at == first.occurred_at
    audit = env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=16)[-1]
    assert audit.reason is SnapshotCommandReason.ACTIVATE_IDEMPOTENT
    assert audit.authorization_event_id == "authz-current-activation-retry"
    assert audit.occurred_at == env.clock.value
    with pytest.raises(SourceSnapshotConflict):
        activate(env, identity, reason="changed activation reason")
    assert (
        env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=20)[
            -1
        ].reason
        is SnapshotCommandReason.CONFLICT
    )


def test_rollback_retry_preserves_observation_event_and_rejects_reason_change(
    env: Environment,
) -> None:
    first, _ = active_pair(env)
    env.clock.value += timedelta(seconds=1)
    applied = rollback(env, first[2], reason="same rollback reason")
    before_events, _ = env.repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    before_observation = env.repository.get_runtime_observation(
        DEPLOYMENT_ID, SOURCE_ID
    )
    env.clock.value += timedelta(seconds=1)
    retried = rollback(
        env,
        first[2],
        reason="same rollback reason",
        audit_event_id="authz-current-rollback-retry",
    )
    after_events, _ = env.repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    assert after_events == before_events
    assert (
        env.repository.get_runtime_observation(DEPLOYMENT_ID, SOURCE_ID)
        == before_observation
    )
    assert retried.occurred_at == applied.occurred_at
    audit = env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=32)[-1]
    assert audit.reason is SnapshotCommandReason.ROLLBACK_IDEMPOTENT
    assert audit.authorization_event_id == "authz-current-rollback-retry"
    assert audit.occurred_at == env.clock.value
    with pytest.raises(SourceSnapshotConflict):
        rollback(env, first[2], reason="changed rollback reason")
    assert (
        env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=40)[
            -1
        ].reason
        is SnapshotCommandReason.CONFLICT
    )


def test_current_activated_target_without_rollback_event_is_not_rollback_retry(
    env: Environment,
) -> None:
    _, _, identity = active_snapshot(env)
    with pytest.raises(SourceSnapshotConflict):
        rollback(env, identity)
    _, lifecycle = env.repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    assert lifecycle.active_snapshot is not None
    assert lifecycle.active_snapshot.snapshot_id == identity.snapshot_id
    assert (
        env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=16)[
            -1
        ].reason
        is SnapshotCommandReason.CONFLICT
    )


def test_stale_validated_predecessor_cannot_activate_over_new_pointer(
    env: Environment,
) -> None:
    _, first_snapshot_id, _ = active_snapshot(env)
    _, _, stale_identity = validated_snapshot(
        env, payload=records_content([("record-1", "Stale candidate")])
    )
    approve(env, stale_identity, reason="approve stale candidate")
    _, _, winning_identity = validated_snapshot(
        env, payload=records_content([("record-1", "Winning candidate")])
    )
    approve(env, winning_identity, reason="approve winning candidate")
    env.clock.value += timedelta(seconds=1)
    activate(env, winning_identity, reason="activate winning candidate")
    events_before, lifecycle_before = env.repository.get_lifecycle_snapshot(
        DEPLOYMENT_ID, SOURCE_ID
    )
    assert lifecycle_before.active_snapshot is not None
    assert lifecycle_before.active_snapshot.snapshot_id == winning_identity.snapshot_id
    env.clock.value += timedelta(seconds=1)
    with pytest.raises(SourceSnapshotConflict):
        activate(env, stale_identity, reason="activate stale candidate")
    events_after, lifecycle_after = env.repository.get_lifecycle_snapshot(
        DEPLOYMENT_ID, SOURCE_ID
    )
    assert events_after == events_before
    assert lifecycle_after == lifecycle_before
    stale_validation = next(
        event.validation_report
        for event in events_after
        if event.event_type is SourceSnapshotEventType.VALIDATED
        and event.snapshot is not None
        and event.snapshot.snapshot_id == stale_identity.snapshot_id
    )
    assert stale_validation is not None
    assert stale_validation.diff.previous_snapshot is not None
    assert stale_validation.diff.previous_snapshot.snapshot_id == first_snapshot_id
    audit = env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=40)[-1]
    assert audit.reason is SnapshotCommandReason.CONFLICT
    assert audit.authorization_event_id == "authorization-source_snapshot_activate"


@pytest.mark.parametrize(
    ("outcome", "error"),
    [
        (LifecycleWriteOutcome.CONFLICT, SourceSnapshotConflict),
        (cast(LifecycleWriteOutcome, "UNKNOWN"), SourceSnapshotUnavailable),
    ],
)
def test_approval_repository_outcomes_are_never_silently_accepted(
    env: Environment,
    monkeypatch: pytest.MonkeyPatch,
    outcome: LifecycleWriteOutcome,
    error: type[Exception],
) -> None:
    _, _, identity = validated_snapshot(env)
    events_before, lifecycle_before = env.repository.get_lifecycle_snapshot(
        DEPLOYMENT_ID, SOURCE_ID
    )
    monkeypatch.setattr(
        env.repository, "append_lifecycle_atomic", lambda *_args: outcome
    )
    with pytest.raises(error):
        approve(env, identity)
    events_after, lifecycle_after = env.repository.get_lifecycle_snapshot(
        DEPLOYMENT_ID, SOURCE_ID
    )
    assert events_after == events_before
    assert lifecycle_after == lifecycle_before
    if outcome is LifecycleWriteOutcome.CONFLICT:
        assert (
            env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=16)[
                -1
            ].reason
            is SnapshotCommandReason.CONFLICT
        )


def test_approval_repository_exception_and_atomic_failure_leave_no_partial_write(
    env: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, identity = validated_snapshot(env)
    events_before, lifecycle_before = env.repository.get_lifecycle_snapshot(
        DEPLOYMENT_ID, SOURCE_ID
    )
    audits_before = env.repository.list_command_audits(
        DEPLOYMENT_ID, SOURCE_ID, limit=16
    )
    env.repository.fail_next_atomic_write()
    with pytest.raises(SourceSnapshotUnavailable):
        approve(env, identity)
    assert env.repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID) == (
        events_before,
        lifecycle_before,
    )
    assert (
        env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=16)
        == audits_before
    )
    monkeypatch.setattr(
        env.repository,
        "append_lifecycle_atomic",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("secret")),
    )
    with pytest.raises(SourceSnapshotUnavailable) as caught:
        approve(env, identity)
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize(
    ("outcome", "error"),
    [
        (LifecycleWriteOutcome.CONFLICT, SourceSnapshotConflict),
        (cast(LifecycleWriteOutcome, "UNKNOWN"), SourceSnapshotUnavailable),
    ],
)
def test_pointer_repository_outcomes_leave_pointer_and_observation_unchanged(
    env: Environment,
    monkeypatch: pytest.MonkeyPatch,
    outcome: LifecycleWriteOutcome,
    error: type[Exception],
) -> None:
    _, _, identity = validated_snapshot(env)
    approve(env, identity)
    before_events, before_lifecycle = env.repository.get_lifecycle_snapshot(
        DEPLOYMENT_ID, SOURCE_ID
    )
    before_audits = env.repository.list_command_audits(
        DEPLOYMENT_ID, SOURCE_ID, limit=16
    )
    monkeypatch.setattr(
        env.repository, "activate_or_rollback_atomic", lambda *_args: outcome
    )
    with pytest.raises(error):
        activate(env, identity)
    after_events, after_lifecycle = env.repository.get_lifecycle_snapshot(
        DEPLOYMENT_ID, SOURCE_ID
    )
    assert after_events == before_events
    assert after_lifecycle == before_lifecycle
    assert env.repository.get_runtime_observation(DEPLOYMENT_ID, SOURCE_ID) is None
    after_audits = env.repository.list_command_audits(
        DEPLOYMENT_ID, SOURCE_ID, limit=20
    )
    if outcome is LifecycleWriteOutcome.CONFLICT:
        assert after_audits[:-1] == before_audits
        assert after_audits[-1].reason is SnapshotCommandReason.CONFLICT
    else:
        assert after_audits == before_audits


def test_activation_atomic_failure_writes_no_event_pointer_audit_or_observation(
    env: Environment,
) -> None:
    _, _, identity = validated_snapshot(env)
    approve(env, identity)
    before_events, before_lifecycle = env.repository.get_lifecycle_snapshot(
        DEPLOYMENT_ID, SOURCE_ID
    )
    before_audits = env.repository.list_command_audits(
        DEPLOYMENT_ID, SOURCE_ID, limit=16
    )
    env.repository.fail_next_atomic_write()
    with pytest.raises(SourceSnapshotUnavailable):
        activate(env, identity)
    assert env.repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID) == (
        before_events,
        before_lifecycle,
    )
    assert (
        env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=16)
        == before_audits
    )
    assert env.repository.get_runtime_observation(DEPLOYMENT_ID, SOURCE_ID) is None


def test_nonmonotonic_observation_race_is_audited_without_pointer_change(
    env: Environment,
) -> None:
    _, first_snapshot_id, _ = active_snapshot(env)
    _, _, second_identity = validated_snapshot(
        env, payload=records_content([("record-1", "Replacement")])
    )
    approve(env, second_identity)
    before_events, before_lifecycle = env.repository.get_lifecycle_snapshot(
        DEPLOYMENT_ID, SOURCE_ID
    )
    before_observation = env.repository.get_runtime_observation(
        DEPLOYMENT_ID, SOURCE_ID
    )
    with pytest.raises(SourceSnapshotConflict):
        activate(env, second_identity)
    after_events, after_lifecycle = env.repository.get_lifecycle_snapshot(
        DEPLOYMENT_ID, SOURCE_ID
    )
    assert after_events == before_events
    assert after_lifecycle == before_lifecycle
    assert after_lifecycle.active_snapshot is not None
    assert after_lifecycle.active_snapshot.snapshot_id == first_snapshot_id
    assert (
        env.repository.get_runtime_observation(DEPLOYMENT_ID, SOURCE_ID)
        == before_observation
    )
    assert (
        env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=24)[
            -1
        ].reason
        is SnapshotCommandReason.CONFLICT
    )


def test_concurrent_activation_recovers_responsible_event_and_atomic_observation(
    env: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, identity = validated_snapshot(env)
    approve(env, identity)
    original = env.repository.activate_or_rollback_atomic

    def concurrent_activate(
        event: SourceSnapshotLifecycleEvent,
        observation: SourceRuntimeObservation,
        applied_audit: SnapshotCommandAuditRecord,
        idempotent_audit: SnapshotCommandAuditRecord,
    ) -> LifecycleWriteOutcome:
        concurrent = replace(event, event_id="concurrent-activation-event")
        assert (
            original(
                concurrent,
                observation,
                atomic_audit(concurrent, SnapshotCommandReason.ACTIVATED, applied=True),
                atomic_audit(
                    concurrent,
                    SnapshotCommandReason.ACTIVATE_IDEMPOTENT,
                    applied=False,
                ),
            )
            is LifecycleWriteOutcome.APPLIED
        )
        return original(event, observation, applied_audit, idempotent_audit)

    monkeypatch.setattr(
        env.repository, "activate_or_rollback_atomic", concurrent_activate
    )
    result = activate(env, identity)
    assert result.event_type is SourceSnapshotEventType.ACTIVATED
    assert result.state is SourceSnapshotState.ACTIVE
    events, _ = env.repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    responsible = next(
        event
        for event in events
        if event.event_type is SourceSnapshotEventType.ACTIVATED
    )
    assert responsible.event_id == "concurrent-activation-event"
    observation = env.repository.get_runtime_observation(DEPLOYMENT_ID, SOURCE_ID)
    assert observation is not None
    assert observation.active_snapshot_id == identity.snapshot_id
    assert (
        env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=16)[
            -1
        ].reason
        is SnapshotCommandReason.ACTIVATE_IDEMPOTENT
    )


def test_concurrent_rollback_recovers_unique_responsible_event_and_observation(
    env: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, _ = active_pair(env)
    env.clock.value += timedelta(seconds=1)
    original = env.repository.activate_or_rollback_atomic

    def concurrent_rollback(
        event: SourceSnapshotLifecycleEvent,
        observation: SourceRuntimeObservation,
        applied_audit: SnapshotCommandAuditRecord,
        idempotent_audit: SnapshotCommandAuditRecord,
    ) -> LifecycleWriteOutcome:
        concurrent = replace(event, event_id="concurrent-rollback-event")
        assert (
            original(
                concurrent,
                observation,
                atomic_audit(
                    concurrent, SnapshotCommandReason.ROLLED_BACK, applied=True
                ),
                atomic_audit(
                    concurrent,
                    SnapshotCommandReason.ROLLBACK_IDEMPOTENT,
                    applied=False,
                ),
            )
            is LifecycleWriteOutcome.APPLIED
        )
        return original(event, observation, applied_audit, idempotent_audit)

    monkeypatch.setattr(
        env.repository, "activate_or_rollback_atomic", concurrent_rollback
    )
    result = rollback(
        env,
        first[2],
        reason="concurrent rollback reason",
        audit_event_id="authz-current-concurrent-rollback",
    )
    assert result.event_type is SourceSnapshotEventType.ROLLED_BACK
    assert result.state is SourceSnapshotState.ACTIVE
    assert result.active_snapshot_id == first[1]
    events, _ = env.repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    rollback_events = tuple(
        event
        for event in events
        if event.event_type is SourceSnapshotEventType.ROLLED_BACK
    )
    assert len(rollback_events) == 1
    assert rollback_events[0].event_id == "concurrent-rollback-event"
    assert result.occurred_at == rollback_events[0].occurred_at
    observation = env.repository.get_runtime_observation(DEPLOYMENT_ID, SOURCE_ID)
    assert observation is not None
    assert observation.active_snapshot_id == first[1]
    audit = env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=32)[-1]
    assert audit.reason is SnapshotCommandReason.ROLLBACK_IDEMPOTENT
    assert audit.authorization_event_id == "authz-current-concurrent-rollback"

    monkeypatch.setattr(env.repository, "activate_or_rollback_atomic", original)
    with pytest.raises(SourceSnapshotConflict):
        rollback(env, first[2], reason="different concurrent rollback reason")
    assert (
        env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=40)[
            -1
        ].reason
        is SnapshotCommandReason.CONFLICT
    )


@pytest.mark.parametrize("command_name", ["approve", "activate", "rollback"])
@pytest.mark.parametrize("fault", ["raise", "conflict", "applied"])
def test_governance_retry_repository_faults_never_return_success(
    env: Environment,
    monkeypatch: pytest.MonkeyPatch,
    command_name: str,
    fault: str,
) -> None:
    if command_name == "approve":
        _, _, identity = validated_snapshot(env)
        approve(env, identity, reason="stable retry reason")
        command = env.service.approve
        request = governance_authorized(Operation.SOURCE_SNAPSHOT_APPROVE, identity)
        repository_method = "append_lifecycle_atomic"
    elif command_name == "activate":
        _, _, identity = active_snapshot(env, activation_reason="stable retry reason")
        command = env.service.activate
        request = governance_authorized(Operation.SOURCE_SNAPSHOT_ACTIVATE, identity)
        repository_method = "activate_or_rollback_atomic"
    else:
        first, _ = active_pair(env)
        identity = first[2]
        env.clock.value += timedelta(seconds=1)
        rollback(env, identity, reason="stable retry reason")
        command = env.service.rollback
        request = governance_authorized(Operation.SOURCE_SNAPSHOT_ROLLBACK, identity)
        repository_method = "activate_or_rollback_atomic"
    before_events, before_lifecycle = env.repository.get_lifecycle_snapshot(
        DEPLOYMENT_ID, SOURCE_ID
    )
    before_observation = env.repository.get_runtime_observation(
        DEPLOYMENT_ID, SOURCE_ID
    )

    def faulty(*_args: Any) -> LifecycleWriteOutcome:
        if fault == "raise":
            raise RuntimeError("secret")
        if fault == "conflict":
            return LifecycleWriteOutcome.CONFLICT
        return LifecycleWriteOutcome.APPLIED

    monkeypatch.setattr(env.repository, repository_method, faulty)
    expected = (
        SourceSnapshotConflict if fault == "conflict" else SourceSnapshotUnavailable
    )
    with pytest.raises(expected) as caught:
        command(
            request,
            source_id=SOURCE_ID,
            identity=identity,
            reason="stable retry reason",
        )
    assert "secret" not in str(caught.value)
    assert env.repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID) == (
        before_events,
        before_lifecycle,
    )
    assert (
        env.repository.get_runtime_observation(DEPLOYMENT_ID, SOURCE_ID)
        == before_observation
    )
    if fault == "conflict":
        assert (
            env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=40)[
                -1
            ].reason
            is SnapshotCommandReason.CONFLICT
        )


def test_clock_event_command_and_failure_audit_failures_are_safe(
    env: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, identity = validated_snapshot(env)
    audits_before = env.repository.list_command_audits(
        DEPLOYMENT_ID, SOURCE_ID, limit=16
    )
    events_before, lifecycle_before = env.repository.get_lifecycle_snapshot(
        DEPLOYMENT_ID, SOURCE_ID
    )
    env.clock.value -= timedelta(seconds=1)
    with pytest.raises(SourceSnapshotUnavailable):
        approve(env, identity)
    assert (
        env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=16)
        == audits_before
    )
    env.clock.value += timedelta(seconds=1)
    monkeypatch.setattr(env.service, "_event_id_factory", lambda: "not safe")
    with pytest.raises(SourceSnapshotUnavailable):
        approve(env, identity)
    monkeypatch.setattr(env.service, "_event_id_factory", Ids("event-safe"))
    monkeypatch.setattr(
        env.service,
        "_command_id_factory",
        lambda: (_ for _ in ()).throw(RuntimeError("secret")),
    )
    with pytest.raises(SourceSnapshotUnavailable):
        approve(env, identity)
    assert env.repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID) == (
        events_before,
        lifecycle_before,
    )
    assert (
        env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=16)
        == audits_before
    )
    monkeypatch.setattr(env.service, "_command_id_factory", Ids("command-safe"))
    snapshot_id = parse(env, ingest(env, payload=content(record_id="record-2")))
    unvalidated = immutable_identity(env, snapshot_id)
    monkeypatch.setattr(
        env.repository,
        "append_command_audit",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("secret")),
    )
    with pytest.raises(SourceSnapshotUnavailable) as caught:
        approve(env, unvalidated)
    assert "secret" not in str(caught.value)


def test_governance_rechecks_active_registration(env: Environment) -> None:
    _, _, identity = validated_snapshot(env)
    env.registry.current = registration(active=False)
    with pytest.raises(SourceSnapshotNotFound):
        approve(env, identity)


def test_corrupt_validation_evidence_blocks_approval_and_rollback(
    env: Environment,
) -> None:
    first, _ = active_pair(env)
    events, lifecycle = env.repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    validation = next(
        event
        for event in events
        if event.event_type is SourceSnapshotEventType.VALIDATED
        and event.snapshot is not None
        and event.snapshot.snapshot_id == first[1]
    )
    report = validation.validation_report
    assert report is not None
    assert lifecycle.active_snapshot is not None
    scope = (DEPLOYMENT_ID, SOURCE_ID)
    audit_ids_before = tuple(
        audit.command_event_id
        for audit in env.repository._audits[scope]  # noqa: SLF001
    )
    event_ids_before = tuple(
        event.event_id
        for event in env.repository._events[scope]  # noqa: SLF001
    )
    object.__setattr__(report, "content_hash", f"sha256:{'f' * 64}")
    env.clock.value += timedelta(seconds=1)
    with pytest.raises(SourceSnapshotUnavailable):
        rollback(env, first[2])
    assert (
        tuple(
            event.event_id
            for event in env.repository._events[scope]  # noqa: SLF001
        )
        == event_ids_before
    )
    assert (
        tuple(
            audit.command_event_id
            for audit in env.repository._audits[scope]  # noqa: SLF001
        )
        == audit_ids_before
    )
    assert (
        env.repository._observations[scope].active_snapshot_id  # noqa: SLF001
        == lifecycle.active_snapshot.snapshot_id
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        env.repository.get_lifecycle_snapshot(*scope)
    with pytest.raises(SourceSnapshotPersistenceError):
        env.repository.list_command_audits(*scope, limit=32)


def test_rollback_of_never_active_validated_snapshot_is_audited_conflict(
    env: Environment,
) -> None:
    _, _, identity = validated_snapshot(env)
    with pytest.raises(SourceSnapshotConflict):
        rollback(env, identity)
    assert (
        env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=16)[
            -1
        ].reason
        is SnapshotCommandReason.CONFLICT
    )


def test_impossible_governance_projection_states_fail_closed(
    env: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, snapshot_id, identity = validated_snapshot(env)
    snapshot = env.repository.get_snapshot(DEPLOYMENT_ID, SOURCE_ID, snapshot_id)
    events, lifecycle = env.repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    assert snapshot is not None
    quarantined_projection = SimpleNamespace(
        creator_for=lifecycle.creator_for,
        state_for=lambda _reference: SourceSnapshotState.QUARANTINED,
        active_snapshot=lifecycle.active_snapshot,
    )
    monkeypatch.setattr(
        env.service,
        "_snapshot",
        lambda _source_id: (events, quarantined_projection),
    )
    with pytest.raises(SourceSnapshotConflict):
        approve(env, identity)

    monkeypatch.setattr(
        env.service, "_snapshot", lambda _source_id: (events, lifecycle)
    )
    approve(env, identity)
    approved_events, approved_lifecycle = env.repository.get_lifecycle_snapshot(
        DEPLOYMENT_ID, SOURCE_ID
    )
    fake_validation = cast(
        SourceSnapshotLifecycleEvent,
        SimpleNamespace(
            event_type=SourceSnapshotEventType.VALIDATED,
            validation_report=None,
        ),
    )
    monkeypatch.setattr(
        env.service, "_verified_validation_event", lambda *_args: fake_validation
    )
    with pytest.raises(SourceSnapshotUnavailable):
        activate(env, identity)

    monkeypatch.setattr(
        env.service,
        "_snapshot",
        lambda _source_id: (approved_events, approved_lifecycle),
    )
    fake_failure = cast(
        SourceSnapshotLifecycleEvent,
        SimpleNamespace(event_type=SourceSnapshotEventType.VALIDATION_FAILED),
    )
    monkeypatch.setattr(
        env.service, "_verified_validation_event", lambda *_args: fake_failure
    )
    with pytest.raises(SourceSnapshotUnavailable):
        env.service.rollback(
            governance_authorized(Operation.SOURCE_SNAPSHOT_ROLLBACK, identity),
            source_id=SOURCE_ID,
            identity=identity,
            reason="impossible rollback",
        )


def test_defensive_governance_helpers_reject_corrupt_internal_values(
    env: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, snapshot_id, identity = validated_snapshot(env)
    snapshot = env.repository.get_snapshot(DEPLOYMENT_ID, SOURCE_ID, snapshot_id)
    events, lifecycle = env.repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    assert snapshot is not None
    request = governance_authorized(Operation.SOURCE_SNAPSHOT_APPROVE, identity)
    with pytest.raises(SourceSnapshotAuthorizationBindingDenied):
        env.service._require_authorized(  # noqa: SLF001
            request, Operation.SOURCE_SNAPSHOT_APPROVE, SOURCE_ID
        )
    with pytest.raises(SourceSnapshotAuthorizationBindingDenied):
        env.service._require_authorized(  # noqa: SLF001
            authorized(Operation.SOURCE_SNAPSHOT_PARSE),
            Operation.SOURCE_SNAPSHOT_PARSE,
            SOURCE_ID,
            identity=identity,
        )
    with pytest.raises(SourceSnapshotNotFound):
        env.service._snapshot_for_identity(SOURCE_ID, cast(Any, object()))  # noqa: SLF001

    corrupt_identity = identity.model_copy()
    object.__setattr__(corrupt_identity, "content_hash", object())
    with pytest.raises(SourceSnapshotNotFound):
        env.service._snapshot_for_identity(SOURCE_ID, corrupt_identity)  # noqa: SLF001

    monkeypatch.setattr(
        source_app,
        "SourceSnapshotRef",
        lambda *_args: SimpleNamespace(
            snapshot_id="different", content_hash="different"
        ),
    )
    with pytest.raises(SourceSnapshotNotFound):
        env.service._snapshot_for_identity(SOURCE_ID, identity)  # noqa: SLF001
    monkeypatch.undo()

    with pytest.raises(SourceSnapshotUnavailable):
        env.service._require_creator_separation(  # noqa: SLF001
            request, snapshot, (), lifecycle, NOW
        )
    with pytest.raises(SourceSnapshotUnavailable):
        env.service._verified_validation_event(snapshot, ())  # noqa: SLF001

    class RaisingReport:
        @property
        def snapshot(self) -> object:
            raise ValueError("corrupt report")

    broken_validation = cast(
        SourceSnapshotLifecycleEvent,
        SimpleNamespace(
            event_type=SourceSnapshotEventType.VALIDATED,
            snapshot=snapshot.reference(),
            raw_object=snapshot.raw_object,
            validation_report=RaisingReport(),
        ),
    )
    with pytest.raises(SourceSnapshotUnavailable):
        env.service._verified_validation_event(  # noqa: SLF001
            snapshot, (broken_validation,)
        )
    validation = next(
        event
        for event in events
        if event.event_type is SourceSnapshotEventType.VALIDATED
    )
    with pytest.raises(SourceSnapshotUnavailable):
        env.service._unique_governance_event(  # noqa: SLF001
            (validation, validation),
            snapshot.reference(),
            SourceSnapshotEventType.VALIDATED,
        )
    with pytest.raises(SourceSnapshotUnavailable):
        env.service._responsible_event(validation, ())  # noqa: SLF001


def test_observation_and_governance_result_construction_fail_closed(
    env: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, snapshot_id, identity = validated_snapshot(env)
    snapshot = env.repository.get_snapshot(DEPLOYMENT_ID, SOURCE_ID, snapshot_id)
    events, lifecycle = env.repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    assert snapshot is not None
    event = events[-1]
    other = RawObjectMetadata.from_bytes(
        deployment_id=DEPLOYMENT_ID,
        source_id=SOURCE_ID,
        original_name="other.json",
        media_type=SYNTHETIC_MEDIA_TYPE,
        charset=SYNTHETIC_CHARSET,
        retrieved_at=NOW,
        effective_from=None,
        content=b"other",
    )
    monkeypatch.setattr(env.service, "_raw_metadata", lambda *_args: other)
    with pytest.raises(SourceSnapshotUnavailable):
        env.service._observation(snapshot, event)  # noqa: SLF001
    monkeypatch.undo()

    monkeypatch.setattr(
        source_app,
        "SourceRuntimeObservation",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("corrupt observation")),
    )
    with pytest.raises(SourceSnapshotUnavailable):
        env.service._observation(snapshot, event)  # noqa: SLF001
    monkeypatch.undo()

    wrong_event = cast(
        SourceSnapshotLifecycleEvent,
        SimpleNamespace(snapshot=None, previous_active_snapshot=None),
    )
    with pytest.raises(SourceSnapshotUnavailable):
        env.service._governance_result(snapshot, wrong_event, lifecycle)  # noqa: SLF001
    missing_state = cast(
        Any,
        SimpleNamespace(state_for=lambda _reference: None, active_snapshot=None),
    )
    with pytest.raises(SourceSnapshotUnavailable):
        env.service._governance_result(snapshot, event, missing_state)  # noqa: SLF001
    monkeypatch.setattr(
        source_app,
        "SnapshotGovernanceCommandResult",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("corrupt result")),
    )
    with pytest.raises(SourceSnapshotUnavailable):
        env.service._governance_result(snapshot, event, lifecycle)  # noqa: SLF001


def test_ingest_retry_recovers_retrieved_and_never_returns_retrieved(
    env: Environment,
) -> None:
    payload = content()
    metadata = RawObjectMetadata.from_bytes(
        deployment_id=DEPLOYMENT_ID,
        source_id=SOURCE_ID,
        original_name="synthetic.json",
        media_type=SYNTHETIC_MEDIA_TYPE,
        charset=SYNTHETIC_CHARSET,
        retrieved_at=NOW - timedelta(minutes=1),
        effective_from=None,
        content=payload,
    )
    env.store.put_exact(metadata, payload)
    event = SourceSnapshotLifecycleEvent(
        sequence=1,
        event_id="interrupted-retrieval",
        deployment_id=DEPLOYMENT_ID,
        source_id=SOURCE_ID,
        event_type=SourceSnapshotEventType.RETRIEVED,
        raw_object=metadata.reference(),
        snapshot=None,
        previous_active_snapshot=None,
        actor_id="source-operator-1",
        actor_type=LifecycleActorType.SERVICE,
        reason="source object retrieved",
        occurred_at=NOW,
    )
    applied = env.service._audit_pair(  # noqa: SLF001
        authorized(Operation.SOURCE_SNAPSHOT_INGEST), event
    )
    replay = env.service._audit_pair(  # noqa: SLF001
        authorized(Operation.SOURCE_SNAPSHOT_INGEST), event, idempotent=True
    )
    env.repository.save_retrieval_atomic(metadata, event, applied, replay)

    result = env.service.ingest(
        authorized(Operation.SOURCE_SNAPSHOT_INGEST),
        source_id=SOURCE_ID,
        original_name="synthetic.json",
        media_type=SYNTHETIC_MEDIA_TYPE,
        charset=SYNTHETIC_CHARSET,
        retrieved_at=NOW - timedelta(minutes=1),
        effective_from=None,
        content=payload,
    )
    assert result.object_id == metadata.object_id
    assert result.state is RawObjectState.QUARANTINED


def test_completed_ingest_retry_audits_both_idempotent_subtransitions(
    env: Environment,
) -> None:
    object_id = ingest(env)
    retried = ingest(env)
    assert retried == object_id
    _, lifecycle = env.repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    assert lifecycle.raw_objects[0].state is RawObjectState.QUARANTINED
    audits = env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=10)
    assert [audit.reason for audit in audits[-2:]] == [
        SnapshotCommandReason.RETRIEVE_IDEMPOTENT,
        SnapshotCommandReason.QUARANTINE_IDEMPOTENT,
    ]


def test_clock_rollback_never_appends_an_idempotent_audit(env: Environment) -> None:
    object_id = ingest(env)
    snapshot_id = parse(env, object_id)
    env.service.validate(
        authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
        source_id=SOURCE_ID,
        snapshot_id=snapshot_id,
    )
    before = env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=20)
    env.clock.value = NOW - timedelta(seconds=1)
    with pytest.raises(SourceSnapshotUnavailable):
        ingest(env)
    with pytest.raises(SourceSnapshotUnavailable):
        env.service.parse(
            authorized(Operation.SOURCE_SNAPSHOT_PARSE),
            source_id=SOURCE_ID,
            object_id=object_id,
        )
    with pytest.raises(SourceSnapshotUnavailable):
        env.service.validate(
            authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
            source_id=SOURCE_ID,
            snapshot_id=snapshot_id,
        )
    assert (
        env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=20) == before
    )
    env.clock.value = NOW
    assert ingest(env) == object_id


@pytest.mark.parametrize(
    "replacement",
    [
        {"operation": Operation.SOURCE_SNAPSHOT_PARSE},
        {"context_tenant": "other-tenant"},
        {"target_tenant": "other-tenant"},
        {"target_type": "source_snapshot"},
        {"target_id": "source-wrong"},
        {"principal": actor(tenant_id="other-tenant")},
        {"principal": actor(actor_type=ActorType.AGENT)},
        {"principal": actor(scopes=frozenset())},
        {"principal": actor(roles=frozenset())},
        {"audit_event_id": "not safe"},
    ],
)
def test_forged_authorized_requests_cannot_bypass_policy(
    env: Environment, replacement: dict[str, Any]
) -> None:
    operation = cast(
        Operation,
        replacement.pop("operation", Operation.SOURCE_SNAPSHOT_INGEST),
    )
    request = authorized(operation, **replacement)
    with pytest.raises(SourceSnapshotAuthorizationBindingDenied):
        env.service.ingest(
            request,
            source_id=SOURCE_ID,
            original_name="synthetic.json",
            media_type=SYNTHETIC_MEDIA_TYPE,
            charset=SYNTHETIC_CHARSET,
            retrieved_at=NOW,
            effective_from=None,
            content=content(),
        )
    assert env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=2) == ()


def test_target_hash_is_bounded_collision_resistant_and_validates_components() -> None:
    first = source_authorization_target_id("a-b", "c", "d")
    second = source_authorization_target_id("a", "b-c", "d")
    assert first != second
    assert first.startswith("source-")
    assert len(first) == 71
    with pytest.raises(ValueError):
        source_authorization_target_id("bad id", SOURCE_SET_ID, SOURCE_ID)


@pytest.mark.parametrize(
    "current",
    [None, registration(active=False), registration(source_set_id="other-set")],
)
def test_source_must_be_active_in_exact_configured_set(
    env: Environment, current: SourceRegistration | None
) -> None:
    env.registry.current = current
    with pytest.raises(SourceSnapshotNotFound):
        ingest(env)


def test_future_retrieval_is_rejected_but_future_effective_time_is_allowed(
    env: Environment,
) -> None:
    with pytest.raises(SourceSnapshotRequestInvalid):
        ingest(env, retrieved_at=NOW + timedelta(seconds=1))
    object_id = ingest(env, effective_from=NOW + timedelta(days=365))
    assert object_id.startswith("raw-")


def test_parser_rejection_is_safe_audited_and_does_not_write_parsed_event(
    env: Environment,
) -> None:
    secret = "sensitive-source-value"  # pragma: allowlist secret
    object_id = ingest(env, payload=b'{"secret":"' + secret.encode() + b'"}')
    with pytest.raises(SourceSnapshotParseRejected) as caught:
        parse(env, object_id)
    assert caught.value.code is FiniteParserErrorCode.UNKNOWN_FIELD
    assert secret not in str(caught.value)
    events, _ = env.repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    assert SourceSnapshotEventType.PARSED not in {event.event_type for event in events}
    audits = env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=10)
    assert audits[-1].reason is SnapshotCommandReason.PARSER_REJECTED


def test_media_binding_failure_is_audited_without_running_parser(
    env: Environment,
) -> None:
    object_id = ingest(env, media_type="text/plain")
    with pytest.raises(SourceSnapshotParseRejected) as caught:
        parse(env, object_id)
    assert caught.value.code is FiniteParserErrorCode.INVALID_INPUT
    audits = env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=10)
    assert audits[-1].reason is SnapshotCommandReason.MEDIA_BINDING_DENIED


def test_validation_failure_is_successful_operational_result(env: Environment) -> None:
    snapshot_id = parse(env, ingest(env, payload=content(declared_count=2)))
    result = env.service.validate(
        authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
        source_id=SOURCE_ID,
        snapshot_id=snapshot_id,
    )
    assert not result.passed
    assert result.state is SourceSnapshotState.QUARANTINED
    assert result.reason_codes
    audits = env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=10)
    assert audits[-1].reason is SnapshotCommandReason.VALIDATION_FAILED


def test_validation_result_reason_bound_covers_closed_enum(env: Environment) -> None:
    snapshot_id = parse(env, ingest(env))
    result = env.service.validate(
        authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
        source_id=SOURCE_ID,
        snapshot_id=snapshot_id,
    )
    payload = result.model_dump()
    payload["reason_codes"] = list(ValidationReasonCode)
    assert len(type(result).model_validate(payload).reason_codes) == len(
        ValidationReasonCode
    )


class BoundParser:
    parser_id = FiniteParserId.SYNTHETIC_JSON_V1
    parser_version = SYNTHETIC_PARSER_VERSION
    media_type = SYNTHETIC_MEDIA_TYPE
    charset = SYNTHETIC_CHARSET

    def __init__(self, result: object) -> None:
        self.result = result
        self.calls = 0

    def parse(self, raw: bytes) -> FiniteParserOutput:
        del raw
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return cast(FiniteParserOutput, self.result)


def service_with_parser(env: Environment, parser: Any) -> SourceSnapshotService:
    return SourceSnapshotService(
        env.repository,
        env.store,
        env.registry,
        parser,
        deployment_id=DEPLOYMENT_ID,
        source_set_id=SOURCE_SET_ID,
        control_tenant_id=TENANT_ID,
        clock=env.clock,
        event_id_factory=env.event_ids,
        command_id_factory=env.command_ids,
    )


def test_parser_contract_corruption_and_unexpected_failure_are_unavailable(
    env: Environment,
) -> None:
    object_id = ingest(env)
    with pytest.raises(SourceSnapshotUnavailable):
        service_with_parser(env, BoundParser(object())).parse(
            authorized(Operation.SOURCE_SNAPSHOT_PARSE),
            source_id=SOURCE_ID,
            object_id=object_id,
        )
    with pytest.raises(SourceSnapshotUnavailable):
        service_with_parser(env, BoundParser(RuntimeError("secret"))).parse(
            authorized(Operation.SOURCE_SNAPSHOT_PARSE),
            source_id=SOURCE_ID,
            object_id=object_id,
        )


def test_wrong_schema_output_is_stable_parse_rejection(env: Environment) -> None:
    object_id = ingest(env)
    output = env.parser.parse(content())
    wrong = FiniteParserOutput(
        "wrong-schema", output.declared_record_count, output.records
    )
    with pytest.raises(SourceSnapshotParseRejected) as caught:
        service_with_parser(env, BoundParser(wrong)).parse(
            authorized(Operation.SOURCE_SNAPSHOT_PARSE),
            source_id=SOURCE_ID,
            object_id=object_id,
        )
    assert caught.value.code is FiniteParserErrorCode.SCHEMA_MISMATCH


def test_unknown_artifacts_and_invalid_ids_are_generic_not_found(
    env: Environment,
) -> None:
    with pytest.raises(SourceSnapshotNotFound):
        env.service.parse(
            authorized(Operation.SOURCE_SNAPSHOT_PARSE),
            source_id=SOURCE_ID,
            object_id="missing-raw",
        )
    with pytest.raises(SourceSnapshotNotFound):
        env.service.validate(
            authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
            source_id=SOURCE_ID,
            snapshot_id="not safe",
        )


def test_registration_infrastructure_failure_is_unavailable(env: Environment) -> None:
    env.registry.failure = RuntimeError("secret")
    with pytest.raises(SourceSnapshotUnavailable) as caught:
        ingest(env)
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize(
    ("fault", "error"),
    [
        ("store_raise", SourceSnapshotUnavailable),
        ("store_conflict", SourceSnapshotConflict),
        ("store_unknown", SourceSnapshotUnavailable),
        ("snapshot_raise", SourceSnapshotUnavailable),
        ("retrieval_raise", SourceSnapshotUnavailable),
        ("retrieval_conflict", SourceSnapshotConflict),
        ("retrieval_unknown", SourceSnapshotUnavailable),
        ("retrieval_no_write", SourceSnapshotUnavailable),
        ("quarantine_raise", SourceSnapshotUnavailable),
        ("quarantine_conflict", SourceSnapshotConflict),
        ("quarantine_unknown", SourceSnapshotUnavailable),
    ],
)
def test_ingest_infrastructure_conflict_and_unknown_outcomes_fail_closed(
    env: Environment,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
    error: type[Exception],
) -> None:
    original_put = env.store.put_exact

    def put(metadata: RawObjectMetadata, raw: bytes) -> ArtifactWriteOutcome:
        if fault == "store_raise":
            raise RuntimeError("secret store detail")
        if fault == "store_conflict":
            original_put(metadata, raw)
            return ArtifactWriteOutcome.CONFLICT
        if fault == "store_unknown":
            return cast(ArtifactWriteOutcome, "UNKNOWN")
        return original_put(metadata, raw)

    original_snapshot = env.repository.get_lifecycle_snapshot

    def lifecycle(deployment_id: str, source_id: str) -> tuple[Any, Any]:
        if fault == "snapshot_raise":
            raise RuntimeError("secret repository detail")
        return original_snapshot(deployment_id, source_id)

    def retrieval(*_args: Any, **_kwargs: Any) -> ArtifactWriteOutcome:
        if fault == "retrieval_raise":
            raise RuntimeError("secret repository detail")
        if fault == "retrieval_conflict":
            return ArtifactWriteOutcome.CONFLICT
        if fault == "retrieval_unknown":
            return cast(ArtifactWriteOutcome, "UNKNOWN")
        if fault == "retrieval_no_write":
            return ArtifactWriteOutcome.APPLIED
        raise AssertionError("unexpected retrieval fault fixture")

    original_quarantine = env.repository.append_lifecycle_atomic

    def quarantine(*args: Any, **kwargs: Any) -> LifecycleWriteOutcome:
        if fault == "quarantine_raise":
            raise RuntimeError("secret repository detail")
        if fault == "quarantine_conflict":
            return LifecycleWriteOutcome.CONFLICT
        if fault == "quarantine_unknown":
            return cast(LifecycleWriteOutcome, "UNKNOWN")
        return original_quarantine(*args, **kwargs)

    if fault.startswith("store"):
        monkeypatch.setattr(env.store, "put_exact", put)
    elif fault == "snapshot_raise":
        monkeypatch.setattr(env.repository, "get_lifecycle_snapshot", lifecycle)
    elif fault.startswith("retrieval"):
        monkeypatch.setattr(env.repository, "save_retrieval_atomic", retrieval)
    else:
        monkeypatch.setattr(env.repository, "append_lifecycle_atomic", quarantine)
    with pytest.raises(error) as caught:
        ingest(env)
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize(
    ("fault", "error"),
    [
        ("raw_read_raise", SourceSnapshotUnavailable),
        ("raw_read_invalid", SourceSnapshotUnavailable),
        ("verified_read_raise", SourceSnapshotUnavailable),
        ("snapshot_create_raise", SourceSnapshotUnavailable),
        ("parsed_save_raise", SourceSnapshotUnavailable),
        ("parsed_save_conflict", SourceSnapshotConflict),
        ("parsed_save_unknown", SourceSnapshotUnavailable),
        ("invalid_lifecycle", SourceSnapshotConflict),
    ],
)
def test_parse_infrastructure_corruption_and_conflicts_fail_closed(
    env: Environment,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
    error: type[Exception],
) -> None:
    object_id = ingest(env)
    if fault == "raw_read_raise":
        monkeypatch.setattr(
            env.repository,
            "get_raw_metadata",
            lambda *_args: (_ for _ in ()).throw(RuntimeError("secret")),
        )
    elif fault == "raw_read_invalid":
        other = RawObjectMetadata.from_bytes(
            deployment_id=DEPLOYMENT_ID,
            source_id="other-source",
            original_name="synthetic.json",
            media_type=SYNTHETIC_MEDIA_TYPE,
            charset=SYNTHETIC_CHARSET,
            retrieved_at=NOW,
            effective_from=None,
            content=content(),
        )
        monkeypatch.setattr(env.repository, "get_raw_metadata", lambda *_args: other)
    elif fault == "verified_read_raise":
        monkeypatch.setattr(
            env.store,
            "get_verified",
            lambda *_args: (_ for _ in ()).throw(RuntimeError("secret")),
        )
    elif fault == "snapshot_create_raise":
        monkeypatch.setattr(
            ParsedSourceSnapshot,
            "create",
            lambda **_kwargs: (_ for _ in ()).throw(ValueError("secret")),
        )
    elif fault.startswith("parsed_save"):

        def save(*_args: Any, **_kwargs: Any) -> ArtifactWriteOutcome:
            if fault == "parsed_save_raise":
                raise RuntimeError("secret")
            if fault == "parsed_save_conflict":
                return ArtifactWriteOutcome.CONFLICT
            return cast(ArtifactWriteOutcome, "UNKNOWN")

        monkeypatch.setattr(env.repository, "save_parsed_snapshot_atomic", save)
    else:
        parse(env, object_id)
        monkeypatch.setattr(env.service, "_parsed_retry", lambda *_args: None)
    with pytest.raises(error) as caught:
        parse(env, object_id)
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize(
    ("fault", "error"),
    [
        ("snapshot_read_raise", SourceSnapshotUnavailable),
        ("snapshot_read_invalid", SourceSnapshotUnavailable),
        ("validation_compute_raise", SourceSnapshotUnavailable),
        ("validation_append_raise", SourceSnapshotUnavailable),
        ("validation_append_conflict", SourceSnapshotConflict),
        ("validation_append_unknown", SourceSnapshotUnavailable),
        ("invalid_lifecycle", SourceSnapshotConflict),
    ],
)
def test_validate_infrastructure_corruption_and_conflicts_fail_closed(
    env: Environment,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
    error: type[Exception],
) -> None:
    snapshot_id = parse(env, ingest(env))
    if fault == "snapshot_read_raise":
        monkeypatch.setattr(
            env.repository,
            "get_snapshot",
            lambda *_args: (_ for _ in ()).throw(RuntimeError("secret")),
        )
    elif fault == "snapshot_read_invalid":
        monkeypatch.setattr(env.repository, "get_snapshot", lambda *_args: object())
    elif fault == "validation_compute_raise":
        monkeypatch.setattr(
            source_app,
            "validate_snapshot",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("secret")),
        )
    elif fault.startswith("validation_append"):

        def append(*_args: Any, **_kwargs: Any) -> LifecycleWriteOutcome:
            if fault == "validation_append_raise":
                raise RuntimeError("secret")
            if fault == "validation_append_conflict":
                return LifecycleWriteOutcome.CONFLICT
            return cast(LifecycleWriteOutcome, "UNKNOWN")

        monkeypatch.setattr(env.repository, "append_lifecycle_atomic", append)
    else:
        env.service.validate(
            authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
            source_id=SOURCE_ID,
            snapshot_id=snapshot_id,
        )
        monkeypatch.setattr(env.service, "_validation_retry", lambda *_args: None)
    with pytest.raises(error) as caught:
        env.service.validate(
            authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
            source_id=SOURCE_ID,
            snapshot_id=snapshot_id,
        )
    assert "secret" not in str(caught.value)


def test_validation_atomic_predecessor_race_maps_to_audited_conflict(
    env: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_id = parse(env, ingest(env))
    before_events, _ = env.repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    monkeypatch.setattr(
        env.repository,
        "append_lifecycle_atomic",
        lambda *_args, **_kwargs: LifecycleWriteOutcome.CONFLICT,
    )
    with pytest.raises(SourceSnapshotConflict):
        env.service.validate(
            authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
            source_id=SOURCE_ID,
            snapshot_id=snapshot_id,
        )
    after_events, _ = env.repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    assert after_events == before_events
    audit = env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=10)[-1]
    assert audit.reason is SnapshotCommandReason.CONFLICT
    assert audit.authorization_event_id == "authorization-source_snapshot_validate"


@pytest.mark.parametrize(
    ("fault", "error"),
    [
        ("raise", SourceSnapshotUnavailable),
        ("unexpected", SourceSnapshotUnavailable),
        ("conflict", SourceSnapshotConflict),
    ],
)
def test_parse_retry_atomic_failure_never_returns_success(
    env: Environment,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
    error: type[Exception],
) -> None:
    object_id = ingest(env)
    parse(env, object_id)

    def save(*_args: Any, **_kwargs: Any) -> ArtifactWriteOutcome:
        if fault == "raise":
            raise RuntimeError("secret")
        if fault == "conflict":
            return ArtifactWriteOutcome.CONFLICT
        return ArtifactWriteOutcome.APPLIED

    monkeypatch.setattr(env.repository, "save_parsed_snapshot_atomic", save)
    with pytest.raises(error):
        parse(env, object_id)
    if fault == "conflict":
        assert (
            env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=10)[
                -1
            ].reason
            is SnapshotCommandReason.CONFLICT
        )


@pytest.mark.parametrize(
    ("fault", "error"),
    [
        ("raise", SourceSnapshotUnavailable),
        ("unexpected", SourceSnapshotUnavailable),
        ("conflict", SourceSnapshotConflict),
    ],
)
def test_validation_retry_atomic_failure_never_returns_success(
    env: Environment,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
    error: type[Exception],
) -> None:
    snapshot_id = parse(env, ingest(env))
    env.service.validate(
        authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
        source_id=SOURCE_ID,
        snapshot_id=snapshot_id,
    )

    def append(*_args: Any, **_kwargs: Any) -> LifecycleWriteOutcome:
        if fault == "raise":
            raise RuntimeError("secret")
        if fault == "conflict":
            return LifecycleWriteOutcome.CONFLICT
        return LifecycleWriteOutcome.APPLIED

    monkeypatch.setattr(env.repository, "append_lifecycle_atomic", append)
    with pytest.raises(error):
        env.service.validate(
            authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
            source_id=SOURCE_ID,
            snapshot_id=snapshot_id,
        )
    if fault == "conflict":
        assert (
            env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=10)[
                -1
            ].reason
            is SnapshotCommandReason.CONFLICT
        )


def test_validation_failure_retry_is_audited_with_original_report_time(
    env: Environment,
) -> None:
    snapshot_id = parse(env, ingest(env, payload=content(declared_count=2)))
    first = env.service.validate(
        authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
        source_id=SOURCE_ID,
        snapshot_id=snapshot_id,
    )
    second = env.service.validate(
        authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
        source_id=SOURCE_ID,
        snapshot_id=snapshot_id,
    )
    assert second == first
    assert (
        env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=10)[
            -1
        ].reason
        is SnapshotCommandReason.VALIDATION_FAILURE_IDEMPOTENT
    )


def test_validation_after_activation_is_not_misclassified_as_retry(
    env: Environment,
) -> None:
    object_id = ingest(env)
    snapshot_id = parse(env, object_id)
    env.service.validate(
        authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
        source_id=SOURCE_ID,
        snapshot_id=snapshot_id,
    )
    approve_and_activate(env, object_id=object_id, snapshot_id=snapshot_id)
    before = env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=20)
    with pytest.raises(SourceSnapshotConflict):
        env.service.validate(
            authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
            source_id=SOURCE_ID,
            snapshot_id=snapshot_id,
        )
    after = env.repository.list_command_audits(DEPLOYMENT_ID, SOURCE_ID, limit=20)
    assert len(after) == len(before) + 1
    assert after[-1].reason is SnapshotCommandReason.CONFLICT
    assert after[-1].authorization_event_id == "authorization-source_snapshot_validate"


def test_second_snapshot_validates_against_exact_active_predecessor(
    env: Environment,
) -> None:
    first_object = ingest(
        env, payload=records_content([("record-1", "Original Entity")])
    )
    first_snapshot = parse(env, first_object)
    env.service.validate(
        authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
        source_id=SOURCE_ID,
        snapshot_id=first_snapshot,
    )
    approve_and_activate(env, object_id=first_object, snapshot_id=first_snapshot)

    second_object = ingest(
        env,
        payload=records_content(
            [
                ("record-1", "Changed Entity"),
                ("record-2", "Added Entity"),
            ]
        ),
    )
    second_snapshot = parse(env, second_object)
    first_result = env.service.validate(
        authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
        source_id=SOURCE_ID,
        snapshot_id=second_snapshot,
    )
    retry = env.service.validate(
        authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
        source_id=SOURCE_ID,
        snapshot_id=second_snapshot,
    )
    active = env.repository.get_snapshot(DEPLOYMENT_ID, SOURCE_ID, first_snapshot)
    events, lifecycle = env.repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    report = events[-1].validation_report
    assert active is not None
    assert report is not None
    assert lifecycle.active_snapshot == active.reference()
    assert report.diff.previous_snapshot == active.reference()
    assert first_result.previous_snapshot_id == first_snapshot
    assert first_result.previous_snapshot_content_hash == active.content_hash
    assert first_result.added_record_count == 1
    assert first_result.changed_record_count == 1
    assert first_result.removed_record_count == 0
    assert retry == first_result
    assert retry.validated_at == report.validated_at


def test_unexpected_deletion_fails_validation_against_active_predecessor(
    env: Environment,
) -> None:
    first_object = ingest(
        env, payload=records_content([("record-1", "Original Entity")])
    )
    first_snapshot = parse(env, first_object)
    env.service.validate(
        authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
        source_id=SOURCE_ID,
        snapshot_id=first_snapshot,
    )
    approve_and_activate(env, object_id=first_object, snapshot_id=first_snapshot)

    second_object = ingest(
        env, payload=records_content([("record-2", "Replacement Entity")])
    )
    second_snapshot = parse(env, second_object)
    result = env.service.validate(
        authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
        source_id=SOURCE_ID,
        snapshot_id=second_snapshot,
    )
    assert not result.passed
    assert result.state is SourceSnapshotState.QUARANTINED
    assert ValidationReasonCode.UNEXPECTED_DELETION in result.reason_codes
    assert result.previous_snapshot_id == first_snapshot
    assert result.added_record_count == 1
    assert result.removed_record_count == 1


def test_validation_retry_rejects_corrupted_stored_report_without_audit(
    env: Environment,
) -> None:
    snapshot_id = parse(env, ingest(env))
    env.service.validate(
        authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
        source_id=SOURCE_ID,
        snapshot_id=snapshot_id,
    )
    events, _ = env.repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    report = events[-1].validation_report
    assert report is not None
    scope = (DEPLOYMENT_ID, SOURCE_ID)
    audit_ids_before = tuple(
        audit.command_event_id
        for audit in env.repository._audits[scope]  # noqa: SLF001
    )
    object.__setattr__(report, "content_hash", "sha256:" + "0" * 64)
    with pytest.raises(SourceSnapshotUnavailable):
        env.service.validate(
            authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
            source_id=SOURCE_ID,
            snapshot_id=snapshot_id,
        )
    assert (
        tuple(
            audit.command_event_id
            for audit in env.repository._audits[scope]  # noqa: SLF001
        )
        == audit_ids_before
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        env.repository.list_command_audits(*scope, limit=10)


def test_failure_audit_persistence_failure_replaces_domain_failure(
    env: Environment,
) -> None:
    object_id = ingest(env, payload=b"not-json")
    env.repository.fail_next_atomic_write()
    with pytest.raises(SourceSnapshotUnavailable):
        parse(env, object_id)
    events, _ = env.repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    assert SourceSnapshotEventType.PARSED not in {event.event_type for event in events}


def test_service_rejects_non_finite_configuration() -> None:
    parser = SyntheticJsonSourceParser()
    wrong_parser = BoundParser(object())
    wrong_parser.parser_version = "2.0.0"
    with pytest.raises(ValueError):
        SourceSnapshotService(
            InMemorySourceSnapshotRepository(),
            InMemoryImmutableRawObjectStore(),
            Registry(),
            parser,
            deployment_id="bad id",
            source_set_id=SOURCE_SET_ID,
            control_tenant_id=TENANT_ID,
        )
    with pytest.raises(ValueError):
        SourceSnapshotService(
            InMemorySourceSnapshotRepository(),
            InMemoryImmutableRawObjectStore(),
            Registry(),
            wrong_parser,
            deployment_id=DEPLOYMENT_ID,
            source_set_id=SOURCE_SET_ID,
            control_tenant_id=TENANT_ID,
        )


class BrokenParserBinding:
    @property
    def parser_id(self) -> FiniteParserId:
        raise RuntimeError("broken binding")


@pytest.mark.parametrize(
    "clock_value",
    [NOW.replace(tzinfo=None), cast(datetime, "not-a-datetime")],
)
def test_invalid_clock_is_safe_unavailable(
    env: Environment, clock_value: datetime
) -> None:
    env.clock.value = clock_value
    with pytest.raises(SourceSnapshotUnavailable):
        ingest(env)


@pytest.mark.parametrize("factory_kind", ["event", "command"])
@pytest.mark.parametrize("failure_kind", ["raise", "invalid"])
def test_identifier_factory_failure_is_safe_unavailable(
    env: Environment,
    factory_kind: str,
    failure_kind: str,
) -> None:
    def factory() -> str:
        if failure_kind == "raise":
            raise RuntimeError("secret ID detail")
        return "not safe"

    if factory_kind == "event":
        env.service._event_id_factory = factory  # noqa: SLF001
    else:
        env.service._command_id_factory = factory  # noqa: SLF001
    with pytest.raises(SourceSnapshotUnavailable) as caught:
        ingest(env)
    assert "secret" not in str(caught.value)


def test_default_providers_and_parser_binding_access_failure() -> None:
    service = SourceSnapshotService(
        InMemorySourceSnapshotRepository(),
        InMemoryImmutableRawObjectStore(),
        Registry(),
        SyntheticJsonSourceParser(),
        deployment_id=DEPLOYMENT_ID,
        source_set_id=SOURCE_SET_ID,
        control_tenant_id=TENANT_ID,
    )
    result = service.ingest(
        authorized(Operation.SOURCE_SNAPSHOT_INGEST),
        source_id=SOURCE_ID,
        original_name="synthetic.json",
        media_type=SYNTHETIC_MEDIA_TYPE,
        charset=SYNTHETIC_CHARSET,
        retrieved_at=datetime.now(UTC) - timedelta(minutes=1),
        effective_from=None,
        content=content(),
    )
    assert result.state is RawObjectState.QUARANTINED
    with pytest.raises(ValueError):
        SourceSnapshotService(
            InMemorySourceSnapshotRepository(),
            InMemoryImmutableRawObjectStore(),
            Registry(),
            cast(Any, BrokenParserBinding()),
            deployment_id=DEPLOYMENT_ID,
            source_set_id=SOURCE_SET_ID,
            control_tenant_id=TENANT_ID,
        )


def test_invalid_request_shapes_and_exception_code_are_rejected(
    env: Environment,
) -> None:
    with pytest.raises(SourceSnapshotRequestInvalid):
        env.service.ingest(
            authorized(Operation.SOURCE_SNAPSHOT_INGEST),
            source_id=SOURCE_ID,
            original_name="../secret.json",
            media_type=SYNTHETIC_MEDIA_TYPE,
            charset=SYNTHETIC_CHARSET,
            retrieved_at=NOW,
            effective_from=None,
            content=content(),
        )
    with pytest.raises(ValueError):
        SourceSnapshotParseRejected(cast(FiniteParserErrorCode, "INVALID"))
    with pytest.raises(SourceSnapshotNotFound):
        env.service.parse(
            authorized(Operation.SOURCE_SNAPSHOT_PARSE),
            source_id=SOURCE_ID,
            object_id="not safe",
        )
    with pytest.raises(SourceSnapshotNotFound):
        env.service.validate(
            authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
            source_id=SOURCE_ID,
            snapshot_id="missing-snapshot",
        )


def test_malformed_authorization_and_private_scope_guards_fail_closed(
    env: Environment,
) -> None:
    with pytest.raises(SourceSnapshotAuthorizationBindingDenied):
        env.service.ingest(
            cast(AuthorizedRequest, object()),
            source_id=SOURCE_ID,
            original_name="synthetic.json",
            media_type=SYNTHETIC_MEDIA_TYPE,
            charset=SYNTHETIC_CHARSET,
            retrieved_at=NOW,
            effective_from=None,
            content=content(),
        )
    with pytest.raises(SourceSnapshotNotFound):
        env.service._require_registration("not safe")  # noqa: SLF001


def test_malformed_raw_and_snapshot_records_are_unavailable(
    env: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    malformed_raw = object.__new__(RawObjectMetadata)
    monkeypatch.setattr(
        env.repository, "get_raw_metadata", lambda *_args: malformed_raw
    )
    with pytest.raises(SourceSnapshotUnavailable):
        env.service.parse(
            authorized(Operation.SOURCE_SNAPSHOT_PARSE),
            source_id=SOURCE_ID,
            object_id="raw-malformed",
        )

    monkeypatch.undo()
    snapshot_id = parse(env, ingest(env))
    snapshot = env.repository.get_snapshot(DEPLOYMENT_ID, SOURCE_ID, snapshot_id)
    assert snapshot is not None
    object.__setattr__(snapshot, "parser_id", "other-parser")
    with pytest.raises(SourceSnapshotUnavailable):
        env.service.validate(
            authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
            source_id=SOURCE_ID,
            snapshot_id=snapshot_id,
        )


def test_valid_snapshot_returned_under_wrong_repository_key_is_unavailable(
    env: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_id = parse(env, ingest(env))
    snapshot = env.repository.get_snapshot(DEPLOYMENT_ID, SOURCE_ID, snapshot_id)
    assert snapshot is not None
    monkeypatch.setattr(env.repository, "get_snapshot", lambda *_args: snapshot)
    with pytest.raises(SourceSnapshotUnavailable):
        env.service._parsed_snapshot(SOURCE_ID, "different-snapshot")  # noqa: SLF001


def test_application_lifecycle_bound_fails_before_projection(
    env: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    ingest(env)
    events, lifecycle = env.repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    monkeypatch.setattr(
        env.repository,
        "get_lifecycle_snapshot",
        lambda *_args: (events * 2049, lifecycle),
    )
    with pytest.raises(SourceSnapshotUnavailable):
        env.service._snapshot(SOURCE_ID)  # noqa: SLF001


def test_retry_corruption_guards_reject_ambiguous_or_inconsistent_links(
    env: Environment,
) -> None:
    object_id = ingest(env)
    snapshot_id = parse(env, object_id)
    metadata = env.repository.get_raw_metadata(DEPLOYMENT_ID, SOURCE_ID, object_id)
    snapshot = env.repository.get_snapshot(DEPLOYMENT_ID, SOURCE_ID, snapshot_id)
    events, lifecycle = env.repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    assert metadata is not None
    assert snapshot is not None
    parsed_event = events[-1]

    with pytest.raises(SourceSnapshotUnavailable):
        env.service._parsed_retry(  # noqa: SLF001
            metadata, (parsed_event, parsed_event), lifecycle
        )
    missing_snapshot = cast(
        SourceSnapshotLifecycleEvent,
        SimpleNamespace(
            event_type=SourceSnapshotEventType.PARSED,
            raw_object=metadata.reference(),
            snapshot=None,
        ),
    )
    with pytest.raises(SourceSnapshotUnavailable):
        env.service._parsed_retry(  # noqa: SLF001
            metadata, (missing_snapshot,), lifecycle
        )
    no_state = cast(
        Any,
        SimpleNamespace(state_for=lambda _reference: None),
    )
    with pytest.raises(SourceSnapshotUnavailable):
        env.service._parsed_retry(metadata, (parsed_event,), no_state)  # noqa: SLF001


def test_validation_retry_corruption_and_lifecycle_guards_fail_closed(
    env: Environment,
) -> None:
    snapshot_id = parse(env, ingest(env))
    env.service.validate(
        authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
        source_id=SOURCE_ID,
        snapshot_id=snapshot_id,
    )
    snapshot = env.repository.get_snapshot(DEPLOYMENT_ID, SOURCE_ID, snapshot_id)
    events, lifecycle = env.repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    assert snapshot is not None
    event = events[-1]
    report = event.validation_report
    assert report is not None

    with pytest.raises(SourceSnapshotUnavailable):
        env.service._validation_retry(  # noqa: SLF001
            snapshot, (event, event), lifecycle
        )
    no_report = cast(
        SourceSnapshotLifecycleEvent,
        SimpleNamespace(
            event_type=SourceSnapshotEventType.VALIDATED,
            snapshot=snapshot.reference(),
            validation_report=None,
        ),
    )
    with pytest.raises(SourceSnapshotUnavailable):
        env.service._validation_retry(snapshot, (no_report,), lifecycle)  # noqa: SLF001

    original_hash = report.content_hash
    object.__setattr__(report, "content_hash", f"sha256:{'f' * 64}")
    with pytest.raises(SourceSnapshotUnavailable):
        env.service._validation_retry(snapshot, (event,), lifecycle)  # noqa: SLF001
    object.__setattr__(report, "content_hash", original_hash)

    original_diff = report.diff
    object.__setattr__(report, "diff", object())
    with pytest.raises(SourceSnapshotUnavailable):
        env.service._validation_retry(snapshot, (event,), lifecycle)  # noqa: SLF001
    object.__setattr__(report, "diff", original_diff)

    other_metadata = RawObjectMetadata.from_bytes(
        deployment_id=DEPLOYMENT_ID,
        source_id=SOURCE_ID,
        original_name="other.json",
        media_type=SYNTHETIC_MEDIA_TYPE,
        charset=SYNTHETIC_CHARSET,
        retrieved_at=NOW,
        effective_from=None,
        content=b"other",
    )
    inconsistent = cast(
        SourceSnapshotLifecycleEvent,
        SimpleNamespace(
            event_type=SourceSnapshotEventType.VALIDATED,
            snapshot=snapshot.reference(),
            validation_report=report,
            raw_object=other_metadata.reference(),
        ),
    )
    with pytest.raises(SourceSnapshotUnavailable):
        env.service._validation_retry(snapshot, (inconsistent,), lifecycle)  # noqa: SLF001

    wrong_state = cast(
        Any,
        SimpleNamespace(
            state_for=lambda _reference: SourceSnapshotState.ACTIVE,
            active_snapshot=None,
        ),
    )
    wrong_state_result = env.service._validation_retry(  # noqa: SLF001
        snapshot, (event,), wrong_state
    )
    assert wrong_state_result is not None
    assert not isinstance(wrong_state_result, tuple)
    moved_active = cast(
        Any,
        SimpleNamespace(
            state_for=lambda _reference: SourceSnapshotState.VALIDATED,
            active_snapshot=snapshot.reference(),
        ),
    )
    moved_active_result = env.service._validation_retry(  # noqa: SLF001
        snapshot, (event,), moved_active
    )
    assert moved_active_result is not None
    assert not isinstance(moved_active_result, tuple)


def test_active_and_raw_projection_corruption_helpers_fail_closed(
    env: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_id = parse(env, ingest(env))
    snapshot = env.repository.get_snapshot(DEPLOYMENT_ID, SOURCE_ID, snapshot_id)
    _, lifecycle = env.repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    assert snapshot is not None

    not_active = cast(
        Any,
        SimpleNamespace(
            active_snapshot=snapshot.reference(),
            state_for=lambda _reference: SourceSnapshotState.VALIDATED,
        ),
    )
    with pytest.raises(SourceSnapshotUnavailable):
        env.service._active_snapshot(SOURCE_ID, not_active)  # noqa: SLF001

    raw_metadata = env.repository.get_raw_metadata(
        DEPLOYMENT_ID, SOURCE_ID, snapshot.raw_object.object_id
    )
    assert raw_metadata is not None
    other = ParsedSourceSnapshot.create(
        raw_metadata=raw_metadata,
        parser_id=snapshot.parser_id,
        parser_version=snapshot.parser_version,
        schema_id=snapshot.schema_id,
        declared_record_count=snapshot.declared_record_count,
        parsed_at=NOW + timedelta(minutes=1),
        records=env.parser.parse(content()).records,
    )
    active = cast(
        Any,
        SimpleNamespace(
            active_snapshot=snapshot.reference(),
            state_for=lambda _reference: SourceSnapshotState.ACTIVE,
        ),
    )
    assert env.service._active_snapshot(SOURCE_ID, active) == snapshot  # noqa: SLF001
    monkeypatch.setattr(env.service, "_parsed_snapshot", lambda *_args: other)
    with pytest.raises(SourceSnapshotUnavailable):
        env.service._active_snapshot(SOURCE_ID, active)  # noqa: SLF001

    duplicated_raw = cast(
        Any,
        SimpleNamespace(raw_objects=lifecycle.raw_objects * 2),
    )
    with pytest.raises(SourceSnapshotUnavailable):
        env.service._raw_state(  # noqa: SLF001
            duplicated_raw, lifecycle.raw_objects[0].raw_object
        )


def test_internal_event_and_audit_construction_fail_closed(env: Environment) -> None:
    metadata = RawObjectMetadata.from_bytes(
        deployment_id=DEPLOYMENT_ID,
        source_id=SOURCE_ID,
        original_name="synthetic.json",
        media_type=SYNTHETIC_MEDIA_TYPE,
        charset=SYNTHETIC_CHARSET,
        retrieved_at=NOW,
        effective_from=None,
        content=content(),
    )
    request = authorized(Operation.SOURCE_SNAPSHOT_INGEST)
    with pytest.raises(SourceSnapshotUnavailable):
        env.service._event(  # noqa: SLF001
            request,
            source_id=SOURCE_ID,
            sequence=1,
            event_type=SourceSnapshotEventType.APPROVED,
            raw_object=metadata.reference(),
            snapshot=None,
            report=None,
            now=NOW,
        )
    malformed = replace(request, operation=cast(Operation, "invalid"))
    with pytest.raises(SourceSnapshotUnavailable):
        env.service._audit(  # noqa: SLF001
            malformed,
            SOURCE_ID,
            metadata.reference(),
            None,
            SnapshotCommandOutcome.FAILURE,
            SnapshotCommandReason.CONFLICT,
            NOW,
        )
