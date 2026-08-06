"""Governed source registrations and derived runtime freshness."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SECRET_REF = re.compile(
    r"^(?:env|vault|secret-manager|file-secret):[A-Za-z0-9][A-Za-z0-9._/:-]{0,239}$"
)
MAX_SHORT_TEXT_LENGTH = 256
MAX_LONG_TEXT_LENGTH = 2000
MAX_FRESHNESS_SECONDS = 10 * 366 * 24 * 60 * 60
MAX_REQUIRED_SOURCES = 256
MAX_SOURCE_LISTING_RECORDS = 512


class SourceAccessMethod(StrEnum):
    PUBLIC_DOWNLOAD = "PUBLIC_DOWNLOAD"
    API = "API"
    SFTP = "SFTP"
    MANUAL_UPLOAD = "MANUAL_UPLOAD"
    INTERNAL = "INTERNAL"


class SourceAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    QUARANTINED = "QUARANTINED"


class SourceFreshnessStatus(StrEnum):
    CURRENT = "CURRENT"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"
    QUARANTINED = "QUARANTINED"


class ObservationWriteOutcome(StrEnum):
    APPLIED = "APPLIED"
    IDEMPOTENT = "IDEMPOTENT"


class ObservationConflict(Exception):
    """An older or equal-but-different runtime observation was rejected."""

    def __init__(self) -> None:
        super().__init__("source observation conflicts with current state")


class ActivationBlockReason(StrEnum):
    MISSING_NAME = "MISSING_NAME"
    MISSING_OWNER = "MISSING_OWNER"
    MISSING_RESPONSIBLE_OPERATOR = "MISSING_RESPONSIBLE_OPERATOR"
    MISSING_JURISDICTION = "MISSING_JURISDICTION"
    MISSING_LEGAL_SCOPE = "MISSING_LEGAL_SCOPE"
    MISSING_DATA_SCOPE = "MISSING_DATA_SCOPE"
    MISSING_ACCESS_METHOD = "MISSING_ACCESS_METHOD"
    MISSING_LICENCE_SUMMARY = "MISSING_LICENCE_SUMMARY"
    MISSING_REFRESH_EXPECTATION = "MISSING_REFRESH_EXPECTATION"
    MISSING_STALE_THRESHOLD = "MISSING_STALE_THRESHOLD"
    STALE_THRESHOLD_BEFORE_REFRESH = "STALE_THRESHOLD_BEFORE_REFRESH"


def _require_id(value: object, field: str) -> str:
    if not isinstance(value, str) or SAFE_ID.fullmatch(value) is None:
        raise ValueError(f"invalid {field}")
    return value


def _validate_optional_text(value: str | None, field: str, limit: int) -> None:
    if value is not None and len(value) > limit:
        raise ValueError(f"{field} exceeds {limit} characters")


def _missing(value: str | None) -> bool:
    return value is None or not value.strip()


@dataclass(frozen=True, slots=True)
class SourceRegistration:
    """Persistable governance record; incomplete drafts remain non-active."""

    deployment_id: str
    source_set_id: str
    source_id: str
    name: str | None = None
    owner: str | None = None
    responsible_operator: str | None = None
    jurisdiction: str | None = None
    legal_scope: str | None = None
    data_scope: str | None = None
    access_method: SourceAccessMethod | None = None
    credential_secret_ref: str | None = None
    licence_summary: str | None = None
    contractual_constraints: str | None = None
    refresh_expectation: timedelta | None = None
    stale_after: timedelta | None = None
    active: bool = False

    def __post_init__(self) -> None:
        _require_id(self.deployment_id, "deployment_id")
        _require_id(self.source_set_id, "source_set_id")
        _require_id(self.source_id, "source_id")
        for field in (
            "name",
            "owner",
            "responsible_operator",
            "jurisdiction",
        ):
            _validate_optional_text(getattr(self, field), field, MAX_SHORT_TEXT_LENGTH)
        for field in (
            "legal_scope",
            "data_scope",
            "licence_summary",
            "contractual_constraints",
        ):
            _validate_optional_text(getattr(self, field), field, MAX_LONG_TEXT_LENGTH)
        if (
            self.credential_secret_ref is not None
            and SECRET_REF.fullmatch(self.credential_secret_ref) is None
        ):
            raise ValueError("credential_secret_ref must be a provider reference")
        for field in ("refresh_expectation", "stale_after"):
            value = getattr(self, field)
            if value is not None:
                seconds = value.total_seconds()
                if (
                    seconds < 1
                    or seconds > MAX_FRESHNESS_SECONDS
                    or not seconds.is_integer()
                ):
                    raise ValueError(
                        f"{field} must be whole seconds between 1 and "
                        f"{MAX_FRESHNESS_SECONDS}"
                    )
        if self.active and self.activation_block_reasons():
            raise ValueError("active source must have complete governance")

    def activation_block_reasons(self) -> tuple[ActivationBlockReason, ...]:
        checks = (
            (self.name, ActivationBlockReason.MISSING_NAME),
            (self.owner, ActivationBlockReason.MISSING_OWNER),
            (
                self.responsible_operator,
                ActivationBlockReason.MISSING_RESPONSIBLE_OPERATOR,
            ),
            (self.jurisdiction, ActivationBlockReason.MISSING_JURISDICTION),
            (self.legal_scope, ActivationBlockReason.MISSING_LEGAL_SCOPE),
            (self.data_scope, ActivationBlockReason.MISSING_DATA_SCOPE),
            (
                self.licence_summary,
                ActivationBlockReason.MISSING_LICENCE_SUMMARY,
            ),
        )
        reasons = [reason for value, reason in checks if _missing(value)]
        if self.access_method is None:
            reasons.append(ActivationBlockReason.MISSING_ACCESS_METHOD)
        if self.refresh_expectation is None:
            reasons.append(ActivationBlockReason.MISSING_REFRESH_EXPECTATION)
        if self.stale_after is None:
            reasons.append(ActivationBlockReason.MISSING_STALE_THRESHOLD)
        if (
            self.refresh_expectation is not None
            and self.stale_after is not None
            and self.stale_after < self.refresh_expectation
        ):
            reasons.append(ActivationBlockReason.STALE_THRESHOLD_BEFORE_REFRESH)
        return tuple(reasons)

    def activated(self) -> SourceRegistration:
        reasons = self.activation_block_reasons()
        if reasons:
            raise SourceActivationRefused(reasons)
        return replace(self, active=True)


class SourceActivationRefused(Exception):
    """Activation failed with stable governance reason codes."""

    def __init__(self, reasons: tuple[ActivationBlockReason, ...]) -> None:
        super().__init__("source activation refused")
        self.reasons = reasons


@dataclass(frozen=True, slots=True)
class SourceSetManifest:
    """Persisted expected required membership, independent of source rows."""

    deployment_id: str
    source_set_id: str
    required_source_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_id(self.deployment_id, "deployment_id")
        _require_id(self.source_set_id, "source_set_id")
        for source_id in self.required_source_ids:
            _require_id(source_id, "required_source_id")
        if len(self.required_source_ids) > MAX_REQUIRED_SOURCES:
            raise ValueError(
                f"required source manifest exceeds {MAX_REQUIRED_SOURCES} members"
            )
        if tuple(sorted(set(self.required_source_ids))) != self.required_source_ids:
            raise ValueError("required source IDs must be sorted and unique")


@dataclass(frozen=True, slots=True)
class SourceRuntimeObservation:
    """Trusted active-snapshot pointer and source dependency observation."""

    deployment_id: str
    source_id: str
    availability: SourceAvailability
    observed_at: datetime
    active_snapshot_id: str | None = None
    retrieved_at: datetime | None = None
    effective_from: datetime | None = None

    def __post_init__(self) -> None:
        _require_id(self.deployment_id, "deployment_id")
        _require_id(self.source_id, "source_id")
        if self.active_snapshot_id is not None:
            _require_id(self.active_snapshot_id, "active_snapshot_id")
        for field in ("observed_at", "retrieved_at", "effective_from"):
            value = getattr(self, field)
            if value is not None and (
                value.tzinfo is None or value.utcoffset() is None
            ):
                raise ValueError(f"{field} must be timezone-aware")
        if self.retrieved_at is not None and self.retrieved_at > self.observed_at:
            raise ValueError("retrieved_at must not be after observed_at")


@dataclass(frozen=True, slots=True)
class SourceEvaluation:
    registration: SourceRegistration
    status: SourceFreshnessStatus
    observation: SourceRuntimeObservation | None


def evaluate_source(
    registration: SourceRegistration,
    observation: SourceRuntimeObservation | None,
    *,
    now: datetime,
) -> SourceEvaluation:
    """Derive status from current trusted facts; never persist CURRENT."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(UTC)
    if observation is not None and (
        observation.deployment_id != registration.deployment_id
        or observation.source_id != registration.source_id
    ):
        raise ValueError("source observation identity mismatch")
    if not registration.active:
        return SourceEvaluation(
            registration, SourceFreshnessStatus.QUARANTINED, observation
        )
    if observation is None:
        return SourceEvaluation(registration, SourceFreshnessStatus.UNAVAILABLE, None)
    if observation.observed_at.astimezone(UTC) > now:
        return SourceEvaluation(
            registration, SourceFreshnessStatus.UNAVAILABLE, observation
        )
    if observation.availability is SourceAvailability.QUARANTINED:
        return SourceEvaluation(
            registration, SourceFreshnessStatus.QUARANTINED, observation
        )
    if observation.availability is SourceAvailability.UNAVAILABLE:
        return SourceEvaluation(
            registration, SourceFreshnessStatus.UNAVAILABLE, observation
        )
    if (
        observation.active_snapshot_id is None
        or observation.retrieved_at is None
        or registration.stale_after is None
        or (
            observation.effective_from is not None
            and observation.effective_from.astimezone(UTC) > now
        )
    ):
        return SourceEvaluation(
            registration, SourceFreshnessStatus.UNAVAILABLE, observation
        )
    status = (
        SourceFreshnessStatus.STALE
        if now >= observation.retrieved_at.astimezone(UTC) + registration.stale_after
        else SourceFreshnessStatus.CURRENT
    )
    return SourceEvaluation(registration, status, observation)


