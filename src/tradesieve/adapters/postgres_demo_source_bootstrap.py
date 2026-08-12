"""Exact, fail-closed transition from the TS-201 synthetic demo marker."""

from __future__ import annotations

import json
import re
from enum import StrEnum
from typing import Any

import psycopg
from psycopg import Connection

from tradesieve.adapters.postgres_source_registry import REGISTRATION_COLUMNS
from tradesieve.domain.source_registry import SourceRegistration

LEGACY_DEMO_SNAPSHOT_ID = "synthetic-snapshot-v1"
_REAL_SNAPSHOT_ID = re.compile(r"^snapshot-[a-f0-9]{64}$")


class DemoSourcePreflightState(StrEnum):
    EMPTY = "EMPTY"
    REGISTERED = "REGISTERED"
    LEGACY_REMOVED = "LEGACY_REMOVED"
    SNAPSHOT_GRAPH = "SNAPSHOT_GRAPH"


class DemoSourceBootstrapUnavailable(Exception):
    """Stable boundary that never reveals the conflicting persisted state."""

    def __init__(self) -> None:
        super().__init__("synthetic demo source bootstrap is unavailable")


class _UnsafeDemoState(Exception):
    pass


def prepare_demo_source_bootstrap(
    connection: Connection[Any],
    *,
    registration: SourceRegistration,
) -> DemoSourcePreflightState:
    """Lock one demo scope and classify/delete only the exact TS-201 marker.

    The session advisory lock is deliberately the first database statement. It stays
    held until the bootstrap connection closes, serializing the following registry
    and snapshot writes without broad table locks.
    """

    if not isinstance(registration, SourceRegistration) or not registration.active:
        raise ValueError("demo source preflight requires an active registration")
    scope_key = (
        f"tradesieve:demo-source:{registration.deployment_id}:"
        f"{registration.source_set_id}:{registration.source_id}"
    )
    try:
        connection.execute(
            "SELECT pg_advisory_lock(hashtextextended(%s, 0))",
            (scope_key,),
        )
        with connection.transaction():
            manifest_row = connection.execute(
                "SELECT required_source_ids FROM source_set_manifest "
                "WHERE deployment_id = %s AND source_set_id = %s FOR UPDATE",
                (registration.deployment_id, registration.source_set_id),
            ).fetchone()
            registration_row = connection.execute(
                f"SELECT {REGISTRATION_COLUMNS} FROM source_registry "
                "WHERE deployment_id = %s AND source_id = %s FOR UPDATE",
                (registration.deployment_id, registration.source_id),
            ).fetchone()
            observation_row = connection.execute(
                "SELECT availability, observed_at, active_snapshot_id, "
                "retrieved_at, effective_from FROM source_runtime_observation "
                "WHERE deployment_id = %s AND source_id = %s FOR UPDATE",
                (registration.deployment_id, registration.source_id),
            ).fetchone()
            counts = connection.execute(
                "SELECT "
                "(SELECT count(*) FROM source_raw_object_metadata "
                " WHERE deployment_id = %s AND source_id = %s), "
                "(SELECT count(*) FROM source_parsed_snapshot "
                " WHERE deployment_id = %s AND source_id = %s), "
                "(SELECT count(*) FROM source_snapshot_validation_evidence "
                " WHERE deployment_id = %s AND source_id = %s), "
                "(SELECT count(*) FROM source_snapshot_lifecycle_state "
                " WHERE deployment_id = %s AND source_id = %s), "
                "(SELECT count(*) FROM source_snapshot_lifecycle_event "
                " WHERE deployment_id = %s AND source_id = %s), "
                "(SELECT count(*) FROM source_snapshot_command_audit "
                " WHERE deployment_id = %s AND source_id = %s)",
                (registration.deployment_id, registration.source_id) * 6,
            ).fetchone()
            state = _classify(
                registration,
                manifest_row,
                registration_row,
                observation_row,
                counts,
            )
            if state is not DemoSourcePreflightState.LEGACY_REMOVED:
                return state
            assert observation_row is not None
            deleted = connection.execute(
                "DELETE FROM source_runtime_observation "
                "WHERE deployment_id = %s AND source_id = %s "
                "AND availability = %s AND active_snapshot_id = %s "
                "AND observed_at = %s AND retrieved_at = %s "
                "AND effective_from = %s",
                (
                    registration.deployment_id,
                    registration.source_id,
                    observation_row[0],
                    observation_row[2],
                    observation_row[1],
                    observation_row[3],
                    observation_row[4],
                ),
            )
            if type(deleted.rowcount) is not int or deleted.rowcount != 1:
                raise _UnsafeDemoState
            return state
    except (
        DemoSourceBootstrapUnavailable,
        _UnsafeDemoState,
        psycopg.Error,
        AttributeError,
        TypeError,
        ValueError,
    ):
        raise DemoSourceBootstrapUnavailable from None


