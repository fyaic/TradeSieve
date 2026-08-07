"""Strict persistence-codec evidence for TS-202 Slice C2."""

from __future__ import annotations

import copy
import json
import traceback
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, cast

import pytest

import tradesieve.adapters.source_snapshot_codec as codec
from tradesieve.adapters.source_snapshot_codec import (
    MAX_SNAPSHOT_DOCUMENT_BYTES,
    MAX_VALIDATION_DOCUMENT_BYTES,
    SourceSnapshotCodecError,
    ValidationEvidenceCodecError,
    decode_snapshot_document,
    decode_validation_document,
    encode_snapshot_document,
    encode_validation_document,
)
from tradesieve.domain.source_snapshot import (
    ParsedAssertionInput,
    ParsedRecordInput,
    ParsedSourceSnapshot,
    RawObjectMetadata,
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

NOW = datetime(2026, 8, 7, 2, tzinfo=UTC)


def raw_metadata(
    content: bytes = b'{"synthetic":"source"}',
    *,
    retrieved_at: datetime = NOW,
    source_id: str = "synthetic-source",
) -> RawObjectMetadata:
    return RawObjectMetadata.from_bytes(
        deployment_id="demo-deployment",
        source_id=source_id,
        original_name="synthetic-source.json",
        media_type=SYNTHETIC_MEDIA_TYPE,
        charset=SYNTHETIC_CHARSET,
        retrieved_at=retrieved_at,
        effective_from=retrieved_at - timedelta(days=1),
        content=content,
    )


def record(
    record_id: str | None,
    index: int,
    *values: str | int | bool | None,
) -> ParsedRecordInput:
    return ParsedRecordInput(
        source_record_id=record_id,
        native_locator=f"/records/{index}",
        effective_from="2026-08-01" if index % 2 == 0 else None,
        effective_to=None if index % 2 == 0 else "2026-12-31",
        assertions=tuple(
            ParsedAssertionInput(
                field_name=f"field.{assertion_index}",
                native_locator=f"/records/{index}/values/{assertion_index}",
                native_value=value,
                normalized_value=f"normalized-{assertion_index}",
            )
            for assertion_index, value in enumerate(values)
        ),
    )


def snapshot_for(
    raw: RawObjectMetadata,
    records: tuple[ParsedRecordInput, ...],
    *,
    parsed_at: datetime | None = None,
    parser_id: str = FiniteParserId.SYNTHETIC_JSON_V1.value,
    parser_version: str = SYNTHETIC_PARSER_VERSION,
    schema_id: str = SYNTHETIC_SCHEMA_ID,
    declared_record_count: int | None = None,
) -> ParsedSourceSnapshot:
    return ParsedSourceSnapshot.create(
        raw_metadata=raw,
        parser_id=parser_id,
        parser_version=parser_version,
        schema_id=schema_id,
        declared_record_count=(
            len(records) if declared_record_count is None else declared_record_count
        ),
        parsed_at=parsed_at or raw.retrieved_at + timedelta(minutes=1),
        records=records,
    )


def complex_snapshot() -> tuple[RawObjectMetadata, ParsedSourceSnapshot]:
    raw = raw_metadata()
    parsed = snapshot_for(
        raw,
        (
            record("record-a", 0, "private value", 7),
            record("record-b", 1, True, False, None),
        ),
    )
    return raw, parsed


def changed_snapshots() -> tuple[ParsedSourceSnapshot, ParsedSourceSnapshot]:
    previous_raw = raw_metadata(b"previous", retrieved_at=NOW)
    previous = snapshot_for(
        previous_raw,
        (
            record("removed", 0, "removed"),
            record("changed", 1, "old"),
            record("same", 2, "same"),
        ),
    )
    current_raw = raw_metadata(b"current", retrieved_at=NOW + timedelta(minutes=2))
    current = snapshot_for(
        current_raw,
        (
            record("same", 0, "same"),
            record("added", 1, "added"),
            record("changed", 2, "new"),
        ),
    )
    return previous, current


def canonical(document: object) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def parsed_document(payload: bytes) -> dict[str, Any]:
    value = json.loads(payload)
    assert type(value) is dict
    return value


def decode_snapshot(
    payload: object,
    raw: RawObjectMetadata,
    parsed: ParsedSourceSnapshot,
    **overrides: object,
) -> ParsedSourceSnapshot:
    arguments: dict[str, object] = {
        "deployment_id": parsed.deployment_id,
        "source_id": parsed.source_id,
        "snapshot_id": parsed.snapshot_id,
        "content_hash": parsed.content_hash,
        "raw_metadata": raw,
    }
    arguments.update(overrides)
    return decode_snapshot_document(payload, **arguments)  # type: ignore[arg-type]


def validation_report(
    snapshot: ParsedSourceSnapshot,
    previous: ParsedSourceSnapshot | None,
    *,
    validated_at: datetime | None = None,
) -> ValidationReport:
    return validate_snapshot(
        snapshot,
        expected_schema_id=SYNTHETIC_SCHEMA_ID,
        previous_accepted=previous,
        validated_at=validated_at or snapshot.parsed_at + timedelta(minutes=1),
    )


def decode_validation(
    payload: object,
    report: ValidationReport,
    snapshot: ParsedSourceSnapshot,
    previous: ParsedSourceSnapshot | None,
    **overrides: object,
) -> ValidationReport:
    arguments: dict[str, object] = {
        "deployment_id": snapshot.deployment_id,
        "source_id": snapshot.source_id,
        "snapshot_id": snapshot.snapshot_id,
        "report_content_hash": report.content_hash,
        "expected_schema_id": report.expected_schema_id,
        "validated_at": report.validated_at,
        "snapshot": snapshot,
        "previous_accepted": previous,
    }
    arguments.update(overrides)
    return decode_validation_document(payload, **arguments)  # type: ignore[arg-type]


def assert_snapshot_rejected(
    payload: object, raw: RawObjectMetadata, parsed: ParsedSourceSnapshot
) -> None:
    with pytest.raises(SourceSnapshotCodecError) as caught:
        decode_snapshot(payload, raw, parsed)
    assert str(caught.value) == (
        "source snapshot persistence document failed verification"
    )
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert caught.value.__suppress_context__


def assert_validation_rejected(
    payload: object,
    report: ValidationReport,
    snapshot: ParsedSourceSnapshot,
    previous: ParsedSourceSnapshot | None,
) -> None:
    with pytest.raises(ValidationEvidenceCodecError) as caught:
        decode_validation(payload, report, snapshot, previous)
    assert str(caught.value) == (
        "validation evidence persistence document failed verification"
    )
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert caught.value.__suppress_context__


def test_snapshot_round_trip_is_canonical_deterministic_and_complete() -> None:
    raw, parsed = complex_snapshot()

    first = encode_snapshot_document(parsed, raw_metadata=raw)
    second = encode_snapshot_document(parsed, raw_metadata=raw)
    document = parsed_document(first)

    assert first == second == canonical(document)
    assert len(first) <= MAX_SNAPSHOT_DOCUMENT_BYTES
    assert decode_snapshot(first, raw, parsed) == parsed
    assert document["raw_object"] == {
        "object_id": raw.object_id,
        "content_hash": raw.content_hash,
        "byte_length": raw.byte_length,
    }
    assert document["records"][0]["record_key"] == parsed.records[0].record_key
    assert document["records"][0]["assertions"][0]["assertion_id"] == (
        parsed.records[0].assertions[0].assertion_id
    )
    assert [
        assertion["native_value_json"]
        for source_record in document["records"]
        for assertion in source_record["assertions"]
    ] == ['"private value"', "7", "true", "false", "null"]


def test_snapshot_round_trip_preserves_optional_record_identity() -> None:
    raw = raw_metadata(b"missing-id")
    parsed = snapshot_for(raw, (record(None, 0, "private"),))
    payload = encode_snapshot_document(parsed, raw_metadata=raw)

    assert decode_snapshot(payload, raw, parsed) == parsed


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        "{}",
        bytearray(b"{}"),
        b"",
        b'"JSON string masquerading as a document"',
        b"{",
        b"\xff",
        b"NaN",
        b"1.0",
        canonical([[[[[[[[[None]]]]]]]]]),
    ],
)
def test_snapshot_decoder_rejects_wrong_transport_types_and_unsafe_json(
    payload: object,
) -> None:
    raw, parsed = complex_snapshot()
    assert_snapshot_rejected(payload, raw, parsed)


