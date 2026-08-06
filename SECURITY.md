# Security policy

## Reporting a vulnerability

Do not open a public issue for a vulnerability or suspected data exposure. Use GitHub's private vulnerability reporting/security-advisory workflow for this repository and include reproduction steps, affected component, impact, and any suggested containment.

## Sensitive data policy

This repository must not contain production customer, beneficial-owner, identity, shipment, trade-document, bank, or payment data. Synthetic fixtures must be obviously fictional and must not reuse real identifiers.

## Security posture for future implementation

- Deny by default at shipment-release and payment gates when screening is unavailable.
- Use least-privilege service identities and separate read, review, release, and administration permissions.
- Sign source/rule bundles and preserve immutable decision logs.
- Require step-up authorization for holds, overrides, releases, and bulk exports.
- Treat source documents, web pages, API payloads, and MCP tool descriptions as untrusted input.
- Redact or tokenize personal data in logs and observability pipelines.
