"""Canonical live official screening application-path tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, cast

import pytest
from pydantic import ValidationError

from tradesieve.application.contracts import BusinessAction, Signal
from tradesieve.application.official_screening import (
    OfficialGoodsCandidate,
    OfficialPartyIdentifier,
    OfficialScreeningRequest,
    OfficialScreeningService,
)
from tradesieve.domain.eu_dual_use import (
    EuDualUseControlEntry,
    EuDualUseControlList,
)
from tradesieve.domain.eu_fsf import (
    EuFsfAlias,
    EuFsfEntity,
    EuFsfIdentifier,
    EuFsfRegulation,
    EuFsfSnapshot,
    EuFsfSubjectType,
)

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
) -> tuple[OfficialScreeningService, Source, Source, Parser]:
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
    return (
        OfficialScreeningService(
            cast(Any, fsf_source),
            cast(Any, dual_source),
            fsf_parser=parser,
        ),
        fsf_source,
        dual_source,
        parser,
    )


def test_exact_sanctions_and_annex_entry_hold_with_versioned_evidence() -> None:
    subject, fsf_source, dual_source, parser = service()
    result = subject.screen(request())

    assert result.signal is Signal.RED
    assert result.business_action is BusinessAction.HOLD
    assert result.automatic_clearance is False
    assert result.sanctions.statuses == ["MATCH"]
    assert result.sanctions.evidence[0].eu_reference_number == "EU.1"
    assert result.sanctions.evidence[0].identifier_assertion_hash.startswith("sha256:")
    assert "36-3823186" not in result.model_dump_json()
    assert result.dual_use.status == "CONTROL_ENTRY_FOUND"
    assert result.dual_use.requested_code == "0A000"
    assert result.dual_use.entry_content_hash is not None
    assert result.dual_use.source_native_locator == "/ANNEX/NP[NO.P='0A000']"
    assert len(result.caveats) == 5
    assert fsf_source.calls == dual_source.calls == 1
    assert parser.contents == [b"source bytes"]


def test_missing_goods_classification_requests_evidence_after_no_match() -> None:
    subject, *_ = service()
    result = subject.screen(
        request(number="not-listed", code=None, verified=False, specification=False)
    )
    assert result.signal is Signal.YELLOW
    assert result.business_action is BusinessAction.REQUEST_EVIDENCE
    assert result.sanctions.statuses == ["NO_MATCH"]
    assert result.sanctions.evidence == []
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
    assert result.sanctions.statuses == ["REVIEW_REQUIRED"]
    assert result.sanctions.evidence[0].usable_for_exact_match is False


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
        fsf_parser=Parser(parsed),
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
            fsf_parser=Parser(fsf_snapshot()),
        )


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
