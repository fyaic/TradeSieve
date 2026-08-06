"""Canonical typed application contracts shared by every external adapter.

These models validate transport shape and referential integrity. They deliberately do
not treat absent business facts as a transport error: completeness evaluation belongs
to the application controls and must produce explicit findings rather than an implicit
green result.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    WithJsonSchema,
    model_validator,
)

SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"

ShortText = Annotated[str, StringConstraints(min_length=1, max_length=256)]
LongText = Annotated[str, StringConstraints(min_length=1, max_length=5000)]
Reference = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
CountryCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]
CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
ContentHash = Annotated[
    str,
    StringConstraints(pattern=r"^sha256:[a-f0-9]{64}$"),
]
PositiveQuantity = Annotated[
    Decimal,
    Field(gt=0, max_digits=24, decimal_places=6),
]
Percentage = Annotated[
    Decimal,
    Field(ge=0, le=100, max_digits=7, decimal_places=4),
]
Confidence = Annotated[
    Decimal,
    Field(ge=0, le=1, max_digits=6, decimal_places=5),
]

MONEY_PATTERN = r"^(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,6})?$"


def _require_money_string(value: object) -> object:
    if not isinstance(value, str) or re.fullmatch(MONEY_PATTERN, value) is None:
        raise ValueError("money amounts must be canonical decimal strings")
    return value


MoneyAmount = Annotated[
    Decimal,
    BeforeValidator(_require_money_string),
    Field(gt=0, max_digits=24, decimal_places=6),
    WithJsonSchema({"type": "string", "pattern": MONEY_PATTERN}),
]


class ContractModel(BaseModel):
    """Strict base configuration for every canonical contract object."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_default=True,
    )


class DataClassification(StrEnum):
    SYNTHETIC = "SYNTHETIC"
    APPROVED_PILOT = "APPROVED_PILOT"
    PRODUCTION = "PRODUCTION"


class ProposedAction(StrEnum):
    CUSTOMER_ONBOARDING = "CUSTOMER_ONBOARDING"
    QUOTE_RELEASE = "QUOTE_RELEASE"
    ORDER_ACCEPTANCE = "ORDER_ACCEPTANCE"
    BOOKING = "BOOKING"
    SHIPMENT_RELEASE = "SHIPMENT_RELEASE"
    PAYMENT = "PAYMENT"


class PartyRole(StrEnum):
    APPLICANT = "APPLICANT"
    CUSTOMER = "CUSTOMER"
    SELLER = "SELLER"
    BUYER = "BUYER"
    SUPPLIER = "SUPPLIER"
    CONSIGNEE = "CONSIGNEE"
    END_USER = "END_USER"
    SHIPPER = "SHIPPER"
    CARRIER = "CARRIER"
    VESSEL = "VESSEL"
    PAYER = "PAYER"
    PAYEE = "PAYEE"
    ORIGINATING_BANK = "ORIGINATING_BANK"
    INTERMEDIARY_BANK = "INTERMEDIARY_BANK"
    BENEFICIARY_BANK = "BENEFICIARY_BANK"
    BENEFICIAL_OWNER = "BENEFICIAL_OWNER"
    CONTROLLER = "CONTROLLER"
    OTHER = "OTHER"


class EntityType(StrEnum):
    ORGANIZATION = "ORGANIZATION"
    PERSON = "PERSON"
    VESSEL = "VESSEL"
    BANK = "BANK"
    OTHER = "OTHER"


class RelationshipType(StrEnum):
    OWNERSHIP = "OWNERSHIP"
    CONTROL = "CONTROL"


class FactClass(StrEnum):
    VERIFIED_FACT = "VERIFIED_FACT"
    SOURCE_ASSERTION = "SOURCE_ASSERTION"
    INFERENCE = "INFERENCE"
    UNKNOWN = "UNKNOWN"


class ReviewerStatus(StrEnum):
    UNREVIEWED = "UNREVIEWED"
    REVIEWED = "REVIEWED"
    DISPUTED = "DISPUTED"


class ClassificationScheme(StrEnum):
    HS = "HS"
    CN = "CN"
    TARIC = "TARIC"
    ECCN = "ECCN"
    EU_DUAL_USE_ANNEX_I = "EU_DUAL_USE_ANNEX_I"
    EU_COMMON_MILITARY_LIST = "EU_COMMON_MILITARY_LIST"
    OTHER = "OTHER"