def test_snapshot_decoder_rejects_bom_trailing_duplicate_and_noncanonical_json() -> (
    None
):
    raw, parsed = complex_snapshot()
    payload = encode_snapshot_document(parsed, raw_metadata=raw)
    document = parsed_document(payload)
    schema_version = document.pop("schema_version")
    unsorted = {"schema_version": schema_version, **document}
    unsorted_bytes = json.dumps(
        unsorted,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=False,
    ).encode("utf-8")
    assert unsorted_bytes != payload

    for corrupt in (
        b"\xef\xbb\xbf" + payload,
        payload + b"\n",
        payload.replace(b'{"content_hash"', b'{ "content_hash"', 1),
        unsorted_bytes,
        b'{"schema_version":"1.0.0","schema_version":"1.0.0"}',
        b'{"raw":{"value":1,"value":2}}',
    ):
        assert_snapshot_rejected(corrupt, raw, parsed)


def test_snapshot_decoder_rejects_unknown_missing_and_wrong_nested_shapes() -> None:
    raw, parsed = complex_snapshot()
    base = parsed_document(encode_snapshot_document(parsed, raw_metadata=raw))
    corruptions: list[dict[str, Any]] = []

    for path in ("root", "raw", "record", "assertion"):
        unknown = copy.deepcopy(base)
        missing = copy.deepcopy(base)
        if path == "root":
            unknown["unknown"] = True
            missing.pop("content_hash")
        elif path == "raw":
            unknown["raw_object"]["unknown"] = True
            missing["raw_object"].pop("byte_length")
        elif path == "record":
            unknown["records"][0]["unknown"] = True
            missing["records"][0].pop("record_key")
        else:
            unknown["records"][0]["assertions"][0]["unknown"] = True
            missing["records"][0]["assertions"][0].pop("assertion_id")
        corruptions.extend((unknown, missing))

    wrong_records = copy.deepcopy(base)
    wrong_records["records"] = "not-an-array"
    wrong_record = copy.deepcopy(base)
    wrong_record["records"][0] = []
    wrong_assertions = copy.deepcopy(base)
    wrong_assertions["records"][0]["assertions"] = "not-an-array"
    wrong_assertion = copy.deepcopy(base)
    wrong_assertion["records"][0]["assertions"][0] = []
    corruptions.extend((wrong_records, wrong_record, wrong_assertions, wrong_assertion))

    for corruption in corruptions:
        assert_snapshot_rejected(canonical(corruption), raw, parsed)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("schema_version",), "2.0.0"),
        (("deployment_id",), "other-deployment"),
        (("source_id",), "other-source"),
        (("snapshot_id",), "snapshot-corrupt"),
        (("content_hash",), "sha256:" + "0" * 64),
        (("raw_object", "object_id"), "raw-corrupt"),
        (("raw_object", "content_hash"), "sha256:" + "0" * 64),
        (("raw_object", "byte_length"), True),
        (("parser_id",), "other-parser"),
        (("parser_version",), "2.0.0"),
        (("schema_id",), "other-schema"),
        (("declared_record_count",), True),
        (("declared_record_count",), 99),
        (("parsed_at",), "2026-08-07T02:01:00+00:00"),
        (("parsed_at",), "not-a-timeZ"),
        (("parsed_at",), "2026-08-07T02:01:00.000000Z"),
        (("records", 0, "record_key"), "record-corrupt"),
        (("records", 0, "snapshot_id"), "snapshot-corrupt"),
        (("records", 0, "raw_object_id"), "raw-corrupt"),
        (("records", 0, "source_record_id"), "different-record"),
        (("records", 0, "native_locator"), "/different"),
        (("records", 0, "native_locator"), "not-a-pointer"),
        (("records", 0, "effective_from"), "2026-08-02"),
        (("records", 0, "effective_from"), "not-a-date"),
        (("records", 0, "effective_to"), "2026-12-31"),
        (("records", 0, "assertions", 0, "assertion_id"), "assertion-corrupt"),
        (("records", 0, "assertions", 0, "snapshot_id"), "snapshot-corrupt"),
        (("records", 0, "assertions", 0, "raw_object_id"), "raw-corrupt"),
        (
            ("records", 0, "assertions", 0, "source_record_id"),
            "different-record",
        ),
        (("records", 0, "assertions", 0, "field_name"), "other.field"),
        (("records", 0, "assertions", 0, "field_name"), "unsafe field"),
        (("records", 0, "assertions", 0, "native_locator"), "/different"),
        (("records", 0, "assertions", 0, "native_locator"), "not-a-pointer"),
        (("records", 0, "assertions", 0, "native_value_json"), '"changed"'),
        (("records", 0, "assertions", 0, "native_value_json"), "{"),
        (("records", 0, "assertions", 0, "normalized_value"), "changed"),
        (("records", 0, "assertions", 0, "normalized_value"), ""),
        (("records", 0, "assertions", 0, "parser_id"), "other-parser"),
        (("records", 0, "assertions", 0, "parser_version"), "2.0.0"),
    ],
)
def test_snapshot_decoder_rejects_every_materialized_field_corruption(
    path: tuple[str | int, ...], value: object
) -> None:
    raw, parsed = complex_snapshot()
    document: Any = parsed_document(encode_snapshot_document(parsed, raw_metadata=raw))
    target = document
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    assert_snapshot_rejected(canonical(document), raw, parsed)


