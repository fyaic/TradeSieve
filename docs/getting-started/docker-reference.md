# Docker reference deployment

**Status:** TS-201 source-registry, TS-202 immutable source snapshots, TS-205 rule-bundle governance, the TS-302 synthetic screening-receipt lifecycle, and the TS-505 demo CRM seam. This stack proves packaging, PostgreSQL migration, private immutable raw bytes, governed synthetic source/rule readiness, authorized idempotent intake, a fixed browser demonstration, independent app/worker processes, persistence, and fail-closed health behavior. Deterministic live screening decisions, review workflows, public screening REST/CLI, MCP, and production controls are not implemented yet.

## Demo-only configuration

Copy the complete environment template:

```bash
cp .env.example .env
```

Every value in `.env.example` is documented inline. The database credential and synthetic source/rule coverage IDs are intentionally weak demo defaults. Central configuration validation refuses those values, debug mode, or demo bootstrap whenever `TRADESIEVE_MODE=production`.

`TRADESIEVE_MIGRATION_ROOT` explicitly names the directory containing `alembic.ini` and `migrations/`. The image sets it to `/app`; startup validates both paths before invoking Alembic, so installed package paths are never mistaken for deployment resources.

`TRADESIEVE_RAW_OBJECT_ROOT` is the pre-created private `/var/lib/tradesieve/raw`
directory. Bootstrap mounts its named volume read/write; app and worker mount the same
volume read-only; migration mounts no raw volume. The image owns the `0700` root as UID
`10001`, and immutable objects are verified `0600` files.

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

The demo-only synthetic logistics CRM is available at:

```text
http://127.0.0.1:8080/demo/crm
```

It accepts only repository-owned fixture IDs and uses precomputed canonical results. See [the demo CRM guide](demo-crm.md) before evaluating it; it is not a live sanctions or export-control engine.

`docker compose up` starts PostgreSQL, runs the real Alembic migrations, loads the governed synthetic registration, creates and verifies two immutable source snapshots through retrieve/parse/validate/approve/activate, and uses authorized application services to activate one immutable synthetic rule bundle. It then starts the non-root read-only app and worker containers. The app, worker, and PostgreSQL services each have a healthcheck. No `runtime_coverage` source/rule marker is written or trusted.

The application image uses the Docker Official Image for Python 3.13.14 through Google's Docker Hub pull-through cache and copies the separately pinned `uv` 0.12.1 binary. The acceptance script verifies both versions, UID `10001`, and read-only `/app` behavior inside the final image.

The reference database is the Docker Official Image for PostgreSQL 18.4, accessed through Google's Docker Hub pull-through cache and pinned to the same official multi-platform digest. The acceptance script verifies the server and `psql` minor versions. PostgreSQL 18 stores its versioned data below `/var/lib/postgresql`; the named volume mounts that parent path so data survives container recreation.

PostgreSQL, migrations, bootstrap, worker, and the app's database path stay on an internal network. Only the app also joins a narrow edge bridge so Docker can publish its health endpoint to `127.0.0.1`; no PostgreSQL or worker port is published.

To run the one-shot steps independently:

```bash
docker compose up -d --wait postgres
docker compose run --rm migrate
docker compose run --rm bootstrap-demo
docker compose up -d --wait app worker
```

Inspect the private operations-safe registry projection:

```bash
docker compose run --rm --no-deps app \
  python -m tradesieve.manage list-sources
```

The command emits one deterministic JSON envelope. It exits `0` when all required
manifest members are current and `2` when the set is unhealthy. Credential
references and private contractual constraints are deliberately absent. This
operations view is CLI-only until an authenticated administration adapter exists.

Inspect the authorized operations-safe snapshot listing in demo mode:

```bash
docker compose run --rm --no-deps app \
  python -m tradesieve.manage list-source-snapshots
```

