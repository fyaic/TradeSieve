# Four-source official refresh and screening technical preview

**Status:** persisted CLI and authenticated REST technical preview; not production clearance.

TradeSieve has two deliberately separate official-source workflows. `refresh-official-sources` retrieves, verifies, projects, and atomically activates EU FSF, EU Annex I, OFAC SDN and OFAC Consolidated in PostgreSQL. `screen-active` and `POST /v1/official-screenings` use only that fresh four-source bundle. `screen-official` remains a stateless diagnostic that retrieves all four publications for every invocation.

## What it actually does

The refresh workflow retrieves all four publications before it can advance the active pointer:

| Control | Official source | Implemented behavior |
| --- | --- | --- |
| EU financial sanctions | data.europa.eu dataset metadata → European Commission FSF XML 1.1 distribution | fixed URL/TLS policy; bounded gzip or identity transfer; exact decompressed-byte hash; finite XML parser; immutable entity/alias/identifier rows; strong typed identifier exact match; exact normalized-alias candidates; source-native locator |
| EU dual-use Annex I | Publications Office CELLAR item for CELEX `32025R2003` | bounded TLS retrieval, exact response length, ZIP safety, Formex schema/title checks, 384 unique entries across categories 0–9, immutable row projection, explicit-code lookup and missing-fact assessment |
| OFAC SDN | Treasury Sanctions List Service comprehensive `SDN.XML` | fixed SLS entrypoint; one strictly checked GovCloud signed redirect; bounded XML; stable UID and row projection; finite exact identifier types; exact normalized primary-name/alias candidates; source program and native locator |
| OFAC Consolidated | Treasury Sanctions List Service comprehensive `CONSOLIDATED.XML` | same transport/parser/integrity boundary, retained as a distinct list kind and snapshot rather than merged into SDN |
| Technical assertions | Hand-reviewed bundle bound to the exact official `3A001` entry hash | deterministic ADC `3A001.a.5.a`/`.a.14` and cell `3A001.e.1` comparisons; canonical units, strict boundaries, evidence refs, explicit incomplete/unsupported/source-drift states; never classification or clearance |
| BIS Common High Priority Items guidance | Versioned 50-item HS-6 fixture checked against the official BIS publication on 2026-08-11 | exact candidate lookup, tier, source URL/hash and named evidence gaps; a hit requests enhanced due diligence and is never classification, prohibition or clearance |
| Active source bundle | PostgreSQL migration `20260810_0007` | four content-addressed raw objects and typed row projections, immutable activation event, atomic active pointer, idempotent repeat activation, full reconstruction/re-hash on read, 48-hour freshness gate |

The dual-use source is the current 2025 delegated update known on 2026-08-10. Unlike the daily sanctions catalogue discovery, its CELEX/CELLAR version is pinned. A later delegated regulation must be discovered, reviewed, tested, and activated before this connector can claim the new version.

## Input

Pass a JSON file or `-` for standard input. The reader is limited to 1 MiB, requires UTF-8, rejects duplicate keys and validates a closed schema.

A runnable public-source candidate example is committed at [`examples/requests/official-screening.json`](../../examples/requests/official-screening.json). It contains no customer transaction data.

```json
{
  "schema_version": "1.0.0",
  "party_identifiers": [
    {"type": "regnumber", "value": "example-registration", "country": "RU"},
    {"type": "imo", "value": "example-imo", "country": null}
  ],
  "party_names": [
    {"name": "Example International Logistics LLC"}
  ],
  "goods": {
    "hs_code": "854231",
    "annex_i_code": "3A001",
    "classification_verified": true,
    "technical_specification_available": true,
    "product_family": "ADC_INTEGRATED_CIRCUIT",
    "technical_facts": [
      {"fact_id": "resolution_bits", "unit": "BITS", "numeric_value": "12", "evidence_ref": "datasheet-1", "verified": true},
      {"fact_id": "sample_rate_msps", "unit": "MSPS", "numeric_value": "401", "evidence_ref": "datasheet-1", "verified": true},
      {"fact_id": "stores_or_processes_digitised_data", "unit": "BOOLEAN", "boolean_value": false, "evidence_ref": "datasheet-1", "verified": true}
    ]
  }
}
```

Supported request identifier types are the finite EU FSF inventory: `birthcert`, `drivinglicence`, `electionid`, `euvat`, `fiscalcode`, `id`, `imo`, `nationcert`, `other`, `passport`, `regnumber`, `residentperm`, `ssn`, `swiftbic`, `taxid`, `tradelic`, `travelcardid`, and `unssn`. A reviewed subset (`id`, `imo`, `passport`, `regnumber`, `swiftbic`, `taxid`) is also mapped to finite strong OFAC identifier types. Other OFAC `idType` facts are retained but never treated as strong merely because they occur in the XML.

At least one `party_identifiers` or `party_names` entry is required. Names are Unicode NFKC-normalized, whitespace-collapsed, case-folded, and compared separately against EU FSF and both OFAC list projections. A name result is only a candidate: any candidate/ambiguity or EU weak-alias review state produces `RED` + `HOLD` for authorised human review. The current implementation does not perform fuzzy similarity, transliteration, token reordering, ownership/control propagation, OFAC 50 Percent Rule evaluation, or entity resolution across corporate registries.

`hs_code` accepts a documented 6-, 8- or 10-digit candidate and compares only its HS-6 prefix with the versioned BIS CHPL fixture. A hit returns `CANDIDATE`, the tier and named missing facts and causes `YELLOW/REQUEST_EVIDENCE` unless a stronger hold already applies. Absence from CHPL is not a negative legal conclusion.

