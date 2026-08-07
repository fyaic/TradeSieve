"""Safe runtime management commands for the reference deployment."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime

import psycopg
from alembic import command
from alembic.config import Config

from tradesieve.adapters.postgres_source_registry import PostgresSourceRegistry
from tradesieve.application.auth import (
    AuthorizationAuditFailure,
    AuthorizationDenied,
    AuthorizationUnavailable,
    Operation,
)
from tradesieve.application.rule_bundle import RuleBundleUnavailable
from tradesieve.application.source_registry import SourceRegistryService
from tradesieve.application.source_snapshot_contracts import SourceSnapshotListing
from tradesieve.config import Settings, get_settings
from tradesieve.demo_rule_bundle import DemoRuleActor, authorize_demo_rule_request
from tradesieve.demo_source_snapshot import (
    DEMO_SOURCE_ID,
    DemoSourceActor,
    authorize_demo_source_request,
)
from tradesieve.runtime import (
    MIGRATION_REVISION,
    bootstrap_demo,
    build_demo_rule_services,
    build_demo_source_services,
    build_source_snapshot_readiness_service,
    check_readiness,
    connect,
)


def sqlalchemy_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url


def alembic_database_url(database_url: str) -> str:
    """Select psycopg and escape ConfigParser interpolation markers."""
    return sqlalchemy_database_url(database_url).replace("%", "%%")


def migration_config(settings: Settings) -> Config:
    """Build an Alembic config from an explicit, validated deployment root."""
    migration_root = settings.migration_root.resolve()
    config_path = migration_root / "alembic.ini"
    script_location = migration_root / "migrations"
    if not config_path.is_file():
        raise FileNotFoundError(f"missing Alembic config: {config_path}")
    if not (script_location / "env.py").is_file():
        raise FileNotFoundError(f"missing Alembic migration scripts: {script_location}")

    config = Config(config_path)
    config.set_main_option("script_location", str(script_location))
    config.set_main_option(
        "sqlalchemy.url", alembic_database_url(settings.database_url)
    )
    return config


def migrate(settings: Settings) -> None:
    config = migration_config(settings)
    command.upgrade(config, "head")


def inspect_runtime(settings: Settings) -> int:
    status = check_readiness(settings)
    print(
        json.dumps(
            {
                "ready": status.ready,
                "expected_migration": MIGRATION_REVISION,
                "expected_source_coverage": settings.required_source_set,
                "expected_rule_coverage": settings.required_rule_set,
                "checks": status.checks,
            },
            sort_keys=True,
        )
    )
    return 0 if status.ready else 1


def list_sources(settings: Settings) -> int:
    """Print the private operations-safe source registry projection as JSON."""

    with connect(settings) as connection:
        listing = SourceRegistryService(
            PostgresSourceRegistry(connection)
        ).query_source_set(settings.deployment_id, settings.required_source_set)
    print(
        json.dumps(
            listing.model_dump(mode="json"),
            sort_keys=True,
        )
    )
    return 0 if listing.ready else 2


def list_rules(settings: Settings) -> int:
    """Print the authorized safe active-rule projection in explicit demo mode."""

    if settings.mode != "demo":
        print(json.dumps({"active_bundle": None, "status": "DISABLED"}, sort_keys=True))
        return 3
    try:
        with connect(settings) as connection:
            bundle, service, authorization = build_demo_rule_services(
                settings, connection
            )
            authorized = authorize_demo_rule_request(
                settings,
                authorization,
                bundle,
                actor=DemoRuleActor.OPERATOR,
                operation=Operation.POLICY_READ,
                now=datetime.now(UTC),
            )
            active = service.current_active(authorized)
    except (
        psycopg.Error,
        AuthorizationAuditFailure,
        AuthorizationDenied,
        AuthorizationUnavailable,
        RuleBundleUnavailable,
    ):
        print(
            json.dumps({"active_bundle": None, "status": "UNAVAILABLE"}, sort_keys=True)
        )
        return 2
    if active is None:
        print(
            json.dumps(
                {"active_bundle": None, "status": "NO_ACTIVE_BUNDLE"},
                sort_keys=True,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "active_bundle": active.model_dump(mode="json"),
                "status": "ACTIVE",
            },
            sort_keys=True,
        )
    )
    return 0


def list_source_snapshots(settings: Settings) -> int:
    """Print one authorized canonical snapshot listing in explicit demo mode."""

    empty = SourceSnapshotListing(snapshots=[]).model_dump_json()
    if settings.mode != "demo":
        print(empty)
        return 3
    listing: SourceSnapshotListing | None = None
    payload = empty
    try:
        with connect(settings) as connection:
            if build_source_snapshot_readiness_service(settings, connection).is_ready():
                graph = build_demo_source_services(settings, connection)
                authorized = authorize_demo_source_request(
                    settings,
                    graph.authorization,
                    actor=DemoSourceActor.READER,
                    operation=Operation.SOURCE_READ,
                    now=datetime.now(UTC),
                )
                listing = graph.query.list_snapshots(
                    authorized,
                    source_id=DEMO_SOURCE_ID,
                )
                payload = listing.model_dump_json()
    except Exception:
        print(empty)
        return 2
    if listing is None:
        print(empty)
        return 2
    print(payload)
    return 0 if listing.snapshots else 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "migrate",
            "bootstrap-demo",
            "inspect",
            "list-sources",
            "list-source-snapshots",
            "list-rules",
        ),
    )
    args = parser.parse_args()
    settings = get_settings()
    if args.command == "migrate":
        migrate(settings)
    elif args.command == "bootstrap-demo":
        bootstrap_demo(settings)
    elif args.command == "inspect":
        raise SystemExit(inspect_runtime(settings))
    elif args.command == "list-sources":
        raise SystemExit(list_sources(settings))
    elif args.command == "list-source-snapshots":
        raise SystemExit(list_source_snapshots(settings))
    else:
        raise SystemExit(list_rules(settings))


if __name__ == "__main__":  # pragma: no cover
    main()
