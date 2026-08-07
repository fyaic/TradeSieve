"""Configuration safety tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from tradesieve.config import Settings


def production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "mode": "production",
        "database_url": "postgresql://service:strong-password@db/tradesieve",  # pragma: allowlist secret
        "debug": False,
        "demo_bootstrap_enabled": False,
        "rule_bundle_tenant_id": "tenant-eu-1",
        "deployment_id": "production-eu-1",
        "required_source_set": "approved-sources-v1",
        "required_rule_set": "approved-rules-v1",
    }
    values.update(overrides)
    return Settings.model_validate(values)


def test_demo_configuration_is_explicit() -> None:
    settings = Settings()
    assert settings.mode == "demo"
    assert settings.demo_bootstrap_enabled is True
    assert settings.rule_bundle_tenant_id == "demo-tenant"
    assert settings.required_source_set.startswith("synthetic")
    assert settings.raw_object_root == Path("/var/lib/tradesieve/raw")


@pytest.mark.parametrize(
    "root",
    [Path("."), Path("relative/raw"), Path("/"), Path("/private/../raw")],
)
def test_raw_object_root_requires_an_absolute_private_path(root: Path) -> None:
    with pytest.raises(ValidationError, match="absolute private path"):
        Settings(raw_object_root=root)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("http_port", 0),
        ("http_port", 65536),
        ("database_connect_timeout_seconds", 0),
        ("worker_heartbeat_seconds", 0),
        ("worker_stale_after_seconds", 0),
    ],
)
def test_runtime_numeric_settings_reject_unsafe_ranges(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({field: value})


@pytest.mark.parametrize("stale_after", [1, 2])
def test_worker_stale_threshold_must_exceed_heartbeat(stale_after: int) -> None:
    with pytest.raises(ValidationError, match="must exceed"):
        Settings(worker_heartbeat_seconds=2, worker_stale_after_seconds=stale_after)


@pytest.mark.parametrize(
    "unsafe_override",
    [
        {
            "database_url": "postgresql://service:local_demo_only@db/tradesieve"
        },  # pragma: allowlist secret
        {"debug": True},
        {"demo_bootstrap_enabled": True},
        {"rule_bundle_tenant_id": "synthetic-tenant"},
        {"deployment_id": "demo-eu-1"},
        {"required_source_set": "synthetic-source"},
        {"required_rule_set": "demo-rule"},
    ],
)
def test_production_rejects_each_unsafe_demo_setting(
    unsafe_override: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="non-demo mode refuses"):
        production_settings(**unsafe_override)


def test_safe_production_configuration_validates() -> None:
    assert production_settings().mode == "production"


@pytest.mark.parametrize(
    "field",
    [
        "rule_bundle_tenant_id",
        "deployment_id",
        "required_source_set",
        "required_rule_set",
    ],
)
def test_runtime_registry_identifiers_are_bounded_and_safe(field: str) -> None:
    for value in ["", "bad/id", "x" * 129]:
        with pytest.raises(ValidationError):
            Settings.model_validate({field: value})


def test_validation_errors_hide_database_credentials() -> None:
    secret_url = (
        "postgresql://service:supersecret@db/tradesieve"  # pragma: allowlist secret
    )
    with pytest.raises(ValidationError) as exc_info:
        production_settings(database_url=secret_url, debug=True)
    assert "supersecret" not in str(exc_info.value)
