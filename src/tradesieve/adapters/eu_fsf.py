"""Official EU FSF catalogue retrieval and secure finite XML parsing."""

from __future__ import annotations

import hashlib
import http.client
import json
import ssl
import zlib
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from io import BytesIO
from types import MappingProxyType
from typing import Never, Protocol
from urllib.parse import parse_qs, urljoin, urlsplit
from xml.etree import ElementTree

import certifi

from tradesieve.domain.eu_fsf import (
    EU_FSF_DATASET_ID,
    EU_FSF_DATASET_URL,
    EU_FSF_DISTRIBUTION_TITLE,
    EU_FSF_DOWNLOAD_HOST,
    EU_FSF_DOWNLOAD_PATH,
    EU_FSF_REUSE_LICENCE_ID,
    EU_FSF_REUSE_LICENCE_RESOURCE,
    EU_FSF_XML_NAMESPACE,
    MAX_EU_FSF_ALIASES_PER_ENTITY,
    MAX_EU_FSF_CATALOGUE_BYTES,
    MAX_EU_FSF_ENTITIES,
    MAX_EU_FSF_IDENTIFIERS_PER_ENTITY,
    MAX_EU_FSF_RAW_BYTES,
    SAFE_TOKEN,
    EuFsfAlias,
    EuFsfEntity,
    EuFsfIdentifier,
    EuFsfRegulation,
    EuFsfSnapshot,
    EuFsfSubjectType,
)
from tradesieve.ports.eu_fsf import EuFsfHttpDocument, EuFsfHttpTransport

_CATALOGUE_HOST = "data.europa.eu"
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECTS = 3
_READ_CHUNK_BYTES = 64 * 1024
_ROOT = f"{{{EU_FSF_XML_NAMESPACE}}}export"
_ENTITY = f"{{{EU_FSF_XML_NAMESPACE}}}sanctionEntity"
_REGULATION = f"{{{EU_FSF_XML_NAMESPACE}}}regulation"
_SUBJECT_TYPE = f"{{{EU_FSF_XML_NAMESPACE}}}subjectType"
_ALIAS = f"{{{EU_FSF_XML_NAMESPACE}}}nameAlias"
_IDENTIFICATION = f"{{{EU_FSF_XML_NAMESPACE}}}identification"
_PUBLICATION_URL = f"{{{EU_FSF_XML_NAMESPACE}}}publicationUrl"
_REMARK = f"{{{EU_FSF_XML_NAMESPACE}}}remark"
_REGULATION_SUMMARY = f"{{{EU_FSF_XML_NAMESPACE}}}regulationSummary"

_ROOT_ATTRIBUTES = frozenset({"generationDate", "globalFileId"})
_ENTITY_ATTRIBUTES = frozenset(
    {
        "designationDate",
        "designationDetails",
        "unitedNationId",
        "euReferenceNumber",
        "logicalId",
    }
)
_ENTITY_CHILDREN = frozenset(
    {
        _REMARK,
        _REGULATION,
        _SUBJECT_TYPE,
        _ALIAS,
        f"{{{EU_FSF_XML_NAMESPACE}}}address",
        f"{{{EU_FSF_XML_NAMESPACE}}}birthdate",
        _IDENTIFICATION,
        f"{{{EU_FSF_XML_NAMESPACE}}}citizenship",
    }
)
_REGULATION_ATTRIBUTES = frozenset(
    {
        "regulationType",
        "organisationType",
        "publicationDate",
        "entryIntoForceDate",
        "numberTitle",
        "programme",
        "logicalId",
    }
)
_SUBJECT_ATTRIBUTES = frozenset({"code", "classificationCode"})
_ALIAS_ATTRIBUTES = frozenset(
    {
        "firstName",
        "middleName",
        "lastName",
        "wholeName",
        "function",
        "gender",
        "title",
        "nameLanguage",
        "strong",
        "regulationLanguage",
        "logicalId",
    }
)
_IDENTIFIER_ATTRIBUTES = frozenset(
    {
        "diplomatic",
        "knownExpired",
        "knownFalse",
        "reportedLost",
        "revokedByIssuer",
        "issuedBy",
        "issueDate",
        "validFrom",
        "validTo",
        "latinNumber",
        "nameOnDocument",
        "number",
        "region",
        "identificationTypeCode",
        "identificationTypeDescription",
        "countryIso2Code",
        "countryDescription",
        "regulationLanguage",
        "logicalId",
    }
)


