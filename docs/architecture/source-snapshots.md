# Immutable source snapshot boundary

**Status:** TS-202 implements the synthetic immutable domain and parser, authorized
application workflow, private local-volume bytes, PostgreSQL 18.4 migration/repository,
runtime/demo wiring, bounded safe queries, rollback, exact citation resolution, a
narrow demo CLI listing, and generated canonical safe DTO schemas. It adds no source
snapshot REST route, MCP tool, raw export, or real source integration.

## Implemented boundary

The domain keeps four concerns distinct:

| Concern | Authority and invariant |
| --- | --- |
| Raw object metadata/reference | Derived from bounded bytes and retrieval/effective metadata; SHA-256 and object identity are recomputed, never caller flags |
| Parsed snapshot | Frozen records/assertions preserve native JSON Pointer/value, normalized value, parser ID/version, raw reference, record ID, and derived snapshot/hash |
| Validation and diff | Exact schema/count/ID/locator/date checks and ordered semantic record changes; empty data and every unexpected deletion block acceptance |
| Lifecycle and command audit | Append-only retrieve → quarantine → parse → validate → approve → activate, with bounded replay, creator separation, previously-active rollback, and exact authorization/lifecycle audit links |

All datetimes are UTC. Raw bytes, native/normalized values, names, media metadata,
record/assertion counts, canonical snapshot payloads, lifecycle history, and read
limits are bounded. Snapshot and validation hashes are recomputable. A parsed or
validated snapshot has no active effect: only the explicit human activation or
rollback transition changes the active pointer.

Snapshot integrity verification recomputes the snapshot hash/ID, ordered record
keys, ordered assertion IDs, and record/assertion provenance. Verified repository
reads apply the same check, so changing an internal derived ID cannot evade the
semantic content hash.

Semantic record hashes deliberately exclude retrieval and snapshot identity so an
unchanged record in a later raw retrieval is not reported as changed. They retain
native locator/value, normalized value, effective dates, and parser ID/version, so
a parser-version change is material and inspectable.

## Module map

The hash/content helpers and cross-object invariants remain cohesive while the
parser, command workflow, and read boundary are separate application concerns:

| Module | Responsibility |
| --- | --- |
| `tradesieve.domain.source_snapshot` | Immutable artifacts, computed validation/diff, lifecycle fold/projection, command-audit linkage, and shared bounds |
| `tradesieve.application.auth` | Explicit retrieve/parse/validate/approve/activate/rollback deny-default policies |
| `tradesieve.ports.source_snapshot` | Closed synthetic parser inventory, immutable object-store port, and named repository/UoW transaction boundaries |
| `tradesieve.adapters.in_memory_source_snapshot` | Locking copy-on-write reference object store/repository used to prove idempotence, conflicts, corruption detection, and atomic projections |
| `tradesieve.adapters.synthetic_source_parser` | Strict UTF-8, duplicate-key-safe, no-float, bounded JSON parser for the one registered synthetic schema |
| `tradesieve.application.source_snapshot` | Exact authorization binding and ingest, parse, validate, approve, activate, and rollback orchestration |
| `tradesieve.application.source_snapshot_contracts` | Canonical redacted listing/detail/history DTO roots shared one-way by generation and query consumers |
| `tradesieve.application.source_snapshot_query` | Authorized verified reads that materialize the canonical DTOs plus exact accepted-snapshot citation verification for TS-205 |
| `tradesieve.adapters.local_raw_object_store` | Private content-addressed `0700`/`0600` immutable bytes with verified reads |
| `tradesieve.adapters.postgres_source_snapshot` | PostgreSQL immutable artifacts, lifecycle/state/observation projection, audit linkage, and fail-closed replay |

There is no caller-selected parser/plugin seam. The only source-snapshot interface is
the private demo CLI metadata listing; canonical schema registration is not an endpoint.
There is no source-snapshot REST/MCP/raw-export surface, real data, or network retrieval.

## Finite parser and application workflow

The built-in parser accepts exactly `application/json`, strict UTF-8, parser
`synthetic-json-v1` version `1.0.0`, and schema
`tradesieve-synthetic-source-v1`. It rejects BOMs, duplicate keys, floats and
non-finite numbers, unknown fields, unsafe nesting, malformed shapes, and values or
collections outside the shared domain bounds. Source bytes never select code or a
plugin.

`SourceSnapshotService` implements the complete Slice B state path:

1. ingest verifies exact source registration, media binding, byte bounds, immutable
   raw identity, quarantine, and linked command-audit persistence;
2. parse reads verified immutable bytes through the object-store port and persists
   the content-addressed parsed snapshot;
3. validate computes the schema/count/record/diff report against the active accepted
   predecessor and records either `VALIDATED` or `VALIDATION_FAILED`;
4. approve and activate require exact immutable identity, a distinct authorized human,
   and retry-safe lifecycle evidence; and