class LegalNexusType(StrEnum):
    JURISDICTION = "JURISDICTION"
    REGULATORY_REGIME = "REGULATORY_REGIME"
    BANK_POLICY = "BANK_POLICY"
    CONTRACT_POLICY = "CONTRACT_POLICY"
    OTHER = "OTHER"


class DocumentType(StrEnum):
    ORDER = "ORDER"
    INVOICE = "INVOICE"
    PACKING_LIST = "PACKING_LIST"
    TRANSPORT = "TRANSPORT"
    PAYMENT = "PAYMENT"
    TECHNICAL_SPECIFICATION = "TECHNICAL_SPECIFICATION"
    END_USER_STATEMENT = "END_USER_STATEMENT"
    OWNERSHIP_RECORD = "OWNERSHIP_RECORD"
    WEBSITE_CAPTURE = "WEBSITE_CAPTURE"
    OTHER = "OTHER"


class CaseState(StrEnum):
    INCOMPLETE = "INCOMPLETE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    ESCALATE = "ESCALATE"
    HUMAN_CLEARED = "HUMAN_CLEARED"
    HUMAN_BLOCKED = "HUMAN_BLOCKED"
    CLOSED_NO_ACTION = "CLOSED_NO_ACTION"


class Signal(StrEnum):
    RED = "RED"
    YELLOW = "YELLOW"
    GREEN_CANDIDATE = "GREEN_CANDIDATE"
    GREEN_HUMAN = "GREEN_HUMAN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class Priority(StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    NONE = "NONE"


class BusinessAction(StrEnum):
    HOLD = "HOLD"
    REQUEST_EVIDENCE = "REQUEST_EVIDENCE"
    ESCALATE = "ESCALATE"
    BLOCK = "BLOCK"
    MONITOR = "MONITOR"
    NO_ACTION = "NO_ACTION"
    ALLOW_WITHIN_HUMAN_DECISION = "ALLOW_WITHIN_HUMAN_DECISION"


class FindingStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    SUPERSEDED = "SUPERSEDED"


class EvidenceRequirementStatus(StrEnum):
    REQUIRED = "REQUIRED"
    PROVIDED = "PROVIDED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class HumanDecisionType(StrEnum):
    HUMAN_CLEARED = "HUMAN_CLEARED"
    HUMAN_BLOCKED = "HUMAN_BLOCKED"
    CLOSED_NO_ACTION = "CLOSED_NO_ACTION"


class SourceStatusValue(StrEnum):
    CURRENT = "CURRENT"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"
    QUARANTINED = "QUARANTINED"


class EventType(StrEnum):
    SCREENING_COMPLETED = "screening.completed"
    CASE_STATE_CHANGED = "case.state_changed"
    HUMAN_DECISION_RECORDED = "human_decision.recorded"


class HealthStatus(StrEnum):
    OK = "OK"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


class Identifier(ContractModel):
    type: Reference
    value: ShortText
    issuer: ShortText | None = None


class ExternalObject(ContractModel):
    system: Reference
    object_type: Reference
    object_id: ShortText
    object_version: ShortText


class Party(ContractModel):
    party_ref: Reference
    roles: Annotated[list[PartyRole], Field(min_length=1, max_length=16)]
    entity_type: EntityType
    legal_name: ShortText | None = None
    aliases: Annotated[list[ShortText], Field(max_length=64)] = Field(
        default_factory=list
    )
    country: CountryCode | None = None
    identifiers: Annotated[list[Identifier], Field(max_length=64)] = Field(
        default_factory=list
    )
    address: LongText | None = None


class OwnershipControlRelationship(ContractModel):
    relationship_ref: Reference
    from_party_ref: Reference
    to_party_ref: Reference
    relationship_type: RelationshipType
    ownership_percentage: Percentage | None = None
    control_basis: LongText | None = None
    fact_class: FactClass = FactClass.UNKNOWN
    evidence_refs: Annotated[list[Reference], Field(max_length=64)] = Field(
        default_factory=list
    )
    source_refs: Annotated[list[Reference], Field(max_length=64)] = Field(
        default_factory=list
    )
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None
    reviewer_status: ReviewerStatus = ReviewerStatus.UNREVIEWED

    @model_validator(mode="after")
    def validate_time_window(self) -> OwnershipControlRelationship:
        if (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_to < self.valid_from
        ):
            raise ValueError("valid_to must be on or after valid_from")
        return self


