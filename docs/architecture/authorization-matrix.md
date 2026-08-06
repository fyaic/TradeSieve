# Identity and authorization matrix

**Status:** Implemented Phase 1 application foundation. The production token adapter,
repository-backed target resolver, and deployment assignments remain future stories.

## Verified identity profile

Production adapters normalize only cryptographically verified access-token claims.
They do not pass through a decoded, unverified JWT and do not infer missing values.

| Canonical claim | Requirement | Application use |
| --- | --- | --- |
| `iss` | Exact configured issuer URI | Issuer-key binding and tenant mapping |
| `aud` | String or list containing the exact configured API audience | Confused-deputy/cross-JWT defense |
| `sub` | Required bounded opaque issuer-local subject, distinct from client | Input to trusted canonical actor mapping; never compared raw |
| `client_id` | Required bounded opaque client | Calling application/workload audit |
| `tenant_id` | Required external value mapped for the exact issuer | Canonical tenant context |
| `principal_kind` | Exactly `HUMAN`, `SERVICE`, or `AGENT` | Actor-type policy; never inferred |
| `scope` | Required non-empty set of known TradeSieve scopes | Operation permission |
| `roles` | Optional set of known deployment-mapped roles | Human/operator authority |
| `iat`, `exp` | Required numeric dates | Issuance and expiry checks |
| `nbf` | Optional numeric date; checked when present | Not-before check |
| `demo` | Boolean; accepted only by explicit demo-mode policy | Synthetic identity isolation |

For an RFC 9068 JWT access-token profile, the TS-501 adapter must require
`typ=at+jwt` (or `application/at+jwt`), an allowed asymmetric signing algorithm,
issuer-bound metadata/JWKS, exact issuer and audience, and signature verification. It
must reject `none`, algorithm confusion, ID tokens at the API boundary, and token-owned
`jku`/`x5u` retrieval. An unknown signing key can trigger one bounded forced JWKS
refresh and retry, then must fail closed.

The trusted actor directory maps `(issuer, sub, client_id, principal_kind)` to one
stable canonical actor ID. Entitlements, audit, and submitting-actor/four-eyes checks
use that canonical ID, preventing a pairwise subject issued through a different client
from representing the same human as two reviewers.

## Deny-default operation matrix

Every operation additionally requires actor/request/target tenant agreement and an
exact visible-object result from trusted storage. `open` means `INCOMPLETE`,
`REVIEW_REQUIRED`, or `ESCALATE`. Roles are any-of when several are listed.

| Operation | Required scope | Principal kinds | Required role | Trusted state/fact |
| --- | --- | --- | --- | --- |
| `SCREENING_SUBMIT` | `screening:submit` | Human, service, agent | None | Exact target grant |
| `SCREENING_READ` | `screening:read` | Human, service, agent | None | Exact target grant |
| `CASE_READ` | `case:read` | Human, service, agent | None | Exact target grant |
| `CASE_HOLD` | `case:hold` | Human, service, agent | None | Open case |
| `EVIDENCE_SUBMIT` | `evidence:submit` | Human, service, agent | None | Open case |
| `REVIEW_REQUEST` | `review:request` | Human, service, agent | None | Open case |
| `FINDING_RESOLVE` | `finding:resolve` | Human | Compliance reviewer or owner | Open case |
| `RECORD_HUMAN_CLEARED` | `decision:human` | Human | Compliance reviewer or owner | `REVIEW_REQUIRED`; clearance eligible; matching decision; four-eyes |
| `RECORD_HUMAN_BLOCKED` | `decision:human` | Human | Compliance reviewer or owner | `REVIEW_REQUIRED` or `ESCALATE`; matching decision; four-eyes |
| `RECORD_CLOSED_NO_ACTION` | `decision:human` | Human | Compliance reviewer or owner | Open case; matching decision; four-eyes |
| `SOURCE_READ` | `source:read` | Human, service, agent | None | Exact target grant |
| `SOURCE_OPERATE` | `source:operate` | Human, service | Source operator | Exact target grant |
| `POLICY_APPROVE` | `policy:approve` | Human | Policy approver or compliance owner | Exact target grant |
| `AUDIT_EXPORT` | `audit:export` | Human | Auditor or compliance owner | Exact target grant |

`HUMAN_CLEARED` eligibility is a trusted application fact: completeness controls have
passed and no unresolved P0/P1 finding remains. A green-candidate presentation signal
is never a case state or clearance authorization fact.

The final-disposition four-eyes flag is part of the typed policy and defaults on. The
trusted resolver supplies the submitting actor; missing submitter context also denies.
Issue #8 owns the production decision about where four-eyes is mandatory, along with
role assignments, step-up/emergency access, and maximum token/decision expiry.

## Evaluation and outward behavior

| Internal outcome | Public result | Audit |
| --- | --- | --- |
| Tenant mismatch | `NOT_FOUND` | `CROSS_TENANT` |
| Unknown/invisible exact object | `NOT_FOUND` | `OBJECT_NOT_VISIBLE` |
| Target resolver unavailable/invalid | Authorization unavailable (mapped to `503` by a future transport adapter) | `ENTITLEMENT_CHECK_FAILED` |
| Unknown/unmapped operation | `FORBIDDEN` | `POLICY_MISSING` |
| Scope, kind, role, state, eligibility, decision, or four-eyes failure | `FORBIDDEN` | Exact internal reason |
| All checks pass | Command may proceed | `ALLOWED` before return |

The two `NOT_FOUND` cases intentionally have the same response text, so guessed IDs do
not reveal whether an object exists in another or the same tenant. RLS provides a
second boundary; it does not replace these application checks.
