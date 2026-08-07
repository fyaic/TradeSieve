"""Strict canonical codecs for private source-snapshot persistence documents."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Never

from tradesieve.domain.source_snapshot import (
    MAX_ASSERTIONS_PER_RECORD,
    MAX_NATIVE_VALUE_BYTES,
    MAX_RECORDS_PER_SNAPSHOT,
    ParsedAssertionInput,
    ParsedRecordInput,
    ParsedSourceSnapshot,
    RawObjectMetadata,
    SourceSnapshotRef,
    ValidationReport,
    validate_snapshot,
)
from tradesieve.ports.source_snapshot import (
    SYNTHETIC_CHARSET,
    SYNTHETIC_MEDIA_TYPE,
    SYNTHETIC_PARSER_VERSION,
    SYNTHETIC_SCHEMA_ID,
    FiniteParserId,
)

SNAPSHOT_DOCUMENT_SCHEMA_VERSION = "1.0.0"
VALIDATION_DOCUMENT_SCHEMA_VERSION = "1.0.0"
MAX_SNAPSHOT_DOCUMENT_BYTES = 4_194_304
MAX_VALIDATION_DOCUMENT_BYTES = 2_097_152
_MAX_JSON_DEPTH = 8
_MAX_VALIDATION_REASONS = MAX_RECORDS_PER_SNAPSHOT * (MAX_ASSERTIONS_PER_RECORD + 8)


class SourceSnapshotCodecError(ValueError):
    """Stable, redacted failure for a persisted parsed-snapshot document."""

    def __init__(self) -> None:
        super().__init__("source snapshot persistence document failed verification")


class ValidationEvidenceCodecError(ValueError):
    """Stable, redacted failure for a persisted validation-evidence document."""

    def __init__(self) -> None:
        super().__init__("validation evidence persistence document failed verification")


class _InvalidDocument(Exception):
    pass


def encode_snapshot_document(
    snapshot: ParsedSourceSnapshot,
    *,
    raw_metadata: RawObjectMetadata,
) -> bytes:
    """Encode one complete materialized snapshot as canonical UTF-8 JSON."""

    try:
        checked_raw = _reconstitute_raw_metadata(raw_metadata)
        _require_snapshot(snapshot, checked_raw)
        return _encode_document(
            _snapshot_document(snapshot), MAX_SNAPSHOT_DOCUMENT_BYTES
        )
    except Exception:
        pass
    raise SourceSnapshotCodecError from None


def decode_snapshot_document(
    payload: object,
    *,
    deployment_id: str,
    source_id: str,
    snapshot_id: str,
    content_hash: str,
    raw_metadata: RawObjectMetadata,
) -> ParsedSourceSnapshot:
    """Rebuild a snapshot and compare its entire materialized persistence form."""

    try:
        checked_raw = _reconstitute_raw_metadata(raw_metadata)
        document = _load_document(payload, MAX_SNAPSHOT_DOCUMENT_BYTES)
        rebuilt = _decode_snapshot_shape(
            document,
            deployment_id=_string(deployment_id),
            source_id=_string(source_id),
            snapshot_id=_string(snapshot_id),
            content_hash=_string(content_hash),
            raw_metadata=checked_raw,
        )
        if (
            _encode_document(_snapshot_document(rebuilt), MAX_SNAPSHOT_DOCUMENT_BYTES)
            != payload
        ):
            raise _InvalidDocument
        return rebuilt
    except Exception:
        pass
    raise SourceSnapshotCodecError from None


def encode_validation_document(
    report: ValidationReport,
    *,
    snapshot: ParsedSourceSnapshot,
    previous_accepted: ParsedSourceSnapshot | None,
) -> bytes:
    """Encode evidence only after recomputing it from its trusted snapshots."""

    try:
        _require_validation_inputs(snapshot, previous_accepted)
        if type(report) is not ValidationReport:
            raise _InvalidDocument
        recomputed = validate_snapshot(
            snapshot,
            expected_schema_id=SYNTHETIC_SCHEMA_ID,
            previous_accepted=previous_accepted,
            validated_at=report.validated_at,
        )
        if recomputed != report:
            raise _InvalidDocument
        return _encode_document(
            _validation_document(report, snapshot), MAX_VALIDATION_DOCUMENT_BYTES
        )
    except Exception:
        pass
    raise ValidationEvidenceCodecError from None


def decode_validation_document(
    payload: object,
    *,
    deployment_id: str,
    source_id: str,
    snapshot_id: str,
    report_content_hash: str,
    expected_schema_id: str,
    validated_at: datetime,
    snapshot: ParsedSourceSnapshot,
    previous_accepted: ParsedSourceSnapshot | None,
) -> ValidationReport:
    """Recompute evidence using the separately loaded exact predecessor."""

    try:
        _require_validation_inputs(snapshot, previous_accepted)
        row_identity = (
            _string(deployment_id),
            _string(source_id),
            _string(snapshot_id),
            _string(report_content_hash),
            _string(expected_schema_id),
            _datetime_text(validated_at),
        )
        document = _load_document(payload, MAX_VALIDATION_DOCUMENT_BYTES)
        _validate_validation_shape(document)
        document_identity = (
            _string(document["deployment_id"]),
            _string(document["source_id"]),
            _reference(document["snapshot"])[0],
            _string(document["content_hash"]),
            _string(document["expected_schema_id"]),
            _string(document["validated_at"]),
        )
        if (
            _string(document["schema_version"]) != VALIDATION_DOCUMENT_SCHEMA_VERSION
            or row_identity != document_identity
            or deployment_id != snapshot.deployment_id
            or source_id != snapshot.source_id
            or snapshot_id != snapshot.snapshot_id
            or expected_schema_id != SYNTHETIC_SCHEMA_ID
            or _reference(document["snapshot"])
            != (
                snapshot.snapshot_id,
                snapshot.content_hash,
            )
        ):
            raise _InvalidDocument
        recomputed = validate_snapshot(
            snapshot,
            expected_schema_id=expected_schema_id,
            previous_accepted=previous_accepted,
            validated_at=validated_at,
        )
        if (
            recomputed.content_hash != report_content_hash
            or _encode_document(
                _validation_document(recomputed, snapshot),
                MAX_VALIDATION_DOCUMENT_BYTES,
            )
            != payload
        ):
            raise _InvalidDocument
        return recomputed
    except Exception:
        pass
    raise ValidationEvidenceCodecError from None


def _reconstitute_raw_metadata(value: RawObjectMetadata) -> RawObjectMetadata:
    if type(value) is not RawObjectMetadata:
        raise _InvalidDocument
    rebuilt = RawObjectMetadata.reconstitute(
        deployment_id=value.deployment_id,
        source_id=value.source_id,
        object_id=value.object_id,
        original_name=value.original_name,
        media_type=value.media_type,
        charset=value.charset,
        retrieved_at=value.retrieved_at,
        effective_from=value.effective_from,
        byte_length=value.byte_length,
        content_hash=value.content_hash,
    )
    return rebuilt


def _require_snapshot(
    snapshot: ParsedSourceSnapshot, raw_metadata: RawObjectMetadata
) -> None:
    if type(snapshot) is not ParsedSourceSnapshot:
        raise _InvalidDocument
    snapshot.verify_integrity()
    if (
        snapshot.deployment_id != raw_metadata.deployment_id
        or snapshot.source_id != raw_metadata.source_id
        or snapshot.raw_object != raw_metadata.reference()
        or raw_metadata.media_type != SYNTHETIC_MEDIA_TYPE
        or raw_metadata.charset != SYNTHETIC_CHARSET
        or snapshot.parser_id != FiniteParserId.SYNTHETIC_JSON_V1.value
        or snapshot.parser_version != SYNTHETIC_PARSER_VERSION
        or snapshot.schema_id != SYNTHETIC_SCHEMA_ID
    ):
        raise _InvalidDocument


def _require_validation_inputs(
    snapshot: ParsedSourceSnapshot,
    previous_accepted: ParsedSourceSnapshot | None,
) -> None:
    if type(snapshot) is not ParsedSourceSnapshot:
        raise _InvalidDocument
    snapshot.verify_integrity()
    if (
        snapshot.parser_id != FiniteParserId.SYNTHETIC_JSON_V1.value
        or snapshot.parser_version != SYNTHETIC_PARSER_VERSION
        or snapshot.schema_id != SYNTHETIC_SCHEMA_ID
    ):
        raise _InvalidDocument
    if previous_accepted is not None:
        if type(previous_accepted) is not ParsedSourceSnapshot:
            raise _InvalidDocument
        previous_accepted.verify_integrity()


def _snapshot_document(snapshot: ParsedSourceSnapshot) -> dict[str, Any]:
    return {
        "schema_version": SNAPSHOT_DOCUMENT_SCHEMA_VERSION,
        "deployment_id": snapshot.deployment_id,
        "source_id": snapshot.source_id,
        "snapshot_id": snapshot.snapshot_id,
        "content_hash": snapshot.content_hash,
        "raw_object": {
            "object_id": snapshot.raw_object.object_id,
            "content_hash": snapshot.raw_object.content_hash,
            "byte_length": snapshot.raw_object.byte_length,
        },
        "parser_id": snapshot.parser_id,
        "parser_version": snapshot.parser_version,
        "schema_id": snapshot.schema_id,
        "declared_record_count": snapshot.declared_record_count,
        "parsed_at": _datetime_text(snapshot.parsed_at),
        "records": [
            {
                "record_key": record.record_key,
                "snapshot_id": record.snapshot_id,
                "raw_object_id": record.raw_object_id,
                "source_record_id": record.source_record_id,
                "native_locator": record.native_locator,
                "effective_from": record.effective_from,
                "effective_to": record.effective_to,
                "assertions": [
                    {
                        "assertion_id": assertion.assertion_id,
                        "snapshot_id": assertion.snapshot_id,
                        "raw_object_id": assertion.raw_object_id,
                        "source_record_id": assertion.source_record_id,
                        "field_name": assertion.field_name,
                        "native_locator": assertion.native_locator,
                        "native_value_json": assertion.native_value_json,
                        "normalized_value": assertion.normalized_value,
                        "parser_id": assertion.parser_id,
                        "parser_version": assertion.parser_version,
                    }
                    for assertion in record.assertions
                ],
            }
            for record in snapshot.records
        ],
    }


def _decode_snapshot_shape(
    document: dict[str, Any],
    *,
    deployment_id: str,
    source_id: str,
    snapshot_id: str,
    content_hash: str,
    raw_metadata: RawObjectMetadata,
) -> ParsedSourceSnapshot:
    _exact_keys(
        document,
        {
            "schema_version",
            "deployment_id",
            "source_id",
            "snapshot_id",
            "content_hash",
            "raw_object",
            "parser_id",
            "parser_version",
            "schema_id",
            "declared_record_count",
            "parsed_at",
            "records",
        },
    )
    raw_reference = _raw_reference(document["raw_object"])
    document_identity = (
        _string(document["deployment_id"]),
        _string(document["source_id"]),
        _string(document["snapshot_id"]),
        _string(document["content_hash"]),
    )
    if (
        _string(document["schema_version"]) != SNAPSHOT_DOCUMENT_SCHEMA_VERSION
        or document_identity != (deployment_id, source_id, snapshot_id, content_hash)
        or deployment_id != raw_metadata.deployment_id
        or source_id != raw_metadata.source_id
        or raw_reference
        != (
            raw_metadata.object_id,
            raw_metadata.content_hash,
            raw_metadata.byte_length,
        )
        or raw_metadata.media_type != SYNTHETIC_MEDIA_TYPE
        or raw_metadata.charset != SYNTHETIC_CHARSET
        or _string(document["parser_id"]) != FiniteParserId.SYNTHETIC_JSON_V1.value
        or _string(document["parser_version"]) != SYNTHETIC_PARSER_VERSION
        or _string(document["schema_id"]) != SYNTHETIC_SCHEMA_ID
    ):
        raise _InvalidDocument
    records = tuple(
        _parsed_record(value)
        for value in _list(document["records"], MAX_RECORDS_PER_SNAPSHOT)
    )
    rebuilt = ParsedSourceSnapshot.create(
        raw_metadata=raw_metadata,
        parser_id=_string(document["parser_id"]),
        parser_version=_string(document["parser_version"]),
        schema_id=_string(document["schema_id"]),
        declared_record_count=_integer(document["declared_record_count"]),
        parsed_at=_datetime(document["parsed_at"]),
        records=records,
    )
    if rebuilt.snapshot_id != snapshot_id or rebuilt.content_hash != content_hash:
        raise _InvalidDocument
    return rebuilt


def _parsed_record(value: object) -> ParsedRecordInput:
    document = _object(value)
    _exact_keys(
        document,
        {
            "record_key",
            "snapshot_id",
            "raw_object_id",
            "source_record_id",
            "native_locator",
            "effective_from",
            "effective_to",
            "assertions",
        },
    )
    _string(document["record_key"])
    _string(document["snapshot_id"])
    _string(document["raw_object_id"])
    assertions = tuple(
        _parsed_assertion(item)
        for item in _list(document["assertions"], MAX_ASSERTIONS_PER_RECORD)
    )
    return ParsedRecordInput(
        source_record_id=_optional_string(document["source_record_id"]),
        native_locator=_string(document["native_locator"]),
        effective_from=_optional_string(document["effective_from"]),
        effective_to=_optional_string(document["effective_to"]),
        assertions=assertions,
    )


def _parsed_assertion(value: object) -> ParsedAssertionInput:
    document = _object(value)
    _exact_keys(
        document,
        {
            "assertion_id",
            "snapshot_id",
            "raw_object_id",
            "source_record_id",
            "field_name",
            "native_locator",
            "native_value_json",
            "normalized_value",
            "parser_id",
            "parser_version",
        },
    )
    for key in (
        "assertion_id",
        "snapshot_id",
        "raw_object_id",
        "field_name",
        "native_locator",
        "normalized_value",
        "parser_id",
        "parser_version",
    ):
        _string(document[key])
    _optional_string(document["source_record_id"])
    return ParsedAssertionInput(
        field_name=document["field_name"],
        native_locator=document["native_locator"],
        native_value=_native_scalar(document["native_value_json"]),
        normalized_value=document["normalized_value"],
    )


def _validation_document(
    report: ValidationReport, snapshot: ParsedSourceSnapshot
) -> dict[str, Any]:
    return {
        "schema_version": VALIDATION_DOCUMENT_SCHEMA_VERSION,
        "deployment_id": snapshot.deployment_id,
        "source_id": snapshot.source_id,
        "snapshot": _reference_document(report.snapshot),
        "expected_schema_id": report.expected_schema_id,
        "passed": report.passed,
        "reasons": [
            {
                "code": reason.code.value,
                "record_locator": reason.record_locator,
                "record_id": reason.record_id,
            }
            for reason in report.reasons
        ],
        "diff": {
            "previous_snapshot": (
                _reference_document(report.diff.previous_snapshot)
                if report.diff.previous_snapshot is not None
                else None
            ),
            "new_snapshot": _reference_document(report.diff.new_snapshot),
            "added_record_ids": list(report.diff.added_record_ids),
            "removed_record_ids": list(report.diff.removed_record_ids),
            "changed_records": [
                {
                    "source_record_id": change.source_record_id,
                    "previous_record_hash": change.previous_record_hash,
                    "new_record_hash": change.new_record_hash,
                }
                for change in report.diff.changed_records
            ],
            "content_hash": report.diff.content_hash,
        },
        "validated_at": _datetime_text(report.validated_at),
        "content_hash": report.content_hash,
    }


def _validate_validation_shape(document: dict[str, Any]) -> None:
    _exact_keys(
        document,
        {
            "schema_version",
            "deployment_id",
            "source_id",
            "snapshot",
            "expected_schema_id",
            "passed",
            "reasons",
            "diff",
            "validated_at",
            "content_hash",
        },
    )
    for key in (
        "schema_version",
        "deployment_id",
        "source_id",
        "expected_schema_id",
        "validated_at",
        "content_hash",
    ):
        _string(document[key])
    _reference(document["snapshot"])
    _boolean(document["passed"])
    for item in _list(document["reasons"], _MAX_VALIDATION_REASONS):
        reason = _object(item)
        _exact_keys(reason, {"code", "record_locator", "record_id"})
        _string(reason["code"])
        _optional_string(reason["record_locator"])
        _optional_string(reason["record_id"])
    diff = _object(document["diff"])
    _exact_keys(
        diff,
        {
            "previous_snapshot",
            "new_snapshot",
            "added_record_ids",
            "removed_record_ids",
            "changed_records",
            "content_hash",
        },
    )
    if diff["previous_snapshot"] is not None:
        _reference(diff["previous_snapshot"])
    _reference(diff["new_snapshot"])
    for key in ("added_record_ids", "removed_record_ids"):
        for item in _list(diff[key], MAX_RECORDS_PER_SNAPSHOT):
            _string(item)
    for item in _list(diff["changed_records"], MAX_RECORDS_PER_SNAPSHOT):
        changed = _object(item)
        _exact_keys(
            changed,
            {"source_record_id", "previous_record_hash", "new_record_hash"},
        )
        for key in changed:
            _string(changed[key])
    _string(diff["content_hash"])


def _reference_document(reference: SourceSnapshotRef) -> dict[str, str]:
    return {
        "snapshot_id": reference.snapshot_id,
        "content_hash": reference.content_hash,
    }


def _reference(value: object) -> tuple[str, str]:
    document = _object(value)
    _exact_keys(document, {"snapshot_id", "content_hash"})
    return _string(document["snapshot_id"]), _string(document["content_hash"])


def _raw_reference(value: object) -> tuple[str, str, int]:
    document = _object(value)
    _exact_keys(document, {"object_id", "content_hash", "byte_length"})
    return (
        _string(document["object_id"]),
        _string(document["content_hash"]),
        _integer(document["byte_length"]),
    )


def _native_scalar(value: object) -> str | int | bool | None:
    text = _string(value)
    if len(text.encode("utf-8")) > MAX_NATIVE_VALUE_BYTES:
        raise _InvalidDocument
    try:
        parsed: object = json.loads(
            text,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
    except Exception:
        raise _InvalidDocument from None
    if type(parsed) not in {str, int, bool, type(None)}:
        raise _InvalidDocument
    if _encode_document(parsed, MAX_NATIVE_VALUE_BYTES).decode("utf-8") != text:
        raise _InvalidDocument
    return parsed  # type: ignore[return-value]


def _load_document(value: object, maximum: int) -> dict[str, Any]:
    if type(value) is not bytes or not value or len(value) > maximum:
        raise _InvalidDocument
    if value.startswith(b"\xef\xbb\xbf"):
        raise _InvalidDocument
    try:
        text = value.decode("utf-8", errors="strict")
        parsed: object = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
    except Exception:
        raise _InvalidDocument from None
    _depth(parsed)
    document = _object(parsed)
    if _encode_document(document, maximum) != value:
        raise _InvalidDocument
    return document


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidDocument
        result[key] = value
    return result


def _reject_number(_: str) -> Never:
    raise _InvalidDocument


def _depth(value: object) -> None:
    pending = [(value, 1)]
    while pending:
        current, depth = pending.pop()
        if depth > _MAX_JSON_DEPTH:
            raise _InvalidDocument
        if type(current) is dict:
            pending.extend((member, depth + 1) for member in current.values())
        elif type(current) is list:
            pending.extend((member, depth + 1) for member in current)


def _encode_document(value: object, maximum: int) -> bytes:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(encoded) > maximum:
        raise _InvalidDocument
    return encoded


def _object(value: object) -> dict[str, Any]:
    if type(value) is not dict:
        raise _InvalidDocument
    return value


def _exact_keys(value: dict[str, Any], expected: set[str]) -> None:
    if set(value) != expected:
        raise _InvalidDocument


def _list(value: object, maximum: int) -> list[Any]:
    if type(value) is not list or len(value) > maximum:
        raise _InvalidDocument
    return value


def _string(value: object) -> str:
    if type(value) is not str:
        raise _InvalidDocument
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return _string(value)


def _integer(value: object) -> int:
    if type(value) is not int:
        raise _InvalidDocument
    return value


def _boolean(value: object) -> bool:
    if type(value) is not bool:
        raise _InvalidDocument
    return value


def _datetime_text(value: datetime) -> str:
    if type(value) is not datetime or value.tzinfo is None:
        raise _InvalidDocument
    offset = value.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise _InvalidDocument
    return value.isoformat().replace("+00:00", "Z")


def _datetime(value: object) -> datetime:
    text = _string(value)
    if not text.endswith("Z"):
        raise _InvalidDocument
    try:
        parsed = datetime.fromisoformat(text.removesuffix("Z") + "+00:00")
    except ValueError:
        raise _InvalidDocument from None
    if _datetime_text(parsed) != text or parsed.tzinfo is not UTC:
        raise _InvalidDocument
    return parsed


__all__ = [
    "MAX_SNAPSHOT_DOCUMENT_BYTES",
    "MAX_VALIDATION_DOCUMENT_BYTES",
    "SNAPSHOT_DOCUMENT_SCHEMA_VERSION",
    "VALIDATION_DOCUMENT_SCHEMA_VERSION",
    "SourceSnapshotCodecError",
    "ValidationEvidenceCodecError",
    "decode_snapshot_document",
    "decode_validation_document",
    "encode_snapshot_document",
    "encode_validation_document",
]
