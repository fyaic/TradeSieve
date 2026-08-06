"""Unit evidence for immutable source-snapshot domain primitives."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from tradesieve.domain.source_snapshot import (
    AUTHORIZATION_OPERATION_BY_EVENT_TYPE,
    MAX_ASSERTIONS_PER_RECORD,
    MAX_CANONICAL_SNAPSHOT_BYTES,
    MAX_LIFECYCLE_EVENTS,
    MAX_NATIVE_VALUE_BYTES,
    MAX_QUERY_LIMIT,
    MAX_RAW_OBJECT_BYTES,
    MAX_RECORDS_PER_SNAPSHOT,
    MAX_SEQUENCE,
    ChangedRecord,
    InvalidSourceSnapshotTransition,
    LifecycleActorType,
    ParsedAssertionInput,
    ParsedRecordInput,
    ParsedSourceSnapshot,
    RawObjectIntegrityError,
    RawObjectMetadata,
    RawObjectRef,
    RawObjectState,
    RawObjectStateRecord,
    SnapshotCommandAuditRecord,
    SnapshotCommandOutcome,
    SnapshotCommandReason,
    SnapshotStateRecord,
    SourceSnapshotDiff,
    SourceSnapshotEventType,
    SourceSnapshotIntegrityError,
    SourceSnapshotLifecycle,
    SourceSnapshotLifecycleEvent,
    SourceSnapshotRef,
    SourceSnapshotState,
    ValidationReason,
    ValidationReasonCode,
    ValidationReport,
    bytes_sha256,
    canonical_sha256,
    fold_source_snapshot_events,
    require_query_limit,
    semantically_applied_lifecycle_transition,
    validate_atomic_command_audit,
    validate_lifecycle_projection,
    validate_snapshot,
    verify_raw_bytes,
)

NOW = datetime(2026, 8, 6, 12, tzinfo=UTC)
RAW_BYTES = b'{"declared_count":1,"records":[]}'


def raw_metadata(content: bytes = RAW_BYTES) -> RawObjectMetadata:
    return RawObjectMetadata.from_bytes(
        deployment_id="demo-deployment",
        source_id="synthetic-source",
        original_name="synthetic-source.json",
        media_type="application/json",
        charset="utf-8",
        retrieved_at=NOW,
        effective_from=NOW - timedelta(days=1),
        content=content,
    )


def record_input(
    record_id: str | None = "record-1",
    *,
    native_value: str | int | bool | None = "SYNTHETIC ENTITY",
) -> ParsedRecordInput:
    return ParsedRecordInput(
        source_record_id=record_id,
        native_locator="/records/0",
        effective_from="2026-08-01",
        effective_to=None,
        assertions=(
            ParsedAssertionInput(
                field_name="entity.name",
                native_locator="/records/0/name",
                native_value=native_value,
                normalized_value=str(native_value),
            ),
        ),
    )


def snapshot(
    records: tuple[ParsedRecordInput, ...] | None = None,
    *,
    raw: RawObjectMetadata | None = None,
    schema_id: str = "synthetic-source-v1",
    parser_version: str = "1.0.0",
    declared_count: int | None = None,
    parsed_at: datetime | None = None,
) -> ParsedSourceSnapshot:
    records = records if records is not None else (record_input(),)
    return ParsedSourceSnapshot.create(
        raw_metadata=raw or raw_metadata(),
        parser_id="synthetic-json-v1",
        parser_version=parser_version,
        schema_id=schema_id,
        declared_record_count=(
            len(records) if declared_count is None else declared_count
        ),
        parsed_at=parsed_at or NOW + timedelta(minutes=1),
        records=records,
    )


def test_raw_metadata_derives_identity_and_hash_from_bytes_and_verifies_reads() -> None:
    metadata = raw_metadata()

    assert metadata.content_hash == bytes_sha256(RAW_BYTES)
    assert metadata.byte_length == len(RAW_BYTES)
    assert metadata.object_id.startswith("raw-")
    assert metadata.recomputed_object_id() == metadata.object_id
    assert metadata.reference().content_hash == metadata.content_hash
    assert verify_raw_bytes(metadata, RAW_BYTES) is RAW_BYTES
    assert raw_metadata().object_id == metadata.object_id
    assert raw_metadata(RAW_BYTES + b" ").object_id != metadata.object_id


@pytest.mark.parametrize("content", [None, b"", RAW_BYTES + b" "])
def test_raw_reads_fail_closed_when_missing_or_hash_mismatched(
    content: bytes | None,
) -> None:
    with pytest.raises(RawObjectIntegrityError):
        verify_raw_bytes(raw_metadata(), content)


def test_raw_reads_reject_joint_storage_key_and_object_id_tampering() -> None:
    metadata = raw_metadata()
    object.__setattr__(metadata, "object_id", "raw-corrupt")

    assert metadata.recomputed_object_id() != metadata.object_id
    with pytest.raises(RawObjectIntegrityError):
        verify_raw_bytes(metadata, RAW_BYTES)


def test_raw_verification_normalizes_malformed_metadata_to_integrity_failure() -> None:
    metadata = raw_metadata()
    object.__setattr__(metadata, "retrieved_at", "not-a-datetime")

    with pytest.raises(RawObjectIntegrityError):
        verify_raw_bytes(metadata, RAW_BYTES)


def test_snapshot_creation_rejects_tampered_raw_metadata_identity() -> None:
    metadata = raw_metadata()
    object.__setattr__(metadata, "object_id", "raw-corrupt")

    with pytest.raises(RawObjectIntegrityError):
        snapshot(raw=metadata)


def test_raw_metadata_and_snapshot_are_frozen_and_have_no_public_hash_input() -> None:
    metadata = raw_metadata()
    parsed = snapshot()

    with pytest.raises(FrozenInstanceError):
        metadata.content_hash = "sha256:" + "0" * 64  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        parsed.snapshot_id = "snapshot-forged"  # type: ignore[misc]
    with pytest.raises(TypeError):
        RawObjectMetadata.from_bytes(  # type: ignore[call-arg]
            deployment_id="demo-deployment",
            source_id="synthetic-source",
            original_name="synthetic-source.json",
            media_type="application/json",
            charset="utf-8",
            retrieved_at=NOW,
            effective_from=None,
            content=RAW_BYTES,
            content_hash="sha256:" + "0" * 64,
        )


def test_snapshot_hash_is_deterministic_and_assertions_retain_full_provenance() -> None:
    first = snapshot()
    second = snapshot()
    record = first.records[0]
    assertion = record.assertions[0]

    assert first == second
    assert first.content_hash == first.recomputed_content_hash()
    first.verify_integrity()
    assert first.snapshot_id == f"snapshot-{first.content_hash.removeprefix('sha256:')}"
    assert record.snapshot_id == first.snapshot_id
    assert record.raw_object_id == first.raw_object.object_id
    assert record.source_record_id == "record-1"
    assert record.native_locator == "/records/0"
    assert assertion.snapshot_id == first.snapshot_id
    assert assertion.raw_object_id == first.raw_object.object_id
    assert assertion.source_record_id == "record-1"
    assert assertion.native_locator == "/records/0/name"
    assert assertion.native_value_json == '"SYNTHETIC ENTITY"'
    assert assertion.parser_id == first.parser_id
    assert assertion.parser_version == first.parser_version
    assert assertion.content_hash().startswith("sha256:")
    assert record.content_hash().startswith("sha256:")


@pytest.mark.parametrize(
    "corruption",
    ["snapshot_id", "record_key", "assertion_id", "assertion_provenance"],
)
def test_snapshot_integrity_rejects_derived_identity_or_provenance_corruption(
    corruption: str,
) -> None:
    parsed = snapshot()
    record = parsed.records[0]
    assertion = record.assertions[0]
    if corruption == "snapshot_id":
        object.__setattr__(parsed, "snapshot_id", "snapshot-corrupt")
    elif corruption == "record_key":
        object.__setattr__(record, "record_key", "record-corrupt")
    elif corruption == "assertion_id":
        object.__setattr__(assertion, "assertion_id", "assertion-corrupt")
    else:
        object.__setattr__(assertion, "snapshot_id", "snapshot-corrupt")

    with pytest.raises(SourceSnapshotIntegrityError):
        parsed.verify_integrity()


@pytest.mark.parametrize(
    "corruption",
    ["snapshot_id", "record_key", "assertion_id", "assertion_provenance"],
)
def test_validation_blocks_internal_identity_corruption(corruption: str) -> None:
    parsed = snapshot()
    if corruption == "snapshot_id":
        object.__setattr__(parsed, "snapshot_id", "snapshot-corrupt")
    elif corruption == "record_key":
        object.__setattr__(parsed.records[0], "record_key", "record-corrupt")
    elif corruption == "assertion_id":
        object.__setattr__(
            parsed.records[0].assertions[0], "assertion_id", "assertion-corrupt"
        )
    else:
        object.__setattr__(
            parsed.records[0].assertions[0], "snapshot_id", "snapshot-corrupt"
        )

    report = validate_snapshot(
        parsed,
        expected_schema_id="synthetic-source-v1",
        previous_accepted=None,
        validated_at=NOW + timedelta(minutes=2),
    )

    assert ValidationReasonCode.SNAPSHOT_HASH_MISMATCH in {
        reason.code for reason in report.reasons
    }


@pytest.mark.parametrize(
    "value",
    ["native", 7, True, False, None],
)
def test_native_json_scalar_types_are_preserved_canonically(
    value: str | int | bool | None,
) -> None:
    assertion = snapshot((record_input(native_value=value),)).records[0].assertions[0]

    expected = {
        "native": '"native"',
        7: "7",
        True: "true",
        False: "false",
        None: "null",
    }
    assert assertion.native_value_json == expected[value]


@pytest.mark.parametrize("value", [1.5, {"unsafe": "object"}, ["unsafe"]])
def test_native_values_reject_floats_and_composites(value: object) -> None:
    with pytest.raises(ValueError, match="JSON scalar"):
        ParsedAssertionInput(
            field_name="entity.name",
            native_locator="/records/0/name",
            native_value=value,  # type: ignore[arg-type]
            normalized_value="unsafe",
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("original_name", "../unsafe.json", "plain object name"),
        ("media_type", "Application/JSON", "lowercase media type"),
        ("charset", "utf 8", "charset token"),
        ("retrieved_at", datetime(2026, 8, 6, 12), "UTC datetime"),
        (
            "retrieved_at",
            datetime(2026, 8, 6, 12, tzinfo=UTC).astimezone(
                timezone(timedelta(hours=8))
            ),
            "UTC datetime",
        ),
    ],
)
def test_raw_metadata_rejects_unsafe_names_types_and_non_utc_times(
    field: str, value: object, message: str
) -> None:
    arguments: dict[str, object] = {
        "deployment_id": "demo-deployment",
        "source_id": "synthetic-source",
        "original_name": "synthetic-source.json",
        "media_type": "application/json",
        "charset": "utf-8",
        "retrieved_at": NOW,
        "effective_from": None,
        "content": RAW_BYTES,
    }
    arguments[field] = value
    with pytest.raises(ValueError, match=message):
        RawObjectMetadata.from_bytes(**arguments)  # type: ignore[arg-type]


def test_raw_object_and_snapshot_count_bounds_are_enforced() -> None:
    with pytest.raises(ValueError, match="object-size bound"):
        raw_metadata(b"x" * (MAX_RAW_OBJECT_BYTES + 1))

    assertion = ParsedAssertionInput("name", "/name", "x", "x")
    with pytest.raises(ValueError, match="assertion-count bound"):
        ParsedRecordInput(
            "record-1",
            "/records/0",
            None,
            None,
            (assertion,) * (MAX_ASSERTIONS_PER_RECORD + 1),
        )

    record = record_input()
    with pytest.raises(ValueError, match="record bound"):
        snapshot((record,) * (MAX_RECORDS_PER_SNAPSHOT + 1))


def test_native_value_and_locator_bounds_are_enforced() -> None:
    with pytest.raises(ValueError, match="encoded byte limit"):
        ParsedAssertionInput(
            "name",
            "/name",
            "x" * MAX_NATIVE_VALUE_BYTES,
            "safe",
        )
    with pytest.raises(ValueError, match="JSON Pointer"):
        ParsedAssertionInput("name", "https://unsafe.example", "x", "safe")
    with pytest.raises(ValueError, match="JSON Pointer"):
        ParsedAssertionInput("name", "/bad~escape", "x", "safe")


def test_canonical_sha256_is_order_independent_and_rejects_non_json_content() -> None:
    assert canonical_sha256({"b": 2, "a": 1}) == canonical_sha256({"a": 1, "b": 2})
    with pytest.raises(ValueError, match="canonical JSON"):
        canonical_sha256({"bad": object()})


def test_derived_identities_change_with_material_content() -> None:
    first = snapshot()
    changed = snapshot((record_input(native_value="CHANGED"),))

    assert first.snapshot_id != changed.snapshot_id
    assert first.content_hash != changed.content_hash
    assert first.records[0].content_hash() != changed.records[0].content_hash()


def test_validation_report_is_computed_and_first_diff_is_deterministic() -> None:
    parsed = snapshot()

    first = validate_snapshot(
        parsed,
        expected_schema_id="synthetic-source-v1",
        previous_accepted=None,
        validated_at=NOW + timedelta(minutes=2),
    )
    second = validate_snapshot(
        parsed,
        expected_schema_id="synthetic-source-v1",
        previous_accepted=None,
        validated_at=NOW + timedelta(minutes=2),
    )

    assert first == second
    assert first.passed
    assert first.reasons == ()
    assert first.snapshot == parsed.reference()
    assert first.diff.previous_snapshot is None
    assert first.diff.new_snapshot == parsed.reference()
    assert first.diff.added_record_ids == ("record-1",)
    assert first.diff.removed_record_ids == ()
    assert first.diff.changed_records == ()
    assert first.content_hash.startswith("sha256:")


def test_validation_computes_all_structural_and_date_reasons_in_stable_order() -> None:
    bad_records = (
        ParsedRecordInput(
            source_record_id=None,
            native_locator="/records/0",
            effective_from="2026-8-1",
            effective_to=None,
            assertions=(),
        ),
        ParsedRecordInput(
            source_record_id="bad record id",
            native_locator="/records/1",
            effective_from="2026-08-03",
            effective_to="2026-08-02",
            assertions=(),
        ),
        ParsedRecordInput(
            source_record_id="duplicate",
            native_locator="/records/2",
            effective_from=None,
            effective_to="not-a-date",
            assertions=(
                ParsedAssertionInput("name", "/records/2/name", "one", "one"),
                ParsedAssertionInput("alias", "/records/2/name", "two", "two"),
            ),
        ),
        ParsedRecordInput(
            source_record_id="duplicate",
            native_locator="/records/3",
            effective_from=None,
            effective_to=None,
            assertions=(),
        ),
    )
    parsed = snapshot(
        bad_records,
        schema_id="wrong-schema",
        declared_count=3,
    )

    report = validate_snapshot(
        parsed,
        expected_schema_id="synthetic-source-v1",
        previous_accepted=None,
        validated_at=NOW + timedelta(minutes=2),
    )

    codes = tuple(reason.code for reason in report.reasons)
    assert not report.passed
    assert codes == tuple(sorted(codes))
    assert set(codes) == {
        ValidationReasonCode.SCHEMA_MISMATCH,
        ValidationReasonCode.DECLARED_COUNT_MISMATCH,
        ValidationReasonCode.MISSING_RECORD_ID,
        ValidationReasonCode.INVALID_RECORD_ID,
        ValidationReasonCode.DUPLICATE_RECORD_ID,
        ValidationReasonCode.DUPLICATE_LOCATOR,
        ValidationReasonCode.EMPTY_RECORD_ASSERTIONS,
        ValidationReasonCode.INVALID_EFFECTIVE_DATE,
        ValidationReasonCode.INVALID_EFFECTIVE_RANGE,
    }
    assert report.diff.added_record_ids == ()


def test_validation_recomputes_snapshot_hash_instead_of_trusting_stored_value() -> None:
    parsed = snapshot()
    object.__setattr__(parsed, "content_hash", "sha256:" + "0" * 64)

    report = validate_snapshot(
        parsed,
        expected_schema_id="synthetic-source-v1",
        previous_accepted=None,
        validated_at=NOW + timedelta(minutes=2),
    )

    assert ValidationReasonCode.SNAPSHOT_HASH_MISMATCH in {
        reason.code for reason in report.reasons
    }


def test_diff_orders_added_removed_changed_and_retains_old_new_hashes() -> None:
    previous = snapshot(
        (
            record_input("removed"),
            record_input("changed", native_value="old"),
            record_input("same", native_value="same"),
        )
    )
    new_raw = raw_metadata(RAW_BYTES + b"-delta")
    current = snapshot(
        (
            record_input("same", native_value="same"),
            record_input("added", native_value="added"),
            record_input("changed", native_value="new"),
        ),
        raw=new_raw,
        parsed_at=NOW + timedelta(minutes=3),
    )

    diff = SourceSnapshotDiff.create(previous, current)
    report = validate_snapshot(
        current,
        expected_schema_id="synthetic-source-v1",
        previous_accepted=previous,
        validated_at=NOW + timedelta(minutes=4),
    )

    assert diff.added_record_ids == ("added",)
    assert diff.removed_record_ids == ("removed",)
    assert tuple(item.source_record_id for item in diff.changed_records) == ("changed",)
    change = diff.changed_records[0]
    assert change.previous_record_hash == previous.records[1].content_hash()
    assert change.new_record_hash == current.records[2].content_hash()
    assert report.diff == diff
    assert report.reasons == (
        ValidationReason(ValidationReasonCode.UNEXPECTED_DELETION, record_id="removed"),
    )
    assert not report.passed


def test_diff_requires_same_scope_and_valid_unique_record_ids() -> None:
    other_source_raw = RawObjectMetadata.from_bytes(
        deployment_id="demo-deployment",
        source_id="other-source",
        original_name="other.json",
        media_type="application/json",
        charset="utf-8",
        retrieved_at=NOW,
        effective_from=None,
        content=b"other",
    )
    with pytest.raises(ValueError, match="scope mismatch"):
        SourceSnapshotDiff.create(snapshot(), snapshot(raw=other_source_raw))
    with pytest.raises(ValueError, match="valid record identifiers"):
        SourceSnapshotDiff.create(None, snapshot((record_input(None),)))
    with pytest.raises(ValueError, match="unique record identifiers"):
        SourceSnapshotDiff.create(
            None, snapshot((record_input("same"), record_input("same")))
        )


def test_changed_record_and_reference_types_reject_forged_shapes() -> None:
    digest = "sha256:" + "1" * 64
    with pytest.raises(ValueError, match="must differ"):
        ChangedRecord("record-1", digest, digest)
    with pytest.raises(ValueError, match="canonical SHA-256"):
        SourceSnapshotRef("snapshot-1", "not-a-hash")
    with pytest.raises(ValueError, match="derived from"):
        SourceSnapshotRef("snapshot-forged", digest)
    with pytest.raises(ValueError, match="byte_length"):
        RawObjectRef("raw-1", digest, MAX_RAW_OBJECT_BYTES + 1)


def test_identical_semantic_records_across_retrievals_are_not_changed() -> None:
    first = snapshot(raw=raw_metadata(b"first retrieval"))
    second = snapshot(
        raw=RawObjectMetadata.from_bytes(
            deployment_id="demo-deployment",
            source_id="synthetic-source",
            original_name="synthetic-source.json",
            media_type="application/json",
            charset="utf-8",
            retrieved_at=NOW + timedelta(minutes=1),
            effective_from=NOW,
            content=b"second retrieval",
        ),
        parsed_at=NOW + timedelta(minutes=2),
    )

    diff = SourceSnapshotDiff.create(first, second)

    assert first.raw_object != second.raw_object
    assert first.records[0].content_hash() == second.records[0].content_hash()
    assert diff.changed_records == ()


def test_parser_version_is_material_to_semantic_record_diff() -> None:
    raw = raw_metadata()
    first = snapshot(raw=raw, parser_version="1.0.0")
    reparsed = snapshot(raw=raw, parser_version="1.0.1")

    diff = SourceSnapshotDiff.create(first, reparsed)

    assert tuple(item.source_record_id for item in diff.changed_records) == (
        "record-1",
    )
    assert first.records[0].content_hash() != reparsed.records[0].content_hash()


def test_empty_snapshot_and_record_without_assertions_are_validation_failures() -> None:
    empty = snapshot(())
    empty_record = snapshot(
        (
            ParsedRecordInput(
                "record-1",
                "/records/0",
                "2026-08-01",
                None,
                (),
            ),
        )
    )

    empty_report = validate_snapshot(
        empty,
        expected_schema_id="synthetic-source-v1",
        previous_accepted=None,
        validated_at=NOW + timedelta(minutes=2),
    )
    empty_record_report = validate_snapshot(
        empty_record,
        expected_schema_id="synthetic-source-v1",
        previous_accepted=None,
        validated_at=NOW + timedelta(minutes=2),
    )

    assert {reason.code for reason in empty_report.reasons} == {
        ValidationReasonCode.EMPTY_SNAPSHOT
    }
    assert {reason.code for reason in empty_record_report.reasons} == {
        ValidationReasonCode.EMPTY_RECORD_ASSERTIONS
    }


def test_snapshot_rejects_parse_before_retrieval_and_non_utc_parse_time() -> None:
    with pytest.raises(ValueError, match="before raw retrieval"):
        snapshot(parsed_at=NOW - timedelta(seconds=1))
    with pytest.raises(ValueError, match="UTC datetime"):
        snapshot(parsed_at=datetime(2026, 8, 6, 12))


def test_normalized_value_and_aggregate_snapshot_use_utf8_byte_bounds() -> None:
    with pytest.raises(ValueError, match="encoded byte limit"):
        ParsedAssertionInput(
            "name",
            "/name",
            "safe",
            "汉" * (MAX_NATIVE_VALUE_BYTES // 2),
        )
    expanded_records = tuple(
        ParsedRecordInput(
            f"record-{index}",
            f"/records/{index}",
            None,
            None,
            (
                ParsedAssertionInput(
                    "name",
                    f"/records/{index}/name",
                    "x",
                    "汉" * 1000,
                ),
            ),
        )
        for index in range(MAX_CANONICAL_SNAPSHOT_BYTES // 3000 + 1)
    )
    assert len(expanded_records) <= MAX_RECORDS_PER_SNAPSHOT
    with pytest.raises(ValueError, match="canonical payload-size bound"):
        snapshot(expanded_records)


def test_validation_checks_previous_scope_before_invalid_id_empty_diff_path() -> None:
    previous = snapshot()
    other_raw = RawObjectMetadata.from_bytes(
        deployment_id="demo-deployment",
        source_id="other-source",
        original_name="other.json",
        media_type="application/json",
        charset="utf-8",
        retrieved_at=NOW,
        effective_from=None,
        content=b"other",
    )
    invalid_current = snapshot((record_input(None),), raw=other_raw)

    with pytest.raises(ValueError, match="scope mismatch"):
        validate_snapshot(
            invalid_current,
            expected_schema_id="synthetic-source-v1",
            previous_accepted=previous,
            validated_at=NOW + timedelta(minutes=2),
        )


def test_validation_fails_closed_on_corrupt_previous_accepted_snapshot() -> None:
    previous = snapshot()
    current = snapshot(raw=raw_metadata(b"new"))
    object.__setattr__(previous, "content_hash", "sha256:" + "0" * 64)

    with pytest.raises(ValueError, match="integrity verification"):
        validate_snapshot(
            current,
            expected_schema_id="synthetic-source-v1",
            previous_accepted=previous,
            validated_at=NOW + timedelta(minutes=2),
        )


def test_validation_cannot_precede_snapshot_parsing() -> None:
    parsed = snapshot()
    with pytest.raises(ValueError, match="before snapshot parsing"):
        validate_snapshot(
            parsed,
            expected_schema_id="synthetic-source-v1",
            previous_accepted=None,
            validated_at=parsed.parsed_at - timedelta(seconds=1),
        )


def validation_report_for(
    parsed: ParsedSourceSnapshot,
    previous: ParsedSourceSnapshot | None = None,
    *,
    expected_schema_id: str = "synthetic-source-v1",
) -> ValidationReport:
    return validate_snapshot(
        parsed,
        expected_schema_id=expected_schema_id,
        previous_accepted=previous,
        validated_at=NOW + timedelta(minutes=5),
    )


def lifecycle_event(
    sequence: int,
    event_type: SourceSnapshotEventType,
    raw: RawObjectMetadata,
    parsed: ParsedSourceSnapshot | None,
    *,
    previous_active: SourceSnapshotRef | None = None,
    actor_id: str = "source-operator-1",
    actor_type: LifecycleActorType = LifecycleActorType.SERVICE,
    report: ValidationReport | None = None,
    event_id: str | None = None,
    deployment_id: str | None = None,
) -> SourceSnapshotLifecycleEvent:
    return SourceSnapshotLifecycleEvent(
        sequence=sequence,
        event_id=event_id or f"source-event-{sequence}",
        deployment_id=deployment_id or raw.deployment_id,
        source_id=raw.source_id,
        event_type=event_type,
        raw_object=raw.reference(),
        snapshot=parsed.reference() if parsed else None,
        previous_active_snapshot=previous_active,
        actor_id=actor_id,
        actor_type=actor_type,
        reason=f"synthetic {event_type.value.lower()}",
        occurred_at=NOW + timedelta(minutes=10 + sequence),
        validation_report=report,
    )


def accepted_events(
    raw: RawObjectMetadata,
    parsed: ParsedSourceSnapshot,
    *,
    start: int = 1,
    previous_active: SourceSnapshotRef | None = None,
    previous_snapshot: ParsedSourceSnapshot | None = None,
) -> tuple[SourceSnapshotLifecycleEvent, ...]:
    report = validation_report_for(parsed, previous_snapshot)
    return (
        lifecycle_event(start, SourceSnapshotEventType.RETRIEVED, raw, None),
        lifecycle_event(start + 1, SourceSnapshotEventType.QUARANTINED, raw, None),
        lifecycle_event(start + 2, SourceSnapshotEventType.PARSED, raw, parsed),
        lifecycle_event(
            start + 3,
            SourceSnapshotEventType.VALIDATED,
            raw,
            parsed,
            report=report,
        ),
        lifecycle_event(
            start + 4,
            SourceSnapshotEventType.APPROVED,
            raw,
            parsed,
            actor_id="source-approver-1",
            actor_type=LifecycleActorType.HUMAN,
        ),
        lifecycle_event(
            start + 5,
            SourceSnapshotEventType.ACTIVATED,
            raw,
            parsed,
            previous_active=previous_active,
            actor_id="source-approver-1",
            actor_type=LifecycleActorType.HUMAN,
        ),
    )


def test_fold_proves_retrieve_quarantine_parse_validate_approve_activate() -> None:
    raw = raw_metadata()
    parsed = snapshot(raw=raw)
    events = accepted_events(raw, parsed)

    lifecycle = fold_source_snapshot_events(events)

    assert lifecycle.raw_objects[0].state is RawObjectState.PARSED
    assert lifecycle.state_for(parsed.reference()) is SourceSnapshotState.ACTIVE
    assert lifecycle.creator_for(parsed.reference()) == "source-operator-1"
    assert lifecycle.active_snapshot == parsed.reference()
    assert lifecycle.activated_snapshots == (parsed.reference(),)
    validate_lifecycle_projection(events, lifecycle)


def test_parse_or_validation_success_never_activates_without_human_steps() -> None:
    raw = raw_metadata()
    parsed = snapshot(raw=raw)
    events = accepted_events(raw, parsed)[:4]

    lifecycle = fold_source_snapshot_events(events)

    assert lifecycle.state_for(parsed.reference()) is SourceSnapshotState.VALIDATED
    assert lifecycle.active_snapshot is None
    assert lifecycle.activated_snapshots == ()


def test_fold_rejects_validation_against_stale_active_predecessor() -> None:
    first_raw = raw_metadata(b"first")
    first = snapshot(raw=first_raw)
    first_events = accepted_events(first_raw, first)
    second_raw = raw_metadata(b"second")
    second = snapshot(raw=second_raw)
    stale_report = validation_report_for(second, None)
    stale_events = first_events + (
        lifecycle_event(7, SourceSnapshotEventType.RETRIEVED, second_raw, None),
        lifecycle_event(8, SourceSnapshotEventType.QUARANTINED, second_raw, None),
        lifecycle_event(9, SourceSnapshotEventType.PARSED, second_raw, second),
        lifecycle_event(
            10,
            SourceSnapshotEventType.VALIDATED,
            second_raw,
            second,
            report=stale_report,
        ),
    )
    with pytest.raises(InvalidSourceSnapshotTransition):
        fold_source_snapshot_events(stale_events)


def test_validation_failure_stays_quarantined_and_blocks_approval() -> None:
    raw = raw_metadata()
    parsed = snapshot(raw=raw, schema_id="wrong-schema")
    report = validation_report_for(parsed)
    events = (
        lifecycle_event(1, SourceSnapshotEventType.RETRIEVED, raw, None),
        lifecycle_event(2, SourceSnapshotEventType.QUARANTINED, raw, None),
        lifecycle_event(3, SourceSnapshotEventType.PARSED, raw, parsed),
        lifecycle_event(
            4,
            SourceSnapshotEventType.VALIDATION_FAILED,
            raw,
            parsed,
            report=report,
        ),
    )
    lifecycle = fold_source_snapshot_events(events)
    assert lifecycle.state_for(parsed.reference()) is SourceSnapshotState.QUARANTINED
    approve = lifecycle_event(
        5,
        SourceSnapshotEventType.APPROVED,
        raw,
        parsed,
        actor_id="source-approver-1",
        actor_type=LifecycleActorType.HUMAN,
    )
    with pytest.raises(InvalidSourceSnapshotTransition):
        fold_source_snapshot_events(events + (approve,))


def test_activation_supersedes_and_rollback_targets_only_previously_active_snapshot() -> (
    None
):
    first_raw = raw_metadata(b"first")
    first = snapshot(raw=first_raw)
    first_events = accepted_events(first_raw, first)
    second_raw = raw_metadata(b"second")
    second = snapshot(
        (record_input(native_value="changed"),),
        raw=second_raw,
        parsed_at=NOW + timedelta(minutes=2),
    )
    second_events = accepted_events(
        second_raw,
        second,
        start=7,
        previous_active=first.reference(),
        previous_snapshot=first,
    )
    before_rollback = first_events + second_events

    superseded = fold_source_snapshot_events(before_rollback)
    assert superseded.active_snapshot == second.reference()
    assert superseded.state_for(first.reference()) is SourceSnapshotState.SUPERSEDED

    rollback = lifecycle_event(
        13,
        SourceSnapshotEventType.ROLLED_BACK,
        first_raw,
        first,
        previous_active=second.reference(),
        actor_id="source-approver-2",
        actor_type=LifecycleActorType.HUMAN,
    )
    rolled_back = fold_source_snapshot_events(before_rollback + (rollback,))
    assert rolled_back.active_snapshot == first.reference()
    assert rolled_back.state_for(second.reference()) is SourceSnapshotState.ROLLED_BACK
    assert rolled_back.state_for(first.reference()) is SourceSnapshotState.ACTIVE


def test_lifecycle_requires_contiguous_sequence_and_ordered_utc_history() -> None:
    raw = raw_metadata()
    parsed = snapshot(raw=raw)
    events = accepted_events(raw, parsed)

    with pytest.raises(InvalidSourceSnapshotTransition):
        fold_source_snapshot_events((events[0], replace(events[1], sequence=3)))
    with pytest.raises(InvalidSourceSnapshotTransition):
        fold_source_snapshot_events(
            (
                events[0],
                replace(
                    events[1], occurred_at=events[0].occurred_at - timedelta(seconds=1)
                ),
            )
        )
    with pytest.raises(ValueError, match="history exceeds"):
        fold_source_snapshot_events((events[0],) * (MAX_LIFECYCLE_EVENTS + 1))


def test_activated_retry_is_not_idempotent_after_target_is_superseded() -> None:
    first_raw = raw_metadata(b"first")
    first = snapshot(raw=first_raw)
    first_events = accepted_events(first_raw, first)
    second_raw = raw_metadata(b"second")
    second = snapshot(
        (record_input(native_value="changed"),),
        raw=second_raw,
        parsed_at=NOW + timedelta(minutes=2),
    )
    all_events = first_events + accepted_events(
        second_raw,
        second,
        start=7,
        previous_active=first.reference(),
        previous_snapshot=first,
    )
    lifecycle = fold_source_snapshot_events(all_events)

    assert (
        semantically_applied_lifecycle_transition(
            first_events[-1], lifecycle, all_events
        )
        is None
    )
    assert (
        semantically_applied_lifecycle_transition(all_events[-1], lifecycle, all_events)
        == all_events[-1]
    )


def test_projection_rejects_forged_members_order_duplicates_and_active_pointer() -> (
    None
):
    raw = raw_metadata()
    parsed = snapshot(raw=raw)
    lifecycle = fold_source_snapshot_events(accepted_events(raw, parsed))

    with pytest.raises(ValueError, match="sorted and unique"):
        replace(lifecycle, raw_objects=lifecycle.raw_objects * 2)
    with pytest.raises(ValueError, match="sorted and unique"):
        replace(lifecycle, snapshots=lifecycle.snapshots * 2)
    with pytest.raises(ValueError, match="sorted and unique"):
        replace(lifecycle, activated_snapshots=lifecycle.activated_snapshots * 2)
    with pytest.raises(ValueError, match="active pointer"):
        replace(lifecycle, active_snapshot=None)
    with pytest.raises(ValueError, match="exactly match"):
        replace(lifecycle, activated_snapshots=())
    with pytest.raises(ValueError, match="parsed raw"):
        SourceSnapshotLifecycle(
            raw_objects=(
                RawObjectStateRecord(raw.reference(), RawObjectState.QUARANTINED),
            ),
            snapshots=lifecycle.snapshots,
            active_snapshot=parsed.reference(),
            activated_snapshots=(parsed.reference(),),
        )
    with pytest.raises(ValueError, match="state must be typed"):
        SnapshotStateRecord(
            parsed.reference(),
            raw.reference(),
            "operator-1",
            "ACTIVE",  # type: ignore[arg-type]
        )


def test_projection_cannot_drop_superseded_snapshot_from_activation_history() -> None:
    first_raw = raw_metadata(b"first")
    first = snapshot(raw=first_raw)
    second_raw = raw_metadata(b"second")
    second = snapshot(
        (record_input(native_value="changed"),),
        raw=second_raw,
        parsed_at=NOW + timedelta(minutes=2),
    )
    events = accepted_events(first_raw, first) + accepted_events(
        second_raw,
        second,
        start=7,
        previous_active=first.reference(),
        previous_snapshot=first,
    )
    lifecycle = fold_source_snapshot_events(events)

    with pytest.raises(ValueError, match="exactly match"):
        replace(lifecycle, activated_snapshots=(second.reference(),))


def test_projection_validation_detects_mismatch() -> None:
    raw = raw_metadata()
    parsed = snapshot(raw=raw)
    events = accepted_events(raw, parsed)
    lifecycle = fold_source_snapshot_events(events)
    partial = fold_source_snapshot_events(events[:4])

    validate_lifecycle_projection(events, lifecycle)
    with pytest.raises(InvalidSourceSnapshotTransition):
        validate_lifecycle_projection(events, partial)


@pytest.mark.parametrize("limit", [1, MAX_QUERY_LIMIT])
def test_query_limit_accepts_only_bounded_positive_integers(limit: int) -> None:
    assert require_query_limit(limit) == limit


@pytest.mark.parametrize("limit", [True, 0, MAX_QUERY_LIMIT + 1, 1.5])
def test_query_limit_rejects_unbounded_or_invalid_values(limit: object) -> None:
    with pytest.raises(ValueError, match="bounded query"):
        require_query_limit(limit)  # type: ignore[arg-type]


def command_audit(
    event: SourceSnapshotLifecycleEvent,
    reason: SnapshotCommandReason,
    *,
    applied: bool,
) -> SnapshotCommandAuditRecord:
    snapshot_ref = event.snapshot
    return SnapshotCommandAuditRecord(
        command_event_id=f"command-{event.sequence}-{reason.value.lower()}",
        authorization_event_id=f"authz-{event.sequence}",
        lifecycle_event_id=event.event_id if applied else None,
        tenant_id="control-tenant",
        deployment_id=event.deployment_id,
        source_id=event.source_id,
        raw_object_id=event.raw_object.object_id,
        snapshot_id=snapshot_ref.snapshot_id if snapshot_ref else None,
        snapshot_content_hash=snapshot_ref.content_hash if snapshot_ref else None,
        actor_id=event.actor_id,
        actor_type=event.actor_type,
        operation=AUTHORIZATION_OPERATION_BY_EVENT_TYPE[event.event_type],
        outcome=SnapshotCommandOutcome.SUCCESS,
        reason=reason,
        occurred_at=event.occurred_at,
    )


def test_command_audit_links_exact_authorization_target_actor_and_lifecycle_event() -> (
    None
):
    raw = raw_metadata()
    parsed = snapshot(raw=raw)
    activated = accepted_events(raw, parsed)[-1]
    audit = command_audit(activated, SnapshotCommandReason.ACTIVATED, applied=True)

    validate_atomic_command_audit(
        audit,
        raw.reference(),
        parsed.reference(),
        activated,
        SnapshotCommandReason.ACTIVATED,
    )

    with pytest.raises(ValueError, match="does not match"):
        validate_atomic_command_audit(
            replace(audit, actor_id="other-actor"),
            raw.reference(),
            parsed.reference(),
            activated,
            SnapshotCommandReason.ACTIVATED,
        )
    with pytest.raises(ValueError, match="does not match"):
        validate_atomic_command_audit(
            audit,
            raw.reference(),
            parsed.reference(),
            activated,
            SnapshotCommandReason.APPROVED,
        )
    with pytest.raises(ValueError, match="does not match"):
        validate_atomic_command_audit(
            replace(audit, operation="SOURCE_SNAPSHOT_APPROVE"),
            raw.reference(),
            parsed.reference(),
            activated,
            SnapshotCommandReason.ACTIVATED,
        )
    approved_binding = replace(
        audit,
        operation="SOURCE_SNAPSHOT_APPROVE",
        reason=SnapshotCommandReason.APPROVED,
    )
    with pytest.raises(ValueError, match="does not match"):
        validate_atomic_command_audit(
            approved_binding,
            raw.reference(),
            parsed.reference(),
            activated,
            SnapshotCommandReason.APPROVED,
        )


def test_idempotent_audit_has_no_forged_lifecycle_link_but_retains_exact_target() -> (
    None
):
    raw = raw_metadata()
    parsed = snapshot(raw=raw)
    activated = accepted_events(raw, parsed)[-1]
    audit = command_audit(
        activated, SnapshotCommandReason.ACTIVATE_IDEMPOTENT, applied=False
    )

    validate_atomic_command_audit(
        audit,
        raw.reference(),
        parsed.reference(),
        None,
        SnapshotCommandReason.ACTIVATE_IDEMPOTENT,
    )
    with pytest.raises(ValueError, match="does not match"):
        validate_atomic_command_audit(
            audit,
            raw_metadata(b"different").reference(),
            parsed.reference(),
            None,
            SnapshotCommandReason.ACTIVATE_IDEMPOTENT,
        )


def test_command_audit_rejects_mismatched_outcome_and_partial_snapshot_identity() -> (
    None
):
    raw = raw_metadata()
    parsed = snapshot(raw=raw)
    activated = accepted_events(raw, parsed)[-1]
    values = command_audit(activated, SnapshotCommandReason.ACTIVATED, applied=True)

    with pytest.raises(ValueError, match="outcome must match"):
        replace(values, outcome=SnapshotCommandOutcome.FAILURE)
    with pytest.raises(ValueError, match="present together"):
        replace(values, snapshot_content_hash=None)
    with pytest.raises(ValueError, match="only applied transitions"):
        replace(values, lifecycle_event_id=None)
    with pytest.raises(ValueError, match="derived from"):
        replace(values, snapshot_content_hash="sha256:" + "1" * 64)


def test_lifecycle_event_rejects_agents_nonhuman_final_steps_and_report_mismatch() -> (
    None
):
    raw = raw_metadata()
    parsed = snapshot(raw=raw)
    passing = validation_report_for(parsed)
    failing_parsed = snapshot(raw=raw, schema_id="wrong-schema")
    failing = validation_report_for(failing_parsed)

    with pytest.raises(ValueError, match="agents cannot mutate"):
        lifecycle_event(
            1,
            SourceSnapshotEventType.RETRIEVED,
            raw,
            None,
            actor_type=LifecycleActorType.AGENT,
        )
    with pytest.raises(ValueError, match="require a human"):
        lifecycle_event(4, SourceSnapshotEventType.APPROVED, raw, parsed)
    with pytest.raises(ValueError, match="passing computed report"):
        lifecycle_event(
            4,
            SourceSnapshotEventType.VALIDATED,
            raw,
            failing_parsed,
            report=failing,
        )
    with pytest.raises(ValueError, match="blocking reasons"):
        lifecycle_event(
            4,
            SourceSnapshotEventType.VALIDATION_FAILED,
            raw,
            parsed,
            report=passing,
        )
    with pytest.raises(ValueError, match="identify the event snapshot"):
        lifecycle_event(
            4,
            SourceSnapshotEventType.VALIDATED,
            raw,
            failing_parsed,
            report=passing,
        )


def test_diff_and_validation_report_hashes_are_recomputable_and_tamper_evident() -> (
    None
):
    raw = raw_metadata()
    parsed = snapshot(raw=raw)
    report = validation_report_for(parsed)

    assert report.diff.recomputed_content_hash() == report.diff.content_hash
    assert report.recomputed_content_hash() == report.content_hash

    object.__setattr__(report.diff, "content_hash", "sha256:" + "0" * 64)
    with pytest.raises(ValueError, match="content verification"):
        lifecycle_event(
            4,
            SourceSnapshotEventType.VALIDATED,
            raw,
            parsed,
            report=report,
        )

    report = validation_report_for(parsed)
    object.__setattr__(report, "content_hash", "sha256:" + "0" * 64)
    with pytest.raises(ValueError, match="content verification"):
        lifecycle_event(
            4,
            SourceSnapshotEventType.VALIDATED,
            raw,
            parsed,
            report=report,
        )


def test_validation_lifecycle_event_cannot_precede_report_timestamp() -> None:
    raw = raw_metadata()
    parsed = snapshot(raw=raw)
    report = validation_report_for(parsed)
    event = lifecycle_event(
        4,
        SourceSnapshotEventType.VALIDATED,
        raw,
        parsed,
        report=report,
    )

    with pytest.raises(ValueError, match="cannot precede"):
        replace(event, occurred_at=report.validated_at - timedelta(seconds=1))


def test_low_level_artifact_validators_reject_malformed_typed_values() -> None:
    digest = "sha256:" + "1" * 64
    with pytest.raises(ValueError, match="invalid object_id"):
        RawObjectRef("unsafe id", digest, 1)
    with pytest.raises(TypeError, match="raw content must be bytes"):
        bytes_sha256("bytes")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="raw content must be bytes"):
        RawObjectMetadata.from_bytes(
            deployment_id="demo-deployment",
            source_id="synthetic-source",
            original_name="synthetic.json",
            media_type="application/json",
            charset="utf-8",
            retrieved_at=NOW,
            effective_from=None,
            content="bytes",  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="raw metadata must be typed"):
        verify_raw_bytes("metadata", b"bytes")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="bounded printable text"):
        raw_metadata_from_arguments(original_name="")
    with pytest.raises(ValueError, match="canonical field"):
        ParsedAssertionInput("unsafe field", "/name", "value", "value")
    with pytest.raises(ValueError, match="parsed assertion inputs"):
        ParsedRecordInput("record-1", "/records/0", None, None, [])  # type: ignore[arg-type]


def raw_metadata_from_arguments(**overrides: object) -> RawObjectMetadata:
    arguments: dict[str, object] = {
        "deployment_id": "demo-deployment",
        "source_id": "synthetic-source",
        "original_name": "synthetic.json",
        "media_type": "application/json",
        "charset": "utf-8",
        "retrieved_at": NOW,
        "effective_from": None,
        "content": RAW_BYTES,
    }
    arguments.update(overrides)
    return RawObjectMetadata.from_bytes(**arguments)  # type: ignore[arg-type]


def test_materialized_assertion_and_record_reject_malformed_or_mismatched_content() -> (
    None
):
    parsed = snapshot()
    record = parsed.records[0]
    assertion = record.assertions[0]

    with pytest.raises(ValueError, match="canonical field"):
        replace(assertion, field_name="unsafe field")
    with pytest.raises(ValueError, match="canonical JSON"):
        replace(assertion, native_value_json="{")
    with pytest.raises(ValueError, match="canonical JSON scalar"):
        replace(assertion, native_value_json='"\\u0053YNTHETIC ENTITY"')
    with pytest.raises(ValueError, match="semantic version"):
        replace(assertion, parser_version="v1")
    with pytest.raises(ValueError, match="typed assertions"):
        replace(record, assertions=[])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="assertion-count bound"):
        replace(record, assertions=(assertion,) * (MAX_ASSERTIONS_PER_RECORD + 1))
    with pytest.raises(ValueError, match="provenance"):
        replace(record, assertions=(replace(assertion, raw_object_id="raw-other"),))


def test_snapshot_factory_rejects_wrong_container_types_and_independent_count_bound() -> (
    None
):
    raw = raw_metadata()
    with pytest.raises(TypeError, match="raw_metadata must be typed"):
        ParsedSourceSnapshot.create(
            raw_metadata="raw",  # type: ignore[arg-type]
            parser_id="parser",
            parser_version="1.0.0",
            schema_id="schema",
            declared_record_count=0,
            parsed_at=NOW,
            records=(),
        )
    with pytest.raises(ValueError, match="semantic version"):
        ParsedSourceSnapshot.create(
            raw_metadata=raw,
            parser_id="parser",
            parser_version="v1",
            schema_id="schema",
            declared_record_count=0,
            parsed_at=NOW,
            records=(),
        )
    with pytest.raises(ValueError, match="tuple of parsed"):
        ParsedSourceSnapshot.create(
            raw_metadata=raw,
            parser_id="parser",
            parser_version="1.0.0",
            schema_id="schema",
            declared_record_count=0,
            parsed_at=NOW,
            records=[],  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="record-count bound"):
        ParsedSourceSnapshot.create(
            raw_metadata=raw,
            parser_id="parser",
            parser_version="1.0.0",
            schema_id="schema",
            declared_record_count=0,
            parsed_at=NOW,
            records=(record_input(),) * (MAX_RECORDS_PER_SNAPSHOT + 1),
        )


def test_validation_entrypoints_reject_untyped_values() -> None:
    with pytest.raises(ValueError, match="code must be typed"):
        ValidationReason("EMPTY_SNAPSHOT")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="snapshot must be typed"):
        untyped_snapshot: Any = "snapshot"
        validate_snapshot(
            untyped_snapshot,
            expected_schema_id="schema",
            previous_accepted=None,
            validated_at=NOW,
        )


def test_lifecycle_event_shape_validators_reject_untyped_and_inconsistent_fields() -> (
    None
):
    raw = raw_metadata()
    parsed = snapshot(raw=raw)
    events = accepted_events(raw, parsed)
    retrieved, quarantined, parsed_event, validated, _, activated = events

    invalid_changes: tuple[
        tuple[SourceSnapshotLifecycleEvent, dict[str, object]], ...
    ] = (
        (retrieved, {"sequence": True}),
        (retrieved, {"sequence": MAX_SEQUENCE + 1}),
        (retrieved, {"event_type": "RETRIEVED"}),
        (retrieved, {"raw_object": "raw"}),
        (parsed_event, {"snapshot": "snapshot"}),
        (activated, {"previous_active_snapshot": "snapshot"}),
        (retrieved, {"actor_type": "SERVICE"}),
        (retrieved, {"snapshot": parsed.reference()}),
        (parsed_event, {"previous_active_snapshot": parsed.reference()}),
        (validated, {"validation_report": None}),
        (quarantined, {"validation_report": validation_report_for(parsed)}),
    )
    for original, changes in invalid_changes:
        with pytest.raises(ValueError):
            replace(original, **changes)  # type: ignore[arg-type]


def test_projection_value_objects_reject_untyped_members_and_inconsistent_history() -> (
    None
):
    raw = raw_metadata()
    parsed = snapshot(raw=raw)
    partial = fold_source_snapshot_events(accepted_events(raw, parsed)[:4])
    record = partial.snapshots[0]

    with pytest.raises(ValueError, match="snapshot must be typed"):
        replace(record, snapshot="snapshot")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="raw_object must be typed"):
        replace(record, raw_object="raw")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="raw_object must be typed"):
        RawObjectStateRecord("raw", RawObjectState.PARSED)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="state must be typed"):
        RawObjectStateRecord(raw.reference(), "PARSED")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="raw_objects must be typed"):
        replace(partial, raw_objects=[])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="snapshots must be typed"):
        replace(partial, snapshots=[])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="activated_snapshots must be typed"):
        replace(partial, activated_snapshots=[])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="active_snapshot must be typed"):
        replace(partial, active_snapshot="snapshot")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="accepted lifecycle states"):
        replace(partial, activated_snapshots=(parsed.reference(),))
    assert (
        partial.state_for(
            SourceSnapshotRef("snapshot-" + "1" * 64, "sha256:" + "1" * 64)
        )
        is None
    )
    assert (
        partial.creator_for(
            SourceSnapshotRef("snapshot-" + "1" * 64, "sha256:" + "1" * 64)
        )
        is None
    )


def test_fold_rejects_untyped_scope_identity_alias_and_duplicate_history() -> None:
    raw = raw_metadata()
    parsed = snapshot(raw=raw)
    events = accepted_events(raw, parsed)

    with pytest.raises(ValueError, match="typed lifecycle events"):
        fold_source_snapshot_events(list(events))  # type: ignore[arg-type]
    with pytest.raises(InvalidSourceSnapshotTransition):
        fold_source_snapshot_events(
            (events[0], replace(events[1], deployment_id="other-deployment"))
        )
    with pytest.raises(InvalidSourceSnapshotTransition):
        fold_source_snapshot_events(
            (events[0], replace(events[1], event_id=events[0].event_id))
        )
    aliased_raw = RawObjectRef(
        raw.object_id,
        "sha256:"
        + ("0" if raw.content_hash[-1] != "0" else "1")
        + raw.content_hash[-63:],
        raw.byte_length,
    )
    with pytest.raises(InvalidSourceSnapshotTransition):
        fold_source_snapshot_events(
            (events[0], replace(events[1], raw_object=aliased_raw))
        )


def test_fold_rejects_each_out_of_order_or_invalid_transition_family() -> None:
    raw = raw_metadata()
    parsed = snapshot(raw=raw)
    events = accepted_events(raw, parsed)

    duplicate_retrieval = replace(events[0], sequence=2, event_id="duplicate-retrieval")
    quarantine_first = replace(events[1], sequence=1, event_id="quarantine-first")
    parse_first = replace(events[2], sequence=1, event_id="parse-first")
    validate_first = replace(events[3], sequence=1, event_id="validate-first")
    approve_same_creator = replace(events[4], actor_id="source-operator-1")
    other = snapshot(raw=raw_metadata(b"other"))
    wrong_previous = other.reference()
    activate_wrong_previous = replace(
        events[5], previous_active_snapshot=wrong_previous
    )

    histories = (
        (events[0], duplicate_retrieval),
        (quarantine_first,),
        (parse_first,),
        (validate_first,),
        events[:4] + (approve_same_creator,),
        events[:5] + (activate_wrong_previous,),
    )
    for history in histories:
        with pytest.raises(InvalidSourceSnapshotTransition):
            fold_source_snapshot_events(history)


def test_rollback_rejects_snapshot_that_was_never_previously_active() -> None:
    first_raw = raw_metadata(b"first")
    first = snapshot(raw=first_raw)
    active_events = accepted_events(first_raw, first)
    candidate_raw = raw_metadata(b"candidate")
    candidate = snapshot(raw=candidate_raw)
    rollback = lifecycle_event(
        7,
        SourceSnapshotEventType.ROLLED_BACK,
        candidate_raw,
        candidate,
        previous_active=first.reference(),
        actor_id="source-approver-2",
        actor_type=LifecycleActorType.HUMAN,
    )

    with pytest.raises(InvalidSourceSnapshotTransition):
        fold_source_snapshot_events(active_events + (rollback,))


def test_semantic_idempotence_validates_inputs_and_all_transition_families() -> None:
    raw = raw_metadata()
    parsed = snapshot(raw=raw)
    events = accepted_events(raw, parsed)
    lifecycle = fold_source_snapshot_events(events)

    for attempted in events:
        responsible = semantically_applied_lifecycle_transition(
            attempted, lifecycle, events
        )
        assert responsible == attempted
    assert (
        semantically_applied_lifecycle_transition(
            replace(events[0], reason="different reason"), lifecycle, events
        )
        is None
    )
    with pytest.raises(ValueError, match="attempted event must be typed"):
        untyped_event: Any = "event"
        semantically_applied_lifecycle_transition(untyped_event, lifecycle, events)
    with pytest.raises(ValueError, match="lifecycle must be typed"):
        untyped_lifecycle: Any = "lifecycle"
        semantically_applied_lifecycle_transition(events[0], untyped_lifecycle, events)
    with pytest.raises(ValueError, match="current events must be typed"):
        semantically_applied_lifecycle_transition(
            events[0],
            lifecycle,
            list(events),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="projection must be typed"):
        validate_lifecycle_projection(events, "projection")  # type: ignore[arg-type]


def test_command_audit_type_validation_and_raw_only_target_binding() -> None:
    raw = raw_metadata()
    parsed = snapshot(raw=raw)
    events = accepted_events(raw, parsed)
    retrieved = events[0]
    audit = command_audit(retrieved, SnapshotCommandReason.RETRIEVED, applied=True)

    assert audit.snapshot_id is None
    validate_atomic_command_audit(
        audit,
        raw.reference(),
        None,
        retrieved,
        SnapshotCommandReason.RETRIEVED,
    )
    failure = replace(
        audit,
        command_event_id="command-failure",
        lifecycle_event_id=None,
        outcome=SnapshotCommandOutcome.FAILURE,
        reason=SnapshotCommandReason.CONFLICT,
    )
    assert failure.outcome is SnapshotCommandOutcome.FAILURE
    with pytest.raises(ValueError, match="successful command"):
        validate_atomic_command_audit(
            failure,
            raw.reference(),
            None,
            None,
            SnapshotCommandReason.CONFLICT,
        )

    invalid_changes: tuple[dict[str, object], ...] = (
        {"actor_type": "HUMAN"},
        {"outcome": "SUCCESS"},
        {"reason": "RETRIEVED"},
    )
    for changes in invalid_changes:
        with pytest.raises(ValueError):
            replace(audit, **changes)  # type: ignore[arg-type]


def test_atomic_audit_validator_rejects_untyped_arguments() -> None:
    raw = raw_metadata()
    parsed = snapshot(raw=raw)
    event = accepted_events(raw, parsed)[-1]
    audit = command_audit(event, SnapshotCommandReason.ACTIVATED, applied=True)

    cases: tuple[tuple[Any, Any, Any, Any, Any], ...] = (
        (
            "audit",
            raw.reference(),
            parsed.reference(),
            event,
            SnapshotCommandReason.ACTIVATED,
        ),
        (audit, "raw", parsed.reference(), event, SnapshotCommandReason.ACTIVATED),
        (audit, raw.reference(), "snapshot", event, SnapshotCommandReason.ACTIVATED),
        (
            audit,
            raw.reference(),
            parsed.reference(),
            "event",
            SnapshotCommandReason.ACTIVATED,
        ),
        (audit, raw.reference(), parsed.reference(), event, "ACTIVATED"),
    )
    for (
        candidate_audit,
        candidate_raw,
        candidate_snapshot,
        candidate_event,
        reason,
    ) in cases:
        with pytest.raises(ValueError):
            validate_atomic_command_audit(
                candidate_audit,
                candidate_raw,
                candidate_snapshot,
                candidate_event,
                reason,
            )