@dataclass(frozen=True, slots=True)
class SourceSetReadiness:
    ready: bool
    status: SourceFreshnessStatus
    required_source_ids: tuple[str, ...]
    issues: tuple[SourceSetIssue, ...]


@dataclass(frozen=True, slots=True)
class SourceSetIssue:
    source_id: str
    status: SourceFreshnessStatus

    def __post_init__(self) -> None:
        _require_id(self.source_id, "source_id")


def evaluate_required_source_set(
    evaluations: tuple[SourceEvaluation, ...],
    expected_required_source_ids: tuple[str, ...],
) -> SourceSetReadiness:
    for source_id in expected_required_source_ids:
        _require_id(source_id, "expected_source_id")
    if tuple(sorted(set(expected_required_source_ids))) != expected_required_source_ids:
        raise ValueError("expected required source IDs must be sorted and unique")
    required_ids = tuple(sorted(set(expected_required_source_ids)))
    if not required_ids:
        return SourceSetReadiness(False, SourceFreshnessStatus.UNAVAILABLE, (), ())
    by_source_id = {item.registration.source_id: item for item in evaluations}
    issues = tuple(
        SourceSetIssue(
            source_id,
            (
                by_source_id[source_id].status
                if source_id in by_source_id
                else SourceFreshnessStatus.UNAVAILABLE
            ),
        )
        for source_id in required_ids
        if source_id not in by_source_id
        or by_source_id[source_id].status is not SourceFreshnessStatus.CURRENT
    )
    statuses = {issue.status for issue in issues}
    for status in (
        SourceFreshnessStatus.UNAVAILABLE,
        SourceFreshnessStatus.QUARANTINED,
        SourceFreshnessStatus.STALE,
    ):
        if status in statuses:
            return SourceSetReadiness(
                False,
                status,
                required_ids,
                issues,
            )
    return SourceSetReadiness(
        True,
        SourceFreshnessStatus.CURRENT,
        required_ids,
        (),
    )
