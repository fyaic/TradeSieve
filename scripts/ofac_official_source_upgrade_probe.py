"""Prove that migration 0007 preserves a legacy EU-only active bundle safely."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import psycopg
from alembic import command
from official_source_postgres_probe import build_bundle

from tradesieve.adapters.postgres_official_sources import (
    OfficialSourcePersistenceError,
    PostgresOfficialSourceRepository,
)
from tradesieve.config import get_settings
from tradesieve.domain.official_sources import OfficialSourceBundle
from tradesieve.manage import migration_config


def legacy_bundle_identity(bundle: OfficialSourceBundle) -> tuple[str, str]:
    fsf = bundle.fsf_snapshot
    dual = bundle.dual_use_control_list
    canonical = json.dumps(
        {
            "dual_use_snapshot_content_hash": dual.content_hash,
            "dual_use_snapshot_id": dual.snapshot_id,
            "fsf_snapshot_content_hash": fsf.content_hash,
            "fsf_snapshot_id": fsf.snapshot_id,
        },
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    content_hash = f"sha256:{hashlib.sha256(canonical).hexdigest()}"
    return f"official-bundle-{content_hash[7:]}", content_hash


def scalar(connection: psycopg.Connection[Any], query: str) -> object:
    row = connection.execute(query).fetchone()
    assert row is not None and len(row) == 1
    return row[0]


def main() -> None:
    settings = get_settings()
    config = migration_config(settings)
    command.upgrade(config, "20260810_0006")
    bundle = build_bundle()
    legacy_bundle_id, legacy_bundle_hash = legacy_bundle_identity(bundle)

    with psycopg.connect(settings.database_url, autocommit=True) as connection:
        repository = PostgresOfficialSourceRepository(connection)
        with connection.transaction():
            repository._persist_raw(
                bundle.fsf_snapshot.raw_content_hash,
                bundle.fsf_raw_content,
                bundle.fsf_content_type,
                bundle.fsf_retrieved_at,
            )
            repository._persist_raw(
                bundle.dual_use_control_list.source_archive_hash,
                bundle.dual_use_raw_content,
                bundle.dual_use_content_type,
                bundle.dual_use_control_list.retrieved_at,
            )
            repository._persist_fsf(
                bundle,
                snapshot_hash=bundle.fsf_snapshot.content_hash,
            )
            repository._persist_dual_use(
                bundle,
                snapshot_hash=bundle.dual_use_control_list.content_hash,
            )
            connection.execute(
                "INSERT INTO official_screening_source_bundle "
                "(bundle_id, bundle_content_hash, fsf_snapshot_id, "
                "fsf_snapshot_content_hash, dual_use_snapshot_id, "
                "dual_use_snapshot_content_hash, first_activated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    legacy_bundle_id,
                    legacy_bundle_hash,
                    bundle.fsf_snapshot.snapshot_id,
                    bundle.fsf_snapshot.content_hash,
                    bundle.dual_use_control_list.snapshot_id,
                    bundle.dual_use_control_list.content_hash,
                    bundle.activated_at,
                ),
            )
            event = connection.execute(
                "INSERT INTO official_screening_source_activation_event "
                "(bundle_id, bundle_content_hash, activated_at) "
                "VALUES (%s, %s, %s) RETURNING sequence",
                (legacy_bundle_id, legacy_bundle_hash, bundle.activated_at),
            ).fetchone()
            assert event is not None and len(event) == 1
            connection.execute(
                "INSERT INTO official_screening_source_state "
                "(singleton, active_bundle_id, active_bundle_content_hash, "
                "fsf_observed_at, dual_use_observed_at, updated_at, sequence) "
                "VALUES (TRUE, %s, %s, %s, %s, %s, %s)",
                (
                    legacy_bundle_id,
                    legacy_bundle_hash,
                    bundle.fsf_retrieved_at,
                    bundle.dual_use_control_list.retrieved_at,
                    bundle.activated_at,
                    event[0],
                ),
            )
        assert scalar(connection, "SELECT version_num FROM alembic_version") == (
            "20260810_0006"
        )
        assert (
            scalar(connection, "SELECT count(*) FROM official_source_raw_object") == 2
        )
        assert scalar(connection, "SELECT count(*) FROM eu_fsf_official_entity") == 1
        assert (
            scalar(connection, "SELECT count(*) FROM eu_dual_use_official_entry") == 300
        )

    command.upgrade(config, "head")
    with psycopg.connect(settings.database_url, autocommit=True) as connection:
        assert scalar(connection, "SELECT version_num FROM alembic_version") == (
            "20260810_0007"
        )
        assert (
            scalar(connection, "SELECT count(*) FROM official_source_raw_object") == 2
        )
        assert scalar(connection, "SELECT count(*) FROM eu_fsf_official_entity") == 1
        assert (
            scalar(connection, "SELECT count(*) FROM eu_dual_use_official_entry") == 300
        )
        state = connection.execute(
            "SELECT ofac_sdn_observed_at, ofac_consolidated_observed_at "
            "FROM official_screening_source_state WHERE singleton = TRUE"
        ).fetchone()
        assert state == (None, None)
        bundle_row = connection.execute(
            "SELECT ofac_sdn_snapshot_id, ofac_consolidated_snapshot_id "
            "FROM official_screening_source_bundle WHERE bundle_id = %s",
            (legacy_bundle_id,),
        ).fetchone()
        assert bundle_row == (None, None)
        try:
            PostgresOfficialSourceRepository(connection).get_active()
        except OfficialSourcePersistenceError:
            pass
        else:
            raise AssertionError("legacy partial active bundle must fail closed")

    print(
        json.dumps(
            {
                "from_revision": "20260810_0006",
                "legacy_active_fails_closed": True,
                "legacy_dual_entries_preserved": 300,
                "legacy_fsf_entities_preserved": 1,
                "legacy_raw_objects_preserved": 2,
                "status": "PASS",
                "to_revision": "20260810_0007",
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
