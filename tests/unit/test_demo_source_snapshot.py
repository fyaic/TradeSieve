"""D1 demo source graph, authorization, resume, and legacy preflight evidence."""

from __future__ import annotations

import copy
import json
from contextlib import nullcontext
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import psycopg
import pytest

from tradesieve import runtime
from tradesieve.adapters.in_memory_source_snapshot import (
    InMemorySourceSnapshotRepository,
)
from tradesieve.adapters.local_raw_object_store import LocalImmutableRawObjectStore
from tradesieve.adapters.postgres_demo_source_bootstrap import (
    DemoSourceBootstrapUnavailable,
    DemoSourcePreflightState,
    prepare_demo_source_bootstrap,
)
from tradesieve.adapters.postgres_source_snapshot import (
    PostgresSourceSnapshotRepository,
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
    Role,
    Scope,
    TargetObject,
)
from tradesieve.application.source_snapshot import (
    SOURCE_SNAPSHOT_TARGET_TYPE,
    SOURCE_TARGET_TYPE,
    ImmutableSourceSnapshotIdentity,
    SourceSnapshotService,
    source_authorization_target_id,
    source_snapshot_authorization_target_id,
)
from tradesieve.application.source_snapshot_query import SourceSnapshotQueryService
from tradesieve.config import Settings
from tradesieve.demo_source_snapshot import (
    DEMO_SOURCE_APPROVER,
    DEMO_SOURCE_EFFECTIVE_FROM,
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
from tradesieve.domain.source_registry import (
    ObservationWriteOutcome,
    SourceRegistration,
    SourceRuntimeObservation,
    SourceSetManifest,
)
from tradesieve.domain.source_snapshot import (
    ParsedSourceSnapshot,
    RawObjectMetadata,
    SourceSnapshotEventType,
    SourceSnapshotLifecycle,
    SourceSnapshotLifecycleEvent,
    SourceSnapshotState,
)


class Ids:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return f"{self.prefix}-{self.value}"


class Registry:
    def __init__(self, registration: SourceRegistration) -> None:
        self.registration = registration
        self.manifest = SourceSetManifest(
            registration.deployment_id,
            registration.source_set_id,
            (registration.source_id,),
        )

    def save_manifest(self, manifest: SourceSetManifest) -> None:
        self.manifest = manifest

    def get_manifest(
        self, deployment_id: str, source_set_id: str
    ) -> SourceSetManifest | None:
        return (
            self.manifest
            if (deployment_id, source_set_id)
            == (self.registration.deployment_id, self.registration.source_set_id)
            else None
        )

    def save_registration(self, registration: SourceRegistration) -> None:
        self.registration = registration

    def get_registration(
        self, deployment_id: str, source_id: str
    ) -> SourceRegistration | None:
        return (
            self.registration
            if (deployment_id, source_id)
            == (self.registration.deployment_id, self.registration.source_id)
            else None
        )

    def save_observation(
        self, observation: SourceRuntimeObservation
    ) -> ObservationWriteOutcome:
        del observation
        return ObservationWriteOutcome.APPLIED

    def list_entries(
        self, deployment_id: str, source_set_id: str, *, limit: int
    ) -> tuple[tuple[SourceRegistration, SourceRuntimeObservation | None], ...]:
        del limit
        if (deployment_id, source_set_id) != (
            self.registration.deployment_id,
            self.registration.source_set_id,
        ):
            return ()
        return ((self.registration, None),)


@dataclass
class GraphEnvironment:
    settings: Settings
    graph: runtime.DemoSourceServices
    repository: InMemorySourceSnapshotRepository
    audits: list[AuthorizationAuditEvent]


_NOT_OVERRIDDEN = object()


class RepositoryView:
    def __init__(
        self,
        repository: InMemorySourceSnapshotRepository,
        *,
        events: tuple[SourceSnapshotLifecycleEvent, ...] | None = None,
        lifecycle: SourceSnapshotLifecycle | None = None,
        metadata: RawObjectMetadata | None | object = _NOT_OVERRIDDEN,
        snapshot: ParsedSourceSnapshot | None | object = _NOT_OVERRIDDEN,
        stored: tuple[ParsedSourceSnapshot, ...] | None = None,
    ) -> None:
        self.repository = repository
        self.events = events
        self.lifecycle = lifecycle
        self.metadata = metadata
        self.snapshot = snapshot
        self.stored = stored

    def get_lifecycle_snapshot(
        self, deployment_id: str, source_id: str
    ) -> tuple[tuple[SourceSnapshotLifecycleEvent, ...], SourceSnapshotLifecycle]:
        events, lifecycle = self.repository.get_lifecycle_snapshot(
            deployment_id, source_id
        )
        return self.events or events, self.lifecycle or lifecycle

    def get_raw_metadata(
        self, deployment_id: str, source_id: str, object_id: str
    ) -> RawObjectMetadata | None:
        if self.metadata is not _NOT_OVERRIDDEN:
            return cast(RawObjectMetadata | None, self.metadata)
        return self.repository.get_raw_metadata(deployment_id, source_id, object_id)

    def get_snapshot(
        self, deployment_id: str, source_id: str, snapshot_id: str
    ) -> ParsedSourceSnapshot | None:
        if self.snapshot is not _NOT_OVERRIDDEN:
            return cast(ParsedSourceSnapshot | None, self.snapshot)
        return self.repository.get_snapshot(deployment_id, source_id, snapshot_id)

    def list_snapshots(
        self, deployment_id: str, source_id: str, *, limit: int
    ) -> tuple[ParsedSourceSnapshot, ...]:
        if self.stored is not None:
            return self.stored
        return self.repository.list_snapshots(deployment_id, source_id, limit=limit)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.repository, name)


