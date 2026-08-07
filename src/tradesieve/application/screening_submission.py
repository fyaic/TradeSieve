"""Private B1 key, authorization-target, and canonical-intake command binding."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Protocol, cast

from tradesieve.application.auth import (
    ActorContext,
    ActorType,
    AuthorizationAuditFailure,
    AuthorizationDenied,
    AuthorizationRequest,
    AuthorizationService,
    AuthorizationUnavailable,
    AuthorizedRequest,
    Operation,
    RequestContext,
    Role,
    Scope,
    TargetObject,
)
from tradesieve.application.screening_intake import CanonicalScreeningIntake
from tradesieve.domain.screening_submission import (
    SCREENING_SUBMIT_OPERATION,
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
    ScreeningSubmissionIntegrityError,
)

MAX_IDEMPOTENCY_KEY_BYTES: Final = 256
_SAFE_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class ScreeningSubmissionCommandErrorCode(StrEnum):
    INVALID_IDEMPOTENCY_KEY = "INVALID_IDEMPOTENCY_KEY"
    AUTHORIZATION_BINDING = "AUTHORIZATION_BINDING"
    INTEGRITY_FAILURE = "INTEGRITY_FAILURE"


class ScreeningSubmissionCommandError(Exception):
    """Finite public failure with no key, request, attribution, or hash detail."""

    def __init__(self, code: ScreeningSubmissionCommandErrorCode) -> None:
        if type(code) is not ScreeningSubmissionCommandErrorCode:
            raise TypeError("invalid screening submission command error")
        self.code = code
        super().__init__(f"screening submission command rejected: {code.value}")


def _safe_call[T](operation: Callable[[], T]) -> T:
    failure: ScreeningSubmissionCommandError | None = None
    result: T | None = None
    try:
        result = operation()
    except ScreeningSubmissionCommandError as error:
        failure = error
    except ScreeningSubmissionIntegrityError:
        failure = ScreeningSubmissionCommandError(
            ScreeningSubmissionCommandErrorCode.INTEGRITY_FAILURE
        )
    except Exception:
        failure = ScreeningSubmissionCommandError(
            ScreeningSubmissionCommandErrorCode.INTEGRITY_FAILURE
        )
    if failure is not None:
        raise failure
    return cast(T, result)


def _key_digest(key: object) -> str:
    if type(key) is not str:
        raise ScreeningSubmissionCommandError(
            ScreeningSubmissionCommandErrorCode.INVALID_IDEMPOTENCY_KEY
        )
    encoding_failed = False
    try:
        encoded = key.encode("utf-8")
    except UnicodeEncodeError:
        encoding_failed = True
        encoded = b""
    if encoding_failed or not 1 <= len(encoded) <= MAX_IDEMPOTENCY_KEY_BYTES:
        raise ScreeningSubmissionCommandError(
            ScreeningSubmissionCommandErrorCode.INVALID_IDEMPOTENCY_KEY
        )
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _scope_target_id(scope: ScreeningIdempotencyScope) -> str:
    return f"idem-{scope.fingerprint().removeprefix('sha256:')}"


def _scope_unwrapped(
    intake: CanonicalScreeningIntake, idempotency_key: object
) -> ScreeningIdempotencyScope:
    if type(intake) is not CanonicalScreeningIntake:
        raise ScreeningSubmissionCommandError(
            ScreeningSubmissionCommandErrorCode.INTEGRITY_FAILURE
        )
    context = intake.context
    return ScreeningIdempotencyScope(
        tenant_id=context.tenant_id,
        client_id=context.actor.client_id,
        operation=SCREENING_SUBMIT_OPERATION,
        key_digest=_key_digest(idempotency_key),
    )


def screening_idempotency_scope(
    intake: CanonicalScreeningIntake, idempotency_key: object
) -> ScreeningIdempotencyScope:
    """Digest an exact private key into its explicit authenticated scope."""

    return _safe_call(lambda: _scope_unwrapped(intake, idempotency_key))


def _target_unwrapped(scope: ScreeningIdempotencyScope) -> TargetObject:
    verified_scope = scope.reverify()
    return TargetObject(
        tenant_id=verified_scope.tenant_id,
        object_type="SCREENING_IDEMPOTENCY",
        object_id=_scope_target_id(verified_scope),
    )


def screening_authorization_target(scope: ScreeningIdempotencyScope) -> TargetObject:
    """Derive the authorization target only from the exact idempotency scope."""

    return _safe_call(lambda: _target_unwrapped(scope))


def _context_is_exact(candidate: object, expected: RequestContext) -> bool:
    if (
        type(candidate) is not RequestContext
        or type(candidate.actor) is not ActorContext
    ):
        return False
    actor = candidate.actor
    return (
        type(candidate.tenant_id) is str
        and type(candidate.correlation_id) is str
        and type(actor.subject) is str
        and type(actor.client_id) is str
        and type(actor.tenant_id) is str
        and type(actor.actor_type) is ActorType
        and type(actor.scopes) is frozenset
        and all(type(scope) is Scope for scope in actor.scopes)
        and type(actor.roles) is frozenset
        and all(type(role) is Role for role in actor.roles)
        and type(actor.issuer) is str
        and type(actor.audience) is str
        and type(actor.issued_at) is datetime
        and actor.issued_at.tzinfo is UTC
        and type(actor.expires_at) is datetime
        and actor.expires_at.tzinfo is UTC
        and type(actor.demo_identity) is bool
        and candidate == expected
    )


def _target_is_exact(candidate: object, expected: TargetObject) -> bool:
    return (
        type(candidate) is TargetObject
        and type(candidate.tenant_id) is str
        and type(candidate.object_type) is str
        and type(candidate.object_id) is str
        and candidate == expected
    )


def _require_audit_id(value: object) -> str:
    if type(value) is not str or _SAFE_ID.fullmatch(value) is None:
        raise ScreeningSubmissionCommandError(
            ScreeningSubmissionCommandErrorCode.AUTHORIZATION_BINDING
        )
    return value


def _domain_actor_type(value: ActorType) -> ScreeningActorType:
    return ScreeningActorType(value.value)


@dataclass(frozen=True, slots=True)
class BoundScreeningSubmission:
    """Private immutable command bound to intake, scope, and an ALLOW audit event."""

    _intake: CanonicalScreeningIntake = field(repr=False)
    _scope: ScreeningIdempotencyScope = field(repr=False)
    _authorization_target: TargetObject = field(repr=False)
    _authorization_event_id: str = field(repr=False)

    def __post_init__(self) -> None:
        def initialize() -> None:
            if type(self._intake) is not CanonicalScreeningIntake:
                raise ScreeningSubmissionCommandError(
                    ScreeningSubmissionCommandErrorCode.INTEGRITY_FAILURE
                )
            intake = self._intake.reverify()
            scope = self._scope.reverify()
            context = intake.context
            expected_target = _target_unwrapped(scope)
            if (
                scope.tenant_id != context.tenant_id
                or scope.client_id != context.actor.client_id
                or scope.operation != SCREENING_SUBMIT_OPERATION
                or not _target_is_exact(self._authorization_target, expected_target)
            ):
                raise ScreeningSubmissionCommandError(
                    ScreeningSubmissionCommandErrorCode.INTEGRITY_FAILURE
                )
            authorization_event_id = _require_audit_id(self._authorization_event_id)
            target = TargetObject(
                tenant_id=expected_target.tenant_id,
                object_type=expected_target.object_type,
                object_id=expected_target.object_id,
            )
            object.__setattr__(self, "_intake", intake)
            object.__setattr__(self, "_scope", scope)
            object.__setattr__(self, "_authorization_target", target)
            object.__setattr__(self, "_authorization_event_id", authorization_event_id)

        _safe_call(initialize)

    def reverify(self) -> BoundScreeningSubmission:
        return _safe_call(
            lambda: type(self)(
                _intake=self._intake,
                _scope=self._scope,
                _authorization_target=self._authorization_target,
                _authorization_event_id=self._authorization_event_id,
            )
        )

    @property
    def scope(self) -> ScreeningIdempotencyScope:
        return _safe_call(lambda: self.reverify()._scope.reverify())

    @property
    def authorization_target(self) -> TargetObject:
        def read() -> TargetObject:
            target = self.reverify()._authorization_target
            return TargetObject(
                tenant_id=target.tenant_id,
                object_type=target.object_type,
                object_id=target.object_id,
            )

        return _safe_call(read)

    def accepted_intake_reference(self, intake_id: object) -> AcceptedIntakeReference:
        def build() -> AcceptedIntakeReference:
            verified = self.reverify()
            context = verified._intake.context
            return AcceptedIntakeReference(
                intake_id=cast(str, intake_id),
                tenant_id=context.tenant_id,
                client_id=context.actor.client_id,
                actor_subject=context.actor.subject,
                actor_type=_domain_actor_type(context.actor.actor_type),
                correlation_id=context.correlation_id,
                scope_fingerprint=verified._scope.fingerprint(),
                schema_version=verified._intake.schema_version,
                media_type=verified._intake.media_type,
                byte_length=verified._intake.byte_length,
                byte_hash=verified._intake.byte_hash,
                canonical_hash=verified._intake.canonical_hash,
                received_at=verified._intake.received_at,
            )

        return _safe_call(build)

    def _verified_intake_for_persistence(self) -> CanonicalScreeningIntake:
        return _safe_call(lambda: self.reverify()._intake.reverify())

    def attempt_audit(
        self,
        *,
        attempt_id: object,
        outcome: ScreeningAttemptOutcome,
        occurred_at: datetime,
        result: ScreeningIdempotencyResult,
    ) -> ScreeningAttemptAudit:
        def build() -> ScreeningAttemptAudit:
            verified = self.reverify()
            context = verified._intake.context
            return ScreeningAttemptAudit(
                attempt_id=cast(str, attempt_id),
                outcome=outcome,
                occurred_at=occurred_at,
                scope=verified._scope,
                actor_type=_domain_actor_type(context.actor.actor_type),
                actor_subject=context.actor.subject,
                correlation_id=context.correlation_id,
                authorization_event_id=verified._authorization_event_id,
                byte_hash=verified._intake.byte_hash,
                canonical_hash=verified._intake.canonical_hash,
                result=result,
            )

        return _safe_call(build)


def _bind_unwrapped(
    intake: CanonicalScreeningIntake,
    idempotency_key: object,
    authorized_request: AuthorizedRequest,
) -> BoundScreeningSubmission:
    verified_intake = intake.reverify()
    context = verified_intake.context
    scope = _scope_unwrapped(verified_intake, idempotency_key)
    target = _target_unwrapped(scope)
    if (
        type(authorized_request) is not AuthorizedRequest
        or type(authorized_request.operation) is not Operation
        or authorized_request.operation is not Operation.SCREENING_SUBMIT
        or not _context_is_exact(authorized_request.context, context)
        or not _target_is_exact(authorized_request.target, target)
    ):
        raise ScreeningSubmissionCommandError(
            ScreeningSubmissionCommandErrorCode.AUTHORIZATION_BINDING
        )
    authorization_event_id = _require_audit_id(authorized_request.audit_event_id)
    return BoundScreeningSubmission(
        _intake=verified_intake,
        _scope=scope,
        _authorization_target=target,
        _authorization_event_id=authorization_event_id,
    )


def bind_screening_submission(
    intake: CanonicalScreeningIntake,
    idempotency_key: object,
    authorized_request: AuthorizedRequest,
) -> BoundScreeningSubmission:
    """Bind an exact private key/intake to the matching accepted authorization."""

    return _safe_call(
        lambda: _bind_unwrapped(intake, idempotency_key, authorized_request)
    )


class ScreeningSubmissionPersistenceFailure(Exception):
    """Fixed known persistence failure without private submission detail."""

    def __init__(self) -> None:
        super().__init__("screening submission persistence failed")


class ScreeningSubmissionCommitOutcomeUnknown(Exception):
    """Fixed unknown commit outcome requiring an exact read-only resolution."""

    def __init__(self) -> None:
        super().__init__("screening submission commit outcome unknown")


def _persistence_call[T](operation: Callable[[], T]) -> T:
    failure: ScreeningSubmissionPersistenceFailure | None = None
    result: T | None = None
    try:
        result = operation()
    except ScreeningSubmissionPersistenceFailure as error:
        failure = error
    except Exception:
        failure = ScreeningSubmissionPersistenceFailure()
    if failure is not None:
        raise failure
    return cast(T, result)


def _verified_proposed_result(
    bound: BoundScreeningSubmission,
    proposed_result: ScreeningIdempotencyResult,
    occurred_at: datetime,
) -> ScreeningIdempotencyResult:
    verified_bound = bound.reverify()
    verified_proposed = proposed_result.reverify()
    if verified_proposed.committed_at != occurred_at:
        raise ScreeningSubmissionPersistenceFailure()
    accepted = verified_bound.accepted_intake_reference(
        verified_proposed.intake.intake_id
    )
    screening = ScreeningIdentity(
        screening_id=verified_proposed.screening.screening_id,
        intake=accepted,
        created_at=occurred_at,
    )
    outbox = ScreeningAcceptedOutboxIntent(
        event_id=verified_proposed.outbox.event_id,
        event_type=ScreeningOutboxEventType.SCREENING_ACCEPTED,
        schema_version=accepted.schema_version,
        occurred_at=occurred_at,
        intake_id=accepted.intake_id,
        screening_id=screening.screening_id,
    )
    expected = ScreeningIdempotencyResult(
        scope=verified_bound.scope,
        canonical_hash=accepted.canonical_hash,
        intake=accepted,
        screening=screening,
        outbox=outbox,
        committed_at=occurred_at,
    )
    if verified_proposed != expected:
        raise ScreeningSubmissionPersistenceFailure()
    return expected


@dataclass(frozen=True, slots=True)
class ScreeningSubmissionAtomicWrite:
    """Private exact graph and attempt inputs for one atomic persistence call."""

    bound: BoundScreeningSubmission = field(repr=False)
    proposed_result: ScreeningIdempotencyResult = field(repr=False)
    attempt_id: str = field(repr=False)
    occurred_at: datetime = field(repr=False)

    def __post_init__(self) -> None:
        def initialize() -> None:
            if (
                type(self.bound) is not BoundScreeningSubmission
                or type(self.proposed_result) is not ScreeningIdempotencyResult
                or type(self.occurred_at) is not datetime
                or self.occurred_at.tzinfo is not UTC
            ):
                raise ScreeningSubmissionPersistenceFailure()
            attempt_id = _require_audit_id(self.attempt_id)
            bound = self.bound.reverify()
            proposed = _verified_proposed_result(
                bound, self.proposed_result, self.occurred_at
            )
            object.__setattr__(self, "bound", bound)
            object.__setattr__(self, "proposed_result", proposed)
            object.__setattr__(self, "attempt_id", attempt_id)

        _persistence_call(initialize)

    def reverify(self) -> ScreeningSubmissionAtomicWrite:
        return type(self)(
            bound=self.bound,
            proposed_result=self.proposed_result,
            attempt_id=self.attempt_id,
            occurred_at=self.occurred_at,
        )


@dataclass(frozen=True, slots=True)
class ScreeningSubmissionWriteResult:
    """Finite disposition beside the stable safe receipt and private audit."""

    outcome: ScreeningAttemptOutcome
    receipt: ScreeningAcceptanceReceipt
    audit: ScreeningAttemptAudit = field(repr=False)

    def __post_init__(self) -> None:
        def initialize() -> None:
            if (
                type(self.outcome) is not ScreeningAttemptOutcome
                or type(self.receipt) is not ScreeningAcceptanceReceipt
                or type(self.audit) is not ScreeningAttemptAudit
            ):
                raise ScreeningSubmissionPersistenceFailure()
            audit = self.audit.reverify()
            receipt = self.receipt.reverify()
            if (
                self.outcome is not audit.outcome
                or receipt != ScreeningAcceptanceReceipt.from_result(audit.result)
            ):
                raise ScreeningSubmissionPersistenceFailure()
            object.__setattr__(self, "audit", audit)
            object.__setattr__(self, "receipt", receipt)

        _persistence_call(initialize)

    def reverify(self) -> ScreeningSubmissionWriteResult:
        return type(self)(
            outcome=self.outcome,
            receipt=self.receipt,
            audit=self.audit,
        )


class ScreeningSubmissionUnitOfWork(Protocol):
    """Narrow atomic write and exact verified read boundary."""

    def submit_atomic(
        self, request: ScreeningSubmissionAtomicWrite
    ) -> ScreeningSubmissionWriteResult: ...

    def read_result(
        self, scope: ScreeningIdempotencyScope
    ) -> ScreeningIdempotencyResult | None: ...

    def resolve_attempt(
        self,
        scope: ScreeningIdempotencyScope,
        canonical_hash: str,
        attempt_id: str,
    ) -> ScreeningSubmissionWriteResult | None: ...


class ScreeningSubmissionServiceErrorCode(StrEnum):
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    UNAVAILABLE = "UNAVAILABLE"


class ScreeningSubmissionServiceError(Exception):
    """Finite service failure without private submission or dependency detail."""

    def __init__(self, code: ScreeningSubmissionServiceErrorCode) -> None:
        if type(code) is not ScreeningSubmissionServiceErrorCode:
            raise TypeError("invalid screening submission service error")
        self.code = code
        super().__init__(f"screening submission failed: {code.value}")


class ScreeningSubmissionServiceDisposition(StrEnum):
    APPLIED = "APPLIED"
    REPLAY = "REPLAY"


@dataclass(frozen=True, slots=True)
class ScreeningSubmissionServiceResult:
    """Minimal safe service success without private audit or idempotency state."""

    disposition: ScreeningSubmissionServiceDisposition
    receipt: ScreeningAcceptanceReceipt

    def __post_init__(self) -> None:
        if (
            type(self.disposition) is not ScreeningSubmissionServiceDisposition
            or type(self.receipt) is not ScreeningAcceptanceReceipt
        ):
            raise ScreeningSubmissionServiceError(
                ScreeningSubmissionServiceErrorCode.UNAVAILABLE
            )
        object.__setattr__(self, "receipt", _unavailable_call(self.receipt.reverify))

    def reverify(self) -> ScreeningSubmissionServiceResult:
        return type(self)(disposition=self.disposition, receipt=self.receipt)


def _unavailable_call[T](operation: Callable[[], T]) -> T:
    failure: ScreeningSubmissionServiceError | None = None
    result: T | None = None
    try:
        result = operation()
    except ScreeningSubmissionServiceError as error:
        failure = error
    except Exception:
        failure = ScreeningSubmissionServiceError(
            ScreeningSubmissionServiceErrorCode.UNAVAILABLE
        )
    if failure is not None:
        raise failure
    return cast(T, result)


class ScreeningSubmissionService:
    """Authorize, construct, and atomically submit one strict canonical intake."""

    def __init__(
        self,
        authorization_service: AuthorizationService,
        unit_of_work: ScreeningSubmissionUnitOfWork,
        *,
        clock: Callable[[], datetime],
        attempt_id_factory: Callable[[], str],
        intake_id_factory: Callable[[], str],
        screening_id_factory: Callable[[], str],
        outbox_id_factory: Callable[[], str],
    ) -> None:
        if (
            type(authorization_service) is not AuthorizationService
            or any(
                not callable(getattr(unit_of_work, method, None))
                for method in ("submit_atomic", "read_result", "resolve_attempt")
            )
            or any(
                not callable(dependency)
                for dependency in (
                    clock,
                    attempt_id_factory,
                    intake_id_factory,
                    screening_id_factory,
                    outbox_id_factory,
                )
            )
        ):
            raise TypeError("invalid screening submission service dependency")
        self._authorization_service = authorization_service
        self._unit_of_work = unit_of_work
        self._clock = clock
        self._attempt_id_factory = attempt_id_factory
        self._intake_id_factory = intake_id_factory
        self._screening_id_factory = screening_id_factory
        self._outbox_id_factory = outbox_id_factory

    def submit(
        self,
        intake: CanonicalScreeningIntake,
        idempotency_key: object,
    ) -> ScreeningSubmissionServiceResult:
        verified_intake, scope, target = _unavailable_call(
            lambda: self._submission_identity(intake, idempotency_key)
        )
        attempt_time = _unavailable_call(self._attempt_time)
        authorized = self._require_authorization(verified_intake, target, attempt_time)
        bound = _unavailable_call(
            lambda: bind_screening_submission(
                verified_intake, idempotency_key, authorized
            )
        )
        request = _unavailable_call(lambda: self._atomic_write(bound, attempt_time))
        written: ScreeningSubmissionWriteResult | None = None
        outcome_unknown = False
        persistence_failed = False
        try:
            written = self._unit_of_work.submit_atomic(request)
        except ScreeningSubmissionCommitOutcomeUnknown:
            outcome_unknown = True
        except Exception:
            persistence_failed = True
        if persistence_failed:
            raise ScreeningSubmissionServiceError(
                ScreeningSubmissionServiceErrorCode.UNAVAILABLE
            )
        if outcome_unknown:
            return self._resolve_unknown(
                bound,
                scope,
                verified_intake.canonical_hash,
                request.attempt_id,
                attempt_time,
            )
        verified_write = _unavailable_call(
            lambda: self._verified_current_write(
                cast(ScreeningSubmissionWriteResult, written),
                bound,
                request.attempt_id,
                attempt_time,
            )
        )
        return self._service_result(verified_write)

    @staticmethod
    def _submission_identity(
        intake: CanonicalScreeningIntake,
        idempotency_key: object,
    ) -> tuple[
        CanonicalScreeningIntake,
        ScreeningIdempotencyScope,
        TargetObject,
    ]:
        if type(intake) is not CanonicalScreeningIntake:
            raise ScreeningSubmissionServiceError(
                ScreeningSubmissionServiceErrorCode.UNAVAILABLE
            )
        verified = intake.reverify()
        scope = screening_idempotency_scope(verified, idempotency_key)
        target = screening_authorization_target(scope)
        return verified, scope, target

    def _attempt_time(self) -> datetime:
        value = self._clock()
        if type(value) is not datetime or value.tzinfo is not UTC:
            raise ScreeningSubmissionServiceError(
                ScreeningSubmissionServiceErrorCode.UNAVAILABLE
            )
        return value

    def _require_authorization(
        self,
        intake: CanonicalScreeningIntake,
        target: TargetObject,
        attempt_time: datetime,
    ) -> AuthorizedRequest:
        authorized: AuthorizedRequest | None = None
        authorization_failure: (
            AuthorizationDenied
            | AuthorizationUnavailable
            | AuthorizationAuditFailure
            | None
        ) = None
        service_failure: ScreeningSubmissionServiceError | None = None
        try:
            authorized = self._authorization_service.require(
                AuthorizationRequest(
                    context=intake.context,
                    operation=Operation.SCREENING_SUBMIT,
                    target=target,
                ),
                now=attempt_time,
            )
        except (
            AuthorizationDenied,
            AuthorizationUnavailable,
            AuthorizationAuditFailure,
        ) as error:
            authorization_failure = error
        except Exception:
            service_failure = ScreeningSubmissionServiceError(
                ScreeningSubmissionServiceErrorCode.UNAVAILABLE
            )
        if authorization_failure is not None:
            raise authorization_failure
        if service_failure is not None:
            raise service_failure
        return cast(AuthorizedRequest, authorized)

    def _atomic_write(
        self,
        bound: BoundScreeningSubmission,
        attempt_time: datetime,
    ) -> ScreeningSubmissionAtomicWrite:
        attempt_id = self._attempt_id_factory()
        accepted = bound.accepted_intake_reference(self._intake_id_factory())
        screening = ScreeningIdentity(
            screening_id=self._screening_id_factory(),
            intake=accepted,
            created_at=attempt_time,
        )
        outbox = ScreeningAcceptedOutboxIntent(
            event_id=self._outbox_id_factory(),
            event_type=ScreeningOutboxEventType.SCREENING_ACCEPTED,
            schema_version=accepted.schema_version,
            occurred_at=attempt_time,
            intake_id=accepted.intake_id,
            screening_id=screening.screening_id,
        )
        proposed = ScreeningIdempotencyResult(
            scope=bound.scope,
            canonical_hash=accepted.canonical_hash,
            intake=accepted,
            screening=screening,
            outbox=outbox,
            committed_at=attempt_time,
        )
        return ScreeningSubmissionAtomicWrite(
            bound=bound,
            proposed_result=proposed,
            attempt_id=attempt_id,
            occurred_at=attempt_time,
        )

    def _resolve_unknown(
        self,
        bound: BoundScreeningSubmission,
        scope: ScreeningIdempotencyScope,
        canonical_hash: str,
        attempt_id: str,
        attempt_time: datetime,
    ) -> ScreeningSubmissionServiceResult:
        resolved = _unavailable_call(
            lambda: self._unit_of_work.resolve_attempt(
                scope, canonical_hash, attempt_id
            )
        )
        if resolved is None:
            raise ScreeningSubmissionServiceError(
                ScreeningSubmissionServiceErrorCode.UNAVAILABLE
            )
        verified = _unavailable_call(
            lambda: self._verified_current_write(
                resolved, bound, attempt_id, attempt_time
            )
        )
        return self._service_result(verified)

    @staticmethod
    def _verified_current_write(
        written: ScreeningSubmissionWriteResult,
        bound: BoundScreeningSubmission,
        attempt_id: str,
        attempt_time: datetime,
    ) -> ScreeningSubmissionWriteResult:
        if type(written) is not ScreeningSubmissionWriteResult:
            raise ScreeningSubmissionServiceError(
                ScreeningSubmissionServiceErrorCode.UNAVAILABLE
            )
        verified = written.reverify()
        expected_audit = bound.attempt_audit(
            attempt_id=attempt_id,
            outcome=verified.outcome,
            occurred_at=attempt_time,
            result=verified.audit.result,
        )
        if verified.audit != expected_audit:
            raise ScreeningSubmissionServiceError(
                ScreeningSubmissionServiceErrorCode.UNAVAILABLE
            )
        return verified

    @staticmethod
    def _service_result(
        written: ScreeningSubmissionWriteResult,
    ) -> ScreeningSubmissionServiceResult:
        if written.outcome is ScreeningAttemptOutcome.CONFLICT:
            raise ScreeningSubmissionServiceError(
                ScreeningSubmissionServiceErrorCode.IDEMPOTENCY_CONFLICT
            )
        disposition = ScreeningSubmissionServiceDisposition(written.outcome.value)
        return ScreeningSubmissionServiceResult(
            disposition=disposition,
            receipt=written.receipt,
        )


__all__ = [
    "BoundScreeningSubmission",
    "MAX_IDEMPOTENCY_KEY_BYTES",
    "SCREENING_SUBMIT_OPERATION",
    "ScreeningSubmissionCommandError",
    "ScreeningSubmissionCommandErrorCode",
    "ScreeningSubmissionAtomicWrite",
    "ScreeningSubmissionCommitOutcomeUnknown",
    "ScreeningSubmissionPersistenceFailure",
    "ScreeningSubmissionUnitOfWork",
    "ScreeningSubmissionWriteResult",
    "ScreeningSubmissionService",
    "ScreeningSubmissionServiceDisposition",
    "ScreeningSubmissionServiceError",
    "ScreeningSubmissionServiceErrorCode",
    "ScreeningSubmissionServiceResult",
    "bind_screening_submission",
    "screening_authorization_target",
    "screening_idempotency_scope",
]
