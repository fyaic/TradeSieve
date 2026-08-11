# TradeSieve MCP stdio smoke test — 2026-08-11

## Scope and safety boundary

This smoke test drives the packaged server directly over MCP stdio JSON-RPC. It does
not use REST as a substitute. The target is the isolated local demonstration stack and
the repository's synthetic request. No customer data, production credential, human
decision or external write is in scope.

The server is expected to expose exactly one read-only tool:
`screen_transaction`. Absence of clearance, mutation, raw-source export, arbitrary
file, network and SQL tools is part of acceptance.

## Pre-execution plan

| Case | Assertion | Side effect | Cleanup |
|---|---|---|---|
| S1 handshake | Client completes MCP initialization over stdio | None | Server subprocess exits with client |
| S2 inventory | Exactly `screen_transaction`; read-only/idempotent annotations | None | None |
| S3 live call | Structured result is `RED` + `HOLD`, `automatic_clearance=false`, and has an active official bundle ID | Read-only PostgreSQL transaction | Connection closes |
| S4 failure posture | Unexpected inventory/result fails the probe and does not fall back to green | None | Process exits non-zero |

Planned command:

```bash
uv run --locked python scripts/mcp_stdio_probe.py \
  --project-name tradesieve-walkthrough \
  --request examples/requests/official-screening.json
```

## Execution record

Status: **PASS**

Executed from repository commit worktree on macOS against the packaged
`tradesieve:0.1.0-alpha.2` image and Compose project
`tradesieve-walkthrough`. The Python MCP client opened the server through
`docker compose exec -T app tradesieve-mcp`; the app and database were not accessed
through REST during this probe.

Bounded result:

```json
{
  "protocol": "stdio",
  "protocol_version": "2025-11-25",
  "tools": ["screen_transaction"],
  "invalid_request_rejected": true,
  "signal": "RED",
  "business_action": "HOLD",
  "automatic_clearance": false,
  "source_bundle_id": "official-bundle-29ecef0561b30897b2f73870714b9181fd7fae96c36d1985aeb5ebbfd66834b4",
  "eu_fsf_statuses": ["NO_CANDIDATE"],
  "ofac_statuses": [
    {"list_kind": "SDN", "name_statuses": ["CANDIDATE"]},
    {"list_kind": "CONSOLIDATED", "name_statuses": ["NO_CANDIDATE"]}
  ],
  "dual_use_status": "CONTROL_ENTRY_FOUND",
  "sensitive_goods_status": "CANDIDATE"
}
```

The inventory check also asserted `readOnlyHint=true`, `destructiveHint=false`,
`idempotentHint=true`, and `openWorldHint=false`. A structurally incomplete request
returned an MCP tool error before any screening result. Closing the client terminated
the stdio subprocess; the long-running demo stack remained healthy and contained no
new externally published port or remote MCP listener.
