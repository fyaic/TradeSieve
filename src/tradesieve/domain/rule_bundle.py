"""Immutable, cited deterministic-rule and bundle lifecycle primitives."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SAFE_OPERATION = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
SEMANTIC_VERSION = re.compile(
    r"^(?:0|[1-9][0-9]{0,8})\."
    r"(?:0|[1-9][0-9]{0,8})\."
    r"(?:0|[1-9][0-9]{0,8})$"
)
CONTENT_HASH = re.compile(r"^sha256:[a-f0-9]{64}$")
MAX_SHORT_TEXT_LENGTH = 256
MAX_LONG_TEXT_LENGTH = 5000
MAX_RULES_PER_BUNDLE = 256
MAX_CITATIONS_PER_RULE = 16
MAX_TESTS_PER_RULE = 64
MAX_FACT_PATHS = 32
MAX_CANONICAL_CONTENT_BYTES = 1_048_576

type ContentHasher = Callable[[object], str]


class RuleAction(StrEnum):
    CUSTOMER_ONBOARDING = "CUSTOMER_ONBOARDING"
    QUOTE_RELEASE = "QUOTE_RELEASE"
    ORDER_ACCEPTANCE = "ORDER_ACCEPTANCE"
    BOOKING = "BOOKING"
    SHIPMENT_RELEASE = "SHIPMENT_RELEASE"
    PAYMENT = "PAYMENT"


class RuleActivity(StrEnum):
    SALE = "SALE"
    SUPPLY = "SUPPLY"
    EXPORT = "EXPORT"
    TRANSFER = "TRANSFER"
    BROKERING = "BROKERING"
    TECHNICAL_ASSISTANCE = "TECHNICAL_ASSISTANCE"
    FINANCING = "FINANCING"
    TRANSPORT = "TRANSPORT"
    TRANSIT = "TRANSIT"
    IMPORT = "IMPORT"
    OTHER = "OTHER"


class CanonicalFactPath(StrEnum):
    PROPOSED_ACTION = "proposed_action"
    LEGAL_NEXUS = "legal_nexus"
    ACTIVITIES = "activities"
    PARTIES = "parties"
    GOODS = "goods"
    ROUTE = "route"
    PAYMENT = "payment"


class RuleEvaluationOutcome(StrEnum):
    """Presence result only; no value represents legal clearance or permission."""

    FACTS_PRESENT = "FACTS_PRESENT"
    MISSING_FACTS = "MISSING_FACTS"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class CitationKind(StrEnum):
    INTERNAL_POLICY = "INTERNAL_POLICY"
    OFFICIAL_SOURCE_PROVISION = "OFFICIAL_SOURCE_PROVISION"


class RuleEvaluatorKind(StrEnum):
    LEGAL_NEXUS_PRESENCE = "LEGAL_NEXUS_PRESENCE"
    DATA_COMPLETENESS_PRESENCE = "DATA_COMPLETENESS_PRESENCE"


class RuleBundleState(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"
    ROLLED_BACK = "ROLLED_BACK"


class RuleBundleEventType(StrEnum):
    DRAFTED = "DRAFTED"
    APPROVED = "APPROVED"
    ACTIVATED = "ACTIVATED"
    RETIRED = "RETIRED"
    ROLLED_BACK = "ROLLED_BACK"


class LifecycleActorType(StrEnum):
    HUMAN = "HUMAN"
    SERVICE = "SERVICE"
    AGENT = "AGENT"


class DraftWriteOutcome(StrEnum):
    APPLIED = "APPLIED"
    IDEMPOTENT = "IDEMPOTENT"
    CONFLICT = "CONFLICT"


class LifecycleWriteOutcome(StrEnum):
    APPLIED = "APPLIED"
    IDEMPOTENT = "IDEMPOTENT"
    CONFLICT = "CONFLICT"


class RuleBundleCommandOutcome(StrEnum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


class RuleBundleCommandReason(StrEnum):
    DRAFT_APPLIED = "DRAFT_APPLIED"
    DRAFT_IDEMPOTENT = "DRAFT_IDEMPOTENT"
    APPROVED = "APPROVED"
    APPROVE_IDEMPOTENT = "APPROVE_IDEMPOTENT"
    ACTIVATED = "ACTIVATED"
    ACTIVATE_IDEMPOTENT = "ACTIVATE_IDEMPOTENT"
    RETIRED = "RETIRED"
    RETIRE_IDEMPOTENT = "RETIRE_IDEMPOTENT"
    ROLLED_BACK = "ROLLED_BACK"
    ROLLBACK_IDEMPOTENT = "ROLLBACK_IDEMPOTENT"
    DRAFT_CONFLICT = "DRAFT_CONFLICT"
    NOT_FOUND = "NOT_FOUND"
    AUTHORIZATION_BINDING_DENIED = "AUTHORIZATION_BINDING_DENIED"
    AUTHOR_SEPARATION_DENIED = "AUTHOR_SEPARATION_DENIED"
    GOVERNANCE_BLOCKED = "GOVERNANCE_BLOCKED"
    OFFICIAL_CITATION_UNVERIFIED = "OFFICIAL_CITATION_UNVERIFIED"
    INVALID_LIFECYCLE = "INVALID_LIFECYCLE"
    PERSISTENCE_FAILURE = "PERSISTENCE_FAILURE"


class GovernanceBlockReason(StrEnum):
    MISSING_BUNDLE_OWNER = "MISSING_BUNDLE_OWNER"
    EMPTY_BUNDLE = "EMPTY_BUNDLE"
    MISSING_RULE_OWNER = "MISSING_RULE_OWNER"
    MISSING_CITATION = "MISSING_CITATION"
    OFFICIAL_CITATION_UNVERIFIED = "OFFICIAL_CITATION_UNVERIFIED"
    MISSING_TEST = "MISSING_TEST"
    FIXTURE_FAILED = "FIXTURE_FAILED"
    BUNDLE_NOT_YET_EFFECTIVE = "BUNDLE_NOT_YET_EFFECTIVE"
    BUNDLE_EXPIRED = "BUNDLE_EXPIRED"
    RULE_NOT_YET_EFFECTIVE = "RULE_NOT_YET_EFFECTIVE"
    RULE_EXPIRED = "RULE_EXPIRED"


SUCCESS_COMMAND_REASONS = frozenset(
    {
        RuleBundleCommandReason.DRAFT_APPLIED,
        RuleBundleCommandReason.DRAFT_IDEMPOTENT,
        RuleBundleCommandReason.APPROVED,
        RuleBundleCommandReason.APPROVE_IDEMPOTENT,
        RuleBundleCommandReason.ACTIVATED,
        RuleBundleCommandReason.ACTIVATE_IDEMPOTENT,
        RuleBundleCommandReason.RETIRED,
        RuleBundleCommandReason.RETIRE_IDEMPOTENT,
        RuleBundleCommandReason.ROLLED_BACK,
        RuleBundleCommandReason.ROLLBACK_IDEMPOTENT,
    }
)


def _require_id(value: object, field: str) -> str:
    if not isinstance(value, str) or SAFE_ID.fullmatch(value) is None:
        raise ValueError(f"invalid {field}")
    return value


def _require_semver(value: object, field: str) -> str:
    if not isinstance(value, str) or SEMANTIC_VERSION.fullmatch(value) is None:
        raise ValueError(f"{field} must be a strict release semantic version")
    return value


def _require_text(
    value: object, field: str, *, maximum: int, optional: bool = False
) -> str | None:
    if value is None and optional:
        return None
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or len(value) > maximum
        or not value.isprintable()
    ):
        raise ValueError(f"{field} must be bounded non-blank printable text")
    return value


def _require_enum_tuple(
    values: object,
    enum_type: type[StrEnum],
    field: str,
    limit: int,
    *,
    allow_empty: bool = True,
) -> tuple[StrEnum, ...]:
    if not isinstance(values, tuple) or any(
        not isinstance(value, enum_type) for value in values
    ):
        raise ValueError(f"{field} must contain typed enum members")
    if not allow_empty and not values:
        raise ValueError(f"{field} must not be empty")
    if len(values) > limit:
        raise ValueError(f"{field} exceeds {limit} members")
    if tuple(sorted(set(values), key=str)) != values:
        raise ValueError(f"{field} must be sorted and unique")
    return values


def _require_hash(value: object, field: str) -> str:
    if not isinstance(value, str) or CONTENT_HASH.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical SHA-256 reference")
    return value


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _canonical_bytes(payload: object) -> bytes:
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("canonical content must be JSON serializable") from exc


def _require_canonical_size(payload: object, field: str) -> int:
    size = len(_canonical_bytes(payload))
    if size > MAX_CANONICAL_CONTENT_BYTES:
        raise ValueError(f"{field} exceeds {MAX_CANONICAL_CONTENT_BYTES} encoded bytes")
    return size


def canonical_sha256(payload: object) -> str:
    """Hash canonical UTF-8 JSON; callers never supply a persisted content hash."""

    encoded = _canonical_bytes(payload)
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _calculated_hash(hasher: ContentHasher, payload: object) -> str:
    value = hasher(payload)
    return _require_hash(value, "content hasher result")


@dataclass(frozen=True, slots=True)
class RuleBundleCommandAuditRecord:
    command_event_id: str
    authorization_event_id: str
    lifecycle_event_id: str | None
    tenant_id: str
    deployment_id: str
    rule_set_id: str
    bundle_id: str
    bundle_version: str
    bundle_content_hash: str
    target_type: str
    target_id: str
    actor_id: str
    actor_type: LifecycleActorType
    operation: str
    outcome: RuleBundleCommandOutcome
    reason: RuleBundleCommandReason
    occurred_at: datetime

    def __post_init__(self) -> None:
        for field in (
            "command_event_id",
            "authorization_event_id",
            "tenant_id",
            "deployment_id",
            "rule_set_id",
            "bundle_id",
            "target_type",
            "target_id",
            "actor_id",
        ):
            _require_id(getattr(self, field), field)
        if self.lifecycle_event_id is not None:
            _require_id(self.lifecycle_event_id, "lifecycle_event_id")
        _require_semver(self.bundle_version, "bundle version")
        _require_hash(self.bundle_content_hash, "bundle content hash")
        if not isinstance(self.actor_type, LifecycleActorType):
            raise ValueError("actor_type must be a typed lifecycle actor")
        if (
            not isinstance(self.operation, str)
            or SAFE_OPERATION.fullmatch(self.operation) is None
        ):
            raise ValueError("operation must be a bounded symbolic name")
        if not isinstance(self.outcome, RuleBundleCommandOutcome):
            raise ValueError("outcome must be a typed command outcome")
        if not isinstance(self.reason, RuleBundleCommandReason):
            raise ValueError("reason must be a typed command reason")
        if (self.reason in SUCCESS_COMMAND_REASONS) != (
            self.outcome is RuleBundleCommandOutcome.SUCCESS
        ):
            raise ValueError("command outcome must match its reason")
        reason_requires_lifecycle = self.reason in {
            RuleBundleCommandReason.DRAFT_APPLIED,
            RuleBundleCommandReason.APPROVED,
            RuleBundleCommandReason.ACTIVATED,
            RuleBundleCommandReason.RETIRED,
            RuleBundleCommandReason.ROLLED_BACK,
        }
        if reason_requires_lifecycle != (self.lifecycle_event_id is not None):
            raise ValueError("lifecycle event linkage must match command reason")
        if (
            not isinstance(self.occurred_at, datetime)
            or self.occurred_at.tzinfo is None
            or self.occurred_at.utcoffset() is None
        ):
            raise ValueError("occurred_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class EffectiveWindow:
    effective_from: datetime
    effective_until: datetime | None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.effective_from, datetime)
            or self.effective_from.tzinfo is None
            or self.effective_from.utcoffset() is None
        ):
            raise ValueError("effective_from must be timezone-aware")
        if self.effective_until is not None and (
            not isinstance(self.effective_until, datetime)
            or self.effective_until.tzinfo is None
            or self.effective_until.utcoffset() is None
        ):
            raise ValueError("effective_until must be timezone-aware")
        if (
            self.effective_until is not None
            and self.effective_until <= self.effective_from
        ):
            raise ValueError("effective_until must be after effective_from")

    def contains(self, moment: datetime) -> bool:
        if (
            not isinstance(moment, datetime)
            or moment.tzinfo is None
            or moment.utcoffset() is None
        ):
            raise ValueError("moment must be timezone-aware")
        normalized = moment.astimezone(UTC)
        return self.effective_from.astimezone(UTC) <= normalized and (
            self.effective_until is None
            or normalized < self.effective_until.astimezone(UTC)
        )


@dataclass(frozen=True, slots=True)
class RuleScope:
    proposed_actions: tuple[RuleAction, ...]
    activities: tuple[RuleActivity, ...] = ()

    def __post_init__(self) -> None:
        _require_enum_tuple(
            self.proposed_actions,
            RuleAction,
            "proposed_actions",
            6,
            allow_empty=False,
        )
        _require_enum_tuple(self.activities, RuleActivity, "activities", 11)


@dataclass(frozen=True, slots=True)
class InternalPolicyCitation:
    citation_ref: str
    policy_id: str
    policy_version: str
    provision_locator: str
    private_policy_text: str

    def __post_init__(self) -> None:
        _require_id(self.citation_ref, "citation_ref")
        _require_id(self.policy_id, "policy_id")
        _require_semver(self.policy_version, "policy_version")
        _require_text(
            self.provision_locator,
            "provision_locator",
            maximum=MAX_SHORT_TEXT_LENGTH,
        )
        _require_text(
            self.private_policy_text,
            "private_policy_text",
            maximum=MAX_LONG_TEXT_LENGTH,
        )

    @property
    def kind(self) -> CitationKind:
        return CitationKind.INTERNAL_POLICY

    def policy_content_hash(self, hasher: ContentHasher = canonical_sha256) -> str:
        return _calculated_hash(
            hasher,
            {
                "policy_id": self.policy_id,
                "policy_version": self.policy_version,
                "private_policy_text": self.private_policy_text,
                "provision_locator": self.provision_locator,
            },
        )


@dataclass(frozen=True, slots=True)
class OfficialSourceProvisionCitation:
    citation_ref: str
    source_id: str
    snapshot_id: str
    snapshot_content_hash: str
    provision_locator: str

    def __post_init__(self) -> None:
        _require_id(self.citation_ref, "citation_ref")
        _require_id(self.source_id, "source_id")
        _require_id(self.snapshot_id, "snapshot_id")
        _require_hash(self.snapshot_content_hash, "snapshot_content_hash")
        _require_text(
            self.provision_locator,
            "provision_locator",
            maximum=MAX_SHORT_TEXT_LENGTH,
        )

    @property
    def kind(self) -> CitationKind:
        return CitationKind.OFFICIAL_SOURCE_PROVISION


type RuleCitation = InternalPolicyCitation | OfficialSourceProvisionCitation


@dataclass(frozen=True, slots=True)
class LegalNexusPresenceSpec:
    @property
    def kind(self) -> RuleEvaluatorKind:
        return RuleEvaluatorKind.LEGAL_NEXUS_PRESENCE

    @property
    def required_fact_paths(self) -> tuple[CanonicalFactPath, ...]:
        return (CanonicalFactPath.LEGAL_NEXUS,)


@dataclass(frozen=True, slots=True)
class DataCompletenessPresenceSpec:
    required_fact_paths: tuple[CanonicalFactPath, ...]

    def __post_init__(self) -> None:
        _require_enum_tuple(
            self.required_fact_paths,
            CanonicalFactPath,
            "required_fact_paths",
            MAX_FACT_PATHS,
            allow_empty=False,
        )

    @property
    def kind(self) -> RuleEvaluatorKind:
        return RuleEvaluatorKind.DATA_COMPLETENESS_PRESENCE


type RuleEvaluatorSpec = LegalNexusPresenceSpec | DataCompletenessPresenceSpec


@dataclass(frozen=True, slots=True)
class RuleFixture:
    fixture_id: str
    proposed_action: RuleAction
    activities: tuple[RuleActivity, ...]
    present_fact_paths: tuple[CanonicalFactPath, ...]
    expected_outcome: RuleEvaluationOutcome
    expected_missing_fact_paths: tuple[CanonicalFactPath, ...] = ()

    def __post_init__(self) -> None:
        _require_id(self.fixture_id, "fixture_id")
        if not isinstance(self.proposed_action, RuleAction):
            raise ValueError("proposed_action must be a typed rule action")
        if not isinstance(self.expected_outcome, RuleEvaluationOutcome):
            raise ValueError("expected_outcome must be a typed evaluation outcome")
        _require_enum_tuple(self.activities, RuleActivity, "activities", 11)
        _require_enum_tuple(
            self.present_fact_paths,
            CanonicalFactPath,
            "present_fact_paths",
            MAX_FACT_PATHS,
        )
        _require_enum_tuple(
            self.expected_missing_fact_paths,
            CanonicalFactPath,
            "expected_missing_fact_paths",
            MAX_FACT_PATHS,
        )
        if (self.expected_outcome is RuleEvaluationOutcome.MISSING_FACTS) != bool(
            self.expected_missing_fact_paths
        ):
            raise ValueError(
                "only MISSING_FACTS fixtures may declare non-empty missing paths"
            )


@dataclass(frozen=True, slots=True)
class RuleVersion:
    rule_id: str
    version: str
    kind: RuleEvaluatorKind
    owner: str | None
    effective_window: EffectiveWindow
    scope: RuleScope
    evaluator: RuleEvaluatorSpec
    citations: tuple[RuleCitation, ...] = ()
    fixtures: tuple[RuleFixture, ...] = ()
    internal_notes: str | None = None

    def __post_init__(self) -> None:
        _require_id(self.rule_id, "rule_id")
        _require_semver(self.version, "rule version")
        if not isinstance(self.kind, RuleEvaluatorKind):
            raise ValueError("kind must be a typed rule evaluator kind")
        if not isinstance(self.effective_window, EffectiveWindow):
            raise ValueError("effective_window must be typed")
        if not isinstance(self.scope, RuleScope):
            raise ValueError("scope must be typed")
        if not isinstance(
            self.evaluator, (LegalNexusPresenceSpec, DataCompletenessPresenceSpec)
        ):
            raise ValueError("evaluator must be a finite typed specification")
        _require_text(
            self.owner,
            "rule owner",
            maximum=MAX_SHORT_TEXT_LENGTH,
            optional=True,
        )
        _require_text(
            self.internal_notes,
            "internal_notes",
            maximum=MAX_LONG_TEXT_LENGTH,
            optional=True,
        )
        if self.kind is not self.evaluator.kind:
            raise ValueError("rule kind must match the finite evaluator spec")
        if not isinstance(self.citations, tuple) or any(
            not isinstance(
                citation,
                (InternalPolicyCitation, OfficialSourceProvisionCitation),
            )
            for citation in self.citations
        ):
            raise ValueError("citations must contain typed citation values")
        if len(self.citations) > MAX_CITATIONS_PER_RULE:
            raise ValueError(f"citations exceeds {MAX_CITATIONS_PER_RULE} members")
        citation_refs = tuple(item.citation_ref for item in self.citations)
        if tuple(sorted(set(citation_refs))) != citation_refs:
            raise ValueError("citations must be sorted by unique citation_ref")
        if not isinstance(self.fixtures, tuple) or any(
            not isinstance(fixture, RuleFixture) for fixture in self.fixtures
        ):
            raise ValueError("fixtures must contain typed rule fixtures")
        if len(self.fixtures) > MAX_TESTS_PER_RULE:
            raise ValueError(f"fixtures exceeds {MAX_TESTS_PER_RULE} members")
        fixture_ids = tuple(item.fixture_id for item in self.fixtures)
        if tuple(sorted(set(fixture_ids))) != fixture_ids:
            raise ValueError("fixtures must be sorted by unique fixture_id")

    def canonical_content(self, hasher: ContentHasher = canonical_sha256) -> object:
        return {
            "citations": [_citation_content(item, hasher) for item in self.citations],
            "effective_window": _window_content(self.effective_window),
            "evaluator": _evaluator_content(self.evaluator),
            "fixtures": [_fixture_content(item) for item in self.fixtures],
            "internal_notes": self.internal_notes,
            "kind": self.kind.value,
            "owner": self.owner,
            "rule_id": self.rule_id,
            "scope": _scope_content(self.scope),
            "version": self.version,
        }

    def content_hash(self, hasher: ContentHasher = canonical_sha256) -> str:
        payload = self.canonical_content(hasher)
        _require_canonical_size(payload, "rule canonical content")
        return _calculated_hash(hasher, payload)


@dataclass(frozen=True, slots=True)
class RuleBundleVersion:
    tenant_id: str
    deployment_id: str
    rule_set_id: str
    bundle_id: str
    version: str
    owner: str | None
    effective_window: EffectiveWindow
    authored_by: str
    rules: tuple[RuleVersion, ...]
    internal_notes: str | None = None

    def __post_init__(self) -> None:
        for field in (
            "tenant_id",
            "deployment_id",
            "rule_set_id",
            "bundle_id",
            "authored_by",
        ):
            _require_id(getattr(self, field), field)
        _require_semver(self.version, "bundle version")
        if not isinstance(self.effective_window, EffectiveWindow):
            raise ValueError("effective_window must be typed")
        _require_text(
            self.owner,
            "bundle owner",
            maximum=MAX_SHORT_TEXT_LENGTH,
            optional=True,
        )
        _require_text(
            self.internal_notes,
            "internal_notes",
            maximum=MAX_LONG_TEXT_LENGTH,
            optional=True,
        )
        if not isinstance(self.rules, tuple) or any(
            not isinstance(rule, RuleVersion) for rule in self.rules
        ):
            raise ValueError("rules must contain typed rule versions")
        if len(self.rules) > MAX_RULES_PER_BUNDLE:
            raise ValueError(f"rules exceeds {MAX_RULES_PER_BUNDLE} members")
        rule_ids = tuple(item.rule_id for item in self.rules)
        if tuple(sorted(set(rule_ids))) != rule_ids:
            raise ValueError("rules must be sorted by unique rule_id")

    def canonical_content(self, hasher: ContentHasher = canonical_sha256) -> object:
        return {
            "authored_by": self.authored_by,
            "bundle_id": self.bundle_id,
            "deployment_id": self.deployment_id,
            "effective_window": _window_content(self.effective_window),
            "internal_notes": self.internal_notes,
            "owner": self.owner,
            "rule_set_id": self.rule_set_id,
            "rules": [
                {
                    "content_hash": item.content_hash(hasher),
                    "rule_id": item.rule_id,
                    "version": item.version,
                }
                for item in self.rules
            ],
            "tenant_id": self.tenant_id,
            "version": self.version,
        }

    def content_hash(self, hasher: ContentHasher = canonical_sha256) -> str:
        rule_payloads = [rule.canonical_content(hasher) for rule in self.rules]
        payload = self.canonical_content(hasher)
        aggregate_size = len(_canonical_bytes(payload)) + sum(
            len(_canonical_bytes(item)) for item in rule_payloads
        )
        if aggregate_size > MAX_CANONICAL_CONTENT_BYTES:
            raise ValueError(
                f"bundle aggregate exceeds {MAX_CANONICAL_CONTENT_BYTES} encoded bytes"
            )
        return _calculated_hash(hasher, payload)


def _window_content(window: EffectiveWindow) -> object:
    return {
        "effective_from": _utc_text(window.effective_from),
        "effective_until": (
            _utc_text(window.effective_until)
            if window.effective_until is not None
            else None
        ),
    }


def _scope_content(scope: RuleScope) -> object:
    return {
        "activities": [item.value for item in scope.activities],
        "proposed_actions": [item.value for item in scope.proposed_actions],
    }


def _citation_content(citation: RuleCitation, hasher: ContentHasher) -> object:
    if isinstance(citation, InternalPolicyCitation):
        return {
            "citation_ref": citation.citation_ref,
            "kind": citation.kind.value,
            "policy_content_hash": citation.policy_content_hash(hasher),
            "policy_id": citation.policy_id,
            "policy_version": citation.policy_version,
            "private_policy_text": citation.private_policy_text,
            "provision_locator": citation.provision_locator,
        }
    return {
        "citation_ref": citation.citation_ref,
        "kind": citation.kind.value,
        "provision_locator": citation.provision_locator,
        "snapshot_content_hash": citation.snapshot_content_hash,
        "snapshot_id": citation.snapshot_id,
        "source_id": citation.source_id,
    }


def _evaluator_content(evaluator: RuleEvaluatorSpec) -> object:
    return {
        "kind": evaluator.kind.value,
        "required_fact_paths": [item.value for item in evaluator.required_fact_paths],
    }


def _fixture_content(fixture: RuleFixture) -> object:
    return {
        "activities": [item.value for item in fixture.activities],
        "expected_missing_fact_paths": [
            item.value for item in fixture.expected_missing_fact_paths
        ],
        "expected_outcome": fixture.expected_outcome.value,
        "fixture_id": fixture.fixture_id,
        "present_fact_paths": [item.value for item in fixture.present_fact_paths],
        "proposed_action": fixture.proposed_action.value,
    }


@dataclass(frozen=True, slots=True)
class RulePresenceEvaluation:
    outcome: RuleEvaluationOutcome
    missing_fact_paths: tuple[CanonicalFactPath, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, RuleEvaluationOutcome):
            raise ValueError("outcome must be a typed evaluation outcome")
        _require_enum_tuple(
            self.missing_fact_paths,
            CanonicalFactPath,
            "missing_fact_paths",
            MAX_FACT_PATHS,
        )
        if (self.outcome is RuleEvaluationOutcome.MISSING_FACTS) != bool(
            self.missing_fact_paths
        ):
            raise ValueError(
                "only MISSING_FACTS evaluations may contain missing fact paths"
            )


def evaluate_rule_presence(
    rule: RuleVersion,
    *,
    proposed_action: RuleAction,
    activities: tuple[RuleActivity, ...],
    present_fact_paths: tuple[CanonicalFactPath, ...],
) -> RulePresenceEvaluation:
    if not isinstance(rule, RuleVersion):
        raise ValueError("rule must be a typed rule version")
    if not isinstance(proposed_action, RuleAction):
        raise ValueError("proposed_action must be a typed rule action")
    _require_enum_tuple(activities, RuleActivity, "activities", 11)
    _require_enum_tuple(
        present_fact_paths,
        CanonicalFactPath,
        "present_fact_paths",
        MAX_FACT_PATHS,
    )
    if proposed_action not in rule.scope.proposed_actions:
        return RulePresenceEvaluation(RuleEvaluationOutcome.NOT_APPLICABLE, ())
    if (
        rule.scope.activities
        and activities
        and not set(rule.scope.activities).intersection(activities)
    ):
        return RulePresenceEvaluation(RuleEvaluationOutcome.NOT_APPLICABLE, ())
    effective_present = set(present_fact_paths)
    effective_present.add(CanonicalFactPath.PROPOSED_ACTION)
    if activities:
        effective_present.add(CanonicalFactPath.ACTIVITIES)
    else:
        effective_present.discard(CanonicalFactPath.ACTIVITIES)
    required = set(rule.evaluator.required_fact_paths)
    if rule.scope.activities and not activities:
        required.add(CanonicalFactPath.ACTIVITIES)
    missing = tuple(
        sorted(
            (item for item in required if item not in effective_present),
            key=str,
        )
    )
    if missing:
        return RulePresenceEvaluation(RuleEvaluationOutcome.MISSING_FACTS, missing)
    return RulePresenceEvaluation(RuleEvaluationOutcome.FACTS_PRESENT, ())


def failed_rule_fixtures(rule: RuleVersion) -> tuple[str, ...]:
    return tuple(
        fixture.fixture_id
        for fixture in rule.fixtures
        if evaluate_rule_presence(
            rule,
            proposed_action=fixture.proposed_action,
            activities=fixture.activities,
            present_fact_paths=fixture.present_fact_paths,
        )
        != RulePresenceEvaluation(
            fixture.expected_outcome,
            fixture.expected_missing_fact_paths,
        )
    )


@dataclass(frozen=True, slots=True)
class GovernanceIssue:
    reason: GovernanceBlockReason
    rule_id: str | None = None
    failing_fixture_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.reason, GovernanceBlockReason):
            raise ValueError("reason must be a typed governance block reason")
        bundle_reasons = {
            GovernanceBlockReason.MISSING_BUNDLE_OWNER,
            GovernanceBlockReason.EMPTY_BUNDLE,
            GovernanceBlockReason.BUNDLE_NOT_YET_EFFECTIVE,
            GovernanceBlockReason.BUNDLE_EXPIRED,
        }
        fixture_reason = self.reason is GovernanceBlockReason.FIXTURE_FAILED
        if (self.reason in bundle_reasons) != (self.rule_id is None):
            raise ValueError("governance reason has an invalid rule_id shape")
        if self.rule_id is not None:
            _require_id(self.rule_id, "rule_id")
        if not isinstance(self.failing_fixture_ids, tuple):
            raise ValueError("failing_fixture_ids must be a tuple")
        for fixture_id in self.failing_fixture_ids:
            _require_id(fixture_id, "failing_fixture_id")
        if tuple(sorted(set(self.failing_fixture_ids))) != self.failing_fixture_ids:
            raise ValueError("failing_fixture_ids must be sorted and unique")
        if fixture_reason != bool(self.failing_fixture_ids):
            raise ValueError("only FIXTURE_FAILED issues may name failing fixture IDs")


def _sorted_issues(issues: list[GovernanceIssue]) -> tuple[GovernanceIssue, ...]:
    return tuple(
        sorted(
            issues,
            key=lambda item: (
                item.reason.value,
                item.rule_id or "",
                item.failing_fixture_ids,
            ),
        )
    )


def approval_block_reasons(bundle: RuleBundleVersion) -> tuple[GovernanceIssue, ...]:
    if not isinstance(bundle, RuleBundleVersion):
        raise ValueError("bundle must be a typed rule bundle version")
    issues: list[GovernanceIssue] = []
    if bundle.owner is None:
        issues.append(GovernanceIssue(GovernanceBlockReason.MISSING_BUNDLE_OWNER))
    if not bundle.rules:
        issues.append(GovernanceIssue(GovernanceBlockReason.EMPTY_BUNDLE))
    for rule in bundle.rules:
        if rule.owner is None:
            issues.append(
                GovernanceIssue(GovernanceBlockReason.MISSING_RULE_OWNER, rule.rule_id)
            )
        if not rule.citations:
            issues.append(
                GovernanceIssue(GovernanceBlockReason.MISSING_CITATION, rule.rule_id)
            )
        if not rule.fixtures:
            issues.append(
                GovernanceIssue(GovernanceBlockReason.MISSING_TEST, rule.rule_id)
            )
        elif failed := failed_rule_fixtures(rule):
            issues.append(
                GovernanceIssue(
                    GovernanceBlockReason.FIXTURE_FAILED,
                    rule.rule_id,
                    failed,
                )
            )
    return _sorted_issues(issues)


def activation_block_reasons(
    bundle: RuleBundleVersion, *, now: datetime
) -> tuple[GovernanceIssue, ...]:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    issues = list(approval_block_reasons(bundle))
    normalized = now.astimezone(UTC)
    if normalized < bundle.effective_window.effective_from.astimezone(UTC):
        issues.append(GovernanceIssue(GovernanceBlockReason.BUNDLE_NOT_YET_EFFECTIVE))
    elif (
        bundle.effective_window.effective_until is not None
        and normalized >= bundle.effective_window.effective_until.astimezone(UTC)
    ):
        issues.append(GovernanceIssue(GovernanceBlockReason.BUNDLE_EXPIRED))
    for rule in bundle.rules:
        if normalized < rule.effective_window.effective_from.astimezone(UTC):
            issues.append(
                GovernanceIssue(
                    GovernanceBlockReason.RULE_NOT_YET_EFFECTIVE,
                    rule.rule_id,
                )
            )
        elif (
            rule.effective_window.effective_until is not None
            and normalized >= rule.effective_window.effective_until.astimezone(UTC)
        ):
            issues.append(
                GovernanceIssue(GovernanceBlockReason.RULE_EXPIRED, rule.rule_id)
            )
    return _sorted_issues(issues)


@dataclass(frozen=True, slots=True, order=True)
class RuleBundleRef:
    bundle_id: str
    version: str
    content_hash: str

    def __post_init__(self) -> None:
        _require_id(self.bundle_id, "bundle_id")
        _require_semver(self.version, "bundle version")
        _require_hash(self.content_hash, "bundle content_hash")


def rule_bundle_ref(
    bundle: RuleBundleVersion, *, hasher: ContentHasher = canonical_sha256
) -> RuleBundleRef:
    if not isinstance(bundle, RuleBundleVersion):
        raise ValueError("bundle must be a typed rule bundle version")
    return RuleBundleRef(bundle.bundle_id, bundle.version, bundle.content_hash(hasher))


@dataclass(frozen=True, slots=True)
class RescreenImpact:
    previous_bundle: RuleBundleRef | None
    new_bundle: RuleBundleRef | None
    added_rule_ids: tuple[str, ...]
    removed_rule_ids: tuple[str, ...]
    changed_rule_ids: tuple[str, ...]
    affected_fact_paths: tuple[CanonicalFactPath, ...]
    rescreen_required: bool

    def __post_init__(self) -> None:
        if self.previous_bundle is None and self.new_bundle is None:
            raise ValueError("impact requires a previous or new bundle")
        for field in ("previous_bundle", "new_bundle"):
            value = getattr(self, field)
            if value is not None and not isinstance(value, RuleBundleRef):
                raise ValueError(f"{field} must be a typed bundle reference")
        changed_sets: list[set[str]] = []
        for field in ("added_rule_ids", "removed_rule_ids", "changed_rule_ids"):
            values = getattr(self, field)
            if not isinstance(values, tuple):
                raise ValueError(f"{field} must be a tuple")
            for value in values:
                _require_id(value, field)
            if tuple(sorted(set(values))) != values:
                raise ValueError(f"{field} must be sorted and unique")
            changed_sets.append(set(values))
        if any(
            left.intersection(right)
            for index, left in enumerate(changed_sets)
            for right in changed_sets[index + 1 :]
        ):
            raise ValueError("added, removed, and changed rule IDs must be disjoint")
        _require_enum_tuple(
            self.affected_fact_paths,
            CanonicalFactPath,
            "affected_fact_paths",
            MAX_FACT_PATHS,
        )
        has_rule_delta = bool(set().union(*changed_sets))
        if (
            self.rescreen_required is not has_rule_delta
            or bool(self.affected_fact_paths) is not has_rule_delta
        ):
            raise ValueError(
                "affected paths and rescreen_required must match the rule delta"
            )
        if self.previous_bundle is None and (
            self.removed_rule_ids or self.changed_rule_ids
        ):
            raise ValueError("initial impact cannot remove or change rules")
        if self.new_bundle is None and (self.added_rule_ids or self.changed_rule_ids):
            raise ValueError("retirement impact cannot add or change rules")


def calculate_rescreen_impact(
    previous: RuleBundleVersion | None,
    new: RuleBundleVersion | None,
    *,
    hasher: ContentHasher = canonical_sha256,
) -> RescreenImpact:
    if previous is None and new is None:
        raise ValueError("impact requires a previous or new bundle")
    for field, value in (("previous", previous), ("new", new)):
        if value is not None and not isinstance(value, RuleBundleVersion):
            raise ValueError(f"{field} must be a typed rule bundle version")
    previous_rules = {item.rule_id: item for item in previous.rules} if previous else {}
    new_rules = {item.rule_id: item for item in new.rules} if new else {}
    added = tuple(sorted(new_rules.keys() - previous_rules.keys()))
    removed = tuple(sorted(previous_rules.keys() - new_rules.keys()))
    window_changed = (
        previous is not None
        and new is not None
        and previous.effective_window != new.effective_window
    )
    changed = tuple(
        sorted(
            rule_id
            for rule_id in previous_rules.keys() & new_rules.keys()
            if window_changed
            or previous_rules[rule_id].content_hash(hasher)
            != new_rules[rule_id].content_hash(hasher)
        )
    )
    affected_ids = set((*added, *removed, *changed))
    affected_paths = tuple(
        sorted(
            {
                path
                for bundle in (previous, new)
                if bundle is not None
                for rule in bundle.rules
                if rule.rule_id in affected_ids
                for path in (
                    CanonicalFactPath.PROPOSED_ACTION,
                    *((CanonicalFactPath.ACTIVITIES,) if rule.scope.activities else ()),
                    *rule.evaluator.required_fact_paths,
                )
            },
            key=str,
        )
    )
    return RescreenImpact(
        previous_bundle=(
            rule_bundle_ref(previous, hasher=hasher) if previous is not None else None
        ),
        new_bundle=rule_bundle_ref(new, hasher=hasher) if new is not None else None,
        added_rule_ids=added,
        removed_rule_ids=removed,
        changed_rule_ids=changed,
        affected_fact_paths=affected_paths,
        rescreen_required=bool(affected_ids),
    )


@dataclass(frozen=True, slots=True)
class RuleBundleLifecycleEvent:
    sequence: int
    event_id: str
    tenant_id: str
    deployment_id: str
    rule_set_id: str
    event_type: RuleBundleEventType
    previous_bundle: RuleBundleRef | None
    new_bundle: RuleBundleRef | None
    reason: str
    actor_id: str
    actor_type: LifecycleActorType
    occurred_at: datetime
    rescreen_impact: RescreenImpact | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 1
            or self.sequence > 9_223_372_036_854_775_807
        ):
            raise ValueError("sequence must be a positive signed BIGINT")
        for field in (
            "event_id",
            "tenant_id",
            "deployment_id",
            "rule_set_id",
            "actor_id",
        ):
            _require_id(getattr(self, field), field)
        if not isinstance(self.event_type, RuleBundleEventType):
            raise ValueError("event_type must be a typed lifecycle event")
        if not isinstance(self.actor_type, LifecycleActorType):
            raise ValueError("actor_type must be a typed lifecycle actor")
        for field in ("previous_bundle", "new_bundle"):
            value = getattr(self, field)
            if value is not None and not isinstance(value, RuleBundleRef):
                raise ValueError(f"{field} must be a typed bundle reference")
        _require_text(self.reason, "reason", maximum=MAX_LONG_TEXT_LENGTH)
        if (
            not isinstance(self.occurred_at, datetime)
            or self.occurred_at.tzinfo is None
            or self.occurred_at.utcoffset() is None
        ):
            raise ValueError("occurred_at must be timezone-aware")
        allowed_actor_types = (
            {LifecycleActorType.HUMAN, LifecycleActorType.SERVICE}
            if self.event_type is RuleBundleEventType.DRAFTED
            else {LifecycleActorType.HUMAN}
        )
        if self.actor_type not in allowed_actor_types:
            raise ValueError("actor type cannot perform this lifecycle event")
        transition = (self.previous_bundle, self.new_bundle)
        expected = {
            RuleBundleEventType.DRAFTED: (False, True),
            RuleBundleEventType.APPROVED: (False, True),
            RuleBundleEventType.ACTIVATED: (None, True),
            RuleBundleEventType.RETIRED: (True, False),
            RuleBundleEventType.ROLLED_BACK: (True, True),
        }[self.event_type]
        if expected[0] is not None and bool(transition[0]) is not expected[0]:
            raise ValueError("invalid previous bundle for lifecycle event")
        if bool(transition[1]) is not expected[1]:
            raise ValueError("invalid new bundle for lifecycle event")
        requires_impact = self.event_type in {
            RuleBundleEventType.ACTIVATED,
            RuleBundleEventType.RETIRED,
            RuleBundleEventType.ROLLED_BACK,
        }
        if requires_impact != (self.rescreen_impact is not None):
            raise ValueError(
                "activation, retirement, and rollback events require rescreen impact"
            )
        if self.rescreen_impact is not None and (
            self.rescreen_impact.previous_bundle != self.previous_bundle
            or self.rescreen_impact.new_bundle != self.new_bundle
        ):
            raise ValueError("event and rescreen impact bundle references must match")


@dataclass(frozen=True, slots=True)
class RuleBundleStateRecord:
    bundle: RuleBundleRef
    state: RuleBundleState


@dataclass(frozen=True, slots=True)
class RuleSetLifecycle:
    states: tuple[RuleBundleStateRecord, ...]
    active_bundle: RuleBundleRef | None

    def state_for(self, bundle: RuleBundleRef) -> RuleBundleState | None:
        return next(
            (item.state for item in self.states if item.bundle == bundle),
            None,
        )


class InvalidLifecycleTransition(Exception):
    def __init__(self) -> None:
        super().__init__("invalid rule bundle lifecycle transition")


def fold_rule_bundle_events(
    events: tuple[RuleBundleLifecycleEvent, ...],
) -> RuleSetLifecycle:
    if not isinstance(events, tuple) or any(
        not isinstance(event, RuleBundleLifecycleEvent) for event in events
    ):
        raise ValueError("events must contain typed lifecycle events")
    states: dict[RuleBundleRef, RuleBundleState] = {}
    activated: set[RuleBundleRef] = set()
    active: RuleBundleRef | None = None
    identity: tuple[str, str, str] | None = None
    previous_sequence = 0
    event_ids: set[str] = set()
    version_hashes: dict[tuple[str, str], str] = {}
    for event in events:
        event_identity = (event.tenant_id, event.deployment_id, event.rule_set_id)
        if identity is None:
            identity = event_identity
        if (
            event_identity != identity
            or event.sequence <= previous_sequence
            or event.event_id in event_ids
        ):
            raise InvalidLifecycleTransition
        previous_sequence = event.sequence
        event_ids.add(event.event_id)
        previous = event.previous_bundle
        new = event.new_bundle
        for reference in (previous, new):
            if reference is None:
                continue
            version_identity = (reference.bundle_id, reference.version)
            known_hash = version_hashes.setdefault(
                version_identity, reference.content_hash
            )
            if known_hash != reference.content_hash:
                raise InvalidLifecycleTransition
        if event.event_type is RuleBundleEventType.DRAFTED:
            assert new is not None
            if new in states:
                raise InvalidLifecycleTransition
            states[new] = RuleBundleState.DRAFT
        elif event.event_type is RuleBundleEventType.APPROVED:
            assert new is not None
            if states.get(new) is not RuleBundleState.DRAFT:
                raise InvalidLifecycleTransition
            states[new] = RuleBundleState.APPROVED
        elif event.event_type is RuleBundleEventType.ACTIVATED:
            assert new is not None
            if states.get(new) is not RuleBundleState.APPROVED or previous != active:
                raise InvalidLifecycleTransition
            if previous is not None:
                states[previous] = RuleBundleState.RETIRED
            states[new] = RuleBundleState.ACTIVE
            active = new
            activated.add(new)
        elif event.event_type is RuleBundleEventType.RETIRED:
            if previous != active or previous is None:
                raise InvalidLifecycleTransition
            states[previous] = RuleBundleState.RETIRED
            active = None
        else:
            assert event.event_type is RuleBundleEventType.ROLLED_BACK
            assert previous is not None and new is not None
            if (
                previous != active
                or new == previous
                or new not in activated
                or states.get(new)
                not in {RuleBundleState.RETIRED, RuleBundleState.ROLLED_BACK}
            ):
                raise InvalidLifecycleTransition
            states[previous] = RuleBundleState.ROLLED_BACK
            states[new] = RuleBundleState.ACTIVE
            active = new
            activated.add(new)
    return RuleSetLifecycle(
        tuple(
            RuleBundleStateRecord(bundle, states[bundle]) for bundle in sorted(states)
        ),
        active,
    )


def semantically_applied_lifecycle_transition(
    attempted: RuleBundleLifecycleEvent,
    lifecycle: RuleSetLifecycle,
    current_events: tuple[RuleBundleLifecycleEvent, ...],
) -> RuleBundleLifecycleEvent | None:
    """Return the stored transition responsible for an identical state intent."""

    if not isinstance(attempted, RuleBundleLifecycleEvent):
        raise ValueError("attempted lifecycle event must be typed")
    if not isinstance(lifecycle, RuleSetLifecycle):
        raise ValueError("lifecycle must be typed")
    if not isinstance(current_events, tuple) or any(
        not isinstance(event, RuleBundleLifecycleEvent) for event in current_events
    ):
        raise ValueError("current events must contain typed lifecycle events")
    if attempted.event_type is RuleBundleEventType.DRAFTED:
        raise ValueError("draft idempotence is defined by immutable bundle identity")

    if attempted.event_type is RuleBundleEventType.APPROVED:
        assert attempted.new_bundle is not None
        if lifecycle.state_for(attempted.new_bundle) is not RuleBundleState.APPROVED:
            return None
        candidates = (
            stored
            for stored in current_events
            if stored.event_type is RuleBundleEventType.APPROVED
            and stored.new_bundle == attempted.new_bundle
        )
    elif attempted.event_type is RuleBundleEventType.ACTIVATED:
        if lifecycle.active_bundle != attempted.new_bundle:
            return None
        candidates = (
            stored
            for stored in current_events
            if stored.event_type
            in {RuleBundleEventType.ACTIVATED, RuleBundleEventType.ROLLED_BACK}
            and stored.new_bundle == attempted.new_bundle
        )
    elif attempted.event_type is RuleBundleEventType.RETIRED:
        assert attempted.previous_bundle is not None
        if not (
            lifecycle.active_bundle is None
            and lifecycle.state_for(attempted.previous_bundle)
            is RuleBundleState.RETIRED
        ):
            return None
        candidates = (
            stored
            for stored in current_events
            if stored.event_type is RuleBundleEventType.RETIRED
            and stored.previous_bundle == attempted.previous_bundle
        )
    else:
        assert attempted.event_type is RuleBundleEventType.ROLLED_BACK
        assert attempted.previous_bundle is not None
        if not (
            lifecycle.active_bundle == attempted.new_bundle
            and lifecycle.state_for(attempted.previous_bundle)
            is RuleBundleState.ROLLED_BACK
        ):
            return None
        candidates = (
            stored
            for stored in current_events
            if stored.event_type is RuleBundleEventType.ROLLED_BACK
            and stored.new_bundle == attempted.new_bundle
        )
    responsible = next(iter(reversed(tuple(candidates))), None)
    if responsible is None:
        return None
    if (
        responsible.event_type is attempted.event_type
        and responsible.previous_bundle == attempted.previous_bundle
        and responsible.new_bundle == attempted.new_bundle
        and responsible.rescreen_impact == attempted.rescreen_impact
        and responsible.reason == attempted.reason
    ):
        return responsible
    return None


def validate_atomic_command_audit(
    audit: RuleBundleCommandAuditRecord,
    scope: tuple[str, str, str],
    reference: RuleBundleRef,
    reason: RuleBundleCommandReason,
    lifecycle_event: RuleBundleLifecycleEvent | None,
) -> None:
    """Validate the command audit attached to one atomic persistence mutation."""

    if not isinstance(audit, RuleBundleCommandAuditRecord):
        raise ValueError("command audit must be typed")
    if (
        not isinstance(scope, tuple)
        or len(scope) != 3
        or any(not isinstance(item, str) for item in scope)
    ):
        raise ValueError("command audit scope must be a typed identity tuple")
    if not isinstance(reference, RuleBundleRef):
        raise ValueError("command audit reference must be typed")
    if not isinstance(reason, RuleBundleCommandReason):
        raise ValueError("command audit reason must be typed")
    if lifecycle_event is not None and not isinstance(
        lifecycle_event, RuleBundleLifecycleEvent
    ):
        raise ValueError("lifecycle event must be typed")
    lifecycle_event_id = (
        lifecycle_event.event_id if lifecycle_event is not None else None
    )
    if (
        (audit.tenant_id, audit.deployment_id, audit.rule_set_id) != scope
        or (audit.bundle_id, audit.bundle_version, audit.bundle_content_hash)
        != (reference.bundle_id, reference.version, reference.content_hash)
        or audit.reason is not reason
        or audit.lifecycle_event_id != lifecycle_event_id
        or (
            lifecycle_event is not None
            and (
                audit.actor_id != lifecycle_event.actor_id
                or audit.actor_type is not lifecycle_event.actor_type
                or audit.occurred_at != lifecycle_event.occurred_at
            )
        )
    ):
        raise ValueError("command audit does not match the atomic write")
