# Open-source projects and standards

**Review date:** 2026-08-06. Repository activity and licences should be rechecked before adoption.

## Sanctions/entity screening

### OpenSanctions yente

- Repository: [opensanctions/yente](https://github.com/opensanctions/yente)
- Role: search and bulk matching of FollowTheMoney entities, including people, companies, and vessels.
- Deployment: supports on-premises operation with Elasticsearch/OpenSearch.
- Code licence: MIT.
- Data caveat: commercial use of OpenSanctions data requires a data licence even though yente code is MIT.
- Recommendation: **prototype candidate** for party matching; do not delegate case disposition or legal-source governance to it.

The `/match` API uses multiple properties such as names, dates, nationality, tax identifiers, and addresses, which is materially safer than single-name search. Source: [OpenSanctions API](https://api.opensanctions.org/).

### yente-client

- Repository: [opensanctions/yente-client](https://github.com/opensanctions/yente-client)
- Role: typed SDK, CLI tooling, and MCP server for yente.
- Code licence: MIT.
- Recommendation: **direct reference implementation** for TradeSieve's adapter design; validate protocol-version support and security boundaries before reuse.

### Moov Watchman

- Repository: [moov-io/watchman](https://github.com/moov-io/watchman)
- Role: Go service for downloading, parsing, searching, and monitoring multiple sanctions/watch lists; includes API, UI, and webhooks.
- Code licence: Apache-2.0.
- Recommendation: **benchmark and connector reference**, especially for official list parsing and deployability.
- Caveat: configuration and source defaults must be audited against current law. For example, the old UK OFSI consolidated list stopped updating on 2026-01-28, while Watchman documentation still distinguishes the newer UKSL connector.

## Entity and ownership data models

### FollowTheMoney

OpenSanctions uses FollowTheMoney to represent people, companies, sanctions, addresses, identifiers, and relationships as an entity graph. Its lineage-friendly format is a useful starting point for TradeSieve ingestion.

- [OpenSanctions entity structure](https://www.opensanctions.org/docs/entities/)
- [FollowTheMoney-based JSON](https://www.opensanctions.org/docs/bulk/json/)

Recommendation: **adapt concepts, not blindly adopt the entire ontology**. TradeSieve additionally needs bitemporal validity, legal provisions, products, transactions, documents, findings, and human decisions.

### Beneficial Ownership Data Standard

- Repository: [openownership/data-standard](https://github.com/openownership/data-standard)
- Standard: [BODS](https://standard.openownership.org/)
- Schema licence: Apache-2.0; documentation has its own stated terms.
- Recommendation: **use as an interchange/reference model** for entity, person, ownership/control statement, provenance, and interest structures.

### GLEIF

Use LEI and available Level 2 relationship data as identifiers and evidence, not as a complete global ownership graph. GLEIF provides API and bulk Golden Copy data.

- [GLEIF API](https://www.gleif.org/en/lei-data/gleif-api)
- [Access and use LEI data](https://www.gleif.org/en/lei-data/access-and-use-lei-data)

## Entity-resolution research tooling

### Splink

- Repository: [moj-analytical-services/splink](https://github.com/moj-analytical-services/splink)
- Code licence: MIT.
- Method: scalable probabilistic record linkage based on Fellegi-Sunter, with blocking, fuzzy comparisons, term-frequency adjustment, and model diagnostics.
- Recommendation: **evaluate for historic deduplication, calibration, and ownership/entity linking**, not as a sanctions decision engine.

### Ditto

- Repository: [megagonlabs/ditto](https://github.com/megagonlabs/ditto)
- Code licence: Apache-2.0.
- Method: Transformer-based pair classification for entity matching.
- Recommendation: **research baseline only**. The repository's runtime stack is older, and a learned pair classifier does not provide the source/rule/case controls the product requires.

### OpenSanctions Pairs

- Paper/code: [OpenSanctions Pairs](https://arxiv.org/abs/2603.11051), [OSINT_entity_resolution](https://github.com/chansmi/OSINT_entity_resolution)
- Role: real-world multilingual sanctions-entity pair benchmark.
- Recommendation: **use as an external benchmark and methodology reference**, subject to dataset licence and contamination review.

## Policy and decision infrastructure

### Open Policy Agent

- Repository: [open-policy-agent/opa](https://github.com/open-policy-agent/opa)
- Code licence: Apache-2.0.
- Strengths: policy/data separation, bundle distribution, bundle signatures, decision IDs/logs, REST and SDK integration.
- References: [OPA overview](https://www.openpolicyagent.org/docs), [signed bundles](https://www.openpolicyagent.org/docs/management-bundles), [decision logs](https://www.openpolicyagent.org/docs/management-decision-logs).
- Recommendation: **prototype**, while also testing whether compliance authors need a constrained decision-table DSL compiled to Rego.

OPA should evaluate already-normalized facts. It should not parse regulations or infer legal meaning from prose at runtime.

## Interface standards

| Standard | Planned use | Source |
| --- | --- | --- |
| MCP `2026-07-28` | Agent tools/resources and bounded review workflows | [Release](https://blog.modelcontextprotocol.io/posts/2026-07-28/) |
| OpenAPI 3.1.x | Canonical REST contract | [Specification](https://spec.openapis.org/oas/) |
| JSON Schema 2020-12 | Shared request/result/evidence schemas | [Specification](https://json-schema.org/specification) |
| AsyncAPI | Event contracts if/when a broker is selected | [Documentation](https://www.asyncapi.com/docs) |
| CLI Guidelines | Composable stdout/stderr, JSON output, errors, help | [clig.dev](https://clig.dev/) |
| RFC 9068 | OAuth 2.0 JWT access-token profile and resource-server validation | [RFC Editor](https://www.rfc-editor.org/rfc/rfc9068.html) |
| RFC 8725 | JWT security best current practices and cross-JWT defenses | [RFC Editor](https://www.rfc-editor.org/rfc/rfc8725.html) |

## Reuse gate

No project is adopted solely because it is open source. A decision requires:

- current maintenance and security posture;
- code and data licence review;
- source freshness and jurisdiction coverage;
- reproducible tests on TradeSieve fixtures;
- operability, observability, and failure-mode review;
- privacy/data-locality fit;
- a replacement or exit strategy.
