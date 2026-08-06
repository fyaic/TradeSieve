"""Byte-level readiness and rollback-safe demo restart evidence."""

from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from tradesieve import runtime
from tradesieve.adapters.in_memory_source_snapshot import (
    InMemorySourceSnapshotRepository,
)
from tradesieve.adapters.local_raw_object_store import LocalImmutableRawObjectStore
from tradesieve.adapters.synthetic_source_parser import SyntheticJsonSourceParser
from tradesieve.application.auth import (
    AuthorizationAuditEvent,
    AuthorizationService,
    Operation,
)
from tradesieve.application.source_snapshot import SourceSnapshotService
from tradesieve.application.source_snapshot_query import SourceSnapshotQueryService
from tradesieve.application.source_snapshot_readiness import (
    SourceSnapshotReadinessService,
)
from tradesieve.config import Settings
from tradesieve.demo_source_snapshot import (
    DEMO_SOURCE_APPROVER,
    DEMO_SOURCE_ID,
    DemoSourceActor,
    DemoSourceEntitlementResolver,
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
    LifecycleActorType,
    SourceSnapshotEventType,
    SourceSnapshotLifecycleEvent,
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
        self.write_count = 0

    def save_manifest(self, manifest: SourceSetManifest) -> None:
        self.write_count += 1
        self.manifest = manifest

    def get_manifest(
        self, deployment_id: str, source_set_id: str
    ) -> SourceSetManifest | None:
        if (deployment_id, source_set_id) != (
            self.registration.deployment_id,
            self.registration.source_set_id,
        ):
            return None
        return self.manifest

    def save_registration(self, registration: SourceRegistration) -> None:
        self.write_count += 1
        self.registration = registration

    def get_registration(
        self, deployment_id: str, source_id: str
    ) -> SourceRegistration | None:
        if (deployment_id, source_id) != (
            self.registration.deployment_id,
            self.registration.source_id,
        ):
            return None
        return self.registration

    def save_observation(
        self, observation: SourceRuntimeObservation
    ) -> ObservationWriteOutcome:
        del observation
        self.write_count += 1
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


_DEFAULT = object()


class RegistryView:
    def __init__(
        self,
        registry: Registry,
        *,
        manifest: object = _DEFAULT,
        registration: object = _DEFAULT,
    ) -> None:
        self.registry = registry
        self.manifest = manifest
        self.registration = registration

    def get_manifest(self, deployment_id: str, source_set_id: str) -> object:
        if self.manifest is not _DEFAULT:
            return self.manifest
        return self.registry.get_manifest(deployment_id, source_set_id)

    def get_registration(self, deployment_id: str, source_id: str) -> object:
        if self.registration is not _DEFAULT:
            return self.registration
        return self.registry.get_registration(deployment_id, source_id)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.registry, name)


class RepositoryView:
    def __init__(
        self,
        repository: InMemorySourceSnapshotRepository,
        *,
        lifecycle: object = _DEFAULT,
        observation: object = _DEFAULT,
        snapshot: object = _DEFAULT,
        metadata: object = _DEFAULT,
        audits: object = _DEFAULT,
        failure: bool = False,
    ) -> None:
        self.repository = repository
        self.lifecycle = lifecycle
        self.observation = observation
        self.snapshot = snapshot
        self.metadata = metadata
        self.audits = audits
        self.failure = failure

    def get_lifecycle_snapshot(self, deployment_id: str, source_id: str) -> object:
        if self.failure:
            raise RuntimeError("private repository failure")
        if self.lifecycle is not _DEFAULT:
            return self.lifecycle
        return self.repository.get_lifecycle_snapshot(deployment_id, source_id)

    def get_runtime_observation(self, deployment_id: str, source_id: str) -> object:
        if self.observation is not _DEFAULT:
            return self.observation
        return self.repository.get_runtime_observation(deployment_id, source_id)

    def get_snapshot(
        self, deployment_id: str, source_id: str, snapshot_id: str
    ) -> object:
        if self.snapshot is not _DEFAULT:
            return self.snapshot
        return self.repository.get_snapshot(deployment_id, source_id, snapshot_id)

    def get_raw_metadata(
        self, deployment_id: str, source_id: str, object_id: str
    ) -> object:
        if self.metadata is not _DEFAULT:
            return self.metadata
        return self.repository.get_raw_metadata(deployment_id, source_id, object_id)

    def list_command_audits(
        self, deployment_id: str, source_id: str, *, limit: int
    ) -> object:
        if self.audits is not _DEFAULT:
            return self.audits
        return self.repository.list_command_audits(
            deployment_id, source_id, limit=limit
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self.repository, name)


