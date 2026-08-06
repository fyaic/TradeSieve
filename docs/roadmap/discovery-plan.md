# Product maturity roadmap

**Status:** Updated delivery sequence
**Note:** The filename is retained so earlier issue/PR links remain valid. The old discovery-only phase numbering is superseded: Phase 1 now ends with a runnable service MVP.

## Continuous precondition — incident and governance separation

An active listing/banking/payment incident is handled by qualified legal/compliance, security, finance, and management owners. Product development is never a substitute for immediate incident containment, evidence preservation, bank/regulator/counsel coordination, or required legal action.

Every phase requires named product, compliance/legal, engineering, security/privacy, source/data, and operations decision owners.

## Phase 1 — Service MVP

Outcome: an evaluator can take, run, use, and integrate a private service MVP through REST, CLI, MCP, reviewer UI, and webhooks on an approved synthetic/pilot scope.

Included gates:

- CRM/OMS/payment event and field inventory sufficient for one sample integration.
- Initial legal-nexus/source/rule scope and licence/access record.
- EU and OFAC source snapshot/hash/diff lifecycle proofs subject to access approval.
- Multilingual fixture and matcher benchmark.
- Canonical schema/OpenAPI and equivalent REST/CLI/MCP results.
- Party plus goods/route/end-use/payment completeness vertical slice.
- Findings, evidence, holds, human-only decision, expiry, invalidation, and audit.
- Signed webhook sample CRM/OMS gate, fail-closed behavior, replay, backup/restore, and golden-path documentation.

Exit gate: the [Phase 1 MVP acceptance criteria](../product/mvp-scope.md) pass from a clean environment, with explicit known limitations and no claim of production legal coverage.

Detailed plan: [Phase 1 delivery plan](../delivery/phase-1-plan.md) and [backlog decomposition](../delivery/phase-1-backlog.md).

## Phase 2 — Approved pilot and historic current replay

Outcome: validate the MVP with a small legally/privacy-approved sample of real active/open records and a real integration sandbox.

Deliverables:

- approved data-handling/retention/privacy plan and representative sample;
- real field completeness and mapping report;
- current-source replay clearly separated from as-of reconstruction;
- measured candidate recall/precision sample, reviewer workload and case SLAs;
- pilot source licences/coverage and source-operation runbook;
- remediation, rescreen, correction, challenge, and incident processes;
- pilot security test, monitoring, backup/restore, and support ownership.

Exit gate: compliance, operations, integration, privacy/security, and product accept pilot error/workload and residual risk before wider use.

## Phase 3 — Production design and hardening

Outcome: a deployment-specific release is approved for defined business gates, jurisdictions, sources, tenants, volume, and availability objectives.

Deliverables:

- production architecture, capacity, HA/DR/RPO/RTO and dependency design;
- approved identity, authorization, four-eyes, secret/storage/network and audit controls;
- DPIA/automated-decision/AI-governance assessment where applicable;
- production source/vendor contracts, licences, exit plans and quality SLAs;
- operational, source, security, business-continuity and incident runbooks;
- release/change/rollback/rescreen governance and evidence;
- penetration/security testing and production acceptance.

Exit gate: named product, engineering, compliance/legal, security/privacy, data/source, integration, and operations owners sign off the intended use and residual risks.

## Phase 4 — Coverage and scale expansion

Potential outcomes are evidence-driven, not precommitted:

- more jurisdictions/regimes and legal/policy matrices;
- stronger ownership/control, vessel, corporate, and trade intelligence;
- goods classification workflow and qualified technical-review integrations;
- more CRM/OMS/payment connectors and tenants;
- controlled as-of legal/source reconstruction;
- specialized search/matching services or broker only when measured load/trust boundaries require them;
- broader reviewer workflow or platform capabilities only when the focused service is insufficient.

Every expansion requires source/licence/privacy/security review, test fixtures, reviewer-workload evidence, migration/rollback, and no weakening of human-only clearance.

## Current GitHub discovery and design issues

1. [Inventory CRM, OMS, booking, and payment events](https://github.com/fyaic/TradeSieve/issues/1).
2. [Define the first jurisdiction and legal-nexus matrix](https://github.com/fyaic/TradeSieve/issues/2).
3. [Prototype an EU sanctions snapshot, hash, diff, and replay](https://github.com/fyaic/TradeSieve/issues/3).
4. [Prototype OFAC SLS snapshot, delta, and replay](https://github.com/fyaic/TradeSieve/issues/4).
5. [Specify a multilingual party-screening fixture set](https://github.com/fyaic/TradeSieve/issues/5).
6. [Benchmark yente, Watchman, and probabilistic baselines](https://github.com/fyaic/TradeSieve/issues/6).
7. [Design canonical screening request, result, evidence, and event contracts](https://github.com/fyaic/TradeSieve/issues/7).
8. [Design reviewer roles, human release, expiry, and segregation of duties](https://github.com/fyaic/TradeSieve/issues/8).
9. [Threat-model API, CLI, and MCP access](https://github.com/fyaic/TradeSieve/issues/9).
