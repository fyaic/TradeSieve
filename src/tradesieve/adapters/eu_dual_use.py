"""Official CELLAR retrieval and finite Formex parsing for EU Annex I."""

from __future__ import annotations

import hashlib
import http.client
import ssl
import zipfile
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from enum import StrEnum
from io import BytesIO
from types import MappingProxyType
from typing import Never, Protocol
from urllib.parse import urlsplit
from xml.etree import ElementTree

import certifi

from tradesieve.domain.eu_dual_use import (
    CONTROL_CODE,
    EU_DUAL_USE_CELEX,
    EU_DUAL_USE_SOURCE_URL,
    MAX_EU_DUAL_USE_ARCHIVE_BYTES,
    MAX_EU_DUAL_USE_DOCUMENT_BYTES,
    MAX_EU_DUAL_USE_ENTRIES,
    MIN_EU_DUAL_USE_ENTRIES,
    EuDualUseControlEntry,
    EuDualUseControlList,
)
from tradesieve.ports.eu_dual_use import (
    EuDualUseHttpDocument,
    EuDualUseHttpTransport,
)

_CELLAR_HOST = "publications.europa.eu"
_CELLAR_PATH = "/resource/cellar/ec080244-c0fa-11f0-a612-01aa75ed71a1.0006.02/DOC_1"
_READ_CHUNK_BYTES = 64 * 1024
_MAX_ARCHIVE_MEMBERS = 16
_FORMEX_MEMBER_SUFFIX = ".000302.fmx.xml"


class EuDualUseSourceErrorCode(StrEnum):
    INVALID_URL = "INVALID_URL"
    NETWORK_UNAVAILABLE = "NETWORK_UNAVAILABLE"
    HTTP_STATUS = "HTTP_STATUS"
    RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"
    RESPONSE_INVALID = "RESPONSE_INVALID"
    ARCHIVE_INVALID = "ARCHIVE_INVALID"
    XML_SECURITY_REJECTED = "XML_SECURITY_REJECTED"
    XML_INVALID = "XML_INVALID"
    XML_SCHEMA_DRIFT = "XML_SCHEMA_DRIFT"
    CONTROL_LIST_INVALID = "CONTROL_LIST_INVALID"


_MESSAGES = MappingProxyType(
    {
        EuDualUseSourceErrorCode.INVALID_URL: "EU dual-use source URL is invalid",
        EuDualUseSourceErrorCode.NETWORK_UNAVAILABLE: (
            "EU dual-use source is unavailable"
        ),
        EuDualUseSourceErrorCode.HTTP_STATUS: (
            "EU dual-use source returned an invalid status"
        ),
        EuDualUseSourceErrorCode.RESPONSE_TOO_LARGE: (
            "EU dual-use source exceeds its byte bound"
        ),
        EuDualUseSourceErrorCode.RESPONSE_INVALID: (
            "EU dual-use source response is invalid"
        ),
        EuDualUseSourceErrorCode.ARCHIVE_INVALID: (
            "EU dual-use source archive is invalid"
        ),
        EuDualUseSourceErrorCode.XML_SECURITY_REJECTED: (
            "EU dual-use XML contains a forbidden declaration"
        ),
        EuDualUseSourceErrorCode.XML_INVALID: "EU dual-use XML is invalid",
        EuDualUseSourceErrorCode.XML_SCHEMA_DRIFT: (
            "EU dual-use XML schema is unsupported"
        ),
        EuDualUseSourceErrorCode.CONTROL_LIST_INVALID: (
            "EU dual-use control list failed integrity checks"
        ),
    }
)


class EuDualUseSourceError(Exception):
    def __init__(self, code: EuDualUseSourceErrorCode) -> None:
        if not isinstance(code, EuDualUseSourceErrorCode):
            raise ValueError("source error code must be typed")
        self.code = code
        super().__init__(_MESSAGES[code])


def _fail(code: EuDualUseSourceErrorCode) -> Never:
    raise EuDualUseSourceError(code) from None


def _validate_url(url: str) -> None:
    if not isinstance(url, str) or url != EU_DUAL_USE_SOURCE_URL:
        _fail(EuDualUseSourceErrorCode.INVALID_URL)
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != _CELLAR_HOST
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != _CELLAR_PATH
        or parsed.query
        or parsed.fragment
    ):  # Exact equality above makes this a defence-in-depth invariant.
        _fail(EuDualUseSourceErrorCode.INVALID_URL)  # pragma: no cover


class _Response(Protocol):
    status: int

    def getheader(self, name: str) -> str | None: ...

    def read(self, amount: int | None = None) -> bytes: ...


class _Connection(Protocol):
    def request(self, method: str, url: str, *, headers: dict[str, str]) -> None: ...

    def getresponse(self) -> _Response: ...

    def close(self) -> None: ...


