"""Operations-safe source snapshot reads and exact official-citation resolution."""

from __future__ import annotations

from tradesieve.application.auth import ActorType, AuthorizedRequest, Operation, Scope
from tradesieve.application.source_snapshot import (
    SOURCE_TARGET_TYPE,
    ImmutableSourceSnapshotIdentity,
    SourceSnapshotAuthorizationBindingDenied,
    SourceSnapshotNotFound,
    SourceSnapshotUnavailable,
    source_authorization_target_id,
)
from tradesieve.application.source_snapshot_contracts import (
    ChangedSourceRecordView,
    SourceSnapshotDetail,
    SourceSnapshotDiffView,
    SourceSnapshotHistory,
    SourceSnapshotHistoryRecord,
    SourceSnapshotListing,
    SourceSnapshotReferenceView,
    SourceSnapshotSummary,
)
from tradesieve.domain.rule_bundle import OfficialSourceProvisionCitation
from tradesieve.domain.source_registry import SourceRegistration
from tradesieve.domain.source_snapshot import (
    MAX_LIFECYCLE_EVENTS,
    MAX_QUERY_LIMIT,
    MAX_RECORDS_PER_SNAPSHOT,
    SAFE_ID,
    InvalidSourceSnapshotTransition,
    ParsedSourceSnapshot,
    RawObjectMetadata,
    SourceSnapshotDiff,
    SourceSnapshotEventType,
    SourceSnapshotIntegrityError,
    SourceSnapshotLifecycle,
    SourceSnapshotLifecycleEvent,
    SourceSnapshotRef,
    SourceSnapshotState,
    ValidationReport,
    require_query_limit,
    validate_lifecycle_projection,
)
from tradesieve.ports.source_registry import SourceRegistryRepository
from tradesieve.ports.source_snapshot import (
    SYNTHETIC_CHARSET,
    SYNTHETIC_MEDIA_TYPE,
    SYNTHETIC_PARSER_VERSION,
    SYNTHETIC_SCHEMA_ID,
    FiniteParserId,
    SourceSnapshotRepository,
)


