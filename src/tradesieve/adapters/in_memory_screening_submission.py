"""Atomic in-memory B2 screening-submission unit of work."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from threading import RLock
from typing import cast

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
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CONTENT_HASH = re.compile(r"^sha256:[a-f0-9]{64}$")


class ScreeningSubmissionFaultPoint(StrEnum):
    INTAKE = "INTAKE"
    SCREENING = "SCREENING"
    IDEMPOTENCY = "IDEMPOTENCY"
    AUDIT = "AUDIT"
    OUTBOX = "OUTBOX"
    UNKNOWN_BEFORE_SWAP = "UNKNOWN_BEFORE_SWAP"
    UNKNOWN_AFTER_SWAP = "UNKNOWN_AFTER_SWAP"


class ScreeningSubmissionStore(StrEnum):
    INTAKE = "INTAKE"
    SCREENING = "SCREENING"
    IDEMPOTENCY = "IDEMPOTENCY"
    AUDIT = "AUDIT"
    OUTBOX = "OUTBOX"


type _IntakeStore = dict[str, CanonicalScreeningIntake]
type _ScreeningStore = dict[str, ScreeningIdentity]


@dataclass(frozen=True, slots=True)
class _LedgerEnvelope:
    result: ScreeningIdempotencyResult = field(repr=False)
    attempt_ids: tuple[str, ...] = field(repr=False)
    attempt_chain_hash: str = field(repr=False)


type _ResultStore = dict[str, _LedgerEnvelope]
type _AuditStore = dict[str, ScreeningAttemptAudit]
type _OutboxStore = dict[str, ScreeningAcceptedOutboxIntent]


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
        failure = ScreeningSubmissionPersistenceFailure()
    if failure is not None:
        raise failure
    return cast(T, result)


def _invalid() -> ScreeningSubmissionPersistenceFailure:
    return ScreeningSubmissionPersistenceFailure()


def _attempt_chain_hash(attempt_ids: tuple[str, ...]) -> str:
    payload = json.dumps(
        attempt_ids,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _envelope(
    result: ScreeningIdempotencyResult,
    attempt_ids: tuple[str, ...],
) -> _LedgerEnvelope:
    return _LedgerEnvelope(
        result=result.reverify(),
        attempt_ids=attempt_ids,
        attempt_chain_hash=_attempt_chain_hash(attempt_ids),
    )


class InMemoryScreeningSubmissionUnitOfWork:
    """RLock-serialized copy-then-swap reference transaction boundary."""

    def __init__(self) -> None:
        self._intakes: _IntakeStore = {}
        self._screenings: _ScreeningStore = {}
        self._results: _ResultStore = {}
        self._attempts: _AuditStore = {}
        self._outbox: _OutboxStore = {}
        self._faults: set[ScreeningSubmissionFaultPoint] = set()
        self._lock = RLock()

    def inject_fault_once(self, point: ScreeningSubmissionFaultPoint) -> None:
        def inject() -> None:
            if type(point) is not ScreeningSubmissionFaultPoint:
                raise _invalid()
            with self._lock:
                self._faults.add(point)

        _persistence_boundary(inject)

    def corrupt_remove_for_test(
        self, store: ScreeningSubmissionStore, key: str
    ) -> None:
        def corrupt() -> None:
            with self._lock:
                mapping, attribute = self._store_for_test(store)
                changed = dict(mapping)
                changed.pop(key, None)
                object.__setattr__(self, attribute, changed)

        _persistence_boundary(corrupt)

    def corrupt_replace_for_test(
        self,
        store: ScreeningSubmissionStore,
        key: str,
        value: object,
    ) -> None:
        def corrupt() -> None:
            with self._lock:
                mapping, attribute = self._store_for_test(store)
                changed = dict(mapping)
                changed[key] = copy.deepcopy(value)
                object.__setattr__(self, attribute, changed)

        _persistence_boundary(corrupt)

    def stored_counts_for_test(self) -> tuple[int, int, int, int, int]:
        with self._lock:
            return (
                len(self._intakes),
                len(self._screenings),
                len(self._results),
                len(self._attempts),
                len(self._outbox),
            )

    def submit_atomic(
        self, request: ScreeningSubmissionAtomicWrite
    ) -> ScreeningSubmissionWriteResult:
        return _persistence_boundary(lambda: self._submit_unwrapped(request))

    def read_result(
        self, scope: ScreeningIdempotencyScope
    ) -> ScreeningIdempotencyResult | None:
        return _persistence_boundary(lambda: self._read_unwrapped(scope))

    def resolve_attempt(
        self,
        scope: ScreeningIdempotencyScope,
        canonical_hash: str,
        attempt_id: str,
    ) -> ScreeningSubmissionWriteResult | None:
        return _persistence_boundary(
            lambda: self._resolve_unwrapped(scope, canonical_hash, attempt_id)
        )

    def _submit_unwrapped(
        self, request: ScreeningSubmissionAtomicWrite
    ) -> ScreeningSubmissionWriteResult:
        if type(request) is not ScreeningSubmissionAtomicWrite:
            raise _invalid()
        with self._lock:
            verified_request = request.reverify()
            self._verify_state(
                self._intakes,
                self._screenings,
                self._results,
                self._attempts,
                self._outbox,
            )
            existing_attempt = self._attempts.get(verified_request.attempt_id)
            if existing_attempt is not None:
                return self._exact_duplicate(verified_request, existing_attempt)
            if verified_request.attempt_id in self._allocated_ids():
                raise _invalid()

            scope = verified_request.bound.scope
            ledger_key = scope.fingerprint()
            existing_envelope = self._results.get(ledger_key)
            if existing_envelope is None:
                return self._apply_new(verified_request)
            return self._append_existing(verified_request, existing_envelope)

    def _apply_new(
        self, request: ScreeningSubmissionAtomicWrite
    ) -> ScreeningSubmissionWriteResult:
        proposed = request.proposed_result.reverify()
        canonical_intake = request.bound._verified_intake_for_persistence()
        applied = request.bound.attempt_audit(
            attempt_id=request.attempt_id,
            outcome=ScreeningAttemptOutcome.APPLIED,
            occurred_at=request.occurred_at,
            result=proposed,
        )
        proposed_ids = {
            proposed.intake.intake_id,
            proposed.screening.screening_id,
            proposed.outbox.event_id,
            request.attempt_id,
        }
        if len(proposed_ids) != 4 or proposed_ids & self._allocated_ids():
            raise _invalid()

        intakes = dict(self._intakes)
        screenings = dict(self._screenings)
        results = dict(self._results)
        attempts = dict(self._attempts)
        outbox = dict(self._outbox)

        intakes[proposed.intake.intake_id] = canonical_intake.reverify()
        self._raise_staging_fault(ScreeningSubmissionFaultPoint.INTAKE)
        screenings[proposed.screening.screening_id] = proposed.screening.reverify()
        self._raise_staging_fault(ScreeningSubmissionFaultPoint.SCREENING)
        results[proposed.scope.fingerprint()] = _envelope(
            proposed, (applied.attempt_id,)
        )
        self._raise_staging_fault(ScreeningSubmissionFaultPoint.IDEMPOTENCY)
        attempts[applied.attempt_id] = applied.reverify()
        self._raise_staging_fault(ScreeningSubmissionFaultPoint.AUDIT)
        outbox[proposed.outbox.event_id] = proposed.outbox.reverify()
        self._raise_staging_fault(ScreeningSubmissionFaultPoint.OUTBOX)

        self._verify_state(intakes, screenings, results, attempts, outbox)
        self._raise_unknown_fault(ScreeningSubmissionFaultPoint.UNKNOWN_BEFORE_SWAP)
        self._swap(intakes, screenings, results, attempts, outbox)
        self._raise_unknown_fault(ScreeningSubmissionFaultPoint.UNKNOWN_AFTER_SWAP)
        return self._write_result(applied)

    def _append_existing(
        self,
        request: ScreeningSubmissionAtomicWrite,
        existing_envelope: _LedgerEnvelope,
    ) -> ScreeningSubmissionWriteResult:
        result = existing_envelope.result.reverify()
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
        intakes = dict(self._intakes)
        screenings = dict(self._screenings)
        results = dict(self._results)
        attempts = dict(self._attempts)
        outbox = dict(self._outbox)
        results[result.scope.fingerprint()] = _envelope(
            result, (*existing_envelope.attempt_ids, audit.attempt_id)
        )
        self._raise_staging_fault(ScreeningSubmissionFaultPoint.IDEMPOTENCY)
        attempts[audit.attempt_id] = audit.reverify()
        self._raise_staging_fault(ScreeningSubmissionFaultPoint.AUDIT)
        self._verify_state(intakes, screenings, results, attempts, outbox)
        self._raise_unknown_fault(ScreeningSubmissionFaultPoint.UNKNOWN_BEFORE_SWAP)
        self._swap(intakes, screenings, results, attempts, outbox)
        self._raise_unknown_fault(ScreeningSubmissionFaultPoint.UNKNOWN_AFTER_SWAP)
        return self._write_result(audit)

    def _exact_duplicate(
        self,
        request: ScreeningSubmissionAtomicWrite,
        existing_attempt: ScreeningAttemptAudit,
    ) -> ScreeningSubmissionWriteResult:
        stored = existing_attempt.reverify()
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
        return self._write_result(stored)

    def _read_unwrapped(
        self, scope: ScreeningIdempotencyScope
    ) -> ScreeningIdempotencyResult | None:
        verified_scope = self._verified_scope(scope)
        with self._lock:
            self._verify_state(
                self._intakes,
                self._screenings,
                self._results,
                self._attempts,
                self._outbox,
            )
            envelope = self._results.get(verified_scope.fingerprint())
            return None if envelope is None else envelope.result.reverify()

    def _resolve_unwrapped(
        self,
        scope: ScreeningIdempotencyScope,
        canonical_hash: str,
        attempt_id: str,
    ) -> ScreeningSubmissionWriteResult | None:
        verified_scope = self._verified_scope(scope)
        if (
            type(canonical_hash) is not str
            or _CONTENT_HASH.fullmatch(canonical_hash) is None
            or type(attempt_id) is not str
            or _SAFE_ID.fullmatch(attempt_id) is None
        ):
            raise _invalid()
        with self._lock:
            self._verify_state(
                self._intakes,
                self._screenings,
                self._results,
                self._attempts,
                self._outbox,
            )
            audit = self._attempts.get(attempt_id)
            if (
                audit is None
                or audit.scope != verified_scope
                or audit.canonical_hash != canonical_hash
            ):
                return None
            return self._write_result(audit)

    @staticmethod
    def _verified_scope(scope: ScreeningIdempotencyScope) -> ScreeningIdempotencyScope:
        if type(scope) is not ScreeningIdempotencyScope:
            raise _invalid()
        return scope.reverify()

    @staticmethod
    def _write_result(audit: ScreeningAttemptAudit) -> ScreeningSubmissionWriteResult:
        verified = audit.reverify()
        return ScreeningSubmissionWriteResult(
            outcome=verified.outcome,
            receipt=ScreeningAcceptanceReceipt.from_result(verified.result),
            audit=verified,
        )

    def _raise_staging_fault(self, point: ScreeningSubmissionFaultPoint) -> None:
        if point in self._faults:
            self._faults.remove(point)
            raise ScreeningSubmissionPersistenceFailure()

    def _raise_unknown_fault(self, point: ScreeningSubmissionFaultPoint) -> None:
        if point in self._faults:
            self._faults.remove(point)
            raise ScreeningSubmissionCommitOutcomeUnknown()

    def _allocated_ids(self) -> set[str]:
        return (
            set(self._intakes)
            | set(self._screenings)
            | set(self._attempts)
            | set(self._outbox)
        )

    def _swap(
        self,
        intakes: _IntakeStore,
        screenings: _ScreeningStore,
        results: _ResultStore,
        attempts: _AuditStore,
        outbox: _OutboxStore,
    ) -> None:
        self._intakes = intakes
        self._screenings = screenings
        self._results = results
        self._attempts = attempts
        self._outbox = outbox

    def _store_for_test(
        self, store: ScreeningSubmissionStore
    ) -> tuple[dict[str, object], str]:
        if type(store) is not ScreeningSubmissionStore:
            raise _invalid()
        if store is ScreeningSubmissionStore.INTAKE:
            return cast(dict[str, object], self._intakes), "_intakes"
        if store is ScreeningSubmissionStore.SCREENING:
            return cast(dict[str, object], self._screenings), "_screenings"
        if store is ScreeningSubmissionStore.IDEMPOTENCY:
            return cast(dict[str, object], self._results), "_results"
        if store is ScreeningSubmissionStore.AUDIT:
            return cast(dict[str, object], self._attempts), "_attempts"
        return cast(dict[str, object], self._outbox), "_outbox"

    @staticmethod
    def _verify_state(
        intakes: _IntakeStore,
        screenings: _ScreeningStore,
        results: _ResultStore,
        attempts: _AuditStore,
        outbox: _OutboxStore,
    ) -> None:
        if any(
            type(store) is not dict
            for store in (intakes, screenings, results, attempts, outbox)
        ):
            raise _invalid()

        allocated_key_sets = (
            set(intakes),
            set(screenings),
            set(attempts),
            set(outbox),
        )
        if any(
            type(key) is not str or _SAFE_ID.fullmatch(key) is None
            for keys in allocated_key_sets
            for key in keys
        ) or sum(len(keys) for keys in allocated_key_sets) != len(
            set().union(*allocated_key_sets)
        ):
            raise _invalid()

        verified_results: dict[str, ScreeningIdempotencyResult] = {}
        intake_ids: set[str] = set()
        screening_ids: set[str] = set()
        outbox_ids: set[str] = set()
        for ledger_key, envelope in results.items():
            if type(ledger_key) is not str or type(envelope) is not _LedgerEnvelope:
                raise _invalid()
            result = envelope.result.reverify()
            if ledger_key != result.scope.fingerprint():
                raise _invalid()
            stored_intake = intakes.get(result.intake.intake_id)
            stored_screening = screenings.get(result.screening.screening_id)
            stored_outbox = outbox.get(result.outbox.event_id)
            if (
                type(stored_intake) is not CanonicalScreeningIntake
                or type(stored_screening) is not ScreeningIdentity
                or type(stored_outbox) is not ScreeningAcceptedOutboxIntent
            ):
                raise _invalid()
            canonical_intake = stored_intake.reverify()
            context = canonical_intake.context
            accepted = AcceptedIntakeReference(
                intake_id=result.intake.intake_id,
                tenant_id=context.tenant_id,
                client_id=context.actor.client_id,
                actor_subject=context.actor.subject,
                actor_type=ScreeningActorType(context.actor.actor_type.value),
                correlation_id=context.correlation_id,
                scope_fingerprint=result.scope.fingerprint(),
                schema_version=canonical_intake.schema_version,
                media_type=canonical_intake.media_type,
                byte_length=canonical_intake.byte_length,
                byte_hash=canonical_intake.byte_hash,
                canonical_hash=canonical_intake.canonical_hash,
                received_at=canonical_intake.received_at,
            )
            if (
                accepted != result.intake
                or stored_screening.reverify() != result.screening
                or stored_outbox.reverify() != result.outbox
            ):
                raise _invalid()
            verified_results[ledger_key] = result
            intake_ids.add(result.intake.intake_id)
            screening_ids.add(result.screening.screening_id)
            outbox_ids.add(result.outbox.event_id)

        if (
            len(intake_ids) != len(results)
            or len(screening_ids) != len(results)
            or len(outbox_ids) != len(results)
            or set(intakes) != intake_ids
            or set(screenings) != screening_ids
            or set(outbox) != outbox_ids
        ):
            raise _invalid()

        indexed_attempt_ids: list[str] = []
        for ledger_key, envelope in results.items():
            if (
                type(envelope.attempt_ids) is not tuple
                or not envelope.attempt_ids
                or any(
                    type(attempt_id) is not str
                    or _SAFE_ID.fullmatch(attempt_id) is None
                    for attempt_id in envelope.attempt_ids
                )
                or len(set(envelope.attempt_ids)) != len(envelope.attempt_ids)
                or type(envelope.attempt_chain_hash) is not str
                or envelope.attempt_chain_hash
                != _attempt_chain_hash(envelope.attempt_ids)
            ):
                raise _invalid()
            indexed_attempt_ids.extend(envelope.attempt_ids)
            for position, attempt_id in enumerate(envelope.attempt_ids):
                stored_audit = attempts.get(attempt_id)
                if type(stored_audit) is not ScreeningAttemptAudit:
                    raise _invalid()
                audit = stored_audit.reverify()
                if (
                    attempt_id != audit.attempt_id
                    or audit.scope.fingerprint() != ledger_key
                    or audit.result != verified_results[ledger_key]
                    or (position == 0)
                    != (audit.outcome is ScreeningAttemptOutcome.APPLIED)
                ):
                    raise _invalid()
        if len(indexed_attempt_ids) != len(set(indexed_attempt_ids)) or set(
            attempts
        ) != set(indexed_attempt_ids):
            raise _invalid()


__all__ = [
    "InMemoryScreeningSubmissionUnitOfWork",
    "ScreeningSubmissionFaultPoint",
    "ScreeningSubmissionStore",
]
