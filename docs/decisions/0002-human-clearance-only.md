# ADR-0002: Human clearance only

- Status: Accepted
- Date: 2026-08-06

## Context

Sanctions and export-control outcomes depend on legal nexus, ownership/control, technical specifications, end use/user, route, payment, licences, and current law. A list or model match cannot resolve all of these facts.

## Decision

Automation may produce findings, request evidence, place holds, and escalate. Only an authorized human reviewer may create `HUMAN_CLEARED`, and the decision must identify the action, scope, evidence, rationale, source/rule versions, approver, and expiry.

## Consequences

- Agents and models never return an automatic green light.
- Missing material evidence cannot be converted into a low score.
- Overrides and releases require segregation of duties and immutable logs.
- Reviewer experience and workload are first-class system requirements.
