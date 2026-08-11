# ADR-0009: Bind hand-reviewed technical assertions to exact official text

**Status:** Accepted for the prototype boundary; not production legal approval
**Date:** 2026-08-10

## Context

An Annex I control entry such as `3A001` contains many nested product definitions,
numeric thresholds, alternatives, exclusions, notes, and defined terms. Finding that
the code exists in the official list is useful evidence, but it is not a technical
parameter assessment. Conversely, generating executable legal rules from free text at
runtime would make source drift, interpretation errors, and model nondeterminism hard
to detect or audit.

Phase 1 requirement FR-GDS-003 asks TradeSieve to compare known technical facts with
versioned control assertions and expose missing parameters. FR-GDS-004 explicitly
keeps automatic definitive classification outside Phase 1, and ADR-0002 reserves
clearance for an authorised human.

## Decision

1. TradeSieve may execute only finite, hand-reviewed technical assertions shipped as
   versioned code or governed data. Callers cannot submit expressions, scripts,
   templates, plugins, or legal text for execution.
2. Every assertion bundle is bound to an exact CELEX identifier, Annex entry code,
   entry content hash, source-native locator, rule ID, semantic version, and canonical
   bundle hash. If the active official entry is absent or its hash changes, the rule is
   disabled and the assessment fails closed as `SOURCE_MISMATCH`.
3. Inputs are typed facts with a canonical unit, value, evidence reference, and
   verification state. Missing or unverified facts remain explicit; they are never
   inferred from a product description, HS/CN/TARIC code, or model output.
4. Assertion outcomes are limited to `MATCHED`, `NOT_MATCHED`, `INCOMPLETE`,
   `NOT_APPLICABLE`, `UNSUPPORTED`, and `SOURCE_MISMATCH`. A result describes only the
   evaluated branch. It is neither a definitive product classification nor clearance.
5. The first bounded bundle covers the numeric portions of `3A001.a.5.a`,
   `3A001.a.14`, and `3A001.e.1` in CELEX `32025R2003`: ADC resolution/sample-rate
   thresholds and primary/secondary electrochemical-cell density thresholds. Notes,
   exclusions, measurement conditions, and strict boundary operators are represented
   explicitly.
6. CLI, REST, and demo CRM use the same canonical Pydantic input and application
   service. The output exposes normalized comparisons, rule/source identities,
   required facts, missing facts, and evidence references without reproducing the full
   legal text.

## Consequences

- A synthetic logistics transaction can exercise a real official-source-bound numeric
  rule instead of a precomputed result.
- A legal-text update cannot silently continue using an old compiled interpretation.
- Coverage grows assertion by assertion; the engine must disclose unsupported branches
  and must not delay a clearly labelled prototype solely because a named domain reviewer
  is unavailable.
- HS or free-text candidate generation remains a separate workflow. Qualified product
  classification, destination/end-use/catch-all analysis, licences, and human decision
  remain required.
- The initial assertions are an engineering interpretation. They must not be described
  or deployed as complete EU dual-use coverage, legal classification or production
  clearance authority.
