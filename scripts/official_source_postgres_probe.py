"""Real PostgreSQL probe for official-source immutable projections."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg

from tradesieve.adapters.postgres_official_sources import (
    PostgresOfficialSourceRepository,
)
from tradesieve.config import get_settings
from tradesieve.domain.eu_dual_use import (
    EuDualUseControlEntry,
    EuDualUseControlList,
)
from tradesieve.domain.eu_fsf import (
    EuFsfAlias,
    EuFsfEntity,
    EuFsfIdentifier,
    EuFsfRegulation,
    EuFsfSnapshot,
    EuFsfSubjectType,
)
from tradesieve.domain.official_sources import (
    OfficialSourceBundle,
    OfficialSourceWriteOutcome,
)

NOW = datetime(2026, 8, 10, 8, tzinfo=UTC)
FSF_RAW = b"official-source-postgres-probe-fsf"
DUAL_RAW = b"official-source-postgres-probe-dual"


def build_bundle() -> OfficialSourceBundle:
    fsf = EuFsfSnapshot(
        generation_date=NOW - timedelta(hours=2),
        global_file_id="184961",
        raw_content_hash=f"sha256:{hashlib.sha256(FSF_RAW).hexdigest()}",
        raw_byte_length=len(FSF_RAW),
        entities=(
            EuFsfEntity(
                logical_id="1",
                eu_reference_number="EU.PROBE.1",
                united_nations_id=None,
                designation_date=date(2026, 8, 1),
                subject_type=EuFsfSubjectType.ENTERPRISE,
                regulation=EuFsfRegulation(
                    programme="PROBE",
                    number_title="2026/1",
                    publication_date=date(2026, 8, 1),
                    entry_into_force_date=date(2026, 8, 2),
                    publication_url="https://eur-lex.europa.eu/probe",
                    native_locator="/probe/regulation",
                ),
                aliases=(
                    EuFsfAlias.create(
                        logical_id="1",
                        whole_name="Synthetic Projection Probe",
                        strong=True,
                        language="EN",
                        native_locator="/probe/alias/1",
                    ),
                ),
                identifiers=(
                    EuFsfIdentifier.create(
                        logical_id="10",
                        type_code="regnumber",
                        number="PROBE-0001",
                        country_code="CN",
                        known_expired=False,
                        known_false=False,
                        reported_lost=False,
                        revoked_by_issuer=False,
                        native_locator="/probe/identifier/10",
                    ),
                ),
            ),
        ),
    )
    dual = EuDualUseControlList(
        retrieved_at=NOW - timedelta(minutes=2),
        source_archive_hash=f"sha256:{hashlib.sha256(DUAL_RAW).hexdigest()}",
        source_archive_bytes=len(DUAL_RAW),
        formex_document_hash="sha256:" + "d" * 64,
        entries=tuple(
            EuDualUseControlEntry(
                code=f"{category}A{index:03d}",
                text=f"Synthetic projection probe entry {category}-{index}",
                native_locator=f"/ANNEX/NP[NO.P='{category}A{index:03d}']",
            )
            for category in range(10)
            for index in range(30)
        ),
    )
    return OfficialSourceBundle(
        fsf_raw_content=FSF_RAW,
        fsf_content_type="application/xml",
        fsf_retrieved_at=NOW - timedelta(minutes=3),
        fsf_snapshot=fsf,
        dual_use_raw_content=DUAL_RAW,
        dual_use_content_type="application/zip",
        dual_use_control_list=dual,
        activated_at=NOW,
    )


def scalar(connection: psycopg.Connection[Any], query: str) -> object:
    row = connection.execute(query).fetchone()
    assert row is not None and len(row) == 1
    return row[0]


def expect_check_violation(connection: psycopg.Connection[Any], query: str) -> None:
    try:
        with connection.transaction():
            connection.execute(query)
    except psycopg.Error as error:
        assert error.sqlstate in {"23514", "55000"}
        return
    raise AssertionError("expected PostgreSQL check violation")


def main() -> None:
    settings = get_settings()
    bundle = build_bundle()
    with psycopg.connect(settings.database_url, autocommit=True) as connection:
        assert scalar(connection, "SELECT version_num FROM alembic_version") == (
            "20260810_0006"
        )
        repository = PostgresOfficialSourceRepository(connection)
        assert repository.activate(bundle) is OfficialSourceWriteOutcome.APPLIED
        assert repository.activate(bundle) is OfficialSourceWriteOutcome.IDEMPOTENT
        active = repository.get_active()
        assert active.fsf_snapshot == bundle.fsf_snapshot
        assert active.dual_use_control_list == bundle.dual_use_control_list
        assert (
            scalar(connection, "SELECT count(*) FROM official_source_raw_object") == 2
        )
        assert scalar(connection, "SELECT count(*) FROM eu_fsf_official_entity") == 1
        assert scalar(connection, "SELECT count(*) FROM eu_fsf_official_alias") == 1
        assert (
            scalar(connection, "SELECT count(*) FROM eu_fsf_official_identifier") == 1
        )
        assert (
            scalar(connection, "SELECT count(*) FROM eu_dual_use_official_entry") == 300
        )
        assert (
            scalar(
                connection,
                "SELECT count(*) FROM official_screening_source_activation_event",
            )
            == 1
        )
        expect_check_violation(
            connection,
            "UPDATE eu_fsf_official_alias SET strong = FALSE",
        )
        expect_check_violation(
            connection,
            "INSERT INTO eu_fsf_official_alias SELECT snapshot_id, "
            "snapshot_content_hash, entity_logical_id, '2', whole_name, "
            "normalized_name, strong, language, native_locator, assertion_hash "
            "FROM eu_fsf_official_alias LIMIT 1",
        )
        expect_check_violation(
            connection,
            "INSERT INTO official_source_raw_object VALUES "
            "('sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
            "1, 'application/octet-stream', decode('62', 'hex'), CURRENT_TIMESTAMP)",
        )
    print(
        json.dumps(
            {
                "active_round_trip": True,
                "activation_events": 1,
                "dual_use_entries": 300,
                "fsf_aliases": 1,
                "fsf_entities": 1,
                "fsf_identifiers": 1,
                "idempotent_refresh": True,
                "immutable_guards": 3,
                "migration": "20260810_0006",
                "status": "PASS",
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
