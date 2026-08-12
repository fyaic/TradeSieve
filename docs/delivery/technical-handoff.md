# Technical handoff

**Release target:** `0.1.0a5`
**Audience:** engineering, platform, security, data governance and future compliance-rule owners

This document keeps engineering evidence out of the business-facing README while preserving an auditable handoff.

## 1. Implemented runtime slice

- PostgreSQL-backed immutable source snapshots, source-specific projections and one atomic active bundle containing EU FSF, EU Annex I, OFAC SDN and OFAC Consolidated;
- exact normalized-name and selected strong-identifier candidate screening;
- versioned BIS CHPL 50-item HS-6 guidance candidate set;
- source-hash-bound technical assertions for the first reviewed `3A001` product families;
- authenticated REST technical preview, active-source CLI, local read-only stdio MCP and synthetic CRM over the same application service;
- deny-default authorization primitives, immutable authorization/audit foundations, idempotent intake foundations and fail-closed readiness;
- hardened Docker Compose reference stack with PostgreSQL 18.4, non-root read-only app/worker containers and local named volumes.

## 2. Acceptance evidence carried forward from `0.1.0a4`

The `0.1.0a4` release branch passed 2,081 repository tests with 100% statement and branch coverage, the independent PostgreSQL 18.4 gate, the full Compose gate and the networked official-source gate.

Migration `20260810_0007` was exercised from an empty database for four-source activation, idempotency, exact readback and immutability constraints. The upgrade path also retained each pre-existing EU evidence row from migration `0006`; because the upgraded state had no OFAC projections, active screening failed closed until a complete four-source bundle was activated.

On 2026-08-11 the networked end-to-end gate parsed and persisted:

| Source | Parsed records |
| --- | ---: |
| EU Financial Sanctions Files | 6,234 entities |
| EU Annex I | 384 control entries |
| OFAC SDN | 19,199 entries |
| OFAC Consolidated | 481 entries |

A repeated refresh returned `IDEMPOTENT`. A public SOVCOMFLOT sample returned `RED/HOLD` with `RUSSIA-EO14024` program evidence. The synthetic CRM bound the same four-source bundle and also exercised CHPL candidates and the bounded, source-hash-bound `3A001` comparisons.

On 2026-08-12 REST, CLI and MCP were re-run against the same active bundle. All returned `RED/HOLD`, `automatic_clearance=false` and bundle `official-bundle-29ecef0561b30897b2f73870714b9181fd7fae96c36d1985aeb5ebbfd66834b4`; invalid MCP input was rejected. See [structured evidence](../evidence/interface-parity-2026-08-12.json).

The final local `0.1.0a5` repository gate passed 2,084 tests with 100% statement and branch coverage. A clean isolated `tradesieve-delivery-check` Compose project then built `0.1.0-alpha.5`, activated the four-source bundle, passed readiness and active-screening checks, rendered the synthetic CRM and returned `RED/HOLD` from the live page. REST and MCP were rerun against that deployment and returned the same bundle and conservative action; the MCP probe also rejected invalid input. GitHub Actions and the Release provide the independent hosted run and final artifact hashes. Counts are evidence of the implemented boundary, not a claim of global data coverage, legal accuracy or production fitness.

## 3. `0.1.0a5` delivery changes

- business-oriented README with stable delivery links and screenshot-backed outcomes;
- one-command local business demo bootstrap with bounded official-source retry and active-screening verification;
- 7-day warning / 30-day expiry for repository-owned synthetic demo snapshots so a long-running demo does not become unusable after two hours; official four-source screening remains limited to 48 hours;
- business delivery scorecard and explicit human/professional sign-off gap;
- current REST/CLI/MCP parity evidence and visual summary;
- refreshed PDF/release packaging and alpha version metadata.

## 4. Production blockers

The following are not optional polish. They block real-transaction production use:

1. formal service identity, OIDC/OAuth, tenant and object authorization, secret rotation and administrative break-glass controls;
2. persisted arbitrary screening cases, reviewer queue, evidence upload, dispositions, four-eyes approval, rescreening and retention/deletion controls;
3. signed webhooks, request idempotency for the production API, rate limits, replay protection and consumer recovery semantics;
4. complete ownership/control propagation, OFAC 50 Percent Rule policy, fuzzy/transliteration resolution and professional false-positive/false-negative validation;
5. complete Annex I implementation plus Russia-specific goods, services, route, end-use, catch-all, licensing and legal-effect policy under qualified review;
6. controlled workbook/internal-knowledge ingestion with provenance, permissions, expiry, dispute and non-authoritative policy semantics;
7. production database/object storage, encryption/key management, backups and tested recovery objectives;
8. observability, on-call runbooks, vulnerability response, capacity/load testing and controlled source-update operations;
9. approved privacy/data-residency design for customer and investigation data, including any Agent/model processor;
10. named business and compliance owners who approve fields, actions, service-level objectives and rule releases.

## 5. External and supply-chain dependencies

Runtime screening does not call regulator websites and does not upload customer requests to them. The controlled refresh job downloads public regulator artifacts; see the [external dependency register](../operations/external-dependencies.md) for exact domains, redirects, authentication, licensing notes, failure behavior and production replacements.

The reference stack uses local PostgreSQL and local volumes. Production should replace these with managed, encrypted, monitored services and private immutable object storage. Build/release additionally depends on GitHub Actions, pinned actions, Python/uv, locked Redocly tooling and container registries.

## 6. Operator sequence

1. deploy/migrate without making an incomplete bundle active;
2. run the source refresh under a dedicated service identity and restricted egress;
3. verify source versions, counts, hashes, freshness and activation receipt;
4. exercise active screening through the intended interface;
5. keep consumers blocked on errors, timeouts or unavailable sources;
6. monitor freshness before the 48-hour official bundle expiry;
7. retain activation, request/result and human decision references under the approved policy;
8. roll back application code without mutating historical evidence; activate a prior verified bundle only through the governed mechanism.

## 7. Recommended implementation order

1. case/reviewer workflow and production API idempotency;
2. identity, tenant authorization and signed event delivery;
3. internal experience-pool ingestion and provenance controls;
4. ownership/control graph and entity-resolution workbench;
5. professionally approved policy packs for Russia and broader export controls;
6. production operations, SLOs, recovery and controlled source-refresh scheduling;
7. remote MCP only after OAuth, tenant/tool authorization and audit are complete.
