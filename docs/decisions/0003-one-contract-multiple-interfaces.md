# ADR-0003: One contract, multiple interfaces

- Status: Accepted
- Date: 2026-08-06

## Context

The product must support conventional business-system integration and agent-native use through API, CLI, and MCP.

## Decision

Define one canonical application contract and shared schemas. REST/OpenAPI, events, CLI, and MCP adapt that contract without owning business rules.

## Consequences

- Equivalent calls return equivalent decision IDs and typed findings.
- Interface parity can be contract-tested.
- MCP and CLI remain replaceable adapters rather than alternate implementations.
- Breaking domain changes require explicit schema/version migration.
