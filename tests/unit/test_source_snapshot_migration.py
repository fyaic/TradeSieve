"""Static and executable assertions for the source-snapshot schema migration."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

import pytest

EXPECTED_TABLES = {
    "source_raw_object_metadata",
    "source_parsed_snapshot",
    "source_snapshot_validation_evidence",
    "source_snapshot_lifecycle_state",
    "source_snapshot_lifecycle_event",
    "source_snapshot_command_audit",
}

EXPECTED_FUNCTIONS = {
    "tradesieve_valid_source_snapshot_payload",
    "tradesieve_valid_source_validation_payload",
    "tradesieve_validate_source_parsed_snapshot_insert",
    "tradesieve_validate_source_validation_insert",
    "tradesieve_validate_source_snapshot_audit_link",
    "tradesieve_require_source_snapshot_event_audit",
}

EXPECTED_CONSTRAINTS = {
    "pk_source_raw_object_metadata",
    "uq_source_raw_object_full_ref",
    "ck_source_raw_object_ids",
    "ck_source_raw_object_name",
    "ck_source_raw_object_media",
    "ck_source_raw_object_time",
    "ck_source_raw_object_size_hash",
    "fk_source_raw_object_registry",
    "pk_source_parsed_snapshot",
    "uq_source_parsed_snapshot_full_ref",
    "uq_source_parsed_snapshot_event_ref",
    "ck_source_parsed_snapshot_ids",
    "ck_source_parsed_snapshot_hashes",
    "ck_source_parsed_snapshot_binding",
    "ck_source_parsed_snapshot_time",
    "ck_source_parsed_snapshot_payload",
    "fk_source_parsed_snapshot_registry",
    "fk_source_parsed_snapshot_raw",
    "pk_source_snapshot_validation",
    "uq_source_snapshot_validation_full_ref",
    "ck_source_snapshot_validation_ids",
    "ck_source_snapshot_validation_hashes",
    "ck_source_snapshot_validation_previous",
    "ck_source_snapshot_validation_schema_time",
    "ck_source_snapshot_validation_payload",
    "fk_source_snapshot_validation_current",
    "fk_source_snapshot_validation_previous",
    "pk_source_snapshot_lifecycle_state",
    "ck_source_snapshot_state_ids",
    "ck_source_snapshot_state_sequence",
    "ck_source_snapshot_state_active",
    "ck_source_snapshot_state_time",
    "fk_source_snapshot_state_registry",
    "fk_source_snapshot_state_active",
    "pk_source_snapshot_lifecycle_event",
    "uq_source_snapshot_lifecycle_event_id",
    "ck_source_snapshot_event_ids",
    "ck_source_snapshot_event_sequence",
    "ck_source_snapshot_event_type",
    "ck_source_snapshot_event_raw_ref",
    "ck_source_snapshot_event_snapshot_ref",
    "ck_source_snapshot_event_previous_ref",
    "ck_source_snapshot_event_actor",
    "ck_source_snapshot_event_reason_time",
    "ck_source_snapshot_event_snapshot_shape",
    "ck_source_snapshot_event_previous_shape",
    "ck_source_snapshot_event_validation_shape",
    "ck_source_snapshot_event_validation_hashes",
    "fk_source_snapshot_event_registry",
    "fk_source_snapshot_event_raw",
    "fk_source_snapshot_event_snapshot_raw",
    "fk_source_snapshot_event_previous",
    "fk_source_snapshot_event_validation",
    "uq_source_snapshot_audit_lifecycle",
    "ck_source_snapshot_audit_ids",
    "ck_source_snapshot_audit_snapshot_ref",
    "ck_source_snapshot_audit_snapshot_shape",
    "ck_source_snapshot_audit_target",
    "ck_source_snapshot_audit_operation",
    "ck_source_snapshot_audit_actor",
    "ck_source_snapshot_audit_outcome",
    "ck_source_snapshot_audit_reason",
    "ck_source_snapshot_audit_outcome_reason",
    "ck_source_snapshot_audit_operation_reason",
    "ck_source_snapshot_audit_lifecycle_link",
    "ck_source_snapshot_audit_time",
    "fk_source_snapshot_audit_authorization",
    "fk_source_snapshot_audit_lifecycle",
    "fk_source_snapshot_audit_registry",
    "fk_source_observation_active_snapshot",
}

EXPECTED_TRIGGERS = {
    "trg_source_parsed_snapshot_insert",
    "trg_source_snapshot_validation_insert",
    "trg_source_raw_object_immutable",
    "trg_source_parsed_snapshot_immutable",
    "trg_source_snapshot_validation_immutable",
    "trg_source_snapshot_event_immutable",
    "trg_source_snapshot_audit_immutable",
    "trg_source_snapshot_audit_link",
    "trg_source_snapshot_event_requires_audit",
}

EXPECTED_INDEXES = {
    "ix_source_raw_object_scope_retrieved",
    "ix_source_parsed_snapshot_scope_parsed",
    "ix_source_snapshot_validation_scope_time",
    "ix_source_snapshot_event_scope_time",
    "ix_source_snapshot_event_scope_type",
    "ix_source_snapshot_audit_scope_time",
}

EXPECTED_EVENT_TYPES = {
    "RETRIEVED",
    "QUARANTINED",
    "PARSED",
    "VALIDATION_FAILED",
    "VALIDATED",
    "APPROVED",
    "ACTIVATED",
    "ROLLED_BACK",
}

EXPECTED_OPERATIONS = {
    "SOURCE_SNAPSHOT_INGEST",
    "SOURCE_SNAPSHOT_PARSE",
    "SOURCE_SNAPSHOT_VALIDATE",
    "SOURCE_SNAPSHOT_APPROVE",
    "SOURCE_SNAPSHOT_ACTIVATE",
    "SOURCE_SNAPSHOT_ROLLBACK",
}

EXPECTED_REASONS = {
    "RETRIEVED",
    "RETRIEVE_IDEMPOTENT",
    "QUARANTINED",
    "QUARANTINE_IDEMPOTENT",
    "PARSED",
    "PARSE_IDEMPOTENT",
    "VALIDATION_FAILED",
    "VALIDATION_FAILURE_IDEMPOTENT",
    "VALIDATED",
    "VALIDATE_IDEMPOTENT",
    "APPROVED",
    "APPROVE_IDEMPOTENT",
    "ACTIVATED",
    "ACTIVATE_IDEMPOTENT",
    "ROLLED_BACK",
    "ROLLBACK_IDEMPOTENT",
    "CONFLICT",
    "AUTHORIZATION_BINDING_DENIED",
    "CREATOR_SEPARATION_DENIED",
    "VALIDATION_BLOCKED",
    "INVALID_LIFECYCLE",
    "PARSER_REJECTED",
    "MEDIA_BINDING_DENIED",
    "PERSISTENCE_FAILURE",
}


def migration_module() -> Any:
    path = (
        Path(__file__).parents[2]
        / "migrations/versions/20260806_0004_source_snapshots.py"
    )
    spec = importlib.util.spec_from_file_location("source_snapshot_migration", path)
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


def quoted_enum_values(value: str) -> set[str]:
    return set(re.findall(r"'([A-Z][A-Z0-9_]*)'", value))


def test_source_snapshot_migration_owns_exact_schema_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration, sql = upgrade_sql(monkeypatch)

    assert migration.revision == "20260806_0004"
    assert migration.down_revision == "20260806_0003"
    assert set(re.findall(r"CREATE TABLE ([a-z0-9_]+)", sql)) == EXPECTED_TABLES
    assert set(re.findall(r"CREATE FUNCTION ([a-z0-9_]+)\(", sql)) == EXPECTED_FUNCTIONS
    assert (
        set(re.findall(r"CONSTRAINT (?!TRIGGER)([a-z0-9_]+)", sql))
        == EXPECTED_CONSTRAINTS
    )
    assert (
        set(re.findall(r"CREATE (?:CONSTRAINT )?TRIGGER ([a-z0-9_]+)", sql))
        == EXPECTED_TRIGGERS
    )
    assert set(re.findall(r"CREATE INDEX ([a-z0-9_]+)", sql)) == EXPECTED_INDEXES
    assert "command_event_id VARCHAR(128) PRIMARY KEY" in sql
    assert "CREATE TABLE authorization_audit_event" not in sql
    assert "CREATE FUNCTION tradesieve_reject_immutable_change" not in sql
    assert "REFERENCES authorization_audit_event(event_id)" in sql
    assert sql.count("EXECUTE FUNCTION tradesieve_reject_immutable_change()") == 5
    assert "ON DELETE CASCADE" not in sql
    assert "FOR UPDATE" not in sql


def test_payload_documents_are_byte_exact_bounded_and_strictly_shaped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, sql = upgrade_sql(monkeypatch)
    snapshot = section(
        sql,
        "CREATE FUNCTION tradesieve_valid_source_snapshot_payload",
        "CREATE FUNCTION tradesieve_valid_source_validation_payload",
    )
    validation = section(
        sql,
        "CREATE FUNCTION tradesieve_valid_source_validation_payload",
        "CREATE TABLE source_raw_object_metadata",
    )

    for payload, maximum, key_count in (
        (snapshot, 4_194_304, 12),
        (validation, 2_097_152, 10),
    ):
        assert "(value BYTEA)" in payload
        assert "IMMUTABLE STRICT PARALLEL SAFE" in payload
        assert "octet_length(value) = 0" in payload
        assert f"octet_length(value) > {maximum}" in payload
        assert "document_text := convert_from(value, 'UTF8')" in payload
        assert "document_text IS JSON OBJECT WITH UNIQUE KEYS" in payload
        assert "document := document_text::jsonb" in payload
        assert "EXCEPTION WHEN OTHERS" in payload
        assert "jsonb_typeof(document) = 'object'" in payload
        assert f"jsonb_object_keys(document)) = {key_count}" in payload
        assert "document ?& ARRAY[" in payload
        assert "document ->> 'schema_version' = '1.0.0'" in payload

    assert {
        "schema_version",
        "deployment_id",
        "source_id",
        "snapshot_id",
        "content_hash",
        "raw_object",
        "parser_id",
        "parser_version",
        "schema_id",
        "declared_record_count",
        "parsed_at",
        "records",
    } <= set(re.findall(r"'([a-z_]+)'", snapshot))
    assert "jsonb_typeof(document -> 'records') = 'array'" in snapshot
    assert "document ->> 'declared_record_count' ~ '^(0|[1-9][0-9]*)$'" in snapshot
    assert "(document ->> 'declared_record_count')::numeric <= 512" in snapshot
    assert {
        "schema_version",
        "deployment_id",
        "source_id",
        "snapshot",
        "expected_schema_id",
        "passed",
        "reasons",
        "diff",
        "validated_at",
        "content_hash",
    } <= set(re.findall(r"'([a-z_]+)'", validation))
    for expected in (
        "jsonb_typeof(document -> 'snapshot') = 'object'",
        "jsonb_typeof(document -> 'passed') = 'boolean'",
        "jsonb_typeof(document -> 'reasons') = 'array'",
        "jsonb_typeof(document -> 'diff') = 'object'",
    ):
        assert expected in validation
    assert sql.count("private_payload BYTEA NOT NULL") == 2
    assert "octet_length(private_payload) BETWEEN 1 AND 4194304" in sql
    assert "octet_length(private_payload) BETWEEN 1 AND 2097152" in sql


def test_raw_and_parsed_rows_bind_exact_content_addressed_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, sql = upgrade_sql(monkeypatch)
    raw = section(
        sql,
        "CREATE TABLE source_raw_object_metadata",
        "CREATE TABLE source_parsed_snapshot",
    )
    parsed = section(
        sql,
        "CREATE TABLE source_parsed_snapshot",
        "CREATE TABLE source_snapshot_validation_evidence",
    )
    compact_raw = normalized(raw)
    compact_parsed = normalized(parsed)

    assert "PRIMARY KEY (deployment_id, source_id, object_id)" in compact_raw
    assert (
        "UNIQUE (deployment_id, source_id, object_id, content_hash, byte_length)"
        in compact_raw
    )
    assert "object_id ~ '^raw-[a-f0-9]{64}$'" in raw
    assert "byte_length BETWEEN 0 AND 1048576" in raw
    assert "content_hash ~ '^sha256:[a-f0-9]{64}$'" in raw
    assert "position('/' IN original_name) = 0" in raw
    assert "REFERENCES source_registry(deployment_id, source_id)" in compact_raw

    assert "PRIMARY KEY (deployment_id, source_id, snapshot_id)" in compact_parsed
    assert (
        "UNIQUE (deployment_id, source_id, snapshot_id, content_hash, "
        "raw_object_id, raw_content_hash, raw_byte_length)" in compact_parsed
    )
    assert "snapshot_id = 'snapshot-' || substr(content_hash, 8)" in compact_parsed
    assert "parser_id = 'synthetic-json-v1'" in parsed
    assert "parser_version = '1.0.0'" in parsed
    assert "schema_id = 'tradesieve-synthetic-source-v1'" in parsed
    assert "declared_record_count BETWEEN 0 AND 512" in parsed
    assert (
        "FOREIGN KEY (deployment_id, source_id, raw_object_id, raw_content_hash, "
        "raw_byte_length) REFERENCES source_raw_object_metadata "
        "(deployment_id, source_id, object_id, content_hash, byte_length)"
        in compact_parsed
    )
    assert "BEFORE INSERT ON source_parsed_snapshot" in parsed
    assert "NEW.parsed_at < raw_retrieved_at" in parsed
    for exact_raw_field in (
        "deployment_id",
        "source_id",
        "object_id",
        "content_hash",
        "byte_length",
    ):
        assert exact_raw_field in parsed


def test_validation_and_state_keep_exact_scoped_snapshot_projections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, sql = upgrade_sql(monkeypatch)
    validation = section(
        sql,
        "CREATE TABLE source_snapshot_validation_evidence",
        "CREATE TABLE source_snapshot_lifecycle_state",
    )
    state = section(
        sql,
        "CREATE TABLE source_snapshot_lifecycle_state",
        "CREATE TABLE source_snapshot_lifecycle_event",
    )
    compact_validation = normalized(validation)
    compact_state = normalized(state)

    assert "PRIMARY KEY (deployment_id, source_id, snapshot_id)" in compact_validation
    assert (
        "UNIQUE (deployment_id, source_id, snapshot_id, snapshot_content_hash, "
        "report_content_hash, diff_content_hash, passed)" in compact_validation
    )
    assert (
        "num_nonnulls( previous_deployment_id, previous_source_id, "
        "previous_snapshot_id, previous_snapshot_content_hash ) IN (0, 4)"
        in compact_validation
    )
    assert "previous_deployment_id = deployment_id" in validation
    assert "previous_source_id = source_id" in validation
    assert "MATCH FULL" in validation
    assert (
        "FOREIGN KEY (deployment_id, source_id, snapshot_id, "
        "snapshot_content_hash) REFERENCES source_parsed_snapshot "
        "(deployment_id, source_id, snapshot_id, content_hash)" in compact_validation
    )
    assert "expected_schema_id = 'tradesieve-synthetic-source-v1'" in validation
    assert "BEFORE INSERT ON source_snapshot_validation_evidence" in validation
    assert "NEW.validated_at < snapshot_parsed_at" in validation

    assert "PRIMARY KEY (deployment_id, source_id)" in compact_state
    assert "last_sequence >= 0" in state
    assert (
        "num_nonnulls( active_snapshot_id, active_snapshot_content_hash ) IN (0, 2)"
        in compact_state
    )
    assert (
        "FOREIGN KEY (deployment_id, source_id, active_snapshot_id, "
        "active_snapshot_content_hash) REFERENCES source_parsed_snapshot "
        "(deployment_id, source_id, snapshot_id, content_hash)" in compact_state
    )
    assert "REFERENCES source_registry(deployment_id, source_id)" in compact_state

    observation = sql.split("ALTER TABLE source_runtime_observation", maxsplit=1)[1]
    assert "ADD CONSTRAINT fk_source_observation_active_snapshot" in observation
    assert (
        "FOREIGN KEY (deployment_id, source_id, active_snapshot_id) "
        "REFERENCES source_parsed_snapshot (deployment_id, source_id, snapshot_id) "
        "NOT VALID" in normalized(observation)
    )
    assert "UPDATE source_runtime_observation" not in sql
    assert "DELETE FROM source_runtime_observation" not in sql


def test_lifecycle_event_constraints_encode_every_shape_and_exact_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, sql = upgrade_sql(monkeypatch)
    event = section(
        sql,
        "CREATE TABLE source_snapshot_lifecycle_event",
        "CREATE TABLE source_snapshot_command_audit",
    )
    event_types = section(
        event,
        "CONSTRAINT ck_source_snapshot_event_type CHECK (",
        "CONSTRAINT ck_source_snapshot_event_raw_ref",
    )
    compact_event = normalized(event)

    assert quoted_enum_values(event_types) == EXPECTED_EVENT_TYPES
    assert "PRIMARY KEY (deployment_id, source_id, sequence)" in compact_event
    assert "UNIQUE (event_id)" in event
    assert "sequence > 0" in event
    assert "num_nonnulls(snapshot_id, snapshot_content_hash) IN (0, 2)" in event
    assert (
        "num_nonnulls( previous_active_snapshot_id, "
        "previous_active_snapshot_content_hash ) IN (0, 2)" in compact_event
    )
    assert (
        "event_type IN ('RETRIEVED', 'QUARANTINED') AND snapshot_id IS NULL"
        in compact_event
    )
    assert (
        "event_type NOT IN ('RETRIEVED', 'QUARANTINED') AND "
        "snapshot_id IS NOT NULL" in compact_event
    )
    assert (
        "event_type = 'ROLLED_BACK' AND previous_active_snapshot_id IS NOT NULL"
        in compact_event
    )
    assert "event_type = 'ACTIVATED'" in event
    assert (
        "event_type NOT IN ('ACTIVATED', 'ROLLED_BACK') AND "
        "previous_active_snapshot_id IS NULL" in compact_event
    )
    assert "actor_type IN ('HUMAN', 'SERVICE')" in event
    assert (
        "event_type NOT IN ('APPROVED', 'ACTIVATED', 'ROLLED_BACK') OR "
        "actor_type = 'HUMAN'" in compact_event
    )
    assert "event_type = 'VALIDATED' AND validation_passed IS TRUE" in compact_event
    assert (
        "event_type = 'VALIDATION_FAILED' AND validation_passed IS FALSE"
        in compact_event
    )
    for reference in (
        "fk_source_snapshot_event_registry",
        "fk_source_snapshot_event_raw",
        "fk_source_snapshot_event_snapshot_raw",
        "fk_source_snapshot_event_previous",
        "fk_source_snapshot_event_validation",
    ):
        assert f"CONSTRAINT {reference} FOREIGN KEY" in event
    assert (
        "REFERENCES source_snapshot_validation_evidence "
        "(deployment_id, source_id, snapshot_id, snapshot_content_hash, "
        "report_content_hash, diff_content_hash, passed)" in compact_event
    )


def test_command_audit_has_closed_taxonomy_and_authorization_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, sql = upgrade_sql(monkeypatch)
    audit = section(
        sql,
        "CREATE TABLE source_snapshot_command_audit",
        "CREATE FUNCTION tradesieve_require_source_snapshot_event_audit",
    )
    operations = section(
        audit,
        "CONSTRAINT ck_source_snapshot_audit_operation CHECK (",
        "CONSTRAINT ck_source_snapshot_audit_actor",
    )
    reasons = section(
        audit,
        "CONSTRAINT ck_source_snapshot_audit_reason CHECK (",
        "CONSTRAINT ck_source_snapshot_audit_outcome_reason",
    )
    compact_audit = normalized(audit)

    assert quoted_enum_values(operations) == EXPECTED_OPERATIONS
    assert quoted_enum_values(reasons) == EXPECTED_REASONS
    assert "outcome IN ('SUCCESS', 'FAILURE')" in audit
    assert "target_type VARCHAR(128) NOT NULL" in audit
    assert "target_id VARCHAR(128) NOT NULL" in audit
    assert "target_type = 'source'" in audit
    assert "target_type = 'source_snapshot'" in audit
    assert "target_id ~ '^source-[a-f0-9]{64}$'" in audit
    assert "target_id ~ '^source-snapshot-[a-f0-9]{64}$'" in audit
    assert (
        "reason IN ( 'RETRIEVED', 'QUARANTINED', 'PARSED', "
        "'VALIDATION_FAILED', 'VALIDATED', 'APPROVED', 'ACTIVATED', "
        "'ROLLED_BACK' )) = (lifecycle_event_id IS NOT NULL)" in compact_audit
    )
    assert "REFERENCES authorization_audit_event(event_id)" in compact_audit
    assert "BEFORE INSERT ON source_snapshot_command_audit" in sql
    assert "CONSTRAINT fk_source_snapshot_audit_raw" not in audit
    assert "CONSTRAINT fk_source_snapshot_audit_snapshot" not in audit
    assert "CONSTRAINT fk_source_snapshot_event_raw FOREIGN KEY" in sql
    assert "CONSTRAINT fk_source_snapshot_event_snapshot_raw FOREIGN KEY" in sql
    assert "Standalone failure audits intentionally have no unconditional" in audit
    assert "artifact FK. Applied provenance is enforced through the linked" in audit
    for authorization_binding in (
        "linked_authorization.outcome <> 'ALLOW'",
        "linked_authorization.actor_subject <> NEW.actor_id",
        "linked_authorization.actor_type <> NEW.actor_type",
        "linked_authorization.actor_tenant_id <> NEW.tenant_id",
        "linked_authorization.request_tenant_id <> NEW.tenant_id",
        "linked_authorization.target_tenant_id <> NEW.tenant_id",
        "linked_authorization.operation <> NEW.operation",
        "NEW.target_type || ':' || NEW.target_id",
    ):
        assert authorization_binding in audit
    for lifecycle_binding in (
        "NEW.deployment_id <> linked_event.deployment_id",
        "NEW.source_id <> linked_event.source_id",
        "NEW.raw_object_id <> linked_event.raw_object_id",
        "NEW.snapshot_id IS DISTINCT FROM linked_event.snapshot_id",
        "NEW.snapshot_content_hash IS DISTINCT FROM",
        "NEW.actor_id <> linked_event.actor_id",
        "NEW.actor_type <> linked_event.actor_type",
        "NEW.occurred_at <> linked_event.occurred_at",
    ):
        assert lifecycle_binding in audit
    assert audit.count("expected_reason := CASE linked_event.event_type") == 1
    assert audit.count("expected_operation := CASE linked_event.event_type") == 1
    assert "NEW.reason <> expected_reason" in audit
    assert "NEW.operation <> expected_operation" in audit
    assert "NEW.target_type <> expected_target_type" in audit


def test_deferred_audit_and_immutability_triggers_are_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, sql = upgrade_sql(monkeypatch)
    deferred = section(
        sql,
        "CREATE FUNCTION tradesieve_require_source_snapshot_event_audit",
        "CREATE INDEX ix_source_raw_object_scope_retrieved",
    )

    assert "SELECT count(*) INTO linked_count" in deferred
    assert "WHERE lifecycle_event_id = NEW.event_id" in deferred
    assert "IF linked_count <> 1" in deferred
    assert "CREATE CONSTRAINT TRIGGER trg_source_snapshot_event_requires_audit" in sql
    assert "AFTER INSERT ON source_snapshot_lifecycle_event" in sql
    assert "DEFERRABLE INITIALLY DEFERRED" in sql
    for table in (
        "source_raw_object_metadata",
        "source_parsed_snapshot",
        "source_snapshot_validation_evidence",
        "source_snapshot_lifecycle_event",
        "source_snapshot_command_audit",
    ):
        assert f"BEFORE UPDATE OR DELETE ON {table}" in sql


def test_source_snapshot_migration_downgrade_reverses_owned_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration, sql = downgrade_sql(monkeypatch)

    assert migration.revision == "20260806_0004"
    assert migration.down_revision == "20260806_0003"
    assert set(re.findall(r"DROP TABLE ([a-z0-9_]+)", sql)) == EXPECTED_TABLES
    assert set(re.findall(r"DROP FUNCTION ([a-z0-9_]+)\(", sql)) == EXPECTED_FUNCTIONS
    assert "DROP TABLE authorization_audit_event" not in sql
    assert "DROP FUNCTION tradesieve_reject_immutable_change" not in sql
    assert "CASCADE" not in sql

    observation = sql.index("DROP CONSTRAINT fk_source_observation_active_snapshot")
    audit = sql.index("DROP TABLE source_snapshot_command_audit")
    audit_link = sql.index(
        "DROP FUNCTION tradesieve_validate_source_snapshot_audit_link"
    )
    event_audit_trigger = sql.index(
        "DROP TRIGGER trg_source_snapshot_event_requires_audit"
    )
    event_audit_function = sql.index(
        "DROP FUNCTION tradesieve_require_source_snapshot_event_audit"
    )
    event = sql.index("DROP TABLE source_snapshot_lifecycle_event")
    state = sql.index("DROP TABLE source_snapshot_lifecycle_state")
    validation = sql.index("DROP TABLE source_snapshot_validation_evidence")
    validation_function = sql.index(
        "DROP FUNCTION tradesieve_validate_source_validation_insert"
    )
    parsed = sql.index("DROP TABLE source_parsed_snapshot")
    parsed_function = sql.index(
        "DROP FUNCTION tradesieve_validate_source_parsed_snapshot_insert"
    )
    raw = sql.index("DROP TABLE source_raw_object_metadata")
    validation_payload = sql.index(
        "DROP FUNCTION tradesieve_valid_source_validation_payload"
    )
    snapshot_payload = sql.index(
        "DROP FUNCTION tradesieve_valid_source_snapshot_payload"
    )
    assert (
        observation
        < audit
        < audit_link
        < event_audit_trigger
        < event_audit_function
        < event
        < state
        < validation
        < validation_function
        < parsed
        < parsed_function
        < raw
        < validation_payload
        < snapshot_payload
    )
