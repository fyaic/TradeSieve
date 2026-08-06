"""Identity and deny-by-default authorization safety tests."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import NoReturn, cast

import pytest

from tradesieve.adapters.identity import DemoVerifiedClaimsProvider
from tradesieve.application.auth import (
    DEFAULT_POLICIES,
    ActorContext,
    ActorType,
    AuthenticationFailure,
    AuthorizationAuditEvent,
    AuthorizationAuditFailure,
    AuthorizationDenied,
    AuthorizationOutcome,
    AuthorizationPolicy,
    AuthorizationReason,
    AuthorizationRequest,
    AuthorizationService,
    AuthorizationUnavailable,
    ClaimsValidationPolicy,
    ClaimsValidator,
    IdentityAuthenticator,
    Operation,
    PublicDenialCode,
    RequestContext,
    ResolvedTargetFacts,
    Role,
    Scope,
    TargetObject,
)
from tradesieve.application.contracts import CaseState, HumanDecisionType
from tradesieve.ports.identity import IdentityVerificationError

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
ISSUER = "https://identity.example.test/"
AUDIENCE = "https://api.tradesieve.example.test/v1"


class KnownTenants:
    def __init__(
        self, result: str | None = "tenant-canonical", *, fail: bool = False
    ) -> None:
        self.result = result
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def resolve(self, issuer: str, external_tenant_id: str) -> str | None:
        self.calls.append((issuer, external_tenant_id))
        if self.fail:
            raise RuntimeError("tenant directory detail")
        return self.result


class KnownActors:
    def __init__(
        self, result: str | None = "actor-canonical", *, fail: bool = False
    ) -> None:
        self.result = result
        self.fail = fail
        self.calls: list[tuple[str, str, str, str]] = []

    def resolve(
        self,
        issuer: str,
        external_subject: str,
        client_id: str,
        principal_kind: str,
    ) -> str | None:
        self.calls.append((issuer, external_subject, client_id, principal_kind))
        if self.fail:
            raise RuntimeError("identity directory detail")
        return self.result


class StaticClaimsProvider:
    def __init__(self, claims: Mapping[str, object], *, reject: bool = False) -> None:
        self.claims = claims
        self.reject = reject

    def verify(self, bearer_token: str) -> Mapping[str, object]:
        if self.reject:
            raise IdentityVerificationError("provider detail must stay private")
        assert bearer_token == "raw-bearer"
        return self.claims


class InvalidClaimsProvider:
    def verify(self, bearer_token: str) -> Mapping[str, object]:
        assert bearer_token
        return cast(Mapping[str, object], ["not", "claims"])


def valid_claims(**overrides: object) -> dict[str, object]:
    claims: dict[str, object] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": (NOW - timedelta(minutes=1)).timestamp(),
        "nbf": (NOW - timedelta(minutes=1)).timestamp(),
        "exp": (NOW + timedelta(minutes=5)).timestamp(),
        "sub": "auth0|human-1",
        "client_id": "reviewer-ui",
        "tenant_id": "external-tenant-1",
        "principal_kind": "HUMAN",
        "scope": "screening:read case:read",
        "roles": ["compliance_reviewer"],
        "demo": False,
    }
    claims.update(overrides)
    return claims


def validator(
    *,
    tenants: KnownTenants | None = None,
    actors: KnownActors | None = None,
    allow_demo: bool = False,
    clock: datetime = NOW,
) -> ClaimsValidator:
    return ClaimsValidator(
        ClaimsValidationPolicy(
            issuer=ISSUER,
            audience=AUDIENCE,
            allow_demo_identities=allow_demo,
            deployment_mode="demo" if allow_demo else "production",
        ),
        tenants or KnownTenants(),
        actors or KnownActors(),
        clock=lambda: clock,
    )


def test_valid_claims_are_normalized_after_tenant_mapping() -> None:
    tenants = KnownTenants()
    actors = KnownActors()
    claims = valid_claims(
        aud=["unrelated-audience", AUDIENCE],
        scope=["screening:read", "case:read"],
        roles="compliance_reviewer auditor",
    )
    actor = validator(tenants=tenants, actors=actors).validate(claims)

    assert actor == ActorContext(
        subject="actor-canonical",
        client_id="reviewer-ui",
        tenant_id="tenant-canonical",
        actor_type=ActorType.HUMAN,
        scopes=frozenset({Scope.SCREENING_READ, Scope.CASE_READ}),
        roles=frozenset({Role.COMPLIANCE_REVIEWER, Role.AUDITOR}),
        issuer=ISSUER,
        audience=AUDIENCE,
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=5),
        demo_identity=False,
    )
    assert tenants.calls == [(ISSUER, "external-tenant-1")]
    assert actors.calls == [(ISSUER, "auth0|human-1", "reviewer-ui", "HUMAN")]


def test_optional_claims_and_default_clock_are_supported() -> None:
    current = datetime.now(UTC)
    claims = valid_claims(
        iat=(current - timedelta(seconds=5)).timestamp(),
        exp=(current + timedelta(minutes=1)).timestamp(),
    )
    del claims["nbf"]
    del claims["roles"]
    actor = ClaimsValidator(
        ClaimsValidationPolicy(ISSUER, AUDIENCE), KnownTenants(), KnownActors()
    ).validate(claims)
    assert actor.roles == frozenset()


@pytest.mark.parametrize(
    ("policy_args", "message"),
    [
        ({"issuer": ""}, "issuer"),
        ({"issuer": "not-a-uri"}, "issuer"),
        ({"issuer": "ftp://identity.example.test"}, "issuer"),
        ({"issuer": "http://identity.example.test"}, "HTTPS"),
        ({"issuer": "https:///missing-host"}, "issuer"),
        ({"issuer": "https://user@identity.example.test"}, "credentials"),
        ({"issuer": "https://identity.example.test?query=1"}, "credentials"),
        ({"audience": ""}, "audience"),
        ({"audience": " has-space"}, "audience"),
        ({"audience": "x" * 257}, "audience"),
        ({"clock_skew": timedelta(microseconds=-1)}, "clock_skew"),
        ({"clock_skew": timedelta(seconds=301)}, "clock_skew"),
        ({"allow_demo_identities": True}, "demo deployment mode"),
        ({"deployment_mode": "staging"}, "deployment_mode"),
    ],
)
def test_claim_policy_configuration_fails_with_value_error(
    policy_args: dict[str, object], message: str
) -> None:
    values: dict[str, object] = {"issuer": ISSUER, "audience": "urn:tradesieve:api"}
    values.update(policy_args)
    with pytest.raises(ValueError, match=message):
        ClaimsValidationPolicy(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("updates", "expected_code"),
    [
        ({"iss": 3}, "INVALID_ISSUER"),
        ({"iss": "https://other.example.test"}, "ISSUER_MISMATCH"),
        ({"aud": None}, "INVALID_AUDIENCE"),
        ({"aud": []}, "INVALID_AUDIENCE"),
        ({"aud": [AUDIENCE, 3]}, "INVALID_AUDIENCE"),
        ({"aud": ["bad audience"]}, "INVALID_AUDIENCE"),
        ({"aud": "https://other.example.test"}, "AUDIENCE_MISMATCH"),
        ({"iat": True}, "INVALID_IAT"),
        ({"exp": 10**30}, "INVALID_EXP"),
        (
            {"iat": (NOW + timedelta(minutes=1)).timestamp()},
            "TOKEN_ISSUED_IN_FUTURE",
        ),
        (
            {"nbf": (NOW + timedelta(minutes=1)).timestamp()},
            "TOKEN_NOT_YET_VALID",
        ),
        (
            {"exp": (NOW - timedelta(seconds=45)).timestamp()},
            "TOKEN_EXPIRED",
        ),
        (
            {
                "iat": (NOW - timedelta(minutes=1)).timestamp(),
                "exp": (NOW - timedelta(minutes=1)).timestamp(),
            },
            "INVALID_TOKEN_LIFETIME",
        ),
        ({"principal_kind": None}, "INVALID_PRINCIPAL_KIND"),
        ({"principal_kind": "ROBOT"}, "INVALID_PRINCIPAL_KIND"),
        ({"demo": "true"}, "INVALID_DEMO_CLAIM"),
        ({"demo": True}, "DEMO_IDENTITY_DISABLED"),
        ({"tenant_id": "invalid tenant"}, "INVALID_TENANT_ID"),
        ({"sub": ""}, "INVALID_SUBJECT"),
        ({"client_id": "bad client"}, "INVALID_CLIENT_ID"),
        ({"client_id": "auth0|human-1"}, "ACTOR_CLIENT_NOT_DISTINCT"),
        ({"scope": None}, "INVALID_SCOPE"),
        ({"scope": ""}, "INVALID_SCOPE"),
        ({"scope": [3]}, "INVALID_SCOPE"),
        ({"scope": "UPPER:scope"}, "INVALID_SCOPE"),
        ({"scope": "unknown:scope"}, "UNKNOWN_SCOPE"),
        ({"roles": 3}, "INVALID_ROLES"),
        ({"roles": ["bad role"]}, "INVALID_ROLES"),
        ({"roles": "unknown_role"}, "UNKNOWN_ROLES"),
    ],
)
def test_invalid_claims_fail_closed(
    updates: dict[str, object], expected_code: str
) -> None:
    with pytest.raises(AuthenticationFailure) as exc_info:
        validator().validate(valid_claims(**updates))
    assert exc_info.value.code == expected_code
    assert str(exc_info.value) == "authentication failed"


def test_unknown_and_invalid_mapped_tenants_fail_closed() -> None:
    with pytest.raises(AuthenticationFailure) as unknown:
        validator(tenants=KnownTenants(None)).validate(valid_claims())
    assert unknown.value.code == "UNKNOWN_TENANT"

    with pytest.raises(AuthenticationFailure) as invalid:
        validator(tenants=KnownTenants("bad tenant")).validate(valid_claims())
    assert invalid.value.code == "INVALID_TENANT_ID"

    with pytest.raises(AuthenticationFailure) as unavailable:
        validator(tenants=KnownTenants(fail=True)).validate(valid_claims())
    assert unavailable.value.code == "TENANT_RESOLUTION_FAILED"
    assert "tenant directory" not in str(unavailable.value)


@pytest.mark.parametrize(
    "updates",
    [
        {"scope": "unknown:scope"},
        {"roles": "unknown_role"},
    ],
)
def test_invalid_permissions_do_not_call_identity_directories(
    updates: dict[str, object],
) -> None:
    tenants = KnownTenants()
    actors = KnownActors()
    with pytest.raises(AuthenticationFailure):
        validator(tenants=tenants, actors=actors).validate(valid_claims(**updates))
    assert tenants.calls == []
    assert actors.calls == []


def test_actor_subject_is_mapped_to_a_stable_canonical_identity() -> None:
    for actors, expected_code in [
        (KnownActors(None), "UNKNOWN_ACTOR"),
        (KnownActors("bad actor"), "INVALID_ACTOR_ID"),
        (KnownActors(fail=True), "ACTOR_RESOLUTION_FAILED"),
    ]:
        with pytest.raises(AuthenticationFailure) as exc_info:
            validator(actors=actors).validate(valid_claims())
        assert exc_info.value.code == expected_code
        assert "identity directory" not in str(exc_info.value)

    pairwise_actor = validator().validate(valid_claims(sub="pairwise|reviewer-for-cli"))
    assert pairwise_actor.subject == "actor-canonical"


def test_demo_claim_requires_policy_opt_in() -> None:
    actor = validator(allow_demo=True).validate(valid_claims(demo=True))
    assert actor.demo_identity is True

    local_demo_policy = ClaimsValidationPolicy(
        "http://localhost:8081/",
        "urn:tradesieve:demo",
        deployment_mode="demo",
    )
    assert local_demo_policy.issuer.startswith("http://localhost")


def test_identity_clock_must_be_aware() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        validator(clock=NOW.replace(tzinfo=None)).validate(valid_claims())


def test_authenticator_wraps_provider_failure_without_detail() -> None:
    successful = IdentityAuthenticator(
        StaticClaimsProvider(valid_claims()), validator()
    ).authenticate("raw-bearer")
    assert successful.subject == "actor-canonical"

    authenticator = IdentityAuthenticator(
        StaticClaimsProvider(valid_claims(), reject=True), validator()
    )
    with pytest.raises(AuthenticationFailure) as exc_info:
        authenticator.authenticate("secret-token")
    assert exc_info.value.code == "CREDENTIAL_REJECTED"
    assert "provider detail" not in str(exc_info.value)

    with pytest.raises(AuthenticationFailure) as invalid_claims:
        IdentityAuthenticator(InvalidClaimsProvider(), validator()).authenticate(
            "raw-bearer"
        )
    assert invalid_claims.value.code == "INVALID_VERIFIED_CLAIMS"


def test_demo_provider_refuses_non_demo_or_implicit_activation() -> None:
    claims = valid_claims(demo=True)
    with pytest.raises(ValueError, match="demo mode"):
        DemoVerifiedClaimsProvider(mode="production", enabled=True, claims=claims)
    with pytest.raises(ValueError, match="explicit opt-in"):
        DemoVerifiedClaimsProvider(mode="demo", enabled=False, claims=claims)
    with pytest.raises(ValueError, match="explicitly marked"):
        DemoVerifiedClaimsProvider(
            mode="demo", enabled=True, claims=valid_claims(demo=False)
        )
    with pytest.raises(ValueError, match="empty token"):
        DemoVerifiedClaimsProvider(
            mode="demo", enabled=True, claims=claims, token_factory=lambda: ""
        )
    with pytest.raises(ValueError, match="empty token"):
        DemoVerifiedClaimsProvider(
            mode="demo",
            enabled=True,
            claims=claims,
            token_factory=lambda: cast(str, None),
        )


def test_demo_provider_uses_process_local_opaque_bearer() -> None:
    original_claims = valid_claims(demo=True)
    provider = DemoVerifiedClaimsProvider(
        mode="demo",
        enabled=True,
        claims=original_claims,
        token_factory=lambda: "ephemeral-bearer",
    )
    cast(list[str], original_claims["roles"]).append("auditor")
    claims = dict(provider.verify(provider.issue_bearer_token()))
    claims["sub"] = "attempted-mutation"
    cast(list[str], claims["roles"]).append("policy_approver")
    assert provider.verify("ephemeral-bearer")["sub"] == "auth0|human-1"
    assert provider.verify("ephemeral-bearer")["roles"] == ["compliance_reviewer"]

    for rejected in ["wrong", cast(str, None)]:
        with pytest.raises(IdentityVerificationError):
            provider.verify(rejected)

    random_provider = DemoVerifiedClaimsProvider(
        mode="demo", enabled=True, claims=valid_claims(demo=True)
    )
    assert random_provider.issue_bearer_token()


class ExactEntitlements:
    def __init__(
        self,
        grants: Mapping[tuple[str, str, str, str, str, str], ResolvedTargetFacts],
        *,
        fail: bool = False,
    ) -> None:
        self.grants = grants
        self.fail = fail
        self.calls: list[tuple[str, str, str, str, str, str]] = []

    def resolve(
        self,
        *,
        actor_subject: str,
        actor_tenant_id: str,
        operation: str,
        target_tenant_id: str,
        target_type: str,
        target_id: str,
    ) -> ResolvedTargetFacts | None:
        call = (
            actor_subject,
            actor_tenant_id,
            operation,
            target_tenant_id,
            target_type,
            target_id,
        )
        self.calls.append(call)
        if self.fail:
            raise RuntimeError("entitlement store unavailable")
        return self.grants.get(call)


class InvalidEntitlements(ExactEntitlements):
    def resolve(
        self,
        *,
        actor_subject: str,
        actor_tenant_id: str,
        operation: str,
        target_tenant_id: str,
        target_type: str,
        target_id: str,
    ) -> ResolvedTargetFacts | None:
        super().resolve(
            actor_subject=actor_subject,
            actor_tenant_id=actor_tenant_id,
            operation=operation,
            target_tenant_id=target_tenant_id,
            target_type=target_type,
            target_id=target_id,
        )
        return cast(ResolvedTargetFacts, True)


def actor(
    *,
    subject: str = "reviewer-1",
    actor_type: ActorType = ActorType.HUMAN,
    scopes: frozenset[Scope] = frozenset(Scope),
    roles: frozenset[Role] = frozenset(Role),
) -> ActorContext:
    return ActorContext(
        subject=subject,
        client_id="reviewer-ui",
        tenant_id="tenant-1",
        actor_type=actor_type,
        scopes=scopes,
        roles=roles,
        issuer=ISSUER,
        audience=AUDIENCE,
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=5),
        demo_identity=False,
    )


def request(
    operation: Operation | str,
    *,
    principal: ActorContext | None = None,
    context_tenant: str = "tenant-1",
    target_tenant: str = "tenant-1",
    object_id: str = "case-1",
    decision: HumanDecisionType | None = None,
) -> AuthorizationRequest:
    return AuthorizationRequest(
        context=RequestContext(
            actor=principal or actor(),
            tenant_id=context_tenant,
            correlation_id="correlation-1",
        ),
        operation=operation,
        target=TargetObject(
            tenant_id=target_tenant,
            object_type="case",
            object_id=object_id,
        ),
        human_decision=decision,
    )


def exact_grant(
    authorization_request: AuthorizationRequest,
) -> tuple[str, str, str, str, str, str]:
    return (
        authorization_request.context.actor.subject,
        authorization_request.context.actor.tenant_id,
        str(authorization_request.operation),
        authorization_request.target.tenant_id,
        authorization_request.target.object_type,
        authorization_request.target.object_id,
    )


DEFAULT_TARGET_FACTS = ResolvedTargetFacts(
    case_state=CaseState.REVIEW_REQUIRED,
    submitting_actor_id="submitter-2",
    author_actor_id="policy-author-2",
    clearance_eligible=True,
)


def service_for(
    authorization_request: AuthorizationRequest,
    events: list[AuthorizationAuditEvent],
    *,
    policies: Mapping[Operation, AuthorizationPolicy] = DEFAULT_POLICIES,
    event_id: str = "authz-test-1",
    facts: ResolvedTargetFacts = DEFAULT_TARGET_FACTS,
) -> tuple[AuthorizationService, ExactEntitlements]:
    entitlements = ExactEntitlements({exact_grant(authorization_request): facts})
    service = AuthorizationService(
        events.append,
        entitlements,
        policies=policies,
        event_id_factory=lambda: event_id,
    )
    return service, entitlements


FINAL_OPERATIONS = {
    Operation.RECORD_HUMAN_CLEARED: HumanDecisionType.HUMAN_CLEARED,
    Operation.RECORD_HUMAN_BLOCKED: HumanDecisionType.HUMAN_BLOCKED,
    Operation.RECORD_CLOSED_NO_ACTION: HumanDecisionType.CLOSED_NO_ACTION,
}

FINAL_POLICY_OPERATIONS = {
    Operation.POLICY_APPROVE,
    Operation.POLICY_ACTIVATE,
    Operation.POLICY_RETIRE,
    Operation.POLICY_ROLLBACK,
}


@pytest.mark.parametrize("operation", list(Operation))
def test_default_matrix_explicitly_allows_each_mapped_operation(
    operation: Operation,
) -> None:
    events: list[AuthorizationAuditEvent] = []
    authorization_request = request(
        operation,
        decision=FINAL_OPERATIONS.get(operation),
    )
    service, _ = service_for(authorization_request, events)

    authorized = service.require(authorization_request, now=NOW)
    assert authorized.operation is operation
    assert authorized.audit_event_id == "authz-test-1"
    assert events[-1].outcome is AuthorizationOutcome.ALLOW
    assert events[-1].reason is AuthorizationReason.ALLOWED


def test_default_policy_matrix_is_exhaustive() -> None:
    assert set(DEFAULT_POLICIES) == set(Operation)


@pytest.mark.parametrize(
    ("principal_type", "expected_reason"),
    [
        (ActorType.HUMAN, AuthorizationReason.ALLOWED),
        (ActorType.SERVICE, AuthorizationReason.ALLOWED),
        (ActorType.AGENT, AuthorizationReason.ACTOR_TYPE_DENIED),
    ],
)
def test_policy_draft_requires_author_role_and_rejects_agents(
    principal_type: ActorType,
    expected_reason: AuthorizationReason,
) -> None:
    events: list[AuthorizationAuditEvent] = []
    authorization_request = request(
        Operation.POLICY_DRAFT,
        principal=actor(
            actor_type=principal_type,
            scopes=frozenset({Scope.POLICY_OPERATE}),
            roles=frozenset({Role.POLICY_AUTHOR}),
        ),
    )
    service, _ = service_for(authorization_request, events)
    if expected_reason is AuthorizationReason.ALLOWED:
        service.require(authorization_request, now=NOW)
        assert events[-1].outcome is AuthorizationOutcome.ALLOW
    else:
        with pytest.raises(AuthorizationDenied):
            service.require(authorization_request, now=NOW)
        assert events[-1].outcome is AuthorizationOutcome.DENY
    assert events[-1].reason is expected_reason

    missing_role_request = request(
        Operation.POLICY_DRAFT,
        principal=actor(
            actor_type=ActorType.HUMAN,
            scopes=frozenset({Scope.POLICY_OPERATE}),
            roles=frozenset(),
        ),
    )
    missing_role_service, _ = service_for(missing_role_request, events)
    with pytest.raises(AuthorizationDenied):
        missing_role_service.require(missing_role_request, now=NOW)
    assert events[-1].reason is AuthorizationReason.ROLE_DENIED


@pytest.mark.parametrize("operation", sorted(FINAL_POLICY_OPERATIONS, key=str))
@pytest.mark.parametrize("principal_type", [ActorType.SERVICE, ActorType.AGENT])
def test_non_humans_cannot_run_final_policy_lifecycle_operations(
    operation: Operation,
    principal_type: ActorType,
) -> None:
    events: list[AuthorizationAuditEvent] = []
    authorization_request = request(
        operation,
        principal=actor(
            actor_type=principal_type,
            scopes=frozenset({Scope.POLICY_APPROVE}),
            roles=frozenset({Role.POLICY_APPROVER}),
        ),
    )
    service, entitlements = service_for(authorization_request, events)
    with pytest.raises(AuthorizationDenied):
        service.require(authorization_request, now=NOW)
    assert events[-1].reason is AuthorizationReason.ACTOR_TYPE_DENIED
    assert events[-1].outcome is AuthorizationOutcome.DENY
    assert entitlements.calls == []


@pytest.mark.parametrize("operation", sorted(FINAL_POLICY_OPERATIONS, key=str))
@pytest.mark.parametrize("author_actor_id", [None, "reviewer-1"])
def test_final_policy_lifecycle_requires_distinct_known_author_and_audits_denial(
    operation: Operation,
    author_actor_id: str | None,
) -> None:
    events: list[AuthorizationAuditEvent] = []
    authorization_request = request(
        operation,
        principal=actor(
            scopes=frozenset({Scope.POLICY_APPROVE}),
            roles=frozenset({Role.POLICY_APPROVER}),
        ),
    )
    service, entitlements = service_for(
        authorization_request,
        events,
        facts=ResolvedTargetFacts(author_actor_id=author_actor_id),
    )
    with pytest.raises(AuthorizationDenied) as exc_info:
        service.require(authorization_request, now=NOW)
    assert exc_info.value.code is PublicDenialCode.FORBIDDEN
    assert events[-1].reason is (AuthorizationReason.AUTHOR_APPROVER_SEPARATION_DENIED)
    assert events[-1].outcome is AuthorizationOutcome.DENY
    assert entitlements.calls == [exact_grant(authorization_request)]


@pytest.mark.parametrize(
    ("operation", "decision", "case_state", "eligible", "expected_reason"),
    [
        (
            Operation.RECORD_HUMAN_CLEARED,
            HumanDecisionType.HUMAN_CLEARED,
            CaseState.REVIEW_REQUIRED,
            True,
            AuthorizationReason.ALLOWED,
        ),
        (
            Operation.RECORD_HUMAN_CLEARED,
            HumanDecisionType.HUMAN_CLEARED,
            CaseState.INCOMPLETE,
            True,
            AuthorizationReason.CASE_STATE_DENIED,
        ),
        (
            Operation.RECORD_HUMAN_CLEARED,
            HumanDecisionType.HUMAN_CLEARED,
            CaseState.ESCALATE,
            True,
            AuthorizationReason.CASE_STATE_DENIED,
        ),
        (
            Operation.RECORD_HUMAN_CLEARED,
            HumanDecisionType.HUMAN_CLEARED,
            CaseState.REVIEW_REQUIRED,
            False,
            AuthorizationReason.CLEARANCE_NOT_ELIGIBLE,
        ),
        *[
            (
                Operation.RECORD_HUMAN_BLOCKED,
                HumanDecisionType.HUMAN_BLOCKED,
                state,
                False,
                AuthorizationReason.ALLOWED,
            )
            for state in [CaseState.REVIEW_REQUIRED, CaseState.ESCALATE]
        ],
        (
            Operation.RECORD_HUMAN_BLOCKED,
            HumanDecisionType.HUMAN_BLOCKED,
            CaseState.INCOMPLETE,
            False,
            AuthorizationReason.CASE_STATE_DENIED,
        ),
        *[
            (
                Operation.RECORD_CLOSED_NO_ACTION,
                HumanDecisionType.CLOSED_NO_ACTION,
                state,
                False,
                AuthorizationReason.ALLOWED,
            )
            for state in [
                CaseState.INCOMPLETE,
                CaseState.REVIEW_REQUIRED,
                CaseState.ESCALATE,
            ]
        ],
    ],
)
def test_final_disposition_state_matrix_is_explicit(
    operation: Operation,
    decision: HumanDecisionType,
    case_state: CaseState,
    eligible: bool,
    expected_reason: AuthorizationReason,
) -> None:
    events: list[AuthorizationAuditEvent] = []
    authorization_request = request(operation, decision=decision)
    service, _ = service_for(
        authorization_request,
        events,
        facts=ResolvedTargetFacts(
            case_state=case_state,
            submitting_actor_id="submitter-2",
            clearance_eligible=eligible,
        ),
    )
    if expected_reason is AuthorizationReason.ALLOWED:
        service.require(authorization_request, now=NOW)
    else:
        with pytest.raises(AuthorizationDenied):
            service.require(authorization_request, now=NOW)
    assert events[-1].reason is expected_reason


@pytest.mark.parametrize("principal_type", [ActorType.SERVICE, ActorType.AGENT])
@pytest.mark.parametrize("operation,decision", FINAL_OPERATIONS.items())
def test_non_humans_cannot_forge_any_final_disposition(
    principal_type: ActorType,
    operation: Operation,
    decision: HumanDecisionType,
) -> None:
    events: list[AuthorizationAuditEvent] = []
    authorization_request = request(
        operation,
        principal=actor(actor_type=principal_type),
        decision=decision,
    )
    service, _ = service_for(authorization_request, events)

    with pytest.raises(AuthorizationDenied) as exc_info:
        service.require(authorization_request, now=NOW)
    assert exc_info.value.code is PublicDenialCode.FORBIDDEN
    assert events[-1].reason is AuthorizationReason.ACTOR_TYPE_DENIED


@pytest.mark.parametrize(
    ("authorization_request", "facts", "reason"),
    [
        (
            request(
                Operation.CASE_READ,
                principal=actor(scopes=frozenset({Scope.SCREENING_READ})),
            ),
            ResolvedTargetFacts(),
            AuthorizationReason.MISSING_SCOPE,
        ),
        (
            request(
                Operation.SOURCE_OPERATE, principal=actor(actor_type=ActorType.AGENT)
            ),
            ResolvedTargetFacts(),
            AuthorizationReason.ACTOR_TYPE_DENIED,
        ),
        (
            request(Operation.SOURCE_OPERATE, principal=actor(roles=frozenset())),
            ResolvedTargetFacts(),
            AuthorizationReason.ROLE_DENIED,
        ),
        (
            request(Operation.CASE_HOLD),
            ResolvedTargetFacts(case_state=CaseState.HUMAN_CLEARED),
            AuthorizationReason.CASE_STATE_DENIED,
        ),
        (
            request(
                Operation.RECORD_HUMAN_CLEARED,
                decision=HumanDecisionType.HUMAN_BLOCKED,
            ),
            ResolvedTargetFacts(
                case_state=CaseState.REVIEW_REQUIRED,
                submitting_actor_id="submitter-2",
                clearance_eligible=True,
            ),
            AuthorizationReason.DECISION_CONTEXT_MISMATCH,
        ),
        (
            request(
                Operation.RECORD_HUMAN_CLEARED,
                decision=HumanDecisionType.HUMAN_CLEARED,
            ),
            ResolvedTargetFacts(
                case_state=CaseState.ESCALATE,
                submitting_actor_id="submitter-2",
                clearance_eligible=True,
            ),
            AuthorizationReason.CASE_STATE_DENIED,
        ),
        (
            request(
                Operation.RECORD_HUMAN_CLEARED,
                decision=HumanDecisionType.HUMAN_CLEARED,
            ),
            ResolvedTargetFacts(
                case_state=CaseState.REVIEW_REQUIRED,
                submitting_actor_id="submitter-2",
                clearance_eligible=False,
            ),
            AuthorizationReason.CLEARANCE_NOT_ELIGIBLE,
        ),
        (
            request(
                Operation.RECORD_HUMAN_CLEARED,
                decision=HumanDecisionType.HUMAN_CLEARED,
            ),
            ResolvedTargetFacts(
                case_state=CaseState.REVIEW_REQUIRED,
                submitting_actor_id=None,
                clearance_eligible=True,
            ),
            AuthorizationReason.FOUR_EYES_DENIED,
        ),
        (
            request(
                Operation.RECORD_HUMAN_CLEARED,
                decision=HumanDecisionType.HUMAN_CLEARED,
            ),
            ResolvedTargetFacts(
                case_state=CaseState.REVIEW_REQUIRED,
                submitting_actor_id="reviewer-1",
                clearance_eligible=True,
            ),
            AuthorizationReason.FOUR_EYES_DENIED,
        ),
    ],
)
def test_policy_denials_are_forbidden_and_audited(
    authorization_request: AuthorizationRequest,
    facts: ResolvedTargetFacts,
    reason: AuthorizationReason,
) -> None:
    events: list[AuthorizationAuditEvent] = []
    service, _ = service_for(authorization_request, events, facts=facts)
    with pytest.raises(AuthorizationDenied) as exc_info:
        service.require(authorization_request, now=NOW)
    assert exc_info.value.code is PublicDenialCode.FORBIDDEN
    assert str(exc_info.value) == "forbidden"
    assert exc_info.value.audit_event_id == "authz-test-1"
    assert events[-1].reason is reason
    assert events[-1].outcome is AuthorizationOutcome.DENY


def test_cross_tenant_context_and_target_are_concealed_and_audited() -> None:
    for authorization_request in [
        request(Operation.CASE_READ, context_tenant="tenant-2"),
        request(Operation.CASE_READ, target_tenant="tenant-2"),
    ]:
        events: list[AuthorizationAuditEvent] = []
        entitlements = ExactEntitlements({})
        service = AuthorizationService(
            events.append,
            entitlements,
            event_id_factory=lambda: "authz-cross-tenant",
        )
        with pytest.raises(AuthorizationDenied) as exc_info:
            service.require(authorization_request, now=NOW)
        assert exc_info.value.code is PublicDenialCode.NOT_FOUND
        assert str(exc_info.value) == "object not found"
        assert events[-1].reason is AuthorizationReason.CROSS_TENANT
        assert entitlements.calls == []


def test_same_tenant_guessed_objects_do_not_leak_existence() -> None:
    outcomes: list[tuple[PublicDenialCode, str]] = []
    for guessed_id in ["case-unknown-1", "case-unknown-2"]:
        events: list[AuthorizationAuditEvent] = []
        authorization_request = request(Operation.CASE_READ, object_id=guessed_id)
        service = AuthorizationService(
            events.append,
            ExactEntitlements({}),
            event_id_factory=lambda: "authz-not-visible",
        )
        with pytest.raises(AuthorizationDenied) as exc_info:
            service.require(authorization_request, now=NOW)
        outcomes.append((exc_info.value.code, str(exc_info.value)))
        assert events[-1].reason is AuthorizationReason.OBJECT_NOT_VISIBLE
    assert outcomes == [
        (PublicDenialCode.NOT_FOUND, "object not found"),
        (PublicDenialCode.NOT_FOUND, "object not found"),
    ]


def test_entitlement_failure_is_concealed_and_audited() -> None:
    events: list[AuthorizationAuditEvent] = []
    authorization_request = request(Operation.CASE_READ)
    service = AuthorizationService(
        events.append,
        ExactEntitlements({}, fail=True),
        event_id_factory=lambda: "authz-entitlement-failed",
    )
    with pytest.raises(AuthorizationUnavailable) as exc_info:
        service.require(authorization_request, now=NOW)
    assert str(exc_info.value) == "authorization unavailable"
    assert exc_info.value.audit_event_id == "authz-entitlement-failed"
    assert events[-1].reason is AuthorizationReason.ENTITLEMENT_CHECK_FAILED

    invalid_result_service = AuthorizationService(
        events.append,
        InvalidEntitlements({}),
        event_id_factory=lambda: "authz-invalid-entitlement-result",
    )
    with pytest.raises(AuthorizationUnavailable):
        invalid_result_service.require(authorization_request, now=NOW)
    assert events[-1].reason is AuthorizationReason.ENTITLEMENT_CHECK_FAILED


def test_unknown_and_unmapped_operations_are_denied_without_object_check() -> None:
    cases: list[
        tuple[AuthorizationRequest, Mapping[Operation, AuthorizationPolicy]]
    ] = [
        (request("UNKNOWN_OPERATION"), DEFAULT_POLICIES),
        (
            request(Operation.CASE_READ),
            {
                key: value
                for key, value in DEFAULT_POLICIES.items()
                if key is not Operation.CASE_READ
            },
        ),
    ]
    for authorization_request, policies in cases:
        events: list[AuthorizationAuditEvent] = []
        service, entitlements = service_for(
            authorization_request, events, policies=policies
        )
        with pytest.raises(AuthorizationDenied) as exc_info:
            service.require(authorization_request, now=NOW)
        assert exc_info.value.code is PublicDenialCode.FORBIDDEN
        assert events[-1].reason is AuthorizationReason.POLICY_MISSING
        assert entitlements.calls == []


@pytest.mark.parametrize(
    "authorization_request",
    [
        request("UNKNOWN_OPERATION"),
        request(
            Operation.CASE_READ,
            principal=actor(scopes=frozenset({Scope.SCREENING_READ})),
        ),
        request(
            Operation.SOURCE_OPERATE,
            principal=actor(actor_type=ActorType.AGENT),
        ),
        request(
            Operation.SOURCE_OPERATE,
            principal=actor(roles=frozenset()),
        ),
    ],
)
def test_coarse_policy_denial_does_not_call_target_resolver(
    authorization_request: AuthorizationRequest,
) -> None:
    events: list[AuthorizationAuditEvent] = []
    service, entitlements = service_for(authorization_request, events)
    with pytest.raises(AuthorizationDenied):
        service.require(authorization_request, now=NOW)
    assert entitlements.calls == []


def test_audit_event_is_minimal_complete_and_contains_no_claims_or_token() -> None:
    events: list[AuthorizationAuditEvent] = []
    authorization_request = request(Operation.CASE_READ)
    service, _ = service_for(authorization_request, events)
    service.require(authorization_request, now=NOW.astimezone())

    event = events[-1]
    assert event == AuthorizationAuditEvent(
        event_id="authz-test-1",
        occurred_at=NOW,
        actor_subject="reviewer-1",
        client_id="reviewer-ui",
        actor_type=ActorType.HUMAN,
        actor_tenant_id="tenant-1",
        request_tenant_id="tenant-1",
        target_tenant_id="tenant-1",
        operation="CASE_READ",
        target_ref="case:case-1",
        correlation_id="correlation-1",
        outcome=AuthorizationOutcome.ALLOW,
        reason=AuthorizationReason.ALLOWED,
    )
    assert "bearer" not in repr(event).lower()
    assert "scope" not in repr(event).lower()


def test_authorization_audit_failure_fails_closed() -> None:
    authorization_request = request(Operation.CASE_READ)

    def failed_sink(event: AuthorizationAuditEvent) -> NoReturn:
        raise RuntimeError(f"audit down for {event.event_id}")

    service = AuthorizationService(
        failed_sink,
        ExactEntitlements({exact_grant(authorization_request): ResolvedTargetFacts()}),
        event_id_factory=lambda: "authz-audit-failed",
    )
    with pytest.raises(AuthorizationAuditFailure) as exc_info:
        service.require(authorization_request, now=NOW)
    assert str(exc_info.value) == "authorization audit unavailable"

    for invalid_event_id in ["invalid event id", cast(str, None)]:

        def invalid_factory(value: str = invalid_event_id) -> str:
            return value

        invalid_event_service = AuthorizationService(
            lambda event: None,
            ExactEntitlements(
                {exact_grant(authorization_request): ResolvedTargetFacts()}
            ),
            event_id_factory=invalid_factory,
        )
        with pytest.raises(AuthorizationAuditFailure):
            invalid_event_service.require(authorization_request, now=NOW)


def test_default_authorization_event_identifier_is_safe() -> None:
    events: list[AuthorizationAuditEvent] = []
    authorization_request = request(Operation.CASE_READ)
    service = AuthorizationService(
        events.append,
        ExactEntitlements({exact_grant(authorization_request): ResolvedTargetFacts()}),
    )
    authorized = service.require(authorization_request, now=NOW)
    assert authorized.audit_event_id.startswith("authz-")


def test_authorization_time_and_input_value_objects_validate() -> None:
    authorization_request = request(Operation.CASE_READ)
    service, _ = service_for(authorization_request, [])
    with pytest.raises(ValueError, match="timezone-aware"):
        service.require(authorization_request, now=NOW.replace(tzinfo=None))

    with pytest.raises(TypeError, match="actor context"):
        RequestContext(cast(ActorContext, None), "tenant-1", "correlation-1")
    for tenant, correlation in [("bad tenant", "ok"), ("tenant-1", "bad value")]:
        with pytest.raises(ValueError):
            RequestContext(actor(), tenant, correlation)
    for values in [
        ("bad tenant", "case", "case-1"),
        ("tenant-1", "bad type", "case-1"),
        ("tenant-1", "case", "bad id"),
    ]:
        with pytest.raises(ValueError):
            TargetObject(*values)
    with pytest.raises(ValueError):
        ResolvedTargetFacts(submitting_actor_id="bad submitter")
    for operation in ["lowercase", "BAD OPERATION", ""]:
        with pytest.raises(ValueError, match="symbolic"):
            request(operation)
