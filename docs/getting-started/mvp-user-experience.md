# Target MVP user experience

> **Important:** this is the Phase 1 acceptance contract for version `0.1.0`, not a claim that the current design-only repository already implements these commands. The README must switch from “target” to “released” only after the golden-path test passes.

## What the user receives

The MVP release should let a user choose one of four entry points while reaching the same service and result model:

- Docker Compose for a local/private demo;
- REST/OpenAPI for CRM/OMS/payment integration;
- `tradesieve` CLI for analysts and automation;
- MCP Streamable HTTP for authorized agents;
- a minimal reviewer web page for the human-only decision step.

## 1. Start a local demo

Target workflow:

```bash
git clone https://github.com/fyaic/TradeSieve.git
cd TradeSieve
cp .env.example .env
docker compose up --build
```

Expected endpoints in demo mode:

| Endpoint | Purpose |
| --- | --- |
| `http://localhost:8080/docs` | Interactive REST documentation |
| `http://localhost:8080/review` | Minimal reviewer console |
| `http://localhost:8080/health/live` | Process liveness |
| `http://localhost:8080/health/ready` | Database/migrations plus required source/rule coverage |
| `http://localhost:8081/mcp` | MCP Streamable HTTP endpoint |

Demo bootstrap must create synthetic users/credentials only through an explicit demo command and must refuse those defaults in non-demo mode.

## 2. Screen with REST

The release contains `examples/requests/transaction-screening.json`.

Target request:

```bash
curl --fail-with-body \
  -H "Authorization: Bearer $TRADESIEVE_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: demo-quote-001-v1" \
  -H "X-Correlation-ID: crm-quote-001" \
  --data @examples/requests/transaction-screening.json \
  http://localhost:8080/v1/screenings
```

The [generated review-required response](../../examples/responses/transaction-screening.review-required.json)
is the executable decision-first shape. It includes stable screening/case IDs, typed
findings and evidence requirements, active holds, the canonical input and result hashes,
and explicit input/source/rule/matcher/model versions.

HTTP `201` means the result was created. It does not mean the transaction is cleared; the caller enforces `business_action`.

## 3. Use the CLI

Target installation and calls:

```bash
uv tool install tradesieve-cli==0.1.0
tradesieve configure set-url http://localhost:8080
tradesieve transaction validate --input examples/requests/transaction-screening.json
tradesieve transaction screen \
  --input examples/requests/transaction-screening.json \
  --idempotency-key demo-quote-001-v1
tradesieve case show case_... --json
tradesieve case evidence-required case_... --json
tradesieve source list --stale --json
```

The human output begins with signal, priority, state, action, reason, case ID, and required evidence. `--json` returns the canonical machine result. A zero exit code means the command succeeded, not that the business action may proceed.

## 4. Connect an AI agent through MCP

Target Streamable HTTP configuration:

```json
{
  "mcpServers": {
    "tradesieve": {
      "type": "http",
      "url": "http://localhost:8081/mcp",
      "headers": {
        "Authorization": "Bearer ${TRADESIEVE_AGENT_TOKEN}"
      }
    }
  }
}
```

Example safe agent workflow:

1. Call `screen_transaction` using structured synthetic facts.
2. Read `findings`, `required_evidence`, `source_refs`, and `permitted_next_actions`.
3. Call `explain_rule_evaluation` for a cited result.
4. With explicit user confirmation, call `request_human_review`.
5. Return the case link to the user.

The general agent tool set cannot create `HUMAN_CLEARED`, release a business hold, export bulk case data, fetch arbitrary URLs, or access arbitrary files.

## 5. Human review

The reviewer signs in through the configured identity provider, then:

1. opens the P0/P1 queue;
2. reads verified facts, unknowns, source assertions, rules, candidate match evidence, and model suggestions in separate sections;
3. requests or attaches approved synthetic/pilot evidence;
4. resolves each finding with rationale;
5. records a scoped decision with action, business object, evidence, version set, reason, and expiry;
6. confirms step-up/four-eyes requirements when configured.

No green state is effective until this human event exists. A source/input/rule change or expiry can return it to review.

## 6. Connect CRM or OMS

### Before an action

1. Map the source object into the canonical request.
2. Use a stable external object/action ID and idempotency key.
3. Submit before quote release, order acceptance, booking, shipment release, or payment.
4. Persist TradeSieve IDs, state, action, expiry, and short reason.
5. Enforce `HOLD`, `REQUEST_EVIDENCE`, or `ESCALATE` locally.

### On a webhook

1. Verify signature and timestamp.
2. Deduplicate `event_id`.
3. Match tenant/external object/case IDs.
4. Re-read the case from TradeSieve.
5. Release only the exact named action when an effective human decision exists.
6. Record the downstream action and acknowledgement for reconciliation.

### On failure

- timeout/unavailable: hold and retry/reconcile;
- stale required source: hold and route to operations/compliance;
- `401/403`: hold and fix identity/authorization;
- schema error: hold and correct mapping;
- webhook failure: do not infer state; poll/reconcile by case ID.

## 7. Verify the installation

The release must include one command that runs the golden path and returns non-zero when any invariant fails. Target form:

```bash
./scripts/demo_golden_path.sh
```

The script must verify:

- API/CLI/MCP result parity;
- idempotent retry;
- evidence-oriented `HOLD` result;
- service/agent denial of human clearance;
- authorized human decision and signed webhook;
- source delta rescreen/invalidation;
- replay and redacted evidence/audit export.

## Production handoff checklist

The demo is not a production deployment. Before live data/actions, complete:

- approved legal nexus/regime/source coverage and licences;
- real CRM/OMS/payment field/data-quality assessment;
- identity, reviewer roles, four-eyes, expiry, and operations ownership;
- matching benchmark/threshold and reviewer workload acceptance;
- privacy/DPIA/automated-decision and retention review;
- threat model, penetration/security testing, secret/storage/network design;
- backup/restore/DR, capacity, monitoring, incident and fail-closed runbooks;
- production release sign-off by product, engineering, compliance, and security.
