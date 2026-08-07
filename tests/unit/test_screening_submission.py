"""TS-302 B1 immutable idempotency records and safe command binding."""

from __future__ import annotations

import copy
import hashlib
import json
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta, timezone
from threading import Barrier, Event, Lock
from typing import cast

import pytest

from tradesieve.adapters.in_memory_screening_submission import (
    InMemoryScreeningSubmissionUnitOfWork,
    ScreeningSubmissionFaultPoint,
    ScreeningSubmissionStore,
)
from tradesieve.application.auth import (
    ActorContext,
    ActorType,
    AuthorizationAuditEvent,
    AuthorizationAuditFailure,
    AuthorizationDenied,
    AuthorizationOutcome,
    AuthorizationReason,
    AuthorizationRequest,
    AuthorizationService,
    AuthorizationUnavailable,
    AuthorizedRequest,
    Operation,
    PublicDenialCode,
    RequestContext,
    ResolvedTargetFacts,
    Scope,
    TargetObject,
)
from tradesieve.application.screening_intake import CanonicalScreeningIntake
from tradesieve.application.screening_submission import (
    MAX_IDEMPOTENCY_KEY_BYTES,
    SCREENING_SUBMIT_OPERATION,
    BoundScreeningSubmission,
    ScreeningSubmissionAtomicWrite,
    ScreeningSubmissionCommandError,
    ScreeningSubmissionCommandErrorCode,
    ScreeningSubmissionCommitOutcomeUnknown,
    ScreeningSubmissionPersistenceFailure,
    ScreeningSubmissionService,
    ScreeningSubmissionServiceDisposition,
    ScreeningSubmissionServiceError,
    ScreeningSubmissionServiceErrorCode,
    ScreeningSubmissionServiceResult,
    ScreeningSubmissionUnitOfWork,
    ScreeningSubmissionWriteResult,
    bind_screening_submission,
    screening_authorization_target,
    screening_idempotency_scope,
)
from tradesieve.domain.screening_submission import (
    JSON_MEDIA_TYPE,
    SCHEMA_VERSION,
    AcceptedIntakeReference,
    ScreeningAcceptanceReceipt,
    ScreeningAcceptedOutboxIntent,
    ScreeningActorType,
    ScreeningAttemptAudit,
    ScreeningAttemptOutcome,
    ScreeningIdempotencyResult,
    ScreeningIdempotencyScope,
    ScreeningIdentity,
    ScreeningOutboxEventType,
    ScreeningReceiptType,
    ScreeningSubmissionIntegrityError,
)

NOW = datetime(2026, 8, 7, 10, 0, tzinfo=UTC)
COMMITTED_AT = NOW + timedelta(seconds=1)
HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64
PRIVATE_KEY = "private idempotency key / SECRET"  # pragma: allowlist secret
BUSINESS_SENTINEL = "PRIVATE-CUSTOMER-PAYMENT-DOCUMENT-SENTINEL"


class StringSubclass(str):
    pass


def request_payload(
    *,
    tenant_id: str = "tenant-1",
    correlation_id: str = "correlation-1",
    action: str = "CUSTOMER_ONBOARDING",
) -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "tenant_id": tenant_id,
        "correlation_id": correlation_id,
        "data_classification": "SYNTHETIC",
        "external_object": {
            "system": "synthetic-crm",
            "object_type": "CUSTOMER",
            "object_id": BUSINESS_SENTINEL,
            "object_version": "1",
        },
        "proposed_action": action,
        "activities": [],
        "action_due_at": None,
        "legal_nexus": [],
        "parties": [],
        "ownership_and_control": [],
        "goods": [],
        "route": None,
        "documents": [],
        "payment": None,
    }


def actor(
    *,
    tenant_id: str = "tenant-1",
    client_id: str = "client-1",
    subject: str = "actor-1",
    actor_tenant_id: str | None = None,
    actor_type: ActorType = ActorType.SERVICE,
    scopes: frozenset[Scope] = frozenset({Scope.SCREENING_SUBMIT}),
) -> ActorContext:
    return ActorContext(
        subject=subject,
        client_id=client_id,
        tenant_id=actor_tenant_id or tenant_id,
        actor_type=actor_type,
        scopes=scopes,
        roles=frozenset(),
        issuer="https://identity.example.test/",
        audience="urn:tradesieve:test",
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=5),
        demo_identity=False,
    )


def context(
    *,
    tenant_id: str = "tenant-1",
    client_id: str = "client-1",
    subject: str = "actor-1",
    correlation_id: str = "correlation-1",
    actor_tenant_id: str | None = None,
    actor_type: ActorType = ActorType.SERVICE,
    scopes: frozenset[Scope] = frozenset({Scope.SCREENING_SUBMIT}),
) -> RequestContext:
    return RequestContext(
        actor=actor(
            tenant_id=tenant_id,
            client_id=client_id,
            subject=subject,
            actor_tenant_id=actor_tenant_id,
            actor_type=actor_type,
            scopes=scopes,
        ),
        tenant_id=tenant_id,
        correlation_id=correlation_id,
    )


def intake(
    *,
    tenant_id: str = "tenant-1",
    client_id: str = "client-1",
    subject: str = "actor-1",
    correlation_id: str = "correlation-1",
    action: str = "CUSTOMER_ONBOARDING",
    indent: int | None = None,
    actor_tenant_id: str | None = None,
    actor_type: ActorType = ActorType.SERVICE,
    scopes: frozenset[Scope] = frozenset({Scope.SCREENING_SUBMIT}),
) -> CanonicalScreeningIntake:
    document = request_payload(
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        action=action,
    )
    raw = json.dumps(
        document,
        ensure_ascii=False,
        separators=None if indent else (",", ":"),
        indent=indent,
    ).encode()
    return CanonicalScreeningIntake.decode(
        raw,
        "application/json",
        context=context(
            tenant_id=tenant_id,
            client_id=client_id,
            subject=subject,
            correlation_id=correlation_id,
            actor_tenant_id=actor_tenant_id,
            actor_type=actor_type,
            scopes=scopes,
        ),
        received_at=NOW,
    )


def authorized(
    item: CanonicalScreeningIntake,
    key: object = PRIVATE_KEY,
    *,
    audit_event_id: str = "authz-opaque-1",
) -> AuthorizedRequest:
    scope = screening_idempotency_scope(item, key)
    return AuthorizedRequest(
        context=item.context,
        operation=Operation.SCREENING_SUBMIT,
        target=screening_authorization_target(scope),
        audit_event_id=audit_event_id,
    )


def command(
    item: CanonicalScreeningIntake | None = None,
    key: object = PRIVATE_KEY,
) -> BoundScreeningSubmission:
    actual = item or intake()
    return bind_screening_submission(actual, key, authorized(actual, key))


def graph(
    bound: BoundScreeningSubmission | None = None,
    *,
    intake_id: str = "intake-opaque-1",
    screening_id: str = "screening-opaque-1",
    outbox_id: str = "outbox-opaque-1",
    committed_at: datetime = COMMITTED_AT,
) -> tuple[
    AcceptedIntakeReference,
    ScreeningIdentity,
    ScreeningAcceptedOutboxIntent,
    ScreeningIdempotencyResult,
]:
    actual = bound or command()
    accepted = actual.accepted_intake_reference(intake_id)
    identity = ScreeningIdentity(
        screening_id=screening_id,
        intake=accepted,
        created_at=committed_at,
    )
    outbox = ScreeningAcceptedOutboxIntent(
        event_id=outbox_id,
        event_type=ScreeningOutboxEventType.SCREENING_ACCEPTED,
        schema_version=SCHEMA_VERSION,
        occurred_at=committed_at,
        intake_id=accepted.intake_id,
        screening_id=identity.screening_id,
    )
    result = ScreeningIdempotencyResult(
        scope=actual.scope,
        canonical_hash=accepted.canonical_hash,
        intake=accepted,
        screening=identity,
        outbox=outbox,
        committed_at=committed_at,
    )
    return accepted, identity, outbox, result


def atomic_write(
    bound: BoundScreeningSubmission | None = None,
    *,
    attempt_id: str = "attempt-applied",
    occurred_at: datetime = COMMITTED_AT,
    intake_id: str = "intake-opaque-1",
    screening_id: str = "screening-opaque-1",
    outbox_id: str = "outbox-opaque-1",
) -> ScreeningSubmissionAtomicWrite:
    actual = bound or command()
    _, _, _, proposed = graph(
        actual,
        intake_id=intake_id,
        screening_id=screening_id,
        outbox_id=outbox_id,
        committed_at=occurred_at,
    )
    return ScreeningSubmissionAtomicWrite(
        bound=actual,
        proposed_result=proposed,
        attempt_id=attempt_id,
        occurred_at=occurred_at,
    )


class SequenceIdFactory:
    def __init__(self, prefix: str) -> None:
        self._prefix = prefix
        self._next = 0
        self._lock = Lock()

    def __call__(self) -> str:
        with self._lock:
            self._next += 1
            return f"{self._prefix}-{self._next}"


class ScreeningEntitlements:
    def __init__(self, *, visible: bool = True, fail: bool = False) -> None:
        self.visible = visible
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
            raise RuntimeError(BUSINESS_SENTINEL)
        return ResolvedTargetFacts() if self.visible else None


class ObservedSubmissionUnitOfWork:
    def __init__(self, delegate: InMemoryScreeningSubmissionUnitOfWork) -> None:
        self.delegate = delegate
        self.submit_calls: list[ScreeningSubmissionAtomicWrite] = []
        self.read_calls: list[ScreeningIdempotencyScope] = []
        self.resolve_calls: list[tuple[ScreeningIdempotencyScope, str, str]] = []
        self.submit_override: (
            Callable[[ScreeningSubmissionAtomicWrite], ScreeningSubmissionWriteResult]
            | None
        ) = None
        self.resolve_override: (
            Callable[
                [ScreeningIdempotencyScope, str, str],
                ScreeningSubmissionWriteResult | None,
            ]
            | None
        ) = None

    def submit_atomic(
        self, request: ScreeningSubmissionAtomicWrite
    ) -> ScreeningSubmissionWriteResult:
        self.submit_calls.append(request.reverify())
        if self.submit_override is not None:
            return self.submit_override(request)
        return self.delegate.submit_atomic(request)

    def read_result(
        self, scope: ScreeningIdempotencyScope
    ) -> ScreeningIdempotencyResult | None:
        self.read_calls.append(scope.reverify())
        return self.delegate.read_result(scope)

    def resolve_attempt(
        self,
        scope: ScreeningIdempotencyScope,
        canonical_hash: str,
        attempt_id: str,
    ) -> ScreeningSubmissionWriteResult | None:
        self.resolve_calls.append((scope.reverify(), canonical_hash, attempt_id))
        if self.resolve_override is not None:
            return self.resolve_override(scope, canonical_hash, attempt_id)
        return self.delegate.resolve_attempt(scope, canonical_hash, attempt_id)


def screening_service(
    repository: InMemoryScreeningSubmissionUnitOfWork | None = None,
    *,
    events: list[AuthorizationAuditEvent] | None = None,
    entitlements: ScreeningEntitlements | None = None,
    observed: ObservedSubmissionUnitOfWork | None = None,
    clock: Callable[[], datetime] = lambda: COMMITTED_AT,
    authorization_event_id_factory: Callable[[], str] | None = None,
    attempt_id_factory: Callable[[], str] | None = None,
    intake_id_factory: Callable[[], str] | None = None,
    screening_id_factory: Callable[[], str] | None = None,
    outbox_id_factory: Callable[[], str] | None = None,
    audit_sink: Callable[[AuthorizationAuditEvent], None] | None = None,
) -> tuple[
    ScreeningSubmissionService,
    ScreeningEntitlements,
    ObservedSubmissionUnitOfWork,
    list[AuthorizationAuditEvent],
]:
    actual_repository = repository or InMemoryScreeningSubmissionUnitOfWork()
    actual_events = events if events is not None else []
    actual_entitlements = entitlements or ScreeningEntitlements()
    actual_observed = observed or ObservedSubmissionUnitOfWork(actual_repository)
    authorization = AuthorizationService(
        audit_sink or actual_events.append,
        actual_entitlements,
        event_id_factory=(
            authorization_event_id_factory or SequenceIdFactory("authz-service")
        ),
    )
    return (
        ScreeningSubmissionService(
            authorization,
            actual_observed,
            clock=clock,
            attempt_id_factory=attempt_id_factory
            or SequenceIdFactory("attempt-service"),
            intake_id_factory=intake_id_factory or SequenceIdFactory("intake-service"),
            screening_id_factory=screening_id_factory
            or SequenceIdFactory("screening-service"),
            outbox_id_factory=outbox_id_factory or SequenceIdFactory("outbox-service"),
        ),
        actual_entitlements,
        actual_observed,
        actual_events,
    )


def test_exact_private_key_is_retained_only_as_sha256_digest() -> None:
    item = intake()
    scope = screening_idempotency_scope(item, PRIVATE_KEY)
    expected = "sha256:" + hashlib.sha256(PRIVATE_KEY.encode()).hexdigest()

    assert scope == ScreeningIdempotencyScope(
        tenant_id="tenant-1",
        client_id="client-1",
        operation=SCREENING_SUBMIT_OPERATION,
        key_digest=expected,
    )
    assert scope.key_digest == expected
    assert PRIVATE_KEY not in repr(scope)
    assert expected not in repr(scope)
    assert "client-1" not in repr(scope)
    assert "tenant-1" not in repr(scope)
    assert not hasattr(scope, "idempotency_key")


def test_key_byte_boundaries_are_exact_and_opaque() -> None:
    item = intake()
    for key in ("x", "x" * MAX_IDEMPOTENCY_KEY_BYTES, "é" * 128, " \x00 "):
        assert screening_idempotency_scope(item, key).key_digest.startswith("sha256:")

    for invalid_key in (
        "",
        "x" * (MAX_IDEMPOTENCY_KEY_BYTES + 1),
        "é" * 129,
        "\ud800",
        b"private-key",
        StringSubclass("private-key"),
        None,
    ):
        with pytest.raises(ScreeningSubmissionCommandError) as exc_info:
            screening_idempotency_scope(item, invalid_key)
        assert (
            exc_info.value.code
            is ScreeningSubmissionCommandErrorCode.INVALID_IDEMPOTENCY_KEY
        )
        rendered = "".join(traceback.format_exception(exc_info.value))
        assert repr(invalid_key) not in str(exc_info.value)
        assert repr(invalid_key) not in repr(exc_info.value)
        if isinstance(invalid_key, str) and invalid_key:
            assert invalid_key not in rendered


