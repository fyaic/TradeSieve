# Immutable source snapshot boundary

**Status:** TS-202 Slice A implements domain invariants, authorization policy,
ports, and in-memory reference adapters only. The finite synthetic parser,
application commands, PostgreSQL/object-volume adapters, migration, runtime wiring,
and demo commands remain later TS-202 slices.

## Implemented boundary

The Slice A domain keeps four concerns distinct:

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

Slice A remains in one cohesive domain module to keep the hash/content helpers and
cross-object invariants non-circular:

| Module | Responsibility |
| --- | --- |
| `tradesieve.domain.source_snapshot` | Immutable artifacts, computed validation/diff, lifecycle fold/projection, command-audit linkage, and shared bounds |
| `tradesieve.application.auth` | Explicit retrieve/parse/validate/approve/activate/rollback deny-default policies |
| `tradesieve.ports.source_snapshot` | Closed synthetic parser inventory, immutable object-store port, and named repository/UoW transaction boundaries |
| `tradesieve.adapters.in_memory_source_snapshot` | Locking copy-on-write reference object store/repository used to prove idempotence, conflicts, corruption detection, and atomic projections |

No application workflow or parser implementation lives in these modules. There is
no caller-selected parser/plugin seam, filesystem adapter, PostgreSQL table, migration,
REST/MCP raw export, real data, or network retrieval in Slice A.

## Authorization

Read access continues to use `SOURCE_READ`. `SOURCE_SNAPSHOT_INGEST` names the future
application workflow that retrieves immutable bytes/metadata and immediately records
quarantine on normal success; parse and validate are separate operations. All three
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
verified reads. A future retrieve command may write bytes before the database metadata
transaction. Database failure can therefore leave a safe immutable unreferenced blob,
never a falsely active snapshot. Bounded orphan reconciliation/retention is deferred
to the durable-adapter/operations slices and must not delete a referenced object.

The object-store port intentionally exposes no delete or raw-export method. Missing or
hash-mismatched bytes fail unavailable. The reference adapter is in-memory only; the
accepted later demo design uses a private local volume and no public raw-object surface.

## Deferred acceptance work

Later TS-202 slices own the one finite built-in synthetic JSON parser and application
commands, PostgreSQL migration/repository constraints, private local-volume storage,
runtime/demo/CLI wiring, and Compose evidence. Production source choices, licences,
role assignments, and legal/data coverage remain outside this synthetic proof.
