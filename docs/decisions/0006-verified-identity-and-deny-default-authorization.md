# ADR-0006: Verify identity before deny-default application authorization

- Status: Accepted
- Date: 2026-08-06

## Context

TradeSieve accepts requests from human reviewers, workload clients, and bounded agent
interfaces. A transport-level bearer check is not enough: the application must prevent
cross-JWT confusion, tenant spoofing, guessed-object access, forged workflow facts, and
service or agent identities recording a final human disposition. PostgreSQL row-level
security cannot be the only authorization boundary because CLI, REST, workers, and MCP
must all enforce the same policy before application commands run.

Deployment issue #9 asks for an identity and authorization model. Issue #8 still needs
human decisions about production role assignment, maximum token and decision expiry,
emergency access, step-up authentication, and which actions require four-eyes review.
The implementation must fail closed while those deployment choices remain open.

[RFC 9068](https://www.rfc-editor.org/rfc/rfc9068.html) defines a JWT profile for
OAuth 2.0 access tokens, including explicit access-token typing, required claims, exact
issuer/audience validation, signature validation, and rejection of the `none`
algorithm. [RFC 8725](https://www.rfc-editor.org/rfc/rfc8725.html) supplies the JWT
security baseline, including algorithm verification and cross-JWT confusion defenses.

## Decision

The `VerifiedClaimsProvider` port is the cryptographic trust boundary. It accepts the
raw bearer token and returns normalized claims only after a production adapter has:

- required `typ=at+jwt` or `typ=application/at+jwt` for the RFC 9068 profile;
- verified the signature using an explicit asymmetric-algorithm allowlist and
  issuer-bound keys;
- rejected `none`, symmetric/asymmetric algorithm confusion, and an unexpected token
  type;
- matched configured issuer and API audience exactly;
- ignored token-supplied key locations such as `jku` and `x5u`; and
- handled key rotation with a bounded issuer-metadata/JWKS cache, at most one forced
  refresh and retry for an unknown key, then failure.

The application never decodes an unverified JWT. TS-501 will implement the production
OIDC/workload adapter and its metadata, cache, retry, and failure behavior; TS-601 does
not add a vendor SDK.

The provider normalizes deployment-configured claims to `sub`, `client_id`,
`tenant_id`, `principal_kind`, `scope`, and optional `roles`. `sub` and `client_id`
must be distinct bounded opaque values. Because an OIDC subject is unique only within
an issuer and may be pairwise for a client or sector, it is not used directly for
entitlement, audit, or four-eyes comparison. The trusted `ActorResolver` maps issuer,
external subject, client, and principal kind to one stable internal actor ID. The
`client_id` remains distinct in audit. `principal_kind` is exactly `HUMAN`, `SERVICE`,
or `AGENT`; it is never inferred. Unknown scopes, roles, or principal kinds are
rejected. An external tenant or organization claim is likewise not authoritative until
the `TenantResolver` maps the exact issuer/value pair to a known internal tenant.

Claims validation requires exact issuer and expected audience membership, `sub`,
`client_id`, tenant, principal kind, a non-empty scope set, `iat`, and `exp`. It checks
optional `nbf` when present, uses an injectable UTC clock, and permits only a bounded
zero-to-five-minute skew. Production token lifetime limits remain a deployment choice
for issue #8 rather than a hardcoded claim-mapping assumption.

Authorization evaluates, in order:

1. authenticated actor, request tenant, and target tenant agreement;
2. typed operation policy, scope, principal kind, and role, before any trusted-storage
   call;
3. exact target visibility plus workflow facts resolved from trusted storage; and
4. persisted case state, clearance eligibility, requested decision, and four-eyes rule.

The caller supplies only target identity and the requested decision. It cannot supply
visibility, persisted case state, submitting actor, or clearance eligibility. A missing
or failing target resolution is denied. Cross-tenant and invisible-object results share
the same public `NOT_FOUND` response; audit retains the internal reason. Resolver
failure produces a distinct safe authorization-unavailable outcome so callers cannot
mistake an infrastructure outage for an absent object. New or omitted operations
receive `POLICY_MISSING`, never an implicit allow and never a target-store lookup.

All allowed and denied evaluations write a structured audit event before returning. It
contains actor, client, tenant context, operation, opaque target reference, reason,
outcome, correlation, and UTC timestamp—not the bearer token, raw claims, or object
payload. Audit sink failure prevents the command from proceeding.

Each final disposition is a separate typed operation. `HUMAN_CLEARED` is allowed only
from `REVIEW_REQUIRED` with a trusted clearance-eligible fact; `HUMAN_BLOCKED` is
allowed from `REVIEW_REQUIRED` or `ESCALATE`; `CLOSED_NO_ACTION` is allowed from any
open state. All require a named human with the neutral `decision:human` scope, a
designated reviewer role, the exact matching decision, and a different recorded
submitter when the policy's conservative four-eyes default is enabled. Service and
agent identities are denied regardless of forged scope or role claims.

The synthetic demo provider uses a process-local randomly generated opaque bearer and
can be constructed only when both deployment mode is `demo` and a separate opt-in is
enabled. Demo claims are explicitly marked, and claim validation also makes demo
acceptance structurally impossible in production mode. No fixed demo bearer or signing
key is committed.

## Consequences

- REST, CLI, MCP, and worker adapters must create the same authenticated request context
  and call the same authorization service.
- Exact object grants and workflow facts require a trusted repository implementation in
  TS-501/TS-403; caller-provided booleans or state strings are not substitutes.
- PostgreSQL RLS remains required defense in depth, but application authorization is the
  primary command boundary.
- Production role grants, emergency access, maximum expiry, IdP claim mappings, and
  canonical actor/tenant mappings and four-eyes deployment configuration remain
  explicit human decisions tracked in #8.
- The complete operation matrix is maintained in
  [Authorization matrix](../architecture/authorization-matrix.md) and tested for enum
  exhaustiveness.
