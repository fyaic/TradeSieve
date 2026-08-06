# Contributing

TradeSieve is in discovery. Contributions should improve the evidence base, clarify requirements, or make an explicit design decision.

## Workflow

1. Create a focused branch named `agent/<description>` or `docs/<description>`.
2. Link the change to an issue or explain the decision in the pull request.
3. Separate factual research from project recommendations.
4. Add primary-source links, access dates, and licence notes.
5. Run `./scripts/check_docs.sh` before opening a pull request.
6. Use a pull request; do not push directly to protected branches.

## Content safety

- Do not commit live customer, beneficial-owner, bank, shipment, invoice, or payment data.
- Use synthetic fixtures for future tests.
- Do not add instructions that conceal parties, routes, ownership, origin, end use, or payment flows.
- Do not turn an AI output, a name match, or an HS code into an automatic clearance.
- Report security concerns according to [SECURITY.md](SECURITY.md).

## Decision records

Material architectural choices require an ADR under `docs/decisions/`. ADRs are immutable once accepted; supersede them with a new ADR instead of editing the decision history.