class _ConnectionFactory(Protocol):
    def __call__(
        self, host: str, timeout: float, context: ssl.SSLContext
    ) -> _Connection: ...


class _FormexParser(Protocol):
    def parse(
        self, archive: bytes, *, retrieved_at: datetime
    ) -> EuDualUseControlList: ...


def _default_connection(
    host: str, timeout: float, context: ssl.SSLContext
) -> _Connection:
    return http.client.HTTPSConnection(host, timeout=timeout, context=context)


class HttpsEuDualUseTransport:
    """GET-only transport hard-bound to the official CELLAR content item."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 30,
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

    def get(self, url: str, *, maximum_bytes: int) -> EuDualUseHttpDocument:
        _validate_url(url)
        if (
            isinstance(maximum_bytes, bool)
            or not isinstance(maximum_bytes, int)
            or not 1 <= maximum_bytes <= MAX_EU_DUAL_USE_ARCHIVE_BYTES
        ):
            raise ValueError("maximum_bytes is invalid")
        connection: _Connection | None = None
        try:
            connection = self._connection_factory(
                _CELLAR_HOST, self._timeout_seconds, self._ssl_context
            )
            connection.request(
                "GET",
                _CELLAR_PATH,
                headers={
                    "Accept": "application/zip, application/octet-stream",
                    "Accept-Encoding": "identity",
                    "User-Agent": "TradeSieve-EU-Dual-Use/0.1",
                },
            )
            response = connection.getresponse()
            if response.status != 200:
                _fail(EuDualUseSourceErrorCode.HTTP_STATUS)
            if (response.getheader("Content-Encoding") or "identity").lower() not in {
                "",
                "identity",
            }:
                _fail(EuDualUseSourceErrorCode.RESPONSE_INVALID)
            declared = response.getheader("Content-Length")
            if declared is not None:
                try:
                    declared_length = int(declared)
                except ValueError:
                    _fail(EuDualUseSourceErrorCode.RESPONSE_INVALID)
                if declared_length < 1:
                    _fail(EuDualUseSourceErrorCode.RESPONSE_INVALID)
                if declared_length > maximum_bytes:
                    _fail(EuDualUseSourceErrorCode.RESPONSE_TOO_LARGE)
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(min(_READ_CHUNK_BYTES, maximum_bytes + 1 - total))
                if not isinstance(chunk, bytes):
                    _fail(EuDualUseSourceErrorCode.RESPONSE_INVALID)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > maximum_bytes:
                    _fail(EuDualUseSourceErrorCode.RESPONSE_TOO_LARGE)
            if total == 0:
                _fail(EuDualUseSourceErrorCode.RESPONSE_INVALID)
            return EuDualUseHttpDocument(
                final_url=url,
                content_type=response.getheader("Content-Type")
                or "application/octet-stream",
                content=b"".join(chunks),
            )
        except EuDualUseSourceError:
            raise
        except Exception:
            _fail(EuDualUseSourceErrorCode.NETWORK_UNAVAILABLE)
        finally:
            if connection is not None:
                with suppress(Exception):
                    connection.close()


class EuDualUseFormexParser:
    """Extract top-level Annex I entries from the official bounded Formex archive."""

    def parse(self, archive: bytes, *, retrieved_at: datetime) -> EuDualUseControlList:
        if (
            not isinstance(archive, bytes)
            or not 1 <= len(archive) <= MAX_EU_DUAL_USE_ARCHIVE_BYTES
        ):
            _fail(EuDualUseSourceErrorCode.ARCHIVE_INVALID)
        if (
            not isinstance(retrieved_at, datetime)
            or retrieved_at.tzinfo is None
            or retrieved_at.utcoffset() is None
        ):
            _fail(EuDualUseSourceErrorCode.CONTROL_LIST_INVALID)
        try:
            with zipfile.ZipFile(BytesIO(archive)) as bundle:
                members = bundle.infolist()
                if not 1 <= len(members) <= _MAX_ARCHIVE_MEMBERS:
                    _fail(EuDualUseSourceErrorCode.ARCHIVE_INVALID)
                if any(
                    member.is_dir()
                    or "/" in member.filename
                    or "\\" in member.filename
                    or member.filename in {".", ".."}
                    or member.file_size > MAX_EU_DUAL_USE_DOCUMENT_BYTES
                    for member in members
                ):
                    _fail(EuDualUseSourceErrorCode.ARCHIVE_INVALID)
                candidates = [
                    member
                    for member in members
                    if member.filename.endswith(_FORMEX_MEMBER_SUFFIX)
                ]
                if len(candidates) != 1:
                    _fail(EuDualUseSourceErrorCode.ARCHIVE_INVALID)
                member = candidates[0]
                if member.file_size < 1:
                    _fail(EuDualUseSourceErrorCode.ARCHIVE_INVALID)
                document = bundle.read(member)
        except EuDualUseSourceError:
            raise
        except (OSError, RuntimeError, ValueError, zipfile.BadZipFile):
            _fail(EuDualUseSourceErrorCode.ARCHIVE_INVALID)
        return self._parse_document(
            document,
            archive_hash=f"sha256:{hashlib.sha256(archive).hexdigest()}",
            archive_bytes=len(archive),
            retrieved_at=retrieved_at,
        )

    @staticmethod
    def _parse_document(
        document: bytes,
        *,
        archive_hash: str,
        archive_bytes: int,
        retrieved_at: datetime,
    ) -> EuDualUseControlList:
        upper_prefix = document[:65_536].upper()
        if b"<!DOCTYPE" in upper_prefix or b"<!ENTITY" in upper_prefix:
            _fail(EuDualUseSourceErrorCode.XML_SECURITY_REJECTED)
        try:
            root = ElementTree.fromstring(document)
        except ElementTree.ParseError:
            _fail(EuDualUseSourceErrorCode.XML_INVALID)
        if root.tag != "ANNEX" or root.attrib.get("NNC") != "YES":
            _fail(EuDualUseSourceErrorCode.XML_SCHEMA_DRIFT)
        title = root.find("./TITLE/TI/P")
        subtitle = root.find("./TITLE/STI/P")
        if (
            title is None
            or " ".join(title.itertext()).strip() != "ANNEX I"
            or subtitle is None
            or "LIST OF DUAL-USE ITEMS" not in " ".join(subtitle.itertext())
        ):
            _fail(EuDualUseSourceErrorCode.XML_SCHEMA_DRIFT)
        entries: list[EuDualUseControlEntry] = []
        for node in root.iter("NP"):
            number = node.find("./NO.P")
            if number is None:
                continue
            code = "".join(number.itertext()).strip()
            if CONTROL_CODE.fullmatch(code) is None:
                continue
            text = " ".join(" ".join(node.itertext()).split())
            if not text.startswith(code + " "):  # Element order guarantees prefix.
                _fail(  # pragma: no cover
                    EuDualUseSourceErrorCode.CONTROL_LIST_INVALID
                )
            entries.append(
                EuDualUseControlEntry(
                    code=code,
                    text=text[len(code) + 1 :],
                    native_locator=f"/ANNEX/NP[NO.P='{code}']",
                )
            )
            if len(entries) > MAX_EU_DUAL_USE_ENTRIES:
                _fail(EuDualUseSourceErrorCode.CONTROL_LIST_INVALID)
        if not MIN_EU_DUAL_USE_ENTRIES <= len(entries) <= MAX_EU_DUAL_USE_ENTRIES:
            _fail(EuDualUseSourceErrorCode.CONTROL_LIST_INVALID)
        try:
            return EuDualUseControlList(
                retrieved_at=retrieved_at,
                source_archive_hash=archive_hash,
                source_archive_bytes=archive_bytes,
                formex_document_hash=(f"sha256:{hashlib.sha256(document).hexdigest()}"),
                entries=tuple(entries),
            )
        except ValueError:
            _fail(EuDualUseSourceErrorCode.CONTROL_LIST_INVALID)


class EuDualUseOfficialSourceConnector:
    def __init__(
        self,
        transport: EuDualUseHttpTransport,
        *,
        parser: _FormexParser | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not hasattr(transport, "get"):
            raise ValueError("transport must implement bounded GET")
        self._transport = transport
        self._parser = parser or EuDualUseFormexParser()
        self._clock = clock or (lambda: datetime.now(UTC))

    def retrieve(self) -> EuDualUseControlList:
        try:
            document = self._transport.get(
                EU_DUAL_USE_SOURCE_URL,
                maximum_bytes=MAX_EU_DUAL_USE_ARCHIVE_BYTES,
            )
        except EuDualUseSourceError:
            raise
        except Exception:
            _fail(EuDualUseSourceErrorCode.NETWORK_UNAVAILABLE)
        if document.final_url != EU_DUAL_USE_SOURCE_URL:
            _fail(EuDualUseSourceErrorCode.RESPONSE_INVALID)
        media_type = document.content_type.partition(";")[0].strip().lower()
        if media_type not in {
            "application/zip",
            "application/octet-stream",
            "application/x-zip-compressed",
        }:
            _fail(EuDualUseSourceErrorCode.RESPONSE_INVALID)
        now = self._clock()
        try:
            return self._parser.parse(document.content, retrieved_at=now)
        except EuDualUseSourceError:
            raise
        except Exception:
            _fail(EuDualUseSourceErrorCode.CONTROL_LIST_INVALID)


__all__ = [
    "EU_DUAL_USE_CELEX",
    "EuDualUseFormexParser",
    "EuDualUseOfficialSourceConnector",
    "EuDualUseSourceError",
    "EuDualUseSourceErrorCode",
    "HttpsEuDualUseTransport",
]
