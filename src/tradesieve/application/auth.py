"""Verified identity context and deny-by-default application authorization."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Literal, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

from tradesieve.application.contracts import CaseState, HumanDecisionType
from tradesieve.ports.identity import (
    ActorResolver,
    IdentityVerificationError,
    TenantResolver,
    VerifiedClaimsProvider,
)

SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SAFE_SCOPE = re.compile(r"^[a-z][a-z0-9:-]{0,127}$")
SAFE_ROLE = re.compile(r"^[a-z][a-z0-9_:-]{0,127}$")
SAFE_OPERATION = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
MAX_CLOCK_SKEW = timedelta(minutes=5)


class AuthenticationFailure(Exception):
    """Safe authentication failure without credential or claim disclosure."""

    def __init__(self, code: str) -> None:
        super().__init__("authentication failed")
        self.code = code


class ActorType(StrEnum):
    HUMAN = "HUMAN"
    SERVICE = "SERVICE"
    AGENT = "AGENT"


class Scope(StrEnum):
    SCREENING_SUBMIT = "screening:submit"
    SCREENING_READ = "screening:read"
    CASE_READ = "case:read"
    CASE_HOLD = "case:hold"
    EVIDENCE_SUBMIT = "evidence:submit"
    REVIEW_REQUEST = "review:request"
    FINDING_RESOLVE = "finding:resolve"
    HUMAN_DECISION = "decision:human"
    SOURCE_READ = "source:read"
    SOURCE_OPERATE = "source:operate"
    SOURCE_APPROVE = "source:approve"
    POLICY_READ = "policy:read"
    POLICY_OPERATE = "policy:operate"
    POLICY_APPROVE = "policy:approve"
    AUDIT_EXPORT = "audit:export"


class Role(StrEnum):
    COMPLIANCE_REVIEWER = "compliance_reviewer"
    COMPLIANCE_OWNER = "compliance_owner"
    SOURCE_OPERATOR = "source_operator"
    SOURCE_APPROVER = "source_approver"
    POLICY_AUTHOR = "policy_author"
    POLICY_APPROVER = "policy_approver"
    AUDITOR = "auditor"


class Operation(StrEnum):
    SCREENING_SUBMIT = "SCREENING_SUBMIT"
    SCREENING_READ = "SCREENING_READ"
    CASE_READ = "CASE_READ"
    CASE_HOLD = "CASE_HOLD"
    EVIDENCE_SUBMIT = "EVIDENCE_SUBMIT"
    REVIEW_REQUEST = "REVIEW_REQUEST"
    FINDING_RESOLVE = "FINDING_RESOLVE"
    RECORD_HUMAN_CLEARED = "RECORD_HUMAN_CLEARED"
    RECORD_HUMAN_BLOCKED = "RECORD_HUMAN_BLOCKED"
    RECORD_CLOSED_NO_ACTION = "RECORD_CLOSED_NO_ACTION"
    SOURCE_READ = "SOURCE_READ"
    SOURCE_OPERATE = "SOURCE_OPERATE"
    SOURCE_SNAPSHOT_INGEST = "SOURCE_SNAPSHOT_INGEST"
    SOURCE_SNAPSHOT_PARSE = "SOURCE_SNAPSHOT_PARSE"
    SOURCE_SNAPSHOT_VALIDATE = "SOURCE_SNAPSHOT_VALIDATE"
    SOURCE_SNAPSHOT_APPROVE = "SOURCE_SNAPSHOT_APPROVE"
    SOURCE_SNAPSHOT_ACTIVATE = "SOURCE_SNAPSHOT_ACTIVATE"
    SOURCE_SNAPSHOT_ROLLBACK = "SOURCE_SNAPSHOT_ROLLBACK"
    POLICY_READ = "POLICY_READ"
    POLICY_DRAFT = "POLICY_DRAFT"
    POLICY_APPROVE = "POLICY_APPROVE"
    POLICY_ACTIVATE = "POLICY_ACTIVATE"
    POLICY_RETIRE = "POLICY_RETIRE"
    POLICY_ROLLBACK = "POLICY_ROLLBACK"
    AUDIT_EXPORT = "AUDIT_EXPORT"


class AuthorizationReason(StrEnum):
    ALLOWED = "ALLOWED"
    CROSS_TENANT = "CROSS_TENANT"
    OBJECT_NOT_VISIBLE = "OBJECT_NOT_VISIBLE"
    ENTITLEMENT_CHECK_FAILED = "ENTITLEMENT_CHECK_FAILED"
    POLICY_MISSING = "POLICY_MISSING"
    MISSING_SCOPE = "MISSING_SCOPE"
    ACTOR_TYPE_DENIED = "ACTOR_TYPE_DENIED"
    ROLE_DENIED = "ROLE_DENIED"
    CASE_STATE_DENIED = "CASE_STATE_DENIED"
    CLEARANCE_NOT_ELIGIBLE = "CLEARANCE_NOT_ELIGIBLE"
    DECISION_CONTEXT_MISMATCH = "DECISION_CONTEXT_MISMATCH"
    FOUR_EYES_DENIED = "FOUR_EYES_DENIED"
    AUTHOR_APPROVER_SEPARATION_DENIED = "AUTHOR_APPROVER_SEPARATION_DENIED"


class AuthorizationOutcome(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"


class PublicDenialCode(StrEnum):
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"


def _validate_config_text(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 256
        or value != value.strip()
        or any(character.isspace() for character in value)
    ):
        raise ValueError(f"{field} must be a bounded non-empty value")
    return value


def _validate_issuer(value: object, deployment_mode: str) -> str:
    issuer = _validate_config_text(value, "issuer")
    parsed = urlsplit(issuer)
    local_demo_http = (
        deployment_mode == "demo"
        and parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    )
    if parsed.scheme != "https" and not local_demo_http:
        raise ValueError("issuer must use HTTPS outside explicit local demo mode")
    if (
        not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("issuer must be an absolute URI without credentials or query")
    return issuer


@dataclass(frozen=True, slots=True)
class ClaimsValidationPolicy:
    issuer: str
    audience: str
    clock_skew: timedelta = timedelta(seconds=30)
    allow_demo_identities: bool = False
    deployment_mode: Literal["demo", "production"] = "production"

    def __post_init__(self) -> None:
        if self.deployment_mode not in {"demo", "production"}:
            raise ValueError("deployment_mode must be demo or production")
        _validate_issuer(self.issuer, self.deployment_mode)
        _validate_config_text(self.audience, "audience")
        if not timedelta(0) <= self.clock_skew <= MAX_CLOCK_SKEW:
            raise ValueError("clock_skew must be between zero and five minutes")
        if self.allow_demo_identities and self.deployment_mode != "demo":
            raise ValueError("demo identities require explicit demo deployment mode")


@dataclass(frozen=True, slots=True)
class ActorContext:
    subject: str
    client_id: str
    tenant_id: str
    actor_type: ActorType
    scopes: frozenset[Scope]
    roles: frozenset[Role]
    issuer: str
    audience: str
    issued_at: datetime
    expires_at: datetime
    demo_identity: bool


def _require_identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or SAFE_IDENTIFIER.fullmatch(value) is None:
        raise AuthenticationFailure(f"INVALID_{field.upper()}")
    return value


def _require_opaque_claim(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 256
        or not value.isprintable()
        or any(character.isspace() for character in value)
    ):
        raise AuthenticationFailure(f"INVALID_{field.upper()}")
    return value


def _require_context_identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or SAFE_IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"invalid {field}")
    return value


def _require_issuer(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise AuthenticationFailure("INVALID_ISSUER")
    return value


def _numeric_date(
    claims: Mapping[str, object], name: str, *, required: bool = True
) -> datetime | None:
    value = claims.get(name)
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AuthenticationFailure(f"INVALID_{name.upper()}")
    try:
        return datetime.fromtimestamp(value, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise AuthenticationFailure(f"INVALID_{name.upper()}") from exc


def _audiences(claims: Mapping[str, object]) -> frozenset[str]:
    audience = claims.get("aud")
    if isinstance(audience, str):
        items = [audience]
    elif isinstance(audience, list) and audience:
        items = audience
    else:
        raise AuthenticationFailure("INVALID_AUDIENCE")
    if any(
        not isinstance(item, str)
        or not item
        or len(item) > 256
        or any(character.isspace() for character in item)
        for item in items
    ):
        raise AuthenticationFailure("INVALID_AUDIENCE")
    return frozenset(items)


def _permissions(
    claims: Mapping[str, object],
    name: str,
    *,
    required: bool,
) -> frozenset[str]:
    value = claims.get(name)
    if value is None and not required:
        items: list[object] = []
    elif isinstance(value, str):
        items = list(value.split())
    elif isinstance(value, list):
        items = value
    else:
        raise AuthenticationFailure(f"INVALID_{name.upper()}")
    pattern = SAFE_SCOPE if name == "scope" else SAFE_ROLE
    if (required and not items) or any(
        not isinstance(item, str) or pattern.fullmatch(item) is None for item in items
    ):
        raise AuthenticationFailure(f"INVALID_{name.upper()}")
    return frozenset(item for item in items if isinstance(item, str))


class ClaimsValidator:
    """Validate normalized claims after cryptographic token verification."""

    def __init__(
        self,
        policy: ClaimsValidationPolicy,
        tenant_resolver: TenantResolver,
        actor_resolver: ActorResolver,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._policy = policy
        self._tenant_resolver = tenant_resolver
        self._actor_resolver = actor_resolver
        self._clock = clock or (lambda: datetime.now(UTC))

    def validate(self, claims: Mapping[str, object]) -> ActorContext:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("identity clock must return a timezone-aware datetime")
        now = now.astimezone(UTC)
        issuer = _require_issuer(claims.get("iss"))
        if issuer != self._policy.issuer:
            raise AuthenticationFailure("ISSUER_MISMATCH")
        audiences = _audiences(claims)
        if self._policy.audience not in audiences:
            raise AuthenticationFailure("AUDIENCE_MISMATCH")

        issued_at = _numeric_date(claims, "iat")
        not_before = _numeric_date(claims, "nbf", required=False)
        expires_at = _numeric_date(claims, "exp")
        assert issued_at is not None
        assert expires_at is not None
        if issued_at > now + self._policy.clock_skew:
            raise AuthenticationFailure("TOKEN_ISSUED_IN_FUTURE")
        if not_before is not None and not_before > now + self._policy.clock_skew:
            raise AuthenticationFailure("TOKEN_NOT_YET_VALID")
        if expires_at <= issued_at:
            raise AuthenticationFailure("INVALID_TOKEN_LIFETIME")
        if expires_at <= now - self._policy.clock_skew:
            raise AuthenticationFailure("TOKEN_EXPIRED")

        principal_kind = claims.get("principal_kind")
        try:
            actor_type = (
                ActorType(principal_kind) if isinstance(principal_kind, str) else None
            )
        except (TypeError, ValueError) as exc:
            raise AuthenticationFailure("INVALID_PRINCIPAL_KIND") from exc
        if actor_type is None:
            raise AuthenticationFailure("INVALID_PRINCIPAL_KIND")
        external_subject = _require_opaque_claim(claims.get("sub"), "subject")
        client_id = _require_opaque_claim(claims.get("client_id"), "client_id")
        if external_subject == client_id:
            raise AuthenticationFailure("ACTOR_CLIENT_NOT_DISTINCT")
        demo_identity = claims.get("demo", False)
        if not isinstance(demo_identity, bool):
            raise AuthenticationFailure("INVALID_DEMO_CLAIM")
        if demo_identity and not self._policy.allow_demo_identities:
            raise AuthenticationFailure("DEMO_IDENTITY_DISABLED")

        scope_values = _permissions(claims, "scope", required=True)
        role_values = _permissions(claims, "roles", required=False)
        try:
            scopes = frozenset(Scope(item) for item in scope_values)
        except ValueError as exc:
            raise AuthenticationFailure("UNKNOWN_SCOPE") from exc
        try:
            roles = frozenset(Role(item) for item in role_values)
        except ValueError as exc:
            raise AuthenticationFailure("UNKNOWN_ROLES") from exc

        external_tenant_id = _require_identifier(claims.get("tenant_id"), "tenant_id")
        try:
            tenant_id = self._tenant_resolver.resolve(issuer, external_tenant_id)
        except Exception as exc:
            raise AuthenticationFailure("TENANT_RESOLUTION_FAILED") from exc
        if tenant_id is None:
            raise AuthenticationFailure("UNKNOWN_TENANT")
        try:
            actor_id = self._actor_resolver.resolve(
                issuer, external_subject, client_id, actor_type.value
            )
        except Exception as exc:
            raise AuthenticationFailure("ACTOR_RESOLUTION_FAILED") from exc
        if actor_id is None:
            raise AuthenticationFailure("UNKNOWN_ACTOR")
        return ActorContext(
            subject=_require_identifier(actor_id, "actor_id"),
            client_id=client_id,
            tenant_id=_require_identifier(tenant_id, "tenant_id"),
            actor_type=actor_type,
            scopes=scopes,
            roles=roles,
            issuer=issuer,
            audience=self._policy.audience,
            issued_at=issued_at,
            expires_at=expires_at,
            demo_identity=demo_identity,
        )


class IdentityAuthenticator:
    """Authenticate at the verification port, then validate normalized claims."""

    def __init__(
        self, provider: VerifiedClaimsProvider, validator: ClaimsValidator
    ) -> None:
        self._provider = provider
        self._validator = validator

    def authenticate(self, bearer_token: str) -> ActorContext:
        try:
            claims = self._provider.verify(bearer_token)
        except IdentityVerificationError as exc:
            raise AuthenticationFailure("CREDENTIAL_REJECTED") from exc
        if not isinstance(claims, Mapping):
            raise AuthenticationFailure("INVALID_VERIFIED_CLAIMS")
        return self._validator.validate(claims)


@dataclass(frozen=True, slots=True)
class RequestContext:
    actor: ActorContext
    tenant_id: str
    correlation_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.actor, ActorContext):
            raise TypeError("actor context is required")
        _require_context_identifier(self.tenant_id, "tenant_id")
        _require_context_identifier(self.correlation_id, "correlation_id")


@dataclass(frozen=True, slots=True)
class TargetObject:
    tenant_id: str
    object_type: str
    object_id: str

    def __post_init__(self) -> None:
        _require_context_identifier(self.tenant_id, "tenant_id")
        _require_context_identifier(self.object_type, "object_type")
        _require_context_identifier(self.object_id, "object_id")

    @property
    def opaque_ref(self) -> str:
        return f"{self.object_type}:{self.object_id}"


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    context: RequestContext
    operation: Operation | str
    target: TargetObject
    human_decision: HumanDecisionType | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.operation, str)
            or SAFE_OPERATION.fullmatch(self.operation) is None
        ):
            raise ValueError("operation must be a bounded symbolic name")


@dataclass(frozen=True, slots=True)
class AuthorizationPolicy:
    required_scope: Scope
    actor_types: frozenset[ActorType]
    any_role: frozenset[Role] = frozenset()
    allowed_case_states: frozenset[CaseState] | None = None
    expected_decision: HumanDecisionType | None = None
    four_eyes_required: bool = False
    clearance_eligibility_required: bool = False
    author_approver_separation_required: bool = False


ALL_ACTORS = frozenset(ActorType)
HUMANS = frozenset({ActorType.HUMAN})
HUMAN_OR_SERVICE = frozenset({ActorType.HUMAN, ActorType.SERVICE})
OPEN_CASE_STATES = frozenset(
    {CaseState.INCOMPLETE, CaseState.REVIEW_REQUIRED, CaseState.ESCALATE}
)
REVIEWABLE_CASE_STATES = frozenset({CaseState.REVIEW_REQUIRED, CaseState.ESCALATE})
REVIEWER_ROLES = frozenset({Role.COMPLIANCE_REVIEWER, Role.COMPLIANCE_OWNER})


def _final_decision_policy(
    decision: HumanDecisionType,
    allowed_case_states: frozenset[CaseState],
    *,
    clearance_eligibility_required: bool = False,
) -> AuthorizationPolicy:
    return AuthorizationPolicy(
        Scope.HUMAN_DECISION,
        HUMANS,
        REVIEWER_ROLES,
        allowed_case_states=allowed_case_states,
        expected_decision=decision,
        four_eyes_required=True,
        clearance_eligibility_required=clearance_eligibility_required,
    )


DEFAULT_POLICIES: Mapping[Operation, AuthorizationPolicy] = MappingProxyType(
    {
        Operation.SCREENING_SUBMIT: AuthorizationPolicy(
            Scope.SCREENING_SUBMIT, ALL_ACTORS
        ),
        Operation.SCREENING_READ: AuthorizationPolicy(Scope.SCREENING_READ, ALL_ACTORS),
        Operation.CASE_READ: AuthorizationPolicy(Scope.CASE_READ, ALL_ACTORS),
        Operation.CASE_HOLD: AuthorizationPolicy(
            Scope.CASE_HOLD, ALL_ACTORS, allowed_case_states=OPEN_CASE_STATES
        ),
        Operation.EVIDENCE_SUBMIT: AuthorizationPolicy(
            Scope.EVIDENCE_SUBMIT,
            ALL_ACTORS,
            allowed_case_states=OPEN_CASE_STATES,
        ),
        Operation.REVIEW_REQUEST: AuthorizationPolicy(
            Scope.REVIEW_REQUEST,
            ALL_ACTORS,
            allowed_case_states=OPEN_CASE_STATES,
        ),
        Operation.FINDING_RESOLVE: AuthorizationPolicy(
            Scope.FINDING_RESOLVE,
            HUMANS,
            REVIEWER_ROLES,
            allowed_case_states=OPEN_CASE_STATES,
        ),
        Operation.RECORD_HUMAN_CLEARED: _final_decision_policy(
            HumanDecisionType.HUMAN_CLEARED,
            frozenset({CaseState.REVIEW_REQUIRED}),
            clearance_eligibility_required=True,
        ),
        Operation.RECORD_HUMAN_BLOCKED: _final_decision_policy(
            HumanDecisionType.HUMAN_BLOCKED, REVIEWABLE_CASE_STATES
        ),
        Operation.RECORD_CLOSED_NO_ACTION: _final_decision_policy(
            HumanDecisionType.CLOSED_NO_ACTION, OPEN_CASE_STATES
        ),
        Operation.SOURCE_READ: AuthorizationPolicy(Scope.SOURCE_READ, ALL_ACTORS),
        Operation.SOURCE_OPERATE: AuthorizationPolicy(
            Scope.SOURCE_OPERATE,
            HUMAN_OR_SERVICE,
            frozenset({Role.SOURCE_OPERATOR}),
        ),
        Operation.SOURCE_SNAPSHOT_INGEST: AuthorizationPolicy(
            Scope.SOURCE_OPERATE,
            HUMAN_OR_SERVICE,
            frozenset({Role.SOURCE_OPERATOR}),
        ),
        Operation.SOURCE_SNAPSHOT_PARSE: AuthorizationPolicy(
            Scope.SOURCE_OPERATE,
            HUMAN_OR_SERVICE,
            frozenset({Role.SOURCE_OPERATOR}),
        ),
        Operation.SOURCE_SNAPSHOT_VALIDATE: AuthorizationPolicy(
            Scope.SOURCE_OPERATE,
            HUMAN_OR_SERVICE,
            frozenset({Role.SOURCE_OPERATOR}),
        ),
        Operation.SOURCE_SNAPSHOT_APPROVE: AuthorizationPolicy(
            Scope.SOURCE_APPROVE,
            HUMANS,
            frozenset({Role.SOURCE_APPROVER, Role.COMPLIANCE_OWNER}),
            author_approver_separation_required=True,
        ),
        Operation.SOURCE_SNAPSHOT_ACTIVATE: AuthorizationPolicy(
            Scope.SOURCE_APPROVE,
            HUMANS,
            frozenset({Role.SOURCE_APPROVER, Role.COMPLIANCE_OWNER}),
            author_approver_separation_required=True,
        ),
        Operation.SOURCE_SNAPSHOT_ROLLBACK: AuthorizationPolicy(
            Scope.SOURCE_APPROVE,
            HUMANS,
            frozenset({Role.SOURCE_APPROVER, Role.COMPLIANCE_OWNER}),
            author_approver_separation_required=True,
        ),
        Operation.POLICY_READ: AuthorizationPolicy(Scope.POLICY_READ, ALL_ACTORS),
        Operation.POLICY_DRAFT: AuthorizationPolicy(
            Scope.POLICY_OPERATE,
            HUMAN_OR_SERVICE,
            frozenset({Role.POLICY_AUTHOR}),
        ),
        Operation.POLICY_APPROVE: AuthorizationPolicy(
            Scope.POLICY_APPROVE,
            HUMANS,
            frozenset({Role.POLICY_APPROVER, Role.COMPLIANCE_OWNER}),
            author_approver_separation_required=True,
        ),
        Operation.POLICY_ACTIVATE: AuthorizationPolicy(
            Scope.POLICY_APPROVE,
            HUMANS,
            frozenset({Role.POLICY_APPROVER, Role.COMPLIANCE_OWNER}),
            author_approver_separation_required=True,
        ),
        Operation.POLICY_RETIRE: AuthorizationPolicy(
            Scope.POLICY_APPROVE,
            HUMANS,
            frozenset({Role.POLICY_APPROVER, Role.COMPLIANCE_OWNER}),
            author_approver_separation_required=True,
        ),
        Operation.POLICY_ROLLBACK: AuthorizationPolicy(
            Scope.POLICY_APPROVE,
            HUMANS,
            frozenset({Role.POLICY_APPROVER, Role.COMPLIANCE_OWNER}),
            author_approver_separation_required=True,
        ),
        Operation.AUDIT_EXPORT: AuthorizationPolicy(
            Scope.AUDIT_EXPORT,
            HUMANS,
            frozenset({Role.AUDITOR, Role.COMPLIANCE_OWNER}),
        ),
    }
)


class ObjectEntitlementResolver(Protocol):
    """Resolve exact object visibility and authorization facts from trusted state."""

    def resolve(
        self,
        *,
        actor_subject: str,
        actor_tenant_id: str,
        operation: str,
        target_tenant_id: str,
        target_type: str,
        target_id: str,
    ) -> ResolvedTargetFacts | None: ...


@dataclass(frozen=True, slots=True)
class ResolvedTargetFacts:
    """Authorization facts loaded from trusted storage, never command input."""

    case_state: CaseState | None = None
    submitting_actor_id: str | None = None
    author_actor_id: str | None = None
    clearance_eligible: bool = False

    def __post_init__(self) -> None:
        if self.submitting_actor_id is not None:
            _require_context_identifier(self.submitting_actor_id, "submitting_actor_id")
        if self.author_actor_id is not None:
            _require_context_identifier(self.author_actor_id, "author_actor_id")


@dataclass(frozen=True, slots=True)
class AuthorizationAuditEvent:
    event_id: str
    occurred_at: datetime
    actor_subject: str
    client_id: str
    actor_type: ActorType
    actor_tenant_id: str
    request_tenant_id: str
    target_tenant_id: str
    operation: str
    target_ref: str
    correlation_id: str
    outcome: AuthorizationOutcome
    reason: AuthorizationReason


type AuthorizationAuditSink = Callable[[AuthorizationAuditEvent], None]


@dataclass(frozen=True, slots=True)
class AuthorizedRequest:
    context: RequestContext
    operation: Operation
    target: TargetObject
    audit_event_id: str


class AuthorizationDenied(Exception):
    """Safe denial with no cross-tenant or object-existence disclosure."""

    def __init__(self, code: PublicDenialCode, audit_event_id: str) -> None:
        message = (
            "object not found" if code is PublicDenialCode.NOT_FOUND else "forbidden"
        )
        super().__init__(message)
        self.code = code
        self.audit_event_id = audit_event_id


class AuthorizationAuditFailure(Exception):
    """Authorization could not complete because its audit record failed."""

    def __init__(self) -> None:
        super().__init__("authorization audit unavailable")


class AuthorizationUnavailable(Exception):
    """Authorization dependency failed without disclosing object existence."""

    def __init__(self, audit_event_id: str) -> None:
        super().__init__("authorization unavailable")
        self.audit_event_id = audit_event_id


class AuthorizationService:
    """Evaluate exact entitlement and typed policy, auditing before return."""

    def __init__(
        self,
        audit_sink: AuthorizationAuditSink,
        entitlement_resolver: ObjectEntitlementResolver,
        *,
        policies: Mapping[Operation, AuthorizationPolicy] = DEFAULT_POLICIES,
        event_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._audit_sink = audit_sink
        self._entitlement_resolver = entitlement_resolver
        self._policies = MappingProxyType(dict(policies))
        self._event_id_factory = event_id_factory or (lambda: f"authz-{uuid4().hex}")

    def require(
        self, request: AuthorizationRequest, *, now: datetime
    ) -> AuthorizedRequest:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        reason = self._evaluate(request)
        allowed = reason is AuthorizationReason.ALLOWED
        event_id = self._event_id_factory()
        if not isinstance(event_id, str) or SAFE_IDENTIFIER.fullmatch(event_id) is None:
            raise AuthorizationAuditFailure
        event = AuthorizationAuditEvent(
            event_id=event_id,
            occurred_at=now.astimezone(UTC),
            actor_subject=request.context.actor.subject,
            client_id=request.context.actor.client_id,
            actor_type=request.context.actor.actor_type,
            actor_tenant_id=request.context.actor.tenant_id,
            request_tenant_id=request.context.tenant_id,
            target_tenant_id=request.target.tenant_id,
            operation=str(request.operation),
            target_ref=request.target.opaque_ref,
            correlation_id=request.context.correlation_id,
            outcome=(
                AuthorizationOutcome.ALLOW if allowed else AuthorizationOutcome.DENY
            ),
            reason=reason,
        )
        try:
            self._audit_sink(event)
        except Exception as exc:
            raise AuthorizationAuditFailure from exc
        if reason is AuthorizationReason.ENTITLEMENT_CHECK_FAILED:
            raise AuthorizationUnavailable(event_id)
        if not allowed:
            concealed_reasons = {
                AuthorizationReason.CROSS_TENANT,
                AuthorizationReason.OBJECT_NOT_VISIBLE,
            }
            code = (
                PublicDenialCode.NOT_FOUND
                if reason in concealed_reasons
                else PublicDenialCode.FORBIDDEN
            )
            raise AuthorizationDenied(code, event_id)
        assert isinstance(request.operation, Operation)
        return AuthorizedRequest(
            context=request.context,
            operation=request.operation,
            target=request.target,
            audit_event_id=event_id,
        )

    def _evaluate(self, request: AuthorizationRequest) -> AuthorizationReason:
        actor = request.context.actor
        if (
            request.context.tenant_id != actor.tenant_id
            or request.target.tenant_id != actor.tenant_id
        ):
            return AuthorizationReason.CROSS_TENANT
        if not isinstance(request.operation, Operation):
            return AuthorizationReason.POLICY_MISSING
        policy = self._policies.get(request.operation)
        if policy is None:
            return AuthorizationReason.POLICY_MISSING
        if policy.required_scope not in actor.scopes:
            return AuthorizationReason.MISSING_SCOPE
        if actor.actor_type not in policy.actor_types:
            return AuthorizationReason.ACTOR_TYPE_DENIED
        if policy.any_role and not policy.any_role.intersection(actor.roles):
            return AuthorizationReason.ROLE_DENIED
        try:
            target_facts = self._entitlement_resolver.resolve(
                actor_subject=actor.subject,
                actor_tenant_id=actor.tenant_id,
                operation=request.operation,
                target_tenant_id=request.target.tenant_id,
                target_type=request.target.object_type,
                target_id=request.target.object_id,
            )
        except Exception:
            return AuthorizationReason.ENTITLEMENT_CHECK_FAILED
        if target_facts is not None and not isinstance(
            target_facts, ResolvedTargetFacts
        ):
            return AuthorizationReason.ENTITLEMENT_CHECK_FAILED
        if target_facts is None:
            return AuthorizationReason.OBJECT_NOT_VISIBLE
        if (
            policy.allowed_case_states is not None
            and target_facts.case_state not in policy.allowed_case_states
        ):
            return AuthorizationReason.CASE_STATE_DENIED
        if (
            policy.clearance_eligibility_required
            and not target_facts.clearance_eligible
        ):
            return AuthorizationReason.CLEARANCE_NOT_ELIGIBLE
        if (
            policy.expected_decision is not None
            and request.human_decision is not policy.expected_decision
        ):
            return AuthorizationReason.DECISION_CONTEXT_MISMATCH
        if policy.four_eyes_required and (
            target_facts.submitting_actor_id is None
            or target_facts.submitting_actor_id == actor.subject
        ):
            return AuthorizationReason.FOUR_EYES_DENIED
        if policy.author_approver_separation_required and (
            target_facts.author_actor_id is None
            or target_facts.author_actor_id == actor.subject
        ):
            return AuthorizationReason.AUTHOR_APPROVER_SEPARATION_DENIED
        return AuthorizationReason.ALLOWED
