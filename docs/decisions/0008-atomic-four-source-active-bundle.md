# ADR-0008: Activate the four official sources as one atomic screening bundle

**Status:** Accepted
**Date:** 2026-08-10

## Context

ADR-0007 established source-specific immutable projections for EU FSF and EU Annex I. The OFAC SLS connector subsequently proved safe retrieval and deterministic projections for both comprehensive publications: SDN and Consolidated. Adding OFAC as an independently refreshed side table would let one request combine source versions that were never reviewed or activated together. Treating an upgraded 0006 EU-only pointer as complete would also create a false runtime-coverage claim.

The regulator/current-source pool must remain distinct from the internal case/experience pool. Official publications establish external source assertions. Spreadsheet rows, chat findings, reviewer notes and prior decisions retain attributed internal provenance and cannot silently become official-list facts.

## Decision

1. One active official screening bundle binds exactly four typed projections: EU FSF, EU Annex I, OFAC SDN and OFAC Consolidated.
2. Each raw publication is stored by content hash. Every source-specific projection is immutable, count-checked, identity-checked and fully reconstructed/re-hashed on read.
3. One refresh retrieves and validates all four sources before a single database transaction writes missing immutable objects, appends one activation event and advances the singleton active pointer.
4. Migration `20260810_0007` preserves every 0006 EU raw object, projection, bundle, event and state row. Historical EU-only rows remain nullable for OFAC references, but any new or updated active state must bind both correctly typed OFAC snapshots.
5. Runtime reads fail closed on an upgraded EU-only active pointer, missing rows, kind swaps, count/sequence/hash mismatch, future timestamps or source age over 48 hours.
6. EU and OFAC evidence remain source-separated in the result. A candidate can tighten the result to `RED/HOLD`; no source can set `automatic_clearance=true`.
7. OFAC list membership does not implement the 50 Percent Rule, ownership/control propagation, program legal effect, licence analysis or transaction legality. Those require separately governed rules and human/legal review.
8. `screen-active`, authenticated REST and demo CRM invoke the same persisted application service. The versioned OpenAPI/JSON Schema publishes this technical-preview contract; MCP remains a later adapter over the same application boundary.

## Consequences

- Callers never observe a partially refreshed four-source set.
- An upgrade may make screening temporarily unavailable until the first successful four-source refresh; this is intentional failure closure, not a migration error.
- Retaining all normalized OFAC child facts increases storage and read cost. The current MVP reconstructs the active projection in memory; indexed database-native candidate queries are a future performance optimization that must preserve identical evidence semantics.
- The independent PostgreSQL gate proves legacy preservation, partial-state refusal, fresh four-source activation, idempotency, immutable constraints and zero residue on PostgreSQL 18.4.
- The isolated live gate proves current official counts, idempotent replay, a Russia-program OFAC candidate, and CLI/REST/CRM bundle parity without retaining test infrastructure.