class SourceSnapshotQueryService:
    """Return bounded metadata views after exact source-level authorization."""

    def __init__(
        self,
        repository: SourceSnapshotRepository,
        source_registry: SourceRegistryRepository,
        *,
        deployment_id: str,
        source_set_id: str,
        control_tenant_id: str,
    ) -> None:
        for value in (deployment_id, source_set_id, control_tenant_id):
            if not isinstance(value, str) or SAFE_ID.fullmatch(value) is None:
                raise ValueError(
                    "source snapshot query configuration contains an invalid ID"
                )
        self._repository = repository
        self._source_registry = source_registry
        self._deployment_id = deployment_id
        self._source_set_id = source_set_id
        self._control_tenant_id = control_tenant_id

    def list_snapshots(
        self,
        authorized: AuthorizedRequest,
        *,
        source_id: str,
        limit: int = MAX_QUERY_LIMIT,
    ) -> SourceSnapshotListing:
        self._require_authorized(authorized, source_id)
        self._require_registration(source_id)
        require_query_limit(limit)
        events, lifecycle = self._lifecycle(source_id)
        try:
            snapshots = self._repository.list_snapshots(
                self._deployment_id, source_id, limit=MAX_QUERY_LIMIT
            )
        except Exception:
            raise SourceSnapshotUnavailable from None
        if not isinstance(snapshots, tuple) or len(snapshots) > MAX_QUERY_LIMIT:
            raise SourceSnapshotUnavailable from None
        try:
            ordered = tuple(sorted(snapshots, key=lambda item: item.snapshot_id))
        except (AttributeError, TypeError, ValueError):
            raise SourceSnapshotUnavailable from None
        if snapshots != ordered or len({item.snapshot_id for item in snapshots}) != len(
            snapshots
        ):
            raise SourceSnapshotUnavailable from None
        try:
            verified = tuple(
                self._verified_snapshot_value(item, source_id) for item in snapshots
            )
            summaries = [self._summary(item, events, lifecycle) for item in verified]
            return SourceSnapshotListing(snapshots=summaries[:limit])
        except SourceSnapshotUnavailable:
            raise
        except (AttributeError, TypeError, ValueError):
            raise SourceSnapshotUnavailable from None

    def get_snapshot(
        self,
        authorized: AuthorizedRequest,
        *,
        source_id: str,
        identity: ImmutableSourceSnapshotIdentity,
    ) -> SourceSnapshotDetail:
        self._require_authorized(authorized, source_id)
        self._require_registration(source_id)
        reference = self._identity_reference(identity)
        snapshot = self._snapshot(source_id, reference)
        events, lifecycle = self._lifecycle(source_id)
        try:
            report = self._validation_report(snapshot, events, lifecycle)
            return SourceSnapshotDetail(
                summary=self._summary(snapshot, events, lifecycle, report=report),
                diff=self._diff_view(report.diff) if report is not None else None,
            )
        except SourceSnapshotUnavailable:
            raise
        except (AttributeError, TypeError, ValueError):
            raise SourceSnapshotUnavailable from None

    def history(
        self,
        authorized: AuthorizedRequest,
        *,
        source_id: str,
        limit: int = MAX_QUERY_LIMIT,
    ) -> SourceSnapshotHistory:
        self._require_authorized(authorized, source_id)
        self._require_registration(source_id)
        require_query_limit(limit)
        events, _ = self._lifecycle(source_id)
        try:
            return SourceSnapshotHistory(
                events=[self._history_record(event) for event in events[-limit:]]
            )
        except (AttributeError, TypeError, ValueError):
            raise SourceSnapshotUnavailable from None

    def _require_authorized(
        self, authorized: AuthorizedRequest, source_id: str
    ) -> None:
        try:
            actor = authorized.context.actor
            expected_target = source_authorization_target_id(
                self._deployment_id, self._source_set_id, source_id
            )
            valid = (
                isinstance(authorized, AuthorizedRequest)
                and authorized.operation is Operation.SOURCE_READ
                and authorized.context.tenant_id == self._control_tenant_id
                and actor.tenant_id == self._control_tenant_id
                and authorized.target.tenant_id == self._control_tenant_id
                and authorized.target.object_type == SOURCE_TARGET_TYPE
                and authorized.target.object_id == expected_target
                and actor.actor_type in set(ActorType)
                and Scope.SOURCE_READ in actor.scopes
                and isinstance(actor.subject, str)
                and SAFE_ID.fullmatch(actor.subject) is not None
                and isinstance(authorized.audit_event_id, str)
                and SAFE_ID.fullmatch(authorized.audit_event_id) is not None
            )
        except (AttributeError, TypeError, ValueError):
            valid = False
        if not valid:
            raise SourceSnapshotAuthorizationBindingDenied from None

    def _require_registration(self, source_id: str) -> SourceRegistration:
        if not isinstance(source_id, str) or SAFE_ID.fullmatch(source_id) is None:
            raise SourceSnapshotNotFound from None
        try:
            registration = self._source_registry.get_registration(
                self._deployment_id, source_id
            )
        except Exception:
            raise SourceSnapshotUnavailable from None
        if not isinstance(registration, SourceRegistration) or (
            registration.deployment_id,
            registration.source_set_id,
            registration.source_id,
        ) != (self._deployment_id, self._source_set_id, source_id):
            raise SourceSnapshotNotFound from None
        return registration

    @staticmethod
    def _identity_reference(
        identity: ImmutableSourceSnapshotIdentity,
    ) -> SourceSnapshotRef:
        if not isinstance(identity, ImmutableSourceSnapshotIdentity):
            raise SourceSnapshotNotFound from None
        try:
            return SourceSnapshotRef(identity.snapshot_id, identity.content_hash)
        except (AttributeError, TypeError, ValueError):
            raise SourceSnapshotNotFound from None

    def _snapshot(
        self, source_id: str, reference: SourceSnapshotRef
    ) -> ParsedSourceSnapshot:
        try:
            snapshot = self._repository.get_snapshot(
                self._deployment_id, source_id, reference.snapshot_id
            )
        except Exception:
            raise SourceSnapshotUnavailable from None
        if snapshot is None:
            raise SourceSnapshotNotFound from None
        verified = self._verified_snapshot_value(snapshot, source_id)
        if verified.reference() != reference:
            raise SourceSnapshotNotFound from None
        return verified

    def _verified_snapshot_value(
        self, snapshot: object, source_id: str
    ) -> ParsedSourceSnapshot:
        try:
            if not isinstance(snapshot, ParsedSourceSnapshot) or (
                snapshot.deployment_id != self._deployment_id
                or snapshot.source_id != source_id
                or len(snapshot.records) > MAX_RECORDS_PER_SNAPSHOT
                or not _snapshot_has_finite_binding(snapshot)
            ):
                raise ValueError("snapshot binding is invalid")
            snapshot.verify_integrity()
            return snapshot
        except (AttributeError, SourceSnapshotIntegrityError, TypeError, ValueError):
            raise SourceSnapshotUnavailable from None

    def _raw_metadata(self, snapshot: ParsedSourceSnapshot) -> RawObjectMetadata:
        try:
            metadata = self._repository.get_raw_metadata(
                self._deployment_id, snapshot.source_id, snapshot.raw_object.object_id
            )
        except Exception:
            raise SourceSnapshotUnavailable from None
        try:
            valid = (
                isinstance(metadata, RawObjectMetadata)
                and metadata.deployment_id == self._deployment_id
                and metadata.source_id == snapshot.source_id
                and metadata.reference() == snapshot.raw_object
                and metadata.recomputed_object_id() == metadata.object_id
                and metadata.media_type == SYNTHETIC_MEDIA_TYPE
                and metadata.charset == SYNTHETIC_CHARSET
            )
        except (AttributeError, TypeError, ValueError):
            valid = False
        if not valid:
            raise SourceSnapshotUnavailable from None
        assert isinstance(metadata, RawObjectMetadata)
        return metadata

    def _lifecycle(
        self, source_id: str
    ) -> tuple[tuple[SourceSnapshotLifecycleEvent, ...], SourceSnapshotLifecycle]:
        try:
            result = self._repository.get_lifecycle_snapshot(
                self._deployment_id, source_id
            )
            events, lifecycle = result
        except Exception:
            raise SourceSnapshotUnavailable from None
        try:
            if (
                not isinstance(events, tuple)
                or len(events) > MAX_LIFECYCLE_EVENTS
                or any(
                    not isinstance(event, SourceSnapshotLifecycleEvent)
                    or event.deployment_id != self._deployment_id
                    or event.source_id != source_id
                    or not _event_is_reconstructable(event)
                    for event in events
                )
                or not isinstance(lifecycle, SourceSnapshotLifecycle)
            ):
                raise ValueError("lifecycle binding is invalid")
            validate_lifecycle_projection(events, lifecycle)
        except (
            AttributeError,
            InvalidSourceSnapshotTransition,
            TypeError,
            ValueError,
        ):
            raise SourceSnapshotUnavailable from None
        return events, lifecycle

    def _validation_report(
        self,
        snapshot: ParsedSourceSnapshot,
        events: tuple[SourceSnapshotLifecycleEvent, ...],
        lifecycle: SourceSnapshotLifecycle,
    ) -> ValidationReport | None:
        reference = snapshot.reference()
        state = lifecycle.state_for(reference)
        if state is None:
            raise SourceSnapshotUnavailable from None
        candidates = tuple(
            event.validation_report
            for event in events
            if event.snapshot == reference
            and event.event_type
            in {
                SourceSnapshotEventType.VALIDATED,
                SourceSnapshotEventType.VALIDATION_FAILED,
            }
        )
        if state is SourceSnapshotState.PARSED:
            if candidates:
                raise SourceSnapshotUnavailable from None
            return None
        if len(candidates) != 1 or candidates[0] is None:
            raise SourceSnapshotUnavailable from None
        report = candidates[0]
        try:
            reasons = tuple(
                sorted(
                    set(report.reasons),
                    key=lambda item: (
                        item.code,
                        item.record_id or "",
                        item.record_locator or "",
                    ),
                )
            )
            valid = (
                report.snapshot == reference
                and report.expected_schema_id == SYNTHETIC_SCHEMA_ID
                and report.recomputed_content_hash() == report.content_hash
                and report.diff.recomputed_content_hash() == report.diff.content_hash
                and report.diff.new_snapshot == reference
                and report.reasons == reasons
                and len(report.diff.added_record_ids) <= MAX_RECORDS_PER_SNAPSHOT
                and len(report.diff.removed_record_ids) <= MAX_RECORDS_PER_SNAPSHOT
                and len(report.diff.changed_records) <= MAX_RECORDS_PER_SNAPSHOT
            )
        except (AttributeError, TypeError, ValueError):
            valid = False
        if not valid:
            raise SourceSnapshotUnavailable from None
        return report

    def _summary(
        self,
        snapshot: ParsedSourceSnapshot,
        events: tuple[SourceSnapshotLifecycleEvent, ...],
        lifecycle: SourceSnapshotLifecycle,
        *,
        report: ValidationReport | None | object = ...,
    ) -> SourceSnapshotSummary:
        metadata = self._raw_metadata(snapshot)
        checked_report = (
            self._validation_report(snapshot, events, lifecycle)
            if report is ...
            else report
        )
        if checked_report is not None and not isinstance(
            checked_report, ValidationReport
        ):
            raise SourceSnapshotUnavailable from None
        state = lifecycle.state_for(snapshot.reference())
        if state is None:
            raise SourceSnapshotUnavailable from None
        diff = checked_report.diff if checked_report is not None else None
        reasons = (
            sorted({item.code for item in checked_report.reasons})
            if checked_report is not None
            else []
        )
        return SourceSnapshotSummary(
            deployment_id=snapshot.deployment_id,
            source_id=snapshot.source_id,
            snapshot_id=snapshot.snapshot_id,
            snapshot_content_hash=snapshot.content_hash,
            raw_object_id=metadata.object_id,
            raw_content_hash=metadata.content_hash,
            raw_byte_length=metadata.byte_length,
            media_type=metadata.media_type,
            charset=metadata.charset,
            parser_id=FiniteParserId(snapshot.parser_id),
            parser_version=snapshot.parser_version,
            schema_id=snapshot.schema_id,
            declared_record_count=snapshot.declared_record_count,
            record_count=len(snapshot.records),
            retrieved_at=metadata.retrieved_at,
            effective_from=metadata.effective_from,
            parsed_at=snapshot.parsed_at,
            state=state,
            active=state is SourceSnapshotState.ACTIVE,
            validation_passed=(checked_report.passed if checked_report else None),
            validation_reason_codes=reasons,
            validated_at=(checked_report.validated_at if checked_report else None),
            validation_report_hash=(
                checked_report.content_hash if checked_report else None
            ),
            previous_snapshot=(
                _reference_view(diff.previous_snapshot) if diff is not None else None
            ),
            diff_content_hash=(diff.content_hash if diff is not None else None),
            added_record_count=(len(diff.added_record_ids) if diff else 0),
            removed_record_count=(len(diff.removed_record_ids) if diff else 0),
            changed_record_count=(len(diff.changed_records) if diff else 0),
        )

    @staticmethod
    def _diff_view(diff: SourceSnapshotDiff) -> SourceSnapshotDiffView:
        return SourceSnapshotDiffView(
            previous_snapshot=_reference_view(diff.previous_snapshot),
            new_snapshot=_required_reference_view(diff.new_snapshot),
            content_hash=diff.content_hash,
            added_record_ids=list(diff.added_record_ids),
            removed_record_ids=list(diff.removed_record_ids),
            changed_records=[
                ChangedSourceRecordView(
                    source_record_id=item.source_record_id,
                    previous_record_hash=item.previous_record_hash,
                    new_record_hash=item.new_record_hash,
                )
                for item in diff.changed_records
            ],
        )

    @staticmethod
    def _history_record(
        event: SourceSnapshotLifecycleEvent,
    ) -> SourceSnapshotHistoryRecord:
        report = event.validation_report
        return SourceSnapshotHistoryRecord(
            sequence=event.sequence,
            event_type=event.event_type,
            raw_object_id=event.raw_object.object_id,
            raw_content_hash=event.raw_object.content_hash,
            raw_byte_length=event.raw_object.byte_length,
            snapshot=_reference_view(event.snapshot),
            previous_active_snapshot=_reference_view(event.previous_active_snapshot),
            actor_type=event.actor_type,
            occurred_at=event.occurred_at,
            validation_passed=(report.passed if report else None),
            validation_reason_codes=(
                sorted({item.code for item in report.reasons}) if report else []
            ),
            validation_report_hash=(report.content_hash if report else None),
            diff_content_hash=(report.diff.content_hash if report else None),
        )


