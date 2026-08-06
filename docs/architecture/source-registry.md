# Runtime source registry

**Status:** Implemented TS-201 boundary for source governance and runtime
freshness. TS-202 Slice A now defines immutable source objects, validation/diff,
authorization, lifecycle, persistence ports, and in-memory reference semantics;
application/durable/runtime integration remains later slices.

## Purpose and authority

The registry answers two different questions without conflating them:

1. Is a source governed well enough to activate?
2. Is the active source fact usable now?

PostgreSQL stores three separate records:

| Record | Authority |
| --- | --- |
| `source_set_manifest` | Sole authority for the required members of a deployment/source set |
| `source_registry` | Stable source identity, governance, activation state, and freshness policy |
| `source_runtime_observation` | Latest monotonic availability fact and forward-compatible active-snapshot pointer |

A source row does not carry an independent `required` flag. The operational read
model derives that value from the manifest, so two persisted authorities cannot
disagree. A missing or empty manifest fails readiness even when source rows exist.
An expected manifest member that is missing, inactive, stale, or unavailable also
fails readiness. Optional unhealthy registrations remain visible but do not make
the required set unavailable.

Required manifests are sorted, unique, safe-ID arrays capped at 256 members.
Operational listing queries are bounded at 512 source rows and fail closed if that
limit is exceeded; this prevents the private projection from becoming an
unbounded future API contract.

## Governance and activation

Stable deployment, source-set, and source IDs use the same constrained identifier
format. A draft may be saved with incomplete governance, but it cannot be active.
Activation requires:

- source name and owner;
- responsible operator;
- jurisdiction and legal/data scope;
- access method;
- a public-safe licence summary;
- refresh expectation and stale threshold, in whole seconds;
- a stale threshold greater than or equal to the refresh expectation.

Activation refusal returns stable reason codes for every missing or inconsistent
field. Credential values are never stored in the registry: the optional
`credential_secret_ref` accepts only a constrained provider reference such as
`vault:sources/example`. Private contractual constraints may be persisted for
authorized internal use, but neither field appears in the operations-safe read
model.

## Derived runtime status

`CURRENT` is never persisted. The application derives status at query time using
an injected UTC clock:

| Status | Derived condition |
| --- | --- |
| `CURRENT` | Registration is active, availability is `AVAILABLE`, an active snapshot/retrieval time exists, and age is below the stale threshold |
| `STALE` | The same facts exist and `now >= retrieved_at + stale_after` |
| `UNAVAILABLE` | Required runtime facts are missing, availability failed, or an observation/effective time is in the future |
| `QUARANTINED` | Registration is inactive or the latest observation is explicitly quarantined |

An `AVAILABLE` observation is permitted to arrive without a snapshot pointer or
retrieval time so incomplete connector facts can be recorded, but evaluation
deliberately treats it as `UNAVAILABLE`. When present, `retrieved_at` must not be
later than `observed_at`.

Runtime observations are monotonic by `observed_at`. A newer observation is
applied, an exactly equal observation is idempotent, and an older or
equal-but-different observation raises an explicit conflict. The PostgreSQL upsert
enforces the ordering atomically with
`EXCLUDED.observed_at > source_runtime_observation.observed_at`.

The internal readiness result includes deterministic issues sorted by source ID,
with each source's exact status. Screening policy can therefore fail closed and
explain which required source is unhealthy. The unauthenticated health endpoint
exposes only generic `OK` or `UNAVAILABLE` states and never emits source IDs.

## Operator projection

The private operations command returns one deterministic JSON envelope:

```bash
docker compose run --rm --no-deps app \
  python -m tradesieve.manage list-sources
```

The envelope contains set readiness, sorted issues, and sorted safe source
records. It exits `0` when the required set is current and `2` otherwise. It
includes public governance and active snapshot metadata, but excludes credential
references and private contractual text. TS-201 does not add an unauthenticated
REST registry endpoint; a future authenticated adapter can reuse the same
application query DTO.

`bootstrap-demo` is an explicitly demo-only reseed/refresh operation. Each run
upserts the synthetic manifest and governance record and records a new current
synthetic observation. The monotonic observation guard still applies: a future or
otherwise newer stored observation is never silently overwritten, and the command
fails with an explicit conflict. The same command delegates rule setup to the
authorized TS-205 rule-bundle services; it does not insert a rule coverage marker.
Production configuration rejects demo bootstrap.

## Snapshot boundary

The `active_snapshot_id`, retrieval time, and effective time are trusted runtime
facts needed for freshness and forward compatibility. TS-201 does not claim that
this pointer is an immutable snapshot ledger. The accepted
[TS-202 snapshot boundary](source-snapshots.md) owns raw-object retention, hashes,
parsing and validation, diff, approval, atomic activation history, rollback, and
audit evidence. Slice A's repository port requires an activation/rollback event and
this observation to update in one future PostgreSQL transaction.
