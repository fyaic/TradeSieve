"""Immutable OFAC SLS evidence, replayable diffs, and candidate indexes.

OFAC list membership is source evidence.  It is not ownership/control evidence and
does not itself authorize a legal or business disposition.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final

OFAC_SLS_HOST: Final = "sanctionslistservice.ofac.treas.gov"
OFAC_SLS_NAMESPACE: Final = (
    "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/XML"
)
OFAC_SLS_SCHEMA_ID: Final = "ofac-sls-legacy-xml"
OFAC_SLS_PARSER_ID: Final = "ofac-sls-legacy-xml-v1"
OFAC_SLS_PARSER_VERSION: Final = "1.0.0"

MAX_OFAC_RAW_BYTES: Final = 64 * 1024 * 1024
MAX_OFAC_ENTRIES: Final = 50_000
MAX_OFAC_PROGRAMS_PER_ENTRY: Final = 128
MAX_OFAC_ALIASES_PER_ENTRY: Final = 512
MAX_OFAC_ADDRESSES_PER_ENTRY: Final = 512
MAX_OFAC_IDENTIFIERS_PER_ENTRY: Final = 512
MAX_OFAC_FACTS_PER_ENTRY: Final = 256
MAX_OFAC_STRING_BYTES: Final = 4096
MAX_OFAC_LOCATOR_BYTES: Final = 512

SAFE_UID: Final = re.compile(r"^[1-9][0-9]{0,18}$")
CONTENT_HASH: Final = re.compile(r"^sha256:[a-f0-9]{64}$")

# Exact candidate retrieval is deliberately finite.  The XML contains descriptive
# idType values such as Gender and Organization Type that must never be treated as
# strong identifiers merely because they occur in idList.
OFAC_EXACT_IDENTIFIER_TYPES: Final = frozenset(
    {
        "Aircraft Manufacturer's Serial Number (MSN)",
        "Aircraft Serial Identification",
        "Aircraft Tail Number",
        "British National Overseas Passport",
        "Business Registration Number",
        "Company Number",
        "Diplomatic Passport",
        "MMSI",
        "National Foreign ID Number",
        "National ID No.",
        "Passport",
        "Registration ID",
        "Registration Number",
        "SWIFT/BIC",
        "Stateless Person Passport",
        "Tax ID No.",
        "Tazkira National ID Card",
        "UK Company Number",
        "Unified Social Credit Code (USCC)",
        "United Social Credit Code Certificate (USCCC)",
        "Vessel Registration Identification",
    }
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


def _require_bounded(value: str, field: str, maximum: int) -> None:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum:
        raise ValueError(f"{field} must be a bounded non-empty string")


def _require_optional_bounded(value: str | None, field: str, maximum: int) -> None:
    if value is not None:
        _require_bounded(value, field, maximum)


def _require_uid(value: str, field: str) -> None:
    if SAFE_UID.fullmatch(value) is None:
        raise ValueError(f"{field} must be a positive numeric UID")


def normalize_ofac_name(value: str) -> str:
    """Return an exact normalized-name candidate key, never a disposition."""

    _require_bounded(value, "name", MAX_OFAC_STRING_BYTES)
    result = " ".join(unicodedata.normalize("NFKC", value).split()).casefold()
    _require_bounded(result, "normalized name", MAX_OFAC_STRING_BYTES)
    return result


def normalize_ofac_identifier(type_code: str, value: str) -> str:
    """Normalize formatting for a finite strong-identifier inventory."""

    if type_code not in OFAC_EXACT_IDENTIFIER_TYPES:
        raise ValueError("identifier type is outside the finite exact inventory")
    _require_bounded(value, "identifier", MAX_OFAC_STRING_BYTES)
    normalized = unicodedata.normalize("NFKC", value).upper()
    normalized = "".join(
        character
        for character in normalized
        if not character.isspace()
        and unicodedata.category(character) not in {"Pd", "Pc"}
    )
    _require_bounded(normalized, "normalized identifier", 512)
    return normalized


class OfacSlsListKind(StrEnum):
    SDN = "SDN"
    CONSOLIDATED = "CONSOLIDATED"


class OfacSubjectType(StrEnum):
    AIRCRAFT = "Aircraft"
    ENTITY = "Entity"
    INDIVIDUAL = "Individual"
    VESSEL = "Vessel"


class OfacFactKind(StrEnum):
    NATIONALITY = "nationality"
    CITIZENSHIP = "citizenship"
    DATE_OF_BIRTH = "date_of_birth"
    PLACE_OF_BIRTH = "place_of_birth"


class OfacCandidateStatus(StrEnum):
    NO_CANDIDATE = "NO_CANDIDATE"
    CANDIDATE = "CANDIDATE"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True, slots=True)
class OfacAlias:
    uid: str
    alias_type: str
    category: str
    whole_name: str
    normalized_name: str
    native_locator: str

    @classmethod
    def create(
        cls,
        *,
        uid: str,
        alias_type: str,
        category: str,
        whole_name: str,
        native_locator: str,
    ) -> OfacAlias:
        return cls(
            uid=uid,
            alias_type=alias_type,
            category=category,
            whole_name=whole_name,
            normalized_name=normalize_ofac_name(whole_name),
            native_locator=native_locator,
        )

    def __post_init__(self) -> None:
        _require_uid(self.uid, "alias uid")
        _require_bounded(self.alias_type, "alias type", 64)
        if self.category not in {"strong", "weak"}:
            raise ValueError("alias category must be strong or weak")
        _require_bounded(self.whole_name, "alias name", MAX_OFAC_STRING_BYTES)
        if self.normalized_name != normalize_ofac_name(self.whole_name):
            raise ValueError("alias normalized name is not canonical")
        _require_bounded(self.native_locator, "alias locator", MAX_OFAC_LOCATOR_BYTES)

    def canonical_content(self) -> dict[str, object]:
        return {
            "alias_type": self.alias_type,
            "category": self.category,
            "native_locator": self.native_locator,
            "normalized_name": self.normalized_name,
            "uid": self.uid,
            "whole_name": self.whole_name,
        }


@dataclass(frozen=True, slots=True)
class OfacAddress:
    uid: str
    address_lines: tuple[str, ...]
    city: str | None
    state_or_province: str | None
    postal_code: str | None
    country: str | None
    region: str | None
    native_locator: str

    def __post_init__(self) -> None:
        _require_uid(self.uid, "address uid")
        if (
            not isinstance(self.address_lines, tuple)
            or len(self.address_lines) > 3
            or any(
                not isinstance(item, str)
                or not item
                or len(item.encode("utf-8")) > MAX_OFAC_STRING_BYTES
                for item in self.address_lines
            )
        ):
            raise ValueError("address lines must be a bounded typed tuple")
        for value, field in (
            (self.city, "city"),
            (self.state_or_province, "state_or_province"),
            (self.postal_code, "postal_code"),
            (self.country, "country"),
            (self.region, "region"),
        ):
            _require_optional_bounded(value, field, MAX_OFAC_STRING_BYTES)
        _require_bounded(self.native_locator, "address locator", MAX_OFAC_LOCATOR_BYTES)

    def canonical_content(self) -> dict[str, object]:
        return {
            "address_lines": list(self.address_lines),
            "city": self.city,
            "country": self.country,
            "native_locator": self.native_locator,
            "postal_code": self.postal_code,
            "region": self.region,
            "state_or_province": self.state_or_province,
            "uid": self.uid,
        }


@dataclass(frozen=True, slots=True)
class OfacIdentifier:
    uid: str
    type_code: str
    number: str
    normalized_number: str | None
    country: str | None
    issue_date: str | None
    expiration_date: str | None
    native_locator: str

    @classmethod
    def create(
        cls,
        *,
        uid: str,
        type_code: str,
        number: str,
        country: str | None,
        issue_date: str | None,
        expiration_date: str | None,
        native_locator: str,
    ) -> OfacIdentifier:
        normalized = (
            normalize_ofac_identifier(type_code, number)
            if type_code in OFAC_EXACT_IDENTIFIER_TYPES
            else None
        )
        return cls(
            uid=uid,
            type_code=type_code,
            number=number,
            normalized_number=normalized,
            country=country,
            issue_date=issue_date,
            expiration_date=expiration_date,
            native_locator=native_locator,
        )

    def __post_init__(self) -> None:
        _require_uid(self.uid, "identifier uid")
        _require_bounded(self.type_code, "identifier type", 256)
        _require_bounded(self.number, "identifier number", MAX_OFAC_STRING_BYTES)
        expected = (
            normalize_ofac_identifier(self.type_code, self.number)
            if self.type_code in OFAC_EXACT_IDENTIFIER_TYPES
            else None
        )
        if self.normalized_number != expected:
            raise ValueError("identifier normalized number is not canonical")
        for value, field in (
            (self.country, "identifier country"),
            (self.issue_date, "identifier issue date"),
            (self.expiration_date, "identifier expiration date"),
        ):
            _require_optional_bounded(value, field, MAX_OFAC_STRING_BYTES)
        _require_bounded(
            self.native_locator, "identifier locator", MAX_OFAC_LOCATOR_BYTES
        )

    @property
    def usable_for_exact_candidate(self) -> bool:
        return self.normalized_number is not None

    def canonical_content(self) -> dict[str, object]:
        return {
            "country": self.country,
            "expiration_date": self.expiration_date,
            "issue_date": self.issue_date,
            "native_locator": self.native_locator,
            "normalized_number": self.normalized_number,
            "number": self.number,
            "type_code": self.type_code,
            "uid": self.uid,
        }


@dataclass(frozen=True, slots=True)
class OfacFact:
    kind: OfacFactKind
    uid: str
    value: str
    main_entry: bool
    native_locator: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, OfacFactKind):
            raise ValueError("fact kind must be typed")
        _require_uid(self.uid, "fact uid")
        _require_bounded(self.value, "fact value", MAX_OFAC_STRING_BYTES)
        if not isinstance(self.main_entry, bool):
            raise ValueError("fact main_entry must be boolean")
        _require_bounded(self.native_locator, "fact locator", MAX_OFAC_LOCATOR_BYTES)

    def canonical_content(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "main_entry": self.main_entry,
            "native_locator": self.native_locator,
            "uid": self.uid,
            "value": self.value,
        }


@dataclass(frozen=True, slots=True)
class OfacVesselInfo:
    call_sign: str | None
    vessel_type: str | None
    vessel_flag: str | None
    vessel_owner: str | None
    tonnage: int | None
    gross_registered_tonnage: int | None
    native_locator: str

    def __post_init__(self) -> None:
        for value, field in (
            (self.call_sign, "call_sign"),
            (self.vessel_type, "vessel_type"),
            (self.vessel_flag, "vessel_flag"),
            (self.vessel_owner, "vessel_owner"),
        ):
            _require_optional_bounded(value, field, MAX_OFAC_STRING_BYTES)
        for numeric_value in (self.tonnage, self.gross_registered_tonnage):
            if numeric_value is not None and (
                isinstance(numeric_value, bool)
                or not isinstance(numeric_value, int)
                or numeric_value < 0
            ):
                raise ValueError("vessel tonnage must be a non-negative integer")
        _require_bounded(self.native_locator, "vessel locator", MAX_OFAC_LOCATOR_BYTES)

    def canonical_content(self) -> dict[str, object]:
        return {
            "call_sign": self.call_sign,
            "gross_registered_tonnage": self.gross_registered_tonnage,
            "native_locator": self.native_locator,
            "tonnage": self.tonnage,
            "vessel_flag": self.vessel_flag,
            "vessel_owner": self.vessel_owner,
            "vessel_type": self.vessel_type,
        }


def _validate_unique_uid_items(
    values: tuple[OfacAlias | OfacAddress | OfacIdentifier | OfacFact, ...],
    field: str,
) -> None:
    uids = [item.uid for item in values]
    if uids != sorted(uids, key=int) or len(set(uids)) != len(uids):
        raise ValueError(f"{field} must have unique numeric UID order")


@dataclass(frozen=True, slots=True)
class OfacEntry:
    uid: str
    list_kind: OfacSlsListKind
    first_name: str | None
    last_name: str
    whole_name: str
    normalized_name: str
    title: str | None
    subject_type: OfacSubjectType
    remarks: str | None
    programs: tuple[str, ...]
    aliases: tuple[OfacAlias, ...]
    addresses: tuple[OfacAddress, ...]
    identifiers: tuple[OfacIdentifier, ...]
    facts: tuple[OfacFact, ...]
    vessel_info: OfacVesselInfo | None

    @classmethod
    def create(
        cls,
        *,
        uid: str,
        list_kind: OfacSlsListKind,
        first_name: str | None,
        last_name: str,
        title: str | None,
        subject_type: OfacSubjectType,
        remarks: str | None,
        programs: tuple[str, ...],
        aliases: tuple[OfacAlias, ...],
        addresses: tuple[OfacAddress, ...],
        identifiers: tuple[OfacIdentifier, ...],
        facts: tuple[OfacFact, ...],
        vessel_info: OfacVesselInfo | None,
    ) -> OfacEntry:
        whole_name = f"{first_name} {last_name}" if first_name else last_name
        return cls(
            uid=uid,
            list_kind=list_kind,
            first_name=first_name,
            last_name=last_name,
            whole_name=whole_name,
            normalized_name=normalize_ofac_name(whole_name),
            title=title,
            subject_type=subject_type,
            remarks=remarks,
            programs=programs,
            aliases=aliases,
            addresses=addresses,
            identifiers=identifiers,
            facts=facts,
            vessel_info=vessel_info,
        )

    def __post_init__(self) -> None:
        _require_uid(self.uid, "entry uid")
        if not isinstance(self.list_kind, OfacSlsListKind):
            raise ValueError("entry list kind must be typed")
        _require_optional_bounded(
            self.first_name, "entry first name", MAX_OFAC_STRING_BYTES
        )
        _require_bounded(self.last_name, "entry last name", MAX_OFAC_STRING_BYTES)
        expected_name = (
            f"{self.first_name} {self.last_name}" if self.first_name else self.last_name
        )
        if self.whole_name != expected_name:
            raise ValueError("entry whole name is not canonical")
        if self.normalized_name != normalize_ofac_name(self.whole_name):
            raise ValueError("entry normalized name is not canonical")
        _require_optional_bounded(self.title, "entry title", MAX_OFAC_STRING_BYTES)
        if not isinstance(self.subject_type, OfacSubjectType):
            raise ValueError("entry subject type must be typed")
        _require_optional_bounded(self.remarks, "entry remarks", MAX_OFAC_STRING_BYTES)
        if (
            not isinstance(self.programs, tuple)
            or not 1 <= len(self.programs) <= MAX_OFAC_PROGRAMS_PER_ENTRY
            or any(
                not isinstance(item, str) or not item or len(item.encode("utf-8")) > 256
                for item in self.programs
            )
            or self.programs != tuple(sorted(set(self.programs)))
        ):
            raise ValueError("programs must be a sorted unique bounded tuple")
        collection_rules = (
            (self.aliases, OfacAlias, MAX_OFAC_ALIASES_PER_ENTRY, "aliases"),
            (
                self.addresses,
                OfacAddress,
                MAX_OFAC_ADDRESSES_PER_ENTRY,
                "addresses",
            ),
            (
                self.identifiers,
                OfacIdentifier,
                MAX_OFAC_IDENTIFIERS_PER_ENTRY,
                "identifiers",
            ),
            (self.facts, OfacFact, MAX_OFAC_FACTS_PER_ENTRY, "facts"),
        )
        for values, expected_type, maximum, field in collection_rules:
            if (
                not isinstance(values, tuple)
                or len(values) > maximum
                or any(not isinstance(item, expected_type) for item in values)
            ):
                raise ValueError(f"{field} must be a bounded typed tuple")
            _validate_unique_uid_items(values, field)
        if self.vessel_info is not None and not isinstance(
            self.vessel_info, OfacVesselInfo
        ):
            raise ValueError("vessel_info must be typed or absent")
        if self.subject_type is not OfacSubjectType.VESSEL and self.vessel_info:
            raise ValueError("only a vessel entry may carry vessel_info")

    @property
    def entry_hash(self) -> str:
        return _sha256(self.canonical_content())

    def canonical_content(self) -> dict[str, object]:
        return {
            "addresses": [item.canonical_content() for item in self.addresses],
            "aliases": [item.canonical_content() for item in self.aliases],
            "facts": [item.canonical_content() for item in self.facts],
            "first_name": self.first_name,
            "identifiers": [item.canonical_content() for item in self.identifiers],
            "last_name": self.last_name,
            "list_kind": self.list_kind.value,
            "normalized_name": self.normalized_name,
            "programs": list(self.programs),
            "remarks": self.remarks,
            "subject_type": self.subject_type.value,
            "title": self.title,
            "uid": self.uid,
            "vessel_info": (
                self.vessel_info.canonical_content()
                if self.vessel_info is not None
                else None
            ),
            "whole_name": self.whole_name,
        }


@dataclass(frozen=True, slots=True)
class OfacSnapshot:
    list_kind: OfacSlsListKind
    publish_date: date
    declared_record_count: int
    retrieved_at: datetime
    source_last_modified: datetime | None
    raw_content_hash: str
    raw_byte_length: int
    entries: tuple[OfacEntry, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.list_kind, OfacSlsListKind):
            raise ValueError("snapshot list kind must be typed")
        if not isinstance(self.publish_date, date):
            raise ValueError("publish_date must be typed")
        if (
            isinstance(self.declared_record_count, bool)
            or not isinstance(self.declared_record_count, int)
            or self.declared_record_count < 1
        ):
            raise ValueError("declared_record_count must be positive")
        if (
            not isinstance(self.retrieved_at, datetime)
            or self.retrieved_at.tzinfo is None
            or self.retrieved_at.utcoffset() is None
        ):
            raise ValueError("retrieved_at must be timezone-aware")
        if self.source_last_modified is not None and (
            not isinstance(self.source_last_modified, datetime)
            or self.source_last_modified.tzinfo is None
            or self.source_last_modified.utcoffset() is None
        ):
            raise ValueError("source_last_modified must be aware or absent")
        if CONTENT_HASH.fullmatch(self.raw_content_hash) is None:
            raise ValueError("raw_content_hash is invalid")
        if (
            isinstance(self.raw_byte_length, bool)
            or not isinstance(self.raw_byte_length, int)
            or not 1 <= self.raw_byte_length <= MAX_OFAC_RAW_BYTES
        ):
            raise ValueError("raw_byte_length is invalid")
        if (
            not isinstance(self.entries, tuple)
            or not 1 <= len(self.entries) <= MAX_OFAC_ENTRIES
            or any(not isinstance(item, OfacEntry) for item in self.entries)
        ):
            raise ValueError("entries must be a bounded non-empty typed tuple")
        if len(self.entries) != self.declared_record_count:
            raise ValueError("declared record count does not match entries")
        uids = [item.uid for item in self.entries]
        if uids != sorted(uids, key=int) or len(set(uids)) != len(uids):
            raise ValueError("entries must have unique numeric UID order")
        if any(item.list_kind is not self.list_kind for item in self.entries):
            raise ValueError("entry list kind does not match snapshot")

    @property
    def content_hash(self) -> str:
        return _sha256(
            {
                "declared_record_count": self.declared_record_count,
                "entry_hashes": [item.entry_hash for item in self.entries],
                "list_kind": self.list_kind.value,
                "parser_id": OFAC_SLS_PARSER_ID,
                "parser_version": OFAC_SLS_PARSER_VERSION,
                "publish_date": self.publish_date.isoformat(),
                "raw_byte_length": self.raw_byte_length,
                "raw_content_hash": self.raw_content_hash,
                "schema_id": OFAC_SLS_SCHEMA_ID,
                "source_last_modified": (
                    self.source_last_modified.isoformat()
                    if self.source_last_modified is not None
                    else None
                ),
            }
        )

    @property
    def snapshot_id(self) -> str:
        return (
            f"ofac-sls-{self.list_kind.value.lower()}-"
            f"{self.content_hash.removeprefix('sha256:')}"
        )


@dataclass(frozen=True, slots=True)
class OfacSnapshotDiff:
    list_kind: OfacSlsListKind
    previous_snapshot_id: str
    current_snapshot_id: str
    added_uids: tuple[str, ...]
    removed_uids: tuple[str, ...]
    modified_uids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.list_kind, OfacSlsListKind):
            raise ValueError("diff list kind must be typed")
        expected_prefix = f"ofac-sls-{self.list_kind.value.lower()}-"
        if not self.previous_snapshot_id.startswith(expected_prefix) or not (
            self.current_snapshot_id.startswith(expected_prefix)
        ):
            raise ValueError("diff snapshot identities are invalid")
        for values in (self.added_uids, self.removed_uids, self.modified_uids):
            if (
                not isinstance(values, tuple)
                or any(SAFE_UID.fullmatch(item) is None for item in values)
                or values != tuple(sorted(set(values), key=int))
            ):
                raise ValueError("diff UIDs must be sorted unique numeric tuples")
        if set(self.added_uids) & set(self.removed_uids):
            raise ValueError("a UID cannot be both added and removed")


def diff_ofac_snapshots(
    previous: OfacSnapshot, current: OfacSnapshot
) -> OfacSnapshotDiff:
    if not isinstance(previous, OfacSnapshot) or not isinstance(current, OfacSnapshot):
        raise ValueError("diff requires typed snapshots")
    if previous.list_kind is not current.list_kind:
        raise ValueError("cannot diff different OFAC list kinds")
    old = {entry.uid: entry.entry_hash for entry in previous.entries}
    new = {entry.uid: entry.entry_hash for entry in current.entries}
    return OfacSnapshotDiff(
        list_kind=current.list_kind,
        previous_snapshot_id=previous.snapshot_id,
        current_snapshot_id=current.snapshot_id,
        added_uids=tuple(sorted(new.keys() - old.keys(), key=int)),
        removed_uids=tuple(sorted(old.keys() - new.keys(), key=int)),
        modified_uids=tuple(
            sorted(
                (uid for uid in old.keys() & new.keys() if old[uid] != new[uid]),
                key=int,
            )
        ),
    )


@dataclass(frozen=True, slots=True)
class OfacIdentifierQuery:
    type_code: str
    number: str

    def __post_init__(self) -> None:
        if self.type_code not in OFAC_EXACT_IDENTIFIER_TYPES:
            raise ValueError("query type is outside the finite exact inventory")
        _require_bounded(self.number, "query number", MAX_OFAC_STRING_BYTES)

    @property
    def normalized_number(self) -> str:
        return normalize_ofac_identifier(self.type_code, self.number)


@dataclass(frozen=True, slots=True)
class OfacIdentifierEvidence:
    snapshot_id: str
    snapshot_content_hash: str
    entry_uid: str
    list_kind: OfacSlsListKind
    subject_type: OfacSubjectType
    programs: tuple[str, ...]
    identifier: OfacIdentifier


@dataclass(frozen=True, slots=True)
class OfacIdentifierCandidates:
    status: OfacCandidateStatus
    query: OfacIdentifierQuery
    evidence: tuple[OfacIdentifierEvidence, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.status, OfacCandidateStatus):
            raise ValueError("candidate status must be typed")
        if not isinstance(self.query, OfacIdentifierQuery):
            raise ValueError("candidate query must be typed")
        if not isinstance(self.evidence, tuple) or any(
            not isinstance(item, OfacIdentifierEvidence) for item in self.evidence
        ):
            raise ValueError("candidate evidence must be a typed tuple")
        if (self.status is OfacCandidateStatus.NO_CANDIDATE) != (
            len(self.evidence) == 0
        ):
            raise ValueError("NO_CANDIDATE must exactly match empty evidence")


class OfacIdentifierIndex:
    """Read-only exact candidate retrieval for one integrity-checked snapshot."""

    def __init__(self, snapshot: OfacSnapshot) -> None:
        if not isinstance(snapshot, OfacSnapshot):
            raise ValueError("identifier index requires a typed snapshot")
        self._snapshot = snapshot
        values: dict[tuple[str, str], list[tuple[OfacEntry, OfacIdentifier]]] = {}
        for entry in snapshot.entries:
            for identifier in entry.identifiers:
                if identifier.usable_for_exact_candidate:
                    assert identifier.normalized_number is not None
                    values.setdefault(
                        (identifier.type_code, identifier.normalized_number), []
                    ).append((entry, identifier))
        self._values = MappingProxyType(
            {
                key: tuple(sorted(items, key=lambda item: int(item[0].uid)))
                for key, items in values.items()
            }
        )

    def search(self, query: OfacIdentifierQuery) -> OfacIdentifierCandidates:
        if not isinstance(query, OfacIdentifierQuery):
            raise ValueError("identifier search requires a typed query")
        hits = self._values.get((query.type_code, query.normalized_number), ())
        evidence = tuple(
            OfacIdentifierEvidence(
                snapshot_id=self._snapshot.snapshot_id,
                snapshot_content_hash=self._snapshot.content_hash,
                entry_uid=entry.uid,
                list_kind=entry.list_kind,
                subject_type=entry.subject_type,
                programs=entry.programs,
                identifier=identifier,
            )
            for entry, identifier in hits
        )
        status = (
            OfacCandidateStatus.NO_CANDIDATE
            if not evidence
            else (
                OfacCandidateStatus.CANDIDATE
                if len(evidence) == 1
                else OfacCandidateStatus.AMBIGUOUS
            )
        )
        return OfacIdentifierCandidates(status=status, query=query, evidence=evidence)


@dataclass(frozen=True, slots=True)
class OfacNameQuery:
    name: str

    def __post_init__(self) -> None:
        _require_bounded(self.name, "query name", MAX_OFAC_STRING_BYTES)

    @property
    def normalized_name(self) -> str:
        return normalize_ofac_name(self.name)


@dataclass(frozen=True, slots=True)
class OfacNameEvidence:
    snapshot_id: str
    snapshot_content_hash: str
    entry_uid: str
    list_kind: OfacSlsListKind
    subject_type: OfacSubjectType
    programs: tuple[str, ...]
    source_name: str
    native_locator: str
    alias_category: str | None


@dataclass(frozen=True, slots=True)
class OfacNameCandidates:
    status: OfacCandidateStatus
    query: OfacNameQuery
    evidence: tuple[OfacNameEvidence, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.status, OfacCandidateStatus):
            raise ValueError("name candidate status must be typed")
        if not isinstance(self.query, OfacNameQuery):
            raise ValueError("name candidate query must be typed")
        if not isinstance(self.evidence, tuple) or any(
            not isinstance(item, OfacNameEvidence) for item in self.evidence
        ):
            raise ValueError("name candidate evidence must be a typed tuple")
        if (self.status is OfacCandidateStatus.NO_CANDIDATE) != (
            len(self.evidence) == 0
        ):
            raise ValueError("NO_CANDIDATE must exactly match empty evidence")


class OfacNameIndex:
    """Exact normalized names and aliases remain review candidates only."""

    def __init__(self, snapshot: OfacSnapshot) -> None:
        if not isinstance(snapshot, OfacSnapshot):
            raise ValueError("name index requires a typed snapshot")
        self._snapshot = snapshot
        values: dict[str, list[tuple[OfacEntry, str, str, str | None]]] = {}
        for entry in snapshot.entries:
            values.setdefault(entry.normalized_name, []).append(
                (
                    entry,
                    entry.whole_name,
                    f"/sdnList/sdnEntry[uid='{entry.uid}']/lastName",
                    None,
                )
            )
            for alias in entry.aliases:
                values.setdefault(alias.normalized_name, []).append(
                    (
                        entry,
                        alias.whole_name,
                        alias.native_locator,
                        alias.category,
                    )
                )
        self._values = MappingProxyType(
            {
                key: tuple(sorted(items, key=lambda item: (int(item[0].uid), item[2])))
                for key, items in values.items()
            }
        )

    def search(self, query: OfacNameQuery) -> OfacNameCandidates:
        if not isinstance(query, OfacNameQuery):
            raise ValueError("name search requires a typed query")
        hits = self._values.get(query.normalized_name, ())
        evidence = tuple(
            OfacNameEvidence(
                snapshot_id=self._snapshot.snapshot_id,
                snapshot_content_hash=self._snapshot.content_hash,
                entry_uid=entry.uid,
                list_kind=entry.list_kind,
                subject_type=entry.subject_type,
                programs=entry.programs,
                source_name=source_name,
                native_locator=locator,
                alias_category=category,
            )
            for entry, source_name, locator, category in hits
        )
        distinct_entries = {item.entry_uid for item in evidence}
        status = (
            OfacCandidateStatus.NO_CANDIDATE
            if not evidence
            else (
                OfacCandidateStatus.CANDIDATE
                if len(distinct_entries) == 1
                else OfacCandidateStatus.AMBIGUOUS
            )
        )
        return OfacNameCandidates(status=status, query=query, evidence=evidence)
