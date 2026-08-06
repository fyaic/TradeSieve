"""Immutable source artifacts, deterministic validation/diff, and lifecycle state."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Final

SAFE_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SAFE_FIELD: Final = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
SAFE_MEDIA_TYPE: Final = re.compile(
    r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,63}/[a-z0-9][a-z0-9!#$&^_.+-]{0,63}$"
)
SAFE_CHARSET: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
CONTENT_HASH: Final = re.compile(r"^sha256:[a-f0-9]{64}$")
SEMANTIC_VERSION: Final = re.compile(
    r"^(?:0|[1-9][0-9]{0,8})\."
    r"(?:0|[1-9][0-9]{0,8})\."
    r"(?:0|[1-9][0-9]{0,8})$"
)
MAX_RAW_OBJECT_BYTES: Final = 1_048_576
MAX_RECORDS_PER_SNAPSHOT: Final = 512
MAX_ASSERTIONS_PER_RECORD: Final = 64
MAX_NATIVE_VALUE_BYTES: Final = 4096
MAX_CANONICAL_SNAPSHOT_BYTES: Final = 1_048_576
MAX_LOCATOR_LENGTH: Final = 512
MAX_ORIGINAL_NAME_LENGTH: Final = 256
MAX_REASON_LENGTH: Final = 2000
MAX_LIFECYCLE_EVENTS: Final = 4096
MAX_QUERY_LIMIT: Final = 512
MAX_SEQUENCE: Final = 9_223_372_036_854_775_807

type NativeScalar = str | int | bool | None


class LifecycleActorType(StrEnum):
    HUMAN = "HUMAN"
    SERVICE = "SERVICE"
    AGENT = "AGENT"


class SourceSnapshotEventType(StrEnum):
    RETRIEVED = "RETRIEVED"
    QUARANTINED = "QUARANTINED"
    PARSED = "PARSED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    ACTIVATED = "ACTIVATED"
    ROLLED_BACK = "ROLLED_BACK"


class RawObjectState(StrEnum):
    RETRIEVED = "RETRIEVED"
    QUARANTINED = "QUARANTINED"
    PARSED = "PARSED"


class SourceSnapshotState(StrEnum):
    PARSED = "PARSED"
    QUARANTINED = "QUARANTINED"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    ROLLED_BACK = "ROLLED_BACK"


class ValidationReasonCode(StrEnum):
    SNAPSHOT_HASH_MISMATCH = "SNAPSHOT_HASH_MISMATCH"
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
    DECLARED_COUNT_MISMATCH = "DECLARED_COUNT_MISMATCH"
    EMPTY_SNAPSHOT = "EMPTY_SNAPSHOT"
    EMPTY_RECORD_ASSERTIONS = "EMPTY_RECORD_ASSERTIONS"
    MISSING_RECORD_ID = "MISSING_RECORD_ID"
    INVALID_RECORD_ID = "INVALID_RECORD_ID"
    DUPLICATE_RECORD_ID = "DUPLICATE_RECORD_ID"
    DUPLICATE_LOCATOR = "DUPLICATE_LOCATOR"
    INVALID_EFFECTIVE_DATE = "INVALID_EFFECTIVE_DATE"
    INVALID_EFFECTIVE_RANGE = "INVALID_EFFECTIVE_RANGE"
    UNEXPECTED_DELETION = "UNEXPECTED_DELETION"


class ArtifactWriteOutcome(StrEnum):
    APPLIED = "APPLIED"
    IDEMPOTENT = "IDEMPOTENT"
    CONFLICT = "CONFLICT"


class LifecycleWriteOutcome(StrEnum):
    APPLIED = "APPLIED"
    IDEMPOTENT = "IDEMPOTENT"
    CONFLICT = "CONFLICT"


class SnapshotCommandOutcome(StrEnum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


class SnapshotCommandReason(StrEnum):
    RETRIEVED = "RETRIEVED"
    RETRIEVE_IDEMPOTENT = "RETRIEVE_IDEMPOTENT"
    QUARANTINED = "QUARANTINED"
    QUARANTINE_IDEMPOTENT = "QUARANTINE_IDEMPOTENT"
    PARSED = "PARSED"
    PARSE_IDEMPOTENT = "PARSE_IDEMPOTENT"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    VALIDATION_FAILURE_IDEMPOTENT = "VALIDATION_FAILURE_IDEMPOTENT"
    VALIDATED = "VALIDATED"
    VALIDATE_IDEMPOTENT = "VALIDATE_IDEMPOTENT"
    APPROVED = "APPROVED"
    APPROVE_IDEMPOTENT = "APPROVE_IDEMPOTENT"
    ACTIVATED = "ACTIVATED"
    ACTIVATE_IDEMPOTENT = "ACTIVATE_IDEMPOTENT"
    ROLLED_BACK = "ROLLED_BACK"
    ROLLBACK_IDEMPOTENT = "ROLLBACK_IDEMPOTENT"
    CONFLICT = "CONFLICT"
    AUTHORIZATION_BINDING_DENIED = "AUTHORIZATION_BINDING_DENIED"
    CREATOR_SEPARATION_DENIED = "CREATOR_SEPARATION_DENIED"
    VALIDATION_BLOCKED = "VALIDATION_BLOCKED"
    INVALID_LIFECYCLE = "INVALID_LIFECYCLE"
    PERSISTENCE_FAILURE = "PERSISTENCE_FAILURE"


SUCCESS_COMMAND_REASONS: Final = frozenset(
    reason
    for reason in SnapshotCommandReason
    if reason.value.endswith("IDEMPOTENT")
    or reason
    in {
        SnapshotCommandReason.RETRIEVED,
        SnapshotCommandReason.QUARANTINED,
        SnapshotCommandReason.PARSED,
        SnapshotCommandReason.VALIDATION_FAILED,
        SnapshotCommandReason.VALIDATED,
        SnapshotCommandReason.APPROVED,
        SnapshotCommandReason.ACTIVATED,
        SnapshotCommandReason.ROLLED_BACK,
    }
)

AUTHORIZATION_OPERATION_BY_EVENT_TYPE: Final = {
    SourceSnapshotEventType.RETRIEVED: "SOURCE_SNAPSHOT_INGEST",
    SourceSnapshotEventType.QUARANTINED: "SOURCE_SNAPSHOT_INGEST",
    SourceSnapshotEventType.PARSED: "SOURCE_SNAPSHOT_PARSE",
    SourceSnapshotEventType.VALIDATION_FAILED: "SOURCE_SNAPSHOT_VALIDATE",
    SourceSnapshotEventType.VALIDATED: "SOURCE_SNAPSHOT_VALIDATE",
    SourceSnapshotEventType.APPROVED: "SOURCE_SNAPSHOT_APPROVE",
    SourceSnapshotEventType.ACTIVATED: "SOURCE_SNAPSHOT_ACTIVATE",
    SourceSnapshotEventType.ROLLED_BACK: "SOURCE_SNAPSHOT_ROLLBACK",
}

EVENT_TYPE_BY_SUCCESS_COMMAND_REASON: Final = {
    SnapshotCommandReason.RETRIEVED: SourceSnapshotEventType.RETRIEVED,
    SnapshotCommandReason.RETRIEVE_IDEMPOTENT: SourceSnapshotEventType.RETRIEVED,
    SnapshotCommandReason.QUARANTINED: SourceSnapshotEventType.QUARANTINED,
    SnapshotCommandReason.QUARANTINE_IDEMPOTENT: (SourceSnapshotEventType.QUARANTINED),
    SnapshotCommandReason.PARSED: SourceSnapshotEventType.PARSED,
    SnapshotCommandReason.PARSE_IDEMPOTENT: SourceSnapshotEventType.PARSED,
    SnapshotCommandReason.VALIDATION_FAILED: (
        SourceSnapshotEventType.VALIDATION_FAILED
    ),
    SnapshotCommandReason.VALIDATION_FAILURE_IDEMPOTENT: (
        SourceSnapshotEventType.VALIDATION_FAILED
    ),
    SnapshotCommandReason.VALIDATED: SourceSnapshotEventType.VALIDATED,
    SnapshotCommandReason.VALIDATE_IDEMPOTENT: SourceSnapshotEventType.VALIDATED,
    SnapshotCommandReason.APPROVED: SourceSnapshotEventType.APPROVED,
    SnapshotCommandReason.APPROVE_IDEMPOTENT: SourceSnapshotEventType.APPROVED,
    SnapshotCommandReason.ACTIVATED: SourceSnapshotEventType.ACTIVATED,
    SnapshotCommandReason.ACTIVATE_IDEMPOTENT: SourceSnapshotEventType.ACTIVATED,
    SnapshotCommandReason.ROLLED_BACK: SourceSnapshotEventType.ROLLED_BACK,
    SnapshotCommandReason.ROLLBACK_IDEMPOTENT: SourceSnapshotEventType.ROLLED_BACK,
}


class RawObjectIntegrityError(Exception):
    def __init__(self) -> None:
        super().__init__("raw object is unavailable or failed content verification")


class SourceSnapshotIntegrityError(Exception):
    def __init__(self) -> None:
        super().__init__("source snapshot failed identity or provenance verification")


class InvalidSourceSnapshotTransition(Exception):
    def __init__(self) -> None:
        super().__init__("invalid source snapshot lifecycle transition")


def _require_id(value: object, field: str) -> str:
    if not isinstance(value, str) or SAFE_ID.fullmatch(value) is None:
        raise ValueError(f"invalid {field}")
    return value


def _require_hash(value: object, field: str) -> str:
    if not isinstance(value, str) or CONTENT_HASH.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical SHA-256 reference")
    return value


def _require_semver(value: object, field: str) -> str:
    if not isinstance(value, str) or SEMANTIC_VERSION.fullmatch(value) is None:
        raise ValueError(f"{field} must be a strict release semantic version")
    return value


def _require_utc(value: object, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field} must be a UTC datetime")
    offset = value.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ValueError(f"{field} must be a UTC datetime")
    return value.astimezone(UTC)


def _require_text(value: object, field: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or not value.isprintable()
    ):
        raise ValueError(f"{field} must be bounded printable text")
    return value


def _require_encoded_text(value: object, field: str, maximum: int) -> str:
    text = _require_text(value, field, maximum)
    if len(text.encode("utf-8")) > maximum:
        raise ValueError(f"{field} exceeds the encoded byte limit")
    return text


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("content must be canonical JSON") from exc


def canonical_sha256(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


def bytes_sha256(value: bytes) -> str:
    if not isinstance(value, bytes):
        raise TypeError("raw content must be bytes")
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _derived_raw_object_id(
    *,
    deployment_id: str,
    source_id: str,
    original_name: str,
    media_type: str,
    charset: str,
    retrieved_at: datetime,
    effective_from: datetime | None,
    byte_length: int,
    content_hash: str,
) -> str:
    identity_hash = canonical_sha256(
        {
            "deployment_id": deployment_id,
            "source_id": source_id,
            "original_name": original_name,
            "media_type": media_type,
            "charset": charset,
            "retrieved_at": retrieved_at.isoformat(),
            "effective_from": (effective_from.isoformat() if effective_from else None),
            "byte_length": byte_length,
            "content_hash": content_hash,
        }
    )
    return f"raw-{identity_hash.removeprefix('sha256:')}"


def _canonical_native_value(value: NativeScalar) -> str:
    if isinstance(value, float) or not isinstance(value, (str, int, bool, type(None))):
        raise ValueError("native value must be a JSON scalar without floats")
    encoded = _canonical_bytes(value)
    if len(encoded) > MAX_NATIVE_VALUE_BYTES:
        raise ValueError("native value exceeds the encoded byte limit")
    return encoded.decode("utf-8")


def _require_locator(value: object, field: str) -> str:
    locator = _require_text(value, field, MAX_LOCATOR_LENGTH)
    if not locator.startswith("/") or re.search(r"~(?![01])", locator):
        raise ValueError(f"{field} must be a bounded JSON Pointer")
    return locator


@dataclass(frozen=True, slots=True, order=True)
class RawObjectRef:
    object_id: str
    content_hash: str
    byte_length: int

    def __post_init__(self) -> None:
        _require_id(self.object_id, "object_id")
        _require_hash(self.content_hash, "content_hash")
        if (
            isinstance(self.byte_length, bool)
            or not isinstance(self.byte_length, int)
            or not 0 <= self.byte_length <= MAX_RAW_OBJECT_BYTES
        ):
            raise ValueError("byte_length is outside the raw-object bound")


@dataclass(frozen=True, slots=True, init=False)
class RawObjectMetadata:
    deployment_id: str
    source_id: str
    object_id: str
    original_name: str
    media_type: str
    charset: str
    retrieved_at: datetime
    effective_from: datetime | None
    byte_length: int
    content_hash: str

    @classmethod
    def from_bytes(
        cls,
        *,
        deployment_id: str,
        source_id: str,
        original_name: str,
        media_type: str,
        charset: str,
        retrieved_at: datetime,
        effective_from: datetime | None,
        content: bytes,
    ) -> RawObjectMetadata:
        _require_id(deployment_id, "deployment_id")
        _require_id(source_id, "source_id")
        _require_text(original_name, "original_name", MAX_ORIGINAL_NAME_LENGTH)
        if (
            "/" in original_name
            or "\\" in original_name
            or original_name in {".", ".."}
        ):
            raise ValueError("original_name must be a plain object name")
        if (
            not isinstance(media_type, str)
            or SAFE_MEDIA_TYPE.fullmatch(media_type) is None
        ):
            raise ValueError("media_type must be a bounded lowercase media type")
        if not isinstance(charset, str) or SAFE_CHARSET.fullmatch(charset) is None:
            raise ValueError("charset must be a bounded charset token")
        retrieved = _require_utc(retrieved_at, "retrieved_at")
        effective = (
            _require_utc(effective_from, "effective_from")
            if effective_from is not None
            else None
        )
        if not isinstance(content, bytes):
            raise TypeError("raw content must be bytes")
        if len(content) > MAX_RAW_OBJECT_BYTES:
            raise ValueError("raw content exceeds the object-size bound")
        content_hash = bytes_sha256(content)
        instance = object.__new__(cls)
        values = {
            "deployment_id": deployment_id,
            "source_id": source_id,
            "object_id": _derived_raw_object_id(
                deployment_id=deployment_id,
                source_id=source_id,
                original_name=original_name,
                media_type=media_type,
                charset=charset,
                retrieved_at=retrieved,
                effective_from=effective,
                byte_length=len(content),
                content_hash=content_hash,
            ),
            "original_name": original_name,
            "media_type": media_type,
            "charset": charset,
            "retrieved_at": retrieved,
            "effective_from": effective,
            "byte_length": len(content),
            "content_hash": content_hash,
        }
        for field, value in values.items():
            object.__setattr__(instance, field, value)
        return instance

    def reference(self) -> RawObjectRef:
        return RawObjectRef(self.object_id, self.content_hash, self.byte_length)

    def recomputed_object_id(self) -> str:
        return _derived_raw_object_id(
            deployment_id=self.deployment_id,
            source_id=self.source_id,
            original_name=self.original_name,
            media_type=self.media_type,
            charset=self.charset,
            retrieved_at=self.retrieved_at,
            effective_from=self.effective_from,
            byte_length=self.byte_length,
            content_hash=self.content_hash,
        )


def verify_raw_bytes(metadata: RawObjectMetadata, content: bytes | None) -> bytes:
    if not isinstance(metadata, RawObjectMetadata):
        raise TypeError("raw metadata must be typed")
    try:
        identity_valid = metadata.recomputed_object_id() == metadata.object_id
    except (AttributeError, TypeError, ValueError) as exc:
        raise RawObjectIntegrityError from exc
    if (
        content is None
        or not isinstance(content, bytes)
        or not identity_valid
        or len(content) != metadata.byte_length
        or bytes_sha256(content) != metadata.content_hash
    ):
        raise RawObjectIntegrityError
    return content


@dataclass(frozen=True, slots=True)
class ParsedAssertionInput:
    field_name: str
    native_locator: str
    native_value: NativeScalar
    normalized_value: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.field_name, str)
            or SAFE_FIELD.fullmatch(self.field_name) is None
        ):
            raise ValueError("field_name must be a bounded canonical field")
        _require_locator(self.native_locator, "native_locator")
        _canonical_native_value(self.native_value)
        _require_encoded_text(
            self.normalized_value, "normalized_value", MAX_NATIVE_VALUE_BYTES
        )


@dataclass(frozen=True, slots=True)
class ParsedRecordInput:
    source_record_id: str | None
    native_locator: str
    effective_from: str | None
    effective_to: str | None
    assertions: tuple[ParsedAssertionInput, ...]

    def __post_init__(self) -> None:
        if self.source_record_id is not None:
            _require_text(self.source_record_id, "source_record_id", 128)
        _require_locator(self.native_locator, "native_locator")
        for field in ("effective_from", "effective_to"):
            value = getattr(self, field)
            if value is not None:
                _require_text(value, field, 32)
        if not isinstance(self.assertions, tuple) or any(
            not isinstance(item, ParsedAssertionInput) for item in self.assertions
        ):
            raise ValueError("assertions must be a tuple of parsed assertion inputs")
        if len(self.assertions) > MAX_ASSERTIONS_PER_RECORD:
            raise ValueError("record exceeds the assertion-count bound")


@dataclass(frozen=True, slots=True)
class SourceAssertion:
    assertion_id: str
    snapshot_id: str
    raw_object_id: str
    source_record_id: str | None
    field_name: str
    native_locator: str
    native_value_json: str
    normalized_value: str
    parser_id: str
    parser_version: str

    def __post_init__(self) -> None:
        for field in ("assertion_id", "snapshot_id", "raw_object_id", "parser_id"):
            _require_id(getattr(self, field), field)
        if self.source_record_id is not None:
            _require_text(self.source_record_id, "source_record_id", 128)
        if SAFE_FIELD.fullmatch(self.field_name) is None:
            raise ValueError("field_name must be a bounded canonical field")
        _require_locator(self.native_locator, "native_locator")
        try:
            parsed = json.loads(self.native_value_json)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError("native_value_json must contain canonical JSON") from exc
        if _canonical_native_value(parsed) != self.native_value_json:
            raise ValueError("native_value_json must contain a canonical JSON scalar")
        _require_encoded_text(
            self.normalized_value, "normalized_value", MAX_NATIVE_VALUE_BYTES
        )
        _require_semver(self.parser_version, "parser_version")

    def content_hash(self) -> str:
        content = _assertion_content(self)
        content.pop("raw_object_id")
        return canonical_sha256(content)


@dataclass(frozen=True, slots=True)
class SourceRecord:
    record_key: str
    snapshot_id: str
    raw_object_id: str
    source_record_id: str | None
    native_locator: str
    effective_from: str | None
    effective_to: str | None
    assertions: tuple[SourceAssertion, ...]

    def __post_init__(self) -> None:
        for field in ("record_key", "snapshot_id", "raw_object_id"):
            _require_id(getattr(self, field), field)
        if self.source_record_id is not None:
            _require_text(self.source_record_id, "source_record_id", 128)
        _require_locator(self.native_locator, "native_locator")
        if not isinstance(self.assertions, tuple) or any(
            not isinstance(item, SourceAssertion) for item in self.assertions
        ):
            raise ValueError("assertions must be immutable typed assertions")
        if len(self.assertions) > MAX_ASSERTIONS_PER_RECORD:
            raise ValueError("record exceeds the assertion-count bound")
        if any(
            item.snapshot_id != self.snapshot_id
            or item.raw_object_id != self.raw_object_id
            or item.source_record_id != self.source_record_id
            for item in self.assertions
        ):
            raise ValueError("assertion provenance must match its source record")

    def content_hash(self) -> str:
        content = _record_content(self)
        content.pop("raw_object_id")
        assertions = content["assertions"]
        assert isinstance(assertions, list)
        for assertion in assertions:
            assert isinstance(assertion, dict)
            assertion.pop("raw_object_id")
        return canonical_sha256(content)


@dataclass(frozen=True, slots=True, order=True)
class SourceSnapshotRef:
    snapshot_id: str
    content_hash: str

    def __post_init__(self) -> None:
        _require_id(self.snapshot_id, "snapshot_id")
        _require_hash(self.content_hash, "content_hash")
        expected = f"snapshot-{self.content_hash.removeprefix('sha256:')}"
        if self.snapshot_id != expected:
            raise ValueError("snapshot_id must be derived from its content hash")


def _derived_record_key(record_index: int, native_locator: str) -> str:
    digest = canonical_sha256(
        {"record_index": record_index, "native_locator": native_locator}
    )
    return f"record-{digest.removeprefix('sha256:')}"


def _derived_assertion_id(
    *,
    record_index: int,
    assertion_index: int,
    field_name: str,
    native_locator: str,
    native_value_json: str,
    normalized_value: str,
    parser_id: str,
    parser_version: str,
) -> str:
    digest = canonical_sha256(
        {
            "record_index": record_index,
            "assertion_index": assertion_index,
            "field_name": field_name,
            "native_locator": native_locator,
            "native_value_json": native_value_json,
            "normalized_value": normalized_value,
            "parser_id": parser_id,
            "parser_version": parser_version,
        }
    )
    return f"assertion-{digest.removeprefix('sha256:')}"


@dataclass(frozen=True, slots=True, init=False)
class ParsedSourceSnapshot:
    deployment_id: str
    source_id: str
    snapshot_id: str
    raw_object: RawObjectRef
    parser_id: str
    parser_version: str
    schema_id: str
    declared_record_count: int
    parsed_at: datetime
    records: tuple[SourceRecord, ...]
    content_hash: str

    @classmethod
    def create(
        cls,
        *,
        raw_metadata: RawObjectMetadata,
        parser_id: str,
        parser_version: str,
        schema_id: str,
        declared_record_count: int,
        parsed_at: datetime,
        records: tuple[ParsedRecordInput, ...],
    ) -> ParsedSourceSnapshot:
        if not isinstance(raw_metadata, RawObjectMetadata):
            raise TypeError("raw_metadata must be typed")
        _require_id(parser_id, "parser_id")
        _require_semver(parser_version, "parser_version")
        _require_id(schema_id, "schema_id")
        if (
            isinstance(declared_record_count, bool)
            or not isinstance(declared_record_count, int)
            or not 0 <= declared_record_count <= MAX_RECORDS_PER_SNAPSHOT
        ):
            raise ValueError("declared_record_count is outside the record bound")
        parsed = _require_utc(parsed_at, "parsed_at")
        if parsed < raw_metadata.retrieved_at:
            raise ValueError("parsed_at must not be before raw retrieval")
        if not isinstance(records, tuple) or any(
            not isinstance(item, ParsedRecordInput) for item in records
        ):
            raise ValueError("records must be a tuple of parsed record inputs")
        if len(records) > MAX_RECORDS_PER_SNAPSHOT:
            raise ValueError("snapshot exceeds the record-count bound")
        if raw_metadata.recomputed_object_id() != raw_metadata.object_id:
            raise RawObjectIntegrityError
        raw_reference = raw_metadata.reference()
        base_content = _snapshot_input_content(
            raw_metadata.deployment_id,
            raw_metadata.source_id,
            raw_reference,
            parser_id,
            parser_version,
            schema_id,
            declared_record_count,
            parsed,
            records,
        )
        if len(_canonical_bytes(base_content)) > MAX_CANONICAL_SNAPSHOT_BYTES:
            raise ValueError("snapshot exceeds the canonical payload-size bound")
        content_hash = canonical_sha256(base_content)
        snapshot_id = f"snapshot-{content_hash.removeprefix('sha256:')}"
        materialized: list[SourceRecord] = []
        for record_index, record in enumerate(records):
            assertions: list[SourceAssertion] = []
            for assertion_index, assertion in enumerate(record.assertions):
                native_value_json = _canonical_native_value(assertion.native_value)
                assertions.append(
                    SourceAssertion(
                        assertion_id=_derived_assertion_id(
                            record_index=record_index,
                            assertion_index=assertion_index,
                            field_name=assertion.field_name,
                            native_locator=assertion.native_locator,
                            native_value_json=native_value_json,
                            normalized_value=assertion.normalized_value,
                            parser_id=parser_id,
                            parser_version=parser_version,
                        ),
                        snapshot_id=snapshot_id,
                        raw_object_id=raw_reference.object_id,
                        source_record_id=record.source_record_id,
                        field_name=assertion.field_name,
                        native_locator=assertion.native_locator,
                        native_value_json=native_value_json,
                        normalized_value=assertion.normalized_value,
                        parser_id=parser_id,
                        parser_version=parser_version,
                    )
                )
            materialized.append(
                SourceRecord(
                    record_key=_derived_record_key(record_index, record.native_locator),
                    snapshot_id=snapshot_id,
                    raw_object_id=raw_reference.object_id,
                    source_record_id=record.source_record_id,
                    native_locator=record.native_locator,
                    effective_from=record.effective_from,
                    effective_to=record.effective_to,
                    assertions=tuple(assertions),
                )
            )
        instance = object.__new__(cls)
        values = {
            "deployment_id": raw_metadata.deployment_id,
            "source_id": raw_metadata.source_id,
            "snapshot_id": snapshot_id,
            "raw_object": raw_reference,
            "parser_id": parser_id,
            "parser_version": parser_version,
            "schema_id": schema_id,
            "declared_record_count": declared_record_count,
            "parsed_at": parsed,
            "records": tuple(materialized),
            "content_hash": content_hash,
        }
        for field, value in values.items():
            object.__setattr__(instance, field, value)
        return instance

    def reference(self) -> SourceSnapshotRef:
        return SourceSnapshotRef(self.snapshot_id, self.content_hash)

    def recomputed_content_hash(self) -> str:
        return canonical_sha256(_snapshot_content(self))

    def verify_integrity(self) -> None:
        recomputed_hash = self.recomputed_content_hash()
        if (
            recomputed_hash != self.content_hash
            or self.snapshot_id != f"snapshot-{recomputed_hash.removeprefix('sha256:')}"
        ):
            raise SourceSnapshotIntegrityError
        for record_index, record in enumerate(self.records):
            expected_record_identity = (
                _derived_record_key(record_index, record.native_locator),
                self.snapshot_id,
                self.raw_object.object_id,
            )
            if (
                record.record_key,
                record.snapshot_id,
                record.raw_object_id,
            ) != expected_record_identity:
                raise SourceSnapshotIntegrityError
            for assertion_index, assertion in enumerate(record.assertions):
                expected_assertion_identity = (
                    _derived_assertion_id(
                        record_index=record_index,
                        assertion_index=assertion_index,
                        field_name=assertion.field_name,
                        native_locator=assertion.native_locator,
                        native_value_json=assertion.native_value_json,
                        normalized_value=assertion.normalized_value,
                        parser_id=assertion.parser_id,
                        parser_version=assertion.parser_version,
                    ),
                    self.snapshot_id,
                    self.raw_object.object_id,
                    record.source_record_id,
                    self.parser_id,
                    self.parser_version,
                )
                if (
                    assertion.assertion_id,
                    assertion.snapshot_id,
                    assertion.raw_object_id,
                    assertion.source_record_id,
                    assertion.parser_id,
                    assertion.parser_version,
                ) != expected_assertion_identity:
                    raise SourceSnapshotIntegrityError


def _assertion_content(assertion: SourceAssertion) -> dict[str, object]:
    return {
        "raw_object_id": assertion.raw_object_id,
        "source_record_id": assertion.source_record_id,
        "field_name": assertion.field_name,
        "native_locator": assertion.native_locator,
        "native_value_json": assertion.native_value_json,
        "normalized_value": assertion.normalized_value,
        "parser_id": assertion.parser_id,
        "parser_version": assertion.parser_version,
    }


def _record_content(record: SourceRecord) -> dict[str, object]:
    return {
        "raw_object_id": record.raw_object_id,
        "source_record_id": record.source_record_id,
        "native_locator": record.native_locator,
        "effective_from": record.effective_from,
        "effective_to": record.effective_to,
        "assertions": [_assertion_content(item) for item in record.assertions],
    }


def _snapshot_input_content(
    deployment_id: str,
    source_id: str,
    raw_object: RawObjectRef,
    parser_id: str,
    parser_version: str,
    schema_id: str,
    declared_record_count: int,
    parsed_at: datetime,
    records: tuple[ParsedRecordInput, ...],
) -> dict[str, object]:
    return {
        "deployment_id": deployment_id,
        "source_id": source_id,
        "raw_object": {
            "object_id": raw_object.object_id,
            "content_hash": raw_object.content_hash,
            "byte_length": raw_object.byte_length,
        },
        "parser_id": parser_id,
        "parser_version": parser_version,
        "schema_id": schema_id,
        "declared_record_count": declared_record_count,
        "parsed_at": parsed_at.isoformat(),
        "records": [
            {
                "raw_object_id": raw_object.object_id,
                "source_record_id": record.source_record_id,
                "native_locator": record.native_locator,
                "effective_from": record.effective_from,
                "effective_to": record.effective_to,
                "assertions": [
                    {
                        "raw_object_id": raw_object.object_id,
                        "source_record_id": record.source_record_id,
                        "field_name": assertion.field_name,
                        "native_locator": assertion.native_locator,
                        "native_value_json": _canonical_native_value(
                            assertion.native_value
                        ),
                        "normalized_value": assertion.normalized_value,
                        "parser_id": parser_id,
                        "parser_version": parser_version,
                    }
                    for assertion in record.assertions
                ],
            }
            for record in records
        ],
    }


def _snapshot_content(snapshot: ParsedSourceSnapshot) -> dict[str, object]:
    return {
        "deployment_id": snapshot.deployment_id,
        "source_id": snapshot.source_id,
        "raw_object": {
            "object_id": snapshot.raw_object.object_id,
            "content_hash": snapshot.raw_object.content_hash,
            "byte_length": snapshot.raw_object.byte_length,
        },
        "parser_id": snapshot.parser_id,
        "parser_version": snapshot.parser_version,
        "schema_id": snapshot.schema_id,
        "declared_record_count": snapshot.declared_record_count,
        "parsed_at": snapshot.parsed_at.isoformat(),
        "records": [_record_content(record) for record in snapshot.records],
    }


@dataclass(frozen=True, slots=True, order=True)
class ChangedRecord:
    source_record_id: str
    previous_record_hash: str
    new_record_hash: str

    def __post_init__(self) -> None:
        _require_id(self.source_record_id, "source_record_id")
        _require_hash(self.previous_record_hash, "previous_record_hash")
        _require_hash(self.new_record_hash, "new_record_hash")
        if self.previous_record_hash == self.new_record_hash:
            raise ValueError("changed record hashes must differ")


@dataclass(frozen=True, slots=True, init=False)
class SourceSnapshotDiff:
    previous_snapshot: SourceSnapshotRef | None
    new_snapshot: SourceSnapshotRef
    added_record_ids: tuple[str, ...]
    removed_record_ids: tuple[str, ...]
    changed_records: tuple[ChangedRecord, ...]
    content_hash: str

    def recomputed_content_hash(self) -> str:
        return canonical_sha256(_diff_content(self))

    @classmethod
    def create(
        cls,
        previous: ParsedSourceSnapshot | None,
        new: ParsedSourceSnapshot,
    ) -> SourceSnapshotDiff:
        if previous is not None and (
            previous.deployment_id != new.deployment_id
            or previous.source_id != new.source_id
        ):
            raise ValueError("snapshot diff scope mismatch")
        previous_records = _records_by_valid_id(previous) if previous else {}
        new_records = _records_by_valid_id(new)
        previous_ids = set(previous_records)
        new_ids = set(new_records)
        added = tuple(sorted(new_ids - previous_ids))
        removed = tuple(sorted(previous_ids - new_ids))
        changed = tuple(
            ChangedRecord(
                record_id,
                previous_records[record_id].content_hash(),
                new_records[record_id].content_hash(),
            )
            for record_id in sorted(previous_ids & new_ids)
            if previous_records[record_id].content_hash()
            != new_records[record_id].content_hash()
        )
        previous_ref = previous.reference() if previous else None
        new_ref = new.reference()
        instance = object.__new__(cls)
        for field, value in {
            "previous_snapshot": previous_ref,
            "new_snapshot": new_ref,
            "added_record_ids": added,
            "removed_record_ids": removed,
            "changed_records": changed,
        }.items():
            object.__setattr__(instance, field, value)
        object.__setattr__(
            instance, "content_hash", canonical_sha256(_diff_content(instance))
        )
        return instance


def _records_by_valid_id(
    snapshot: ParsedSourceSnapshot,
) -> dict[str, SourceRecord]:
    result: dict[str, SourceRecord] = {}
    for record in snapshot.records:
        if (
            record.source_record_id is None
            or SAFE_ID.fullmatch(record.source_record_id) is None
        ):
            raise ValueError("snapshot diff requires valid record identifiers")
        if record.source_record_id in result:
            raise ValueError("snapshot diff requires unique record identifiers")
        result[record.source_record_id] = record
    return result


def _snapshot_ref_content(reference: SourceSnapshotRef | None) -> object:
    if reference is None:
        return None
    return {
        "snapshot_id": reference.snapshot_id,
        "content_hash": reference.content_hash,
    }


def _diff_content(diff: SourceSnapshotDiff) -> dict[str, object]:
    return {
        "previous_snapshot": _snapshot_ref_content(diff.previous_snapshot),
        "new_snapshot": _snapshot_ref_content(diff.new_snapshot),
        "added_record_ids": diff.added_record_ids,
        "removed_record_ids": diff.removed_record_ids,
        "changed_records": [
            {
                "source_record_id": item.source_record_id,
                "previous_record_hash": item.previous_record_hash,
                "new_record_hash": item.new_record_hash,
            }
            for item in diff.changed_records
        ],
    }


@dataclass(frozen=True, slots=True, order=True)
class ValidationReason:
    code: ValidationReasonCode
    record_locator: str | None = None
    record_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, ValidationReasonCode):
            raise ValueError("validation reason code must be typed")
        if self.record_locator is not None:
            _require_locator(self.record_locator, "record_locator")
        if self.record_id is not None:
            _require_text(self.record_id, "record_id", 128)


@dataclass(frozen=True, slots=True, init=False)
class ValidationReport:
    snapshot: SourceSnapshotRef
    expected_schema_id: str
    reasons: tuple[ValidationReason, ...]
    diff: SourceSnapshotDiff
    validated_at: datetime
    content_hash: str

    def recomputed_content_hash(self) -> str:
        return canonical_sha256(_validation_report_content(self))

    @property
    def passed(self) -> bool:
        return not self.reasons


def validate_snapshot(
    snapshot: ParsedSourceSnapshot,
    *,
    expected_schema_id: str,
    previous_accepted: ParsedSourceSnapshot | None,
    validated_at: datetime,
) -> ValidationReport:
    if not isinstance(snapshot, ParsedSourceSnapshot):
        raise TypeError("snapshot must be typed")
    _require_id(expected_schema_id, "expected_schema_id")
    checked_at = _require_utc(validated_at, "validated_at")
    if checked_at < snapshot.parsed_at:
        raise ValueError("validated_at must not be before snapshot parsing")
    if previous_accepted is not None:
        if (
            previous_accepted.deployment_id != snapshot.deployment_id
            or previous_accepted.source_id != snapshot.source_id
        ):
            raise ValueError("previous accepted snapshot scope mismatch")
        try:
            previous_accepted.verify_integrity()
        except SourceSnapshotIntegrityError as exc:
            raise ValueError(
                "previous accepted snapshot failed integrity verification"
            ) from exc
    reasons: list[ValidationReason] = []
    try:
        snapshot.verify_integrity()
    except SourceSnapshotIntegrityError:
        snapshot_hash_valid = False
    else:
        snapshot_hash_valid = True
    if not snapshot_hash_valid:
        reasons.append(ValidationReason(ValidationReasonCode.SNAPSHOT_HASH_MISMATCH))
    if snapshot.schema_id != expected_schema_id:
        reasons.append(ValidationReason(ValidationReasonCode.SCHEMA_MISMATCH))
    if snapshot.declared_record_count != len(snapshot.records):
        reasons.append(ValidationReason(ValidationReasonCode.DECLARED_COUNT_MISMATCH))
    if not snapshot.records:
        reasons.append(ValidationReason(ValidationReasonCode.EMPTY_SNAPSHOT))
    ids: set[str] = set()
    diff_safe = snapshot_hash_valid
    for record in snapshot.records:
        record_id = record.source_record_id
        if record_id is None:
            diff_safe = False
            reasons.append(
                ValidationReason(
                    ValidationReasonCode.MISSING_RECORD_ID,
                    record_locator=record.native_locator,
                )
            )
        elif SAFE_ID.fullmatch(record_id) is None:
            diff_safe = False
            reasons.append(
                ValidationReason(
                    ValidationReasonCode.INVALID_RECORD_ID,
                    record_locator=record.native_locator,
                    record_id=record_id,
                )
            )
        elif record_id in ids:
            diff_safe = False
            reasons.append(
                ValidationReason(
                    ValidationReasonCode.DUPLICATE_RECORD_ID,
                    record_locator=record.native_locator,
                    record_id=record_id,
                )
            )
        else:
            ids.add(record_id)
        locators: set[str] = set()
        if not record.assertions:
            reasons.append(
                ValidationReason(
                    ValidationReasonCode.EMPTY_RECORD_ASSERTIONS,
                    record_locator=record.native_locator,
                    record_id=record_id,
                )
            )
        for assertion in record.assertions:
            if assertion.native_locator in locators:
                reasons.append(
                    ValidationReason(
                        ValidationReasonCode.DUPLICATE_LOCATOR,
                        record_locator=assertion.native_locator,
                        record_id=record_id,
                    )
                )
            locators.add(assertion.native_locator)
        start = _canonical_date(record.effective_from)
        end = _canonical_date(record.effective_to)
        if record.effective_from is not None and start is None:
            reasons.append(
                ValidationReason(
                    ValidationReasonCode.INVALID_EFFECTIVE_DATE,
                    record_locator=record.native_locator,
                    record_id=record_id,
                )
            )
        if record.effective_to is not None and end is None:
            reasons.append(
                ValidationReason(
                    ValidationReasonCode.INVALID_EFFECTIVE_DATE,
                    record_locator=record.native_locator,
                    record_id=record_id,
                )
            )
        if start is not None and end is not None and end < start:
            reasons.append(
                ValidationReason(
                    ValidationReasonCode.INVALID_EFFECTIVE_RANGE,
                    record_locator=record.native_locator,
                    record_id=record_id,
                )
            )
    if previous_accepted is not None:
        _records_by_valid_id(previous_accepted)
    diff = (
        SourceSnapshotDiff.create(previous_accepted, snapshot)
        if diff_safe
        else _empty_diff(previous_accepted, snapshot)
    )
    reasons.extend(
        ValidationReason(ValidationReasonCode.UNEXPECTED_DELETION, record_id=record_id)
        for record_id in diff.removed_record_ids
    )
    ordered = tuple(
        sorted(
            set(reasons),
            key=lambda item: (
                item.code,
                item.record_id or "",
                item.record_locator or "",
            ),
        )
    )
    report = object.__new__(ValidationReport)
    for field, value in {
        "snapshot": _verified_snapshot_ref(snapshot),
        "expected_schema_id": expected_schema_id,
        "reasons": ordered,
        "diff": diff,
        "validated_at": checked_at,
    }.items():
        object.__setattr__(report, field, value)
    object.__setattr__(
        report,
        "content_hash",
        canonical_sha256(_validation_report_content(report)),
    )
    return report


def _empty_diff(
    previous: ParsedSourceSnapshot | None, new: ParsedSourceSnapshot
) -> SourceSnapshotDiff:
    instance = object.__new__(SourceSnapshotDiff)
    previous_ref = _verified_snapshot_ref(previous) if previous else None
    new_ref = _verified_snapshot_ref(new)
    for field, value in {
        "previous_snapshot": previous_ref,
        "new_snapshot": new_ref,
        "added_record_ids": (),
        "removed_record_ids": (),
        "changed_records": (),
    }.items():
        object.__setattr__(instance, field, value)
    object.__setattr__(
        instance, "content_hash", canonical_sha256(_diff_content(instance))
    )
    return instance


def _verified_snapshot_ref(snapshot: ParsedSourceSnapshot) -> SourceSnapshotRef:
    content_hash = snapshot.recomputed_content_hash()
    return SourceSnapshotRef(
        f"snapshot-{content_hash.removeprefix('sha256:')}", content_hash
    )


def _validation_report_content(report: ValidationReport) -> dict[str, object]:
    return {
        "snapshot": _snapshot_ref_content(report.snapshot),
        "expected_schema_id": report.expected_schema_id,
        "reasons": [
            {
                "code": item.code,
                "record_locator": item.record_locator,
                "record_id": item.record_id,
            }
            for item in report.reasons
        ],
        "diff_hash": report.diff.content_hash,
        "validated_at": report.validated_at.isoformat(),
    }


def _canonical_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


@dataclass(frozen=True, slots=True)
class SourceSnapshotLifecycleEvent:
    sequence: int
    event_id: str
    deployment_id: str
    source_id: str
    event_type: SourceSnapshotEventType
    raw_object: RawObjectRef
    snapshot: SourceSnapshotRef | None
    previous_active_snapshot: SourceSnapshotRef | None
    actor_id: str
    actor_type: LifecycleActorType
    reason: str
    occurred_at: datetime
    validation_report: ValidationReport | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or not 1 <= self.sequence <= MAX_SEQUENCE
        ):
            raise ValueError("sequence must be a positive signed BIGINT")
        for field in ("event_id", "deployment_id", "source_id", "actor_id"):
            _require_id(getattr(self, field), field)
        if not isinstance(self.event_type, SourceSnapshotEventType):
            raise ValueError("event_type must be typed")
        if not isinstance(self.raw_object, RawObjectRef):
            raise ValueError("raw_object must be typed")
        for field in ("snapshot", "previous_active_snapshot"):
            value = getattr(self, field)
            if value is not None and not isinstance(value, SourceSnapshotRef):
                raise ValueError(f"{field} must be typed")
        if not isinstance(self.actor_type, LifecycleActorType):
            raise ValueError("actor_type must be typed")
        _require_text(self.reason, "reason", MAX_REASON_LENGTH)
        _require_utc(self.occurred_at, "occurred_at")
        if self.actor_type is LifecycleActorType.AGENT:
            raise ValueError("agents cannot mutate source snapshot lifecycle")
        if (
            self.event_type
            in {
                SourceSnapshotEventType.APPROVED,
                SourceSnapshotEventType.ACTIVATED,
                SourceSnapshotEventType.ROLLED_BACK,
            }
            and self.actor_type is not LifecycleActorType.HUMAN
        ):
            raise ValueError("approval, activation, and rollback require a human")
        requires_snapshot = self.event_type not in {
            SourceSnapshotEventType.RETRIEVED,
            SourceSnapshotEventType.QUARANTINED,
        }
        if requires_snapshot != (self.snapshot is not None):
            raise ValueError("event snapshot reference does not match event type")
        requires_previous = self.event_type in {
            SourceSnapshotEventType.ACTIVATED,
            SourceSnapshotEventType.ROLLED_BACK,
        }
        if not requires_previous and self.previous_active_snapshot is not None:
            raise ValueError("previous active snapshot is invalid for event type")
        requires_report = self.event_type in {
            SourceSnapshotEventType.VALIDATION_FAILED,
            SourceSnapshotEventType.VALIDATED,
        }
        if requires_report != (self.validation_report is not None):
            raise ValueError("validation event must carry the computed report")
        if (
            self.validation_report is not None
            and self.validation_report.snapshot != self.snapshot
        ):
            raise ValueError("validation report must identify the event snapshot")
        report = self.validation_report
        if report is not None and (
            report.recomputed_content_hash() != report.content_hash
            or report.diff.recomputed_content_hash() != report.diff.content_hash
        ):
            raise ValueError("validation report must pass content verification")
        if report is not None and self.occurred_at < report.validated_at:
            raise ValueError("validation event cannot precede report validation")
        if self.event_type is SourceSnapshotEventType.VALIDATED and (
            report is None or not report.passed
        ):
            raise ValueError("VALIDATED requires a passing computed report")
        if self.event_type is SourceSnapshotEventType.VALIDATION_FAILED and (
            report is None or report.passed
        ):
            raise ValueError("VALIDATION_FAILED requires blocking reasons")


@dataclass(frozen=True, slots=True)
class SnapshotStateRecord:
    snapshot: SourceSnapshotRef
    raw_object: RawObjectRef
    creator_actor_id: str
    state: SourceSnapshotState

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, SourceSnapshotRef):
            raise ValueError("snapshot must be typed")
        if not isinstance(self.raw_object, RawObjectRef):
            raise ValueError("raw_object must be typed")
        _require_id(self.creator_actor_id, "creator_actor_id")
        if not isinstance(self.state, SourceSnapshotState):
            raise ValueError("state must be typed")


@dataclass(frozen=True, slots=True)
class RawObjectStateRecord:
    raw_object: RawObjectRef
    state: RawObjectState

    def __post_init__(self) -> None:
        if not isinstance(self.raw_object, RawObjectRef):
            raise ValueError("raw_object must be typed")
        if not isinstance(self.state, RawObjectState):
            raise ValueError("state must be typed")


@dataclass(frozen=True, slots=True)
class SourceSnapshotLifecycle:
    raw_objects: tuple[RawObjectStateRecord, ...]
    snapshots: tuple[SnapshotStateRecord, ...]
    active_snapshot: SourceSnapshotRef | None
    activated_snapshots: tuple[SourceSnapshotRef, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.raw_objects, tuple) or any(
            not isinstance(item, RawObjectStateRecord) for item in self.raw_objects
        ):
            raise ValueError("raw_objects must be typed")
        if not isinstance(self.snapshots, tuple) or any(
            not isinstance(item, SnapshotStateRecord) for item in self.snapshots
        ):
            raise ValueError("snapshots must be typed")
        if not isinstance(self.activated_snapshots, tuple) or any(
            not isinstance(item, SourceSnapshotRef) for item in self.activated_snapshots
        ):
            raise ValueError("activated_snapshots must be typed")
        raw_refs = tuple(item.raw_object for item in self.raw_objects)
        snapshot_refs = tuple(item.snapshot for item in self.snapshots)
        if tuple(sorted(set(raw_refs))) != raw_refs:
            raise ValueError("raw-object projection must be sorted and unique")
        if tuple(sorted(set(snapshot_refs))) != snapshot_refs:
            raise ValueError("snapshot projection must be sorted and unique")
        if tuple(sorted(set(self.activated_snapshots))) != self.activated_snapshots:
            raise ValueError("activated snapshot history must be sorted and unique")
        if self.active_snapshot is not None and not isinstance(
            self.active_snapshot, SourceSnapshotRef
        ):
            raise ValueError("active_snapshot must be typed")
        raw_states = {item.raw_object: item.state for item in self.raw_objects}
        states = {item.snapshot: item.state for item in self.snapshots}
        if any(
            item.raw_object not in raw_states
            or raw_states[item.raw_object] is not RawObjectState.PARSED
            for item in self.snapshots
        ):
            raise ValueError("snapshot projection must reference parsed raw objects")
        active_records = tuple(
            reference
            for reference, state in states.items()
            if state is SourceSnapshotState.ACTIVE
        )
        if active_records != (
            () if self.active_snapshot is None else (self.active_snapshot,)
        ):
            raise ValueError("active pointer must match the projected active state")
        if any(
            reference not in states
            or states[reference]
            not in {
                SourceSnapshotState.ACTIVE,
                SourceSnapshotState.SUPERSEDED,
                SourceSnapshotState.ROLLED_BACK,
            }
            for reference in self.activated_snapshots
        ):
            raise ValueError(
                "activated history must reference accepted lifecycle states"
            )
        proven_activated = tuple(
            sorted(
                reference
                for reference, state in states.items()
                if state
                in {
                    SourceSnapshotState.ACTIVE,
                    SourceSnapshotState.SUPERSEDED,
                    SourceSnapshotState.ROLLED_BACK,
                }
            )
        )
        if self.activated_snapshots != proven_activated:
            raise ValueError("activated history must exactly match projected states")

    def state_for(self, snapshot: SourceSnapshotRef) -> SourceSnapshotState | None:
        return next(
            (item.state for item in self.snapshots if item.snapshot == snapshot), None
        )

    def creator_for(self, snapshot: SourceSnapshotRef) -> str | None:
        return next(
            (
                item.creator_actor_id
                for item in self.snapshots
                if item.snapshot == snapshot
            ),
            None,
        )


def fold_source_snapshot_events(
    events: tuple[SourceSnapshotLifecycleEvent, ...],
) -> SourceSnapshotLifecycle:
    if not isinstance(events, tuple) or any(
        not isinstance(event, SourceSnapshotLifecycleEvent) for event in events
    ):
        raise ValueError("events must contain typed lifecycle events")
    if len(events) > MAX_LIFECYCLE_EVENTS:
        raise ValueError("lifecycle history exceeds its bound")
    raw_states: dict[RawObjectRef, RawObjectState] = {}
    snapshot_states: dict[SourceSnapshotRef, SourceSnapshotState] = {}
    snapshot_raw: dict[SourceSnapshotRef, RawObjectRef] = {}
    creators: dict[SourceSnapshotRef, str] = {}
    validated: set[SourceSnapshotRef] = set()
    approved: set[SourceSnapshotRef] = set()
    activated: set[SourceSnapshotRef] = set()
    active: SourceSnapshotRef | None = None
    scope: tuple[str, str] | None = None
    event_ids: set[str] = set()
    sequence = 0
    occurred_at: datetime | None = None
    raw_hashes: dict[str, tuple[str, int]] = {}
    for event in events:
        event_scope = (event.deployment_id, event.source_id)
        if scope is None:
            scope = event_scope
        if (
            event_scope != scope
            or event.sequence != sequence + 1
            or event.event_id in event_ids
            or (occurred_at is not None and event.occurred_at < occurred_at)
        ):
            raise InvalidSourceSnapshotTransition
        sequence = event.sequence
        occurred_at = event.occurred_at
        event_ids.add(event.event_id)
        known_raw = raw_hashes.setdefault(
            event.raw_object.object_id,
            (event.raw_object.content_hash, event.raw_object.byte_length),
        )
        if known_raw != (event.raw_object.content_hash, event.raw_object.byte_length):
            raise InvalidSourceSnapshotTransition
        event_type = event.event_type
        snapshot = event.snapshot
        raw = event.raw_object
        if event_type is SourceSnapshotEventType.RETRIEVED:
            if raw in raw_states:
                raise InvalidSourceSnapshotTransition
            raw_states[raw] = RawObjectState.RETRIEVED
        elif event_type is SourceSnapshotEventType.QUARANTINED:
            if raw_states.get(raw) is not RawObjectState.RETRIEVED:
                raise InvalidSourceSnapshotTransition
            raw_states[raw] = RawObjectState.QUARANTINED
        elif event_type is SourceSnapshotEventType.PARSED:
            assert snapshot is not None
            if (
                raw_states.get(raw) is not RawObjectState.QUARANTINED
                or snapshot in snapshot_states
            ):
                raise InvalidSourceSnapshotTransition
            raw_states[raw] = RawObjectState.PARSED
            snapshot_states[snapshot] = SourceSnapshotState.PARSED
            snapshot_raw[snapshot] = raw
            creators[snapshot] = event.actor_id
        elif event_type in {
            SourceSnapshotEventType.VALIDATION_FAILED,
            SourceSnapshotEventType.VALIDATED,
        }:
            assert snapshot is not None
            if (
                snapshot_states.get(snapshot) is not SourceSnapshotState.PARSED
                or snapshot_raw.get(snapshot) != raw
            ):
                raise InvalidSourceSnapshotTransition
            if event_type is SourceSnapshotEventType.VALIDATION_FAILED:
                snapshot_states[snapshot] = SourceSnapshotState.QUARANTINED
            else:
                snapshot_states[snapshot] = SourceSnapshotState.VALIDATED
                validated.add(snapshot)
        elif event_type is SourceSnapshotEventType.APPROVED:
            assert snapshot is not None
            if (
                snapshot_states.get(snapshot) is not SourceSnapshotState.VALIDATED
                or snapshot_raw.get(snapshot) != raw
                or creators.get(snapshot) == event.actor_id
            ):
                raise InvalidSourceSnapshotTransition
            snapshot_states[snapshot] = SourceSnapshotState.APPROVED
            approved.add(snapshot)
        elif event_type is SourceSnapshotEventType.ACTIVATED:
            assert snapshot is not None
            if (
                snapshot_states.get(snapshot) is not SourceSnapshotState.APPROVED
                or snapshot_raw.get(snapshot) != raw
                or event.previous_active_snapshot != active
                or creators.get(snapshot) == event.actor_id
            ):
                raise InvalidSourceSnapshotTransition
            if active is not None:
                snapshot_states[active] = SourceSnapshotState.SUPERSEDED
            snapshot_states[snapshot] = SourceSnapshotState.ACTIVE
            active = snapshot
            activated.add(snapshot)
        else:
            assert event_type is SourceSnapshotEventType.ROLLED_BACK
            assert snapshot is not None
            if (
                active is None
                or event.previous_active_snapshot != active
                or snapshot == active
                or snapshot not in activated
                or snapshot not in validated
                or snapshot not in approved
                or snapshot_states.get(snapshot)
                not in {SourceSnapshotState.SUPERSEDED, SourceSnapshotState.ROLLED_BACK}
                or snapshot_raw.get(snapshot) != raw
                or creators.get(snapshot) == event.actor_id
            ):
                raise InvalidSourceSnapshotTransition
            snapshot_states[active] = SourceSnapshotState.ROLLED_BACK
            snapshot_states[snapshot] = SourceSnapshotState.ACTIVE
            active = snapshot
            activated.add(snapshot)
    return SourceSnapshotLifecycle(
        tuple(
            RawObjectStateRecord(reference, raw_states[reference])
            for reference in sorted(raw_states)
        ),
        tuple(
            SnapshotStateRecord(
                reference,
                snapshot_raw[reference],
                creators[reference],
                snapshot_states[reference],
            )
            for reference in sorted(snapshot_states)
        ),
        active,
        tuple(sorted(activated)),
    )


def validate_lifecycle_projection(
    events: tuple[SourceSnapshotLifecycleEvent, ...],
    projection: SourceSnapshotLifecycle,
) -> None:
    if not isinstance(projection, SourceSnapshotLifecycle):
        raise ValueError("projection must be typed")
    if fold_source_snapshot_events(events) != projection:
        raise InvalidSourceSnapshotTransition


def semantically_applied_lifecycle_transition(
    attempted: SourceSnapshotLifecycleEvent,
    lifecycle: SourceSnapshotLifecycle,
    current_events: tuple[SourceSnapshotLifecycleEvent, ...],
) -> SourceSnapshotLifecycleEvent | None:
    if not isinstance(attempted, SourceSnapshotLifecycleEvent):
        raise ValueError("attempted event must be typed")
    if not isinstance(lifecycle, SourceSnapshotLifecycle):
        raise ValueError("lifecycle must be typed")
    if not isinstance(current_events, tuple) or any(
        not isinstance(event, SourceSnapshotLifecycleEvent) for event in current_events
    ):
        raise ValueError("current events must be typed")
    candidates = tuple(
        event
        for event in current_events
        if event.event_type is attempted.event_type
        and event.raw_object == attempted.raw_object
        and event.snapshot == attempted.snapshot
        and event.previous_active_snapshot == attempted.previous_active_snapshot
        and event.validation_report == attempted.validation_report
        and event.reason == attempted.reason
    )
    if not candidates:
        return None
    responsible = candidates[-1]
    raw_state = next(
        (
            item.state
            for item in lifecycle.raw_objects
            if item.raw_object == attempted.raw_object
        ),
        None,
    )
    snapshot_state = (
        lifecycle.state_for(attempted.snapshot)
        if attempted.snapshot is not None
        else None
    )
    expected_applied = {
        SourceSnapshotEventType.RETRIEVED: raw_state is not None,
        SourceSnapshotEventType.QUARANTINED: raw_state
        in {RawObjectState.QUARANTINED, RawObjectState.PARSED},
        SourceSnapshotEventType.PARSED: snapshot_state is not None,
        SourceSnapshotEventType.VALIDATION_FAILED: snapshot_state
        is SourceSnapshotState.QUARANTINED,
        SourceSnapshotEventType.VALIDATED: snapshot_state
        in {
            SourceSnapshotState.VALIDATED,
            SourceSnapshotState.APPROVED,
            SourceSnapshotState.ACTIVE,
            SourceSnapshotState.SUPERSEDED,
            SourceSnapshotState.ROLLED_BACK,
        },
        SourceSnapshotEventType.APPROVED: snapshot_state
        in {
            SourceSnapshotState.APPROVED,
            SourceSnapshotState.ACTIVE,
            SourceSnapshotState.SUPERSEDED,
            SourceSnapshotState.ROLLED_BACK,
        },
        SourceSnapshotEventType.ACTIVATED: lifecycle.active_snapshot
        == attempted.snapshot,
        SourceSnapshotEventType.ROLLED_BACK: lifecycle.active_snapshot
        == attempted.snapshot,
    }[attempted.event_type]
    return responsible if expected_applied else None


@dataclass(frozen=True, slots=True)
class SnapshotCommandAuditRecord:
    command_event_id: str
    authorization_event_id: str
    lifecycle_event_id: str | None
    tenant_id: str
    deployment_id: str
    source_id: str
    raw_object_id: str
    snapshot_id: str | None
    snapshot_content_hash: str | None
    actor_id: str
    actor_type: LifecycleActorType
    operation: str
    outcome: SnapshotCommandOutcome
    reason: SnapshotCommandReason
    occurred_at: datetime

    def __post_init__(self) -> None:
        for field in (
            "command_event_id",
            "authorization_event_id",
            "tenant_id",
            "deployment_id",
            "source_id",
            "raw_object_id",
            "actor_id",
            "operation",
        ):
            _require_id(getattr(self, field), field)
        if self.lifecycle_event_id is not None:
            _require_id(self.lifecycle_event_id, "lifecycle_event_id")
        if (self.snapshot_id is None) != (self.snapshot_content_hash is None):
            raise ValueError("snapshot ID and hash must be present together")
        if self.snapshot_id is not None:
            _require_id(self.snapshot_id, "snapshot_id")
            _require_hash(self.snapshot_content_hash, "snapshot_content_hash")
            assert self.snapshot_content_hash is not None
            SourceSnapshotRef(self.snapshot_id, self.snapshot_content_hash)
        if not isinstance(self.actor_type, LifecycleActorType):
            raise ValueError("actor_type must be typed")
        if not isinstance(self.outcome, SnapshotCommandOutcome):
            raise ValueError("outcome must be typed")
        if not isinstance(self.reason, SnapshotCommandReason):
            raise ValueError("reason must be typed")
        if (self.reason in SUCCESS_COMMAND_REASONS) != (
            self.outcome is SnapshotCommandOutcome.SUCCESS
        ):
            raise ValueError("command outcome must match its reason")
        if (self.lifecycle_event_id is not None) != (
            self.reason
            in {
                SnapshotCommandReason.RETRIEVED,
                SnapshotCommandReason.QUARANTINED,
                SnapshotCommandReason.PARSED,
                SnapshotCommandReason.VALIDATION_FAILED,
                SnapshotCommandReason.VALIDATED,
                SnapshotCommandReason.APPROVED,
                SnapshotCommandReason.ACTIVATED,
                SnapshotCommandReason.ROLLED_BACK,
            }
        ):
            raise ValueError("only applied transitions link a lifecycle event")
        _require_utc(self.occurred_at, "occurred_at")


def validate_atomic_command_audit(
    audit: SnapshotCommandAuditRecord,
    raw_object: RawObjectRef,
    snapshot: SourceSnapshotRef | None,
    event: SourceSnapshotLifecycleEvent | None,
    expected_reason: SnapshotCommandReason,
) -> None:
    if not isinstance(audit, SnapshotCommandAuditRecord):
        raise ValueError("audit must be typed")
    if event is not None and not isinstance(event, SourceSnapshotLifecycleEvent):
        raise ValueError("event must be typed")
    if not isinstance(raw_object, RawObjectRef):
        raise ValueError("raw_object must be typed")
    if snapshot is not None and not isinstance(snapshot, SourceSnapshotRef):
        raise ValueError("snapshot must be typed")
    if not isinstance(expected_reason, SnapshotCommandReason):
        raise ValueError("expected_reason must be typed")
    try:
        expected_event_type = EVENT_TYPE_BY_SUCCESS_COMMAND_REASON[expected_reason]
    except KeyError as exc:
        raise ValueError("expected_reason must identify a successful command") from exc
    expected_operation = AUTHORIZATION_OPERATION_BY_EVENT_TYPE[expected_event_type]
    event_id = event.event_id if event else None
    if (
        audit.reason is not expected_reason
        or audit.operation != expected_operation
        or audit.lifecycle_event_id != event_id
        or audit.raw_object_id != raw_object.object_id
        or audit.snapshot_id != (snapshot.snapshot_id if snapshot else None)
        or audit.snapshot_content_hash != (snapshot.content_hash if snapshot else None)
        or (
            event is not None
            and (
                event.event_type is not expected_event_type
                or (audit.deployment_id, audit.source_id)
                != (event.deployment_id, event.source_id)
                or event.raw_object != raw_object
                or event.snapshot != snapshot
                or audit.actor_id != event.actor_id
                or audit.actor_type is not event.actor_type
                or audit.occurred_at != event.occurred_at
            )
        )
    ):
        raise ValueError("command audit does not match the atomic lifecycle write")


def require_query_limit(limit: int) -> int:
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= MAX_QUERY_LIMIT
    ):
        raise ValueError("limit must be within the bounded query range")
    return limit
