"""Central runtime configuration and safety validation."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEMO_PASSWORD_MARKER = "local_demo_only"  # pragma: allowlist secret
DEMO_COVERAGE_PREFIXES = ("demo", "synthetic")
Port = Annotated[int, Field(ge=1, le=65535)]
PositiveSeconds = Annotated[int, Field(gt=0)]
RuntimeId = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]


class Settings(BaseSettings):
    """Environment-backed settings with fail-closed non-demo validation."""

    model_config = SettingsConfigDict(
        env_prefix="TRADESIEVE_",
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
    )

    mode: Literal["demo", "production"] = "demo"
    database_url: str = "postgresql://tradesieve:local_demo_only@postgres:5432/tradesieve"  # pragma: allowlist secret
    debug: bool = False
    demo_bootstrap_enabled: bool = True
    rule_bundle_tenant_id: RuntimeId = "demo-tenant"
    deployment_id: RuntimeId = "demo"
    required_source_set: RuntimeId = "synthetic-demo-sources-v1"
    required_rule_set: RuntimeId = "synthetic-demo-rules-v1"
    migration_root: Path = Path(".")
    http_host: str = "0.0.0.0"
    http_port: Port = 8080
    database_connect_timeout_seconds: PositiveSeconds = 2
    worker_heartbeat_seconds: PositiveSeconds = 2
    worker_stale_after_seconds: PositiveSeconds = 10

    @model_validator(mode="after")
    def reject_unsafe_non_demo_configuration(self) -> Self:
        if self.worker_stale_after_seconds <= self.worker_heartbeat_seconds:
            raise ValueError(
                "worker stale threshold must exceed the heartbeat interval"
            )
        if self.mode == "demo":
            return self

        unsafe: list[str] = []
        if DEMO_PASSWORD_MARKER in self.database_url:
            unsafe.append("default demo database credential")
        if self.debug:
            unsafe.append("debug mode")
        if self.demo_bootstrap_enabled:
            unsafe.append("demo bootstrap")
        if self.rule_bundle_tenant_id.lower().startswith(DEMO_COVERAGE_PREFIXES):
            unsafe.append("demo/synthetic rule-bundle tenant identity")
        if self.deployment_id.lower().startswith(DEMO_COVERAGE_PREFIXES):
            unsafe.append("demo deployment identity")
        if self.required_source_set.lower().startswith(DEMO_COVERAGE_PREFIXES):
            unsafe.append("synthetic/demo required source coverage")
        if self.required_rule_set.lower().startswith(DEMO_COVERAGE_PREFIXES):
            unsafe.append("synthetic/demo required rule coverage")
        if unsafe:
            raise ValueError(
                "non-demo mode refuses unsafe settings: " + ", ".join(unsafe)
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
