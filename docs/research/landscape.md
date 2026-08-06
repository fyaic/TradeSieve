# Research landscape

**Research date:** 2026-08-06
**Scope:** official data, open-source/community systems, entity-resolution research, industrial trade-compliance products, and agent interfaces.

## Executive synthesis

TradeSieve should not begin by building a fuzzy-name search engine from scratch. Strong prior art already exists for list ingestion, entity graphs, matching APIs, probabilistic linkage, policy-as-code, REST schemas, CLI conventions, and MCP adapters.

The defensible product boundary is the control plane around those components:

1. Official-source registry, immutable snapshots, legal/rule versioning, and as-of-time replay.
2. Logistics-specific transaction context across parties, ownership, goods, routes, documents, and payments.
3. Deterministic hold/escalation rules and human case workflow.
4. A single typed contract exposed consistently through API, events, CLI, and MCP.
5. Evidence quality, reviewer workload, and operational integration rather than an opaque “risk score.”

## Build, reuse, and buy hypothesis

| Capability | Initial position | Rationale |
| --- | --- | --- |
| Source snapshots and provenance | Build | Core audit and replay requirement; no generic matcher owns the legal-source lifecycle needed here |
| Entity graph/data model | Reuse/adapt | FollowTheMoney and BODS provide useful patterns; retain source-native data and TradeSieve-specific validity/provenance |
| Party candidate matching | Evaluate yente/Watchman first | Both are active open-source systems; yente supports multi-property matching and on-prem deployment |
| Cross-dataset entity resolution | Experiment with Splink and OpenSanctions Pairs | Useful for calibration and backfill; not a legal-decision engine |
| Legal/operational rules | Build on a policy engine | OPA offers signed bundles, decisions, and audit hooks; domain authoring usability must be tested |
| Goods classification | Build workflow; buy expertise/content as needed | HS/CN/TARIC is only an entry point; technical and legal review cannot be replaced by a generic model |
| Ownership, vessel, trade-flow intelligence | Buy or partner if needed | Official lists do not provide complete networks or historic commercial data |
| Case review and release | Build | The original requirement needs logistics-specific holds, evidence requests, expiry, and CRM/OMS integration |
| CLI and MCP | Build thin adapters | OpenSanctions yente-client is direct prior art; TradeSieve must expose the full case/rule contract, not only matching |

## Core findings

### 1. The hard problem is evidence orchestration

Name matching is necessary but insufficient. The system must combine legal nexus, party identity, ownership/control, product facts, route, end use/user, payment, documents, source freshness, and human disposition.

### 2. Matching should be a staged pipeline

A practical pipeline is:

1. Normalize scripts, names, identifiers, dates, addresses, and legal forms.
2. Generate candidates with exact identifiers, lexical/phonetic methods, aliases, and blocking keys.
3. Score multiple attributes rather than a company name alone.
4. Use an LLM or learned matcher only as a bounded second opinion on ambiguous candidates.
5. Route uncertainty to a reviewer and record the final label for future evaluation.

### 3. Continuous monitoring is an industry baseline

Commercial systems consistently combine onboarding screening, ongoing/delta rescreening, case remediation, internal watchlists, APIs/webhooks, and audit reports. TradeSieve's MVP should not stop at one-time search.

### 4. Product screening is not entity screening

Goods classification needs a product master, technical attributes, versioned legal annexes, destination/end-use rules, evidence requests, and qualified review. A party-screening engine can be reused, but it does not solve this domain.

### 5. Agent-native must mean safer structure, not more autonomy

CLI and MCP should return typed findings, missing facts, sources, and permitted next actions. The agent should not receive a single unqualified “allow/deny” tool. Mutations are narrow, authorized, idempotent, and auditable.

## Recommended reference stack for a prototype

- **Entity model and matcher:** Evaluate FollowTheMoney + yente with a locally mirrored, legally licensed dataset.
- **Policy evaluation:** Spike OPA/Rego with signed bundles and decision logs; compare with a simpler domain decision-table representation.
- **Persistence:** Relational case/event store plus search index; evaluate a graph projection before choosing a graph database.
- **Contracts:** JSON Schema Draft 2020-12 and OpenAPI 3.1.x.
- **Interfaces:** REST plus `tradesieve` CLI and MCP `2026-07-28`; events only after workflow integration is understood.
- **Observability:** OpenTelemetry-compatible traces tied to immutable screening and decision IDs.

## Research gaps before architecture freeze

- Field-level audit of actual CRM/OMS/payment data and missingness.
- Legal nexus and policy matrix for EU, UK, US, UN, PRC, customer, and bank requirements.
- Licence review for every dataset used commercially.
- Backfill volume, date range, and availability of historical list snapshots.
- Measured performance of yente, Watchman, Splink, and a learned/LLM second pass on Chinese/Russian/English fixtures.
- Reviewer workload and false-positive tolerance at each business gate.
- Deployment, data-locality, retention, and privacy requirements.