@dataclass
class Environment:
    settings: Settings
    registry: Registry
    repository: InMemorySourceSnapshotRepository
    store: LocalImmutableRawObjectStore
    graph: runtime.DemoSourceServices
    authorization_audits: list[AuthorizationAuditEvent]


def build_environment(root: Path) -> Environment:
    root.mkdir(mode=0o700, parents=True)
    settings = Settings(raw_object_root=root)
    fixtures = synthetic_demo_source_fixtures(settings)
    registry = Registry(demo_source_registration(settings))
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
    authorization_audits: list[AuthorizationAuditEvent] = []
    authorization = AuthorizationService(
        authorization_audits.append,
        DemoSourceEntitlementResolver(settings, repository, fixtures),
        event_id_factory=Ids("authorization-event"),
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
    return Environment(
        settings,
        registry,
        repository,
        store,
        graph,
        authorization_audits,
    )


def complete_environment(
    environment: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        runtime,
        "build_demo_source_services",
        lambda settings, connection: environment.graph,
    )
    runtime._bootstrap_demo_source_snapshots(environment.settings, cast(Any, object()))


def readiness(
    environment: Environment,
    *,
    registry: object | None = None,
    repository: object | None = None,
    store: object | None = None,
    now: datetime | object = _DEFAULT,
) -> SourceSnapshotReadinessService:
    checked_now = datetime.now(UTC) if now is _DEFAULT else now
    return SourceSnapshotReadinessService(
        cast(Any, registry or environment.registry),
        cast(Any, repository or environment.repository),
        cast(Any, store or environment.store),
        deployment_id=environment.settings.deployment_id,
        source_set_id=environment.settings.required_source_set,
        clock=lambda: cast(Any, checked_now),
    )


def filesystem_facts(root: Path) -> tuple[tuple[str, int, bytes | None], ...]:
    return tuple(
        (
            str(path.relative_to(root)),
            path.stat().st_mode,
            path.read_bytes() if path.is_file() else None,
        )
        for path in sorted(root.rglob("*"))
    )


def test_complete_evidence_is_ready_and_check_is_strictly_side_effect_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = build_environment(tmp_path / "raw")
    complete_environment(environment, monkeypatch)
    before = (
        environment.repository.get_lifecycle_snapshot(
            environment.settings.deployment_id, DEMO_SOURCE_ID
        ),
        environment.repository.list_snapshots(
            environment.settings.deployment_id, DEMO_SOURCE_ID, limit=512
        ),
        environment.repository.list_command_audits(
            environment.settings.deployment_id, DEMO_SOURCE_ID, limit=512
        ),
        environment.repository.get_runtime_observation(
            environment.settings.deployment_id, DEMO_SOURCE_ID
        ),
        tuple(environment.authorization_audits),
        environment.registry.write_count,
        filesystem_facts(environment.settings.raw_object_root),
    )
    service = readiness(environment)
    assert service.is_ready() is True
    assert service.is_ready() is True
    after = (
        environment.repository.get_lifecycle_snapshot(
            environment.settings.deployment_id, DEMO_SOURCE_ID
        ),
        environment.repository.list_snapshots(
            environment.settings.deployment_id, DEMO_SOURCE_ID, limit=512
        ),
        environment.repository.list_command_audits(
            environment.settings.deployment_id, DEMO_SOURCE_ID, limit=512
        ),
        environment.repository.get_runtime_observation(
            environment.settings.deployment_id, DEMO_SOURCE_ID
        ),
        tuple(environment.authorization_audits),
        environment.registry.write_count,
        filesystem_facts(environment.settings.raw_object_root),
    )
    assert after == before


@pytest.mark.parametrize(
    "corruption",
    [
        "clock-type",
        "clock-naive",
        "manifest-missing",
        "manifest-type",
        "manifest-empty",
        "manifest-scope",
        "registration-missing",
        "registration-type",
        "registration-inactive",
        "registration-scope",
        "repository-failure",
        "events-type",
        "lifecycle-type",
        "lifecycle-projection",
        "observation-missing",
        "observation-type",
        "observation-active",
        "observation-stale",
        "snapshot-missing",
        "snapshot-type",
        "snapshot-reference",
        "metadata-missing",
        "metadata-type",
        "metadata-reference",
        "metadata-corrupt",
        "active-evidence",
        "raw-corrupt",
        "audit-type",
        "audit-empty",
        "audit-item-type",
        "audit-scope",
    ],
)
def test_every_missing_stale_malformed_or_corrupt_boundary_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    environment = build_environment(tmp_path / corruption / "raw")
    complete_environment(environment, monkeypatch)
    events, lifecycle = environment.repository.get_lifecycle_snapshot(
        environment.settings.deployment_id, DEMO_SOURCE_ID
    )
    observation = environment.repository.get_runtime_observation(
        environment.settings.deployment_id, DEMO_SOURCE_ID
    )
    active = environment.repository.get_snapshot(
        environment.settings.deployment_id,
        DEMO_SOURCE_ID,
        cast(Any, lifecycle.active_snapshot).snapshot_id,
    )
    assert observation is not None and active is not None
    metadata = environment.repository.get_raw_metadata(
        environment.settings.deployment_id,
        DEMO_SOURCE_ID,
        active.raw_object.object_id,
    )
    audits = environment.repository.list_command_audits(
        environment.settings.deployment_id, DEMO_SOURCE_ID, limit=512
    )
    assert metadata is not None
    registry: object = environment.registry
    repository: object = environment.repository
    store: object = environment.store
    now: object = datetime.now(UTC)

    if corruption == "clock-type":
        now = "not-a-clock"
    elif corruption == "clock-naive":
        now = datetime.now()
    elif corruption.startswith("manifest"):
        manifest: object = environment.registry.manifest
        if corruption == "manifest-missing":
            manifest = None
        elif corruption == "manifest-type":
            manifest = object()
        else:
            manifest = copy.deepcopy(environment.registry.manifest)
            field = (
                "required_source_ids"
                if corruption == "manifest-empty"
                else "deployment_id"
            )
            value: object = () if corruption == "manifest-empty" else "other-deployment"
            object.__setattr__(manifest, field, value)
        registry = RegistryView(environment.registry, manifest=manifest)
    elif corruption.startswith("registration"):
        registration: object = environment.registry.registration
        if corruption == "registration-missing":
            registration = None
        elif corruption == "registration-type":
            registration = object()
        elif corruption == "registration-inactive":
            registration = replace(environment.registry.registration, active=False)
        else:
            registration = copy.deepcopy(environment.registry.registration)
            object.__setattr__(registration, "deployment_id", "other-deployment")
        registry = RegistryView(environment.registry, registration=registration)
    elif corruption == "repository-failure":
        repository = RepositoryView(environment.repository, failure=True)
    elif corruption == "events-type":
        repository = RepositoryView(
            environment.repository, lifecycle=(list(events), lifecycle)
        )
    elif corruption == "lifecycle-type":
        repository = RepositoryView(
            environment.repository, lifecycle=(events, object())
        )
    elif corruption == "lifecycle-projection":
        changed = copy.deepcopy(lifecycle)
        object.__setattr__(changed, "active_snapshot", None)
        repository = RepositoryView(environment.repository, lifecycle=(events, changed))
    elif corruption.startswith("observation"):
        changed_observation: object = observation
        if corruption == "observation-missing":
            changed_observation = None
        elif corruption == "observation-type":
            changed_observation = object()
        elif corruption == "observation-active":
            changed_observation = replace(
                observation, active_snapshot_id="other-snapshot"
            )
        else:
            changed_observation = replace(
                observation,
                observed_at=datetime.now(UTC),
                retrieved_at=datetime.now(UTC) - timedelta(hours=3),
            )
        repository = RepositoryView(
            environment.repository, observation=changed_observation
        )
    elif corruption.startswith("snapshot"):
        changed_snapshot: object
        if corruption == "snapshot-missing":
            changed_snapshot = None
        elif corruption == "snapshot-type":
            changed_snapshot = object()
        else:
            changed_snapshot = next(
                item
                for item in environment.repository.list_snapshots(
                    environment.settings.deployment_id, DEMO_SOURCE_ID, limit=512
                )
                if item.reference() != lifecycle.active_snapshot
            )
        repository = RepositoryView(
            environment.repository,
            snapshot=changed_snapshot,
        )
    elif corruption.startswith("metadata"):
        changed_metadata: object = metadata
        if corruption == "metadata-missing":
            changed_metadata = None
        elif corruption == "metadata-type":
            changed_metadata = object()
        elif corruption == "metadata-reference":
            inactive = next(
                item
                for item in environment.repository.list_snapshots(
                    environment.settings.deployment_id, DEMO_SOURCE_ID, limit=512
                )
                if item.reference() != lifecycle.active_snapshot
            )
            changed_metadata = environment.repository.get_raw_metadata(
                environment.settings.deployment_id,
                DEMO_SOURCE_ID,
                inactive.raw_object.object_id,
            )
        else:
            changed_metadata = copy.deepcopy(metadata)
            object.__setattr__(changed_metadata, "original_name", "other.json")
        repository = RepositoryView(environment.repository, metadata=changed_metadata)
    elif corruption == "active-evidence":
        changed_observed_at = observation.observed_at + timedelta(seconds=1)
        repository = RepositoryView(
            environment.repository,
            observation=replace(
                observation,
                observed_at=changed_observed_at,
            ),
        )
        now = changed_observed_at + timedelta(seconds=1)
    elif corruption == "raw-corrupt":

        class CorruptStore:
            def get_verified(self, reference: object) -> bytes:
                del reference
                return b"corrupt bytes"

        store = CorruptStore()
    else:
        changed_audits: object
        if corruption == "audit-type":
            changed_audits = list(audits)
        elif corruption == "audit-empty":
            changed_audits = ()
        elif corruption == "audit-item-type":
            changed_audits = (object(),)
        else:
            changed_audit = copy.deepcopy(audits[0])
            object.__setattr__(changed_audit, "deployment_id", "other-deployment")
            changed_audits = (changed_audit,)
        repository = RepositoryView(environment.repository, audits=changed_audits)

    assert (
        readiness(
            environment,
            registry=registry,
            repository=repository,
            store=store,
            now=now,
        ).is_ready()
        is False
    )


def test_human_rollback_is_ready_and_restart_preserves_governed_pointer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = build_environment(tmp_path / "raw")
    complete_environment(environment, monkeypatch)
    snapshots = environment.repository.list_snapshots(
        environment.settings.deployment_id, DEMO_SOURCE_ID, limit=512
    )
    _, lifecycle = environment.repository.get_lifecycle_snapshot(
        environment.settings.deployment_id, DEMO_SOURCE_ID
    )
    original_active = next(
        snapshot
        for snapshot in snapshots
        if snapshot.reference() == lifecycle.active_snapshot
    )
    target = next(
        snapshot
        for snapshot in snapshots
        if snapshot.reference() != lifecycle.active_snapshot
    )
    authorized = authorize_demo_source_request(
        environment.settings,
        environment.graph.authorization,
        actor=DemoSourceActor.APPROVER,
        operation=Operation.SOURCE_SNAPSHOT_ROLLBACK,
        now=datetime.now(UTC),
        identity=demo_snapshot_identity(target),
    )
    result = environment.graph.service.rollback(
        authorized,
        source_id=DEMO_SOURCE_ID,
        identity=demo_snapshot_identity(target),
        reason="synthetic governed rollback",
    )
    assert result.event_type is SourceSnapshotEventType.ROLLED_BACK
    assert readiness(environment).is_ready() is True
    events_before, lifecycle_before = environment.repository.get_lifecycle_snapshot(
        environment.settings.deployment_id, DEMO_SOURCE_ID
    )
    artifacts_before = environment.repository.list_snapshots(
        environment.settings.deployment_id, DEMO_SOURCE_ID, limit=512
    )
    command_audits_before = environment.repository.list_command_audits(
        environment.settings.deployment_id, DEMO_SOURCE_ID, limit=512
    )
    runtime._bootstrap_demo_source_snapshots(environment.settings, cast(Any, object()))
    events_after, lifecycle_after = environment.repository.get_lifecycle_snapshot(
        environment.settings.deployment_id, DEMO_SOURCE_ID
    )
    assert (events_after, lifecycle_after) == (events_before, lifecycle_before)
    assert lifecycle_after.active_snapshot == target.reference()
    assert (
        environment.repository.list_snapshots(
            environment.settings.deployment_id, DEMO_SOURCE_ID, limit=512
        )
        == artifacts_before
    )
    assert (
        environment.repository.list_command_audits(
            environment.settings.deployment_id, DEMO_SOURCE_ID, limit=512
        )
        == command_audits_before
    )
    assert events_after[-1].actor_id == DEMO_SOURCE_APPROVER
    assert events_after[-1].actor_type is LifecycleActorType.HUMAN

    authorized_back = authorize_demo_source_request(
        environment.settings,
        environment.graph.authorization,
        actor=DemoSourceActor.APPROVER,
        operation=Operation.SOURCE_SNAPSHOT_ROLLBACK,
        now=datetime.now(UTC),
        identity=demo_snapshot_identity(original_active),
    )
    result_back = environment.graph.service.rollback(
        authorized_back,
        source_id=DEMO_SOURCE_ID,
        identity=demo_snapshot_identity(original_active),
        reason="synthetic governed rollback to current version",
    )
    assert result_back.event_type is SourceSnapshotEventType.ROLLED_BACK
    assert readiness(environment).is_ready() is True
    second_events_before, second_lifecycle_before = (
        environment.repository.get_lifecycle_snapshot(
            environment.settings.deployment_id, DEMO_SOURCE_ID
        )
    )
    second_artifacts_before = environment.repository.list_snapshots(
        environment.settings.deployment_id, DEMO_SOURCE_ID, limit=512
    )
    second_command_audits_before = environment.repository.list_command_audits(
        environment.settings.deployment_id, DEMO_SOURCE_ID, limit=512
    )
    runtime._bootstrap_demo_source_snapshots(environment.settings, cast(Any, object()))
    second_events_after, second_lifecycle_after = (
        environment.repository.get_lifecycle_snapshot(
            environment.settings.deployment_id, DEMO_SOURCE_ID
        )
    )
    assert (second_events_after, second_lifecycle_after) == (
        second_events_before,
        second_lifecycle_before,
    )
    assert second_lifecycle_after.active_snapshot == original_active.reference()
    assert (
        environment.repository.list_snapshots(
            environment.settings.deployment_id, DEMO_SOURCE_ID, limit=512
        )
        == second_artifacts_before
    )
    assert (
        environment.repository.list_command_audits(
            environment.settings.deployment_id, DEMO_SOURCE_ID, limit=512
        )
        == second_command_audits_before
    )
    assert [event.event_type for event in second_events_after[12:]] == [
        SourceSnapshotEventType.ROLLED_BACK,
        SourceSnapshotEventType.ROLLED_BACK,
    ]


@pytest.mark.parametrize(
    "corruption",
    ["event-type", "actor", "previous", "target", "raw", "future", "overbound"],
)
def test_invalid_rollback_tail_is_rejected_before_new_authorization_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    environment = build_environment(tmp_path / corruption / "raw")
    complete_environment(environment, monkeypatch)
    snapshots = environment.repository.list_snapshots(
        environment.settings.deployment_id, DEMO_SOURCE_ID, limit=512
    )
    _, lifecycle = environment.repository.get_lifecycle_snapshot(
        environment.settings.deployment_id, DEMO_SOURCE_ID
    )
    target = next(
        snapshot
        for snapshot in snapshots
        if snapshot.reference() != lifecycle.active_snapshot
    )
    authorized = authorize_demo_source_request(
        environment.settings,
        environment.graph.authorization,
        actor=DemoSourceActor.APPROVER,
        operation=Operation.SOURCE_SNAPSHOT_ROLLBACK,
        now=datetime.now(UTC),
        identity=demo_snapshot_identity(target),
    )
    environment.graph.service.rollback(
        authorized,
        source_id=DEMO_SOURCE_ID,
        identity=demo_snapshot_identity(target),
        reason="synthetic governed rollback",
    )
    events, projected = environment.repository.get_lifecycle_snapshot(
        environment.settings.deployment_id, DEMO_SOURCE_ID
    )
    changed = list(copy.deepcopy(events))
    tail = changed[-1]
    if corruption == "event-type":
        object.__setattr__(tail, "event_type", SourceSnapshotEventType.ACTIVATED)
    elif corruption == "actor":
        object.__setattr__(tail, "actor_id", "other-human")
    elif corruption == "previous":
        object.__setattr__(tail, "previous_active_snapshot", tail.snapshot)
    elif corruption == "target":
        object.__setattr__(tail, "snapshot", tail.previous_active_snapshot)
    elif corruption == "raw":
        previous = next(
            snapshot
            for snapshot in snapshots
            if snapshot.reference() == tail.previous_active_snapshot
        )
        object.__setattr__(tail, "raw_object", previous.raw_object)
    elif corruption == "future":
        object.__setattr__(tail, "occurred_at", datetime.now(UTC) + timedelta(days=1))
    else:
        changed = list(events) + [events[-1]] * (513 - len(events))
    view = RepositoryView(
        environment.repository,
        lifecycle=(tuple(changed), projected),
    )
    graph = replace(environment.graph, repository=cast(Any, view))
    monkeypatch.setattr(
        runtime,
        "build_demo_source_services",
        lambda settings, connection: graph,
    )
    audit_count = len(environment.authorization_audits)
    with pytest.raises(
        RuntimeError, match="synthetic demo source bootstrap is unavailable"
    ):
        runtime._bootstrap_demo_source_snapshots(
            environment.settings, cast(Any, object())
        )
    assert len(environment.authorization_audits) == audit_count


def test_active_evidence_helper_rejects_missing_or_tampered_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = build_environment(tmp_path / "raw")
    complete_environment(environment, monkeypatch)
    events, lifecycle = environment.repository.get_lifecycle_snapshot(
        environment.settings.deployment_id, DEMO_SOURCE_ID
    )
    snapshot = environment.repository.get_snapshot(
        environment.settings.deployment_id,
        DEMO_SOURCE_ID,
        cast(Any, lifecycle.active_snapshot).snapshot_id,
    )
    observation = environment.repository.get_runtime_observation(
        environment.settings.deployment_id, DEMO_SOURCE_ID
    )
    assert snapshot is not None and observation is not None
    metadata = environment.repository.get_raw_metadata(
        environment.settings.deployment_id,
        DEMO_SOURCE_ID,
        snapshot.raw_object.object_id,
    )
    assert metadata is not None
    check = SourceSnapshotReadinessService._active_evidence_is_ready
    assert check(events, snapshot, metadata, observation) is True
    active = snapshot.reference()
    validation_index = next(
        index
        for index, event in enumerate(events)
        if event.event_type is SourceSnapshotEventType.VALIDATED
        and event.snapshot == active
    )
    approval_index = next(
        index
        for index, event in enumerate(events)
        if event.event_type is SourceSnapshotEventType.APPROVED
        and event.snapshot == active
    )
    pointer_events = tuple(
        event
        for event in events
        if event.event_type
        in {SourceSnapshotEventType.ACTIVATED, SourceSnapshotEventType.ROLLED_BACK}
    )
    cases: list[
        tuple[tuple[SourceSnapshotLifecycleEvent, ...], SourceRuntimeObservation]
    ] = [
        (
            tuple(
                event for index, event in enumerate(events) if index != validation_index
            ),
            observation,
        ),
        (events + (events[validation_index],), observation),
        (
            tuple(
                event for index, event in enumerate(events) if index != approval_index
            ),
            observation,
        ),
        (events + (events[approval_index],), observation),
        (
            tuple(event for event in events if event not in pointer_events),
            observation,
        ),
        (
            events,
            replace(
                observation, observed_at=observation.observed_at + timedelta(seconds=1)
            ),
        ),
    ]
    broken_report = copy.deepcopy(events)
    object.__setattr__(broken_report[validation_index], "validation_report", None)
    cases.append((tuple(broken_report), observation))
    for changed_events, changed_observation in cases:
        assert check(changed_events, snapshot, metadata, changed_observation) is False