The command returns two deterministic metadata summaries when verified source evidence
is ready and an exact empty envelope with exit `2` when it is unavailable or corrupt.
It exposes identifiers, hashes, finite parser/schema metadata, counts, times, lifecycle
state, and validation/diff summaries only. It has no raw-byte, record/assertion,
registration-admin, actor-identity, reason, audit-ID, REST, or MCP surface.

Inspect the authorized active-rule projection in demo mode:

```bash
docker compose run --rm --no-deps app \
  python -m tradesieve.manage list-rules
```

The command exits `0` with one `ACTIVE` bundle or `2` with a generic unavailable/no-active envelope. It exposes stable bundle/rule identities, hashes, effective windows, finite evaluator kinds, fixture counts, and citation identities. Private policy text and internal notes are omitted. The command is deliberately disabled with exit `3` outside explicit demo mode; it is not a production identity adapter or screening interface.

Exercise the authorized idempotent receipt lifecycle with the built-in synthetic transaction:

```bash
docker compose run --rm --no-deps app \
  tradesieve-manage submit-demo-screening \
  --idempotency-key synthetic-order-001 \
  --fixture baseline
```

The first call exits `0` with `APPLIED`; the exact retry exits `0` with `REPLAY` and the same opaque intake, screening and outbox references. The finite `changed` fixture under the same key exits `4` with `IDEMPOTENCY_CONFLICT` and no receipt:

```bash
docker compose run --rm --no-deps app \
  tradesieve-manage submit-demo-screening \
  --idempotency-key synthetic-order-001 \
  --fixture changed
```

The command accepts no request file, stdin body or production identity. It is disabled with exit `3` outside explicit demo mode and maps dependency/integrity failures to a generic exit `2` envelope. Output contains only finite status/disposition and the safe receipt; it omits the request, idempotency key/digest, hashes, actor, correlation, audit details and nested errors. This operations demo proves authorized durable intake only. It performs no rule evaluation and creates no finding, result, case or clearance.

## Health semantics

- `/health/live` checks only that the HTTP process can respond. It does not query PostgreSQL or infer source coverage.
- `/health/ready` returns `503` unless PostgreSQL is reachable, the expected migration revision is applied, every member of the configured source-set manifest is active/current with verified active snapshot metadata and private raw bytes, and the configured tenant/rule-set has one validated, governed, effective active bundle whose persisted content hash and lifecycle projection agree.
- Official-source citations fail readiness unless an explicit snapshot/provision resolver verifies them with the exact boolean `True`. The current synthetic demo uses internal-policy citations and makes no official-source or legal-coverage claim.
- A required source is stale at `age >= stale_after`; missing runtime facts, explicit failure, or future observations are unavailable. Inactive or explicitly quarantined sources remain distinct internally.
- Internal operations can see sorted source IDs and exact states. The public health payload maps stale/quarantined states to generic `UNAVAILABLE` and never emits source IDs.
- The worker is healthy only when readiness passes and its database heartbeat is recent.
- HTTP success is operational health only; it is never screening clearance.

The app can therefore remain live while readiness fails closed during a database outage or unhealthy required source.

## Clean-environment verification and teardown

The automated acceptance script uses separate fresh and legacy/main projects. It proves
private-volume ownership/read-only boundaries, pristine and exact legacy upgrades,
safe projections, idempotent bootstrap, database/raw identity across recreation, two
application rollbacks, append-only/audit constraints, missing and same-length-tampered
raw failures, lifecycle/observation corruption refusal and controlled repair, a database
outage, an APPLIED/REPLAY/CONFLICT screening receipt lifecycle with stable references
across container recreation, the packaged CRM page plus one canonical red-light demo response,
and zero container/network/volume residue:

```bash
./scripts/test_compose.sh
```

The independent PostgreSQL 18.4 repository/concurrency/constraint gate remains a
separate acceptance path:

```bash
./scripts/test_source_snapshot_postgres.sh
./scripts/test_screening_submission_postgres.sh
```

Manual teardown:

```bash
docker compose down --volumes --remove-orphans
```

The named volume contains synthetic demo state only and is deleted by this command. Do not place production/pilot evidence in the reference stack.
