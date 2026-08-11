# Codex Agent MCP demonstration — 2026-08-11

## Purpose

Prove that an independently tasked Codex sub-Agent can consume TradeSieve through the
packaged MCP stdio server, rather than receiving a copied REST response or a mocked
screening result.

## Agent task

The sub-Agent was given read-only authority and asked to:

1. read `examples/requests/official-screening.json`;
2. run `scripts/mcp_stdio_probe.py` against Compose project
   `tradesieve-walkthrough`;
3. verify the MCP handshake, exact tool inventory, invalid-input rejection and
   structured official-source result;
4. check the Compose service health;
5. modify, commit and push nothing.

The probe launches `docker compose exec -T app tradesieve-mcp` as an stdio MCP server,
initializes a `ClientSession`, calls `tools/list`, submits an invalid tool request and
then calls `screen_transaction` with the repository fixture. It does not call the
TradeSieve REST endpoint.

## Independent Agent report

Status: **PASS**

The final re-run used app and worker image `tradesieve:0.1.0-alpha.2`.

| Assertion | Observed result |
|---|---|
| Agent identity | Codex sub-Agent |
| Transport | MCP stdio, not REST |
| Negotiated protocol | `2025-11-25` |
| Handshake and inventory | Successful; only `screen_transaction` |
| Invalid request | Rejected |
| Aggregate decision support | `RED` / `HOLD` |
| Automatic clearance | `false` |
| EU FSF name state | `NO_CANDIDATE` |
| OFAC SDN name state | `CANDIDATE` |
| OFAC Consolidated name state | `NO_CANDIDATE` |
| EU dual-use state | `CONTROL_ENTRY_FOUND` |
| CHPL sensitive-goods state | `CANDIDATE` |
| Active source bundle | `official-bundle-29ecef0561b30897b2f73870714b9181fd7fae96c36d1985aeb5ebbfd66834b4` |
| Runtime health | app, PostgreSQL and worker healthy |
| Probe exit status | `0` |
| Repository mutation | None |

## Interpretation boundary

This proves local MCP protocol compatibility, canonical structured output and use of
the active official-source bundle for one synthetic request. It does not prove remote
MCP authentication, production authorization, legal correctness, entity ownership
propagation, complete export classification or permission to release a transaction.
