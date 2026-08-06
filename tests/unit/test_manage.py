"""Runtime management command tests."""

from __future__ import annotations

import json
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import psycopg
import pytest
from alembic.config import Config

from tradesieve import manage
from tradesieve.config import Settings
from tradesieve.runtime import RuntimeStatus


def test_sqlalchemy_database_url_selects_psycopg_driver() -> None:
    assert manage.sqlalchemy_database_url(  # pragma: allowlist secret
        "postgresql://user:pass@db/name"
    ) == ("postgresql+psycopg://user:pass@db/name")
    explicit = "postgresql+psycopg://user:pass@db/name"  # pragma: allowlist secret
    assert manage.sqlalchemy_database_url(explicit) == explicit
    assert (
        manage.alembic_database_url(  # pragma: allowlist secret
            "postgresql://user:p%40ss@db/name"
        )
        == "postgresql+psycopg://user:p%%40ss@db/name"
    )


def test_migrate_configures_real_revision_path_driver_and_encoded_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_upgrade(config: Config, target: str) -> None:
        captured["url"] = config.get_main_option("sqlalchemy.url")
        captured["script_location"] = config.get_main_option("script_location")
        captured["target"] = target

    monkeypatch.setattr("tradesieve.manage.command.upgrade", fake_upgrade)
    settings = Settings(
        database_url="postgresql://tradesieve:p%40ss@postgres:5432/tradesieve"  # pragma: allowlist secret
    )
    manage.migrate(settings)
    assert captured["url"] == (
        "postgresql+psycopg://tradesieve:p%40ss@postgres:5432/tradesieve"
    )
    assert str(captured["script_location"]).endswith("/migrations")
    assert captured["target"] == "head"


def test_migrate_uses_explicit_root_in_installed_package_layout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    deployment_root = tmp_path / "deployment"
    migrations = deployment_root / "migrations"
    migrations.mkdir(parents=True)
    (deployment_root / "alembic.ini").write_text("[alembic]\n", encoding="utf-8")
    (migrations / "env.py").write_text("", encoding="utf-8")
    fake_installed_module = (
        tmp_path / ".venv/lib/python3.13/site-packages/tradesieve/manage.py"
    )
    captured: dict[str, str] = {}

    def fake_upgrade(config: Config, target: str) -> None:
        captured["config"] = config.config_file_name or ""
        captured["scripts"] = config.get_main_option("script_location") or ""
        captured["target"] = target

    monkeypatch.setattr(manage, "__file__", str(fake_installed_module))
    monkeypatch.setattr("tradesieve.manage.command.upgrade", fake_upgrade)
    manage.migrate(Settings(migration_root=deployment_root))
    assert captured == {
        "config": str(deployment_root / "alembic.ini"),
        "scripts": str(migrations),
        "target": "head",
    }


@pytest.mark.parametrize("missing_path", ["alembic.ini", "migrations/env.py"])
def test_migration_config_refuses_missing_deployment_resources(
    tmp_path: Path, missing_path: str
) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    if missing_path != "alembic.ini":
        (tmp_path / "alembic.ini").write_text("[alembic]\n", encoding="utf-8")
    if missing_path != "migrations/env.py":
        (migrations / "env.py").write_text("", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="missing Alembic"):
        manage.migration_config(Settings(migration_root=tmp_path))


@pytest.mark.parametrize("ready", [False, True])
def test_inspect_runtime_returns_status_and_details(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    ready: bool,
) -> None:
    status = RuntimeStatus(ready, {"database": "OK" if ready else "UNAVAILABLE"})
    monkeypatch.setattr(manage, "check_readiness", lambda settings: status)
    assert manage.inspect_runtime(Settings()) == (0 if ready else 1)
    payload = json.loads(capsys.readouterr().out)
    assert payload["ready"] is ready
    assert payload["expected_migration"] == "20260806_0003"
    assert payload["expected_source_coverage"] == "synthetic-demo-sources-v1"
    assert payload["expected_rule_coverage"] == "synthetic-demo-rules-v1"


@pytest.mark.parametrize("command_name", ["migrate", "bootstrap-demo"])
def test_main_routes_mutating_commands(
    monkeypatch: pytest.MonkeyPatch, command_name: str
) -> None:
    called: list[str] = []
    monkeypatch.setattr(sys, "argv", ["tradesieve.manage", command_name])
    monkeypatch.setattr(manage, "get_settings", Settings)
    monkeypatch.setattr(manage, "migrate", lambda settings: called.append("migrate"))
    monkeypatch.setattr(
        manage, "bootstrap_demo", lambda settings: called.append("bootstrap-demo")
    )
    manage.main()
    assert called == [command_name]


def test_main_propagates_inspect_exit_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["tradesieve.manage", "inspect"])
    monkeypatch.setattr(manage, "get_settings", Settings)
    monkeypatch.setattr(manage, "inspect_runtime", lambda settings: 1)
    with pytest.raises(SystemExit) as exc_info:
        manage.main()
    assert exc_info.value.code == 1