def graph_environment(root: Path) -> GraphEnvironment:
    root.mkdir(mode=0o700)
    settings = Settings(raw_object_root=root)
    fixtures = synthetic_demo_source_fixtures(settings)
    registration = demo_source_registration(settings)
    registry = Registry(registration)
    repository = InMemorySourceSnapshotRepository()
    store = LocalImmutableRawObjectStore(root)
    parser = SyntheticJsonSourceParser()
    service = SourceSnapshotService(
        repository,
        store,
        registry,
        parser,
        deployment_id=settings.deployment_id,
        source_set_id=settings.required_source_set,
        control_tenant_id=settings.rule_bundle_tenant_id,
        event_id_factory=Ids("source-event"),
        command_id_factory=Ids("source-command"),
    )
    query = SourceSnapshotQueryService(
        repository,
        registry,
        deployment_id=settings.deployment_id,
        source_set_id=settings.required_source_set,
        control_tenant_id=settings.rule_bundle_tenant_id,
    )
    audits: list[AuthorizationAuditEvent] = []
    authorization = AuthorizationService(
        audits.append,
        DemoSourceEntitlementResolver(settings, repository, fixtures),
        event_id_factory=Ids("authz"),
    )
    graph = runtime.DemoSourceServices(
        settings.deployment_id,
        fixtures,
        repository,
        store,
        parser,
        service,
        query,
        authorization,
    )
    return GraphEnvironment(settings, graph, repository, audits)


def test_fixtures_are_stable_finite_obviously_synthetic_and_ordered() -> None:
    settings = Settings()
    first = synthetic_demo_source_fixtures(settings)
    second = synthetic_demo_source_fixtures(settings)
    assert first == second
    assert len({item.content_hash for item in first}) == 2
    parser = SyntheticJsonSourceParser()
    parsed = tuple(parser.parse(item.content) for item in first)
    assert [item.declared_record_count for item in parsed] == [1, 2]
    assert [record.source_record_id for record in parsed[1].records] == [
        "synthetic-entity-alpha",
        "synthetic-entity-beta",
    ]
    assert b"SYNTHETIC" in first[0].content
    with pytest.raises(RuntimeError, match="explicit demo mode"):
        synthetic_demo_source_fixtures(
            Settings(
                mode="production",
                database_url="postgresql://service:strong@db/tradesieve",  # pragma: allowlist secret
                demo_bootstrap_enabled=False,
                rule_bundle_tenant_id="tenant-1",
                deployment_id="production-1",
                required_source_set="approved-sources",
                required_rule_set="approved-rules",
            )
        )


@pytest.mark.parametrize(
    ("fixture_id", "original_name", "content", "effective_from"),
    [
        ("wrong", "synthetic-demo-source-v1.json", b"{}", DEMO_SOURCE_EFFECTIVE_FROM),
        ("synthetic-demo-source-v1", "wrong.json", b"{}", DEMO_SOURCE_EFFECTIVE_FROM),
        (
            "synthetic-demo-source-v1",
            "synthetic-demo-source-v1.txt",
            b"{}",
            DEMO_SOURCE_EFFECTIVE_FROM,
        ),
        (
            "synthetic-demo-source-v1",
            "synthetic-demo-source-v1.json",
            "{}",
            DEMO_SOURCE_EFFECTIVE_FROM,
        ),
        (
            "synthetic-demo-source-v1",
            "synthetic-demo-source-v1.json",
            b"",
            DEMO_SOURCE_EFFECTIVE_FROM,
        ),
        (
            "synthetic-demo-source-v1",
            "synthetic-demo-source-v1.json",
            b"{}",
            DEMO_SOURCE_EFFECTIVE_FROM.replace(tzinfo=None),
        ),
        (
            "synthetic-demo-source-v1",
            "synthetic-demo-source-v1.json",
            b"{}",
            DEMO_SOURCE_EFFECTIVE_FROM.astimezone(timezone(timedelta(hours=1))),
        ),
    ],
)
def test_fixture_rejects_every_non_synthetic_or_non_utc_boundary(
    fixture_id: str,
    original_name: str,
    content: object,
    effective_from: datetime,
) -> None:
    with pytest.raises(ValueError, match="synthetic demo source fixture is invalid"):
        DemoSourceFixture(
            fixture_id,
            original_name,
            cast(Any, content),
            effective_from,
        )


@pytest.mark.parametrize(
    "fixtures",
    [[], (), ("not-a-fixture", "not-a-fixture")],
)
def test_entitlement_resolver_requires_exactly_two_typed_fixtures(
    fixtures: object,
) -> None:
    with pytest.raises(ValueError, match="requires two fixtures"):
        DemoSourceEntitlementResolver(
            Settings(),
            InMemorySourceSnapshotRepository(),
            cast(Any, fixtures),
        )


