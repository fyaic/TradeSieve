"""Evidence for bounded snapshot reads and exact official citation resolution."""

from __future__ import annotations

import json
from copy import copy
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import ValidationError

import tradesieve.application.source_snapshot_query as query_app
from tradesieve.adapters.in_memory_rule_bundle import InMemoryRuleBundleRepository
from tradesieve.adapters.in_memory_source_snapshot import (
    InMemoryImmutableRawObjectStore,
    InMemorySourceSnapshotRepository,
)
from tradesieve.adapters.synthetic_source_parser import SyntheticJsonSourceParser
from tradesieve.application.auth import (
    ActorContext,
    ActorType,
    AuthorizedRequest,
    Operation,
    RequestContext,
    Role,
    Scope,
    TargetObject,
)
from tradesieve.application.rule_bundle import (
    RULE_BUNDLE_TARGET_TYPE,
    RULE_SET_TARGET_TYPE,
    ImmutableRuleBundleIdentity,
    RuleBundleGovernanceBlocked,
    RuleBundleReadinessService,
    RuleBundleService,
    rule_bundle_authorization_target_id,
    rule_set_authorization_target_id,
)
from tradesieve.application.source_snapshot import (
    SOURCE_SNAPSHOT_TARGET_TYPE,
    SOURCE_TARGET_TYPE,
    ImmutableSourceSnapshotIdentity,
    SourceSnapshotAuthorizationBindingDenied,
    SourceSnapshotNotFound,
    SourceSnapshotService,
    SourceSnapshotUnavailable,
    source_authorization_target_id,
    source_snapshot_authorization_target_id,
)
from tradesieve.application.source_snapshot_contracts import (
    ChangedSourceRecordView,
    SourceSnapshotDiffView,
    SourceSnapshotHistory,
    SourceSnapshotHistoryRecord,
    SourceSnapshotListing,
    SourceSnapshotReferenceView,
    SourceSnapshotSummary,
)
from tradesieve.application.source_snapshot_query import (
    SourceSnapshotOfficialCitationResolver,
    SourceSnapshotQueryService,
)
from tradesieve.domain import rule_bundle as rule_domain
from tradesieve.domain.rule_bundle import (
    CanonicalFactPath,
    EffectiveWindow,
    OfficialSourceProvisionCitation,
    RuleAction,
    RuleActivity,
    RuleBundleVersion,
    RuleEvaluationOutcome,
    RuleEvaluatorKind,
    RuleFixture,
    RuleScope,
    RuleVersion,
    rule_bundle_ref,
)
from tradesieve.domain.source_registry import (
    ObservationWriteOutcome,
    SourceAccessMethod,
    SourceRegistration,
    SourceRuntimeObservation,
    SourceSetManifest,
)
from tradesieve.domain.source_snapshot import (
    MAX_QUERY_LIMIT,
    ArtifactWriteOutcome,
    LifecycleWriteOutcome,
    ParsedAssertionInput,
    ParsedRecordInput,
    ParsedSourceSnapshot,
    RawObjectMetadata,
    SnapshotCommandAuditRecord,
    SourceSnapshotEventType,
    SourceSnapshotLifecycle,
    SourceSnapshotLifecycleEvent,
    SourceSnapshotState,
    ValidationReasonCode,
    canonical_sha256,
    fold_source_snapshot_events,
)
from tradesieve.ports.source_snapshot import (
    SYNTHETIC_CHARSET,
    SYNTHETIC_MEDIA_TYPE,
    SYNTHETIC_PARSER_VERSION,
    SYNTHETIC_SCHEMA_ID,
    FiniteParserId,
    FiniteParserOutput,
    SourceSnapshotPersistenceError,
)

NOW = datetime(2026, 8, 6, 12, tzinfo=UTC)
DEPLOYMENT_ID = "demo-deployment"
SOURCE_SET_ID = "official-source-set"
SOURCE_ID = "synthetic-source"
TENANT_ID = "control-tenant"