class ClassificationCandidate(ContractModel):
    candidate_ref: Reference
    scheme: ClassificationScheme
    scheme_name: ShortText | None = None
    code: ShortText
    candidate_only: Literal[True] = True
    rationale: LongText | None = None
    confidence: Confidence | None = None
    fact_class: FactClass = FactClass.UNKNOWN
    evidence_refs: Annotated[list[Reference], Field(max_length=64)] = Field(
        default_factory=list
    )
    source_refs: Annotated[list[Reference], Field(max_length=64)] = Field(
        default_factory=list
    )
    reviewer_status: ReviewerStatus = ReviewerStatus.UNREVIEWED

    @model_validator(mode="after")
    def name_other_scheme(self) -> ClassificationCandidate:
        if self.scheme is ClassificationScheme.OTHER and self.scheme_name is None:
            raise ValueError("scheme_name is required when scheme is OTHER")
        return self


class LegalNexus(ContractModel):
    nexus_ref: Reference
    nexus_type: LegalNexusType
    jurisdiction: CountryCode | None = None
    basis: LongText | None = None
    fact_class: FactClass = FactClass.UNKNOWN
    evidence_refs: Annotated[list[Reference], Field(max_length=64)] = Field(
        default_factory=list
    )
    source_refs: Annotated[list[Reference], Field(max_length=64)] = Field(
        default_factory=list
    )


class EndUse(ContractModel):
    description: LongText | None = None
    end_user_ref: Reference | None = None
    installation_site: ShortText | None = None
    country: CountryCode | None = None


class GoodsLine(ContractModel):
    line_ref: Reference
    description: LongText | None = None
    manufacturer: ShortText | None = None
    model_or_part_number: ShortText | None = None
    technical_specification: LongText | None = None
    software_version: ShortText | None = None
    firmware_version: ShortText | None = None
    classification_candidates: Annotated[
        list[ClassificationCandidate], Field(max_length=32)
    ] = Field(default_factory=list)
    quantity: PositiveQuantity | None = None
    quantity_unit: ShortText | None = None
    unit_value: MoneyAmount | None = None
    total_value: MoneyAmount | None = None
    currency: CurrencyCode | None = None
    origin_country: CountryCode | None = None
    end_use: EndUse | None = None


class Route(ContractModel):
    origin_country: CountryCode | None = None
    loading_location: ShortText | None = None
    transit_countries: Annotated[list[CountryCode], Field(max_length=32)] = Field(
        default_factory=list
    )
    discharge_location: ShortText | None = None
    destination_country: CountryCode | None = None
    final_use_country: CountryCode | None = None
    carrier_ref: Reference | None = None
    vessel_ref: Reference | None = None


class DocumentReference(ContractModel):
    document_ref: Reference
    document_type: DocumentType
    external_reference: ShortText | None = None
    content_hash: ContentHash | None = None
    media_type: ShortText | None = None
    issued_at: AwareDatetime | None = None
    related_party_refs: Annotated[list[Reference], Field(max_length=64)] = Field(
        default_factory=list
    )
    related_goods_refs: Annotated[list[Reference], Field(max_length=64)] = Field(
        default_factory=list
    )


class PaymentPath(ContractModel):
    payment_ref: Reference
    payer_ref: Reference | None = None
    payee_ref: Reference | None = None
    originating_bank_ref: Reference | None = None
    intermediary_bank_refs: Annotated[list[Reference], Field(max_length=16)] = Field(
        default_factory=list
    )
    beneficiary_bank_ref: Reference | None = None
    amount: MoneyAmount | None = None
    currency: CurrencyCode | None = None
    origin_country: CountryCode | None = None
    destination_country: CountryCode | None = None
    purpose: LongText | None = None
    evidence_refs: Annotated[list[Reference], Field(max_length=64)] = Field(
        default_factory=list
    )


