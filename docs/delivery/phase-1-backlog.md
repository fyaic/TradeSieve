# Phase 1 backlog decomposition

**Status:** Product backlog baseline. GitHub Issues are the execution source of truth; this document preserves scope and traceability.

## [E1 — Walking skeleton and distribution](https://github.com/fyaic/TradeSieve/issues/11)

| Story | Sprint | User outcome | Acceptance summary | Depends on |
| --- | --- | --- | --- | --- |
| [TS-101](https://github.com/fyaic/TradeSieve/issues/17) Project skeleton and CI | S1 | Developer can build/test consistently | Locked Python project, format/lint/type/test/contract CI, pinned Actions, separate PostgreSQL/Compose gates, no secret/production data | ADR-0004 proposed |
| [TS-102](https://github.com/fyaic/TradeSieve/issues/18) Docker reference deployment | S1 | Evaluator can start service locally | Compose starts app/worker/PostgreSQL, health/readiness distinguish runtime from source coverage, clean teardown | TS-101 |
| TS-103 Configuration and secret boundary | S1 | Deployer understands every setting | Typed config, environment template, safe defaults, secret validation/redaction | TS-101 |
| TS-104 Database migrations and restore smoke test | S1/S3 | Operator can evolve and recover state | Fresh migrate, upgrade fixture DB, backup/isolated restore evidence | TS-102 |
| TS-105 Release packaging and provenance | S3 | Evaluator receives reproducible `0.1.0` artifacts | Image digest, CLI package, SBOM, release notes, known limitations, clean-install evidence | All epics |

## [E2 — Source provenance and versioned controls](https://github.com/fyaic/TradeSieve/issues/12)

| Story | Sprint | User outcome | Acceptance summary | Depends on |
| --- | --- | --- | --- | --- |
| [TS-201](https://github.com/fyaic/TradeSieve/issues/19) Runtime source registry | S1 | Source operator knows scope/owner/licence/freshness | Governance fields required; read API/CLI redacts credentials | TS-101/103 |
| [TS-202](https://github.com/fyaic/TradeSieve/issues/20) Immutable snapshot lifecycle | S1/S2 | Operator can retrieve/quarantine/hash/validate/diff/activate/rollback | Durable synthetic lifecycle/rollback, private bytes, safe canonical DTOs, and corruption refusal; no REST/MCP/raw export | TS-201/104 |
| [TS-203](https://github.com/fyaic/TradeSieve/issues/3) EU source connector proof | S2 | Team proves EU data lifecycle | Approved official endpoint, raw/hash/parser/diff/locator, access/licence record | TS-202 |
| [TS-204](https://github.com/fyaic/TradeSieve/issues/4) OFAC SLS connector proof | S2 | Team proves delta-capable source lifecycle | Snapshot/delta, stable IDs, program fields, history boundary documented | TS-202 |
| [TS-205](https://github.com/fyaic/TradeSieve/issues/27) Versioned rule bundle | S1/S2 | Result identifies cited rule/policy version | Source/scope/effective/owner/tests/activation/rollback stored | Issue #2, TS-202 |
| TS-206 Impact analysis and rescreen jobs | S2 | Relevant open cases update after source/rule change | Test delta queues linked cases, produces events, invalidates affected decision only | TS-202/205/402 |

## [E3 — Screening and transaction domain](https://github.com/fyaic/TradeSieve/issues/13)

| Story | Sprint | User outcome | Acceptance summary | Depends on |
| --- | --- | --- | --- | --- |
| [TS-301](https://github.com/fyaic/TradeSieve/issues/7) Canonical request/result schemas | S1 | Integrator has one stable typed contract | OpenAPI plus examples cover action/parties/goods/route/end-use/payment/evidence/version/result | Issue #7 |
| [TS-302](https://github.com/fyaic/TradeSieve/issues/21) Intake, canonical hash, and idempotency | S1 | Caller can retry safely | Same key/body returns same IDs; key/body conflict typed; original input preserved | TS-301/104 |
| [TS-303](https://github.com/fyaic/TradeSieve/issues/22) Deterministic completeness and exact-identifier controls | S1 | User receives exact missing facts and strong-ID findings | Golden/incomplete/negative fixtures pass; missing facts never default green | TS-301/205 |
| [TS-304](https://github.com/fyaic/TradeSieve/issues/5) Multilingual synthetic fixture set | S1/S2 | Matcher selection has an approved test basis | Chinese/Cyrillic/Latin, aliases, identifiers, weak/ambiguous negatives, ownership | Issue #5 |
| [TS-305](https://github.com/fyaic/TradeSieve/issues/6) Matcher adapter and benchmark | S2 | Reviewer receives explainable candidates | Exact/pg_trgm/yente/Watchman/Splink candidates compared; active threshold/version approved | Issue #6, TS-304 |
| TS-306 Goods/route/end-use/payment consistency controls | S2 | User sees non-party risk and required evidence | Golden case produces named facts/conflicts; HS/category never definitive | Issues #1/#2, TS-301/205 |
| TS-307 Reproducible screening orchestration | S1/S2 | Same input/version set replays | Result/version/input hashes stable; model suggestions isolated; coverage warnings visible | TS-302/303/305/306 |

## [E4 — Case, evidence, and human decision](https://github.com/fyaic/TradeSieve/issues/14)

| Story | Sprint | User outcome | Acceptance summary | Depends on |
| --- | --- | --- | --- | --- |
| [TS-401](https://github.com/fyaic/TradeSieve/issues/23) Structured findings and conservative state machine | S1 | Caller gets hold/review/escalate and reason | P0/P1/P2/state/action transitions and forbidden paths fully tested | TS-303 |
| TS-402 Append-only case/evidence events | S2 | Reviewer can add evidence without erasing history | Evidence hash/reference, actor/time, finding resolution event, conflict retention | TS-401/104 |
| TS-403 Human decision, scope, expiry, and invalidation | S2 | Authorized reviewer can release only a named action | Role/four-eyes/completeness/version/rationale/expiry enforced; agent/service denied | Issue #8, TS-402 |
| TS-404 Minimal reviewer queue and case view | S2 | Reviewer can complete golden path in browser | P0/P1 ordering, facts/unknowns/sources/rules/suggestions separated, accessible controls | TS-402/403 |
| TS-405 Evidence/audit pack | S3 | Audit reviewer can reconstruct a case | Decision-first redacted export with source URLs/dates/versions and chronological events | TS-403/604 |

## [E5 — API, CLI, MCP, and business integration](https://github.com/fyaic/TradeSieve/issues/15)

| Story | Sprint | User outcome | Acceptance summary | Depends on |
| --- | --- | --- | --- | --- |
| [TS-501](https://github.com/fyaic/TradeSieve/issues/24) Versioned REST API and typed errors | S1/S2 | CRM/OMS can screen/read/submit/request safely | Auth, limits, idempotency, OpenAPI, error semantics and interactive docs pass | TS-301/307/401 |
| TS-502 CLI client | S3 | Analyst can validate/screen/read using files/stdin | Human/JSON output, stdout/stderr, safe exit codes, REST parity | TS-501 |
| TS-503 MCP adapter | S3 | Agent can screen/read/explain/request review safely | Official SDK/target protocol, structured parity, narrow scopes, no clearance/bulk/arbitrary tools | Issue #9, TS-501 |
| TS-504 Signed webhook delivery | S3 | Caller receives reliable state changes | Outbox, signature/timestamp/rotation, retry/dead-letter, at-least-once and dedupe tests | TS-401/104 |
| [TS-505](https://github.com/fyaic/TradeSieve/issues/37) Sample CRM/OMS gate | S1 demo / S3 full | Integrator first sees a fixed synthetic CRM interception demo, then the complete integration | Demo maps fixed records to canonical contracts and enforces conservative results; full story later adds formal REST, webhook verification, re-read and named-action release | Issue #1, TS-501/504 |

## [E6 — Security, operations, and MVP acceptance](https://github.com/fyaic/TradeSieve/issues/16)

| Story | Sprint | User outcome | Acceptance summary | Depends on |
| --- | --- | --- | --- | --- |
| [TS-601](https://github.com/fyaic/TradeSieve/issues/25) Identity, tenant context, and authorization matrix | S1/S2 | Every action is least privilege | OIDC/workload integration seam, tenant/object/role checks, cross-tenant denial, audit | Issue #9, TS-501 |
| TS-602 Agent/content threat controls | S2/S3 | Untrusted evidence cannot instruct tools/service | Upload/size/type/path/SSRF/archive/prompt-injection negative tests | Issue #9, TS-503 |
| [TS-603](https://github.com/fyaic/TradeSieve/issues/26) Redacted observability | S1/S3 | Operator can diagnose without leaking data | Correlation, structured logs/metrics/traces; golden telemetry secret/PII scan passes | TS-101/501 |
| TS-604 Audit integrity and backup/restore | S2/S3 | Auditor/operator can trust and recover records | Append-only DB roles/events, hash checks, backup isolated restore | TS-104/402 |
| TS-605 Performance and workload evidence | S3 | Owners understand MVP limits | p95 screening, batch/worker/webhook behavior, matcher/reviewer metrics published | TS-305/307/504 |
| TS-606 Golden-path documentation and test | S3 | New evaluator can take, use, and integrate MVP | Clean clone through API/CLI/MCP/reviewer/webhook/source delta/replay without maintainer | All Phase 1 Must stories |

## Existing discovery/spike issues

The transferred repository already contains the core discovery issues:

- [#1 business systems and event inventory](https://github.com/fyaic/TradeSieve/issues/1)
- [#2 jurisdiction and legal-nexus matrix](https://github.com/fyaic/TradeSieve/issues/2)
- [#3 EU source lifecycle](https://github.com/fyaic/TradeSieve/issues/3)
- [#4 OFAC source lifecycle](https://github.com/fyaic/TradeSieve/issues/4)
- [#5 multilingual fixtures](https://github.com/fyaic/TradeSieve/issues/5)
- [#6 matcher benchmark](https://github.com/fyaic/TradeSieve/issues/6)
- [#7 canonical contract](https://github.com/fyaic/TradeSieve/issues/7)
- [#8 reviewer/human decision model](https://github.com/fyaic/TradeSieve/issues/8)
- [#9 API/CLI/MCP threat model](https://github.com/fyaic/TradeSieve/issues/9)

They should be relabelled as spikes/stories under the Phase 1 milestone rather than duplicated.

## First refinement order

1. Name owners and resolve issues #1, #2, #7, and #8 enough to meet Definition of Ready.
2. Refine TS-101/102/301/302/401/501 as the Sprint 1 walking vertical slice.
3. Time-box source access/licence and MCP SDK compatibility spikes before committing their implementation depth.
4. Accept the synthetic fixture specification before any matcher threshold decision.
5. Remove or defer scope explicitly when capacity is known; never silently trade away human-only clearance, provenance, idempotency, or fail-closed behavior.