def test_real_demo_graph_builder_uses_durable_adapters(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    root.mkdir(mode=0o700)
    settings = Settings(raw_object_root=root)
    graph = runtime.build_demo_source_services(settings, cast(Any, object()))
    assert graph.deployment_id == settings.deployment_id
    assert isinstance(graph.repository, PostgresSourceSnapshotRepository)
    assert isinstance(graph.object_store, LocalImmutableRawObjectStore)
    assert isinstance(graph.parser, SyntheticJsonSourceParser)
    assert isinstance(graph.service, SourceSnapshotService)
    assert isinstance(graph.query, SourceSnapshotQueryService)
    assert isinstance(graph.authorization, AuthorizationService)


def test_real_demo_graph_reaches_two_versions_and_full_repeat_is_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = graph_environment(tmp_path / "raw")
    monkeypatch.setattr(
        runtime,
        "build_demo_source_services",
        lambda settings, connection: environment.graph,
    )
    runtime._bootstrap_demo_source_snapshots(environment.settings, cast(Any, object()))
    events, lifecycle = environment.repository.get_lifecycle_snapshot(
        environment.settings.deployment_id, DEMO_SOURCE_ID
    )
    audits = environment.repository.list_command_audits(
        environment.settings.deployment_id, DEMO_SOURCE_ID, limit=100
    )
    assert [event.event_type for event in events] == [
        SourceSnapshotEventType.RETRIEVED,
        SourceSnapshotEventType.QUARANTINED,
        SourceSnapshotEventType.PARSED,
        SourceSnapshotEventType.VALIDATED,
        SourceSnapshotEventType.APPROVED,
        SourceSnapshotEventType.ACTIVATED,
    ] * 2
    assert len(audits) == 12
    assert len(environment.audits) == 11
    snapshots = environment.repository.list_snapshots(
        environment.settings.deployment_id, DEMO_SOURCE_ID, limit=100
    )
    assert len(snapshots) == 2
    active = next(
        item for item in snapshots if item.reference() == lifecycle.active_snapshot
    )
    inactive = next(item for item in snapshots if item is not active)
    assert lifecycle.state_for(active.reference()) is SourceSnapshotState.ACTIVE
    assert lifecycle.state_for(inactive.reference()) is SourceSnapshotState.SUPERSEDED
    validation = tuple(
        event.validation_report
        for event in events
        if event.event_type is SourceSnapshotEventType.VALIDATED
    )
    assert validation[0] is not None and validation[1] is not None
    assert validation[0].diff.added_record_ids == ("synthetic-entity-alpha",)
    assert validation[1].diff.added_record_ids == ("synthetic-entity-beta",)
    assert validation[1].diff.removed_record_ids == ()
    assert [item.source_record_id for item in validation[1].diff.changed_records] == [
        "synthetic-entity-alpha"
    ]
    before = (events, audits, snapshots)
    runtime._bootstrap_demo_source_snapshots(environment.settings, cast(Any, object()))
    assert len(environment.audits) == 12
    assert before == (
        environment.repository.get_lifecycle_snapshot(
            environment.settings.deployment_id, DEMO_SOURCE_ID
        )[0],
        environment.repository.list_command_audits(
            environment.settings.deployment_id, DEMO_SOURCE_ID, limit=100
        ),
        environment.repository.list_snapshots(
            environment.settings.deployment_id, DEMO_SOURCE_ID, limit=100
        ),
    )


def test_partial_quarantined_stage_resumes_without_replacing_raw_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = graph_environment(tmp_path / "raw")
    fixture = environment.graph.fixtures[0]
    now = datetime.now(UTC)
    authorized = authorize_demo_source_request(
        environment.settings,
        environment.graph.authorization,
        actor=DemoSourceActor.OPERATOR,
        operation=Operation.SOURCE_SNAPSHOT_INGEST,
        now=now,
    )
    raw = environment.graph.service.ingest(
        authorized,
        source_id=DEMO_SOURCE_ID,
        original_name=fixture.original_name,
        media_type=environment.graph.parser.media_type,
        charset=environment.graph.parser.charset,
        retrieved_at=now - timedelta(seconds=1),
        effective_from=fixture.effective_from,
        content=fixture.content,
    )
    monkeypatch.setattr(
        runtime,
        "build_demo_source_services",
        lambda settings, connection: environment.graph,
    )
    runtime._bootstrap_demo_source_snapshots(environment.settings, cast(Any, object()))
    events, lifecycle = environment.repository.get_lifecycle_snapshot(
        environment.settings.deployment_id, DEMO_SOURCE_ID
    )
    assert len(events) == 12
    assert events[0].raw_object.object_id == raw.object_id
    assert lifecycle.active_snapshot is not None


@pytest.mark.parametrize("mismatch", ["listing", "history"])
def test_bootstrap_rejects_query_views_inconsistent_with_verified_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
) -> None:
    environment = graph_environment(tmp_path / "raw")
    monkeypatch.setattr(
        runtime,
        "build_demo_source_services",
        lambda settings, connection: environment.graph,
    )
    runtime._bootstrap_demo_source_snapshots(environment.settings, cast(Any, object()))

    class QueryView:
        def list_snapshots(
            self, authorized: AuthorizedRequest, *, source_id: str
        ) -> object:
            if mismatch == "listing":
                return SimpleNamespace(snapshots=[])
            return environment.graph.query.list_snapshots(
                authorized, source_id=source_id
            )

        def history(self, authorized: AuthorizedRequest, *, source_id: str) -> object:
            if mismatch == "history":
                return SimpleNamespace(events=[])
            return environment.graph.query.history(authorized, source_id=source_id)

    corrupt_graph = replace(environment.graph, query=cast(Any, QueryView()))
    monkeypatch.setattr(
        runtime,
        "build_demo_source_services",
        lambda settings, connection: corrupt_graph,
    )
    with pytest.raises(
        RuntimeError, match="synthetic demo source bootstrap is unavailable"
    ):
        runtime._bootstrap_demo_source_snapshots(
            environment.settings, cast(Any, object())
        )


