"""Static executable assertions for OFAC official-source migration 0007."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

import pytest

EXPECTED_TABLES = {
    "ofac_sls_official_snapshot",
    "ofac_sls_official_entry",
    "ofac_sls_official_program",
    "ofac_sls_official_alias",
    "ofac_sls_official_address",
    "ofac_sls_official_identifier",
    "ofac_sls_official_fact",
    "ofac_sls_official_vessel",
}
EXPECTED_INDEXES = {
    "ix_ofac_sls_official_alias_lookup",
    "ix_ofac_sls_official_identifier_lookup",
}
EXPECTED_FUNCTIONS = {
    "tradesieve_validate_ofac_projection_counts",
    "tradesieve_reject_activated_ofac_projection_insert",
    "tradesieve_require_complete_ofac_active_state",
}


def migration_module() -> Any:
    path = (
        Path(__file__).parents[2]
        / "migrations/versions/20260810_0007_ofac_official_projections.py"
    )
    spec = importlib.util.spec_from_file_location("ofac_official_migration", path)
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


def test_migration_owns_only_the_ofac_projection_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration, sql = sql_for(monkeypatch, "upgrade")

    assert migration.revision == "20260810_0007"
    assert migration.down_revision == "20260810_0006"
    assert set(re.findall(r"CREATE TABLE ([a-z0-9_]+)", sql)) == EXPECTED_TABLES
    assert set(re.findall(r"CREATE INDEX ([a-z0-9_]+)", sql)) == EXPECTED_INDEXES
    assert set(re.findall(r"CREATE FUNCTION ([a-z0-9_]+)\(", sql)) == (
        EXPECTED_FUNCTIONS
    )
    assert sql.count("EXECUTE FUNCTION tradesieve_reject_immutable_change()") == 8
    assert sql.count("REFERENCING NEW TABLE AS inserted_rows") == 7
    assert (
        sql.count(
            "EXECUTE FUNCTION tradesieve_reject_activated_ofac_projection_insert()"
        )
        == 7
    )
    assert sql.count("DEFERRABLE INITIALLY DEFERRED") == 1
    assert "CREATE EXTENSION" not in sql
    assert "ON DELETE CASCADE" not in sql


def test_schema_binds_complete_typed_four_source_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, sql = sql_for(monkeypatch, "upgrade")

    assert "list_kind IN ('SDN', 'CONSOLIDATED')" in sql
    assert "entry_count = declared_record_count" in sql
    assert "publish_date <= retrieved_at::DATE" in sql
    assert "REFERENCES official_source_raw_object (content_hash)" in sql
    assert sql.count("REFERENCES ofac_sls_official_entry") == 6
    assert "ck_official_screening_source_bundle_ofac_complete" in sql
    assert "ofac_sdn_snapshot_id VARCHAR(96)" in sql
    assert "ofac_consolidated_snapshot_id VARCHAR(96)" in sql
    assert "sdn.list_kind = 'SDN'" in sql
    assert "consolidated.list_kind = 'CONSOLIDATED'" in sql
    assert "active official source set requires OFAC" in sql
    assert "ofac_sdn_observed_at TIMESTAMPTZ" in sql
    assert "ofac_consolidated_observed_at TIMESTAMPTZ" in sql


def test_count_and_immutability_guards_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, sql = sql_for(monkeypatch, "upgrade")

    for field in (
        "actual_entries",
        "actual_programs",
        "actual_aliases",
        "actual_addresses",
        "actual_identifiers",
        "actual_facts",
        "actual_vessels",
    ):
        assert f"{field} <> NEW." in sql
    assert "OFAC projection count mismatch" in sql
    assert "activated OFAC projection is immutable" in sql
    assert sql.count("USING ERRCODE = '23514'") == 3


def test_downgrade_removes_only_0007_objects_and_restores_old_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, sql = sql_for(monkeypatch, "downgrade")

    assert set(re.findall(r"DROP TABLE ([a-z0-9_]+)", sql)) == EXPECTED_TABLES
    for function in EXPECTED_FUNCTIONS:
        assert f"DROP FUNCTION {function}();" in sql
    assert "DROP COLUMN ofac_consolidated_observed_at" in sql
    assert "DROP COLUMN ofac_sdn_observed_at" in sql
    assert "ADD CONSTRAINT ck_official_screening_source_state_time" in sql
    assert "DROP TABLE eu_fsf_official_snapshot" not in sql
    assert "DROP TABLE official_screening_source_bundle" not in sql
