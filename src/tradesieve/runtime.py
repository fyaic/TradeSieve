"""Database-backed runtime health and bootstrap operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from psycopg import Connection

from tradesieve.adapters.postgres_authorization import PostgresAuthorizationAuditSink
from tradesieve.adapters.postgres_rule_bundle import PostgresRuleBundleRepository
from tradesieve.adapters.postgres_source_registry import PostgresSourceRegistry
from tradesieve.application.auth import (
    AuthorizationService,
    AuthorizedRequest,
    Operation,
)
from tradesieve.application.contracts import HashedVersionReference
from tradesieve.application.rule_bundle import (
    RuleBundleReadinessService,
    RuleBundleService,
)
from tradesieve.application.source_registry import (
    SourceRegistryService,
    readiness_check_value,
)
from tradesieve.config import Settings
from tradesieve.demo_rule_bundle import (
    DEMO_ACTIVATION_REASON,
    DEMO_APPROVAL_REASON,
    DEMO_POLICY_APPROVER,
    DEMO_POLICY_AUTHOR,
    DemoRuleActor,
    DemoRuleEntitlementResolver,
    authorize_demo_rule_request,
    demo_bundle_identity,
    synthetic_demo_rule_bundle,
)
from tradesieve.domain.rule_bundle import (
    DraftWriteOutcome,
    LifecycleWriteOutcome,
    RuleBundleEventType,
    RuleBundleRef,
    RuleBundleState,
    RuleBundleVersion,
    rule_bundle_ref,
)
from tradesieve.domain.source_registry import (
    SourceAccessMethod,
    SourceAvailability,
    SourceRegistration,
    SourceRuntimeObservation,
)

MIGRATION_REVISION = "20260806_0003"


@dataclass(frozen=True, slots=True)
class RuntimeStatus:
    ready: bool
    checks: dict[str, str]

    def public_checks(self) -> dict[str, str]:
        """Return operational states without coverage IDs or migration versions."""
        public_states = {"OK", "UNAVAILABLE", "NOT_APPLIED"}
        return {
            name: value if value in public_states else "UNAVAILABLE"
            for name, value in self.checks.items()
        }


def connect(settings: Settings) -> Connection[Any]:
    return psycopg.connect(
        settings.database_url,
        connect_timeout=settings.database_connect_timeout_seconds,
        autocommit=True,
    )


def check_readiness(settings: Settings) -> RuntimeStatus:
    checks = {
        "database": "UNAVAILABLE",
        "migration": "NOT_APPLIED",
        "required_source_coverage": "UNAVAILABLE",
        "required_rule_coverage": "UNAVAILABLE",
    }
    try:
        connection = connect(settings)
    except psycopg.Error:
        return RuntimeStatus(ready=False, checks=checks)

    with connection:
        checks["database"] = "OK"
        try:
            revision = connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()
        except psycopg.Error:
            return RuntimeStatus(ready=False, checks=checks)
        if not revision or revision[0] != MIGRATION_REVISION:
            return RuntimeStatus(ready=False, checks=checks)
        checks["migration"] = "OK"

        try:
            source_readiness = SourceRegistryService(
                PostgresSourceRegistry(connection)
            ).required_set_readiness(
                settings.deployment_id, settings.required_source_set
            )
            rule_ready = RuleBundleReadinessService(
                PostgresRuleBundleRepository(connection),
                deployment_id=settings.deployment_id,
                rule_set_id=settings.required_rule_set,
            ).is_current_active_ready(settings.rule_bundle_tenant_id)
        except (psycopg.Error, TypeError, ValueError):
            return RuntimeStatus(ready=False, checks=checks)

    checks["required_source_coverage"] = readiness_check_value(source_readiness)
    if rule_ready:
        checks["required_rule_coverage"] = "OK"
    ready = all(value == "OK" for value in checks.values())
    return RuntimeStatus(ready=ready, checks=checks)


def bootstrap_demo(settings: Settings) -> None:
    if settings.mode != "demo" or not settings.demo_bootstrap_enabled:
        raise RuntimeError("demo bootstrap is disabled outside explicit demo mode")
    with connect(settings) as connection:
        registry = SourceRegistryService(PostgresSourceRegistry(connection))
        registry.define_required_sources(
            settings.deployment_id,
            settings.required_source_set,
            ("synthetic-source-v1",),
        )
        registry.register(
            SourceRegistration(
                deployment_id=settings.deployment_id,
                source_set_id=settings.required_source_set,
                source_id="synthetic-source-v1",
                name="Synthetic source fixture",
                owner="TradeSieve demo",
                responsible_operator="demo-source-operator",
                jurisdiction="SYNTHETIC",
                legal_scope="Synthetic screening behavior only",
                data_scope="Synthetic entities with no production data",
                access_method=SourceAccessMethod.INTERNAL,
                licence_summary="Synthetic demo fixture; no production use",
                refresh_expectation=timedelta(hours=1),
                stale_after=timedelta(hours=2),
            )
        )
        registry.activate(settings.deployment_id, "synthetic-source-v1")
        now = datetime.now(UTC)
        registry.record_observation(
            SourceRuntimeObservation(
                deployment_id=settings.deployment_id,
                source_id="synthetic-source-v1",
                availability=SourceAvailability.AVAILABLE,
                observed_at=now,
                active_snapshot_id="synthetic-snapshot-v1",
                retrieved_at=now,
                effective_from=now,
            )
        )
        _bootstrap_demo_rule_bundle(settings, connection, now)


def build_demo_rule_services(
    settings: Settings, connection: Connection[Any]
) -> tuple[RuleBundleVersion, RuleBundleService, AuthorizationService]:
    """Build the explicit demo-only service graph over real PostgreSQL adapters."""

    bundle = synthetic_demo_rule_bundle(settings)
    repository = PostgresRuleBundleRepository(connection)
    authorization = AuthorizationService(
        PostgresAuthorizationAuditSink(connection),
        DemoRuleEntitlementResolver(settings, bundle),
    )
    service = RuleBundleService(
        repository,
        deployment_id=settings.deployment_id,
        rule_set_id=settings.required_rule_set,
    )
    return bundle, service, authorization


def _bootstrap_demo_rule_bundle(
    settings: Settings, connection: Connection[Any], now: datetime
) -> None:
    bundle, service, authorization = build_demo_rule_services(settings, connection)
    read = authorize_demo_rule_request(
        settings,
        authorization,
        bundle,
        actor=DemoRuleActor.OPERATOR,
        operation=Operation.POLICY_READ,
        now=now,
    )
    stage = _demo_bundle_stage(service, read, bundle)
    identity = demo_bundle_identity(bundle)
    if stage == "EMPTY":
        draft = authorize_demo_rule_request(
            settings,
            authorization,
            bundle,
            actor=DemoRuleActor.AUTHOR,
            operation=Operation.POLICY_DRAFT,
            now=now,
        )
        draft_outcome = service.save_draft(draft, bundle)
        if draft_outcome not in {
            DraftWriteOutcome.APPLIED,
            DraftWriteOutcome.IDEMPOTENT,
        }:
            raise RuntimeError("synthetic demo draft did not persist")
        stage = "DRAFT"
    if stage == "DRAFT":
        approve = authorize_demo_rule_request(
            settings,
            authorization,
            bundle,
            actor=DemoRuleActor.APPROVER,
            operation=Operation.POLICY_APPROVE,
            now=now,
        )
        approval_outcome = service.approve(
            approve,
            identity,
            reason=DEMO_APPROVAL_REASON,
        )
        if approval_outcome not in {
            LifecycleWriteOutcome.APPLIED,
            LifecycleWriteOutcome.IDEMPOTENT,
        }:
            raise RuntimeError("synthetic demo approval did not persist")
        stage = "APPROVED"
    if stage == "APPROVED":
        activate = authorize_demo_rule_request(
            settings,
            authorization,
            bundle,
            actor=DemoRuleActor.APPROVER,
            operation=Operation.POLICY_ACTIVATE,
            now=now,
        )
        activation_outcome = service.activate(
            activate,
            identity,
            reason=DEMO_ACTIVATION_REASON,
        )
        if activation_outcome not in {
            LifecycleWriteOutcome.APPLIED,
            LifecycleWriteOutcome.IDEMPOTENT,
        }:
            raise RuntimeError("synthetic demo activation did not persist")
    if _demo_bundle_stage(service, read, bundle) != "ACTIVE":
        raise RuntimeError("synthetic demo bundle did not become exactly active")


def _demo_bundle_stage(
    service: RuleBundleService,
    authorized_read: AuthorizedRequest,
    bundle: RuleBundleVersion,
) -> str:
    reference = rule_bundle_ref(bundle)
    listing = service.list_bundles(authorized_read)
    history = service.history(authorized_read)
    if not listing.bundles and not history.events:
        return "EMPTY"
    if len(listing.bundles) != 1:
        raise RuntimeError("conflicting synthetic demo bundle state")
    summary = listing.bundles[0]
    if (
        summary.tenant_id,
        summary.deployment_id,
        summary.rule_set_id,
        summary.bundle_id,
        summary.version,
        summary.content_hash,
        summary.authored_by,
        summary.rule_count,
    ) != (
        bundle.tenant_id,
        bundle.deployment_id,
        bundle.rule_set_id,
        reference.bundle_id,
        reference.version,
        reference.content_hash,
        DEMO_POLICY_AUTHOR,
        len(bundle.rules),
    ):
        raise RuntimeError("conflicting synthetic demo bundle identity")
    expected = (
        (
            RuleBundleEventType.DRAFTED,
            DEMO_POLICY_AUTHOR,
            "Immutable rule bundle draft saved.",
            None,
            reference,
        ),
        (
            RuleBundleEventType.APPROVED,
            DEMO_POLICY_APPROVER,
            DEMO_APPROVAL_REASON,
            None,
            reference,
        ),
        (
            RuleBundleEventType.ACTIVATED,
            DEMO_POLICY_APPROVER,
            DEMO_ACTIVATION_REASON,
            None,
            reference,
        ),
    )
    if not 1 <= len(history.events) <= len(expected):
        raise RuntimeError("conflicting synthetic demo lifecycle history")
    for index, event in enumerate(history.events):
        event_type, actor_id, reason, previous, new = expected[index]
        if (
            event.sequence != index + 1
            or event.event_type is not event_type
            or event.actor_id != actor_id
            or event.reason != reason
            or _history_reference(event.previous_bundle) != previous
            or _history_reference(event.new_bundle) != new
        ):
            raise RuntimeError("conflicting synthetic demo lifecycle history")
    stage = ("DRAFT", "APPROVED", "ACTIVE")[len(history.events) - 1]
    expected_state = {
        "DRAFT": RuleBundleState.DRAFT,
        "APPROVED": RuleBundleState.APPROVED,
        "ACTIVE": RuleBundleState.ACTIVE,
    }[stage]
    active = service.current_active(authorized_read)
    if summary.state is not expected_state or summary.active is not (stage == "ACTIVE"):
        raise RuntimeError("conflicting synthetic demo lifecycle projection")
    if (active is None) is (stage == "ACTIVE"):
        raise RuntimeError("conflicting synthetic demo active pointer")
    if active is not None and active.summary.content_hash != reference.content_hash:
        raise RuntimeError("conflicting synthetic demo active content")
    return stage


def _history_reference(value: HashedVersionReference | None) -> RuleBundleRef | None:
    if value is None:
        return None
    return RuleBundleRef(value.resource_id, value.version, value.content_hash)


def record_worker_heartbeat(settings: Settings) -> None:
    with connect(settings) as connection:
        connection.execute(
            "INSERT INTO runtime_component_heartbeat (component, observed_at) "
            "VALUES ('worker', CURRENT_TIMESTAMP) "
            "ON CONFLICT (component) DO UPDATE "
            "SET observed_at = EXCLUDED.observed_at"
        )


def worker_is_fresh(settings: Settings) -> bool:
    if not check_readiness(settings).ready:
        return False
    try:
        with connect(settings) as connection:
            row = connection.execute(
                "SELECT observed_at FROM runtime_component_heartbeat "
                "WHERE component = 'worker'"
            ).fetchone()
    except psycopg.Error:
        return False
    if not row:
        return False
    observed_at = row[0]
    if not isinstance(observed_at, datetime):
        return False
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    return observed_at >= datetime.now(UTC) - timedelta(
        seconds=settings.worker_stale_after_seconds
    )
