"""Database-backed runtime health and bootstrap operations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Never, cast
from uuid import uuid4

import psycopg
from psycopg import Connection

from tradesieve.adapters.local_raw_object_store import LocalImmutableRawObjectStore
from tradesieve.adapters.postgres_authorization import PostgresAuthorizationAuditSink
from tradesieve.adapters.postgres_demo_source_bootstrap import (
    DemoSourcePreflightState,
    prepare_demo_source_bootstrap,
)
from tradesieve.adapters.postgres_rule_bundle import PostgresRuleBundleRepository
from tradesieve.adapters.postgres_screening_submission import (
    PostgresScreeningSubmissionUnitOfWork,
)
from tradesieve.adapters.postgres_source_registry import PostgresSourceRegistry
from tradesieve.adapters.postgres_source_snapshot import (
    PostgresSourceSnapshotRepository,
)
from tradesieve.adapters.synthetic_source_parser import SyntheticJsonSourceParser
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
from tradesieve.application.screening_submission import (
    ScreeningSubmissionService,
    ScreeningSubmissionServiceResult,
)
from tradesieve.application.source_registry import (
    SourceRegistryService,
    readiness_check_value,
)
from tradesieve.application.source_snapshot import (
    SourceSnapshotService,
)
from tradesieve.application.source_snapshot_query import SourceSnapshotQueryService
from tradesieve.application.source_snapshot_readiness import (
    SourceSnapshotReadinessService,
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
from tradesieve.demo_screening_submission import (
    DemoScreeningEntitlementResolver,
    DemoScreeningFixture,
    synthetic_demo_screening_intake,
)
from tradesieve.demo_source_snapshot import (
    DEMO_SOURCE_ACTIVATION_REASON,
    DEMO_SOURCE_APPROVAL_REASON,
    DEMO_SOURCE_APPROVER,
    DEMO_SOURCE_ID,
    DEMO_SOURCE_OPERATOR,
    DemoSourceActor,
    DemoSourceEntitlementResolver,
    DemoSourceFixture,
    authorize_demo_source_request,
    demo_snapshot_identity,
    demo_source_registration,
    synthetic_demo_source_fixtures,
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
from tradesieve.domain.source_snapshot import (
    MAX_QUERY_LIMIT,
    LifecycleActorType,
    ParsedSourceSnapshot,
    RawObjectMetadata,
    SourceSnapshotEventType,
    SourceSnapshotLifecycle,
    SourceSnapshotLifecycleEvent,
    SourceSnapshotRef,
    SourceSnapshotState,
    ValidationReport,
)
from tradesieve.ports.source_snapshot import (
    SYNTHETIC_CHARSET,
    SYNTHETIC_MEDIA_TYPE,
    FiniteSourceParser,
    ImmutableRawObjectStore,
    SourceSnapshotPersistenceError,
    SourceSnapshotRepository,
)

MIGRATION_REVISION = "20260810_0006"


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


def connect_screening(settings: Settings) -> Connection[Any]:
    """Open one non-autocommit connection owned by a screening UoW operation."""

    return psycopg.connect(
        settings.database_url,
        connect_timeout=settings.database_connect_timeout_seconds,
        autocommit=False,
    )


def submit_demo_screening(
    settings: Settings,
    idempotency_key: object,
    fixture: DemoScreeningFixture,
    *,
    clock: Callable[[], datetime] | None = None,
    id_factory: Callable[[str], str] | None = None,
) -> ScreeningSubmissionServiceResult:
    """Submit one synthetic intake through real authorization and PostgreSQL UoW."""

    if settings.mode != "demo":
        raise RuntimeError("synthetic screening requires explicit demo mode")
    attempt_time = (clock or (lambda: datetime.now(UTC)))()
    intake = synthetic_demo_screening_intake(settings, fixture, now=attempt_time)
    allocate = id_factory or (lambda prefix: f"{prefix}-{uuid4().hex}")
    unit_of_work = PostgresScreeningSubmissionUnitOfWork(
        lambda: connect_screening(settings)
    )
    with connect(settings) as authorization_connection:
        authorization = AuthorizationService(
            PostgresAuthorizationAuditSink(authorization_connection),
            DemoScreeningEntitlementResolver(settings),
            event_id_factory=lambda: allocate("authz"),
        )
        service = ScreeningSubmissionService(
            authorization,
            unit_of_work,
            clock=lambda: attempt_time,
            attempt_id_factory=lambda: allocate("attempt"),
            intake_id_factory=lambda: allocate("intake"),
            screening_id_factory=lambda: allocate("screening"),
            outbox_id_factory=lambda: allocate("outbox"),
        )
        return service.submit(intake, idempotency_key)


def build_source_snapshot_readiness_service(
    settings: Settings,
    connection: Connection[Any],
    *,
    clock: Callable[[], datetime] | None = None,
) -> SourceSnapshotReadinessService:
    """Build the byte-level source evidence proof over durable read adapters."""

    return SourceSnapshotReadinessService(
        PostgresSourceRegistry(connection),
        PostgresSourceSnapshotRepository(connection),
        LocalImmutableRawObjectStore(settings.raw_object_root),
        deployment_id=settings.deployment_id,
        source_set_id=settings.required_source_set,
        clock=clock,
    )


def check_readiness(settings: Settings) -> RuntimeStatus:
    checks = {
        "database": "UNAVAILABLE",
        "migration": "NOT_APPLIED",
        "required_source_coverage": "UNAVAILABLE",
        "required_source_snapshot_evidence": "UNAVAILABLE",
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
            now = datetime.now(UTC)
            source_readiness = SourceRegistryService(
                PostgresSourceRegistry(connection),
                clock=lambda: now,
            ).required_set_readiness(
                settings.deployment_id, settings.required_source_set
            )
            snapshot_ready = build_source_snapshot_readiness_service(
                settings,
                connection,
                clock=lambda: now,
            ).is_ready()
            rule_ready = RuleBundleReadinessService(
                PostgresRuleBundleRepository(connection),
                deployment_id=settings.deployment_id,
                rule_set_id=settings.required_rule_set,
            ).is_current_active_ready(settings.rule_bundle_tenant_id)
        except (
            psycopg.Error,
            SourceSnapshotPersistenceError,
            TypeError,
            ValueError,
        ):
            return RuntimeStatus(ready=False, checks=checks)

    checks["required_source_coverage"] = readiness_check_value(source_readiness)
    if snapshot_ready:
        checks["required_source_snapshot_evidence"] = "OK"
    if rule_ready:
        checks["required_rule_coverage"] = "OK"
    ready = all(value == "OK" for value in checks.values())
    return RuntimeStatus(ready=ready, checks=checks)


def bootstrap_demo(settings: Settings) -> None:
    if settings.mode != "demo" or not settings.demo_bootstrap_enabled:
        raise RuntimeError("demo bootstrap is disabled outside explicit demo mode")
    with connect(settings) as connection:
        registration = demo_source_registration(settings)
        preflight = prepare_demo_source_bootstrap(
            connection,
            registration=registration,
        )
        if preflight is DemoSourcePreflightState.EMPTY:
            registry = SourceRegistryService(PostgresSourceRegistry(connection))
            registry.define_required_sources(
                settings.deployment_id,
                settings.required_source_set,
                (DEMO_SOURCE_ID,),
            )
            registry.register(registration)
        _bootstrap_demo_source_snapshots(settings, connection)
        now = datetime.now(UTC)
        _bootstrap_demo_rule_bundle(settings, connection, now)


@dataclass(frozen=True, slots=True)
class DemoSourceServices:
    deployment_id: str
    fixtures: tuple[DemoSourceFixture, ...]
    repository: SourceSnapshotRepository
    object_store: ImmutableRawObjectStore
    parser: FiniteSourceParser
    service: SourceSnapshotService
    query: SourceSnapshotQueryService
    authorization: AuthorizationService


def build_demo_source_services(
    settings: Settings,
    connection: Connection[Any],
    *,
    clock: Callable[[], datetime] | None = None,
) -> DemoSourceServices:
    """Build the explicit demo source graph over the real durable adapters."""

    fixtures = synthetic_demo_source_fixtures(settings)
    repository = PostgresSourceSnapshotRepository(connection)
    object_store = LocalImmutableRawObjectStore(settings.raw_object_root)
    parser = SyntheticJsonSourceParser()
    registry = PostgresSourceRegistry(connection)
    service = SourceSnapshotService(
        repository,
        object_store,
        registry,
        parser,
        deployment_id=settings.deployment_id,
        source_set_id=settings.required_source_set,
        control_tenant_id=settings.rule_bundle_tenant_id,
        clock=clock,
    )
    query = SourceSnapshotQueryService(
        repository,
        registry,
        deployment_id=settings.deployment_id,
        source_set_id=settings.required_source_set,
        control_tenant_id=settings.rule_bundle_tenant_id,
    )
    authorization = AuthorizationService(
        PostgresAuthorizationAuditSink(connection),
        DemoSourceEntitlementResolver(settings, repository, fixtures),
    )
    return DemoSourceServices(
        settings.deployment_id,
        fixtures,
        repository,
        object_store,
        parser,
        service,
        query,
        authorization,
    )


@dataclass(frozen=True, slots=True)
class _DemoSourceStage:
    event_count: int
    events: tuple[SourceSnapshotLifecycleEvent, ...]
    lifecycle: SourceSnapshotLifecycle
    metadata: tuple[RawObjectMetadata | None, RawObjectMetadata | None]
    snapshots: tuple[ParsedSourceSnapshot | None, ParsedSourceSnapshot | None]


def _bootstrap_demo_source_snapshots(
    settings: Settings,
    connection: Connection[Any],
) -> None:
    graph = build_demo_source_services(settings, connection)
    discovery_time = datetime.now(UTC)
    stage = _demo_source_stage(graph, now=discovery_time)
    read = authorize_demo_source_request(
        settings,
        graph.authorization,
        actor=DemoSourceActor.READER,
        operation=Operation.SOURCE_READ,
        now=discovery_time,
    )
    listing = graph.query.list_snapshots(read, source_id=DEMO_SOURCE_ID)
    history = graph.query.history(read, source_id=DEMO_SOURCE_ID)
    if (
        len(listing.snapshots) != sum(item is not None for item in stage.snapshots)
        or len(history.events) != stage.event_count
    ):
        _demo_source_unavailable()

    retrieval_time = discovery_time
    while stage.event_count < 12:
        fixture_index, offset = divmod(stage.event_count, 6)
        fixture = graph.fixtures[fixture_index]
        metadata = stage.metadata[fixture_index]
        snapshot = stage.snapshots[fixture_index]
        now = datetime.now(UTC)
        if offset in {0, 1}:
            ingest = authorize_demo_source_request(
                settings,
                graph.authorization,
                actor=DemoSourceActor.OPERATOR,
                operation=Operation.SOURCE_SNAPSHOT_INGEST,
                now=now,
            )
            graph.service.ingest(
                ingest,
                source_id=DEMO_SOURCE_ID,
                original_name=fixture.original_name,
                media_type=SYNTHETIC_MEDIA_TYPE,
                charset=SYNTHETIC_CHARSET,
                retrieved_at=(
                    metadata.retrieved_at if metadata is not None else retrieval_time
                ),
                effective_from=fixture.effective_from,
                content=fixture.content,
            )
        elif offset == 2:
            metadata = cast(RawObjectMetadata, metadata)
            parse = authorize_demo_source_request(
                settings,
                graph.authorization,
                actor=DemoSourceActor.OPERATOR,
                operation=Operation.SOURCE_SNAPSHOT_PARSE,
                now=now,
            )
            graph.service.parse(
                parse,
                source_id=DEMO_SOURCE_ID,
                object_id=metadata.object_id,
            )
        elif offset == 3:
            snapshot = cast(ParsedSourceSnapshot, snapshot)
            validate = authorize_demo_source_request(
                settings,
                graph.authorization,
                actor=DemoSourceActor.OPERATOR,
                operation=Operation.SOURCE_SNAPSHOT_VALIDATE,
                now=now,
            )
            result = graph.service.validate(
                validate,
                source_id=DEMO_SOURCE_ID,
                snapshot_id=snapshot.snapshot_id,
            )
            if not result.passed:
                _demo_source_unavailable()
        else:
            assert offset in {4, 5}
            snapshot = cast(ParsedSourceSnapshot, snapshot)
            identity = demo_snapshot_identity(snapshot)
            operation = (
                Operation.SOURCE_SNAPSHOT_APPROVE
                if offset == 4
                else Operation.SOURCE_SNAPSHOT_ACTIVATE
            )
            authorized = authorize_demo_source_request(
                settings,
                graph.authorization,
                actor=DemoSourceActor.APPROVER,
                operation=operation,
                now=now,
                identity=identity,
            )
            if offset == 4:
                graph.service.approve(
                    authorized,
                    source_id=DEMO_SOURCE_ID,
                    identity=identity,
                    reason=DEMO_SOURCE_APPROVAL_REASON,
                )
            else:
                graph.service.activate(
                    authorized,
                    source_id=DEMO_SOURCE_ID,
                    identity=identity,
                    reason=DEMO_SOURCE_ACTIVATION_REASON,
                )
        updated = _demo_source_stage(graph, now=datetime.now(UTC))
        if updated.event_count <= stage.event_count:
            _demo_source_unavailable()
        stage = updated


def _demo_source_stage(
    graph: DemoSourceServices,
    *,
    now: datetime,
) -> _DemoSourceStage:
    try:
        return _checked_demo_source_stage(graph, now=now)
    except Exception:
        _demo_source_unavailable()


def _checked_demo_source_stage(
    graph: DemoSourceServices,
    *,
    now: datetime,
) -> _DemoSourceStage:
    events, lifecycle = graph.repository.get_lifecycle_snapshot(
        graph.deployment_id,
        DEMO_SOURCE_ID,
    )
    if len(events) > MAX_QUERY_LIMIT:
        _demo_source_unavailable()
    base_events = events[:12]
    rollback_tail = events[12:]
    expected_types = (
        SourceSnapshotEventType.RETRIEVED,
        SourceSnapshotEventType.QUARANTINED,
        SourceSnapshotEventType.PARSED,
        SourceSnapshotEventType.VALIDATED,
        SourceSnapshotEventType.APPROVED,
        SourceSnapshotEventType.ACTIVATED,
    ) * 2
    metadata: list[RawObjectMetadata | None] = [None, None]
    snapshots: list[ParsedSourceSnapshot | None] = [None, None]
    for position, event in enumerate(base_events):
        fixture_index, offset = divmod(position, 6)
        fixture = graph.fixtures[fixture_index]
        if event.event_type is not expected_types[position]:
            _demo_source_unavailable()
        expected_actor = DEMO_SOURCE_OPERATOR if offset < 4 else DEMO_SOURCE_APPROVER
        expected_actor_type = (
            LifecycleActorType.SERVICE if offset < 4 else LifecycleActorType.HUMAN
        )
        expected_reason = (
            "source object retrieved",
            "source object quarantined",
            "source object parsed",
            "source validation passed",
            DEMO_SOURCE_APPROVAL_REASON,
            DEMO_SOURCE_ACTIVATION_REASON,
        )[offset]
        event_facts = (
            event.actor_id,
            event.actor_type,
            event.reason,
            event.raw_object.content_hash,
        )
        expected_event_facts = (
            expected_actor,
            expected_actor_type,
            expected_reason,
            fixture.content_hash,
        )
        if event_facts != expected_event_facts or event.occurred_at > now:
            _demo_source_unavailable()
        if metadata[fixture_index] is None:
            item = graph.repository.get_raw_metadata(
                graph.deployment_id,
                DEMO_SOURCE_ID,
                event.raw_object.object_id,
            )
            if item is None:
                _demo_source_unavailable()
            metadata[fixture_index] = item
        item = metadata[fixture_index]
        assert item is not None
        metadata_facts = (
            item.reference(),
            item.original_name,
            item.media_type,
            item.charset,
            item.effective_from,
        )
        expected_metadata_facts = (
            event.raw_object,
            fixture.original_name,
            SYNTHETIC_MEDIA_TYPE,
            SYNTHETIC_CHARSET,
            fixture.effective_from,
        )
        if (
            metadata_facts != expected_metadata_facts
            or item.retrieved_at > now
            or graph.object_store.get_verified(item.reference()) != fixture.content
        ):
            _demo_source_unavailable()
        if offset < 2:
            continue
        if snapshots[fixture_index] is None:
            snapshot_reference = cast(SourceSnapshotRef, event.snapshot)
            parsed = graph.repository.get_snapshot(
                graph.deployment_id,
                DEMO_SOURCE_ID,
                snapshot_reference.snapshot_id,
            )
            if parsed is None:
                _demo_source_unavailable()
            output = graph.parser.parse(fixture.content)
            expected = ParsedSourceSnapshot.create(
                raw_metadata=item,
                parser_id=graph.parser.parser_id,
                parser_version=graph.parser.parser_version,
                schema_id=output.schema_id,
                declared_record_count=output.declared_record_count,
                parsed_at=parsed.parsed_at,
                records=output.records,
            )
            if parsed != expected:
                _demo_source_unavailable()
            snapshots[fixture_index] = parsed
        parsed = snapshots[fixture_index]
        assert parsed is not None
        if event.snapshot != parsed.reference():
            _demo_source_unavailable()
        if offset == 3:
            report = cast(ValidationReport, event.validation_report)
            first_snapshot = snapshots[0]
            assert fixture_index == 0 or first_snapshot is not None
            previous = (
                first_snapshot.reference()
                if fixture_index == 1 and first_snapshot is not None
                else None
            )
            expected_added = (
                ("synthetic-entity-alpha",)
                if fixture_index == 0
                else ("synthetic-entity-beta",)
            )
            expected_changed = () if fixture_index == 0 else ("synthetic-entity-alpha",)
            report_facts = (
                report.passed,
                report.diff.previous_snapshot,
                report.diff.added_record_ids,
                report.diff.removed_record_ids,
                tuple(item.source_record_id for item in report.diff.changed_records),
            )
            expected_report_facts = (
                True,
                previous,
                expected_added,
                (),
                expected_changed,
            )
            if report_facts != expected_report_facts:
                _demo_source_unavailable()
    expected_snapshot_count = sum(
        1 for index in range(2) if len(base_events) >= index * 6 + 3
    )
    stored = graph.repository.list_snapshots(
        graph.deployment_id,
        DEMO_SOURCE_ID,
        limit=MAX_QUERY_LIMIT,
    )
    if len(stored) != expected_snapshot_count or set(stored) != {
        item for item in snapshots if item is not None
    }:
        _demo_source_unavailable()
    pointer_events = tuple(
        event
        for event in events
        if event.event_type
        in {
            SourceSnapshotEventType.ACTIVATED,
            SourceSnapshotEventType.ROLLED_BACK,
        }
    )
    expected_active = pointer_events[-1].snapshot if pointer_events else None
    if lifecycle.active_snapshot != expected_active:
        _demo_source_unavailable()
    if rollback_tail:
        _check_demo_rollback_tail(
            rollback_tail,
            snapshots=cast(
                tuple[ParsedSourceSnapshot, ParsedSourceSnapshot], tuple(snapshots)
            ),
            now=now,
        )
    if len(base_events) == 12:
        first = cast(ParsedSourceSnapshot, snapshots[0])
        second = cast(ParsedSourceSnapshot, snapshots[1])
        active = cast(SourceSnapshotRef, expected_active)
        inactive = (
            first.reference() if active == second.reference() else second.reference()
        )
        if lifecycle.state_for(
            active
        ) is not SourceSnapshotState.ACTIVE or lifecycle.state_for(inactive) not in {
            SourceSnapshotState.SUPERSEDED,
            SourceSnapshotState.ROLLED_BACK,
        }:
            _demo_source_unavailable()
    return _DemoSourceStage(
        len(events),
        events,
        lifecycle,
        (metadata[0], metadata[1]),
        (snapshots[0], snapshots[1]),
    )


def _check_demo_rollback_tail(
    events: tuple[SourceSnapshotLifecycleEvent, ...],
    *,
    snapshots: tuple[ParsedSourceSnapshot, ParsedSourceSnapshot],
    now: datetime,
) -> None:
    by_reference = {snapshot.reference(): snapshot for snapshot in snapshots}
    current = snapshots[1].reference()
    for event in events:
        target = event.snapshot
        if (
            event.event_type is not SourceSnapshotEventType.ROLLED_BACK
            or event.actor_id != DEMO_SOURCE_APPROVER
            or event.actor_type is not LifecycleActorType.HUMAN
            or target not in by_reference
            or event.previous_active_snapshot != current
            or target == current
            or event.raw_object != by_reference[target].raw_object
            or event.occurred_at > now
        ):
            _demo_source_unavailable()
        current = target


def _demo_source_unavailable() -> Never:
    raise RuntimeError("synthetic demo source bootstrap is unavailable") from None


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
