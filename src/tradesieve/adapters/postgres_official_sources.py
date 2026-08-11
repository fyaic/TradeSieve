"""PostgreSQL immutable projections and atomic active pointer for official sources."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any, Never

import psycopg
from psycopg import Connection

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
from tradesieve.domain.ofac_sls import (
    OfacAddress,
    OfacAlias,
    OfacEntry,
    OfacFact,
    OfacFactKind,
    OfacIdentifier,
    OfacSlsListKind,
    OfacSnapshot,
    OfacSubjectType,
    OfacVesselInfo,
)
from tradesieve.domain.official_sources import (
    ActiveOfficialSources,
    OfficialSourceBundle,
    OfficialSourceWriteOutcome,
)


class OfficialSourcePersistenceError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("official source persistence is unavailable")


def _fail() -> Never:
    raise OfficialSourcePersistenceError from None


class PostgresOfficialSourceRepository:
    """Persist one complete source set and expose only its active projections."""

    def __init__(self, connection: Connection[Any]) -> None:
        if not hasattr(connection, "execute") or not hasattr(connection, "transaction"):
            raise ValueError("official source repository requires a connection")
        self._connection = connection

    def activate(self, bundle: OfficialSourceBundle) -> OfficialSourceWriteOutcome:
        if not isinstance(bundle, OfficialSourceBundle):
            raise ValueError("official source bundle must be typed")
        try:
            fsf_snapshot_hash = bundle.fsf_snapshot.content_hash
            dual_use_snapshot_hash = bundle.dual_use_control_list.content_hash
            ofac_sdn_snapshot_hash = bundle.ofac_sdn_snapshot.content_hash
            ofac_consolidated_snapshot_hash = (
                bundle.ofac_consolidated_snapshot.content_hash
            )
            bundle_id = bundle.bundle_id
            bundle_hash = "sha256:" + bundle_id.removeprefix("official-bundle-")
            with self._connection.transaction():
                self._persist_raw(
                    bundle.fsf_snapshot.raw_content_hash,
                    bundle.fsf_raw_content,
                    bundle.fsf_content_type,
                    bundle.fsf_retrieved_at,
                )
                self._persist_raw(
                    bundle.dual_use_control_list.source_archive_hash,
                    bundle.dual_use_raw_content,
                    bundle.dual_use_content_type,
                    bundle.dual_use_control_list.retrieved_at,
                )
                self._persist_raw(
                    bundle.ofac_sdn_snapshot.raw_content_hash,
                    bundle.ofac_sdn_raw_content,
                    bundle.ofac_sdn_content_type,
                    bundle.ofac_sdn_snapshot.retrieved_at,
                )
                self._persist_raw(
                    bundle.ofac_consolidated_snapshot.raw_content_hash,
                    bundle.ofac_consolidated_raw_content,
                    bundle.ofac_consolidated_content_type,
                    bundle.ofac_consolidated_snapshot.retrieved_at,
                )
                self._persist_fsf(bundle, snapshot_hash=fsf_snapshot_hash)
                self._persist_dual_use(bundle, snapshot_hash=dual_use_snapshot_hash)
                self._persist_ofac(
                    bundle.ofac_sdn_snapshot,
                    snapshot_hash=ofac_sdn_snapshot_hash,
                )
                self._persist_ofac(
                    bundle.ofac_consolidated_snapshot,
                    snapshot_hash=ofac_consolidated_snapshot_hash,
                )
                self._persist_bundle(
                    bundle,
                    bundle_id=bundle_id,
                    bundle_hash=bundle_hash,
                    fsf_snapshot_hash=fsf_snapshot_hash,
                    dual_use_snapshot_hash=dual_use_snapshot_hash,
                    ofac_sdn_snapshot_hash=ofac_sdn_snapshot_hash,
                    ofac_consolidated_snapshot_hash=(ofac_consolidated_snapshot_hash),
                )
                self._connection.execute(
                    "LOCK TABLE official_screening_source_state "
                    "IN SHARE ROW EXCLUSIVE MODE"
                )
                current = self._connection.execute(
                    "SELECT active_bundle_id, active_bundle_content_hash "
                    "FROM official_screening_source_state WHERE singleton = TRUE "
                    "FOR UPDATE"
                ).fetchone()
                if current is not None and current == (
                    bundle_id,
                    bundle_hash,
                ):
                    self._connection.execute(
                        "UPDATE official_screening_source_state SET "
                        "fsf_observed_at = GREATEST(fsf_observed_at, %s), "
                        "dual_use_observed_at = GREATEST(dual_use_observed_at, %s), "
                        "ofac_sdn_observed_at = "
                        "GREATEST(ofac_sdn_observed_at, %s), "
                        "ofac_consolidated_observed_at = "
                        "GREATEST(ofac_consolidated_observed_at, %s), "
                        "updated_at = GREATEST(updated_at, %s) "
                        "WHERE singleton = TRUE",
                        (
                            bundle.fsf_retrieved_at,
                            bundle.dual_use_control_list.retrieved_at,
                            bundle.ofac_sdn_snapshot.retrieved_at,
                            bundle.ofac_consolidated_snapshot.retrieved_at,
                            bundle.activated_at,
                        ),
                    )
                    outcome = OfficialSourceWriteOutcome.IDEMPOTENT
                else:
                    sequence_row = self._connection.execute(
                        "INSERT INTO official_screening_source_activation_event "
                        "(bundle_id, bundle_content_hash, activated_at) "
                        "VALUES (%s, %s, %s) RETURNING sequence",
                        (
                            bundle_id,
                            bundle_hash,
                            bundle.activated_at,
                        ),
                    ).fetchone()
                    if (
                        sequence_row is None
                        or len(sequence_row) != 1
                        or isinstance(sequence_row[0], bool)
                        or not isinstance(sequence_row[0], int)
                        or sequence_row[0] < 1
                    ):
                        _fail()
                    self._connection.execute(
                        "INSERT INTO official_screening_source_state "
                        "(singleton, active_bundle_id, active_bundle_content_hash, "
                        "fsf_observed_at, dual_use_observed_at, "
                        "ofac_sdn_observed_at, ofac_consolidated_observed_at, "
                        "updated_at, sequence) "
                        "VALUES (TRUE, %s, %s, %s, %s, %s, %s, %s, %s) "
                        "ON CONFLICT (singleton) DO UPDATE SET "
                        "active_bundle_id = EXCLUDED.active_bundle_id, "
                        "active_bundle_content_hash = "
                        "EXCLUDED.active_bundle_content_hash, "
                        "fsf_observed_at = EXCLUDED.fsf_observed_at, "
                        "dual_use_observed_at = EXCLUDED.dual_use_observed_at, "
                        "ofac_sdn_observed_at = EXCLUDED.ofac_sdn_observed_at, "
                        "ofac_consolidated_observed_at = "
                        "EXCLUDED.ofac_consolidated_observed_at, "
                        "updated_at = EXCLUDED.updated_at, "
                        "sequence = EXCLUDED.sequence",
                        (
                            bundle_id,
                            bundle_hash,
                            bundle.fsf_retrieved_at,
                            bundle.dual_use_control_list.retrieved_at,
                            bundle.ofac_sdn_snapshot.retrieved_at,
                            bundle.ofac_consolidated_snapshot.retrieved_at,
                            bundle.activated_at,
                            sequence_row[0],
                        ),
                    )
                    outcome = OfficialSourceWriteOutcome.APPLIED
            return outcome
        except OfficialSourcePersistenceError:
            raise
        except (psycopg.Error, ValueError, TypeError, IndexError):
            _fail()

    def get_active(self) -> ActiveOfficialSources:
        try:
            state = self._connection.execute(
                "SELECT state.active_bundle_id, "
                "state.active_bundle_content_hash, state.fsf_observed_at, "
                "state.dual_use_observed_at, state.ofac_sdn_observed_at, "
                "state.ofac_consolidated_observed_at, state.updated_at, "
                "bundle.fsf_snapshot_id, bundle.fsf_snapshot_content_hash, "
                "bundle.dual_use_snapshot_id, "
                "bundle.dual_use_snapshot_content_hash, "
                "bundle.ofac_sdn_snapshot_id, "
                "bundle.ofac_sdn_snapshot_content_hash, "
                "bundle.ofac_consolidated_snapshot_id, "
                "bundle.ofac_consolidated_snapshot_content_hash, "
                "event.activated_at "
                "FROM official_screening_source_state AS state "
                "JOIN official_screening_source_bundle AS bundle "
                "ON bundle.bundle_id = state.active_bundle_id "
                "AND bundle.bundle_content_hash = state.active_bundle_content_hash "
                "JOIN official_screening_source_activation_event AS event "
                "ON event.sequence = state.sequence "
                "AND event.bundle_id = state.active_bundle_id "
                "AND event.bundle_content_hash = state.active_bundle_content_hash "
                "WHERE state.singleton = TRUE"
            ).fetchone()
            if state is None or len(state) != 16:
                _fail()
            (
                bundle_id,
                bundle_hash,
                fsf_observed_at,
                dual_observed_at,
                ofac_sdn_observed_at,
                ofac_consolidated_observed_at,
                updated_at,
                fsf_snapshot_id,
                fsf_snapshot_hash,
                dual_snapshot_id,
                dual_snapshot_hash,
                ofac_sdn_snapshot_id,
                ofac_sdn_snapshot_hash,
                ofac_consolidated_snapshot_id,
                ofac_consolidated_snapshot_hash,
                event_activated_at,
            ) = state
            for value in (
                fsf_observed_at,
                dual_observed_at,
                ofac_sdn_observed_at,
                ofac_consolidated_observed_at,
                updated_at,
                event_activated_at,
            ):
                if (
                    not isinstance(value, datetime)
                    or value.tzinfo is None
                    or value.utcoffset() is None
                ):
                    _fail()
            if event_activated_at > updated_at:
                _fail()
            fsf_snapshot = self._load_fsf(fsf_snapshot_id, fsf_snapshot_hash)
            if fsf_snapshot.generation_date > fsf_observed_at:
                _fail()
            dual_use = self._load_dual_use(
                dual_snapshot_id,
                dual_snapshot_hash,
                retrieved_at=dual_observed_at,
            )
            ofac_sdn = self._load_ofac(
                ofac_sdn_snapshot_id,
                ofac_sdn_snapshot_hash,
                retrieved_at=ofac_sdn_observed_at,
            )
            ofac_consolidated = self._load_ofac(
                ofac_consolidated_snapshot_id,
                ofac_consolidated_snapshot_hash,
                retrieved_at=ofac_consolidated_observed_at,
            )
            return ActiveOfficialSources(
                bundle_id=bundle_id,
                bundle_content_hash=bundle_hash,
                fsf_retrieved_at=fsf_observed_at,
                fsf_snapshot=fsf_snapshot,
                dual_use_control_list=dual_use,
                ofac_sdn_snapshot=ofac_sdn,
                ofac_consolidated_snapshot=ofac_consolidated,
                activated_at=updated_at,
            )
        except OfficialSourcePersistenceError:
            raise
        except (psycopg.Error, ValueError, TypeError, IndexError):
            _fail()

    def _persist_raw(
        self,
        content_hash: str,
        content: bytes,
        media_type: str,
        retrieved_at: datetime,
    ) -> None:
        self._connection.execute(
            "INSERT INTO official_source_raw_object "
            "(content_hash, byte_length, media_type, content, first_retrieved_at) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (content_hash, len(content), media_type, content, retrieved_at),
        )
        row = self._connection.execute(
            "SELECT byte_length, media_type FROM official_source_raw_object "
            "WHERE content_hash = %s",
            (content_hash,),
        ).fetchone()
        if row != (len(content), media_type):
            _fail()

    def _persist_fsf(self, bundle: OfficialSourceBundle, *, snapshot_hash: str) -> None:
        snapshot = bundle.fsf_snapshot
        snapshot_id = snapshot.snapshot_id
        entity_count = len(snapshot.entities)
        alias_count = sum(len(item.aliases) for item in snapshot.entities)
        identifier_count = sum(len(item.identifiers) for item in snapshot.entities)
        inserted = self._connection.execute(
            "INSERT INTO eu_fsf_official_snapshot "
            "(snapshot_id, snapshot_content_hash, raw_content_hash, "
            "raw_byte_length, generation_date, generation_date_text, "
            "global_file_id, first_retrieved_at, entity_count, alias_count, "
            "identifier_count) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT DO NOTHING RETURNING snapshot_id",
            (
                snapshot_id,
                snapshot_hash,
                snapshot.raw_content_hash,
                snapshot.raw_byte_length,
                snapshot.generation_date,
                snapshot.generation_date.isoformat(),
                snapshot.global_file_id,
                bundle.fsf_retrieved_at,
                entity_count,
                alias_count,
                identifier_count,
            ),
        ).fetchone()
        if inserted is None:
            existing = self._connection.execute(
                "SELECT snapshot_content_hash, raw_content_hash, raw_byte_length, "
                "generation_date_text, global_file_id, entity_count, alias_count, "
                "identifier_count FROM eu_fsf_official_snapshot "
                "WHERE snapshot_id = %s",
                (snapshot_id,),
            ).fetchone()
            if existing != (
                snapshot_hash,
                snapshot.raw_content_hash,
                snapshot.raw_byte_length,
                snapshot.generation_date.isoformat(),
                snapshot.global_file_id,
                entity_count,
                alias_count,
                identifier_count,
            ):
                _fail()
            return
        if inserted != (snapshot_id,):
            _fail()
        self._copy_many(
            "COPY eu_fsf_official_entity (snapshot_id, snapshot_content_hash, "
            "entity_logical_id, eu_reference_number, united_nations_id, "
            "designation_date, subject_type, regulation_programme, "
            "regulation_number_title, regulation_publication_date, "
            "regulation_entry_into_force_date, regulation_publication_url, "
            "regulation_native_locator, entity_content_hash) FROM STDIN",
            (
                (
                    snapshot_id,
                    snapshot_hash,
                    entity.logical_id,
                    entity.eu_reference_number,
                    entity.united_nations_id,
                    entity.designation_date,
                    entity.subject_type.value,
                    entity.regulation.programme,
                    entity.regulation.number_title,
                    entity.regulation.publication_date,
                    entity.regulation.entry_into_force_date,
                    entity.regulation.publication_url,
                    entity.regulation.native_locator,
                    entity.entity_hash,
                )
                for entity in snapshot.entities
            ),
        )
        self._copy_many(
            "COPY eu_fsf_official_alias (snapshot_id, snapshot_content_hash, "
            "entity_logical_id, alias_logical_id, whole_name, normalized_name, "
            "strong, language, native_locator, assertion_hash) FROM STDIN",
            (
                (
                    snapshot_id,
                    snapshot_hash,
                    entity.logical_id,
                    alias.logical_id,
                    alias.whole_name,
                    alias.normalized_name,
                    alias.strong,
                    alias.language,
                    alias.native_locator,
                    alias.assertion_hash,
                )
                for entity in snapshot.entities
                for alias in entity.aliases
            ),
        )
        self._copy_many(
            "COPY eu_fsf_official_identifier (snapshot_id, snapshot_content_hash, "
            "entity_logical_id, identifier_logical_id, type_code, number, "
            "normalized_number, country_code, known_expired, known_false, "
            "reported_lost, revoked_by_issuer, native_locator, assertion_hash) "
            "FROM STDIN",
            (
                (
                    snapshot_id,
                    snapshot_hash,
                    entity.logical_id,
                    identifier.logical_id,
                    identifier.type_code,
                    identifier.number,
                    identifier.normalized_number,
                    identifier.country_code,
                    identifier.known_expired,
                    identifier.known_false,
                    identifier.reported_lost,
                    identifier.revoked_by_issuer,
                    identifier.native_locator,
                    identifier.assertion_hash,
                )
                for entity in snapshot.entities
                for identifier in entity.identifiers
            ),
        )

    def _persist_dual_use(
        self, bundle: OfficialSourceBundle, *, snapshot_hash: str
    ) -> None:
        control_list = bundle.dual_use_control_list
        snapshot_id = control_list.snapshot_id
        inserted = self._connection.execute(
            "INSERT INTO eu_dual_use_official_snapshot "
            "(snapshot_id, snapshot_content_hash, raw_content_hash, "
            "raw_byte_length, formex_document_hash, celex, effective_from, "
            "first_retrieved_at, entry_count) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT DO NOTHING RETURNING snapshot_id",
            (
                snapshot_id,
                snapshot_hash,
                control_list.source_archive_hash,
                control_list.source_archive_bytes,
                control_list.formex_document_hash,
                EU_DUAL_USE_CELEX,
                EU_DUAL_USE_EFFECTIVE_FROM,
                control_list.retrieved_at,
                len(control_list.entries),
            ),
        ).fetchone()
        if inserted is None:
            existing = self._connection.execute(
                "SELECT snapshot_content_hash, raw_content_hash, raw_byte_length, "
                "formex_document_hash, celex, effective_from, entry_count "
                "FROM eu_dual_use_official_snapshot WHERE snapshot_id = %s",
                (snapshot_id,),
            ).fetchone()
            if existing != (
                snapshot_hash,
                control_list.source_archive_hash,
                control_list.source_archive_bytes,
                control_list.formex_document_hash,
                EU_DUAL_USE_CELEX,
                EU_DUAL_USE_EFFECTIVE_FROM,
                len(control_list.entries),
            ):
                _fail()
            return
        if inserted != (snapshot_id,):
            _fail()
        self._copy_many(
            "COPY eu_dual_use_official_entry (snapshot_id, "
            "snapshot_content_hash, control_code, entry_sequence, control_text, "
            "native_locator, entry_content_hash) FROM STDIN",
            (
                (
                    snapshot_id,
                    snapshot_hash,
                    entry.code,
                    sequence,
                    entry.text,
                    entry.native_locator,
                    entry.content_hash,
                )
                for sequence, entry in enumerate(control_list.entries, start=1)
            ),
        )

    def _persist_ofac(self, snapshot: OfacSnapshot, *, snapshot_hash: str) -> None:
        snapshot_id = snapshot.snapshot_id
        counts = (
            len(snapshot.entries),
            sum(len(entry.programs) for entry in snapshot.entries),
            sum(len(entry.aliases) for entry in snapshot.entries),
            sum(len(entry.addresses) for entry in snapshot.entries),
            sum(len(entry.identifiers) for entry in snapshot.entries),
            sum(len(entry.facts) for entry in snapshot.entries),
            sum(entry.vessel_info is not None for entry in snapshot.entries),
        )
        inserted = self._connection.execute(
            "INSERT INTO ofac_sls_official_snapshot "
            "(snapshot_id, snapshot_content_hash, list_kind, publish_date, "
            "declared_record_count, retrieved_at, source_last_modified, "
            "raw_content_hash, raw_byte_length, entry_count, program_count, "
            "alias_count, address_count, identifier_count, fact_count, "
            "vessel_count) VALUES ("
            + ", ".join(["%s"] * 16)
            + ") ON CONFLICT DO NOTHING RETURNING snapshot_id",
            (
                snapshot_id,
                snapshot_hash,
                snapshot.list_kind.value,
                snapshot.publish_date,
                snapshot.declared_record_count,
                snapshot.retrieved_at,
                snapshot.source_last_modified,
                snapshot.raw_content_hash,
                snapshot.raw_byte_length,
                *counts,
            ),
        ).fetchone()
        if inserted is None:
            existing = self._connection.execute(
                "SELECT snapshot_content_hash, list_kind, publish_date, "
                "declared_record_count, source_last_modified, raw_content_hash, "
                "raw_byte_length, entry_count, program_count, alias_count, "
                "address_count, identifier_count, fact_count, vessel_count "
                "FROM ofac_sls_official_snapshot WHERE snapshot_id = %s",
                (snapshot_id,),
            ).fetchone()
            if existing != (
                snapshot_hash,
                snapshot.list_kind.value,
                snapshot.publish_date,
                snapshot.declared_record_count,
                snapshot.source_last_modified,
                snapshot.raw_content_hash,
                snapshot.raw_byte_length,
                *counts,
            ):
                _fail()
            return
        if inserted != (snapshot_id,):
            _fail()
        self._copy_many(
            "COPY ofac_sls_official_entry (snapshot_id, "
            "snapshot_content_hash, list_kind, entry_uid, first_name, last_name, "
            "whole_name, normalized_name, title, subject_type, remarks, "
            "entry_content_hash) FROM STDIN",
            (
                (
                    snapshot_id,
                    snapshot_hash,
                    snapshot.list_kind.value,
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
                for entry in snapshot.entries
            ),
        )
        self._copy_many(
            "COPY ofac_sls_official_program (snapshot_id, "
            "snapshot_content_hash, list_kind, entry_uid, program_sequence, "
            "program) FROM STDIN",
            (
                (
                    snapshot_id,
                    snapshot_hash,
                    snapshot.list_kind.value,
                    entry.uid,
                    sequence,
                    program,
                )
                for entry in snapshot.entries
                for sequence, program in enumerate(entry.programs, start=1)
            ),
        )
        self._copy_many(
            "COPY ofac_sls_official_alias (snapshot_id, snapshot_content_hash, "
            "list_kind, entry_uid, alias_uid, alias_type, category, whole_name, "
            "normalized_name, native_locator) FROM STDIN",
            (
                (
                    snapshot_id,
                    snapshot_hash,
                    snapshot.list_kind.value,
                    entry.uid,
                    alias.uid,
                    alias.alias_type,
                    alias.category,
                    alias.whole_name,
                    alias.normalized_name,
                    alias.native_locator,
                )
                for entry in snapshot.entries
                for alias in entry.aliases
            ),
        )
        self._copy_many(
            "COPY ofac_sls_official_address (snapshot_id, "
            "snapshot_content_hash, list_kind, entry_uid, address_uid, "
            "address1, address2, address3, city, state_or_province, postal_code, "
            "country, region, native_locator) FROM STDIN",
            (
                (
                    snapshot_id,
                    snapshot_hash,
                    snapshot.list_kind.value,
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
                for entry in snapshot.entries
                for address in entry.addresses
            ),
        )
        self._copy_many(
            "COPY ofac_sls_official_identifier (snapshot_id, "
            "snapshot_content_hash, list_kind, entry_uid, identifier_uid, "
            "type_code, number, normalized_number, country, issue_date, "
            "expiration_date, native_locator) FROM STDIN",
            (
                (
                    snapshot_id,
                    snapshot_hash,
                    snapshot.list_kind.value,
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
                for entry in snapshot.entries
                for identifier in entry.identifiers
            ),
        )
        self._copy_many(
            "COPY ofac_sls_official_fact (snapshot_id, snapshot_content_hash, "
            "list_kind, entry_uid, fact_uid, fact_kind, fact_value, main_entry, "
            "native_locator) FROM STDIN",
            (
                (
                    snapshot_id,
                    snapshot_hash,
                    snapshot.list_kind.value,
                    entry.uid,
                    fact.uid,
                    fact.kind.value,
                    fact.value,
                    fact.main_entry,
                    fact.native_locator,
                )
                for entry in snapshot.entries
                for fact in entry.facts
            ),
        )
        self._copy_many(
            "COPY ofac_sls_official_vessel (snapshot_id, "
            "snapshot_content_hash, list_kind, entry_uid, call_sign, "
            "vessel_type, vessel_flag, vessel_owner, tonnage, "
            "gross_registered_tonnage, native_locator) FROM STDIN",
            (
                (
                    snapshot_id,
                    snapshot_hash,
                    snapshot.list_kind.value,
                    entry.uid,
                    entry.vessel_info.call_sign,
                    entry.vessel_info.vessel_type,
                    entry.vessel_info.vessel_flag,
                    entry.vessel_info.vessel_owner,
                    entry.vessel_info.tonnage,
                    entry.vessel_info.gross_registered_tonnage,
                    entry.vessel_info.native_locator,
                )
                for entry in snapshot.entries
                if entry.vessel_info is not None
            ),
        )

    def _persist_bundle(
        self,
        bundle: OfficialSourceBundle,
        *,
        bundle_id: str,
        bundle_hash: str,
        fsf_snapshot_hash: str,
        dual_use_snapshot_hash: str,
        ofac_sdn_snapshot_hash: str,
        ofac_consolidated_snapshot_hash: str,
    ) -> None:
        self._connection.execute(
            "INSERT INTO official_screening_source_bundle "
            "(bundle_id, bundle_content_hash, fsf_snapshot_id, "
            "fsf_snapshot_content_hash, dual_use_snapshot_id, "
            "dual_use_snapshot_content_hash, ofac_sdn_snapshot_id, "
            "ofac_sdn_snapshot_content_hash, ofac_consolidated_snapshot_id, "
            "ofac_consolidated_snapshot_content_hash, first_activated_at) "
            "VALUES (" + ", ".join(["%s"] * 11) + ") ON CONFLICT DO NOTHING",
            (
                bundle_id,
                bundle_hash,
                bundle.fsf_snapshot.snapshot_id,
                fsf_snapshot_hash,
                bundle.dual_use_control_list.snapshot_id,
                dual_use_snapshot_hash,
                bundle.ofac_sdn_snapshot.snapshot_id,
                ofac_sdn_snapshot_hash,
                bundle.ofac_consolidated_snapshot.snapshot_id,
                ofac_consolidated_snapshot_hash,
                bundle.activated_at,
            ),
        )
        existing = self._connection.execute(
            "SELECT bundle_content_hash, fsf_snapshot_id, "
            "fsf_snapshot_content_hash, dual_use_snapshot_id, "
            "dual_use_snapshot_content_hash, ofac_sdn_snapshot_id, "
            "ofac_sdn_snapshot_content_hash, ofac_consolidated_snapshot_id, "
            "ofac_consolidated_snapshot_content_hash "
            "FROM official_screening_source_bundle WHERE bundle_id = %s",
            (bundle_id,),
        ).fetchone()
        if existing != (
            bundle_hash,
            bundle.fsf_snapshot.snapshot_id,
            fsf_snapshot_hash,
            bundle.dual_use_control_list.snapshot_id,
            dual_use_snapshot_hash,
            bundle.ofac_sdn_snapshot.snapshot_id,
            ofac_sdn_snapshot_hash,
            bundle.ofac_consolidated_snapshot.snapshot_id,
            ofac_consolidated_snapshot_hash,
        ):
            _fail()

    def _copy_many(self, query: str, rows: Iterable[tuple[object, ...]]) -> None:
        with self._connection.cursor() as cursor, cursor.copy(query) as copy:
            for row in rows:
                copy.write_row(row)

    def _load_fsf(self, snapshot_id: object, snapshot_hash: object) -> EuFsfSnapshot:
        row = self._connection.execute(
            "SELECT generation_date_text, global_file_id, raw_content_hash, "
            "raw_byte_length, entity_count, alias_count, identifier_count "
            "FROM eu_fsf_official_snapshot "
            "WHERE snapshot_id = %s AND snapshot_content_hash = %s",
            (snapshot_id, snapshot_hash),
        ).fetchone()
        if row is None or len(row) != 7:
            _fail()
        entity_rows = self._connection.execute(
            "SELECT entity_logical_id, eu_reference_number, united_nations_id, "
            "designation_date, subject_type, regulation_programme, "
            "regulation_number_title, regulation_publication_date, "
            "regulation_entry_into_force_date, regulation_publication_url, "
            "regulation_native_locator, entity_content_hash "
            "FROM eu_fsf_official_entity WHERE snapshot_id = %s "
            "ORDER BY CAST(entity_logical_id AS NUMERIC)",
            (snapshot_id,),
        ).fetchall()
        alias_rows = self._connection.execute(
            "SELECT entity_logical_id, alias_logical_id, whole_name, "
            "normalized_name, strong, language, native_locator, assertion_hash "
            "FROM eu_fsf_official_alias WHERE snapshot_id = %s "
            "ORDER BY CAST(entity_logical_id AS NUMERIC), "
            "CAST(alias_logical_id AS NUMERIC)",
            (snapshot_id,),
        ).fetchall()
        identifier_rows = self._connection.execute(
            "SELECT entity_logical_id, identifier_logical_id, type_code, number, "
            "normalized_number, country_code, known_expired, known_false, "
            "reported_lost, revoked_by_issuer, native_locator, assertion_hash "
            "FROM eu_fsf_official_identifier WHERE snapshot_id = %s "
            "ORDER BY CAST(entity_logical_id AS NUMERIC), "
            "CAST(identifier_logical_id AS NUMERIC)",
            (snapshot_id,),
        ).fetchall()
        if (
            len(entity_rows) != row[4]
            or len(alias_rows) != row[5]
            or len(identifier_rows) != row[6]
        ):
            _fail()
        aliases: dict[str, list[EuFsfAlias]] = {}
        for item in alias_rows:
            if len(item) != 8:
                _fail()
            alias = EuFsfAlias(
                logical_id=item[1],
                whole_name=item[2],
                normalized_name=item[3],
                strong=item[4],
                language=item[5],
                native_locator=item[6],
            )
            if alias.assertion_hash != item[7]:
                _fail()
            aliases.setdefault(item[0], []).append(alias)
        identifiers: dict[str, list[EuFsfIdentifier]] = {}
        for item in identifier_rows:
            if len(item) != 12:
                _fail()
            identifier = EuFsfIdentifier(
                logical_id=item[1],
                type_code=item[2],
                number=item[3],
                normalized_number=item[4],
                country_code=item[5],
                known_expired=item[6],
                known_false=item[7],
                reported_lost=item[8],
                revoked_by_issuer=item[9],
                native_locator=item[10],
            )
            if identifier.assertion_hash != item[11]:
                _fail()
            identifiers.setdefault(item[0], []).append(identifier)
        entities: list[EuFsfEntity] = []
        for item in entity_rows:
            if len(item) != 12:
                _fail()
            entity = EuFsfEntity(
                logical_id=item[0],
                eu_reference_number=item[1],
                united_nations_id=item[2],
                designation_date=item[3],
                subject_type=EuFsfSubjectType(item[4]),
                regulation=EuFsfRegulation(
                    programme=item[5],
                    number_title=item[6],
                    publication_date=item[7],
                    entry_into_force_date=item[8],
                    publication_url=item[9],
                    native_locator=item[10],
                ),
                aliases=tuple(aliases.pop(item[0], [])),
                identifiers=tuple(identifiers.pop(item[0], [])),
            )
            if entity.entity_hash != item[11]:
                _fail()
            entities.append(entity)
        if aliases or identifiers:
            _fail()
        snapshot = EuFsfSnapshot(
            generation_date=datetime.fromisoformat(row[0]),
            global_file_id=row[1],
            raw_content_hash=row[2],
            raw_byte_length=row[3],
            entities=tuple(entities),
        )
        if (
            snapshot.snapshot_id != snapshot_id
            or snapshot.content_hash != snapshot_hash
        ):
            _fail()
        return snapshot

    def _load_dual_use(
        self,
        snapshot_id: object,
        snapshot_hash: object,
        *,
        retrieved_at: datetime,
    ) -> EuDualUseControlList:
        row = self._connection.execute(
            "SELECT raw_content_hash, raw_byte_length, formex_document_hash, "
            "celex, effective_from, entry_count "
            "FROM eu_dual_use_official_snapshot "
            "WHERE snapshot_id = %s AND snapshot_content_hash = %s",
            (snapshot_id, snapshot_hash),
        ).fetchone()
        if (
            row is None
            or len(row) != 6
            or row[3] != EU_DUAL_USE_CELEX
            or row[4] != EU_DUAL_USE_EFFECTIVE_FROM
        ):
            _fail()
        rows = self._connection.execute(
            "SELECT control_code, control_text, native_locator, entry_content_hash "
            "FROM eu_dual_use_official_entry WHERE snapshot_id = %s "
            "ORDER BY entry_sequence",
            (snapshot_id,),
        ).fetchall()
        if len(rows) != row[5]:
            _fail()
        entries: list[EuDualUseControlEntry] = []
        for item in rows:
            if len(item) != 4:
                _fail()
            entry = EuDualUseControlEntry(item[0], item[1], item[2])
            if entry.content_hash != item[3]:
                _fail()
            entries.append(entry)
        control_list = EuDualUseControlList(
            retrieved_at=retrieved_at,
            source_archive_hash=row[0],
            source_archive_bytes=row[1],
            formex_document_hash=row[2],
            entries=tuple(entries),
        )
        if (
            control_list.snapshot_id != snapshot_id
            or control_list.content_hash != snapshot_hash
        ):
            _fail()
        return control_list

    def _load_ofac(
        self,
        snapshot_id: object,
        snapshot_hash: object,
        *,
        retrieved_at: datetime,
    ) -> OfacSnapshot:
        row = self._connection.execute(
            "SELECT list_kind, publish_date, declared_record_count, "
            "source_last_modified, raw_content_hash, raw_byte_length, "
            "entry_count, program_count, alias_count, address_count, "
            "identifier_count, fact_count, vessel_count "
            "FROM ofac_sls_official_snapshot "
            "WHERE snapshot_id = %s AND snapshot_content_hash = %s",
            (snapshot_id, snapshot_hash),
        ).fetchone()
        if row is None or len(row) != 13:
            _fail()
        entry_rows = self._connection.execute(
            "SELECT entry_uid, first_name, last_name, whole_name, "
            "normalized_name, title, subject_type, remarks, entry_content_hash "
            "FROM ofac_sls_official_entry WHERE snapshot_id = %s "
            "ORDER BY CAST(entry_uid AS NUMERIC)",
            (snapshot_id,),
        ).fetchall()
        program_rows = self._connection.execute(
            "SELECT entry_uid, program_sequence, program "
            "FROM ofac_sls_official_program WHERE snapshot_id = %s "
            "ORDER BY CAST(entry_uid AS NUMERIC), program_sequence",
            (snapshot_id,),
        ).fetchall()
        alias_rows = self._connection.execute(
            "SELECT entry_uid, alias_uid, alias_type, category, whole_name, "
            "normalized_name, native_locator FROM ofac_sls_official_alias "
            "WHERE snapshot_id = %s ORDER BY CAST(entry_uid AS NUMERIC), "
            "CAST(alias_uid AS NUMERIC)",
            (snapshot_id,),
        ).fetchall()
        address_rows = self._connection.execute(
            "SELECT entry_uid, address_uid, address1, address2, address3, city, "
            "state_or_province, postal_code, country, region, native_locator "
            "FROM ofac_sls_official_address WHERE snapshot_id = %s "
            "ORDER BY CAST(entry_uid AS NUMERIC), CAST(address_uid AS NUMERIC)",
            (snapshot_id,),
        ).fetchall()
        identifier_rows = self._connection.execute(
            "SELECT entry_uid, identifier_uid, type_code, number, "
            "normalized_number, country, issue_date, expiration_date, "
            "native_locator FROM ofac_sls_official_identifier "
            "WHERE snapshot_id = %s ORDER BY CAST(entry_uid AS NUMERIC), "
            "CAST(identifier_uid AS NUMERIC)",
            (snapshot_id,),
        ).fetchall()
        fact_rows = self._connection.execute(
            "SELECT entry_uid, fact_uid, fact_kind, fact_value, main_entry, "
            "native_locator FROM ofac_sls_official_fact WHERE snapshot_id = %s "
            "ORDER BY CAST(entry_uid AS NUMERIC), CAST(fact_uid AS NUMERIC)",
            (snapshot_id,),
        ).fetchall()
        vessel_rows = self._connection.execute(
            "SELECT entry_uid, call_sign, vessel_type, vessel_flag, vessel_owner, "
            "tonnage, gross_registered_tonnage, native_locator "
            "FROM ofac_sls_official_vessel WHERE snapshot_id = %s "
            "ORDER BY CAST(entry_uid AS NUMERIC)",
            (snapshot_id,),
        ).fetchall()
        expected_counts = row[6:13]
        actual_counts = (
            len(entry_rows),
            len(program_rows),
            len(alias_rows),
            len(address_rows),
            len(identifier_rows),
            len(fact_rows),
            len(vessel_rows),
        )
        if expected_counts != actual_counts or row[2] != len(entry_rows):
            _fail()
        programs: dict[str, list[tuple[int, str]]] = {}
        for item in program_rows:
            if len(item) != 3:
                _fail()
            programs.setdefault(item[0], []).append((item[1], item[2]))
        aliases: dict[str, list[OfacAlias]] = {}
        for item in alias_rows:
            if len(item) != 7:
                _fail()
            aliases.setdefault(item[0], []).append(
                OfacAlias(
                    uid=item[1],
                    alias_type=item[2],
                    category=item[3],
                    whole_name=item[4],
                    normalized_name=item[5],
                    native_locator=item[6],
                )
            )
        addresses: dict[str, list[OfacAddress]] = {}
        for item in address_rows:
            if len(item) != 11:
                _fail()
            addresses.setdefault(item[0], []).append(
                OfacAddress(
                    uid=item[1],
                    address_lines=tuple(
                        value for value in item[2:5] if value is not None
                    ),
                    city=item[5],
                    state_or_province=item[6],
                    postal_code=item[7],
                    country=item[8],
                    region=item[9],
                    native_locator=item[10],
                )
            )
        identifiers: dict[str, list[OfacIdentifier]] = {}
        for item in identifier_rows:
            if len(item) != 9:
                _fail()
            identifiers.setdefault(item[0], []).append(
                OfacIdentifier(
                    uid=item[1],
                    type_code=item[2],
                    number=item[3],
                    normalized_number=item[4],
                    country=item[5],
                    issue_date=item[6],
                    expiration_date=item[7],
                    native_locator=item[8],
                )
            )
        facts: dict[str, list[OfacFact]] = {}
        for item in fact_rows:
            if len(item) != 6:
                _fail()
            facts.setdefault(item[0], []).append(
                OfacFact(
                    kind=OfacFactKind(item[2]),
                    uid=item[1],
                    value=item[3],
                    main_entry=item[4],
                    native_locator=item[5],
                )
            )
        vessels: dict[str, OfacVesselInfo] = {}
        for item in vessel_rows:
            if len(item) != 8 or item[0] in vessels:
                _fail()
            vessels[item[0]] = OfacVesselInfo(
                call_sign=item[1],
                vessel_type=item[2],
                vessel_flag=item[3],
                vessel_owner=item[4],
                tonnage=item[5],
                gross_registered_tonnage=item[6],
                native_locator=item[7],
            )
        list_kind = OfacSlsListKind(row[0])
        entries: list[OfacEntry] = []
        for item in entry_rows:
            if len(item) != 9:
                _fail()
            program_values = programs.pop(item[0], [])
            if [sequence for sequence, _value in program_values] != list(
                range(1, len(program_values) + 1)
            ):
                _fail()
            entry = OfacEntry(
                uid=item[0],
                list_kind=list_kind,
                first_name=item[1],
                last_name=item[2],
                whole_name=item[3],
                normalized_name=item[4],
                title=item[5],
                subject_type=OfacSubjectType(item[6]),
                remarks=item[7],
                programs=tuple(value for _sequence, value in program_values),
                aliases=tuple(aliases.pop(item[0], [])),
                addresses=tuple(addresses.pop(item[0], [])),
                identifiers=tuple(identifiers.pop(item[0], [])),
                facts=tuple(facts.pop(item[0], [])),
                vessel_info=vessels.pop(item[0], None),
            )
            if entry.entry_hash != item[8]:
                _fail()
            entries.append(entry)
        if programs or aliases or addresses or identifiers or facts or vessels:
            _fail()
        snapshot = OfacSnapshot(
            list_kind=list_kind,
            publish_date=row[1],
            declared_record_count=row[2],
            retrieved_at=retrieved_at,
            source_last_modified=row[3],
            raw_content_hash=row[4],
            raw_byte_length=row[5],
            entries=tuple(entries),
        )
        if (
            snapshot.snapshot_id != snapshot_id
            or snapshot.content_hash != snapshot_hash
        ):
            _fail()
        return snapshot


__all__ = [
    "OfficialSourcePersistenceError",
    "PostgresOfficialSourceRepository",
]