class SourceSnapshotOfficialCitationResolver:
    """Fail-closed verification of one locator in an accepted immutable snapshot."""

    def __init__(
        self,
        repository: SourceSnapshotRepository,
        source_registry: SourceRegistryRepository,
        *,
        deployment_id: str,
        source_set_id: str,
        control_tenant_id: str,
    ) -> None:
        for value in (deployment_id, source_set_id, control_tenant_id):
            if not isinstance(value, str) or SAFE_ID.fullmatch(value) is None:
                raise ValueError(
                    "official citation resolver configuration contains an invalid ID"
                )
        self._repository = repository
        self._source_registry = source_registry
        self._deployment_id = deployment_id
        self._source_set_id = source_set_id
        self._control_tenant_id = control_tenant_id

    def verify(
        self,
        *,
        tenant_id: str,
        deployment_id: str,
        citation: OfficialSourceProvisionCitation,
    ) -> bool:
        try:
            if (
                tenant_id != self._control_tenant_id
                or deployment_id != self._deployment_id
                or not isinstance(citation, OfficialSourceProvisionCitation)
                or not isinstance(citation.source_id, str)
                or SAFE_ID.fullmatch(citation.source_id) is None
            ):
                return False
            if (
                OfficialSourceProvisionCitation(
                    citation_ref=citation.citation_ref,
                    source_id=citation.source_id,
                    snapshot_id=citation.snapshot_id,
                    snapshot_content_hash=citation.snapshot_content_hash,
                    provision_locator=citation.provision_locator,
                )
                != citation
            ):
                return False
            registration = self._source_registry.get_registration(
                self._deployment_id, citation.source_id
            )
            if not isinstance(registration, SourceRegistration) or (
                registration.deployment_id,
                registration.source_set_id,
                registration.source_id,
            ) != (self._deployment_id, self._source_set_id, citation.source_id):
                return False
            reference = SourceSnapshotRef(
                citation.snapshot_id, citation.snapshot_content_hash
            )
            snapshot = self._repository.get_snapshot(
                self._deployment_id, citation.source_id, reference.snapshot_id
            )
            if not isinstance(snapshot, ParsedSourceSnapshot) or (
                snapshot.deployment_id,
                snapshot.source_id,
                snapshot.reference(),
            ) != (self._deployment_id, citation.source_id, reference):
                return False
            snapshot.verify_integrity()
            if not _snapshot_has_finite_binding(snapshot):
                return False
            metadata = self._repository.get_raw_metadata(
                self._deployment_id, citation.source_id, snapshot.raw_object.object_id
            )
            if (
                not isinstance(metadata, RawObjectMetadata)
                or metadata.deployment_id != self._deployment_id
                or metadata.source_id != citation.source_id
                or metadata.reference() != snapshot.raw_object
                or metadata.recomputed_object_id() != metadata.object_id
                or metadata.media_type != SYNTHETIC_MEDIA_TYPE
                or metadata.charset != SYNTHETIC_CHARSET
            ):
                return False
            events, lifecycle = self._repository.get_lifecycle_snapshot(
                self._deployment_id, citation.source_id
            )
            if (
                not isinstance(events, tuple)
                or len(events) > MAX_LIFECYCLE_EVENTS
                or any(
                    not isinstance(event, SourceSnapshotLifecycleEvent)
                    or event.deployment_id != self._deployment_id
                    or event.source_id != citation.source_id
                    or not _event_is_reconstructable(event)
                    for event in events
                )
                or not isinstance(lifecycle, SourceSnapshotLifecycle)
            ):
                return False
            validate_lifecycle_projection(events, lifecycle)
            state = lifecycle.state_for(reference)
            if (
                reference not in lifecycle.activated_snapshots
                or state
                not in {
                    SourceSnapshotState.ACTIVE,
                    SourceSnapshotState.SUPERSEDED,
                    SourceSnapshotState.ROLLED_BACK,
                }
                or not _has_verified_passing_validation(events, snapshot)
            ):
                return False
            matches = sum(
                int(record.native_locator == citation.provision_locator)
                + sum(
                    assertion.native_locator == citation.provision_locator
                    for assertion in record.assertions
                )
                for record in snapshot.records
            )
            return matches == 1
        except Exception:
            return False


