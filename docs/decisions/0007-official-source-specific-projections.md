# ADR-0007: Use source-specific immutable projections for official controls

**Status:** Accepted
**Date:** 2026-08-10

## Context

The generic Phase 1 snapshot model intentionally bounded a parsed source to 512 records and a compact canonical payload. The live EU Financial Sanctions File observed on 2026-08-10 contains 6,234 entities, 31,053 aliases, and 3,007 substantive strong identifiers in a 25.8 MiB XML document. The current EU dual-use Annex I Formex text contains 384 long, hierarchical control entries. Raising the generic JSON limits would create monolithic rows, poor query plans, and unclear source semantics.

The first useful product slice must prove that official bytes—not synthetic fixtures—can drive a conservative screening result with exact provenance. It must not imply that an exact identifier match is legal clearance or that an HS code is a definitive dual-use classification.

## Decision

1. Retain the generic snapshot lifecycle for bounded source-neutral governance, but model high-volume official lists through source-specific immutable projections.
2. Store raw bytes by content hash and project entities, aliases, identifiers, legal control entries, and activation events into queryable rows with source-native locators.
3. The EU FSF connector discovers the exact XML 1.1 distribution through official data.europa.eu metadata, restricts all network destinations, rejects unsafe/unknown XML shape, and keeps strong typed identifiers separate from name candidates.
4. The EU Annex I connector reads the exact official Publications Office CELLAR item for a reviewed CELEX version, rejects unsafe archives/XML, and projects unique control entries. Version change is an explicit governed event, not an unnoticed overwrite.
5. Exact identifier and explicit Annex I code lookup produce evidence. They never grant automatic clearance. Missing, conflicting, ambiguous, stale, or unavailable evidence tightens the business action.
6. HS/CN/TARIC codes may later retrieve candidates, but only qualified classification plus required technical parameters can support a formal controlled-item finding.
7. CLI, REST, and MCP adapters call the same application service. `refresh-official-sources` owns retrieval and atomic activation; `screen-active`, authenticated `POST /v1/official-screenings`, and the visible demo CRM action use the same persisted engine. `screen-official` remains a stateless network diagnostic. MCP remains future work.

## Consequences

- The source boundary is more code than a generic blob parser, but schema drift, URL policy, licensing, identifiers, and legal versions become explicit and independently testable.
- Migration `20260810_0006` and `test_official_source_postgres.sh` enforce row-level official projections, content-addressed raw bytes, idempotent activation, a single active version and database immutability on PostgreSQL 18.4.
- Daily FSF retrieval can discover the current distribution; annual/ad hoc legal-list amendments require a monitored CELEX update workflow.
- The first party-screening slice includes explainable exact normalized-alias candidates. Full party screening still requires fuzzy/transliterated entity resolution and ownership/control. Full goods screening still requires technical-rule assertions, catch-all/end-use/destination controls, and Russia-specific annexes.
- No source or matcher response can bypass ADR-0002 human-only clearance.
