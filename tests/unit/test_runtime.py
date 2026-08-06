"""Database-backed runtime status tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any, Self, cast

import psycopg
import pytest

from tradesieve import runtime
from tradesieve.config import Settings
from tradesieve.domain.source_registry import ObservationConflict


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
        coverage: list[tuple[Any, ...]] | None = None,
        source_manifest: list[str] | None = None,
        source_entries: list[tuple[Any, ...]] | None = None,
        observation_insert_rows: list[tuple[Any, ...]] | None = None,
        current_observation: tuple[Any, ...] | None = None,
        heartbeat: datetime | None = None,
        fail_query: str | None = None,
    ) -> None:
        self.revision = revision
        self.coverage = coverage if coverage is not None else required_coverage()
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
        if "SELECT coverage_kind" in query:
            return FakeResult(self.coverage)
        if "SELECT observed_at" in query:
            return FakeResult([] if self.heartbeat is None else [(self.heartbeat,)])
        return FakeResult([])


def required_coverage() -> list[tuple[Any, ...]]:
    return [("RULE", "synthetic-demo-rules-v1")]


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


def test_readiness_rejects_coverage_query_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_connection(monkeypatch, FakeConnection(fail_query="runtime_coverage"))
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


def test_readiness_rejects_missing_rule_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_connection(
        monkeypatch,
        FakeConnection(coverage=[]),
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
        "required_rule_coverage": "OK",
    }


def test_bootstrap_and_worker_heartbeat_write_expected_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection()
    use_connection(monkeypatch, connection)
    runtime.bootstrap_demo(Settings())
    runtime.record_worker_heartbeat(Settings())
    assert (
        sum("INSERT INTO runtime_coverage" in query for query, _ in connection.executed)
        == 1
    )
    assert any(
        "INSERT INTO source_set_manifest" in query for query, _ in connection.executed
    )
    assert any(
        "INSERT INTO source_registry" in query for query, _ in connection.executed
    )
    assert any(
        "INSERT INTO source_runtime_observation" in query
        for query, _ in connection.executed
    )
    assert any(
        "INSERT INTO runtime_component_heartbeat" in query
        for query, _ in connection.executed
    )


def test_demo_bootstrap_refuses_to_overwrite_a_newer_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    future = datetime.now(UTC) + timedelta(days=1)
    connection = FakeConnection(
        observation_insert_rows=[],
        current_observation=source_observation_row(observed_at=future),
    )
    use_connection(monkeypatch, connection)
    with pytest.raises(ObservationConflict):
        runtime.bootstrap_demo(Settings())


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
    ("heartbeat", "expected"),
    [
        (None, False),
        (datetime.now(UTC) - timedelta(minutes=1), False),
        (datetime.now(UTC).replace(tzinfo=None), True),
        (datetime.now(UTC), True),
    ],
)
def test_worker_heartbeat_freshness(
    monkeypatch: pytest.MonkeyPatch,
    heartbeat: datetime | None,
    expected: bool,
) -> None:
    monkeypatch.setattr(
        runtime,
        "check_readiness",
        lambda settings: runtime.RuntimeStatus(True, {"database": "OK"}),
    )
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
