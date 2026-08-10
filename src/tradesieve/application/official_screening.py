"""One canonical application path over live official EU source connectors."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
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
    EuFsfNameCandidateStatus,
    EuFsfNameIndex,
    EuFsfNameQuery,
    EuFsfSnapshot,
)
from tradesieve.domain.official_sources import ActiveOfficialSources


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


class _ActiveOfficialRepository(Protocol):
    def get_active(self) -> ActiveOfficialSources: ...


class OfficialPartyIdentifier(ContractModel):
    type: str
    value: str
    country: str | None = None

    @model_validator(mode="after")
    def validate_typed_identifier(self) -> OfficialPartyIdentifier:
        EuFsfExactQuery(self.type, self.value, self.country)
        return self


class OfficialPartyName(ContractModel):
    name: str

    @model_validator(mode="after")
    def validate_name(self) -> OfficialPartyName:
        EuFsfNameQuery(self.name)
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
        list[OfficialPartyIdentifier], Field(max_length=64)
    ] = Field(default_factory=list)
    party_names: Annotated[list[OfficialPartyName], Field(max_length=64)] = Field(
        default_factory=list
    )
    goods: OfficialGoodsCandidate

    @model_validator(mode="after")
    def require_party_search_fact(self) -> OfficialScreeningRequest:
        if not self.party_identifiers and not self.party_names:
            raise ValueError("at least one party identifier or name is required")
        return self


class OfficialSanctionsEvidence(ContractModel):
    eu_reference_number: str
    entity_logical_id: str
    subject_type: str
    identifier_type: str
    identifier_assertion_hash: str
    source_native_locator: str
    country_consistent: bool | None
    usable_for_exact_match: bool


class OfficialSanctionsNameEvidence(ContractModel):
    eu_reference_number: str
    entity_logical_id: str
    subject_type: str
    alias_assertion_hash: str
    source_native_locator: str
    strong_alias: bool


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
    identifier_query_count: int
    identifier_statuses: list[str]
    identifier_evidence: list[OfficialSanctionsEvidence]
    name_query_count: int
    name_statuses: list[str]
    name_evidence: list[OfficialSanctionsNameEvidence]


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
    source_bundle_id: str | None = None
    source_bundle_content_hash: str | None = None
    source_bundle_activated_at: str | None = None
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
        dual_list = self._dual_use_source.retrieve()
        return OfficialScreeningEngine().screen(
            request,
            fsf_snapshot=fsf_snapshot,
            fsf_retrieved_at=retrieved_fsf.retrieved_at,
            dual_use_control_list=dual_list,
        )


class OfficialScreeningEngine:
    """Canonical deterministic evaluation over one already verified source pair."""

    def screen(
        self,
        request: OfficialScreeningRequest,
        *,
        fsf_snapshot: EuFsfSnapshot,
        fsf_retrieved_at: datetime,
        dual_use_control_list: EuDualUseControlList,
        source_bundle: ActiveOfficialSources | None = None,
    ) -> OfficialScreeningResult:
        if not isinstance(request, OfficialScreeningRequest):
            raise ValueError("official screening request must be typed")
        if not isinstance(fsf_snapshot, EuFsfSnapshot) or not isinstance(
            dual_use_control_list, EuDualUseControlList
        ):
            raise ValueError("official screening projections must be typed")
        if (
            not isinstance(fsf_retrieved_at, datetime)
            or fsf_retrieved_at.tzinfo is None
            or fsf_retrieved_at.utcoffset() is None
        ):
            raise ValueError("official FSF retrieval time must be timezone-aware")
        if source_bundle is not None and (
            not isinstance(source_bundle, ActiveOfficialSources)
            or source_bundle.fsf_snapshot != fsf_snapshot
            or source_bundle.dual_use_control_list != dual_use_control_list
            or source_bundle.fsf_retrieved_at != fsf_retrieved_at
        ):
            raise ValueError("official source bundle does not bind the projections")
        fsf_index = EuFsfExactIndex(fsf_snapshot)
        matches = [
            fsf_index.query(EuFsfExactQuery(item.type, item.value, item.country))
            for item in request.party_identifiers
        ]
        identifier_evidence = [
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
        name_index = EuFsfNameIndex(fsf_snapshot)
        name_candidates = [
            name_index.query(EuFsfNameQuery(item.name)) for item in request.party_names
        ]
        name_evidence = [
            OfficialSanctionsNameEvidence(
                eu_reference_number=item.eu_reference_number,
                entity_logical_id=item.entity_logical_id,
                subject_type=item.subject_type.value,
                alias_assertion_hash=item.alias.assertion_hash,
                source_native_locator=item.alias.native_locator,
                strong_alias=item.alias.strong,
            )
            for candidates in name_candidates
            for item in candidates.evidence
        ]

        goods = request.goods
        dual_assessment = EuDualUseAssessmentEngine(dual_use_control_list).assess(
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
        name_candidate_signal = any(
            candidates.status is not EuFsfNameCandidateStatus.NO_CANDIDATE
            for candidates in name_candidates
        )
        dual_entry_signal = dual_assessment.entry is not None
        if strong_sanctions_signal or name_candidate_signal or dual_entry_signal:
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
            source_bundle_id=(
                source_bundle.bundle_id if source_bundle is not None else None
            ),
            source_bundle_content_hash=(
                source_bundle.bundle_content_hash if source_bundle is not None else None
            ),
            source_bundle_activated_at=(
                source_bundle.activated_at.isoformat()
                if source_bundle is not None
                else None
            ),
            sanctions=OfficialSanctionsResult(
                source_generation_date=fsf_snapshot.generation_date.isoformat(),
                source_global_file_id=fsf_snapshot.global_file_id,
                source_snapshot_id=fsf_snapshot.snapshot_id,
                source_snapshot_content_hash=fsf_snapshot.content_hash,
                source_raw_content_hash=fsf_snapshot.raw_content_hash,
                source_retrieved_at=fsf_retrieved_at.isoformat(),
                identifier_query_count=len(matches),
                identifier_statuses=[match.status.value for match in matches],
                identifier_evidence=identifier_evidence,
                name_query_count=len(name_candidates),
                name_statuses=[item.status.value for item in name_candidates],
                name_evidence=name_evidence,
            ),
            dual_use=OfficialDualUseResult(
                source_effective_from=EU_DUAL_USE_EFFECTIVE_FROM.isoformat(),
                source_snapshot_id=dual_use_control_list.snapshot_id,
                source_snapshot_content_hash=dual_use_control_list.content_hash,
                source_archive_hash=dual_use_control_list.source_archive_hash,
                source_retrieved_at=dual_use_control_list.retrieved_at.isoformat(),
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
                "Name results are exact normalized-alias candidates, not fuzzy matches.",
                "Transliteration, fuzzy matching and ownership/control remain required.",
                "An Annex I code is not inferred from HS/CN/TARIC data.",
                "Catch-all, destination, end-use and sanctions controls remain required.",
                "Only an authorised human may clear or block the transaction.",
            ],
        )


class PersistedOfficialScreeningService:
    """Screen only against a fresh, atomically active official source bundle."""

    def __init__(
        self,
        repository: _ActiveOfficialRepository,
        *,
        clock: Callable[[], datetime] | None = None,
        maximum_source_age: timedelta = timedelta(hours=48),
    ) -> None:
        if not hasattr(repository, "get_active"):
            raise ValueError("official source repository must implement get_active")
        if clock is not None and not callable(clock):
            raise ValueError("screening clock must be callable")
        if (
            not isinstance(maximum_source_age, timedelta)
            or maximum_source_age <= timedelta(0)
            or maximum_source_age > timedelta(days=30)
        ):
            raise ValueError("maximum official source age is invalid")
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))
        self._maximum_source_age = maximum_source_age

    def screen(self, request: OfficialScreeningRequest) -> OfficialScreeningResult:
        active = self._repository.get_active()
        if not isinstance(active, ActiveOfficialSources):
            raise RuntimeError(
                "active official source repository returned invalid data"
            )
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise RuntimeError("official screening clock is invalid")
        source_times = (
            active.fsf_retrieved_at,
            active.dual_use_control_list.retrieved_at,
            active.activated_at,
        )
        if any(
            timestamp > now or now - timestamp > self._maximum_source_age
            for timestamp in source_times
        ):
            raise RuntimeError("active official source bundle is stale")
        return OfficialScreeningEngine().screen(
            request,
            fsf_snapshot=active.fsf_snapshot,
            fsf_retrieved_at=active.fsf_retrieved_at,
            dual_use_control_list=active.dual_use_control_list,
            source_bundle=active,
        )


__all__ = [
    "EU_FSF_IDENTIFIER_TYPES",
    "OfficialGoodsCandidate",
    "OfficialPartyIdentifier",
    "OfficialPartyName",
    "OfficialScreeningRequest",
    "OfficialScreeningResult",
    "OfficialScreeningEngine",
    "OfficialScreeningService",
    "PersistedOfficialScreeningService",
]