def test_scope_equality_includes_every_dimension() -> None:
    base = screening_idempotency_scope(intake(), PRIVATE_KEY)
    variants = (
        replace(base, tenant_id="tenant-2"),
        replace(base, client_id="client-2"),
        replace(base, operation="OTHER_OPERATION"),
        replace(base, key_digest=HASH_B),
    )
    assert all(item != base for item in variants)
    assert len({base, *variants}) == 5
    assert base.reverify() == base
    with pytest.raises(FrozenInstanceError):
        base.operation = "OTHER_OPERATION"  # type: ignore[misc]


def test_target_is_deterministic_scope_only_and_contains_no_request_values() -> None:
    base = screening_idempotency_scope(intake(), PRIVATE_KEY)
    first = screening_authorization_target(base)
    second = screening_authorization_target(base.reverify())
    assert first == second
    assert first.tenant_id == "tenant-1"
    assert first.object_type == "SCREENING_IDEMPOTENCY"
    assert first.object_id.startswith("idem-")
    for private in (
        PRIVATE_KEY,
        base.key_digest,
        "client-1",
        BUSINESS_SENTINEL,
    ):
        assert private not in first.object_id

    variants = (
        replace(base, tenant_id="tenant-2"),
        replace(base, client_id="client-2"),
        replace(base, operation="OTHER_OPERATION"),
        replace(base, key_digest=HASH_B),
    )
    assert (
        len(
            {
                first.object_id,
                *(screening_authorization_target(v).object_id for v in variants),
            }
        )
        == 5
    )


def test_exact_authorized_request_binds_to_a_private_immutable_command() -> None:
    item = intake()
    accepted_authorization = authorized(item)
    bound = bind_screening_submission(item, PRIVATE_KEY, accepted_authorization)

    assert bound.scope == screening_idempotency_scope(item, PRIVATE_KEY)
    assert bound.authorization_target == accepted_authorization.target
    assert bound.reverify() == bound
    assert repr(bound) == "BoundScreeningSubmission()"
    rendered = repr(bound)
    for private in (
        PRIVATE_KEY,
        bound.scope.key_digest,
        item.byte_hash,
        item.canonical_hash,
        "client-1",
        "actor-1",
        "correlation-1",
        BUSINESS_SENTINEL,
    ):
        assert private not in rendered


def test_command_scope_and_target_reads_return_no_alias() -> None:
    bound = command()
    returned_scope = bound.scope
    returned_target = bound.authorization_target
    object.__setattr__(returned_scope, "client_id", "mutated-client")
    object.__setattr__(returned_target, "object_id", "mutated-target")
    assert bound.scope.client_id == "client-1"
    assert bound.authorization_target.object_id.startswith("idem-")


def test_authorization_and_intake_input_alias_mutation_does_not_change_command() -> (
    None
):
    item = intake()
    accepted_authorization = authorized(item)
    bound = bind_screening_submission(item, PRIVATE_KEY, accepted_authorization)
    original_scope = bound.scope
    original_target = bound.authorization_target

    object.__setattr__(accepted_authorization.context.actor, "client_id", "mutated")
    object.__setattr__(accepted_authorization.target, "object_id", "mutated")
    object.__setattr__(accepted_authorization, "audit_event_id", "mutated")
    object.__setattr__(item, "byte_hash", HASH_B)
    assert bound.scope == original_scope
    assert bound.authorization_target == original_target
    assert bound.reverify() == bound


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_operation",
        "wrong_target",
        "wrong_tenant",
        "wrong_correlation",
        "wrong_client",
        "bad_audit_id",
        "wrong_authorized_type",
        "wrong_context_type",
    ],
)
def test_authorization_binding_mismatches_are_finite_and_redacted(
    mutation: str,
) -> None:
    item = intake()
    accepted = authorized(item)
    if mutation == "wrong_operation":
        object.__setattr__(accepted, "operation", Operation.SCREENING_READ)
    elif mutation == "wrong_target":
        object.__setattr__(
            accepted,
            "target",
            TargetObject("tenant-1", "SCREENING_IDEMPOTENCY", "wrong-target"),
        )
    elif mutation == "wrong_tenant":
        object.__setattr__(accepted.context, "tenant_id", "tenant-2")
    elif mutation == "wrong_correlation":
        object.__setattr__(accepted.context, "correlation_id", "correlation-2")
    elif mutation == "wrong_client":
        object.__setattr__(accepted.context.actor, "client_id", "client-2")
    elif mutation == "bad_audit_id":
        object.__setattr__(accepted, "audit_event_id", BUSINESS_SENTINEL + " /bad")
    elif mutation == "wrong_authorized_type":
        accepted = cast(AuthorizedRequest, object())
    else:
        object.__setattr__(accepted, "context", object())

    with pytest.raises(ScreeningSubmissionCommandError) as exc_info:
        bind_screening_submission(item, PRIVATE_KEY, accepted)
    assert (
        exc_info.value.code is ScreeningSubmissionCommandErrorCode.AUTHORIZATION_BINDING
    )
    rendered = "".join(traceback.format_exception(exc_info.value))
    for private in (PRIVATE_KEY, BUSINESS_SENTINEL, "client-2", "correlation-2"):
        assert private not in str(exc_info.value)
        assert private not in repr(exc_info.value)
        assert private not in rendered


def test_accepted_intake_reference_is_bound_to_canonical_intake() -> None:
    item = intake()
    accepted = command(item).accepted_intake_reference("intake-opaque-1")
    assert accepted == AcceptedIntakeReference(
        intake_id="intake-opaque-1",
        tenant_id="tenant-1",
        client_id="client-1",
        actor_subject="actor-1",
        actor_type=ScreeningActorType.SERVICE,
        correlation_id="correlation-1",
        scope_fingerprint=command(item).scope.fingerprint(),
        schema_version="1.0.0",
        media_type=JSON_MEDIA_TYPE,
        byte_length=item.byte_length,
        byte_hash=item.byte_hash,
        canonical_hash=item.canonical_hash,
        received_at=NOW,
    )
    assert accepted.reverify() == accepted
    rendered = repr(accepted)
    for private in (
        accepted.tenant_id,
        accepted.client_id,
        accepted.actor_subject,
        accepted.correlation_id,
        accepted.scope_fingerprint,
        accepted.media_type,
        accepted.byte_hash,
        accepted.canonical_hash,
    ):
        assert private not in rendered


def test_complete_domain_graph_and_public_receipt_are_exact_and_safe() -> None:
    accepted, identity, outbox, result = graph()
    receipt = ScreeningAcceptanceReceipt.from_result(result)

    assert identity.intake == accepted
    assert result.intake == accepted
    assert result.screening == identity
    assert result.outbox == outbox
    assert receipt == ScreeningAcceptanceReceipt(
        receipt_type=ScreeningReceiptType.SCREENING_ACCEPTED,
        schema_version=SCHEMA_VERSION,
        accepted_at=COMMITTED_AT,
        intake_id="intake-opaque-1",
        screening_id="screening-opaque-1",
        outbox_event_id="outbox-opaque-1",
    )
    assert result.reverify() == result
    assert identity.reverify() == identity
    assert outbox.reverify() == outbox
    assert receipt.reverify() == receipt

    assert {item.name for item in fields(outbox)} == {
        "event_id",
        "event_type",
        "schema_version",
        "occurred_at",
        "intake_id",
        "screening_id",
    }
    assert {item.name for item in fields(receipt)} == {
        "receipt_type",
        "schema_version",
        "accepted_at",
        "intake_id",
        "screening_id",
        "outbox_event_id",
    }
    public_rendering = repr(outbox) + repr(receipt)
    for private in (
        PRIVATE_KEY,
        result.scope.key_digest,
        accepted.byte_hash,
        accepted.canonical_hash,
        accepted.media_type,
        accepted.tenant_id,
        "client-1",
        "actor-1",
        "correlation-1",
        BUSINESS_SENTINEL,
    ):
        assert private not in public_rendering


@pytest.mark.parametrize(
    ("outcome", "canonical_hash"),
    [
        (ScreeningAttemptOutcome.APPLIED, None),
        (ScreeningAttemptOutcome.REPLAY, None),
        (ScreeningAttemptOutcome.CONFLICT, HASH_B),
    ],
)
def test_attempt_audit_outcomes_are_finite_linked_and_attributable(
    outcome: ScreeningAttemptOutcome, canonical_hash: str | None
) -> None:
    bound = command()
    _, _, _, result = graph(bound)
    if canonical_hash is None:
        audit = bound.attempt_audit(
            attempt_id=f"attempt-{outcome.value.lower()}",
            outcome=outcome,
            occurred_at=COMMITTED_AT,
            result=result,
        )
    else:
        audit = ScreeningAttemptAudit(
            attempt_id="attempt-conflict",
            outcome=outcome,
            occurred_at=COMMITTED_AT + timedelta(seconds=1),
            scope=bound.scope,
            actor_type=ScreeningActorType.SERVICE,
            actor_subject="actor-1",
            correlation_id="correlation-1",
            authorization_event_id="authz-opaque-1",
            byte_hash=bound.accepted_intake_reference("temp-intake").byte_hash,
            canonical_hash=canonical_hash,
            result=result,
        )
    assert audit.outcome is outcome
    assert audit.result == result
    assert audit.actor_subject == "actor-1"
    assert audit.correlation_id == "correlation-1"
    assert audit.authorization_event_id == "authz-opaque-1"
    assert audit.reverify() == audit
    rendered = repr(audit)
    for private in (
        "actor-1",
        "client-1",
        "correlation-1",
        "authz-opaque-1",
        audit.byte_hash,
        audit.canonical_hash,
        audit.scope.key_digest,
    ):
        assert private not in rendered


def test_semantic_retry_changes_attempt_attribution_not_stable_result() -> None:
    first = command(intake(indent=None))
    _, _, _, result = graph(first)
    retry_intake = intake(
        subject="actor-2",
        correlation_id="correlation-2",
        indent=2,
    )
    retry = command(retry_intake)
    audit = retry.attempt_audit(
        attempt_id="attempt-replay",
        outcome=ScreeningAttemptOutcome.REPLAY,
        occurred_at=COMMITTED_AT + timedelta(seconds=1),
        result=result,
    )
    assert retry.scope == first.scope
    assert retry_intake.byte_hash != first.accepted_intake_reference("temp").byte_hash
    assert retry_intake.canonical_hash == result.canonical_hash
    assert audit.actor_subject == "actor-2"
    assert audit.correlation_id == "correlation-2"
    assert audit.byte_hash == retry_intake.byte_hash
    assert audit.result == result


def test_business_change_can_form_only_a_conflict_attempt_against_stable_result() -> (
    None
):
    first = command()
    _, _, _, result = graph(first)
    changed = command(intake(action="QUOTE_RELEASE"))
    assert changed.scope == first.scope
    assert changed.accepted_intake_reference("temp").canonical_hash != (
        result.canonical_hash
    )
    conflict = changed.attempt_audit(
        attempt_id="attempt-conflict",
        outcome=ScreeningAttemptOutcome.CONFLICT,
        occurred_at=COMMITTED_AT + timedelta(seconds=1),
        result=result,
    )
    assert conflict.outcome is ScreeningAttemptOutcome.CONFLICT
    assert conflict.result == result


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("tenant_id", StringSubclass("tenant-1")),
        ("client_id", "bad client"),
        ("operation", StringSubclass(SCREENING_SUBMIT_OPERATION)),
        ("key_digest", "a" * 64),
    ],
)
def test_scope_constructor_requires_exact_bounded_types(
    field_name: str, replacement: object
) -> None:
    scope = screening_idempotency_scope(intake(), PRIVATE_KEY)
    with pytest.raises(ScreeningSubmissionIntegrityError):
        replace(scope, **{field_name: replacement})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("intake_id", StringSubclass("intake-opaque-1")),
        ("tenant_id", "bad tenant"),
        ("client_id", StringSubclass("client-1")),
        ("actor_subject", StringSubclass("actor-1")),
        ("actor_type", "SERVICE"),
        ("correlation_id", StringSubclass("correlation-1")),
        ("scope_fingerprint", "a" * 64),
        ("schema_version", "1.0.1"),
        ("media_type", "text/json"),
        ("media_type", StringSubclass(JSON_MEDIA_TYPE)),
        ("byte_length", True),
        ("byte_length", 0),
        ("byte_length", 1_048_577),
        ("byte_hash", "sha256:" + "A" * 64),
        ("canonical_hash", "not-a-hash"),
        ("received_at", datetime(2026, 8, 7, 12, tzinfo=UTC)),
    ],
)
def test_accepted_intake_constructor_rejects_type_time_and_hash_confusion(
    field_name: str, replacement: object
) -> None:
    accepted, _, _, _ = graph()
    if field_name == "received_at":
        replacement = datetime(2026, 8, 7, 18, tzinfo=timezone(timedelta(hours=8)))
    with pytest.raises(ScreeningSubmissionIntegrityError):
        replace(accepted, **{field_name: replacement})  # type: ignore[arg-type]


