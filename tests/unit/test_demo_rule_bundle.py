"""Synthetic demo bundle, entitlement, and bootstrap orchestration tests."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from psycopg import Connection

from tradesieve import runtime
from tradesieve.adapters.in_memory_rule_bundle import InMemoryRuleBundleRepository
from tradesieve.application.auth import (
    AuthorizationDenied,
    AuthorizationRequest,
    AuthorizationService,
    Operation,
    TargetObject,
)
from tradesieve.application.rule_bundle import RuleBundleService
from tradesieve.config import Settings
from tradesieve.demo_rule_bundle import (
    DEMO_APPROVAL_REASON,
    DEMO_POLICY_AUTHOR,
    DemoRuleActor,
    DemoRuleEntitlementResolver,
    authorize_demo_rule_request,
    demo_bundle_identity,
    synthetic_demo_rule_bundle,
)
from tradesieve.domain.rule_bundle import (
    InternalPolicyCitation,
    LifecycleWriteOutcome,
    RuleBundleCommandReason,
    RuleBundleEventType,
    RuleBundleState,
    activation_block_reasons,
    failed_rule_fixtures,
    rule_bundle_ref,
)

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def services() -> tuple[
    Settings,
    InMemoryRuleBundleRepository,
    RuleBundleService,
    AuthorizationService,
    list[object],
]:
    settings = Settings()
    bundle = synthetic_demo_rule_bundle(settings)
    repository = InMemoryRuleBundleRepository()
    authorization_events: list[object] = []
    authorization = AuthorizationService(
        authorization_events.append,
        DemoRuleEntitlementResolver(settings, bundle),
    )
    service = RuleBundleService(
        repository,
        deployment_id=settings.deployment_id,
        rule_set_id=settings.required_rule_set,
        clock=lambda: NOW,
    )
    return settings, repository, service, authorization, authorization_events


def test_demo_bundle_is_stable_governed_finite_and_explicitly_non_clearance() -> None:
    settings = Settings()
    first = synthetic_demo_rule_bundle(settings)
    second = synthetic_demo_rule_bundle(settings)
    assert first == second
    assert rule_bundle_ref(first) == rule_bundle_ref(second)
    assert first.authored_by == DEMO_POLICY_AUTHOR
    assert len(first.rules) == 2
    assert first.effective_window.effective_until is None
    assert activation_block_reasons(first, now=NOW) == ()
    assert all(failed_rule_fixtures(rule) == () for rule in first.rules)
    private = json.dumps(
        {
            "bundle_note": first.internal_notes,
            "rule_notes": [rule.internal_notes for rule in first.rules],
            "policy_text": [
                citation.private_policy_text
                for rule in first.rules
                for citation in rule.citations
                if isinstance(citation, InternalPolicyCitation)
            ],
        }
    ).lower()
    assert "not legal advice" in private
    assert "clearance" in private


def test_demo_entitlements_are_exact_and_disabled_outside_demo() -> None:
    settings, _, _, authorization, events = services()
    bundle = synthetic_demo_rule_bundle(settings)
    for actor, operation in (
        (DemoRuleActor.AUTHOR, Operation.POLICY_DRAFT),
        (DemoRuleActor.APPROVER, Operation.POLICY_APPROVE),
        (DemoRuleActor.APPROVER, Operation.POLICY_ACTIVATE),
        (DemoRuleActor.OPERATOR, Operation.POLICY_READ),
    ):
        authorized = authorize_demo_rule_request(
            settings,
            authorization,
            bundle,
            actor=actor,
            operation=operation,
            now=NOW,
        )
        assert authorized.operation is operation
    assert len(events) == 4
    resolver = DemoRuleEntitlementResolver(settings, bundle)
    assert (
        resolver.resolve(
            actor_subject=DEMO_POLICY_AUTHOR,
            actor_tenant_id="wrong-tenant",
            operation=Operation.POLICY_DRAFT,
            target_tenant_id=settings.rule_bundle_tenant_id,
            target_type="rule_set",
            target_id="irrelevant",
        )
        is None
    )

    valid = authorize_demo_rule_request(
        settings,
        authorization,
        bundle,
        actor=DemoRuleActor.OPERATOR,
        operation=Operation.POLICY_READ,
        now=NOW,
    )
    with pytest.raises(AuthorizationDenied):
        authorization.require(
            AuthorizationRequest(
                context=valid.context,
                operation=Operation.POLICY_READ,
                target=TargetObject(
                    settings.rule_bundle_tenant_id,
                    "rule_set",
                    "wrong-target",
                ),
            ),
            now=NOW,
        )
    assert len(events) == 6

    production = Settings(
        mode="production",
        database_url="postgresql://service:strong-password@db/tradesieve",  # pragma: allowlist secret
        demo_bootstrap_enabled=False,
        rule_bundle_tenant_id="tenant-1",
        deployment_id="production-1",
        required_source_set="approved-sources-v1",
        required_rule_set="approved-rules-v1",
    )
    with pytest.raises(RuntimeError, match="explicit demo mode"):
        synthetic_demo_rule_bundle(production)
    with pytest.raises(ValueError, match="timezone-aware"):
        authorize_demo_rule_request(
            settings,
            authorization,
            bundle,
            actor=DemoRuleActor.OPERATOR,
            operation=Operation.POLICY_READ,
            now=NOW.replace(tzinfo=None),
        )


def test_demo_bootstrap_continues_exact_draft_and_repeat_keeps_history_stable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, repository, service, authorization, authorization_events = services()
    bundle = synthetic_demo_rule_bundle(settings)
    draft = authorize_demo_rule_request(
        settings,
        authorization,
        bundle,
        actor=DemoRuleActor.AUTHOR,
        operation=Operation.POLICY_DRAFT,
        now=NOW,
    )
    service.save_draft(draft, bundle)
    monkeypatch.setattr(
        runtime,
        "build_demo_rule_services",
        lambda active_settings, connection: (bundle, service, authorization),
    )
    connection = cast(Connection[Any], object())
    runtime._bootstrap_demo_rule_bundle(settings, connection, NOW)
    history = repository.list_lifecycle_events(
        settings.rule_bundle_tenant_id,
        settings.deployment_id,
        settings.required_rule_set,
        limit=10,
    )
    assert [event.event_type for event in history] == [
        RuleBundleEventType.DRAFTED,
        RuleBundleEventType.APPROVED,
        RuleBundleEventType.ACTIVATED,
    ]
    audits = repository.list_command_audits(
        settings.rule_bundle_tenant_id,
        settings.deployment_id,
        settings.required_rule_set,
        limit=10,
    )
    assert [audit.reason for audit in audits] == [
        RuleBundleCommandReason.DRAFT_APPLIED,
        RuleBundleCommandReason.APPROVED,
        RuleBundleCommandReason.ACTIVATED,
    ]
    before = (history, audits)
    runtime._bootstrap_demo_rule_bundle(settings, connection, NOW)
    assert (
        repository.list_lifecycle_events(
            settings.rule_bundle_tenant_id,
            settings.deployment_id,
            settings.required_rule_set,
            limit=10,
        )
        == before[0]
    )
    assert (
        repository.list_command_audits(
            settings.rule_bundle_tenant_id,
            settings.deployment_id,
            settings.required_rule_set,
            limit=10,
        )
        == before[1]
    )
    assert len(authorization_events) == 5

    read = authorize_demo_rule_request(
        settings,
        authorization,
        bundle,
        actor=DemoRuleActor.OPERATOR,
        operation=Operation.POLICY_READ,
        now=NOW,
    )
    view = service.current_active(read)
    assert view is not None
    assert view.summary.state is RuleBundleState.ACTIVE
    serialized = json.dumps(view.model_dump(mode="json"))
    assert "private_policy_text" not in serialized
    assert "internal_notes" not in serialized
    assert "policy_content_hash" in serialized


def test_demo_bootstrap_rejects_changed_exact_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, repository, service, authorization, _ = services()
    bundle = synthetic_demo_rule_bundle(settings)
    monkeypatch.setattr(
        runtime,
        "build_demo_rule_services",
        lambda active_settings, connection: (bundle, service, authorization),
    )
    connection = cast(Connection[Any], object())
    runtime._bootstrap_demo_rule_bundle(settings, connection, NOW)
    events = repository.list_lifecycle_events(
        settings.rule_bundle_tenant_id,
        settings.deployment_id,
        settings.required_rule_set,
        limit=10,
    )
    repository.corrupt_replace_lifecycle_events_for_test(
        settings.rule_bundle_tenant_id,
        settings.deployment_id,
        settings.required_rule_set,
        (events[0], replace(events[1], reason="Changed review intent."), events[2]),
    )
    with pytest.raises(RuntimeError, match="conflicting synthetic demo lifecycle"):
        runtime._bootstrap_demo_rule_bundle(settings, connection, NOW)


def test_demo_service_graph_uses_real_adapters_without_database_io() -> None:
    bundle, service, authorization = runtime.build_demo_rule_services(
        Settings(), cast(Connection[Any], object())
    )
    assert bundle == synthetic_demo_rule_bundle(Settings())
    assert isinstance(service, RuleBundleService)
    assert isinstance(authorization, AuthorizationService)


@pytest.mark.parametrize(
    ("initial_stage", "method_name", "forced_outcome", "message"),
    [
        ("EMPTY", "save_draft", object(), "draft did not persist"),
        ("DRAFT", "approve", object(), "approval did not persist"),
        ("APPROVED", "activate", object(), "activation did not persist"),
    ],
)
def test_demo_bootstrap_rejects_impossible_repository_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    initial_stage: str,
    method_name: str,
    forced_outcome: object,
    message: str,
) -> None:
    settings, _, service, authorization, _ = services()
    bundle = synthetic_demo_rule_bundle(settings)
    if initial_stage in {"DRAFT", "APPROVED"}:
        draft = authorize_demo_rule_request(
            settings,
            authorization,
            bundle,
            actor=DemoRuleActor.AUTHOR,
            operation=Operation.POLICY_DRAFT,
            now=NOW,
        )
        service.save_draft(draft, bundle)
    if initial_stage == "APPROVED":
        approval = authorize_demo_rule_request(
            settings,
            authorization,
            bundle,
            actor=DemoRuleActor.APPROVER,
            operation=Operation.POLICY_APPROVE,
            now=NOW,
        )
        service.approve(
            approval,
            demo_bundle_identity(bundle),
            reason=DEMO_APPROVAL_REASON,
        )
    monkeypatch.setattr(service, method_name, lambda *args, **kwargs: forced_outcome)
    monkeypatch.setattr(
        runtime,
        "build_demo_rule_services",
        lambda active_settings, connection: (bundle, service, authorization),
    )
    with pytest.raises(RuntimeError, match=message):
        runtime._bootstrap_demo_rule_bundle(
            settings, cast(Connection[Any], object()), NOW
        )


def test_demo_bootstrap_checks_final_persisted_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, _, service, authorization, _ = services()
    bundle = synthetic_demo_rule_bundle(settings)
    draft = authorize_demo_rule_request(
        settings,
        authorization,
        bundle,
        actor=DemoRuleActor.AUTHOR,
        operation=Operation.POLICY_DRAFT,
        now=NOW,
    )
    service.save_draft(draft, bundle)
    monkeypatch.setattr(
        service,
        "approve",
        lambda *args, **kwargs: LifecycleWriteOutcome.APPLIED,
    )
    monkeypatch.setattr(
        service,
        "activate",
        lambda *args, **kwargs: LifecycleWriteOutcome.APPLIED,
    )
    monkeypatch.setattr(
        runtime,
        "build_demo_rule_services",
        lambda active_settings, connection: (bundle, service, authorization),
    )
    with pytest.raises(RuntimeError, match="did not become exactly active"):
        runtime._bootstrap_demo_rule_bundle(
            settings, cast(Connection[Any], object()), NOW
        )


def test_demo_stage_rejects_every_inconsistent_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, _, service, authorization, _ = services()
    bundle = synthetic_demo_rule_bundle(settings)
    connection = cast(Connection[Any], object())
    monkeypatch.setattr(
        runtime,
        "build_demo_rule_services",
        lambda active_settings, active_connection: (
            bundle,
            service,
            authorization,
        ),
    )
    runtime._bootstrap_demo_rule_bundle(settings, connection, NOW)

    read = authorize_demo_rule_request(
        settings,
        authorization,
        bundle,
        actor=DemoRuleActor.OPERATOR,
        operation=Operation.POLICY_READ,
        now=NOW,
    )
    listing = service.list_bundles(read)
    history = service.history(read)
    active = service.current_active(read)
    assert active is not None

    class ProjectionService:
        def __init__(
            self, projected_listing: Any, projected_history: Any, projected_active: Any
        ) -> None:
            self._listing = projected_listing
            self._history = projected_history
            self._active = projected_active

        def list_bundles(self, authorized: object) -> Any:
            del authorized
            return self._listing

        def history(self, authorized: object) -> Any:
            del authorized
            return self._history

        def current_active(self, authorized: object) -> Any:
            del authorized
            return self._active

    def assert_stage_rejected(
        projected_listing: Any,
        projected_history: Any,
        projected_active: Any,
        message: str,
        *,
        expected_bundle: object = bundle,
    ) -> None:
        projected = cast(
            RuleBundleService,
            ProjectionService(projected_listing, projected_history, projected_active),
        )
        with pytest.raises(RuntimeError, match=message):
            runtime._demo_bundle_stage(
                projected,
                read,
                cast(Any, expected_bundle),
            )

    assert_stage_rejected(
        listing.model_copy(update={"bundles": [*listing.bundles, listing.bundles[0]]}),
        history,
        active,
        "bundle state",
    )
    assert_stage_rejected(
        listing,
        history,
        active,
        "bundle identity",
        expected_bundle=replace(bundle, owner="changed-owner"),
    )
    assert_stage_rejected(
        listing,
        history.model_copy(update={"events": []}),
        active,
        "lifecycle history",
    )
    inconsistent_summary = listing.bundles[0].model_copy(
        update={"state": RuleBundleState.DRAFT, "active": False}
    )
    assert_stage_rejected(
        listing.model_copy(update={"bundles": [inconsistent_summary]}),
        history,
        active,
        "lifecycle projection",
    )
    assert_stage_rejected(listing, history, None, "active pointer")
    changed_active = active.model_copy(
        update={
            "summary": active.summary.model_copy(
                update={"content_hash": "sha256:" + "b" * 64}
            )
        }
    )
    assert_stage_rejected(listing, history, changed_active, "active content")