def _reference_view(
    reference: SourceSnapshotRef | None,
) -> SourceSnapshotReferenceView | None:
    if reference is None:
        return None
    return SourceSnapshotReferenceView(
        snapshot_id=reference.snapshot_id, content_hash=reference.content_hash
    )


def _required_reference_view(
    reference: SourceSnapshotRef,
) -> SourceSnapshotReferenceView:
    return SourceSnapshotReferenceView(
        snapshot_id=reference.snapshot_id, content_hash=reference.content_hash
    )


def _event_is_reconstructable(event: SourceSnapshotLifecycleEvent) -> bool:
    try:
        return (
            SourceSnapshotLifecycleEvent(
                sequence=event.sequence,
                event_id=event.event_id,
                deployment_id=event.deployment_id,
                source_id=event.source_id,
                event_type=event.event_type,
                raw_object=event.raw_object,
                snapshot=event.snapshot,
                previous_active_snapshot=event.previous_active_snapshot,
                actor_id=event.actor_id,
                actor_type=event.actor_type,
                reason=event.reason,
                occurred_at=event.occurred_at,
                validation_report=event.validation_report,
            )
            == event
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _has_verified_passing_validation(
    events: tuple[SourceSnapshotLifecycleEvent, ...],
    snapshot: ParsedSourceSnapshot,
) -> bool:
    reference = snapshot.reference()
    validation_events = tuple(
        event
        for event in events
        if event.snapshot == reference
        and event.event_type
        in {
            SourceSnapshotEventType.VALIDATED,
            SourceSnapshotEventType.VALIDATION_FAILED,
        }
    )
    if len(validation_events) != 1:
        return False
    event = validation_events[0]
    report = event.validation_report
    if event.event_type is not SourceSnapshotEventType.VALIDATED or report is None:
        return False
    try:
        ordered_reasons = tuple(
            sorted(
                set(report.reasons),
                key=lambda item: (
                    item.code,
                    item.record_id or "",
                    item.record_locator or "",
                ),
            )
        )
        return (
            report.passed
            and report.snapshot == reference
            and report.expected_schema_id == SYNTHETIC_SCHEMA_ID
            and report.reasons == ordered_reasons
            and report.recomputed_content_hash() == report.content_hash
            and report.diff.recomputed_content_hash() == report.diff.content_hash
            and report.diff.new_snapshot == reference
        )
    except (AttributeError, TypeError, ValueError):
        return False


__all__ = [
    "ChangedSourceRecordView",
    "SourceSnapshotOfficialCitationResolver",
    "SourceSnapshotDetail",
    "SourceSnapshotDiffView",
    "SourceSnapshotHistory",
    "SourceSnapshotHistoryRecord",
    "SourceSnapshotListing",
    "SourceSnapshotQueryService",
    "SourceSnapshotReferenceView",
    "SourceSnapshotSummary",
]


def _snapshot_has_finite_binding(snapshot: ParsedSourceSnapshot) -> bool:
    return (
        snapshot.parser_id == FiniteParserId.SYNTHETIC_JSON_V1
        and snapshot.parser_version == SYNTHETIC_PARSER_VERSION
        and snapshot.schema_id == SYNTHETIC_SCHEMA_ID
    )
