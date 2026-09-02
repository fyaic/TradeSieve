# Dependency and MCP smoke test — 2026-09-02

**Decision:** GO for the dependency-maintenance change. This is engineering
compatibility evidence, not a production-readiness or legal-effectiveness claim.

## Scope and environment

This smoke verifies the coordinated September dependency update, especially the
`mcp` 2.1.1 error boundary, through the packaged local stdio server and the same
persisted four-source application service used by CLI, authenticated REST and the
synthetic CRM.

- CPython 3.13.14 and `uv` 0.12.8;
- PostgreSQL 18.4 reference container;
- `mcp` 2.1.1 / negotiated protocol `2025-11-25`;
- Docker 29.6.2 on the local acceptance host;
- public synthetic request `examples/requests/official-screening.json` only.

## Test plan and result

| Check | Expected result | Side effect / cleanup | Result |
| --- | --- | --- | --- |
| Repository gate | Lock, format, lint, typing, contracts, unit/architecture, build, docs, OpenAPI, secret and vulnerability checks pass | Build artifacts only | PASS |
| Reference Compose | Fresh migration, bootstrap, runtime/failure controls and teardown pass | Isolated containers, networks and volumes removed | PASS |
| MCP handshake | Initialize over stdio and negotiate the supported protocol | Server subprocess exits | PASS |
| Tool inventory | Exactly `screen_transaction`, marked read-only, non-destructive, idempotent and closed-world | None | PASS |
| Negative boundary | Invalid input is rejected; no clearance/mutating tool exists; unavailable or stale source returns a bounded HOLD instruction without an internal exception | None | PASS |
| Live happy path | Fresh official bundle returns `RED` / `HOLD`, `automatic_clearance=false` and the same bundle identity through CLI, REST, CRM and MCP | Isolated official-source database removed | PASS |

## Evidence summary

- 2,088 repository tests passed with 100% statement and branch coverage.
- Package build, documentation checks, OpenAPI lint and dependency vulnerability
  audit passed; the audit reported no known vulnerabilities.
- The full Compose reference deployment passed on isolated ports and left zero
  project residue.
- The live gate applied one official-source refresh and accepted its replay as
  idempotent. It parsed 6,234 EU FSF entities, 384 EU Annex I entries, 19,321 OFAC
  SDN entries and 481 OFAC Consolidated entries.
- CLI and authenticated REST returned the same canonical result. The synthetic CRM
  and MCP stdio server used the same active four-source bundle.
- The public SOVCOMFLOT candidate remained `RED` / `HOLD`; MCP never returned
  automatic clearance.
- A deliberately stale pre-existing snapshot was rejected by the upgraded MCP
  adapter with the bounded instruction to keep the business action on hold.
- The live gate removed its temporary evidence directory, containers, networks and
  volumes after the assertions completed.

## Compatibility finding

MCP SDK 2.1 redacts unexpected tool exceptions as a generic execution failure. The
adapter now raises the SDK's anticipated `ToolError` for known source/database
failures. This preserves the sanitized, fail-closed HOLD guidance without exposing
the underlying exception. Unit tests cover this boundary and the direct stdio live
gate covers the packaged behavior.

## Boundary

The result demonstrates dependency compatibility and the existing prototype
contract on the tested source bundle. It does not add remote MCP/OAuth, case
mutation, human clearance, ownership/control propagation, full Russia-specific
goods/route rules, comprehensive legal coverage or production approval.
