"""Static executable assertions for canonical screening submission migration 0005."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

import pytest

EXPECTED_TABLES = {
    "screening_opaque_id_registry",
    "screening_accepted_intake",
    "screening_identity",
    "screening_idempotency_ledger",
    "screening_attempt_audit",
    "screening_accepted_outbox",
}

EXPECTED_FUNCTIONS = {
    "tradesieve_screening_scope_fingerprint",
    "tradesieve_validate_screening_attempt",
    "tradesieve_require_screening_complete_graph",
    "tradesieve_require_screening_registry_child",
}

EXPECTED_TRIGGERS = {
    "trg_screening_registry_immutable",
    "trg_screening_intake_immutable",
    "trg_screening_identity_immutable",
    "trg_screening_ledger_immutable",
    "trg_screening_attempt_immutable",
    "trg_screening_outbox_immutable",
    "trg_screening_attempt_validate",
    "trg_screening_ledger_complete",
    "trg_screening_registry_child",
}

EXPECTED_INDEXES = {
    "ix_screening_intake_tenant_received",
    "ix_screening_identity_tenant_created",
    "ix_screening_attempt_scope_time",
    "ix_screening_outbox_occurred",
}

EXPECTED_CONSTRAINTS = {
    "pk_screening_opaque_id_registry",
    "uq_screening_opaque_id_kind",
    "ck_screening_opaque_id",
    "ck_screening_opaque_kind",
    "pk_screening_accepted_intake",
    "uq_screening_intake_result_ref",
    "ck_screening_intake_kind",
    "ck_screening_intake_ids",
    "ck_screening_intake_actor",
    "ck_screening_intake_document",
    "ck_screening_intake_hashes",
    "ck_screening_intake_time",
    "fk_screening_intake_registry",
    "pk_screening_identity",
    "uq_screening_identity_intake",
    "uq_screening_identity_outbox_ref",
    "uq_screening_identity_result_ref",
    "ck_screening_identity_kind",
    "ck_screening_identity_ids",
    "ck_screening_identity_hashes",
    "ck_screening_identity_time",
    "fk_screening_identity_registry",
    "fk_screening_identity_intake",
    "pk_screening_accepted_outbox",
    "uq_screening_outbox_intake",
    "uq_screening_outbox_screening",
    "uq_screening_outbox_result_ref",
    "ck_screening_outbox_kind",
    "ck_screening_outbox_ids",
    "ck_screening_outbox_shape",
    "ck_screening_outbox_time",
    "fk_screening_outbox_registry",
    "fk_screening_outbox_intake",
    "fk_screening_outbox_screening",
    "pk_screening_idempotency_ledger",
    "uq_screening_ledger_fingerprint",
    "uq_screening_ledger_intake",
    "uq_screening_ledger_screening",
    "uq_screening_ledger_outbox",
    "uq_screening_ledger_scope_ref",
    "ck_screening_ledger_ids",
    "ck_screening_ledger_operation",
    "ck_screening_ledger_hashes",
    "ck_screening_ledger_fingerprint",
    "ck_screening_ledger_time",
    "fk_screening_ledger_intake",
    "fk_screening_ledger_screening",
    "fk_screening_ledger_outbox",
    "pk_screening_attempt",
    "uq_screening_attempt_id",
    "uq_screening_attempt_authorization",
    "ck_screening_attempt_kind",
    "ck_screening_attempt_ids",
    "ck_screening_attempt_actor",
    "ck_screening_attempt_hashes",
    "ck_screening_attempt_sequence",
    "ck_screening_attempt_outcome_sequence",
    "ck_screening_attempt_time",
    "fk_screening_attempt_registry",
    "fk_screening_attempt_ledger",
    "fk_screening_attempt_authorization",
}


def migration_module() -> Any:
    path = (
        Path(__file__).parents[2]
        / "migrations/versions/20260806_0005_screening_submissions.py"
    )
    spec = importlib.util.spec_from_file_location(
        "screening_submission_migration", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def upgrade_sql(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, str]:
    migration = migration_module()
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "execute", statements.append)
    migration.upgrade()
    assert len(statements) == 1
    return migration, statements[0]


def downgrade_sql(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, str]:
    migration = migration_module()
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "execute", statements.append)
    migration.downgrade()
    assert len(statements) == 1
    return migration, statements[0]


def section(sql: str, start: str, end: str) -> str:
    assert start in sql
    tail = sql.split(start, maxsplit=1)[1]
    assert end in tail
    return tail.split(end, maxsplit=1)[0]


def normalized(value: str) -> str:
    return " ".join(value.split())


def test_migration_owns_exact_0005_schema_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration, sql = upgrade_sql(monkeypatch)

    assert migration.revision == "20260806_0005"
    assert migration.down_revision == "20260806_0004"
    assert set(re.findall(r"CREATE TABLE ([a-z0-9_]+)", sql)) == EXPECTED_TABLES
    assert set(re.findall(r"CREATE FUNCTION ([a-z0-9_]+)\(", sql)) == (
        EXPECTED_FUNCTIONS
    )
    assert set(re.findall(r"CONSTRAINT (?!TRIGGER)([a-z0-9_]+)", sql)) == (
        EXPECTED_CONSTRAINTS
    )
    assert (
        set(re.findall(r"CREATE (?:CONSTRAINT )?TRIGGER ([a-z0-9_]+)", sql))
        == EXPECTED_TRIGGERS
    )
    assert set(re.findall(r"CREATE INDEX ([a-z0-9_]+)", sql)) == EXPECTED_INDEXES
    assert "CREATE TABLE authorization_audit_event" not in sql
    assert "CREATE FUNCTION tradesieve_reject_immutable_change" not in sql
    assert sql.count("EXECUTE FUNCTION tradesieve_reject_immutable_change()") == 6
    assert "ON DELETE CASCADE" not in sql
    assert "CREATE EXTENSION" not in sql
    assert "idempotency_key" not in sql
    assert sql.count("key_digest VARCHAR(71) NOT NULL") == 2


def test_global_registry_enforces_one_exact_typed_child_across_all_kinds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, sql = upgrade_sql(monkeypatch)
    registry = section(
        sql,
        "CREATE TABLE screening_opaque_id_registry",
        "CREATE TABLE screening_accepted_intake",
    )
    registry_check = section(
        sql,
        "CREATE FUNCTION tradesieve_require_screening_registry_child",
        "CREATE INDEX ix_screening_intake_tenant_received",
    )

    assert "PRIMARY KEY (opaque_id)" in normalized(registry)
    assert "UNIQUE (opaque_id, object_kind)" in normalized(registry)
    assert "object_kind IN ('INTAKE', 'SCREENING', 'ATTEMPT', 'OUTBOX')" in registry
    for table, identifier, kind in (
        ("screening_accepted_intake", "intake_id", "INTAKE"),
        ("screening_identity", "screening_id", "SCREENING"),
        ("screening_attempt_audit", "attempt_id", "ATTEMPT"),
        ("screening_accepted_outbox", "event_id", "OUTBOX"),
    ):
        table_sql = sql.split(f"CREATE TABLE {table}", maxsplit=1)[1].split(
            ");", maxsplit=1
        )[0]
        assert f"id_kind = '{kind}'" in table_sql
        assert f"({identifier}, id_kind)" in normalized(table_sql)
        assert (
            "REFERENCES screening_opaque_id_registry (opaque_id, object_kind)"
            in normalized(table_sql)
        )
    assert registry_check.count("WHEN '") == 4
    assert "WHEN 'INTAKE'" in registry_check
    assert "WHEN 'SCREENING'" in registry_check
    assert "WHEN 'ATTEMPT'" in registry_check
    assert "WHEN 'OUTBOX'" in registry_check
    assert registry_check.count("JOIN screening_idempotency_ledger") == 3
    assert "IF linked_count <> 1" in registry_check
    assert "CREATE CONSTRAINT TRIGGER trg_screening_registry_child" in sql
    assert "DEFERRABLE INITIALLY DEFERRED" in sql


def test_intake_row_binds_exact_raw_context_attribution_hashes_and_utc_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, sql = upgrade_sql(monkeypatch)
    intake = section(
        sql,
        "CREATE TABLE screening_accepted_intake",
        "CREATE TABLE screening_identity",
    )
    compact = normalized(intake)

    for column in (
        "tenant_id VARCHAR(128) NOT NULL",
        "client_id VARCHAR(128) NOT NULL",
        "actor_subject VARCHAR(128) NOT NULL",
        "actor_type VARCHAR(16) NOT NULL",
        "correlation_id VARCHAR(128) NOT NULL",
        "scope_fingerprint VARCHAR(71) NOT NULL",
        "schema_version VARCHAR(16) NOT NULL",
        "media_type VARCHAR(32) NOT NULL",
        "raw_body BYTEA NOT NULL",
        "byte_length INTEGER NOT NULL",
        "byte_hash VARCHAR(71) NOT NULL",
        "canonical_hash VARCHAR(71) NOT NULL",
        "received_at TIMESTAMPTZ NOT NULL",
        "private_context BYTEA NOT NULL",
    ):
        assert column in intake
    assert "schema_version = '1.0.0'" in intake
    assert "media_type = 'application/json'" in intake
    assert "byte_length BETWEEN 1 AND 1048576" in intake
    assert "octet_length(raw_body) = byte_length" in intake
    assert "octet_length(private_context) BETWEEN 1 AND 8192" in intake
    assert intake.count("~ '^sha256:[a-f0-9]{64}$'") == 3
    assert "actor_type IN ('HUMAN', 'SERVICE', 'AGENT')" in intake
    assert "isfinite(received_at)" in intake
    assert (
        "UNIQUE (intake_id, tenant_id, client_id, scope_fingerprint, "
        "canonical_hash)" in compact
    )


def test_scope_and_graph_foreign_keys_encode_exact_one_to_one_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, sql = upgrade_sql(monkeypatch)
    fingerprint = section(
        sql,
        "CREATE FUNCTION tradesieve_screening_scope_fingerprint",
        "CREATE TABLE screening_opaque_id_registry",
    )
    screening = section(
        sql,
        "CREATE TABLE screening_identity",
        "CREATE TABLE screening_accepted_outbox",
    )
    outbox = section(
        sql,
        "CREATE TABLE screening_accepted_outbox",
        "CREATE TABLE screening_idempotency_ledger",
    )
    ledger = section(
        sql,
        "CREATE TABLE screening_idempotency_ledger",
        "CREATE TABLE screening_attempt_audit",
    )
    compact_screening = normalized(screening)
    compact_outbox = normalized(outbox)
    compact_ledger = normalized(ledger)
    canonical_scope = fingerprint.split("SELECT", maxsplit=1)[1]

    assert "IMMUTABLE STRICT PARALLEL SAFE" in fingerprint
    assert "sha256(convert_to(" in fingerprint
    assert canonical_scope.index("client_id") < canonical_scope.index("key_digest")
    assert canonical_scope.index("key_digest") < canonical_scope.index("operation")
    assert canonical_scope.index("operation") < canonical_scope.index("tenant_id")
    assert "PRIMARY KEY (tenant_id, client_id, operation, key_digest)" in (
        compact_ledger
    )
    assert "operation = 'SCREENING_SUBMIT'" in ledger
    assert "scope_fingerprint = tradesieve_screening_scope_fingerprint(" in ledger
    assert "UNIQUE (scope_fingerprint)" in compact_ledger
    for stable_id in ("intake_id", "screening_id", "outbox_id"):
        assert f"UNIQUE ({stable_id})" in compact_ledger

    assert "UNIQUE (intake_id)" in compact_screening
    assert (
        "FOREIGN KEY (intake_id, tenant_id, client_id, scope_fingerprint, "
        "canonical_hash) REFERENCES screening_accepted_intake "
        "(intake_id, tenant_id, client_id, scope_fingerprint, canonical_hash)"
        in compact_screening
    )
    assert "UNIQUE (intake_id)" in compact_outbox
    assert "UNIQUE (screening_id)" in compact_outbox
    assert "event_type = 'screening.accepted'" in outbox
    assert "schema_version = '1.0.0'" in outbox
    assert (
        "FOREIGN KEY (screening_id, intake_id, occurred_at) "
        "REFERENCES screening_identity (screening_id, intake_id, created_at)"
        in compact_outbox
    )
    for exact_reference in (
        "fk_screening_ledger_intake",
        "fk_screening_ledger_screening",
        "fk_screening_ledger_outbox",
    ):
        assert f"CONSTRAINT {exact_reference} FOREIGN KEY" in ledger
    assert ledger.count("DEFERRABLE INITIALLY DEFERRED") == 3


def test_attempt_rows_are_bounded_contiguous_and_have_closed_hash_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, sql = upgrade_sql(monkeypatch)
    attempt = section(
        sql,
        "CREATE TABLE screening_attempt_audit",
        "CREATE FUNCTION tradesieve_validate_screening_attempt",
    )
    validation = section(
        sql,
        "CREATE FUNCTION tradesieve_validate_screening_attempt",
        "CREATE FUNCTION tradesieve_require_screening_complete_graph",
    )
    compact_attempt = normalized(attempt)

    assert (
        "PRIMARY KEY (tenant_id, client_id, operation, key_digest, sequence)"
        in compact_attempt
    )
    assert "UNIQUE (attempt_id)" in compact_attempt
    assert "UNIQUE (authorization_event_id)" in compact_attempt
    assert "sequence BETWEEN 1 AND 4096" in attempt
    assert "sequence = 1 AND outcome = 'APPLIED'" in compact_attempt
    assert "sequence > 1 AND outcome IN ('REPLAY', 'CONFLICT')" in compact_attempt
    assert "REFERENCES authorization_audit_event(event_id)" in compact_attempt
    assert "FOR UPDATE" in validation
    assert "SELECT COALESCE(max(sequence), 0) + 1 INTO expected_sequence" in (
        normalized(validation)
    )
    assert "ORDER BY sequence DESC" in validation
    assert "LIMIT 4097" in validation
    assert "IF expected_sequence > 4096" in validation
    assert "screening attempt history limit reached" in validation
    assert "NEW.sequence <> expected_sequence" in validation
    assert "NEW.sequence > 1 AND NEW.outcome = 'APPLIED'" in normalized(validation)
    assert (
        "NEW.canonical_hash = linked_ledger.canonical_hash AND "
        "NEW.outcome <> 'REPLAY'" in normalized(validation)
    )
    assert (
        "NEW.canonical_hash <> linked_ledger.canonical_hash AND "
        "NEW.outcome <> 'CONFLICT'" in normalized(validation)
    )


def test_attempt_trigger_requires_exact_allow_target_attribution_and_applied_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, sql = upgrade_sql(monkeypatch)
    validation = section(
        sql,
        "CREATE FUNCTION tradesieve_validate_screening_attempt",
        "CREATE FUNCTION tradesieve_require_screening_complete_graph",
    )
    compact = normalized(validation)

    for authorization_match in (
        "linked_authorization.outcome <> 'ALLOW'",
        "linked_authorization.reason <> 'ALLOWED'",
        "linked_authorization.occurred_at <> NEW.occurred_at",
        "linked_authorization.actor_subject <> NEW.actor_subject",
        "linked_authorization.actor_type <> NEW.actor_type",
        "linked_authorization.client_id <> NEW.client_id",
        "linked_authorization.actor_tenant_id <> NEW.tenant_id",
        "linked_authorization.request_tenant_id <> NEW.tenant_id",
        "linked_authorization.target_tenant_id <> NEW.tenant_id",
        "linked_authorization.operation <> NEW.operation",
        "linked_authorization.target_ref <> expected_target",
        "linked_authorization.correlation_id <> NEW.correlation_id",
    ):
        assert authorization_match in validation
    assert (
        "'SCREENING_IDEMPOTENCY:idem-' || substr(NEW.scope_fingerprint, 8)" in compact
    )
    target_assignment = section(validation, "expected_target :=", ";")
    assert "scope_fingerprint" in target_assignment
    assert "actor" not in target_assignment
    assert "correlation" not in target_assignment
    assert "byte_hash" not in target_assignment
    assert "canonical_hash" not in target_assignment
    for applied_match in (
        "NEW.occurred_at <> linked_ledger.committed_at",
        "linked_ledger.committed_at < linked_intake.received_at",
        "NEW.actor_type <> linked_intake.actor_type",
        "NEW.actor_subject <> linked_intake.actor_subject",
        "NEW.correlation_id <> linked_intake.correlation_id",
        "NEW.byte_hash <> linked_intake.byte_hash",
        "NEW.canonical_hash <> linked_intake.canonical_hash",
        "NEW.canonical_hash <> linked_ledger.canonical_hash",
    ):
        assert applied_match in validation


def test_deferred_complete_graph_immutability_and_indexes_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, sql = upgrade_sql(monkeypatch)
    complete = section(
        sql,
        "CREATE FUNCTION tradesieve_require_screening_complete_graph",
        "CREATE FUNCTION tradesieve_require_screening_registry_child",
    )
    index_statements = re.findall(r"CREATE INDEX [^;]+;", sql, flags=re.DOTALL)

    assert "SELECT count(*) INTO graph_count" in complete
    assert "JOIN screening_identity AS screening" in complete
    assert "JOIN screening_accepted_outbox AS outbox" in complete
    assert "NEW.committed_at >= intake.received_at" in complete
    assert "IF graph_count <> 1" in complete
    assert "SELECT count(*) INTO applied_count" in complete
    assert "sequence = 1" in complete
    assert "outcome = 'APPLIED'" in complete
    assert "occurred_at = NEW.committed_at" in complete
    assert "IF applied_count <> 1" in complete
    assert "CREATE CONSTRAINT TRIGGER trg_screening_ledger_complete" in sql
    assert "AFTER INSERT ON screening_idempotency_ledger" in sql
    assert sql.count("DEFERRABLE INITIALLY DEFERRED") >= 10

    for table in EXPECTED_TABLES:
        assert f"BEFORE UPDATE OR DELETE ON {table}" in sql
    assert len(index_statements) == len(EXPECTED_INDEXES)
    assert all("raw_body" not in statement for statement in index_statements)
    assert all("private_context" not in statement for statement in index_statements)
    assert all("key_digest" not in statement for statement in index_statements)


def test_downgrade_removes_only_0005_objects_in_reverse_dependency_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration, sql = downgrade_sql(monkeypatch)

    assert migration.revision == "20260806_0005"
    assert migration.down_revision == "20260806_0004"
    assert set(re.findall(r"DROP TABLE ([a-z0-9_]+)", sql)) == EXPECTED_TABLES
    assert set(re.findall(r"DROP FUNCTION ([a-z0-9_]+)\(", sql)) == (EXPECTED_FUNCTIONS)
    assert "authorization_audit_event" not in sql
    assert "source_" not in sql
    assert "rule_bundle" not in sql
    assert "runtime_" not in sql
    assert "tradesieve_reject_immutable_change" not in sql
    assert "CASCADE" not in sql

    attempt = sql.index("DROP TABLE screening_attempt_audit")
    attempt_function = sql.index("DROP FUNCTION tradesieve_validate_screening_attempt")
    ledger = sql.index("DROP TABLE screening_idempotency_ledger")
    graph_function = sql.index(
        "DROP FUNCTION tradesieve_require_screening_complete_graph"
    )
    outbox = sql.index("DROP TABLE screening_accepted_outbox")
    screening = sql.index("DROP TABLE screening_identity")
    intake = sql.index("DROP TABLE screening_accepted_intake")
    registry = sql.index("DROP TABLE screening_opaque_id_registry")
    registry_function = sql.index(
        "DROP FUNCTION tradesieve_require_screening_registry_child"
    )
    fingerprint = sql.index("DROP FUNCTION tradesieve_screening_scope_fingerprint")
    assert (
        attempt
        < attempt_function
        < ledger
        < graph_function
        < outbox
        < screening
        < intake
        < registry
        < registry_function
        < fingerprint
    )
