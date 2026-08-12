# Phase 1 service MVP

**Planning window:** 2026-08-10 through 2026-09-18 (three two-week sprints)
**Planning assumption:** dates are a baseline until team capacity and source access are confirmed.
**Current repository state:** `0.1.0a5` business-demo-ready engineering prototype. A real-source screening vertical slice, active-source CLI, authenticated technical-preview REST endpoint, one local read-only stdio MCP tool and synthetic CRM are implemented and packaged with a one-command demo and screenshot-backed handoff. Full transaction/case persistence, reviewer workflow, webhook, remote MCP/OAuth and the broader target tool set remain acceptance targets.

## Phase 1 outcome

At the end of Phase 1, an evaluator can clone the repository, start a private local demo, submit the same synthetic transaction through REST, CLI, and MCP, see an evidence-oriented hold/review result, resolve the case through an authorized human workflow, and connect a sample CRM/OMS gate through documented synchronous and webhook contracts.

## Golden vertical slice

The Phase 1 demo must show all of the following in one repeatable script:

1. Start TradeSieve and PostgreSQL with Docker Compose.
2. Load versioned synthetic sanctions/ownership/goods/rule fixtures; optionally load approved official pilot snapshots.
3. Submit a transaction containing a cross-script party candidate, incomplete goods specification, Russia-related route facts, and a payment party.
4. Return `REVIEW_REQUIRED` or `ESCALATE`, a business `HOLD`, structured P0/P1/P2 findings, required evidence, source/rule/schema versions, and stable IDs.
5. Retrieve the identical result through REST, CLI JSON, and MCP structured output.
6. Submit synthetic evidence and request human review.
7. Have an authorized reviewer resolve findings and create a scoped, expiring human decision through the reviewer console/API.
8. Deliver a signed webhook; the sample integration verifies, deduplicates, re-reads the case, and releases only the named synthetic business action.
9. Activate a source delta, trigger targeted rescreening, and demonstrate decision invalidation or review when the changed assertion is relevant.
10. Export a redacted evidence/audit pack and replay the case from preserved versions.

## Included capabilities

| Capability | Phase 1 depth |
| --- | --- |
| Distribution | One documented container image/Compose stack, CLI package, MCP endpoint, examples, and release notes |
| Sources | Implemented governed synthetic registry and durable snapshot/hash/diff/rollback proof; EU and OFAC connectors remain subject to access/licence review |
| Matching | Exact identifiers, deterministic normalization, configurable candidate matcher behind an adapter, benchmark report |
| Goods/route/payment | Structured intake, completeness/consistency checks, sensitive/restricted candidates; no definitive classification |
| Rules | Small cited versioned rule/policy bundle sufficient for synthetic vertical slice |
| Cases | Findings, evidence requests, holds, append-only events, human decision, expiry, invalidation, audit |
| REST | Versioned OpenAPI contract, idempotency, typed errors, health/coverage endpoints |
| CLI | Validate, screen, show case, list required evidence/source freshness; human and JSON output |
| MCP | Screen/read/explain/request-review tools; read-mostly and least privilege |
| Reviewer experience | Minimal authenticated queue and case detail sufficient for golden path |
| Integration | Generic CRM/OMS synchronous gate and signed webhook consumer example |
| Operations | Migrations, backup/restore proof, redacted observability, CI, SBOM/security checks as available |

## Explicit exclusions

- Production legal clearance or a claim of complete sanctions/export-control coverage.
- Definitive dual-use/customs classification.
- Comprehensive beneficial-ownership, vessel, adverse-media, or trade-flow intelligence.
- Full historical/as-of legal reconstruction.
- Multi-tenant SaaS billing/administration or vendor-specific CRM marketplace packages.
- High availability across regions, zero-downtime source changes, or production SLA.
- Autonomous evidence collection from unrestricted websites or autonomous relationship termination.
- General MCP tools for clearing cases, arbitrary SQL/files/network, or bulk personal-data export.

## “User can take it and use it” release contents

- versioned source archive or image digest;
- `docker compose` deployment and environment template;
- database migrations and synthetic seed pack;
- OpenAPI contract and interactive docs;
- CLI installation package and shell-completion/help;
- MCP Streamable HTTP configuration plus local stdio option if retained;
- request/response/webhook examples and sample CRM gate;
- reviewer account/bootstrap instructions for demo only;
- architecture, threat model, data/licence register, runbook, backup/restore, and troubleshooting;
- automated contract/unit/integration/E2E tests and published benchmark/release evidence;
- SBOM and known-limitations statement.

## Acceptance gates

### User acceptance

- A new evaluator completes the documented golden path without repository-author intervention.
- Every hold explains what is unresolved and which user/role must act.
- No interface presents “no match” or HTTP success as legal permission.

### Integration acceptance

- Sample CRM/OMS integration maps an external object/action, submits idempotently, stores TradeSieve IDs/state/action, verifies webhook signature, and deduplicates events.
- Timeout, stale source, unauthorized response, and unsupported schema tests fail closed.

### Compliance acceptance

- Named compliance owner approves the Phase 1 legal/source scope and synthetic rule fixtures.
- Human-only clearance, evidence requirements, decision expiry, and source-change rescreen behavior pass negative tests.

### Engineering/security acceptance

- Contract, unit, migration, integration, E2E, authorization, prompt-injection, and replay tests pass.
- No secrets or production data are present; default telemetry is redacted.
- A clean environment build, install, backup, restore, and teardown are demonstrated.

## Phase 1 is not production approval

Passing the MVP acceptance gates proves that the service shape and integration contract work on an approved test scope. Production use still requires legal/source coverage approval, real-data quality validation, privacy/security review, operational ownership, matching thresholds, performance/capacity evidence, and deployment-specific sign-off.
