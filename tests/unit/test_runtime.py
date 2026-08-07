"""Database-backed runtime status tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Any, Self, cast

import psycopg
import pytest

from tradesieve import runtime
from tradesieve.adapters.local_raw_object_store import LocalImmutableRawObjectStore
from tradesieve.adapters.postgres_demo_source_bootstrap import (
    DemoSourceBootstrapUnavailable,
    DemoSourcePreflightState,
)
from tradesieve.config import Settings
from tradesieve.ports.source_snapshot import SourceSnapshotPersistenceError


class FakeResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows


class FakeConnection:
    def __init__(
        self,
        *,
        revision: str | None = runtime.MIGRATION_REVISION,
        rule_ready: bool = True,
        snapshot_ready: bool = True,
        source_manifest: list[str] | None = None,
        source_entries: list[tuple[Any, ...]] | None = None,
        observation_insert_rows: list[tuple[Any, ...]] | None = None,
        current_observation: tuple[Any, ...] | None = None,
        heartbeat: datetime | None = None,
        fail_query: str | None = None,
    ) -> None:
        self.revision = revision
        self.rule_ready = rule_ready
        self.snapshot_ready = snapshot_ready
        self.source_manifest = (
            ["synthetic-source-v1"] if source_manifest is None else source_manifest
        )
        self.source_entries = (
            [source_entry()] if source_entries is None else source_entries
        )
        self.observation_insert_rows = (
            [source_observation_row()]
            if observation_insert_rows is None
            else observation_insert_rows
        )
        self.current_observation = current_observation
        self.heartbeat = heartbeat
        self.fail_query = fail_query
        self.executed: list[tuple[str, object | None]] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def execute(self, query: str, params: object | None = None) -> FakeResult:
        self.executed.append((query, params))
        if self.fail_query and self.fail_query in query:
            raise psycopg.ProgrammingError("synthetic query failure")
        if "alembic_version" in query:
            return FakeResult([] if self.revision is None else [(self.revision,)])
        if "SELECT required_source_ids FROM source_set_manifest" in query:
            return FakeResult([(self.source_manifest,)])
        if "FROM source_registry AS registry" in query:
            return FakeResult(self.source_entries)
        if query.startswith("SELECT deployment_id") and "FROM source_registry" in query:
            return FakeResult([source_registration_row(active=False)])
        if "INSERT INTO source_runtime_observation" in query:
            return FakeResult(self.observation_insert_rows)
        if query.startswith("SELECT availability"):
            return FakeResult(
                [] if self.current_observation is None else [self.current_observation]
            )
        if "SELECT observed_at" in query:
            return FakeResult([] if self.heartbeat is None else [(self.heartbeat,)])
        return FakeResult([])


def source_registration_row(*, active: bool = True) -> tuple[Any, ...]:
    return (
        "demo",
        "synthetic-demo-sources-v1",
        "synthetic-source-v1",
        "Synthetic source fixture",
        "TradeSieve demo",
        "demo-source-operator",
        "SYNTHETIC",
        "Synthetic screening behavior only",
        "Synthetic entities with no production data",
        "INTERNAL",
        None,
        "Synthetic demo fixture; no production use",
        None,
        3600,
        7200,
        active,
    )


def source_observation_row(
    *,
    availability: str = "AVAILABLE",
    observed_at: datetime | None = None,
    retrieved_at: datetime | None = None,
) -> tuple[Any, ...]:
    observed = observed_at or datetime.now(UTC)
    retrieved = observed if retrieved_at is None else retrieved_at
    return (
        availability,
        observed,
        "synthetic-snapshot-v1",
        retrieved,
        retrieved,
    )


def source_entry(
    *,
    active: bool = True,
    availability: str = "AVAILABLE",
    observed_at: datetime | None = None,
    retrieved_at: datetime | None = None,
) -> tuple[Any, ...]:
    return source_registration_row(active=active) + source_observation_row(
        availability=availability,
        observed_at=observed_at,
        retrieved_at=retrieved_at,
    )


def use_connection(monkeypatch: pytest.MonkeyPatch, connection: FakeConnection) -> None:
    monkeypatch.setattr(runtime, "connect", lambda settings: cast(Any, connection))
    object_store = object()
    monkeypatch.setattr(
        runtime,
        "LocalImmutableRawObjectStore",
        lambda root: object_store,
    )

    class FakeSnapshotReadiness:
        def __init__(
            self,
            registry: object,
            repository: object,
            store: object,
            **kwargs: object,
        ) -> None:
            assert registry is not None
            assert repository is not None
            assert store is object_store
            clock = kwargs.pop("clock")
            assert callable(clock)
            assert kwargs == {
                "deployment_id": "demo",
                "source_set_id": "synthetic-demo-sources-v1",
            }

        def is_ready(self) -> bool:
            return connection.snapshot_ready

    monkeypatch.setattr(
        runtime,
        "SourceSnapshotReadinessService",
        FakeSnapshotReadiness,
    )

    class FakeRuleReadiness:
        def __init__(self, repository: object, **kwargs: object) -> None:
            assert repository is not None
            assert kwargs == {
                "deployment_id": "demo",
                "rule_set_id": "synthetic-demo-rules-v1",
            }

        def is_current_active_ready(self, tenant_id: str) -> bool:
            assert tenant_id == "demo-tenant"
            if connection.fail_query == "rule_bundle":
                raise ValueError("synthetic rule read failure")
            return connection.rule_ready

    monkeypatch.setattr(runtime, "RuleBundleReadinessService", FakeRuleReadiness)


def test_connect_passes_timeout_and_autocommit(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_connect(database_url: str, **kwargs: object) -> object:
        captured.update(database_url=database_url, **kwargs)
        return object()

    monkeypatch.setattr("tradesieve.runtime.psycopg.connect", fake_connect)
    settings = Settings(database_connect_timeout_seconds=7)
    result = runtime.connect(settings)
    assert result is not None
    assert captured == {
        "database_url": settings.database_url,
        "connect_timeout": 7,
        "autocommit": True,
    }


def test_readiness_reports_database_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(settings: Settings) -> None:
        raise psycopg.OperationalError(f"synthetic unavailable: {settings.mode}")

    monkeypatch.setattr(runtime, "connect", unavailable)
    status = runtime.check_readiness(Settings())
    assert status.ready is False
    assert status.checks["database"] == "UNAVAILABLE"


@pytest.mark.parametrize(
    ("connection", "expected_migration"),
    [
        (FakeConnection(fail_query="alembic_version"), "NOT_APPLIED"),
        (FakeConnection(revision=None), "NOT_APPLIED"),
        (FakeConnection(revision="old"), "NOT_APPLIED"),
    ],
)
def test_readiness_rejects_missing_or_wrong_migration(
    monkeypatch: pytest.MonkeyPatch,
    connection: FakeConnection,
    expected_migration: str,
) -> None:
    use_connection(monkeypatch, connection)
    status = runtime.check_readiness(Settings())
    assert status.ready is False
    assert status.checks["database"] == "OK"
    assert status.checks["migration"] == expected_migration


def test_readiness_rejects_rule_bundle_query_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_connection(monkeypatch, FakeConnection(fail_query="rule_bundle"))
    status = runtime.check_readiness(Settings())
    assert status.ready is False
    assert status.checks["migration"] == "OK"


def test_public_checks_fail_closed_for_unknown_internal_state() -> None:
    status = runtime.RuntimeStatus(
        False,
        {
            "migration": "unexpected-version",
            "stale_source": "STALE",
            "quarantined_source": "QUARANTINED",
        },
    )
    assert status.public_checks() == {
        "migration": "UNAVAILABLE",
        "stale_source": "UNAVAILABLE",
        "quarantined_source": "UNAVAILABLE",
    }


def test_readiness_requires_both_coverage_sets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_connection(
        monkeypatch,
        FakeConnection(source_entries=[]),
    )
    status = runtime.check_readiness(Settings())
    assert status.ready is False
    assert status.checks["required_source_coverage"] == "UNAVAILABLE"
    assert status.public_checks()["required_rule_coverage"] == "OK"


def test_readiness_requires_byte_verified_source_snapshot_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_connection(monkeypatch, FakeConnection(snapshot_ready=False))
    status = runtime.check_readiness(Settings())
    assert status.ready is False
    assert status.checks["required_source_coverage"] == "OK"
    assert status.checks["required_source_snapshot_evidence"] == "UNAVAILABLE"
    assert status.checks["required_rule_coverage"] == "OK"


def test_readiness_rejects_missing_rule_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_connection(
        monkeypatch,
        FakeConnection(rule_ready=False),
    )
    status = runtime.check_readiness(Settings())
    assert status.ready is False
    assert status.checks["required_rule_coverage"] == "UNAVAILABLE"


@pytest.mark.parametrize(
    ("source_entries", "expected_internal"),
    [
        (
            [
                source_entry(
                    observed_at=datetime.now(UTC),
                    retrieved_at=datetime.now(UTC) - timedelta(hours=3),
                )
            ],
            "STALE",
        ),
        ([source_entry(availability="UNAVAILABLE")], "UNAVAILABLE"),
        ([source_entry(active=False)], "QUARANTINED"),
    ],
)
def test_readiness_reports_derived_source_state_but_public_health_redacts_it(
    monkeypatch: pytest.MonkeyPatch,
    source_entries: list[tuple[Any, ...]],
    expected_internal: str,
) -> None:
    use_connection(monkeypatch, FakeConnection(source_entries=source_entries))
    status = runtime.check_readiness(Settings())
    assert status.ready is False
    assert status.checks["required_source_coverage"] == expected_internal
    assert status.public_checks()["required_source_coverage"] == "UNAVAILABLE"


def test_readiness_rejects_empty_source_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_connection(monkeypatch, FakeConnection(source_manifest=[]))
    status = runtime.check_readiness(Settings())
    assert status.ready is False
    assert status.checks["required_source_coverage"] == "UNAVAILABLE"


def test_readiness_degrades_malformed_source_rows_instead_of_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = list(source_entry())
    invalid[9] = "NOT_AN_ACCESS_METHOD"
    use_connection(monkeypatch, FakeConnection(source_entries=[tuple(invalid)]))
    status = runtime.check_readiness(Settings())
    assert status.ready is False
    assert status.checks["required_source_coverage"] == "UNAVAILABLE"


def test_readiness_passes_with_migration_and_required_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_connection(monkeypatch, FakeConnection())
    status = runtime.check_readiness(Settings())
    assert status.ready is True
    assert status.public_checks() == {
        "database": "OK",
        "migration": "OK",
        "required_source_coverage": "OK",
        "required_source_snapshot_evidence": "OK",
        "required_rule_coverage": "OK",
    }


def test_readiness_missing_raw_object_root_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection()
    use_connection(monkeypatch, connection)
    monkeypatch.setattr(
        runtime,
        "LocalImmutableRawObjectStore",
        lambda root: (_ for _ in ()).throw(SourceSnapshotPersistenceError()),
    )
    status = runtime.check_readiness(Settings())
    assert status.ready is False
    assert status.checks["required_source_snapshot_evidence"] == "UNAVAILABLE"


def test_snapshot_readiness_builder_uses_durable_read_adapters(
    tmp_path: Path,
) -> None:
    root = tmp_path / "raw"
    root.mkdir(mode=0o700)
    service = runtime.build_source_snapshot_readiness_service(
        Settings(raw_object_root=root), cast(Any, object())
    )
    assert isinstance(service._object_store, LocalImmutableRawObjectStore)


def test_bootstrap_and_worker_heartbeat_write_expected_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection()
    use_connection(monkeypatch, connection)
    bootstrapped: list[str] = []

    def preflight(active_connection: object, registration: object) -> object:
        del active_connection, registration
        bootstrapped.append("preflight")
        return DemoSourcePreflightState.EMPTY

    monkeypatch.setattr(
        runtime,
        "prepare_demo_source_bootstrap",
        preflight,
    )
    monkeypatch.setattr(
        runtime,
        "_bootstrap_demo_source_snapshots",
        lambda settings, active_connection: bootstrapped.append("source"),
    )
    monkeypatch.setattr(
        runtime,
        "_bootstrap_demo_rule_bundle",
        lambda settings, active_connection, now: bootstrapped.append("rule"),
    )
    runtime.bootstrap_demo(Settings())
    runtime.record_worker_heartbeat(Settings())
    assert bootstrapped == ["preflight", "source", "rule"]
    assert not any(
        "INSERT INTO runtime_coverage" in query for query, _ in connection.executed
    )
    assert any(
        "INSERT INTO source_set_manifest" in query for query, _ in connection.executed
    )
    assert any(
        "INSERT INTO source_registry" in query for query, _ in connection.executed
    )
    assert not any(
        "INSERT INTO source_runtime_observation" in query
        for query, _ in connection.executed
    )
    assert any(
        "INSERT INTO runtime_component_heartbeat" in query
        for query, _ in connection.executed
    )


def test_bootstrap_reuses_exact_registered_source_without_registry_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection()
    use_connection(monkeypatch, connection)
    bootstrapped: list[str] = []
    monkeypatch.setattr(
        runtime,
        "prepare_demo_source_bootstrap",
        lambda active_connection, registration: DemoSourcePreflightState.REGISTERED,
    )
    monkeypatch.setattr(
        runtime,
        "_bootstrap_demo_source_snapshots",
        lambda settings, active_connection: bootstrapped.append("source"),
    )
    monkeypatch.setattr(
        runtime,
        "_bootstrap_demo_rule_bundle",
        lambda settings, active_connection, now: bootstrapped.append("rule"),
    )
    runtime.bootstrap_demo(Settings())
    assert bootstrapped == ["source", "rule"]
    assert connection.executed == []


def test_demo_bootstrap_preflight_failure_has_no_downstream_bootstrap_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection()
    use_connection(monkeypatch, connection)
    monkeypatch.setattr(
        runtime,
        "prepare_demo_source_bootstrap",
        lambda active_connection, registration: (_ for _ in ()).throw(
            DemoSourceBootstrapUnavailable()
        ),
    )
    monkeypatch.setattr(
        runtime,
        "_bootstrap_demo_source_snapshots",
        lambda settings, active_connection: pytest.fail("source bootstrap ran"),
    )
    with pytest.raises(DemoSourceBootstrapUnavailable):
        runtime.bootstrap_demo(Settings())
    assert connection.executed == []


def test_worker_freshness_fails_when_readiness_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime,
        "check_readiness",
        lambda settings: runtime.RuntimeStatus(False, {"database": "UNAVAILABLE"}),
    )
    assert runtime.worker_is_fresh(Settings()) is False


@pytest.mark.parametrize(
    ("heartbeat_age", "naive", "expected"),
    [
        (None, False, False),
        (timedelta(minutes=1), False, False),
        (timedelta(), True, True),
        (timedelta(), False, True),
    ],
)
def test_worker_heartbeat_freshness(
    monkeypatch: pytest.MonkeyPatch,
    heartbeat_age: timedelta | None,
    naive: bool,
    expected: bool,
) -> None:
    monkeypatch.setattr(
        runtime,
        "check_readiness",
        lambda settings: runtime.RuntimeStatus(True, {"database": "OK"}),
    )
    heartbeat = None if heartbeat_age is None else datetime.now(UTC) - heartbeat_age
    if heartbeat is not None and naive:
        heartbeat = heartbeat.replace(tzinfo=None)
    use_connection(monkeypatch, FakeConnection(heartbeat=heartbeat))
    assert runtime.worker_is_fresh(Settings()) is expected


def test_worker_freshness_handles_database_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime,
        "check_readiness",
        lambda settings: runtime.RuntimeStatus(True, {"database": "OK"}),
    )

    def unavailable(settings: Settings) -> None:
        raise psycopg.OperationalError(f"synthetic unavailable: {settings.mode}")

    monkeypatch.setattr(runtime, "connect", unavailable)
    assert runtime.worker_is_fresh(Settings()) is False


def test_worker_freshness_rejects_invalid_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime,
        "check_readiness",
        lambda settings: runtime.RuntimeStatus(True, {"database": "OK"}),
    )
    connection = FakeConnection()
    connection.heartbeat = cast(Any, "not-a-timestamp")
    use_connection(monkeypatch, connection)
    assert runtime.worker_is_fresh(Settings()) is False
