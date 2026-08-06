# Agent interface principles

## Goal

Make TradeSieve easy for software and AI agents to use without giving agents authority to make final legal dispositions.

## One application contract

The REST API is the canonical service contract. CLI and MCP are adapters that call the same application layer and emit the same identifiers, finding schema, error taxonomy, and decision states.

```text
Business service
├── REST/OpenAPI adapter
├── event/webhook adapter
├── tradesieve CLI adapter
└── MCP adapter
```

No adapter owns matching thresholds, legal rules, release logic, or data-source parsing.

## Proposed MCP surface

### Read and screen tools

- `screen_party`
- `screen_goods`
- `screen_transaction`
- `get_screening_case`
- `list_case_findings`
- `list_required_evidence`
- `get_source_snapshot`
- `explain_rule_evaluation`

### Controlled workflow tools

- `submit_case_evidence`
- `request_human_review`
- `place_business_hold`

`release_business_hold` should not be exposed to general-purpose agents. If later exposed to a tightly controlled reviewer agent, it requires step-up authentication, explicit user confirmation, segregation of duties, an existing human approval record, and an idempotency key.

### Resources

- Versioned public rule summaries and source manifests.
- Redacted case summaries scoped to caller authorization.
- JSON Schemas and interface documentation.

Large raw source files, unrestricted case search, and bulk personal-data export should not be exposed as general MCP resources.

## MCP protocol target

Target the MCP `2026-07-28` stateless protocol for remote deployments while retaining a compatibility strategy for clients on `2025-11-25`. The current protocol makes requests self-describing and routable and strengthens authorization; compatibility must be validated against official SDKs rather than assumed.

Sources:

- [MCP 2026-07-28 release](https://blog.modelcontextprotocol.io/posts/2026-07-28/)
- [MCP specification repository](https://github.com/modelcontextprotocol/modelcontextprotocol)
- [MCP security best practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices)

## Agent-safe tool design

1. **Structured results first.** Return JSON Schema-constrained data plus concise human text.
2. **Explicit uncertainty.** Distinguish verified fact, inference, unknown, and reviewer decision.
3. **No green-by-default.** Missing material facts produce `REVIEW_REQUIRED`, not a low score.
4. **Bounded effects.** Screening is read-only; holds and evidence submission are explicit tools.
5. **Idempotency.** Every mutation accepts an idempotency key and returns an immutable event ID.
6. **Opaque handles.** Use case/evidence handles rather than exposing storage paths or unrestricted identifiers.
7. **Least privilege.** OAuth scopes map to tool and case permissions; CLI service accounts receive equivalent scopes.
8. **Prompt-injection resistance.** Treat uploaded files, source pages, and returned tool text as untrusted evidence.
9. **Traceability.** Record client/agent identity, tool arguments, schema version, source/rule versions, and result hash.
10. **Stable errors.** Return typed errors such as `MISSING_EVIDENCE`, `SOURCE_STALE`, `REVIEW_REQUIRED`, and `PERMISSION_DENIED`.

## CLI shape

```bash
tradesieve party screen --input party.json --json
tradesieve goods screen --input goods.json --json
tradesieve transaction screen --input transaction.json --json
tradesieve case show CASE_ID --json
tradesieve source list --stale --json
tradesieve replay --as-of 2026-07-23 --input transaction.json --json
```

CLI requirements:

- Human-readable output by default; stable `--json` output for agents and scripts.
- Primary result on stdout; diagnostics on stderr.
- Zero exit status only for successful command execution, not to mean “transaction cleared.”
- Distinct non-zero exit codes for validation, unavailable sources, permission, and system errors.
- `--no-input`, `--quiet`, and idempotency support for automation.
- Never place secrets or personal identifiers in command history examples.

Reference: [Command Line Interface Guidelines](https://clig.dev/).

## API and schema standards

- OpenAPI 3.1.x for HTTP contracts.
- JSON Schema Draft 2020-12 for shared domain schemas.
- AsyncAPI only if an event broker becomes part of the chosen architecture.
- Version schemas independently from legal-source versions and matching-model versions.

References: [OpenAPI](https://spec.openapis.org/oas/), [JSON Schema](https://json-schema.org/specification), and [AsyncAPI](https://www.asyncapi.com/docs).

## Important prior art

OpenSanctions' `yente-client` already offers typed SDK/CLI tooling and an MCP server. TradeSieve should test and potentially wrap this capability for entity matching while keeping its own source governance, transaction model, goods/route rules, cases, and human approvals.

- [OpenSanctions API guide](https://www.opensanctions.org/docs/api/)
- [yente-client](https://github.com/opensanctions/yente-client)
