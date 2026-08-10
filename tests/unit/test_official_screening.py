"""Canonical live official screening application-path tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

import pytest
from pydantic import ValidationError

import tradesieve.domain.eu_dual_use_technical as technical
from tradesieve.application.contracts import BusinessAction, Signal
from tradesieve.application.official_screening import (
    OfficialGoodsCandidate,
    OfficialPartyIdentifier,
    OfficialPartyName,
    OfficialScreeningEngine,
    OfficialScreeningRequest,
    OfficialScreeningService,
    OfficialTechnicalFact,
    PersistedOfficialScreeningService,
)
from tradesieve.domain.eu_dual_use import (
    EuDualUseControlEntry,
    EuDualUseControlList,
)
from tradesieve.domain.eu_dual_use_technical import (
    TechnicalCellType,
    TechnicalFactId,
    TechnicalFactUnit,
    TechnicalProductFamily,
)
from tradesieve.domain.eu_fsf import (
    EuFsfAlias,
    EuFsfEntity,
    EuFsfIdentifier,
    EuFsfRegulation,
    EuFsfSnapshot,
    EuFsfSubjectType,
)
from tradesieve.domain.ofac_sls import OfacSlsListKind, OfacSnapshot
from tradesieve.domain.official_sources import (
    ActiveOfficialSources,
    official_source_bundle_id,
)

from ..ofac_test_data import ofac_pair

NOW = datetime(2026, 8, 10, 4, 30, tzinfo=UTC)


def fsf_identifier(*, expired: bool = False) -> EuFsfIdentifier:
    return EuFsfIdentifier.create(
        logical_id="10",
        type_code="regnumber",
        number="36-3823186",
        country_code="US",
        known_expired=expired,
        known_false=False,
        reported_lost=False,
        revoked_by_issuer=False,
        native_locator="/export/sanctionEntity[@logicalId='1']/identification[1]",
    )


def fsf_snapshot(*, expired: bool = False) -> EuFsfSnapshot:
    return EuFsfSnapshot(
        generation_date=datetime(2026, 8, 5, 16, 47, tzinfo=UTC),
        global_file_id="184961",
        raw_content_hash="sha256:" + "a" * 64,
        raw_byte_length=100,
        entities=(
            EuFsfEntity(
                logical_id="1",
                eu_reference_number="EU.1",
                united_nations_id=None,
                designation_date=date(2026, 8, 1),
                subject_type=EuFsfSubjectType.ENTERPRISE,
                regulation=EuFsfRegulation(
                    programme="TEST",
                    number_title="2026/1",
                    publication_date=date(2026, 8, 1),
                    entry_into_force_date=date(2026, 8, 2),
                    publication_url="https://eur-lex.europa.eu/example",
                    native_locator="/export/sanctionEntity[1]/regulation[1]",
                ),
                aliases=(
                    EuFsfAlias.create(
                        logical_id="1",
                        whole_name="Listed Example",
                        strong=True,
                        language="EN",
                        native_locator="/export/sanctionEntity[1]/nameAlias[1]",
                    ),
                ),
                identifiers=(fsf_identifier(expired=expired),),
            ),
        ),
    )


def dual_list() -> EuDualUseControlList:
    return EuDualUseControlList(
        retrieved_at=NOW,
        source_archive_hash="sha256:" + "b" * 64,
        source_archive_bytes=100,
        formex_document_hash="sha256:" + "c" * 64,
        entries=tuple(
            EuDualUseControlEntry(
                code=f"{category}A{index:03d}",
                text=f"Controlled entry {category}-{index}",
                native_locator=f"/ANNEX/NP[NO.P='{category}A{index:03d}']",
            )
            for category in range(10)
            for index in range(30)
        ),
    )


@dataclass(frozen=True)
class RetrievedFsf:
    retrieved_at: datetime
    content_hash: str
    content: bytes


class Source:
    def __init__(self, value: Any) -> None:
        self.value = value
        self.calls = 0

    def retrieve(self) -> Any:
        self.calls += 1
        return self.value


class Parser:
    def __init__(self, value: EuFsfSnapshot) -> None:
        self.value = value
        self.contents: list[bytes] = []

    def parse(self, content: bytes) -> EuFsfSnapshot:
        self.contents.append(content)
        return self.value


@dataclass(frozen=True)
class RetrievedOfac:
    content_hash: str
    snapshot: OfacSnapshot


class OfacSource:
    def __init__(self, snapshots: tuple[OfacSnapshot, OfacSnapshot]) -> None:
        self.values = {
            snapshot.list_kind: RetrievedOfac(snapshot.raw_content_hash, snapshot)
            for snapshot in snapshots
        }
        self.calls: list[OfacSlsListKind] = []

    def retrieve(self, list_kind: OfacSlsListKind) -> RetrievedOfac:
        self.calls.append(list_kind)
        return self.values[list_kind]


class OfacParser:
    def __init__(self) -> None:
        self.calls: list[OfacSlsListKind] = []

    def parse(self, source: RetrievedOfac) -> OfacSnapshot:
        self.calls.append(source.snapshot.list_kind)
        return source.snapshot


def request(
    *,
    number: str = "36-3823186",
    code: str | None = "0A000",
    verified: bool = True,
    specification: bool = True,
) -> OfficialScreeningRequest:
    return OfficialScreeningRequest(
        party_identifiers=[
            OfficialPartyIdentifier(type="regnumber", value=number, country="US")
        ],
        goods=OfficialGoodsCandidate(
            annex_i_code=code,
            classification_verified=verified,
            technical_specification_available=specification,
        ),
    )


def service(
    *,
    snapshot: EuFsfSnapshot | None = None,
) -> tuple[OfficialScreeningService, Source, Source, Parser, OfacSource, OfacParser]:
    parsed = snapshot or fsf_snapshot()
    fsf_source = Source(
        RetrievedFsf(
            retrieved_at=NOW,
            content_hash=parsed.raw_content_hash,
            content=b"source bytes",
        )
    )
    dual_source = Source(dual_list())
    parser = Parser(parsed)
    ofac_source = OfacSource(ofac_pair(retrieved_at=NOW))
    ofac_parser = OfacParser()
    return (
        OfficialScreeningService(
            cast(Any, fsf_source),
            cast(Any, dual_source),
            ofac_source,
            fsf_parser=parser,
            ofac_parser=cast(Any, ofac_parser),
        ),
        fsf_source,
        dual_source,
        parser,
        ofac_source,
        ofac_parser,
    )


def test_exact_sanctions_and_annex_entry_hold_with_versioned_evidence() -> None:
    subject, fsf_source, dual_source, parser, ofac_source, ofac_parser = service()
    result = subject.screen(request())

    assert result.signal is Signal.RED
    assert result.business_action is BusinessAction.HOLD
    assert result.automatic_clearance is False
    assert result.source_bundle_id is None
    assert result.sanctions.identifier_statuses == ["MATCH"]
    assert result.sanctions.identifier_evidence[0].eu_reference_number == "EU.1"
    assert result.sanctions.identifier_evidence[0].identifier_assertion_hash.startswith(
        "sha256:"
    )
    assert result.sanctions.name_statuses == []
    assert result.sanctions.name_evidence == []
    assert [item.list_kind for item in result.ofac.lists] == ["SDN", "CONSOLIDATED"]
    assert all(item.identifier_evidence for item in result.ofac.lists)
    assert "36-3823186" not in result.model_dump_json()
    assert result.dual_use.status == "CONTROL_ENTRY_FOUND"
    assert result.dual_use.requested_code == "0A000"
    assert result.dual_use.entry_content_hash is not None
    assert result.dual_use.source_native_locator == "/ANNEX/NP[NO.P='0A000']"
    assert len(result.caveats) == 7
    assert fsf_source.calls == dual_source.calls == 1
    assert parser.contents == [b"source bytes"]
    assert ofac_source.calls == [OfacSlsListKind.SDN, OfacSlsListKind.CONSOLIDATED]
    assert ofac_parser.calls == ofac_source.calls


def test_source_bound_secondary_cell_threshold_is_exposed_in_canonical_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listing = dual_list()
    entry = next(item for item in listing.entries if item.code == "3A001")
    monkeypatch.setattr(technical, "EU_DUAL_USE_3A001_ENTRY_HASH", entry.content_hash)
    facts = [
        OfficialTechnicalFact(
            fact_id=TechnicalFactId.IS_BATTERY,
            unit=TechnicalFactUnit.BOOLEAN,
            boolean_value=False,
            evidence_ref="manufacturer-datasheet-1",
            verified=True,
        ),
        OfficialTechnicalFact(
            fact_id=TechnicalFactId.CELL_TYPE,
            unit=TechnicalFactUnit.CELL_TYPE,
            text_value=TechnicalCellType.SECONDARY,
            evidence_ref="manufacturer-datasheet-1",
            verified=True,
        ),
        OfficialTechnicalFact.model_validate(
            {
                "fact_id": "energy_density_wh_per_kg",
                "unit": "WH_PER_KG",
                "numeric_value": "380",
                "evidence_ref": "manufacturer-datasheet-1",
                "verified": True,
            }
        ),
        OfficialTechnicalFact.model_validate(
            {
                "fact_id": "measurement_temperature_celsius",
                "unit": "CELSIUS",
                "numeric_value": "20",
                "evidence_ref": "manufacturer-datasheet-1",
                "verified": True,
            }
        ),
    ]
    result = OfficialScreeningEngine().screen(
        OfficialScreeningRequest(
            party_names=[OfficialPartyName(name="Unlisted synthetic buyer")],
            goods=OfficialGoodsCandidate(
                annex_i_code="3A001",
                classification_verified=True,
                technical_specification_available=True,
                product_family=TechnicalProductFamily.ELECTROCHEMICAL_CELL,
                technical_facts=facts,
            ),
        ),
        fsf_snapshot=fsf_snapshot(),
        fsf_retrieved_at=NOW,
        dual_use_control_list=listing,
    )

    assert result.signal is Signal.RED
    assert result.business_action is BusinessAction.HOLD
    assessment = result.dual_use.technical_assessment
    assert assessment is not None
    assert assessment.status == "MATCHED"
    assert assessment.rule_id == "eu-3a001-e-1-secondary-cell"
    assert assessment.source_entry_content_hash == entry.content_hash
    assert assessment.evidence_refs == ["manufacturer-datasheet-1"]
    assert [item.matched for item in assessment.comparisons] == [True, True]
    assert assessment.automatic_clearance is False


def test_technical_payload_contract_rejects_partial_and_duplicate_facts() -> None:
    fact = {
        "fact_id": "resolution_bits",
        "unit": "BITS",
        "numeric_value": "12",
        "evidence_ref": "datasheet-1",
        "verified": True,
    }
    invalid_goods = (
        {
            "annex_i_code": "3A001",
            "technical_specification_available": True,
            "product_family": "ADC_INTEGRATED_CIRCUIT",
            "technical_facts": [],
        },
        {
            "annex_i_code": "3A001",
            "technical_specification_available": True,
            "product_family": "ADC_INTEGRATED_CIRCUIT",
            "technical_facts": [fact, fact],
        },
        {
            "annex_i_code": "3A001",
            "technical_specification_available": False,
            "product_family": "ADC_INTEGRATED_CIRCUIT",
            "technical_facts": [fact],
        },
        {
            "annex_i_code": None,
            "technical_specification_available": True,
            "product_family": "ADC_INTEGRATED_CIRCUIT",
            "technical_facts": [fact],
        },
    )
    for payload in invalid_goods:
        with pytest.raises(ValidationError):
            OfficialGoodsCandidate.model_validate(payload)
    with pytest.raises(ValidationError):
        OfficialTechnicalFact.model_validate(
            {
                **fact,
                "numeric_value": None,
                "boolean_value": True,
            }
        )


def test_missing_goods_classification_requests_evidence_after_no_match() -> None:
    subject, *_ = service()
    result = subject.screen(
        request(number="not-listed", code=None, verified=False, specification=False)
    )
    assert result.signal is Signal.YELLOW
    assert result.business_action is BusinessAction.REQUEST_EVIDENCE
    assert result.sanctions.identifier_statuses == ["NO_MATCH"]
    assert result.sanctions.identifier_evidence == []
    assert result.dual_use.status == "MISSING_CLASSIFICATION"
    assert "annex_i_classification" in result.dual_use.missing_facts


def test_verified_unlisted_candidate_is_monitor_only_never_clearance() -> None:
    subject, *_ = service()
    result = subject.screen(request(number="not-listed", code="0A999"))
    assert result.signal is Signal.GREEN_CANDIDATE
    assert result.business_action is BusinessAction.MONITOR
    assert result.automatic_clearance is False
    assert result.dual_use.status == "ENTRY_NOT_FOUND"
    assert result.dual_use.entry_content_hash is None
    assert result.dual_use.source_native_locator is None


def test_unusable_sanctions_identifier_is_fail_closed() -> None:
    subject, *_ = service(snapshot=fsf_snapshot(expired=True))
    result = subject.screen(request(code="0A999"))
    assert result.signal is Signal.RED
    assert result.business_action is BusinessAction.HOLD
    assert result.sanctions.identifier_statuses == ["REVIEW_REQUIRED"]
    assert result.sanctions.identifier_evidence[0].usable_for_exact_match is False


def test_exact_normalized_name_candidate_holds_without_echoing_queried_name() -> None:
    subject, *_ = service()
    result = subject.screen(
        OfficialScreeningRequest(
            party_names=[OfficialPartyName(name="  LISTED   EXAMPLE ")],
            goods=OfficialGoodsCandidate(
                annex_i_code="0A999",
                classification_verified=True,
                technical_specification_available=True,
            ),
        )
    )

    assert result.signal is Signal.RED
    assert result.business_action is BusinessAction.HOLD
    assert result.sanctions.identifier_query_count == 0
    assert result.sanctions.name_query_count == 1
    assert result.sanctions.name_statuses == ["CANDIDATE"]
    evidence = result.sanctions.name_evidence[0]
    assert evidence.eu_reference_number == "EU.1"
    assert evidence.alias_assertion_hash.startswith("sha256:")
    assert evidence.strong_alias is True
    assert "LISTED   EXAMPLE" not in result.model_dump_json()


def test_integrity_mismatch_and_untyped_dependencies_fail_closed() -> None:
    parsed = fsf_snapshot()
    mismatched_source = Source(
        RetrievedFsf(
            NOW,
            "sha256:" + "f" * 64,
            b"source bytes",
        )
    )
    subject = OfficialScreeningService(
        cast(Any, mismatched_source),
        cast(Any, Source(dual_list())),
        OfacSource(ofac_pair(retrieved_at=NOW)),
        fsf_parser=Parser(parsed),
        ofac_parser=cast(Any, OfacParser()),
    )
    assert (
        parsed.raw_content_hash
        != cast(RetrievedFsf, mismatched_source.value).content_hash
    )
    with pytest.raises(RuntimeError, match="integrity"):
        subject.screen(request())

    with pytest.raises(ValueError):
        subject.screen(cast(Any, "bad"))
    with pytest.raises(ValueError):
        OfficialScreeningService(
            cast(Any, object()),
            cast(Any, Source(dual_list())),
            OfacSource(ofac_pair(retrieved_at=NOW)),
            fsf_parser=Parser(fsf_snapshot()),
            ofac_parser=cast(Any, OfacParser()),
        )


def active_sources() -> ActiveOfficialSources:
    fsf = fsf_snapshot()
    dual = dual_list()
    ofac_sdn, ofac_consolidated = ofac_pair(retrieved_at=NOW)
    bundle_id = official_source_bundle_id(fsf, dual, ofac_sdn, ofac_consolidated)
    return ActiveOfficialSources(
        bundle_id=bundle_id,
        bundle_content_hash="sha256:" + bundle_id.removeprefix("official-bundle-"),
        fsf_retrieved_at=NOW,
        fsf_snapshot=fsf,
        dual_use_control_list=dual,
        ofac_sdn_snapshot=ofac_sdn,
        ofac_consolidated_snapshot=ofac_consolidated,
        activated_at=NOW,
    )


class ActiveRepository:
    def __init__(self, value: object) -> None:
        self.value = value

    def get_active(self) -> object:
        return self.value


def test_persisted_service_uses_fresh_atomic_bundle_and_exposes_identity() -> None:
    active = active_sources()
    result = PersistedOfficialScreeningService(
        cast(Any, ActiveRepository(active)), clock=lambda: NOW
    ).screen(request(code="0A999"))

    assert result.source_bundle_id == active.bundle_id
    assert result.source_bundle_content_hash == active.bundle_content_hash
    assert result.source_bundle_activated_at == NOW.isoformat()
    assert result.sanctions.source_retrieved_at == NOW.isoformat()


def test_persisted_service_refuses_missing_stale_future_or_invalid_sources() -> None:
    active = active_sources()
    for value, clock in (
        ("bad", lambda: NOW),
        (active, lambda: NOW + timedelta(days=3)),
        (active, lambda: NOW - timedelta(seconds=1)),
    ):
        subject = PersistedOfficialScreeningService(
            cast(Any, ActiveRepository(value)), clock=clock
        )
        with pytest.raises(RuntimeError):
            subject.screen(request())

    with pytest.raises(RuntimeError, match="clock"):
        PersistedOfficialScreeningService(
            cast(Any, ActiveRepository(active)),
            clock=lambda: datetime(2026, 8, 10),
        ).screen(request())


def test_engine_and_persisted_dependency_contracts_fail_closed() -> None:
    active = active_sources()
    engine = OfficialScreeningEngine()
    invalid_engine_calls: tuple[Callable[[], object], ...] = (
        lambda: engine.screen(
            cast(Any, "bad"),
            fsf_snapshot=active.fsf_snapshot,
            fsf_retrieved_at=NOW,
            dual_use_control_list=active.dual_use_control_list,
            ofac_sdn_snapshot=active.ofac_sdn_snapshot,
            ofac_consolidated_snapshot=active.ofac_consolidated_snapshot,
        ),
        lambda: engine.screen(
            request(),
            fsf_snapshot=cast(Any, "bad"),
            fsf_retrieved_at=NOW,
            dual_use_control_list=active.dual_use_control_list,
            ofac_sdn_snapshot=active.ofac_sdn_snapshot,
            ofac_consolidated_snapshot=active.ofac_consolidated_snapshot,
        ),
        lambda: engine.screen(
            request(),
            fsf_snapshot=active.fsf_snapshot,
            fsf_retrieved_at=NOW,
            dual_use_control_list=active.dual_use_control_list,
            ofac_sdn_snapshot=cast(Any, "bad"),
            ofac_consolidated_snapshot=active.ofac_consolidated_snapshot,
        ),
        lambda: engine.screen(
            request(),
            fsf_snapshot=active.fsf_snapshot,
            fsf_retrieved_at=NOW,
            dual_use_control_list=active.dual_use_control_list,
            ofac_sdn_snapshot=active.ofac_consolidated_snapshot,
            ofac_consolidated_snapshot=active.ofac_consolidated_snapshot,
        ),
        lambda: engine.screen(
            request(),
            fsf_snapshot=active.fsf_snapshot,
            fsf_retrieved_at=NOW,
            dual_use_control_list=active.dual_use_control_list,
            ofac_sdn_snapshot=active.ofac_sdn_snapshot,
            ofac_consolidated_snapshot=cast(Any, "bad"),
        ),
        lambda: engine.screen(
            request(),
            fsf_snapshot=active.fsf_snapshot,
            fsf_retrieved_at=NOW,
            dual_use_control_list=active.dual_use_control_list,
            ofac_sdn_snapshot=active.ofac_sdn_snapshot,
            ofac_consolidated_snapshot=active.ofac_sdn_snapshot,
        ),
        lambda: engine.screen(
            request(),
            fsf_snapshot=active.fsf_snapshot,
            fsf_retrieved_at=datetime(2026, 8, 10),
            dual_use_control_list=active.dual_use_control_list,
            ofac_sdn_snapshot=active.ofac_sdn_snapshot,
            ofac_consolidated_snapshot=active.ofac_consolidated_snapshot,
        ),
        lambda: engine.screen(
            request(),
            fsf_snapshot=active.fsf_snapshot,
            fsf_retrieved_at=NOW,
            dual_use_control_list=active.dual_use_control_list,
            ofac_sdn_snapshot=active.ofac_sdn_snapshot,
            ofac_consolidated_snapshot=active.ofac_consolidated_snapshot,
            source_bundle=replace(active, fsf_retrieved_at=NOW - timedelta(seconds=1)),
        ),
    )
    for action in invalid_engine_calls:
        with pytest.raises(ValueError):
            action()

    invalid_services: tuple[Callable[[], object], ...] = (
        lambda: PersistedOfficialScreeningService(cast(Any, object())),
        lambda: PersistedOfficialScreeningService(
            cast(Any, ActiveRepository(active)), clock=cast(Any, "bad")
        ),
        lambda: PersistedOfficialScreeningService(
            cast(Any, ActiveRepository(active)), maximum_source_age=timedelta(0)
        ),
        lambda: PersistedOfficialScreeningService(
            cast(Any, ActiveRepository(active)),
            maximum_source_age=timedelta(days=31),
        ),
        lambda: PersistedOfficialScreeningService(
            cast(Any, ActiveRepository(active)), maximum_source_age=cast(Any, "bad")
        ),
    )
    for action in invalid_services:
        with pytest.raises(ValueError):
            action()


@pytest.mark.parametrize(
    "payload",
    [
        {
            "party_identifiers": [],
            "goods": {},
        },
        {
            "party_identifiers": [{"type": "unknown", "value": "1"}],
            "goods": {},
        },
        {
            "party_identifiers": [{"type": "regnumber", "value": "1"}],
            "goods": {"annex_i_code": "bad"},
        },
    ],
)
def test_request_contract_rejects_unsafe_or_untyped_input(payload: object) -> None:
    with pytest.raises(ValidationError):
        OfficialScreeningRequest.model_validate(payload)
