"""Source registry application-service and safe projection tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from tradesieve.application.contracts import SourceStatusValue
from tradesieve.application.source_registry import (
    SourceActivationState,
    SourceNotFound,
    SourceRegistryRecord,
    SourceRegistryService,
    readiness_check_value,
)
from tradesieve.domain.source_registry import (
    MAX_FRESHNESS_SECONDS,
    MAX_LONG_TEXT_LENGTH,
    MAX_SHORT_TEXT_LENGTH,
    MAX_SOURCE_LISTING_RECORDS,
    ActivationBlockReason,
    ObservationConflict,
    ObservationWriteOutcome,
    SourceAccessMethod,
    SourceAvailability,
    SourceFreshnessStatus,
    SourceRegistration,
    SourceRuntimeObservation,
    SourceSetManifest,
)

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


class MemorySourceRegistry:
    def __init__(self) -> None:
        self.manifests: dict[tuple[str, str], SourceSetManifest] = {}
        self.registrations: dict[tuple[str, str], SourceRegistration] = {}
        self.observations: dict[tuple[str, str], SourceRuntimeObservation] = {}

    def save_manifest(self, manifest: SourceSetManifest) -> None:
        self.manifests[(manifest.deployment_id, manifest.source_set_id)] = manifest

    def get_manifest(
        self, deployment_id: str, source_set_id: str
    ) -> SourceSetManifest | None:
        return self.manifests.get((deployment_id, source_set_id))

    def save_registration(self, registration: SourceRegistration) -> None:
        self.registrations[(registration.deployment_id, registration.source_id)] = (
            registration
        )

    def get_registration(
        self, deployment_id: str, source_id: str
    ) -> SourceRegistration | None:
        return self.registrations.get((deployment_id, source_id))

    def save_observation(
        self, observation: SourceRuntimeObservation
    ) -> ObservationWriteOutcome:
        key = (observation.deployment_id, observation.source_id)
        current = self.observations.get(key)
        if current is not None and observation.observed_at <= current.observed_at:
            if observation == current:
                return ObservationWriteOutcome.IDEMPOTENT
            raise ObservationConflict
        self.observations[key] = observation
        return ObservationWriteOutcome.APPLIED

    def list_entries(
        self, deployment_id: str, source_set_id: str, *, limit: int
    ) -> tuple[tuple[SourceRegistration, SourceRuntimeObservation | None], ...]:
        registrations = [
            item
            for item in self.registrations.values()
            if item.deployment_id == deployment_id
            and item.source_set_id == source_set_id
        ]
        return tuple(
            (
                item,
                self.observations.get((item.deployment_id, item.source_id)),
            )
            for item in reversed(registrations)
        )[:limit]


def registration(
    source_id: str = "source-1", **overrides: object
) -> SourceRegistration:
    values: dict[str, object] = {
        "deployment_id": "deployment-1",
        "source_set_id": "source-set-1",
        "source_id": source_id,
        "name": f"Synthetic {source_id}",
        "owner": "Source owner",
        "responsible_operator": "source-operator",
        "jurisdiction": "SYNTHETIC",
        "legal_scope": "Synthetic legal scope",
        "data_scope": "Synthetic entity records",
        "access_method": SourceAccessMethod.API,
        "credential_secret_ref": f"vault:sources/{source_id}",
        "licence_summary": "Public-safe synthetic licence summary",
        "contractual_constraints": "PRIVATE CONTRACT TERM",
        "refresh_expectation": timedelta(hours=1),
        "stale_after": timedelta(hours=2),
    }
    values.update(overrides)
    return SourceRegistration(**values)  # type: ignore[arg-type]


def observation(
    source_id: str = "source-1", **overrides: object
) -> SourceRuntimeObservation:
    values: dict[str, object] = {
        "deployment_id": "deployment-1",
        "source_id": source_id,
        "availability": SourceAvailability.AVAILABLE,
        "observed_at": NOW,
        "active_snapshot_id": f"snapshot-{source_id}",
        "retrieved_at": NOW,
        "effective_from": NOW - timedelta(days=1),
    }
    values.update(overrides)
    return SourceRuntimeObservation(**values)  # type: ignore[arg-type]


def service(
    repository: MemorySourceRegistry | None = None,
) -> tuple[SourceRegistryService, MemorySourceRegistry]:
    storage = repository or MemorySourceRegistry()
    return SourceRegistryService(storage, clock=lambda: NOW), storage


def test_registration_activation_observation_and_safe_listing() -> None:
    registry, storage = service()
    registry.define_required_sources("deployment-1", "source-set-1", ("source-1",))
    registry.register(registration())
    active = registry.activate("deployment-1", "source-1")
    assert active.active is True
    assert registry.record_observation(observation()) is ObservationWriteOutcome.APPLIED

    listing = registry.query_source_set("deployment-1", "source-set-1")
    assert listing.ready is True
    assert listing.status is SourceStatusValue.CURRENT
    assert listing.issues == []
    record = listing.sources[0]
    assert record.required is True
    assert record.activation_state is SourceActivationState.ACTIVE
    assert record.access_method is SourceAccessMethod.API
    assert record.status is SourceStatusValue.CURRENT
    assert record.active_snapshot_id == "snapshot-source-1"
    assert record.retrieved_at == NOW
    assert record.refresh_expectation_seconds == 3600
    assert record.stale_after_seconds == 7200
    payload = listing.model_dump_json()
    assert "credential" not in payload.lower()
    assert "PRIVATE CONTRACT TERM" not in payload
    assert "Public-safe synthetic licence summary" in payload
    assert storage.get_manifest("deployment-1", "source-set-1") is not None


def test_source_dto_schema_serialization_and_direct_validation_are_constrained() -> (
    None
):
    registry, _ = service()
    registry.register(registration())
    registry.activate("deployment-1", "source-1")
    registry.record_observation(observation())
    record = registry.query_source_set("deployment-1", "source-set-1").sources[0]
    payload = record.model_dump(mode="json")
    assert payload["access_method"] == "API"

    schema = SourceRegistryRecord.model_json_schema()["properties"]
    assert any(
        alternative.get("maxLength") == MAX_LONG_TEXT_LENGTH
        for alternative in schema["legal_scope"]["anyOf"]
    )
    assert any(
        alternative.get("$ref", "").endswith("/SourceAccessMethod")
        for alternative in schema["access_method"]["anyOf"]
    )
    assert any(
        alternative.get("minimum") == 1
        and alternative.get("maximum") == MAX_FRESHNESS_SECONDS
        for alternative in schema["refresh_expectation_seconds"]["anyOf"]
    )

    payload["legal_scope"] = "x" * (MAX_LONG_TEXT_LENGTH + 1)
    with pytest.raises(ValidationError):
        SourceRegistryRecord.model_validate(payload)
    payload["legal_scope"] = "Synthetic legal scope"
    payload["access_method"] = "UNCONSTRAINED_VALUE"
    with pytest.raises(ValidationError):
        SourceRegistryRecord.model_validate(payload)
    payload["access_method"] = "API"
    payload["refresh_expectation_seconds"] = 0
    with pytest.raises(ValidationError):
        SourceRegistryRecord.model_validate(payload)


def test_text_boundaries_survive_safe_projection() -> None:
    registry, _ = service()
    registry.define_required_sources("deployment-1", "source-set-1", ("source-1",))
    registry.register(
        registration(
            name="n" * MAX_SHORT_TEXT_LENGTH,
            legal_scope="l" * MAX_LONG_TEXT_LENGTH,
        )
    )
    registry.activate("deployment-1", "source-1")
    registry.record_observation(observation())
    record = registry.query_source_set("deployment-1", "source-set-1").sources[0]
    assert len(record.name or "") == MAX_SHORT_TEXT_LENGTH
    assert len(record.legal_scope or "") == MAX_LONG_TEXT_LENGTH


def test_listing_is_sorted_and_optional_failure_does_not_fail_readiness() -> None:
    registry, _ = service()
    registry.define_required_sources("deployment-1", "source-set-1", ("source-2",))
    for source_id in ["source-2", "source-1"]:
        registry.register(registration(source_id))
        registry.activate("deployment-1", source_id)
    registry.record_observation(observation("source-2"))
    registry.record_observation(
        observation("source-1", availability=SourceAvailability.UNAVAILABLE)
    )

    listing = registry.query_source_set("deployment-1", "source-set-1")
    assert [item.source_id for item in listing.sources] == [
        "source-1",
        "source-2",
    ]
    assert listing.ready is True
    assert listing.sources[0].required is False
    assert listing.sources[0].status is SourceStatusValue.UNAVAILABLE
    assert registry.list_sources("deployment-1", "source-set-1") == listing.sources


def test_missing_manifest_member_is_an_explicit_sorted_issue() -> None:
    registry, _ = service()
    registry.define_required_sources(
        "deployment-1",
        "source-set-1",
        ("source-missing-b", "source-missing-a"),
    )
    listing = registry.query_source_set("deployment-1", "source-set-1")
    assert listing.ready is False
    assert listing.status is SourceStatusValue.UNAVAILABLE
    assert [issue.model_dump() for issue in listing.issues] == [
        {"source_id": "source-missing-a", "status": "UNAVAILABLE"},
        {"source_id": "source-missing-b", "status": "UNAVAILABLE"},
    ]


def test_draft_is_visible_as_non_active_and_never_current() -> None:
    registry, _ = service()
    registry.define_required_sources("deployment-1", "source-set-1", ("source-draft",))
    registry.register(
        SourceRegistration("deployment-1", "source-set-1", "source-draft")
    )
    listing = registry.query_source_set("deployment-1", "source-set-1")
    record = listing.sources[0]
    assert record.activation_state is SourceActivationState.DRAFT
    assert record.status is SourceStatusValue.QUARANTINED
    assert record.activation_block_reasons
    assert listing.issues[0].status is SourceStatusValue.QUARANTINED


def test_blank_draft_governance_is_safely_normalized_and_fails_closed() -> None:
    registry, _ = service()
    registry.define_required_sources("deployment-1", "source-set-1", ("source-1",))
    registry.register(
        registration(
            name="  Synthetic draft  ",
            owner="  ",
            legal_scope="",
        )
    )
    listing = registry.query_source_set("deployment-1", "source-set-1")
    record = listing.sources[0]
    assert record.name == "Synthetic draft"
    assert record.owner is None
    assert record.legal_scope is None
    assert record.status is SourceStatusValue.QUARANTINED
    assert record.activation_block_reasons == [
        ActivationBlockReason.MISSING_OWNER,
        ActivationBlockReason.MISSING_LEGAL_SCOPE,
    ]
    assert listing.ready is False
    assert listing.issues[0].status is SourceStatusValue.QUARANTINED


def test_service_rejects_unknown_source_and_duplicate_manifest() -> None:
    registry, _ = service()
    with pytest.raises(SourceNotFound, match="source not found"):
        registry.activate("deployment-1", "missing")
    with pytest.raises(SourceNotFound):
        registry.record_observation(observation("missing"))
    with pytest.raises(ValueError, match="sorted and unique"):
        registry.define_required_sources(
            "deployment-1", "source-set-1", ("source-1", "source-1")
        )


def test_observation_updates_are_monotonic_and_explicit() -> None:
    registry, _ = service()
    registry.register(registration())
    current = observation()
    assert registry.record_observation(current) is ObservationWriteOutcome.APPLIED
    assert registry.record_observation(current) is ObservationWriteOutcome.IDEMPOTENT
    with pytest.raises(ObservationConflict):
        registry.record_observation(
            observation(
                observed_at=NOW,
                availability=SourceAvailability.UNAVAILABLE,
            )
        )
    with pytest.raises(ObservationConflict):
        registry.record_observation(
            observation(
                observed_at=NOW - timedelta(seconds=1),
                retrieved_at=NOW - timedelta(seconds=1),
            )
        )


def test_default_clock_and_empty_manifest_fail_closed() -> None:
    storage = MemorySourceRegistry()
    registry = SourceRegistryService(storage)
    registry.define_required_sources("deployment-1", "source-set-1", ())
    listing = registry.query_source_set("deployment-1", "source-set-1")
    assert listing.ready is False
    assert listing.sources == []


def test_listing_refuses_more_than_the_bounded_query_seam() -> None:
    registry, _ = service()
    for index in range(MAX_SOURCE_LISTING_RECORDS + 1):
        registry.register(
            SourceRegistration(
                "deployment-1",
                "source-set-1",
                f"source-{index:03d}",
            )
        )
    with pytest.raises(ValueError, match="source listing exceeds"):
        registry.query_source_set("deployment-1", "source-set-1")


@pytest.mark.parametrize(
    ("ready", "status", "expected"),
    [
        (True, SourceFreshnessStatus.CURRENT, "OK"),
        (False, SourceFreshnessStatus.STALE, "STALE"),
        (False, SourceFreshnessStatus.QUARANTINED, "QUARANTINED"),
        (False, SourceFreshnessStatus.UNAVAILABLE, "UNAVAILABLE"),
    ],
)
def test_readiness_check_value_preserves_internal_reason(
    ready: bool, status: SourceFreshnessStatus, expected: str
) -> None:
    from tradesieve.domain.source_registry import SourceSetReadiness

    value = SourceSetReadiness(ready, status, (), ())
    assert readiness_check_value(value) == expected
