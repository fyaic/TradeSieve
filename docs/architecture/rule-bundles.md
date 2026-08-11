# Versioned rule bundles

**Status:** Implemented TS-205 foundation for one explicitly synthetic internal-policy bundle. Official source snapshots/provisions remain TS-202; general completeness findings and holds remain TS-303.

## Safety boundary

The demo bundle contains two finite presence evaluators: one checks whether configured transaction facts are present and one checks whether legal-nexus facts are present. Presence is not truth, applicability, legality, or clearance. The bundle and its private policy text state that limitation, and neither evaluation nor the operations projection can create `HUMAN_CLEARED`.

Production legal scope is unresolved. The demo therefore cites versioned internal synthetic policy and does not fabricate an official source snapshot, legal provision, jurisdictional conclusion, licence determination, or sanctions-free claim.

## Immutable content and identity

A rule bundle is scoped by `tenant_id`, `deployment_id`, and `rule_set_id`. Its immutable identity is:

```text
bundle_id + semantic version + canonical SHA-256 content hash
```

The hash covers the effective window, owner/author, ordered rules, evaluator specifications, citations, fixtures, and private notes. Each rule also has its own version and content hash. The strict PostgreSQL codec requires the exact versioned payload shape, recomputes every rule/bundle hash on read, and rejects unknown, missing, malformed, cross-scope, or hash-mismatched content.

Each rule carries:

- a bounded evaluator kind and explicit scope;
- an effective window;
- an accountable owner;
- one or more typed citations;
- deterministic positive/missing fixtures that must pass before approval/activation.

Internal-policy citations store private text but expose only policy identity, locator, and derived content hash. The reserved official-source citation carries an immutable snapshot ID/hash and provision locator; readiness defaults to unavailable unless an injected TS-202 resolver returns the exact boolean `True`.

## Lifecycle and governance

Lifecycle state is folded from append-only events:

```text
DRAFTED → APPROVED → ACTIVATED → RETIRED
                         ↘ ROLLED_BACK
```

Approval and activation require valid fixtures/citations/effective windows and a human actor distinct from the author. Activation, retirement, and rollback carry a deterministic rescreen-impact document. A transactional state projection stores the last sequence and active pointer, but reads lock/fold the bounded history and reject any disagreement rather than trusting the projection alone.

Every command is bound to an `AuthorizedRequest` returned by `AuthorizationService`. Demo code never constructs that proof directly. The PostgreSQL unit of work commits content/event/state/command audit atomically, and database constraints require each applied lifecycle event to have exactly one command audit linked to an existing `ALLOW` authorization event with the same actor type/subject, tenant, operation, and typed target. Bundle content, lifecycle events, command audits, and authorization decisions are append-only.

## Deterministic demo bootstrap

`bootstrap-demo` uses three distinct human demo identities with exact entitlements:

| Identity | Allowed operation |
| --- | --- |
| `demo-policy-author` | Draft the configured demo rule set |
| `demo-policy-approver` | Approve and activate the exact immutable demo bundle |
| `demo-policy-operator` | Read the configured demo rule set |

The bootstrap accepts only the exact empty, draft, approved, or active prefix for the built-in bundle. It continues an exact partial lifecycle, leaves an exact active bundle unchanged, and fails on changed content, actors, reasons, event order, state, or active pointer. Repeating bootstrap records the new read authorization decision but creates no duplicate bundle, lifecycle event, or command audit.

The identities and entitlement resolver exist only in `TRADESIEVE_MODE=demo`. Production configuration rejects demo/synthetic tenant, deployment, source-set, and rule-set identities, and disables demo bootstrap.

## Readiness and operations view

Rule readiness is `OK` only when all of the following are true for the configured tenant/deployment/rule set:

1. bounded lifecycle history decodes and folds successfully;
2. the state projection exactly matches that history;
3. the active pointer identifies a stored bundle with the same canonical hash;
4. the bundle and every rule are effective at the current UTC time;
5. activation governance still has no blocking issue;
6. every official citation, if present, verifies through the injected resolver.

Any database, decode, projection, content, expiry, governance, or citation-verification problem fails closed. Public health exposes only generic `OK`/`UNAVAILABLE` status.

The private demo command below uses the same PostgreSQL repository, real authorization decision, and safe application projection:

```bash
docker compose run --rm --no-deps app \
  python -m tradesieve.manage list-rules
```

It is an operations inspection surface, not REST/MCP screening parity. It omits private policy text, internal notes, fixture bodies, and authorization internals. Production mode returns a fixed `DISABLED` envelope before any demo identity is synthesized.

## Deferred work

- TS-202 must provide immutable official-source snapshot/provision resolution and broader version-ledger behavior.
- TS-303 must turn missing facts into canonical findings/evidence requests/holds and integrate these evaluators into screening orchestration.
- REST, public screening CLI, and MCP adapters must prove canonical contract parity. The later official-source vertical slice proves this for `OfficialScreeningRequest`/`OfficialScreeningResult`; TS-205 itself remains only the rule-bundle inspection proof.