def _duplicates(values: list[str]) -> list[str]:
    return sorted(value for value, count in Counter(values).items() if count > 1)


class ScreeningRequest(ContractModel):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    tenant_id: Reference
    correlation_id: Reference
    data_classification: DataClassification
    external_object: ExternalObject
    proposed_action: ProposedAction
    action_due_at: AwareDatetime | None = None
    legal_nexus: Annotated[list[LegalNexus], Field(max_length=64)] = Field(
        default_factory=list
    )
    parties: Annotated[list[Party], Field(max_length=256)] = Field(default_factory=list)
    ownership_and_control: Annotated[
        list[OwnershipControlRelationship], Field(max_length=512)
    ] = Field(default_factory=list)
    goods: Annotated[list[GoodsLine], Field(max_length=256)] = Field(
        default_factory=list
    )
    route: Route | None = None
    documents: Annotated[list[DocumentReference], Field(max_length=256)] = Field(
        default_factory=list
    )
    payment: PaymentPath | None = None

    @model_validator(mode="after")
    def validate_reference_graph(self) -> ScreeningRequest:
        """Reject ambiguous identifiers and dangling in-request references."""

        groups = {
            "party_ref": [item.party_ref for item in self.parties],
            "nexus_ref": [item.nexus_ref for item in self.legal_nexus],
            "relationship_ref": [
                item.relationship_ref for item in self.ownership_and_control
            ],
            "line_ref": [item.line_ref for item in self.goods],
            "document_ref": [item.document_ref for item in self.documents],
            "payment_ref": [self.payment.payment_ref] if self.payment else [],
            "candidate_ref": [
                candidate.candidate_ref
                for line in self.goods
                for candidate in line.classification_candidates
            ],
        }
        duplicate_messages = [
            f"duplicate {name}: {', '.join(duplicates)}"
            for name, values in groups.items()
            if (duplicates := _duplicates(values))
        ]
        if duplicate_messages:
            raise ValueError("; ".join(duplicate_messages))

        party_refs = set(groups["party_ref"])
        goods_refs = set(groups["line_ref"])
        evidence_refs = set(groups["document_ref"])
        used_party_refs = {
            reference
            for relationship in self.ownership_and_control
            for reference in (
                relationship.from_party_ref,
                relationship.to_party_ref,
            )
        }
        used_evidence_refs = {
            reference
            for relationship in self.ownership_and_control
            for reference in relationship.evidence_refs
        }
        for nexus in self.legal_nexus:
            used_evidence_refs.update(nexus.evidence_refs)
        for line in self.goods:
            if line.end_use and line.end_use.end_user_ref:
                used_party_refs.add(line.end_use.end_user_ref)
            for candidate in line.classification_candidates:
                used_evidence_refs.update(candidate.evidence_refs)
        if self.route:
            used_party_refs.update(
                reference
                for reference in (self.route.carrier_ref, self.route.vessel_ref)
                if reference
            )
        if self.payment:
            used_party_refs.update(
                reference
                for reference in (
                    self.payment.payer_ref,
                    self.payment.payee_ref,
                    self.payment.originating_bank_ref,
                    *self.payment.intermediary_bank_refs,
                    self.payment.beneficiary_bank_ref,
                )
                if reference
            )
            used_evidence_refs.update(self.payment.evidence_refs)
        for document in self.documents:
            used_party_refs.update(document.related_party_refs)
            used_evidence_refs.add(document.document_ref)

        unknown_party_refs = sorted(used_party_refs - party_refs)
        unknown_goods_refs = sorted(
            {
                reference
                for document in self.documents
                for reference in document.related_goods_refs
            }
            - goods_refs
        )
        unknown_evidence_refs = sorted(used_evidence_refs - evidence_refs)
        reference_messages = []
        if unknown_party_refs:
            reference_messages.append(
                f"unknown party refs: {', '.join(unknown_party_refs)}"
            )
        if unknown_goods_refs:
            reference_messages.append(
                f"unknown goods refs: {', '.join(unknown_goods_refs)}"
            )
        if unknown_evidence_refs:
            reference_messages.append(
                f"unknown evidence refs: {', '.join(unknown_evidence_refs)}"
            )
        if reference_messages:
            raise ValueError("; ".join(reference_messages))
        return self

    def canonical_input_hash(self) -> str:
        """Return the stable hash used to bind a result to this input snapshot."""

        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return f"sha256:{hashlib.sha256(payload).hexdigest()}"