def test_snapshot_decoder_rejects_order_native_scalar_and_collection_corruption() -> (
    None
):
    raw, parsed = complex_snapshot()
    base = parsed_document(encode_snapshot_document(parsed, raw_metadata=raw))

    reversed_records = copy.deepcopy(base)
    reversed_records["records"].reverse()
    reversed_assertions = copy.deepcopy(base)
    reversed_assertions["records"][0]["assertions"].reverse()
    noncanonical_scalar = copy.deepcopy(base)
    noncanonical_scalar["records"][0]["assertions"][0]["native_value_json"] = (
        '"private \\u0076alue"'
    )
    composite_scalar = copy.deepcopy(base)
    composite_scalar["records"][0]["assertions"][0]["native_value_json"] = "{}"
    float_scalar = copy.deepcopy(base)
    float_scalar["records"][0]["assertions"][0]["native_value_json"] = "1.0"
    nonfinite_scalar = copy.deepcopy(base)
    nonfinite_scalar["records"][0]["assertions"][0]["native_value_json"] = "NaN"
    oversized_scalar = copy.deepcopy(base)
    oversized_scalar["records"][0]["assertions"][0]["native_value_json"] = (
        '"' + "x" * 4_097 + '"'
    )
    too_many_records = copy.deepcopy(base)
    too_many_records["records"] = [base["records"][0]] * 513
    too_many_assertions = copy.deepcopy(base)
    too_many_assertions["records"][0]["assertions"] = [
        base["records"][0]["assertions"][0]
    ] * 65

    for corruption in (
        reversed_records,
        reversed_assertions,
        noncanonical_scalar,
        composite_scalar,
        float_scalar,
        nonfinite_scalar,
        oversized_scalar,
        too_many_records,
        too_many_assertions,
    ):
        assert_snapshot_rejected(canonical(corruption), raw, parsed)