class EuFsfSourceErrorCode(StrEnum):
    INVALID_URL = "INVALID_URL"
    NETWORK_UNAVAILABLE = "NETWORK_UNAVAILABLE"
    HTTP_STATUS = "HTTP_STATUS"
    REDIRECT_INVALID = "REDIRECT_INVALID"
    RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"
    RESPONSE_INVALID = "RESPONSE_INVALID"
    CATALOGUE_INVALID = "CATALOGUE_INVALID"
    DISTRIBUTION_INVALID = "DISTRIBUTION_INVALID"
    XML_SECURITY_REJECTED = "XML_SECURITY_REJECTED"
    XML_INVALID = "XML_INVALID"
    XML_SCHEMA_DRIFT = "XML_SCHEMA_DRIFT"
    XML_BOUNDS_EXCEEDED = "XML_BOUNDS_EXCEEDED"
    XML_DUPLICATE_IDENTITY = "XML_DUPLICATE_IDENTITY"


_ERROR_MESSAGES = MappingProxyType(
    {
        EuFsfSourceErrorCode.INVALID_URL: "EU FSF source URL is invalid",
        EuFsfSourceErrorCode.NETWORK_UNAVAILABLE: "EU FSF source is unavailable",
        EuFsfSourceErrorCode.HTTP_STATUS: "EU FSF source returned an invalid status",
        EuFsfSourceErrorCode.REDIRECT_INVALID: "EU FSF redirect is invalid",
        EuFsfSourceErrorCode.RESPONSE_TOO_LARGE: "EU FSF response exceeds its bound",
        EuFsfSourceErrorCode.RESPONSE_INVALID: "EU FSF response is invalid",
        EuFsfSourceErrorCode.CATALOGUE_INVALID: "EU FSF catalogue metadata is invalid",
        EuFsfSourceErrorCode.DISTRIBUTION_INVALID: (
            "EU FSF XML distribution metadata is invalid"
        ),
        EuFsfSourceErrorCode.XML_SECURITY_REJECTED: (
            "EU FSF XML contains a forbidden declaration"
        ),
        EuFsfSourceErrorCode.XML_INVALID: "EU FSF XML is invalid",
        EuFsfSourceErrorCode.XML_SCHEMA_DRIFT: "EU FSF XML schema is unsupported",
        EuFsfSourceErrorCode.XML_BOUNDS_EXCEEDED: "EU FSF XML exceeds a safe bound",
        EuFsfSourceErrorCode.XML_DUPLICATE_IDENTITY: (
            "EU FSF XML contains a duplicate identity"
        ),
    }
)


class EuFsfSourceError(Exception):
    """Stable source failure that never includes URLs, content, or identifiers."""

    def __init__(self, code: EuFsfSourceErrorCode) -> None:
        if not isinstance(code, EuFsfSourceErrorCode):
            raise ValueError("EU FSF error code must be typed")
        self.code = code
        super().__init__(_ERROR_MESSAGES[code])


class _HttpResponse(Protocol):
    status: int

    def getheader(self, name: str) -> str | None: ...

    def read(self, amount: int | None = None) -> bytes: ...


class _HttpsConnection(Protocol):
    def request(self, method: str, url: str, *, headers: dict[str, str]) -> None: ...

    def getresponse(self) -> _HttpResponse: ...

    def close(self) -> None: ...


