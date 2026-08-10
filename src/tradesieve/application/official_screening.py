"""One canonical application path over live official EU source connectors."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Protocol

from pydantic import Field, model_validator

from tradesieve.application.contracts import (
    BusinessAction,
    ContractModel,
    Signal,
)
from tradesieve.domain.eu_dual_use import (
    EU_DUAL_USE_CELEX,
    EU_DUAL_USE_EFFECTIVE_FROM,
    EuDualUseAssessmentEngine,
    EuDualUseAssessmentStatus,
    EuDualUseControlList,
    normalize_control_code,
)
from tradesieve.domain.eu_fsf import (
    EU_FSF_IDENTIFIER_TYPES,
    EuFsfExactIndex,
    EuFsfExactMatchStatus,
    EuFsfExactQuery,
    EuFsfSnapshot,
)


class _RetrievedFsf(Protocol):
    @property
    def retrieved_at(self) -> datetime: ...

    @property
    def content_hash(self) -> str: ...

    @property
    def content(self) -> bytes: ...


class _EuFsfSource(Protocol):
    def retrieve(self) -> _RetrievedFsf: ...


class _EuDualUseSource(Protocol):
    def retrieve(self) -> EuDualUseControlList: ...


class _EuFsfParser(Protocol):
    def parse(self, content: bytes) -> EuFsfSnapshot: ...


class OfficialPartyIdentifier(ContractModel):
    type: str
    value: str
    country: str | None = None

    @model_validator(mode="after")
    def validate_typed_identifier(self) -> OfficialPartyIdentifier:
        EuFsfExactQuery(self.type, self.value, self.country)
        return self


class OfficialGoodsCandidate(ContractModel):
    annex_i_code: str | None = None
    classification_verified: bool = False
    technical_specification_available: bool = False

    @model_validator(mode="after")
    def validate_control_code(self) -> OfficialGoodsCandidate:
        if self.annex_i_code is not None:
            normalize_control_code(self.annex_i_code)
        return self


class OfficialScreeningRequest(ContractModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    party_identifiers: Annotated[
        list[OfficialPartyIdentifier], Field(min_length=1, max_length=64)
    ]
    goods: OfficialGoodsCandidate


class OfficialSanctionsEvidence(ContractModel):
    eu_reference_number: str
    entity_logical_id: str
    subject_type: str
    identifier_type: str
    identifier_assertion_hash: str
    source_native_locator: str
    country_consistent: bool | None
    usable_for_exact_match: bool


class OfficialSanctionsResult(ContractModel):
    source: Literal["EU_CONSOLIDATED_FINANCIAL_SANCTIONS_FILE"] = (
        "EU_CONSOLIDATED_FINANCIAL_SANCTIONS_FILE"
    )
    source_generation_date: str
    source_global_file_id: str
    source_snapshot_id: str
    source_snapshot_content_hash: str
    source_raw_content_hash: str
    source_retrieved_at: str
    query_count: int
    statuses: list[str]
    evidence: list[OfficialSanctionsEvidence]


class OfficialDualUseResult(ContractModel):
    source: Literal["EU_DUAL_USE_ANNEX_I"] = "EU_DUAL_USE_ANNEX_I"
    source_celex: Literal["32025R2003"] = EU_DUAL_USE_CELEX
    source_effective_from: str
    source_snapshot_id: str
    source_snapshot_content_hash: str
    source_archive_hash: str
    source_retrieved_at: str
    status: str
    requested_code: str | None
    entry_content_hash: str | None
    source_native_locator: str | None
    missing_facts: list[str]


class OfficialScreeningResult(ContractModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    signal: Signal
    business_action: BusinessAction
    automatic_clearance: Literal[False] = False
    sanctions: OfficialSanctionsResult
    dual_use: OfficialDualUseResult
    caveats: list[str]


class OfficialScreeningService:
    """Retrieve, verify, and evaluate the current official source bytes per run."""

    def __init__(
        self,
        fsf_source: _EuFsfSource,
        dual_use_source: _EuDualUseSource,
        *,
        fsf_parser: _EuFsfParser,
    ) -> None:
        if not hasattr(fsf_source, "retrieve") or not hasattr(
            dual_use_source, "retrieve"
        ):
            raise ValueError("official screening sources must implement retrieve")
        self._fsf_source = fsf_source
        self._dual_use_source = dual_use_source
        self._fsf_parser = fsf_parser

    def screen(self, request: OfficialScreeningRequest) -> OfficialScreeningResult:
        if not isinstance(request, OfficialScreeningRequest):
            raise ValueError("official screening request must be typed")
        retrieved_fsf = self._fsf_source.retrieve()
        fsf_snapshot = self._fsf_parser.parse(retrieved_fsf.content)
        if fsf_snapshot.raw_content_hash != retrieved_fsf.content_hash:
            raise RuntimeError("official FSF retrieval/parser integrity mismatch")
        fsf_index = EuFsfExactIndex(fsf_snapshot)
        matches = [
            fsf_index.query(EuFsfExactQuery(item.type, item.value, item.country))
            for item in request.party_identifiers
        ]
        evidence = [
            OfficialSanctionsEvidence(
                eu_reference_number=item.eu_reference_number,
                entity_logical_id=item.entity_logical_id,
                subject_type=item.subject_type.value,
                identifier_type=item.identifier.type_code,
                identifier_assertion_hash=item.identifier.assertion_hash,
                source_native_locator=item.identifier.native_locator,
                country_consistent=item.country_consistent,
                usable_for_exact_match=item.identifier.usable_for_exact_match,
            )
            for match in matches
            for item in match.evidence
        ]

        dual_list = self._dual_use_source.retrieve()
        goods = request.goods
        dual_assessment = EuDualUseAssessmentEngine(dual_list).assess(
            goods.annex_i_code,
            classification_verified=goods.classification_verified,
            technical_specification_available=(goods.technical_specification_available),
        )
        sanctions_statuses = {match.status for match in matches}
        strong_sanctions_signal = bool(
            sanctions_statuses
            & {
                EuFsfExactMatchStatus.MATCH,
                EuFsfExactMatchStatus.AMBIGUOUS,
                EuFsfExactMatchStatus.REVIEW_REQUIRED,
            }
        )
        dual_entry_signal = dual_assessment.entry is not None
        if strong_sanctions_signal or dual_entry_signal:
            signal = Signal.RED
            action = BusinessAction.HOLD
        elif dual_assessment.status in {
            EuDualUseAssessmentStatus.MISSING_CLASSIFICATION,
            EuDualUseAssessmentStatus.TECHNICAL_REVIEW_REQUIRED,
        }:
            signal = Signal.YELLOW
            action = BusinessAction.REQUEST_EVIDENCE
        else:
            signal = Signal.GREEN_CANDIDATE
            action = BusinessAction.MONITOR

        entry = dual_assessment.entry
        return OfficialScreeningResult(
            signal=signal,
            business_action=action,
            sanctions=OfficialSanctionsResult(
                source_generation_date=fsf_snapshot.generation_date.isoformat(),
                source_global_file_id=fsf_snapshot.global_file_id,
                source_snapshot_id=fsf_snapshot.snapshot_id,
                source_snapshot_content_hash=fsf_snapshot.content_hash,
                source_raw_content_hash=fsf_snapshot.raw_content_hash,
                source_retrieved_at=retrieved_fsf.retrieved_at.isoformat(),
                query_count=len(matches),
                statuses=[match.status.value for match in matches],
                evidence=evidence,
            ),
            dual_use=OfficialDualUseResult(
                source_effective_from=EU_DUAL_USE_EFFECTIVE_FROM.isoformat(),
                source_snapshot_id=dual_list.snapshot_id,
                source_snapshot_content_hash=dual_list.content_hash,
                source_archive_hash=dual_list.source_archive_hash,
                source_retrieved_at=dual_list.retrieved_at.isoformat(),
                status=dual_assessment.status.value,
                requested_code=dual_assessment.requested_code,
                entry_content_hash=entry.content_hash if entry is not None else None,
                source_native_locator=(
                    entry.native_locator if entry is not None else None
                ),
                missing_facts=list(dual_assessment.missing_facts),
            ),
            caveats=[
                "Exact source evidence is not legal clearance.",
                "Name-only matching and ownership/control are outside this slice.",
                "An Annex I code is not inferred from HS/CN/TARIC data.",
                "Catch-all, destination, end-use and sanctions controls remain required.",
                "Only an authorised human may clear or block the transaction.",
            ],
        )


__all__ = [
    "EU_FSF_IDENTIFIER_TYPES",
    "OfficialGoodsCandidate",
    "OfficialPartyIdentifier",
    "OfficialScreeningRequest",
    "OfficialScreeningResult",
    "OfficialScreeningService",
]
