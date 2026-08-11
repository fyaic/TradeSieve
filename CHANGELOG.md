# Changelog

All notable changes to TradeSieve are documented here. Versions follow Semantic Versioning for public interfaces and PEP 440 for the Python package.

## [0.1.0a2] - 2026-08-11

Agent-interface and delivery hardening preview.

### Added

- Local read-only `tradesieve-mcp` stdio server with one canonical
  `screen_transaction` tool over the active official-source bundle.
- Direct MCP protocol probe, independent Codex sub-Agent acceptance evidence and a
  project-scoped Codex configuration example.
- Consolidated external-service/outbound dependency register and a screenshot-backed
  CRM/API/CLI/MCP demonstration guide.
- Locked Redocly documentation tooling and a non-security bug-report template.

### Changed

- README, release handoff, architecture decisions and Agent guidance now distinguish
  the implemented local stdio tool from the future remote MCP/OAuth boundary.
- Security policy now states supported versions, acknowledgement target and a private
  fallback reporting route.

### Safety boundary

- MCP is read-only and exposes no clearance, case mutation, arbitrary search, bulk
  export, filesystem, SQL or network tool.
- stdio inherits its trusted host process identity. Remote Streamable HTTP,
  OAuth/OIDC, tenant/tool authorization and case/review tools are not included.

## [0.1.0a1] - 2026-08-11

First distributable engineering prototype.

### Added

- Atomic PostgreSQL activation of verified EU FSF, EU dual-use Annex I, OFAC SDN and OFAC Consolidated source projections.
- Exact strong-identifier and exact normalized-name candidate evidence with source-native locators and hashes.
- Explicit Annex I candidate assessment plus a bounded, source-bound `3A001` technical-assertion bundle.
- BIS Common High Priority List exact HS-6 candidate signaling for all 50 official guidance items.
- Authenticated `POST /v1/official-screenings`, active-source CLI and a five-record synthetic China international-logistics CRM demonstrator.
- Immutable intake, authorization, audit, rule-bundle and source-snapshot foundations with PostgreSQL 18.4 acceptance gates.
- Reproducible Python package, Docker Compose reference deployment, generated OpenAPI/JSON Schema and handoff documentation.

### Safety boundary

- Every result has `automatic_clearance=false`; only an authorized human may release a real business action.
- No fuzzy/transliteration matching, ownership/control propagation, OFAC 50 Percent Rule, complete Russia goods/route/legal-effect engine, general workbook ingestion, production case workflow, OIDC tenancy, webhook or MCP server is included.
- CHPL, HS and technical candidates do not constitute customs/export classification, prohibition, licence determination or clearance.

[0.1.0a2]: https://github.com/fyaic/TradeSieve/releases/tag/v0.1.0-alpha.2
[0.1.0a1]: https://github.com/fyaic/TradeSieve/releases/tag/v0.1.0-alpha.1
