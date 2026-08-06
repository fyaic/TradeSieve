# TradeSieve

> Independent, agent-ready trade-compliance decision-support service for sanctions, export-control, and transaction-risk triage.

[![Status: Phase 1 planned](https://img.shields.io/badge/status-Phase%201%20planned-6f42c1)](#project-status)
[![Interfaces: API CLI MCP](https://img.shields.io/badge/interfaces-API%20%7C%20CLI%20%7C%20MCP-0969da)](docs/architecture/agent-interface-principles.md)
[![Decision model: Human cleared](https://img.shields.io/badge/decision-human--cleared-b7791f)](docs/decisions/0002-human-clearance-only.md)

TradeSieve is designed as an independently deployable service beside CRM, order, booking, shipment, and payment systems. It normalizes approved sanctions/export-control evidence, screens proposed business actions, preserves source/rule provenance, and routes uncertainty to authorized human review.

## Project status

**Phase 1 implementation has started; no runnable screening MVP exists yet.** The repository includes a locked Python project, CI quality gates, and a Docker reference stack that proves app/worker/PostgreSQL health and fail-closed readiness. Screening and review behavior remain acceptance targets. The [target MVP user experience](docs/getting-started/mvp-user-experience.md) is not a working screening quickstart until implementation and release gates pass.

Nothing in this repository is legal advice or an automatic legal-clearance mechanism.

## Phase 1 outcome

Version `0.1.0` should let an evaluator:

1. start a private demo with Docker Compose;
2. submit the same synthetic transaction through REST, CLI, and MCP;
3. receive structured findings, missing evidence, versions, and a `HOLD`/review action;
4. complete the authorized human review path;
5. connect a sample CRM/OMS synchronous gate and signed webhook;
6. demonstrate source-change rescreening, replay, and a redacted evidence/audit pack.

See [Phase 1 service MVP](docs/product/mvp-scope.md), [delivery plan](docs/delivery/phase-1-plan.md), and [backlog decomposition](docs/delivery/phase-1-backlog.md).

## Product principles

- **Focused service boundary:** business systems remain systems of record; TradeSieve owns screening evidence, cases, decisions, and provenance.
- **One contract, multiple interfaces:** REST/OpenAPI is canonical; CLI and MCP are typed adapters over the same application service.
- **Evidence before score:** results identify source/rule/input/model versions, facts, uncertainty, and required action.
- **Human clearance only:** automation may hold or escalate; only an authorized reviewer can issue a scoped, expiring release.
- **As-of-time readiness:** raw sources, deltas, hashes, and decisions are immutable and replayable; complete historic reconstruction is a later coverage question.
- **AI as an assistant:** models may extract, normalize, compare, and draft; deterministic controls and human review own disposition.
- **Private by default:** production customer/transaction data does not leave the approved deployment boundary by default.

## Intended interfaces

| Interface | Primary users | Intended use |
| --- | --- | --- |
| REST/OpenAPI | CRM, OMS, booking, payment systems | Idempotent pre-action screening and case operations |
| Events/webhooks | Integration services | Rescreening, holds, human decisions, source-change notifications |
| `tradesieve` CLI | Operators, CI jobs, analysts | Validation, screening, replay, source inspection, diagnostics |
| TradeSieve MCP | Authorized AI agents | Structured evidence retrieval and review-safe workflows |
| Reviewer console | Compliance reviewers | Findings, evidence, human decision, expiry, audit |

## Repository map

| Path | Purpose |
| --- | --- |
| `docs/requirements/` | Original request, problem, scope, success criteria |
| `docs/product/` | Service definition, personas/journeys, detailed requirements, MVP |
| `docs/architecture/` | Service/domain/integration/security/implementation design |
| `docs/decisions/` | Architecture decision records |
| `docs/delivery/` | Agile operating model, Phase 1 plan and backlog |
| `docs/getting-started/` | Target/released user and integration journeys |
| `docs/research/` | Regulatory, community, academic, and industry research |
| `api/openapi/` | Versioned canonical HTTP contract |
| `examples/` | Synthetic requests and safe expected responses |
| `research/sources.yaml` | Machine-readable source registry |
| `src/tradesieve/` | Python domain/application/port package skeleton |
| `tests/` | Unit and architecture-boundary tests |
| `compose.yaml`, `Dockerfile` | Demo-only reference runtime and pinned image build |
| `scripts/` | Repository and contract checks |

Start with the [documentation index](docs/README.md).

Developers can reproduce all non-container local gates from a clean checkout with `./scripts/check.sh`. Full reference-deployment acceptance additionally requires `./scripts/test_compose.sh`; see the [development setup](docs/getting-started/development.md) for pinned prerequisites and scope.

The [Docker reference deployment](docs/getting-started/docker-reference.md) starts only the runtime health/readiness foundation; it does not screen transactions or grant legal clearance.

## Current contract artifacts

- [Draft OpenAPI 3.1 contract](api/openapi/tradesieve.v1.json)
- [Synthetic transaction request](examples/requests/transaction-screening.json)
- [Expected review-required response](examples/responses/transaction-screening.review-required.json)

## Explicit non-goals

- Declaring a transaction legal, sanctions-free, or licence-exempt.
- Treating a country flag, HS code, or fuzzy name match as a final decision.
- Replacing customs classification, export-control counsel, regulators, banks, or commercial intelligence providers.
- Sending production customer or shipment data to third-party AI services by default.
- Scraping and silently overwriting legal sources without provenance and version control.

## Governance

This is a private `fyaic` organization repository. Delivery follows the [agile operating model](docs/delivery/agile-operating-model.md), [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and [GOVERNANCE.md](GOVERNANCE.md).
