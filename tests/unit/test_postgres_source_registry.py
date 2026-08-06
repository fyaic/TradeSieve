"""PostgreSQL source-registry adapter SQL mapping tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from psycopg import Connection

from tradesieve.adapters.postgres_source_registry import PostgresSourceRegistry
from tradesieve.domain.source_registry import (
    MAX_LONG_TEXT_LENGTH,
    MAX_SHORT_TEXT_LENGTH,
    ObservationConflict,
    ObservationWriteOutcome,
    SourceAccessMethod,
    SourceAvailability,
    SourceRegistration,
    SourceRuntimeObservation,
    SourceSetManifest,
)

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


class FakeResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows


class FakeConnection:
    def __init__(self, responses: list[list[tuple[Any, ...]]] | None = None) -> None:
        self.responses = list(responses or [])
        self.executed: list[tuple[str, object | None]] = []

    def execute(self, query: str, params: object | None = None) -> FakeResult:
        self.executed.append((query, params))
        rows = self.responses.pop(0) if self.responses else []
        return FakeResult(rows)


def adapter(connection: FakeConnection) -> PostgresSourceRegistry:
    return PostgresSourceRegistry(cast(Connection[Any], connection))


def registration_row() -> tuple[Any, ...]:
    return (
        "deployment-1",
        "source-set-1",
        "source-1",
        "Synthetic source",
        "Source owner",
        "source-operator",
        "SYNTHETIC",
        "Synthetic legal scope",
        "Synthetic data scope",
        "API",
        "vault:sources/source-1",
        "Public-safe licence summary",
        "Private contract note",
        3600,
        7200,
        True,
    )


def registration() -> SourceRegistration:
    return SourceRegistration(
        deployment_id="deployment-1",
        source_set_id="source-set-1",
        source_id="source-1",
        name="Synthetic source",
        owner="Source owner",
        responsible_operator="source-operator",
        jurisdiction="SYNTHETIC",
        legal_scope="Synthetic legal scope",
        data_scope="Synthetic data scope",
        access_method=SourceAccessMethod.API,
        credential_secret_ref="vault:sources/source-1",  # pragma: allowlist secret
        licence_summary="Public-safe licence summary",
        contractual_constraints="Private contract note",
        refresh_expectation=timedelta(hours=1),
        stale_after=timedelta(hours=2),
        active=True,
    )


def observation_row() -> tuple[Any, ...]:
    return (
        "AVAILABLE",
        NOW,
        "snapshot-1",
        NOW - timedelta(minutes=5),
        NOW - timedelta(days=1),
    )


def observation(**overrides: object) -> SourceRuntimeObservation:
    values: dict[str, object] = {
        "deployment_id": "deployment-1",
        "source_id": "source-1",
        "availability": SourceAvailability.AVAILABLE,
        "observed_at": NOW,
        "active_snapshot_id": "snapshot-1",
        "retrieved_at": NOW - timedelta(minutes=5),
        "effective_from": NOW - timedelta(days=1),
    }
    values.update(overrides)
    return SourceRuntimeObservation(**values)  # type: ignore[arg-type]


def test_manifest_round_trip_supports_json_and_missing_rows() -> None:
    connection = FakeConnection()
    repository = adapter(connection)
    manifest = SourceSetManifest(
        "deployment-1", "source-set-1", ("source-1", "source-2")
    )
    repository.save_manifest(manifest)
    assert "ON CONFLICT" in connection.executed[-1][0]
    assert "required_source_ids" in connection.executed[-1][0]

    for stored in [
        ["source-1", "source-2"],
        '["source-1", "source-2"]',
    ]:
        loaded = adapter(FakeConnection([[(stored,)]]))
        assert loaded.get_manifest("deployment-1", "source-set-1") == manifest
    assert (
        adapter(FakeConnection([[]])).get_manifest("deployment-1", "source-set-1")
        is None
    )
    with pytest.raises(ValueError, match="JSON array"):
        adapter(FakeConnection([[({"source": "source-1"},)]])).get_manifest(
            "deployment-1", "source-set-1"
        )
    for corrupt in [
        [1],
        [True],
        ["x" * 129],
        ["source-2", "source-1"],
        ["source-1", "source-1"],
    ]:
        with pytest.raises(ValueError):
            adapter(FakeConnection([[((corrupt),)]])).get_manifest(
                "deployment-1", "source-set-1"
            )


def test_registration_round_trip_persists_private_fields() -> None:
    connection = FakeConnection()
    repository = adapter(connection)
    expected = registration()
    repository.save_registration(expected)
    query, params = connection.executed[-1]
    assert "ON CONFLICT" in query
    assert "credential_secret_ref" in query
    assert params is not None
    assert "vault:sources/source-1" in cast(tuple[object, ...], params)
    assert "Private contract note" in cast(tuple[object, ...], params)

    loaded = adapter(FakeConnection([[registration_row()]])).get_registration(
        "deployment-1", "source-1"
    )
    assert loaded == expected
    assert (
        adapter(FakeConnection([[]])).get_registration("deployment-1", "missing")
        is None
    )


def test_registration_text_boundaries_round_trip_without_dto_drift() -> None:
    row = list(registration_row())
    row[3] = "n" * MAX_SHORT_TEXT_LENGTH
    row[7] = "l" * MAX_LONG_TEXT_LENGTH
    loaded = adapter(FakeConnection([[tuple(row)]])).get_registration(
        "deployment-1", "source-1"
    )
    assert loaded is not None
    assert len(loaded.name or "") == MAX_SHORT_TEXT_LENGTH
    assert len(loaded.legal_scope or "") == MAX_LONG_TEXT_LENGTH


def test_draft_row_with_optional_values_round_trips() -> None:
    row = (
        "deployment-1",
        "source-set-1",
        "source-draft",
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        False,
    )
    loaded = adapter(FakeConnection([[row]])).get_registration(
        "deployment-1", "source-draft"
    )
    assert loaded == SourceRegistration("deployment-1", "source-set-1", "source-draft")


def test_observation_write_is_monotonic_idempotent_or_conflict() -> None:
    current = observation()
    applied_connection = FakeConnection([[observation_row()]])
    assert (
        adapter(applied_connection).save_observation(current)
        is ObservationWriteOutcome.APPLIED
    )
    assert "EXCLUDED.observed_at >" in applied_connection.executed[0][0]

    idempotent = adapter(FakeConnection([[], [observation_row()]]))
    assert idempotent.save_observation(current) is ObservationWriteOutcome.IDEMPOTENT

    conflicting_row = (
        "UNAVAILABLE",
        NOW,
        "snapshot-1",
        NOW - timedelta(minutes=5),
        NOW - timedelta(days=1),
    )
    with pytest.raises(ObservationConflict):
        adapter(FakeConnection([[], [conflicting_row]])).save_observation(current)
    with pytest.raises(ObservationConflict):
        adapter(FakeConnection([[], []])).save_observation(current)


def test_list_entries_maps_observed_and_unobserved_rows() -> None:
    observed = registration_row() + observation_row()
    unobserved = (
        registration_row()[:2]
        + ("source-2",)
        + registration_row()[3:]
        + (None, None, None, None, None)
    )
    entries = adapter(
        FakeConnection([[tuple(unobserved), tuple(observed)]])
    ).list_entries("deployment-1", "source-set-1", limit=10)
    assert [entry[0].source_id for entry in entries] == ["source-2", "source-1"]
    assert entries[0][1] is None
    assert entries[1][1] == observation()


def test_list_entries_requires_a_positive_bound() -> None:
    with pytest.raises(ValueError, match="limit must be positive"):
        adapter(FakeConnection()).list_entries("deployment-1", "source-set-1", limit=0)