def test_nested_record_constructor_linkage_and_time_checks_fail_closed() -> None:
    accepted, identity, outbox, result = graph()
    other_accepted = replace(accepted, intake_id="intake-opaque-2")
    bad_identity_inputs: tuple[dict[str, object], ...] = (
        {"screening_id": StringSubclass("screening-opaque-1")},
        {"intake": object()},
        {"created_at": NOW - timedelta(seconds=1)},
        {"created_at": datetime(2026, 8, 7, 18, tzinfo=timezone(timedelta(hours=8)))},
    )
    for identity_updates in bad_identity_inputs:
        with pytest.raises(ScreeningSubmissionIntegrityError):
            replace(identity, **identity_updates)  # type: ignore[arg-type]

    bad_outbox_inputs: tuple[dict[str, object], ...] = (
        {"event_id": "bad event"},
        {"event_type": "screening.accepted"},
        {"schema_version": "2.0.0"},
        {"occurred_at": datetime(2026, 8, 7, 18, tzinfo=timezone(timedelta(hours=8)))},
        {"intake_id": "bad intake"},
        {"screening_id": "bad screening"},
    )
    for outbox_updates in bad_outbox_inputs:
        with pytest.raises(ScreeningSubmissionIntegrityError):
            replace(outbox, **outbox_updates)  # type: ignore[arg-type]

    wrong_identity = ScreeningIdentity(
        "screening-opaque-1", other_accepted, COMMITTED_AT
    )
    wrong_outbox = replace(outbox, intake_id=other_accepted.intake_id)
    result_updates: tuple[dict[str, object], ...] = (
        {"scope": object()},
        {"canonical_hash": HASH_B},
        {"intake": object()},
        {"screening": object()},
        {"outbox": object()},
        {"screening": wrong_identity},
        {"outbox": wrong_outbox},
        {"committed_at": NOW},
        {"committed_at": datetime(2026, 8, 7, 18, tzinfo=timezone(timedelta(hours=8)))},
    )
    for result_update in result_updates:
        with pytest.raises(ScreeningSubmissionIntegrityError):
            replace(result, **result_update)  # type: ignore[arg-type]


def test_attempt_and_receipt_constructor_failures_are_finite_and_redacted() -> None:
    bound = command()
    _, _, _, result = graph(bound)
    audit = bound.attempt_audit(
        attempt_id="attempt-applied",
        outcome=ScreeningAttemptOutcome.APPLIED,
        occurred_at=COMMITTED_AT,
        result=result,
    )
    audit_updates: tuple[dict[str, object], ...] = (
        {"attempt_id": "bad attempt"},
        {"outcome": "APPLIED"},
        {"occurred_at": NOW},
        {"occurred_at": COMMITTED_AT + timedelta(seconds=20)},
        {"outcome": ScreeningAttemptOutcome.REPLAY, "canonical_hash": HASH_B},
        {"scope": replace(audit.scope, client_id="client-2")},
        {"actor_type": "SERVICE"},
        {"actor_type": ScreeningActorType.HUMAN},
        {"actor_subject": "bad actor"},
        {"actor_subject": "actor-2"},
        {"correlation_id": "bad correlation"},
        {"correlation_id": "correlation-2"},
        {"authorization_event_id": "bad event"},
        {"byte_hash": "bad-hash"},
        {"byte_hash": HASH_B},
        {"canonical_hash": HASH_B},
        {"result": object()},
    )
    for audit_update in audit_updates:
        with pytest.raises(ScreeningSubmissionIntegrityError) as exc_info:
            replace(audit, **audit_update)  # type: ignore[arg-type]
        assert BUSINESS_SENTINEL not in repr(exc_info.value)

    conflict = replace(
        audit,
        outcome=ScreeningAttemptOutcome.CONFLICT,
        canonical_hash=HASH_B,
    )
    with pytest.raises(ScreeningSubmissionIntegrityError):
        replace(conflict, canonical_hash=result.canonical_hash)

    receipt = ScreeningAcceptanceReceipt.from_result(result)
    receipt_updates: tuple[dict[str, object], ...] = (
        {"receipt_type": "SCREENING_ACCEPTED"},
        {"schema_version": "2.0.0"},
        {"accepted_at": datetime(2026, 8, 7, 18, tzinfo=timezone(timedelta(hours=8)))},
        {"intake_id": "bad intake"},
        {"screening_id": "bad screening"},
        {"outbox_event_id": "bad outbox"},
    )
    for receipt_update in receipt_updates:
        with pytest.raises(ScreeningSubmissionIntegrityError):
            replace(receipt, **receipt_update)  # type: ignore[arg-type]
    with pytest.raises(ScreeningSubmissionIntegrityError):
        ScreeningAcceptanceReceipt.from_result(
            cast(ScreeningIdempotencyResult, object())
        )


def test_nested_constructor_inputs_are_defensively_reconstructed() -> None:
    accepted, identity, outbox, result = graph()
    assert identity.intake is not accepted
    assert result.intake is not accepted
    assert result.screening is not identity
    assert result.outbox is not outbox

    object.__setattr__(accepted, "intake_id", "mutated-intake")
    object.__setattr__(identity, "screening_id", "mutated-screening")
    object.__setattr__(outbox, "event_id", "mutated-outbox")
    assert result.intake.intake_id == "intake-opaque-1"
    assert result.screening.screening_id == "screening-opaque-1"
    assert result.outbox.event_id == "outbox-opaque-1"
    assert result.reverify() == result


def test_post_construction_tamper_is_detected_by_reverification() -> None:
    accepted, identity, outbox, result = graph()
    audit = command().attempt_audit(
        attempt_id="attempt-applied",
        outcome=ScreeningAttemptOutcome.APPLIED,
        occurred_at=COMMITTED_AT,
        result=result,
    )
    receipt = ScreeningAcceptanceReceipt.from_result(result)
    cases = (
        (accepted, "byte_length", 0),
        (accepted, "media_type", "text/json"),
        (identity, "created_at", NOW - timedelta(seconds=1)),
        (outbox, "screening_id", "bad screening"),
        (result, "canonical_hash", HASH_B),
        (audit, "occurred_at", COMMITTED_AT + timedelta(seconds=20)),
        (audit, "byte_hash", HASH_B),
        (audit, "canonical_hash", HASH_B),
        (audit, "actor_type", ScreeningActorType.HUMAN),
        (audit, "actor_subject", "actor-2"),
        (audit, "correlation_id", "correlation-2"),
        (receipt, "schema_version", "2.0.0"),
    )
    for record, field_name, replacement in cases:
        corrupted = copy.deepcopy(record)
        object.__setattr__(corrupted, field_name, replacement)
        with pytest.raises(ScreeningSubmissionIntegrityError):
            corrupted.reverify()

    for field_name, replacement in (
        ("tenant_id", "tenant-2"),
        ("client_id", "client-2"),
        ("operation", "OTHER_OPERATION"),
        ("key_digest", HASH_B),
    ):
        corrupted = copy.deepcopy(result)
        object.__setattr__(corrupted.scope, field_name, replacement)
        with pytest.raises(ScreeningSubmissionIntegrityError):
            corrupted.reverify()

    for field_name, replacement in (
        ("client_id", "client-2"),
        ("scope_fingerprint", HASH_B),
        ("media_type", "text/json"),
    ):
        corrupted = copy.deepcopy(result)
        object.__setattr__(corrupted.intake, field_name, replacement)
        with pytest.raises(ScreeningSubmissionIntegrityError):
            corrupted.reverify()


def test_command_public_error_constructor_and_unexpected_failures_are_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(TypeError, match="^invalid screening submission command error$"):
        ScreeningSubmissionCommandError(
            cast(ScreeningSubmissionCommandErrorCode, "PRIVATE-CODE")
        )

    sentinel = "PRIVATE-NESTED-BINDING-SENTINEL"

    def explode(_scope: ScreeningIdempotencyScope) -> TargetObject:
        raise RuntimeError(sentinel)

    monkeypatch.setattr(
        "tradesieve.application.screening_submission._target_unwrapped", explode
    )
    item = intake()
    with pytest.raises(ScreeningSubmissionCommandError) as exc_info:
        screening_authorization_target(screening_idempotency_scope(item, PRIVATE_KEY))
    assert exc_info.value.code is ScreeningSubmissionCommandErrorCode.INTEGRITY_FAILURE
    rendered = "".join(traceback.format_exception(exc_info.value))
    assert sentinel not in str(exc_info.value)
    assert sentinel not in repr(exc_info.value)
    assert sentinel not in rendered
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


def test_application_boundary_maps_invalid_input_domain_and_command_state() -> None:
    with pytest.raises(ScreeningSubmissionCommandError) as intake_type_error:
        screening_idempotency_scope(
            cast(CanonicalScreeningIntake, object()), PRIVATE_KEY
        )
    assert (
        intake_type_error.value.code
        is ScreeningSubmissionCommandErrorCode.INTEGRITY_FAILURE
    )

    corrupted_scope = screening_idempotency_scope(intake(), PRIVATE_KEY)
    object.__setattr__(corrupted_scope, "key_digest", "bad-hash")
    with pytest.raises(ScreeningSubmissionCommandError) as scope_error:
        screening_authorization_target(corrupted_scope)
    assert (
        scope_error.value.code is ScreeningSubmissionCommandErrorCode.INTEGRITY_FAILURE
    )

    valid = command()
    with pytest.raises(ScreeningSubmissionCommandError) as command_type_error:
        BoundScreeningSubmission(
            _intake=cast(CanonicalScreeningIntake, object()),
            _scope=valid.scope,
            _authorization_target=valid.authorization_target,
            _authorization_event_id="authz-opaque-1",
        )
    assert (
        command_type_error.value.code
        is ScreeningSubmissionCommandErrorCode.INTEGRITY_FAILURE
    )

    changed_scope = replace(valid.scope, client_id="client-2")
    with pytest.raises(ScreeningSubmissionCommandError) as linkage_error:
        BoundScreeningSubmission(
            _intake=intake(),
            _scope=changed_scope,
            _authorization_target=screening_authorization_target(changed_scope),
            _authorization_event_id="authz-opaque-1",
        )
    assert (
        linkage_error.value.code
        is ScreeningSubmissionCommandErrorCode.INTEGRITY_FAILURE
    )


def test_atomic_uow_applies_reads_resolves_and_returns_only_defensive_copies() -> None:
    repository = InMemoryScreeningSubmissionUnitOfWork()
    port: ScreeningSubmissionUnitOfWork = repository
    request = atomic_write()
    written = port.submit_atomic(request)

    assert written.outcome is ScreeningAttemptOutcome.APPLIED
    assert written.audit.result == request.proposed_result
    assert written.receipt == ScreeningAcceptanceReceipt.from_result(
        request.proposed_result
    )
    assert written.reverify() == written
    assert port.read_result(request.bound.scope) == request.proposed_result
    assert (
        port.resolve_attempt(
            request.bound.scope,
            request.proposed_result.canonical_hash,
            request.attempt_id,
        )
        == written
    )
    assert repository.stored_counts_for_test() == (1, 1, 1, 1, 1)

    returned = port.read_result(request.bound.scope)
    assert returned is not None
    object.__setattr__(returned.intake, "byte_hash", HASH_B)
    object.__setattr__(written.audit, "actor_subject", "mutated-actor")
    assert port.read_result(request.bound.scope) == request.proposed_result
    resolved = port.resolve_attempt(
        request.bound.scope,
        request.proposed_result.canonical_hash,
        request.attempt_id,
    )
    assert resolved is not None
    assert resolved.audit.actor_subject == "actor-1"

    missing_scope = screening_idempotency_scope(intake(), "another-private-key")
    before = repository.stored_counts_for_test()
    assert port.read_result(missing_scope) is None
    assert port.resolve_attempt(missing_scope, HASH_A, "missing-attempt") is None
    assert port.resolve_attempt(request.bound.scope, HASH_B, request.attempt_id) is None
    assert (
        port.resolve_attempt(
            request.bound.scope,
            request.proposed_result.canonical_hash,
            "missing-attempt",
        )
        is None
    )
    assert repository.stored_counts_for_test() == before

    assert repr(request) == "ScreeningSubmissionAtomicWrite()"
    rendered = repr(written)
    for private in (
        PRIVATE_KEY,
        request.bound.scope.key_digest,
        request.proposed_result.intake.byte_hash,
        request.proposed_result.canonical_hash,
        "actor-1",
        "correlation-1",
        BUSINESS_SENTINEL,
    ):
        assert private not in repr(request)
        assert private not in rendered


@pytest.mark.parametrize(
    "mutation",
    [
        "bound_type",
        "result_type",
        "attempt_format",
        "attempt_subclass",
        "time_offset",
        "time_mismatch",
        "semantic_result",
        "representation_result",
    ],
)
def test_atomic_write_reconstructs_the_exact_bound_graph_before_persistence(
    mutation: str,
) -> None:
    bound = command()
    base = atomic_write(bound)
    values: dict[str, object] = {
        "bound": bound,
        "proposed_result": base.proposed_result,
        "attempt_id": base.attempt_id,
        "occurred_at": base.occurred_at,
    }
    if mutation == "bound_type":
        values["bound"] = object()
    elif mutation == "result_type":
        values["proposed_result"] = object()
    elif mutation == "attempt_format":
        values["attempt_id"] = "bad attempt"
    elif mutation == "attempt_subclass":
        values["attempt_id"] = StringSubclass("attempt-applied")
    elif mutation == "time_offset":
        values["occurred_at"] = datetime(
            2026, 8, 7, 18, 0, 1, tzinfo=timezone(timedelta(hours=8))
        )
    elif mutation == "time_mismatch":
        values["occurred_at"] = COMMITTED_AT + timedelta(seconds=1)
    elif mutation == "semantic_result":
        values["proposed_result"] = graph(command(intake(action="QUOTE_RELEASE")))[3]
    else:
        values["proposed_result"] = graph(command(intake(indent=2)))[3]

    with pytest.raises(ScreeningSubmissionPersistenceFailure) as exc_info:
        ScreeningSubmissionAtomicWrite(**values)  # type: ignore[arg-type]
    rendered = "".join(traceback.format_exception(exc_info.value))
    assert str(exc_info.value) == "screening submission persistence failed"
    for private in (PRIVATE_KEY, HASH_A, HASH_B, BUSINESS_SENTINEL, "attempt-applied"):
        assert private not in str(exc_info.value)
        assert private not in repr(exc_info.value)
        assert private not in rendered

    corrupted = copy.deepcopy(base)
    object.__setattr__(corrupted.bound._scope, "operation", "OTHER_OPERATION")
    with pytest.raises(ScreeningSubmissionPersistenceFailure):
        corrupted.reverify()


