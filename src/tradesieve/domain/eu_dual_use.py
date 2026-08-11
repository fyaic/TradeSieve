"""Versioned EU Annex I evidence and conservative goods-control assessment.

An Annex I code is a legal classification candidate, not an HS-code mapping and not
an export authorisation.  The model therefore never emits an automatic clearance.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final

EU_DUAL_USE_CELEX: Final = "32025R2003"
EU_DUAL_USE_SOURCE_URL: Final = (
    "https://publications.europa.eu/resource/cellar/"
    "ec080244-c0fa-11f0-a612-01aa75ed71a1.0006.02/DOC_1"
)
EU_DUAL_USE_LEGAL_URL: Final = (
    "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32025R2003"
)
EU_DUAL_USE_EFFECTIVE_FROM: Final = date(2025, 11, 15)
EU_DUAL_USE_PUBLISHED_ON: Final = date(2025, 11, 14)
EU_DUAL_USE_SCHEMA_ID: Final = "formex-06.02.3-20250123"
EU_DUAL_USE_PARSER_ID: Final = "eu-annex-i-formex-v1"
EU_DUAL_USE_PARSER_VERSION: Final = "1.0.0"

MAX_EU_DUAL_USE_ARCHIVE_BYTES: Final = 8 * 1024 * 1024
MAX_EU_DUAL_USE_DOCUMENT_BYTES: Final = 8 * 1024 * 1024
MAX_EU_DUAL_USE_ENTRY_BYTES: Final = 64 * 1024
MIN_EU_DUAL_USE_ENTRIES: Final = 300
MAX_EU_DUAL_USE_ENTRIES: Final = 1_000

CONTROL_CODE: Final = re.compile(r"^[0-9][A-E][0-9]{3}$")
CONTENT_HASH: Final = re.compile(r"^sha256:[a-f0-9]{64}$")


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def normalize_control_code(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("control code must be a string")
    normalized = "".join(value.upper().split())
    if CONTROL_CODE.fullmatch(normalized) is None:
        raise ValueError("control code is not an Annex I code")
    return normalized


class EuDualUseAssessmentStatus(StrEnum):
    MISSING_CLASSIFICATION = "MISSING_CLASSIFICATION"
    ENTRY_NOT_FOUND = "ENTRY_NOT_FOUND"
    TECHNICAL_REVIEW_REQUIRED = "TECHNICAL_REVIEW_REQUIRED"
    CONTROL_ENTRY_FOUND = "CONTROL_ENTRY_FOUND"


@dataclass(frozen=True, slots=True)
class EuDualUseControlEntry:
    code: str
    text: str
    native_locator: str

    def __post_init__(self) -> None:
        if self.code != normalize_control_code(self.code):
            raise ValueError("control code must be canonical")
        if (
            not isinstance(self.text, str)
            or not self.text
            or len(self.text.encode("utf-8")) > MAX_EU_DUAL_USE_ENTRY_BYTES
        ):
            raise ValueError("control text is outside its bound")
        if (
            not isinstance(self.native_locator, str)
            or not self.native_locator.startswith("/ANNEX/")
            or len(self.native_locator.encode("utf-8")) > 512
        ):
            raise ValueError("control locator is invalid")

    @property
    def content_hash(self) -> str:
        return _canonical_hash(
            {
                "code": self.code,
                "native_locator": self.native_locator,
                "text": self.text,
            }
        )


@dataclass(frozen=True, slots=True)
class EuDualUseControlList:
    retrieved_at: datetime
    source_archive_hash: str
    source_archive_bytes: int
    formex_document_hash: str
    entries: tuple[EuDualUseControlEntry, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.retrieved_at, datetime)
            or self.retrieved_at.tzinfo is None
            or self.retrieved_at.utcoffset() is None
        ):
            raise ValueError("retrieved_at must be timezone-aware")
        if CONTENT_HASH.fullmatch(self.source_archive_hash) is None:
            raise ValueError("source archive hash is invalid")
        if CONTENT_HASH.fullmatch(self.formex_document_hash) is None:
            raise ValueError("Formex document hash is invalid")
        if (
            isinstance(self.source_archive_bytes, bool)
            or not isinstance(self.source_archive_bytes, int)
            or not 1 <= self.source_archive_bytes <= MAX_EU_DUAL_USE_ARCHIVE_BYTES
        ):
            raise ValueError("source archive byte length is invalid")
        if (
            not isinstance(self.entries, tuple)
            or not MIN_EU_DUAL_USE_ENTRIES
            <= len(self.entries)
            <= MAX_EU_DUAL_USE_ENTRIES
            or any(
                not isinstance(entry, EuDualUseControlEntry) for entry in self.entries
            )
        ):
            raise ValueError("control entries are outside their typed bounds")
        codes = [entry.code for entry in self.entries]
        if len(codes) != len(set(codes)):
            raise ValueError("control codes must be unique")
        if {code[0] for code in codes} != set("0123456789"):
            raise ValueError("all Annex I categories must be represented")

    @property
    def content_hash(self) -> str:
        return _canonical_hash(
            {
                "celex": EU_DUAL_USE_CELEX,
                "effective_from": EU_DUAL_USE_EFFECTIVE_FROM.isoformat(),
                "entry_hashes": [entry.content_hash for entry in self.entries],
                "formex_document_hash": self.formex_document_hash,
                "parser_id": EU_DUAL_USE_PARSER_ID,
                "parser_version": EU_DUAL_USE_PARSER_VERSION,
                "published_on": EU_DUAL_USE_PUBLISHED_ON.isoformat(),
                "schema_id": EU_DUAL_USE_SCHEMA_ID,
                "source_archive_bytes": self.source_archive_bytes,
                "source_archive_hash": self.source_archive_hash,
            }
        )

    @property
    def snapshot_id(self) -> str:
        return f"eu-dual-use-{self.content_hash.removeprefix('sha256:')}"


@dataclass(frozen=True, slots=True)
class EuDualUseAssessment:
    status: EuDualUseAssessmentStatus
    requested_code: str | None
    snapshot_id: str
    snapshot_content_hash: str
    source_celex: str
    effective_from: date
    entry: EuDualUseControlEntry | None
    missing_facts: tuple[str, ...]
    automatic_clearance: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.status, EuDualUseAssessmentStatus):
            raise ValueError("assessment status must be typed")
        if self.requested_code is not None:
            normalize_control_code(self.requested_code)
        if not self.snapshot_id.startswith("eu-dual-use-"):
            raise ValueError("assessment snapshot identity is invalid")
        if CONTENT_HASH.fullmatch(self.snapshot_content_hash) is None:
            raise ValueError("assessment snapshot hash is invalid")
        if self.source_celex != EU_DUAL_USE_CELEX:
            raise ValueError("assessment source version is invalid")
        if self.effective_from != EU_DUAL_USE_EFFECTIVE_FROM:
            raise ValueError("assessment effective date is invalid")
        if self.entry is not None and not isinstance(self.entry, EuDualUseControlEntry):
            raise ValueError("assessment entry must be typed")
        if not isinstance(self.missing_facts, tuple) or any(
            not isinstance(item, str) or not item for item in self.missing_facts
        ):
            raise ValueError("missing facts must be a typed tuple")
        if self.automatic_clearance is not False:
            raise ValueError("Annex I assessment cannot grant automatic clearance")


class EuDualUseAssessmentEngine:
    """Evaluate an explicit Annex I candidate against one official version."""

    def __init__(self, control_list: EuDualUseControlList) -> None:
        if not isinstance(control_list, EuDualUseControlList):
            raise ValueError("assessment engine requires a typed control list")
        self._control_list = control_list
        self._entries = MappingProxyType(
            {entry.code: entry for entry in control_list.entries}
        )

    def assess(
        self,
        classification_code: str | None,
        *,
        classification_verified: bool,
        technical_specification_available: bool,
    ) -> EuDualUseAssessment:
        for value in (classification_verified, technical_specification_available):
            if not isinstance(value, bool):
                raise ValueError("assessment fact flags must be boolean")
        code = (
            None
            if classification_code is None
            else normalize_control_code(classification_code)
        )
        entry = self._entries.get(code) if code is not None else None
        missing: list[str] = []
        if code is None:
            status = EuDualUseAssessmentStatus.MISSING_CLASSIFICATION
            missing.extend(("annex_i_classification", "qualified_classifier_review"))
        elif entry is None:
            status = EuDualUseAssessmentStatus.ENTRY_NOT_FOUND
            missing.append("catch_all_and_national_control_review")
            if not classification_verified:
                missing.append("qualified_classifier_review")
        else:
            if not classification_verified:
                missing.append("qualified_classifier_review")
            if not technical_specification_available:
                missing.append("technical_specification")
            status = (
                EuDualUseAssessmentStatus.CONTROL_ENTRY_FOUND
                if not missing
                else EuDualUseAssessmentStatus.TECHNICAL_REVIEW_REQUIRED
            )
        return EuDualUseAssessment(
            status=status,
            requested_code=code,
            snapshot_id=self._control_list.snapshot_id,
            snapshot_content_hash=self._control_list.content_hash,
            source_celex=EU_DUAL_USE_CELEX,
            effective_from=EU_DUAL_USE_EFFECTIVE_FROM,
            entry=entry,
            missing_facts=tuple(missing),
        )
