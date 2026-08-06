# Docker reference deployment

**Status:** TS-102 runnable health/readiness foundation. This stack proves packaging, PostgreSQL migration, required synthetic coverage, independent app/worker processes, and fail-closed health behavior. Screening, review, CLI, MCP, and production controls are not implemented yet.

## Demo-only configuration

Copy the complete environment template:

```bash
cp .env.example .env
```

Every value in `.env.example` is documented inline. The database credential and synthetic source/rule coverage IDs are intentionally weak demo defaults. Central configuration validation refuses those values, debug mode, or demo bootstrap whenever `TRADESIEVE_MODE=production`.

`TRADESIEVE_MIGRATION_ROOT` explicitly names the directory containing `alembic.ini` and `migrations/`. The image sets it to `/app`; startup validates both paths before invoking Alembic, so installed package paths are never mistaken for deployment resources.

The reference Compose stack is explicitly demo-only because it includes the `bootstrap-demo` one-shot service. It must not be reused as a production deployment file.

## Build, start, migrate, bootstrap, and inspect

```bash
docker compose build
docker compose up -d --wait
docker compose ps
docker compose run --rm --no-deps app python -m tradesieve.manage inspect
curl --fail http://127.0.0.1:8080/health/live
curl --fail http://127.0.0.1:8080/health/ready
```

`docker compose up` starts PostgreSQL, runs the real Alembic migration, explicitly loads the two named synthetic demo coverage records, and then starts the non-root read-only app and worker containers. The app, worker, and PostgreSQL services each have a healthcheck.

The application image uses the Docker Official Image for Python 3.13.14 through Google's Docker Hub pull-through cache and copies the separately pinned `uv` 0.12.1 binary. The acceptance script verifies both versions, UID `10001`, and read-only `/app` behavior inside the final image.

The reference database is the Docker Official Image for PostgreSQL 18.4, accessed through Google's Docker Hub pull-through cache and pinned to the same official multi-platform digest. The acceptance script verifies the server and `psql` minor versions. PostgreSQL 18 stores its versioned data below `/var/lib/postgresql`; the named volume mounts that parent path so data survives container recreation.

To run the one-shot steps independently:

```bash
docker compose up -d --wait postgres
docker compose run --rm migrate
docker compose run --rm bootstrap-demo
docker compose up -d --wait app worker
```

## Health semantics

- `/health/live` checks only that the HTTP process can respond. It does not query PostgreSQL or infer source coverage.
- `/health/ready` returns `503` unless PostgreSQL is reachable, the expected migration revision is applied, and both configured required source/rule coverage IDs are active.
- The worker is healthy only when readiness passes and its database heartbeat is recent.
- HTTP success is operational health only; it is never screening clearance.

The app can therefore remain live while readiness fails closed during a database outage or missing required coverage.

## Clean-environment verification and teardown

The automated acceptance script uses a unique Compose project, proves pre-migration and unsafe-production failures, starts all services, tests source-coverage/database fail-closed behavior, and removes volumes/containers:

```bash
./scripts/test_compose.sh
```

Manual teardown:

```bash
docker compose down --volumes --remove-orphans
```

The named volume contains synthetic demo state only and is deleted by this command. Do not place production/pilot evidence in the reference stack.