def test_write_result_requires_exact_disposition_receipt_and_audit_linkage() -> None:
    repository = InMemoryScreeningSubmissionUnitOfWork()
    request = atomic_write()
    written = repository.submit_atomic(request)
    other_receipt = ScreeningAcceptanceReceipt.from_result(
        graph(
            command(),
            intake_id="intake-other",
            screening_id="screening-other",
            outbox_id="outbox-other",
        )[3]
    )
    invalid_inputs: tuple[dict[str, object], ...] = (
        {"outcome": "APPLIED"},
        {"outcome": ScreeningAttemptOutcome.REPLAY},
        {"receipt": object()},
        {"receipt": other_receipt},
        {"audit": object()},
    )
    for updates in invalid_inputs:
        with pytest.raises(ScreeningSubmissionPersistenceFailure):
            replace(written, **updates)  # type: ignore[arg-type]

    corrupted = copy.deepcopy(written)
    object.__setattr__(corrupted.audit, "canonical_hash", HASH_B)
    with pytest.raises(ScreeningSubmissionPersistenceFailure):
        corrupted.reverify()

    for failure_type, expected in (
        (
            ScreeningSubmissionPersistenceFailure,
            "screening submission persistence failed",
        ),
        (
            ScreeningSubmissionCommitOutcomeUnknown,
            "screening submission commit outcome unknown",
        ),
    ):
        failure = failure_type()
        assert str(failure) == expected
        assert repr(failure) == f"{failure_type.__name__}('{expected}')"


def test_replay_and_conflict_discard_proposals_but_keep_current_attempt_evidence() -> (
    None
):
    repository = InMemoryScreeningSubmissionUnitOfWork()
    applied_request = atomic_write()
    applied = repository.submit_atomic(applied_request)

    retry_intake = intake(subject="actor-2", correlation_id="correlation-2", indent=2)
    retry_request = atomic_write(
        command(retry_intake),
        attempt_id="attempt-replay",
        occurred_at=COMMITTED_AT + timedelta(seconds=1),
        intake_id="discarded-intake-replay",
        screening_id="discarded-screening-replay",
        outbox_id="discarded-outbox-replay",
    )
    replay = repository.submit_atomic(retry_request)
    assert replay.outcome is ScreeningAttemptOutcome.REPLAY
    assert replay.receipt == applied.receipt
    assert replay.audit.actor_subject == "actor-2"
    assert replay.audit.correlation_id == "correlation-2"
    assert replay.audit.byte_hash == retry_intake.byte_hash
    assert replay.audit.result == applied.audit.result

    changed_intake = intake(
        subject="actor-3",
        correlation_id="correlation-3",
        action="QUOTE_RELEASE",
    )
    conflict_request = atomic_write(
        command(changed_intake),
        attempt_id="attempt-conflict",
        occurred_at=COMMITTED_AT + timedelta(seconds=2),
        intake_id="discarded-intake-conflict",
        screening_id="discarded-screening-conflict",
        outbox_id="discarded-outbox-conflict",
    )
    conflict = repository.submit_atomic(conflict_request)
    assert conflict.outcome is ScreeningAttemptOutcome.CONFLICT
    assert conflict.receipt == applied.receipt
    assert conflict.audit.actor_subject == "actor-3"
    assert conflict.audit.correlation_id == "correlation-3"
    assert conflict.audit.byte_hash == changed_intake.byte_hash
    assert conflict.audit.canonical_hash == changed_intake.canonical_hash
    assert conflict.audit.result == applied.audit.result
    assert repository.stored_counts_for_test() == (1, 1, 1, 3, 1)
    assert repository.read_result(applied_request.bound.scope) == (
        applied_request.proposed_result
    )
    assert "discarded-intake-replay" not in repository._intakes
    assert "discarded-screening-replay" not in repository._screenings
    assert "discarded-outbox-replay" not in repository._outbox
    assert "discarded-intake-conflict" not in repository._intakes
    assert (
        repository.resolve_attempt(
            retry_request.bound.scope,
            retry_request.proposed_result.canonical_hash,
            retry_request.attempt_id,
        )
        == replay
    )
    assert (
        repository.resolve_attempt(
            conflict_request.bound.scope,
            conflict_request.proposed_result.canonical_hash,
            conflict_request.attempt_id,
        )
        == conflict
    )


def test_exact_repeated_attempt_returns_stored_outcome_without_a_second_audit() -> None:
    repository = InMemoryScreeningSubmissionUnitOfWork()
    applied_request = atomic_write()
    applied = repository.submit_atomic(applied_request)
    assert repository.submit_atomic(applied_request.reverify()) == applied
    assert repository.stored_counts_for_test() == (1, 1, 1, 1, 1)

    changed_applied_proposal = atomic_write(
        applied_request.bound,
        attempt_id=applied_request.attempt_id,
        intake_id="changed-intake",
        screening_id="changed-screening",
        outbox_id="changed-outbox",
    )
    with pytest.raises(ScreeningSubmissionPersistenceFailure):
        repository.submit_atomic(changed_applied_proposal)

    retry_bound = command(
        intake(subject="actor-2", correlation_id="correlation-2", indent=2)
    )
    replay_request = atomic_write(
        retry_bound,
        attempt_id="attempt-replay",
        occurred_at=COMMITTED_AT + timedelta(seconds=1),
        intake_id="discarded-intake",
        screening_id="discarded-screening",
        outbox_id="discarded-outbox",
    )
    replay = repository.submit_atomic(replay_request)
    assert repository.submit_atomic(replay_request.reverify()) == replay

    conflict_request = atomic_write(
        command(intake(action="QUOTE_RELEASE")),
        attempt_id="attempt-conflict",
        occurred_at=COMMITTED_AT + timedelta(seconds=2),
        intake_id="conflict-intake",
        screening_id="conflict-screening",
        outbox_id="conflict-outbox",
    )
    conflict = repository.submit_atomic(conflict_request)
    assert repository.submit_atomic(conflict_request.reverify()) == conflict
    assert repository.stored_counts_for_test() == (1, 1, 1, 3, 1)

    changed_attempt = atomic_write(
        command(intake(subject="actor-3", correlation_id="correlation-3", indent=2)),
        attempt_id=replay_request.attempt_id,
        occurred_at=replay_request.occurred_at,
        intake_id="other-intake",
        screening_id="other-screening",
        outbox_id="other-outbox",
    )
    before = repository.stored_counts_for_test()
    with pytest.raises(ScreeningSubmissionPersistenceFailure):
        repository.submit_atomic(changed_attempt)
    assert repository.stored_counts_for_test() == before


@pytest.mark.parametrize(
    ("first_field", "second_field"),
    [
        ("intake_id", "screening_id"),
        ("intake_id", "outbox_id"),
        ("intake_id", "attempt_id"),
        ("screening_id", "outbox_id"),
        ("screening_id", "attempt_id"),
        ("outbox_id", "attempt_id"),
    ],
)
def test_new_graph_requires_pairwise_distinct_global_opaque_ids(
    first_field: str, second_field: str
) -> None:
    values = {
        "intake_id": "pair-intake",
        "screening_id": "pair-screening",
        "outbox_id": "pair-outbox",
        "attempt_id": "pair-attempt",
    }
    values[second_field] = values[first_field]
    request = atomic_write(**values)  # type: ignore[arg-type]
    repository = InMemoryScreeningSubmissionUnitOfWork()
    with pytest.raises(ScreeningSubmissionPersistenceFailure):
        repository.submit_atomic(request)
    assert repository.stored_counts_for_test() == (0, 0, 0, 0, 0)


@pytest.mark.parametrize(
    ("field_name", "colliding_id"),
    [
        ("intake_id", "first-intake"),
        ("intake_id", "first-screening"),
        ("screening_id", "first-outbox"),
        ("outbox_id", "first-attempt"),
        ("attempt_id", "first-intake"),
        ("attempt_id", "first-attempt"),
    ],
)
def test_cross_graph_same_or_cross_type_global_id_collision_is_atomic(
    field_name: str, colliding_id: str
) -> None:
    repository = InMemoryScreeningSubmissionUnitOfWork()
    first = atomic_write(
        attempt_id="first-attempt",
        intake_id="first-intake",
        screening_id="first-screening",
        outbox_id="first-outbox",
    )
    repository.submit_atomic(first)
    values = {
        "attempt_id": "second-attempt",
        "intake_id": "second-intake",
        "screening_id": "second-screening",
        "outbox_id": "second-outbox",
    }
    values[field_name] = colliding_id
    second = atomic_write(
        command(intake(), "different-scope-key"),
        **values,  # type: ignore[arg-type]
    )
    before = (
        repository._intakes,
        repository._screenings,
        repository._results,
        repository._attempts,
        repository._outbox,
    )
    with pytest.raises(ScreeningSubmissionPersistenceFailure):
        repository.submit_atomic(second)
    after = (
        repository._intakes,
        repository._screenings,
        repository._results,
        repository._attempts,
        repository._outbox,
    )
    assert after == before


def test_existing_ledger_discards_business_ids_but_attempt_id_is_global() -> None:
    repository = InMemoryScreeningSubmissionUnitOfWork()
    first = atomic_write(
        attempt_id="first-attempt",
        intake_id="first-intake",
        screening_id="first-screening",
        outbox_id="first-outbox",
    )
    repository.submit_atomic(first)
    retry = atomic_write(
        command(intake(indent=2)),
        attempt_id="retry-attempt",
        occurred_at=COMMITTED_AT + timedelta(seconds=1),
        intake_id="first-screening",
        screening_id="discarded-screening",
        outbox_id="discarded-outbox",
    )
    assert repository.submit_atomic(retry).outcome is ScreeningAttemptOutcome.REPLAY

    colliding_attempt = atomic_write(
        command(intake(indent=2)),
        attempt_id="first-intake",
        occurred_at=COMMITTED_AT + timedelta(seconds=2),
        intake_id="ignored-intake",
        screening_id="ignored-screening",
        outbox_id="ignored-outbox",
    )
    before = repository.stored_counts_for_test()
    with pytest.raises(ScreeningSubmissionPersistenceFailure):
        repository.submit_atomic(colliding_attempt)
    assert repository.stored_counts_for_test() == before


@pytest.mark.parametrize(
    "fault_point",
    [
        ScreeningSubmissionFaultPoint.INTAKE,
        ScreeningSubmissionFaultPoint.SCREENING,
        ScreeningSubmissionFaultPoint.IDEMPOTENCY,
        ScreeningSubmissionFaultPoint.AUDIT,
        ScreeningSubmissionFaultPoint.OUTBOX,
    ],
)
def test_each_known_staging_fault_is_one_shot_and_leaves_every_store_unchanged(
    fault_point: ScreeningSubmissionFaultPoint,
) -> None:
    repository = InMemoryScreeningSubmissionUnitOfWork()
    request = atomic_write()
    before = (
        repository._intakes,
        repository._screenings,
        repository._results,
        repository._attempts,
        repository._outbox,
    )
    repository.inject_fault_once(fault_point)
    with pytest.raises(ScreeningSubmissionPersistenceFailure) as exc_info:
        repository.submit_atomic(request)
    after = (
        repository._intakes,
        repository._screenings,
        repository._results,
        repository._attempts,
        repository._outbox,
    )
    assert after == before
    assert repository.stored_counts_for_test() == (0, 0, 0, 0, 0)
    assert (
        repository.resolve_attempt(
            request.bound.scope,
            request.proposed_result.canonical_hash,
            request.attempt_id,
        )
        is None
    )
    rendered = "".join(traceback.format_exception(exc_info.value))
    for private in (
        PRIVATE_KEY,
        request.bound.scope.key_digest,
        request.proposed_result.canonical_hash,
        request.attempt_id,
        BUSINESS_SENTINEL,
    ):
        assert private not in str(exc_info.value)
        assert private not in repr(exc_info.value)
        assert private not in rendered

    assert repository.submit_atomic(request).outcome is ScreeningAttemptOutcome.APPLIED


@pytest.mark.parametrize(
    ("fault_point", "committed"),
    [
        (ScreeningSubmissionFaultPoint.UNKNOWN_BEFORE_SWAP, False),
        (ScreeningSubmissionFaultPoint.UNKNOWN_AFTER_SWAP, True),
    ],
)
def test_unknown_outcome_points_are_exactly_resolvable_without_retry_or_write(
    fault_point: ScreeningSubmissionFaultPoint, committed: bool
) -> None:
    repository = InMemoryScreeningSubmissionUnitOfWork()
    request = atomic_write()
    repository.inject_fault_once(fault_point)
    with pytest.raises(ScreeningSubmissionCommitOutcomeUnknown) as exc_info:
        repository.submit_atomic(request)
    rendered = "".join(traceback.format_exception(exc_info.value))
    for private in (
        PRIVATE_KEY,
        request.bound.scope.key_digest,
        request.proposed_result.canonical_hash,
        request.attempt_id,
        BUSINESS_SENTINEL,
    ):
        assert private not in str(exc_info.value)
        assert private not in repr(exc_info.value)
        assert private not in rendered

    before_resolution = repository.stored_counts_for_test()
    resolution = repository.resolve_attempt(
        request.bound.scope,
        request.proposed_result.canonical_hash,
        request.attempt_id,
    )
    assert repository.stored_counts_for_test() == before_resolution
    if committed:
        assert before_resolution == (1, 1, 1, 1, 1)
        assert resolution is not None
        assert resolution.outcome is ScreeningAttemptOutcome.APPLIED
        assert repository.submit_atomic(request) == resolution
        assert repository.stored_counts_for_test() == before_resolution
    else:
        assert before_resolution == (0, 0, 0, 0, 0)
        assert resolution is None
        assert repository.submit_atomic(request).outcome is (
            ScreeningAttemptOutcome.APPLIED
        )


