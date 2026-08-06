"""Immutable rule-bundle domain and lifecycle safety tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from typing import cast

import pytest

from tradesieve.application.contracts import (
    STRICT_SEMVER_PATTERN,
    ProposedAction,
    RegulatedActivity,
)
from tradesieve.application.contracts import (
    RuleEvaluationOutcome as ContractRuleEvaluationOutcome,
)
from tradesieve.application.contracts import RuleFactPath as ContractRuleFactPath
from tradesieve.domain import rule_bundle as domain
from tradesieve.domain.rule_bundle import (
    CanonicalFactPath,
    CitationKind,
    DataCompletenessPresenceSpec,
    EffectiveWindow,
    GovernanceBlockReason,
    GovernanceIssue,
    InternalPolicyCitation,
    InvalidLifecycleTransition,
    LegalNexusPresenceSpec,
    LifecycleActorType,
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
    RuleBundleStateRecord,
    RuleBundleVersion,
    RuleEvaluationOutcome,
    RuleEvaluatorKind,
    RuleFixture,
    RulePresenceEvaluation,
    RuleScope,
    RuleSetLifecycle,
    RuleVersion,
    activation_block_reasons,
    approval_block_reasons,
    calculate_rescreen_impact,
    canonical_sha256,
    evaluate_rule_presence,
    failed_rule_fixtures,
    fold_rule_bundle_events,
    rule_bundle_ref,
    semantically_applied_lifecycle_transition,
    validate_atomic_command_audit,
)

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
WINDOW = EffectiveWindow(NOW - timedelta(days=1), NOW + timedelta(days=1))
HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64


def internal_citation(
    citation_ref: str = "citation-internal-1",
) -> InternalPolicyCitation:
    return InternalPolicyCitation(
        citation_ref=citation_ref,
        policy_id="synthetic-phase1-policy",
        policy_version="1.0.0",
        provision_locator="section-nexus-1",
        private_policy_text=(
            "Synthetic Phase 1 policy: required facts trigger review only."
        ),
    )


def fixture(
    fixture_id: str = "fixture-1-present",
    *,
    action: RuleAction = RuleAction.QUOTE_RELEASE,
    activities: tuple[RuleActivity, ...] = (RuleActivity.EXPORT,),
    present: tuple[CanonicalFactPath, ...] = (CanonicalFactPath.LEGAL_NEXUS,),
    outcome: RuleEvaluationOutcome = RuleEvaluationOutcome.FACTS_PRESENT,
    missing: tuple[CanonicalFactPath, ...] = (),
) -> RuleFixture:
    return RuleFixture(fixture_id, action, activities, present, outcome, missing)


def rule(**overrides: object) -> RuleVersion:
    values: dict[str, object] = {
        "rule_id": "synthetic-legal-nexus",
        "version": "1.0.0",
        "kind": RuleEvaluatorKind.LEGAL_NEXUS_PRESENCE,
        "owner": "synthetic-policy-owner",
        "effective_window": WINDOW,
        "scope": RuleScope(
            (RuleAction.QUOTE_RELEASE,),
            (RuleActivity.EXPORT,),
        ),
        "evaluator": LegalNexusPresenceSpec(),
        "citations": (internal_citation(),),
        "fixtures": (
            fixture(),
            fixture(
                "fixture-2-missing",
                present=(),
                outcome=RuleEvaluationOutcome.MISSING_FACTS,
                missing=(CanonicalFactPath.LEGAL_NEXUS,),
            ),
            fixture(
                "fixture-3-not-applicable",
                action=RuleAction.PAYMENT,
                present=(),
                outcome=RuleEvaluationOutcome.NOT_APPLICABLE,
            ),
        ),
        "internal_notes": "Synthetic-only evaluator; not legal advice.",
    }
    values.update(overrides)
    return RuleVersion(**values)  # type: ignore[arg-type]


def bundle(version: str = "1.0.0", **overrides: object) -> RuleBundleVersion:
    values: dict[str, object] = {
        "tenant_id": "tenant-demo",
        "deployment_id": "demo",
        "rule_set_id": "synthetic-demo-rules-v1",
        "bundle_id": "synthetic-phase1-bundle",
        "version": version,
        "owner": "synthetic-policy-owner",
        "effective_window": WINDOW,
        "authored_by": "policy-author-1",
        "rules": (rule(version=version),),
        "internal_notes": "Private synthetic bundle note.",
    }
    values.update(overrides)
    return RuleBundleVersion(**values)  # type: ignore[arg-type]


def event(
    sequence: int,
    event_type: RuleBundleEventType,
    *,
    previous: RuleBundleRef | None = None,
    new: RuleBundleRef | None = None,
    impact: RescreenImpact | None = None,
    actor_type: LifecycleActorType = LifecycleActorType.HUMAN,
    event_id: str | None = None,
    tenant_id: str = "tenant-demo",
) -> RuleBundleLifecycleEvent:
    return RuleBundleLifecycleEvent(
        sequence=sequence,
        event_id=event_id or f"rule-event-{sequence}",
        tenant_id=tenant_id,
        deployment_id="demo",
        rule_set_id="synthetic-demo-rules-v1",
        event_type=event_type,
        previous_bundle=previous,
        new_bundle=new,
        reason="Synthetic lifecycle acceptance reason.",
        actor_id="policy-operator-2",
        actor_type=actor_type,
        occurred_at=NOW + timedelta(seconds=sequence),
        rescreen_impact=impact,
    )


def test_domain_and_contract_action_activity_outcome_enums_remain_exact() -> None:
    assert {item.value for item in RuleAction} == {
        item.value for item in ProposedAction
    }
    assert {item.value for item in RuleActivity} == {
        item.value for item in RegulatedActivity
    }
    assert {item.value for item in RuleEvaluationOutcome} == {
        item.value for item in ContractRuleEvaluationOutcome
    }
    assert {item.value for item in CanonicalFactPath} == {
        item.value for item in ContractRuleFactPath
    }
    assert domain.SEMANTIC_VERSION.pattern == STRICT_SEMVER_PATTERN


def test_effective_window_uses_half_open_utc_boundary() -> None:
    offset_window = EffectiveWindow(
        datetime(2026, 8, 6, 20, 0, tzinfo=timezone(timedelta(hours=8))),
        datetime(2026, 8, 6, 22, 0, tzinfo=timezone(timedelta(hours=8))),
    )
    assert offset_window.contains(NOW)
    assert offset_window.contains(NOW + timedelta(hours=1, minutes=59))
    assert not offset_window.contains(NOW - timedelta(microseconds=1))
    assert not offset_window.contains(NOW + timedelta(hours=2))
    assert EffectiveWindow(NOW, None).contains(NOW + timedelta(days=100))


@pytest.mark.parametrize(
    "values",
    [
        (cast(datetime, None), None),
        (NOW.replace(tzinfo=None), None),
        (NOW, NOW.replace(tzinfo=None)),
        (NOW, NOW),
        (NOW, NOW - timedelta(seconds=1)),
        (cast(datetime, "bad"), None),
    ],
)
def test_effective_window_rejects_invalid_types_order_and_timezone(
    values: tuple[datetime, datetime | None],
) -> None:
    with pytest.raises(ValueError):
        EffectiveWindow(*values)
    with pytest.raises(ValueError, match="timezone-aware"):
        WINDOW.contains(cast(datetime, "bad"))


def test_internal_and_official_citations_are_strict_and_distinct() -> None:
    citation = internal_citation()
    assert citation.kind is CitationKind.INTERNAL_POLICY
    assert citation.policy_content_hash() == canonical_sha256(
        {
            "policy_id": citation.policy_id,
            "policy_version": citation.policy_version,
            "private_policy_text": citation.private_policy_text,
            "provision_locator": citation.provision_locator,
        }
    )
    official = OfficialSourceProvisionCitation(
        "citation-official-1",
        "official-source-1",
        "snapshot-1",
        HASH_A,
        "article-1",
    )
    assert official.kind is CitationKind.OFFICIAL_SOURCE_PROVISION
    official_content = rule(citations=(official,)).canonical_content()
    assert isinstance(official_content, dict)
    assert official_content["citations"] == [
        {
            "citation_ref": "citation-official-1",
            "kind": "OFFICIAL_SOURCE_PROVISION",
            "provision_locator": "article-1",
            "snapshot_content_hash": HASH_A,
            "snapshot_id": "snapshot-1",
            "source_id": "official-source-1",
        }
    ]


@pytest.mark.parametrize(
    ("constructor", "args"),
    [
        (
            InternalPolicyCitation,
            ("bad ref", "policy", "1.0.0", "section", "text"),
        ),
        (
            InternalPolicyCitation,
            ("citation", "policy", "01.0.0", "section", "text"),
        ),
        (
            InternalPolicyCitation,
            ("citation", "policy", "1234567890.0.0", "section", "text"),
        ),
        (
            InternalPolicyCitation,
            ("citation", "policy", "1.0.0", " section", "text"),
        ),
        (
            InternalPolicyCitation,
            ("citation", "policy", "1.0.0", "section", " "),
        ),
        (
            InternalPolicyCitation,
            ("citation", "policy", "1.0.0", "section", "x\x00"),
        ),
        (
            OfficialSourceProvisionCitation,
            ("citation", "bad source", "snapshot", HASH_A, "article"),
        ),
        (
            OfficialSourceProvisionCitation,
            ("citation", "source", "snapshot", "bad", "article"),
        ),
        (
            OfficialSourceProvisionCitation,
            ("citation", "source", "bad snapshot", HASH_A, "article"),
        ),
    ],
)
def test_citations_reject_unsafe_identity_version_text_and_hash(
    constructor: Callable[..., object], args: tuple[object, ...]
) -> None:
    with pytest.raises(ValueError):
        constructor(*args)


def test_scope_and_specs_require_sorted_typed_finite_members() -> None:
    assert LegalNexusPresenceSpec().required_fact_paths == (
        CanonicalFactPath.LEGAL_NEXUS,
    )
    spec = DataCompletenessPresenceSpec(
        (CanonicalFactPath.GOODS, CanonicalFactPath.ROUTE)
    )
    assert spec.kind is RuleEvaluatorKind.DATA_COMPLETENESS_PRESENCE
    for value in [
        (),
        (CanonicalFactPath.ROUTE, CanonicalFactPath.GOODS),
        cast(tuple[CanonicalFactPath, ...], ("goods",)),
        (CanonicalFactPath.GOODS,) * 33,
    ]:
        with pytest.raises(ValueError):
            DataCompletenessPresenceSpec(value)
    for actions, activities in [
        ((), ()),
        (
            (RuleAction.QUOTE_RELEASE, RuleAction.CUSTOMER_ONBOARDING),
            (),
        ),
        (cast(tuple[RuleAction, ...], ("QUOTE_RELEASE",)), ()),
        ((RuleAction.QUOTE_RELEASE,), cast(tuple[RuleActivity, ...], ["EXPORT"])),
        ((RuleAction.QUOTE_RELEASE,) * 7, ()),
        ((RuleAction.QUOTE_RELEASE,), (RuleActivity.EXPORT,) * 12),
    ]:
        with pytest.raises(ValueError):
            RuleScope(actions, activities)


def test_presence_evaluator_honors_action_and_activity_scope() -> None:
    scoped = rule()
    assert evaluate_rule_presence(
        scoped,
        proposed_action=RuleAction.QUOTE_RELEASE,
        activities=(RuleActivity.EXPORT,),
        present_fact_paths=(CanonicalFactPath.LEGAL_NEXUS,),
    ) == RulePresenceEvaluation(RuleEvaluationOutcome.FACTS_PRESENT, ())
    assert evaluate_rule_presence(
        scoped,
        proposed_action=RuleAction.QUOTE_RELEASE,
        activities=(),
        present_fact_paths=(),
    ) == RulePresenceEvaluation(
        RuleEvaluationOutcome.MISSING_FACTS,
        (CanonicalFactPath.ACTIVITIES, CanonicalFactPath.LEGAL_NEXUS),
    )
    assert evaluate_rule_presence(
        scoped,
        proposed_action=RuleAction.QUOTE_RELEASE,
        activities=(),
        present_fact_paths=(
            CanonicalFactPath.ACTIVITIES,
            CanonicalFactPath.LEGAL_NEXUS,
        ),
    ) == RulePresenceEvaluation(
        RuleEvaluationOutcome.MISSING_FACTS,
        (CanonicalFactPath.ACTIVITIES,),
    )
    assert (
        evaluate_rule_presence(
            scoped,
            proposed_action=RuleAction.QUOTE_RELEASE,
            activities=(RuleActivity.SALE,),
            present_fact_paths=(),
        ).outcome
        is RuleEvaluationOutcome.NOT_APPLICABLE
    )
    assert (
        evaluate_rule_presence(
            scoped,
            proposed_action=RuleAction.PAYMENT,
            activities=(RuleActivity.EXPORT,),
            present_fact_paths=(),
        ).outcome
        is RuleEvaluationOutcome.NOT_APPLICABLE
    )
    missing = evaluate_rule_presence(
        replace(scoped, scope=RuleScope((RuleAction.QUOTE_RELEASE,), ())),
        proposed_action=RuleAction.QUOTE_RELEASE,
        activities=(),
        present_fact_paths=(),
    )
    assert missing == RulePresenceEvaluation(
        RuleEvaluationOutcome.MISSING_FACTS,
        (CanonicalFactPath.LEGAL_NEXUS,),
    )
    explicit_activity = replace(
        scoped,
        kind=RuleEvaluatorKind.DATA_COMPLETENESS_PRESENCE,
        evaluator=DataCompletenessPresenceSpec(
            (
                CanonicalFactPath.ACTIVITIES,
                CanonicalFactPath.PROPOSED_ACTION,
            )
        ),
    )
    assert evaluate_rule_presence(
        explicit_activity,
        proposed_action=RuleAction.QUOTE_RELEASE,
        activities=(RuleActivity.EXPORT,),
        present_fact_paths=(),
    ) == RulePresenceEvaluation(RuleEvaluationOutcome.FACTS_PRESENT, ())
    assert {item.expected_outcome for item in scoped.fixtures} == {
        RuleEvaluationOutcome.FACTS_PRESENT,
        RuleEvaluationOutcome.MISSING_FACTS,
        RuleEvaluationOutcome.NOT_APPLICABLE,
    }
    assert failed_rule_fixtures(scoped) == ()


def test_fixture_shape_and_presence_inputs_fail_closed() -> None:
    for values in [
        {
            "fixture_id": "fixture",
            "proposed_action": cast(RuleAction, "QUOTE_RELEASE"),
            "activities": (),
            "present_fact_paths": (),
            "expected_outcome": RuleEvaluationOutcome.NOT_APPLICABLE,
        },
        {
            "fixture_id": "fixture",
            "proposed_action": RuleAction.QUOTE_RELEASE,
            "activities": cast(tuple[RuleActivity, ...], ("EXPORT",)),
            "present_fact_paths": (),
            "expected_outcome": RuleEvaluationOutcome.NOT_APPLICABLE,
        },
        {
            "fixture_id": "fixture",
            "proposed_action": RuleAction.QUOTE_RELEASE,
            "activities": (),
            "present_fact_paths": (),
            "expected_outcome": cast(RuleEvaluationOutcome, "PASS"),
        },
        {
            "fixture_id": "fixture",
            "proposed_action": RuleAction.QUOTE_RELEASE,
            "activities": (),
            "present_fact_paths": (),
            "expected_outcome": RuleEvaluationOutcome.FACTS_PRESENT,
            "expected_missing_fact_paths": (CanonicalFactPath.GOODS,),
        },
        {
            "fixture_id": "fixture",
            "proposed_action": RuleAction.QUOTE_RELEASE,
            "activities": (),
            "present_fact_paths": (),
            "expected_outcome": RuleEvaluationOutcome.MISSING_FACTS,
        },
    ]:
        with pytest.raises(ValueError):
            RuleFixture(**values)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        evaluate_rule_presence(
            cast(RuleVersion, "bad"),
            proposed_action=RuleAction.QUOTE_RELEASE,
            activities=(),
            present_fact_paths=(),
        )
    with pytest.raises(ValueError):
        evaluate_rule_presence(
            rule(),
            proposed_action=cast(RuleAction, "QUOTE_RELEASE"),
            activities=(),
            present_fact_paths=(),
        )
    with pytest.raises(ValueError):
        RulePresenceEvaluation(cast(RuleEvaluationOutcome, "MISSING_FACTS"), ())
    with pytest.raises(ValueError):
        RulePresenceEvaluation(
            RuleEvaluationOutcome.FACTS_PRESENT,
            (CanonicalFactPath.GOODS,),
        )


def test_rule_and_bundle_hashes_are_canonical_bounded_and_injected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = rule()
    first = item.content_hash()
    assert first == item.content_hash()
    assert bundle().content_hash() == bundle().content_hash()
    assert item.content_hash(lambda payload: HASH_A) == HASH_A
    assert internal_citation().policy_content_hash(lambda payload: HASH_B) == HASH_B
    with pytest.raises(ValueError, match="canonical SHA-256"):
        item.content_hash(lambda payload: "bad")
    with pytest.raises(ValueError, match="JSON serializable"):
        canonical_sha256(object())
    monkeypatch.setattr(domain, "MAX_CANONICAL_CONTENT_BYTES", 100)
    with pytest.raises(ValueError, match="rule canonical content"):
        item.content_hash()
    with pytest.raises(ValueError, match="bundle aggregate"):
        bundle(rules=()).content_hash()


@pytest.mark.parametrize(
    "overrides",
    [
        {"rule_id": "bad id"},
        {"version": cast(str, None)},
        {"kind": cast(RuleEvaluatorKind, "LEGAL_NEXUS_PRESENCE")},
        {"effective_window": cast(EffectiveWindow, "window")},
        {"scope": cast(RuleScope, "scope")},
        {"evaluator": cast(LegalNexusPresenceSpec, "spec")},
        {
            "kind": RuleEvaluatorKind.DATA_COMPLETENESS_PRESENCE,
            "evaluator": LegalNexusPresenceSpec(),
        },
        {"owner": " owner"},
        {"internal_notes": "x" * 5001},
        {"citations": cast(tuple[InternalPolicyCitation, ...], [internal_citation()])},
        {"citations": cast(tuple[InternalPolicyCitation, ...], ("bad",))},
        {"citations": (internal_citation(),) * 17},
        {
            "citations": (
                internal_citation("citation-z"),
                internal_citation("citation-a"),
            )
        },
        {"fixtures": cast(tuple[RuleFixture, ...], [fixture()])},
        {"fixtures": cast(tuple[RuleFixture, ...], ("bad",))},
        {"fixtures": (fixture(),) * 65},
        {"fixtures": (fixture("fixture-z"), fixture("fixture-a"))},
    ],
)
def test_rule_rejects_invalid_or_unbounded_content(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        rule(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"tenant_id": "bad tenant"},
        {"version": "1.0"},
        {"effective_window": cast(EffectiveWindow, "window")},
        {"owner": ""},
        {"internal_notes": "\n"},
        {"rules": cast(tuple[RuleVersion, ...], [rule()])},
        {"rules": cast(tuple[RuleVersion, ...], ("bad",))},
        {"rules": (rule(),) * 257},
        {
            "rules": (
                rule(rule_id="z-rule"),
                rule(rule_id="a-rule"),
            )
        },
    ],
)
def test_bundle_rejects_invalid_or_unbounded_content(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        bundle(**overrides)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        rule_bundle_ref(cast(RuleBundleVersion, "bad"))


def test_governance_issues_are_structured_sorted_and_fixture_specific() -> None:
    bad_fixture = fixture(
        "fixture-failing",
        present=(),
        outcome=RuleEvaluationOutcome.FACTS_PRESENT,
    )
    incomplete = bundle(
        owner=None,
        rules=(
            rule(
                owner=None,
                citations=(),
                fixtures=(bad_fixture,),
            ),
        ),
    )
    issues = approval_block_reasons(incomplete)
    assert issues == tuple(
        sorted(
            (
                GovernanceIssue(
                    GovernanceBlockReason.FIXTURE_FAILED,
                    "synthetic-legal-nexus",
                    ("fixture-failing",),
                ),
                GovernanceIssue(GovernanceBlockReason.MISSING_BUNDLE_OWNER),
                GovernanceIssue(
                    GovernanceBlockReason.MISSING_CITATION,
                    "synthetic-legal-nexus",
                ),
                GovernanceIssue(
                    GovernanceBlockReason.MISSING_RULE_OWNER,
                    "synthetic-legal-nexus",
                ),
            ),
            key=lambda item: (item.reason.value, item.rule_id or ""),
        )
    )
    empty = approval_block_reasons(bundle(rules=()))
    assert empty == (GovernanceIssue(GovernanceBlockReason.EMPTY_BUNDLE),)
    missing_test = approval_block_reasons(bundle(rules=(rule(fixtures=()),)))
    assert missing_test == (
        GovernanceIssue(
            GovernanceBlockReason.MISSING_TEST,
            "synthetic-legal-nexus",
        ),
    )
    assert failed_rule_fixtures(incomplete.rules[0]) == ("fixture-failing",)
    assert approval_block_reasons(bundle()) == ()
    assert (
        GovernanceIssue(
            GovernanceBlockReason.OFFICIAL_CITATION_UNVERIFIED,
            "synthetic-legal-nexus",
        ).rule_id
        == "synthetic-legal-nexus"
    )
    with pytest.raises(ValueError):
        approval_block_reasons(cast(RuleBundleVersion, "bad"))


@pytest.mark.parametrize(
    ("reason", "rule_id", "fixture_ids"),
    [
        (GovernanceBlockReason.MISSING_BUNDLE_OWNER, "rule", ()),
        (GovernanceBlockReason.MISSING_RULE_OWNER, None, ()),
        (GovernanceBlockReason.FIXTURE_FAILED, None, ("fixture",)),
        (GovernanceBlockReason.FIXTURE_FAILED, "rule", ()),
        (GovernanceBlockReason.MISSING_TEST, "rule", ("fixture",)),
        (GovernanceBlockReason.FIXTURE_FAILED, "bad rule", ("fixture",)),
        (GovernanceBlockReason.FIXTURE_FAILED, "rule", ("bad fixture",)),
        (GovernanceBlockReason.FIXTURE_FAILED, "rule", ("z", "a")),
    ],
)
def test_governance_issue_rejects_corrupt_reason_shapes(
    reason: GovernanceBlockReason,
    rule_id: str | None,
    fixture_ids: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError):
        GovernanceIssue(reason, rule_id, fixture_ids)
    with pytest.raises(ValueError):
        GovernanceIssue(
            cast(GovernanceBlockReason, "MISSING_TEST"),
            "rule",
        )
    with pytest.raises(ValueError):
        GovernanceIssue(
            GovernanceBlockReason.FIXTURE_FAILED,
            "rule",
            cast(tuple[str, ...], ["fixture"]),
        )


def test_activation_blocks_future_and_expired_bundle_and_rule_windows() -> None:
    future = EffectiveWindow(NOW + timedelta(seconds=1), None)
    expired = EffectiveWindow(NOW - timedelta(days=2), NOW)
    issues = activation_block_reasons(
        bundle(
            effective_window=future,
            rules=(rule(effective_window=future),),
        ),
        now=NOW,
    )
    assert {item.reason for item in issues} == {
        GovernanceBlockReason.BUNDLE_NOT_YET_EFFECTIVE,
        GovernanceBlockReason.RULE_NOT_YET_EFFECTIVE,
    }
    issues = activation_block_reasons(
        bundle(
            effective_window=expired,
            rules=(rule(effective_window=expired),),
        ),
        now=NOW,
    )
    assert {item.reason for item in issues} == {
        GovernanceBlockReason.BUNDLE_EXPIRED,
        GovernanceBlockReason.RULE_EXPIRED,
    }
    assert activation_block_reasons(bundle(), now=NOW) == ()
    with pytest.raises(ValueError, match="timezone-aware"):
        activation_block_reasons(bundle(), now=NOW.replace(tzinfo=None))


def test_rescreen_impact_covers_initial_retire_change_and_window_change() -> None:
    first = bundle()
    initial = calculate_rescreen_impact(None, first)
    assert initial.previous_bundle is None
    assert initial.new_bundle == rule_bundle_ref(first)
    assert initial.added_rule_ids == ("synthetic-legal-nexus",)
    assert initial.affected_fact_paths == (
        CanonicalFactPath.ACTIVITIES,
        CanonicalFactPath.LEGAL_NEXUS,
        CanonicalFactPath.PROPOSED_ACTION,
    )
    assert initial.rescreen_required

    retired = calculate_rescreen_impact(first, None)
    assert retired.new_bundle is None
    assert retired.removed_rule_ids == ("synthetic-legal-nexus",)
    assert retired.affected_fact_paths == initial.affected_fact_paths
    assert retired.rescreen_required

    unchanged = calculate_rescreen_impact(first, bundle())
    assert not unchanged.rescreen_required
    assert unchanged.affected_fact_paths == ()

    changed_rule = rule(version="1.1.0", internal_notes="changed")
    changed_bundle = bundle("1.1.0", rules=(changed_rule,))
    changed = calculate_rescreen_impact(first, changed_bundle)
    assert changed.changed_rule_ids == ("synthetic-legal-nexus",)
    assert changed.affected_fact_paths == (
        CanonicalFactPath.ACTIVITIES,
        CanonicalFactPath.LEGAL_NEXUS,
        CanonicalFactPath.PROPOSED_ACTION,
    )

    shifted = replace(
        changed_bundle,
        version="1.2.0",
        effective_window=EffectiveWindow(
            NOW - timedelta(hours=1), NOW + timedelta(hours=2)
        ),
    )
    window_impact = calculate_rescreen_impact(changed_bundle, shifted)
    assert window_impact.changed_rule_ids == ("synthetic-legal-nexus",)


def test_rescreen_impact_includes_scope_and_evaluator_dependencies() -> None:
    unscoped = bundle(rules=(rule(scope=RuleScope((RuleAction.QUOTE_RELEASE,), ())),))
    action_changed = bundle(
        "1.1.0",
        rules=(
            rule(
                version="1.1.0",
                scope=RuleScope((RuleAction.PAYMENT,), ()),
            ),
        ),
    )
    action_impact = calculate_rescreen_impact(unscoped, action_changed)
    assert action_impact.affected_fact_paths == (
        CanonicalFactPath.LEGAL_NEXUS,
        CanonicalFactPath.PROPOSED_ACTION,
    )

    activity_changed = bundle(
        "1.2.0",
        rules=(
            rule(
                version="1.2.0",
                scope=RuleScope(
                    (RuleAction.PAYMENT,),
                    (RuleActivity.SALE,),
                ),
            ),
        ),
    )
    activity_impact = calculate_rescreen_impact(action_changed, activity_changed)
    assert activity_impact.affected_fact_paths == (
        CanonicalFactPath.ACTIVITIES,
        CanonicalFactPath.LEGAL_NEXUS,
        CanonicalFactPath.PROPOSED_ACTION,
    )


def test_rescreen_impact_rejects_inconsistent_or_caller_forged_metadata() -> None:
    ref = rule_bundle_ref(bundle())
    with pytest.raises(ValueError):
        calculate_rescreen_impact(None, None)
    with pytest.raises(ValueError):
        calculate_rescreen_impact(cast(RuleBundleVersion, "bad"), bundle())
    invalid_values: list[dict[str, object]] = [
        {"previous_bundle": None, "new_bundle": None},
        {"previous_bundle": cast(RuleBundleRef, "bad"), "new_bundle": ref},
        {"previous_bundle": None, "new_bundle": ref, "added_rule_ids": ["rule"]},
        {
            "previous_bundle": None,
            "new_bundle": ref,
            "added_rule_ids": ("z-rule", "a-rule"),
            "rescreen_required": True,
        },
        {
            "previous_bundle": None,
            "new_bundle": ref,
            "added_rule_ids": ("rule",),
            "removed_rule_ids": ("rule",),
        },
        {
            "previous_bundle": None,
            "new_bundle": ref,
            "added_rule_ids": ("rule",),
            "rescreen_required": True,
        },
        {
            "previous_bundle": None,
            "new_bundle": ref,
            "removed_rule_ids": ("rule",),
            "affected_fact_paths": (CanonicalFactPath.GOODS,),
            "rescreen_required": True,
        },
        {
            "previous_bundle": None,
            "new_bundle": ref,
            "changed_rule_ids": ("rule",),
            "affected_fact_paths": (CanonicalFactPath.GOODS,),
            "rescreen_required": True,
        },
        {
            "previous_bundle": ref,
            "new_bundle": None,
            "added_rule_ids": ("rule",),
            "affected_fact_paths": (CanonicalFactPath.GOODS,),
            "rescreen_required": True,
        },
        {
            "previous_bundle": ref,
            "new_bundle": None,
            "changed_rule_ids": ("rule",),
            "affected_fact_paths": (CanonicalFactPath.GOODS,),
            "rescreen_required": True,
        },
        {
            "previous_bundle": None,
            "new_bundle": ref,
            "added_rule_ids": ("rule",),
            "rescreen_required": False,
        },
        {
            "previous_bundle": None,
            "new_bundle": ref,
            "affected_fact_paths": cast(
                tuple[CanonicalFactPath, ...], ("legal_nexus",)
            ),
        },
    ]
    defaults: dict[str, object] = {
        "previous_bundle": None,
        "new_bundle": ref,
        "added_rule_ids": (),
        "removed_rule_ids": (),
        "changed_rule_ids": (),
        "affected_fact_paths": (),
        "rescreen_required": False,
    }
    for overrides in invalid_values:
        with pytest.raises(ValueError):
            RescreenImpact(**(defaults | overrides))  # type: ignore[arg-type]


def test_single_rollback_event_derives_outgoing_and_historical_states() -> None:
    first = bundle()
    second = bundle("1.1.0", rules=(rule(version="1.1.0"),))
    first_ref = rule_bundle_ref(first)
    second_ref = rule_bundle_ref(second)
    first_impact = calculate_rescreen_impact(None, first)
    second_impact = calculate_rescreen_impact(first, second)
    rollback_impact = calculate_rescreen_impact(second, first)
    retirement_impact = calculate_rescreen_impact(first, None)
    events = (
        event(1, RuleBundleEventType.DRAFTED, new=first_ref),
        event(2, RuleBundleEventType.APPROVED, new=first_ref),
        event(
            3,
            RuleBundleEventType.ACTIVATED,
            new=first_ref,
            impact=first_impact,
        ),
        event(4, RuleBundleEventType.DRAFTED, new=second_ref),
        event(5, RuleBundleEventType.APPROVED, new=second_ref),
        event(
            6,
            RuleBundleEventType.ACTIVATED,
            previous=first_ref,
            new=second_ref,
            impact=second_impact,
        ),
        event(
            7,
            RuleBundleEventType.ROLLED_BACK,
            previous=second_ref,
            new=first_ref,
            impact=rollback_impact,
        ),
    )
    lifecycle = fold_rule_bundle_events(events)
    assert lifecycle.active_bundle == first_ref
    assert lifecycle.state_for(first_ref) is RuleBundleState.ACTIVE
    assert lifecycle.state_for(second_ref) is RuleBundleState.ROLLED_BACK
    assert lifecycle.state_for(rule_bundle_ref(bundle("2.0.0"))) is None

    retired = fold_rule_bundle_events(
        (
            *events,
            event(
                8,
                RuleBundleEventType.RETIRED,
                previous=first_ref,
                impact=retirement_impact,
            ),
        )
    )
    assert retired.active_bundle is None
    assert retired.state_for(first_ref) is RuleBundleState.RETIRED


@pytest.mark.parametrize(
    ("event_type", "actor_type"),
    [
        (RuleBundleEventType.DRAFTED, LifecycleActorType.AGENT),
        (RuleBundleEventType.APPROVED, LifecycleActorType.SERVICE),
        (RuleBundleEventType.ACTIVATED, LifecycleActorType.AGENT),
        (RuleBundleEventType.RETIRED, LifecycleActorType.SERVICE),
        (RuleBundleEventType.ROLLED_BACK, LifecycleActorType.AGENT),
    ],
)
def test_lifecycle_event_rejects_non_authoritative_actor_types(
    event_type: RuleBundleEventType, actor_type: LifecycleActorType
) -> None:
    first = bundle()
    ref = rule_bundle_ref(first)
    impact = calculate_rescreen_impact(None, first)
    previous = (
        ref
        if event_type in {RuleBundleEventType.RETIRED, RuleBundleEventType.ROLLED_BACK}
        else None
    )
    new = None if event_type is RuleBundleEventType.RETIRED else ref
    if event_type is RuleBundleEventType.ROLLED_BACK:
        new = rule_bundle_ref(bundle("1.1.0", rules=(rule(version="1.1.0"),)))
        impact = calculate_rescreen_impact(
            first, bundle("1.1.0", rules=(rule(version="1.1.0"),))
        )
    with pytest.raises(ValueError, match="actor type"):
        event(
            1,
            event_type,
            previous=previous,
            new=new,
            impact=(
                impact
                if event_type
                in {
                    RuleBundleEventType.ACTIVATED,
                    RuleBundleEventType.RETIRED,
                    RuleBundleEventType.ROLLED_BACK,
                }
                else None
            ),
            actor_type=actor_type,
        )


def test_lifecycle_event_rejects_invalid_shape_impact_and_runtime_types() -> None:
    first = bundle()
    ref = rule_bundle_ref(first)
    impact = calculate_rescreen_impact(None, first)
    cases: list[dict[str, object]] = [
        {"sequence": cast(int, "1")},
        {"sequence": 0},
        {"sequence": 9_223_372_036_854_775_808},
        {"event_type": cast(RuleBundleEventType, "ACTIVATED")},
        {"actor_type": cast(LifecycleActorType, "HUMAN")},
        {"new_bundle": cast(RuleBundleRef, "bad")},
        {"previous_bundle": ref},
        {"new_bundle": None},
        {"reason": ""},
        {"occurred_at": NOW.replace(tzinfo=None)},
        {"rescreen_impact": impact},
    ]
    base: dict[str, object] = {
        "sequence": 1,
        "event_id": "event-1",
        "tenant_id": "tenant-demo",
        "deployment_id": "demo",
        "rule_set_id": "set-1",
        "event_type": RuleBundleEventType.DRAFTED,
        "previous_bundle": None,
        "new_bundle": ref,
        "reason": "Synthetic reason.",
        "actor_id": "author-1",
        "actor_type": LifecycleActorType.HUMAN,
        "occurred_at": NOW,
        "rescreen_impact": None,
    }
    for overrides in cases:
        with pytest.raises(ValueError):
            RuleBundleLifecycleEvent(**(base | overrides))  # type: ignore[arg-type]
    mismatched = replace(
        impact,
        new_bundle=rule_bundle_ref(bundle("1.1.0", rules=(rule(version="1.1.0"),))),
    )
    with pytest.raises(ValueError, match="references must match"):
        event(
            1,
            RuleBundleEventType.ACTIVATED,
            new=ref,
            impact=mismatched,
        )


def test_fold_rejects_corrupt_identity_order_hash_and_transitions() -> None:
    first = bundle()
    first_ref = rule_bundle_ref(first)
    first_impact = calculate_rescreen_impact(None, first)
    draft = event(1, RuleBundleEventType.DRAFTED, new=first_ref)
    approved = event(2, RuleBundleEventType.APPROVED, new=first_ref)
    active = event(
        3,
        RuleBundleEventType.ACTIVATED,
        new=first_ref,
        impact=first_impact,
    )
    invalid_sequences = [
        cast(tuple[RuleBundleLifecycleEvent, ...], [draft]),
        (draft, replace(approved, tenant_id="tenant-other")),
        (draft, replace(approved, sequence=1)),
        (draft, replace(approved, event_id=draft.event_id)),
        (approved,),
        (draft, draft),
        (draft, event(2, RuleBundleEventType.DRAFTED, new=first_ref)),
        (draft, active),
        (
            draft,
            approved,
            event(
                3,
                RuleBundleEventType.ACTIVATED,
                previous=first_ref,
                new=first_ref,
                impact=calculate_rescreen_impact(first, first),
            ),
        ),
        (
            draft,
            approved,
            event(
                3,
                RuleBundleEventType.RETIRED,
                previous=first_ref,
                impact=calculate_rescreen_impact(first, None),
            ),
        ),
    ]
    for events in invalid_sequences:
        with pytest.raises((InvalidLifecycleTransition, ValueError)):
            fold_rule_bundle_events(events)

    different_hash = replace(first_ref, content_hash=HASH_A)
    with pytest.raises(InvalidLifecycleTransition):
        fold_rule_bundle_events(
            (
                draft,
                replace(approved, new_bundle=different_hash),
            )
        )
    second = bundle("1.1.0", rules=(rule(version="1.1.0"),))
    second_ref = rule_bundle_ref(second)
    second_impact = calculate_rescreen_impact(first, second)
    never_active = (
        draft,
        approved,
        active,
        event(4, RuleBundleEventType.DRAFTED, new=second_ref),
        event(5, RuleBundleEventType.APPROVED, new=second_ref),
        event(
            6,
            RuleBundleEventType.ROLLED_BACK,
            previous=first_ref,
            new=second_ref,
            impact=second_impact,
        ),
    )
    with pytest.raises(InvalidLifecycleTransition):
        fold_rule_bundle_events(never_active)
    with pytest.raises(InvalidLifecycleTransition):
        fold_rule_bundle_events(
            (
                draft,
                approved,
                active,
                event(
                    4,
                    RuleBundleEventType.ROLLED_BACK,
                    previous=first_ref,
                    new=first_ref,
                    impact=calculate_rescreen_impact(first, first),
                ),
            )
        )


def test_shared_idempotence_and_atomic_audit_helpers_reject_untyped_inputs() -> None:
    item = bundle()
    reference = rule_bundle_ref(item)
    drafted = event(1, RuleBundleEventType.DRAFTED, new=reference)
    approved = event(2, RuleBundleEventType.APPROVED, new=reference)
    lifecycle = RuleSetLifecycle(
        (RuleBundleStateRecord(reference, RuleBundleState.APPROVED),), None
    )
    scope = (approved.tenant_id, approved.deployment_id, approved.rule_set_id)
    audit = RuleBundleCommandAuditRecord(
        "command-1",
        "authorization-1",
        approved.event_id,
        *scope,
        reference.bundle_id,
        reference.version,
        reference.content_hash,
        "rule_bundle",
        "target-1",
        approved.actor_id,
        approved.actor_type,
        "POLICY_WRITE",
        RuleBundleCommandOutcome.SUCCESS,
        RuleBundleCommandReason.APPROVED,
        approved.occurred_at,
    )

    assert semantically_applied_lifecycle_transition(approved, lifecycle, ()) is None
    invalid_semantic_arguments = (
        (cast(RuleBundleLifecycleEvent, object()), lifecycle, ()),
        (approved, cast(RuleSetLifecycle, object()), ()),
        (
            approved,
            lifecycle,
            cast(tuple[RuleBundleLifecycleEvent, ...], (object(),)),
        ),
        (drafted, fold_rule_bundle_events((drafted,)), (drafted,)),
    )
    for attempted, current, events in invalid_semantic_arguments:
        with pytest.raises(ValueError):
            semantically_applied_lifecycle_transition(attempted, current, events)

    invalid_audit_arguments = (
        (cast(RuleBundleCommandAuditRecord, object()), scope, reference, approved),
        (audit, cast(tuple[str, str, str], object()), reference, approved),
        (audit, scope, cast(RuleBundleRef, object()), approved),
        (audit, scope, reference, cast(RuleBundleLifecycleEvent, object())),
    )
    for (
        candidate,
        candidate_scope,
        candidate_reference,
        candidate_event,
    ) in invalid_audit_arguments:
        with pytest.raises(ValueError):
            validate_atomic_command_audit(
                candidate,
                candidate_scope,
                candidate_reference,
                RuleBundleCommandReason.APPROVED,
                candidate_event,
            )
    with pytest.raises(ValueError):
        validate_atomic_command_audit(
            audit,
            scope,
            reference,
            cast(RuleBundleCommandReason, object()),
            approved,
        )
