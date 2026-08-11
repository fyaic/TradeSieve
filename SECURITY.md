# Security policy

## Supported versions

| Version | Supported |
|---|---|
| Latest tagged `0.1.x` alpha | Security fixes on a best-effort evaluation basis |
| Older tags and untagged snapshots | No |

This repository is an engineering prototype, not a production clearance service and
does not currently offer a production security SLA.

## Reporting a vulnerability

Do not open a public issue for a vulnerability or suspected data exposure. Use GitHub's private vulnerability reporting/security-advisory workflow for this repository and include reproduction steps, affected component, impact, and any suggested containment.

The maintainers will acknowledge a complete private report within five business days
when repository access and GitHub delivery are operational. If private vulnerability
reporting is unavailable, contact the repository organization owner through the
existing private project channel; do not include secrets, customer data or exploit
payloads in a public issue. Remediation timing depends on severity and prototype
scope, and will be communicated in the private advisory.

## Sensitive data policy

This repository must not contain production customer, beneficial-owner, identity, shipment, trade-document, bank, or payment data. Synthetic fixtures must be obviously fictional and must not reuse real identifiers.

## Security posture for future implementation

- Deny by default at shipment-release and payment gates when screening is unavailable.
- Use least-privilege service identities and separate read, review, release, and administration permissions.
- Sign source/rule bundles and preserve immutable decision logs.
- Require step-up authorization for holds, overrides, releases, and bulk exports.
- Treat source documents, web pages, API payloads, and MCP tool descriptions as untrusted input.
- Redact or tokenize personal data in logs and observability pipelines.