def test_snapshot_decoder_rejects_every_row_and_raw_metadata_mismatch() -> None:
    raw, parsed = complex_snapshot()
    payload = encode_snapshot_document(parsed, raw_metadata=raw)

    for overrides in (
        {"deployment_id": "other-deployment"},
        {"source_id": "other-source"},
        {"snapshot_id": "snapshot-corrupt"},
        {"content_hash": "sha256:" + "0" * 64},
        {"deployment_id": True},
        {"raw_metadata": raw_metadata(b"different")},
        {"raw_metadata": cast(RawObjectMetadata, object())},
    ):
        with pytest.raises(SourceSnapshotCodecError):
            decode_snapshot(payload, raw, parsed, **overrides)

    corrupted_raw = copy.copy(raw)
    object.__setattr__(corrupted_raw, "media_type", "text/plain")
    with pytest.raises(SourceSnapshotCodecError):
        decode_snapshot(payload, corrupted_raw, parsed)


def test_snapshot_encoder_rejects_untyped_corrupt_and_nonfinite_binding() -> None:
    raw, parsed = complex_snapshot()

    with pytest.raises(SourceSnapshotCodecError):
        encode_snapshot_document(cast(ParsedSourceSnapshot, object()), raw_metadata=raw)
    with pytest.raises(SourceSnapshotCodecError):
        encode_snapshot_document(parsed, raw_metadata=cast(RawObjectMetadata, object()))

    corrupt = copy.copy(parsed)
    object.__setattr__(corrupt, "content_hash", "sha256:" + "0" * 64)
    with pytest.raises(SourceSnapshotCodecError):
        encode_snapshot_document(corrupt, raw_metadata=raw)

    inputs = (
        record("record-a", 0, "private value", 7),
        record("record-b", 1, True, False, None),
    )
    for drift in (
        snapshot_for(raw, inputs, parser_id="other-parser"),
        snapshot_for(raw, inputs, parser_version="2.0.0"),
        snapshot_for(raw, inputs, schema_id="other-schema"),
    ):
        with pytest.raises(SourceSnapshotCodecError):
            encode_snapshot_document(drift, raw_metadata=raw)


