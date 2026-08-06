"""Authorized rule-bundle governance, safe reads, and deterministic evaluation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Protocol
from uuid import uuid4

from pydantic import Field, model_validator

from tradesieve.application.auth import (
    ActorType,
    AuthorizedRequest,
    Operation,
)
from tradesieve.application.contracts import (
    CanonicalDatetime,
    ContentHash,
    ContractModel,
    HashedVersionReference,
    LongText,
    Reference,
    RuleEvaluationRecord,
    RuleFactPath,
    ScreeningRequest,
    SemanticVersion,
    ShortText,
)
from tradesieve.application.contracts import (
    RuleEvaluationOutcome as ContractRuleEvaluationOutcome,
)
from tradesieve.domain.rule_bundle import (
    CanonicalFactPath,
    CitationKind,
    DraftWriteOutcome,
    GovernanceBlockReason,
    GovernanceIssue,
    InternalPolicyCitation,
    InvalidLifecycleTransition,
    LifecycleActorType,
    LifecycleWriteOutcome,
    OfficialSourceProvisionCitation,
    RescreenImpact,
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
    RuleEvaluatorKind,
    RuleSetLifecycle,
    RuleVersion,
    activation_block_reasons,
    approval_block_reasons,
    calculate_rescreen_impact,
    canonical_sha256,
    evaluate_rule_presence,
    fold_rule_bundle_events,
    rule_bundle_ref,
)
from tradesieve.ports.rule_bundle import (
    RuleBundlePersistenceError,
    RuleBundleRepository,
)

MAX_RULE_BUNDLE_LISTING = 256
MAX_RULE_BUNDLE_HISTORY = 4096
RULE_SET_TARGET_TYPE = "rule_set"
RULE_BUNDLE_TARGET_TYPE = "rule_bundle"
SCREENING_REQUEST_TARGET_TYPE = "screening_request"


class OfficialCitationResolver(Protocol):
    """Verify a cited immutable official snapshot/provision through TS-202 state."""

    def verify(
        self,
        *,
        tenant_id: str,
        deployment_id: str,
        citation: OfficialSourceProvisionCitation,
    ) -> bool: ...


class UnavailableOfficialCitationResolver:
    """Conservative default until an official snapshot resolver is injected."""

    def verify(
        self,
        *,
        tenant_id: str,
        deployment_id: str,
        citation: OfficialSourceProvisionCitation,
    ) -> bool:
        del tenant_id, deployment_id, citation
        return False


class RuleBundleApplicationError(Exception):
    pass


class RuleBundleAuthorizationBindingDenied(RuleBundleApplicationError):
    def __init__(self) -> None:
        super().__init__("rule bundle authorization binding denied")


class RuleBundleNotFound(RuleBundleApplicationError):
    def __init__(self) -> None:
        super().__init__("rule bundle not found")


class RuleBundleConflict(RuleBundleApplicationError):
    def __init__(self) -> None:
        super().__init__("rule bundle command conflict")


class RuleBundleUnavailable(RuleBundleApplicationError):
    def __init__(self) -> None:
        super().__init__("rule bundle state unavailable")


class RuleBundleGovernanceBlocked(RuleBundleApplicationError):
    def __init__(self, issues: tuple[GovernanceIssue, ...]) -> None:
        super().__init__("rule bundle governance blocked")
        self.issues = issues


class ImmutableRuleBundleIdentity(ContractModel):
    bundle_id: Reference
    version: SemanticVersion
    content_hash: ContentHash


class SafeCitationRecord(ContractModel):
    citation_ref: Reference
    kind: CitationKind
    provision_locator: ShortText
    policy_id: Reference | None = None
    policy_version: SemanticVersion | None = None
    policy_content_hash: ContentHash | None = None
    source_id: Reference | None = None
    snapshot_id: Reference | None = None
    snapshot_content_hash: ContentHash | None = None

    @model_validator(mode="after")
    def require_kind_specific_identity(self) -> SafeCitationRecord:
        internal = (self.policy_id, self.policy_version, self.policy_content_hash)
        official = (self.source_id, self.snapshot_id, self.snapshot_content_hash)
        if self.kind is CitationKind.INTERNAL_POLICY:
            if any(item is None for item in internal) or any(
                item is not None for item in official
            ):
                raise ValueError("internal citation has an invalid public shape")
        elif any(item is None for item in official) or any(
            item is not None for item in internal
        ):
            raise ValueError("official citation has an invalid public shape")
        return self


class SafeRuleRecord(ContractModel):
    rule_id: Reference
    version: SemanticVersion
    content_hash: ContentHash
    kind: RuleEvaluatorKind
    owner: ShortText | None
    effective_from: CanonicalDatetime
    effective_until: CanonicalDatetime | None
    proposed_actions: Annotated[list[RuleAction], Field(min_length=1, max_length=6)]
    activities: Annotated[list[RuleActivity], Field(max_length=11)]
    citations: Annotated[list[SafeCitationRecord], Field(max_length=16)]
    fixture_count: Annotated[int, Field(ge=0, le=64)]

    @model_validator(mode="after")
    def require_canonical_order(self) -> SafeRuleRecord:
        for values in (self.proposed_actions, self.activities):
            if values != sorted(set(values), key=str):
                raise ValueError("rule scope values must be sorted and unique")
        if [item.citation_ref for item in self.citations] != sorted(
            {item.citation_ref for item in self.citations}
        ):
            raise ValueError("citations must be sorted and unique")
        return self


class RuleBundleSummary(ContractModel):
    tenant_id: Reference
    deployment_id: Reference
    rule_set_id: Reference
    bundle_id: Reference
    version: SemanticVersion
    content_hash: ContentHash
    owner: ShortText | None
    authored_by: Reference
    effective_from: CanonicalDatetime
    effective_until: CanonicalDatetime | None
    state: RuleBundleState
    active: bool
    rule_count: Annotated[int, Field(ge=0, le=256)]


class RuleBundleView(ContractModel):
    summary: RuleBundleSummary
    rules: Annotated[list[SafeRuleRecord], Field(max_length=256)]


class RuleBundleListing(ContractModel):
    bundles: Annotated[
        list[RuleBundleSummary], Field(max_length=MAX_RULE_BUNDLE_LISTING)
    ]


class RuleBundleHistoryRecord(ContractModel):
    sequence: Annotated[int, Field(ge=1, le=9_223_372_036_854_775_807)]
    event_id: Reference
    event_type: RuleBundleEventType
    previous_bundle: HashedVersionReference | None
    new_bundle: HashedVersionReference | None
    actor_id: Reference
    actor_type: LifecycleActorType
    reason: LongText
    occurred_at: CanonicalDatetime
    rescreen_impact: RescreenImpact | None


class RuleBundleHistory(ContractModel):
    events: Annotated[list[RuleBundleHistoryRecord], Field(max_length=256)]


def _hashed_target(prefix: str, payload: object) -> str:
    return f"{prefix}-{canonical_sha256(payload).removeprefix('sha256:')}"


def rule_set_authorization_target_id(deployment_id: str, rule_set_id: str) -> str:
    """Canonical namespace target; raw bounded components are never concatenated."""

    return _hashed_target(
        "ruleset",
        {"deployment_id": deployment_id, "rule_set_id": rule_set_id},
    )


def rule_bundle_authorization_target_id(
    deployment_id: str,
    rule_set_id: str,
    bundle_id: str,
    version: str,
    content_hash: str,
) -> str:
    """Canonical immutable-version target including the exact content hash."""

    return _hashed_target(
        "bundle",
        {
            "bundle_id": bundle_id,
            "content_hash": content_hash,
            "deployment_id": deployment_id,
            "rule_set_id": rule_set_id,
            "version": version,
        },
    )


def screening_request_authorization_target_id(
    request: ScreeningRequest,
    deployment_id: str,
    rule_set_id: str,
) -> str:
    """Bind screening authorization to the request and evaluator rule namespace."""

    return _hashed_target(
        "screening",
        {
            "deployment_id": deployment_id,
            "external_object": request.external_object.model_dump(mode="json"),
            "input_hash": request.canonical_input_hash(),
            "rule_set_id": rule_set_id,
            "tenant_id": request.tenant_id,
        },
    )


class RuleBundleService:
    """Govern immutable bundles through exact authorized targets and atomic writes."""

    def __init__(
        self,
        repository: RuleBundleRepository,
        *,
        deployment_id: str,
        rule_set_id: str,
        official_citation_resolver: OfficialCitationResolver | None = None,
        clock: Callable[[], datetime] | None = None,
        event_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._repository = repository
        self._deployment_id = deployment_id
        self._rule_set_id = rule_set_id
        self._official_resolver = (
            official_citation_resolver or UnavailableOfficialCitationResolver()
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._event_id_factory = event_id_factory or (
            lambda: f"rule-event-{uuid4().hex}"
        )

    def save_draft(
        self,
        authorized: AuthorizedRequest,
        bundle: RuleBundleVersion,
    ) -> DraftWriteOutcome:
        now = self._now()
        reference = rule_bundle_ref(bundle)
        try:
            self._require_authorized(
                authorized,
                Operation.POLICY_DRAFT,
                RULE_SET_TARGET_TYPE,
                rule_set_authorization_target_id(
                    self._deployment_id, self._rule_set_id
                ),
            )
        except RuleBundleAuthorizationBindingDenied:
            self._record_failure(
                authorized,
                reference,
                RuleBundleCommandReason.AUTHORIZATION_BINDING_DENIED,
                now,
            )
            raise
        actor_id = authorized.context.actor.subject
        if (
            bundle.tenant_id != authorized.context.tenant_id
            or bundle.deployment_id != self._deployment_id
            or bundle.rule_set_id != self._rule_set_id
            or bundle.authored_by != actor_id
            or authorized.context.actor.actor_type
            not in {ActorType.HUMAN, ActorType.SERVICE}
        ):
            self._record_failure(
                authorized,
                reference,
                RuleBundleCommandReason.AUTHORIZATION_BINDING_DENIED,
                now,
            )
            raise RuleBundleAuthorizationBindingDenied
        lifecycle_event_id = self._new_event_id()
        drafted = RuleBundleLifecycleEvent(
            sequence=self._next_sequence(bundle.tenant_id),
            event_id=lifecycle_event_id,
            tenant_id=bundle.tenant_id,
            deployment_id=self._deployment_id,
            rule_set_id=self._rule_set_id,
            event_type=RuleBundleEventType.DRAFTED,
            previous_bundle=None,
            new_bundle=reference,
            reason="Immutable rule bundle draft saved.",
            actor_id=actor_id,
            actor_type=self._lifecycle_actor(authorized),
            occurred_at=now,
        )
        applied = self._audit(
            authorized,
            reference,
            RuleBundleCommandOutcome.SUCCESS,
            RuleBundleCommandReason.DRAFT_APPLIED,
            now,
            lifecycle_event_id=lifecycle_event_id,
        )
        idempotent = self._audit(
            authorized,
            reference,
            RuleBundleCommandOutcome.SUCCESS,
            RuleBundleCommandReason.DRAFT_IDEMPOTENT,
            now,
        )
        try:
            outcome = self._repository.save_draft_atomic(
                bundle, drafted, applied, idempotent
            )
        except RuleBundlePersistenceError as exc:
            raise RuleBundleUnavailable from exc
        if outcome is DraftWriteOutcome.CONFLICT:
            self._record_failure(
                authorized,
                reference,
                RuleBundleCommandReason.DRAFT_CONFLICT,
                now,
            )
            raise RuleBundleConflict
        return outcome

    def approve(
        self,
        authorized: AuthorizedRequest,
        identity: ImmutableRuleBundleIdentity,
        *,
        reason: str,
    ) -> LifecycleWriteOutcome:
        now = self._now()
        bundle = self._authorized_bundle(
            authorized,
            Operation.POLICY_APPROVE,
            identity.bundle_id,
            identity.version,
            identity.content_hash,
            now,
        )
        self._require_distinct_author(authorized, bundle, now)
        self._require_governance(authorized, bundle, now, activation=False)
        return self._write_lifecycle(
            authorized,
            bundle,
            RuleBundleEventType.APPROVED,
            previous=None,
            new=rule_bundle_ref(bundle),
            impact=None,
            reason=reason,
            now=now,
        )

    def activate(
        self,
        authorized: AuthorizedRequest,
        identity: ImmutableRuleBundleIdentity,
        *,
        reason: str,
    ) -> LifecycleWriteOutcome:
        now = self._now()
        bundle = self._authorized_bundle(
            authorized,
            Operation.POLICY_ACTIVATE,
            identity.bundle_id,
            identity.version,
            identity.content_hash,
            now,
        )
        self._require_distinct_author(authorized, bundle, now)
        self._require_governance(authorized, bundle, now, activation=True)
        events = self._events(bundle.tenant_id)
        lifecycle = fold_rule_bundle_events(events)
        target_ref = rule_bundle_ref(bundle)
        previous = lifecycle.active_bundle
        responsible = None
        if previous == target_ref:
            responsible = next(
                (
                    event
                    for event in reversed(events)
                    if event.event_type
                    in {
                        RuleBundleEventType.ACTIVATED,
                        RuleBundleEventType.ROLLED_BACK,
                    }
                    and event.new_bundle == target_ref
                ),
                None,
            )
        if (
            responsible is not None
            and responsible.event_type is RuleBundleEventType.ACTIVATED
        ):
            previous = responsible.previous_bundle
            impact = responsible.rescreen_impact
            assert impact is not None
        else:
            previous_bundle = self._bundle_for_ref(bundle.tenant_id, previous)
            impact = calculate_rescreen_impact(previous_bundle, bundle)
        return self._write_lifecycle(
            authorized,
            bundle,
            RuleBundleEventType.ACTIVATED,
            previous=previous,
            new=target_ref,
            impact=impact,
            reason=reason,
            now=now,
        )

    def retire(
        self,
        authorized: AuthorizedRequest,
        identity: ImmutableRuleBundleIdentity,
        *,
        reason: str,
    ) -> LifecycleWriteOutcome:
        now = self._now()
        bundle = self._authorized_bundle(
            authorized,
            Operation.POLICY_RETIRE,
            identity.bundle_id,
            identity.version,
            identity.content_hash,
            now,
        )
        self._require_distinct_author(authorized, bundle, now)
        reference = rule_bundle_ref(bundle)
        lifecycle = fold_rule_bundle_events(self._events(bundle.tenant_id))
        if lifecycle.active_bundle not in {reference, None}:
            self._record_failure(
                authorized,
                reference,
                RuleBundleCommandReason.INVALID_LIFECYCLE,
                now,
            )
            raise RuleBundleConflict
        return self._write_lifecycle(
            authorized,
            bundle,
            RuleBundleEventType.RETIRED,
            previous=reference,
            new=None,
            impact=calculate_rescreen_impact(bundle, None),
            reason=reason,
            now=now,
        )

    def rollback(
        self,
        authorized: AuthorizedRequest,
        identity: ImmutableRuleBundleIdentity,
        *,
        reason: str,
    ) -> LifecycleWriteOutcome:
        now = self._now()
        target = self._authorized_bundle(
            authorized,
            Operation.POLICY_ROLLBACK,
            identity.bundle_id,
            identity.version,
            identity.content_hash,
            now,
        )
        self._require_distinct_author(authorized, target, now)
        self._require_governance(authorized, target, now, activation=True)
        events = self._events(target.tenant_id)
        lifecycle = fold_rule_bundle_events(events)
        target_ref = rule_bundle_ref(target)
        previous = lifecycle.active_bundle
        if previous == target_ref:
            previous = next(
                (
                    event.previous_bundle
                    for event in reversed(events)
                    if event.event_type is RuleBundleEventType.ROLLED_BACK
                    and event.new_bundle == target_ref
                ),
                None,
            )
        if previous is None:
            self._record_failure(
                authorized,
                target_ref,
                RuleBundleCommandReason.INVALID_LIFECYCLE,
                now,
            )
            raise RuleBundleConflict
        previous_bundle = self._bundle_for_ref(target.tenant_id, previous)
        assert previous_bundle is not None
        return self._write_lifecycle(
            authorized,
            target,
            RuleBundleEventType.ROLLED_BACK,
            previous=previous,
            new=target_ref,
            impact=calculate_rescreen_impact(previous_bundle, target),
            reason=reason,
            now=now,
        )

    def get_bundle(
        self,
        authorized: AuthorizedRequest,
        identity: ImmutableRuleBundleIdentity,
    ) -> RuleBundleView:
        now = self._now()
        bundle = self._authorized_bundle(
            authorized,
            Operation.POLICY_READ,
            identity.bundle_id,
            identity.version,
            identity.content_hash,
            now,
            audit_failure=False,
        )
        return self._to_view(bundle, self._lifecycle(bundle.tenant_id))

    def list_bundles(
        self, authorized: AuthorizedRequest, *, limit: int = MAX_RULE_BUNDLE_LISTING
    ) -> RuleBundleListing:
        self._require_query_namespace(authorized)
        self._require_query_limit(limit)
        tenant_id = authorized.context.tenant_id
        try:
            bundles = self._repository.list_bundles(
                tenant_id,
                self._deployment_id,
                self._rule_set_id,
                limit=MAX_RULE_BUNDLE_LISTING + 1,
            )
        except RuleBundlePersistenceError as exc:
            raise RuleBundleUnavailable from exc
        if len(bundles) > MAX_RULE_BUNDLE_LISTING:
            raise RuleBundleUnavailable
        lifecycle = self._lifecycle(tenant_id)
        return RuleBundleListing(
            bundles=[self._to_summary(item, lifecycle) for item in bundles[:limit]]
        )

    def current_active(self, authorized: AuthorizedRequest) -> RuleBundleView | None:
        self._require_query_namespace(authorized)
        tenant_id = authorized.context.tenant_id
        lifecycle = self._lifecycle(tenant_id)
        bundle = self._bundle_for_ref(tenant_id, lifecycle.active_bundle)
        return self._to_view(bundle, lifecycle) if bundle is not None else None

    def history(
        self, authorized: AuthorizedRequest, *, limit: int = 256
    ) -> RuleBundleHistory:
        self._require_query_namespace(authorized)
        self._require_query_limit(limit)
        events = self._events(authorized.context.tenant_id)
        return RuleBundleHistory(
            events=[self._to_history(item) for item in events[-limit:]]
        )

    def _write_lifecycle(
        self,
        authorized: AuthorizedRequest,
        bundle: RuleBundleVersion,
        event_type: RuleBundleEventType,
        *,
        previous: RuleBundleRef | None,
        new: RuleBundleRef | None,
        impact: RescreenImpact | None,
        reason: str,
        now: datetime,
    ) -> LifecycleWriteOutcome:
        bundle_reference = rule_bundle_ref(bundle)
        lifecycle_event_id = self._new_event_id()
        try:
            event = RuleBundleLifecycleEvent(
                sequence=self._next_sequence(bundle.tenant_id),
                event_id=lifecycle_event_id,
                tenant_id=bundle.tenant_id,
                deployment_id=self._deployment_id,
                rule_set_id=self._rule_set_id,
                event_type=event_type,
                previous_bundle=previous,
                new_bundle=new,
                reason=reason,
                actor_id=authorized.context.actor.subject,
                actor_type=self._lifecycle_actor(authorized),
                occurred_at=now,
                rescreen_impact=impact,
            )
        except ValueError as exc:
            self._record_failure(
                authorized,
                bundle_reference,
                RuleBundleCommandReason.INVALID_LIFECYCLE,
                now,
            )
            raise RuleBundleConflict from exc
        applied_reason, idempotent_reason = self._lifecycle_reasons(event_type)
        reference = new or previous
        assert reference is not None
        applied = self._audit(
            authorized,
            reference,
            RuleBundleCommandOutcome.SUCCESS,
            applied_reason,
            now,
            lifecycle_event_id=lifecycle_event_id,
        )
        idempotent = self._audit(
            authorized,
            reference,
            RuleBundleCommandOutcome.SUCCESS,
            idempotent_reason,
            now,
        )
        try:
            outcome = self._repository.append_lifecycle_atomic(
                event, applied, idempotent
            )
        except RuleBundlePersistenceError as exc:
            raise RuleBundleUnavailable from exc
        if outcome is LifecycleWriteOutcome.CONFLICT:
            self._record_failure(
                authorized,
                bundle_reference,
                RuleBundleCommandReason.INVALID_LIFECYCLE,
                now,
            )
            raise RuleBundleConflict
        return outcome

    def _authorized_bundle(
        self,
        authorized: AuthorizedRequest,
        operation: Operation,
        bundle_id: str,
        version: str,
        content_hash: str,
        now: datetime,
        *,
        audit_failure: bool = True,
    ) -> RuleBundleVersion:
        attempted_reference = RuleBundleRef(bundle_id, version, content_hash)
        expected_target = rule_bundle_authorization_target_id(
            self._deployment_id,
            self._rule_set_id,
            bundle_id,
            version,
            content_hash,
        )
        try:
            self._require_authorized(
                authorized,
                operation,
                RULE_BUNDLE_TARGET_TYPE,
                expected_target,
            )
        except RuleBundleAuthorizationBindingDenied:
            if audit_failure:
                self._record_failure(
                    authorized,
                    attempted_reference,
                    RuleBundleCommandReason.AUTHORIZATION_BINDING_DENIED,
                    now,
                )
            raise
        try:
            bundle = self._repository.get_bundle(
                authorized.context.tenant_id,
                self._deployment_id,
                self._rule_set_id,
                bundle_id,
                version,
            )
        except RuleBundlePersistenceError as exc:
            raise RuleBundleUnavailable from exc
        if bundle is None or bundle.content_hash() != content_hash:
            if audit_failure:
                self._record_failure(
                    authorized,
                    attempted_reference,
                    RuleBundleCommandReason.NOT_FOUND,
                    now,
                )
            raise RuleBundleNotFound
        return bundle

    def _require_governance(
        self,
        authorized: AuthorizedRequest,
        bundle: RuleBundleVersion,
        now: datetime,
        *,
        activation: bool,
    ) -> None:
        issues = list(
            activation_block_reasons(bundle, now=now)
            if activation
            else approval_block_reasons(bundle)
        )
        official_rule_ids: set[str] = set()
        for rule in bundle.rules:
            for citation in rule.citations:
                if not isinstance(citation, OfficialSourceProvisionCitation):
                    continue
                try:
                    verified = self._official_resolver.verify(
                        tenant_id=bundle.tenant_id,
                        deployment_id=bundle.deployment_id,
                        citation=citation,
                    )
                except Exception:
                    verified = False
                if verified is not True:
                    official_rule_ids.add(rule.rule_id)
        issues.extend(
            GovernanceIssue(
                GovernanceBlockReason.OFFICIAL_CITATION_UNVERIFIED,
                rule_id,
            )
            for rule_id in sorted(official_rule_ids)
        )
        if not issues:
            return
        ordered = tuple(
            sorted(
                set(issues),
                key=lambda item: (
                    item.reason.value,
                    item.rule_id or "",
                    item.failing_fixture_ids,
                ),
            )
        )
        reason = (
            RuleBundleCommandReason.OFFICIAL_CITATION_UNVERIFIED
            if official_rule_ids
            else RuleBundleCommandReason.GOVERNANCE_BLOCKED
        )
        self._record_failure(
            authorized,
            rule_bundle_ref(bundle),
            reason,
            now,
        )
        raise RuleBundleGovernanceBlocked(ordered)

    def _require_distinct_author(
        self,
        authorized: AuthorizedRequest,
        bundle: RuleBundleVersion,
        now: datetime,
    ) -> None:
        if (
            authorized.context.actor.actor_type is not ActorType.HUMAN
            or bundle.authored_by == authorized.context.actor.subject
        ):
            self._record_failure(
                authorized,
                rule_bundle_ref(bundle),
                RuleBundleCommandReason.AUTHOR_SEPARATION_DENIED,
                now,
            )
            raise RuleBundleAuthorizationBindingDenied

    def _record_failure(
        self,
        authorized: AuthorizedRequest,
        reference: RuleBundleRef,
        reason: RuleBundleCommandReason,
        now: datetime,
    ) -> None:
        audit = self._audit(
            authorized,
            reference,
            RuleBundleCommandOutcome.FAILURE,
            reason,
            now,
        )
        try:
            self._repository.append_command_audit(audit)
        except RuleBundlePersistenceError as exc:
            raise RuleBundleUnavailable from exc

    def _audit(
        self,
        authorized: AuthorizedRequest,
        reference: RuleBundleRef,
        outcome: RuleBundleCommandOutcome,
        reason: RuleBundleCommandReason,
        now: datetime,
        *,
        lifecycle_event_id: str | None = None,
    ) -> RuleBundleCommandAuditRecord:
        return RuleBundleCommandAuditRecord(
            command_event_id=self._new_event_id(),
            authorization_event_id=authorized.audit_event_id,
            lifecycle_event_id=lifecycle_event_id,
            tenant_id=authorized.context.tenant_id,
            deployment_id=self._deployment_id,
            rule_set_id=self._rule_set_id,
            bundle_id=reference.bundle_id,
            bundle_version=reference.version,
            bundle_content_hash=reference.content_hash,
            target_type=authorized.target.object_type,
            target_id=authorized.target.object_id,
            actor_id=authorized.context.actor.subject,
            actor_type=self._lifecycle_actor(authorized),
            operation=str(authorized.operation),
            outcome=outcome,
            reason=reason,
            occurred_at=now,
        )

    def _require_query_namespace(self, authorized: AuthorizedRequest) -> None:
        self._require_authorized(
            authorized,
            Operation.POLICY_READ,
            RULE_SET_TARGET_TYPE,
            rule_set_authorization_target_id(self._deployment_id, self._rule_set_id),
        )

    @staticmethod
    def _require_authorized(
        authorized: AuthorizedRequest,
        operation: Operation,
        target_type: str,
        target_id: str,
    ) -> None:
        if not isinstance(authorized, AuthorizedRequest):
            raise RuleBundleAuthorizationBindingDenied
        actor = authorized.context.actor
        if (
            authorized.operation is not operation
            or authorized.context.tenant_id != actor.tenant_id
            or authorized.target.tenant_id != actor.tenant_id
            or authorized.target.object_type != target_type
            or authorized.target.object_id != target_id
        ):
            raise RuleBundleAuthorizationBindingDenied

    def _events(self, tenant_id: str) -> tuple[RuleBundleLifecycleEvent, ...]:
        return self._snapshot(tenant_id)[0]

    def _snapshot(
        self, tenant_id: str
    ) -> tuple[tuple[RuleBundleLifecycleEvent, ...], RuleSetLifecycle]:
        try:
            events, lifecycle = self._repository.get_lifecycle_snapshot(
                tenant_id,
                self._deployment_id,
                self._rule_set_id,
            )
        except RuleBundlePersistenceError as exc:
            raise RuleBundleUnavailable from exc
        if len(events) > MAX_RULE_BUNDLE_HISTORY:
            raise RuleBundleUnavailable
        return events, lifecycle

    def _lifecycle(self, tenant_id: str) -> RuleSetLifecycle:
        return self._snapshot(tenant_id)[1]

    def _next_sequence(self, tenant_id: str) -> int:
        events = self._events(tenant_id)
        return events[-1].sequence + 1 if events else 1

    def _bundle_for_ref(
        self, tenant_id: str, reference: RuleBundleRef | None
    ) -> RuleBundleVersion | None:
        if reference is None:
            return None
        try:
            bundle = self._repository.get_bundle(
                tenant_id,
                self._deployment_id,
                self._rule_set_id,
                reference.bundle_id,
                reference.version,
            )
        except RuleBundlePersistenceError as exc:
            raise RuleBundleUnavailable from exc
        if bundle is None or rule_bundle_ref(bundle) != reference:
            raise RuleBundleUnavailable
        return bundle

    def _to_view(
        self, bundle: RuleBundleVersion, lifecycle: RuleSetLifecycle
    ) -> RuleBundleView:
        return RuleBundleView(
            summary=self._to_summary(bundle, lifecycle),
            rules=[self._to_rule(item) for item in bundle.rules],
        )

    @staticmethod
    def _to_summary(
        bundle: RuleBundleVersion, lifecycle: RuleSetLifecycle
    ) -> RuleBundleSummary:
        reference = rule_bundle_ref(bundle)
        state = lifecycle.state_for(reference)
        if state is None:
            raise RuleBundleUnavailable
        return RuleBundleSummary(
            tenant_id=bundle.tenant_id,
            deployment_id=bundle.deployment_id,
            rule_set_id=bundle.rule_set_id,
            bundle_id=bundle.bundle_id,
            version=bundle.version,
            content_hash=reference.content_hash,
            owner=bundle.owner,
            authored_by=bundle.authored_by,
            effective_from=bundle.effective_window.effective_from,
            effective_until=bundle.effective_window.effective_until,
            state=state,
            active=lifecycle.active_bundle == reference,
            rule_count=len(bundle.rules),
        )

    @staticmethod
    def _to_rule(rule: RuleVersion) -> SafeRuleRecord:
        citations = []
        for citation in rule.citations:
            if isinstance(citation, InternalPolicyCitation):
                citations.append(
                    SafeCitationRecord(
                        citation_ref=citation.citation_ref,
                        kind=citation.kind,
                        provision_locator=citation.provision_locator,
                        policy_id=citation.policy_id,
                        policy_version=citation.policy_version,
                        policy_content_hash=citation.policy_content_hash(),
                    )
                )
            else:
                citations.append(
                    SafeCitationRecord(
                        citation_ref=citation.citation_ref,
                        kind=citation.kind,
                        provision_locator=citation.provision_locator,
                        source_id=citation.source_id,
                        snapshot_id=citation.snapshot_id,
                        snapshot_content_hash=citation.snapshot_content_hash,
                    )
                )
        return SafeRuleRecord(
            rule_id=rule.rule_id,
            version=rule.version,
            content_hash=rule.content_hash(),
            kind=rule.kind,
            owner=rule.owner,
            effective_from=rule.effective_window.effective_from,
            effective_until=rule.effective_window.effective_until,
            proposed_actions=list(rule.scope.proposed_actions),
            activities=list(rule.scope.activities),
            citations=citations,
            fixture_count=len(rule.fixtures),
        )

    @staticmethod
    def _to_history(event: RuleBundleLifecycleEvent) -> RuleBundleHistoryRecord:
        def reference(value: RuleBundleRef | None) -> HashedVersionReference | None:
            if value is None:
                return None
            return HashedVersionReference(
                resource_id=value.bundle_id,
                version=value.version,
                content_hash=value.content_hash,
            )

        return RuleBundleHistoryRecord(
            sequence=event.sequence,
            event_id=event.event_id,
            event_type=event.event_type,
            previous_bundle=reference(event.previous_bundle),
            new_bundle=reference(event.new_bundle),
            actor_id=event.actor_id,
            actor_type=event.actor_type,
            reason=event.reason,
            occurred_at=event.occurred_at,
            rescreen_impact=event.rescreen_impact,
        )

    def _now(self) -> datetime:
        now = self._clock()
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise ValueError("rule bundle clock must return a timezone-aware datetime")
        return now.astimezone(UTC)

    def _new_event_id(self) -> str:
        value = self._event_id_factory()
        if not isinstance(value, str):
            raise ValueError("event ID provider must return a string")
        return value

    @staticmethod
    def _lifecycle_actor(authorized: AuthorizedRequest) -> LifecycleActorType:
        return LifecycleActorType(authorized.context.actor.actor_type.value)

    @staticmethod
    def _lifecycle_reasons(
        event_type: RuleBundleEventType,
    ) -> tuple[RuleBundleCommandReason, RuleBundleCommandReason]:
        return {
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
        }[event_type]

    @staticmethod
    def _require_query_limit(limit: int) -> None:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= MAX_RULE_BUNDLE_LISTING
        ):
            raise ValueError(f"limit must be between 1 and {MAX_RULE_BUNDLE_LISTING}")


class RuleBundleReadinessService:
    """Fail closed unless one validated, effective, governed bundle is active."""

    def __init__(
        self,
        repository: RuleBundleRepository,
        *,
        deployment_id: str,
        rule_set_id: str,
        official_citation_resolver: OfficialCitationResolver | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._deployment_id = deployment_id
        self._rule_set_id = rule_set_id
        self._official_resolver = (
            official_citation_resolver or UnavailableOfficialCitationResolver()
        )
        self._clock = clock or (lambda: datetime.now(UTC))

    def is_current_active_ready(self, tenant_id: str) -> bool:
        now = self._now()
        try:
            events, lifecycle = self._repository.get_lifecycle_snapshot(
                tenant_id,
                self._deployment_id,
                self._rule_set_id,
            )
            reference = lifecycle.active_bundle
            if (
                len(events) > MAX_RULE_BUNDLE_HISTORY
                or reference is None
                or lifecycle.state_for(reference) is not RuleBundleState.ACTIVE
            ):
                return False
            bundle = self._repository.get_bundle(
                tenant_id,
                self._deployment_id,
                self._rule_set_id,
                reference.bundle_id,
                reference.version,
            )
        except (RuleBundlePersistenceError, InvalidLifecycleTransition, ValueError):
            return False
        if (
            bundle is None
            or rule_bundle_ref(bundle) != reference
            or activation_block_reasons(bundle, now=now)
        ):
            return False
        for rule in bundle.rules:
            for citation in rule.citations:
                if not isinstance(citation, OfficialSourceProvisionCitation):
                    continue
                try:
                    verified = self._official_resolver.verify(
                        tenant_id=bundle.tenant_id,
                        deployment_id=bundle.deployment_id,
                        citation=citation,
                    )
                except Exception:
                    return False
                if verified is not True:
                    return False
        return True

    def _now(self) -> datetime:
        now = self._clock()
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise ValueError(
                "rule readiness clock must return a timezone-aware datetime"
            )
        return now.astimezone(UTC)


class RulePresenceEvaluationService:
    """Evaluate the exact active, effective bundle without producing clearance."""

    def __init__(
        self,
        repository: RuleBundleRepository,
        *,
        deployment_id: str,
        rule_set_id: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._deployment_id = deployment_id
        self._rule_set_id = rule_set_id
        self._clock = clock or (lambda: datetime.now(UTC))

    def evaluate(
        self,
        authorized: AuthorizedRequest,
        request: ScreeningRequest,
    ) -> list[RuleEvaluationRecord]:
        RuleBundleService._require_authorized(
            authorized,
            Operation.SCREENING_SUBMIT,
            SCREENING_REQUEST_TARGET_TYPE,
            screening_request_authorization_target_id(
                request,
                self._deployment_id,
                self._rule_set_id,
            ),
        )
        if request.tenant_id != authorized.context.tenant_id:
            raise RuleBundleAuthorizationBindingDenied
        now = self._now()
        try:
            events, lifecycle = self._repository.get_lifecycle_snapshot(
                request.tenant_id,
                self._deployment_id,
                self._rule_set_id,
            )
        except RuleBundlePersistenceError as exc:
            raise RuleBundleUnavailable from exc
        if len(events) > MAX_RULE_BUNDLE_HISTORY:
            raise RuleBundleUnavailable
        reference = lifecycle.active_bundle
        if (
            reference is None
            or lifecycle.state_for(reference) is not RuleBundleState.ACTIVE
        ):
            raise RuleBundleUnavailable
        try:
            bundle = self._repository.get_bundle(
                request.tenant_id,
                self._deployment_id,
                self._rule_set_id,
                reference.bundle_id,
                reference.version,
            )
        except RuleBundlePersistenceError as exc:
            raise RuleBundleUnavailable from exc
        if bundle is None or rule_bundle_ref(bundle) != reference:
            raise RuleBundleUnavailable
        if not bundle.effective_window.contains(now) or any(
            not rule.effective_window.contains(now) for rule in bundle.rules
        ):
            raise RuleBundleUnavailable

        action = RuleAction(request.proposed_action.value)
        activities = tuple(
            sorted({RuleActivity(item.value) for item in request.activities}, key=str)
        )
        present_paths = {RuleFactPath.PROPOSED_ACTION}
        if activities:
            present_paths.add(RuleFactPath.ACTIVITIES)
        for populated, path in (
            (bool(request.legal_nexus), RuleFactPath.LEGAL_NEXUS),
            (bool(request.parties), RuleFactPath.PARTIES),
            (bool(request.goods), RuleFactPath.GOODS),
            (request.route is not None, RuleFactPath.ROUTE),
            (request.payment is not None, RuleFactPath.PAYMENT),
        ):
            if populated:
                present_paths.add(path)
        domain_paths = tuple(
            sorted(
                (CanonicalFactPath(item.value) for item in present_paths),
                key=str,
            )
        )
        request_hash = request.canonical_input_hash()
        bundle_identity = HashedVersionReference(
            resource_id=reference.bundle_id,
            version=reference.version,
            content_hash=reference.content_hash,
        )
        records: list[RuleEvaluationRecord] = []
        for rule in sorted(
            bundle.rules,
            key=lambda item: (item.rule_id, item.version, item.content_hash()),
        ):
            evaluation = evaluate_rule_presence(
                rule,
                proposed_action=action,
                activities=activities,
                present_fact_paths=domain_paths,
            )
            missing = [
                RuleFactPath(item.value) for item in evaluation.missing_fact_paths
            ]
            rule_identity = HashedVersionReference(
                resource_id=rule.rule_id,
                version=rule.version,
                content_hash=rule.content_hash(),
            )
            outcome = ContractRuleEvaluationOutcome(evaluation.outcome.value)
            evaluation_id = _hashed_target(
                "eval",
                {
                    "bundle": bundle_identity.model_dump(mode="json"),
                    "input_hash": request_hash,
                    "missing_fact_paths": [item.value for item in missing],
                    "outcome": outcome.value,
                    "rule": rule_identity.model_dump(mode="json"),
                },
            )
            records.append(
                RuleEvaluationRecord(
                    evaluation_id=evaluation_id,
                    bundle=bundle_identity,
                    rule=rule_identity,
                    outcome=outcome,
                    missing_fact_paths=missing,
                )
            )
        return records

    def _now(self) -> datetime:
        now = self._clock()
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise ValueError(
                "rule evaluation clock must return a timezone-aware datetime"
            )
        return now.astimezone(UTC)