type _ConnectionFactory = Callable[[str, float, ssl.SSLContext], _HttpsConnection]


def _default_connection(
    host: str, timeout: float, context: ssl.SSLContext
) -> _HttpsConnection:
    return http.client.HTTPSConnection(host, timeout=timeout, context=context)


def _fail(code: EuFsfSourceErrorCode) -> Never:
    raise EuFsfSourceError(code) from None


def _validate_official_url(url: str) -> None:
    if not isinstance(url, str) or not url:
        _fail(EuFsfSourceErrorCode.INVALID_URL)
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
        or parsed.fragment
    ):
        _fail(EuFsfSourceErrorCode.INVALID_URL)
    if parsed.hostname == _CATALOGUE_HOST:
        if url != EU_FSF_DATASET_URL:
            _fail(EuFsfSourceErrorCode.INVALID_URL)
        return
    if parsed.hostname != EU_FSF_DOWNLOAD_HOST or parsed.path != EU_FSF_DOWNLOAD_PATH:
        _fail(EuFsfSourceErrorCode.INVALID_URL)
    query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    if set(query) != {"token"} or len(query["token"]) != 1:
        _fail(EuFsfSourceErrorCode.INVALID_URL)
    if SAFE_TOKEN.fullmatch(query["token"][0]) is None:
        _fail(EuFsfSourceErrorCode.INVALID_URL)


