# Regulatory and official data sources

**Access date:** 2026-08-06
**Rule:** official primary sources control legal interpretation; aggregated/community datasets are implementation aids.

## Source map

| Domain | Official source | Machine use | TradeSieve use | Critical caveat |
| --- | --- | --- | --- | --- |
| EU designated parties | [EU financial sanctions consolidated list](https://finance.ec.europa.eu/eu-and-world/sanctions-restrictive-measures/overview-sanctions-and-related-resources_en) | Commission portal; formats/access require connector validation | Party designations and source routing | Confirm controlling Official Journal/EUR-Lex act for a material decision |
| EU regimes/legal acts | [EU Sanctions Map](https://www.sanctionsmap.eu/) and [EUR-Lex](https://eur-lex.europa.eu/) | Web/legal documents | Regime discovery, scope, provisions, annexes | A country or keyword is not a complete legal rule |
| EU dual-use | [Regulation (EU) 2021/821](https://eur-lex.europa.eu/eli/reg/2021/821) | HTML/PDF/XML via EU publications services | Versioned Annex I technical controls | Latest consolidated version included the 2025 update and a 2026 corrigendum at research time |
| EU customs/trade measures | [TARIC](https://taxation-customs.ec.europa.eu/customs/common-customs-tariff-cct/tariff-classification-goods/eu-customs-tariff-taric_en) | Database; daily transmission to member-state systems | CN/TARIC candidate measures and destination checks | Customs code does not replace technical dual-use classification |
| Russia trade restrictions | [Regulation (EU) No 833/2014](https://eur-lex.europa.eu/eli/reg/2014/833) | Consolidated acts plus amendments | Versioned prohibitions, services, and goods annexes | Check the latest consolidated text and later Official Journal amendments |
| Russia anti-circumvention | [Commission enhanced due-diligence guidance](https://finance.ec.europa.eu/document/download/3c86c9a8-f09e-4092-ab8c-a9e678df1494_en?filename=guidance-eu-operators-russia-sanctions-circumvention_en.pdf) | PDF | Red-flag taxonomy and evidence requirements | Guidance supports but does not replace binding acts |
| US sanctions | [OFAC Sanctions List Service](https://ofac.treasury.gov/sanctions-list-service) | Downloads, customized datasets, deltas/archives | SDN and non-SDN data, historical deltas | Program-specific effects differ; list names alone do not encode every prohibition |
| US ownership | [OFAC FAQ 401](https://ofac.treasury.gov/faqs/401) | Human-readable guidance | Ownership-rule configuration and graph tests | The 50 Percent Rule requires direct/indirect aggregate ownership evidence |
| US export screening | [Consolidated Screening List](https://www.trade.gov/consolidated-screening-list) | CSV/TSV/JSON/API | BIS/State/Treasury screening aid | The API contains active records; verify official underlying publications and plan separately for history |
| UK designations | [UK Sanctions List](https://www.gov.uk/government/publications/the-uk-sanctions-list) | Static CSV/XML/TXT/JSON-like formats listed by UK | UK party, ship, regime, and measure records | Since 2026-01-28 the old OFSI consolidated list is no longer updated |
| UN designations | [UN Security Council Consolidated List](https://main.un.org/securitycouncil/en/content/un-sc-consolidated-list) | XML/HTML/PDF | UN reference numbers, aliases, identifiers, regimes | Measures remain regime-specific and must be implemented through applicable law |
| Legal entities/relationships | [GLEIF LEI data](https://www.gleif.org/en/lei-data/access-and-use-lei-data) | API and Golden Copy files | Stable identifiers and available Level 2 relationship evidence | LEI coverage and reported parent relationships are incomplete for global ownership |
| Beneficial ownership schema | [BODS](https://standard.openownership.org/) | JSON Schema/open tooling | Relationship interchange and provenance patterns | Registry availability, verification, privacy, and licence vary by jurisdiction |

## Freshness observation

The EU adopted a 21st Russia sanctions package on 2026-07-23, including amendments to Regulation 833/2014 and large new listing batches. This is direct evidence that a yearly/manual refresh is not an acceptable operating model.

Source: [Council of the EU, 21st package](https://www.consilium.europa.eu/en/press/press-releases/2026/07/23/21st-package-of-sanctions-eu-hits-russian-energy-financial-services-and-crypto-hard/).

## Source lifecycle

Every connector should implement the following lifecycle:

1. Register source owner, jurisdiction, scope, legal basis, access method, and licence.
2. Retrieve to a quarantine area without executing active content.
3. Record retrieval time, source-reported update/effective dates, HTTP metadata, and SHA-256.
4. Preserve the exact raw object; never overwrite a prior version.
5. Parse into normalized assertions while retaining source-native values and record locators.
6. Validate schema, counts, identifiers, effective dates, and unexpected deletions.
7. Diff against the prior accepted version.
8. Require approval for parser/rule changes; automatically activate source-only deltas only under an approved policy.
9. Identify affected open relationships/cases and queue rescreening.
10. Retain rollback and replay capability.

## Historical backfill

Backfill has two different questions:

- **Current-exposure replay:** screen historic customers/orders/receivables against current sources and rules.
- **As-of-time reconstruction:** determine what facts, lists, and rules were effective when the action occurred.

The second requires archived official publications, vendor history, or internally preserved snapshots. Current-only APIs cannot reconstruct delisted entities or prior rule text. TradeSieve should clearly label `CURRENT_REPLAY` and `AS_OF_REPLAY`; they must never be conflated.

## Country/regime modelling

Model a jurisdiction matrix rather than `country = banned`:

- issuing jurisdiction and legal nexus;
- sanctions regime and legal act;
- measure type and affected activity;
- party/ownership/control scope;
- goods/services/technology scope;
- destination, origin, transit, end use/user;
- licence, exception, derogation, notification, and expiry;
- effective dates and source version.

The European Commission notes that the EU maintains more than 40 regimes with targeted measures of different types. Source: [EU sanctions overview](https://finance.ec.europa.eu/eu-and-world/sanctions-restrictive-measures/overview-sanctions-and-related-resources_en).

## Licence and access gate

Before a source enters production, record:

- permission for commercial/internal use;
- redistribution and derivative-data restrictions;
- storage and retention limits;
- whether the source may be exposed through API/CLI/MCP;
- attribution obligations;
- personal-data and cross-border constraints;
- rate limits, credentials, and availability expectations.