def test_bootstrap_fails_closed_if_fixture_validation_does_not_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = graph_environment(tmp_path / "raw")
    fixture = environment.graph.fixtures[0]
    now = datetime.now(UTC)
    ingest = authorize_demo_source_request(
        environment.settings,
        environment.graph.authorization,
        actor=DemoSourceActor.OPERATOR,
        operation=Operation.SOURCE_SNAPSHOT_INGEST,
        now=now,
    )
    raw = environment.graph.service.ingest(
        ingest,
        source_id=DEMO_SOURCE_ID,
        original_name=fixture.original_name,
        media_type=environment.graph.parser.media_type,
        charset=environment.graph.parser.charset,
        retrieved_at=now,
        effective_from=fixture.effective_from,
        content=fixture.content,
    )
    parse = authorize_demo_source_request(
        environment.settings,
        environment.graph.authorization,
        actor=DemoSourceActor.OPERATOR,
        operation=Operation.SOURCE_SNAPSHOT_PARSE,
        now=now,
    )
    environment.graph.service.parse(
        parse,
        source_id=DEMO_SOURCE_ID,
        object_id=raw.object_id,
    )

    class ServiceView:
        def validate(self, *args: object, **kwargs: object) -> object:
            return SimpleNamespace(passed=False)

        def __getattr__(self, name: str) -> Any:
            return getattr(environment.graph.service, name)

    graph = replace(environment.graph, service=cast(Any, ServiceView()))
    monkeypatch.setattr(
        runtime,
        "build_demo_source_services",
        lambda settings, connection: graph,
    )
    with pytest.raises(
        RuntimeError, match="synthetic demo source bootstrap is unavailable"
    ):
        runtime._bootstrap_demo_source_snapshots(
            environment.settings, cast(Any, object())
        )


def test_bootstrap_fails_closed_if_command_makes_no_verified_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = graph_environment(tmp_path / "raw")
    unchanged = runtime._checked_demo_source_stage(
        environment.graph, now=datetime.now(UTC)
    )
    monkeypatch.setattr(
        runtime,
        "build_demo_source_services",
        lambda settings, connection: environment.graph,
    )
    monkeypatch.setattr(
        runtime,
        "_demo_source_stage",
        lambda graph, now: unchanged,
    )
    with pytest.raises(
        RuntimeError, match="synthetic demo source bootstrap is unavailable"
    ):
        runtime._bootstrap_demo_source_snapshots(
            environment.settings, cast(Any, object())
        )
    assert (
        len(
            environment.repository.list_command_audits(
                environment.settings.deployment_id, DEMO_SOURCE_ID, limit=100
            )
        )
        == 2
    )


@pytest.mark.parametrize(
    "corruption",
    [
        "repository_failure",
        "too_many_events",
        "event_type",
        "event_facts",
        "future_event",
        "missing_metadata",
        "metadata_facts",
        "future_metadata",
        "raw_bytes",
        "missing_snapshot",
        "parsed_mismatch",
        "event_snapshot",
        "validation",
        "stored_count",
        "stored_set",
        "active",
        "states",
    ],
)
def test_bootstrap_rejects_corrupt_persisted_graph_before_authorization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    environment = graph_environment(tmp_path / "raw")
    monkeypatch.setattr(
        runtime,
        "build_demo_source_services",
        lambda settings, connection: environment.graph,
    )
    runtime._bootstrap_demo_source_snapshots(environment.settings, cast(Any, object()))
    events, lifecycle = environment.repository.get_lifecycle_snapshot(
        environment.settings.deployment_id, DEMO_SOURCE_ID
    )
    snapshots = environment.repository.list_snapshots(
        environment.settings.deployment_id, DEMO_SOURCE_ID, limit=100
    )
    first_snapshot = environment.repository.get_snapshot(
        environment.settings.deployment_id,
        DEMO_SOURCE_ID,
        cast(Any, events[2].snapshot).snapshot_id,
    )
    second_snapshot = environment.repository.get_snapshot(
        environment.settings.deployment_id,
        DEMO_SOURCE_ID,
        cast(Any, events[8].snapshot).snapshot_id,
    )
    first_metadata = environment.repository.get_raw_metadata(
        environment.settings.deployment_id,
        DEMO_SOURCE_ID,
        events[0].raw_object.object_id,
    )
    assert first_snapshot is not None
    assert second_snapshot is not None
    assert first_metadata is not None
    repository: object = RepositoryView(environment.repository)
    object_store: object = environment.graph.object_store

    if corruption == "repository_failure":

        class FailingRepository:
            def get_lifecycle_snapshot(self, *args: object) -> object:
                raise ValueError("private persisted state")

        repository = FailingRepository()
    elif corruption == "too_many_events":
        repository = RepositoryView(
            environment.repository, events=events + (events[-1],)
        )
    elif corruption in {"event_type", "event_facts", "future_event"}:
        changed = list(events)
        if corruption == "event_type":
            changed[0] = replace(
                changed[0], event_type=SourceSnapshotEventType.QUARANTINED
            )
        elif corruption == "event_facts":
            changed[0] = replace(changed[0], actor_id="unexpected-actor")
        else:
            changed[0] = replace(
                changed[0], occurred_at=datetime.now(UTC) + timedelta(days=1)
            )
        repository = RepositoryView(environment.repository, events=tuple(changed))
    elif corruption == "missing_metadata":
        repository = RepositoryView(environment.repository, metadata=None)
    elif corruption in {"metadata_facts", "future_metadata"}:
        metadata = copy.deepcopy(first_metadata)
        if corruption == "metadata_facts":
            object.__setattr__(metadata, "original_name", "unexpected.json")
        else:
            object.__setattr__(
                metadata, "retrieved_at", datetime.now(UTC) + timedelta(days=1)
            )
        repository = RepositoryView(environment.repository, metadata=metadata)
    elif corruption == "raw_bytes":

        class ObjectStoreView:
            def get_verified(self, reference: object) -> bytes:
                del reference
                return b"corrupt synthetic bytes"

        object_store = ObjectStoreView()
    elif corruption == "missing_snapshot":
        repository = RepositoryView(environment.repository, snapshot=None)
    elif corruption == "parsed_mismatch":
        parsed = copy.deepcopy(first_snapshot)
        object.__setattr__(parsed, "schema_id", "unexpected-schema")
        repository = RepositoryView(environment.repository, snapshot=parsed)
    elif corruption == "event_snapshot":
        changed = list(events)
        event = copy.deepcopy(changed[4])
        object.__setattr__(event, "snapshot", second_snapshot.reference())
        changed[4] = event
        repository = RepositoryView(environment.repository, events=tuple(changed))
    elif corruption == "validation":
        changed = list(events)
        event = copy.deepcopy(changed[3])
        report = copy.deepcopy(event.validation_report)
        assert report is not None
        diff = copy.deepcopy(report.diff)
        object.__setattr__(diff, "added_record_ids", ("unexpected-record",))
        object.__setattr__(report, "diff", diff)
        object.__setattr__(event, "validation_report", report)
        changed[3] = event
        repository = RepositoryView(environment.repository, events=tuple(changed))
    elif corruption == "stored_count":
        repository = RepositoryView(environment.repository, stored=())
    elif corruption == "stored_set":
        repository = RepositoryView(
            environment.repository,
            stored=(snapshots[0], snapshots[0]),
        )
    elif corruption == "active":
        changed_lifecycle = copy.deepcopy(lifecycle)
        object.__setattr__(changed_lifecycle, "active_snapshot", None)
        repository = RepositoryView(environment.repository, lifecycle=changed_lifecycle)
    else:
        changed_lifecycle = copy.deepcopy(lifecycle)
        state_records = list(changed_lifecycle.snapshots)
        first_record = next(
            item
            for item in state_records
            if item.snapshot == first_snapshot.reference()
        )
        object.__setattr__(first_record, "state", SourceSnapshotState.APPROVED)
        object.__setattr__(changed_lifecycle, "snapshots", tuple(state_records))
        repository = RepositoryView(environment.repository, lifecycle=changed_lifecycle)

    graph = replace(
        environment.graph,
        repository=cast(Any, repository),
        object_store=cast(Any, object_store),
    )
    monkeypatch.setattr(
        runtime,
        "build_demo_source_services",
        lambda settings, connection: graph,
    )
    audit_count = len(environment.audits)
    with pytest.raises(RuntimeError) as exc_info:
        runtime._bootstrap_demo_source_snapshots(
            environment.settings, cast(Any, object())
        )
    assert str(exc_info.value) == "synthetic demo source bootstrap is unavailable"
    assert "private" not in str(exc_info.value)
    assert len(environment.audits) == audit_count