class HttpsEuFsfTransport:
    """Small GET-only TLS transport with explicit redirect and byte controls."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        connection_factory: _ConnectionFactory = _default_connection,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 0 < timeout_seconds <= 120
        ):
            raise ValueError("timeout_seconds is invalid")
        if not callable(connection_factory):
            raise ValueError("connection_factory must be callable")
        self._timeout_seconds = float(timeout_seconds)
        self._connection_factory = connection_factory
        self._ssl_context = ssl_context or ssl.create_default_context(
            cafile=certifi.where()
        )

    def get(self, url: str, *, maximum_bytes: int) -> EuFsfHttpDocument:
        if (
            isinstance(maximum_bytes, bool)
            or not isinstance(maximum_bytes, int)
            or not 1 <= maximum_bytes <= MAX_EU_FSF_RAW_BYTES
        ):
            raise ValueError("maximum_bytes is invalid")
        current = url
        for redirect_count in range(_MAX_REDIRECTS + 1):
            _validate_official_url(current)
            parsed = urlsplit(current)
            host = parsed.hostname
            if host is None:  # Exact URL validation guarantees a host.
                _fail(EuFsfSourceErrorCode.INVALID_URL)  # pragma: no cover
            connection: _HttpsConnection | None = None
            try:
                connection = self._connection_factory(
                    host, self._timeout_seconds, self._ssl_context
                )
                target = parsed.path + (f"?{parsed.query}" if parsed.query else "")
                connection.request(
                    "GET",
                    target,
                    headers={
                        "Accept": "application/json, application/xml, text/xml",
                        "Accept-Encoding": "gzip, identity",
                        "User-Agent": "TradeSieve-EU-FSF/0.1",
                    },
                )
                response = connection.getresponse()
                if response.status in _REDIRECT_STATUSES:
                    location = response.getheader("Location")
                    if location is None or redirect_count == _MAX_REDIRECTS:
                        _fail(EuFsfSourceErrorCode.REDIRECT_INVALID)
                    current = urljoin(current, location)
                    _validate_official_url(current)
                    continue
                if response.status != 200:
                    _fail(EuFsfSourceErrorCode.HTTP_STATUS)
                content_encoding = (
                    response.getheader("Content-Encoding") or "identity"
                ).lower()
                if content_encoding not in {"gzip", "identity"}:
                    _fail(EuFsfSourceErrorCode.RESPONSE_INVALID)
                declared = response.getheader("Content-Length")
                if declared is not None:
                    try:
                        declared_length = int(declared)
                    except ValueError:
                        _fail(EuFsfSourceErrorCode.RESPONSE_INVALID)
                    if declared_length < 0:
                        _fail(EuFsfSourceErrorCode.RESPONSE_INVALID)
                    if declared_length > maximum_bytes:
                        _fail(EuFsfSourceErrorCode.RESPONSE_TOO_LARGE)
                chunks: list[bytes] = []
                total = 0
                decoded_total = 0
                decompressor = (
                    zlib.decompressobj(16 + zlib.MAX_WBITS)
                    if content_encoding == "gzip"
                    else None
                )
                while True:
                    chunk = response.read(
                        min(_READ_CHUNK_BYTES, maximum_bytes + 1 - total)
                    )
                    if not isinstance(chunk, bytes):
                        _fail(EuFsfSourceErrorCode.RESPONSE_INVALID)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > maximum_bytes:
                        _fail(EuFsfSourceErrorCode.RESPONSE_TOO_LARGE)
                if declared is not None and total != declared_length:
                    _fail(EuFsfSourceErrorCode.RESPONSE_INVALID)
                content = b"".join(chunks)
                if decompressor is not None:
                    decoded_chunks: list[bytes] = []
                    pending = content
                    try:
                        while pending:
                            decoded = decompressor.decompress(
                                pending, maximum_bytes + 1 - decoded_total
                            )
                            decoded_chunks.append(decoded)
                            decoded_total += len(decoded)
                            if decoded_total > maximum_bytes:
                                _fail(EuFsfSourceErrorCode.RESPONSE_TOO_LARGE)
                            next_pending = decompressor.unconsumed_tail
                            if next_pending and len(next_pending) == len(pending):
                                _fail(EuFsfSourceErrorCode.RESPONSE_INVALID)
                            pending = next_pending
                        decoded = decompressor.flush(maximum_bytes + 1 - decoded_total)
                    except zlib.error:
                        _fail(EuFsfSourceErrorCode.RESPONSE_INVALID)
                    decoded_chunks.append(decoded)
                    decoded_total += len(decoded)
                    if decoded_total > maximum_bytes:
                        _fail(EuFsfSourceErrorCode.RESPONSE_TOO_LARGE)
                    if (
                        not decompressor.eof
                        or decompressor.unused_data
                        or decompressor.unconsumed_tail
                    ):
                        _fail(EuFsfSourceErrorCode.RESPONSE_INVALID)
                    content = b"".join(decoded_chunks)
                content_type = response.getheader("Content-Type") or (
                    "application/octet-stream"
                )
                return EuFsfHttpDocument(
                    final_url=current,
                    content_type=content_type,
                    content=content,
                )
            except EuFsfSourceError:
                raise
            except Exception:
                _fail(EuFsfSourceErrorCode.NETWORK_UNAVAILABLE)
            finally:
                if connection is not None:  # pragma: no branch
                    with suppress(Exception):
                        connection.close()
        # The last redirect is rejected inside the loop before this point.
        _fail(EuFsfSourceErrorCode.REDIRECT_INVALID)  # pragma: no cover


@dataclass(frozen=True, slots=True)
class EuFsfDistribution:
    distribution_id: str
    download_url: str
    licence_id: str
    licence_resource: str

    def __post_init__(self) -> None:
        for value in (
            self.distribution_id,
            self.download_url,
            self.licence_id,
            self.licence_resource,
        ):
            if not isinstance(value, str) or not value:
                raise ValueError("distribution fields must be non-empty strings")
        _validate_official_url(self.download_url)
        if (
            self.licence_id != EU_FSF_REUSE_LICENCE_ID
            or self.licence_resource != EU_FSF_REUSE_LICENCE_RESOURCE
        ):
            raise ValueError("distribution licence is unsupported")


@dataclass(frozen=True, slots=True)
class EuFsfRetrievedSource:
    distribution: EuFsfDistribution
    retrieved_at: datetime
    content_type: str
    content_hash: str
    content: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.distribution, EuFsfDistribution):
            raise ValueError("distribution must be typed")
        if (
            not isinstance(self.retrieved_at, datetime)
            or self.retrieved_at.tzinfo is None
            or self.retrieved_at.utcoffset() is None
        ):
            raise ValueError("retrieved_at must be timezone-aware")
        if not isinstance(self.content_type, str) or not self.content_type:
            raise ValueError("content_type must be non-empty")
        if (
            not isinstance(self.content, bytes)
            or not 1 <= len(self.content) <= MAX_EU_FSF_RAW_BYTES
        ):
            raise ValueError("content is outside the EU FSF byte bound")
        expected = f"sha256:{hashlib.sha256(self.content).hexdigest()}"
        if self.content_hash != expected:
            raise ValueError("content_hash does not match source bytes")


class _DuplicateKey(Exception):
    pass


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey
        result[key] = value
    return result


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        _fail(EuFsfSourceErrorCode.CATALOGUE_INVALID)
    return value


def _one_string(value: object) -> str:
    if (
        not isinstance(value, list)
        or len(value) != 1
        or not isinstance(value[0], str)
        or not value[0]
    ):
        _fail(EuFsfSourceErrorCode.DISTRIBUTION_INVALID)
    return value[0]


class EuFsfOfficialSourceConnector:
    """Discover the current official XML distribution, then retrieve exact bytes."""

    def __init__(
        self,
        transport: EuFsfHttpTransport,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not hasattr(transport, "get"):
            raise ValueError("transport must implement bounded GET")
        self._transport = transport
        self._clock = clock or (lambda: datetime.now(UTC))

    def retrieve(self) -> EuFsfRetrievedSource:
        try:
            catalogue = self._transport.get(
                EU_FSF_DATASET_URL, maximum_bytes=MAX_EU_FSF_CATALOGUE_BYTES
            )
        except EuFsfSourceError:
            raise
        except Exception:
            _fail(EuFsfSourceErrorCode.NETWORK_UNAVAILABLE)
        if catalogue.final_url != EU_FSF_DATASET_URL:
            _fail(EuFsfSourceErrorCode.CATALOGUE_INVALID)
        media_type = catalogue.content_type.partition(";")[0].strip().lower()
        if media_type not in {"application/json", "application/ld+json"}:
            _fail(EuFsfSourceErrorCode.CATALOGUE_INVALID)
        try:
            decoded: object = json.loads(
                catalogue.content.decode("utf-8", errors="strict"),
                object_pairs_hook=_unique_object,
            )
        except (_DuplicateKey, UnicodeError, json.JSONDecodeError):
            _fail(EuFsfSourceErrorCode.CATALOGUE_INVALID)
        root = _object(decoded)
        result = _object(root.get("result"))
        if result.get("id") != EU_FSF_DATASET_ID:
            _fail(EuFsfSourceErrorCode.CATALOGUE_INVALID)
        distributions = result.get("distributions")
        if not isinstance(distributions, list) or not 1 <= len(distributions) <= 64:
            _fail(EuFsfSourceErrorCode.CATALOGUE_INVALID)
        candidates: list[EuFsfDistribution] = []
        for raw_distribution in distributions:
            distribution = _object(raw_distribution)
            title = distribution.get("title")
            file_format = distribution.get("format")
            if not isinstance(title, dict) or not isinstance(file_format, dict):
                continue
            if (
                title.get("en") != EU_FSF_DISTRIBUTION_TITLE
                or file_format.get("id") != "XML"
            ):
                continue
            licence = _object(distribution.get("license"))
            distribution_id = distribution.get("id")
            if not isinstance(distribution_id, str) or not distribution_id:
                _fail(EuFsfSourceErrorCode.DISTRIBUTION_INVALID)
            access_url = _one_string(distribution.get("access_url"))
            download_url = _one_string(distribution.get("download_url"))
            if access_url != download_url:
                _fail(EuFsfSourceErrorCode.DISTRIBUTION_INVALID)
            try:
                candidates.append(
                    EuFsfDistribution(
                        distribution_id=distribution_id,
                        download_url=download_url,
                        licence_id=str(licence.get("id", "")),
                        licence_resource=str(licence.get("resource", "")),
                    )
                )
            except (EuFsfSourceError, ValueError):
                _fail(EuFsfSourceErrorCode.DISTRIBUTION_INVALID)
        if len(candidates) != 1:
            _fail(EuFsfSourceErrorCode.DISTRIBUTION_INVALID)
        selected = candidates[0]
        try:
            downloaded = self._transport.get(
                selected.download_url, maximum_bytes=MAX_EU_FSF_RAW_BYTES
            )
        except EuFsfSourceError:
            raise
        except Exception:
            _fail(EuFsfSourceErrorCode.NETWORK_UNAVAILABLE)
        if downloaded.final_url != selected.download_url:
            _fail(EuFsfSourceErrorCode.RESPONSE_INVALID)
        downloaded_type = downloaded.content_type.partition(";")[0].strip().lower()
        if downloaded_type not in {
            "application/xml",
            "text/xml",
            "application/octet-stream",
        }:
            _fail(EuFsfSourceErrorCode.RESPONSE_INVALID)
        now = self._clock()
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            _fail(EuFsfSourceErrorCode.RESPONSE_INVALID)
        return EuFsfRetrievedSource(
            distribution=selected,
            retrieved_at=now,
            content_type=downloaded_type,
            content_hash=f"sha256:{hashlib.sha256(downloaded.content).hexdigest()}",
            content=downloaded.content,
        )


def _exact_attributes(
    element: ElementTree.Element,
    allowed: frozenset[str],
    required: frozenset[str],
) -> dict[str, str]:
    attributes = element.attrib
    if set(attributes) - allowed or not required.issubset(attributes):
        _fail(EuFsfSourceErrorCode.XML_SCHEMA_DRIFT)
    return attributes


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        _fail(EuFsfSourceErrorCode.XML_INVALID)


def _optional_date(value: str | None) -> date | None:
    return None if not value else _date(value)


def _boolean(value: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    _fail(EuFsfSourceErrorCode.XML_INVALID)


def _country(value: str | None) -> str | None:
    return None if value in {None, "", "00"} else value


def _text(element: ElementTree.Element) -> str:
    if list(element):
        _fail(EuFsfSourceErrorCode.XML_SCHEMA_DRIFT)
    value = (element.text or "").strip()
    if not value:
        _fail(EuFsfSourceErrorCode.XML_INVALID)
    return value


class EuFsfXmlParser:
    """Finite FSF 1.1 parser; XML cannot select code, entities, or network access."""

    def parse(self, content: bytes) -> EuFsfSnapshot:
        if (
            not isinstance(content, bytes)
            or not 1 <= len(content) <= MAX_EU_FSF_RAW_BYTES
        ):
            _fail(EuFsfSourceErrorCode.XML_BOUNDS_EXCEEDED)
        upper_prefix = content[:65_536].upper()
        if b"<!DOCTYPE" in upper_prefix or b"<!ENTITY" in upper_prefix:
            _fail(EuFsfSourceErrorCode.XML_SECURITY_REJECTED)
        entities: list[EuFsfEntity] = []
        root_attributes: dict[str, str] | None = None
        try:
            for event, element in ElementTree.iterparse(
                BytesIO(content), events=("start", "end")
            ):
                if event == "start" and root_attributes is None:
                    if element.tag != _ROOT:
                        _fail(EuFsfSourceErrorCode.XML_SCHEMA_DRIFT)
                    root_attributes = _exact_attributes(
                        element,
                        _ROOT_ATTRIBUTES,
                        _ROOT_ATTRIBUTES,
                    ).copy()
                elif event == "end" and element.tag == _ENTITY:
                    if len(entities) >= MAX_EU_FSF_ENTITIES:
                        _fail(EuFsfSourceErrorCode.XML_BOUNDS_EXCEEDED)
                    entities.append(self._entity(element))
                    element.clear()
                elif event == "end" and element.tag == _ROOT:
                    if any(child.tag != _ENTITY for child in element):
                        _fail(EuFsfSourceErrorCode.XML_SCHEMA_DRIFT)
        except EuFsfSourceError:
            raise
        except ElementTree.ParseError:
            _fail(EuFsfSourceErrorCode.XML_INVALID)
        except (TypeError, UnicodeError, ValueError):
            _fail(EuFsfSourceErrorCode.XML_INVALID)
        if root_attributes is None or not entities:
            _fail(EuFsfSourceErrorCode.XML_INVALID)
        entity_ids = [item.logical_id for item in entities]
        entity_refs = [item.eu_reference_number for item in entities]
        if len(set(entity_ids)) != len(entity_ids) or len(set(entity_refs)) != len(
            entity_refs
        ):
            _fail(EuFsfSourceErrorCode.XML_DUPLICATE_IDENTITY)
        try:
            generation = datetime.fromisoformat(root_attributes["generationDate"])
        except ValueError:
            _fail(EuFsfSourceErrorCode.XML_INVALID)
        if generation.tzinfo is None or generation.utcoffset() is None:
            _fail(EuFsfSourceErrorCode.XML_INVALID)
        return EuFsfSnapshot(
            generation_date=generation,
            global_file_id=root_attributes["globalFileId"],
            raw_content_hash=f"sha256:{hashlib.sha256(content).hexdigest()}",
            raw_byte_length=len(content),
            entities=tuple(sorted(entities, key=lambda item: int(item.logical_id))),
        )

    @staticmethod
    def _entity(element: ElementTree.Element) -> EuFsfEntity:
        attributes = _exact_attributes(
            element,
            _ENTITY_ATTRIBUTES,
            frozenset({"euReferenceNumber", "logicalId"}),
        )
        if any(child.tag not in _ENTITY_CHILDREN for child in element):
            _fail(EuFsfSourceErrorCode.XML_SCHEMA_DRIFT)
        regulations = [child for child in element if child.tag == _REGULATION]
        subject_types = [child for child in element if child.tag == _SUBJECT_TYPE]
        aliases = [child for child in element if child.tag == _ALIAS]
        identifiers = [child for child in element if child.tag == _IDENTIFICATION]
        if len(regulations) != 1 or len(subject_types) != 1 or not aliases:
            _fail(EuFsfSourceErrorCode.XML_INVALID)
        if (
            len(aliases) > MAX_EU_FSF_ALIASES_PER_ENTITY
            or len(identifiers) > MAX_EU_FSF_IDENTIFIERS_PER_ENTITY
        ):
            _fail(EuFsfSourceErrorCode.XML_BOUNDS_EXCEEDED)
        logical_id = attributes["logicalId"]
        alias_values = tuple(
            sorted(
                (EuFsfXmlParser._alias(item, logical_id) for item in aliases),
                key=lambda item: int(item.logical_id),
            )
        )
        substantive_identifiers = [
            item for item in identifiers if item.attrib.get("number") != "-"
        ]
        identifier_values = tuple(
            sorted(
                (
                    EuFsfXmlParser._identifier(item, logical_id)
                    for item in substantive_identifiers
                ),
                key=lambda item: int(item.logical_id),
            )
        )
        if len({item.logical_id for item in alias_values}) != len(alias_values) or len(
            {item.logical_id for item in identifier_values}
        ) != len(identifier_values):
            _fail(EuFsfSourceErrorCode.XML_DUPLICATE_IDENTITY)
        subject = _exact_attributes(
            subject_types[0], _SUBJECT_ATTRIBUTES, frozenset({"code"})
        )
        try:
            subject_type = EuFsfSubjectType(subject["code"])
        except ValueError:
            _fail(EuFsfSourceErrorCode.XML_SCHEMA_DRIFT)
        return EuFsfEntity(
            logical_id=logical_id,
            eu_reference_number=attributes["euReferenceNumber"],
            united_nations_id=attributes.get("unitedNationId") or None,
            designation_date=_optional_date(attributes.get("designationDate")),
            subject_type=subject_type,
            regulation=EuFsfXmlParser._regulation(regulations[0], logical_id),
            aliases=alias_values,
            identifiers=identifier_values,
        )

    @staticmethod
    def _regulation(element: ElementTree.Element, entity_id: str) -> EuFsfRegulation:
        attributes = _exact_attributes(
            element,
            _REGULATION_ATTRIBUTES,
            frozenset(
                {
                    "publicationDate",
                    "entryIntoForceDate",
                    "numberTitle",
                    "programme",
                    "logicalId",
                }
            ),
        )
        children = list(element)
        if len(children) != 1 or children[0].tag != _PUBLICATION_URL:
            _fail(EuFsfSourceErrorCode.XML_SCHEMA_DRIFT)
        return EuFsfRegulation(
            programme=attributes["programme"],
            number_title=attributes["numberTitle"],
            publication_date=_date(attributes["publicationDate"]),
            entry_into_force_date=_date(attributes["entryIntoForceDate"]),
            publication_url=_text(children[0]),
            native_locator=(
                f"/export/sanctionEntity[@logicalId='{entity_id}']/regulation"
                f"[@logicalId='{attributes['logicalId']}']"
            ),
        )

    @staticmethod
    def _alias(element: ElementTree.Element, entity_id: str) -> EuFsfAlias:
        attributes = _exact_attributes(
            element,
            _ALIAS_ATTRIBUTES,
            frozenset({"wholeName", "strong", "logicalId"}),
        )
        if any(child.tag not in {_REMARK, _REGULATION_SUMMARY} for child in element):
            _fail(EuFsfSourceErrorCode.XML_SCHEMA_DRIFT)
        logical_id = attributes["logicalId"]
        return EuFsfAlias.create(
            logical_id=logical_id,
            whole_name=attributes["wholeName"],
            strong=_boolean(attributes["strong"]),
            language=attributes.get("nameLanguage") or None,
            native_locator=(
                f"/export/sanctionEntity[@logicalId='{entity_id}']/nameAlias"
                f"[@logicalId='{logical_id}']/@wholeName"
            ),
        )

    @staticmethod
    def _identifier(element: ElementTree.Element, entity_id: str) -> EuFsfIdentifier:
        attributes = _exact_attributes(
            element,
            _IDENTIFIER_ATTRIBUTES,
            frozenset(
                {
                    "knownExpired",
                    "knownFalse",
                    "reportedLost",
                    "revokedByIssuer",
                    "number",
                    "identificationTypeCode",
                    "logicalId",
                }
            ),
        )
        if any(child.tag not in {_REMARK, _REGULATION_SUMMARY} for child in element):
            _fail(EuFsfSourceErrorCode.XML_SCHEMA_DRIFT)
        logical_id = attributes["logicalId"]
        return EuFsfIdentifier.create(
            logical_id=logical_id,
            type_code=attributes["identificationTypeCode"],
            number=attributes["number"],
            country_code=_country(attributes.get("countryIso2Code")),
            known_expired=_boolean(attributes["knownExpired"]),
            known_false=_boolean(attributes["knownFalse"]),
            reported_lost=_boolean(attributes["reportedLost"]),
            revoked_by_issuer=_boolean(attributes["revokedByIssuer"]),
            native_locator=(
                f"/export/sanctionEntity[@logicalId='{entity_id}']/identification"
                f"[@logicalId='{logical_id}']/@number"
            ),
        )