class source_helpers:
    NOW = NOW
    DEPLOYMENT_ID = DEPLOYMENT_ID
    SOURCE_SET_ID = SOURCE_SET_ID
    SOURCE_ID = SOURCE_ID
    TENANT_ID = TENANT_ID

    @dataclass
    class Environment:
        repository: InMemorySourceSnapshotRepository
        store: InMemoryImmutableRawObjectStore
        registry: Any
        parser: Any
        clock: Any
        event_ids: Any
        command_ids: Any
        service: SourceSnapshotService

    class Registry:
        def __init__(self, current: SourceRegistration | None = None) -> None:
            self.current = (
                current if current is not None else source_helpers.registration()
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

    class Clock:
        def __init__(self, value: datetime = NOW) -> None:
            self.value = value

        def __call__(self) -> datetime:
            return self.value

    class Ids:
        def __init__(self, prefix: str) -> None:
            self.prefix = prefix
            self.value = 0

        def __call__(self) -> str:
            self.value += 1
            return f"{self.prefix}-{self.value}"

    @staticmethod
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

    @staticmethod
    def actor(
        subject: str = "source-reader",
        actor_type: ActorType = ActorType.HUMAN,
        *,
        scopes: frozenset[Scope] = frozenset({Scope.SOURCE_OPERATE}),
        roles: frozenset[Role] = frozenset({Role.SOURCE_OPERATOR}),
        tenant_id: str = TENANT_ID,
    ) -> ActorContext:
        return ActorContext(
            subject=subject,
            client_id="source-client",
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

    @staticmethod
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
            RequestContext(
                principal or source_helpers.actor(), context_tenant, "correlation-1"
            ),
            operation,
            TargetObject(
                target_tenant,
                target_type,
                target_id
                or source_authorization_target_id(
                    DEPLOYMENT_ID, SOURCE_SET_ID, SOURCE_ID
                ),
            ),
            audit_event_id or f"authorization-{operation.value.lower()}",
        )

    @staticmethod
    def content(*, declared_count: int = 1) -> bytes:
        return json.dumps(
            {
                "schema_id": "tradesieve-synthetic-source-v1",
                "declared_record_count": declared_count,
                "records": [
                    {
                        "record_id": "record-1",
                        "effective_from": "2026-08-01",
                        "effective_to": None,
                        "assertions": [
                            {"field": "entity.name", "value": "Synthetic Entity"}
                        ],
                    }
                ],
            },
            separators=(",", ":"),
        ).encode()

    @staticmethod
    def records_content(records: list[tuple[str, str]]) -> bytes:
        return json.dumps(
            {
                "schema_id": "tradesieve-synthetic-source-v1",
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

    @staticmethod
    def ingest(env: source_helpers.Environment, *, payload: bytes | None = None) -> str:
        return env.service.ingest(
            source_helpers.authorized(Operation.SOURCE_SNAPSHOT_INGEST),
            source_id=SOURCE_ID,
            original_name="synthetic.json",
            media_type=SYNTHETIC_MEDIA_TYPE,
            charset=SYNTHETIC_CHARSET,
            retrieved_at=NOW - timedelta(minutes=1),
            effective_from=NOW + timedelta(days=1),
            content=payload if payload is not None else source_helpers.content(),
        ).object_id

    @staticmethod
    def parse(env: source_helpers.Environment, object_id: str) -> str:
        return env.service.parse(
            source_helpers.authorized(Operation.SOURCE_SNAPSHOT_PARSE),
            source_id=SOURCE_ID,
            object_id=object_id,
        ).snapshot_id

    @staticmethod
    def immutable_identity(
        env: source_helpers.Environment, snapshot_id: str
    ) -> ImmutableSourceSnapshotIdentity:
        snapshot = env.repository.get_snapshot(DEPLOYMENT_ID, SOURCE_ID, snapshot_id)
        assert snapshot is not None
        return ImmutableSourceSnapshotIdentity(
            snapshot_id=snapshot.snapshot_id, content_hash=snapshot.content_hash
        )

    @staticmethod
    def governance_authorized(
        operation: Operation,
        identity: ImmutableSourceSnapshotIdentity,
        *,
        subject: str = "source-approver-2",
    ) -> AuthorizedRequest:
        return source_helpers.authorized(
            operation,
            principal=source_helpers.actor(
                subject,
                ActorType.HUMAN,
                scopes=frozenset({Scope.SOURCE_APPROVE}),
                roles=frozenset({Role.SOURCE_APPROVER}),
            ),
            target_type=SOURCE_SNAPSHOT_TARGET_TYPE,
            target_id=source_snapshot_authorization_target_id(
                DEPLOYMENT_ID,
                SOURCE_SET_ID,
                SOURCE_ID,
                identity.snapshot_id,
                identity.content_hash,
            ),
        )

    @staticmethod
    def validated_snapshot(
        env: source_helpers.Environment, *, payload: bytes | None = None
    ) -> tuple[str, str, ImmutableSourceSnapshotIdentity]:
        object_id = source_helpers.ingest(env, payload=payload)
        snapshot_id = source_helpers.parse(env, object_id)
        result = env.service.validate(
            source_helpers.authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
            source_id=SOURCE_ID,
            snapshot_id=snapshot_id,
        )
        assert result.passed
        return (
            object_id,
            snapshot_id,
            source_helpers.immutable_identity(env, snapshot_id),
        )

    @staticmethod
    def approve(
        env: source_helpers.Environment,
        identity: ImmutableSourceSnapshotIdentity,
        *,
        reason: str = "approve verified snapshot",
    ) -> None:
        env.service.approve(
            source_helpers.governance_authorized(
                Operation.SOURCE_SNAPSHOT_APPROVE, identity
            ),
            source_id=SOURCE_ID,
            identity=identity,
            reason=reason,
        )

    @staticmethod
    def activate(
        env: source_helpers.Environment,
        identity: ImmutableSourceSnapshotIdentity,
        *,
        reason: str = "activate approved snapshot",
    ) -> None:
        env.service.activate(
            source_helpers.governance_authorized(
                Operation.SOURCE_SNAPSHOT_ACTIVATE, identity
            ),
            source_id=SOURCE_ID,
            identity=identity,
            reason=reason,
        )

    @staticmethod
    def rollback(
        env: source_helpers.Environment, identity: ImmutableSourceSnapshotIdentity
    ) -> None:
        env.service.rollback(
            source_helpers.governance_authorized(
                Operation.SOURCE_SNAPSHOT_ROLLBACK, identity
            ),
            source_id=SOURCE_ID,
            identity=identity,
            reason="rollback to accepted snapshot",
        )

    @staticmethod
    def active_snapshot(
        env: source_helpers.Environment, *, payload: bytes | None = None
    ) -> tuple[str, str, ImmutableSourceSnapshotIdentity]:
        result = source_helpers.validated_snapshot(env, payload=payload)
        source_helpers.approve(env, result[2])
        source_helpers.activate(env, result[2])
        return result

    @staticmethod
    def active_pair(
        env: source_helpers.Environment,
    ) -> tuple[
        tuple[str, str, ImmutableSourceSnapshotIdentity],
        tuple[str, str, ImmutableSourceSnapshotIdentity],
    ]:
        first = source_helpers.active_snapshot(env)
        second = source_helpers.validated_snapshot(
            env, payload=source_helpers.records_content([("record-1", "Replacement")])
        )
        source_helpers.approve(env, second[2], reason="approve second snapshot")
        env.clock.value += timedelta(seconds=1)
        source_helpers.activate(env, second[2], reason="activate second snapshot")
        return first, second


class rule_helpers:
    NOW = NOW
    WINDOW = EffectiveWindow(NOW - timedelta(days=1), NOW + timedelta(days=1))
    RULE_SET_ID = "ruleset-1"

    class EventIds:
        def __init__(self) -> None:
            self.value = 0

        def __call__(self) -> str:
            self.value += 1
            return f"generated-event-{self.value}"

    @staticmethod
    def fixture(
        fixture_id: str,
        *,
        action: RuleAction = RuleAction.QUOTE_RELEASE,
        present: tuple[CanonicalFactPath, ...] = (CanonicalFactPath.LEGAL_NEXUS,),
        outcome: RuleEvaluationOutcome = RuleEvaluationOutcome.FACTS_PRESENT,
        missing: tuple[CanonicalFactPath, ...] = (),
    ) -> RuleFixture:
        return RuleFixture(
            fixture_id,
            action,
            (RuleActivity.EXPORT,),
            present,
            outcome,
            missing,
        )

    @staticmethod
    def legal_rule(*, citation: OfficialSourceProvisionCitation) -> RuleVersion:
        return RuleVersion(
            rule_id="rule-nexus",
            version="1.0.0",
            kind=RuleEvaluatorKind.LEGAL_NEXUS_PRESENCE,
            owner="policy-owner",
            effective_window=rule_helpers.WINDOW,
            scope=RuleScope(
                (RuleAction.QUOTE_RELEASE,),
                (RuleActivity.EXPORT,),
            ),
            evaluator=rule_domain.LegalNexusPresenceSpec(),
            citations=(citation,),
            fixtures=(
                rule_helpers.fixture(
                    "fixture-missing",
                    present=(),
                    outcome=RuleEvaluationOutcome.MISSING_FACTS,
                    missing=(CanonicalFactPath.LEGAL_NEXUS,),
                ),
                rule_helpers.fixture(
                    "fixture-not-applicable",
                    action=RuleAction.PAYMENT,
                    present=(),
                    outcome=RuleEvaluationOutcome.NOT_APPLICABLE,
                ),
                rule_helpers.fixture("fixture-present"),
            ),
            internal_notes="Private rule note.",
        )

    @staticmethod
    def bundle(citation: OfficialSourceProvisionCitation) -> RuleBundleVersion:
        return RuleBundleVersion(
            tenant_id=TENANT_ID,
            deployment_id=DEPLOYMENT_ID,
            rule_set_id=rule_helpers.RULE_SET_ID,
            bundle_id="bundle-1",
            version="1.0.0",
            owner="policy-owner",
            effective_window=rule_helpers.WINDOW,
            authored_by="author-1",
            rules=(rule_helpers.legal_rule(citation=citation),),
            internal_notes="Private bundle note.",
        )

    @staticmethod
    def actor(subject: str) -> ActorContext:
        return ActorContext(
            subject=subject,
            client_id="policy-ui",
            tenant_id=TENANT_ID,
            actor_type=ActorType.HUMAN,
            scopes=frozenset(Scope),
            roles=frozenset(Role),
            issuer="https://identity.example.test/",
            audience="tradesieve-api",
            issued_at=NOW - timedelta(minutes=1),
            expires_at=NOW + timedelta(minutes=5),
            demo_identity=False,
        )

    @staticmethod
    def namespace_request(operation: Operation, subject: str) -> AuthorizedRequest:
        return AuthorizedRequest(
            RequestContext(
                rule_helpers.actor(subject), TENANT_ID, "correlation-policy"
            ),
            operation,
            TargetObject(
                TENANT_ID,
                RULE_SET_TARGET_TYPE,
                rule_set_authorization_target_id(
                    DEPLOYMENT_ID, rule_helpers.RULE_SET_ID
                ),
            ),
            f"authorization-{operation.value.lower()}",
        )

    @staticmethod
    def bundle_request(
        operation: Operation, item: RuleBundleVersion
    ) -> AuthorizedRequest:
        reference = rule_bundle_ref(item)
        return AuthorizedRequest(
            RequestContext(
                rule_helpers.actor("approver-2"), TENANT_ID, "correlation-policy"
            ),
            operation,
            TargetObject(
                TENANT_ID,
                RULE_BUNDLE_TARGET_TYPE,
                rule_bundle_authorization_target_id(
                    DEPLOYMENT_ID,
                    rule_helpers.RULE_SET_ID,
                    reference.bundle_id,
                    reference.version,
                    reference.content_hash,
                ),
            ),
            f"authorization-{operation.value.lower()}",
        )

    @staticmethod
    def identity(item: RuleBundleVersion) -> ImmutableRuleBundleIdentity:
        reference = rule_bundle_ref(item)
        return ImmutableRuleBundleIdentity(
            bundle_id=reference.bundle_id,
            version=reference.version,
            content_hash=reference.content_hash,
        )

    @staticmethod
    def save_approve_activate(
        application: RuleBundleService, item: RuleBundleVersion
    ) -> None:
        application.save_draft(
            rule_helpers.namespace_request(Operation.POLICY_DRAFT, "author-1"), item
        )
        application.approve(
            rule_helpers.bundle_request(Operation.POLICY_APPROVE, item),
            rule_helpers.identity(item),
            reason="approve exact official citation",
        )
        application.activate(
            rule_helpers.bundle_request(Operation.POLICY_ACTIVATE, item),
            rule_helpers.identity(item),
            reason="activate exact official citation",
        )


class CountingRepository(InMemorySourceSnapshotRepository):
    def __init__(self) -> None:
        super().__init__()
        self.writes = 0
        self.fail_method: str | None = None
        self.override: dict[str, object] = {}

    def save_retrieval_atomic(
        self,
        metadata: RawObjectMetadata,
        retrieved_event: SourceSnapshotLifecycleEvent,
        applied_audit: SnapshotCommandAuditRecord,
        idempotent_audit: SnapshotCommandAuditRecord,
    ) -> ArtifactWriteOutcome:
        self.writes += 1
        return super().save_retrieval_atomic(
            metadata, retrieved_event, applied_audit, idempotent_audit
        )

    def save_parsed_snapshot_atomic(
        self,
        snapshot: ParsedSourceSnapshot,
        parsed_event: SourceSnapshotLifecycleEvent,
        applied_audit: SnapshotCommandAuditRecord,
        idempotent_audit: SnapshotCommandAuditRecord,
    ) -> ArtifactWriteOutcome:
        self.writes += 1
        return super().save_parsed_snapshot_atomic(
            snapshot, parsed_event, applied_audit, idempotent_audit
        )

    def append_lifecycle_atomic(
        self,
        event: SourceSnapshotLifecycleEvent,
        applied_audit: SnapshotCommandAuditRecord,
        idempotent_audit: SnapshotCommandAuditRecord,
    ) -> LifecycleWriteOutcome:
        self.writes += 1
        return super().append_lifecycle_atomic(event, applied_audit, idempotent_audit)

    def activate_or_rollback_atomic(
        self,
        event: SourceSnapshotLifecycleEvent,
        observation: SourceRuntimeObservation,
        applied_audit: SnapshotCommandAuditRecord,
        idempotent_audit: SnapshotCommandAuditRecord,
    ) -> LifecycleWriteOutcome:
        self.writes += 1
        return super().activate_or_rollback_atomic(
            event, observation, applied_audit, idempotent_audit
        )

    def append_command_audit(self, audit: SnapshotCommandAuditRecord) -> None:
        self.writes += 1
        super().append_command_audit(audit)

    def get_registration_override(self) -> object:
        return self.override.get("registration")

    def get_raw_metadata(self, *args: Any, **kwargs: Any) -> RawObjectMetadata | None:
        if self.fail_method == "raw":
            raise SourceSnapshotPersistenceError
        if "raw" in self.override:
            return cast(RawObjectMetadata | None, self.override["raw"])
        return super().get_raw_metadata(*args, **kwargs)

    def get_snapshot(self, *args: Any, **kwargs: Any) -> ParsedSourceSnapshot | None:
        if self.fail_method == "snapshot":
            raise SourceSnapshotPersistenceError
        if "snapshot" in self.override:
            return cast(ParsedSourceSnapshot | None, self.override["snapshot"])
        return super().get_snapshot(*args, **kwargs)

    def list_snapshots(
        self, *args: Any, **kwargs: Any
    ) -> tuple[ParsedSourceSnapshot, ...]:
        if self.fail_method == "list":
            raise SourceSnapshotPersistenceError
        if "list" in self.override:
            return cast(tuple[ParsedSourceSnapshot, ...], self.override["list"])
        return super().list_snapshots(*args, **kwargs)

    def get_lifecycle_snapshot(
        self, *args: Any, **kwargs: Any
    ) -> tuple[tuple[SourceSnapshotLifecycleEvent, ...], SourceSnapshotLifecycle]:
        if self.fail_method == "lifecycle":
            raise SourceSnapshotPersistenceError
        if "lifecycle" in self.override:
            return cast(
                tuple[
                    tuple[SourceSnapshotLifecycleEvent, ...], SourceSnapshotLifecycle
                ],
                self.override["lifecycle"],
            )
        return super().get_lifecycle_snapshot(*args, **kwargs)


class Registry(source_helpers.Registry):
    def __init__(self, current: SourceRegistration | None = None) -> None:
        super().__init__(current)
        self.return_value: object = self.current

    def get_registration(
        self, deployment_id: str, source_id: str
    ) -> SourceRegistration | None:
        del deployment_id, source_id
        if self.failure is not None:
            raise self.failure
        return cast(SourceRegistration | None, self.return_value)


class TrackingObjectStore(InMemoryImmutableRawObjectStore):
    def __init__(self) -> None:
        super().__init__()
        self.reads = 0

    def get_verified(self, reference: object) -> bytes:
        self.reads += 1
        return super().get_verified(cast(Any, reference))


class AmbiguousLocatorParser:
    parser_id = FiniteParserId.SYNTHETIC_JSON_V1
    parser_version = SYNTHETIC_PARSER_VERSION
    media_type = SYNTHETIC_MEDIA_TYPE
    charset = SYNTHETIC_CHARSET

    def parse(self, content: bytes) -> FiniteParserOutput:
        del content
        locator = "/same-provision"
        return FiniteParserOutput(
            schema_id=SYNTHETIC_SCHEMA_ID,
            declared_record_count=1,
            records=(
                ParsedRecordInput(
                    source_record_id="record-1",
                    native_locator=locator,
                    effective_from="2026-08-01",
                    effective_to=None,
                    assertions=(
                        ParsedAssertionInput(
                            field_name="entity.name",
                            native_locator=locator,
                            native_value="Synthetic Entity",
                            normalized_value="Synthetic Entity",
                        ),
                    ),
                ),
            ),
        )


def make_environment(parser: Any | None = None) -> source_helpers.Environment:
    repository = CountingRepository()
    store = TrackingObjectStore()
    registry = Registry()
    selected_parser = parser if parser is not None else SyntheticJsonSourceParser()
    clock = source_helpers.Clock()
    event_ids = source_helpers.Ids("event")
    command_ids = source_helpers.Ids("command")
    service = SourceSnapshotService(
        repository,
        store,
        registry,
        selected_parser,
        deployment_id=DEPLOYMENT_ID,
        source_set_id=SOURCE_SET_ID,
        control_tenant_id=TENANT_ID,
        clock=clock,
        event_id_factory=event_ids,
        command_id_factory=command_ids,
    )
    return source_helpers.Environment(
        repository,
        store,
        registry,
        selected_parser,
        clock,
        event_ids,
        command_ids,
        service,
    )


@pytest.fixture
def env() -> source_helpers.Environment:
    return make_environment()


def query_service(env: source_helpers.Environment) -> SourceSnapshotQueryService:
    return SourceSnapshotQueryService(
        env.repository,
        env.registry,
        deployment_id=DEPLOYMENT_ID,
        source_set_id=SOURCE_SET_ID,
        control_tenant_id=TENANT_ID,
    )


def resolver(
    env: source_helpers.Environment,
) -> SourceSnapshotOfficialCitationResolver:
    return SourceSnapshotOfficialCitationResolver(
        env.repository,
        env.registry,
        deployment_id=DEPLOYMENT_ID,
        source_set_id=SOURCE_SET_ID,
        control_tenant_id=TENANT_ID,
    )


def read_request(
    actor_type: ActorType = ActorType.HUMAN,
    *,
    source_id: str = SOURCE_ID,
    scopes: frozenset[Scope] = frozenset({Scope.SOURCE_READ}),
    roles: frozenset[Role] = frozenset(),
    **overrides: object,
) -> AuthorizedRequest:
    principal = source_helpers.actor(
        subject=f"{actor_type.value.lower()}-reader",
        actor_type=actor_type,
        scopes=scopes,
        roles=roles,
        tenant_id=cast(str, overrides.pop("actor_tenant", TENANT_ID)),
    )
    return source_helpers.authorized(
        Operation.SOURCE_READ,
        principal=principal,
        context_tenant=cast(str, overrides.pop("context_tenant", TENANT_ID)),
        target_tenant=cast(str, overrides.pop("target_tenant", TENANT_ID)),
        target_type=cast(str, overrides.pop("target_type", "source")),
        target_id=cast(
            str,
            overrides.pop(
                "target_id",
                source_authorization_target_id(DEPLOYMENT_ID, SOURCE_SET_ID, source_id),
            ),
        ),
        audit_event_id=cast(str, overrides.pop("audit_event_id", "read-authz-1")),
    )


def citation(
    snapshot: ParsedSourceSnapshot,
    locator: str,
    **overrides: object,
) -> OfficialSourceProvisionCitation:
    return OfficialSourceProvisionCitation(
        citation_ref="citation-source",
        source_id=cast(str, overrides.get("source_id", snapshot.source_id)),
        snapshot_id=cast(str, overrides.get("snapshot_id", snapshot.snapshot_id)),
        snapshot_content_hash=cast(
            str, overrides.get("snapshot_content_hash", snapshot.content_hash)
        ),
        provision_locator=locator,
    )


def snapshot_for(
    env: source_helpers.Environment, identity: ImmutableSourceSnapshotIdentity
) -> ParsedSourceSnapshot:
    snapshot = env.repository.get_snapshot(
        DEPLOYMENT_ID, SOURCE_ID, identity.snapshot_id
    )
    assert snapshot is not None
    return snapshot


def corrupted_copy(value: Any, field: str, replacement: object) -> Any:
    result = copy(value)
    object.__setattr__(result, field, replacement)
    return result


@pytest.mark.parametrize("actor_type", list(ActorType))
def test_query_allows_each_read_actor_without_role_and_inactive_registration(
    env: source_helpers.Environment, actor_type: ActorType
) -> None:
    source_helpers.active_snapshot(env)
    env.registry.return_value = replace(source_helpers.registration(), active=False)
    before_writes = cast(CountingRepository, env.repository).writes
    before_reads = cast(TrackingObjectStore, env.store).reads
    result = query_service(env).list_snapshots(
        read_request(actor_type, roles=frozenset()), source_id=SOURCE_ID, limit=1
    )
    assert len(result.snapshots) == 1
    assert result.snapshots[0].active is True
    assert cast(CountingRepository, env.repository).writes == before_writes
    assert cast(TrackingObjectStore, env.store).reads == before_reads


def test_query_stage_views_exact_diff_order_and_redaction(
    env: source_helpers.Environment,
) -> None:
    service = query_service(env)
    request = read_request()
    assert service.list_snapshots(request, source_id=SOURCE_ID).snapshots == []
    assert service.history(request, source_id=SOURCE_ID).events == []

    parsed_object = source_helpers.ingest(env)
    parsed_id = source_helpers.parse(env, parsed_object)
    parsed_identity = source_helpers.immutable_identity(env, parsed_id)
    parsed_detail = service.get_snapshot(
        request, source_id=SOURCE_ID, identity=parsed_identity
    )
    assert parsed_detail.summary.state is SourceSnapshotState.PARSED
    assert parsed_detail.summary.validation_passed is None
    assert parsed_detail.diff is None

    validation = env.service.validate(
        source_helpers.authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
        source_id=SOURCE_ID,
        snapshot_id=parsed_id,
    )
    assert validation.passed is True
    source_helpers.approve(env, parsed_identity)
    source_helpers.activate(env, parsed_identity)
    env.clock.value += timedelta(seconds=1)

    second = source_helpers.validated_snapshot(
        env,
        payload=source_helpers.records_content(
            [("record-1", "Replacement"), ("record-2", "Added")]
        ),
    )
    source_helpers.approve(env, second[2], reason="approve replacement")
    source_helpers.activate(env, second[2], reason="activate replacement")
    listing = service.list_snapshots(
        request, source_id=SOURCE_ID, limit=MAX_QUERY_LIMIT
    )
    assert [item.snapshot_id for item in listing.snapshots] == sorted(
        item.snapshot_id for item in listing.snapshots
    )
    states = {item.snapshot_id: item.state for item in listing.snapshots}
    assert states[parsed_id] is SourceSnapshotState.SUPERSEDED
    assert states[second[1]] is SourceSnapshotState.ACTIVE

    detail = service.get_snapshot(request, source_id=SOURCE_ID, identity=second[2])
    assert detail.diff is not None
    assert detail.diff.previous_snapshot is not None
    assert detail.diff.previous_snapshot.snapshot_id == parsed_id
    assert detail.diff.added_record_ids == ["record-2"]
    assert [item.source_record_id for item in detail.diff.changed_records] == [
        "record-1"
    ]
    assert detail.summary.validation_reason_codes == []
    assert detail.summary.media_type == "application/json"
    assert detail.summary.charset == "utf-8"
    assert detail.summary.raw_byte_length > 0

    history = service.history(request, source_id=SOURCE_ID, limit=3)
    assert [item.sequence for item in history.events] == list(
        range(history.events[0].sequence, history.events[-1].sequence + 1)
    )
    assert history.events[-1].event_type is SourceSnapshotEventType.ACTIVATED
    dumped = {
        "listing": listing.model_dump(mode="json"),
        "detail": detail.model_dump(mode="json"),
        "history": history.model_dump(mode="json"),
    }
    rendered = repr(dumped)
    for secret in (
        "original_name",
        "native_locator",
        "native_value",
        "normalized_value",
        "credential",
        "contractual",
        "command_event_id",
        "authorization_event_id",
        "actor_id",
    ):
        assert secret not in rendered


def test_query_validation_failure_and_exact_identity_not_found(
    env: source_helpers.Environment,
) -> None:
    object_id = source_helpers.ingest(
        env, payload=source_helpers.content(declared_count=2)
    )
    snapshot_id = source_helpers.parse(env, object_id)
    result = env.service.validate(
        source_helpers.authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
        source_id=SOURCE_ID,
        snapshot_id=snapshot_id,
    )
    assert result.passed is False
    identity = source_helpers.immutable_identity(env, snapshot_id)
    detail = query_service(env).get_snapshot(
        read_request(), source_id=SOURCE_ID, identity=identity
    )
    assert detail.summary.state is SourceSnapshotState.QUARANTINED
    assert detail.summary.validation_passed is False
    assert detail.summary.validation_reason_codes == [
        ValidationReasonCode.DECLARED_COUNT_MISMATCH
    ]
    assert detail.diff is not None

    missing_hash = "sha256:" + "f" * 64
    missing_id = "snapshot-" + "f" * 64
    with pytest.raises(SourceSnapshotNotFound):
        query_service(env).get_snapshot(
            read_request(),
            source_id=SOURCE_ID,
            identity=ImmutableSourceSnapshotIdentity(
                snapshot_id=missing_id, content_hash=missing_hash
            ),
        )
    with pytest.raises(SourceSnapshotNotFound):
        query_service(env).get_snapshot(
            read_request(),
            source_id=SOURCE_ID,
            identity=cast(ImmutableSourceSnapshotIdentity, object()),
        )


@pytest.mark.parametrize(
    ("authorized_request", "source_id"),
    [
        (read_request(scopes=frozenset()), SOURCE_ID),
        (
            source_helpers.authorized(Operation.SOURCE_OPERATE),
            SOURCE_ID,
        ),
        (read_request(context_tenant="wrong-tenant"), SOURCE_ID),
        (read_request(actor_tenant="wrong-tenant"), SOURCE_ID),
        (read_request(target_tenant="wrong-tenant"), SOURCE_ID),
        (read_request(target_type="source_snapshot"), SOURCE_ID),
        (read_request(target_id="source-forged"), SOURCE_ID),
        (read_request(audit_event_id="unsafe audit"), SOURCE_ID),
        (cast(AuthorizedRequest, object()), SOURCE_ID),
        (read_request(), "unsafe source"),
    ],
)
def test_query_rechecks_forged_authorization(
    env: source_helpers.Environment,
    authorized_request: AuthorizedRequest,
    source_id: str,
) -> None:
    with pytest.raises(SourceSnapshotAuthorizationBindingDenied):
        query_service(env).list_snapshots(authorized_request, source_id=source_id)


@pytest.mark.parametrize("limit", [0, -1, MAX_QUERY_LIMIT + 1, True, 1.0])
def test_query_rejects_unbounded_limits(
    env: source_helpers.Environment, limit: object
) -> None:
    with pytest.raises(ValueError):
        query_service(env).list_snapshots(
            read_request(), source_id=SOURCE_ID, limit=cast(int, limit)
        )
    with pytest.raises(ValueError):
        query_service(env).history(
            read_request(), source_id=SOURCE_ID, limit=cast(int, limit)
        )


def test_query_registration_and_repository_failures_are_safe(
    env: source_helpers.Environment,
) -> None:
    service = query_service(env)
    request = read_request()
    env.registry.return_value = None
    with pytest.raises(SourceSnapshotNotFound):
        service.list_snapshots(request, source_id=SOURCE_ID)
    env.registry.return_value = replace(
        source_helpers.registration(), source_set_id="moved-set"
    )
    with pytest.raises(SourceSnapshotNotFound):
        service.history(request, source_id=SOURCE_ID)
    env.registry.return_value = object()
    with pytest.raises(SourceSnapshotNotFound):
        service.list_snapshots(request, source_id=SOURCE_ID)
    env.registry.failure = RuntimeError("secret repository body")
    with pytest.raises(SourceSnapshotUnavailable, match="unavailable"):
        service.list_snapshots(request, source_id=SOURCE_ID)

    env.registry.failure = None
    env.registry.return_value = source_helpers.registration()
    repository = cast(CountingRepository, env.repository)
    for method in ("list", "lifecycle"):
        repository.fail_method = method
        with pytest.raises(SourceSnapshotUnavailable, match="unavailable"):
            service.list_snapshots(request, source_id=SOURCE_ID)
    repository.fail_method = None
    repository.override["list"] = cast(Any, [object()])
    with pytest.raises(SourceSnapshotUnavailable):
        service.list_snapshots(request, source_id=SOURCE_ID)


def test_query_detects_corrupt_repository_artifacts(
    env: source_helpers.Environment,
) -> None:
    _, _, identity = source_helpers.active_snapshot(env)
    service = query_service(env)
    request = read_request()
    repository = cast(CountingRepository, env.repository)
    snapshot = snapshot_for(env, identity)

    repository.override["snapshot"] = corrupted_copy(
        snapshot, "source_id", "other-source"
    )
    with pytest.raises(SourceSnapshotUnavailable):
        service.get_snapshot(request, source_id=SOURCE_ID, identity=identity)
    repository.override.pop("snapshot")
    repository.fail_method = "snapshot"
    with pytest.raises(SourceSnapshotUnavailable):
        service.get_snapshot(request, source_id=SOURCE_ID, identity=identity)
    repository.fail_method = None

    metadata = repository.get_raw_metadata(
        DEPLOYMENT_ID, SOURCE_ID, snapshot.raw_object.object_id
    )
    assert metadata is not None
    repository.override["raw"] = corrupted_copy(metadata, "media_type", "text/plain")
    with pytest.raises(SourceSnapshotUnavailable):
        service.get_snapshot(request, source_id=SOURCE_ID, identity=identity)
    repository.override.pop("raw")
    repository.fail_method = "raw"
    with pytest.raises(SourceSnapshotUnavailable):
        service.get_snapshot(request, source_id=SOURCE_ID, identity=identity)
    repository.fail_method = None

    events, lifecycle = repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    repository.override["lifecycle"] = cast(Any, ([*events], lifecycle))
    with pytest.raises(SourceSnapshotUnavailable):
        service.history(request, source_id=SOURCE_ID)


def test_official_resolver_exact_record_assertion_and_historical_acceptance(
    env: source_helpers.Environment,
) -> None:
    first, second = source_helpers.active_pair(env)
    first_snapshot = snapshot_for(env, first[2])
    second_snapshot = snapshot_for(env, second[2])
    official = resolver(env)

    assert official.verify(
        tenant_id=TENANT_ID,
        deployment_id=DEPLOYMENT_ID,
        citation=citation(first_snapshot, first_snapshot.records[0].native_locator),
    )
    assert official.verify(
        tenant_id=TENANT_ID,
        deployment_id=DEPLOYMENT_ID,
        citation=citation(
            second_snapshot,
            second_snapshot.records[0].assertions[0].native_locator,
        ),
    )
    assert not official.verify(
        tenant_id=TENANT_ID,
        deployment_id=DEPLOYMENT_ID,
        citation=citation(second_snapshot, "/missing"),
    )

    env.clock.value += timedelta(seconds=1)
    source_helpers.rollback(env, first[2])
    assert official.verify(
        tenant_id=TENANT_ID,
        deployment_id=DEPLOYMENT_ID,
        citation=citation(second_snapshot, second_snapshot.records[0].native_locator),
    )


def test_official_resolver_requires_activation_exact_scope_and_unique_locator(
    env: source_helpers.Environment,
) -> None:
    _, parsed_id, parsed_identity = source_helpers.validated_snapshot(env)
    parsed = snapshot_for(env, parsed_identity)
    official = resolver(env)
    valid_citation = citation(parsed, parsed.records[0].native_locator)
    assert not official.verify(
        tenant_id=TENANT_ID,
        deployment_id=DEPLOYMENT_ID,
        citation=valid_citation,
    )

    source_helpers.approve(env, parsed_identity)
    assert not official.verify(
        tenant_id=TENANT_ID,
        deployment_id=DEPLOYMENT_ID,
        citation=valid_citation,
    )
    source_helpers.activate(env, parsed_identity)
    assert official.verify(
        tenant_id=TENANT_ID,
        deployment_id=DEPLOYMENT_ID,
        citation=valid_citation,
    )
    assert not official.verify(
        tenant_id="wrong-tenant",
        deployment_id=DEPLOYMENT_ID,
        citation=valid_citation,
    )
    assert not official.verify(
        tenant_id=TENANT_ID,
        deployment_id="wrong-deployment",
        citation=valid_citation,
    )
    assert not official.verify(
        tenant_id=TENANT_ID,
        deployment_id=DEPLOYMENT_ID,
        citation=citation(parsed, valid_citation.provision_locator, source_id="other"),
    )
    wrong_hash = "sha256:" + "f" * 64
    wrong_id = "snapshot-" + "f" * 64
    assert not official.verify(
        tenant_id=TENANT_ID,
        deployment_id=DEPLOYMENT_ID,
        citation=citation(
            parsed,
            valid_citation.provision_locator,
            snapshot_id=wrong_id,
            snapshot_content_hash=wrong_hash,
        ),
    )
    assert parsed_id == parsed.snapshot_id


def test_official_resolver_returns_false_for_ambiguous_and_dependency_failures(
    env: source_helpers.Environment,
) -> None:
    ambiguous_env = make_environment(AmbiguousLocatorParser())
    _, _, ambiguous_identity = source_helpers.active_snapshot(ambiguous_env)
    ambiguous_snapshot = snapshot_for(ambiguous_env, ambiguous_identity)
    ambiguous = resolver(ambiguous_env)
    assert ambiguous_snapshot.records[0].native_locator == (
        ambiguous_snapshot.records[0].assertions[0].native_locator
    )
    assert not ambiguous.verify(
        tenant_id=TENANT_ID,
        deployment_id=DEPLOYMENT_ID,
        citation=citation(
            ambiguous_snapshot, ambiguous_snapshot.records[0].native_locator
        ),
    )

    _, _, identity = source_helpers.active_snapshot(env)
    snapshot = snapshot_for(env, identity)
    official = resolver(env)
    repository = cast(CountingRepository, env.repository)
    valid = citation(snapshot, snapshot.records[0].native_locator)

    for method in ("snapshot", "raw", "lifecycle"):
        repository.fail_method = method
        assert not official.verify(
            tenant_id=TENANT_ID, deployment_id=DEPLOYMENT_ID, citation=valid
        )
    repository.fail_method = None
    env.registry.failure = RuntimeError("private failure")
    assert not official.verify(
        tenant_id=TENANT_ID, deployment_id=DEPLOYMENT_ID, citation=valid
    )
    env.registry.failure = None
    for registration in (
        None,
        replace(source_helpers.registration(), source_set_id="moved-set"),
        replace(source_helpers.registration(), active=False),
    ):
        env.registry.return_value = registration
        actual = official.verify(
            tenant_id=TENANT_ID, deployment_id=DEPLOYMENT_ID, citation=valid
        )
        assert actual is (registration is not None and registration.active is False)

    assert not official.verify(
        tenant_id=TENANT_ID,
        deployment_id=DEPLOYMENT_ID,
        citation=cast(OfficialSourceProvisionCitation, object()),
    )


def test_official_resolver_injects_into_rule_bundle_write_and_readiness(
    env: source_helpers.Environment,
) -> None:
    _, _, identity = source_helpers.active_snapshot(env)
    snapshot = snapshot_for(env, identity)
    official = resolver(env)
    official_citation = citation(snapshot, snapshot.records[0].native_locator)
    item = rule_helpers.bundle(official_citation)
    repository = InMemoryRuleBundleRepository()
    application = RuleBundleService(
        repository,
        deployment_id=DEPLOYMENT_ID,
        rule_set_id=rule_helpers.RULE_SET_ID,
        official_citation_resolver=official,
        clock=lambda: rule_helpers.NOW,
        event_id_factory=rule_helpers.EventIds(),
    )
    rule_helpers.save_approve_activate(application, item)
    readiness = RuleBundleReadinessService(
        repository,
        deployment_id=DEPLOYMENT_ID,
        rule_set_id=rule_helpers.RULE_SET_ID,
        official_citation_resolver=official,
        clock=lambda: rule_helpers.NOW,
    )
    assert readiness.is_current_active_ready(TENANT_ID) is True

    wrong_citation = citation(snapshot, "/missing")
    rejected = rule_helpers.bundle(wrong_citation)
    rejected_repository = InMemoryRuleBundleRepository()
    rejected_application = RuleBundleService(
        rejected_repository,
        deployment_id=DEPLOYMENT_ID,
        rule_set_id=rule_helpers.RULE_SET_ID,
        official_citation_resolver=official,
        clock=lambda: rule_helpers.NOW,
        event_id_factory=rule_helpers.EventIds(),
    )
    rejected_application.save_draft(
        rule_helpers.namespace_request(Operation.POLICY_DRAFT, "author-1"), rejected
    )
    with pytest.raises(RuleBundleGovernanceBlocked):
        rejected_application.approve(
            rule_helpers.bundle_request(Operation.POLICY_APPROVE, rejected),
            rule_helpers.identity(rejected),
            reason="must reject a non-exact source provision",
        )

    permissive_repository = InMemoryRuleBundleRepository()
    permissive = cast(Any, SimpleNamespace(verify=lambda **kwargs: True))
    permissive_application = RuleBundleService(
        permissive_repository,
        deployment_id=DEPLOYMENT_ID,
        rule_set_id=rule_helpers.RULE_SET_ID,
        official_citation_resolver=permissive,
        clock=lambda: rule_helpers.NOW,
        event_id_factory=rule_helpers.EventIds(),
    )
    rule_helpers.save_approve_activate(permissive_application, rejected)
    fail_closed = RuleBundleReadinessService(
        permissive_repository,
        deployment_id=DEPLOYMENT_ID,
        rule_set_id=rule_helpers.RULE_SET_ID,
        official_citation_resolver=official,
        clock=lambda: rule_helpers.NOW,
    )
    assert fail_closed.is_current_active_ready(TENANT_ID) is False


def test_dto_validators_enforce_exact_shape_order_and_consistency() -> None:
    content_hash = "sha256:" + "a" * 64
    other_hash = "sha256:" + "b" * 64
    reference = SourceSnapshotReferenceView(
        snapshot_id="snapshot-" + "a" * 64, content_hash=content_hash
    )
    changed = ChangedSourceRecordView(
        source_record_id="record-2",
        previous_record_hash=content_hash,
        new_record_hash=other_hash,
    )
    diff_hash = canonical_sha256(
        {
            "previous_snapshot": None,
            "new_snapshot": {
                "snapshot_id": reference.snapshot_id,
                "content_hash": reference.content_hash,
            },
            "added_record_ids": ["record-1"],
            "removed_record_ids": [],
            "changed_records": [
                {
                    "source_record_id": changed.source_record_id,
                    "previous_record_hash": changed.previous_record_hash,
                    "new_record_hash": changed.new_record_hash,
                }
            ],
        }
    )
    diff = SourceSnapshotDiffView(
        previous_snapshot=None,
        new_snapshot=reference,
        content_hash=diff_hash,
        added_record_ids=["record-1"],
        removed_record_ids=[],
        changed_records=[changed],
    )
    assert diff.changed_records == [changed]

    invalid_factories = (
        lambda: SourceSnapshotReferenceView(
            snapshot_id="snapshot-wrong", content_hash=content_hash
        ),
        lambda: ChangedSourceRecordView(
            source_record_id="record-1",
            previous_record_hash=content_hash,
            new_record_hash=content_hash,
        ),
        lambda: SourceSnapshotDiffView(
            previous_snapshot=None,
            new_snapshot=reference,
            content_hash=content_hash,
            added_record_ids=["record-2", "record-1"],
            removed_record_ids=[],
            changed_records=[],
        ),
        lambda: SourceSnapshotDiffView(
            previous_snapshot=None,
            new_snapshot=reference,
            content_hash=content_hash,
            added_record_ids=[],
            removed_record_ids=["record-1", "record-1"],
            changed_records=[],
        ),
        lambda: SourceSnapshotDiffView(
            previous_snapshot=None,
            new_snapshot=reference,
            content_hash=content_hash,
            added_record_ids=[],
            removed_record_ids=[],
            changed_records=[
                changed,
                replace(
                    changed,
                    previous_record_hash=other_hash,
                    new_record_hash=content_hash,
                ),
            ],
        ),
        lambda: SourceSnapshotListing(
            snapshots=cast(
                list[SourceSnapshotSummary],
                [
                    SimpleNamespace(snapshot_id="b", snapshot_content_hash=other_hash),
                    SimpleNamespace(
                        snapshot_id="a", snapshot_content_hash=content_hash
                    ),
                ],
            )
        ),
        lambda: SourceSnapshotHistory(
            events=cast(
                list[SourceSnapshotHistoryRecord],
                [SimpleNamespace(sequence=1), SimpleNamespace(sequence=3)],
            )
        ),
    )
    for factory in invalid_factories:
        with pytest.raises((ValidationError, TypeError, ValueError)):
            factory()


@pytest.mark.parametrize("bad", ["bad id", "", object()])
def test_query_components_reject_invalid_configuration(bad: object) -> None:
    repository = InMemorySourceSnapshotRepository()
    registry = Registry()
    kwargs: dict[str, object] = {
        "deployment_id": DEPLOYMENT_ID,
        "source_set_id": SOURCE_SET_ID,
        "control_tenant_id": TENANT_ID,
    }
    kwargs["deployment_id"] = bad
    with pytest.raises(ValueError):
        SourceSnapshotQueryService(repository, registry, **cast(Any, kwargs))
    with pytest.raises(ValueError):
        SourceSnapshotOfficialCitationResolver(
            repository, registry, **cast(Any, kwargs)
        )


def test_public_dto_invariants_reject_inconsistent_safe_views(
    env: source_helpers.Environment,
) -> None:
    first, second = source_helpers.active_pair(env)
    env.clock.value += timedelta(seconds=1)
    source_helpers.rollback(env, first[2])
    service = query_service(env)
    request = read_request()
    detail = service.get_snapshot(request, source_id=SOURCE_ID, identity=second[2])
    first_detail = service.get_snapshot(request, source_id=SOURCE_ID, identity=first[2])
    listing = service.list_snapshots(request, source_id=SOURCE_ID)
    history = service.history(request, source_id=SOURCE_ID)
    assert detail.diff is not None

    diff_mutations = (
        {"added_record_ids": ["record-z", "record-a"]},
        {"removed_record_ids": ["record-a", "record-a"]},
        {
            "changed_records": [
                ChangedSourceRecordView(
                    source_record_id="record-z",
                    previous_record_hash="sha256:" + "a" * 64,
                    new_record_hash="sha256:" + "b" * 64,
                ),
                ChangedSourceRecordView(
                    source_record_id="record-a",
                    previous_record_hash="sha256:" + "b" * 64,
                    new_record_hash="sha256:" + "c" * 64,
                ),
            ]
        },
        {"added_record_ids": ["record-1"], "removed_record_ids": ["record-1"]},
        {
            "added_record_ids": ["record-1"],
            "changed_records": detail.diff.changed_records,
        },
        {
            "removed_record_ids": ["record-1"],
            "changed_records": detail.diff.changed_records,
        },
        {"content_hash": "sha256:" + "f" * 64},
    )
    for diff_mutation in diff_mutations:
        diff_candidate = detail.diff.model_copy(update=diff_mutation)
        with pytest.raises(ValueError):
            cast(Any, diff_candidate).require_deterministic_order()

    summary = detail.summary
    summary_mutations = (
        {"snapshot_content_hash": "sha256:" + "f" * 64},
        {"raw_object_id": "invalid raw id"},
        {"media_type": "text/plain"},
        {"charset": "ascii"},
        {"parser_id": cast(Any, "other-parser")},
        {"parser_version": "2.0.0"},
        {"schema_id": "other-schema"},
        {"parsed_at": summary.retrieved_at - timedelta(seconds=1)},
        {"validated_at": summary.parsed_at - timedelta(seconds=1)},
        {
            "validation_reason_codes": [
                ValidationReasonCode.SCHEMA_MISMATCH,
                ValidationReasonCode.DECLARED_COUNT_MISMATCH,
            ]
        },
        {"validation_passed": None},
        {"validation_report_hash": None},
        {"state": SourceSnapshotState.PARSED},
        {"validation_reason_codes": [ValidationReasonCode.SCHEMA_MISMATCH]},
        {"declared_record_count": summary.record_count + 1},
        {"active": True},
    )
    for summary_mutation in summary_mutations:
        summary_candidate = summary.model_copy(update=summary_mutation)
        with pytest.raises(ValueError):
            cast(Any, summary_candidate).require_consistent_public_state()

    failed_env = make_environment()
    failed_object = source_helpers.ingest(
        failed_env, payload=source_helpers.content(declared_count=2)
    )
    failed_id = source_helpers.parse(failed_env, failed_object)
    failed_env.service.validate(
        source_helpers.authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
        source_id=SOURCE_ID,
        snapshot_id=failed_id,
    )
    failed_identity = source_helpers.immutable_identity(failed_env, failed_id)
    failed_detail = query_service(failed_env).get_snapshot(
        read_request(), source_id=SOURCE_ID, identity=failed_identity
    )
    missing_codes = failed_detail.summary.model_copy(
        update={"validation_reason_codes": []}
    )
    with pytest.raises(ValueError):
        cast(Any, missing_codes).require_consistent_public_state()

    no_diff = detail.model_copy(update={"diff": None})
    with pytest.raises(ValueError):
        cast(Any, no_diff).require_exact_diff()
    detail_mutations: tuple[dict[str, Any], ...] = (
        {"content_hash": "sha256:" + "f" * 64},
        {
            "new_snapshot": SourceSnapshotReferenceView(
                snapshot_id="snapshot-" + "f" * 64,
                content_hash="sha256:" + "f" * 64,
            )
        },
        {"previous_snapshot": None},
        {"added_record_ids": ["record-a", "record-b"]},
        {"removed_record_ids": ["record-a"]},
        {"changed_records": []},
    )
    for detail_mutation in detail_mutations:
        mismatched = detail.diff.model_copy(update=detail_mutation)
        detail_candidate = detail.model_copy(update={"diff": mismatched})
        with pytest.raises(ValueError):
            cast(Any, detail_candidate).require_exact_diff()
    assert cast(Any, first_detail).require_exact_diff() is first_detail

    reversed_listing = listing.model_copy(update={"snapshots": listing.snapshots[::-1]})
    with pytest.raises(ValueError):
        cast(Any, reversed_listing).require_deterministic_order()
    duplicated_listing = listing.model_copy(
        update={"snapshots": [listing.snapshots[0], listing.snapshots[0]]}
    )
    with pytest.raises(ValueError):
        cast(Any, duplicated_listing).require_deterministic_order()

    by_type = {item.event_type: item for item in history.events}
    retrieved = by_type[SourceSnapshotEventType.RETRIEVED]
    parsed = by_type[SourceSnapshotEventType.PARSED]
    validated = by_type[SourceSnapshotEventType.VALIDATED]
    rollback = by_type[SourceSnapshotEventType.ROLLED_BACK]
    history_mutations = (
        retrieved.model_copy(
            update={
                "validation_report_hash": "sha256:" + "a" * 64,
                "diff_content_hash": "sha256:" + "b" * 64,
            }
        ),
        validated.model_copy(update={"validation_report_hash": None}),
        validated.model_copy(
            update={
                "validation_passed": None,
                "validation_report_hash": None,
                "diff_content_hash": None,
            }
        ),
        validated.model_copy(
            update={"validation_reason_codes": [ValidationReasonCode.SCHEMA_MISMATCH]}
        ),
        retrieved.model_copy(update={"snapshot": validated.snapshot}),
        parsed.model_copy(update={"snapshot": None}),
        parsed.model_copy(update={"previous_active_snapshot": rollback.snapshot}),
        rollback.model_copy(update={"previous_active_snapshot": None}),
        validated.model_copy(
            update={
                "validation_reason_codes": [
                    ValidationReasonCode.SCHEMA_MISMATCH,
                    ValidationReasonCode.DECLARED_COUNT_MISMATCH,
                ]
            }
        ),
    )
    for history_candidate in history_mutations:
        with pytest.raises(ValueError):
            cast(Any, history_candidate).require_consistent_validation_summary()

    failed_history = query_service(failed_env).history(
        read_request(), source_id=SOURCE_ID
    )
    failed_event = next(
        item
        for item in failed_history.events
        if item.event_type is SourceSnapshotEventType.VALIDATION_FAILED
    )
    wrong_failure = failed_event.model_copy(update={"validation_passed": True})
    with pytest.raises(ValueError):
        cast(Any, wrong_failure).require_consistent_validation_summary()

    gapped = history.model_copy(
        update={"events": [history.events[0], history.events[2]]}
    )
    with pytest.raises(ValueError):
        cast(Any, gapped).require_ascending_sequence()


def test_query_maps_all_public_corruption_paths(
    env: source_helpers.Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = source_helpers.active_pair(env)
    service = query_service(env)
    request = read_request()
    repository = cast(CountingRepository, env.repository)
    snapshots = repository.list_snapshots(
        DEPLOYMENT_ID, SOURCE_ID, limit=MAX_QUERY_LIMIT
    )

    repository.override["list"] = tuple(reversed(snapshots))
    with pytest.raises(SourceSnapshotUnavailable):
        service.list_snapshots(request, source_id=SOURCE_ID)
    repository.override["list"] = (snapshots[0], snapshots[0])
    with pytest.raises(SourceSnapshotUnavailable):
        service.list_snapshots(request, source_id=SOURCE_ID)
    repository.override["list"] = cast(Any, (object(),))
    with pytest.raises(SourceSnapshotUnavailable):
        service.list_snapshots(request, source_id=SOURCE_ID)
    corrupt_second = corrupted_copy(snapshots[1], "source_id", "other-source")
    repository.override["list"] = (snapshots[0], corrupt_second)
    with pytest.raises(SourceSnapshotUnavailable):
        service.list_snapshots(request, source_id=SOURCE_ID, limit=1)
    repository.override.pop("list")

    monkeypatch.setattr(service, "_summary", lambda *args, **kwargs: object())
    with pytest.raises(SourceSnapshotUnavailable):
        service.list_snapshots(request, source_id=SOURCE_ID)
    monkeypatch.undo()

    monkeypatch.setattr(service, "_diff_view", lambda diff: object())
    with pytest.raises(SourceSnapshotUnavailable):
        service.get_snapshot(request, source_id=SOURCE_ID, identity=second[2])
    monkeypatch.undo()

    monkeypatch.setattr(service, "_history_record", lambda event: object())
    with pytest.raises(SourceSnapshotUnavailable):
        service.history(request, source_id=SOURCE_ID)
    monkeypatch.undo()

    with pytest.raises(SourceSnapshotNotFound):
        service._require_registration("unsafe source")
    forged_identity = corrupted_copy(first[2], "content_hash", "not-a-hash")
    with pytest.raises(SourceSnapshotNotFound):
        service.get_snapshot(
            request,
            source_id=SOURCE_ID,
            identity=forged_identity,
        )

    repository.override["snapshot"] = snapshot_for(env, second[2])
    with pytest.raises(SourceSnapshotNotFound):
        service.get_snapshot(request, source_id=SOURCE_ID, identity=first[2])
    repository.override["snapshot"] = corrupted_copy(
        snapshot_for(env, first[2]), "content_hash", "sha256:" + "f" * 64
    )
    with pytest.raises(SourceSnapshotUnavailable):
        service.get_snapshot(request, source_id=SOURCE_ID, identity=first[2])
    repository.override.pop("snapshot")

    snapshot = snapshot_for(env, first[2])
    metadata = repository.get_raw_metadata(
        DEPLOYMENT_ID, SOURCE_ID, snapshot.raw_object.object_id
    )
    assert metadata is not None
    repository.override["raw"] = corrupted_copy(metadata, "object_id", object())
    with pytest.raises(SourceSnapshotUnavailable):
        service.get_snapshot(request, source_id=SOURCE_ID, identity=first[2])
    repository.override.pop("raw")

    events, _ = repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    repository.override["lifecycle"] = (
        events,
        SourceSnapshotLifecycle((), (), None, ()),
    )
    with pytest.raises(SourceSnapshotUnavailable):
        service.history(request, source_id=SOURCE_ID)


def test_private_integrity_helpers_fail_closed_on_inconsistent_evidence(
    env: source_helpers.Environment,
) -> None:
    _, _, identity = source_helpers.active_snapshot(env)
    snapshot = snapshot_for(env, identity)
    service = query_service(env)
    events, lifecycle = env.repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    empty = SourceSnapshotLifecycle((), (), None, ())
    with pytest.raises(SourceSnapshotUnavailable):
        service._validation_report(snapshot, events, empty)

    parsed_lifecycle = fold_source_snapshot_events(events[:3])
    with pytest.raises(SourceSnapshotUnavailable):
        service._validation_report(snapshot, events, parsed_lifecycle)

    without_validation = tuple(
        event
        for event in events
        if event.event_type is not SourceSnapshotEventType.VALIDATED
    )
    with pytest.raises(SourceSnapshotUnavailable):
        service._validation_report(snapshot, without_validation, lifecycle)

    report_event = next(
        event
        for event in events
        if event.event_type is SourceSnapshotEventType.VALIDATED
    )
    assert report_event.validation_report is not None
    corrupt_report = corrupted_copy(report_event.validation_report, "reasons", object())
    corrupt_event = corrupted_copy(report_event, "validation_report", corrupt_report)
    corrupt_events = tuple(
        corrupt_event if event is report_event else event for event in events
    )
    with pytest.raises(SourceSnapshotUnavailable):
        service._validation_report(snapshot, corrupt_events, lifecycle)

    with pytest.raises(SourceSnapshotUnavailable):
        service._summary(snapshot, events, lifecycle, report=object())
    with pytest.raises(SourceSnapshotUnavailable):
        service._summary(snapshot, events, empty, report=None)

    corrupt_domain_event = corrupted_copy(events[0], "reason", object())
    assert query_app._event_is_reconstructable(corrupt_domain_event) is False
    assert query_app._has_verified_passing_validation((), snapshot) is False

    failed_env = make_environment()
    failed_object = source_helpers.ingest(
        failed_env, payload=source_helpers.content(declared_count=2)
    )
    failed_id = source_helpers.parse(failed_env, failed_object)
    failed_env.service.validate(
        source_helpers.authorized(Operation.SOURCE_SNAPSHOT_VALIDATE),
        source_id=SOURCE_ID,
        snapshot_id=failed_id,
    )
    failed_snapshot = snapshot_for(
        failed_env, source_helpers.immutable_identity(failed_env, failed_id)
    )
    failed_events, _ = failed_env.repository.get_lifecycle_snapshot(
        DEPLOYMENT_ID, SOURCE_ID
    )
    assert (
        query_app._has_verified_passing_validation(failed_events, failed_snapshot)
        is False
    )
    invalid_report_event = next(
        event
        for event in events
        if event.event_type is SourceSnapshotEventType.VALIDATED
    )
    assert invalid_report_event.validation_report is not None
    bad_report = corrupted_copy(
        invalid_report_event.validation_report, "reasons", object()
    )
    bad_event = corrupted_copy(invalid_report_event, "validation_report", bad_report)
    assert (
        query_app._has_verified_passing_validation(
            tuple(
                bad_event if event is invalid_report_event else event
                for event in events
            ),
            snapshot,
        )
        is False
    )


def test_resolver_rejects_extended_and_corrupt_domain_values(
    env: source_helpers.Environment,
) -> None:
    _, _, identity = source_helpers.active_snapshot(env)
    snapshot = snapshot_for(env, identity)
    official = resolver(env)
    valid = citation(snapshot, snapshot.records[0].native_locator)

    class ExtendedCitation(OfficialSourceProvisionCitation):
        pass

    extended = ExtendedCitation(
        valid.citation_ref,
        valid.source_id,
        valid.snapshot_id,
        valid.snapshot_content_hash,
        valid.provision_locator,
    )
    assert not official.verify(
        tenant_id=TENANT_ID,
        deployment_id=DEPLOYMENT_ID,
        citation=extended,
    )

    repository = cast(CountingRepository, env.repository)

    class NonFiniteSnapshot(ParsedSourceSnapshot):
        def verify_integrity(self) -> None:
            return None

    non_finite = object.__new__(NonFiniteSnapshot)
    for field in ParsedSourceSnapshot.__dataclass_fields__:
        object.__setattr__(non_finite, field, getattr(snapshot, field))
    object.__setattr__(non_finite, "parser_id", "unregistered-parser")
    repository.override["snapshot"] = non_finite
    assert not official.verify(
        tenant_id=TENANT_ID, deployment_id=DEPLOYMENT_ID, citation=valid
    )
    repository.override.pop("snapshot")

    repository.override["raw"] = None
    assert not official.verify(
        tenant_id=TENANT_ID, deployment_id=DEPLOYMENT_ID, citation=valid
    )
    repository.override.pop("raw")

    events, lifecycle = repository.get_lifecycle_snapshot(DEPLOYMENT_ID, SOURCE_ID)
    corrupt_event = corrupted_copy(events[0], "source_id", "other-source")
    repository.override["lifecycle"] = ((corrupt_event, *events[1:]), lifecycle)
    assert not official.verify(
        tenant_id=TENANT_ID, deployment_id=DEPLOYMENT_ID, citation=valid
    )