`classification_verified` records whether a qualified classification step supplied the Annex I candidate. `technical_specification_available` records document availability. `product_family` selects only a finite supported rule family; every `technical_facts` item supplies exactly one typed value, canonical unit, evidence reference, and verification flag. TradeSieve does not infer an Annex I code from an HS/CN/TARIC code or use free text/LLMs to create executable legal rules.

The initial bundle supports `ADC_INTEGRATED_CIRCUIT` and `ELECTROCHEMICAL_CELL`. Its result is bound to CELEX `32025R2003`, the exact official `3A001` entry content hash, a rule-bundle hash, and an individual rule ID. An active-source hash change produces `SOURCE_MISMATCH`; missing/unverified facts produce `INCOMPLETE`. `NOT_MATCHED` means only that the evaluated branch did not meet its threshold and is never clearance.

## Refresh and activate

With the reference PostgreSQL stack running:

```bash
docker compose run --rm --no-deps app \
  python -m tradesieve.manage refresh-official-sources
```

The output contains only bundle/snapshot hashes, safe counts, activation time, and `APPLIED` or `IDEMPOTENT`. Raw bytes, aliases, identifiers, control text, source URLs containing access tokens, and database details are not printed. A failed refresh never changes the active pointer.

Migration from 0006 preserves old EU raw objects, projections and activation history. An upgraded EU-only active pointer is intentionally unavailable until the first successful four-source refresh; the service never represents that partial history as current OFAC coverage.

## Screen the active bundle

```bash
docker compose run --rm --no-deps app \
  python -m tradesieve.manage screen-active --request screening.json
```

`screen-active` fails with exit `2` if no bundle exists, any persisted row/hash/count/relationship is corrupt, a source retrieval or activation timestamp is in the future, or any source/bundle age exceeds 48 hours.

## Authenticated HTTP

The demo token below is local-only. The service stores only its SHA-256 digest; production configuration rejects the demo digest.

```bash
curl --fail-with-body \
  -H 'Authorization: Bearer local_demo_only_official_screening_token' \
  -H 'Content-Type: application/json' \
  --data-binary @screening.json \
  http://127.0.0.1:8080/v1/official-screenings
```

Authentication runs before business-body parsing. The endpoint requires JSON and a `Content-Length` between 1 byte and 1 MiB. Invalid credentials return `401`; invalid contracts return a generic `422` without echoing request values; an absent/stale/corrupt source or database failure returns a generic `503` and must be treated as a hold.

This endpoint is an executable vertical slice, not the final tenant-scoped and idempotent `POST /v1/screenings` case API. It currently stores neither the request nor a screening/case record.

The implemented request/result and Bearer security scheme are published in the versioned [OpenAPI artifact](../../api/openapi/tradesieve.v1.json) and [shared JSON Schema registry](../../api/schemas/tradesieve.contracts.v1.json).

## Stateless live diagnostic

```bash
uv sync --locked --all-groups
uv run tradesieve-manage screen-official --request screening.json
```

To keep identifiers out of shell history:

```bash
uv run tradesieve-manage screen-official --request - < screening.json
```

Exit codes:

| Code | Meaning |
| --- | --- |
| `0` | Refresh/active read/stateless retrieval and assessment completed |
| `2` | Required source, database, parser, persisted integrity, or freshness was unavailable/invalid; caller must hold |
| `3` | Request file/JSON/schema was invalid |

## Reproduce the isolated live acceptance

With network access to the official publications and Docker available:

```bash
./scripts/test_official_screening_live.sh
```

The gate uses an isolated project and temporary evidence directory. It runs migration 0007, performs an applied refresh plus an idempotent replay, screens the committed public candidate through CLI and authenticated REST, invokes the fixed CRM official-source route, and proves all three interfaces bind the same four-source bundle. Output is reduced to counts and booleans; containers, networks, volumes and temporary evidence are removed.

## Output and decisions

The JSON output contains only evidence needed to reproduce the source assertion: source generation/publish/effective dates, file/snapshot hashes, EU reference or OFAC UID/list kind/program, assertion UID/hash, source-native locator, technical rule/source identity, normalized comparisons, status, missing facts, and the aggregate business action. It deliberately does not echo the queried identifier value, source name value, address, remarks or full Annex I legal text.

- An EU/OFAC exact sanctions identifier candidate, ambiguous/unusable EU identifier, any normalized name candidate, or Annex I entry produces `RED` + `HOLD`.
- A missing HS candidate, classification or technical specification produces `YELLOW` + `REQUEST_EVIDENCE`.
- A CHPL HS-6 candidate produces at least `YELLOW` + `REQUEST_EVIDENCE`; it does not prove that the goods are controlled.
- No exact sanctions identifier and no Annex I entry produces at most `GREEN_CANDIDATE` + `MONITOR`; this is not clearance.

Only an authorised human may clear or block a named transaction. OFAC list membership alone does not implement the 50 Percent Rule or determine program/legal effect. Fuzzy/transliterated matching, ownership/control propagation, destination/end-use/catch-all rules, Russia Regulation `833/2014` goods annexes, technical rules outside the bounded `3A001` assertions, definitive classification, screening/case records, tenant/OIDC authorization, MCP, and webhooks remain outside this slice.

## Safe evaluation data

Use synthetic or specifically approved test identifiers. Although source records are public official designations, do not commit customer, shipment, payment, or investigation data to the repository. Refresh persists official source bytes/projections only; current CLI/REST assessment does not persist its request or result.
