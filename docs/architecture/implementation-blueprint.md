# Implementation blueprint

**Status:** Proposed; validate through Phase 1 spikes and ADR review.

## Recommended Phase 1 stack

| Concern | Proposed choice | Why for this MVP | Revisit trigger |
| --- | --- | --- | --- |
| Language/runtime | Python 3.13+ | Strong sanctions/data/matching ecosystem; one language for API, workers, CLI, MCP, fixtures | Measured performance/security/operations constraint |
| Package/tooling | `uv`, `pyproject.toml`, locked dependencies | Fast reproducible environments and single project workspace | Organization standard requires another tool |
| HTTP/schema | FastAPI + Pydantic v2 | Typed validation, OpenAPI/JSON Schema, ASGI, easy contract-first tests | Contract-first generator or organization framework wins a spike |
| Persistence | PostgreSQL 18-compatible schema + SQLAlchemy/Alembic | Transactions, JSON, RLS, trigram baseline, mature backup/migration | Search/graph/throughput evidence requires a specialized store |
| Matching baseline | Deterministic normalization + identifiers + `pg_trgm` adapter | Explainable walking baseline with no extra service | Benchmark selects yente/Watchman/Splink or dedicated search |
| Jobs/outbox | PostgreSQL job/outbox tables and worker command | Preserves atomicity and avoids broker in Phase 1 | Sustained throughput/latency or independent scaling requires broker |
| Object storage | Adapter: local volume in demo, S3-compatible in deployment | Immutable large objects without database bloat | Organization storage/security standard |
| CLI | Typer or Click client over REST | Typed, composable Python package; same schemas/client | Generated SDK/client proves better |
| MCP | Official MCP Python SDK, Streamable HTTP; optional stdio launcher | Same runtime/models; official protocol/auth path | SDK maturity or client compatibility evidence |
| Reviewer UI | Server-rendered FastAPI/Jinja/HTMX-style minimal console | Small surface for human workflow without separate SPA release | Usability/workflow complexity justifies frontend application |
| Observability | OpenTelemetry + structured logging/metrics | Vendor-neutral trace correlation; deployer chooses backend | Organization platform standard |
| Packaging | OCI image + Docker Compose reference | “Clone and run” MVP and private deployment path | Kubernetes/managed platform becomes an explicit target |

FastAPI is built around OpenAPI and JSON Schema and generates interactive documentation; the official MCP Python SDK supports server/client development and Streamable HTTP. PostgreSQL provides row-level security and `pg_trgm` for a candidate-retrieval baseline. These are implementation-enabling capabilities, not proof that the stack meets Phase 1 acceptance; spikes and benchmarks must verify it.

Primary references:

- [FastAPI features](https://fastapi.tiangolo.com/features/)
- [Official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [PostgreSQL row security](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)
- [PostgreSQL pg_trgm](https://www.postgresql.org/docs/current/pgtrgm.html)
- [OpenTelemetry Python](https://opentelemetry.io/docs/languages/python/instrumentation/)

## Planned repository layout

```text
TradeSieve/
├── api/
│   └── openapi/                 # versioned public HTTP contract
├── docs/
│   ├── requirements/            # source request and problem scope
│   ├── product/                 # users, requirements, MVP
│   ├── architecture/            # domain/runtime/integration/security design
│   ├── decisions/               # immutable ADRs
│   ├── delivery/                # agile process, phase plan, backlog
│   ├── getting-started/         # target and released user journeys
│   ├── research/                # evidence synthesis
│   └── roadmap/                 # longer-horizon discovery roadmap
├── examples/
│   ├── requests/                # synthetic canonical requests
│   ├── responses/               # expected safe outputs/events
│   └── integrations/            # future CRM/webhook samples
├── research/                    # machine-readable source registry
├── scripts/                     # repository/contract checks
├── src/tradesieve/              # created with implementation story
│   ├── domain/
│   ├── application/
│   ├── ports/
│   └── adapters/
│       ├── http/
│       ├── persistence/
│       ├── sources/
│       ├── matching/
│       ├── mcp/
│       ├── cli/
│       └── reviewer_ui/
├── tests/
│   ├── contract/
│   ├── unit/
│   ├── integration/
│   ├── e2e/
│   ├── security/
│   └── fixtures/
└── deploy/                      # Docker/Compose and later deployment overlays
```

Implementation directories are created only when their first story lands; empty scaffolding is not treated as progress.

## Development sequence

### Walking skeleton

1. Establish package, formatting/type/test/contract CI, container, configuration, health/readiness, migration, and one authenticated no-op endpoint.
2. Add canonical schemas/OpenAPI examples and generated/handwritten client contract tests.
3. Add PostgreSQL unit of work, tenant/actor context, idempotency, audit event, outbox, and safe observability.

### Vertical domain slice

1. Persist input/screening/case/finding records.
2. Implement deterministic completeness and exact-identifier controls on synthetic fixtures.
3. Add matcher port plus explainable baseline; benchmark before threshold activation.
4. Add evidence submission, finding resolution, human decision/expiry/invalidation state machine.
5. Add worker for source lifecycle, webhook delivery, and rescreen job.

### Interface and user slice

1. REST create/read endpoints and interactive docs.
2. CLI client with human/JSON parity tests.
3. MCP screen/read/explain/request-review tools and negative authorization tests.
4. Minimal reviewer queue/case/decision pages.
5. Sample CRM gate/webhook consumer and complete golden-path script.

## Test strategy

| Layer | Focus |
| --- | --- |
| Domain unit | State transitions, priorities/actions, expiry, version selection, forbidden clearance paths |
| Contract | OpenAPI/schema examples, backward compatibility, API/CLI/MCP parity, stable errors/events |
| Connector | Recorded/synthetic official-source shapes, hashes, diffs, unexpected deletion/schema failures |
| Matching | Multilingual positive/negative/ambiguous fixtures, recall/precision/calibration/review load |
| Persistence | Migrations, transactions, RLS/tenant boundaries, idempotency, outbox/job leasing, append-only controls |
| Integration | Auth, API, worker, object store, webhook signature/retry/deduplication |
| Security | Authorization matrix, prompt injection, upload/archive, SSRF/path, secrets/log redaction |
| E2E | Docker clean start, golden case, human decision, source delta, replay, backup/restore |

## Quality gates

- Formatting, lint, type check, unit and contract tests on every PR.
- Migration up/down or forward/restore test for schema changes.
- Architecture dependency test prevents domain/application importing adapters.
- Source/rule/matcher changes include fixtures and evaluation evidence.
- Generated OpenAPI is compared with the reviewed contract or produced from one canonical source—never maintained inconsistently in two places.
- Release candidate runs the golden path from a clean clone and produces a known-limitations report.

## Alternatives considered

### Go service plus separate Python matching/MCP

Potentially stronger single-binary operations and static performance, but immediately creates a cross-language/distributed boundary before matching or load evidence demands it.

### TypeScript end to end

Strong API/MCP ecosystem and reviewer frontend alignment, but weaker direct fit with the planned data-quality/entity-resolution experiments. It remains viable if the implementation team is primarily TypeScript and benchmark components are external services.

### Microservices from day one

Rejected for Phase 1 because source, screening, case, decision, outbox, and audit consistency would become a distributed transaction problem before domain boundaries and load are proven.

### Adopt a commercial/full open-source screening product as the product

Commercial and open-source components may provide data/matching/workflow capabilities, but none should be assumed to satisfy the exact source governance, goods/route/payment, human-only decision, replay, and multi-interface contract without a fit/licence/exit evaluation.
