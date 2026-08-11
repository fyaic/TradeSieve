"""Static executable assertions for official-source migration 0006."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

import pytest

EXPECTED_TABLES = {
    "official_source_raw_object",
    "eu_fsf_official_snapshot",
    "eu_fsf_official_entity",
    "eu_fsf_official_alias",
    "eu_fsf_official_identifier",
    "eu_dual_use_official_snapshot",
    "eu_dual_use_official_entry",
    "official_screening_source_bundle",
    "official_screening_source_activation_event",
    "official_screening_source_state",
}
EXPECTED_INDEXES = {
    "ix_eu_fsf_official_alias_lookup",
    "ix_eu_fsf_official_identifier_lookup",
    "ix_official_source_activation_time",
}
IMMUTABLE_TABLES = EXPECTED_TABLES - {"official_screening_source_state"}


def migration_module() -> Any:
    path = (
        Path(__file__).parents[2]
        / "migrations/versions/20260810_0006_official_source_projections.py"
    )
    spec = importlib.util.spec_from_file_location("official_source_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sql_for(monkeypatch: pytest.MonkeyPatch, operation: str) -> tuple[Any, str]:
    migration = migration_module()
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "execute", statements.append)
    getattr(migration, operation)()
    assert len(statements) == 1
    return migration, statements[0]


def test_migration_owns_exact_projection_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration, sql = sql_for(monkeypatch, "upgrade")
    assert migration.revision == "20260810_0006"
    assert migration.down_revision == "20260806_0005"
    assert set(re.findall(r"CREATE TABLE ([a-z0-9_]+)", sql)) == EXPECTED_TABLES
    assert set(re.findall(r"CREATE INDEX ([a-z0-9_]+)", sql)) == EXPECTED_INDEXES
    assert sql.count("EXECUTE FUNCTION tradesieve_reject_immutable_change()") == len(
        IMMUTABLE_TABLES
    )
    assert "CREATE EXTENSION" not in sql
    assert "ON DELETE CASCADE" not in sql
    assert "CREATE FUNCTION tradesieve_validate_official_projection_counts" in sql
    assert sql.count("DEFERRABLE INITIALLY DEFERRED") == 2
    assert "CREATE FUNCTION tradesieve_reject_activated_fsf_projection_insert" in sql
    assert "CREATE FUNCTION tradesieve_reject_activated_dual_projection_insert" in sql
    assert (
        sql.count(
            "EXECUTE FUNCTION tradesieve_reject_activated_fsf_projection_insert()"
        )
        == 3
    )
    assert (
        sql.count(
            "EXECUTE FUNCTION tradesieve_reject_activated_dual_projection_insert()"
        )
        == 1
    )
    assert sql.count("REFERENCING NEW TABLE AS inserted_rows") == 4
    assert sql.count("FOR EACH STATEMENT") == 4


def test_schema_binds_raw_projection_children_bundle_and_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, sql = sql_for(monkeypatch, "upgrade")
    assert "octet_length(content) = byte_length" in sql
    assert "content_hash = 'sha256:' || encode(sha256(content), 'hex')" in sql
    assert "generation_date <= first_retrieved_at" in sql
    assert "generation_date_text::TIMESTAMPTZ = generation_date" in sql
    assert "entity_count BETWEEN 1 AND 20000" in sql
    assert "entry_count BETWEEN 300 AND 1000" in sql
    assert "entry_sequence BETWEEN 1 AND 1000" in sql
    assert "celex = '32025R2003'" in sql
    assert "effective_from = DATE '2025-11-15'" in sql
    assert "REFERENCES official_source_raw_object (content_hash)" in sql
    assert sql.count("REFERENCES eu_fsf_official_entity") == 2
    assert "REFERENCES eu_fsf_official_snapshot" in sql
    assert "REFERENCES eu_dual_use_official_snapshot" in sql
    assert "REFERENCES official_screening_source_bundle" in sql
    assert "REFERENCES official_screening_source_activation_event" in sql
    assert "active_bundle_id VARCHAR(80) NOT NULL" in sql
    assert "normalized_name TEXT NOT NULL" in sql
    assert "normalized_number VARCHAR(256) NOT NULL" in sql
    assert "UNIQUE\n                (snapshot_id, entry_sequence)" in sql


def test_count_guards_and_lookup_indexes_are_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, sql = sql_for(monkeypatch, "upgrade")
    assert "actual_entities <> expected_entities" in sql
    assert "actual_aliases <> expected_aliases" in sql
    assert "actual_identifiers <> expected_identifiers" in sql
    assert "actual_entries <> expected_entries" in sql
    assert "USING ERRCODE = '23514'" in sql
    assert "activated official projection is immutable" in sql
    assert "(snapshot_id, normalized_name)" in sql
    assert "(snapshot_id, type_code, normalized_number)" in sql


def test_downgrade_removes_only_0006_objects_in_dependency_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, sql = sql_for(monkeypatch, "downgrade")
    assert set(re.findall(r"DROP TABLE ([a-z0-9_]+)", sql)) == EXPECTED_TABLES
    assert "DROP FUNCTION tradesieve_validate_official_projection_counts()" in sql
    assert "DROP FUNCTION tradesieve_reject_activated_fsf_projection_insert()" in sql
    assert "DROP FUNCTION tradesieve_reject_activated_dual_projection_insert()" in sql
    assert "screening_accepted_intake" not in sql
    assert sql.index("DROP TABLE official_screening_source_state") < sql.index(
        "DROP TABLE official_screening_source_bundle"
    )
    assert sql.index("DROP TABLE eu_fsf_official_identifier") < sql.index(
        "DROP TABLE eu_fsf_official_snapshot"
    )
