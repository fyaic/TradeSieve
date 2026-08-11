# OFAC Sanctions List Service connector proof

**Status:** TS-204 connector proof completed; TS-204A now activates both publications in the runtime four-source bundle.
**Observed:** 2026-08-10.
**Authority:** U.S. Department of the Treasury, Office of Foreign Assets Control.

## What this proof establishes

TradeSieve can retrieve the two current OFAC Sanctions List Service (SLS)
comprehensive legacy XML publications through fixed official entry points, reject
untrusted transport/schema changes, preserve source-native facts, build deterministic
snapshots, calculate stable-UID deltas, and reproduce the same snapshot from the same
bytes.

The implementation reads:

- `SDN.XML` from the fixed SLS publication endpoint;
- `CONSOLIDATED.XML` from the fixed SLS publication endpoint; and
- the namespace published by OFAC for the supported legacy XML schema.

On 2026-08-10, an SLS `GET` returned one signed redirect to OFAC's fixed AWS GovCloud
S3 publication host. The transport therefore permits exactly one redirect only when
the scheme, host, publication path, requested filename, finite signed-query fields,
region/service credential suffix, expiry, timestamp and signature shape all match the
closed policy. It rejects every other redirect, a second redirect, compression,
oversize/empty bodies, bad media metadata and non-200 final responses. TLS uses the
pinned project CA bundle; downloaded bytes receive a local SHA-256 regardless of
whether the final S3 response exposes an HTTP `Digest` header.

## Live evidence

The repository probe is:

```bash
uv run python scripts/ofac_sls_live_probe.py
```

Its output is deliberately limited to counts, dates, byte lengths, hashes and replay
status; it emits no listed names, identifiers, addresses or UIDs.

| Publication | Publish date | Entries | Aliases | Identifier facts | Bytes | Raw SHA-256 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| SDN | 2026-08-07 | 19,199 | 24,576 | 53,473 | 28,809,039 | `ac00228a68345e5c0d7174713cf97e5d5a8efe7cec5c2f540ed87106f49f7474` |
| Consolidated non-SDN | 2026-07-27 | 481 | 1,186 | 2,632 | 1,062,621 | `6c9eb74d6060a8c7075dd48054e087cb83c7ccd935dfd4df6700635e12d7c84f` |

Both publications parsed twice to the same content-addressed snapshot identity; a
snapshot-to-replay diff produced zero additions, removals and modifications. These
are time-stamped reachability observations, not claims that the counts remain current.

## Preserved model and matching boundary

The projection retains each OFAC UID, primary name, type, title, remarks, program
codes, strong/weak aliases, addresses, identifier facts and their optional country /
issue / expiry fields, nationality, citizenship, dates and places of birth, and vessel
details. Native locators remain attached to projected assertions.

OFAC states that the numeric UID is the stable database key for a record. TradeSieve
therefore calculates additions, removals and modifications by UID plus canonical
entry hash. It does not treat array position or a display name as identity.

Candidate retrieval is intentionally narrower than parsing:

- only a finite reviewed inventory of strong identifier types is eligible for exact
  normalized lookup;
- descriptive `idType` values such as gender are retained as source evidence but are
  never indexed as strong identifiers;
- an exact normalized primary name or alias is only a review candidate;
- zero candidates never means legal clearance; and
- this connector does not infer OFAC's 50 Percent Rule, ownership, control, program
  effect, licence availability or transaction legality.

## Snapshot and history policy

OFAC provides change/delta files, but its FAQ recommends that database administrators
use the comprehensive files and perform a full refresh. TradeSieve follows that safer
baseline: retrieve a comprehensive publication, preserve its raw bytes, project it,
and compute a deterministic local delta against the last accepted snapshot.

The current proof has no authoritative historical backfill. Its history boundary is
`locally_preserved_snapshots_only`: history starts when an operator begins retaining
verified snapshots. Current publications cannot reconstruct facts removed before that
point. Any later OFAC archive/delta ingestion must identify its own provenance and
must not silently fill that gap.

## Runtime integration evidence and remaining boundary

Migration `20260810_0007` adds source-specific immutable OFAC snapshot, entry,
program, alias, address, identifier, fact and vessel projections. EU FSF, EU Annex I,
OFAC SDN and OFAC Consolidated are now activated atomically. An upgraded 0006 EU-only
pointer is preserved as history but fails closed until a complete four-source refresh.

`screen-active`, authenticated `POST /v1/official-screenings` and the demo CRM now use
the same persisted application service. The result keeps SDN and Consolidated evidence
separate, omits source name/identifier values, and treats any candidate as `RED/HOLD`.
The isolated live gate proved an applied refresh, an idempotent replay, current source
counts, an exact public SOVCOMFLOT candidate carrying `RUSSIA-EO14024`, and identical
CLI/REST bundle evidence.

Still required for a production claim:

1. governed scheduling, monitoring, alerting and affected-case rescreening;
2. named compliance/legal review for source scope, program effects and retention;
3. OFAC 50 Percent Rule and other ownership/control propagation with evidence;
4. fuzzy/transliterated entity resolution and reviewed thresholds;
5. formal tenant/OIDC authorization, durable cases and reviewer decisions; and
6. separately scoped API/MCP exposure and operational rate/performance evidence.

## Primary sources

- [OFAC Sanctions List Service](https://ofac.treasury.gov/sanctions-list-service)
- [OFAC FAQ 90 — comprehensive and delta files](https://ofac.treasury.gov/faqs/90)
- [OFAC FAQ 1031 — UID identity](https://ofac.treasury.gov/faqs/1031)
- [OFAC advanced sanctions-list format FAQ](https://ofac.treasury.gov/sdn-list-data-formats-data-schemas/frequently-asked-questions-on-advanced-sanctions-list-standard)
- [OFAC namespace change notice](https://ofac.treasury.gov/recent-actions/20240507_44)
