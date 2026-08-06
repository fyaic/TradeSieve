"""Runtime management command tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

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
    assert payload["expected_migration"] == "20260806_0001"
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
