"""PostgreSQL persistence adapter for the governed source registry."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb

from tradesieve.domain.source_registry import (
    ObservationConflict,
    ObservationWriteOutcome,
    SourceAccessMethod,
    SourceAvailability,
    SourceRegistration,
    SourceRuntimeObservation,
    SourceSetManifest,
)

REGISTRATION_COLUMNS = (
    "deployment_id, source_set_id, source_id, name, owner, "
    "responsible_operator, jurisdiction, legal_scope, data_scope, access_method, "
    "credential_secret_ref, licence_summary, contractual_constraints, "
    "refresh_expectation_seconds, stale_after_seconds, active"
)
OBSERVATION_COLUMNS = (
    "availability, observed_at, active_snapshot_id, retrieved_at, effective_from"
)


class PostgresSourceRegistry:
    def __init__(self, connection: Connection[Any]) -> None:
        self._connection = connection

    def save_manifest(self, manifest: SourceSetManifest) -> None:
        self._connection.execute(
            "INSERT INTO source_set_manifest "
            "(deployment_id, source_set_id, required_source_ids) "
            "VALUES (%s, %s, %s) "
            "ON CONFLICT (deployment_id, source_set_id) DO UPDATE SET "
            "required_source_ids = EXCLUDED.required_source_ids, "
            "updated_at = CURRENT_TIMESTAMP",
            (
                manifest.deployment_id,
                manifest.source_set_id,
                Jsonb(list(manifest.required_source_ids)),
            ),
        )

    def get_manifest(
        self, deployment_id: str, source_set_id: str
    ) -> SourceSetManifest | None:
        row = self._connection.execute(
            "SELECT required_source_ids FROM source_set_manifest "
            "WHERE deployment_id = %s AND source_set_id = %s",
            (deployment_id, source_set_id),
        ).fetchone()
        if row is None:
            return None
        raw_ids = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        if not isinstance(raw_ids, list):
            raise ValueError("required source manifest must be a JSON array")
        if any(not isinstance(item, str) for item in raw_ids):
            raise ValueError("required source manifest members must be strings")
        return SourceSetManifest(
            deployment_id,
            source_set_id,
            tuple(raw_ids),
        )

    def save_registration(self, registration: SourceRegistration) -> None:
        self._connection.execute(
            "INSERT INTO source_registry ("
            + REGISTRATION_COLUMNS
            + ") VALUES ("
            + ", ".join(["%s"] * 16)
            + ") ON CONFLICT (deployment_id, source_id) DO UPDATE SET "
            "source_set_id = EXCLUDED.source_set_id, name = EXCLUDED.name, "
            "owner = EXCLUDED.owner, "
            "responsible_operator = EXCLUDED.responsible_operator, "
            "jurisdiction = EXCLUDED.jurisdiction, "
            "legal_scope = EXCLUDED.legal_scope, data_scope = EXCLUDED.data_scope, "
            "access_method = EXCLUDED.access_method, "
            "credential_secret_ref = EXCLUDED.credential_secret_ref, "
            "licence_summary = EXCLUDED.licence_summary, "
            "contractual_constraints = EXCLUDED.contractual_constraints, "
            "refresh_expectation_seconds = EXCLUDED.refresh_expectation_seconds, "
            "stale_after_seconds = EXCLUDED.stale_after_seconds, "
            "active = EXCLUDED.active, updated_at = CURRENT_TIMESTAMP",
            (
                registration.deployment_id,
                registration.source_set_id,
                registration.source_id,
                registration.name,
                registration.owner,
                registration.responsible_operator,
                registration.jurisdiction,
                registration.legal_scope,
                registration.data_scope,
                (
                    registration.access_method.value
                    if registration.access_method is not None
                    else None
                ),
                registration.credential_secret_ref,
                registration.licence_summary,
                registration.contractual_constraints,
                (
                    int(registration.refresh_expectation.total_seconds())
                    if registration.refresh_expectation is not None
                    else None
                ),
                (
                    int(registration.stale_after.total_seconds())
                    if registration.stale_after is not None
                    else None
                ),
                registration.active,
            ),
        )

    def get_registration(
        self, deployment_id: str, source_id: str
    ) -> SourceRegistration | None:
        row = self._connection.execute(
            f"SELECT {REGISTRATION_COLUMNS} FROM source_registry "
            "WHERE deployment_id = %s AND source_id = %s",
            (deployment_id, source_id),
        ).fetchone()
        return self._registration_from_row(row) if row is not None else None

    def save_observation(
        self, observation: SourceRuntimeObservation
    ) -> ObservationWriteOutcome:
        row = self._connection.execute(
            "INSERT INTO source_runtime_observation ("
            "deployment_id, source_id, availability, observed_at, "
            "active_snapshot_id, retrieved_at, effective_from"
            ") VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (deployment_id, source_id) DO UPDATE SET "
            "availability = EXCLUDED.availability, "
            "observed_at = EXCLUDED.observed_at, "
            "active_snapshot_id = EXCLUDED.active_snapshot_id, "
            "retrieved_at = EXCLUDED.retrieved_at, "
            "effective_from = EXCLUDED.effective_from "
            "WHERE EXCLUDED.observed_at > source_runtime_observation.observed_at "
            "RETURNING availability, observed_at, active_snapshot_id, "
            "retrieved_at, effective_from",
            (
                observation.deployment_id,
                observation.source_id,
                observation.availability.value,
                observation.observed_at,
                observation.active_snapshot_id,
                observation.retrieved_at,
                observation.effective_from,
            ),
        ).fetchone()
        if row is not None:
            return ObservationWriteOutcome.APPLIED
        current = self._connection.execute(
            f"SELECT {OBSERVATION_COLUMNS} FROM source_runtime_observation "
            "WHERE deployment_id = %s AND source_id = %s",
            (observation.deployment_id, observation.source_id),
        ).fetchone()
        if (
            current is not None
            and self._observation_from_row(
                observation.deployment_id, observation.source_id, current
            )
            == observation
        ):
            return ObservationWriteOutcome.IDEMPOTENT
        raise ObservationConflict

    def list_entries(
        self, deployment_id: str, source_set_id: str, *, limit: int
    ) -> tuple[tuple[SourceRegistration, SourceRuntimeObservation | None], ...]:
        if limit < 1:
            raise ValueError("source registry query limit must be positive")
        rows = self._connection.execute(
            f"SELECT {REGISTRATION_COLUMNS}, {OBSERVATION_COLUMNS} "
            "FROM source_registry AS registry "
            "LEFT JOIN source_runtime_observation AS observation "
            "USING (deployment_id, source_id) "
            "WHERE deployment_id = %s AND source_set_id = %s "
            "ORDER BY source_id LIMIT %s",
            (deployment_id, source_set_id, limit),
        ).fetchall()
        entries: list[tuple[SourceRegistration, SourceRuntimeObservation | None]] = []
        for row in rows:
            registration = self._registration_from_row(row[:16])
            observation = (
                None
                if row[17] is None
                else self._observation_from_row(
                    registration.deployment_id,
                    registration.source_id,
                    row[16:21],
                )
            )
            entries.append((registration, observation))
        return tuple(entries)

    @staticmethod
    def _registration_from_row(row: tuple[Any, ...]) -> SourceRegistration:
        return SourceRegistration(
            deployment_id=row[0],
            source_set_id=row[1],
            source_id=row[2],
            name=row[3],
            owner=row[4],
            responsible_operator=row[5],
            jurisdiction=row[6],
            legal_scope=row[7],
            data_scope=row[8],
            access_method=(SourceAccessMethod(row[9]) if row[9] is not None else None),
            credential_secret_ref=row[10],
            licence_summary=row[11],
            contractual_constraints=row[12],
            refresh_expectation=(
                timedelta(seconds=int(row[13])) if row[13] is not None else None
            ),
            stale_after=(
                timedelta(seconds=int(row[14])) if row[14] is not None else None
            ),
            active=bool(row[15]),
        )

    @staticmethod
    def _observation_from_row(
        deployment_id: str, source_id: str, row: tuple[Any, ...]
    ) -> SourceRuntimeObservation:
        return SourceRuntimeObservation(
            deployment_id=deployment_id,
            source_id=source_id,
            availability=SourceAvailability(row[0]),
            observed_at=row[1],
            active_snapshot_id=row[2],
            retrieved_at=row[3],
            effective_from=row[4],
        )