@pytest.mark.parametrize(("ready", "expected_exit"), [(True, 0), (False, 2)])
def test_list_sources_prints_one_safe_envelope_and_distinct_exit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    ready: bool,
    expected_exit: int,
) -> None:
    class FakeListing:
        def __init__(self, listing_ready: bool) -> None:
            self.ready = listing_ready

        def model_dump(self, *, mode: str) -> dict[str, object]:
            assert mode == "json"
            return {
                "deployment_id": "demo",
                "source_set_id": "synthetic-demo-sources-v1",
                "ready": self.ready,
                "status": "CURRENT" if self.ready else "UNAVAILABLE",
                "issues": [],
                "sources": [],
            }

    class FakeService:
        def __init__(self, repository: object) -> None:
            assert repository is not None

        def query_source_set(
            self, deployment_id: str, source_set_id: str
        ) -> FakeListing:
            assert (deployment_id, source_set_id) == (
                "demo",
                "synthetic-demo-sources-v1",
            )
            return FakeListing(ready)

    monkeypatch.setattr(manage, "connect", lambda settings: nullcontext(object()))
    monkeypatch.setattr(manage, "SourceRegistryService", FakeService)
    assert manage.list_sources(Settings()) == expected_exit
    payload = json.loads(capsys.readouterr().out)
    assert payload["ready"] is ready
    assert isinstance(payload["sources"], list)


def test_main_propagates_list_sources_exit_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["tradesieve.manage", "list-sources"])
    monkeypatch.setattr(manage, "get_settings", Settings)
    monkeypatch.setattr(manage, "list_sources", lambda settings: 2)
    with pytest.raises(SystemExit) as exc_info:
        manage.main()
    assert exc_info.value.code == 2


def test_list_rules_prints_safe_active_projection(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeView:
        def model_dump(self, *, mode: str) -> dict[str, object]:
            assert mode == "json"
            return {
                "summary": {
                    "tenant_id": "demo-tenant",
                    "deployment_id": "demo",
                    "rule_set_id": "synthetic-demo-rules-v1",
                    "bundle_id": "synthetic-demo-bundle-v1",
                    "state": "ACTIVE",
                    "active": True,
                },
                "rules": [
                    {
                        "rule_id": "synthetic-rule",
                        "citations": [
                            {
                                "citation_ref": "synthetic-citation",
                                "policy_content_hash": "sha256:" + "a" * 64,
                            }
                        ],
                    }
                ],
            }

    class FakeService:
        def current_active(self, authorized: object) -> FakeView:
            assert authorized == "authorized-read"
            return FakeView()

    monkeypatch.setattr(manage, "connect", lambda settings: nullcontext(object()))
    monkeypatch.setattr(
        manage,
        "build_demo_rule_services",
        lambda settings, connection: ("bundle", FakeService(), "authorization"),
    )
    monkeypatch.setattr(
        manage,
        "authorize_demo_rule_request",
        lambda *args, **kwargs: "authorized-read",
    )
    assert manage.list_rules(Settings()) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"active_bundle", "status"}
    assert payload["status"] == "ACTIVE"
    assert payload["active_bundle"]["summary"]["state"] == "ACTIVE"
    serialized = json.dumps(payload)
    assert "private_policy_text" not in serialized
    assert "internal_notes" not in serialized


@pytest.mark.parametrize(
    ("active", "expected_status"),
    [(None, "NO_ACTIVE_BUNDLE"), ("unavailable", "UNAVAILABLE")],
)
def test_list_rules_returns_safe_unavailable_exit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    active: object,
    expected_status: str,
) -> None:
    class FakeService:
        def current_active(self, authorized: object) -> None:
            assert authorized == "authorized-read"
            if active == "unavailable":
                raise psycopg.OperationalError("private database detail")
            return None

    monkeypatch.setattr(manage, "connect", lambda settings: nullcontext(object()))
    monkeypatch.setattr(
        manage,
        "build_demo_rule_services",
        lambda settings, connection: ("bundle", FakeService(), "authorization"),
    )
    monkeypatch.setattr(
        manage,
        "authorize_demo_rule_request",
        lambda *args, **kwargs: "authorized-read",
    )
    assert manage.list_rules(Settings()) == 2
    assert json.loads(capsys.readouterr().out) == {
        "active_bundle": None,
        "status": expected_status,
    }


def test_list_rules_is_hard_disabled_before_identity_in_production(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = Settings(
        mode="production",
        database_url="postgresql://service:strong-password@db/tradesieve",  # pragma: allowlist secret
        demo_bootstrap_enabled=False,
        rule_bundle_tenant_id="tenant-1",
        deployment_id="production-1",
        required_source_set="approved-sources-v1",
        required_rule_set="approved-rules-v1",
    )
    monkeypatch.setattr(
        manage,
        "build_demo_rule_services",
        lambda settings, connection: pytest.fail("identity path must not run"),
    )
    assert manage.list_rules(settings) == 3
    assert json.loads(capsys.readouterr().out) == {
        "active_bundle": None,
        "status": "DISABLED",
    }


def test_main_propagates_list_rules_exit_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["tradesieve.manage", "list-rules"])
    monkeypatch.setattr(manage, "get_settings", Settings)
    monkeypatch.setattr(manage, "list_rules", lambda settings: 3)
    with pytest.raises(SystemExit) as exc_info:
        manage.main()
    assert exc_info.value.code == 3
