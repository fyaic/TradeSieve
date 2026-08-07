"""Exact PostgreSQL unit of work for immutable screening submissions."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, Protocol, cast

from psycopg import Connection
from psycopg.pq import TransactionStatus

from tradesieve.adapters.screening_submission_codec import (
    decode_request_context,
    encode_request_context,
)
from tradesieve.application.screening_intake import CanonicalScreeningIntake
from tradesieve.application.screening_submission import (
    ScreeningSubmissionAtomicWrite,
    ScreeningSubmissionCommitOutcomeUnknown,
    ScreeningSubmissionPersistenceFailure,
    ScreeningSubmissionWriteResult,
)
from tradesieve.domain.screening_submission import (
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
)

MAX_ATTEMPTS: Final = 4_096
MAX_ATTEMPT_QUERY: Final = MAX_ATTEMPTS + 1

_SAFE_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CONTENT_HASH: Final = re.compile(r"^sha256:[a-f0-9]{64}$")

_LEDGER_COLUMNS: Final = (
    "scope_fingerprint, canonical_hash, intake_id, screening_id, outbox_id, "
    "committed_at"
)
_GRAPH_COLUMNS: Final = (
    "intake.intake_id, intake.id_kind, intake.tenant_id, intake.client_id, "
    "intake.actor_subject, intake.actor_type, intake.correlation_id, "
    "intake.scope_fingerprint, intake.schema_version, intake.media_type, "
    "intake.raw_body, intake.byte_length, intake.byte_hash, "
    "intake.canonical_hash, intake.received_at, intake.private_context, "
    "intake_registry.object_kind, screening.screening_id, screening.id_kind, "
    "screening.intake_id, screening.tenant_id, screening.client_id, "
    "screening.scope_fingerprint, screening.canonical_hash, "
    "screening.created_at, screening_registry.object_kind, outbox.event_id, "
    "outbox.id_kind, outbox.event_type, outbox.schema_version, "
    "outbox.occurred_at, outbox.intake_id, outbox.screening_id, "
    "outbox_registry.object_kind"
)
_ATTEMPT_COLUMNS: Final = (
    "attempt.sequence, attempt.attempt_id, attempt.id_kind, attempt.outcome, "
    "attempt.occurred_at, attempt.actor_type, attempt.actor_subject, "
    "attempt.correlation_id, attempt.authorization_event_id, "
    "attempt.byte_hash, attempt.canonical_hash, attempt.scope_fingerprint, "
    "attempt_registry.object_kind, auth_event.event_id, "
    "auth_event.occurred_at, auth_event.actor_subject, "
    "auth_event.client_id, auth_event.actor_type, "
    "auth_event.actor_tenant_id, auth_event.request_tenant_id, "
    "auth_event.target_tenant_id, auth_event.operation, "
    "auth_event.target_ref, auth_event.correlation_id, "
    "auth_event.outcome, auth_event.reason"
)

type Row = tuple[Any, ...]
type ScopeValues = tuple[str, str, str, str]
type ConnectionFactory = Callable[[], Connection[Any]]


class _ConnectionInfo(Protocol):
    transaction_status: object


class _ConnectionLike(Protocol):
    info: _ConnectionInfo
    autocommit: object

    def execute(self, query: str, params: object = ...) -> object: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _PreparedSubmission:
    request: ScreeningSubmissionAtomicWrite = field(repr=False)
    canonical_intake: CanonicalScreeningIntake = field(repr=False)
    raw_body: bytes = field(repr=False)
    private_context: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class _StoredScope:
    result: ScreeningIdempotencyResult = field(repr=False)
    attempts: tuple[ScreeningAttemptAudit, ...] = field(repr=False)


def _invalid() -> ScreeningSubmissionPersistenceFailure:
    return ScreeningSubmissionPersistenceFailure()


def _persistence_boundary[T](operation: Callable[[], T]) -> T:
    failure: (
        ScreeningSubmissionPersistenceFailure
        | ScreeningSubmissionCommitOutcomeUnknown
        | None
    ) = None
    result: T | None = None
    try:
        result = operation()
    except ScreeningSubmissionCommitOutcomeUnknown as error:
        failure = error
    except ScreeningSubmissionPersistenceFailure as error:
        failure = error
    except Exception:
        failure = _invalid()
    if failure is not None:
        raise failure
    return cast(T, result)


def _scope_values(scope: ScreeningIdempotencyScope) -> ScopeValues:
    return (scope.tenant_id, scope.client_id, scope.operation, scope.key_digest)


def _verified_scope(value: object) -> ScreeningIdempotencyScope:
    if type(value) is not ScreeningIdempotencyScope:
        raise _invalid()
    return value.reverify()


def _string(value: object) -> str:
    if type(value) is not str:
        raise _invalid()
    return value


def _safe_id(value: object) -> str:
    result = _string(value)
    if _SAFE_ID.fullmatch(result) is None:
        raise _invalid()
    return result


def _content_hash(value: object) -> str:
    result = _string(value)
    if _CONTENT_HASH.fullmatch(result) is None:
        raise _invalid()
    return result


def _integer(value: object) -> int:
    if type(value) is not int:
        raise _invalid()
    return value


def _bytes(value: object) -> bytes:
    if type(value) is not bytes:
        raise _invalid()
    return value


def _utc_datetime(value: object) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise _invalid()
    return value.astimezone(UTC)


def _write_result(audit: ScreeningAttemptAudit) -> ScreeningSubmissionWriteResult:
    verified = audit.reverify()
    return ScreeningSubmissionWriteResult(
        outcome=verified.outcome,
        receipt=ScreeningAcceptanceReceipt.from_result(verified.result),
        audit=verified,
    )


class PostgresScreeningSubmissionUnitOfWork:
    """Open one explicit connection per exact write, read, or resolution."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        if not callable(connection_factory):
            raise TypeError("invalid screening submission connection factory")
        self._connection_factory = connection_factory

    def submit_atomic(
        self, request: ScreeningSubmissionAtomicWrite
    ) -> ScreeningSubmissionWriteResult:
        return _persistence_boundary(
            lambda: self._submit_prepared(self._prepare_submission(request))
        )

    def read_result(
        self, scope: ScreeningIdempotencyScope
    ) -> ScreeningIdempotencyResult | None:
        return _persistence_boundary(
            lambda: self._read_result_verified(_verified_scope(scope))
        )

    def resolve_attempt(
        self,
        scope: ScreeningIdempotencyScope,
        canonical_hash: str,
        attempt_id: str,
    ) -> ScreeningSubmissionWriteResult | None:
        def resolve() -> ScreeningSubmissionWriteResult | None:
            verified_scope = _verified_scope(scope)
            verified_hash = _content_hash(canonical_hash)
            verified_attempt = _safe_id(attempt_id)
            return self._resolve_verified(
                verified_scope, verified_hash, verified_attempt
            )

        return _persistence_boundary(resolve)

    @staticmethod
    def _prepare_submission(request: object) -> _PreparedSubmission:
        if type(request) is not ScreeningSubmissionAtomicWrite:
            raise _invalid()
        verified = request.reverify()
        canonical_intake = verified.bound._verified_intake_for_persistence().reverify()
        raw_body = _bytes(canonical_intake._raw_bytes)
        private_context = encode_request_context(canonical_intake.context)
        if decode_request_context(private_context) != canonical_intake.context:
            raise _invalid()
        return _PreparedSubmission(
            request=verified,
            canonical_intake=canonical_intake,
            raw_body=raw_body,
            private_context=private_context,
        )

    def _submit_prepared(
        self, prepared: _PreparedSubmission
    ) -> ScreeningSubmissionWriteResult:
        return self._run_write(lambda connection: self._submit_on(connection, prepared))

    def _read_result_verified(
        self, scope: ScreeningIdempotencyScope
    ) -> ScreeningIdempotencyResult | None:
        stored = self._run_read(lambda connection: self._load_scope(connection, scope))
        return None if stored is None else stored.result.reverify()

    def _resolve_verified(
        self,
        scope: ScreeningIdempotencyScope,
        canonical_hash: str,
        attempt_id: str,
    ) -> ScreeningSubmissionWriteResult | None:
        stored = self._run_read(lambda connection: self._load_scope(connection, scope))
        if stored is None:
            return None
        audit = next(
            (item for item in stored.attempts if item.attempt_id == attempt_id), None
        )
        if (
            audit is None
            or audit.scope != scope
            or audit.canonical_hash != canonical_hash
        ):
            return None
        return _write_result(audit)

    def _submit_on(
        self,
        connection: _ConnectionLike,
        prepared: _PreparedSubmission,
    ) -> ScreeningSubmissionWriteResult:
        request = prepared.request
        scope = request.bound.scope
        lock_rows = self._fetch_rows(
            connection,
            "SELECT 1 FROM (SELECT pg_advisory_xact_lock("
            "hashtextextended(%s, 0))) AS scope_lock",
            (scope.fingerprint(),),
            maximum=1,
        )
        if lock_rows != ((1,),):
            raise _invalid()
        stored = self._load_scope(connection, scope, lock=True)
        if stored is None:
            expected = self._insert_new(connection, prepared)
            previous_count = 0
        else:
            duplicate = next(
                (
                    audit
                    for audit in stored.attempts
                    if audit.attempt_id == request.attempt_id
                ),
                None,
            )
            if duplicate is not None:
                return self._exact_duplicate(request, duplicate)
            if len(stored.attempts) >= MAX_ATTEMPTS:
                raise _invalid()
            expected = self._insert_retry(connection, request, stored)
            previous_count = len(stored.attempts)
        refreshed = self._load_scope(connection, scope, lock=True)
        if (
            refreshed is None
            or refreshed.result != expected.result
            or len(refreshed.attempts) != previous_count + 1
            or refreshed.attempts[-1] != expected
        ):
            raise _invalid()
        return _write_result(refreshed.attempts[-1])

    @staticmethod
    def _exact_duplicate(
        request: ScreeningSubmissionAtomicWrite,
        stored_audit: ScreeningAttemptAudit,
    ) -> ScreeningSubmissionWriteResult:
        stored = stored_audit.reverify()
        if stored.scope != request.bound.scope:
            raise _invalid()
        candidate = request.bound.attempt_audit(
            attempt_id=request.attempt_id,
            outcome=stored.outcome,
            occurred_at=request.occurred_at,
            result=stored.result,
        )
        if candidate != stored or (
            stored.outcome is ScreeningAttemptOutcome.APPLIED
            and request.proposed_result != stored.result
        ):
            raise _invalid()
        return _write_result(stored)

    def _insert_new(
        self,
        connection: _ConnectionLike,
        prepared: _PreparedSubmission,
    ) -> ScreeningAttemptAudit:
        request = prepared.request
        proposed = request.proposed_result.reverify()
        applied = request.bound.attempt_audit(
            attempt_id=request.attempt_id,
            outcome=ScreeningAttemptOutcome.APPLIED,
            occurred_at=request.occurred_at,
            result=proposed,
        )
        allocated = (
            proposed.intake.intake_id,
            proposed.screening.screening_id,
            request.attempt_id,
            proposed.outbox.event_id,
        )
        if len(set(allocated)) != 4:
            raise _invalid()
        for opaque_id, kind in zip(
            allocated, ("INTAKE", "SCREENING", "ATTEMPT", "OUTBOX"), strict=True
        ):
            self._execute_one(
                connection,
                "INSERT INTO screening_opaque_id_registry "
                "(opaque_id, object_kind) VALUES (%s, %s)",
                (opaque_id, kind),
            )
        intake = proposed.intake
        self._execute_one(
            connection,
            "INSERT INTO screening_accepted_intake "
            "(intake_id, id_kind, tenant_id, client_id, actor_subject, "
            "actor_type, correlation_id, scope_fingerprint, schema_version, "
            "media_type, raw_body, byte_length, byte_hash, canonical_hash, "
            "received_at, private_context) VALUES (" + ", ".join(["%s"] * 16) + ")",
            (
                intake.intake_id,
                "INTAKE",
                intake.tenant_id,
                intake.client_id,
                intake.actor_subject,
                intake.actor_type.value,
                intake.correlation_id,
                intake.scope_fingerprint,
                intake.schema_version,
                intake.media_type,
                prepared.raw_body,
                intake.byte_length,
                intake.byte_hash,
                intake.canonical_hash,
                intake.received_at,
                prepared.private_context,
            ),
        )
        screening = proposed.screening
        self._execute_one(
            connection,
            "INSERT INTO screening_identity "
            "(screening_id, id_kind, intake_id, tenant_id, client_id, "
            "scope_fingerprint, canonical_hash, created_at) VALUES ("
            + ", ".join(["%s"] * 8)
            + ")",
            (
                screening.screening_id,
                "SCREENING",
                intake.intake_id,
                intake.tenant_id,
                intake.client_id,
                intake.scope_fingerprint,
                intake.canonical_hash,
                screening.created_at,
            ),
        )
        outbox = proposed.outbox
        self._execute_one(
            connection,
            "INSERT INTO screening_accepted_outbox "
            "(event_id, id_kind, event_type, schema_version, occurred_at, "
            "intake_id, screening_id) VALUES (" + ", ".join(["%s"] * 7) + ")",
            (
                outbox.event_id,
                "OUTBOX",
                outbox.event_type.value,
                outbox.schema_version,
                outbox.occurred_at,
                outbox.intake_id,
                outbox.screening_id,
            ),
        )
        scope = proposed.scope
        self._execute_one(
            connection,
            "INSERT INTO screening_idempotency_ledger "
            "(tenant_id, client_id, operation, key_digest, scope_fingerprint, "
            "canonical_hash, intake_id, screening_id, outbox_id, committed_at) "
            "VALUES (" + ", ".join(["%s"] * 10) + ")",
            (
                *_scope_values(scope),
                scope.fingerprint(),
                proposed.canonical_hash,
                intake.intake_id,
                screening.screening_id,
                outbox.event_id,
                proposed.committed_at,
            ),
        )
        self._insert_attempt(connection, applied, sequence=1)
        return applied.reverify()

    def _insert_retry(
        self,
        connection: _ConnectionLike,
        request: ScreeningSubmissionAtomicWrite,
        stored: _StoredScope,
    ) -> ScreeningAttemptAudit:
        result = stored.result.reverify()
        outcome = (
            ScreeningAttemptOutcome.REPLAY
            if request.proposed_result.canonical_hash == result.canonical_hash
            else ScreeningAttemptOutcome.CONFLICT
        )
        audit = request.bound.attempt_audit(
            attempt_id=request.attempt_id,
            outcome=outcome,
            occurred_at=request.occurred_at,
            result=result,
        )
        self._execute_one(
            connection,
            "INSERT INTO screening_opaque_id_registry "
            "(opaque_id, object_kind) VALUES (%s, %s)",
            (audit.attempt_id, "ATTEMPT"),
        )
        self._insert_attempt(connection, audit, sequence=len(stored.attempts) + 1)
        return audit.reverify()

    def _insert_attempt(
        self,
        connection: _ConnectionLike,
        audit: ScreeningAttemptAudit,
        *,
        sequence: int,
    ) -> None:
        verified = audit.reverify()
        self._execute_one(
            connection,
            "INSERT INTO screening_attempt_audit "
            "(tenant_id, client_id, operation, key_digest, scope_fingerprint, "
            "sequence, attempt_id, id_kind, outcome, occurred_at, actor_type, "
            "actor_subject, correlation_id, authorization_event_id, byte_hash, "
            "canonical_hash) VALUES (" + ", ".join(["%s"] * 16) + ")",
            (
                *_scope_values(verified.scope),
                verified.scope.fingerprint(),
                sequence,
                verified.attempt_id,
                "ATTEMPT",
                verified.outcome.value,
                verified.occurred_at,
                verified.actor_type.value,
                verified.actor_subject,
                verified.correlation_id,
                verified.authorization_event_id,
                verified.byte_hash,
                verified.canonical_hash,
            ),
        )

    def _load_scope(
        self,
        connection: _ConnectionLike,
        scope: ScreeningIdempotencyScope,
        *,
        lock: bool = False,
    ) -> _StoredScope | None:
        scope_params = _scope_values(scope)
        ledger_rows = self._fetch_rows(
            connection,
            f"SELECT {_LEDGER_COLUMNS} FROM screening_idempotency_ledger "
            "WHERE tenant_id = %s AND client_id = %s AND operation = %s "
            "AND key_digest = %s LIMIT 2" + (" FOR SHARE" if lock else ""),
            scope_params,
            maximum=1,
        )
        if not ledger_rows:
            return None
        ledger = ledger_rows[0]
        if len(ledger) != 6:
            raise _invalid()
        scope_fingerprint = _content_hash(ledger[0])
        ledger_canonical_hash = _content_hash(ledger[1])
        ledger_intake_id = _safe_id(ledger[2])
        ledger_screening_id = _safe_id(ledger[3])
        ledger_outbox_id = _safe_id(ledger[4])
        committed_at = _utc_datetime(ledger[5])
        if scope_fingerprint != scope.fingerprint():
            raise _invalid()

        graph_rows = self._fetch_rows(
            connection,
            f"SELECT {_GRAPH_COLUMNS} FROM screening_idempotency_ledger AS ledger "
            "LEFT JOIN screening_accepted_intake AS intake "
            "ON intake.intake_id = ledger.intake_id "
            "LEFT JOIN screening_opaque_id_registry AS intake_registry "
            "ON intake_registry.opaque_id = intake.intake_id "
            "LEFT JOIN screening_identity AS screening "
            "ON screening.screening_id = ledger.screening_id "
            "LEFT JOIN screening_opaque_id_registry AS screening_registry "
            "ON screening_registry.opaque_id = screening.screening_id "
            "LEFT JOIN screening_accepted_outbox AS outbox "
            "ON outbox.event_id = ledger.outbox_id "
            "LEFT JOIN screening_opaque_id_registry AS outbox_registry "
            "ON outbox_registry.opaque_id = outbox.event_id "
            "WHERE ledger.tenant_id = %s AND ledger.client_id = %s "
            "AND ledger.operation = %s AND ledger.key_digest = %s LIMIT 2",
            scope_params,
            maximum=1,
        )
        if len(graph_rows) != 1:
            raise _invalid()
        result = self._result_from_graph(
            graph_rows[0],
            scope,
            ledger_canonical_hash=ledger_canonical_hash,
            ledger_intake_id=ledger_intake_id,
            ledger_screening_id=ledger_screening_id,
            ledger_outbox_id=ledger_outbox_id,
            committed_at=committed_at,
        )
        attempt_rows = self._fetch_rows(
            connection,
            f"SELECT {_ATTEMPT_COLUMNS} FROM screening_attempt_audit AS attempt "
            "LEFT JOIN screening_opaque_id_registry AS attempt_registry "
            "ON attempt_registry.opaque_id = attempt.attempt_id "
            "LEFT JOIN authorization_audit_event AS auth_event "
            "ON auth_event.event_id = attempt.authorization_event_id "
            "WHERE attempt.tenant_id = %s AND attempt.client_id = %s "
            "AND attempt.operation = %s AND attempt.key_digest = %s "
            "ORDER BY attempt.sequence LIMIT %s",
            (*scope_params, MAX_ATTEMPT_QUERY),
            maximum=MAX_ATTEMPT_QUERY,
        )
        if not attempt_rows or len(attempt_rows) > MAX_ATTEMPTS:
            raise _invalid()
        attempts = tuple(
            self._attempt_from_row(row, scope, result, expected_sequence=position)
            for position, row in enumerate(attempt_rows, start=1)
        )
        graph_ids = {
            result.intake.intake_id,
            result.screening.screening_id,
            result.outbox.event_id,
        }
        attempt_ids = tuple(audit.attempt_id for audit in attempts)
        authorization_ids = tuple(audit.authorization_event_id for audit in attempts)
        if (
            len(graph_ids) != 3
            or len(set(attempt_ids)) != len(attempt_ids)
            or len(set(authorization_ids)) != len(authorization_ids)
            or graph_ids.intersection(attempt_ids)
        ):
            raise _invalid()
        return _StoredScope(result=result.reverify(), attempts=attempts)

    @staticmethod
    def _result_from_graph(
        row: Row,
        scope: ScreeningIdempotencyScope,
        *,
        ledger_canonical_hash: str,
        ledger_intake_id: str,
        ledger_screening_id: str,
        ledger_outbox_id: str,
        committed_at: datetime,
    ) -> ScreeningIdempotencyResult:
        if len(row) != 34:
            raise _invalid()
        intake_id = _safe_id(row[0])
        if _string(row[1]) != "INTAKE" or _string(row[16]) != "INTAKE":
            raise _invalid()
        tenant_id = _safe_id(row[2])
        client_id = _safe_id(row[3])
        actor_subject = _safe_id(row[4])
        actor_type = ScreeningActorType(_string(row[5]))
        correlation_id = _safe_id(row[6])
        scope_fingerprint = _content_hash(row[7])
        schema_version = _string(row[8])
        media_type = _string(row[9])
        raw_body = _bytes(row[10])
        byte_length = _integer(row[11])
        byte_hash = _content_hash(row[12])
        canonical_hash = _content_hash(row[13])
        received_at = _utc_datetime(row[14])
        private_context = _bytes(row[15])
        decoded_context = decode_request_context(private_context)
        canonical_intake = CanonicalScreeningIntake.decode(
            raw_body,
            media_type,
            context=decoded_context,
            received_at=received_at,
        ).reverify()
        if encode_request_context(decoded_context) != private_context:
            raise _invalid()
        accepted = AcceptedIntakeReference(
            intake_id=intake_id,
            tenant_id=tenant_id,
            client_id=client_id,
            actor_subject=actor_subject,
            actor_type=actor_type,
            correlation_id=correlation_id,
            scope_fingerprint=scope_fingerprint,
            schema_version=schema_version,
            media_type=media_type,
            byte_length=byte_length,
            byte_hash=byte_hash,
            canonical_hash=canonical_hash,
            received_at=received_at,
        )
        context = canonical_intake.context
        if (
            tenant_id != context.tenant_id
            or client_id != context.actor.client_id
            or actor_subject != context.actor.subject
            or actor_type.value != context.actor.actor_type.value
            or correlation_id != context.correlation_id
            or accepted.schema_version != canonical_intake.schema_version
            or accepted.byte_length != canonical_intake.byte_length
            or accepted.byte_hash != canonical_intake.byte_hash
            or accepted.canonical_hash != canonical_intake.canonical_hash
            or scope_fingerprint != scope.fingerprint()
        ):
            raise _invalid()

        screening_id = _safe_id(row[17])
        if _string(row[18]) != "SCREENING" or _string(row[25]) != "SCREENING":
            raise _invalid()
        if (
            _safe_id(row[19]) != intake_id
            or _safe_id(row[20]) != tenant_id
            or _safe_id(row[21]) != client_id
            or _content_hash(row[22]) != scope_fingerprint
            or _content_hash(row[23]) != canonical_hash
        ):
            raise _invalid()
        screening = ScreeningIdentity(
            screening_id=screening_id,
            intake=accepted,
            created_at=_utc_datetime(row[24]),
        )

        outbox_id = _safe_id(row[26])
        if _string(row[27]) != "OUTBOX" or _string(row[33]) != "OUTBOX":
            raise _invalid()
        outbox = ScreeningAcceptedOutboxIntent(
            event_id=outbox_id,
            event_type=ScreeningOutboxEventType(_string(row[28])),
            schema_version=_string(row[29]),
            occurred_at=_utc_datetime(row[30]),
            intake_id=_safe_id(row[31]),
            screening_id=_safe_id(row[32]),
        )
        result = ScreeningIdempotencyResult(
            scope=scope,
            canonical_hash=ledger_canonical_hash,
            intake=accepted,
            screening=screening,
            outbox=outbox,
            committed_at=committed_at,
        )
        if (
            result.canonical_hash != canonical_hash
            or result.intake.intake_id != ledger_intake_id
            or result.screening.screening_id != ledger_screening_id
            or result.outbox.event_id != ledger_outbox_id
        ):
            raise _invalid()
        return result.reverify()

    @staticmethod
    def _attempt_from_row(
        row: Row,
        scope: ScreeningIdempotencyScope,
        result: ScreeningIdempotencyResult,
        *,
        expected_sequence: int,
    ) -> ScreeningAttemptAudit:
        if len(row) != 26 or _integer(row[0]) != expected_sequence:
            raise _invalid()
        attempt_id = _safe_id(row[1])
        if _string(row[2]) != "ATTEMPT" or _string(row[12]) != "ATTEMPT":
            raise _invalid()
        outcome = ScreeningAttemptOutcome(_string(row[3]))
        occurred_at = _utc_datetime(row[4])
        actor_type = ScreeningActorType(_string(row[5]))
        actor_subject = _safe_id(row[6])
        correlation_id = _safe_id(row[7])
        authorization_event_id = _safe_id(row[8])
        byte_hash = _content_hash(row[9])
        canonical_hash = _content_hash(row[10])
        if _content_hash(row[11]) != scope.fingerprint():
            raise _invalid()
        audit = ScreeningAttemptAudit(
            attempt_id=attempt_id,
            outcome=outcome,
            occurred_at=occurred_at,
            scope=scope,
            actor_type=actor_type,
            actor_subject=actor_subject,
            correlation_id=correlation_id,
            authorization_event_id=authorization_event_id,
            byte_hash=byte_hash,
            canonical_hash=canonical_hash,
            result=result,
        )
        expected_target = (
            "SCREENING_IDEMPOTENCY:idem-" + scope.fingerprint().removeprefix("sha256:")
        )
        authorization_values = (
            _safe_id(row[13]),
            _utc_datetime(row[14]),
            _safe_id(row[15]),
            _safe_id(row[16]),
            _string(row[17]),
            _safe_id(row[18]),
            _safe_id(row[19]),
            _safe_id(row[20]),
            _string(row[21]),
            _string(row[22]),
            _safe_id(row[23]),
            _string(row[24]),
            _string(row[25]),
        )
        expected_authorization = (
            authorization_event_id,
            occurred_at,
            actor_subject,
            scope.client_id,
            actor_type.value,
            scope.tenant_id,
            scope.tenant_id,
            scope.tenant_id,
            scope.operation,
            expected_target,
            correlation_id,
            "ALLOW",
            "ALLOWED",
        )
        if authorization_values != expected_authorization:
            raise _invalid()
        return audit.reverify()

    def _run_write[T](self, operation: Callable[[_ConnectionLike], T]) -> T:
        connection = self._open_connection()
        result: T | None = None
        failure: (
            ScreeningSubmissionPersistenceFailure
            | ScreeningSubmissionCommitOutcomeUnknown
            | None
        ) = None
        statement_failed = False
        try:
            result = operation(connection)
        except Exception:
            statement_failed = True
        if statement_failed:
            self._rollback_quietly(connection)
            failure = _invalid()
        else:
            commit_failed = False
            try:
                connection.commit()
            except Exception:
                commit_failed = True
            if commit_failed:
                if self._transaction_status_unknown(connection):
                    failure = ScreeningSubmissionCommitOutcomeUnknown()
                else:
                    self._rollback_quietly(connection)
                    failure = _invalid()
        close_failed = not self._close_quietly(connection)
        if close_failed and failure is None:
            failure = _invalid()
        if failure is not None:
            raise failure
        return cast(T, result)

    def _run_read[T](self, operation: Callable[[_ConnectionLike], T]) -> T:
        connection = self._open_connection()
        result: T | None = None
        operation_failed = False
        try:
            connection.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            result = operation(connection)
        except Exception:
            operation_failed = True
        rollback_failed = not self._rollback_quietly(connection)
        close_failed = not self._close_quietly(connection)
        if operation_failed or rollback_failed or close_failed:
            raise _invalid()
        return cast(T, result)

    def _open_connection(self) -> _ConnectionLike:
        candidate = self._connection_factory()
        structurally_valid = False
        try:
            structurally_valid = all(
                callable(getattr(candidate, method, None))
                for method in ("execute", "commit", "rollback", "close")
            ) and hasattr(candidate, "info")
        except Exception:
            structurally_valid = False
        connection = cast(_ConnectionLike, candidate)
        initially_idle = False
        if structurally_valid:
            property_failed = False
            autocommit: object = None
            status: object = None
            try:
                autocommit = connection.autocommit
                status = connection.info.transaction_status
            except Exception:
                property_failed = True
            initially_idle = (
                not property_failed
                and type(autocommit) is bool
                and autocommit is False
                and type(status) is TransactionStatus
                and status is TransactionStatus.IDLE
            )
        if not structurally_valid or not initially_idle:
            try:
                close = getattr(candidate, "close", None)
                if callable(close):
                    close()
            except Exception:
                pass
            raise _invalid()
        return connection

    @staticmethod
    def _rollback_quietly(connection: _ConnectionLike) -> bool:
        failed = False
        try:
            connection.rollback()
        except Exception:
            failed = True
        return not failed

    @staticmethod
    def _close_quietly(connection: _ConnectionLike) -> bool:
        failed = False
        try:
            connection.close()
        except Exception:
            failed = True
        return not failed

    @staticmethod
    def _transaction_status_unknown(connection: _ConnectionLike) -> bool:
        status: object = None
        failed = False
        try:
            status = connection.info.transaction_status
        except Exception:
            failed = True
        return (
            not failed
            and type(status) is TransactionStatus
            and (status is TransactionStatus.UNKNOWN)
        )

    @staticmethod
    def _fetch_rows(
        connection: _ConnectionLike,
        query: str,
        params: tuple[object, ...],
        *,
        maximum: int,
    ) -> tuple[Row, ...]:
        cursor = connection.execute(query, params)
        fetchall = getattr(cursor, "fetchall", None)
        if not callable(fetchall):
            raise _invalid()
        rows = fetchall()
        if type(rows) is not list or len(rows) > maximum:
            raise _invalid()
        if any(type(row) is not tuple for row in rows):
            raise _invalid()
        return tuple(cast(list[Row], rows))

    @staticmethod
    def _execute_one(
        connection: _ConnectionLike,
        query: str,
        params: tuple[object, ...],
    ) -> None:
        cursor = connection.execute(query, params)
        rowcount = getattr(cursor, "rowcount", None)
        if type(rowcount) is not int or rowcount != 1:
            raise _invalid()


__all__ = [
    "MAX_ATTEMPTS",
    "MAX_ATTEMPT_QUERY",
    "PostgresScreeningSubmissionUnitOfWork",
]
