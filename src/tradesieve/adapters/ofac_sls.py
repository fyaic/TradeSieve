"""Fixed-host OFAC SLS retrieval and finite legacy-XML parsing."""

from __future__ import annotations

import hashlib
import http.client
import re
import ssl
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum
from io import BytesIO
from typing import Final, Never, Protocol, cast
from urllib.parse import parse_qsl, urlsplit
from xml.etree import ElementTree

import certifi

from tradesieve.domain.ofac_sls import (
    MAX_OFAC_ADDRESSES_PER_ENTRY,
    MAX_OFAC_ALIASES_PER_ENTRY,
    MAX_OFAC_ENTRIES,
    MAX_OFAC_FACTS_PER_ENTRY,
    MAX_OFAC_IDENTIFIERS_PER_ENTRY,
    MAX_OFAC_PROGRAMS_PER_ENTRY,
    MAX_OFAC_RAW_BYTES,
    OFAC_SLS_HOST,
    OFAC_SLS_NAMESPACE,
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
from tradesieve.ports.ofac_sls import OfacSlsHttpDocument, OfacSlsHttpTransport

_BASE_URL: Final = f"https://{OFAC_SLS_HOST}/api/PublicationPreview/exports"
OFAC_SDN_XML_URL: Final = f"{_BASE_URL}/SDN.XML"
OFAC_CONSOLIDATED_XML_URL: Final = f"{_BASE_URL}/CONSOLIDATED.XML"
OFAC_XML_SCHEMA_URL: Final = f"{_BASE_URL}/XML.xsd"
OFAC_LIST_URLS: Final = {
    OfacSlsListKind.SDN: OFAC_SDN_XML_URL,
    OfacSlsListKind.CONSOLIDATED: OFAC_CONSOLIDATED_XML_URL,
}
_ALLOWED_URLS: Final = frozenset((*OFAC_LIST_URLS.values(), OFAC_XML_SCHEMA_URL))
_OFAC_DOWNLOAD_HOST: Final = (
    "wc2h-sls-prod-public-published.s3.us-gov-west-1.amazonaws.com"
)
_REDIRECT_QUERY_FIELDS: Final = frozenset(
    {
        "X-Amz-Algorithm",
        "X-Amz-Credential",
        "X-Amz-Date",
        "X-Amz-Expires",
        "X-Amz-Security-Token",
        "X-Amz-Signature",
        "X-Amz-SignedHeaders",
        "response-content-disposition",
        "response-content-type",
    }
)
_REDIRECT_PATH: Final = re.compile(
    r"^/Published/[A-Za-z0-9._/-]{1,768}/(SDN|CONSOLIDATED)\.XML$"
)
_DIGEST: Final = re.compile(r"^sha-256(?:=|:)?([a-fA-F0-9]{64})$")


def _tag(local_name: str) -> str:
    return f"{{{OFAC_SLS_NAMESPACE}}}{local_name}"


_ROOT: Final = _tag("sdnList")
_PUBLISH: Final = _tag("publshInformation")
_ENTRY: Final = _tag("sdnEntry")
_ENTRY_CHILDREN: Final = frozenset(
    {
        _tag("uid"),
        _tag("firstName"),
        _tag("lastName"),
        _tag("title"),
        _tag("sdnType"),
        _tag("remarks"),
        _tag("programList"),
        _tag("idList"),
        _tag("akaList"),
        _tag("addressList"),
        _tag("nationalityList"),
        _tag("citizenshipList"),
        _tag("dateOfBirthList"),
        _tag("placeOfBirthList"),
        _tag("vesselInfo"),
    }
)


class OfacSlsSourceErrorCode(StrEnum):
    URL_INVALID = "URL_INVALID"
    NETWORK_UNAVAILABLE = "NETWORK_UNAVAILABLE"
    HTTP_STATUS_INVALID = "HTTP_STATUS_INVALID"
    RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"
    RESPONSE_INVALID = "RESPONSE_INVALID"
    CONTENT_DIGEST_MISMATCH = "CONTENT_DIGEST_MISMATCH"
    XML_SECURITY_REJECTED = "XML_SECURITY_REJECTED"
    XML_SCHEMA_DRIFT = "XML_SCHEMA_DRIFT"
    XML_INVALID = "XML_INVALID"
    XML_BOUNDS_EXCEEDED = "XML_BOUNDS_EXCEEDED"
    XML_DUPLICATE_IDENTITY = "XML_DUPLICATE_IDENTITY"
    RECORD_COUNT_MISMATCH = "RECORD_COUNT_MISMATCH"


class OfacSlsSourceError(Exception):
    def __init__(self, code: OfacSlsSourceErrorCode) -> None:
        if not isinstance(code, OfacSlsSourceErrorCode):
            raise ValueError("OFAC source error code must be typed")
        self.code = code
        super().__init__(code.value)


def _fail(code: OfacSlsSourceErrorCode) -> Never:
    raise OfacSlsSourceError(code)


class _HttpResponse(Protocol):
    status: int

    def getheader(self, name: str) -> str | None: ...

    def read(self, amount: int | None = None) -> bytes: ...


class _HttpsConnection(Protocol):
    def request(
        self, method: str, url: str, *, headers: dict[str, str] | None = None
    ) -> None: ...

    def getresponse(self) -> _HttpResponse: ...

    def close(self) -> None: ...


def _default_connection(host: str, timeout: float) -> _HttpsConnection:
    return cast(
        _HttpsConnection,
        http.client.HTTPSConnection(
            host,
            timeout=timeout,
            context=ssl.create_default_context(cafile=certifi.where()),
        ),
    )


def _validate_url(url: str) -> None:
    if not isinstance(url, str) or url not in _ALLOWED_URLS:
        _fail(OfacSlsSourceErrorCode.URL_INVALID)
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != OFAC_SLS_HOST
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.query
        or parsed.path not in {urlsplit(item).path for item in _ALLOWED_URLS}
    ):
        _fail(OfacSlsSourceErrorCode.URL_INVALID)


