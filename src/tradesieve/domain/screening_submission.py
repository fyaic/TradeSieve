"""Immutable B1 idempotency and accepted-screening records.

The domain owns no key parsing, authorization types, persistence, clocks, or ID
allocation. Every opaque business ID is injected by an outer application boundary.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

SAFE_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SAFE_OPERATION: Final = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
CONTENT_HASH: Final = re.compile(r"^sha256:[a-f0-9]{64}$")
SCHEMA_VERSION: Final = "1.0.0"
JSON_MEDIA_TYPE: Final = "application/json"
SCREENING_SUBMIT_OPERATION: Final = "SCREENING_SUBMIT"


class ScreeningSubmissionIntegrityError(Exception):
    """Stable failure without record, scope, hash, or attribution disclosure."""

    def __init__(self) -> None:
        super().__init__("screening submission record failed integrity validation")


class ScreeningAttemptOutcome(StrEnum):
    APPLIED = "APPLIED"
    REPLAY = "REPLAY"
    CONFLICT = "CONFLICT"


class ScreeningActorType(StrEnum):
    HUMAN = "HUMAN"
    SERVICE = "SERVICE"
    AGENT = "AGENT"


class ScreeningOutboxEventType(StrEnum):
    SCREENING_ACCEPTED = "screening.accepted"


class ScreeningReceiptType(StrEnum):
    SCREENING_ACCEPTED = "SCREENING_ACCEPTED"


def _invalid() -> ScreeningSubmissionIntegrityError:
    return ScreeningSubmissionIntegrityError()


def _require_id(value: object) -> str:
    if type(value) is not str or SAFE_ID.fullmatch(value) is None:
        raise _invalid()
    return value


def _require_operation(value: object) -> str:
    if type(value) is not str or SAFE_OPERATION.fullmatch(value) is None:
        raise _invalid()
    return value


def _require_hash(value: object) -> str:
    if type(value) is not str or CONTENT_HASH.fullmatch(value) is None:
        raise _invalid()
    return value


def _require_schema(value: object) -> str:
    if type(value) is not str or value != SCHEMA_VERSION:
        raise _invalid()
    return value


def _require_utc(value: object) -> datetime:
    if type(value) is not datetime or value.tzinfo is not UTC:
        raise _invalid()
    return value


def _require_exact[T](value: object, expected: type[T]) -> T:
    if type(value) is not expected:
        raise _invalid()
    return value


@dataclass(frozen=True, slots=True)
class ScreeningIdempotencyScope:
    tenant_id: str = field(repr=False)
    client_id: str = field(repr=False)
    operation: str
    key_digest: str = field(repr=False)

    def __post_init__(self) -> None:
        _require_id(self.tenant_id)
        _require_id(self.client_id)
        _require_operation(self.operation)
        _require_hash(self.key_digest)

    def reverify(self) -> ScreeningIdempotencyScope:
        return type(self)(
            tenant_id=self.tenant_id,
            client_id=self.client_id,
            operation=self.operation,
            key_digest=self.key_digest,
        )

    def fingerprint(self) -> str:
        verified = self.reverify()
        payload = json.dumps(
            {
                "tenant_id": verified.tenant_id,
                "client_id": verified.client_id,
                "operation": verified.operation,
                "key_digest": verified.key_digest,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return f"sha256:{hashlib.sha256(payload).hexdigest()}"


@dataclass(frozen=True, slots=True)
class AcceptedIntakeReference:
    intake_id: str
    tenant_id: str = field(repr=False)
    client_id: str = field(repr=False)
    actor_subject: str = field(repr=False)
    actor_type: ScreeningActorType = field(repr=False)
    correlation_id: str = field(repr=False)
    scope_fingerprint: str = field(repr=False)
    schema_version: str
    media_type: str = field(repr=False)
    byte_length: int = field(repr=False)
    byte_hash: str = field(repr=False)
    canonical_hash: str = field(repr=False)
    received_at: datetime

    def __post_init__(self) -> None:
        _require_id(self.intake_id)
        _require_id(self.tenant_id)
        _require_id(self.client_id)
        _require_id(self.actor_subject)
        _require_exact(self.actor_type, ScreeningActorType)
        _require_id(self.correlation_id)
        _require_hash(self.scope_fingerprint)
        _require_schema(self.schema_version)
        if type(self.media_type) is not str or self.media_type != JSON_MEDIA_TYPE:
            raise _invalid()
        if type(self.byte_length) is not int or not 1 <= self.byte_length <= 1_048_576:
            raise _invalid()
        _require_hash(self.byte_hash)
        _require_hash(self.canonical_hash)
        _require_utc(self.received_at)

    def reverify(self) -> AcceptedIntakeReference:
        return type(self)(
            intake_id=self.intake_id,
            tenant_id=self.tenant_id,
            client_id=self.client_id,
            actor_subject=self.actor_subject,
            actor_type=self.actor_type,
            correlation_id=self.correlation_id,
            scope_fingerprint=self.scope_fingerprint,
            schema_version=self.schema_version,
            media_type=self.media_type,
            byte_length=self.byte_length,
            byte_hash=self.byte_hash,
            canonical_hash=self.canonical_hash,
            received_at=self.received_at,
        )


@dataclass(frozen=True, slots=True)
class ScreeningIdentity:
    screening_id: str
    intake: AcceptedIntakeReference = field(repr=False)
    created_at: datetime

    def __post_init__(self) -> None:
        _require_id(self.screening_id)
        verified_intake = _require_exact(
            self.intake, AcceptedIntakeReference
        ).reverify()
        _require_utc(self.created_at)
        if self.created_at < verified_intake.received_at:
            raise _invalid()
        object.__setattr__(self, "intake", verified_intake)

    def reverify(self) -> ScreeningIdentity:
        return type(self)(
            screening_id=self.screening_id,
            intake=self.intake,
            created_at=self.created_at,
        )


@dataclass(frozen=True, slots=True)
class ScreeningAcceptedOutboxIntent:
    event_id: str
    event_type: ScreeningOutboxEventType
    schema_version: str
    occurred_at: datetime
    intake_id: str
    screening_id: str

    def __post_init__(self) -> None:
        _require_id(self.event_id)
        _require_exact(self.event_type, ScreeningOutboxEventType)
        _require_schema(self.schema_version)
        _require_utc(self.occurred_at)
        _require_id(self.intake_id)
        _require_id(self.screening_id)

    def reverify(self) -> ScreeningAcceptedOutboxIntent:
        return type(self)(
            event_id=self.event_id,
            event_type=self.event_type,
            schema_version=self.schema_version,
            occurred_at=self.occurred_at,
            intake_id=self.intake_id,
            screening_id=self.screening_id,
        )


@dataclass(frozen=True, slots=True)
class ScreeningIdempotencyResult:
    scope: ScreeningIdempotencyScope = field(repr=False)
    canonical_hash: str = field(repr=False)
    intake: AcceptedIntakeReference = field(repr=False)
    screening: ScreeningIdentity = field(repr=False)
    outbox: ScreeningAcceptedOutboxIntent = field(repr=False)
    committed_at: datetime

    def __post_init__(self) -> None:
        verified_scope = _require_exact(
            self.scope, ScreeningIdempotencyScope
        ).reverify()
        _require_hash(self.canonical_hash)
        verified_intake = _require_exact(
            self.intake, AcceptedIntakeReference
        ).reverify()
        verified_screening = _require_exact(
            self.screening, ScreeningIdentity
        ).reverify()
        verified_outbox = _require_exact(
            self.outbox, ScreeningAcceptedOutboxIntent
        ).reverify()
        _require_utc(self.committed_at)
        if (
            self.canonical_hash != verified_intake.canonical_hash
            or verified_scope.tenant_id != verified_intake.tenant_id
            or verified_scope.client_id != verified_intake.client_id
            or verified_scope.operation != SCREENING_SUBMIT_OPERATION
            or verified_scope.fingerprint() != verified_intake.scope_fingerprint
            or verified_screening.intake != verified_intake
            or verified_screening.created_at != self.committed_at
            or verified_outbox.occurred_at != self.committed_at
            or verified_outbox.intake_id != verified_intake.intake_id
            or verified_outbox.screening_id != verified_screening.screening_id
        ):
            raise _invalid()
        object.__setattr__(self, "scope", verified_scope)
        object.__setattr__(self, "intake", verified_intake)
        object.__setattr__(self, "screening", verified_screening)
        object.__setattr__(self, "outbox", verified_outbox)

    def reverify(self) -> ScreeningIdempotencyResult:
        return type(self)(
            scope=self.scope,
            canonical_hash=self.canonical_hash,
            intake=self.intake,
            screening=self.screening,
            outbox=self.outbox,
            committed_at=self.committed_at,
        )


@dataclass(frozen=True, slots=True)
class ScreeningAttemptAudit:
    attempt_id: str
    outcome: ScreeningAttemptOutcome
    occurred_at: datetime
    scope: ScreeningIdempotencyScope = field(repr=False)
    actor_type: ScreeningActorType = field(repr=False)
    actor_subject: str = field(repr=False)
    correlation_id: str = field(repr=False)
    authorization_event_id: str = field(repr=False)
    byte_hash: str = field(repr=False)
    canonical_hash: str = field(repr=False)
    result: ScreeningIdempotencyResult = field(repr=False)

    def __post_init__(self) -> None:
        _require_id(self.attempt_id)
        _require_exact(self.outcome, ScreeningAttemptOutcome)
        _require_utc(self.occurred_at)
        verified_scope = _require_exact(
            self.scope, ScreeningIdempotencyScope
        ).reverify()
        _require_exact(self.actor_type, ScreeningActorType)
        _require_id(self.actor_subject)
        _require_id(self.correlation_id)
        _require_id(self.authorization_event_id)
        _require_hash(self.byte_hash)
        _require_hash(self.canonical_hash)
        verified_result = _require_exact(
            self.result, ScreeningIdempotencyResult
        ).reverify()
        if verified_scope != verified_result.scope:
            raise _invalid()
        same_canonical_input = self.canonical_hash == verified_result.canonical_hash
        if self.outcome is ScreeningAttemptOutcome.APPLIED:
            if (
                self.occurred_at != verified_result.committed_at
                or self.byte_hash != verified_result.intake.byte_hash
                or not same_canonical_input
                or self.actor_type is not verified_result.intake.actor_type
                or self.actor_subject != verified_result.intake.actor_subject
                or self.correlation_id != verified_result.intake.correlation_id
            ):
                raise _invalid()
        elif self.outcome is ScreeningAttemptOutcome.REPLAY:
            if not same_canonical_input:
                raise _invalid()
        elif same_canonical_input:
            raise _invalid()
        object.__setattr__(self, "scope", verified_scope)
        object.__setattr__(self, "result", verified_result)

    def reverify(self) -> ScreeningAttemptAudit:
        return type(self)(
            attempt_id=self.attempt_id,
            outcome=self.outcome,
            occurred_at=self.occurred_at,
            scope=self.scope,
            actor_type=self.actor_type,
            actor_subject=self.actor_subject,
            correlation_id=self.correlation_id,
            authorization_event_id=self.authorization_event_id,
            byte_hash=self.byte_hash,
            canonical_hash=self.canonical_hash,
            result=self.result,
        )


@dataclass(frozen=True, slots=True)
class ScreeningAcceptanceReceipt:
    receipt_type: ScreeningReceiptType
    schema_version: str
    accepted_at: datetime
    intake_id: str
    screening_id: str
    outbox_event_id: str

    def __post_init__(self) -> None:
        _require_exact(self.receipt_type, ScreeningReceiptType)
        _require_schema(self.schema_version)
        _require_utc(self.accepted_at)
        _require_id(self.intake_id)
        _require_id(self.screening_id)
        _require_id(self.outbox_event_id)

    @classmethod
    def from_result(
        cls, result: ScreeningIdempotencyResult
    ) -> ScreeningAcceptanceReceipt:
        verified = _require_exact(result, ScreeningIdempotencyResult).reverify()
        return cls(
            receipt_type=ScreeningReceiptType.SCREENING_ACCEPTED,
            schema_version=SCHEMA_VERSION,
            accepted_at=verified.committed_at,
            intake_id=verified.intake.intake_id,
            screening_id=verified.screening.screening_id,
            outbox_event_id=verified.outbox.event_id,
        )

    def reverify(self) -> ScreeningAcceptanceReceipt:
        return type(self)(
            receipt_type=self.receipt_type,
            schema_version=self.schema_version,
            accepted_at=self.accepted_at,
            intake_id=self.intake_id,
            screening_id=self.screening_id,
            outbox_event_id=self.outbox_event_id,
        )


__all__ = [
    "AcceptedIntakeReference",
    "JSON_MEDIA_TYPE",
    "SCHEMA_VERSION",
    "SCREENING_SUBMIT_OPERATION",
    "ScreeningAcceptanceReceipt",
    "ScreeningAcceptedOutboxIntent",
    "ScreeningActorType",
    "ScreeningAttemptAudit",
    "ScreeningAttemptOutcome",
    "ScreeningIdempotencyResult",
    "ScreeningIdempotencyScope",
    "ScreeningIdentity",
    "ScreeningOutboxEventType",
    "ScreeningReceiptType",
    "ScreeningSubmissionIntegrityError",
]
