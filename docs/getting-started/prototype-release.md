# Prototype release installation and handoff

**Version:** `0.1.0a5`
**Audience:** evaluator, integration developer and internal demonstrator.  
**Boundary:** synthetic demonstration and architecture evaluation; not production legal clearance.

## Fastest working demonstration

Prerequisites are Git access to the private `fyaic/TradeSieve` repository and Docker Desktop or a compatible Docker Engine with Compose.

```bash
git clone git@github.com:fyaic/TradeSieve.git
cd TradeSieve
git checkout v0.1.0-alpha.5
./scripts/start_business_demo.sh
```

Open `http://127.0.0.1:8080/demo/crm`, choose a fixed quotation and select **发起合规审查**. The refresh requires network access to the named official publications. If refresh has not succeeded, the CRM remains held.

Clean up the isolated demo when finished:

```bash
docker compose --env-file .env.example -p tradesieve-demo \
  down --volumes --remove-orphans
```

This deletes the demo database and private-source object volume.

## CLI and API handoff

The same active official bundle can be screened with:

```bash
docker compose exec -T app \
  tradesieve-manage screen-active --request - \
  < examples/requests/official-screening.json
```

The authenticated technical-preview API is:

```text
POST /v1/official-screenings
```

Its request and response are defined by [OpenAPI](../../api/openapi/tradesieve.v1.json), the [shared schema registry](../../api/schemas/tradesieve.contracts.v1.json), and the [request example](../../examples/requests/official-screening.json). CRM/OMS callers must treat `HOLD`, `REQUEST_EVIDENCE`, `401`, `422`, `503`, timeouts and unparseable responses conservatively. Only `MONITOR` is possible for a green candidate, and it is not a release instruction.

This alpha includes one local read-only stdio MCP tool over the active official-source screening service. It is suitable for trusted-host demonstration and Agent integration tests; it is not a remote OAuth-protected production endpoint. See [MCP and Codex Agent integration](mcp-agent.md).

## Python distribution

Release assets contain a wheel and source archive. For an isolated engineering installation:

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install tradesieve-0.1.0a5-py3-none-any.whl
.venv/bin/tradesieve-manage --help
```

The wheel also installs `tradesieve-mcp`; start it through an MCP client because a
normal shell invocation waits for protocol frames on stdin. The package does not
bundle PostgreSQL or official source data. Use the reference Compose stack for the
complete demonstration.

Maintainers reproduce the artifacts with the pinned toolchain:

```bash
uv sync --locked --all-groups
./scripts/check.sh
uv build
```

## What to show in a demo

1. Refresh and activate one complete EU FSF, EU Annex I, OFAC SDN and OFAC Consolidated bundle.
2. Show exact official candidate evidence and immutable hashes on the Russia-related quotation.
3. Show the `850440` shipment receiving a BIS CHPL Tier 3A candidate and specific evidence requests.
4. Show a bounded `3A001.e.1` technical comparison with synthetic manufacturer facts.
5. Stop PostgreSQL or use a stale bundle and show that the CRM does not silently pass.
6. Run the MCP stdio probe or verified Codex Agent example and show the same active bundle and conservative action.
7. End with the [prototype boundary](../product/prototype-release-boundary.md), especially the missing ownership, fuzzy matching, complete goods/legal-effect, remote MCP authentication and human-case workflow capabilities.

## Release contents and verification

- source tag and release notes;
- Python wheel and source archive;
- Dockerfile and Docker Compose reference stack;
- migration set through `20260810_0007`;
- versioned OpenAPI, JSON Schema and synthetic examples;
- fixed CRM demonstrator, source-refresh/CLI commands and local read-only MCP server;
- unit, contract, PostgreSQL, Compose and live-source acceptance scripts;
- security, contributing, licence, changelog and known-boundary documentation.

The repository is proprietary. Distribution outside the authorized organization requires the copyright holder’s written permission. Official publications and referenced third-party components retain their own terms.
