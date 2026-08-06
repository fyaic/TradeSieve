## Why

Describe the user/operational problem or decision this change addresses. Link the issue, parent epic, and product requirement IDs.

## What changed

Summarize the implementation, documentation, data-source, or research changes.

## Acceptance evidence

- Story acceptance criteria:
- Working demo or reproducible command:
- Negative/failure/safety cases:
- API/CLI/MCP parity or migration evidence, if applicable:

## Evidence and uncertainty

- Primary sources or benchmarks:
- Assumptions and unresolved questions:
- Legal/data/licence review required:

## Risk and safety

- Does this affect holds, releases, matching thresholds, source activation, or authorization?
- Could production or personal data leave an approved boundary?
- Is an ADR or human approval required?

## Verification

- [ ] `./scripts/check_docs.sh` passes
- [ ] Applicable format/lint/type/unit/contract/integration/security checks pass
- [ ] Documentation and machine-readable registries agree
- [ ] OpenAPI, examples, CLI/MCP, events, and migrations agree where applicable
- [ ] Authorization, tenant boundary, idempotency, audit, errors, and redaction were considered
- [ ] No credentials or production data are included
- [ ] Source access dates and licences were rechecked where relevant
- [ ] The applicable Definition of Done is met, or every exception is explicit
