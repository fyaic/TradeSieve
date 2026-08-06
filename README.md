# TradeSieve

> Independent, agent-ready trade compliance control plane for sanctions, export-control, and transaction-risk triage.

[![Status: Discovery](https://img.shields.io/badge/status-discovery-6f42c1)](#project-status)
[![Interfaces: API CLI MCP](https://img.shields.io/badge/interfaces-API%20%7C%20CLI%20%7C%20MCP-0969da)](docs/architecture/agent-interface-principles.md)
[![Decision model: Human cleared](https://img.shields.io/badge/decision-human--cleared-b7791f)](docs/decisions/0002-human-clearance-only.md)

TradeSieve is a research and design repository for an independent screening service that can sit beside CRM, order, booking, shipment, and payment systems. It is intended to normalize official sanctions and export-control sources, screen parties/goods/routes/payments, preserve evidence, and route uncertain cases to authorized reviewers.

## Project status

**Discovery only. No production screening engine exists yet.** Nothing in this repository is legal advice or an automatic clearance mechanism.

The current work records the original logistics-industry problem, surveys official sources and existing systems, and establishes design constraints for a future implementation.

## Product principles

- **Independent control plane:** business systems call TradeSieve; they do not embed legal data or matching rules.
- **One contract, multiple interfaces:** REST/OpenAPI is canonical; CLI and MCP are typed adapters over the same application service.
- **Evidence before score:** every result identifies the source version, rule version, facts, uncertainty, and required action.
- **Human clearance only:** automation may hold or escalate; only an authorized reviewer can issue a time-bounded release.
- **As-of-time reproducibility:** raw sources, deltas, hashes, and decisions are immutable and replayable.
- **AI as an assistant:** models may extract, normalize, compare, and draft; deterministic controls and human review own disposition.

## Intended interfaces

| Interface | Primary users | Intended use |
| --- | --- | --- |
| REST/OpenAPI | CRM, OMS, booking, payment systems | Synchronous screening and case operations |
| Events/webhooks | Integration services | Rescreening, holds, releases, source-change notifications |
| `tradesieve` CLI | Operators, CI jobs, analysts | Batch screening, replay, source inspection, diagnostics |
| TradeSieve MCP | AI agents | Structured evidence retrieval and review-safe workflows |

## Documentation map

- [Original request](docs/requirements/original-request.md)
- [Problem, scope, and success criteria](docs/requirements/problem-and-scope.md)
- [Initial system shape](docs/architecture/initial-system-shape.md)
- [Agent interface principles](docs/architecture/agent-interface-principles.md)
- [Research landscape](docs/research/landscape.md)
- [Official data and regulatory sources](docs/research/regulatory-and-data-sources.md)
- [Open-source and standards review](docs/research/open-source-and-standards.md)
- [Academic research review](docs/research/academic-research.md)
- [Industry patterns](docs/research/industry-patterns.md)
- [Machine-readable source registry](research/sources.yaml)
- [Discovery roadmap](docs/roadmap/discovery-plan.md)

See the [documentation index](docs/README.md) for all records and decisions.

## Explicit non-goals

- Declaring a transaction legal, sanctions-free, or licence-exempt.
- Treating a country flag, HS code, or fuzzy name match as a final decision.
- Replacing customs classification, export-control counsel, regulators, banks, or commercial intelligence providers.
- Sending production customer or shipment data to third-party AI services by default.
- Scraping and silently overwriting legal sources without provenance and version control.

## Repository governance

This repository is currently private and proprietary. See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and [GOVERNANCE.md](GOVERNANCE.md).
