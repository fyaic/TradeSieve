"""Authorized rule-bundle application, in-memory UoW, and evaluator tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Barrier, Lock
from typing import cast

import pytest
from pydantic import ValidationError

from tradesieve.adapters.in_memory_rule_bundle import InMemoryRuleBundleRepository
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
from tradesieve.application.contracts import ScreeningRequest
from tradesieve.application.rule_bundle import (
    MAX_RULE_BUNDLE_HISTORY,
    MAX_RULE_BUNDLE_LISTING,
    RULE_BUNDLE_TARGET_TYPE,
    RULE_SET_TARGET_TYPE,
    SCREENING_REQUEST_TARGET_TYPE,
    ImmutableRuleBundleIdentity,
    OfficialCitationResolver,
    RuleBundleAuthorizationBindingDenied,
    RuleBundleConflict,
    RuleBundleGovernanceBlocked,
    RuleBundleNotFound,
    RuleBundleReadinessService,
    RuleBundleService,
    RuleBundleUnavailable,
    RulePresenceEvaluationService,
    SafeCitationRecord,
    SafeRuleRecord,
    rule_bundle_authorization_target_id,
    rule_set_authorization_target_id,
    screening_request_authorization_target_id,
)
from tradesieve.domain import rule_bundle as domain
from tradesieve.domain.rule_bundle import (
    CanonicalFactPath,
    CitationKind,
    DataCompletenessPresenceSpec,
    DraftWriteOutcome,
    EffectiveWindow,
    GovernanceBlockReason,
    InternalPolicyCitation,
    InvalidLifecycleTransition,
    LifecycleActorType,
    LifecycleWriteOutcome,
    OfficialSourceProvisionCitation,
    RuleAction,
    RuleActivity,
    RuleBundleCommandAuditRecord,
    RuleBundleCommandOutcome,
    RuleBundleCommandReason,
    RuleBundleEventType,
    RuleBundleLifecycleEvent,
    RuleBundleRef,
    RuleBundleState,
    RuleBundleVersion,
    RuleEvaluationOutcome,
    RuleEvaluatorKind,
    RuleFixture,
    RuleScope,
    RuleSetLifecycle,
    RuleVersion,
    calculate_rescreen_impact,
    fold_rule_bundle_events,
    rule_bundle_ref,
)
from tradesieve.ports.rule_bundle import RuleBundlePersistenceError

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
WINDOW = EffectiveWindow(NOW - timedelta(days=1), NOW + timedelta(days=1))
HASH_A = "sha256:" + "a" * 64


class EventIds:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return f"generated-event-{self.value}"


class ConcurrentEventIds(EventIds):
    def __init__(self) -> None:
        super().__init__()
        self.lock = Lock()

    def __call__(self) -> str:
        with self.lock:
            return super().__call__()


class MutableOfficialResolver:
    def __init__(self, result: bool = True, *, fail: bool = False) -> None:
        self.result = result
        self.fail = fail
        self.calls: list[tuple[str, str, str]] = []

    def verify(
        self,
        *,
        tenant_id: str,
        deployment_id: str,
        citation: OfficialSourceProvisionCitation,
    ) -> bool:
        self.calls.append((tenant_id, deployment_id, citation.citation_ref))
        if self.fail:
            raise RuntimeError("resolver detail")
        return self.result


class FailingReadRepository(InMemoryRuleBundleRepository):
    def __init__(self) -> None:
        super().__init__()
        self.fail_bundle_list = False
        self.fail_events = False
        self.fail_get = False
        self.fail_audit_append = False
        self.bundle_listing_override: tuple[RuleBundleVersion, ...] | None = None
        self.event_listing_override: tuple[RuleBundleLifecycleEvent, ...] | None = None
        self.snapshot_override: (
            tuple[tuple[RuleBundleLifecycleEvent, ...], RuleSetLifecycle] | None
        ) = None

    def list_bundles(
        self,
        tenant_id: str,
        deployment_id: str,
        rule_set_id: str,
        *,
        limit: int,
    ) -> tuple[RuleBundleVersion, ...]:
        if self.fail_bundle_list:
            raise RuleBundlePersistenceError
        if self.bundle_listing_override is not None:
            return self.bundle_listing_override
        return super().list_bundles(tenant_id, deployment_id, rule_set_id, limit=limit)

    def list_lifecycle_events(
        self,
        tenant_id: str,
        deployment_id: str,
        rule_set_id: str,
        *,
        limit: int,
    ) -> tuple[RuleBundleLifecycleEvent, ...]:
        if self.fail_events:
            raise RuleBundlePersistenceError
        if self.event_listing_override is not None:
            return self.event_listing_override
        return super().list_lifecycle_events(
            tenant_id, deployment_id, rule_set_id, limit=limit
        )

    def get_lifecycle_snapshot(
        self,
        tenant_id: str,
        deployment_id: str,
        rule_set_id: str,
    ) -> tuple[tuple[RuleBundleLifecycleEvent, ...], RuleSetLifecycle]:
        if self.snapshot_override is not None:
            return self.snapshot_override
        if self.fail_events:
            raise RuleBundlePersistenceError
        if self.event_listing_override is not None:
            try:
                return (
                    self.event_listing_override,
                    fold_rule_bundle_events(self.event_listing_override),
                )
            except (InvalidLifecycleTransition, TypeError, ValueError) as exc:
                raise RuleBundlePersistenceError from exc
        return super().get_lifecycle_snapshot(tenant_id, deployment_id, rule_set_id)

    def get_bundle(
        self,
        tenant_id: str,
        deployment_id: str,
        rule_set_id: str,
        bundle_id: str,
        version: str,
    ) -> RuleBundleVersion | None:
        if self.fail_get:
            raise RuleBundlePersistenceError
        return super().get_bundle(
            tenant_id, deployment_id, rule_set_id, bundle_id, version
        )

    def append_command_audit(self, audit: RuleBundleCommandAuditRecord) -> None:
        if self.fail_audit_append:
            raise RuleBundlePersistenceError
        super().append_command_audit(audit)


def internal_citation() -> InternalPolicyCitation:
    return InternalPolicyCitation(
        citation_ref="citation-internal",
        policy_id="synthetic-policy",
        policy_version="1.0.0",
        provision_locator="section-1",
        private_policy_text="Private synthetic policy text must never reach read DTOs.",
    )


def official_citation() -> OfficialSourceProvisionCitation:
    return OfficialSourceProvisionCitation(
        citation_ref="citation-official",
        source_id="official-source",
        snapshot_id="snapshot-1",
        snapshot_content_hash=HASH_A,
        provision_locator="article-1",
    )


def fixture(
    fixture_id: str,
    *,
    action: RuleAction = RuleAction.QUOTE_RELEASE,
    activities: tuple[RuleActivity, ...] = (RuleActivity.EXPORT,),
    present: tuple[CanonicalFactPath, ...] = (CanonicalFactPath.LEGAL_NEXUS,),
    outcome: RuleEvaluationOutcome = RuleEvaluationOutcome.FACTS_PRESENT,
    missing: tuple[CanonicalFactPath, ...] = (),
) -> RuleFixture:
    return RuleFixture(fixture_id, action, activities, present, outcome, missing)


def legal_rule(
    rule_id: str = "rule-nexus",
    version: str = "1.0.0",
    *,
    citations: tuple[InternalPolicyCitation | OfficialSourceProvisionCitation, ...]
    | None = None,
    fixtures: tuple[RuleFixture, ...] | None = None,
    internal_notes: str = "Private rule note.",
) -> RuleVersion:
    return RuleVersion(
        rule_id=rule_id,
        version=version,
        kind=RuleEvaluatorKind.LEGAL_NEXUS_PRESENCE,
        owner="policy-owner",
        effective_window=WINDOW,
        scope=RuleScope(
            (RuleAction.QUOTE_RELEASE,),
            (RuleActivity.EXPORT,),
        ),
        evaluator=domain.LegalNexusPresenceSpec(),
        citations=citations if citations is not None else (internal_citation(),),
        fixtures=fixtures
        if fixtures is not None
        else (
            fixture("fixture-a-present"),
            fixture(
                "fixture-b-missing",
                present=(),
                outcome=RuleEvaluationOutcome.MISSING_FACTS,
                missing=(CanonicalFactPath.LEGAL_NEXUS,),
            ),
            fixture(
                "fixture-c-not-applicable",
                action=RuleAction.PAYMENT,
                present=(),
                outcome=RuleEvaluationOutcome.NOT_APPLICABLE,
            ),
        ),
        internal_notes=internal_notes,
    )


def all_paths_rule(version: str = "1.0.0") -> RuleVersion:
    required = (
        CanonicalFactPath.ACTIVITIES,
        CanonicalFactPath.GOODS,
        CanonicalFactPath.LEGAL_NEXUS,
        CanonicalFactPath.PARTIES,
        CanonicalFactPath.PAYMENT,
        CanonicalFactPath.PROPOSED_ACTION,
        CanonicalFactPath.ROUTE,
    )
    return RuleVersion(
        rule_id="rule-all-paths",
        version=version,
        kind=RuleEvaluatorKind.DATA_COMPLETENESS_PRESENCE,
        owner="policy-owner",
        effective_window=WINDOW,
        scope=RuleScope(
            (RuleAction.QUOTE_RELEASE,),
            (RuleActivity.EXPORT,),
        ),
        evaluator=DataCompletenessPresenceSpec(required),
        citations=(internal_citation(),),
        fixtures=(
            fixture(
                "fixture-all-present",
                present=(
                    CanonicalFactPath.GOODS,
                    CanonicalFactPath.LEGAL_NEXUS,
                    CanonicalFactPath.PARTIES,
                    CanonicalFactPath.PAYMENT,
                    CanonicalFactPath.ROUTE,
                ),
            ),
        ),
    )


def bundle(
    version: str = "1.0.0",
    *,
    authored_by: str = "author-1",
    rules: tuple[RuleVersion, ...] | None = None,
    internal_notes: str = "Private bundle note.",
    effective_window: EffectiveWindow = WINDOW,
) -> RuleBundleVersion:
    return RuleBundleVersion(
        tenant_id="tenant-1",
        deployment_id="demo",
        rule_set_id="ruleset-1",
        bundle_id="bundle-1",
        version=version,
        owner="policy-owner",
        effective_window=effective_window,
        authored_by=authored_by,
        rules=rules if rules is not None else (legal_rule(version=version),),
        internal_notes=internal_notes,
    )


def identity(item: RuleBundleVersion) -> ImmutableRuleBundleIdentity:
    reference = rule_bundle_ref(item)
    return ImmutableRuleBundleIdentity(
        bundle_id=reference.bundle_id,
        version=reference.version,
        content_hash=reference.content_hash,
    )


def actor(
    subject: str,
    actor_type: ActorType = ActorType.HUMAN,
    *,
    tenant_id: str = "tenant-1",
) -> ActorContext:
    return ActorContext(
        subject=subject,
        client_id="policy-ui",
        tenant_id=tenant_id,
        actor_type=actor_type,
        scopes=frozenset(Scope),
        roles=frozenset(Role),
        issuer="https://identity.example.test/",
        audience="tradesieve-api",
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=5),
        demo_identity=False,
    )


def authorized(
    operation: Operation,
    *,
    principal: ActorContext,
    target_type: str,
    target_id: str,
    target_tenant: str = "tenant-1",
    context_tenant: str = "tenant-1",
) -> AuthorizedRequest:
    return AuthorizedRequest(
        context=RequestContext(principal, context_tenant, "correlation-1"),
        operation=operation,
        target=TargetObject(target_tenant, target_type, target_id),
        audit_event_id=f"authorization-{operation.value.lower()}",
    )


def namespace_request(operation: Operation, subject: str) -> AuthorizedRequest:
    return authorized(
        operation,
        principal=actor(subject),
        target_type=RULE_SET_TARGET_TYPE,
        target_id=rule_set_authorization_target_id("demo", "ruleset-1"),
    )


def bundle_request(
    operation: Operation,
    item: RuleBundleVersion,
    subject: str = "approver-2",
) -> AuthorizedRequest:
    reference = rule_bundle_ref(item)
    return authorized(
        operation,
        principal=actor(subject),
        target_type=RULE_BUNDLE_TARGET_TYPE,
        target_id=rule_bundle_authorization_target_id(
            "demo",
            "ruleset-1",
            reference.bundle_id,
            reference.version,
            reference.content_hash,
        ),
    )


def service(
    repository: InMemoryRuleBundleRepository,
    *,
    resolver: OfficialCitationResolver | None = None,
    clock: datetime = NOW,
    ids: EventIds | None = None,
) -> RuleBundleService:
    return RuleBundleService(
        repository,
        deployment_id="demo",
        rule_set_id="ruleset-1",
        official_citation_resolver=resolver,
        clock=lambda: clock,
        event_id_factory=ids or EventIds(),
    )


def save_approve_activate(
    application: RuleBundleService,
    item: RuleBundleVersion,
) -> None:
    assert (
        application.save_draft(
            namespace_request(Operation.POLICY_DRAFT, "author-1"), item
        )
        is DraftWriteOutcome.APPLIED
    )
    assert (
        application.approve(
            bundle_request(Operation.POLICY_APPROVE, item),
            identity(item),
            reason="Fixtures and cited synthetic policy reviewed.",
        )
        is LifecycleWriteOutcome.APPLIED
    )
    assert (
        application.activate(
            bundle_request(Operation.POLICY_ACTIVATE, item),
            identity(item),
            reason="Activate the reviewed synthetic rule bundle.",
        )
        is LifecycleWriteOutcome.APPLIED
    )


def test_rule_bundle_readiness_requires_valid_effective_content_and_citations() -> None:
    empty = InMemoryRuleBundleRepository()
    readiness = RuleBundleReadinessService(
        empty,
        deployment_id="demo",
        rule_set_id="ruleset-1",
        clock=lambda: NOW,
    )
    assert readiness.is_current_active_ready("tenant-1") is False

    unavailable = FailingReadRepository()
    unavailable.fail_events = True
    unavailable_readiness = RuleBundleReadinessService(
        unavailable,
        deployment_id="demo",
        rule_set_id="ruleset-1",
        clock=lambda: NOW,
    )
    assert unavailable_readiness.is_current_active_ready("tenant-1") is False

    repository = InMemoryRuleBundleRepository()
    item = bundle()
    save_approve_activate(service(repository), item)
    readiness = RuleBundleReadinessService(
        repository,
        deployment_id="demo",
        rule_set_id="ruleset-1",
        clock=lambda: NOW,
    )
    assert readiness.is_current_active_ready("tenant-1") is True
    repository.corrupt_remove_bundle_for_test(
        "tenant-1", "demo", "ruleset-1", item.bundle_id, item.version
    )
    assert readiness.is_current_active_ready("tenant-1") is False

    expired_repository = InMemoryRuleBundleRepository()
    expiring_window = EffectiveWindow(NOW - timedelta(days=1), NOW + timedelta(hours=1))
    expiring = bundle(
        effective_window=expiring_window,
        rules=(replace(legal_rule(), effective_window=expiring_window),),
    )
    save_approve_activate(service(expired_repository), expiring)
    expired = RuleBundleReadinessService(
        expired_repository,
        deployment_id="demo",
        rule_set_id="ruleset-1",
        clock=lambda: NOW + timedelta(hours=2),
    )
    assert expired.is_current_active_ready("tenant-1") is False

    official = OfficialSourceProvisionCitation(
        "citation-official",
        "source-1",
        "snapshot-1",
        HASH_A,
        "article-1",
    )
    official_bundle = bundle(rules=(legal_rule(citations=(official,)),))
    official_repository = InMemoryRuleBundleRepository()
    verified = MutableOfficialResolver(True)
    save_approve_activate(
        service(official_repository, resolver=verified), official_bundle
    )
    default_official = RuleBundleReadinessService(
        official_repository,
        deployment_id="demo",
        rule_set_id="ruleset-1",
        clock=lambda: NOW,
    )
    assert default_official.is_current_active_ready("tenant-1") is False
    verified_readiness = RuleBundleReadinessService(
        official_repository,
        deployment_id="demo",
        rule_set_id="ruleset-1",
        official_citation_resolver=verified,
        clock=lambda: NOW,
    )
    assert verified_readiness.is_current_active_ready("tenant-1") is True
    verified.result = cast(bool, 1)
    assert verified_readiness.is_current_active_ready("tenant-1") is False
    verified.fail = True
    assert verified_readiness.is_current_active_ready("tenant-1") is False

    invalid_clock = RuleBundleReadinessService(
        official_repository,
        deployment_id="demo",
        rule_set_id="ruleset-1",
        clock=lambda: NOW.replace(tzinfo=None),
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        invalid_clock.is_current_active_ready("tenant-1")


def screening_request(*, complete: bool = True) -> ScreeningRequest:
    payload: dict[str, object] = {
        "schema_version": "1.0.0",
        "tenant_id": "tenant-1",
        "correlation_id": "screening-correlation-1",
        "data_classification": "SYNTHETIC",
        "external_object": {
            "system": "synthetic-crm",
            "object_type": "QUOTE",
            "object_id": "quote-1",
            "object_version": "1",
        },
        "proposed_action": "QUOTE_RELEASE",
        "activities": ["SALE", "EXPORT", "EXPORT"] if complete else [],
        "legal_nexus": (
            [
                {
                    "nexus_ref": "nexus-1",
                    "nexus_type": "REGULATORY_REGIME",
                    "regime_code": "SYNTHETIC",
                }
            ]
            if complete
            else []
        ),
        "parties": (
            [
                {
                    "party_ref": "buyer-1",
                    "roles": ["BUYER"],
                    "entity_type": "ORGANIZATION",
                }
            ]
            if complete
            else []
        ),
        "ownership_and_control": [],
        "goods": ([{"line_ref": "line-1"}] if complete else []),
        "route": ({"origin_country": "CN"} if complete else None),
        "documents": [],
        "payment": (
            {"payment_ref": "payment-1", "amount": "1", "currency": "EUR"}
            if complete
            else None
        ),
    }
    return ScreeningRequest.model_validate(payload)


def screening_authorized(
    request: ScreeningRequest,
    *,
    deployment_id: str = "demo",
    rule_set_id: str = "ruleset-1",
) -> AuthorizedRequest:
    return authorized(
        Operation.SCREENING_SUBMIT,
        principal=actor("screening-service", ActorType.SERVICE),
        target_type=SCREENING_REQUEST_TARGET_TYPE,
        target_id=screening_request_authorization_target_id(
            request, deployment_id, rule_set_id
        ),
    )


def audits(
    repository: InMemoryRuleBundleRepository,
) -> tuple[RuleBundleCommandAuditRecord, ...]:
    return repository.list_command_audits("tenant-1", "demo", "ruleset-1", limit=1000)


def events(
    repository: InMemoryRuleBundleRepository,
) -> tuple[RuleBundleLifecycleEvent, ...]:
    return repository.list_lifecycle_events("tenant-1", "demo", "ruleset-1", limit=1000)


def lifecycle_audits(
    event: RuleBundleLifecycleEvent,
    applied_reason: RuleBundleCommandReason,
    idempotent_reason: RuleBundleCommandReason,
    *,
    prefix: str,
) -> tuple[RuleBundleCommandAuditRecord, RuleBundleCommandAuditRecord]:
    reference = event.new_bundle or event.previous_bundle
    assert reference is not None
    applied = RuleBundleCommandAuditRecord(
        command_event_id=f"{prefix}-applied",
        authorization_event_id=f"{prefix}-authorization",
        lifecycle_event_id=event.event_id,
        tenant_id=event.tenant_id,
        deployment_id=event.deployment_id,
        rule_set_id=event.rule_set_id,
        bundle_id=reference.bundle_id,
        bundle_version=reference.version,
        bundle_content_hash=reference.content_hash,
        target_type=RULE_BUNDLE_TARGET_TYPE,
        target_id="target",
        actor_id=event.actor_id,
        actor_type=event.actor_type,
        operation="POLICY_WRITE",
        outcome=RuleBundleCommandOutcome.SUCCESS,
        reason=applied_reason,
        occurred_at=NOW,
    )
    return (
        applied,
        replace(
            applied,
            command_event_id=f"{prefix}-idempotent",
            lifecycle_event_id=None,
            reason=idempotent_reason,
        ),
    )


def test_authorization_targets_are_bounded_collision_resistant_and_exact() -> None:
    namespace = rule_set_authorization_target_id("d" * 128, "r" * 128)
    first = rule_bundle_authorization_target_id(
        "d" * 128, "r" * 128, "b" * 128, "1.0.0", HASH_A
    )
    second = rule_bundle_authorization_target_id(
        "d" * 128, "r" * 128, "b" * 128, "1.0.0", "sha256:" + "b" * 64
    )
    assert len(namespace) < 128
    assert len(first) < 128
    assert first != second
    request = screening_request()
    assert (
        len(screening_request_authorization_target_id(request, "demo", "ruleset-1"))
        < 128
    )
    assert screening_request_authorization_target_id(
        request, "demo", "ruleset-1"
    ) != screening_request_authorization_target_id(request, "other", "ruleset-1")
    assert screening_request_authorization_target_id(
        request, "demo", "ruleset-1"
    ) != screening_request_authorization_target_id(request, "demo", "other")

    for values in [
        {"bundle_id": "bad id", "version": "1.0.0", "content_hash": HASH_A},
        {"bundle_id": "bundle", "version": "1", "content_hash": HASH_A},
        {"bundle_id": "bundle", "version": "1.0.0", "content_hash": "bad"},
    ]:
        with pytest.raises(ValidationError):
            ImmutableRuleBundleIdentity.model_validate(values)


def test_save_draft_is_immutable_idempotent_and_audited_without_duplicate_event() -> (
    None
):
    repository = InMemoryRuleBundleRepository()
    application = service(repository)
    item = bundle()
    request = namespace_request(Operation.POLICY_DRAFT, "author-1")

    assert application.save_draft(request, item) is DraftWriteOutcome.APPLIED
    assert application.save_draft(request, item) is DraftWriteOutcome.IDEMPOTENT
    assert len(events(repository)) == 1
    assert [item.reason for item in audits(repository)] == [
        RuleBundleCommandReason.DRAFT_APPLIED,
        RuleBundleCommandReason.DRAFT_IDEMPOTENT,
    ]

    conflict = replace(item, internal_notes="Different immutable content.")
    with pytest.raises(RuleBundleConflict):
        application.save_draft(request, conflict)
    assert (
        repository.get_bundle("tenant-1", "demo", "ruleset-1", "bundle-1", "1.0.0")
        == item
    )
    assert len(events(repository)) == 1
    assert audits(repository)[-1].reason is RuleBundleCommandReason.DRAFT_CONFLICT
    assert (
        audits(repository)[-1].bundle_content_hash
        == rule_bundle_ref(conflict).content_hash
    )
    assert (
        audits(repository)[-1].bundle_content_hash != rule_bundle_ref(item).content_hash
    )


def test_draft_atomic_failure_and_corruption_never_leave_partial_state_or_false_audit() -> (
    None
):
    repository = InMemoryRuleBundleRepository()
    application = service(repository)
    item = bundle()
    request = namespace_request(Operation.POLICY_DRAFT, "author-1")
    repository.fail_next_atomic_write()
    with pytest.raises(RuleBundleUnavailable):
        application.save_draft(request, item)
    assert repository.list_bundles("tenant-1", "demo", "ruleset-1", limit=10) == ()
    assert events(repository) == ()
    assert audits(repository) == ()

    assert application.save_draft(request, item) is DraftWriteOutcome.APPLIED
    repository.corrupt_remove_drafted_event_for_test(
        "tenant-1", "demo", "ruleset-1", rule_bundle_ref(item)
    )
    before_audits = audits(repository)
    with pytest.raises(RuleBundleUnavailable):
        application.save_draft(request, item)
    assert audits(repository) == before_audits


def test_concurrent_atomic_writes_serialize_without_lost_events_or_audits() -> None:
    repository = InMemoryRuleBundleRepository()
    application = service(repository, ids=ConcurrentEventIds())
    item = bundle()
    draft_request = namespace_request(Operation.POLICY_DRAFT, "author-1")
    draft_barrier = Barrier(3)

    def save_once() -> DraftWriteOutcome:
        draft_barrier.wait()
        return application.save_draft(draft_request, item)

    with ThreadPoolExecutor(max_workers=2) as executor:
        draft_futures = [executor.submit(save_once) for _ in range(2)]
        draft_barrier.wait()
        draft_outcomes = [future.result(timeout=5) for future in draft_futures]
    assert sorted(draft_outcomes) == [
        DraftWriteOutcome.APPLIED,
        DraftWriteOutcome.IDEMPOTENT,
    ]
    assert len(events(repository)) == 1
    assert len(audits(repository)) == 2

    approve_request = bundle_request(Operation.POLICY_APPROVE, item)
    approve_barrier = Barrier(3)

    def approve_once() -> LifecycleWriteOutcome:
        approve_barrier.wait()
        return application.approve(
            approve_request,
            identity(item),
            reason="Concurrent approval intent.",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        approval_futures = [executor.submit(approve_once) for _ in range(2)]
        approve_barrier.wait()
        approval_outcomes = [future.result(timeout=5) for future in approval_futures]
    assert sorted(approval_outcomes) == [
        LifecycleWriteOutcome.APPLIED,
        LifecycleWriteOutcome.IDEMPOTENT,
    ]
    assert len(events(repository)) == 2
    assert len(audits(repository)) == 4

    original_audit = replace(
        audits(repository)[0],
        command_event_id="concurrent-audit-seed",
        lifecycle_event_id=None,
        outcome=RuleBundleCommandOutcome.FAILURE,
        reason=RuleBundleCommandReason.INVALID_LIFECYCLE,
    )
    concurrent_audits = tuple(
        replace(original_audit, command_event_id=f"concurrent-audit-{index}")
        for index in range(16)
    )
    audit_barrier = Barrier(len(concurrent_audits) + 1)

    def append_once(audit: RuleBundleCommandAuditRecord) -> str:
        audit_barrier.wait()
        repository.append_command_audit(audit)
        return audit.command_event_id

    with ThreadPoolExecutor(max_workers=len(concurrent_audits)) as executor:
        audit_futures = [
            executor.submit(append_once, audit) for audit in concurrent_audits
        ]
        audit_barrier.wait()
        appended_ids = {future.result(timeout=5) for future in audit_futures}
    stored_ids = {audit.command_event_id for audit in audits(repository)}
    assert appended_ids <= stored_ids
    assert len(stored_ids) == 4 + len(concurrent_audits)


def test_fail_next_atomic_write_is_consumed_once_under_concurrent_contention() -> None:
    repository = InMemoryRuleBundleRepository()
    application = service(repository, ids=ConcurrentEventIds())
    item = bundle()
    request = namespace_request(Operation.POLICY_DRAFT, "author-1")
    repository.fail_next_atomic_write()
    barrier = Barrier(3)

    def attempt() -> DraftWriteOutcome | type[RuleBundleUnavailable]:
        barrier.wait()
        try:
            return application.save_draft(request, item)
        except RuleBundleUnavailable:
            return RuleBundleUnavailable

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(attempt) for _ in range(2)]
        barrier.wait()
        outcomes = [future.result(timeout=5) for future in futures]
    assert outcomes.count(RuleBundleUnavailable) == 1
    assert outcomes.count(DraftWriteOutcome.APPLIED) == 1
    assert len(events(repository)) == 1
    assert len(audits(repository)) == 1


@pytest.mark.parametrize(
    "authorization_request",
    [
        namespace_request(Operation.POLICY_READ, "author-1"),
        authorized(
            Operation.POLICY_DRAFT,
            principal=actor("author-1"),
            target_type=RULE_SET_TARGET_TYPE,
            target_id="wrong-target",
        ),
        authorized(
            Operation.POLICY_DRAFT,
            principal=actor("author-1"),
            target_type=RULE_SET_TARGET_TYPE,
            target_id=rule_set_authorization_target_id("demo", "ruleset-1"),
            target_tenant="tenant-2",
        ),
    ],
)
def test_save_draft_rejects_operation_tenant_target_and_author_confusion(
    authorization_request: AuthorizedRequest,
) -> None:
    repository = InMemoryRuleBundleRepository()
    application = service(repository)
    with pytest.raises(RuleBundleAuthorizationBindingDenied):
        application.save_draft(authorization_request, bundle())
    assert audits(repository)[-1].reason is (
        RuleBundleCommandReason.AUTHORIZATION_BINDING_DENIED
    )
    assert events(repository) == ()

    with pytest.raises(RuleBundleAuthorizationBindingDenied):
        application.save_draft(
            namespace_request(Operation.POLICY_DRAFT, "different-author"),
            bundle(),
        )
    assert audits(repository)[-1].actor_id == "different-author"


def test_internal_policy_happy_path_redacts_private_text_and_notes() -> None:
    repository = InMemoryRuleBundleRepository()
    application = service(repository)
    item = bundle()
    save_approve_activate(application, item)

    view = application.get_bundle(
        bundle_request(Operation.POLICY_READ, item), identity(item)
    )
    dumped = view.model_dump_json()
    assert "Private synthetic policy text" not in dumped
    assert "Private rule note" not in dumped
    assert "Private bundle note" not in dumped
    assert view.summary.state is RuleBundleState.ACTIVE
    assert view.rules[0].citations[0].policy_content_hash is not None
    assert (
        application.current_active(namespace_request(Operation.POLICY_READ, "reader-1"))
        == view
    )
    listing = application.list_bundles(
        namespace_request(Operation.POLICY_READ, "reader-1"), limit=1
    )
    assert listing.bundles == [view.summary]
    history = application.history(
        namespace_request(Operation.POLICY_READ, "reader-1"), limit=3
    )
    assert [item.event_type for item in history.events] == [
        RuleBundleEventType.DRAFTED,
        RuleBundleEventType.APPROVED,
        RuleBundleEventType.ACTIVATED,
    ]
    for invalid_limit in [0, 257, True, cast(int, "1")]:
        with pytest.raises(ValueError):
            application.list_bundles(
                namespace_request(Operation.POLICY_READ, "reader-1"),
                limit=invalid_limit,
            )


def test_safe_read_dtos_reject_mixed_citations_and_noncanonical_order() -> None:
    internal = {
        "citation_ref": "internal",
        "kind": CitationKind.INTERNAL_POLICY,
        "provision_locator": "section-1",
        "policy_id": "policy-1",
        "policy_version": "1.0.0",
        "policy_content_hash": HASH_A,
    }
    official = {
        "citation_ref": "official",
        "kind": CitationKind.OFFICIAL_SOURCE_PROVISION,
        "provision_locator": "article-1",
        "source_id": "source-1",
        "snapshot_id": "snapshot-1",
        "snapshot_content_hash": HASH_A,
    }
    for invalid in (
        {**internal, "policy_id": None},
        {**internal, "source_id": "source-1"},
        {**official, "snapshot_id": None},
        {**official, "policy_id": "policy-1"},
    ):
        with pytest.raises(ValidationError):
            SafeCitationRecord.model_validate(invalid)

    repository = InMemoryRuleBundleRepository()
    application = service(repository)
    item = bundle()
    save_approve_activate(application, item)
    rule_payload = (
        application.get_bundle(
            bundle_request(Operation.POLICY_READ, item), identity(item)
        )
        .rules[0]
        .model_dump(mode="python")
    )
    with pytest.raises(ValidationError):
        SafeRuleRecord.model_validate(
            {
                **rule_payload,
                "proposed_actions": [
                    RuleAction.QUOTE_RELEASE,
                    RuleAction.PAYMENT,
                ],
            }
        )
    with pytest.raises(ValidationError):
        SafeRuleRecord.model_validate(
            {**rule_payload, "citations": rule_payload["citations"] * 2}
        )


def test_official_citation_default_is_unavailable_and_verified_reads_are_safe() -> None:
    item = bundle(rules=(legal_rule(citations=(official_citation(),)),))

    unavailable_repository = InMemoryRuleBundleRepository()
    unavailable = service(unavailable_repository)
    unavailable.save_draft(namespace_request(Operation.POLICY_DRAFT, "author-1"), item)
    with pytest.raises(RuleBundleGovernanceBlocked):
        unavailable.approve(
            bundle_request(Operation.POLICY_APPROVE, item),
            identity(item),
            reason="No official snapshot resolver is configured.",
        )

    repository = InMemoryRuleBundleRepository()
    application = service(repository, resolver=MutableOfficialResolver(True))
    save_approve_activate(application, item)
    citation = (
        application.get_bundle(
            bundle_request(Operation.POLICY_READ, item), identity(item)
        )
        .rules[0]
        .citations[0]
    )
    assert citation.source_id == "official-source"
    assert citation.snapshot_id == "snapshot-1"
    assert citation.policy_id is None


@pytest.mark.parametrize("resolver_mode", ["false", "error", "truthy-non-bool"])
def test_official_citation_verification_is_scoped_and_fails_closed(
    resolver_mode: str,
) -> None:
    repository = InMemoryRuleBundleRepository()
    resolver = MutableOfficialResolver(
        result=False,
        fail=resolver_mode == "error",
    )
    if resolver_mode == "truthy-non-bool":
        resolver.result = cast(bool, 1)
    application = service(repository, resolver=resolver)
    item = bundle(rules=(legal_rule(citations=(official_citation(),)),))
    application.save_draft(namespace_request(Operation.POLICY_DRAFT, "author-1"), item)
    with pytest.raises(RuleBundleGovernanceBlocked) as exc_info:
        application.approve(
            bundle_request(Operation.POLICY_APPROVE, item),
            identity(item),
            reason="Attempt official approval.",
        )
    assert exc_info.value.issues == (
        domain.GovernanceIssue(
            GovernanceBlockReason.OFFICIAL_CITATION_UNVERIFIED,
            "rule-nexus",
        ),
    )
    assert resolver.calls == [("tenant-1", "demo", "citation-official")]
    assert events(repository)[-1].event_type is RuleBundleEventType.DRAFTED
    assert audits(repository)[-1].reason is (
        RuleBundleCommandReason.OFFICIAL_CITATION_UNVERIFIED
    )


def test_governance_and_fixtures_rerun_on_approve_and_activate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = InMemoryRuleBundleRepository()
    resolver = MutableOfficialResolver(True)
    application = service(repository, resolver=resolver)
    item = bundle(rules=(legal_rule(citations=(official_citation(),)),))
    application.save_draft(namespace_request(Operation.POLICY_DRAFT, "author-1"), item)
    calls = 0
    original = domain.failed_rule_fixtures

    def counted(rule: RuleVersion) -> tuple[str, ...]:
        nonlocal calls
        calls += 1
        return original(rule)

    monkeypatch.setattr(domain, "failed_rule_fixtures", counted)
    application.approve(
        bundle_request(Operation.POLICY_APPROVE, item),
        identity(item),
        reason="Approve after fixture run.",
    )
    resolver.result = False
    with pytest.raises(RuleBundleGovernanceBlocked):
        application.activate(
            bundle_request(Operation.POLICY_ACTIVATE, item),
            identity(item),
            reason="Activation must reverify.",
        )
    assert calls == 2
    assert len(resolver.calls) == 2

    failing = bundle(
        version="1.1.0",
        rules=(
            legal_rule(
                version="1.1.0",
                fixtures=(
                    fixture(
                        "fixture-wrong",
                        present=(),
                        outcome=RuleEvaluationOutcome.FACTS_PRESENT,
                    ),
                ),
            ),
        ),
    )
    application.save_draft(
        namespace_request(Operation.POLICY_DRAFT, "author-1"), failing
    )
    with pytest.raises(RuleBundleGovernanceBlocked) as fixture_failure:
        application.approve(
            bundle_request(Operation.POLICY_APPROVE, failing),
            identity(failing),
            reason="Must not approve failing fixtures.",
        )
    assert (
        fixture_failure.value.issues[0].reason is GovernanceBlockReason.FIXTURE_FAILED
    )


def test_lifecycle_retries_are_semantically_idempotent_and_failure_atomic() -> None:
    repository = InMemoryRuleBundleRepository()
    application = service(repository)
    item = bundle()
    application.save_draft(namespace_request(Operation.POLICY_DRAFT, "author-1"), item)
    approve_request = bundle_request(Operation.POLICY_APPROVE, item)
    assert (
        application.approve(
            approve_request, identity(item), reason="Approve synthetic bundle."
        )
        is LifecycleWriteOutcome.APPLIED
    )
    assert (
        application.approve(
            approve_request, identity(item), reason="Approve synthetic bundle."
        )
        is LifecycleWriteOutcome.IDEMPOTENT
    )
    with pytest.raises(RuleBundleConflict):
        application.approve(
            approve_request, identity(item), reason="Changed approval intent."
        )
    activate_request = bundle_request(Operation.POLICY_ACTIVATE, item)
    assert (
        application.activate(
            activate_request, identity(item), reason="Activate synthetic bundle."
        )
        is LifecycleWriteOutcome.APPLIED
    )
    assert (
        application.activate(
            activate_request, identity(item), reason="Activate synthetic bundle."
        )
        is LifecycleWriteOutcome.IDEMPOTENT
    )
    assert len(events(repository)) == 3

    retire_request = bundle_request(Operation.POLICY_RETIRE, item)
    repository.fail_next_atomic_write()
    before_events = events(repository)
    before_audits = audits(repository)
    with pytest.raises(RuleBundleUnavailable):
        application.retire(
            retire_request, identity(item), reason="Injected atomic failure."
        )
    assert events(repository) == before_events
    assert audits(repository) == before_audits
    assert (
        application.retire(
            retire_request, identity(item), reason="Retire after recovery."
        )
        is LifecycleWriteOutcome.APPLIED
    )
    assert (
        application.retire(
            retire_request, identity(item), reason="Retire after recovery."
        )
        is LifecycleWriteOutcome.IDEMPOTENT
    )


def test_activation_change_rollback_and_impacts_derive_from_immutable_contents() -> (
    None
):
    repository = InMemoryRuleBundleRepository()
    application = service(repository)
    first = bundle()
    save_approve_activate(application, first)
    second = bundle(
        "1.1.0",
        rules=(legal_rule(version="1.1.0", internal_notes="Changed rule."),),
    )
    application.save_draft(
        namespace_request(Operation.POLICY_DRAFT, "author-1"), second
    )
    application.approve(
        bundle_request(Operation.POLICY_APPROVE, second),
        identity(second),
        reason="Approve version two.",
    )
    application.activate(
        bundle_request(Operation.POLICY_ACTIVATE, second),
        identity(second),
        reason="Activate version two.",
    )
    rollback_request = bundle_request(Operation.POLICY_ROLLBACK, first)
    assert (
        application.rollback(
            rollback_request,
            identity(first),
            reason="Rollback to reviewed version one.",
        )
        is LifecycleWriteOutcome.APPLIED
    )
    assert (
        application.rollback(
            rollback_request,
            identity(first),
            reason="Rollback to reviewed version one.",
        )
        is LifecycleWriteOutcome.IDEMPOTENT
    )
    lifecycle = fold_rule_bundle_events(events(repository))
    assert lifecycle.active_bundle == rule_bundle_ref(first)
    assert lifecycle.state_for(rule_bundle_ref(second)) is RuleBundleState.ROLLED_BACK
    rollback_event = events(repository)[-1]
    assert rollback_event.rescreen_impact == calculate_rescreen_impact(second, first)


def test_lifecycle_illegal_state_binding_not_found_and_author_defense_are_audited() -> (
    None
):
    repository = InMemoryRuleBundleRepository()
    application = service(repository)
    item = bundle()
    application.save_draft(namespace_request(Operation.POLICY_DRAFT, "author-1"), item)

    with pytest.raises(RuleBundleConflict):
        application.activate(
            bundle_request(Operation.POLICY_ACTIVATE, item),
            identity(item),
            reason="Cannot activate a draft.",
        )
    assert audits(repository)[-1].reason is RuleBundleCommandReason.INVALID_LIFECYCLE

    wrong_target = authorized(
        Operation.POLICY_APPROVE,
        principal=actor("approver-2"),
        target_type=RULE_BUNDLE_TARGET_TYPE,
        target_id="wrong-target",
    )
    with pytest.raises(RuleBundleAuthorizationBindingDenied):
        application.approve(
            wrong_target, identity(item), reason="Wrong immutable target."
        )
    assert audits(repository)[-1].reason is (
        RuleBundleCommandReason.AUTHORIZATION_BINDING_DENIED
    )

    with pytest.raises(RuleBundleNotFound):
        missing_identity = ImmutableRuleBundleIdentity(
            bundle_id="missing-bundle",
            version="1.0.0",
            content_hash=HASH_A,
        )
        application.approve(
            authorized(
                Operation.POLICY_APPROVE,
                principal=actor("approver-2"),
                target_type=RULE_BUNDLE_TARGET_TYPE,
                target_id=rule_bundle_authorization_target_id(
                    "demo",
                    "ruleset-1",
                    missing_identity.bundle_id,
                    missing_identity.version,
                    missing_identity.content_hash,
                ),
            ),
            missing_identity,
            reason="Missing bundle.",
        )
    assert audits(repository)[-1].reason is RuleBundleCommandReason.NOT_FOUND

    with pytest.raises(RuleBundleAuthorizationBindingDenied):
        application.approve(
            bundle_request(Operation.POLICY_APPROVE, item, subject="author-1"),
            identity(item),
            reason="Author cannot approve own bundle.",
        )
    assert audits(repository)[-1].reason is (
        RuleBundleCommandReason.AUTHOR_SEPARATION_DENIED
    )


def test_lifecycle_rejects_wrong_active_missing_rollback_and_invalid_reason() -> None:
    repository = InMemoryRuleBundleRepository()
    application = service(repository)
    first = bundle()
    save_approve_activate(application, first)

    with pytest.raises(RuleBundleConflict):
        application.rollback(
            bundle_request(Operation.POLICY_ROLLBACK, first),
            identity(first),
            reason="No prior active version exists.",
        )

    second = bundle("1.1.0", rules=(legal_rule(version="1.1.0"),))
    save_approve_activate(application, second)
    with pytest.raises(RuleBundleConflict):
        application.retire(
            bundle_request(Operation.POLICY_RETIRE, first),
            identity(first),
            reason="Cannot retire a non-active bundle.",
        )

    third = bundle("1.2.0", rules=(legal_rule(version="1.2.0"),))
    application.save_draft(namespace_request(Operation.POLICY_DRAFT, "author-1"), third)
    with pytest.raises(RuleBundleConflict):
        application.approve(
            bundle_request(Operation.POLICY_APPROVE, third),
            identity(third),
            reason=" ",
        )
    assert audits(repository)[-1].reason is RuleBundleCommandReason.INVALID_LIFECYCLE


def test_authorization_rejection_does_not_claim_a_failure_audit_that_cannot_persist() -> (
    None
):
    repository = FailingReadRepository()
    application = service(repository)
    item = bundle()
    application.save_draft(namespace_request(Operation.POLICY_DRAFT, "author-1"), item)
    repository.fail_audit_append = True
    with pytest.raises(RuleBundleUnavailable):
        application.approve(
            authorized(
                Operation.POLICY_APPROVE,
                principal=actor("approver-2"),
                target_type=RULE_BUNDLE_TARGET_TYPE,
                target_id="wrong-target",
            ),
            identity(item),
            reason="This denial audit cannot be persisted.",
        )
    assert [audit.reason for audit in audits(repository)] == [
        RuleBundleCommandReason.DRAFT_APPLIED
    ]

    with pytest.raises(RuleBundleAuthorizationBindingDenied):
        RuleBundleService._require_authorized(
            cast(AuthorizedRequest, object()),
            Operation.POLICY_READ,
            RULE_SET_TARGET_TYPE,
            "target",
        )


def test_repository_cas_rejects_ambiguous_transition_and_drafted_append() -> None:
    repository = InMemoryRuleBundleRepository()
    application = service(repository)
    item = bundle()
    save_approve_activate(application, item)
    reference = rule_bundle_ref(item)
    original = events(repository)[-1]
    mismatched_impact = calculate_rescreen_impact(item, item)
    attempted = replace(
        original,
        sequence=original.sequence + 1,
        event_id="ambiguous-activation",
        previous_bundle=reference,
        rescreen_impact=mismatched_impact,
    )
    applied_audit = RuleBundleCommandAuditRecord(
        command_event_id="ambiguous-command-applied",
        authorization_event_id="authorization-activate",
        lifecycle_event_id=attempted.event_id,
        tenant_id="tenant-1",
        deployment_id="demo",
        rule_set_id="ruleset-1",
        bundle_id="bundle-1",
        bundle_version="1.0.0",
        bundle_content_hash=reference.content_hash,
        target_type=RULE_BUNDLE_TARGET_TYPE,
        target_id="target",
        actor_id="approver-2",
        actor_type=LifecycleActorType.HUMAN,
        operation="POLICY_ACTIVATE",
        outcome=RuleBundleCommandOutcome.SUCCESS,
        reason=RuleBundleCommandReason.ACTIVATED,
        occurred_at=NOW,
    )
    idempotent_audit = replace(
        applied_audit,
        command_event_id="ambiguous-command-idempotent",
        lifecycle_event_id=None,
        reason=RuleBundleCommandReason.ACTIVATE_IDEMPOTENT,
    )
    assert (
        repository.append_lifecycle_atomic(attempted, applied_audit, idempotent_audit)
        is LifecycleWriteOutcome.CONFLICT
    )
    assert len(events(repository)) == 3

    drafted = events(repository)[0]
    with pytest.raises(ValueError, match="save_draft_atomic"):
        repository.append_lifecycle_atomic(
            drafted,
            replace(
                applied_audit,
                command_event_id="draft-applied",
                lifecycle_event_id=drafted.event_id,
            ),
            replace(
                idempotent_audit,
                command_event_id="draft-idempotent",
            ),
        )

    same_id_different_intent = replace(original, reason="Different human intent.")
    same_id_applied, same_id_idempotent = lifecycle_audits(
        same_id_different_intent,
        RuleBundleCommandReason.ACTIVATED,
        RuleBundleCommandReason.ACTIVATE_IDEMPOTENT,
        prefix="same-id-different-intent",
    )
    with pytest.raises(RuleBundlePersistenceError):
        repository.append_lifecycle_atomic(
            same_id_different_intent,
            same_id_applied,
            same_id_idempotent,
        )

    stored_approval = events(repository)[1]
    approval_applied, approval_idempotent = lifecycle_audits(
        stored_approval,
        RuleBundleCommandReason.APPROVED,
        RuleBundleCommandReason.APPROVE_IDEMPOTENT,
        prefix="approval-no-longer-responsible",
    )
    with pytest.raises(RuleBundlePersistenceError):
        repository.append_lifecycle_atomic(
            stored_approval,
            approval_applied,
            approval_idempotent,
        )

    missing_reference = RuleBundleRef("missing", "1.0.0", HASH_A)
    missing_event = replace(
        stored_approval,
        sequence=stored_approval.sequence + 10,
        event_id="missing-reference",
        new_bundle=missing_reference,
    )
    with pytest.raises(RuleBundlePersistenceError):
        repository.append_lifecycle_atomic(
            missing_event,
            cast(RuleBundleCommandAuditRecord, None),
            cast(RuleBundleCommandAuditRecord, None),
        )

    with pytest.raises(ValueError, match="command audit"):
        repository.append_lifecycle_atomic(
            stored_approval,
            replace(
                approval_applied,
                command_event_id="mismatched-audit",
                tenant_id="tenant-2",
            ),
            approval_idempotent,
        )

    draft_repository = InMemoryRuleBundleRepository()
    with pytest.raises(ValueError, match="exactly identify"):
        draft_repository.save_draft_atomic(
            item,
            replace(drafted, event_type=RuleBundleEventType.APPROVED),
            cast(RuleBundleCommandAuditRecord, None),
            cast(RuleBundleCommandAuditRecord, None),
        )

    draft_applied, draft_idempotent = lifecycle_audits(
        drafted,
        RuleBundleCommandReason.DRAFT_APPLIED,
        RuleBundleCommandReason.DRAFT_IDEMPOTENT,
        prefix="draft-identity",
    )
    for mismatch in (
        {"actor_id": "different-actor"},
        {"actor_type": LifecycleActorType.SERVICE},
        {"occurred_at": NOW + timedelta(seconds=1)},
        {"bundle_content_hash": "sha256:" + "b" * 64},
    ):
        isolated = InMemoryRuleBundleRepository()
        with pytest.raises(ValueError, match="command audit"):
            isolated.save_draft_atomic(
                item,
                drafted,
                replace(draft_applied, **mismatch),
                draft_idempotent,
            )
        assert isolated.list_bundles("tenant-1", "demo", "ruleset-1", limit=10) == ()
        assert events(isolated) == ()
        assert audits(isolated) == ()

    corrupt_approval = replace(
        drafted,
        event_id="corrupt-approval-before-draft",
        event_type=RuleBundleEventType.APPROVED,
    )
    draft_repository.corrupt_replace_lifecycle_events_for_test(
        "tenant-1", "demo", "ruleset-1", (corrupt_approval,)
    )
    draft_applied, draft_idempotent = lifecycle_audits(
        drafted,
        RuleBundleCommandReason.DRAFT_APPLIED,
        RuleBundleCommandReason.DRAFT_IDEMPOTENT,
        prefix="draft-after-corrupt-approval",
    )
    assert (
        draft_repository.save_draft_atomic(
            item,
            drafted,
            draft_applied,
            draft_idempotent,
        )
        is DraftWriteOutcome.CONFLICT
    )


def test_evaluator_maps_all_request_paths_sorts_rules_and_derives_stable_ids() -> None:
    repository = InMemoryRuleBundleRepository()
    application = service(repository)
    item = bundle(
        rules=(
            all_paths_rule(),
            legal_rule(rule_id="rule-nexus"),
        )
    )
    save_approve_activate(application, item)
    evaluator = RulePresenceEvaluationService(
        repository,
        deployment_id="demo",
        rule_set_id="ruleset-1",
        clock=lambda: NOW,
    )
    request = screening_request()
    auth = screening_authorized(request)
    first = evaluator.evaluate(auth, request)
    second = evaluator.evaluate(auth, request)
    assert first == second
    assert [record.rule.resource_id for record in first] == [
        "rule-all-paths",
        "rule-nexus",
    ]
    assert all(record.outcome.value == "FACTS_PRESENT" for record in first)
    assert all(record.evaluation_id.startswith("eval-") for record in first)
    assert not any(
        forbidden in record.model_dump_json()
        for record in first
        for forbidden in ("PASS", "CLEAR", "HUMAN_CLEARED")
    )

    incomplete = screening_request(complete=False)
    missing = evaluator.evaluate(screening_authorized(incomplete), incomplete)
    assert missing[0].missing_fact_paths == [
        "activities",
        "goods",
        "legal_nexus",
        "parties",
        "payment",
        "route",
    ]
    assert missing[1].missing_fact_paths == ["activities", "legal_nexus"]


def test_evaluator_fails_closed_for_binding_missing_expired_and_corrupt_active_state() -> (
    None
):
    request = screening_request()
    empty_repository = InMemoryRuleBundleRepository()
    evaluator = RulePresenceEvaluationService(
        empty_repository,
        deployment_id="demo",
        rule_set_id="ruleset-1",
        clock=lambda: NOW,
    )
    with pytest.raises(RuleBundleUnavailable):
        evaluator.evaluate(screening_authorized(request), request)
    with pytest.raises(RuleBundleAuthorizationBindingDenied):
        evaluator.evaluate(
            authorized(
                Operation.SCREENING_SUBMIT,
                principal=actor("screening-service", ActorType.SERVICE),
                target_type=SCREENING_REQUEST_TARGET_TYPE,
                target_id="wrong-screening-target",
            ),
            request,
        )
    for reused in (
        screening_authorized(request, deployment_id="other"),
        screening_authorized(request, rule_set_id="other"),
    ):
        with pytest.raises(RuleBundleAuthorizationBindingDenied):
            evaluator.evaluate(reused, request)
    tenant_two_authorization = authorized(
        Operation.SCREENING_SUBMIT,
        principal=actor(
            "screening-service",
            ActorType.SERVICE,
            tenant_id="tenant-2",
        ),
        target_type=SCREENING_REQUEST_TARGET_TYPE,
        target_id=screening_request_authorization_target_id(
            request, "demo", "ruleset-1"
        ),
        target_tenant="tenant-2",
        context_tenant="tenant-2",
    )
    with pytest.raises(RuleBundleAuthorizationBindingDenied):
        evaluator.evaluate(tenant_two_authorization, request)

    repository = InMemoryRuleBundleRepository()
    application = service(repository)
    item = bundle()
    save_approve_activate(application, item)
    expired = RulePresenceEvaluationService(
        repository,
        deployment_id="demo",
        rule_set_id="ruleset-1",
        clock=lambda: NOW + timedelta(days=2),
    )
    with pytest.raises(RuleBundleUnavailable):
        expired.evaluate(screening_authorized(request), request)

    repository.corrupt_remove_bundle_for_test(
        "tenant-1", "demo", "ruleset-1", "bundle-1", "1.0.0"
    )
    corrupt_evaluator = RulePresenceEvaluationService(
        repository,
        deployment_id="demo",
        rule_set_id="ruleset-1",
        clock=lambda: NOW,
    )
    with pytest.raises(RuleBundleUnavailable):
        corrupt_evaluator.evaluate(screening_authorized(request), request)


def test_repository_limits_and_safe_query_binding_fail_closed() -> None:
    repository = InMemoryRuleBundleRepository()
    for method in (
        repository.list_bundles,
        repository.list_lifecycle_events,
        repository.list_command_audits,
    ):
        with pytest.raises(ValueError):
            method("tenant-1", "demo", "ruleset-1", limit=0)
    application = service(repository)
    assert (
        application.current_active(namespace_request(Operation.POLICY_READ, "reader-1"))
        is None
    )
    with pytest.raises(RuleBundleAuthorizationBindingDenied):
        application.list_bundles(namespace_request(Operation.POLICY_DRAFT, "reader-1"))
    with pytest.raises(RuleBundleNotFound):
        application.get_bundle(
            authorized(
                Operation.POLICY_READ,
                principal=actor("reader-1"),
                target_type=RULE_BUNDLE_TARGET_TYPE,
                target_id=rule_bundle_authorization_target_id(
                    "demo", "ruleset-1", "missing", "1.0.0", HASH_A
                ),
            ),
            ImmutableRuleBundleIdentity(
                bundle_id="missing",
                version="1.0.0",
                content_hash=HASH_A,
            ),
        )


def test_query_boundaries_reject_oversized_corrupt_and_unbound_reads() -> None:
    repository = FailingReadRepository()
    application = service(repository)
    item = bundle()
    application.save_draft(namespace_request(Operation.POLICY_DRAFT, "author-1"), item)
    drafted = events(repository)[0]
    before = audits(repository)
    with pytest.raises(RuleBundleAuthorizationBindingDenied):
        application.get_bundle(
            authorized(
                Operation.POLICY_READ,
                principal=actor("reader-1"),
                target_type=RULE_BUNDLE_TARGET_TYPE,
                target_id="wrong-target",
            ),
            identity(item),
        )
    assert audits(repository) == before

    repository.bundle_listing_override = (item,) * (MAX_RULE_BUNDLE_LISTING + 1)
    with pytest.raises(RuleBundleUnavailable):
        application.list_bundles(namespace_request(Operation.POLICY_READ, "reader-1"))

    repository.bundle_listing_override = (item,)
    repository.event_listing_override = ()
    with pytest.raises(RuleBundleUnavailable):
        application.list_bundles(namespace_request(Operation.POLICY_READ, "reader-1"))

    repository.bundle_listing_override = None
    repository.event_listing_override = (drafted,) * (MAX_RULE_BUNDLE_HISTORY + 1)
    with pytest.raises(RuleBundleUnavailable):
        application.history(namespace_request(Operation.POLICY_READ, "reader-1"))
    evaluator = RulePresenceEvaluationService(
        repository,
        deployment_id="demo",
        rule_set_id="ruleset-1",
        clock=lambda: NOW,
    )
    request = screening_request()
    with pytest.raises(RuleBundleUnavailable):
        evaluator.evaluate(screening_authorized(request), request)

    repository.event_listing_override = None
    repository.snapshot_override = (
        (drafted,) * (MAX_RULE_BUNDLE_HISTORY + 1),
        RuleSetLifecycle(states=(), active_bundle=None),
    )
    with pytest.raises(RuleBundleUnavailable):
        application.history(namespace_request(Operation.POLICY_READ, "reader-1"))
    with pytest.raises(RuleBundleUnavailable):
        evaluator.evaluate(screening_authorized(request), request)

    repository.snapshot_override = None
    repository.event_listing_override = (
        cast(RuleBundleLifecycleEvent, "corrupt-event"),
    )
    with pytest.raises(RuleBundleUnavailable):
        application.history(namespace_request(Operation.POLICY_READ, "reader-1"))
    with pytest.raises(RuleBundleUnavailable):
        evaluator.evaluate(screening_authorized(request), request)

    corrupt_repository = InMemoryRuleBundleRepository()
    corrupt_repository.corrupt_replace_lifecycle_events_for_test(
        "tenant-1",
        "demo",
        "ruleset-1",
        (replace(drafted, event_type=RuleBundleEventType.APPROVED),),
    )
    with pytest.raises(RuleBundlePersistenceError):
        corrupt_repository.get_lifecycle_snapshot("tenant-1", "demo", "ruleset-1")


def test_invalid_clocks_and_event_ids_fail_before_mutation() -> None:
    repository = InMemoryRuleBundleRepository()
    invalid_clock = RuleBundleService(
        repository,
        deployment_id="demo",
        rule_set_id="ruleset-1",
        clock=lambda: NOW.replace(tzinfo=None),
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        invalid_clock.save_draft(
            namespace_request(Operation.POLICY_DRAFT, "author-1"), bundle()
        )

    invalid_ids = RuleBundleService(
        repository,
        deployment_id="demo",
        rule_set_id="ruleset-1",
        clock=lambda: NOW,
        event_id_factory=lambda: cast(str, 1),
    )
    with pytest.raises(ValueError, match="event ID"):
        invalid_ids.save_draft(
            namespace_request(Operation.POLICY_DRAFT, "author-1"), bundle()
        )

    evaluator = RulePresenceEvaluationService(
        repository,
        deployment_id="demo",
        rule_set_id="ruleset-1",
        clock=lambda: NOW.replace(tzinfo=None),
    )
    request = screening_request()
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluator.evaluate(screening_authorized(request), request)


def test_repository_read_failures_are_translated_for_queries_and_evaluation() -> None:
    repository = FailingReadRepository()
    application = service(repository)
    repository.fail_bundle_list = True
    with pytest.raises(RuleBundleUnavailable):
        application.list_bundles(namespace_request(Operation.POLICY_READ, "reader-1"))
    repository.fail_bundle_list = False
    repository.fail_events = True
    with pytest.raises(RuleBundleUnavailable):
        application.current_active(namespace_request(Operation.POLICY_READ, "reader-1"))
    repository.fail_events = False
    item = bundle()
    application.save_draft(namespace_request(Operation.POLICY_DRAFT, "author-1"), item)
    repository.fail_get = True
    with pytest.raises(RuleBundleUnavailable):
        application.approve(
            bundle_request(Operation.POLICY_APPROVE, item),
            identity(item),
            reason="Read failure must be unavailable.",
        )
    repository.fail_get = False
    application.approve(
        bundle_request(Operation.POLICY_APPROVE, item),
        identity(item),
        reason="Approve after storage recovery.",
    )
    application.activate(
        bundle_request(Operation.POLICY_ACTIVATE, item),
        identity(item),
        reason="Activate after storage recovery.",
    )
    request = screening_request()
    evaluator = RulePresenceEvaluationService(
        repository,
        deployment_id="demo",
        rule_set_id="ruleset-1",
        clock=lambda: NOW,
    )
    repository.fail_events = True
    with pytest.raises(RuleBundleUnavailable):
        evaluator.evaluate(screening_authorized(request), request)
    repository.fail_events = False
    repository.fail_get = True
    with pytest.raises(RuleBundleUnavailable):
        application.current_active(namespace_request(Operation.POLICY_READ, "reader-1"))
    with pytest.raises(RuleBundleUnavailable):
        evaluator.evaluate(screening_authorized(request), request)
    repository.fail_get = False
    repository.corrupt_remove_bundle_for_test(
        "tenant-1", "demo", "ruleset-1", "bundle-1", "1.0.0"
    )
    with pytest.raises(RuleBundleUnavailable):
        application.current_active(namespace_request(Operation.POLICY_READ, "reader-1"))


def test_command_audit_types_reject_inconsistent_outcomes_and_linkage() -> None:
    base = RuleBundleCommandAuditRecord(
        command_event_id="command-1",
        authorization_event_id="authorization-1",
        lifecycle_event_id=None,
        tenant_id="tenant-1",
        deployment_id="demo",
        rule_set_id="ruleset-1",
        bundle_id="bundle-1",
        bundle_version="1.0.0",
        bundle_content_hash=HASH_A,
        target_type="rule_bundle",
        target_id="target",
        actor_id="actor-1",
        actor_type=LifecycleActorType.HUMAN,
        operation="POLICY_APPROVE",
        outcome=RuleBundleCommandOutcome.FAILURE,
        reason=RuleBundleCommandReason.INVALID_LIFECYCLE,
        occurred_at=NOW,
    )
    assert base.outcome is RuleBundleCommandOutcome.FAILURE
    for changes in (
        {"bundle_content_hash": "not-a-hash"},
        {"outcome": cast(RuleBundleCommandOutcome, "FAILURE")},
        {"reason": cast(RuleBundleCommandReason, "INVALID_LIFECYCLE")},
        {"outcome": RuleBundleCommandOutcome.SUCCESS},
        {
            "outcome": RuleBundleCommandOutcome.SUCCESS,
            "reason": RuleBundleCommandReason.APPROVED,
        },
        {"actor_type": cast(LifecycleActorType, "HUMAN")},
        {"operation": "bad operation"},
        {"occurred_at": NOW.replace(tzinfo=None)},
    ):
        with pytest.raises(ValueError):
            replace(base, **changes)


def test_repository_rejects_duplicate_audit_id_without_partial_lifecycle() -> None:
    repository = InMemoryRuleBundleRepository()
    application = service(repository, ids=EventIds())
    item = bundle()
    save_approve_activate(application, item)
    existing_audit = audits(repository)[0]
    with pytest.raises(RuleBundlePersistenceError):
        repository.append_command_audit(existing_audit)
