# Security, privacy, and trust architecture

**Status:** Phase 1 threat-model baseline; deployment-specific review remains mandatory.

## Protected outcomes

TradeSieve must protect both confidentiality and control integrity. A data leak is harmful, but so are a forged clearance, suppressed finding, stale source presented as current, altered audit history, unauthorized bulk search, or unavailable gate that callers treat as approval.

## Trust zones

| Zone | Trust assumption | Controls |
| --- | --- | --- |
| Business clients | Authenticated but possibly buggy/compromised | OAuth client scopes, schema/size limits, idempotency, tenant/object authorization |
| Reviewer browser | Human identity; session/phishing risks | OIDC, MFA via IdP, secure session, CSRF, step-up for decisions, short expiry |
| MCP/agent | Untrusted model context and tool orchestration | Narrow tools, opaque handles, explicit confirmation, result limits, no clearance tool |
| Uploaded evidence/source content | Fully untrusted | Quarantine, media/type/size validation, no macros/execution, isolated parsing, malware controls where deployed |
| Application/worker | Trusted code but least privilege | Separate runtime roles, outbound allowlists, secret manager, read-only filesystem where possible |
| Database/object storage | Sensitive system of record | Private network, encryption, backups, DB roles/RLS, immutable/hash verification, retention |
| External official/vendor sources | Authenticity/availability can fail | TLS, allowlist, signed/hash validation where available, provenance, quarantine, freshness alarms |

## Identity and authorization

- Integrate with the operator identity provider using OIDC/OAuth; do not build password management for production.
- API clients use client credentials or workload identity with audience-restricted short-lived tokens.
- Authorize every application command/query on tenant, role/scope, action, object, and relevant case state.
- Suggested scopes: `screening:submit`, `screening:read`, `case:read`, `evidence:submit`, `review:request`, `finding:resolve`, `decision:human-clear`, `source:operate`, `policy:approve`, `audit:export`.
- `decision:human-clear` requires a named human identity, reviewer role, case completeness, optional step-up, and four-eyes policy where configured; service-account and agent tokens are denied.
- Source activation separates operator preparation from compliance/data approval for material changes.
- PostgreSQL roles separate migrations, application read/write, worker, audit export, and backup. Row-level security is defense in depth.

## Agent and MCP threats

| Threat | Phase 1 control |
| --- | --- |
| Prompt injection in uploaded/source text | Tool results mark content as untrusted; agent instructions never come from evidence; no automatic link/tool execution |
| Confused deputy | MCP validates token audience/resource and authorization for every tool call; client-provided URLs/paths are not fetched directly |
| Overbroad discovery | Tool/resource inventory is scope-filtered; search requires bounded case/source handles |
| Exfiltration | Output schemas and maximum result sizes; redacted summaries; no bulk evidence/raw-source tools |
| Unauthorized mutation | Read-only default; mutation-specific scopes, idempotency, confirmation, complete audit |
| Tool-result forgery/replay | TLS, opaque IDs, event/result hashes, timestamps, protocol/schema version, audit correlation |
| Clearance delegation | No general clearance tool; human decision API rejects agent/service identities |

## Data classification

| Class | Examples | Default handling |
| --- | --- | --- |
| Public-source | Official list/legal text and public identifiers | Still version/hash/licence controlled |
| Internal | Rule configuration, integration metadata, synthetic fixtures | Private repository/deployment; role restricted |
| Confidential business | Customer/order/shipment/payment facts, reviewer rationale | Private processing, encryption, minimal output/logging, defined retention |
| Sensitive personal | Names/IDs/dates/ownership and investigation evidence | Lawful basis, minimization, access logging, retention/deletion process, privacy review |
| Secrets | Tokens, signing keys, DB credentials | Secret manager/environment injection; never Git, logs, examples, or model context |

## Audit integrity

- Material events are append-only to ordinary application roles.
- Each event records actor/client, authorization outcome, object, action, timestamp, correlation, schema and relevant version/hash references.
- High-value event chains can include previous-event/current-event hashes; external/WORM anchoring is a production decision.
- Operational logs help diagnostics but are not the sole legal/audit record.
- Audit exports are scoped, redacted, access-logged, and themselves generate audit events.

## Privacy and automated-decision safeguards

- Document purpose, lawful basis, data owners, sources, fields, retention, recipients, storage/processing regions, and data-subject procedures before live data.
- Assess GDPR Article 22 and DPIA requirements where EU personal data or materially adverse automated decisions are involved.
- Preserve meaningful human intervention: authority, time, evidence visibility, ability to challenge model/rule suggestions, and recorded independent rationale.
- Do not use case/customer data to train or fine-tune models by default.
- External model use is opt-in per approved deployment with data-processing, region, retention, and redaction controls.

## Supply-chain and runtime baseline

- Pin dependencies and base images; produce lockfiles and SBOM.
- Run static, dependency, secret, container, contract, and test checks in CI with least-privilege GitHub Actions permissions.
- Keep source/evidence parsers isolated from credentials and unnecessary network access.
- Apply request/body/file limits, timeouts, concurrency limits, rate limits, and safe archive extraction.
- Redact tokens, names, identifiers, document bodies, payment details, and raw match queries from default logs/traces.
- Back up encrypted state and test restore; deletion/retention jobs remain auditable.

## Phase 1 security acceptance scenarios

1. Service/agent identity attempts `HUMAN_CLEARED` and receives a denied, audited response.
2. Cross-tenant case ID is guessed and returns no data without confirming existence.
3. Uploaded text instructs the agent to ignore policy/call a URL; no instruction or fetch executes.
4. Webhook with invalid signature, expired timestamp, or duplicate event ID cannot release an action.
5. Source parser sees an unexpected archive/file/schema/deletion and cannot activate the snapshot.
6. Logs/traces from the golden path contain no names, document bodies, tokens, bank/payment details, or evidence payloads.
7. Required source becomes stale/unavailable; screening and caller fail closed.
8. Database backup is restored into an isolated environment and event/source hashes still verify.