def test_snapshot_document_limit_is_enforced_on_both_sides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw, parsed = complex_snapshot()
    payload = encode_snapshot_document(parsed, raw_metadata=raw)
    assert_snapshot_rejected(b" " * (MAX_SNAPSHOT_DOCUMENT_BYTES + 1), raw, parsed)

    monkeypatch.setattr(codec, "MAX_SNAPSHOT_DOCUMENT_BYTES", len(payload))
    assert encode_snapshot_document(parsed, raw_metadata=raw) == payload
    monkeypatch.setattr(codec, "MAX_SNAPSHOT_DOCUMENT_BYTES", len(payload) - 1)
    with pytest.raises(SourceSnapshotCodecError):
        encode_snapshot_document(parsed, raw_metadata=raw)


def test_validation_round_trip_recomputes_full_diff_and_failure_evidence() -> None:
    previous, current = changed_snapshots()
    report = validation_report(current, previous)
    payload = encode_validation_document(
        report, snapshot=current, previous_accepted=previous
    )
    document = parsed_document(payload)

    assert payload == canonical(document)
    assert len(payload) <= MAX_VALIDATION_DOCUMENT_BYTES
    assert decode_validation(payload, report, current, previous) == report
    assert document["passed"] is False
    assert document["diff"]["previous_snapshot"] == {
        "snapshot_id": previous.snapshot_id,
        "content_hash": previous.content_hash,
    }
    assert document["diff"]["added_record_ids"] == ["added"]
    assert document["diff"]["removed_record_ids"] == ["removed"]
    assert document["diff"]["changed_records"][0]["source_record_id"] == "changed"


def test_validation_round_trip_without_predecessor_covers_passing_evidence() -> None:
    _, current = complex_snapshot()
    report = validation_report(current, None)
    payload = encode_validation_document(
        report, snapshot=current, previous_accepted=None
    )

    assert report.passed
    assert decode_validation(payload, report, current, None) == report
    assert parsed_document(payload)["diff"]["previous_snapshot"] is None


def test_validation_decoder_rejects_wrong_transport_and_size() -> None:
    previous, current = changed_snapshots()
    report = validation_report(current, previous)
    payload = encode_validation_document(
        report, snapshot=current, previous_accepted=previous
    )

    corrupt_payloads: tuple[object, ...] = (
        None,
        {},
        payload.decode("utf-8"),
        bytearray(payload),
        b"",
        b"\xef\xbb\xbf" + payload,
        payload + b" ",
        b"{",
        b"\xff",
        b"NaN",
        b"1.0",
        canonical([[[[[[[[[None]]]]]]]]]),
        b" " * (MAX_VALIDATION_DOCUMENT_BYTES + 1),
    )
    for corrupt in corrupt_payloads:
        assert_validation_rejected(corrupt, report, current, previous)


def test_validation_decoder_rejects_unknown_missing_and_wrong_nested_shapes() -> None:
    previous, current = changed_snapshots()
    report = validation_report(current, previous)
    base = parsed_document(
        encode_validation_document(report, snapshot=current, previous_accepted=previous)
    )
    corruptions: list[dict[str, Any]] = []

    locations = (
        (base, "content_hash"),
        (base["snapshot"], "content_hash"),
        (base["reasons"][0], "record_id"),
        (base["diff"], "content_hash"),
        (base["diff"]["previous_snapshot"], "content_hash"),
        (base["diff"]["new_snapshot"], "content_hash"),
        (base["diff"]["changed_records"][0], "new_record_hash"),
    )
    for original, member in locations:
        unknown = copy.deepcopy(base)
        missing = copy.deepcopy(base)
        path = _find_mapping_path(base, original)
        _mapping_at(unknown, path)["unknown"] = True
        _mapping_at(missing, path).pop(member)
        corruptions.extend((unknown, missing))

    wrong_reasons = copy.deepcopy(base)
    wrong_reasons["reasons"] = "not-an-array"
    wrong_reason = copy.deepcopy(base)
    wrong_reason["reasons"][0] = []
    wrong_diff = copy.deepcopy(base)
    wrong_diff["diff"] = []
    wrong_changed = copy.deepcopy(base)
    wrong_changed["diff"]["changed_records"][0] = []
    corruptions.extend((wrong_reasons, wrong_reason, wrong_diff, wrong_changed))

    for corruption in corruptions:
        assert_validation_rejected(canonical(corruption), report, current, previous)

    for duplicate in (
        b'{"schema_version":"1.0.0","schema_version":"1.0.0"}',
        b'{"diff":{"content_hash":"a","content_hash":"b"}}',
    ):
        assert_validation_rejected(duplicate, report, current, previous)


