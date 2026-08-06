# Governance

## Current phase

TradeSieve is a private `fyaic` organization project in Phase 1 planning/delivery. Repository maintainers own scope, access, backlog order, and releases. Domain decisions require named legal/compliance ownership before implementation activation or production use.

## Decision rights

| Area | Required owner |
| --- | --- |
| Product and repository scope | Repository owner |
| Legal-source interpretation | Qualified legal/compliance reviewer |
| Goods classification | Qualified export-control or technical reviewer |
| Security and privacy | Security/privacy owner |
| Production release | Product, engineering, compliance, and security sign-off |

## Delivery governance

- The [agile operating model](docs/delivery/agile-operating-model.md) defines work hierarchy, readiness, done, and PR evidence.
- The `Phase 1 — Service MVP` milestone is the time-boxed source of scope; the product/MVP documents control acceptance.
- Safety invariants, source provenance, human-only clearance, idempotency, and fail-closed behavior cannot be traded away as ordinary sprint scope.
- Planning dates are baselines until named capacity and dependencies are confirmed.
- GitHub Free does not enforce branch protection for this private repository; maintainers apply PR-only review procedurally until the organization plan supports enforcement.

## Change classes

- **Research note:** pull request and source review.
- **Architecture decision:** ADR plus maintainer approval.
- **Rule or legal-source change:** provenance, effective date, tests, compliance approval, and rescreen impact analysis.
- **Model or threshold change:** evaluation report, reviewer-workload analysis, rollback plan, and approval.

No model, rule, or data-source update may silently change the disposition of an existing case.
