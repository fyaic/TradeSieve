"""Safe runtime management commands for the reference deployment."""

from __future__ import annotations

import argparse
import json

from alembic import command
from alembic.config import Config

from tradesieve.adapters.postgres_source_registry import PostgresSourceRegistry
from tradesieve.application.source_registry import SourceRegistryService
from tradesieve.config import Settings, get_settings
from tradesieve.runtime import (
    MIGRATION_REVISION,
    bootstrap_demo,
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("migrate", "bootstrap-demo", "inspect", "list-sources")
    )
    args = parser.parse_args()
    settings = get_settings()
    if args.command == "migrate":
        migrate(settings)
    elif args.command == "bootstrap-demo":
        bootstrap_demo(settings)
    elif args.command == "inspect":
        raise SystemExit(inspect_runtime(settings))
    else:
        raise SystemExit(list_sources(settings))


if __name__ == "__main__":  # pragma: no cover
    main()