def _find_mapping_path(root: object, target: object) -> tuple[str | int, ...]:
    pending: list[tuple[object, tuple[str | int, ...]]] = [(root, ())]
    while pending:
        value, path = pending.pop()
        if value is target:
            return path
        if type(value) is dict:
            pending.extend((member, (*path, key)) for key, member in value.items())
        elif type(value) is list:
            pending.extend(
                (member, (*path, index)) for index, member in enumerate(value)
            )
    raise AssertionError("mapping not found")


def _mapping_at(root: dict[str, Any], path: tuple[str | int, ...]) -> dict[str, Any]:
    value: Any = root
    for key in path:
        value = value[key]
    assert type(value) is dict
    return value


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("schema_version",), "2.0.0"),
        (("deployment_id",), "other-deployment"),
        (("source_id",), "other-source"),
        (("snapshot", "snapshot_id"), "snapshot-corrupt"),
        (("snapshot", "content_hash"), "sha256:" + "0" * 64),
        (("expected_schema_id",), "other-schema"),
        (("passed",), True),
        (("passed",), 1),
        (("validated_at",), "2026-08-07T02:04:00+00:00"),
        (("validated_at",), "not-a-timeZ"),
        (("content_hash",), "sha256:" + "0" * 64),
        (("reasons", 0, "code"), "EMPTY_SNAPSHOT"),
        (("reasons", 0, "record_locator"), "/private/locator"),
        (("reasons", 0, "record_id"), "other-record"),
        (("diff", "previous_snapshot", "snapshot_id"), "snapshot-corrupt"),
        (
            ("diff", "previous_snapshot", "content_hash"),
            "sha256:" + "0" * 64,
        ),
        (("diff", "new_snapshot", "snapshot_id"), "snapshot-corrupt"),
        (("diff", "new_snapshot", "content_hash"), "sha256:" + "0" * 64),
        (("diff", "added_record_ids"), ["added", "added"]),
        (("diff", "removed_record_ids"), ["removed", "removed"]),
        (
            ("diff", "changed_records", 0, "source_record_id"),
            "other-record",
        ),
        (
            ("diff", "changed_records", 0, "previous_record_hash"),
            "sha256:" + "0" * 64,
        ),
        (
            ("diff", "changed_records", 0, "new_record_hash"),
            "sha256:" + "0" * 64,
        ),
        (("diff", "content_hash"), "sha256:" + "0" * 64),
    ],
)
def test_validation_decoder_rejects_every_report_and_diff_corruption(
    path: tuple[str | int, ...], value: object
) -> None:
    previous, current = changed_snapshots()
    report = validation_report(current, previous)
    document: Any = parsed_document(
        encode_validation_document(report, snapshot=current, previous_accepted=previous)
    )
    target = document
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    assert_validation_rejected(canonical(document), report, current, previous)


def test_validation_decoder_rejects_collection_order_bounds_and_wrong_types() -> None:
    previous, current = changed_snapshots()
    report = validation_report(current, previous)
    base = parsed_document(
        encode_validation_document(report, snapshot=current, previous_accepted=previous)
    )
    reversed_reasons = copy.deepcopy(base)
    reversed_reasons["reasons"] = [
        *reversed_reasons["reasons"],
        {**reversed_reasons["reasons"][0], "code": "SCHEMA_MISMATCH"},
    ]
    reversed_reasons["reasons"].reverse()
    wrong_added = copy.deepcopy(base)
    wrong_added["diff"]["added_record_ids"] = [True]
    wrong_removed = copy.deepcopy(base)
    wrong_removed["diff"]["removed_record_ids"] = "removed"
    wrong_changed = copy.deepcopy(base)
    wrong_changed["diff"]["changed_records"] = "changed"
    no_previous = copy.deepcopy(base)
    no_previous["diff"]["previous_snapshot"] = None
    too_many_added = copy.deepcopy(base)
    too_many_added["diff"]["added_record_ids"] = ["record"] * 513
    too_many_changed = copy.deepcopy(base)
    too_many_changed["diff"]["changed_records"] = [
        base["diff"]["changed_records"][0]
    ] * 513

    for corruption in (
        reversed_reasons,
        wrong_added,
        wrong_removed,
        wrong_changed,
        no_previous,
        too_many_added,
        too_many_changed,
    ):
        assert_validation_rejected(canonical(corruption), report, current, previous)


