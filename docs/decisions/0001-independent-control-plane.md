# ADR-0001: Independent control plane

- Status: Accepted
- Date: 2026-08-06

## Context

The original request explicitly requires decoupling from CRM. Legal sources, matching, product rules, and case dispositions also change on lifecycles that differ from CRM/OMS releases.

## Decision

Build TradeSieve as an independent service boundary. Business systems integrate through versioned APIs and events and store only external references, risk state, and enforcement status needed for their workflows.

## Consequences

- Source/rule updates do not require CRM deployment.
- Multiple business systems can share one screening decision model.
- TradeSieve needs its own availability, identity, audit, data-retention, and disaster-recovery design.
- Integration contracts and fail-closed behavior become explicit product requirements.