def _validated_redirect_target(location: str | None, source_url: str) -> str:
    if not isinstance(location, str) or not 1 <= len(location) <= 16_384:
        _fail(OfacSlsSourceErrorCode.RESPONSE_INVALID)
    parsed = urlsplit(location)
    path_match = _REDIRECT_PATH.fullmatch(parsed.path)
    expected_name = urlsplit(source_url).path.rsplit("/", 1)[-1]
    try:
        pairs = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=16,
        )
    except ValueError:
        _fail(OfacSlsSourceErrorCode.RESPONSE_INVALID)
    query = dict(pairs)
    if (
        parsed.scheme != "https"
        or parsed.hostname != _OFAC_DOWNLOAD_HOST
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or path_match is None
        or path_match.group(1) + ".XML" != expected_name
        or ".." in parsed.path.split("/")
        or len(query) != len(pairs)
        or frozenset(query) != _REDIRECT_QUERY_FIELDS
        or any(not value or len(value) > 8192 for value in query.values())
        or query.get("X-Amz-Algorithm") != "AWS4-HMAC-SHA256"
        or query.get("X-Amz-SignedHeaders") != "host"
        or query.get("response-content-type") != "text/xml"
        or not query.get("X-Amz-Credential", "").endswith(
            "/us-gov-west-1/s3/aws4_request"
        )
        or re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", query.get("X-Amz-Date", "")) is None
        or re.fullmatch(r"[a-f0-9]{64}", query.get("X-Amz-Signature", "")) is None
    ):
        _fail(OfacSlsSourceErrorCode.RESPONSE_INVALID)
    try:
        expires = int(query["X-Amz-Expires"])
    except ValueError:
        _fail(OfacSlsSourceErrorCode.RESPONSE_INVALID)
    if not 1 <= expires <= 3600:
        _fail(OfacSlsSourceErrorCode.RESPONSE_INVALID)
    return f"{parsed.path}?{parsed.query}"


