"""Canonical operations-safe contracts for immutable source-snapshot reads.

Registration of these models generates shared schemas only. It deliberately creates
no REST route, MCP tool, raw-object export, authorization path, or persistence access.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, StringConstraints, model_validator

from tradesieve.application.contracts import (
    CanonicalDatetime,
    ContentHash,
    ContractModel,
    Reference,
    SemanticVersion,
)
from tradesieve.domain.source_snapshot import (
    MAX_QUERY_LIMIT,
    MAX_RAW_OBJECT_BYTES,
    MAX_RECORDS_PER_SNAPSHOT,
    MAX_SEQUENCE,
    SAFE_CHARSET,
    SAFE_MEDIA_TYPE,
    LifecycleActorType,
    RawObjectRef,
    SourceSnapshotEventType,
    SourceSnapshotRef,
    SourceSnapshotState,
    ValidationReasonCode,
    canonical_sha256,
)
from tradesieve.ports.source_snapshot import (
    SYNTHETIC_CHARSET,
    SYNTHETIC_MEDIA_TYPE,
    SYNTHETIC_PARSER_VERSION,
    SYNTHETIC_SCHEMA_ID,
    FiniteParserId,
)

MediaType = Annotated[
    str,
    StringConstraints(
        min_length=3,
        max_length=129,
        pattern=SAFE_MEDIA_TYPE.pattern,
    ),
]
Charset = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=32,
        pattern=SAFE_CHARSET.pattern,
    ),
]


class SourceSnapshotReferenceView(ContractModel):
    snapshot_id: Reference
    content_hash: ContentHash

    @model_validator(mode="after")
    def require_derived_identity(self) -> SourceSnapshotReferenceView:
        try:
            SourceSnapshotRef(self.snapshot_id, self.content_hash)
        except (TypeError, ValueError):
            raise ValueError("source snapshot reference is invalid") from None
        return self


class ChangedSourceRecordView(ContractModel):
    source_record_id: Reference
    previous_record_hash: ContentHash
    new_record_hash: ContentHash

    @model_validator(mode="after")
    def require_changed_hashes(self) -> ChangedSourceRecordView:
        if self.previous_record_hash == self.new_record_hash:
            raise ValueError("changed record hashes must differ")
        return self


class SourceSnapshotDiffView(ContractModel):
    previous_snapshot: SourceSnapshotReferenceView | None
    new_snapshot: SourceSnapshotReferenceView
    content_hash: ContentHash
    added_record_ids: Annotated[
        list[Reference], Field(max_length=MAX_RECORDS_PER_SNAPSHOT)
    ]
    removed_record_ids: Annotated[
        list[Reference], Field(max_length=MAX_RECORDS_PER_SNAPSHOT)
    ]
    changed_records: Annotated[
        list[ChangedSourceRecordView], Field(max_length=MAX_RECORDS_PER_SNAPSHOT)
    ]

    @model_validator(mode="after")
    def require_deterministic_order(self) -> SourceSnapshotDiffView:
        if self.added_record_ids != sorted(set(self.added_record_ids)):
            raise ValueError("added record IDs must be sorted and unique")
        if self.removed_record_ids != sorted(set(self.removed_record_ids)):
            raise ValueError("removed record IDs must be sorted and unique")
        changed_ids = [item.source_record_id for item in self.changed_records]
        if changed_ids != sorted(set(changed_ids)):
            raise ValueError("changed records must be sorted and unique")
        added = set(self.added_record_ids)
        removed = set(self.removed_record_ids)
        changed = set(changed_ids)
        if added & removed or added & changed or removed & changed:
            raise ValueError("snapshot diff record categories must not overlap")
        expected_hash = canonical_sha256(
            {
                "previous_snapshot": (
                    {
                        "snapshot_id": self.previous_snapshot.snapshot_id,
                        "content_hash": self.previous_snapshot.content_hash,
                    }
                    if self.previous_snapshot is not None
                    else None
                ),
                "new_snapshot": {
                    "snapshot_id": self.new_snapshot.snapshot_id,
                    "content_hash": self.new_snapshot.content_hash,
                },
                "added_record_ids": self.added_record_ids,
                "removed_record_ids": self.removed_record_ids,
                "changed_records": [
                    {
                        "source_record_id": item.source_record_id,
                        "previous_record_hash": item.previous_record_hash,
                        "new_record_hash": item.new_record_hash,
                    }
                    for item in self.changed_records
                ],
            }
        )
        if self.content_hash != expected_hash:
            raise ValueError("snapshot diff content hash is invalid")
        return self


class SourceSnapshotSummary(ContractModel):
    deployment_id: Reference
    source_id: Reference
    snapshot_id: Reference
    snapshot_content_hash: ContentHash
    raw_object_id: Reference
    raw_content_hash: ContentHash
    raw_byte_length: Annotated[int, Field(ge=0, le=MAX_RAW_OBJECT_BYTES)]
    media_type: MediaType
    charset: Charset
    parser_id: FiniteParserId
    parser_version: SemanticVersion
    schema_id: Reference
    declared_record_count: Annotated[int, Field(ge=0, le=MAX_RECORDS_PER_SNAPSHOT)]
    record_count: Annotated[int, Field(ge=0, le=MAX_RECORDS_PER_SNAPSHOT)]
    retrieved_at: CanonicalDatetime
    effective_from: CanonicalDatetime | None
    parsed_at: CanonicalDatetime
    state: SourceSnapshotState
    active: bool
    validation_passed: bool | None
    validation_reason_codes: Annotated[
        list[ValidationReasonCode], Field(max_length=len(ValidationReasonCode))
    ]
    validated_at: CanonicalDatetime | None
    validation_report_hash: ContentHash | None
    previous_snapshot: SourceSnapshotReferenceView | None
    diff_content_hash: ContentHash | None
    added_record_count: Annotated[int, Field(ge=0, le=MAX_RECORDS_PER_SNAPSHOT)]
    removed_record_count: Annotated[int, Field(ge=0, le=MAX_RECORDS_PER_SNAPSHOT)]
    changed_record_count: Annotated[int, Field(ge=0, le=MAX_RECORDS_PER_SNAPSHOT)]

    @model_validator(mode="after")
    def require_consistent_public_state(self) -> SourceSnapshotSummary:
        try:
            SourceSnapshotRef(self.snapshot_id, self.snapshot_content_hash)
            RawObjectRef(
                self.raw_object_id, self.raw_content_hash, self.raw_byte_length
            )
        except (TypeError, ValueError):
            raise ValueError("snapshot summary identity is invalid") from None
        if (
            self.media_type != SYNTHETIC_MEDIA_TYPE
            or self.charset != SYNTHETIC_CHARSET
            or self.parser_id is not FiniteParserId.SYNTHETIC_JSON_V1
            or self.parser_version != SYNTHETIC_PARSER_VERSION
            or self.schema_id != SYNTHETIC_SCHEMA_ID
        ):
            raise ValueError("snapshot summary finite binding is invalid")
        if self.parsed_at < self.retrieved_at:
            raise ValueError("parsed snapshot cannot precede raw retrieval")
        if self.validated_at is not None and self.validated_at < self.parsed_at:
            raise ValueError("validation cannot precede snapshot parsing")
        if self.validation_reason_codes != sorted(set(self.validation_reason_codes)):
            raise ValueError("validation reason codes must be sorted and unique")
        validation_values = (
            self.validated_at,
            self.validation_report_hash,
            self.diff_content_hash,
        )
        if self.validation_passed is None:
            if (
                any(value is not None for value in validation_values)
                or any((self.validation_reason_codes, self.previous_snapshot))
                or any(
                    (
                        self.added_record_count,
                        self.removed_record_count,
                        self.changed_record_count,
                    )
                )
            ):
                raise ValueError("unvalidated snapshots cannot expose validation data")
        elif any(value is None for value in validation_values):
            raise ValueError("validated snapshots require hash and time summaries")
        expected_validation = {
            SourceSnapshotState.PARSED: None,
            SourceSnapshotState.QUARANTINED: False,
            SourceSnapshotState.VALIDATED: True,
            SourceSnapshotState.APPROVED: True,
            SourceSnapshotState.ACTIVE: True,
            SourceSnapshotState.SUPERSEDED: True,
            SourceSnapshotState.ROLLED_BACK: True,
        }[self.state]
        if self.validation_passed is not expected_validation:
            raise ValueError("validation result must match lifecycle state")
        if self.validation_passed is True and self.validation_reason_codes:
            raise ValueError("passing validation cannot expose blocking codes")
        if (
            self.validation_passed is True
            and self.declared_record_count != self.record_count
        ):
            raise ValueError("passing validation requires the declared record count")
        if self.validation_passed is False and not self.validation_reason_codes:
            raise ValueError("failed validation requires blocking codes")
        if self.active is not (self.state is SourceSnapshotState.ACTIVE):
            raise ValueError("active flag must match lifecycle state")
        return self


class SourceSnapshotDetail(ContractModel):
    summary: SourceSnapshotSummary
    diff: SourceSnapshotDiffView | None

    @model_validator(mode="after")
    def require_exact_diff(self) -> SourceSnapshotDetail:
        if self.diff is None:
            if self.summary.diff_content_hash is not None:
                raise ValueError("validated detail requires its deterministic diff")
        elif (
            self.summary.diff_content_hash != self.diff.content_hash
            or self.summary.snapshot_id != self.diff.new_snapshot.snapshot_id
            or self.summary.snapshot_content_hash != self.diff.new_snapshot.content_hash
            or self.summary.previous_snapshot != self.diff.previous_snapshot
            or self.summary.added_record_count != len(self.diff.added_record_ids)
            or self.summary.removed_record_count != len(self.diff.removed_record_ids)
            or self.summary.changed_record_count != len(self.diff.changed_records)
        ):
            raise ValueError("detail diff does not match snapshot summary")
        return self


class SourceSnapshotListing(ContractModel):
    snapshots: Annotated[list[SourceSnapshotSummary], Field(max_length=MAX_QUERY_LIMIT)]

    @model_validator(mode="after")
    def require_deterministic_order(self) -> SourceSnapshotListing:
        identities = [
            (item.snapshot_id, item.snapshot_content_hash) for item in self.snapshots
        ]
        if identities != sorted(set(identities)):
            raise ValueError("snapshot listing must be sorted and unique")
        return self


class SourceSnapshotHistoryRecord(ContractModel):
    sequence: Annotated[int, Field(ge=1, le=MAX_SEQUENCE)]
    event_type: SourceSnapshotEventType
    raw_object_id: Reference
    raw_content_hash: ContentHash
    raw_byte_length: Annotated[int, Field(ge=0, le=MAX_RAW_OBJECT_BYTES)]
    snapshot: SourceSnapshotReferenceView | None
    previous_active_snapshot: SourceSnapshotReferenceView | None
    actor_type: LifecycleActorType
    occurred_at: CanonicalDatetime
    validation_passed: bool | None
    validation_reason_codes: Annotated[
        list[ValidationReasonCode], Field(max_length=len(ValidationReasonCode))
    ]
    validation_report_hash: ContentHash | None
    diff_content_hash: ContentHash | None

    @model_validator(mode="after")
    def require_consistent_validation_summary(self) -> SourceSnapshotHistoryRecord:
        if self.validation_reason_codes != sorted(set(self.validation_reason_codes)):
            raise ValueError("validation reason codes must be sorted and unique")
        hashes = (self.validation_report_hash, self.diff_content_hash)
        if self.validation_passed is None:
            if (
                any(value is not None for value in hashes)
                or self.validation_reason_codes
            ):
                raise ValueError("non-validation events cannot expose validation data")
        elif any(value is None for value in hashes):
            raise ValueError("validation events require integrity hashes")
        validation_type = self.event_type in {
            SourceSnapshotEventType.VALIDATED,
            SourceSnapshotEventType.VALIDATION_FAILED,
        }
        if validation_type is not (self.validation_passed is not None):
            raise ValueError("validation data must match lifecycle event type")
        if self.event_type is SourceSnapshotEventType.VALIDATED and (
            self.validation_passed is not True or self.validation_reason_codes
        ):
            raise ValueError("VALIDATED requires a passing summary")
        if self.event_type is SourceSnapshotEventType.VALIDATION_FAILED and (
            self.validation_passed is not False or not self.validation_reason_codes
        ):
            raise ValueError("VALIDATION_FAILED requires blocking codes")
        snapshot_required = self.event_type not in {
            SourceSnapshotEventType.RETRIEVED,
            SourceSnapshotEventType.QUARANTINED,
        }
        if snapshot_required is not (self.snapshot is not None):
            raise ValueError("snapshot presence must match lifecycle event type")
        pointer_type = self.event_type in {
            SourceSnapshotEventType.ACTIVATED,
            SourceSnapshotEventType.ROLLED_BACK,
        }
        if not pointer_type and self.previous_active_snapshot is not None:
            raise ValueError("previous active reference is invalid for event type")
        if (
            self.event_type is SourceSnapshotEventType.ROLLED_BACK
            and self.previous_active_snapshot is None
        ):
            raise ValueError("rollback requires the replaced active snapshot")
        return self


class SourceSnapshotHistory(ContractModel):
    events: Annotated[
        list[SourceSnapshotHistoryRecord], Field(max_length=MAX_QUERY_LIMIT)
    ]

    @model_validator(mode="after")
    def require_ascending_sequence(self) -> SourceSnapshotHistory:
        sequences = [item.sequence for item in self.events]
        if any(
            right != left + 1
            for left, right in zip(sequences, sequences[1:], strict=False)
        ):
            raise ValueError("history must be a contiguous ascending sequence")
        return self


SOURCE_SNAPSHOT_CONTRACT_MODELS: tuple[type[ContractModel], ...] = (
    SourceSnapshotListing,
    SourceSnapshotDetail,
    SourceSnapshotHistory,
)


__all__ = [
    "ChangedSourceRecordView",
    "SOURCE_SNAPSHOT_CONTRACT_MODELS",
    "SourceSnapshotDetail",
    "SourceSnapshotDiffView",
    "SourceSnapshotHistory",
    "SourceSnapshotHistoryRecord",
    "SourceSnapshotListing",
    "SourceSnapshotReferenceView",
    "SourceSnapshotSummary",
]
