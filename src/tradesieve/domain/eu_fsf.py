"""Immutable EU FSF sanctions evidence and deterministic exact identifiers.

The consolidated file is evidence for financial-sanctions designations.  It is not
itself a legal-clearance engine: controlling legal acts and an authorised human own
the final disposition.
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

EU_FSF_DATASET_ID: Final = (
    "consolidated-list-of-persons-groups-and-entities-subject-to-eu-financial-sanctions"
)
EU_FSF_DATASET_URL: Final = (
    "https://data.europa.eu/api/hub/search/datasets/" + EU_FSF_DATASET_ID
)
EU_FSF_DOWNLOAD_HOST: Final = "webgate.ec.europa.eu"
EU_FSF_DOWNLOAD_PATH: Final = "/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content"
EU_FSF_XML_NAMESPACE: Final = "http://eu.europa.ec/fpi/fsd/export"
EU_FSF_SCHEMA_ID: Final = "eu-fsf-xml-1.1"
EU_FSF_PARSER_ID: Final = "eu-fsf-xml-v1"
EU_FSF_PARSER_VERSION: Final = "1.0.0"
EU_FSF_DISTRIBUTION_TITLE: Final = "Consolidated Financial Sanctions File 1.1"
EU_FSF_REUSE_LICENCE_ID: Final = "COM_REUSE"
EU_FSF_REUSE_LICENCE_RESOURCE: Final = "http://data.europa.eu/eli/dec/2011/833/oj"

MAX_EU_FSF_RAW_BYTES: Final = 64 * 1024 * 1024
MAX_EU_FSF_CATALOGUE_BYTES: Final = 2 * 1024 * 1024
MAX_EU_FSF_ENTITIES: Final = 20_000
MAX_EU_FSF_ALIASES_PER_ENTITY: Final = 256
MAX_EU_FSF_IDENTIFIERS_PER_ENTITY: Final = 64
MAX_EU_FSF_STRING_BYTES: Final = 4096
MAX_EU_FSF_LOCATOR_BYTES: Final = 512

SAFE_TOKEN: Final = re.compile(r"^[A-Za-z0-9_-]{1,256}$")
SAFE_LOGICAL_ID: Final = re.compile(r"^[1-9][0-9]{0,18}$")
SAFE_REFERENCE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SAFE_COUNTRY: Final = re.compile(r"^[A-Z]{2}$")
CONTENT_HASH: Final = re.compile(r"^sha256:[a-f0-9]{64}$")
SAFE_TYPE_CODE: Final = re.compile(r"^[a-z][a-z0-9]{0,31}$")

EU_FSF_IDENTIFIER_TYPES: Final = frozenset(
    {
        "birthcert",
        "drivinglicence",
        "electionid",
        "euvat",
        "fiscalcode",
        "id",
        "imo",
        "nationcert",
        "other",
        "passport",
        "regnumber",
        "residentperm",
        "ssn",
        "swiftbic",
        "taxid",
        "tradelic",
        "travelcardid",
        "unssn",
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


def normalize_eu_fsf_name(value: str) -> str:
    """Return a deterministic candidate key; never an exact-identifier match."""

    _require_bounded(value, "name", MAX_EU_FSF_STRING_BYTES)
    normalized = " ".join(unicodedata.normalize("NFKC", value).split()).casefold()
    _require_bounded(normalized, "normalized name", MAX_EU_FSF_STRING_BYTES)
    return normalized


def normalize_eu_fsf_identifier(type_code: str, value: str) -> str:
    """Normalize formatting only while retaining the typed source assertion."""

    if type_code not in EU_FSF_IDENTIFIER_TYPES:
        raise ValueError("identifier type is outside the finite FSF inventory")
    _require_bounded(value, "identifier", MAX_EU_FSF_STRING_BYTES)
    normalized = unicodedata.normalize("NFKC", value).upper()
    normalized = "".join(
        character
        for character in normalized
        if not character.isspace()
        and unicodedata.category(character) not in {"Pd", "Pc"}
    )
    if type_code == "imo" and normalized.startswith("IMO"):
        normalized = normalized[3:]
    _require_bounded(normalized, "normalized identifier", 256)
    return normalized


class EuFsfSubjectType(StrEnum):
    PERSON = "person"
    ENTERPRISE = "enterprise"


class EuFsfExactMatchStatus(StrEnum):
    NO_MATCH = "NO_MATCH"
    MATCH = "MATCH"
    AMBIGUOUS = "AMBIGUOUS"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class EuFsfNameCandidateStatus(StrEnum):
    NO_CANDIDATE = "NO_CANDIDATE"
    CANDIDATE = "CANDIDATE"
    AMBIGUOUS = "AMBIGUOUS"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


@dataclass(frozen=True, slots=True)
class EuFsfRegulation:
    programme: str
    number_title: str
    publication_date: date
    entry_into_force_date: date
    publication_url: str
    native_locator: str

    def __post_init__(self) -> None:
        for value, field, maximum in (
            (self.programme, "programme", 64),
            (self.number_title, "number_title", 256),
            (self.publication_url, "publication_url", 2048),
            (self.native_locator, "native_locator", MAX_EU_FSF_LOCATOR_BYTES),
        ):
            _require_bounded(value, field, maximum)
        if not isinstance(self.publication_date, date) or not isinstance(
            self.entry_into_force_date, date
        ):
            raise ValueError("regulation dates must be typed dates")

    def canonical_content(self) -> dict[str, object]:
        return {
            "entry_into_force_date": self.entry_into_force_date.isoformat(),
            "native_locator": self.native_locator,
            "number_title": self.number_title,
            "programme": self.programme,
            "publication_date": self.publication_date.isoformat(),
            "publication_url": self.publication_url,
        }


@dataclass(frozen=True, slots=True)
class EuFsfAlias:
    logical_id: str
    whole_name: str
    normalized_name: str
    strong: bool
    language: str | None
    native_locator: str

    @classmethod
    def create(
        cls,
        *,
        logical_id: str,
        whole_name: str,
        strong: bool,
        language: str | None,
        native_locator: str,
    ) -> EuFsfAlias:
        return cls(
            logical_id=logical_id,
            whole_name=whole_name,
            normalized_name=normalize_eu_fsf_name(whole_name),
            strong=strong,
            language=language,
            native_locator=native_locator,
        )

    def __post_init__(self) -> None:
        if SAFE_LOGICAL_ID.fullmatch(self.logical_id) is None:
            raise ValueError("alias logical_id is invalid")
        _require_bounded(self.whole_name, "whole_name", MAX_EU_FSF_STRING_BYTES)
        if self.normalized_name != normalize_eu_fsf_name(self.whole_name):
            raise ValueError("alias normalized_name is not canonical")
        if not isinstance(self.strong, bool):
            raise ValueError("alias strong flag must be boolean")
        _require_optional_bounded(self.language, "language", 16)
        _require_bounded(
            self.native_locator, "native_locator", MAX_EU_FSF_LOCATOR_BYTES
        )

    def canonical_content(self) -> dict[str, object]:
        return {
            "language": self.language,
            "logical_id": self.logical_id,
            "native_locator": self.native_locator,
            "normalized_name": self.normalized_name,
            "strong": self.strong,
            "whole_name": self.whole_name,
        }

    @property
    def assertion_hash(self) -> str:
        return _sha256(self.canonical_content())


@dataclass(frozen=True, slots=True)
class EuFsfIdentifier:
    logical_id: str
    type_code: str
    number: str
    normalized_number: str
    country_code: str | None
    known_expired: bool
    known_false: bool
    reported_lost: bool
    revoked_by_issuer: bool
    native_locator: str

    @classmethod
    def create(
        cls,
        *,
        logical_id: str,
        type_code: str,
        number: str,
        country_code: str | None,
        known_expired: bool,
        known_false: bool,
        reported_lost: bool,
        revoked_by_issuer: bool,
        native_locator: str,
    ) -> EuFsfIdentifier:
        return cls(
            logical_id=logical_id,
            type_code=type_code,
            number=number,
            normalized_number=normalize_eu_fsf_identifier(type_code, number),
            country_code=country_code,
            known_expired=known_expired,
            known_false=known_false,
            reported_lost=reported_lost,
            revoked_by_issuer=revoked_by_issuer,
            native_locator=native_locator,
        )

    def __post_init__(self) -> None:
        if SAFE_LOGICAL_ID.fullmatch(self.logical_id) is None:
            raise ValueError("identifier logical_id is invalid")
        if self.type_code not in EU_FSF_IDENTIFIER_TYPES:
            raise ValueError("identifier type is outside the finite FSF inventory")
        _require_bounded(self.number, "number", MAX_EU_FSF_STRING_BYTES)
        if self.normalized_number != normalize_eu_fsf_identifier(
            self.type_code, self.number
        ):
            raise ValueError("identifier normalized_number is not canonical")
        if (
            self.country_code is not None
            and SAFE_COUNTRY.fullmatch(self.country_code) is None
        ):
            raise ValueError("identifier country_code is invalid")
        for flag in (
            self.known_expired,
            self.known_false,
            self.reported_lost,
            self.revoked_by_issuer,
        ):
            if not isinstance(flag, bool):
                raise ValueError("identifier status flags must be boolean")
        _require_bounded(
            self.native_locator, "native_locator", MAX_EU_FSF_LOCATOR_BYTES
        )

    @property
    def usable_for_exact_match(self) -> bool:
        return not (
            self.known_expired
            or self.known_false
            or self.reported_lost
            or self.revoked_by_issuer
        )

    @property
    def assertion_hash(self) -> str:
        return _sha256(self.canonical_content())

    def canonical_content(self) -> dict[str, object]:
        return {
            "country_code": self.country_code,
            "known_expired": self.known_expired,
            "known_false": self.known_false,
            "logical_id": self.logical_id,
            "native_locator": self.native_locator,
            "normalized_number": self.normalized_number,
            "number": self.number,
            "reported_lost": self.reported_lost,
            "revoked_by_issuer": self.revoked_by_issuer,
            "type_code": self.type_code,
        }


@dataclass(frozen=True, slots=True)
class EuFsfEntity:
    logical_id: str
    eu_reference_number: str
    united_nations_id: str | None
    designation_date: date | None
    subject_type: EuFsfSubjectType
    regulation: EuFsfRegulation
    aliases: tuple[EuFsfAlias, ...]
    identifiers: tuple[EuFsfIdentifier, ...]

    def __post_init__(self) -> None:
        if SAFE_LOGICAL_ID.fullmatch(self.logical_id) is None:
            raise ValueError("entity logical_id is invalid")
        if SAFE_REFERENCE.fullmatch(self.eu_reference_number) is None:
            raise ValueError("entity eu_reference_number is invalid")
        _require_optional_bounded(self.united_nations_id, "united_nations_id", 128)
        if self.designation_date is not None and not isinstance(
            self.designation_date, date
        ):
            raise ValueError("designation_date must be a date")
        if not isinstance(self.subject_type, EuFsfSubjectType):
            raise ValueError("subject_type must be typed")
        if not isinstance(self.regulation, EuFsfRegulation):
            raise ValueError("regulation must be typed")
        if (
            not isinstance(self.aliases, tuple)
            or not 1 <= len(self.aliases) <= MAX_EU_FSF_ALIASES_PER_ENTITY
            or any(not isinstance(item, EuFsfAlias) for item in self.aliases)
        ):
            raise ValueError("aliases must be a bounded non-empty typed tuple")
        if (
            not isinstance(self.identifiers, tuple)
            or len(self.identifiers) > MAX_EU_FSF_IDENTIFIERS_PER_ENTITY
            or any(not isinstance(item, EuFsfIdentifier) for item in self.identifiers)
        ):
            raise ValueError("identifiers must be a bounded typed tuple")
        alias_ids = [item.logical_id for item in self.aliases]
        identifier_ids = [item.logical_id for item in self.identifiers]
        if alias_ids != sorted(alias_ids, key=int) or len(set(alias_ids)) != len(
            alias_ids
        ):
            raise ValueError("aliases must have unique numeric order")
        if identifier_ids != sorted(identifier_ids, key=int) or len(
            set(identifier_ids)
        ) != len(identifier_ids):
            raise ValueError("identifiers must have unique numeric order")

    @property
    def entity_hash(self) -> str:
        return _sha256(self.canonical_content())

    def canonical_content(self) -> dict[str, object]:
        return {
            "aliases": [item.canonical_content() for item in self.aliases],
            "designation_date": (
                self.designation_date.isoformat()
                if self.designation_date is not None
                else None
            ),
            "eu_reference_number": self.eu_reference_number,
            "identifiers": [item.canonical_content() for item in self.identifiers],
            "logical_id": self.logical_id,
            "regulation": self.regulation.canonical_content(),
            "subject_type": self.subject_type.value,
            "united_nations_id": self.united_nations_id,
        }


@dataclass(frozen=True, slots=True)
class EuFsfSnapshot:
    generation_date: datetime
    global_file_id: str
    raw_content_hash: str
    raw_byte_length: int
    entities: tuple[EuFsfEntity, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.generation_date, datetime)
            or self.generation_date.tzinfo is None
            or self.generation_date.utcoffset() is None
        ):
            raise ValueError("generation_date must be timezone-aware")
        if SAFE_LOGICAL_ID.fullmatch(self.global_file_id) is None:
            raise ValueError("global_file_id is invalid")
        if CONTENT_HASH.fullmatch(self.raw_content_hash) is None:
            raise ValueError("raw_content_hash is invalid")
        if (
            isinstance(self.raw_byte_length, bool)
            or not isinstance(self.raw_byte_length, int)
            or not 1 <= self.raw_byte_length <= MAX_EU_FSF_RAW_BYTES
        ):
            raise ValueError("raw_byte_length is invalid")
        if (
            not isinstance(self.entities, tuple)
            or not 1 <= len(self.entities) <= MAX_EU_FSF_ENTITIES
            or any(not isinstance(item, EuFsfEntity) for item in self.entities)
        ):
            raise ValueError("entities must be a bounded non-empty typed tuple")
        ids = [item.logical_id for item in self.entities]
        references = [item.eu_reference_number for item in self.entities]
        if ids != sorted(ids, key=int) or len(set(ids)) != len(ids):
            raise ValueError("entities must have unique numeric order")
        if len(set(references)) != len(references):
            raise ValueError("EU reference numbers must be unique")

    @property
    def content_hash(self) -> str:
        return _sha256(
            {
                "entity_hashes": [item.entity_hash for item in self.entities],
                "generation_date": self.generation_date.isoformat(),
                "global_file_id": self.global_file_id,
                "parser_id": EU_FSF_PARSER_ID,
                "parser_version": EU_FSF_PARSER_VERSION,
                "raw_byte_length": self.raw_byte_length,
                "raw_content_hash": self.raw_content_hash,
                "schema_id": EU_FSF_SCHEMA_ID,
            }
        )

    @property
    def snapshot_id(self) -> str:
        return f"eu-fsf-{self.content_hash.removeprefix('sha256:')}"


@dataclass(frozen=True, slots=True)
class EuFsfExactQuery:
    type_code: str
    number: str
    country_code: str | None = None

    def __post_init__(self) -> None:
        if self.type_code not in EU_FSF_IDENTIFIER_TYPES:
            raise ValueError("query identifier type is outside the finite inventory")
        _require_bounded(self.number, "query number", MAX_EU_FSF_STRING_BYTES)
        if (
            self.country_code is not None
            and SAFE_COUNTRY.fullmatch(self.country_code) is None
        ):
            raise ValueError("query country_code is invalid")

    @property
    def normalized_number(self) -> str:
        return normalize_eu_fsf_identifier(self.type_code, self.number)


@dataclass(frozen=True, slots=True)
class EuFsfExactEvidence:
    snapshot_id: str
    snapshot_content_hash: str
    entity_logical_id: str
    eu_reference_number: str
    subject_type: EuFsfSubjectType
    identifier: EuFsfIdentifier
    country_consistent: bool | None

    def __post_init__(self) -> None:
        if not self.snapshot_id.startswith("eu-fsf-"):
            raise ValueError("evidence snapshot_id is invalid")
        if CONTENT_HASH.fullmatch(self.snapshot_content_hash) is None:
            raise ValueError("evidence snapshot hash is invalid")
        if SAFE_LOGICAL_ID.fullmatch(self.entity_logical_id) is None:
            raise ValueError("evidence entity identity is invalid")
        if SAFE_REFERENCE.fullmatch(self.eu_reference_number) is None:
            raise ValueError("evidence EU reference is invalid")
        if not isinstance(self.subject_type, EuFsfSubjectType):
            raise ValueError("evidence subject_type must be typed")
        if not isinstance(self.identifier, EuFsfIdentifier):
            raise ValueError("evidence identifier must be typed")
        if self.country_consistent is not None and not isinstance(
            self.country_consistent, bool
        ):
            raise ValueError("country consistency must be boolean or unknown")


@dataclass(frozen=True, slots=True)
class EuFsfExactMatch:
    status: EuFsfExactMatchStatus
    query: EuFsfExactQuery
    evidence: tuple[EuFsfExactEvidence, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.status, EuFsfExactMatchStatus):
            raise ValueError("match status must be typed")
        if not isinstance(self.query, EuFsfExactQuery):
            raise ValueError("match query must be typed")
        if not isinstance(self.evidence, tuple) or any(
            not isinstance(item, EuFsfExactEvidence) for item in self.evidence
        ):
            raise ValueError("match evidence must be a typed tuple")
        if (self.status is EuFsfExactMatchStatus.NO_MATCH) != (len(self.evidence) == 0):
            raise ValueError("NO_MATCH must exactly correspond to empty evidence")


class EuFsfExactIndex:
    """Read-only deterministic exact-identifier index for one verified snapshot."""

    def __init__(self, snapshot: EuFsfSnapshot) -> None:
        if not isinstance(snapshot, EuFsfSnapshot):
            raise ValueError("exact index requires a typed snapshot")
        self._snapshot = snapshot
        index: dict[tuple[str, str], list[tuple[EuFsfEntity, EuFsfIdentifier]]] = {}
        for entity in snapshot.entities:
            for identifier in entity.identifiers:
                index.setdefault(
                    (identifier.type_code, identifier.normalized_number), []
                ).append((entity, identifier))
        self._index = MappingProxyType(
            {
                key: tuple(
                    sorted(
                        value,
                        key=lambda item: (
                            int(item[0].logical_id),
                            int(item[1].logical_id),
                        ),
                    )
                )
                for key, value in index.items()
            }
        )

    @property
    def snapshot_id(self) -> str:
        return self._snapshot.snapshot_id

    def query(self, query: EuFsfExactQuery) -> EuFsfExactMatch:
        if not isinstance(query, EuFsfExactQuery):
            raise ValueError("exact query must be typed")
        pairs = self._index.get((query.type_code, query.normalized_number), ())
        evidence = tuple(
            EuFsfExactEvidence(
                snapshot_id=self._snapshot.snapshot_id,
                snapshot_content_hash=self._snapshot.content_hash,
                entity_logical_id=entity.logical_id,
                eu_reference_number=entity.eu_reference_number,
                subject_type=entity.subject_type,
                identifier=identifier,
                country_consistent=(
                    None
                    if query.country_code is None or identifier.country_code is None
                    else query.country_code == identifier.country_code
                ),
            )
            for entity, identifier in pairs
        )
        if not evidence:
            status = EuFsfExactMatchStatus.NO_MATCH
        else:
            usable = tuple(
                item for item in evidence if item.identifier.usable_for_exact_match
            )
            entity_ids = {item.entity_logical_id for item in usable}
            country_conflict = bool(usable) and all(
                item.country_consistent is False for item in usable
            )
            if not usable or country_conflict:
                status = EuFsfExactMatchStatus.REVIEW_REQUIRED
            elif len(entity_ids) == 1:
                status = EuFsfExactMatchStatus.MATCH
            else:
                status = EuFsfExactMatchStatus.AMBIGUOUS
        return EuFsfExactMatch(status=status, query=query, evidence=evidence)


@dataclass(frozen=True, slots=True)
class EuFsfNameQuery:
    name: str

    def __post_init__(self) -> None:
        _require_bounded(self.name, "query name", MAX_EU_FSF_STRING_BYTES)

    @property
    def normalized_name(self) -> str:
        return normalize_eu_fsf_name(self.name)


@dataclass(frozen=True, slots=True)
class EuFsfNameEvidence:
    snapshot_id: str
    snapshot_content_hash: str
    entity_logical_id: str
    eu_reference_number: str
    subject_type: EuFsfSubjectType
    alias: EuFsfAlias

    def __post_init__(self) -> None:
        if not self.snapshot_id.startswith("eu-fsf-"):
            raise ValueError("name evidence snapshot_id is invalid")
        if CONTENT_HASH.fullmatch(self.snapshot_content_hash) is None:
            raise ValueError("name evidence snapshot hash is invalid")
        if SAFE_LOGICAL_ID.fullmatch(self.entity_logical_id) is None:
            raise ValueError("name evidence entity identity is invalid")
        if SAFE_REFERENCE.fullmatch(self.eu_reference_number) is None:
            raise ValueError("name evidence EU reference is invalid")
        if not isinstance(self.subject_type, EuFsfSubjectType):
            raise ValueError("name evidence subject_type must be typed")
        if not isinstance(self.alias, EuFsfAlias):
            raise ValueError("name evidence alias must be typed")


@dataclass(frozen=True, slots=True)
class EuFsfNameCandidates:
    status: EuFsfNameCandidateStatus
    query: EuFsfNameQuery
    evidence: tuple[EuFsfNameEvidence, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.status, EuFsfNameCandidateStatus):
            raise ValueError("name candidate status must be typed")
        if not isinstance(self.query, EuFsfNameQuery):
            raise ValueError("name query must be typed")
        if not isinstance(self.evidence, tuple) or any(
            not isinstance(item, EuFsfNameEvidence) for item in self.evidence
        ):
            raise ValueError("name candidate evidence must be a typed tuple")
        if (self.status is EuFsfNameCandidateStatus.NO_CANDIDATE) != (
            len(self.evidence) == 0
        ):
            raise ValueError("NO_CANDIDATE must correspond to empty evidence")


class EuFsfNameIndex:
    """Exact normalized-alias candidate index; it never produces legal clearance."""

    def __init__(self, snapshot: EuFsfSnapshot) -> None:
        if not isinstance(snapshot, EuFsfSnapshot):
            raise ValueError("name index requires a typed snapshot")
        self._snapshot = snapshot
        index: dict[str, list[tuple[EuFsfEntity, EuFsfAlias]]] = {}
        for entity in snapshot.entities:
            for alias in entity.aliases:
                index.setdefault(alias.normalized_name, []).append((entity, alias))
        self._index = MappingProxyType(
            {
                key: tuple(
                    sorted(
                        value,
                        key=lambda item: (
                            int(item[0].logical_id),
                            int(item[1].logical_id),
                        ),
                    )
                )
                for key, value in index.items()
            }
        )

    def query(self, query: EuFsfNameQuery) -> EuFsfNameCandidates:
        if not isinstance(query, EuFsfNameQuery):
            raise ValueError("name query must be typed")
        pairs = self._index.get(query.normalized_name, ())
        evidence = tuple(
            EuFsfNameEvidence(
                snapshot_id=self._snapshot.snapshot_id,
                snapshot_content_hash=self._snapshot.content_hash,
                entity_logical_id=entity.logical_id,
                eu_reference_number=entity.eu_reference_number,
                subject_type=entity.subject_type,
                alias=alias,
            )
            for entity, alias in pairs
        )
        if not evidence:
            status = EuFsfNameCandidateStatus.NO_CANDIDATE
        elif not any(item.alias.strong for item in evidence):
            status = EuFsfNameCandidateStatus.REVIEW_REQUIRED
        elif len({item.entity_logical_id for item in evidence}) == 1:
            status = EuFsfNameCandidateStatus.CANDIDATE
        else:
            status = EuFsfNameCandidateStatus.AMBIGUOUS
        return EuFsfNameCandidates(status=status, query=query, evidence=evidence)
