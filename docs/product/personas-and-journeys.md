# Personas and user journeys

**Status:** Phase 1 product baseline
**Data rule:** all examples and acceptance tests use synthetic data until an approved pilot-data plan exists.

## Primary personas

| Persona | Goal | Main interface | Must never be asked to do |
| --- | --- | --- | --- |
| Sales/customer operations | Know whether a quote/onboarding may proceed and what documents to collect | CRM/OMS status plus evidence request | Interpret sanctions law or tune match thresholds |
| Logistics/booking operator | Prevent booking or shipment release while a material issue is open | OMS gate, webhook, reviewer console | Treat a service timeout as approval |
| Finance/treasury operator | Stop payment when payer/payee/bank/path facts are unresolved | Payment gate and case link | Resolve ownership or list matches without authority |
| Compliance reviewer | Review findings, evidence, source/rule versions, and record a scoped decision | Reviewer console and API | Rely on an unexplained score or overwrite history |
| Source/data operator | Retrieve, validate, diff, activate, and roll back source snapshots | CLI and operations API | Silently replace an accepted snapshot |
| Integration engineer | Map business events and fields into one stable contract | OpenAPI, examples, SDK/CLI | Reimplement legal logic inside CRM/OMS |
| Authorized AI agent | Extract/compare evidence, screen, explain, and request review | MCP | Release holds, lower risk, or browse unrestricted case data |
| Security/audit reviewer | Verify access, decisions, changes, and evidence lineage | Audit export and read APIs | Depend on mutable application logs as the sole record |

## Journey 1 — CRM quote gate

1. Sales creates or updates a quote in CRM.
2. CRM maps the quote, involved parties, known goods, route, and end use to a `ScreeningRequest`.
3. CRM sends an idempotent synchronous screening call before the quote-release action.
4. TradeSieve validates completeness, evaluates the accepted source/rule set, and returns a stable screening/case ID.
5. If facts are incomplete or a finding is open, CRM stores only the external IDs, state, action, and short reason, then blocks release.
6. Sales sees the exact evidence request—not a raw similarity score—and supplies documents through the approved path.
7. Compliance resolves findings and, when appropriate, records a scoped human decision.
8. TradeSieve emits a signed `case.state_changed` webhook; CRM verifies it and re-reads the case before releasing the named action.

Acceptance outcome: retrying the original call does not create duplicate cases, and CRM cannot infer clearance from HTTP success alone.

## Journey 2 — Compliance review

1. The reviewer opens a queue ordered by open P0/P1 and business due point.
2. The case view separates verified facts, unknowns, source assertions, rule evaluations, model suggestions, and prior human events.
3. The reviewer resolves each finding with evidence and rationale; the original finding remains immutable.
4. A P0/P1 cannot be closed without required evidence or an authorized, auditable override reason.
5. A release records action, scope, linked business object, source/rule/input versions, approver, timestamp, reason, and expiry.
6. A material source/input/rule change invalidates the relevant release and queues rescreening.

Acceptance outcome: the reviewer can reconstruct why the action was held and why/when it was later released.

## Journey 3 — Analyst using the CLI

1. The analyst validates a synthetic or approved JSON request locally.
2. The CLI sends the same canonical request to the service and prints a human summary.
3. `--json` returns the API response without lossy remapping.
4. Exit status describes command execution/validation errors; it never encodes “legally cleared.”
5. The analyst can fetch a redacted case, list required evidence, and inspect source freshness within granted scopes.

Acceptance outcome: request/result hashes and IDs match the equivalent REST call.

## Journey 4 — Agent-assisted review through MCP

1. An agent discovers a small, permission-filtered tool set.
2. The agent calls `screen_transaction` with structured facts or an opaque approved case handle.
3. The tool returns structured findings, missing evidence, sources, uncertainty, and permitted next actions.
4. The agent may summarize findings or call `request_human_review` with explicit user confirmation.
5. No generally available tool can create a human clearance or export unrestricted case/evidence data.

Acceptance outcome: every tool call records actor/client, arguments hash, schema/protocol version, result ID, and authorization decision.

## Journey 5 — Source update and rescreen

1. A source worker retrieves an official source to quarantine and records retrieval metadata and SHA-256.
2. Validation compares schema, record counts, identifiers, effective dates, and deletions with the last accepted snapshot.
3. Unexpected changes remain quarantined; an operator sees the validation failure.
4. An accepted snapshot becomes immutable and activation emits an event.
5. Impact analysis links changed assertions to open cases and queues targeted rescreening.
6. The old source remains available for replay and rollback.

Acceptance outcome: no source version is silently overwritten and every changed case identifies the triggering snapshot.

## Failure journeys that must be designed first

| Failure | Required behavior |
| --- | --- |
| Control service unavailable at shipment/payment gate | Calling system holds the action; no fail-open fallback |
| Required source stale/unavailable | Return `SOURCE_STALE`/`SOURCE_UNAVAILABLE` and `REVIEW_REQUIRED` or `ESCALATE` under policy |
| Duplicate request/webhook | Idempotent response; no duplicate case, hold, or decision |
| Conflicting documents | Preserve both, create a finding, request human review |
| Prompt injection in a document/source page | Treat content as untrusted evidence; do not execute embedded instructions/tool calls |
| Unauthorized decision attempt | Deny, audit, alert where configured, leave case state unchanged |
| Match service/model unavailable | Continue deterministic controls where safe, mark incomplete coverage, hold rather than infer no match |