class HttpsOfacSlsTransport:
    """Fixed-host transport accepting one tightly validated official S3 redirect."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        connection_factory: Callable[[str, float], _HttpsConnection] | None = None,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 0 < timeout_seconds <= 120
        ):
            raise ValueError("timeout_seconds is invalid")
        self._timeout_seconds = float(timeout_seconds)
        self._connection_factory = connection_factory or _default_connection

    def get(self, url: str, *, maximum_bytes: int) -> OfacSlsHttpDocument:
        _validate_url(url)
        if (
            isinstance(maximum_bytes, bool)
            or not isinstance(maximum_bytes, int)
            or not 1 <= maximum_bytes <= MAX_OFAC_RAW_BYTES
        ):
            raise ValueError("maximum_bytes is invalid")
        parsed = urlsplit(url)
        connection: _HttpsConnection | None = None
        try:
            connection = self._connection_factory(OFAC_SLS_HOST, self._timeout_seconds)
            connection.request(
                "GET",
                parsed.path,
                headers={
                    "Accept": "application/xml,text/xml;q=0.9,*/*;q=0.1",
                    "Accept-Encoding": "identity",
                    "User-Agent": "TradeSieve/1 OFAC-SLS-source-worker",
                },
            )
            response = connection.getresponse()
            if response.status == 302:
                redirect_target = _validated_redirect_target(
                    response.getheader("Location"), url
                )
                with suppress(Exception):
                    connection.close()
                connection = None
                connection = self._connection_factory(
                    _OFAC_DOWNLOAD_HOST, self._timeout_seconds
                )
                connection.request(
                    "GET",
                    redirect_target,
                    headers={
                        "Accept": "application/xml,text/xml;q=0.9,*/*;q=0.1",
                        "Accept-Encoding": "identity",
                        "User-Agent": "TradeSieve/1 OFAC-SLS-source-worker",
                    },
                )
                response = connection.getresponse()
            if response.status != 200:
                _fail(OfacSlsSourceErrorCode.HTTP_STATUS_INVALID)
            encoding = (response.getheader("Content-Encoding") or "identity").lower()
            if encoding != "identity":
                _fail(OfacSlsSourceErrorCode.RESPONSE_INVALID)
            length_header = response.getheader("Content-Length")
            if length_header is not None:
                try:
                    length = int(length_header)
                except ValueError:
                    _fail(OfacSlsSourceErrorCode.RESPONSE_INVALID)
                if length < 1:
                    _fail(OfacSlsSourceErrorCode.RESPONSE_INVALID)
                if length > maximum_bytes:
                    _fail(OfacSlsSourceErrorCode.RESPONSE_TOO_LARGE)
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(min(65_536, maximum_bytes + 1 - total))
                if not isinstance(chunk, bytes):
                    _fail(OfacSlsSourceErrorCode.RESPONSE_INVALID)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > maximum_bytes:
                    _fail(OfacSlsSourceErrorCode.RESPONSE_TOO_LARGE)
            content = b"".join(chunks)
            if not content:
                _fail(OfacSlsSourceErrorCode.RESPONSE_INVALID)
            return OfacSlsHttpDocument(
                final_url=url,
                content_type=response.getheader("Content-Type")
                or "application/octet-stream",
                content=content,
                last_modified=response.getheader("Last-Modified"),
                digest=response.getheader("Digest"),
            )
        except OfacSlsSourceError:
            raise
        except Exception:
            _fail(OfacSlsSourceErrorCode.NETWORK_UNAVAILABLE)
        finally:
            if connection is not None:  # pragma: no branch
                with suppress(Exception):
                    connection.close()


@dataclass(frozen=True, slots=True)
class OfacSlsRetrievedSource:
    list_kind: OfacSlsListKind
    retrieved_at: datetime
    source_last_modified: datetime | None
    content_type: str
    server_digest: str | None
    content_hash: str
    content: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.list_kind, OfacSlsListKind):
            raise ValueError("list_kind must be typed")
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
        if not isinstance(self.content_type, str) or not self.content_type:
            raise ValueError("content_type must be non-empty")
        if (
            not isinstance(self.content, bytes)
            or not 1 <= len(self.content) <= MAX_OFAC_RAW_BYTES
        ):
            raise ValueError("content is outside the OFAC byte bound")
        expected = f"sha256:{hashlib.sha256(self.content).hexdigest()}"
        if self.content_hash != expected:
            raise ValueError("content_hash does not match source bytes")
        if self.server_digest is not None and self.server_digest != expected:
            raise ValueError("server_digest does not match source bytes")


def _last_modified(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        result = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        _fail(OfacSlsSourceErrorCode.RESPONSE_INVALID)
    if result.tzinfo is None or result.utcoffset() is None:
        _fail(OfacSlsSourceErrorCode.RESPONSE_INVALID)
    return result.astimezone(UTC)


def _server_digest(value: str | None) -> str | None:
    if value is None:
        return None
    matched = _DIGEST.fullmatch(value.strip())
    if matched is None:
        _fail(OfacSlsSourceErrorCode.RESPONSE_INVALID)
    return f"sha256:{matched.group(1).lower()}"


class OfacSlsOfficialSourceConnector:
    """Retrieve one of the two fixed official comprehensive list files."""

    def __init__(
        self,
        transport: OfacSlsHttpTransport,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not hasattr(transport, "get"):
            raise ValueError("transport must implement bounded GET")
        self._transport = transport
        self._clock = clock or (lambda: datetime.now(UTC))

    def retrieve(self, list_kind: OfacSlsListKind) -> OfacSlsRetrievedSource:
        if not isinstance(list_kind, OfacSlsListKind):
            raise ValueError("list_kind must be typed")
        url = OFAC_LIST_URLS[list_kind]
        try:
            document = self._transport.get(url, maximum_bytes=MAX_OFAC_RAW_BYTES)
        except OfacSlsSourceError:
            raise
        except Exception:
            _fail(OfacSlsSourceErrorCode.NETWORK_UNAVAILABLE)
        if document.final_url != url:
            _fail(OfacSlsSourceErrorCode.RESPONSE_INVALID)
        media_type = document.content_type.partition(";")[0].strip().lower()
        if media_type not in {
            "application/xml",
            "text/xml",
            "application/octet-stream",
        }:
            _fail(OfacSlsSourceErrorCode.RESPONSE_INVALID)
        now = self._clock()
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            _fail(OfacSlsSourceErrorCode.RESPONSE_INVALID)
        content_hash = f"sha256:{hashlib.sha256(document.content).hexdigest()}"
        digest = _server_digest(document.digest)
        if digest is not None and digest != content_hash:
            _fail(OfacSlsSourceErrorCode.CONTENT_DIGEST_MISMATCH)
        return OfacSlsRetrievedSource(
            list_kind=list_kind,
            retrieved_at=now,
            source_last_modified=_last_modified(document.last_modified),
            content_type=media_type,
            server_digest=digest,
            content_hash=content_hash,
            content=document.content,
        )


def _no_attributes(element: ElementTree.Element) -> None:
    if element.attrib:
        _fail(OfacSlsSourceErrorCode.XML_SCHEMA_DRIFT)


def _children_allowed(element: ElementTree.Element, allowed: frozenset[str]) -> None:
    if any(child.tag not in allowed for child in element):
        _fail(OfacSlsSourceErrorCode.XML_SCHEMA_DRIFT)


def _children(
    element: ElementTree.Element, local_name: str
) -> list[ElementTree.Element]:
    return [child for child in element if child.tag == _tag(local_name)]


def _one(
    element: ElementTree.Element, local_name: str, *, required: bool = True
) -> ElementTree.Element | None:
    values = _children(element, local_name)
    if len(values) > 1 or (required and len(values) != 1):
        _fail(OfacSlsSourceErrorCode.XML_SCHEMA_DRIFT)
    return values[0] if values else None


def _text(element: ElementTree.Element) -> str:
    _no_attributes(element)
    if list(element):
        _fail(OfacSlsSourceErrorCode.XML_SCHEMA_DRIFT)
    value = (element.text or "").strip()
    if not value:
        _fail(OfacSlsSourceErrorCode.XML_INVALID)
    return value


def _one_text(
    element: ElementTree.Element, local_name: str, *, required: bool = True
) -> str | None:
    child = _one(element, local_name, required=required)
    return _text(child) if child is not None else None


def _boolean(value: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    _fail(OfacSlsSourceErrorCode.XML_INVALID)


def _optional_integer(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        result = int(value)
    except ValueError:
        _fail(OfacSlsSourceErrorCode.XML_INVALID)
    if result < 0:
        _fail(OfacSlsSourceErrorCode.XML_INVALID)
    return result


def _ofac_date(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%m/%d/%Y").replace(tzinfo=UTC)
    except ValueError:
        _fail(OfacSlsSourceErrorCode.XML_INVALID)


class OfacSlsXmlParser:
    """Finite parser for OFAC's supported legacy SDN/CONSOLIDATED XML schema."""

    def parse(self, source: OfacSlsRetrievedSource) -> OfacSnapshot:
        if not isinstance(source, OfacSlsRetrievedSource):
            raise ValueError("parser requires a retrieved OFAC source")
        content = source.content
        upper_prefix = content[:65_536].upper()
        if b"<!DOCTYPE" in upper_prefix or b"<!ENTITY" in upper_prefix:
            _fail(OfacSlsSourceErrorCode.XML_SECURITY_REJECTED)
        entries: list[OfacEntry] = []
        publish_date: datetime | None = None
        declared_count: int | None = None
        root_seen = False
        publish_seen = False
        try:
            for event, element in ElementTree.iterparse(
                BytesIO(content), events=("start", "end")
            ):
                if event == "start" and not root_seen:
                    if element.tag != _ROOT:
                        _fail(OfacSlsSourceErrorCode.XML_SCHEMA_DRIFT)
                    _no_attributes(element)
                    root_seen = True
                elif event == "end" and element.tag == _PUBLISH:
                    if publish_seen:
                        _fail(OfacSlsSourceErrorCode.XML_SCHEMA_DRIFT)
                    publish_date, declared_count = self._publish(element)
                    publish_seen = True
                    element.clear()
                elif event == "end" and element.tag == _ENTRY:
                    if not publish_seen:
                        _fail(OfacSlsSourceErrorCode.XML_SCHEMA_DRIFT)
                    if len(entries) >= MAX_OFAC_ENTRIES:
                        _fail(OfacSlsSourceErrorCode.XML_BOUNDS_EXCEEDED)
                    entries.append(self._entry(element, source.list_kind))
                    element.clear()
                elif event == "end" and element.tag == _ROOT:
                    if any(child.tag not in {_PUBLISH, _ENTRY} for child in element):
                        _fail(OfacSlsSourceErrorCode.XML_SCHEMA_DRIFT)
        except OfacSlsSourceError:
            raise
        except ElementTree.ParseError:
            _fail(OfacSlsSourceErrorCode.XML_INVALID)
        except (TypeError, UnicodeError, ValueError):
            _fail(OfacSlsSourceErrorCode.XML_INVALID)
        if (
            not root_seen
            or not publish_seen
            or publish_date is None
            or declared_count is None
            or not entries
        ):
            _fail(OfacSlsSourceErrorCode.XML_INVALID)
        uids = [item.uid for item in entries]
        if len(set(uids)) != len(uids):
            _fail(OfacSlsSourceErrorCode.XML_DUPLICATE_IDENTITY)
        if declared_count != len(entries):
            _fail(OfacSlsSourceErrorCode.RECORD_COUNT_MISMATCH)
        return OfacSnapshot(
            list_kind=source.list_kind,
            publish_date=publish_date.date(),
            declared_record_count=declared_count,
            retrieved_at=source.retrieved_at,
            source_last_modified=source.source_last_modified,
            raw_content_hash=source.content_hash,
            raw_byte_length=len(content),
            entries=tuple(sorted(entries, key=lambda item: int(item.uid))),
        )

    @staticmethod
    def _publish(element: ElementTree.Element) -> tuple[datetime, int]:
        _no_attributes(element)
        _children_allowed(
            element, frozenset({_tag("Publish_Date"), _tag("Record_Count")})
        )
        publish = _one_text(element, "Publish_Date")
        count_text = _one_text(element, "Record_Count")
        assert publish is not None and count_text is not None
        try:
            count = int(count_text)
        except ValueError:
            _fail(OfacSlsSourceErrorCode.XML_INVALID)
        if count < 1 or count > MAX_OFAC_ENTRIES:
            _fail(OfacSlsSourceErrorCode.XML_BOUNDS_EXCEEDED)
        return _ofac_date(publish), count

    @staticmethod
    def _entry(element: ElementTree.Element, list_kind: OfacSlsListKind) -> OfacEntry:
        _no_attributes(element)
        _children_allowed(element, _ENTRY_CHILDREN)
        uid = _one_text(element, "uid")
        first_name = _one_text(element, "firstName", required=False)
        last_name = _one_text(element, "lastName")
        title = _one_text(element, "title", required=False)
        type_text = _one_text(element, "sdnType")
        remarks = _one_text(element, "remarks", required=False)
        assert uid is not None and last_name is not None and type_text is not None
        try:
            subject_type = OfacSubjectType(type_text)
        except ValueError:
            _fail(OfacSlsSourceErrorCode.XML_SCHEMA_DRIFT)
        program_list = _one(element, "programList")
        assert program_list is not None
        _no_attributes(program_list)
        _children_allowed(program_list, frozenset({_tag("program")}))
        programs = [_text(item) for item in _children(program_list, "program")]
        if not programs:
            _fail(OfacSlsSourceErrorCode.XML_INVALID)
        if len(programs) > MAX_OFAC_PROGRAMS_PER_ENTRY:
            _fail(OfacSlsSourceErrorCode.XML_BOUNDS_EXCEEDED)
        if len(set(programs)) != len(programs):
            _fail(OfacSlsSourceErrorCode.XML_DUPLICATE_IDENTITY)
        aliases = OfacSlsXmlParser._aliases(element, uid)
        addresses = OfacSlsXmlParser._addresses(element, uid)
        identifiers = OfacSlsXmlParser._identifiers(element, uid)
        facts = OfacSlsXmlParser._facts(element, uid)
        vessel = OfacSlsXmlParser._vessel(element, uid)
        for values in (aliases, addresses, identifiers, facts):
            value_uids = [item.uid for item in values]
            if len(set(value_uids)) != len(value_uids):
                _fail(OfacSlsSourceErrorCode.XML_DUPLICATE_IDENTITY)
        try:
            return OfacEntry.create(
                uid=uid,
                list_kind=list_kind,
                first_name=first_name,
                last_name=last_name,
                title=title,
                subject_type=subject_type,
                remarks=remarks,
                programs=tuple(sorted(programs)),
                aliases=tuple(sorted(aliases, key=lambda item: int(item.uid))),
                addresses=tuple(sorted(addresses, key=lambda item: int(item.uid))),
                identifiers=tuple(sorted(identifiers, key=lambda item: int(item.uid))),
                facts=tuple(sorted(facts, key=lambda item: int(item.uid))),
                vessel_info=vessel,
            )
        except ValueError:
            _fail(OfacSlsSourceErrorCode.XML_INVALID)

    @staticmethod
    def _aliases(element: ElementTree.Element, entry_uid: str) -> list[OfacAlias]:
        container = _one(element, "akaList", required=False)
        if container is None:
            return []
        _no_attributes(container)
        _children_allowed(container, frozenset({_tag("aka")}))
        items = _children(container, "aka")
        if len(items) > MAX_OFAC_ALIASES_PER_ENTRY:
            _fail(OfacSlsSourceErrorCode.XML_BOUNDS_EXCEEDED)
        result: list[OfacAlias] = []
        allowed = frozenset(
            {
                _tag("uid"),
                _tag("type"),
                _tag("category"),
                _tag("firstName"),
                _tag("lastName"),
            }
        )
        for item in items:
            _no_attributes(item)
            _children_allowed(item, allowed)
            uid = _one_text(item, "uid")
            alias_type = _one_text(item, "type")
            category = _one_text(item, "category")
            first = _one_text(item, "firstName", required=False)
            last = _one_text(item, "lastName", required=False)
            if not first and not last:
                _fail(OfacSlsSourceErrorCode.XML_INVALID)
            assert uid is not None and alias_type is not None and category is not None
            whole_name = f"{first} {last}" if first and last else (first or last)
            assert whole_name is not None
            try:
                result.append(
                    OfacAlias.create(
                        uid=uid,
                        alias_type=alias_type,
                        category=category,
                        whole_name=whole_name,
                        native_locator=(
                            f"/sdnList/sdnEntry[uid='{entry_uid}']/akaList/aka"
                            f"[uid='{uid}']"
                        ),
                    )
                )
            except ValueError:
                _fail(OfacSlsSourceErrorCode.XML_INVALID)
        return result

    @staticmethod
    def _addresses(element: ElementTree.Element, entry_uid: str) -> list[OfacAddress]:
        container = _one(element, "addressList", required=False)
        if container is None:
            return []
        _no_attributes(container)
        _children_allowed(container, frozenset({_tag("address")}))
        items = _children(container, "address")
        if len(items) > MAX_OFAC_ADDRESSES_PER_ENTRY:
            _fail(OfacSlsSourceErrorCode.XML_BOUNDS_EXCEEDED)
        allowed_names = (
            "uid",
            "address1",
            "address2",
            "address3",
            "city",
            "stateOrProvince",
            "postalCode",
            "country",
            "region",
        )
        allowed = frozenset(_tag(name) for name in allowed_names)
        result: list[OfacAddress] = []
        for item in items:
            _no_attributes(item)
            _children_allowed(item, allowed)
            uid = _one_text(item, "uid")
            assert uid is not None
            lines = tuple(
                value
                for value in (
                    _one_text(item, "address1", required=False),
                    _one_text(item, "address2", required=False),
                    _one_text(item, "address3", required=False),
                )
                if value is not None
            )
            try:
                result.append(
                    OfacAddress(
                        uid=uid,
                        address_lines=lines,
                        city=_one_text(item, "city", required=False),
                        state_or_province=_one_text(
                            item, "stateOrProvince", required=False
                        ),
                        postal_code=_one_text(item, "postalCode", required=False),
                        country=_one_text(item, "country", required=False),
                        region=_one_text(item, "region", required=False),
                        native_locator=(
                            f"/sdnList/sdnEntry[uid='{entry_uid}']/addressList/"
                            f"address[uid='{uid}']"
                        ),
                    )
                )
            except ValueError:
                _fail(OfacSlsSourceErrorCode.XML_INVALID)
        return result

    @staticmethod
    def _identifiers(
        element: ElementTree.Element, entry_uid: str
    ) -> list[OfacIdentifier]:
        container = _one(element, "idList", required=False)
        if container is None:
            return []
        _no_attributes(container)
        _children_allowed(container, frozenset({_tag("id")}))
        items = _children(container, "id")
        if len(items) > MAX_OFAC_IDENTIFIERS_PER_ENTRY:
            _fail(OfacSlsSourceErrorCode.XML_BOUNDS_EXCEEDED)
        allowed_names = (
            "uid",
            "idType",
            "idNumber",
            "idCountry",
            "issueDate",
            "expirationDate",
        )
        allowed = frozenset(_tag(name) for name in allowed_names)
        result: list[OfacIdentifier] = []
        for item in items:
            _no_attributes(item)
            _children_allowed(item, allowed)
            uid = _one_text(item, "uid")
            type_code = _one_text(item, "idType")
            number = _one_text(item, "idNumber")
            assert uid is not None and type_code is not None and number is not None
            try:
                result.append(
                    OfacIdentifier.create(
                        uid=uid,
                        type_code=type_code,
                        number=number,
                        country=_one_text(item, "idCountry", required=False),
                        issue_date=_one_text(item, "issueDate", required=False),
                        expiration_date=_one_text(
                            item, "expirationDate", required=False
                        ),
                        native_locator=(
                            f"/sdnList/sdnEntry[uid='{entry_uid}']/idList/id"
                            f"[uid='{uid}']/idNumber"
                        ),
                    )
                )
            except ValueError:
                _fail(OfacSlsSourceErrorCode.XML_INVALID)
        return result

    @staticmethod
    def _facts(element: ElementTree.Element, entry_uid: str) -> list[OfacFact]:
        configurations = (
            (
                "nationalityList",
                "nationality",
                "country",
                OfacFactKind.NATIONALITY,
            ),
            (
                "citizenshipList",
                "citizenship",
                "country",
                OfacFactKind.CITIZENSHIP,
            ),
            (
                "dateOfBirthList",
                "dateOfBirthItem",
                "dateOfBirth",
                OfacFactKind.DATE_OF_BIRTH,
            ),
            (
                "placeOfBirthList",
                "placeOfBirthItem",
                "placeOfBirth",
                OfacFactKind.PLACE_OF_BIRTH,
            ),
        )
        result: list[OfacFact] = []
        for container_name, item_name, value_name, kind in configurations:
            container = _one(element, container_name, required=False)
            if container is None:
                continue
            _no_attributes(container)
            _children_allowed(container, frozenset({_tag(item_name)}))
            for item in _children(container, item_name):
                _no_attributes(item)
                _children_allowed(
                    item,
                    frozenset({_tag("uid"), _tag(value_name), _tag("mainEntry")}),
                )
                uid = _one_text(item, "uid")
                value = _one_text(item, value_name)
                main = _one_text(item, "mainEntry")
                assert uid is not None and value is not None and main is not None
                try:
                    result.append(
                        OfacFact(
                            kind=kind,
                            uid=uid,
                            value=value,
                            main_entry=_boolean(main),
                            native_locator=(
                                f"/sdnList/sdnEntry[uid='{entry_uid}']/"
                                f"{container_name}/{item_name}[uid='{uid}']"
                            ),
                        )
                    )
                except ValueError:
                    _fail(OfacSlsSourceErrorCode.XML_INVALID)
                if len(result) > MAX_OFAC_FACTS_PER_ENTRY:
                    _fail(OfacSlsSourceErrorCode.XML_BOUNDS_EXCEEDED)
        return result

    @staticmethod
    def _vessel(element: ElementTree.Element, entry_uid: str) -> OfacVesselInfo | None:
        vessel = _one(element, "vesselInfo", required=False)
        if vessel is None:
            return None
        _no_attributes(vessel)
        allowed_names = (
            "callSign",
            "vesselType",
            "vesselFlag",
            "vesselOwner",
            "tonnage",
            "grossRegisteredTonnage",
        )
        _children_allowed(vessel, frozenset(_tag(name) for name in allowed_names))
        try:
            return OfacVesselInfo(
                call_sign=_one_text(vessel, "callSign", required=False),
                vessel_type=_one_text(vessel, "vesselType", required=False),
                vessel_flag=_one_text(vessel, "vesselFlag", required=False),
                vessel_owner=_one_text(vessel, "vesselOwner", required=False),
                tonnage=_optional_integer(_one_text(vessel, "tonnage", required=False)),
                gross_registered_tonnage=_optional_integer(
                    _one_text(vessel, "grossRegisteredTonnage", required=False)
                ),
                native_locator=f"/sdnList/sdnEntry[uid='{entry_uid}']/vesselInfo",
            )
        except ValueError:
            _fail(OfacSlsSourceErrorCode.XML_INVALID)