@pytest.mark.parametrize(
    ("changed", "fault_point", "expected_outcome"),
    [
        (
            False,
            ScreeningSubmissionFaultPoint.UNKNOWN_BEFORE_SWAP,
            ScreeningAttemptOutcome.REPLAY,
        ),
        (
            False,
            ScreeningSubmissionFaultPoint.UNKNOWN_AFTER_SWAP,
            ScreeningAttemptOutcome.REPLAY,
        ),
        (
            True,
            ScreeningSubmissionFaultPoint.UNKNOWN_BEFORE_SWAP,
            ScreeningAttemptOutcome.CONFLICT,
        ),
        (
            True,
            ScreeningSubmissionFaultPoint.UNKNOWN_AFTER_SWAP,
            ScreeningAttemptOutcome.CONFLICT,
        ),
    ],
)
def test_unknown_existing_attempt_is_absent_before_swap_and_exact_after_swap(
    changed: bool,
    fault_point: ScreeningSubmissionFaultPoint,
    expected_outcome: ScreeningAttemptOutcome,
) -> None:
    repository = InMemoryScreeningSubmissionUnitOfWork()
    repository.submit_atomic(atomic_write())
    current_item = intake(action="QUOTE_RELEASE") if changed else intake(indent=2)
    current = atomic_write(
        command(current_item),
        attempt_id="unknown-existing-attempt",
        occurred_at=COMMITTED_AT + timedelta(seconds=1),
        intake_id="unknown-discarded-intake",
        screening_id="unknown-discarded-screening",
        outbox_id="unknown-discarded-outbox",
    )
    repository.inject_fault_once(fault_point)
    with pytest.raises(ScreeningSubmissionCommitOutcomeUnknown):
        repository.submit_atomic(current)
    resolution = repository.resolve_attempt(
        current.bound.scope,
        current.proposed_result.canonical_hash,
        current.attempt_id,
    )
    if fault_point is ScreeningSubmissionFaultPoint.UNKNOWN_BEFORE_SWAP:
        assert resolution is None
        assert repository.stored_counts_for_test() == (1, 1, 1, 1, 1)
    else:
        assert resolution is not None
        assert resolution.outcome is expected_outcome
        assert repository.stored_counts_for_test() == (1, 1, 1, 2, 1)


def test_fault_and_corruption_hooks_are_exact_and_never_return_live_aliases() -> None:
    repository = InMemoryScreeningSubmissionUnitOfWork()
    with pytest.raises(ScreeningSubmissionPersistenceFailure):
        repository.inject_fault_once(
            cast(ScreeningSubmissionFaultPoint, "UNKNOWN_FAULT")
        )
    with pytest.raises(ScreeningSubmissionPersistenceFailure):
        repository.corrupt_remove_for_test(
            cast(ScreeningSubmissionStore, "UNKNOWN_STORE"), "identifier"
        )

    request = atomic_write()
    repository.submit_atomic(request)
    replacement = request.proposed_result.outbox.reverify()
    object.__setattr__(replacement, "screening_id", "corrupted-screening")
    repository.corrupt_replace_for_test(
        ScreeningSubmissionStore.OUTBOX,
        request.proposed_result.outbox.event_id,
        replacement,
    )
    object.__setattr__(replacement, "screening_id", "screening-opaque-1")
    with pytest.raises(ScreeningSubmissionPersistenceFailure):
        repository.read_result(request.bound.scope)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_intake",
        "missing_screening",
        "missing_result",
        "missing_audit",
        "missing_outbox",
        "private_intake_tamper",
        "screening_link",
        "result_map_key",
        "result_value_type",
        "result_link",
        "audit_map_key",
        "audit_value_type",
        "audit_link",
        "duplicate_applied",
        "outbox_link",
        "cross_map_id",
        "map_subclass",
    ],
)
def test_all_stores_links_maps_and_original_applied_audit_fail_closed_without_repair(
    mutation: str,
) -> None:
    repository = InMemoryScreeningSubmissionUnitOfWork()
    request = atomic_write()
    written = repository.submit_atomic(request)
    result = written.audit.result
    ledger_key = result.scope.fingerprint()
    stored_envelope = repository._results[ledger_key]
    if mutation == "missing_intake":
        repository.corrupt_remove_for_test(
            ScreeningSubmissionStore.INTAKE, result.intake.intake_id
        )
    elif mutation == "missing_screening":
        repository.corrupt_remove_for_test(
            ScreeningSubmissionStore.SCREENING, result.screening.screening_id
        )
    elif mutation == "missing_result":
        repository.corrupt_remove_for_test(
            ScreeningSubmissionStore.IDEMPOTENCY, ledger_key
        )
    elif mutation == "missing_audit":
        repository.corrupt_remove_for_test(
            ScreeningSubmissionStore.AUDIT, written.audit.attempt_id
        )
    elif mutation == "missing_outbox":
        repository.corrupt_remove_for_test(
            ScreeningSubmissionStore.OUTBOX, result.outbox.event_id
        )
    elif mutation == "private_intake_tamper":
        private_intake = request.bound._verified_intake_for_persistence()
        object.__setattr__(private_intake, "_raw_bytes", b"{}")
        repository.corrupt_replace_for_test(
            ScreeningSubmissionStore.INTAKE,
            result.intake.intake_id,
            private_intake,
        )
    elif mutation == "screening_link":
        repository.corrupt_replace_for_test(
            ScreeningSubmissionStore.SCREENING,
            result.screening.screening_id,
            replace(result.screening, screening_id="screening-forged"),
        )
    elif mutation == "result_map_key":
        repository.corrupt_replace_for_test(
            ScreeningSubmissionStore.IDEMPOTENCY, HASH_B, stored_envelope
        )
    elif mutation == "result_value_type":
        repository.corrupt_replace_for_test(
            ScreeningSubmissionStore.IDEMPOTENCY, ledger_key, object()
        )
    elif mutation == "result_link":
        corrupted_envelope = copy.deepcopy(stored_envelope)
        object.__setattr__(corrupted_envelope.result, "canonical_hash", HASH_B)
        repository.corrupt_replace_for_test(
            ScreeningSubmissionStore.IDEMPOTENCY, ledger_key, corrupted_envelope
        )
    elif mutation == "audit_map_key":
        repository.corrupt_replace_for_test(
            ScreeningSubmissionStore.AUDIT, "attempt-forged-key", written.audit
        )
    elif mutation == "audit_value_type":
        repository.corrupt_replace_for_test(
            ScreeningSubmissionStore.AUDIT, written.audit.attempt_id, object()
        )
    elif mutation == "audit_link":
        corrupted_audit = copy.deepcopy(written.audit)
        object.__setattr__(corrupted_audit, "actor_subject", "actor-forged")
        repository.corrupt_replace_for_test(
            ScreeningSubmissionStore.AUDIT,
            written.audit.attempt_id,
            corrupted_audit,
        )
    elif mutation == "duplicate_applied":
        repository.corrupt_replace_for_test(
            ScreeningSubmissionStore.AUDIT,
            "attempt-duplicate-applied",
            replace(written.audit, attempt_id="attempt-duplicate-applied"),
        )
    elif mutation == "outbox_link":
        repository.corrupt_replace_for_test(
            ScreeningSubmissionStore.OUTBOX,
            result.outbox.event_id,
            replace(result.outbox, screening_id="screening-forged"),
        )
    elif mutation == "cross_map_id":
        repository.corrupt_replace_for_test(
            ScreeningSubmissionStore.OUTBOX,
            result.intake.intake_id,
            replace(result.outbox, event_id=result.intake.intake_id),
        )
    else:
        object.__setattr__(repository, "_intakes", type("Map", (dict,), {})())

    before = (
        repository._intakes,
        repository._screenings,
        repository._results,
        repository._attempts,
        repository._outbox,
    )
    for operation in (
        lambda: repository.read_result(request.bound.scope),
        lambda: repository.resolve_attempt(
            request.bound.scope,
            request.proposed_result.canonical_hash,
            request.attempt_id,
        ),
        lambda: repository.submit_atomic(
            atomic_write(
                request.bound,
                attempt_id="attempt-after-corruption",
                occurred_at=COMMITTED_AT + timedelta(seconds=1),
                intake_id="ignored-after-corruption",
                screening_id="ignored-screening-after-corruption",
                outbox_id="ignored-outbox-after-corruption",
            )
        ),
    ):
        with pytest.raises(ScreeningSubmissionPersistenceFailure):
            operation()
        after = (
            repository._intakes,
            repository._screenings,
            repository._results,
            repository._attempts,
            repository._outbox,
        )
        assert all(
            current is original for current, original in zip(after, before, strict=True)
        )


@pytest.mark.parametrize("changed", [False, True])
def test_same_scope_thread_race_converges_to_one_graph_and_two_exact_audits(
    changed: bool,
) -> None:
    repository = InMemoryScreeningSubmissionUnitOfWork()
    first = atomic_write(
        attempt_id="race-attempt-one",
        intake_id="race-intake-one",
        screening_id="race-screening-one",
        outbox_id="race-outbox-one",
    )
    second_item = (
        intake(action="QUOTE_RELEASE")
        if changed
        else intake(subject="actor-2", correlation_id="correlation-2", indent=2)
    )
    second = atomic_write(
        command(second_item),
        attempt_id="race-attempt-two",
        occurred_at=COMMITTED_AT + timedelta(seconds=1),
        intake_id="race-intake-two",
        screening_id="race-screening-two",
        outbox_id="race-outbox-two",
    )
    barrier = Barrier(2)

    def submit(
        request: ScreeningSubmissionAtomicWrite,
    ) -> ScreeningSubmissionWriteResult:
        barrier.wait()
        return repository.submit_atomic(request)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(submit, (first, second)))

    assert (
        sum(outcome.outcome is ScreeningAttemptOutcome.APPLIED for outcome in outcomes)
        == 1
    )
    loser = (
        ScreeningAttemptOutcome.CONFLICT if changed else ScreeningAttemptOutcome.REPLAY
    )
    assert sum(outcome.outcome is loser for outcome in outcomes) == 1
    assert outcomes[0].receipt == outcomes[1].receipt
    assert repository.stored_counts_for_test() == (1, 1, 1, 2, 1)
    stored = repository.read_result(first.bound.scope)
    assert stored in (first.proposed_result, second.proposed_result)


def test_different_tenant_client_and_key_scopes_remain_independent_under_race() -> None:
    repository = InMemoryScreeningSubmissionUnitOfWork()
    requests = (
        atomic_write(
            attempt_id="scope-attempt-one",
            intake_id="scope-intake-one",
            screening_id="scope-screening-one",
            outbox_id="scope-outbox-one",
        ),
        atomic_write(
            command(intake(), "different-key"),
            attempt_id="scope-attempt-two",
            intake_id="scope-intake-two",
            screening_id="scope-screening-two",
            outbox_id="scope-outbox-two",
        ),
        atomic_write(
            command(intake(tenant_id="tenant-2")),
            attempt_id="scope-attempt-three",
            intake_id="scope-intake-three",
            screening_id="scope-screening-three",
            outbox_id="scope-outbox-three",
        ),
        atomic_write(
            command(intake(client_id="client-2")),
            attempt_id="scope-attempt-four",
            intake_id="scope-intake-four",
            screening_id="scope-screening-four",
            outbox_id="scope-outbox-four",
        ),
    )
    barrier = Barrier(len(requests))

    def submit(
        request: ScreeningSubmissionAtomicWrite,
    ) -> ScreeningSubmissionWriteResult:
        barrier.wait()
        return repository.submit_atomic(request)

    with ThreadPoolExecutor(max_workers=len(requests)) as executor:
        outcomes = tuple(executor.map(submit, requests))

    assert all(
        outcome.outcome is ScreeningAttemptOutcome.APPLIED for outcome in outcomes
    )
    assert len({request.bound.scope.fingerprint() for request in requests}) == 4
    assert len({outcome.receipt for outcome in outcomes}) == 4
    assert repository.stored_counts_for_test() == (4, 4, 4, 4, 4)
    assert all(
        repository.read_result(request.bound.scope) == request.proposed_result
        for request in requests
    )


@pytest.mark.parametrize(
    ("fault_point", "failure_type", "stored_graphs"),
    [
        (
            ScreeningSubmissionFaultPoint.INTAKE,
            ScreeningSubmissionPersistenceFailure,
            1,
        ),
        (
            ScreeningSubmissionFaultPoint.UNKNOWN_BEFORE_SWAP,
            ScreeningSubmissionCommitOutcomeUnknown,
            1,
        ),
        (
            ScreeningSubmissionFaultPoint.UNKNOWN_AFTER_SWAP,
            ScreeningSubmissionCommitOutcomeUnknown,
            2,
        ),
    ],
)
def test_one_shot_fault_is_consumed_exactly_once_under_thread_contention(
    fault_point: ScreeningSubmissionFaultPoint,
    failure_type: type[Exception],
    stored_graphs: int,
) -> None:
    repository = InMemoryScreeningSubmissionUnitOfWork()
    requests = (
        atomic_write(
            attempt_id="fault-race-attempt-one",
            intake_id="fault-race-intake-one",
            screening_id="fault-race-screening-one",
            outbox_id="fault-race-outbox-one",
        ),
        atomic_write(
            command(intake(), "fault-race-key-two"),
            attempt_id="fault-race-attempt-two",
            intake_id="fault-race-intake-two",
            screening_id="fault-race-screening-two",
            outbox_id="fault-race-outbox-two",
        ),
    )
    barrier = Barrier(2)
    repository.inject_fault_once(fault_point)

    def submit(
        request: ScreeningSubmissionAtomicWrite,
    ) -> ScreeningSubmissionWriteResult | Exception:
        barrier.wait()
        try:
            return repository.submit_atomic(request)
        except (
            ScreeningSubmissionPersistenceFailure,
            ScreeningSubmissionCommitOutcomeUnknown,
        ) as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(submit, requests))

    assert sum(type(outcome) is failure_type for outcome in outcomes) == 1
    assert (
        sum(type(outcome) is ScreeningSubmissionWriteResult for outcome in outcomes)
        == 1
    )
    assert repository.stored_counts_for_test() == (
        stored_graphs,
        stored_graphs,
        stored_graphs,
        stored_graphs,
        stored_graphs,
    )


