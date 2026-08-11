"""Synthetic OFAC source projections shared by official-source tests."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime

from tradesieve.domain.ofac_sls import (
    OfacAddress,
    OfacAlias,
    OfacEntry,
    OfacFact,
    OfacFactKind,
    OfacIdentifier,
    OfacSlsListKind,
    OfacSnapshot,
    OfacSubjectType,
    OfacVesselInfo,
)

OFAC_SDN_RAW = b"synthetic official OFAC SDN bytes"
OFAC_CONSOLIDATED_RAW = b"synthetic official OFAC consolidated bytes"


def ofac_snapshot(
    list_kind: OfacSlsListKind,
    *,
    retrieved_at: datetime | None = None,
) -> OfacSnapshot:
    observed_at = retrieved_at or datetime(2026, 8, 10, 6, 55, tzinfo=UTC)
    raw = OFAC_SDN_RAW if list_kind is OfacSlsListKind.SDN else OFAC_CONSOLIDATED_RAW
    uid = "100" if list_kind is OfacSlsListKind.SDN else "200"
    entry = OfacEntry.create(
        uid=uid,
        list_kind=list_kind,
        first_name="OFAC",
        last_name="LISTED EXAMPLE",
        title="Synthetic carrier",
        subject_type=OfacSubjectType.VESSEL,
        remarks="Synthetic test source fact",
        programs=("RUSSIA-EO14024",),
        aliases=(
            OfacAlias.create(
                uid=str(int(uid) + 1),
                alias_type="a.k.a.",
                category="strong",
                whole_name="Listed Example",
                native_locator=f"/sdnList/sdnEntry[uid='{uid}']/akaList/aka[1]",
            ),
        ),
        addresses=(
            OfacAddress(
                uid=str(int(uid) + 2),
                address_lines=("1 Synthetic Port Road", "Dock 2"),
                city="Shanghai",
                state_or_province="Shanghai",
                postal_code="200000",
                country="China",
                region="Asia",
                native_locator=(
                    f"/sdnList/sdnEntry[uid='{uid}']/addressList/address[1]"
                ),
            ),
        ),
        identifiers=(
            OfacIdentifier.create(
                uid=str(int(uid) + 3),
                type_code="Registration Number",
                number="36-3823186",
                country="United States",
                issue_date="2020",
                expiration_date="2030",
                native_locator=f"/sdnList/sdnEntry[uid='{uid}']/idList/id[1]",
            ),
        ),
        facts=(
            OfacFact(
                kind=OfacFactKind.NATIONALITY,
                uid=str(int(uid) + 4),
                value="Russia",
                main_entry=True,
                native_locator=(
                    f"/sdnList/sdnEntry[uid='{uid}']/nationalityList/nationality[1]"
                ),
            ),
        ),
        vessel_info=OfacVesselInfo(
            call_sign="SYNTH",
            vessel_type="Cargo",
            vessel_flag="Russia",
            vessel_owner="Synthetic Owner",
            tonnage=10,
            gross_registered_tonnage=20,
            native_locator=f"/sdnList/sdnEntry[uid='{uid}']/vesselInfo",
        ),
    )
    return OfacSnapshot(
        list_kind=list_kind,
        publish_date=date(2026, 8, 7),
        declared_record_count=1,
        retrieved_at=observed_at,
        source_last_modified=datetime(2026, 8, 7, 18, 36, 56, tzinfo=UTC),
        raw_content_hash=f"sha256:{hashlib.sha256(raw).hexdigest()}",
        raw_byte_length=len(raw),
        entries=(entry,),
    )


def ofac_pair(
    *, retrieved_at: datetime | None = None
) -> tuple[OfacSnapshot, OfacSnapshot]:
    return (
        ofac_snapshot(OfacSlsListKind.SDN, retrieved_at=retrieved_at),
        ofac_snapshot(OfacSlsListKind.CONSOLIDATED, retrieved_at=retrieved_at),
    )