def test_authorization_denies_wrong_actor_scope_and_target(
    tmp_path: Path,
) -> None:
    environment = graph_environment(tmp_path / "raw")
    now = datetime.now(UTC)
    with pytest.raises(AuthorizationDenied):
        authorize_demo_source_request(
            environment.settings,
            environment.graph.authorization,
            actor=DemoSourceActor.READER,
            operation=Operation.SOURCE_SNAPSHOT_INGEST,
            now=now,
        )
    assert str(environment.audits[-1].reason) == AuthorizationReason.MISSING_SCOPE
    missing_identity = ImmutableSourceSnapshotIdentity(
        snapshot_id=f"snapshot-{'0' * 64}",
        content_hash=f"sha256:{'0' * 64}",
    )
    with pytest.raises(AuthorizationDenied):
        authorize_demo_source_request(
            environment.settings,
            environment.graph.authorization,
            actor=DemoSourceActor.APPROVER,
            operation=Operation.SOURCE_SNAPSHOT_APPROVE,
            now=now,
            identity=missing_identity,
        )
    assert str(environment.audits[-1].reason) == AuthorizationReason.OBJECT_NOT_VISIBLE


def test_alternate_approver_is_denied_before_repository_read() -> None:
    settings = Settings()
    fixtures = synthetic_demo_source_fixtures(settings)

    class RepositoryMustNotBeRead:
        def __getattr__(self, name: str) -> object:
            pytest.fail(f"repository was read through {name}")

    audits: list[AuthorizationAuditEvent] = []
    authorization = AuthorizationService(
        audits.append,
        DemoSourceEntitlementResolver(
            settings,
            cast(Any, RepositoryMustNotBeRead()),
            fixtures,
        ),
        event_id_factory=Ids("alternate-approver-authz"),
    )
    now = datetime.now(UTC)
    alternate = ActorContext(
        subject="alternate-demo-source-approver",
        client_id="synthetic-allowlist-probe",
        tenant_id=settings.rule_bundle_tenant_id,
        actor_type=ActorType.HUMAN,
        scopes=frozenset({Scope.SOURCE_APPROVE}),
        roles=frozenset({Role.SOURCE_APPROVER}),
        issuer="http://localhost/tradesieve-demo",
        audience="tradesieve-demo",
        issued_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=1),
        demo_identity=True,
    )
    target_id = source_snapshot_authorization_target_id(
        settings.deployment_id,
        settings.required_source_set,
        DEMO_SOURCE_ID,
        f"snapshot-{'0' * 64}",
        f"sha256:{'0' * 64}",
    )
    with pytest.raises(AuthorizationDenied):
        authorization.require(
            AuthorizationRequest(
                RequestContext(
                    alternate,
                    settings.rule_bundle_tenant_id,
                    "synthetic-allowlist-probe",
                ),
                Operation.SOURCE_SNAPSHOT_APPROVE,
                TargetObject(
                    settings.rule_bundle_tenant_id,
                    SOURCE_SNAPSHOT_TARGET_TYPE,
                    target_id,
                ),
            ),
            now=now,
        )
    assert audits[-1].reason is AuthorizationReason.OBJECT_NOT_VISIBLE