@pytest.mark.parametrize(
    ("changed", "loser_outcome"),
    [
        (False, ScreeningAttemptOutcome.REPLAY),
        (True, ScreeningAttemptOutcome.CONFLICT),
    ],
)
def test_threaded_lock_order_inversion_preserves_earlier_attempt_start_time(
    changed: bool, loser_outcome: ScreeningAttemptOutcome
) -> None:
    repository = InMemoryScreeningSubmissionUnitOfWork()
    winner_time = COMMITTED_AT + timedelta(seconds=20)
    loser_time = COMMITTED_AT
    winner = atomic_write(
        attempt_id="inversion-winner-attempt",
        occurred_at=winner_time,
        intake_id="inversion-winner-intake",
        screening_id="inversion-winner-screening",
        outbox_id="inversion-winner-outbox",
    )
    loser_item = intake(action="QUOTE_RELEASE") if changed else intake(indent=2)
    loser = atomic_write(
        command(loser_item),
        attempt_id="inversion-loser-attempt",
        occurred_at=loser_time,
        intake_id="inversion-loser-intake",
        screening_id="inversion-loser-screening",
        outbox_id="inversion-loser-outbox",
    )
    winner_committed = Event()

    def submit_winner() -> ScreeningSubmissionWriteResult:
        result = repository.submit_atomic(winner)
        winner_committed.set()
        return result

    def submit_loser() -> ScreeningSubmissionWriteResult:
        assert winner_committed.wait(timeout=5)
        return repository.submit_atomic(loser)

    with ThreadPoolExecutor(max_workers=2) as executor:
        winner_future = executor.submit(submit_winner)
        loser_future = executor.submit(submit_loser)
        winner_result = winner_future.result(timeout=5)
        loser_result = loser_future.result(timeout=5)

    assert winner_result.outcome is ScreeningAttemptOutcome.APPLIED
    assert loser_result.outcome is loser_outcome
    assert loser_result.audit.occurred_at == loser_time
    assert loser_result.audit.occurred_at < loser_result.audit.result.committed_at
    assert loser_result.audit.result.committed_at == winner_time
    assert repository.stored_counts_for_test() == (1, 1, 1, 2, 1)


def test_uow_boundary_rejects_untyped_write_read_and_resolution_inputs() -> None:
    repository = InMemoryScreeningSubmissionUnitOfWork()
    request = atomic_write()
    with pytest.raises(ScreeningSubmissionPersistenceFailure):
        repository.submit_atomic(cast(ScreeningSubmissionAtomicWrite, object()))
    with pytest.raises(ScreeningSubmissionPersistenceFailure):
        repository.read_result(cast(ScreeningIdempotencyScope, object()))

    invalid_resolution_inputs: tuple[tuple[object, object], ...] = (
        (object(), "attempt-applied"),
        ("not-a-hash", "attempt-applied"),
        (StringSubclass(request.proposed_result.canonical_hash), "attempt-applied"),
        (request.proposed_result.canonical_hash, object()),
        (request.proposed_result.canonical_hash, "bad attempt"),
        (
            request.proposed_result.canonical_hash,
            StringSubclass("attempt-applied"),
        ),
    )
    for canonical_hash, attempt_id in invalid_resolution_inputs:
        with pytest.raises(ScreeningSubmissionPersistenceFailure):
            repository.resolve_attempt(
                request.bound.scope,
                cast(str, canonical_hash),
                cast(str, attempt_id),
            )
    assert repository.stored_counts_for_test() == (0, 0, 0, 0, 0)


def test_atomic_request_and_repository_storage_do_not_retain_caller_aliases() -> None:
    original_bound = command()
    _, _, _, original_proposed = graph(original_bound)
    request = ScreeningSubmissionAtomicWrite(
        bound=original_bound,
        proposed_result=original_proposed,
        attempt_id="alias-attempt",
        occurred_at=COMMITTED_AT,
    )
    object.__setattr__(original_bound._scope, "client_id", "mutated-client")
    object.__setattr__(original_proposed.intake, "byte_hash", HASH_B)
    repository = InMemoryScreeningSubmissionUnitOfWork()
    written = repository.submit_atomic(request)
    assert written.outcome is ScreeningAttemptOutcome.APPLIED

    object.__setattr__(request.bound._scope, "client_id", "mutated-request-client")
    object.__setattr__(request.proposed_result.intake, "byte_hash", HASH_B)
    stored = repository.read_result(written.audit.scope)
    assert stored == written.audit.result
    assert stored is not request.proposed_result


@pytest.mark.parametrize(
    ("changed", "expected_outcome"),
    [
        (False, ScreeningAttemptOutcome.REPLAY),
        (True, ScreeningAttemptOutcome.CONFLICT),
    ],
)
def test_missing_non_applied_audit_is_detected_on_every_operation(
    changed: bool, expected_outcome: ScreeningAttemptOutcome
) -> None:
    repository = InMemoryScreeningSubmissionUnitOfWork()
    first = atomic_write()
    repository.submit_atomic(first)
    current_item = intake(action="QUOTE_RELEASE") if changed else intake(indent=2)
    current = atomic_write(
        command(current_item),
        attempt_id=f"attempt-{expected_outcome.value.lower()}-missing",
        occurred_at=COMMITTED_AT + timedelta(seconds=1),
        intake_id="missing-discarded-intake",
        screening_id="missing-discarded-screening",
        outbox_id="missing-discarded-outbox",
    )
    assert repository.submit_atomic(current).outcome is expected_outcome
    repository.corrupt_remove_for_test(
        ScreeningSubmissionStore.AUDIT, current.attempt_id
    )
    assert repository.stored_counts_for_test() == (1, 1, 1, 1, 1)
    before = (
        repository._intakes,
        repository._screenings,
        repository._results,
        repository._attempts,
        repository._outbox,
    )
    operations = (
        lambda: repository.read_result(first.bound.scope),
        lambda: repository.resolve_attempt(
            current.bound.scope,
            current.proposed_result.canonical_hash,
            current.attempt_id,
        ),
        lambda: repository.submit_atomic(
            atomic_write(
                current.bound,
                attempt_id="attempt-after-missing",
                occurred_at=COMMITTED_AT + timedelta(seconds=2),
                intake_id="after-missing-intake",
                screening_id="after-missing-screening",
                outbox_id="after-missing-outbox",
            )
        ),
    )
    for operation in operations:
        with pytest.raises(ScreeningSubmissionPersistenceFailure):
            operation()
        after = (
            repository._intakes,
            repository._screenings,
            repository._results,
            repository._attempts,
            repository._outbox,
        )
        assert all(
            current_map is original
            for current_map, original in zip(after, before, strict=True)
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_index_id",
        "reordered_index",
        "duplicate_index_id",
        "forged_index_id",
        "subclass_index_id",
        "non_tuple_index",
        "empty_index",
        "chain_hash",
        "orphan_audit",
    ],
)
def test_attempt_ledger_index_tamper_and_orphan_audit_fail_closed(
    mutation: str,
) -> None:
    repository = InMemoryScreeningSubmissionUnitOfWork()
    first = atomic_write()
    applied = repository.submit_atomic(first)
    replay_request = atomic_write(
        command(intake(indent=2)),
        attempt_id="attempt-index-replay",
        occurred_at=COMMITTED_AT + timedelta(seconds=1),
        intake_id="index-discarded-intake",
        screening_id="index-discarded-screening",
        outbox_id="index-discarded-outbox",
    )
    replay = repository.submit_atomic(replay_request)
    ledger_key = first.bound.scope.fingerprint()
    envelope = repository._results[ledger_key]

    def chain_hash(attempt_ids: tuple[str, ...]) -> str:
        encoded = json.dumps(
            attempt_ids, ensure_ascii=False, separators=(",", ":")
        ).encode()
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    if mutation == "missing_index_id":
        attempt_ids: object = (applied.audit.attempt_id,)
    elif mutation == "reordered_index":
        attempt_ids = (replay.audit.attempt_id, applied.audit.attempt_id)
    elif mutation == "duplicate_index_id":
        attempt_ids = (
            applied.audit.attempt_id,
            replay.audit.attempt_id,
            replay.audit.attempt_id,
        )
    elif mutation == "forged_index_id":
        attempt_ids = (applied.audit.attempt_id, "attempt-index-forged")
    elif mutation == "subclass_index_id":
        attempt_ids = (
            applied.audit.attempt_id,
            StringSubclass(replay.audit.attempt_id),
        )
    elif mutation == "non_tuple_index":
        attempt_ids = [applied.audit.attempt_id, replay.audit.attempt_id]
    elif mutation == "empty_index":
        attempt_ids = ()
    elif mutation == "chain_hash":
        attempt_ids = envelope.attempt_ids
    else:
        orphan = replace(replay.audit, attempt_id="attempt-index-orphan")
        repository.corrupt_replace_for_test(
            ScreeningSubmissionStore.AUDIT, orphan.attempt_id, orphan
        )
        attempt_ids = envelope.attempt_ids

    if mutation != "orphan_audit":
        corrupted_envelope = copy.deepcopy(envelope)
        object.__setattr__(corrupted_envelope, "attempt_ids", attempt_ids)
        if mutation in {
            "missing_index_id",
            "reordered_index",
            "duplicate_index_id",
            "forged_index_id",
        }:
            object.__setattr__(
                corrupted_envelope,
                "attempt_chain_hash",
                chain_hash(cast(tuple[str, ...], attempt_ids)),
            )
        elif mutation == "chain_hash":
            object.__setattr__(corrupted_envelope, "attempt_chain_hash", HASH_B)
        repository.corrupt_replace_for_test(
            ScreeningSubmissionStore.IDEMPOTENCY,
            ledger_key,
            corrupted_envelope,
        )

    before = repository.stored_counts_for_test()
    with pytest.raises(ScreeningSubmissionPersistenceFailure):
        repository.read_result(first.bound.scope)
    with pytest.raises(ScreeningSubmissionPersistenceFailure):
        repository.resolve_attempt(
            replay_request.bound.scope,
            replay_request.proposed_result.canonical_hash,
            replay_request.attempt_id,
        )
    assert repository.stored_counts_for_test() == before


@pytest.mark.parametrize("changed", [False, True])
@pytest.mark.parametrize(
    "fault_point",
    [
        ScreeningSubmissionFaultPoint.IDEMPOTENCY,
        ScreeningSubmissionFaultPoint.AUDIT,
    ],
)
def test_existing_attempt_ledger_and_audit_staging_faults_are_jointly_atomic(
    changed: bool, fault_point: ScreeningSubmissionFaultPoint
) -> None:
    repository = InMemoryScreeningSubmissionUnitOfWork()
    first = atomic_write()
    repository.submit_atomic(first)
    current_item = intake(action="QUOTE_RELEASE") if changed else intake(indent=2)
    current = atomic_write(
        command(current_item),
        attempt_id="attempt-existing-fault",
        occurred_at=COMMITTED_AT + timedelta(seconds=1),
        intake_id="fault-discarded-intake",
        screening_id="fault-discarded-screening",
        outbox_id="fault-discarded-outbox",
    )
    before = (
        repository._intakes,
        repository._screenings,
        repository._results,
        repository._attempts,
        repository._outbox,
    )
    repository.inject_fault_once(fault_point)
    with pytest.raises(ScreeningSubmissionPersistenceFailure):
        repository.submit_atomic(current)
    after = (
        repository._intakes,
        repository._screenings,
        repository._results,
        repository._attempts,
        repository._outbox,
    )
    assert all(
        current_map is original
        for current_map, original in zip(after, before, strict=True)
    )
    assert repository.stored_counts_for_test() == (1, 1, 1, 1, 1)
    assert (
        repository.resolve_attempt(
            current.bound.scope,
            current.proposed_result.canonical_hash,
            current.attempt_id,
        )
        is None
    )
    expected = (
        ScreeningAttemptOutcome.CONFLICT if changed else ScreeningAttemptOutcome.REPLAY
    )
    assert repository.submit_atomic(current).outcome is expected
    assert repository.stored_counts_for_test() == (1, 1, 1, 2, 1)


def test_service_authorizes_and_returns_minimal_stable_apply_and_replay_results() -> (
    None
):
    repository = InMemoryScreeningSubmissionUnitOfWork()
    service, entitlements, observed, events = screening_service(repository)
    original = intake()
    applied = service.submit(original, PRIVATE_KEY)
    same_bytes = service.submit(original, PRIVATE_KEY)
    alternate = service.submit(
        intake(subject="actor-2", correlation_id="correlation-2", indent=2),
        PRIVATE_KEY,
    )

    assert applied.disposition is ScreeningSubmissionServiceDisposition.APPLIED
    assert same_bytes.disposition is ScreeningSubmissionServiceDisposition.REPLAY
    assert alternate.disposition is ScreeningSubmissionServiceDisposition.REPLAY
    assert applied.receipt == same_bytes.receipt == alternate.receipt
    assert applied.reverify() == applied
    assert {item.name for item in fields(applied)} == {"disposition", "receipt"}
    assert repository.stored_counts_for_test() == (1, 1, 1, 3, 1)
    assert len(observed.submit_calls) == 3
    assert observed.read_calls == []
    assert observed.resolve_calls == []
    assert len(events) == 3
    assert all(event.outcome is AuthorizationOutcome.ALLOW for event in events)
    assert all(event.reason is AuthorizationReason.ALLOWED for event in events)
    assert all(event.occurred_at == COMMITTED_AT for event in events)
    assert all(event.operation == SCREENING_SUBMIT_OPERATION for event in events)
    assert all(
        event.target_ref.startswith("SCREENING_IDEMPOTENCY:idem-") for event in events
    )
    assert len(entitlements.calls) == 3
    assert all(call[2] == SCREENING_SUBMIT_OPERATION for call in entitlements.calls)
    assert all(call[4] == "SCREENING_IDEMPOTENCY" for call in entitlements.calls)
    assert tuple(
        audit.authorization_event_id for audit in repository._attempts.values()
    ) == tuple(event.event_id for event in events)

    rendered = repr(applied)
    for private in (
        PRIVATE_KEY,
        original.byte_hash,
        original.canonical_hash,
        "actor-1",
        "client-1",
        "correlation-1",
        BUSINESS_SENTINEL,
    ):
        assert private not in rendered

    with pytest.raises(ScreeningSubmissionServiceError) as conflict_info:
        service.submit(intake(action="QUOTE_RELEASE"), PRIVATE_KEY)
    assert conflict_info.value.code is (
        ScreeningSubmissionServiceErrorCode.IDEMPOTENCY_CONFLICT
    )
    assert repository.stored_counts_for_test() == (1, 1, 1, 4, 1)
    assert len(events) == 4
    assert tuple(
        audit.authorization_event_id for audit in repository._attempts.values()
    ) == tuple(event.event_id for event in events)


