# Changelog

All notable changes to TradeSieve are documented here. Versions follow Semantic Versioning for public interfaces and PEP 440 for the Python package.

## [0.1.0a6] - 2026-08-12

Delivery-asset quality repair; screening behavior is unchanged from `0.1.0a5`.

### Fixed

- Recaptured the four synthetic CRM states from the running application at 2.5K-class resolution and converted every delivery screenshot to a genuine lossless PNG.
- Rebuilt the REST/CLI/MCP parity visual at 2560×1600 and made README screenshots clickable to their original files.
- Regenerated the Chinese walkthrough PDF with the sharp assets instead of the mislabeled, lossy JPEG captures shipped in earlier alphas.

### Added

- A repository gate that rejects missing, non-PNG or undersized walkthrough screenshots before release.

## [0.1.0a5] - 2026-08-12

Business handoff and demonstrator reliability preview; official-source screening semantics are unchanged from `0.1.0a4`.

### Added

- Business-oriented repository front page with stable PDF, CRM, REST, CLI and MCP entry points and screenshot-backed outcomes.
- One-command local business demo bootstrap that verifies source refresh, active screening, service readiness and the synthetic CRM before declaring success.
- Business delivery pack with a scoped 95/100 demonstration/integration-readiness score and a separate technical handoff for engineering evidence and production blockers.
- Same-run REST/CLI/MCP parity evidence and a visual summary suitable for non-technical reviewers.

### Changed

- Repository-owned synthetic demo source fixtures now warn after seven days and expire after thirty days, so a long-running local demo does not become unavailable after two hours. The official four-source active bundle remains limited to 48 hours.
- Business progress wording is shorter and non-technical; detailed test, migration and live-source evidence moved to the technical handoff.
- PDF and release metadata now target `0.1.0a5`.

### Safety boundary

- The 95/100 score applies only to business demonstration and synthetic integration handoff, not production or legal fitness.
- Production use remains blocked on qualified policy review, case/reviewer workflows, ownership/control propagation, complete Russia/export-control effects, production identity/operations and a real CRM mapping exercise.

## [0.1.0a4] - 2026-08-11

Documentation correctness repair; screening behavior is unchanged from `0.1.0a3`.

### Fixed

- Pass host request files into the running Compose app through bounded standard input
  in the README, CLI guide and release handoff, instead of referring to a path that
  does not exist inside the hardened application image.
- Add a regression assertion for the executable container CLI example.

## [0.1.0a3] - 2026-08-11

Release-pipeline repair; application behavior is unchanged from `0.1.0a2`.

### Fixed

- Verify portable wheel/source-archive checksum entries from the directory containing
  the built artifacts, with a repository regression assertion for the working path.

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

[0.1.0a5]: https://github.com/fyaic/TradeSieve/releases/tag/v0.1.0-alpha.5
[0.1.0a6]: https://github.com/fyaic/TradeSieve/releases/tag/v0.1.0-alpha.6
[0.1.0a4]: https://github.com/fyaic/TradeSieve/releases/tag/v0.1.0-alpha.4
[0.1.0a3]: https://github.com/fyaic/TradeSieve/releases/tag/v0.1.0-alpha.3
[0.1.0a2]: https://github.com/fyaic/TradeSieve/releases/tag/v0.1.0-alpha.2
[0.1.0a1]: https://github.com/fyaic/TradeSieve/releases/tag/v0.1.0-alpha.1
