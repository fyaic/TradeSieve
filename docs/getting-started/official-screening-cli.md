# Official EU source screening CLI technical preview

**Status:** implemented technical preview; not a production screening endpoint.

`tradesieve-manage screen-official` is the first executable TradeSieve path that uses current official source bytes rather than repository fixtures. It exists to prove the source, parser, evidence, and conservative-assessment contracts before those contracts are placed behind persisted activation, REST, and MCP adapters.

## What it actually does

Each invocation retrieves both sources afresh:

| Control | Official source | Implemented behavior |
| --- | --- | --- |
| EU financial sanctions | data.europa.eu dataset metadata → European Commission FSF XML 1.1 distribution | bounded TLS retrieval, fixed official URL policy, content hash, finite XML parser, strong typed identifier exact match, ambiguity/unusable-identifier handling, source-native locator |
| EU dual-use Annex I | Publications Office CELLAR item for CELEX `32025R2003` | bounded TLS retrieval, ZIP safety checks, Formex schema/title checks, 384 unique control entries across categories 0–9, explicit-code lookup, technical/classification missing-fact assessment |

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
  "goods": {
    "annex_i_code": "3A001",
    "classification_verified": true,
    "technical_specification_available": true
  }
}
```

Supported FSF identifier types are the finite source inventory: `birthcert`, `drivinglicence`, `electionid`, `euvat`, `fiscalcode`, `id`, `imo`, `nationcert`, `other`, `passport`, `regnumber`, `residentperm`, `ssn`, `swiftbic`, `taxid`, `tradelic`, `travelcardid`, and `unssn`.

`classification_verified` records whether a qualified classification step supplied the Annex I candidate. `technical_specification_available` records only document availability; it does not assert that every legal threshold in the entry has been satisfied. TradeSieve does not infer an Annex I code from an HS/CN/TARIC code.

## Run

```bash
uv sync --locked --all-groups
uv run tradesieve-manage screen-official --request screening.json
```

To keep the identifier out of shell history:

```bash
uv run tradesieve-manage screen-official --request - < screening.json
```

Exit codes:

| Code | Meaning |
| --- | --- |
| `0` | Both official sources were retrieved, verified, parsed, and assessed |
| `2` | An official source, TLS/network dependency, parser, or integrity check was unavailable/invalid; caller must hold |
| `3` | Request file/JSON/schema was invalid |

## Output and decisions

The JSON output contains only evidence needed to reproduce the source assertion: source generation/effective dates, file/snapshot hashes, EU reference, identifier assertion hash, source-native locator, status, missing facts, and the aggregate business action. It deliberately does not echo the queried identifier value or full Annex I legal text.

- An exact usable sanctions identifier, ambiguous identifier, unusable historical identifier, or Annex I entry produces `RED` + `HOLD`.
- A missing classification or technical specification produces `YELLOW` + `REQUEST_EVIDENCE`.
- No exact sanctions identifier and no Annex I entry produces at most `GREEN_CANDIDATE` + `MONITOR`; this is not clearance.

Only an authorised human may clear or block a named transaction. Name/fuzzy matching, ownership/control propagation, destination/end-use/catch-all rules, Russia Regulation `833/2014` goods annexes, persisted source activation, screening/case records, REST authentication, MCP, and webhooks remain outside this technical-preview slice.

## Safe evaluation data

Use synthetic or specifically approved test identifiers. Although source records are public official designations, do not commit customer, shipment, payment, or investigation data to the repository. The command currently evaluates in memory and does not persist its request or result.
