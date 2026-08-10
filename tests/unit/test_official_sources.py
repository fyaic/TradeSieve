"""Official source bundle identity and raw/projection integrity tests."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

import pytest

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
from tradesieve.domain.official_sources import (
    ActiveOfficialSources,
    OfficialSourceBundle,
    official_source_bundle_id,
)

NOW = datetime(2026, 8, 10, 5, tzinfo=UTC)
FSF_RAW = b"official fsf bytes"
DUAL_RAW = b"official dual-use bytes"


def fsf_snapshot() -> EuFsfSnapshot:
    return EuFsfSnapshot(
        generation_date=NOW - timedelta(hours=12),
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


def bundle() -> OfficialSourceBundle:
    return OfficialSourceBundle(
        fsf_raw_content=FSF_RAW,
        fsf_content_type="application/xml",
        fsf_retrieved_at=NOW - timedelta(minutes=3),
        fsf_snapshot=fsf_snapshot(),
        dual_use_raw_content=DUAL_RAW,
        dual_use_content_type="application/zip",
        dual_use_control_list=dual_list(),
        activated_at=NOW,
    )


def test_bundle_binds_raw_bytes_projections_and_active_identity() -> None:
    value = bundle()
    assert value.bundle_id.startswith("official-bundle-")
    assert value.content_hash.startswith("sha256:")
    assert value.bundle_id == official_source_bundle_id(
        value.fsf_snapshot, value.dual_use_control_list
    )
    active = ActiveOfficialSources(
        bundle_id=value.bundle_id,
        bundle_content_hash=value.content_hash,
        fsf_retrieved_at=value.fsf_retrieved_at,
        fsf_snapshot=value.fsf_snapshot,
        dual_use_control_list=value.dual_use_control_list,
        activated_at=value.activated_at,
    )
    assert active.bundle_id == value.bundle_id


def test_bundle_and_active_projection_fail_closed_on_corruption() -> None:
    value = bundle()
    active = ActiveOfficialSources(
        value.bundle_id,
        value.content_hash,
        value.fsf_retrieved_at,
        value.fsf_snapshot,
        value.dual_use_control_list,
        value.activated_at,
    )
    invalid = (
        lambda: official_source_bundle_id(
            cast(Any, "bad"), value.dual_use_control_list
        ),
        lambda: official_source_bundle_id(value.fsf_snapshot, cast(Any, "bad")),
        lambda: replace(value, fsf_snapshot=cast(Any, "bad")),
        lambda: replace(value, dual_use_control_list=cast(Any, "bad")),
        lambda: replace(value, fsf_retrieved_at=cast(Any, datetime(2026, 8, 10))),
        lambda: replace(value, activated_at=cast(Any, datetime(2026, 8, 10))),
        lambda: replace(value, fsf_content_type=""),
        lambda: replace(value, dual_use_content_type="x" * 129),
        lambda: replace(value, fsf_raw_content=cast(Any, "bad")),
        lambda: replace(value, fsf_raw_content=b"wrong"),
        lambda: replace(value, dual_use_raw_content=cast(Any, "bad")),
        lambda: replace(value, dual_use_raw_content=b"wrong"),
        lambda: replace(
            value, activated_at=value.fsf_retrieved_at - timedelta(seconds=1)
        ),
        lambda: replace(
            value,
            activated_at=value.dual_use_control_list.retrieved_at
            - timedelta(seconds=1),
            fsf_retrieved_at=value.dual_use_control_list.retrieved_at
            - timedelta(seconds=2),
        ),
        lambda: replace(
            value,
            fsf_retrieved_at=NOW - timedelta(minutes=1),
            activated_at=NOW - timedelta(minutes=1, seconds=1),
        ),
        lambda: replace(active, fsf_snapshot=cast(Any, "bad")),
        lambda: replace(active, bundle_content_hash="bad"),
        lambda: replace(active, bundle_id="bad"),
        lambda: replace(
            active, fsf_retrieved_at=active.activated_at + timedelta(seconds=1)
        ),
        lambda: replace(
            active,
            activated_at=active.dual_use_control_list.retrieved_at
            - timedelta(seconds=1),
        ),
    )
    for action in invalid:
        with pytest.raises(ValueError):
            action()
