# MCP and Codex Agent integration

**Implemented surface:** local stdio, read-only, one tool, alpha prototype.

TradeSieve ships `tradesieve-mcp`, an MCP server built with the official Python SDK.
It exposes the canonical active-source screening contract to a trusted local Agent
host. The adapter calls the same `PersistedOfficialScreeningService` used by
`screen-active`, authenticated REST and the synthetic CRM.

## Exact tool boundary

| Tool | Effect | Result |
|---|---|---|
| `screen_transaction` | Read the current, complete, fresh official-source bundle and evaluate one bounded request | Typed `OfficialScreeningResult` with signal, business action, source/evidence versions and caveats |

The inventory deliberately has no clearance, hold-release, case mutation, arbitrary
search, bulk export, file, SQL or network tool. Its MCP annotations are read-only,
non-destructive, idempotent and closed-world. `automatic_clearance` is always false.

## Start the local stack and official bundle

```bash
cp .env.example .env
docker compose up -d --build --wait
docker compose run --rm --no-deps app \
  tradesieve-manage refresh-official-sources
```

The refresh operation needs outbound access to the official EU and US publications;
ordinary MCP screening does not. See [External services and outbound dependencies](../operations/external-dependencies.md).

## Verify MCP directly

The repository probe starts the packaged server through Docker Compose, performs the
MCP initialization handshake, enumerates the tool, rejects one invalid request and
submits the synthetic official-source example:

```bash
uv run --locked python scripts/mcp_stdio_probe.py \
  --project-name tradesieve-demo \
  --request examples/requests/official-screening.json
```

This is a protocol test, not a REST wrapper. A successful bounded output contains:

```json
{
  "protocol": "stdio",
  "tools": ["screen_transaction"],
  "invalid_request_rejected": true,
  "signal": "RED",
  "business_action": "HOLD",
  "automatic_clearance": false,
  "source_bundle_id": "official-bundle-..."
}
```

The current local acceptance record is
[TradeSieve MCP stdio smoke test](../evidence/tradesieve-mcp-smoke-2026-08-11.md).

## Configure Codex

Codex reads MCP server definitions from `config.toml`. Copy the repository example
into a trusted project's `.codex/config.toml`, or merge its server block into the user
configuration:

```bash
mkdir -p .codex
cp examples/codex/config.toml .codex/config.toml
```

The example starts the server inside the already-running `tradesieve-demo` app
container:

```toml
[mcp_servers.tradesieve]
command = "docker"
args = ["compose", "-p", "tradesieve-demo", "exec", "-T", "app", "tradesieve-mcp"]
startup_timeout_sec = 20
tool_timeout_sec = 120
```

Restart the Agent host after changing configuration, confirm that the tool inventory
contains only `screen_transaction`, and use an explicit prompt such as:

```text
Read examples/requests/official-screening.json. Call the TradeSieve
screen_transaction tool with that exact request. Report the signal, business_action,
automatic_clearance, source_bundle_id, EU/OFAC candidate states, dual-use status and
sensitive-goods status. Do not reinterpret the result as legal clearance.
```

The verified Codex sub-Agent transcript is recorded in
[Codex Agent MCP demonstration](../evidence/codex-agent-mcp-demo-2026-08-11.md).

## Trust and deployment boundary

The implemented transport is stdio. It inherits the environment and service identity
of the process that launches it; it does not implement a separate end-user login.
Therefore:

- use it only from a trusted local Agent host or controlled service account;
- do not expose its stdin/stdout through a public relay;
- do not give an untrusted Agent unrestricted Docker or host access merely to reach
  this demo configuration;
- keep production customer data out of the repository and command history;
- treat an unavailable tool, database or stale/corrupt source as `HOLD`.

A remotely reachable Streamable HTTP MCP service, OIDC/OAuth authorization, tenant
scopes, per-tool audit identity and case/review tools are future work. Until those
controls exist, remote Agents should use the authenticated REST interface through a
separately governed integration service rather than presenting stdio as a production
remote endpoint.

## Packaging

The wheel installs both entry points:

```text
tradesieve-manage
tradesieve-mcp
```

`tradesieve-mcp` writes MCP protocol frames to stdout. Operational diagnostics must
remain on stderr and must never echo request values, source bytes or database details.
