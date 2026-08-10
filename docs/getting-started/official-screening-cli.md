# Official EU source refresh and screening technical preview

**Status:** persisted CLI and authenticated REST technical preview; not production clearance.

TradeSieve now has two deliberately separate official-source workflows. `refresh-official-sources` retrieves, verifies, projects, and atomically activates EU FSF plus EU Annex I in PostgreSQL. `screen-active` and `POST /v1/official-screenings` use only that fresh active bundle. `screen-official` remains a stateless diagnostic that retrieves both sources for every invocation.

## What it actually does

Each invocation retrieves both sources afresh:

| Control | Official source | Implemented behavior |
| --- | --- | --- |
| EU financial sanctions | data.europa.eu dataset metadata → European Commission FSF XML 1.1 distribution | fixed URL/TLS policy; bounded gzip or identity transfer; exact decompressed-byte hash; finite XML parser; immutable entity/alias/identifier rows; strong typed identifier exact match; exact normalized-alias candidates; source-native locator |
| EU dual-use Annex I | Publications Office CELLAR item for CELEX `32025R2003` | bounded TLS retrieval, exact response length, ZIP safety, Formex schema/title checks, 384 unique entries across categories 0–9, immutable row projection, explicit-code lookup and missing-fact assessment |
| Technical assertions | Hand-reviewed bundle bound to the exact official `3A001` entry hash | deterministic ADC `3A001.a.5.a`/`.a.14` and cell `3A001.e.1` comparisons; canonical units, strict boundaries, evidence refs, explicit incomplete/unsupported/source-drift states; never classification or clearance |
| Active source bundle | PostgreSQL migration `20260810_0006` | content-addressed raw bytes, projection hashes/counts, immutable activation event, atomic active pointer, idempotent repeat activation, full re-hash on read, 48-hour freshness gate |

The dual-use source is the current 2025 delegated update known on 2026-08-10. Unlike the daily sanctions catalogue discovery, its CELEX/CELLAR version is pinned. A later delegated regulation must be discovered, reviewed, tested, and activated before this connector can claim the new version.

## Input

Pass a JSON file or `-` for standard input. The reader is limited to 1 MiB, requires UTF-8, rejects duplicate keys and validates a closed schema.

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

Supported FSF identifier types are the finite source inventory: `birthcert`, `drivinglicence`, `electionid`, `euvat`, `fiscalcode`, `id`, `imo`, `nationcert`, `other`, `passport`, `regnumber`, `residentperm`, `ssn`, `swiftbic`, `taxid`, `tradelic`, `travelcardid`, and `unssn`.

At least one `party_identifiers` or `party_names` entry is required. Names are Unicode NFKC-normalized, whitespace-collapsed, case-folded, and compared against official FSF aliases. A name result is only a candidate: `CANDIDATE`, `AMBIGUOUS`, or weak-alias `REVIEW_REQUIRED` always produces `RED` + `HOLD` for authorised human review. The current implementation does not perform fuzzy similarity, transliteration, token reordering, ownership/control propagation, or entity-resolution across corporate registries.

`classification_verified` records whether a qualified classification step supplied the Annex I candidate. `technical_specification_available` records document availability. `product_family` selects only a finite supported rule family; every `technical_facts` item supplies exactly one typed value, canonical unit, evidence reference, and verification flag. TradeSieve does not infer an Annex I code from an HS/CN/TARIC code or use free text/LLMs to create executable legal rules.

The initial bundle supports `ADC_INTEGRATED_CIRCUIT` and `ELECTROCHEMICAL_CELL`. Its result is bound to CELEX `32025R2003`, the exact official `3A001` entry content hash, a rule-bundle hash, and an individual rule ID. An active-source hash change produces `SOURCE_MISMATCH`; missing/unverified facts produce `INCOMPLETE`. `NOT_MATCHED` means only that the evaluated branch did not meet its threshold and is never clearance.

## Refresh and activate

With the reference PostgreSQL stack running:

```bash
docker compose run --rm --no-deps app \
  python -m tradesieve.manage refresh-official-sources
```

The output contains only bundle/snapshot hashes, safe counts, activation time, and `APPLIED` or `IDEMPOTENT`. Raw bytes, aliases, identifiers, control text, source URLs containing access tokens, and database details are not printed. A failed refresh never changes the active pointer.

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

## Output and decisions

The JSON output contains only evidence needed to reproduce the source assertion: source generation/effective dates, file/snapshot hashes, EU reference, identifier assertion hash, source-native locator, technical rule/source identity, normalized comparisons, status, missing facts, and the aggregate business action. It deliberately does not echo the queried identifier value or full Annex I legal text.

- An exact usable sanctions identifier, ambiguous identifier, unusable historical identifier, any normalized name candidate, or Annex I entry produces `RED` + `HOLD`.
- A missing classification or technical specification produces `YELLOW` + `REQUEST_EVIDENCE`.
- No exact sanctions identifier and no Annex I entry produces at most `GREEN_CANDIDATE` + `MONITOR`; this is not clearance.

Only an authorised human may clear or block a named transaction. Fuzzy/transliterated matching, ownership/control propagation, destination/end-use/catch-all rules, Russia Regulation `833/2014` goods annexes, technical rules outside the bounded `3A001` assertions, definitive classification, screening/case records, tenant/OIDC authorization, MCP, and webhooks remain outside this slice.

## Safe evaluation data

Use synthetic or specifically approved test identifiers. Although source records are public official designations, do not commit customer, shipment, payment, or investigation data to the repository. Refresh persists official source bytes/projections only; current CLI/REST assessment does not persist its request or result.
