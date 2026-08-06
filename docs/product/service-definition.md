# Service definition

**Status:** Phase 1 product baseline
**Last updated:** 2026-08-06

## One-sentence definition

TradeSieve is an independently deployable trade-compliance decision-support service that accepts a proposed business action, checks whether the available party, goods, route, end-use, document, payment, and source evidence is sufficient, creates traceable findings, and routes unresolved risk to an authorized human reviewer.

It is intentionally described as a **service**. A reviewer console, CLI, MCP server, source workers, and integration examples may ship with it, but they are interfaces and operational components around one bounded service—not a mandate to become a large standalone business system.

## Outcome, not implementation shape

The product outcome is:

> Before quote, order, booking, shipment release, or payment, the operator can obtain a reproducible answer explaining whether the action must be held, what evidence is missing, which source/rule versions were used, and who must decide next.

The product shape can evolve:

| Shape | When it is sufficient |
| --- | --- |
| Embedded library | A single trusted application needs local deterministic validation only |
| Independently deployed service | Multiple CRM/OMS/payment/agent clients need one control and audit boundary |
| Service plus reviewer console | Human case triage cannot be performed effectively through existing systems |
| Broader platform | Multiple tenants, source operations, investigations, and policy administration require independent workflows |

Phase 1 chooses **service plus a minimal reviewer console** because human-only clearance is a product invariant and must be demonstrable end to end.

## Jobs to be done

1. **Business gate:** “Before I commit to an action, tell me whether to hold it and exactly what is unresolved.”
2. **Compliance review:** “Show me the original facts, sources, rules, matches, uncertainty, and history so I can make and defend a scoped decision.”
3. **Integration:** “Give my CRM/OMS a stable, typed, idempotent contract without embedding changing legal logic in it.”
4. **Agent assistance:** “Let an authorized agent retrieve evidence and prepare a review without allowing it to issue legal clearance.”
5. **Source operations:** “Show which data/rule version was used and which open cases must be re-screened when it changes.”

## Service boundary

TradeSieve owns:

- screening requests and immutable input snapshots;
- source manifests and accepted source snapshots;
- normalized assertions and source lineage;
- findings, missing-evidence requests, holds, case state, human decisions, expiry, and audit events;
- canonical schemas and behavior shared by API, CLI, MCP, and webhooks.

TradeSieve does not own:

- customer, order, shipment, invoice, or payment master records;
- definitive customs or export-control classification;
- legal opinions, licence applications, or regulator/bank communication;
- a general corporate intelligence warehouse;
- autonomous customer rejection, relationship termination, or transaction clearance.

## Phase 1 value hypotheses

| Hypothesis | Phase 1 evidence |
| --- | --- |
| One control service is safer than rules duplicated across CRM/OMS | Same synthetic transaction produces the same result through API, CLI, and MCP |
| Evidence-oriented output reduces review time | Reviewer can identify every missing fact and source/rule version without reading raw logs |
| A modular monolith is enough for an MVP | One deployable image and PostgreSQL complete the vertical slice without cross-service inconsistency |
| AI-friendly interfaces improve operations without increasing decision authority | MCP agent can screen, explain, and request review but cannot create `HUMAN_CLEARED` |
| Integration can begin before full legal-data coverage | CRM sandbox can enforce hold/review actions using the versioned contract and synthetic/approved pilot sources |

## Product measures

Phase 1 measures are learning and operability metrics, not claims of legal effectiveness:

- percentage of submitted cases with all required fields;
- candidate recall and review precision on the approved synthetic fixture set;
- number of findings with complete source/rule provenance;
- reviewer time per case and cases created per 100 screenings;
- parity failures across REST, CLI, and MCP;
- source freshness and failed-update duration;
- webhook delivery success and duplicate-handling rate;
- number of automated or unauthorized paths capable of setting `HUMAN_CLEARED`—target: zero.

## Product language

- **Screening** means structured decision support, not legal clearance.
- **Finding** is a fact/rule/evidence issue requiring a response.
- **Candidate match** is a record proposed for human/deterministic resolution.
- **Hold** is an operational instruction for a named business action.
- **Human clearance** is a scoped, time-bounded reviewer decision; it is not a universal statement of legality.
- **Current replay** uses current sources/rules against past business facts.
- **As-of replay** reconstructs sources/rules effective at a past time. Phase 1 does not claim full as-of reconstruction.