def test_validation_decoder_rejects_noncanonical_reason_and_diff_order() -> None:
    previous_raw = raw_metadata(b"ordered-previous")
    previous = snapshot_for(
        previous_raw,
        (
            record("z-removed", 0, "z"),
            record("a-removed", 1, "a"),
            record("same", 2, "same"),
        ),
    )
    current_raw = raw_metadata(
        b"ordered-current", retrieved_at=NOW + timedelta(minutes=2)
    )
    current = snapshot_for(current_raw, (record("same", 2, "same"),))
    report = validation_report(current, previous)
    base = parsed_document(
        encode_validation_document(report, snapshot=current, previous_accepted=previous)
    )
    assert [reason["record_id"] for reason in base["reasons"]] == [
        "a-removed",
        "z-removed",
    ]

    wrong_reason_order = copy.deepcopy(base)
    wrong_reason_order["reasons"].reverse()
    wrong_diff_order = copy.deepcopy(base)
    wrong_diff_order["diff"]["removed_record_ids"].reverse()
    for corruption in (wrong_reason_order, wrong_diff_order):
        assert_validation_rejected(canonical(corruption), report, current, previous)


def test_validation_decoder_uses_only_separately_loaded_exact_predecessor() -> None:
    previous, current = changed_snapshots()
    report = validation_report(current, previous)
    payload = encode_validation_document(
        report, snapshot=current, previous_accepted=previous
    )

    with pytest.raises(ValidationEvidenceCodecError):
        decode_validation(payload, report, current, None)
    unrelated = snapshot_for(
        raw_metadata(b"unrelated", source_id="other-source"),
        (record("other", 0, "other"),),
    )
    with pytest.raises(ValidationEvidenceCodecError):
        decode_validation(payload, report, current, unrelated)
    corrupt_previous = copy.copy(previous)
    object.__setattr__(corrupt_previous, "content_hash", "sha256:" + "0" * 64)
    with pytest.raises(ValidationEvidenceCodecError):
        decode_validation(payload, report, current, corrupt_previous)
    with pytest.raises(ValidationEvidenceCodecError):
        decode_validation(
            payload,
            report,
            current,
            cast(ParsedSourceSnapshot, object()),
        )


def test_validation_decoder_rejects_every_row_or_snapshot_disagreement() -> None:
    previous, current = changed_snapshots()
    report = validation_report(current, previous)
    payload = encode_validation_document(
        report, snapshot=current, previous_accepted=previous
    )

    for overrides in (
        {"deployment_id": "other-deployment"},
        {"source_id": "other-source"},
        {"snapshot_id": "snapshot-corrupt"},
        {"report_content_hash": "sha256:" + "0" * 64},
        {"expected_schema_id": "other-schema"},
        {"validated_at": report.validated_at + timedelta(seconds=1)},
        {"deployment_id": True},
        {"validated_at": report.validated_at.replace(tzinfo=None)},
        {"validated_at": report.validated_at.astimezone(timezone(timedelta(hours=8)))},
    ):
        with pytest.raises(ValidationEvidenceCodecError):
            decode_validation(payload, report, current, previous, **overrides)

    with pytest.raises(ValidationEvidenceCodecError):
        decode_validation_document(
            payload,
            deployment_id=current.deployment_id,
            source_id=current.source_id,
            snapshot_id=current.snapshot_id,
            report_content_hash=report.content_hash,
            expected_schema_id=report.expected_schema_id,
            validated_at=report.validated_at,
            snapshot=cast(ParsedSourceSnapshot, object()),
            previous_accepted=previous,
        )

    other_raw = raw_metadata(b"other-current")
    other = snapshot_for(other_raw, (record("other", 0, "other"),))
    with pytest.raises(ValidationEvidenceCodecError):
        decode_validation(payload, report, other, previous)


