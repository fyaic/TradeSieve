"""Side-effect-free readiness proof for governed immutable source snapshots."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from tradesieve.domain.source_registry import (
    MAX_REQUIRED_SOURCES,
    SourceAvailability,
    SourceFreshnessStatus,
    SourceRegistration,
    SourceRuntimeObservation,
    SourceSetManifest,
    evaluate_source,
)
from tradesieve.domain.source_snapshot import (
    MAX_QUERY_LIMIT,
    LifecycleActorType,
    ParsedSourceSnapshot,
    RawObjectMetadata,
    SnapshotCommandAuditRecord,
    SourceSnapshotEventType,
    SourceSnapshotLifecycle,
    SourceSnapshotLifecycleEvent,
    SourceSnapshotState,
    validate_lifecycle_projection,
    verify_raw_bytes,
)
from tradesieve.ports.source_registry import SourceRegistryRepository
from tradesieve.ports.source_snapshot import (
    ImmutableRawObjectStore,
    SourceSnapshotRepository,
)


class SourceSnapshotReadinessService:
    """Prove required active evidence without authorization, audit, or mutation."""

    def __init__(
        self,
        source_registry: SourceRegistryRepository,
        snapshot_repository: SourceSnapshotRepository,
        object_store: ImmutableRawObjectStore,
        *,
        deployment_id: str,
        source_set_id: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._source_registry = source_registry
        self._snapshot_repository = snapshot_repository
        self._object_store = object_store
        self._deployment_id = deployment_id
        self._source_set_id = source_set_id
        self._clock = clock or (lambda: datetime.now(UTC))

    def is_ready(self) -> bool:
        """Return only a public boolean; every dependency or integrity error closes."""

        try:
            now = self._clock()
            if (
                not isinstance(now, datetime)
                or now.tzinfo is None
                or now.utcoffset() is None
            ):
                return False
            manifest = self._manifest()
            if manifest is None:
                return False
            return all(
                self._source_is_ready(source_id, now=now.astimezone(UTC))
                for source_id in manifest.required_source_ids
            )
        except Exception:
            return False

    def _manifest(self) -> SourceSetManifest | None:
        manifest = self._source_registry.get_manifest(
            self._deployment_id, self._source_set_id
        )
        if type(manifest) is not SourceSetManifest:
            return None
        rebuilt = replace(manifest)
        if (
            rebuilt != manifest
            or (manifest.deployment_id, manifest.source_set_id)
            != (self._deployment_id, self._source_set_id)
            or not manifest.required_source_ids
            or len(manifest.required_source_ids) > MAX_REQUIRED_SOURCES
        ):
            return None
        return manifest

    def _source_is_ready(self, source_id: str, *, now: datetime) -> bool:
        registration = self._source_registry.get_registration(
            self._deployment_id, source_id
        )
        if type(registration) is not SourceRegistration:
            return False
        if (
            replace(registration) != registration
            or (
                registration.deployment_id,
                registration.source_set_id,
                registration.source_id,
            )
            != (self._deployment_id, self._source_set_id, source_id)
            or not registration.active
        ):
            return False

        events, lifecycle = self._snapshot_repository.get_lifecycle_snapshot(
            self._deployment_id, source_id
        )
        if type(events) is not tuple or any(
            type(event) is not SourceSnapshotLifecycleEvent or replace(event) != event
            for event in events
        ):
            return False
        if (
            type(lifecycle) is not SourceSnapshotLifecycle
            or replace(lifecycle) != lifecycle
        ):
            return False
        validate_lifecycle_projection(events, lifecycle)
        observation = self._snapshot_repository.get_runtime_observation(
            self._deployment_id, source_id
        )
        if type(observation) is not SourceRuntimeObservation:
            return False
        observation = replace(observation)
        evaluation = evaluate_source(registration, observation, now=now)
        if (
            evaluation.status is not SourceFreshnessStatus.CURRENT
            or observation.availability is not SourceAvailability.AVAILABLE
            or lifecycle.active_snapshot is None
            or observation.active_snapshot_id != lifecycle.active_snapshot.snapshot_id
            or lifecycle.state_for(lifecycle.active_snapshot)
            is not SourceSnapshotState.ACTIVE
        ):
            return False

        snapshot = self._snapshot_repository.get_snapshot(
            self._deployment_id,
            source_id,
            lifecycle.active_snapshot.snapshot_id,
        )
        if type(snapshot) is not ParsedSourceSnapshot:
            return False
        snapshot.verify_integrity()
        if snapshot.reference() != lifecycle.active_snapshot:
            return False
        metadata = self._snapshot_repository.get_raw_metadata(
            self._deployment_id,
            source_id,
            snapshot.raw_object.object_id,
        )
        if type(metadata) is not RawObjectMetadata:
            return False
        if metadata.reference() != snapshot.raw_object or (
            metadata.deployment_id,
            metadata.source_id,
        ) != (self._deployment_id, source_id):
            return False
        content = self._object_store.get_verified(metadata.reference())
        verify_raw_bytes(metadata, content)
        metadata = RawObjectMetadata.from_bytes(
            deployment_id=metadata.deployment_id,
            source_id=metadata.source_id,
            original_name=metadata.original_name,
            media_type=metadata.media_type,
            charset=metadata.charset,
            retrieved_at=metadata.retrieved_at,
            effective_from=metadata.effective_from,
            content=content,
        )
        if not self._active_evidence_is_ready(
            events,
            snapshot,
            metadata,
            observation,
        ):
            return False
        audits = self._snapshot_repository.list_command_audits(
            self._deployment_id,
            source_id,
            limit=MAX_QUERY_LIMIT,
        )
        return (
            type(audits) is tuple
            and bool(audits)
            and all(
                type(item) is SnapshotCommandAuditRecord
                and replace(item) == item
                and (item.deployment_id, item.source_id)
                == (self._deployment_id, source_id)
                for item in audits
            )
        )

    @staticmethod
    def _active_evidence_is_ready(
        events: tuple[SourceSnapshotLifecycleEvent, ...],
        snapshot: ParsedSourceSnapshot,
        metadata: RawObjectMetadata,
        observation: SourceRuntimeObservation,
    ) -> bool:
        reference = snapshot.reference()
        validations = tuple(
            event
            for event in events
            if event.event_type is SourceSnapshotEventType.VALIDATED
            and event.snapshot == reference
        )
        approvals = tuple(
            event
            for event in events
            if event.event_type is SourceSnapshotEventType.APPROVED
            and event.snapshot == reference
        )
        pointer_events = tuple(
            event
            for event in events
            if event.event_type
            in {
                SourceSnapshotEventType.ACTIVATED,
                SourceSnapshotEventType.ROLLED_BACK,
            }
        )
        if len(validations) != 1 or len(approvals) != 1 or not pointer_events:
            return False
        validation = validations[0]
        approval = approvals[0]
        pointer = pointer_events[-1]
        report = validation.validation_report
        if report is None:
            return False
        return (
            report.passed
            and report.snapshot == reference
            and report.recomputed_content_hash() == report.content_hash
            and report.diff.recomputed_content_hash() == report.diff.content_hash
            and approval.actor_type is LifecycleActorType.HUMAN
            and pointer.actor_type is LifecycleActorType.HUMAN
            and pointer.snapshot == reference
            and pointer.raw_object == snapshot.raw_object
            and observation.observed_at == pointer.occurred_at
            and observation.retrieved_at == metadata.retrieved_at
            and observation.effective_from == metadata.effective_from
        )


__all__ = ["SourceSnapshotReadinessService"]