@pytest.mark.parametrize(
    ("actor_tenant_id", "target_tenant_id", "operation", "target_type"),
    [
        ("wrong", "demo-tenant", Operation.SOURCE_READ, SOURCE_TARGET_TYPE),
        ("demo-tenant", "wrong", Operation.SOURCE_READ, SOURCE_TARGET_TYPE),
        (
            "demo-tenant",
            "demo-tenant",
            Operation.SOURCE_SNAPSHOT_INGEST,
            SOURCE_SNAPSHOT_TARGET_TYPE,
        ),
        (
            "demo-tenant",
            "demo-tenant",
            Operation.SOURCE_SNAPSHOT_APPROVE,
            SOURCE_TARGET_TYPE,
        ),
    ],
)
def test_entitlement_resolver_rejects_cross_tenant_operation_and_target_mismatch(
    actor_tenant_id: str,
    target_tenant_id: str,
    operation: str,
    target_type: str,
) -> None:
    settings = Settings()
    resolver = DemoSourceEntitlementResolver(
        settings,
        InMemorySourceSnapshotRepository(),
        synthetic_demo_source_fixtures(settings),
    )
    target_id = source_authorization_target_id(
        settings.deployment_id,
        settings.required_source_set,
        DEMO_SOURCE_ID,
    )
    actor_subject = (
        DEMO_SOURCE_APPROVER
        if operation is Operation.SOURCE_SNAPSHOT_APPROVE
        else DEMO_SOURCE_OPERATOR
    )
    assert (
        resolver.resolve(
            actor_subject=actor_subject,
            actor_tenant_id=actor_tenant_id,
            operation=operation,
            target_tenant_id=target_tenant_id,
            target_type=target_type,
            target_id=target_id,
        )
        is None
    )


@pytest.mark.parametrize(
    ("now", "operation", "identity"),
    [
        ("not-a-time", Operation.SOURCE_READ, None),
        (datetime(2026, 8, 6), Operation.SOURCE_READ, None),
        (datetime.now(UTC), Operation.SOURCE_SNAPSHOT_APPROVE, None),
        (
            datetime.now(UTC),
            Operation.SOURCE_READ,
            ImmutableSourceSnapshotIdentity(
                snapshot_id=f"snapshot-{'0' * 64}",
                content_hash=f"sha256:{'0' * 64}",
            ),
        ),
    ],
)
def test_demo_authorization_requires_aware_time_and_exact_governance_identity(
    tmp_path: Path,
    now: object,
    operation: Operation,
    identity: ImmutableSourceSnapshotIdentity | None,
) -> None:
    environment = graph_environment(tmp_path / "raw")
    with pytest.raises(ValueError):
        authorize_demo_source_request(
            environment.settings,
            environment.graph.authorization,
            actor=DemoSourceActor.APPROVER,
            operation=operation,
            now=cast(Any, now),
            identity=identity,
        )
    assert environment.audits == []


def test_entitlement_resolver_rejects_snapshot_with_unexpected_creator(
    tmp_path: Path,
) -> None:
    environment = graph_environment(tmp_path / "raw")
    fixture = environment.graph.fixtures[0]
    now = datetime.now(UTC)
    ingest = authorize_demo_source_request(
        environment.settings,
        environment.graph.authorization,
        actor=DemoSourceActor.OPERATOR,
        operation=Operation.SOURCE_SNAPSHOT_INGEST,
        now=now,
    )
    raw = environment.graph.service.ingest(
        ingest,
        source_id=DEMO_SOURCE_ID,
        original_name=fixture.original_name,
        media_type=environment.graph.parser.media_type,
        charset=environment.graph.parser.charset,
        retrieved_at=now,
        effective_from=fixture.effective_from,
        content=fixture.content,
    )
    parse = authorize_demo_source_request(
        environment.settings,
        environment.graph.authorization,
        actor=DemoSourceActor.OPERATOR,
        operation=Operation.SOURCE_SNAPSHOT_PARSE,
        now=now,
    )
    parsed = environment.graph.service.parse(
        parse,
        source_id=DEMO_SOURCE_ID,
        object_id=raw.object_id,
    )
    snapshot = environment.repository.get_snapshot(
        environment.settings.deployment_id,
        DEMO_SOURCE_ID,
        parsed.snapshot_id,
    )
    assert isinstance(snapshot, ParsedSourceSnapshot)

    class WrongCreatorLifecycle:
        def creator_for(self, reference: object) -> str:
            del reference
            return "unexpected-creator"

    class RepositoryView:
        def list_snapshots(
            self, deployment_id: str, source_id: str, *, limit: int
        ) -> tuple[ParsedSourceSnapshot, ...]:
            return environment.repository.list_snapshots(
                deployment_id, source_id, limit=limit
            )

        def get_lifecycle_snapshot(
            self, deployment_id: str, source_id: str
        ) -> tuple[tuple[object, ...], object]:
            events, _ = environment.repository.get_lifecycle_snapshot(
                deployment_id, source_id
            )
            return events, WrongCreatorLifecycle()

    resolver = DemoSourceEntitlementResolver(
        environment.settings,
        environment.repository,
        environment.graph.fixtures,
    )
    identity = demo_snapshot_identity(snapshot)
    target_id = source_snapshot_authorization_target_id(
        environment.settings.deployment_id,
        environment.settings.required_source_set,
        DEMO_SOURCE_ID,
        identity.snapshot_id,
        identity.content_hash,
    )
    facts = resolver.resolve(
        actor_subject=DEMO_SOURCE_APPROVER,
        actor_tenant_id=environment.settings.rule_bundle_tenant_id,
        operation=Operation.SOURCE_SNAPSHOT_APPROVE,
        target_tenant_id=environment.settings.rule_bundle_tenant_id,
        target_type=SOURCE_SNAPSHOT_TARGET_TYPE,
        target_id=target_id,
    )
    assert facts is not None
    assert facts.author_actor_id == DEMO_SOURCE_OPERATOR

    resolver = DemoSourceEntitlementResolver(
        environment.settings,
        cast(Any, RepositoryView()),
        environment.graph.fixtures,
    )
    assert (
        resolver.resolve(
            actor_subject=DEMO_SOURCE_APPROVER,
            actor_tenant_id=environment.settings.rule_bundle_tenant_id,
            operation=Operation.SOURCE_SNAPSHOT_APPROVE,
            target_tenant_id=environment.settings.rule_bundle_tenant_id,
            target_type=SOURCE_SNAPSHOT_TARGET_TYPE,
            target_id=target_id,
        )
        is None
    )


