# Industry patterns

This review identifies repeatable product capabilities. It is not a vendor recommendation or procurement decision.

## Products reviewed

### LSEG World-Check One

Official product material describes REST/JSON API integration, onboarding and transaction screening, secondary identifiers, ongoing/delta screening, internal watchlists, webhooks, case remediation, UBO/vessel add-ons, and audit reports.

- [World-Check One API](https://developers.lseg.com/en/api-catalog/customer-and-third-party-screening/world-check-one-api)
- [World-Check One product](https://www.lseg.com/en/risk-intelligence/screening-solutions/world-check-kyc-screening/one-kyc-verification)

Pattern to adopt: separate screening/case services that integrate with CRM rather than forcing all review into CRM.

### SAP Global Trade Services

SAP GTS integrates sanctioned-party, embargo, product classification, licence, logistics-document, and payment checks with feeder systems. Its workflow blocks documents for authorized review and retains audit history.

- [SAP GTS product](https://www.sap.com/products/financial-management/global-trade-management.html)
- [SPL screening for logistics](https://help.sap.com/docs/SAP_GLOBAL_TRADE_SERVICES/bdb1d2fb216941a69f6300006343e977/4bce23cfb7d23c18e10000000a42189b.html)
- [Payment screening integration](https://help.sap.com/docs/SAP_S4HANA_ON-PREMISE/3cb1182b4a184bdd93f8d62e3f1f0741/f6eed353ca9f4408e10000000a174cb4.html)

Pattern to adopt: run controls at business events and preserve a worklist/release workflow, including payment gates.

### Sayari Graph

Sayari positions its platform around resolved corporate entities, ownership/control, shipment/trade flows, primary-source records, and graph investigations.

- [Sayari Graph](https://sayari.com/platform/graph/)

Pattern to adopt: model sourced relationship edges and make ownership/trade paths explainable. Procurement implication: global ownership/trade-flow coverage is a data product, not a one-time crawler task.

### Kharon

Kharon emphasizes analyst-verified networks beyond explicit lists, including ownership/control, export-control, military end-use, and evasion relationships.

- [Kharon platform](https://www.kharon.com/)
- [Kharon approach](https://www.kharon.com/about)

Pattern to adopt: distinguish list matches from network exposure and attach evidence/rationale to relationships. Procurement implication: research depth and update service may be more important than raw record count.

### e2open Global Trade

e2open combines restricted-party screening, country controls, product classification, export-control numbers, licence determination/tracking, and documents.

- [Global Trade suite](https://www.e2open.com/global-trade/)
- [Export Management](https://www.e2open.com/global-trade/export-management/)

Pattern to adopt: party, country, product, licence, and document controls are related but distinct modules.

## Common industry capability map

| Capability | Industry baseline | TradeSieve requirement |
| --- | --- | --- |
| Single/batch party screening | Yes | REST, CLI, and MCP parity |
| Secondary identifiers | Yes | Required; name-only results stay review candidates |
| Ongoing/delta rescreening | Yes | Source-change impact graph and targeted replay |
| Internal watchlists | Common | Versioned, approved, sourced, and expiry-aware |
| Case remediation | Yes | Required, with human release and evidence checklist |
| Audit reports | Yes | Immutable event/evidence history and reproducible replay |
| UBO/ownership | Often add-on/specialist data | Data-source-neutral graph with provenance |
| Vessel/trade flows | Often add-on/specialist data | Optional adapters with licence isolation |
| Product classification | Trade-management suites | Separate technical workflow; not merged into party score |
| CRM/ERP integration | API/feeder systems | Independent control plane plus events/webhooks |
| AI summaries/ranking | Increasingly common | Bounded assistance with model provenance; no autonomous clearance |

## Differentiation hypothesis

TradeSieve should not claim broader proprietary intelligence than established vendors. Its potential differentiation is:

- China-logistics workflow fit and local/private deployment;
- first-class API, CLI, and MCP over one contract;
- official-source and rule provenance exposed to reviewers and agents;
- modular provider adapters instead of vendor lock-in;
- as-of-time replay and source-delta impact analysis;
- explicit human-clearance boundary;
- party, goods, route, documents, and payment combined in one reviewed case.

## Vendor due-diligence questions

1. Which sources, jurisdictions, languages, entity types, and historical versions are included?
2. What are the redistribution, storage, model-training, API, and MCP exposure rights?
3. How are ownership/control and unlisted affiliates sourced and validated?
4. What is the update SLA, delta format, correction process, and historical archive?
5. Can production data remain in-region or on-premises?
6. Can results include source citations and stable identifiers?
7. How are false positives, deletions, and record merges propagated?
8. Can we benchmark on a controlled fixture set before purchase?
9. What are exit/export rights for cases, dispositions, and derived data?
10. Does the provider permit use by automated agents, and under what controls?
