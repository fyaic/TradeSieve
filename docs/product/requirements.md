# Product requirements

**Status:** Phase 1 baseline; detailed design may clarify but must not weaken safety invariants
**Priority convention:** `MUST` = Phase 1 or invariant, `SHOULD` = Phase 1 if capacity permits, `LATER` = explicitly outside Phase 1.

## Product invariants

1. No model, matcher, rule, source connector, CLI, MCP tool, or ordinary API client may create `HUMAN_CLEARED`.
2. Automation may hold or escalate but may not create a final `HUMAN_BLOCKED`/`CLOSED_NO_ACTION` business disposition or terminate a relationship.
3. Missing material facts never produce a low-risk default; they produce `INCOMPLETE` or `REVIEW_REQUIRED`.
4. A successful HTTP/tool/command execution is not a clearance decision.
5. Source-native facts, normalized facts, deterministic evaluations, model suggestions, and human decisions remain distinct and attributable.
6. Every material result is replayable from immutable input, source, rule, schema, and model versions.
7. CRM/OMS/payment systems enforce the returned business action but do not embed sanctions/export-control logic.
8. Production/private business data does not leave the approved trust boundary by default.

## Functional requirements

### Intake and integration

| ID | Priority | Requirement | Acceptance signal |
| --- | --- | --- | --- |
| FR-INT-001 | MUST | Accept one canonical transaction screening request containing business action, parties/roles, goods, route, end use, documents, payment facts, legal nexus, and external references. | Request validates against the published schema and returns typed field errors. |
| FR-INT-002 | MUST | Accept an idempotency key and caller correlation ID for every mutation. | Same caller/key/body returns the same result; changed body with reused key is rejected. |
| FR-INT-003 | MUST | Support synchronous pre-action screening for quote/order/booking/shipment/payment gates. | CRM example can block using the returned `business_action` and case ID. |
| FR-INT-004 | MUST | Publish signed, retryable state-change webhooks with event IDs and schema versions. | Duplicate delivery does not duplicate downstream actions; signature verification example passes. |
| FR-INT-005 | SHOULD | Accept bounded batch requests through CLI using the same item schema. | Batch output preserves an item-level result/error and correlation ID. |
| FR-INT-006 | LATER | Ship vendor-specific CRM/OMS plugins. | Deferred until the real system/event inventory is complete. |

### Source and rule lifecycle

| ID | Priority | Requirement | Acceptance signal |
| --- | --- | --- | --- |
| FR-SRC-001 | MUST | Register source owner, jurisdiction, legal/data scope, access method, licence constraints, refresh expectation, and responsible operator. | Registry rejects activation without mandatory governance fields. |
| FR-SRC-002 | MUST | Preserve each retrieved raw object with retrieval/effective metadata and SHA-256; never overwrite an accepted snapshot. | Two updates create two immutable snapshot IDs and hashes. |
| FR-SRC-003 | MUST | Quarantine, parse, validate, diff, approve, activate, roll back, and audit a source snapshot. | Unexpected deletion/count/schema tests prevent activation. |
| FR-SRC-004 | MUST | Attach source-native locator/value and parser version to every normalized assertion. | Finding can link to the exact snapshot record/field. |
| FR-SRC-005 | MUST | Mark source freshness and expose stale/unavailable required sources to screening. | Screening cannot silently omit a required stale source. |
| FR-SRC-006 | MUST | Version deterministic rules with source/legal basis, scope, effective dates, owner, tests, and activation state. | Result identifies every evaluated rule version. |
| FR-SRC-007 | SHOULD | Calculate changed assertions and target affected open cases for rescreening. | Activating a test delta queues only linked cases. |
| FR-SRC-008 | LATER | Guarantee complete legal as-of reconstruction for all regimes. | Requires proven historic data/legal-text coverage. |

### Party and ownership screening

| ID | Priority | Requirement | Acceptance signal |
| --- | --- | --- | --- |
| FR-PTY-001 | MUST | Screen every configured party role, including known owners/controllers, at defined workflow gates. | Omitted required roles create a missing-evidence finding. |
| FR-PTY-002 | MUST | Normalize aliases, scripts/transliterations, organization suffixes, identifiers, dates, countries, and addresses without discarding source-native values. | Chinese/Cyrillic/Latin fixtures remain traceable to original values. |
| FR-PTY-003 | MUST | Prefer strong identifier evidence, then multi-field candidate scoring; never treat name similarity alone as disposition. | Result exposes matched fields, conflicts, score components, and uncertainty. |
| FR-PTY-004 | MUST | Represent ownership/control relationships with provenance, percentage where known, validity, and reviewer status. | Unverified inferred edges cannot trigger a verified ownership fact. |
| FR-PTY-005 | MUST | Benchmark matcher versions/thresholds before activation and record the active version. | CI/release evidence includes fixture recall, precision, calibration, and review load. |
| FR-PTY-006 | SHOULD | Support internal watchlists with separate provenance and policy effects. | Internal and official facts are distinguishable in output. |

