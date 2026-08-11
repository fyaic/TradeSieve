"""Synthetic screening submission fixtures and entitlement tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest

from tradesieve.application.auth import Operation, ResolvedTargetFacts
from tradesieve.application.contracts import ScreeningRequest
from tradesieve.application.screening_submission import (
    screening_authorization_target,
    screening_idempotency_scope,
)
from tradesieve.config import Settings
from tradesieve.demo_screening_submission import (
    DEMO_SCREENING_ACTOR,
    DemoScreeningEntitlementResolver,
    DemoScreeningFixture,
    synthetic_demo_screening_intake,
)

NOW = datetime(2026, 8, 7, 4, 0, tzinfo=UTC)


def test_demo_fixtures_reuse_the_canonical_synthetic_request_and_change_one_field() -> (
    None
):
    baseline = synthetic_demo_screening_intake(
        Settings(), DemoScreeningFixture.BASELINE, now=NOW
    )
    changed = synthetic_demo_screening_intake(
        Settings(), DemoScreeningFixture.CHANGED, now=NOW
    )

    baseline_request = baseline.request
    changed_request = changed.request
    baseline_payload = baseline_request.model_dump(mode="json")
    changed_payload = changed_request.model_dump(mode="json")
    assert baseline_payload["external_object"]["object_version"] == "1"
    assert changed_payload["external_object"]["object_version"] == "2"
    changed_payload["external_object"]["object_version"] = "1"
    assert changed_payload == baseline_payload
    assert baseline.canonical_hash != changed.canonical_hash
    assert baseline.context == changed.context
    assert baseline.context.actor.subject == DEMO_SCREENING_ACTOR
    assert baseline.context.actor.demo_identity is True
    assert baseline.context.actor.scopes == frozenset({"screening:submit"})
    assert baseline.context.correlation_id == baseline_request.correlation_id


def test_demo_entitlement_allows_only_the_exact_synthetic_actor_and_hashed_target() -> (
    None
):
    intake = synthetic_demo_screening_intake(
        Settings(), DemoScreeningFixture.BASELINE, now=NOW
    )
    target = screening_authorization_target(
        screening_idempotency_scope(intake, "synthetic-key")
    )
    resolver = DemoScreeningEntitlementResolver(Settings())
    exact = {
        "actor_subject": DEMO_SCREENING_ACTOR,
        "actor_tenant_id": intake.context.tenant_id,
        "operation": Operation.SCREENING_SUBMIT.value,
        "target_tenant_id": target.tenant_id,
        "target_type": target.object_type,
        "target_id": target.object_id,
    }
    assert resolver.resolve(**exact) == ResolvedTargetFacts()

    for field, value in (
        ("actor_subject", "other-actor"),
        ("actor_tenant_id", "other-tenant"),
        ("operation", Operation.SCREENING_READ.value),
        ("target_tenant_id", "other-tenant"),
        ("target_type", "SCREENING"),
        ("target_id", "idem-not-a-digest"),
    ):
        candidate = dict(exact)
        candidate[field] = value
        assert resolver.resolve(**candidate) is None


@pytest.mark.parametrize(
    "operation",
    [
        lambda: synthetic_demo_screening_intake(
            Settings(
                mode="production",
                database_url="postgresql://service:strong-password@db/tradesieve",  # pragma: allowlist secret
                demo_bootstrap_enabled=False,
                rule_bundle_tenant_id="tenant-1",
                deployment_id="production-1",
                required_source_set="approved-sources-v1",
                required_rule_set="approved-rules-v1",
                official_api_token_sha256="sha256:" + "a" * 64,
            ),
            DemoScreeningFixture.BASELINE,
            now=NOW,
        ),
        lambda: DemoScreeningEntitlementResolver(
            Settings(
                mode="production",
                database_url="postgresql://service:strong-password@db/tradesieve",  # pragma: allowlist secret
                demo_bootstrap_enabled=False,
                rule_bundle_tenant_id="tenant-1",
                deployment_id="production-1",
                required_source_set="approved-sources-v1",
                required_rule_set="approved-rules-v1",
                official_api_token_sha256="sha256:" + "a" * 64,
            )
        ),
    ],
)
def test_demo_screening_is_unavailable_outside_demo_mode(operation: object) -> None:
    with pytest.raises(RuntimeError, match="explicit demo mode"):
        operation()  # type: ignore[operator]


@pytest.mark.parametrize(
    "fixture, now",
    [
        ("baseline", NOW),
        (DemoScreeningFixture.BASELINE, NOW.replace(tzinfo=None)),
    ],
)
def test_demo_screening_rejects_untyped_fixture_or_naive_time(
    fixture: object, now: datetime
) -> None:
    with pytest.raises((TypeError, ValueError)):
        synthetic_demo_screening_intake(
            Settings(),
            cast(DemoScreeningFixture, fixture),
            now=now,
        )


def test_changed_fixture_fails_closed_if_the_repository_example_shape_drifts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenExample:
        def model_dump(self, *, mode: str) -> dict[str, object]:
            assert mode == "json"
            return {"external_object": []}

    monkeypatch.setattr(
        "tradesieve.demo_screening_submission.transaction_screening_request",
        lambda: cast(ScreeningRequest, BrokenExample()),
    )
    with pytest.raises(RuntimeError, match="fixture is unavailable"):
        synthetic_demo_screening_intake(
            Settings(), DemoScreeningFixture.CHANGED, now=NOW
        )