class Result:
    def __init__(
        self,
        row: tuple[Any, ...] | None = None,
        *,
        rowcount: int = 1,
    ) -> None:
        self.row = row
        self.rowcount = rowcount

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.row


class PreflightConnection:
    def __init__(
        self,
        registration: SourceRegistration,
        *,
        manifest: tuple[Any, ...] | None,
        registration_row: tuple[Any, ...] | None,
        observation: tuple[Any, ...] | None,
        counts: tuple[Any, ...] = (0, 0, 0, 0, 0, 0),
        delete_rowcount: int = 1,
        failure: Exception | None = None,
    ) -> None:
        self.registration = registration
        self.manifest = manifest
        self.registration_row = registration_row
        self.observation = observation
        self.counts = counts
        self.delete_rowcount = delete_rowcount
        self.failure = failure
        self.calls: list[tuple[str, object | None]] = []

    def transaction(self) -> nullcontext[None]:
        return nullcontext()

    def execute(self, query: str, params: object | None = None) -> Result:
        self.calls.append((query, params))
        if self.failure is not None:
            raise self.failure
        if "pg_advisory_lock" in query:
            return Result((None,))
        if "FROM source_set_manifest" in query:
            return Result(self.manifest)
        if "FROM source_registry" in query:
            return Result(self.registration_row)
        if query.startswith("SELECT availability"):
            return Result(self.observation)
        if query.startswith("SELECT (SELECT count"):
            return Result(self.counts)
        if query.startswith("DELETE FROM source_runtime_observation"):
            return Result(rowcount=self.delete_rowcount)
        raise AssertionError(query)


def registration_row(value: SourceRegistration) -> tuple[object, ...]:
    return (
        value.deployment_id,
        value.source_set_id,
        value.source_id,
        value.name,
        value.owner,
        value.responsible_operator,
        value.jurisdiction,
        value.legal_scope,
        value.data_scope,
        value.access_method.value if value.access_method else None,
        value.credential_secret_ref,
        value.licence_summary,
        value.contractual_constraints,
        int(value.refresh_expectation.total_seconds())
        if value.refresh_expectation
        else None,
        int(value.stale_after.total_seconds()) if value.stale_after else None,
        value.active,
    )


def preflight_connection(
    registration: SourceRegistration,
    *,
    observation: tuple[Any, ...] | None = None,
    counts: tuple[Any, ...] = (0, 0, 0, 0, 0, 0),
) -> PreflightConnection:
    return PreflightConnection(
        registration,
        manifest=([registration.source_id],),
        registration_row=registration_row(registration),
        observation=observation,
        counts=counts,
    )


def test_preflight_first_locks_scope_and_compare_deletes_only_exact_marker() -> None:
    registration = demo_source_registration(Settings())
    observed = datetime(2026, 8, 6, tzinfo=UTC)
    marker = ("AVAILABLE", observed, "synthetic-snapshot-v1", observed, observed)
    connection = preflight_connection(registration, observation=marker)
    state = prepare_demo_source_bootstrap(
        cast(Any, connection), registration=registration
    )
    assert state is DemoSourcePreflightState.LEGACY_REMOVED
    assert "pg_advisory_lock" in connection.calls[0][0]
    delete = [item for item in connection.calls if item[0].startswith("DELETE")]
    assert len(delete) == 1
    assert delete[0][1] == (
        registration.deployment_id,
        registration.source_id,
        "AVAILABLE",
        "synthetic-snapshot-v1",
        observed,
        observed,
        observed,
    )


@pytest.mark.parametrize("registration_value", [object(), "inactive"])
def test_preflight_rejects_invalid_registration_before_database_access(
    registration_value: object,
) -> None:
    registration = demo_source_registration(Settings())
    if registration_value == "inactive":
        registration_value = replace(registration, active=False)
    connection = preflight_connection(registration)
    with pytest.raises(ValueError, match="requires an active registration"):
        prepare_demo_source_bootstrap(
            cast(Any, connection),
            registration=cast(Any, registration_value),
        )
    assert connection.calls == []


@pytest.mark.parametrize("rowcount", [0, True])
def test_preflight_fails_closed_if_legacy_compare_delete_loses_race(
    rowcount: object,
) -> None:
    registration = demo_source_registration(Settings())
    observed = datetime(2026, 8, 6, tzinfo=UTC)
    marker = ("AVAILABLE", observed, "synthetic-snapshot-v1", observed, observed)
    connection = preflight_connection(registration, observation=marker)
    connection.delete_rowcount = cast(Any, rowcount)
    with pytest.raises(DemoSourceBootstrapUnavailable):
        prepare_demo_source_bootstrap(cast(Any, connection), registration=registration)
    assert sum(query.startswith("DELETE") for query, _ in connection.calls) == 1