def _classify(
    expected: SourceRegistration,
    manifest_row: tuple[Any, ...] | None,
    registration_row: tuple[Any, ...] | None,
    observation_row: tuple[Any, ...] | None,
    counts_row: tuple[Any, ...] | None,
) -> DemoSourcePreflightState:
    counts = _checked_counts(counts_row)
    registry_absent = manifest_row is None and registration_row is None
    manifest_exact = _manifest_is_exact(manifest_row, expected)
    expected_values = _registration_values(expected)
    registry_exact = manifest_exact and registration_row == expected_values
    legacy_values = (*expected_values[:13], 3600, 7200, *expected_values[15:])
    legacy_registry_exact = manifest_exact and registration_row == legacy_values
    if not registry_absent and not registry_exact and not legacy_registry_exact:
        raise _UnsafeDemoState
    if registry_absent and any(counts):
        raise _UnsafeDemoState
    if observation_row is None:
        if registry_absent:
            return DemoSourcePreflightState.EMPTY
        if legacy_registry_exact:
            raise _UnsafeDemoState
        return (
            DemoSourcePreflightState.REGISTERED
            if not any(counts)
            else DemoSourcePreflightState.SNAPSHOT_GRAPH
        )
    if (not registry_exact and not legacy_registry_exact) or len(observation_row) != 5:
        raise _UnsafeDemoState
    availability, observed_at, snapshot_id, retrieved_at, effective_from = (
        observation_row
    )
    legacy = (
        availability == "AVAILABLE"
        and snapshot_id == LEGACY_DEMO_SNAPSHOT_ID
        and observed_at == retrieved_at == effective_from
    )
    if legacy:
        if any(counts):
            raise _UnsafeDemoState
        return DemoSourcePreflightState.LEGACY_REMOVED
    if legacy_registry_exact:
        raise _UnsafeDemoState
    if (
        isinstance(snapshot_id, str)
        and _REAL_SNAPSHOT_ID.fullmatch(snapshot_id) is not None
        and any(counts)
    ):
        return DemoSourcePreflightState.SNAPSHOT_GRAPH
    raise _UnsafeDemoState


def _manifest_is_exact(
    row: tuple[Any, ...] | None, expected: SourceRegistration
) -> bool:
    if row is None or len(row) != 1:
        return False
    value = row[0]
    try:
        decoded = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return False
    return (
        isinstance(decoded, list)
        and len(decoded) == 1
        and decoded[0] == expected.source_id
    )


def _registration_values(registration: SourceRegistration) -> tuple[object, ...]:
    assert registration.access_method is not None
    assert registration.refresh_expectation is not None
    assert registration.stale_after is not None
    return (
        registration.deployment_id,
        registration.source_set_id,
        registration.source_id,
        registration.name,
        registration.owner,
        registration.responsible_operator,
        registration.jurisdiction,
        registration.legal_scope,
        registration.data_scope,
        registration.access_method.value,
        registration.credential_secret_ref,
        registration.licence_summary,
        registration.contractual_constraints,
        int(registration.refresh_expectation.total_seconds()),
        int(registration.stale_after.total_seconds()),
        registration.active,
    )


def _checked_counts(row: tuple[Any, ...] | None) -> tuple[int, ...]:
    if (
        row is None
        or len(row) != 6
        or any(type(value) is not int or value < 0 for value in row)
    ):
        raise _UnsafeDemoState
    return tuple(row)


__all__ = [
    "DemoSourceBootstrapUnavailable",
    "DemoSourcePreflightState",
    "LEGACY_DEMO_SNAPSHOT_ID",
    "prepare_demo_source_bootstrap",
]
