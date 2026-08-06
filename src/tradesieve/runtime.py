"""Database-backed runtime health and bootstrap operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from psycopg import Connection

from tradesieve.config import Settings

MIGRATION_REVISION = "20260806_0001"


@dataclass(frozen=True, slots=True)
class RuntimeStatus:
    ready: bool
    checks: dict[str, str]

    def public_checks(self) -> dict[str, str]:
        """Return operational states without coverage IDs or migration versions."""
        public_states = {"OK", "UNAVAILABLE", "NOT_APPLIED"}
        return {
            name: value if value in public_states else "UNAVAILABLE"
            for name, value in self.checks.items()
        }


def connect(settings: Settings) -> Connection[Any]:
    return psycopg.connect(
        settings.database_url,
        connect_timeout=settings.database_connect_timeout_seconds,
        autocommit=True,
    )


def check_readiness(settings: Settings) -> RuntimeStatus:
    checks = {
        "database": "UNAVAILABLE",
        "migration": "NOT_APPLIED",
        "required_source_coverage": "UNAVAILABLE",
        "required_rule_coverage": "UNAVAILABLE",
    }
    try:
        connection = connect(settings)
    except psycopg.Error:
        return RuntimeStatus(ready=False, checks=checks)

    with connection:
        checks["database"] = "OK"
        try:
            revision = connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()
        except psycopg.Error:
            return RuntimeStatus(ready=False, checks=checks)
        if not revision or revision[0] != MIGRATION_REVISION:
            return RuntimeStatus(ready=False, checks=checks)
        checks["migration"] = "OK"

        try:
            rows = connection.execute(
                "SELECT coverage_kind, coverage_id FROM runtime_coverage "
                "WHERE active IS TRUE"
            ).fetchall()
        except psycopg.Error:
            return RuntimeStatus(ready=False, checks=checks)

    active = {(str(kind), str(identifier)) for kind, identifier in rows}
    if ("SOURCE", settings.required_source_set) in active:
        checks["required_source_coverage"] = "OK"
    if ("RULE", settings.required_rule_set) in active:
        checks["required_rule_coverage"] = "OK"
    ready = all(value == "OK" for value in checks.values())
    return RuntimeStatus(ready=ready, checks=checks)


def bootstrap_demo(settings: Settings) -> None:
    if settings.mode != "demo" or not settings.demo_bootstrap_enabled:
        raise RuntimeError("demo bootstrap is disabled outside explicit demo mode")
    with connect(settings) as connection:
        for kind, identifier in (
            ("SOURCE", settings.required_source_set),
            ("RULE", settings.required_rule_set),
        ):
            connection.execute(
                "INSERT INTO runtime_coverage (coverage_kind, coverage_id, active) "
                "VALUES (%s, %s, TRUE) "
                "ON CONFLICT (coverage_kind, coverage_id) DO UPDATE "
                "SET active = EXCLUDED.active, updated_at = CURRENT_TIMESTAMP",
                (kind, identifier),
            )


def record_worker_heartbeat(settings: Settings) -> None:
    with connect(settings) as connection:
        connection.execute(
            "INSERT INTO runtime_component_heartbeat (component, observed_at) "
            "VALUES ('worker', CURRENT_TIMESTAMP) "
            "ON CONFLICT (component) DO UPDATE "
            "SET observed_at = EXCLUDED.observed_at"
        )


def worker_is_fresh(settings: Settings) -> bool:
    if not check_readiness(settings).ready:
        return False
    try:
        with connect(settings) as connection:
            row = connection.execute(
                "SELECT observed_at FROM runtime_component_heartbeat "
                "WHERE component = 'worker'"
            ).fetchone()
    except psycopg.Error:
        return False
    if not row:
        return False
    observed_at = row[0]
    if not isinstance(observed_at, datetime):
        return False
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    return observed_at >= datetime.now(UTC) - timedelta(
        seconds=settings.worker_stale_after_seconds
    )
