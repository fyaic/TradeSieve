"""Application service and credential-free read model for source governance."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import Field, StringConstraints

from tradesieve.application.contracts import (
    CanonicalDatetime,
    ContractModel,
    Reference,
    ShortText,
    SourceStatusValue,
)
from tradesieve.domain.source_registry import (
    MAX_FRESHNESS_SECONDS,
    MAX_LONG_TEXT_LENGTH,
    MAX_REQUIRED_SOURCES,
    MAX_SOURCE_LISTING_RECORDS,
    ActivationBlockReason,
    ObservationWriteOutcome,
    SourceAccessMethod,
    SourceEvaluation,
    SourceFreshnessStatus,
    SourceRegistration,
    SourceRuntimeObservation,
    SourceSetIssue,
    SourceSetManifest,
    SourceSetReadiness,
    evaluate_required_source_set,
    evaluate_source,
)
from tradesieve.ports.source_registry import SourceRegistryRepository


class SourceActivationState(StrEnum):
    ACTIVE = "ACTIVE"
    DRAFT = "DRAFT"


SourceLongText = Annotated[
    str,
    StringConstraints(min_length=1, max_length=MAX_LONG_TEXT_LENGTH),
]
FreshnessSeconds = Annotated[int, Field(ge=1, le=MAX_FRESHNESS_SECONDS)]


def _public_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


class SourceRegistryRecord(ContractModel):
    """Operations-safe DTO that deliberately excludes credential_secret_ref."""

    deployment_id: Reference
    source_set_id: Reference
    source_id: Reference
    name: ShortText | None
    owner: ShortText | None
    responsible_operator: ShortText | None
    jurisdiction: ShortText | None
    legal_scope: SourceLongText | None
    data_scope: SourceLongText | None
    access_method: SourceAccessMethod | None
    licence_summary: SourceLongText | None
    required: bool
    refresh_expectation_seconds: FreshnessSeconds | None
    stale_after_seconds: FreshnessSeconds | None
    activation_state: SourceActivationState
    activation_block_reasons: Annotated[
        list[ActivationBlockReason], Field(max_length=11)
    ]
    active_snapshot_id: Reference | None
    status: SourceStatusValue
    retrieved_at: CanonicalDatetime | None
    effective_from: CanonicalDatetime | None


class SourceSetIssueRecord(ContractModel):
    source_id: Reference
    status: SourceStatusValue


class SourceRegistryListing(ContractModel):
    deployment_id: Reference
    source_set_id: Reference
    ready: bool
    status: SourceStatusValue
    issues: Annotated[
        list[SourceSetIssueRecord], Field(max_length=MAX_REQUIRED_SOURCES)
    ]
    sources: Annotated[
        list[SourceRegistryRecord], Field(max_length=MAX_SOURCE_LISTING_RECORDS)
    ]


class SourceNotFound(Exception):
    def __init__(self) -> None:
        super().__init__("source not found")


class SourceRegistryService:
    """Coordinate governance activation, observations, reads, and readiness."""

    def __init__(
        self,
        repository: SourceRegistryRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))

    def register(self, registration: SourceRegistration) -> None:
        self._repository.save_registration(registration)

    def define_required_sources(
        self,
        deployment_id: str,
        source_set_id: str,
        required_source_ids: tuple[str, ...],
    ) -> None:
        self._repository.save_manifest(
            SourceSetManifest(
                deployment_id,
                source_set_id,
                tuple(sorted(required_source_ids)),
            )
        )

    def activate(self, deployment_id: str, source_id: str) -> SourceRegistration:
        registration = self._repository.get_registration(deployment_id, source_id)
        if registration is None:
            raise SourceNotFound
        active = registration.activated()
        self._repository.save_registration(active)
        return active

    def record_observation(
        self, observation: SourceRuntimeObservation
    ) -> ObservationWriteOutcome:
        registration = self._repository.get_registration(
            observation.deployment_id, observation.source_id
        )
        if registration is None:
            raise SourceNotFound
        return self._repository.save_observation(observation)

    def list_sources(
        self, deployment_id: str, source_set_id: str
    ) -> list[SourceRegistryRecord]:
        return self.query_source_set(deployment_id, source_set_id).sources

    def query_source_set(
        self, deployment_id: str, source_set_id: str
    ) -> SourceRegistryListing:
        evaluations = self._evaluations(deployment_id, source_set_id)
        manifest = self._repository.get_manifest(deployment_id, source_set_id)
        expected = manifest.required_source_ids if manifest is not None else ()
        readiness = evaluate_required_source_set(evaluations, expected)
        return SourceRegistryListing(
            deployment_id=deployment_id,
            source_set_id=source_set_id,
            ready=readiness.ready,
            status=SourceStatusValue(readiness.status.value),
            issues=[self._to_issue_record(issue) for issue in readiness.issues],
            sources=[
                self._to_record(item, required=item.registration.source_id in expected)
                for item in evaluations
            ],
        )

    def required_set_readiness(
        self, deployment_id: str, source_set_id: str
    ) -> SourceSetReadiness:
        manifest = self._repository.get_manifest(deployment_id, source_set_id)
        expected = manifest.required_source_ids if manifest is not None else ()
        return evaluate_required_source_set(
            self._evaluations(deployment_id, source_set_id), expected
        )

    def _evaluations(
        self, deployment_id: str, source_set_id: str
    ) -> tuple[SourceEvaluation, ...]:
        now = self._clock()
        entries = sorted(
            self._repository.list_entries(
                deployment_id,
                source_set_id,
                limit=MAX_SOURCE_LISTING_RECORDS + 1,
            ),
            key=lambda item: item[0].source_id,
        )
        if len(entries) > MAX_SOURCE_LISTING_RECORDS:
            raise ValueError(
                f"source listing exceeds {MAX_SOURCE_LISTING_RECORDS} records"
            )
        return tuple(
            evaluate_source(registration, observation, now=now)
            for registration, observation in entries
        )

    @staticmethod
    def _to_record(
        evaluation: SourceEvaluation, *, required: bool
    ) -> SourceRegistryRecord:
        registration = evaluation.registration
        observation = evaluation.observation
        return SourceRegistryRecord(
            deployment_id=registration.deployment_id,
            source_set_id=registration.source_set_id,
            source_id=registration.source_id,
            name=_public_optional_text(registration.name),
            owner=_public_optional_text(registration.owner),
            responsible_operator=_public_optional_text(
                registration.responsible_operator
            ),
            jurisdiction=_public_optional_text(registration.jurisdiction),
            legal_scope=_public_optional_text(registration.legal_scope),
            data_scope=_public_optional_text(registration.data_scope),
            access_method=registration.access_method,
            licence_summary=_public_optional_text(registration.licence_summary),
            required=required,
            refresh_expectation_seconds=(
                int(registration.refresh_expectation.total_seconds())
                if registration.refresh_expectation is not None
                else None
            ),
            stale_after_seconds=(
                int(registration.stale_after.total_seconds())
                if registration.stale_after is not None
                else None
            ),
            activation_state=(
                SourceActivationState.ACTIVE
                if registration.active
                else SourceActivationState.DRAFT
            ),
            activation_block_reasons=list(registration.activation_block_reasons()),
            active_snapshot_id=(
                observation.active_snapshot_id if observation is not None else None
            ),
            status=SourceStatusValue(evaluation.status.value),
            retrieved_at=(
                observation.retrieved_at if observation is not None else None
            ),
            effective_from=(
                observation.effective_from if observation is not None else None
            ),
        )

    @staticmethod
    def _to_issue_record(issue: SourceSetIssue) -> SourceSetIssueRecord:
        return SourceSetIssueRecord(
            source_id=issue.source_id,
            status=SourceStatusValue(issue.status.value),
        )


def readiness_check_value(readiness: SourceSetReadiness) -> str:
    if readiness.ready:
        return "OK"
    if readiness.status is SourceFreshnessStatus.STALE:
        return "STALE"
    if readiness.status is SourceFreshnessStatus.QUARANTINED:
        return "QUARANTINED"
    return "UNAVAILABLE"
