"""PostgreSQL official-source projection adapter mapping tests."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

import psycopg
import pytest
from psycopg import Connection

from tradesieve.adapters.postgres_official_sources import (
    OfficialSourcePersistenceError,
    PostgresOfficialSourceRepository,
)
from tradesieve.domain.eu_dual_use import (
    EU_DUAL_USE_CELEX,
    EU_DUAL_USE_EFFECTIVE_FROM,
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

from ..ofac_test_data import (
    OFAC_CONSOLIDATED_RAW,
    OFAC_SDN_RAW,
    ofac_pair,
)

NOW = datetime(2026, 8, 10, 7, tzinfo=UTC)
FSF_RAW = b"official fsf raw"
DUAL_RAW = b"official dual raw"


def fsf_snapshot() -> EuFsfSnapshot:
    alias = EuFsfAlias.create(
        logical_id="1",
        whole_name="Listed Example",
        strong=True,
        language="EN",
        native_locator="/entity/alias/1",
    )
    identifier = EuFsfIdentifier.create(
        logical_id="10",
        type_code="regnumber",
        number="36-3823186",
        country_code="US",
        known_expired=False,
        known_false=False,
        reported_lost=False,
        revoked_by_issuer=False,
        native_locator="/entity/identifier/10",
    )
    return EuFsfSnapshot(
        generation_date=NOW - timedelta(hours=2),
        global_file_id="184961",
        raw_content_hash=f"sha256:{hashlib.sha256(FSF_RAW).hexdigest()}",
        raw_byte_length=len(FSF_RAW),
        entities=(
            EuFsfEntity(
                logical_id="1",
                eu_reference_number="EU.1",
                united_nations_id=None,
                designation_date=date(2026, 8, 1),
                subject_type=EuFsfSubjectType.ENTERPRISE,
                regulation=EuFsfRegulation(
                    programme="TEST",
                    number_title="2026/1",
                    publication_date=date(2026, 8, 1),
                    entry_into_force_date=date(2026, 8, 2),
                    publication_url="https://eur-lex.europa.eu/example",
                    native_locator="/entity/regulation",
                ),
                aliases=(alias,),
                identifiers=(identifier,),
            ),
        ),
    )


def dual_list(*, retrieved_at: datetime | None = None) -> EuDualUseControlList:
    return EuDualUseControlList(
        retrieved_at=retrieved_at or NOW - timedelta(minutes=2),
        source_archive_hash=f"sha256:{hashlib.sha256(DUAL_RAW).hexdigest()}",
        source_archive_bytes=len(DUAL_RAW),
        formex_document_hash="sha256:" + "c" * 64,
        entries=tuple(
            EuDualUseControlEntry(
                code=f"{category}A{index:03d}",
                text=f"Entry {category}-{index}",
                native_locator=f"/ANNEX/NP[NO.P='{category}A{index:03d}']",
            )
            for category in range(10)
            for index in range(30)
        ),
    )


def bundle() -> OfficialSourceBundle:
    ofac_sdn, ofac_consolidated = ofac_pair(retrieved_at=NOW - timedelta(minutes=1))
    return OfficialSourceBundle(
        fsf_raw_content=FSF_RAW,
        fsf_content_type="application/xml",
        fsf_retrieved_at=NOW - timedelta(minutes=3),
        fsf_snapshot=fsf_snapshot(),
        dual_use_raw_content=DUAL_RAW,
        dual_use_content_type="application/zip",
        dual_use_control_list=dual_list(),
        ofac_sdn_raw_content=OFAC_SDN_RAW,
        ofac_sdn_content_type="text/xml",
        ofac_sdn_snapshot=ofac_sdn,
        ofac_consolidated_raw_content=OFAC_CONSOLIDATED_RAW,
        ofac_consolidated_content_type="text/xml",
        ofac_consolidated_snapshot=ofac_consolidated,
        activated_at=NOW,
    )


class FakeResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows


class FakeTransaction:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def __enter__(self) -> FakeTransaction:
        self.connection.transactions += 1
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class FakeCursor:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def copy(self, query: str) -> FakeCopy:
        rows: list[tuple[object, ...]] = []
        self.connection.many.append((query, rows))
        return FakeCopy(rows)


class FakeCopy:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows

    def __enter__(self) -> FakeCopy:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def write_row(self, row: tuple[object, ...]) -> None:
        self.rows.append(row)


class FakeConnection:
    def __init__(
        self,
        value: OfficialSourceBundle,
        *,
        existing: bool = False,
        faults: dict[str, object] | None = None,
    ) -> None:
        self.value = value
        self.existing = existing
        self.faults = faults or {}
        self.executed: list[tuple[str, object | None]] = []
        self.many: list[tuple[str, list[tuple[object, ...]]]] = []
        self.transactions = 0

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def execute(self, query: str, params: object | None = None) -> FakeResult:
        self.executed.append((query, params))
        for marker, response in self.faults.items():
            if marker in query:
                if isinstance(response, Exception):
                    raise response
                return FakeResult(cast(list[tuple[Any, ...]], response))
        value = self.value
        fsf = value.fsf_snapshot
        dual = value.dual_use_control_list
        ofac_snapshots = {
            value.ofac_sdn_snapshot.snapshot_id: value.ofac_sdn_snapshot,
            value.ofac_consolidated_snapshot.snapshot_id: (
                value.ofac_consolidated_snapshot
            ),
        }
        if query.startswith("SELECT byte_length, media_type"):
            assert isinstance(params, tuple)
            if params[0] == fsf.raw_content_hash:
                return FakeResult([(len(FSF_RAW), value.fsf_content_type)])
            if params[0] == dual.source_archive_hash:
                return FakeResult([(len(DUAL_RAW), value.dual_use_content_type)])
            if params[0] == value.ofac_sdn_snapshot.raw_content_hash:
                return FakeResult([(len(OFAC_SDN_RAW), value.ofac_sdn_content_type)])
            return FakeResult(
                [
                    (
                        len(OFAC_CONSOLIDATED_RAW),
                        value.ofac_consolidated_content_type,
                    )
                ]
            )
        if query.startswith("INSERT INTO eu_fsf_official_snapshot"):
            return FakeResult([] if self.existing else [(fsf.snapshot_id,)])
        if query.startswith("SELECT snapshot_content_hash, list_kind"):
            assert isinstance(params, tuple)
            ofac = ofac_snapshots[cast(str, params[0])]
            counts = (
                len(ofac.entries),
                sum(len(entry.programs) for entry in ofac.entries),
                sum(len(entry.aliases) for entry in ofac.entries),
                sum(len(entry.addresses) for entry in ofac.entries),
                sum(len(entry.identifiers) for entry in ofac.entries),
                sum(len(entry.facts) for entry in ofac.entries),
                sum(entry.vessel_info is not None for entry in ofac.entries),
            )
            return FakeResult(
                [
                    (
                        ofac.content_hash,
                        ofac.list_kind.value,
                        ofac.publish_date,
                        ofac.declared_record_count,
                        ofac.source_last_modified,
                        ofac.raw_content_hash,
                        ofac.raw_byte_length,
                        *counts,
                    )
                ]
            )
        if query.startswith("SELECT snapshot_content_hash, raw_content_hash"):
            if "eu_fsf" in query:
                return FakeResult(
                    [
                        (
                            fsf.content_hash,
                            fsf.raw_content_hash,
                            fsf.raw_byte_length,
                            fsf.generation_date.isoformat(),
                            fsf.global_file_id,
                            len(fsf.entities),
                            sum(len(item.aliases) for item in fsf.entities),
                            sum(len(item.identifiers) for item in fsf.entities),
                        )
                    ]
                )
            if "eu_dual_use" in query:
                return FakeResult(
                    [
                        (
                            dual.content_hash,
                            dual.source_archive_hash,
                            dual.source_archive_bytes,
                            dual.formex_document_hash,
                            EU_DUAL_USE_CELEX,
                            EU_DUAL_USE_EFFECTIVE_FROM,
                            len(dual.entries),
                        )
                    ]
                )
            raise AssertionError("unexpected official snapshot query")
        if query.startswith("INSERT INTO eu_dual_use_official_snapshot"):
            return FakeResult([] if self.existing else [(dual.snapshot_id,)])
        if query.startswith("INSERT INTO ofac_sls_official_snapshot"):
            assert isinstance(params, tuple)
            return FakeResult([] if self.existing else [(params[0],)])
        if query.startswith("SELECT bundle_content_hash"):
            return FakeResult(
                [
                    (
                        value.content_hash,
                        fsf.snapshot_id,
                        fsf.content_hash,
                        dual.snapshot_id,
                        dual.content_hash,
                        value.ofac_sdn_snapshot.snapshot_id,
                        value.ofac_sdn_snapshot.content_hash,
                        value.ofac_consolidated_snapshot.snapshot_id,
                        value.ofac_consolidated_snapshot.content_hash,
                    )
                ]
            )
        if "FROM official_screening_source_state WHERE singleton" in query:
            return FakeResult(
                [(value.bundle_id, value.content_hash)] if self.existing else []
            )
        if query.startswith("INSERT INTO official_screening_source_activation_event"):
            return FakeResult([(1,)])
        if query.startswith("SELECT state.active_bundle_id"):
            return FakeResult(
                [
                    (
                        value.bundle_id,
                        value.content_hash,
                        value.fsf_retrieved_at,
                        dual.retrieved_at,
                        value.ofac_sdn_snapshot.retrieved_at,
                        value.ofac_consolidated_snapshot.retrieved_at,
                        value.activated_at,
                        fsf.snapshot_id,
                        fsf.content_hash,
                        dual.snapshot_id,
                        dual.content_hash,
                        value.ofac_sdn_snapshot.snapshot_id,
                        value.ofac_sdn_snapshot.content_hash,
                        value.ofac_consolidated_snapshot.snapshot_id,
                        value.ofac_consolidated_snapshot.content_hash,
                        value.activated_at,
                    )
                ]
            )
        if query.startswith("SELECT generation_date"):
            return FakeResult(
                [
                    (
                        fsf.generation_date.isoformat(),
                        fsf.global_file_id,
                        fsf.raw_content_hash,
                        fsf.raw_byte_length,
                        len(fsf.entities),
                        sum(len(item.aliases) for item in fsf.entities),
                        sum(len(item.identifiers) for item in fsf.entities),
                    )
                ]
            )
        if query.startswith("SELECT entity_logical_id, eu_reference_number"):
            return FakeResult(
                [
                    (
                        item.logical_id,
                        item.eu_reference_number,
                        item.united_nations_id,
                        item.designation_date,
                        item.subject_type.value,
                        item.regulation.programme,
                        item.regulation.number_title,
                        item.regulation.publication_date,
                        item.regulation.entry_into_force_date,
                        item.regulation.publication_url,
                        item.regulation.native_locator,
                        item.entity_hash,
                    )
                    for item in fsf.entities
                ]
            )
        if query.startswith("SELECT entity_logical_id, alias_logical_id"):
            return FakeResult(
                [
                    (
                        entity.logical_id,
                        item.logical_id,
                        item.whole_name,
                        item.normalized_name,
                        item.strong,
                        item.language,
                        item.native_locator,
                        item.assertion_hash,
                    )
                    for entity in fsf.entities
                    for item in entity.aliases
                ]
            )
        if query.startswith("SELECT entity_logical_id, identifier_logical_id"):
            return FakeResult(
                [
                    (
                        entity.logical_id,
                        item.logical_id,
                        item.type_code,
                        item.number,
                        item.normalized_number,
                        item.country_code,
                        item.known_expired,
                        item.known_false,
                        item.reported_lost,
                        item.revoked_by_issuer,
                        item.native_locator,
                        item.assertion_hash,
                    )
                    for entity in fsf.entities
                    for item in entity.identifiers
                ]
            )
        if query.startswith("SELECT raw_content_hash, raw_byte_length"):
            return FakeResult(
                [
                    (
                        dual.source_archive_hash,
                        dual.source_archive_bytes,
                        dual.formex_document_hash,
                        EU_DUAL_USE_CELEX,
                        EU_DUAL_USE_EFFECTIVE_FROM,
                        len(dual.entries),
                    )
                ]
            )
        if query.startswith("SELECT control_code"):
            return FakeResult(
                [
                    (item.code, item.text, item.native_locator, item.content_hash)
                    for item in dual.entries
                ]
            )
        if query.startswith("SELECT list_kind"):
            assert isinstance(params, tuple)
            ofac = ofac_snapshots[cast(str, params[0])]
            return FakeResult(
                [
                    (
                        ofac.list_kind.value,
                        ofac.publish_date,
                        ofac.declared_record_count,
                        ofac.source_last_modified,
                        ofac.raw_content_hash,
                        ofac.raw_byte_length,
                        len(ofac.entries),
                        sum(len(entry.programs) for entry in ofac.entries),
                        sum(len(entry.aliases) for entry in ofac.entries),
                        sum(len(entry.addresses) for entry in ofac.entries),
                        sum(len(entry.identifiers) for entry in ofac.entries),
                        sum(len(entry.facts) for entry in ofac.entries),
                        sum(entry.vessel_info is not None for entry in ofac.entries),
                    )
                ]
            )
        if query.startswith("SELECT entry_uid, first_name"):
            assert isinstance(params, tuple)
            ofac = ofac_snapshots[cast(str, params[0])]
            return FakeResult(
                [
                    (
                        entry.uid,
                        entry.first_name,
                        entry.last_name,
                        entry.whole_name,
                        entry.normalized_name,
                        entry.title,
                        entry.subject_type.value,
                        entry.remarks,
                        entry.entry_hash,
                    )
                    for entry in ofac.entries
                ]
            )
        if query.startswith("SELECT entry_uid, program_sequence"):
            assert isinstance(params, tuple)
            ofac = ofac_snapshots[cast(str, params[0])]
            return FakeResult(
                [
                    (entry.uid, sequence, program)
                    for entry in ofac.entries
                    for sequence, program in enumerate(entry.programs, start=1)
                ]
            )
        if query.startswith("SELECT entry_uid, alias_uid"):
            assert isinstance(params, tuple)
            ofac = ofac_snapshots[cast(str, params[0])]
            return FakeResult(
                [
                    (
                        entry.uid,
                        alias.uid,
                        alias.alias_type,
                        alias.category,
                        alias.whole_name,
                        alias.normalized_name,
                        alias.native_locator,
                    )
                    for entry in ofac.entries
                    for alias in entry.aliases
                ]
            )
        if query.startswith("SELECT entry_uid, address_uid"):
            assert isinstance(params, tuple)
            ofac = ofac_snapshots[cast(str, params[0])]
            return FakeResult(
                [
                    (
                        entry.uid,
                        address.uid,
                        *(
                            address.address_lines
                            + (None,) * (3 - len(address.address_lines))
                        ),
                        address.city,
                        address.state_or_province,
                        address.postal_code,
                        address.country,
                        address.region,
                        address.native_locator,
                    )
                    for entry in ofac.entries
                    for address in entry.addresses
                ]
            )
        if query.startswith("SELECT entry_uid, identifier_uid"):
            assert isinstance(params, tuple)
            ofac = ofac_snapshots[cast(str, params[0])]
            return FakeResult(
                [
                    (
                        entry.uid,
                        identifier.uid,
                        identifier.type_code,
                        identifier.number,
                        identifier.normalized_number,
                        identifier.country,
                        identifier.issue_date,
                        identifier.expiration_date,
                        identifier.native_locator,
                    )
                    for entry in ofac.entries
                    for identifier in entry.identifiers
                ]
            )
        if query.startswith("SELECT entry_uid, fact_uid"):
            assert isinstance(params, tuple)
            ofac = ofac_snapshots[cast(str, params[0])]
            return FakeResult(
                [
                    (
                        entry.uid,
                        fact.uid,
                        fact.kind.value,
                        fact.value,
                        fact.main_entry,
                        fact.native_locator,
                    )
                    for entry in ofac.entries
                    for fact in entry.facts
                ]
            )
        if query.startswith("SELECT entry_uid, call_sign"):
            assert isinstance(params, tuple)
            ofac = ofac_snapshots[cast(str, params[0])]
            return FakeResult(
                [
                    (
                        entry.uid,
                        entry.vessel_info.call_sign,
                        entry.vessel_info.vessel_type,
                        entry.vessel_info.vessel_flag,
                        entry.vessel_info.vessel_owner,
                        entry.vessel_info.tonnage,
                        entry.vessel_info.gross_registered_tonnage,
                        entry.vessel_info.native_locator,
                    )
                    for entry in ofac.entries
                    if entry.vessel_info is not None
                ]
            )
        return FakeResult([])


def repository(connection: FakeConnection) -> PostgresOfficialSourceRepository:
    return PostgresOfficialSourceRepository(cast(Connection[Any], connection))


def test_applied_projection_write_and_verified_active_round_trip() -> None:
    value = bundle()
    connection = FakeConnection(value)
    subject = repository(connection)

    assert subject.activate(value) is OfficialSourceWriteOutcome.APPLIED
    active = subject.get_active()

    assert active.bundle_id == value.bundle_id
    assert active.fsf_snapshot == value.fsf_snapshot
    assert active.dual_use_control_list == value.dual_use_control_list
    assert active.ofac_sdn_snapshot == value.ofac_sdn_snapshot
    assert active.ofac_consolidated_snapshot == value.ofac_consolidated_snapshot
    assert connection.transactions == 1
    assert [len(rows) for _, rows in connection.many] == [1, 1, 1, 300] + [1] * 14
    assert any("LOCK TABLE" in query for query, _ in connection.executed)


def test_idempotent_write_reuses_projections_and_updates_observation_only() -> None:
    value = bundle()
    connection = FakeConnection(value, existing=True)
    assert (
        repository(connection).activate(value) is OfficialSourceWriteOutcome.IDEMPOTENT
    )
    assert connection.many == []
    assert any(
        query.startswith("UPDATE official_screening_source_state")
        for query, _ in connection.executed
    )


def test_repository_rejects_untyped_inputs_and_redacts_database_errors() -> None:
    with pytest.raises(ValueError):
        PostgresOfficialSourceRepository(cast(Any, object()))
    with pytest.raises(ValueError):
        repository(FakeConnection(bundle())).activate(cast(Any, "bad"))
    connection = FakeConnection(
        bundle(),
        faults={"INSERT INTO official_source_raw_object": psycopg.Error("secret")},
    )
    with pytest.raises(OfficialSourcePersistenceError) as caught:
        repository(connection).activate(bundle())
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("sequence_rows", [[], [(True,)], [("1",)], [(0,)], [(1, 2)]])
def test_activation_rejects_invalid_sequence_result(
    sequence_rows: list[tuple[Any, ...]],
) -> None:
    connection = FakeConnection(
        bundle(),
        faults={
            "INSERT INTO official_screening_source_activation_event": sequence_rows
        },
    )
    with pytest.raises(OfficialSourcePersistenceError):
        repository(connection).activate(bundle())


def test_existing_projection_and_raw_conflicts_fail_closed() -> None:
    cases: tuple[dict[str, object], ...] = (
        {"SELECT byte_length, media_type": [(999, "bad")]},
        {
            "SELECT snapshot_content_hash, raw_content_hash, raw_byte_length, generation_date": [
                ("bad",)
            ]
        },
        {
            "SELECT snapshot_content_hash, raw_content_hash, raw_byte_length, formex_document_hash": [
                ("bad",)
            ]
        },
        {"SELECT snapshot_content_hash, list_kind, publish_date": [("bad",)]},
        {"SELECT bundle_content_hash": [("bad",)]},
    )
    for faults in cases:
        connection = FakeConnection(bundle(), existing=True, faults=faults)
        with pytest.raises(OfficialSourcePersistenceError):
            repository(connection).activate(bundle())


def test_insert_returning_shape_conflicts_fail_closed() -> None:
    cases: tuple[dict[str, object], ...] = (
        {"INSERT INTO eu_fsf_official_snapshot": [("bad",)]},
        {"INSERT INTO eu_dual_use_official_snapshot": [("bad",)]},
        {"INSERT INTO ofac_sls_official_snapshot": [("bad",)]},
    )
    for faults in cases:
        connection = FakeConnection(bundle(), faults=faults)
        with pytest.raises(OfficialSourcePersistenceError):
            repository(connection).activate(bundle())


def test_active_read_rejects_missing_or_malformed_state() -> None:
    for rows in ([], [("bad",)], [("x",) * 16]):
        connection = FakeConnection(
            bundle(), faults={"SELECT state.active_bundle_id": rows}
        )
        with pytest.raises(OfficialSourcePersistenceError):
            repository(connection).get_active()


def test_active_read_rejects_corrupt_projection_rows() -> None:
    value = bundle()
    bad_time = list(
        FakeConnection(value).execute("SELECT state.active_bundle_id").fetchone() or ()
    )
    assert bad_time
    cases: tuple[dict[str, object], ...] = (
        {
            "SELECT state.active_bundle_id": [
                tuple(bad_time[:2] + ["bad"] + bad_time[3:])
            ]
        },
        {
            "SELECT generation_date": [],
        },
        {
            "SELECT entity_logical_id, eu_reference_number": [("bad",)],
        },
        {
            "SELECT entity_logical_id, alias_logical_id": [("bad",)],
        },
        {
            "SELECT entity_logical_id, identifier_logical_id": [("bad",)],
        },
        {
            "SELECT raw_content_hash, raw_byte_length": [],
        },
        {
            "SELECT control_code": [("bad",)],
        },
    )
    for faults in cases:
        connection = FakeConnection(value, faults=faults)
        with pytest.raises(OfficialSourcePersistenceError):
            repository(connection).get_active()


def test_active_read_rejects_time_hash_count_and_dependency_corruption() -> None:
    value = bundle()
    connection = FakeConnection(value)
    state = list(connection.execute("SELECT state.active_bundle_id").fetchone() or ())
    entity_rows = connection.execute(
        "SELECT entity_logical_id, eu_reference_number"
    ).fetchall()
    alias_rows = connection.execute(
        "SELECT entity_logical_id, alias_logical_id"
    ).fetchall()
    identifier_rows = connection.execute(
        "SELECT entity_logical_id, identifier_logical_id"
    ).fetchall()
    entry_rows = connection.execute("SELECT control_code").fetchall()
    fsf_metadata = list(connection.execute("SELECT generation_date").fetchone() or ())

    event_after_update = list(state)
    event_after_update[15] = value.activated_at + timedelta(seconds=1)
    observation_before_generation = list(state)
    observation_before_generation[2] = value.fsf_snapshot.generation_date - timedelta(
        seconds=1
    )
    generation_after_observation = list(fsf_metadata)
    generation_after_observation[0] = value.fsf_retrieved_at + timedelta(seconds=1)
    invalid_generation_type = list(fsf_metadata)
    invalid_generation_type[0] = "bad"
    bad_alias_hash = list(alias_rows[0])
    bad_alias_hash[7] = "sha256:" + "f" * 64
    bad_identifier_hash = list(identifier_rows[0])
    bad_identifier_hash[11] = "sha256:" + "f" * 64
    bad_entity_hash = list(entity_rows[0])
    bad_entity_hash[11] = "sha256:" + "f" * 64
    alias_count_two = list(fsf_metadata)
    alias_count_two[5] = 2
    ghost_alias = list(alias_rows[0])
    ghost_alias[0] = "2"
    wrong_fsf_hash_state = list(state)
    wrong_fsf_hash_state[8] = "sha256:" + "f" * 64
    bad_entry_hash = list(entry_rows[0])
    bad_entry_hash[3] = "sha256:" + "f" * 64
    wrong_dual_hash_state = list(state)
    wrong_dual_hash_state[10] = "sha256:" + "f" * 64

    cases: tuple[dict[str, object], ...] = (
        {"SELECT state.active_bundle_id": [tuple(event_after_update)]},
        {"SELECT state.active_bundle_id": [tuple(observation_before_generation)]},
        {"SELECT generation_date": [tuple(generation_after_observation)]},
        {"SELECT generation_date": [tuple(invalid_generation_type)]},
        {"SELECT entity_logical_id, eu_reference_number": []},
        {"SELECT entity_logical_id, alias_logical_id": [tuple(bad_alias_hash)]},
        {
            "SELECT entity_logical_id, identifier_logical_id": [
                tuple(bad_identifier_hash)
            ]
        },
        {"SELECT entity_logical_id, eu_reference_number": [tuple(bad_entity_hash)]},
        {
            "SELECT generation_date": [tuple(alias_count_two)],
            "SELECT entity_logical_id, alias_logical_id": [
                alias_rows[0],
                tuple(ghost_alias),
            ],
        },
        {"SELECT state.active_bundle_id": [tuple(wrong_fsf_hash_state)]},
        {"SELECT control_code": [tuple(bad_entry_hash), *entry_rows[1:]]},
        {"SELECT control_code": [("bad",), *entry_rows[1:]]},
        {"SELECT state.active_bundle_id": [tuple(wrong_dual_hash_state)]},
        {"SELECT state.active_bundle_id": psycopg.Error("secret")},
    )
    for faults in cases:
        with pytest.raises(OfficialSourcePersistenceError):
            repository(FakeConnection(value, faults=faults)).get_active()


def test_active_read_rejects_dual_snapshot_shape_and_entry_count_corruption() -> None:
    value = bundle()
    connection = FakeConnection(value)
    dual_row = list(
        connection.execute("SELECT raw_content_hash, raw_byte_length").fetchone() or ()
    )
    cases: tuple[list[tuple[Any, ...]], ...] = (
        [("bad",)],
        [tuple(dual_row[:3] + ["wrong", *dual_row[4:]])],
        [
            tuple(
                dual_row[:4]
                + [EU_DUAL_USE_EFFECTIVE_FROM + timedelta(days=1), dual_row[5]]
            )
        ],
        [tuple(dual_row[:5] + [299])],
    )
    for rows in cases:
        with pytest.raises(OfficialSourcePersistenceError):
            repository(
                FakeConnection(
                    value,
                    faults={"SELECT raw_content_hash, raw_byte_length": rows},
                )
            ).get_active()


def test_active_read_rejects_every_ofac_projection_corruption_shape() -> None:
    value = bundle()
    connection = FakeConnection(value)
    snapshot = value.ofac_sdn_snapshot
    params = (snapshot.snapshot_id, snapshot.content_hash)
    metadata = list(connection.execute("SELECT list_kind", params).fetchone() or ())
    entry_rows = connection.execute(
        "SELECT entry_uid, first_name", (snapshot.snapshot_id,)
    ).fetchall()
    program_rows = connection.execute(
        "SELECT entry_uid, program_sequence", (snapshot.snapshot_id,)
    ).fetchall()
    alias_rows = connection.execute(
        "SELECT entry_uid, alias_uid", (snapshot.snapshot_id,)
    ).fetchall()
    address_rows = connection.execute(
        "SELECT entry_uid, address_uid", (snapshot.snapshot_id,)
    ).fetchall()
    identifier_rows = connection.execute(
        "SELECT entry_uid, identifier_uid", (snapshot.snapshot_id,)
    ).fetchall()
    fact_rows = connection.execute(
        "SELECT entry_uid, fact_uid", (snapshot.snapshot_id,)
    ).fetchall()
    vessel_rows = connection.execute(
        "SELECT entry_uid, call_sign", (snapshot.snapshot_id,)
    ).fetchall()
    assert len(metadata) == 13
    assert all(
        rows
        for rows in (
            entry_rows,
            program_rows,
            alias_rows,
            address_rows,
            identifier_rows,
            fact_rows,
            vessel_rows,
        )
    )

    count_mismatch = list(metadata)
    count_mismatch[7] = 2
    declared_count_mismatch = list(metadata)
    declared_count_mismatch[2] = 2
    duplicate_vessel_counts = list(metadata)
    duplicate_vessel_counts[12] = 2
    orphan_alias_counts = list(metadata)
    orphan_alias_counts[8] = 2
    bad_entry_hash = list(entry_rows[0])
    bad_entry_hash[8] = "sha256:" + "f" * 64
    orphan_alias = (
        "999",
        "998",
        "a.k.a.",
        "strong",
        "Orphan Example",
        "orphan example",
        "/sdnList/sdnEntry[uid='999']/akaList/aka[1]",
    )

    cases: tuple[dict[str, object], ...] = (
        {"SELECT list_kind": []},
        {"SELECT list_kind": [("bad",)]},
        {"SELECT list_kind": [tuple(count_mismatch)]},
        {"SELECT list_kind": [tuple(declared_count_mismatch)]},
        {"SELECT entry_uid, program_sequence": [("bad",)]},
        {"SELECT entry_uid, alias_uid": [("bad",)]},
        {"SELECT entry_uid, address_uid": [("bad",)]},
        {"SELECT entry_uid, identifier_uid": [("bad",)]},
        {"SELECT entry_uid, fact_uid": [("bad",)]},
        {"SELECT entry_uid, call_sign": [("bad",)]},
        {
            "SELECT list_kind": [tuple(duplicate_vessel_counts)],
            "SELECT entry_uid, call_sign": [vessel_rows[0], vessel_rows[0]],
        },
        {"SELECT entry_uid, first_name": [("bad",)]},
        {
            "SELECT entry_uid, program_sequence": [
                (program_rows[0][0], 2, program_rows[0][2])
            ]
        },
        {"SELECT entry_uid, first_name": [tuple(bad_entry_hash)]},
        {
            "SELECT list_kind": [tuple(orphan_alias_counts)],
            "SELECT entry_uid, alias_uid": [alias_rows[0], orphan_alias],
        },
    )
    for faults in cases:
        with pytest.raises(OfficialSourcePersistenceError):
            repository(FakeConnection(value, faults=faults)).get_active()


def test_active_read_rejects_ofac_snapshot_identity_and_hash_mismatch() -> None:
    value = bundle()
    connection = FakeConnection(value)
    snapshot = value.ofac_sdn_snapshot
    params = (snapshot.snapshot_id, snapshot.content_hash)
    query_rows = {
        "SELECT list_kind": [connection.execute("SELECT list_kind", params).fetchone()],
        "SELECT entry_uid, first_name": connection.execute(
            "SELECT entry_uid, first_name", (snapshot.snapshot_id,)
        ).fetchall(),
        "SELECT entry_uid, program_sequence": connection.execute(
            "SELECT entry_uid, program_sequence", (snapshot.snapshot_id,)
        ).fetchall(),
        "SELECT entry_uid, alias_uid": connection.execute(
            "SELECT entry_uid, alias_uid", (snapshot.snapshot_id,)
        ).fetchall(),
        "SELECT entry_uid, address_uid": connection.execute(
            "SELECT entry_uid, address_uid", (snapshot.snapshot_id,)
        ).fetchall(),
        "SELECT entry_uid, identifier_uid": connection.execute(
            "SELECT entry_uid, identifier_uid", (snapshot.snapshot_id,)
        ).fetchall(),
        "SELECT entry_uid, fact_uid": connection.execute(
            "SELECT entry_uid, fact_uid", (snapshot.snapshot_id,)
        ).fetchall(),
        "SELECT entry_uid, call_sign": connection.execute(
            "SELECT entry_uid, call_sign", (snapshot.snapshot_id,)
        ).fetchall(),
    }
    assert query_rows["SELECT list_kind"] != [None]
    state = list(connection.execute("SELECT state.active_bundle_id").fetchone() or ())

    wrong_hash_state = list(state)
    wrong_hash_state[12] = "sha256:" + "f" * 64
    with pytest.raises(OfficialSourcePersistenceError):
        repository(
            FakeConnection(
                value,
                faults={"SELECT state.active_bundle_id": [tuple(wrong_hash_state)]},
            )
        ).get_active()

    wrong_id_state = list(state)
    wrong_id_state[11] = "ofac-SDN-wrong"
    with pytest.raises(OfficialSourcePersistenceError):
        repository(
            FakeConnection(
                value,
                faults={
                    "SELECT state.active_bundle_id": [tuple(wrong_id_state)],
                    **query_rows,
                },
            )
        ).get_active()
