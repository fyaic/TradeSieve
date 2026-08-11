# External services and outbound dependencies

**Evidence date:** 2026-08-11
**Scope:** `0.1.0a2` engineering prototype.

TradeSieve does not require a commercial sanctions API. Ordinary screening reads a
previously verified active bundle from the deployment's PostgreSQL database. Network
access is separated from business-request handling: only the source-refresh or
explicit live-diagnostic operation retrieves official publications.

## Runtime dependency by operation

| Operation | Required dependency | Outbound internet | Customer data sent out |
|---|---|---:|---:|
| REST `POST /v1/official-screenings` | PostgreSQL with a fresh complete active bundle | No | No |
| CLI `screen-active` | PostgreSQL with a fresh complete active bundle | No | No |
| MCP `screen_transaction` | Same PostgreSQL/application service through a trusted stdio host | No | No |
| Synthetic CRM screening | Same PostgreSQL/application service | No | No |
| `refresh-official-sources` | PostgreSQL plus the allow-listed official endpoints below | Yes | No |
| `screen-official` diagnostic | The same official endpoints; result is not activated | Yes | No |
| BIS CHPL candidate lookup | Built-in versioned, hash-bound guidance snapshot | No | No |

All business request bodies stay inside the TradeSieve deployment. Refresh requests
are HTTP GET operations for public source material and contain no CRM/order/customer
payload. The connector user agent identifies TradeSieve; retrieved bytes, hashes,
timestamps and normalized projections are stored locally.

## Official publication allow-list

| Source | Hosts and purpose | Authentication | Current behavior |
|---|---|---|---|
| EU Financial Sanctions File | `data.europa.eu` dataset discovery; `webgate.ec.europa.eu` FSF XML download | None | Discover exact expected distribution, enforce host/path/content bounds, parse and hash |
| EU dual-use Annex I | `publications.europa.eu` fixed CELLAR Formex item for CELEX `32025R2003` | None | Fixed host/path and byte/schema/count bounds; not a dynamic “latest law” resolver |
| OFAC SDN and Consolidated | `sanctionslistservice.ofac.treas.gov` fixed SLS endpoints; constrained redirect to `wc2h-sls-prod-public-published.s3.us-gov-west-1.amazonaws.com` | No caller credential; OFAC issues a signed redirect | Permit only the expected host/path/query fields and bounded XML payloads |
| BIS Common High Priority List | `www.bis.gov` is the recorded public provenance URL | None | No runtime call: 50 HS-6 candidates are an embedded snapshot checked on 2026-08-11 |

The EU reuse-licence metadata and exact source identifiers are validated by the
connectors. Before production use, the operator must complete a documented legal,
licence, retention, redistribution and personal-data review for every enabled source.
An API/CLI/MCP result exposes evidence identifiers and locators, not bulk raw-source
files.

## Failure and freshness behavior

- A refresh succeeds only when all four dynamic projections—EU FSF, EU Annex I,
  OFAC SDN and OFAC Consolidated—validate and commit as one atomic bundle.
- Download, redirect, TLS, schema, count, hash, parser or database failure leaves the
  prior active pointer unchanged and exits non-zero.
- Screening refuses an absent, incomplete, corrupt, future-dated or older-than-48-hour
  bundle. REST returns a generic `503`; CLI exits `2`; MCP returns a bounded tool error.
- Callers must preserve `HOLD`; no interface falls back to an empty list or green.
- CHPL does not update itself. Its access date and content hash make staleness visible;
  updating it requires a reviewed code change and a new release.

## Deployment-owned services and storage

| Component | Prototype default | Production responsibility |
|---|---|---|
| Transaction/source store | PostgreSQL 18.4 container and named volume | Managed/HA PostgreSQL, encrypted backups, access controls and recovery tests |
| Raw source evidence | Private local named volume | Private object storage with encryption, retention, immutable/versioned policy and malware/content controls |
| Identity | Local demo token for one REST preview; process identity for CLI/MCP | OIDC/workload identity, secret manager, tenant/tool scopes, rotation and auditable actor identity |
| MCP transport | Local stdio only | Remote Streamable HTTP only after OAuth/OIDC, tenant authorization, rate limits and tool audit are implemented |
| Scheduling | Manual refresh command | Named operator, approved frequency, alerts, retries and staleness escalation |

The prototype does not connect to OpenSanctions, Dow Jones, LSEG/World-Check,
LexisNexis, ComplyAdvantage, a hosted LLM, a vector database or any other commercial
screening service. Such systems may be evaluated later behind replaceable ports; they
are neither hidden dependencies nor prerequisites for this release.

## Build, test and distribution dependencies

These do not receive business data, but a clean build may need them:

| Dependency | Use | Reproducibility/control |
|---|---|---|
| GitHub | Private source, Issues, pull requests, Actions and release assets | Least-privilege workflow permissions and commit-pinned actions |
| Python package index through `uv` | Locked Python build/runtime dependencies | `uv.lock`, pinned `uv` and locked installs |
| `ghcr.io` | Digest-pinned `uv` build image | Image digest pinned in Dockerfile |
| `mirror.gcr.io` | Digest-pinned Python and PostgreSQL images | Image digests pinned in Dockerfile/Compose |
| npm registry | Locked Redocly OpenAPI lint dependency in documentation CI | `package-lock.json`, `npm ci`, no runtime use |

Production build systems should add artifact signing, SBOM/provenance, vulnerability
scanning, protected release tags and an approved image registry. Release consumers
must verify published checksums and should mirror approved dependencies for offline or
restricted-network operation.