### Goods, route, end-use, document, and payment controls

| ID | Priority | Requirement | Acceptance signal |
| --- | --- | --- | --- |
| FR-GDS-001 | MUST | Collect commercial description, manufacturer, model/part number, technical specification availability, software/firmware, origin, proposed HS/CN/TARIC, quantity/value, and end use. | Missing material fields produce named evidence requests. |
| FR-GDS-002 | MUST | Treat HS/CN/TARIC and sensitive-goods lists as candidates only. | No code/category alone creates legal classification or clearance. |
| FR-GDS-003 | MUST | Compare known technical facts with versioned control assertions and expose unmatched required parameters. | Result distinguishes confirmed parameter match from incomplete specification. |
| FR-TXN-001 | MUST | Validate origin, loading/discharge, transit, destination, installation/use location, end user, carrier/vessel, payer/payee/banks, and commercial rationale as applicable. | Route/payment omissions or contradictions create findings. |
| FR-TXN-002 | MUST | Compare facts across order, invoice, packing list, transport, payment, technical, end-user, and website evidence references. | Conflicting values are preserved and linked to a finding. |
| FR-TXN-003 | MUST | Represent affected activity explicitly: sale, supply, export, transfer, brokering, technical assistance, financing, transport, transit, quote, booking, shipment, or payment. | Decision scope names the exact proposed action. |
| FR-TXN-004 | SHOULD | Provide configurable anti-circumvention red flags with cited source/policy basis. | A red flag is explainable and cannot be silently overridden. |
| FR-GDS-004 | LATER | Automatically assign definitive export-control classification. | Remains a qualified technical/human process. |

### Findings, cases, and human review

| ID | Priority | Requirement | Acceptance signal |
| --- | --- | --- | --- |
| FR-CAS-001 | MUST | Create a stable screening ID and a case when any finding/review workflow exists. | Identical idempotent retry returns the same IDs. |
| FR-CAS-002 | MUST | Store each finding as structured priority, verified fact/unknown, evidence, legal/policy basis, assessment, required action, owner, due point, and status. | Evidence-first case view contains no unexplained score-only finding. |
| FR-CAS-003 | MUST | Calculate `ESCALATE` for open P0, otherwise `INCOMPLETE`/`REVIEW_REQUIRED` for missing material facts/open P1; automation may return only a green candidate when no P0/P1 remains. | State-machine tests cover every transition and forbidden path. |
| FR-CAS-004 | MUST | Allow authorized users to submit evidence and resolve findings through append-only events. | Original evidence/finding remains unchanged and event history is complete. |
| FR-CAS-005 | MUST | Restrict final human dispositions (`HUMAN_CLEARED`, `HUMAN_BLOCKED`, `CLOSED_NO_ACTION`) to designated reviewer authority and record action, business object, evidence, rationale, versions, reviewer, and time; clearance also requires expiry. | API/CLI/MCP negative tests cannot bypass role, state, completeness, and four-eyes checks. |
| FR-CAS-006 | MUST | Invalidate or re-review a decision after relevant input/source/rule/model change or expiry. | Material test change moves the case out of effective clearance. |
| FR-CAS-007 | MUST | Enforce segregation of duties for configured high-risk actions and overrides. | User who submitted the case cannot self-approve when four-eyes policy applies. |
| FR-CAS-008 | SHOULD | Provide a minimal reviewer queue and case page. | Reviewer completes the golden path without direct database/CLI access. |

### Interface parity

| ID | Priority | Requirement | Acceptance signal |
| --- | --- | --- | --- |
| FR-API-001 | MUST | Publish a versioned OpenAPI contract and interactive HTTP documentation. | Contract lint and compatibility checks pass in CI. |
| FR-CLI-001 | MUST | Provide a scriptable CLI that calls the service, reads files/stdin, supports JSON output, and separates stdout/stderr. | Golden request result equals REST result after transport metadata is removed. |
| FR-MCP-001 | MUST | Provide structured read/screen/explain/request-review MCP tools over the same application service and authorization. | Tool schemas derive from or test against canonical schemas. |
| FR-MCP-002 | MUST | Do not expose general-purpose human-clearance, unrestricted search, raw-source bulk export, or arbitrary file/network tools. | Tool inventory and authorization tests prove absence/denial. |
| FR-EVT-001 | MUST | Version event envelopes separately from source/rule/model versions. | Consumers can reject unsupported major versions and deduplicate event IDs. |

### Audit and administration

