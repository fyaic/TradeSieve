"""Static and executable assertions for the rule-bundle schema migration."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest


def migration_module() -> Any:
    path = (
        Path(__file__).parents[2] / "migrations/versions/20260806_0003_rule_bundles.py"
    )
    spec = importlib.util.spec_from_file_location("rule_bundle_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rule_bundle_migration_upgrade_has_strict_immutable_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = migration_module()
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "execute", statements.append)
    migration.upgrade()
    assert len(statements) == 1
    sql = statements[0]
    for table in (
        "authorization_audit_event",
        "rule_bundle_version",
        "rule_bundle_lifecycle_state",
        "rule_bundle_lifecycle_event",
        "rule_bundle_command_audit",
    ):
        assert f"CREATE TABLE {table}" in sql
    assert "MATCH FULL" in sql
    assert "ON DELETE CASCADE" not in sql
    assert "FOR UPDATE" not in sql
    assert "octet_length(payload::text) <= 1048576" in sql
    assert "tradesieve_valid_rescreen_impact" in sql
    assert "jsonb_object_keys" in sql
    assert "jsonb_array_elements" in sql
    assert sql.count("CASE WHEN jsonb_typeof(value) <>") >= 3
    assert "rescreen_impact -> 'previous_bundle'" in sql
    assert "rescreen_impact -> 'new_bundle'" in sql
    assert "reason !~ '[[:cntrl:]]'" in sql
    assert "event_type <> 'DRAFTED' AND actor_type = 'HUMAN'" in sql
    for constraint in (
        "ck_rule_bundle_state_ids",
        "ck_rule_bundle_event_ids",
        "ck_rule_bundle_event_shape",
        "ck_rule_bundle_event_impact_document",
        "ck_rule_bundle_audit_ids",
        "ck_rule_bundle_audit_outcome_reason",
        "ck_rule_bundle_audit_lifecycle_link",
        "uq_rule_bundle_audit_lifecycle",
        "ck_authorization_audit_outcome_reason",
    ):
        assert constraint in sql
    for trigger in (
        "trg_rule_bundle_version_immutable",
        "trg_rule_bundle_event_immutable",
        "trg_rule_bundle_audit_immutable",
        "trg_rule_bundle_audit_link",
        "trg_authorization_audit_immutable",
    ):
        assert f"CREATE TRIGGER {trigger}" in sql
    assert "CREATE CONSTRAINT TRIGGER trg_rule_bundle_event_requires_audit" in sql
    assert "tradesieve_validate_rule_bundle_audit_link" in sql
    assert "tradesieve_require_rule_bundle_event_audit" in sql
    assert "DEFERRABLE INITIALLY DEFERRED" in sql
    assert "fk_rule_bundle_audit_authorization" in sql
    for linked_field in (
        "NEW.actor_id <> linked.actor_id",
        "NEW.actor_type <> linked.actor_type",
        "NEW.occurred_at <> linked.occurred_at",
        "NEW.bundle_content_hash <> linked_hash",
    ):
        assert linked_field in sql
    for linked_authorization_field in (
        "linked_authorization.outcome <> 'ALLOW'",
        "linked_authorization.actor_subject <> NEW.actor_id",
        "linked_authorization.actor_type <> NEW.actor_type",
        "linked_authorization.actor_tenant_id <> NEW.tenant_id",
        "linked_authorization.request_tenant_id <> NEW.tenant_id",
        "linked_authorization.target_tenant_id <> NEW.tenant_id",
        "linked_authorization.operation <> NEW.operation",
        "NEW.target_type || ':' || NEW.target_id",
    ):
        assert linked_authorization_field in sql
    assert "target_ref ~" in sql
    assert "ix_rule_bundle_event_scope_time" in sql
    assert "ix_rule_bundle_audit_scope_time" in sql


def test_rule_bundle_migration_downgrade_reverses_dependency_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = migration_module()
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "execute", statements.append)
    migration.downgrade()
    sql = statements[0]
    audit = sql.index("DROP TABLE rule_bundle_command_audit")
    link_function = sql.index(
        "DROP FUNCTION tradesieve_validate_rule_bundle_audit_link"
    )
    authorization = sql.index("DROP TABLE authorization_audit_event")
    event_audit_trigger = sql.index("DROP TRIGGER trg_rule_bundle_event_requires_audit")
    event_audit_function = sql.index(
        "DROP FUNCTION tradesieve_require_rule_bundle_event_audit"
    )
    event = sql.index("DROP TABLE rule_bundle_lifecycle_event")
    state = sql.index("DROP TABLE rule_bundle_lifecycle_state")
    bundle = sql.index("DROP TABLE rule_bundle_version")
    immutable_function = sql.index("DROP FUNCTION tradesieve_reject_immutable_change")
    assert (
        audit
        < link_function
        < authorization
        < event_audit_trigger
        < event_audit_function
        < event
        < state
        < bundle
        < immutable_function
    )
    assert migration.revision == "20260806_0003"
    assert migration.down_revision == "20260806_0002"
