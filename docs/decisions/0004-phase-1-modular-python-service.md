# ADR-0004: Phase 1 modular Python service

**Status:** Proposed
**Date:** 2026-08-06

## Context

Phase 1 must produce a runnable service MVP with REST, CLI, MCP, source/data experiments, human review, audit, and a sample CRM/OMS integration. Domain and source access are still being learned, and the product may remain a focused service rather than become a broad system.

## Decision

Implement Phase 1 as a modular monolith in one Python codebase and application image, with separate runtime commands for HTTP/reviewer UI, MCP, and background workers. Use PostgreSQL for transactional domain/audit/job/outbox state and an object-store port for immutable source/evidence blobs.

Adapters depend on application/domain ports. CLI and MCP use the same canonical schemas, authorization, and application services as REST. Matching engines, source connectors, object storage, identity, and policy evaluation remain replaceable ports.

The proposed stack is Python 3.13+, FastAPI/Pydantic, PostgreSQL, the official MCP Python SDK, and OCI/Docker Compose packaging. Final dependency versions are locked by implementation PRs.

## Consequences

- One team can deliver and test the whole vertical slice without distributed consistency/version drift.
- Python data/entity-resolution components and the official MCP SDK can be evaluated in the same environment.
- Process separation remains possible without service separation.
- PostgreSQL job/outbox patterns must be implemented and load-tested instead of assuming a broker.
- Python performance and framework/security maturity must be measured; this ADR can be superseded if a spike shows material failure.
- A future module becomes a separate service only after a documented trust, scale, ownership, or failure-domain trigger.

## Required validation before acceptance

1. Clean Docker build/start and migration.
2. OpenAPI/Pydantic contract consistency.
3. Official MCP SDK compatibility with target clients/protocol revision and OAuth design.
4. PostgreSQL `pg_trgm`/candidate baseline benchmark against approved fixtures.
5. p95 screening and worker/outbox behavior on the Phase 1 reference dataset.
6. Dependency/security/operations review and backup/restore demonstration.
