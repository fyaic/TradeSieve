# Documentation index

TradeSieve documentation is decision-oriented: requirements preserve what is needed, research records evidence, product documents define outcomes, architecture records proposed/accepted structure, and delivery documents make the work executable.

## Requirements

- [Original request](requirements/original-request.md)
- [Problem, scope, and success criteria](requirements/problem-and-scope.md)

## Product

- [Service definition](product/service-definition.md)
- [Personas and user journeys](product/personas-and-journeys.md)
- [Detailed product requirements](product/requirements.md)
- [Phase 1 service MVP](product/mvp-scope.md)

## User and integration experience

- [Development setup and checks](getting-started/development.md)
- [Target MVP user experience](getting-started/mvp-user-experience.md)
- [Draft OpenAPI contract](../api/openapi/tradesieve.v1.json)
- [Synthetic request](../examples/requests/transaction-screening.json)
- [Expected review-required response](../examples/responses/transaction-screening.review-required.json)

## Architecture

- [Initial system shape](architecture/initial-system-shape.md)
- [Phase 1 service architecture](architecture/mvp-service-architecture.md)
- [Domain and data model](architecture/domain-model.md)
- [Integration design](architecture/integration-design.md)
- [Agent interface principles](architecture/agent-interface-principles.md)
- [Security, privacy, and trust](architecture/security-and-trust.md)
- [Implementation blueprint](architecture/implementation-blueprint.md)

## Architecture decisions

- [ADR-0001: Independent control plane](decisions/0001-independent-control-plane.md)
- [ADR-0002: Human clearance only](decisions/0002-human-clearance-only.md)
- [ADR-0003: One contract, multiple interfaces](decisions/0003-one-contract-multiple-interfaces.md)
- [ADR-0004: Phase 1 modular Python service](decisions/0004-phase-1-modular-python-service.md)

## Delivery

- [Agile operating model](delivery/agile-operating-model.md)
- [Phase 1 delivery plan](delivery/phase-1-plan.md)
- [Phase 1 backlog decomposition](delivery/phase-1-backlog.md)
- [Product maturity roadmap](roadmap/discovery-plan.md)

## Research

- [Landscape synthesis](research/landscape.md)
- [Regulatory and official data sources](research/regulatory-and-data-sources.md)
- [Open-source projects and standards](research/open-source-and-standards.md)
- [Academic research](research/academic-research.md)
- [Industry patterns](research/industry-patterns.md)
- [Machine-readable source registry](../research/sources.yaml)

## Documentation rules

1. Separate verified facts, cited sources, inferences, proposed decisions, and accepted decisions.
2. Add an access date and licence/access note for external data or software.
3. Never state that a screening result is legal clearance.
4. Preserve superseded decisions instead of rewriting accepted history.
5. Distinguish target experience from implemented/released behavior.
6. Do not add customer, employee, payment, or shipment identifiers to Git.