class VersionReference(ContractModel):
    resource_id: Reference
    version: ShortText
    content_hash: ContentHash | None = None


class VersionSet(ContractModel):
    input_schema: ShortText
    input_hash: ContentHash
    sources: Annotated[list[VersionReference], Field(max_length=256)]
    rules: Annotated[list[VersionReference], Field(max_length=256)]
    matcher: VersionReference
    models: Annotated[list[VersionReference], Field(max_length=64)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def reject_duplicate_versions(self) -> VersionSet:
        for category, references in (
            ("sources", self.sources),
            ("rules", self.rules),
            ("models", self.models),
        ):
            duplicates = _duplicates([item.resource_id for item in references])
            if duplicates:
                raise ValueError(
                    f"duplicate {category} resource_id: {', '.join(duplicates)}"
                )
        return self


class Uncertainty(ContractModel):
    fact_path: ShortText
    description: LongText


class Finding(ContractModel):
    finding_id: Reference
    kind: Reference
    priority: Priority
    status: FindingStatus
    fact_class: FactClass
    summary: LongText
    evidence_refs: Annotated[list[Reference], Field(max_length=64)] = Field(
        default_factory=list
    )
    source_refs: Annotated[list[Reference], Field(max_length=64)] = Field(
        default_factory=list
    )
    uncertainty: Annotated[list[Uncertainty], Field(max_length=64)] = Field(
        default_factory=list
    )
    required_evidence_refs: Annotated[list[Reference], Field(max_length=64)] = Field(
        default_factory=list
    )
    required_action: Reference
    owner_role: Reference
    due_before: ProposedAction | None = None


class EvidenceRequirement(ContractModel):
    requirement_ref: Reference
    evidence_type: DocumentType
    description: LongText
    status: EvidenceRequirementStatus
    related_party_refs: Annotated[list[Reference], Field(max_length=64)] = Field(
        default_factory=list
    )
    related_goods_refs: Annotated[list[Reference], Field(max_length=64)] = Field(
        default_factory=list
    )


class Hold(ContractModel):
    hold_id: Reference
    scope: ProposedAction
    reason: LongText
    release_condition: LongText
    active: bool


class ScreeningResult(ContractModel):
    schema_version: Literal["1.0.0"]
    tenant_id: Reference
    correlation_id: Reference
    screening_id: Reference
    case_id: Reference | None
    state: CaseState
    signal: Signal
    highest_priority: Priority
    business_action: BusinessAction
    summary: LongText
    findings: Annotated[list[Finding], Field(max_length=512)]
    required_evidence: Annotated[list[EvidenceRequirement], Field(max_length=256)]
    holds: Annotated[list[Hold], Field(max_length=64)]
    version_set: VersionSet
    result_hash: ContentHash
    created_at: AwareDatetime


class DecisionScope(ContractModel):
    proposed_action: ProposedAction
    external_object: ExternalObject
    party_refs: Annotated[list[Reference], Field(max_length=256)] = Field(
        default_factory=list
    )
    goods_refs: Annotated[list[Reference], Field(max_length=256)] = Field(
        default_factory=list
    )


def _validate_human_clearance_expiry(
    decision: HumanDecisionType, expires_at: datetime | None
) -> None:
    if decision is HumanDecisionType.HUMAN_CLEARED and expires_at is None:
        raise ValueError("expires_at is required for HUMAN_CLEARED")


class HumanDecisionRequest(ContractModel):
    decision: HumanDecisionType
    scope: DecisionScope
    rationale: Annotated[str, StringConstraints(min_length=10, max_length=5000)]
    resolved_finding_ids: Annotated[list[Reference], Field(max_length=512)]
    evidence_refs: Annotated[list[Reference], Field(max_length=256)]
    expires_at: AwareDatetime | None

    @model_validator(mode="after")
    def require_clearance_expiry(self) -> HumanDecisionRequest:
        _validate_human_clearance_expiry(self.decision, self.expires_at)
        return self


class HumanDecision(HumanDecisionRequest):
    tenant_id: Reference
    correlation_id: Reference
    decision_id: Reference
    version_set: VersionSet
    reviewer_id: Reference
    reviewer_role: Reference
    recorded_at: AwareDatetime
    effective_from: AwareDatetime


class Case(ContractModel):
    tenant_id: Reference
    correlation_id: Reference
    case_id: Reference
    state: CaseState
    signal: Signal
    highest_priority: Priority
    business_action: BusinessAction
    findings: Annotated[list[Finding], Field(max_length=512)]
    holds: Annotated[list[Hold], Field(max_length=64)]
    effective_human_decision: HumanDecision | None = None
    updated_at: AwareDatetime


class EvidenceSubmission(ContractModel):
    evidence_type: DocumentType
    content_hash: ContentHash
    media_type: ShortText
    storage_handle: ShortText
    supports_finding_ids: Annotated[list[Reference], Field(max_length=512)]
    note: Annotated[str, StringConstraints(max_length=2000)] | None = None


class ReviewRequest(ContractModel):
    reason: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    requested_reviewer_group: Reference | None = None


class SourceStatus(ContractModel):
    source_id: Reference
    active_snapshot_id: Reference
    status: SourceStatusValue
    retrieved_at: AwareDatetime
    effective_from: AwareDatetime | None = None


class EventSubject(ContractModel):
    screening_id: Reference | None = None
    case_id: Reference | None = None
    external_object_type: Reference
    external_object_id: ShortText


class ScreeningCompletedEventData(ContractModel):
    kind: Literal["screening.completed"]
    screening_id: Reference
    case_id: Reference | None
    case_state: CaseState
    business_action: BusinessAction
    signal: Signal
    result_hash: ContentHash


class CaseStateChangedEventData(ContractModel):
    kind: Literal["case.state_changed"]
    case_id: Reference
    previous_state: CaseState
    case_state: CaseState
    business_action: BusinessAction
    decision_expires_at: AwareDatetime | None


class HumanDecisionRecordedEventData(ContractModel):
    kind: Literal["human_decision.recorded"]
    case_id: Reference
    decision_id: Reference
    decision: HumanDecisionType
    decision_effective_from: AwareDatetime
    decision_expires_at: AwareDatetime | None


EventData = Annotated[
    ScreeningCompletedEventData
    | CaseStateChangedEventData
    | HumanDecisionRecordedEventData,
    Field(discriminator="kind"),
]


class EventEnvelope(ContractModel):
    event_id: Reference
    event_type: EventType
    event_version: Literal["1.0"]
    occurred_at: AwareDatetime
    tenant_id: Reference
    correlation_id: Reference
    subject: EventSubject
    data: EventData

    @model_validator(mode="after")
    def match_event_type_to_data(self) -> EventEnvelope:
        if self.event_type.value != self.data.kind:
            raise ValueError("event_type must match data.kind")
        return self


class Health(ContractModel):
    status: HealthStatus
    checks: Annotated[dict[str, ShortText], Field(max_length=64)] = Field(
        default_factory=dict
    )


class Error(ContractModel):
    code: Reference
    message: LongText
    correlation_id: Reference
    retryable: bool
    details: Annotated[dict[str, str | int | bool | None], Field(max_length=64)] = (
        Field(default_factory=dict)
    )


CONTRACT_MODELS: tuple[type[ContractModel], ...] = (
    Case,
    CaseStateChangedEventData,
    ClassificationCandidate,
    DecisionScope,
    DocumentReference,
    EndUse,
    Error,
    EventEnvelope,
    EvidenceRequirement,
    EvidenceSubmission,
    ExternalObject,
    Finding,
    GoodsLine,
    Health,
    Hold,
    HumanDecision,
    HumanDecisionRecordedEventData,
    HumanDecisionRequest,
    Identifier,
    LegalNexus,
    OwnershipControlRelationship,
    Party,
    PaymentPath,
    ReviewRequest,
    Route,
    ScreeningCompletedEventData,
    ScreeningRequest,
    ScreeningResult,
    SourceStatus,
    Uncertainty,
    VersionReference,
    VersionSet,
)