def test_validation_encoder_rejects_untyped_recomputed_and_binding_corruption() -> None:
    previous, current = changed_snapshots()
    report = validation_report(current, previous)

    with pytest.raises(ValidationEvidenceCodecError):
        encode_validation_document(
            cast(ValidationReport, object()),
            snapshot=current,
            previous_accepted=previous,
        )
    forged_report = copy.copy(report)
    object.__setattr__(forged_report, "content_hash", "sha256:" + "0" * 64)
    with pytest.raises(ValidationEvidenceCodecError):
        encode_validation_document(
            forged_report, snapshot=current, previous_accepted=previous
        )
    with pytest.raises(ValidationEvidenceCodecError):
        encode_validation_document(
            report,
            snapshot=cast(ParsedSourceSnapshot, object()),
            previous_accepted=previous,
        )

    drift_raw = raw_metadata(b"drift")
    drift = snapshot_for(drift_raw, (record("drift", 0, "drift"),), schema_id="drift")
    drift_report = validation_report(drift, None)
    with pytest.raises(ValidationEvidenceCodecError):
        encode_validation_document(drift_report, snapshot=drift, previous_accepted=None)


def test_validation_document_limit_is_enforced_on_both_sides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous, current = changed_snapshots()
    report = validation_report(current, previous)
    payload = encode_validation_document(
        report, snapshot=current, previous_accepted=previous
    )

    monkeypatch.setattr(codec, "MAX_VALIDATION_DOCUMENT_BYTES", len(payload))
    assert (
        encode_validation_document(report, snapshot=current, previous_accepted=previous)
        == payload
    )
    monkeypatch.setattr(codec, "MAX_VALIDATION_DOCUMENT_BYTES", len(payload) - 1)
    with pytest.raises(ValidationEvidenceCodecError):
        encode_validation_document(report, snapshot=current, previous_accepted=previous)


def test_codec_failures_are_bounded_redacted_and_hide_nested_exceptions() -> None:
    private_value = "PRIVATE-NATIVE-VALUE-91d3"
    private_locator = "/private/source/locator/91d3"
    private_path = "/Users/private/source.json"
    database_text = "SELECT secret FROM private_payload"
    raw = raw_metadata(b"private")
    parsed = snapshot_for(
        raw,
        (
            ParsedRecordInput(
                None,
                private_locator,
                None,
                None,
                (
                    ParsedAssertionInput(
                        "field.private",
                        f"{private_locator}/value",
                        private_value,
                        "private-normalized",
                    ),
                ),
            ),
        ),
    )
    payload = encode_snapshot_document(parsed, raw_metadata=raw)
    malicious = payload[:-1] + (
        f'{private_path}{database_text}{private_value}"'.encode()
    )

    with pytest.raises(SourceSnapshotCodecError) as caught:
        decode_snapshot(malicious, raw, parsed)
    rendered = "".join(
        traceback.format_exception(type(caught.value), caught.value, caught.tb)
    )
    for secret in (
        private_value,
        private_locator,
        private_path,
        database_text,
        malicious.decode("utf-8", errors="ignore"),
    ):
        assert secret not in str(caught.value)
        assert secret not in rendered
    assert len(str(caught.value)) < 100
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert caught.value.__suppress_context__

    report = validation_report(parsed, None)
    validation_payload = encode_validation_document(
        report, snapshot=parsed, previous_accepted=None
    )
    validation_body = parsed_document(validation_payload)
    validation_body["reasons"][0]["code"] = f"{private_path}{database_text}"
    malicious_validation = canonical(validation_body)
    with pytest.raises(ValidationEvidenceCodecError) as validation_caught:
        decode_validation(malicious_validation, report, parsed, None)
    validation_rendered = "".join(
        traceback.format_exception(
            type(validation_caught.value),
            validation_caught.value,
            validation_caught.tb,
        )
    )
    for secret in (
        private_value,
        private_locator,
        private_path,
        database_text,
        malicious_validation.decode("utf-8"),
    ):
        assert secret not in str(validation_caught.value)
        assert secret not in validation_rendered
    assert len(str(validation_caught.value)) < 100
    assert validation_caught.value.__cause__ is None
    assert validation_caught.value.__context__ is None
    assert validation_caught.value.__suppress_context__
