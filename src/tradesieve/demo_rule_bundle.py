"""Explicitly demo-only synthetic rule bundle and trusted local entitlements."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum

from tradesieve.application.auth import (
    ActorContext,
    ActorType,
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
from tradesieve.application.rule_bundle import (
    RULE_BUNDLE_TARGET_TYPE,
    RULE_SET_TARGET_TYPE,
    ImmutableRuleBundleIdentity,
    rule_bundle_authorization_target_id,
    rule_set_authorization_target_id,
)
from tradesieve.config import Settings
from tradesieve.domain.rule_bundle import (
    CanonicalFactPath,
    DataCompletenessPresenceSpec,
    EffectiveWindow,
    InternalPolicyCitation,
    LegalNexusPresenceSpec,
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

DEMO_BUNDLE_ID = "synthetic-demo-bundle-v1"
DEMO_BUNDLE_VERSION = "1.0.0"
DEMO_POLICY_AUTHOR = "demo-policy-author"
DEMO_POLICY_APPROVER = "demo-policy-approver"
DEMO_POLICY_OPERATOR = "demo-policy-operator"
DEMO_APPROVAL_REASON = (
    "Synthetic presence-rule fixtures reviewed for demo decision support only."
)
DEMO_ACTIVATION_REASON = (
    "Synthetic presence-rule bundle activated for demo decision support only."
)
DEMO_EFFECTIVE_FROM = datetime(2026, 1, 1, tzinfo=UTC)


class DemoRuleActor(StrEnum):
    AUTHOR = "AUTHOR"
    APPROVER = "APPROVER"
    OPERATOR = "OPERATOR"


def synthetic_demo_rule_bundle(settings: Settings) -> RuleBundleVersion:
    """Return one stable synthetic bundle; no rule expresses legal clearance."""

    _require_demo(settings)
    window = EffectiveWindow(DEMO_EFFECTIVE_FROM, None)
    completeness = RuleVersion(
        rule_id="synthetic-data-completeness",
        version=DEMO_BUNDLE_VERSION,
        kind=RuleEvaluatorKind.DATA_COMPLETENESS_PRESENCE,
        owner="TradeSieve synthetic policy team",
        effective_window=window,
        scope=RuleScope(
            (RuleAction.ORDER_ACCEPTANCE,),
            (RuleActivity.SALE,),
        ),
        evaluator=DataCompletenessPresenceSpec(
            (
                CanonicalFactPath.GOODS,
                CanonicalFactPath.PARTIES,
                CanonicalFactPath.ROUTE,
            )
        ),
        citations=(
            InternalPolicyCitation(
                citation_ref="synthetic-completeness-policy",
                policy_id="synthetic-demo-policy",
                policy_version=DEMO_BUNDLE_VERSION,
                provision_locator="demo-section-completeness",
                private_policy_text=(
                    "Synthetic fixture text: record whether goods, parties, and "
                    "route facts are present. This is not legal advice or clearance."
                ),
            ),
        ),
        fixtures=(
            RuleFixture(
                fixture_id="completeness-facts-present",
                proposed_action=RuleAction.ORDER_ACCEPTANCE,
                activities=(RuleActivity.SALE,),
                present_fact_paths=(
                    CanonicalFactPath.GOODS,
                    CanonicalFactPath.PARTIES,
                    CanonicalFactPath.ROUTE,
                ),
                expected_outcome=RuleEvaluationOutcome.FACTS_PRESENT,
            ),
            RuleFixture(
                fixture_id="completeness-missing",
                proposed_action=RuleAction.ORDER_ACCEPTANCE,
                activities=(RuleActivity.SALE,),
                present_fact_paths=(),
                expected_outcome=RuleEvaluationOutcome.MISSING_FACTS,
                expected_missing_fact_paths=(
                    CanonicalFactPath.GOODS,
                    CanonicalFactPath.PARTIES,
                    CanonicalFactPath.ROUTE,
                ),
            ),
        ),
        internal_notes=(
            "Private synthetic demo note. Presence checks do not determine whether "
            "a transaction is lawful or cleared."
        ),
    )
    legal_nexus = RuleVersion(
        rule_id="synthetic-legal-nexus",
        version=DEMO_BUNDLE_VERSION,
        kind=RuleEvaluatorKind.LEGAL_NEXUS_PRESENCE,
        owner="TradeSieve synthetic policy team",
        effective_window=window,
        scope=RuleScope(
            (RuleAction.QUOTE_RELEASE,),
            (RuleActivity.EXPORT,),
        ),
        evaluator=LegalNexusPresenceSpec(),
        citations=(
            InternalPolicyCitation(
                citation_ref="synthetic-legal-nexus-policy",
                policy_id="synthetic-demo-policy",
                policy_version=DEMO_BUNDLE_VERSION,
                provision_locator="demo-section-legal-nexus",
                private_policy_text=(
                    "Synthetic fixture text: record whether legal-nexus facts are "
                    "present. This makes no jurisdictional or clearance conclusion."
                ),
            ),
        ),
        fixtures=(
            RuleFixture(
                fixture_id="legal-facts-present",
                proposed_action=RuleAction.QUOTE_RELEASE,
                activities=(RuleActivity.EXPORT,),
                present_fact_paths=(CanonicalFactPath.LEGAL_NEXUS,),
                expected_outcome=RuleEvaluationOutcome.FACTS_PRESENT,
            ),
            RuleFixture(
                fixture_id="legal-missing",
                proposed_action=RuleAction.QUOTE_RELEASE,
                activities=(RuleActivity.EXPORT,),
                present_fact_paths=(),
                expected_outcome=RuleEvaluationOutcome.MISSING_FACTS,
                expected_missing_fact_paths=(CanonicalFactPath.LEGAL_NEXUS,),
            ),
        ),
        internal_notes=(
            "Private synthetic demo note. A present fact is not a legal conclusion."
        ),
    )
    return RuleBundleVersion(
        tenant_id=settings.rule_bundle_tenant_id,
        deployment_id=settings.deployment_id,
        rule_set_id=settings.required_rule_set,
        bundle_id=DEMO_BUNDLE_ID,
        version=DEMO_BUNDLE_VERSION,
        owner="TradeSieve synthetic policy owner",
        effective_window=window,
        authored_by=DEMO_POLICY_AUTHOR,
        rules=(completeness, legal_nexus),
        internal_notes=(
            "Private synthetic bundle for deterministic presence-rule demonstration. "
            "It is not legal advice and never represents legal clearance."
        ),
    )


def demo_bundle_identity(bundle: RuleBundleVersion) -> ImmutableRuleBundleIdentity:
    reference = rule_bundle_ref(bundle)
    return ImmutableRuleBundleIdentity(
        bundle_id=reference.bundle_id,
        version=reference.version,
        content_hash=reference.content_hash,
    )


class DemoRuleEntitlementResolver:
    """Resolve only the three exact local demo entitlements used by TS-205."""

    def __init__(self, settings: Settings, bundle: RuleBundleVersion) -> None:
        _require_demo(settings)
        reference = rule_bundle_ref(bundle)
        self._tenant_id = settings.rule_bundle_tenant_id
        self._rule_set_target = rule_set_authorization_target_id(
            settings.deployment_id, settings.required_rule_set
        )
        self._bundle_target = rule_bundle_authorization_target_id(
            settings.deployment_id,
            settings.required_rule_set,
            reference.bundle_id,
            reference.version,
            reference.content_hash,
        )

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
        if actor_tenant_id != self._tenant_id or target_tenant_id != self._tenant_id:
            return None
        entitlement = (actor_subject, operation, target_type, target_id)
        if entitlement in {
            (
                DEMO_POLICY_AUTHOR,
                Operation.POLICY_DRAFT,
                RULE_SET_TARGET_TYPE,
                self._rule_set_target,
            ),
            (
                DEMO_POLICY_OPERATOR,
                Operation.POLICY_READ,
                RULE_SET_TARGET_TYPE,
                self._rule_set_target,
            ),
        }:
            return ResolvedTargetFacts()
        if entitlement in {
            (
                DEMO_POLICY_APPROVER,
                Operation.POLICY_APPROVE,
                RULE_BUNDLE_TARGET_TYPE,
                self._bundle_target,
            ),
            (
                DEMO_POLICY_APPROVER,
                Operation.POLICY_ACTIVATE,
                RULE_BUNDLE_TARGET_TYPE,
                self._bundle_target,
            ),
        }:
            return ResolvedTargetFacts(author_actor_id=DEMO_POLICY_AUTHOR)
        return None


def authorize_demo_rule_request(
    settings: Settings,
    authorization: AuthorizationService,
    bundle: RuleBundleVersion,
    *,
    actor: DemoRuleActor,
    operation: Operation,
    now: datetime,
) -> AuthorizedRequest:
    """Authorize a bound demo request; callers receive AuthorizationService output."""

    _require_demo(settings)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("demo authorization time must be timezone-aware")
    subject, scopes, roles = {
        DemoRuleActor.AUTHOR: (
            DEMO_POLICY_AUTHOR,
            frozenset({Scope.POLICY_OPERATE}),
            frozenset({Role.POLICY_AUTHOR}),
        ),
        DemoRuleActor.APPROVER: (
            DEMO_POLICY_APPROVER,
            frozenset({Scope.POLICY_APPROVE}),
            frozenset({Role.POLICY_APPROVER}),
        ),
        DemoRuleActor.OPERATOR: (
            DEMO_POLICY_OPERATOR,
            frozenset({Scope.POLICY_READ}),
            frozenset(),
        ),
    }[actor]
    if operation in {Operation.POLICY_DRAFT, Operation.POLICY_READ}:
        target_type = RULE_SET_TARGET_TYPE
        target_id = rule_set_authorization_target_id(
            settings.deployment_id, settings.required_rule_set
        )
    else:
        reference = rule_bundle_ref(bundle)
        target_type = RULE_BUNDLE_TARGET_TYPE
        target_id = rule_bundle_authorization_target_id(
            settings.deployment_id,
            settings.required_rule_set,
            reference.bundle_id,
            reference.version,
            reference.content_hash,
        )
    normalized = now.astimezone(UTC)
    context = RequestContext(
        actor=ActorContext(
            subject=subject,
            client_id="tradesieve-demo-cli",
            tenant_id=settings.rule_bundle_tenant_id,
            actor_type=ActorType.HUMAN,
            scopes=scopes,
            roles=roles,
            issuer="http://localhost/tradesieve-demo",
            audience="tradesieve-demo",
            issued_at=normalized - timedelta(minutes=1),
            expires_at=normalized + timedelta(hours=1),
            demo_identity=True,
        ),
        tenant_id=settings.rule_bundle_tenant_id,
        correlation_id=f"demo-{operation.value.lower().replace('_', '-')}",
    )
    return authorization.require(
        AuthorizationRequest(
            context=context,
            operation=operation,
            target=TargetObject(
                settings.rule_bundle_tenant_id,
                target_type,
                target_id,
            ),
        ),
        now=normalized,
    )


def _require_demo(settings: Settings) -> None:
    if settings.mode != "demo":
        raise RuntimeError("synthetic demo rule identities require explicit demo mode")
