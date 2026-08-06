# Discovery roadmap

The roadmap is evidence-gated. Dates should be committed only after access to real systems, stakeholders, and data-quality samples.

## Phase 0 — Governance and incident separation

Deliverables:

- Named product, compliance/legal, engineering, security/privacy, and operations owners.
- Separate P0 incident-response track for any currently listed/blocked entity.
- Approved discovery data-handling plan and synthetic-fixture policy.
- Initial jurisdiction and bank/customer policy matrix.

Exit gate: project discovery is not being used as a substitute for active sanctions incident handling.

## Phase 1 — Business and data discovery

Deliverables:

- CRM/OMS/booking/finance event map.
- Field-level inventory and completeness report.
- Party/goods/route/payment role mapping.
- Historic volume/date-range estimate.
- Reviewer roles, SLAs, and current manual process.

Exit gate: required inputs and enforcement points are explicit.

## Phase 2 — Source and licence proof

Deliverables:

- Working source registry and two end-to-end official connectors.
- Raw snapshot/hash/diff/replay demonstration.
- Licence/access review for all candidate data.
- Historic availability assessment.

Exit gate: one source update can be reproduced and traced without overwriting history.

## Phase 3 — Party-screening spike

Deliverables:

- Shared multilingual synthetic fixture set.
- Benchmarks for yente, Watchman, and exact/probabilistic baselines.
- Candidate-generation and cross-script error analysis.
- Reviewer workload simulation.

Exit gate: an architecture decision selects or rejects candidate matcher components with measured evidence.

## Phase 4 — Vertical slice

Deliverables:

- Canonical JSON Schemas and OpenAPI contract.
- One CRM/OMS sandbox event through party, goods-data completeness, route, rules, case, and human release.
- Equivalent REST, CLI, and MCP read/screen results.
- Immutable audit and source/rule/model provenance.
- Fail-closed shipment/payment behavior test.

Exit gate: the same synthetic case is reproducible across interfaces and can only be released by an authorized human.

## Phase 5 — Historic replay pilot

Deliverables:

- Small approved sample from active/open business records.
- Current-exposure replay separated from as-of-time reconstruction.
- Triage queue, precision sample, reviewer time, and data-gap report.
- Remediation and rescreen strategy.

Exit gate: stakeholders accept the workload and error profile before wider backfill.

## Phase 6 — Production design

Deliverables:

- Threat model, DPIA/privacy review where applicable, and data-retention design.
- Availability, disaster recovery, access control, and segregation-of-duties design.
- Vendor/build decisions and exit plans.
- Operational runbooks, metrics, incident procedures, and release gates.
- Production architecture and delivery plan.

Exit gate: compliance, security, product, engineering, and operations sign off on intended use and residual risks.

## Initial GitHub issues

1. [Inventory CRM, OMS, booking, and payment events](https://github.com/veil-chow-fyaic/TradeSieve/issues/1).
2. [Define the first jurisdiction and legal-nexus matrix](https://github.com/veil-chow-fyaic/TradeSieve/issues/2).
3. [Prototype an EU sanctions snapshot, hash, diff, and replay](https://github.com/veil-chow-fyaic/TradeSieve/issues/3).
4. [Prototype OFAC SLS snapshot, delta, and replay](https://github.com/veil-chow-fyaic/TradeSieve/issues/4).
5. [Specify a multilingual party-screening fixture set](https://github.com/veil-chow-fyaic/TradeSieve/issues/5).
6. [Benchmark yente, Watchman, and probabilistic baselines](https://github.com/veil-chow-fyaic/TradeSieve/issues/6).
7. [Design canonical screening request, result, evidence, and event contracts](https://github.com/veil-chow-fyaic/TradeSieve/issues/7).
8. [Design reviewer roles, human release, expiry, and segregation of duties](https://github.com/veil-chow-fyaic/TradeSieve/issues/8).
9. [Threat-model API, CLI, and MCP access](https://github.com/veil-chow-fyaic/TradeSieve/issues/9).