@pytest.mark.parametrize(
    ("failure", "expected_type", "expected_code"),
    [
        ("missing_scope", AuthorizationDenied, PublicDenialCode.FORBIDDEN),
        ("cross_tenant", AuthorizationDenied, PublicDenialCode.NOT_FOUND),
        ("concealed", AuthorizationDenied, PublicDenialCode.NOT_FOUND),
        ("entitlement", AuthorizationUnavailable, None),
        ("audit_sink", AuthorizationAuditFailure, None),
    ],
)
def test_service_real_authorization_negatives_never_reach_the_uow(
    failure: str,
    expected_type: type[Exception],
    expected_code: PublicDenialCode | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = InMemoryScreeningSubmissionUnitOfWork()
    observed = ObservedSubmissionUnitOfWork(repository)
    events: list[AuthorizationAuditEvent] = []
    entitlements = ScreeningEntitlements(
        visible=failure != "concealed",
        fail=failure == "entitlement",
    )
    item = intake(
        scopes=(
            frozenset()
            if failure == "missing_scope"
            else frozenset({Scope.SCREENING_SUBMIT})
        ),
    )
    if failure == "cross_tenant":
        monkeypatch.setattr(
            "tradesieve.application.screening_submission.screening_authorization_target",
            lambda _scope: TargetObject(
                tenant_id="tenant-2",
                object_type="SCREENING_IDEMPOTENCY",
                object_id="idem-cross-tenant",
            ),
        )

    def failed_audit_sink(_event: AuthorizationAuditEvent) -> None:
        raise RuntimeError(BUSINESS_SENTINEL)

    service, _, _, _ = screening_service(
        repository,
        events=events,
        entitlements=entitlements,
        observed=observed,
        audit_sink=failed_audit_sink if failure == "audit_sink" else None,
    )
    with pytest.raises(expected_type) as exc_info:
        service.submit(item, PRIVATE_KEY)
    if expected_code is not None:
        assert isinstance(exc_info.value, AuthorizationDenied)
        assert exc_info.value.code is expected_code
    assert observed.submit_calls == []
    assert observed.read_calls == []
    assert observed.resolve_calls == []
    assert repository.stored_counts_for_test() == (0, 0, 0, 0, 0)
    if failure == "audit_sink":
        assert events == []
    else:
        assert len(events) == 1
        assert events[0].outcome is AuthorizationOutcome.DENY
    rendered = "".join(traceback.format_exception(exc_info.value))
    assert PRIVATE_KEY not in rendered
    assert BUSINESS_SENTINEL not in str(exc_info.value)
    assert BUSINESS_SENTINEL not in repr(exc_info.value)


@pytest.mark.parametrize("mutation", ["target", "context"])
def test_service_rejects_alternate_authorized_target_or_context_before_uow(
    mutation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = InMemoryScreeningSubmissionUnitOfWork()
    observed = ObservedSubmissionUnitOfWork(repository)
    events: list[AuthorizationAuditEvent] = []
    entitlements = ScreeningEntitlements()
    authorization = AuthorizationService(
        events.append,
        entitlements,
        event_id_factory=lambda: "authz-alternate-binding",
    )
    original_require = authorization.require

    def altered_require(
        request: AuthorizationRequest, *, now: datetime
    ) -> AuthorizedRequest:
        authorized_request = original_require(request, now=now)
        if mutation == "target":
            return replace(
                authorized_request,
                target=TargetObject(
                    tenant_id="tenant-1",
                    object_type="SCREENING_IDEMPOTENCY",
                    object_id="idem-alternate",
                ),
            )
        return replace(
            authorized_request,
            context=context(client_id="client-2"),
        )

    monkeypatch.setattr(authorization, "require", altered_require)
    service = ScreeningSubmissionService(
        authorization,
        observed,
        clock=lambda: COMMITTED_AT,
        attempt_id_factory=lambda: "attempt-alternate",
        intake_id_factory=lambda: "intake-alternate",
        screening_id_factory=lambda: "screening-alternate",
        outbox_id_factory=lambda: "outbox-alternate",
    )
    with pytest.raises(ScreeningSubmissionServiceError) as exc_info:
        service.submit(intake(), PRIVATE_KEY)
    assert exc_info.value.code is ScreeningSubmissionServiceErrorCode.UNAVAILABLE
    assert len(events) == 1
    assert events[0].outcome is AuthorizationOutcome.ALLOW
    assert observed.submit_calls == []
    assert observed.read_calls == []
    assert observed.resolve_calls == []


@pytest.mark.parametrize(
    "fault_point",
    [
        ScreeningSubmissionFaultPoint.INTAKE,
        ScreeningSubmissionFaultPoint.SCREENING,
        ScreeningSubmissionFaultPoint.IDEMPOTENCY,
        ScreeningSubmissionFaultPoint.AUDIT,
        ScreeningSubmissionFaultPoint.OUTBOX,
    ],
)
def test_service_known_write_fault_is_unavailable_without_resolution_or_false_audit(
    fault_point: ScreeningSubmissionFaultPoint,
) -> None:
    repository = InMemoryScreeningSubmissionUnitOfWork()
    repository.inject_fault_once(fault_point)
    service, _, observed, events = screening_service(repository)
    with pytest.raises(ScreeningSubmissionServiceError) as exc_info:
        service.submit(intake(), PRIVATE_KEY)
    assert exc_info.value.code is ScreeningSubmissionServiceErrorCode.UNAVAILABLE
    assert len(events) == 1
    assert events[0].outcome is AuthorizationOutcome.ALLOW
    assert len(observed.submit_calls) == 1
    assert observed.resolve_calls == []
    assert observed.read_calls == []
    assert repository.stored_counts_for_test() == (0, 0, 0, 0, 0)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    rendered = "".join(traceback.format_exception(exc_info.value))
    for private in (
        PRIVATE_KEY,
        observed.submit_calls[0].proposed_result.canonical_hash,
        BUSINESS_SENTINEL,
    ):
        assert private not in str(exc_info.value)
        assert private not in repr(exc_info.value)
        assert private not in rendered


@pytest.mark.parametrize(
    ("fault_point", "committed"),
    [
        (ScreeningSubmissionFaultPoint.UNKNOWN_BEFORE_SWAP, False),
        (ScreeningSubmissionFaultPoint.UNKNOWN_AFTER_SWAP, True),
    ],
)
def test_service_unknown_outcome_resolves_once_and_never_reads_or_retries(
    fault_point: ScreeningSubmissionFaultPoint, committed: bool
) -> None:
    repository = InMemoryScreeningSubmissionUnitOfWork()
    repository.inject_fault_once(fault_point)
    service, _, observed, events = screening_service(repository)
    if committed:
        result = service.submit(intake(), PRIVATE_KEY)
        assert result.disposition is ScreeningSubmissionServiceDisposition.APPLIED
        assert repository.stored_counts_for_test() == (1, 1, 1, 1, 1)
        assert next(iter(repository._attempts.values())).authorization_event_id == (
            events[0].event_id
        )
    else:
        with pytest.raises(ScreeningSubmissionServiceError) as exc_info:
            service.submit(intake(), PRIVATE_KEY)
        assert exc_info.value.code is ScreeningSubmissionServiceErrorCode.UNAVAILABLE
        assert repository.stored_counts_for_test() == (0, 0, 0, 0, 0)
    assert len(events) == 1
    assert len(observed.submit_calls) == 1
    assert len(observed.resolve_calls) == 1
    assert observed.read_calls == []


@pytest.mark.parametrize(
    ("changed", "fault_point", "expected"),
    [
        (
            False,
            ScreeningSubmissionFaultPoint.UNKNOWN_BEFORE_SWAP,
            ScreeningSubmissionServiceErrorCode.UNAVAILABLE,
        ),
        (
            False,
            ScreeningSubmissionFaultPoint.UNKNOWN_AFTER_SWAP,
            ScreeningSubmissionServiceDisposition.REPLAY,
        ),
        (
            True,
            ScreeningSubmissionFaultPoint.UNKNOWN_BEFORE_SWAP,
            ScreeningSubmissionServiceErrorCode.UNAVAILABLE,
        ),
        (
            True,
            ScreeningSubmissionFaultPoint.UNKNOWN_AFTER_SWAP,
            ScreeningSubmissionServiceErrorCode.IDEMPOTENCY_CONFLICT,
        ),
    ],
)
def test_service_unknown_existing_replay_or_conflict_requires_current_attempt(
    changed: bool,
    fault_point: ScreeningSubmissionFaultPoint,
    expected: ScreeningSubmissionServiceDisposition
    | ScreeningSubmissionServiceErrorCode,
) -> None:
    repository = InMemoryScreeningSubmissionUnitOfWork()
    service, _, observed, events = screening_service(repository)
    service.submit(intake(), PRIVATE_KEY)
    repository.inject_fault_once(fault_point)
    current = intake(action="QUOTE_RELEASE") if changed else intake(indent=2)
    if isinstance(expected, ScreeningSubmissionServiceDisposition):
        result = service.submit(current, PRIVATE_KEY)
        assert result.disposition is expected
    else:
        with pytest.raises(ScreeningSubmissionServiceError) as exc_info:
            service.submit(current, PRIVATE_KEY)
        assert exc_info.value.code is expected
    assert len(observed.submit_calls) == 2
    assert len(observed.resolve_calls) == 1
    assert observed.read_calls == []
    expected_attempts = (
        2 if fault_point is ScreeningSubmissionFaultPoint.UNKNOWN_AFTER_SWAP else 1
    )
    assert repository.stored_counts_for_test() == (1, 1, 1, expected_attempts, 1)
    assert len(events) == 2
    assert tuple(
        audit.authorization_event_id for audit in repository._attempts.values()
    ) == tuple(event.event_id for event in events[:expected_attempts])


@pytest.mark.parametrize(
    "resolution",
    ["none", "raise", "wrong_type", "wrong_attempt", "corrupt"],
)
def test_service_unknown_resolution_failure_is_unavailable_without_blind_retry(
    resolution: str,
) -> None:
    repository = InMemoryScreeningSubmissionUnitOfWork()
    observed = ObservedSubmissionUnitOfWork(repository)

    def unknown_submit(
        _request: ScreeningSubmissionAtomicWrite,
    ) -> ScreeningSubmissionWriteResult:
        raise ScreeningSubmissionCommitOutcomeUnknown()

    observed.submit_override = unknown_submit
    foreign = InMemoryScreeningSubmissionUnitOfWork().submit_atomic(
        atomic_write(
            attempt_id="foreign-attempt",
            intake_id="foreign-intake",
            screening_id="foreign-screening",
            outbox_id="foreign-outbox",
        )
    )
    if resolution == "none":
        observed.resolve_override = lambda _scope, _hash, _attempt: None
    elif resolution == "raise":

        def failed_resolution(
            _scope: ScreeningIdempotencyScope,
            _canonical_hash: str,
            _attempt_id: str,
        ) -> ScreeningSubmissionWriteResult | None:
            raise RuntimeError(BUSINESS_SENTINEL)

        observed.resolve_override = failed_resolution
    elif resolution == "wrong_type":
        observed.resolve_override = lambda _scope, _hash, _attempt: cast(
            ScreeningSubmissionWriteResult, object()
        )
    elif resolution == "wrong_attempt":
        observed.resolve_override = lambda _scope, _hash, _attempt: foreign
    else:
        corrupted = copy.deepcopy(foreign)
        object.__setattr__(corrupted.audit, "canonical_hash", HASH_B)
        observed.resolve_override = lambda _scope, _hash, _attempt: corrupted

    service, _, _, events = screening_service(
        repository,
        observed=observed,
        attempt_id_factory=lambda: "service-current-attempt",
    )
    with pytest.raises(ScreeningSubmissionServiceError) as exc_info:
        service.submit(intake(), PRIVATE_KEY)
    assert exc_info.value.code is ScreeningSubmissionServiceErrorCode.UNAVAILABLE
    assert len(events) == 1
    assert len(observed.submit_calls) == 1
    assert len(observed.resolve_calls) == 1
    assert observed.read_calls == []
    assert repository.stored_counts_for_test() == (0, 0, 0, 0, 0)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    rendered = "".join(traceback.format_exception(exc_info.value))
    assert BUSINESS_SENTINEL not in rendered
    assert PRIVATE_KEY not in rendered


@pytest.mark.parametrize(
    "failure",
    [
        "clock_raise",
        "clock_naive",
        "clock_offset",
        "attempt_raise",
        "attempt_subclass",
        "intake_bad",
        "screening_type",
        "outbox_raise",
    ],
)
def test_service_clock_and_id_factory_failures_are_finite_and_never_write(
    failure: str,
) -> None:
    repository = InMemoryScreeningSubmissionUnitOfWork()

    def explode() -> str:
        raise RuntimeError(BUSINESS_SENTINEL)

    def clock() -> datetime:
        if failure == "clock_raise":
            raise RuntimeError(BUSINESS_SENTINEL)
        if failure == "clock_naive":
            return COMMITTED_AT.replace(tzinfo=None)
        if failure == "clock_offset":
            return COMMITTED_AT.astimezone(timezone(timedelta(hours=8)))
        return COMMITTED_AT

    service, _, observed, events = screening_service(
        repository,
        clock=clock,
        attempt_id_factory=(
            explode
            if failure == "attempt_raise"
            else (
                lambda: (
                    StringSubclass("attempt-subclass")
                    if failure == "attempt_subclass"
                    else "attempt-valid"
                )
            )
        ),
        intake_id_factory=(
            (lambda: "bad intake")
            if failure == "intake_bad"
            else lambda: "intake-valid"
        ),
        screening_id_factory=(
            (lambda: cast(str, None))
            if failure == "screening_type"
            else lambda: "screening-valid"
        ),
        outbox_id_factory=(
            explode if failure == "outbox_raise" else lambda: "outbox-valid"
        ),
    )
    with pytest.raises(ScreeningSubmissionServiceError) as exc_info:
        service.submit(intake(), PRIVATE_KEY)
    assert exc_info.value.code is ScreeningSubmissionServiceErrorCode.UNAVAILABLE
    assert observed.submit_calls == []
    assert observed.resolve_calls == []
    assert observed.read_calls == []
    assert repository.stored_counts_for_test() == (0, 0, 0, 0, 0)
    expected_authorizations = 0 if failure.startswith("clock") else 1
    assert len(events) == expected_authorizations
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    rendered = "".join(traceback.format_exception(exc_info.value))
    assert BUSINESS_SENTINEL not in rendered
    assert PRIVATE_KEY not in rendered


def test_service_unexpected_authorization_exception_is_firewalled_before_uow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = InMemoryScreeningSubmissionUnitOfWork()
    observed = ObservedSubmissionUnitOfWork(repository)
    authorization = AuthorizationService(
        lambda _event: None,
        ScreeningEntitlements(),
        event_id_factory=lambda: "authz-unexpected",
    )

    def explode(_request: AuthorizationRequest, *, now: datetime) -> AuthorizedRequest:
        del now
        raise RuntimeError(BUSINESS_SENTINEL)

    monkeypatch.setattr(authorization, "require", explode)
    service = ScreeningSubmissionService(
        authorization,
        observed,
        clock=lambda: COMMITTED_AT,
        attempt_id_factory=lambda: "attempt-unexpected",
        intake_id_factory=lambda: "intake-unexpected",
        screening_id_factory=lambda: "screening-unexpected",
        outbox_id_factory=lambda: "outbox-unexpected",
    )
    with pytest.raises(ScreeningSubmissionServiceError) as exc_info:
        service.submit(intake(), PRIVATE_KEY)
    assert exc_info.value.code is ScreeningSubmissionServiceErrorCode.UNAVAILABLE
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    assert BUSINESS_SENTINEL not in "".join(traceback.format_exception(exc_info.value))
    assert observed.submit_calls == []
    assert observed.resolve_calls == []
    assert observed.read_calls == []


def test_service_duplicate_attempt_is_exact_or_fails_on_changed_authorization() -> None:
    repository = InMemoryScreeningSubmissionUnitOfWork()
    service, _, observed, events = screening_service(
        repository,
        authorization_event_id_factory=lambda: "authz-duplicate",
        attempt_id_factory=lambda: "attempt-duplicate-service",
        intake_id_factory=lambda: "intake-duplicate-service",
        screening_id_factory=lambda: "screening-duplicate-service",
        outbox_id_factory=lambda: "outbox-duplicate-service",
    )
    first = service.submit(intake(), PRIVATE_KEY)
    duplicate = service.submit(intake(), PRIVATE_KEY)
    assert first.disposition is ScreeningSubmissionServiceDisposition.APPLIED
    assert duplicate == first
    assert repository.stored_counts_for_test() == (1, 1, 1, 1, 1)
    assert len(observed.submit_calls) == 2
    assert len(events) == 2

    changed_auth_service, _, changed_observed, changed_events = screening_service(
        repository,
        authorization_event_id_factory=SequenceIdFactory("authz-changed-duplicate"),
        attempt_id_factory=lambda: "attempt-duplicate-service",
        intake_id_factory=lambda: "intake-duplicate-service",
        screening_id_factory=lambda: "screening-duplicate-service",
        outbox_id_factory=lambda: "outbox-duplicate-service",
    )
    with pytest.raises(ScreeningSubmissionServiceError) as exc_info:
        changed_auth_service.submit(intake(), PRIVATE_KEY)
    assert exc_info.value.code is ScreeningSubmissionServiceErrorCode.UNAVAILABLE
    assert len(changed_observed.submit_calls) == 1
    assert len(changed_events) == 1
    assert repository.stored_counts_for_test() == (1, 1, 1, 1, 1)


@pytest.mark.parametrize("collision", ["same_graph", "existing_graph"])
def test_service_global_id_collision_is_unavailable_and_atomic(collision: str) -> None:
    repository = InMemoryScreeningSubmissionUnitOfWork()
    if collision == "existing_graph":
        first_service, _, _, _ = screening_service(
            repository,
            authorization_event_id_factory=lambda: "authz-first-collision",
            attempt_id_factory=lambda: "attempt-first-collision",
            intake_id_factory=lambda: "intake-first-collision",
            screening_id_factory=lambda: "screening-first-collision",
            outbox_id_factory=lambda: "outbox-first-collision",
        )
        first_service.submit(intake(), PRIVATE_KEY)
        expected_counts = (1, 1, 1, 1, 1)
        attempt_id = "attempt-second-collision"
        intake_id = "screening-first-collision"
        screening_id = "screening-second-collision"
        outbox_id = "outbox-second-collision"
        item = intake()
        key = "different-collision-key"
    else:
        expected_counts = (0, 0, 0, 0, 0)
        attempt_id = intake_id = "same-global-id"
        screening_id = "same-screening-id"
        outbox_id = "same-outbox-id"
        item = intake()
        key = PRIVATE_KEY
    service, _, observed, events = screening_service(
        repository,
        authorization_event_id_factory=lambda: "authz-current-collision",
        attempt_id_factory=lambda: attempt_id,
        intake_id_factory=lambda: intake_id,
        screening_id_factory=lambda: screening_id,
        outbox_id_factory=lambda: outbox_id,
    )
    with pytest.raises(ScreeningSubmissionServiceError) as exc_info:
        service.submit(item, key)
    assert exc_info.value.code is ScreeningSubmissionServiceErrorCode.UNAVAILABLE
    assert len(events) == 1
    assert len(observed.submit_calls) == 1
    assert repository.stored_counts_for_test() == expected_counts


def test_service_results_failures_inputs_and_dependencies_are_exact_and_redacted() -> (
    None
):
    repository = InMemoryScreeningSubmissionUnitOfWork()
    service, _, observed, events = screening_service(repository)
    result = service.submit(intake(), PRIVATE_KEY)
    invalid_result_updates: tuple[dict[str, object], ...] = (
        {"disposition": "APPLIED"},
        {"receipt": object()},
    )
    for updates in invalid_result_updates:
        with pytest.raises(ScreeningSubmissionServiceError) as exc_info:
            replace(result, **updates)  # type: ignore[arg-type]
        assert exc_info.value.code is ScreeningSubmissionServiceErrorCode.UNAVAILABLE

    corrupted = copy.deepcopy(result)
    object.__setattr__(corrupted.receipt, "schema_version", "2.0.0")
    with pytest.raises(ScreeningSubmissionServiceError):
        corrupted.reverify()
    with pytest.raises(TypeError, match="^invalid screening submission service error$"):
        ScreeningSubmissionServiceError(
            cast(ScreeningSubmissionServiceErrorCode, "PRIVATE-CODE")
        )

    for item, key in (
        (cast(CanonicalScreeningIntake, object()), PRIVATE_KEY),
        (intake(), StringSubclass(PRIVATE_KEY)),
        (intake(), ""),
    ):
        before_authorization = len(events)
        before_uow = len(observed.submit_calls)
        with pytest.raises(ScreeningSubmissionServiceError) as exc_info:
            service.submit(item, key)
        assert exc_info.value.code is ScreeningSubmissionServiceErrorCode.UNAVAILABLE
        assert len(events) == before_authorization
        assert len(observed.submit_calls) == before_uow
        assert exc_info.value.__cause__ is None
        assert exc_info.value.__context__ is None

    authorization = AuthorizationService(
        lambda _event: None,
        ScreeningEntitlements(),
        event_id_factory=lambda: "authz-dependency",
    )
    valid_dependencies = {
        "authorization_service": authorization,
        "unit_of_work": observed,
        "clock": lambda: COMMITTED_AT,
        "attempt_id_factory": lambda: "attempt-dependency",
        "intake_id_factory": lambda: "intake-dependency",
        "screening_id_factory": lambda: "screening-dependency",
        "outbox_id_factory": lambda: "outbox-dependency",
    }
    for field_name in valid_dependencies:
        invalid = dict(valid_dependencies)
        invalid[field_name] = object()
        with pytest.raises(
            TypeError, match="^invalid screening submission service dependency$"
        ):
            ScreeningSubmissionService(**invalid)  # type: ignore[arg-type]

    rendered = repr(result)
    for private in (
        PRIVATE_KEY,
        BUSINESS_SENTINEL,
        next(iter(repository._results)),
        next(iter(repository._attempts.values())).canonical_hash,
    ):
        assert private not in rendered


@pytest.mark.parametrize("behavior", ["raise", "wrong_type", "wrong_attempt"])
def test_service_rejects_unexpected_or_mismatched_direct_uow_result(
    behavior: str,
) -> None:
    repository = InMemoryScreeningSubmissionUnitOfWork()
    observed = ObservedSubmissionUnitOfWork(repository)
    foreign = InMemoryScreeningSubmissionUnitOfWork().submit_atomic(
        atomic_write(
            attempt_id="direct-foreign-attempt",
            intake_id="direct-foreign-intake",
            screening_id="direct-foreign-screening",
            outbox_id="direct-foreign-outbox",
        )
    )
    if behavior == "raise":

        def failed_submit(
            _request: ScreeningSubmissionAtomicWrite,
        ) -> ScreeningSubmissionWriteResult:
            raise RuntimeError(BUSINESS_SENTINEL)

        observed.submit_override = failed_submit
    elif behavior == "wrong_type":
        observed.submit_override = lambda _request: cast(
            ScreeningSubmissionWriteResult, object()
        )
    else:
        observed.submit_override = lambda _request: foreign
    service, _, _, events = screening_service(
        repository,
        observed=observed,
        attempt_id_factory=lambda: "direct-current-attempt",
    )
    with pytest.raises(ScreeningSubmissionServiceError) as exc_info:
        service.submit(intake(), PRIVATE_KEY)
    assert exc_info.value.code is ScreeningSubmissionServiceErrorCode.UNAVAILABLE
    assert len(events) == 1
    assert len(observed.submit_calls) == 1
    assert observed.resolve_calls == []
    assert observed.read_calls == []
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    rendered = "".join(traceback.format_exception(exc_info.value))
    assert BUSINESS_SENTINEL not in rendered
    assert PRIVATE_KEY not in rendered


def test_service_result_receipt_is_a_defensive_copy_of_persisted_state() -> None:
    repository = InMemoryScreeningSubmissionUnitOfWork()
    service, _, _, _ = screening_service(repository)
    applied = service.submit(intake(), PRIVATE_KEY)
    stable_receipt = applied.receipt.reverify()
    object.__setattr__(applied.receipt, "screening_id", "mutated-screening")
    replay = service.submit(intake(indent=2), PRIVATE_KEY)
    assert replay.disposition is ScreeningSubmissionServiceDisposition.REPLAY
    assert replay.receipt == stable_receipt
    stored = repository.read_result(screening_idempotency_scope(intake(), PRIVATE_KEY))
    assert stored is not None
    assert ScreeningAcceptanceReceipt.from_result(stored) == stable_receipt


@pytest.mark.parametrize("changed", [False, True])
def test_service_same_scope_thread_race_is_finite_and_fully_audited(
    changed: bool,
) -> None:
    repository = InMemoryScreeningSubmissionUnitOfWork()
    service, _, observed, events = screening_service(repository)
    inputs = (
        intake(),
        (
            intake(action="QUOTE_RELEASE")
            if changed
            else intake(subject="actor-2", correlation_id="correlation-2", indent=2)
        ),
    )
    barrier = Barrier(2)

    def submit(item: CanonicalScreeningIntake) -> object:
        barrier.wait()
        try:
            return service.submit(item, PRIVATE_KEY)
        except ScreeningSubmissionServiceError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(submit, inputs))

    assert (
        sum(
            isinstance(outcome, ScreeningSubmissionServiceResult)
            and outcome.disposition is ScreeningSubmissionServiceDisposition.APPLIED
            for outcome in outcomes
        )
        == 1
    )
    if changed:
        assert (
            sum(
                isinstance(outcome, ScreeningSubmissionServiceError)
                and outcome.code
                is ScreeningSubmissionServiceErrorCode.IDEMPOTENCY_CONFLICT
                for outcome in outcomes
            )
            == 1
        )
    else:
        assert (
            sum(
                isinstance(outcome, ScreeningSubmissionServiceResult)
                and outcome.disposition is ScreeningSubmissionServiceDisposition.REPLAY
                for outcome in outcomes
            )
            == 1
        )
        successful = tuple(
            outcome
            for outcome in outcomes
            if isinstance(outcome, ScreeningSubmissionServiceResult)
        )
        assert successful[0].receipt == successful[1].receipt
    assert repository.stored_counts_for_test() == (1, 1, 1, 2, 1)
    assert len(observed.submit_calls) == 2
    assert observed.read_calls == []
    assert len(events) == 2
    assert {
        audit.authorization_event_id for audit in repository._attempts.values()
    } == {event.event_id for event in events}


def test_service_different_scope_dimensions_are_independent_under_thread_race() -> None:
    repository = InMemoryScreeningSubmissionUnitOfWork()
    service, _, observed, events = screening_service(repository)
    submissions = (
        (intake(), PRIVATE_KEY),
        (intake(), "service-different-key"),
        (intake(tenant_id="tenant-2"), PRIVATE_KEY),
        (intake(client_id="client-2"), PRIVATE_KEY),
    )
    barrier = Barrier(len(submissions))

    def submit(values: tuple[CanonicalScreeningIntake, object]) -> object:
        barrier.wait()
        return service.submit(*values)

    with ThreadPoolExecutor(max_workers=len(submissions)) as executor:
        outcomes = tuple(executor.map(submit, submissions))

    assert all(
        isinstance(outcome, ScreeningSubmissionServiceResult)
        and outcome.disposition is ScreeningSubmissionServiceDisposition.APPLIED
        for outcome in outcomes
    )
    results = tuple(
        cast(ScreeningSubmissionServiceResult, outcome) for outcome in outcomes
    )
    assert len({result.receipt for result in results}) == 4
    assert repository.stored_counts_for_test() == (4, 4, 4, 4, 4)
    assert len(observed.submit_calls) == 4
    assert len(events) == 4
    assert {
        audit.authorization_event_id for audit in repository._attempts.values()
    } == {event.event_id for event in events}