5. rollback can select only a previously activated, still accepted snapshot and moves
   the active pointer atomically.

All command retries prove semantic idempotence against the stored event and audit.
Conflicting retries, forged authorization objects, moved/missing registrations, parser
failures, and repository or integrity failures return bounded application errors and
cannot create accepted state.

## Safe queries and citation verification

`SourceSnapshotQueryService` authorizes `SOURCE_READ` against the exact configured
tenant/deployment/source-set/source target. Human, service, and agent identities may
read with `source:read`; no mutation role is inferred. Exact registration membership
is required, but an inactive registration remains inspectable for historical evidence.

List, detail, and latest-history results are deterministic and bounded by the shared
512-item query limit. They expose only artifact IDs/hashes, byte length, media/charset,
finite parser/schema versions, counts, UTC times, lifecycle state, validation codes and
hash summaries, and exact deterministic diff identifiers/hashes. They do **not** expose
raw bytes, original filenames, native or normalized values, record/assertion locators,
credentials, licence or contractual text, free-form lifecycle reasons, actor IDs,
command/authorization audit identifiers, or exception bodies. Public queries use only
verified repository metadata; they never read the raw object store and never mutate or
append an audit.

`SourceSnapshotOfficialCitationResolver` is the fail-closed TS-205 bridge. It returns
`True` only when the citation has the exact control tenant and deployment, exact
source-set registration, derived snapshot ID/hash, finite parser/raw bindings, verified
repository and lifecycle integrity, a passing validation report, proof the snapshot was
actually activated, an accepted state (`ACTIVE`, `SUPERSEDED`, or `ROLLED_BACK`), and a
locator matching exactly one record or assertion in that snapshot. Zero or multiple
matches, never-activated `PARSED`/`VALIDATED`/`APPROVED` snapshots, scope mismatches,
malformed values, and dependency/integrity failures return exactly `False`. Historical
activated snapshots remain verifiable after supersession or rollback. This verifies
identity and provenance only; it makes no claim about legal authority, licence,
completeness, or current source readiness.

## Authorization

Read access uses `SOURCE_READ`. `SOURCE_SNAPSHOT_INGEST` retrieves immutable
bytes/metadata and immediately records quarantine on normal success; parse and
validate are separate operations. All three mutation operations
require `source:operate`, a source-operator role, and a named human or service identity.
Approve, activate, and rollback require a human identity, `source:approve`, a
source-approver or compliance-owner role, an exact object grant, and a trusted creator
different from the approver. Agent identities are denied every mutation.

These are application policy primitives, not production grants. Production IdP role
membership, source authority, licences, emergency access, and compliance ownership
remain deployment decisions.

## Persistence and failure boundary

The repository port makes the future database transactions explicit:

- raw metadata + retrieval event + applied command audit;
- immutable parsed snapshot + parsed event + applied command audit;
- other lifecycle event + applied command audit; and
- activation/rollback event + command audit + the TS-201
  `SourceRuntimeObservation` active pointer.

Retrieval metadata and quarantine are deliberately two atomic repository transitions
because the object store and database cannot share a transaction. This is not an
application-visible resting state: a normal successful `SOURCE_SNAPSHOT_INGEST`
workflow must perform both transitions, and any interruption leaves the retrieved
artifact non-parsed, non-approved, and non-active for explicit recovery.

The last group must commit in one PostgreSQL transaction. The active observation must
identify the event snapshot and exact retrieval/effective timestamps. Verified reads
replay bounded lifecycle history and compare it with referenced immutable artifacts,
audits, and the active observation; corruption fails unavailable.

Successful applied and idempotent command audits can be written only by those atomic
repository methods. The standalone audit append accepts only unlinked failures, so it
cannot fabricate a successful lifecycle command. Verified audits bind each success to
the exact authorized ingest/parse/validate/approve/activate/rollback operation and an
applied lifecycle event of the corresponding type and artifact target.

Raw bytes use a separate immutable object-store port with exact put outcomes and
verified reads. A retrieve command may write bytes before the database metadata
transaction. Database failure can therefore leave a safe immutable unreferenced blob,
never a falsely active snapshot. Bounded orphan reconciliation/retention is deferred
to the durable-adapter/operations slices and must not delete a referenced object.

The object-store port intentionally exposes no delete or raw-export method. Missing or
hash-mismatched bytes fail unavailable. The demo uses a private named local volume:
bootstrap has write access, app/worker have read-only access, and migrations do not
mount it. No public raw-object surface exists.

## Deferred acceptance work

Production source selection, network retrieval, licences, role assignments, legal
authority, legal/data coverage, authenticated administration adapters, and bounded
orphan retention remain outside this synthetic proof. Future REST or MCP work must use
the same canonical safe DTOs and application authorization; D3 itself wires neither.