| ID | Priority | Requirement | Acceptance signal |
| --- | --- | --- | --- |
| FR-AUD-001 | MUST | Create append-only audit events for source, rule, screening, finding, evidence, decision, authz, and integration changes. | Golden case exports a chronological, hashable audit stream. |
| FR-AUD-002 | MUST | Record actor/client, request/result hashes, schema/source/rule/model versions, timestamp, correlation, and authorization outcome. | Required fields are present on all material event types. |
| FR-ADM-001 | MUST | Expose health/readiness, active source/rule versions, and source freshness without leaking sensitive case data. | Operations can distinguish service health from screening coverage. |
| FR-ADM-002 | SHOULD | Export a redacted evidence pack for authorized audit/review. | Export follows the required decision-first order and data minimization rules. |

## Non-functional requirements

Phase 1 targets are reference-environment acceptance thresholds, not production SLAs.

| ID | Area | Phase 1 requirement |
| --- | --- | --- |
| NFR-001 | Availability behavior | Business gates fail closed on timeout/unavailable/stale-required-source conditions; health success alone is not coverage success. |
| NFR-002 | Latency | On the documented reference dataset/hardware, cached single-transaction screening p95 ≤ 3 seconds excluding asynchronous document extraction; benchmark is published. |
| NFR-003 | Determinism | Same canonical input and version set produce the same deterministic findings and decision ID; nondeterministic model suggestions are separately versioned. |
| NFR-004 | Security | TLS in transit; encrypted storage where deployed; external IdP/OIDC integration; least-privilege scopes; secrets outside Git/images; deny-by-default authorization. |
| NFR-005 | Privacy | Data minimization, purpose/retention fields, redaction in logs/telemetry, approved-region processing, and documented DPIA/Article 22 assessment before live personal data. |
| NFR-006 | Audit | Material records are append-only at application level, timestamped in UTC, exportable, and protected from ordinary update/delete roles. |
| NFR-007 | Portability | MVP runs using documented Docker Compose on a developer machine and supports private-network/container deployment without required public SaaS calls. |
| NFR-008 | Recovery | Automated database backup and one tested restore in the release evidence; raw source/evidence objects retain hashes and recoverable locations. |
| NFR-009 | Observability | Structured redacted logs, request/correlation IDs, metrics, and traces; no party names, document bodies, tokens, or payment details in default telemetry. |
| NFR-010 | Compatibility | Versioned REST path and schemas; additive minor changes; breaking changes require a new major version and migration note. |
| NFR-011 | Supply chain | Pinned direct dependencies, lockfile, image digest/SBOM, vulnerability scan, signed release provenance when CI/platform supports it. |
| NFR-012 | Accessibility | Reviewer console keyboard usable with semantic labels and visible risk state not conveyed by color alone. |
| NFR-013 | Internationalization | Unicode throughout; original script preserved; UI/output supports Chinese and English labels without changing machine enums. |
| NFR-014 | Testability | Synthetic fixtures cover positive, negative, ambiguous, incomplete, cross-script, ownership, stale-source, authorization, and idempotency cases. |

## Roles and permissions

| Scope | Integrator | Analyst | Reviewer | Source operator | Administrator | General agent |
| --- | --- | --- | --- | --- | --- | --- |
| Submit screening | Yes | Yes | Yes | No | Yes | Bounded |
| Read own/allowed case | Yes | Yes | Yes | No | Yes | Bounded/redacted |
| Submit evidence | Bounded | Yes | Yes | No | Yes | Confirmation-gated |
| Request review | Yes | Yes | Yes | No | Yes | Confirmation-gated |
| Resolve finding | No | No | Yes | No | Yes | No |
| Record final human disposition | No | No | Authorized reviewer only | No | By explicit role only | No |
| Activate source/rule | No | No | Compliance approval role | Source operator plus approval | Yes | No |
| Export bulk data | No | No | Bounded | Bounded source only | Explicitly authorized | No |

## Traceability to Phase 1 epics

| Epic | Requirements |
| --- | --- |
| E1 Walking skeleton and distribution | NFR-007, NFR-009, NFR-011 |
| E2 Source provenance | FR-SRC-001–007, FR-ADM-001 |
| E3 Screening and domain | FR-PTY-001–005, FR-GDS-001–003, FR-TXN-001–003 |
| E4 Case and human review | FR-CAS-001–008, FR-AUD-001–002 |
| E5 Interface parity | FR-INT-001–005, FR-API-001, FR-CLI-001, FR-MCP-001–002, FR-EVT-001 |
| E6 Integration and release | NFR-001–014 plus end-to-end acceptance |

## Decisions still requiring named owners

1. Initial legal nexuses, Member States, regimes, and bank/customer policies.
2. Required production source licences and historic availability.
3. Real CRM/OMS/payment events, field availability, and enforcement behavior.
4. Reviewer role membership, four-eyes triggers, and maximum decision expiry.
5. Reference dataset scale and accepted matching error/workload thresholds.
6. Deployment region, identity provider, retention, privacy, and security owners.
