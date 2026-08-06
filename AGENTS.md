# Agent working agreement

This repository is designed for collaboration with coding and research agents. Agents must follow these constraints in addition to user instructions.

## Safety boundary

- Treat TradeSieve as decision support, never as legal advice or an automatic legal-clearance service.
- Never turn a match score, country flag, HS/CN code, or model output into `CLEARED`.
- Only an authorized human reviewer may issue a time-bounded release, with recorded evidence and reason.
- Do not place real customer, shipment, payment, credential, or regulated personal data in this repository.
- Never send production data to an external model, tool, or service without an explicit approved data-handling decision.

## Research rules

- Prefer primary sources: legal acts, regulator publications, official datasets, specifications, project repositories, and original papers.
- Record every durable source in `research/sources.yaml` with an access date and intended use.
- Separate facts stated by a source from project inferences and recommendations.
- Date all claims whose truth may change. Do not describe a list, package, API, or regulation as “latest” without rechecking it.
- Treat community datasets and vendor products as implementation aids; they do not override controlling law or official publications.
- Record licence, redistribution, retention, and API-exposure constraints before proposing production use.

## Architecture rules

- Preserve the independent-control-plane boundary in ADR-0001.
- Keep Pydantic application models as the canonical contract source for generated REST/OpenAPI, event, CLI, and MCP adapters. Every adapter must use the same authorization and application services.
- Keep source facts, deterministic rules, model suggestions, reviewer decisions, and downstream enforcement events distinct in schemas and audit logs.
- Design for immutable raw snapshots, hashes, diffs, effective dates, source/rule/model versions, and replay.
- MCP tools that mutate cases or holds must be narrowly scoped, authenticated, auditable, and confirmation-gated. Prefer read-only tools by default.
- Propose material architecture changes with an ADR in `docs/decisions/`.

## Change discipline

1. Read the relevant requirement, product baseline, research note, and ADR before changing it.
2. Update documentation and machine-readable registries together.
3. Add or update tests/checks in proportion to the change.
4. Run `./scripts/check_docs.sh` before committing.
5. Explain uncertainty, open questions, and evidence gaps in the pull request.

For implementation work, link a Phase 1 story/requirement and follow `docs/delivery/agile-operating-model.md`. Do not present target quickstart commands as implemented until the clean-environment golden path passes.

Do not claim production readiness while the repository status remains Phase 1/MVP.
