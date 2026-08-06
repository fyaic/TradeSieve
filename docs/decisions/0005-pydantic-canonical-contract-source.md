# ADR-0005: Pydantic application models are the canonical contract source

- Status: Accepted
- Date: 2026-08-06

## Context

ADR-0003 requires REST/OpenAPI, events, CLI, and MCP to adapt one application
contract without owning business rules. The original draft OpenAPI document was
handwritten before the application package existed. Keeping both handwritten Pydantic
models and handwritten OpenAPI schemas would create two mutable sources and make
interface drift likely.

The intake also represents partially known business facts. A customer-onboarding event,
for example, may not have goods, a route, payment facts, or a resolved legal nexus.
Rejecting that event as a transport error would prevent the completeness controls from
creating conservative findings and holds.

## Decision

Pydantic v2 models in `tradesieve.application.contracts` are the sole canonical typed
source for screening requests, results, evidence, decisions, cases, and events.
Deterministic repository tooling generates the published OpenAPI components and
synthetic examples from those models and provides a check mode that fails on drift.

REST, CLI, MCP, event, and webhook adapters must validate and serialize these models or
generated schemas from this source. They may translate transport concerns, but they
must not copy sanctions, export-control, completeness, matching, or decision logic.
Adapter parity is tested when each adapter is implemented; the final no-interface-only
business-logic gate from ADR-0003 remains in force.

Canonical models reject unknown fields, invalid primitive formats, duplicate object
identifiers, and dangling references. Material business facts may be absent or
explicitly unknown. Application completeness controls—not schema defaults—must turn
those gaps into typed findings, required evidence, and holds. No contract default may
imply a green or cleared result.

## Consequences

- Contract changes begin in the application models, then regenerate reviewed artifacts.
- CI fails if OpenAPI components or committed examples drift from the model source.
- Incomplete but structurally valid intake reaches deterministic completeness controls.
- Classification values in intake remain candidates, never automatic legal conclusions.
- Breaking changes still require an explicit schema-version migration.
