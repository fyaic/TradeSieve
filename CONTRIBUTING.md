# Contributing

TradeSieve is in Phase 1 planning and delivery. Contributions should deliver a linked user/operational outcome, improve the evidence base, clarify requirements, or make an explicit design decision.

## Workflow

1. Refine the issue against the [Definition of Ready](docs/delivery/agile-operating-model.md).
2. Create a focused branch named `feature/<issue>-<description>`, `fix/<issue>-<description>`, `docs/<issue>-<description>`, or `agent/<description>`.
3. Link the change to an issue/requirement and state observable acceptance criteria in the pull request.
4. Separate factual research from project recommendations; add primary-source links, access dates, and licence notes.
5. Update OpenAPI/examples/CLI/MCP parity and migrations when the contract/data changes.
6. Run `./scripts/check.sh` before marking an implementation PR ready; documentation-only changes may run `./scripts/check_docs.sh` plus the pinned OpenAPI lint command.
7. Meet the [Definition of Done](docs/delivery/agile-operating-model.md); use a pull request rather than direct main pushes.

## Content safety

- Do not commit live customer, beneficial-owner, bank, shipment, invoice, or payment data.
- Use synthetic fixtures for future tests.
- Do not add instructions that conceal parties, routes, ownership, origin, end use, or payment flows.
- Do not turn an AI output, a name match, or an HS code into an automatic clearance.
- Report security concerns according to [SECURITY.md](SECURITY.md).

## Decision records

Material architectural choices require an ADR under `docs/decisions/`. ADRs are immutable once accepted; supersede them with a new ADR instead of editing the decision history.
