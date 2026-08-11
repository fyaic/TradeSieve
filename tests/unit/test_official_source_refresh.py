"""Official source refresh application workflow tests."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

import pytest

from tradesieve.application.official_source_refresh import (
    OfficialSourceRefreshService,
)
from tradesieve.domain.eu_dual_use import (
    EuDualUseControlEntry,
    EuDualUseControlList,
)
from tradesieve.domain.eu_fsf import (
    EuFsfAlias,
    EuFsfEntity,
    EuFsfRegulation,
    EuFsfSnapshot,
    EuFsfSubjectType,
)
from tradesieve.domain.ofac_sls import OfacSlsListKind, OfacSnapshot
from tradesieve.domain.official_sources import (
    OfficialSourceBundle,
    OfficialSourceWriteOutcome,
)

from ..ofac_test_data import (
    OFAC_CONSOLIDATED_RAW,
    OFAC_SDN_RAW,
    ofac_pair,
)

NOW = datetime(2026, 8, 10, 6, tzinfo=UTC)
FSF_RAW = b"fsf raw"
DUAL_RAW = b"dual raw"


def fsf_snapshot() -> EuFsfSnapshot:
    return EuFsfSnapshot(
        generation_date=NOW - timedelta(hours=1),
        global_file_id="184961",
        raw_content_hash=f"sha256:{hashlib.sha256(FSF_RAW).hexdigest()}",
        raw_byte_length=len(FSF_RAW),
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
                    native_locator="/entity/regulation",
                ),
                aliases=(
                    EuFsfAlias.create(
                        logical_id="1",
                        whole_name="Listed Example",
                        strong=True,
                        language="EN",
                        native_locator="/entity/alias/1",
                    ),
                ),
                identifiers=(),
            ),
        ),
    )


def dual_list() -> EuDualUseControlList:
    return EuDualUseControlList(
        retrieved_at=NOW - timedelta(minutes=2),
        source_archive_hash=f"sha256:{hashlib.sha256(DUAL_RAW).hexdigest()}",
        source_archive_bytes=len(DUAL_RAW),
        formex_document_hash="sha256:" + "c" * 64,
        entries=tuple(
            EuDualUseControlEntry(
                code=f"{category}A{index:03d}",
                text=f"Entry {category}-{index}",
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
    content_type: str
    content: bytes


@dataclass(frozen=True)
class RetrievedDual:
    retrieved_at: datetime
    content_hash: str
    content_type: str
    content: bytes
    control_list: EuDualUseControlList


@dataclass(frozen=True)
class RetrievedOfac:
    list_kind: OfacSlsListKind
    retrieved_at: datetime
    content_hash: str
    content_type: str
    content: bytes
    snapshot: OfacSnapshot


class Source:
    def __init__(self, value: object) -> None:
        self.value = value

    def retrieve(self) -> object:
        return self.value

    def retrieve_source(self) -> object:
        return self.value


class Parser:
    def __init__(self, value: EuFsfSnapshot) -> None:
        self.value = value

    def parse(self, _content: bytes) -> EuFsfSnapshot:
        return self.value


class OfacSource:
    def __init__(self, values: tuple[RetrievedOfac, RetrievedOfac]) -> None:
        self.values = {item.list_kind: item for item in values}

    def retrieve(self, list_kind: OfacSlsListKind) -> RetrievedOfac:
        return self.values[list_kind]


class OfacParser:
    def parse(self, source: RetrievedOfac) -> OfacSnapshot:
        return source.snapshot


class MappedOfacParser:
    def __init__(self, values: dict[OfacSlsListKind, OfacSnapshot]) -> None:
        self.values = values

    def parse(self, source: RetrievedOfac) -> OfacSnapshot:
        return self.values[source.list_kind]


def build_ofac_source() -> OfacSource:
    ofac_sdn, ofac_consolidated = ofac_pair(retrieved_at=NOW - timedelta(minutes=1))
    return OfacSource(
        (
            RetrievedOfac(
                OfacSlsListKind.SDN,
                ofac_sdn.retrieved_at,
                ofac_sdn.raw_content_hash,
                "text/xml",
                OFAC_SDN_RAW,
                ofac_sdn,
            ),
            RetrievedOfac(
                OfacSlsListKind.CONSOLIDATED,
                ofac_consolidated.retrieved_at,
                ofac_consolidated.raw_content_hash,
                "text/xml",
                OFAC_CONSOLIDATED_RAW,
                ofac_consolidated,
            ),
        )
    )


class Repository:
    def __init__(self, outcome: object = OfficialSourceWriteOutcome.APPLIED) -> None:
        self.outcome = outcome
        self.bundles: list[OfficialSourceBundle] = []

    def activate(self, bundle: OfficialSourceBundle) -> object:
        self.bundles.append(bundle)
        return self.outcome


def build_service(
    *,
    fsf: RetrievedFsf | None = None,
    dual: RetrievedDual | None = None,
    repository: Repository | None = None,
    ofac_source: OfacSource | None = None,
    ofac_parser: object | None = None,
) -> tuple[OfficialSourceRefreshService, Repository]:
    parsed = fsf_snapshot()
    selected_fsf = fsf or RetrievedFsf(
        NOW - timedelta(minutes=3),
        parsed.raw_content_hash,
        "application/xml; charset=utf-8",
        FSF_RAW,
    )
    listing = dual_list()
    selected_dual = dual or RetrievedDual(
        listing.retrieved_at,
        listing.source_archive_hash,
        "application/zip",
        DUAL_RAW,
        listing,
    )
    selected_repository = repository or Repository()
    selected_ofac_source = ofac_source or build_ofac_source()
    selected_ofac_parser = ofac_parser or OfacParser()
    return (
        OfficialSourceRefreshService(
            cast(Any, Source(selected_fsf)),
            cast(Any, Source(selected_dual)),
            selected_ofac_source,
            cast(Any, selected_repository),
            fsf_parser=Parser(parsed),
            ofac_parser=cast(Any, selected_ofac_parser),
            clock=lambda: NOW,
        ),
        selected_repository,
    )


def test_refresh_activates_one_verified_bundle_and_returns_safe_counts() -> None:
    service, repository = build_service()
    result = service.refresh()

    assert result.outcome is OfficialSourceWriteOutcome.APPLIED
    assert result.bundle_id.startswith("official-bundle-")
    assert result.fsf_entity_count == 1
    assert result.fsf_alias_count == 1
    assert result.fsf_identifier_count == 0
    assert result.dual_use_entry_count == 300
    assert result.ofac_sdn_entry_count == 1
    assert result.ofac_consolidated_entry_count == 1
    assert result.activated_at == NOW.isoformat()
    assert len(repository.bundles) == 1
    assert repository.bundles[0].fsf_content_type == "application/xml"


def test_refresh_rejects_integrity_mismatch_and_invalid_persistence_outcome() -> None:
    parsed = fsf_snapshot()
    mismatched_fsf = RetrievedFsf(
        NOW - timedelta(minutes=3),
        "sha256:" + "f" * 64,
        "application/xml",
        FSF_RAW,
    )
    service, _ = build_service(fsf=mismatched_fsf)
    with pytest.raises(RuntimeError, match="FSF"):
        service.refresh()

    listing = dual_list()
    mismatched_dual = RetrievedDual(
        listing.retrieved_at,
        "sha256:" + "f" * 64,
        "application/zip",
        DUAL_RAW,
        listing,
    )
    service, _ = build_service(dual=mismatched_dual)
    with pytest.raises(RuntimeError, match="dual-use"):
        service.refresh()

    service, _ = build_service(repository=Repository("bad"))
    with pytest.raises(RuntimeError, match="outcome"):
        service.refresh()
    assert parsed.raw_content_hash != mismatched_fsf.content_hash


def test_refresh_rejects_each_ofac_kind_and_raw_hash_mismatch() -> None:
    source = build_ofac_source()
    sdn = source.values[OfacSlsListKind.SDN].snapshot
    consolidated = source.values[OfacSlsListKind.CONSOLIDATED].snapshot
    wrong_sdn_hash = replace(sdn, raw_content_hash="sha256:" + "f" * 64)
    wrong_consolidated_hash = replace(
        consolidated, raw_content_hash="sha256:" + "f" * 64
    )
    mappings = (
        {
            OfacSlsListKind.SDN: consolidated,
            OfacSlsListKind.CONSOLIDATED: consolidated,
        },
        {
            OfacSlsListKind.SDN: wrong_sdn_hash,
            OfacSlsListKind.CONSOLIDATED: consolidated,
        },
        {
            OfacSlsListKind.SDN: sdn,
            OfacSlsListKind.CONSOLIDATED: sdn,
        },
        {
            OfacSlsListKind.SDN: sdn,
            OfacSlsListKind.CONSOLIDATED: wrong_consolidated_hash,
        },
    )
    for mapping in mappings:
        service, _ = build_service(
            ofac_source=source,
            ofac_parser=MappedOfacParser(mapping),
        )
        with pytest.raises(RuntimeError, match="OFAC"):
            service.refresh()


def test_refresh_dependency_contracts_and_bundle_time_fail_closed() -> None:
    for kwargs in (
        {"fsf_source": object()},
        {"dual_use_source": object()},
        {"ofac_source": object()},
        {"repository": object()},
        {"fsf_parser": object()},
        {"ofac_parser": object()},
        {"clock": cast(Any, "bad")},
    ):
        values: dict[str, Any] = {
            "fsf_source": Source(object()),
            "dual_use_source": Source(object()),
            "ofac_source": build_ofac_source(),
            "repository": Repository(),
            "fsf_parser": Parser(fsf_snapshot()),
            "ofac_parser": OfacParser(),
            "clock": lambda: NOW,
        }
        values.update(kwargs)
        with pytest.raises(ValueError):
            OfficialSourceRefreshService(**values)

    service, _ = build_service()
    service._clock = lambda: NOW - timedelta(days=1)
    with pytest.raises(ValueError, match="activation"):
        service.refresh()


def test_refresh_accepts_idempotent_repository_outcome() -> None:
    service, _ = build_service(
        repository=Repository(OfficialSourceWriteOutcome.IDEMPOTENT)
    )
    assert service.refresh().outcome is OfficialSourceWriteOutcome.IDEMPOTENT
