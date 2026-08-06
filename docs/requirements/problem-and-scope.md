# Problem, scope, and success criteria

## Problem statement

A China-based international-logistics operator needs to reduce sanctions and export-control exposure across counterparties, ownership, goods, routes, documents, and payments. Existing legal checks focus on domestic corporate registries, court records, and internal blacklists; they do not provide a maintained overseas sanctions/export-control control plane.

The primary failure mode is not simply missing a name on a list. It is allowing a business action to proceed when the relevant party, ownership chain, product classification, destination, end user, payment path, or source version is incomplete or contradictory.

## Users

| User | Need |
| --- | --- |
| Sales/customer operations | Know what evidence is missing before a quote or onboarding proceeds |
| Logistics/booking | Hold or route a shipment before booking/release |
| Finance/treasury | Rescreen payer, payee, banks, and transaction before payment |
| Legal/compliance | Review evidence, resolve matches, record rationale, and release or escalate |
| Data/engineering | Maintain sources, matching, rules, APIs, CLI, MCP, and auditability |
| AI agents | Retrieve structured evidence and perform bounded, review-safe tasks |

## In scope for discovery

### Party and ownership controls

- Buyer, seller, shipper, consignee, notify party, end user, payer, payee, banks, agents, carriers, vessels, directors, owners, and controllers.
- Names, aliases, non-Latin scripts, transliterations, registration/identity identifiers, addresses, dates, and nationalities where lawful and necessary.
- Direct/indirect ownership and control edges with source, confidence, validity period, and reviewer status.

### Goods and technology controls

- Commercial description, manufacturer, model/part number, technical specifications, software/technology, origin, HS/CN/TARIC, proposed control code, and end use.
- EU dual-use Annex I and applicable sanctions annexes as versioned legal sources.
- Candidate flags such as Common High Priority items; candidates are not final classifications.

### Transaction controls

- Origin, route, transit, destination, installation/use location, carriers, vessels, banks, currency, and payment chain.
- Cross-document consistency across order, invoice, packing list, bill of lading, technical documents, and end-user statement.
- Circumvention indicators and unusual commercial explanations.

### Platform controls

- Immutable source snapshots and hashes.
- Current and as-of-time screening.
- Rescreening on source, party, goods, route, or payment changes.
- Human case review, segregation of duties, release expiry, and full audit trail.
- REST/OpenAPI, events/webhooks, CLI, and MCP adapters over one business contract.

## Out of scope until explicitly authorized

- Legal advice, licence applications, delisting petitions, or communication with regulators/banks.
- Autonomous approvals or autonomous termination of customer relationships.
- Circumvention, concealment, relabelling, route substitution, or evasion advice.
- Customs classification based only on AI or HS code.
- Production adverse-media profiling of natural persons without a privacy/legal basis.
- Purchasing commercial datasets or sending live business data to third parties.

## Initial decision model

| State | Meaning | Default action |
| --- | --- | --- |
| `ESCALATE` / P0 | Possible prohibition, list/ownership match, circumvention, falsification, or unresolved licence question | Stop affected action and preserve evidence |
| `REVIEW_REQUIRED` / P1 | Material party, goods, route, end-use, payment, or document facts missing/contradictory | Hold and request evidence |
| `MONITOR` / P2 | No open P0/P1 in the reviewed scope, but correction or monitoring remains | Continue only under configured policy |
| `HUMAN_CLEARED` | Authorized reviewer closed all P0/P1 for a defined action, scope, and time | Permit the named action until expiry/change |

`HUMAN_CLEARED` is not a statement that law permits every related activity.

## Success criteria for an MVP

- Every screening response has a stable decision ID and can be replayed from preserved input, source versions, and rule versions.
- High-confidence identifier matches are not lost in fuzzy-name noise.
- Cross-script and alias test sets have measured recall and reviewer workload.
- CRM/OMS receives a bounded action: hold, request evidence, escalate, monitor, or human-cleared.
- No production action is released solely by an LLM output.
- New or changed source records trigger targeted rescreening of affected open cases.
- The system can explain why a case was held and what specific evidence is needed.
- CLI, MCP, and API return equivalent typed results for equivalent calls.

## Open questions

1. Which legal nexuses and banking/contractual policies must the first release support?
2. Which CRM, OMS, booking, finance, and master-data systems will call the service?
3. What fields and documents exist today, and what is their completeness by workflow stage?
4. What historic period must be replayed, and is as-of-date reconstruction legally required?
5. Which commercial data licences, if any, are available for ownership, vessels, trade flows, and historic records?
6. Who can hold, resolve, override, and release, and what are the required service levels?
7. What is the acceptable reviewer workload and false-negative tolerance for each gate?