@pytest.mark.parametrize(
    "mutate",
    [
        "manifest",
        "manifest_shape",
        "manifest_json",
        "manifest_type",
        "manifest_empty",
        "registration",
        "marker",
        "availability",
        "snapshot_type",
        "times",
        "marker_with_graph",
        "partial_registry",
        "observation_shape",
        "absent_graph",
        "counts_none",
        "counts_shape",
        "counts_bool",
        "counts_negative",
    ],
)
def test_preflight_unknown_customized_and_inconsistent_state_has_no_write(
    mutate: str,
) -> None:
    registration = demo_source_registration(Settings())
    observed = datetime(2026, 8, 6, tzinfo=UTC)
    marker = ("AVAILABLE", observed, "synthetic-snapshot-v1", observed, observed)
    connection = preflight_connection(registration, observation=marker)
    if mutate == "manifest":
        connection.manifest = (["other-source"],)
    elif mutate == "manifest_shape":
        connection.manifest = ([registration.source_id], "extra")
    elif mutate == "manifest_json":
        connection.manifest = ("{",)
    elif mutate == "manifest_type":
        connection.manifest = ({"source": registration.source_id},)
    elif mutate == "manifest_empty":
        connection.manifest = ([],)
    elif mutate == "registration":
        connection.registration_row = registration_row(
            replace(registration, owner="Customized owner")
        )
    elif mutate == "marker":
        connection.observation = (
            "AVAILABLE",
            observed,
            "unknown-marker",
            observed,
            observed,
        )
    elif mutate == "availability":
        connection.observation = (
            "UNAVAILABLE",
            observed,
            "synthetic-snapshot-v1",
            observed,
            observed,
        )
    elif mutate == "snapshot_type":
        connection.observation = ("AVAILABLE", observed, 1, observed, observed)
    elif mutate == "times":
        connection.observation = (
            "AVAILABLE",
            observed,
            "synthetic-snapshot-v1",
            observed - timedelta(seconds=1),
            observed,
        )
    elif mutate == "marker_with_graph":
        connection.counts = (1, 0, 0, 1, 1, 1)
    elif mutate == "partial_registry":
        connection.registration_row = None
    elif mutate == "observation_shape":
        connection.observation = marker[:-1]
    elif mutate == "absent_graph":
        connection.manifest = None
        connection.registration_row = None
        connection.observation = None
        connection.counts = (1, 0, 0, 0, 0, 0)
    elif mutate == "counts_none":
        connection.counts = cast(Any, None)
    elif mutate == "counts_shape":
        connection.counts = (0,)
    elif mutate == "counts_bool":
        connection.counts = (False, 0, 0, 0, 0, 0)
    else:
        connection.counts = (-1, 0, 0, 0, 0, 0)
    with pytest.raises(
        DemoSourceBootstrapUnavailable,
        match="synthetic demo source bootstrap is unavailable",
    ):
        prepare_demo_source_bootstrap(cast(Any, connection), registration=registration)
    assert not any(query.startswith("DELETE") for query, _ in connection.calls)


@pytest.mark.parametrize(
    ("manifest", "registration_present", "observation", "counts", "expected"),
    [
        (None, False, None, (0, 0, 0, 0, 0, 0), DemoSourcePreflightState.EMPTY),
        (
            "exact",
            True,
            None,
            (0, 0, 0, 0, 0, 0),
            DemoSourcePreflightState.REGISTERED,
        ),
        (
            "exact",
            True,
            None,
            (1, 1, 0, 1, 3, 3),
            DemoSourcePreflightState.SNAPSHOT_GRAPH,
        ),
        (
            "json",
            True,
            None,
            (0, 0, 0, 0, 0, 0),
            DemoSourcePreflightState.REGISTERED,
        ),
        (
            "exact",
            True,
            (
                "AVAILABLE",
                datetime(2026, 8, 6, tzinfo=UTC),
                f"snapshot-{'a' * 64}",
                datetime(2026, 8, 6, tzinfo=UTC),
                datetime(2026, 1, 1, tzinfo=UTC),
            ),
            (2, 2, 2, 1, 12, 12),
            DemoSourcePreflightState.SNAPSHOT_GRAPH,
        ),
    ],
)
def test_preflight_classifies_fresh_registered_partial_and_real_graph(
    manifest: str | None,
    registration_present: bool,
    observation: tuple[Any, ...] | None,
    counts: tuple[Any, ...],
    expected: DemoSourcePreflightState,
) -> None:
    registration = demo_source_registration(Settings())
    connection = PreflightConnection(
        registration,
        manifest=(
            (json.dumps([registration.source_id]),)
            if manifest == "json"
            else ([registration.source_id],)
            if manifest
            else None
        ),
        registration_row=registration_row(registration)
        if registration_present
        else None,
        observation=observation,
        counts=counts,
    )
    assert (
        prepare_demo_source_bootstrap(cast(Any, connection), registration=registration)
        is expected
    )


@pytest.mark.parametrize(
    "failure",
    [
        DemoSourceBootstrapUnavailable(),
        psycopg.OperationalError("private sentinel"),
        ValueError("private sentinel"),
    ],
)
def test_preflight_dependency_failures_are_stable_and_redacted(
    failure: Exception,
) -> None:
    registration = demo_source_registration(Settings())
    connection = PreflightConnection(
        registration,
        manifest=None,
        registration_row=None,
        observation=None,
        failure=failure,
    )
    with pytest.raises(DemoSourceBootstrapUnavailable) as exc_info:
        prepare_demo_source_bootstrap(cast(Any, connection), registration=registration)
    assert str(exc_info.value) == "synthetic demo source bootstrap is unavailable"
    assert "sentinel" not in str(exc_info.value)
