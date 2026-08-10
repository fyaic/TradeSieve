"""Official EU dual-use source, parser, domain, and transport tests."""

from __future__ import annotations

import hashlib
import http.client
import io
import ssl
import zipfile
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast

import pytest

import tradesieve.adapters.eu_dual_use as adapter
from tradesieve.adapters.eu_dual_use import (
    EuDualUseFormexParser,
    EuDualUseOfficialSourceConnector,
    EuDualUseSourceError,
    EuDualUseSourceErrorCode,
    HttpsEuDualUseTransport,
)
from tradesieve.domain.eu_dual_use import (
    EU_DUAL_USE_CELEX,
    EU_DUAL_USE_EFFECTIVE_FROM,
    EU_DUAL_USE_SOURCE_URL,
    MAX_EU_DUAL_USE_ARCHIVE_BYTES,
    EuDualUseAssessment,
    EuDualUseAssessmentEngine,
    EuDualUseAssessmentStatus,
    EuDualUseControlEntry,
    EuDualUseControlList,
    normalize_control_code,
)
from tradesieve.ports.eu_dual_use import EuDualUseHttpDocument

NOW = datetime(2026, 8, 10, 4, 30, tzinfo=UTC)


def entries() -> tuple[EuDualUseControlEntry, ...]:
    return tuple(
        EuDualUseControlEntry(
            code=f"{category}A{index:03d}",
            text=f"Controlled example {category}-{index} with technical parameters.",
            native_locator=f"/ANNEX/NP[NO.P='{category}A{index:03d}']",
        )
        for category in range(10)
        for index in range(30)
    )


def control_list() -> EuDualUseControlList:
    return EuDualUseControlList(
        retrieved_at=NOW,
        source_archive_hash="sha256:" + "a" * 64,
        source_archive_bytes=100,
        formex_document_hash="sha256:" + "b" * 64,
        entries=entries(),
    )


def formex_document(
    *,
    control_entries: tuple[EuDualUseControlEntry, ...] | None = None,
    root: str = "ANNEX",
    root_attributes: str = 'NNC="YES"',
    title: str = "ANNEX I",
    subtitle: str = "LIST OF DUAL-USE ITEMS REFERRED TO IN ARTICLE 3",
    prefix: str = "",
) -> bytes:
    body = "".join(
        f"<NP><NO.P>{item.code}</NO.P><TXT>{item.text}</TXT></NP>"
        for item in (control_entries or entries())
    )
    return (
        f"{prefix}<{root} {root_attributes}><TITLE><TI><P>{title}</P></TI>"
        f"<STI><P>{subtitle}</P></STI></TITLE>{body}</{root}>"
    ).encode()


def archive(
    document: bytes | None = None,
    *,
    member_name: str = "L_202502003EN.000302.fmx.xml",
    extra_members: dict[str, bytes] | None = None,
) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(
            member_name, formex_document() if document is None else document
        )
        for name, content in (extra_members or {}).items():
            bundle.writestr(name, content)
    return stream.getvalue()


def source_error(code: EuDualUseSourceErrorCode, action: Any) -> EuDualUseSourceError:
    with pytest.raises(EuDualUseSourceError) as caught:
        action()
    assert caught.value.code is code
    assert str(caught.value)
    assert "https" not in str(caught.value)
    return caught.value


def corrupt(instance: Any, **changes: Any) -> Any:
    return replace(instance, **changes)


def test_formex_archive_parses_versioned_complete_control_list() -> None:
    raw = archive()
    result = EuDualUseFormexParser().parse(raw, retrieved_at=NOW)

    assert len(result.entries) == 300
    assert result.entries[0].code == "0A000"
    assert result.entries[-1].code == "9A029"
    assert result.entries[0].text.startswith("Controlled example")
    assert result.source_archive_bytes == len(raw)
    assert result.source_archive_hash == f"sha256:{hashlib.sha256(raw).hexdigest()}"
    assert result.snapshot_id.startswith("eu-dual-use-")
    assert result.content_hash.startswith("sha256:")
    assert result == EuDualUseFormexParser().parse(raw, retrieved_at=NOW)


def test_assessment_engine_never_clears_and_distinguishes_missing_facts() -> None:
    engine = EuDualUseAssessmentEngine(control_list())

    missing = engine.assess(
        None,
        classification_verified=False,
        technical_specification_available=False,
    )
    assert missing.status is EuDualUseAssessmentStatus.MISSING_CLASSIFICATION
    assert missing.entry is None
    assert missing.automatic_clearance is False

    not_found = engine.assess(
        "0A999",
        classification_verified=False,
        technical_specification_available=True,
    )
    assert not_found.status is EuDualUseAssessmentStatus.ENTRY_NOT_FOUND
    assert not_found.missing_facts == (
        "catch_all_and_national_control_review",
        "qualified_classifier_review",
    )
    verified_not_found = engine.assess(
        "0A999",
        classification_verified=True,
        technical_specification_available=True,
    )
    assert verified_not_found.missing_facts == (
        "catch_all_and_national_control_review",
    )

    incomplete = engine.assess(
        " 0a000 ",
        classification_verified=False,
        technical_specification_available=False,
    )
    assert incomplete.status is EuDualUseAssessmentStatus.TECHNICAL_REVIEW_REQUIRED
    assert incomplete.requested_code == "0A000"
    assert incomplete.entry is not None
    assert incomplete.missing_facts == (
        "qualified_classifier_review",
        "technical_specification",
    )

    found = engine.assess(
        "0A000",
        classification_verified=True,
        technical_specification_available=True,
    )
    assert found.status is EuDualUseAssessmentStatus.CONTROL_ENTRY_FOUND
    assert found.entry == control_list().entries[0]
    assert found.missing_facts == ()
    assert found.source_celex == EU_DUAL_USE_CELEX
    assert found.effective_from == EU_DUAL_USE_EFFECTIVE_FROM


@pytest.mark.parametrize("value", [None, 1, "", "0", "0Z001", "10A001"])
def test_control_code_normalization_rejects_non_annex_codes(value: Any) -> None:
    with pytest.raises(ValueError):
        normalize_control_code(cast(Any, value))


def test_defensive_domain_validation_rejects_corruption() -> None:
    entry = entries()[0]
    listing = control_list()
    assessment = EuDualUseAssessmentEngine(listing).assess(
        "0A000",
        classification_verified=True,
        technical_specification_available=True,
    )
    invalid = (
        lambda: EuDualUseControlEntry("bad", "x", "/ANNEX/x"),
        lambda: EuDualUseControlEntry(" 0a000 ", "x", "/ANNEX/x"),
        lambda: EuDualUseControlEntry("0A000", "", "/ANNEX/x"),
        lambda: EuDualUseControlEntry("0A000", "x", "bad"),
        lambda: corrupt(listing, retrieved_at=datetime(2026, 8, 10)),
        lambda: corrupt(listing, source_archive_hash="bad"),
        lambda: corrupt(listing, formex_document_hash="bad"),
        lambda: corrupt(listing, source_archive_bytes=True),
        lambda: corrupt(listing, entries=entries()[:10]),
        lambda: corrupt(listing, entries=entries()[:-1] + (entries()[0],)),
        lambda: corrupt(
            listing,
            entries=entries()[:-30]
            + tuple(
                EuDualUseControlEntry(
                    code=f"8B{index:03d}",
                    text="replacement",
                    native_locator=f"/ANNEX/NP[NO.P='8B{index:03d}']",
                )
                for index in range(30)
            ),
        ),
        lambda: EuDualUseAssessmentEngine(cast(Any, "bad")),
        lambda: EuDualUseAssessmentEngine(listing).assess(
            "0A000",
            classification_verified=cast(Any, 1),
            technical_specification_available=True,
        ),
        lambda: corrupt(assessment, status="bad"),
        lambda: corrupt(assessment, requested_code="bad"),
        lambda: corrupt(assessment, snapshot_id="bad"),
        lambda: corrupt(assessment, snapshot_content_hash="bad"),
        lambda: corrupt(assessment, source_celex="bad"),
        lambda: corrupt(
            assessment, effective_from=EU_DUAL_USE_EFFECTIVE_FROM.replace(day=16)
        ),
        lambda: corrupt(assessment, entry="bad"),
        lambda: corrupt(assessment, missing_facts=["bad"]),
        lambda: corrupt(assessment, automatic_clearance=True),
        lambda: EuDualUseAssessment(
            status=EuDualUseAssessmentStatus.ENTRY_NOT_FOUND,
            requested_code=None,
            snapshot_id=listing.snapshot_id,
            snapshot_content_hash=listing.content_hash,
            source_celex=EU_DUAL_USE_CELEX,
            effective_from=EU_DUAL_USE_EFFECTIVE_FROM,
            entry=None,
            missing_facts=("",),
        ),
        lambda: corrupt(entry, text="x" * (64 * 1024 + 1)),
    )
    for action in invalid:
        with pytest.raises(ValueError):
            action()


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (b"", EuDualUseSourceErrorCode.ARCHIVE_INVALID),
        (b"not-a-zip", EuDualUseSourceErrorCode.ARCHIVE_INVALID),
        (
            archive(member_name="wrong.xml"),
            EuDualUseSourceErrorCode.ARCHIVE_INVALID,
        ),
        (
            archive(extra_members={"duplicate.000302.fmx.xml": b"x"}),
            EuDualUseSourceErrorCode.ARCHIVE_INVALID,
        ),
        (
            archive(member_name="../bad.000302.fmx.xml"),
            EuDualUseSourceErrorCode.ARCHIVE_INVALID,
        ),
        (
            archive(document=b""),
            EuDualUseSourceErrorCode.ARCHIVE_INVALID,
        ),
    ],
)
def test_formex_parser_rejects_invalid_archives(
    raw: bytes, code: EuDualUseSourceErrorCode
) -> None:
    source_error(code, lambda: EuDualUseFormexParser().parse(raw, retrieved_at=NOW))


def test_formex_parser_rejects_archive_member_and_time_bounds() -> None:
    empty_stream = io.BytesIO()
    with zipfile.ZipFile(empty_stream, "w"):
        pass
    source_error(
        EuDualUseSourceErrorCode.ARCHIVE_INVALID,
        lambda: EuDualUseFormexParser().parse(
            empty_stream.getvalue(), retrieved_at=NOW
        ),
    )
    source_error(
        EuDualUseSourceErrorCode.CONTROL_LIST_INVALID,
        lambda: EuDualUseFormexParser().parse(
            archive(), retrieved_at=datetime(2026, 8, 10)
        ),
    )
    too_many_members = {f"extra-{index}.txt": b"x" for index in range(16)}
    source_error(
        EuDualUseSourceErrorCode.ARCHIVE_INVALID,
        lambda: EuDualUseFormexParser().parse(
            archive(extra_members=too_many_members), retrieved_at=NOW
        ),
    )
    directory_stream = io.BytesIO()
    with zipfile.ZipFile(directory_stream, "w") as bundle:
        bundle.writestr("directory/", b"")
        bundle.writestr("L_202502003EN.000302.fmx.xml", formex_document())
    source_error(
        EuDualUseSourceErrorCode.ARCHIVE_INVALID,
        lambda: EuDualUseFormexParser().parse(
            directory_stream.getvalue(), retrieved_at=NOW
        ),
    )


def test_formex_parser_skips_non_entry_nodes_and_enforces_entry_cap() -> None:
    document = formex_document().replace(
        b"</ANNEX>",
        b"<NP><TXT>no number</TXT></NP><NP><NO.P>note</NO.P></NP></ANNEX>",
    )
    assert (
        len(EuDualUseFormexParser().parse(archive(document), retrieved_at=NOW).entries)
        == 300
    )

    controls = "".join(
        f"<NP><NO.P>{index // 1000}A{index % 1000:03d}</NO.P><TXT>x</TXT></NP>"
        for index in range(1001)
    )
    oversized = (
        '<ANNEX NNC="YES"><TITLE><TI><P>ANNEX I</P></TI><STI><P>'
        "LIST OF DUAL-USE ITEMS</P></STI></TITLE>" + controls + "</ANNEX>"
    ).encode()
    source_error(
        EuDualUseSourceErrorCode.CONTROL_LIST_INVALID,
        lambda: EuDualUseFormexParser().parse(archive(oversized), retrieved_at=NOW),
    )


@pytest.mark.parametrize(
    ("document", "code"),
    [
        (
            formex_document(prefix="<!DOCTYPE ANNEX>"),
            EuDualUseSourceErrorCode.XML_SECURITY_REJECTED,
        ),
        (b"<ANNEX>", EuDualUseSourceErrorCode.XML_INVALID),
        (
            formex_document(root="WRONG"),
            EuDualUseSourceErrorCode.XML_SCHEMA_DRIFT,
        ),
        (
            formex_document(root_attributes='NNC="NO"'),
            EuDualUseSourceErrorCode.XML_SCHEMA_DRIFT,
        ),
        (
            formex_document(title="ANNEX II"),
            EuDualUseSourceErrorCode.XML_SCHEMA_DRIFT,
        ),
        (
            formex_document(subtitle="WRONG"),
            EuDualUseSourceErrorCode.XML_SCHEMA_DRIFT,
        ),
        (
            formex_document(control_entries=entries()[:299]),
            EuDualUseSourceErrorCode.CONTROL_LIST_INVALID,
        ),
        (
            formex_document(control_entries=entries()[:-1] + (entries()[0],)),
            EuDualUseSourceErrorCode.CONTROL_LIST_INVALID,
        ),
    ],
)
def test_formex_parser_rejects_invalid_xml(
    document: bytes, code: EuDualUseSourceErrorCode
) -> None:
    source_error(
        code,
        lambda: EuDualUseFormexParser().parse(archive(document), retrieved_at=NOW),
    )


class FakeTransport:
    def __init__(
        self,
        document: EuDualUseHttpDocument | None = None,
        failure: Exception | None = None,
    ) -> None:
        self.document = document or EuDualUseHttpDocument(
            EU_DUAL_USE_SOURCE_URL, "application/zip", archive()
        )
        self.failure = failure
        self.calls: list[tuple[str, int]] = []

    def get(self, url: str, *, maximum_bytes: int) -> EuDualUseHttpDocument:
        self.calls.append((url, maximum_bytes))
        if self.failure is not None:
            raise self.failure
        return self.document


def test_connector_retrieves_exact_official_source_and_parses_it() -> None:
    transport = FakeTransport()
    result = EuDualUseOfficialSourceConnector(transport, clock=lambda: NOW).retrieve()
    assert len(result.entries) == 300
    assert transport.calls == [(EU_DUAL_USE_SOURCE_URL, MAX_EU_DUAL_USE_ARCHIVE_BYTES)]


@pytest.mark.parametrize(
    "media_type",
    ["application/octet-stream", "application/x-zip-compressed"],
)
def test_connector_accepts_official_archive_media_variants(media_type: str) -> None:
    result = EuDualUseOfficialSourceConnector(
        FakeTransport(
            EuDualUseHttpDocument(EU_DUAL_USE_SOURCE_URL, media_type, archive())
        ),
        clock=lambda: NOW,
    ).retrieve()
    assert len(result.entries) == 300


@pytest.mark.parametrize(
    ("document", "code"),
    [
        (
            EuDualUseHttpDocument(
                EU_DUAL_USE_SOURCE_URL + "x", "application/zip", archive()
            ),
            EuDualUseSourceErrorCode.RESPONSE_INVALID,
        ),
        (
            EuDualUseHttpDocument(EU_DUAL_USE_SOURCE_URL, "text/html", archive()),
            EuDualUseSourceErrorCode.RESPONSE_INVALID,
        ),
    ],
)
def test_connector_rejects_invalid_response(
    document: EuDualUseHttpDocument, code: EuDualUseSourceErrorCode
) -> None:
    source_error(
        code,
        EuDualUseOfficialSourceConnector(
            FakeTransport(document), clock=lambda: NOW
        ).retrieve,
    )


def test_connector_redacts_dependency_failures_and_validates_shape() -> None:
    source_error(
        EuDualUseSourceErrorCode.NETWORK_UNAVAILABLE,
        EuDualUseOfficialSourceConnector(
            FakeTransport(failure=RuntimeError("secret")), clock=lambda: NOW
        ).retrieve,
    )
    source_error(
        EuDualUseSourceErrorCode.HTTP_STATUS,
        EuDualUseOfficialSourceConnector(
            FakeTransport(
                failure=EuDualUseSourceError(EuDualUseSourceErrorCode.HTTP_STATUS)
            ),
            clock=lambda: NOW,
        ).retrieve,
    )
    with pytest.raises(ValueError):
        EuDualUseOfficialSourceConnector(cast(Any, object()))
    with pytest.raises(ValueError):
        EuDualUseSourceError(cast(Any, "bad"))

    class BadParser:
        def parse(
            self, content: bytes, *, retrieved_at: datetime
        ) -> EuDualUseControlList:
            raise RuntimeError("secret")

    source_error(
        EuDualUseSourceErrorCode.CONTROL_LIST_INVALID,
        EuDualUseOfficialSourceConnector(
            FakeTransport(), parser=BadParser(), clock=lambda: NOW
        ).retrieve,
    )
    source_error(
        EuDualUseSourceErrorCode.ARCHIVE_INVALID,
        EuDualUseOfficialSourceConnector(
            FakeTransport(
                EuDualUseHttpDocument(
                    EU_DUAL_USE_SOURCE_URL, "application/zip", b"not-a-zip"
                )
            ),
            clock=lambda: NOW,
        ).retrieve,
    )


class FakeResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        chunks: list[Any] | None = None,
        failure: Exception | None = None,
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self.chunks = list(chunks if chunks is not None else [b"payload", b""])
        self.failure = failure

    def getheader(self, name: str) -> str | None:
        return self.headers.get(name)

    def read(self, amount: int | None = None) -> bytes:
        assert amount is not None and amount > 0
        if self.failure is not None:
            raise self.failure
        return cast(bytes, self.chunks.pop(0))


class FakeConnection:
    def __init__(
        self,
        response: FakeResponse,
        *,
        request_failure: Exception | None = None,
        close_failure: Exception | None = None,
    ) -> None:
        self.response = response
        self.request_failure = request_failure
        self.close_failure = close_failure
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self.closed = 0

    def request(self, method: str, url: str, *, headers: dict[str, str]) -> None:
        if self.request_failure is not None:
            raise self.request_failure
        self.requests.append((method, url, headers))

    def getresponse(self) -> FakeResponse:
        return self.response

    def close(self) -> None:
        self.closed += 1
        if self.close_failure is not None:
            raise self.close_failure


def transport_for(connection: FakeConnection) -> HttpsEuDualUseTransport:
    def factory(host: str, timeout: float, context: ssl.SSLContext) -> FakeConnection:
        assert host == "publications.europa.eu"
        assert timeout == 30
        assert isinstance(context, ssl.SSLContext)
        return connection

    return HttpsEuDualUseTransport(connection_factory=factory)


def test_https_transport_reads_bounded_identity_response_and_closes() -> None:
    response = FakeResponse(
        headers={"Content-Length": "2", "Content-Type": "application/zip"},
        chunks=[b"x", b"y", b""],
    )
    connection = FakeConnection(response)
    result = transport_for(connection).get(EU_DUAL_USE_SOURCE_URL, maximum_bytes=2)
    assert result.content == b"xy"
    assert result.content_type == "application/zip"
    assert connection.closed == 1
    assert connection.requests[0][0:2] == (
        "GET",
        "/resource/cellar/ec080244-c0fa-11f0-a612-01aa75ed71a1.0006.02/DOC_1",
    )
    assert connection.requests[0][2]["Accept-Encoding"] == "identity"


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (FakeResponse(status=503), EuDualUseSourceErrorCode.HTTP_STATUS),
        (
            FakeResponse(headers={"Content-Encoding": "gzip"}),
            EuDualUseSourceErrorCode.RESPONSE_INVALID,
        ),
        (
            FakeResponse(headers={"Content-Length": "bad"}),
            EuDualUseSourceErrorCode.RESPONSE_INVALID,
        ),
        (
            FakeResponse(headers={"Content-Length": "0"}),
            EuDualUseSourceErrorCode.RESPONSE_INVALID,
        ),
        (
            FakeResponse(headers={"Content-Length": "2"}),
            EuDualUseSourceErrorCode.RESPONSE_TOO_LARGE,
        ),
        (
            FakeResponse(chunks=[b"xy"]),
            EuDualUseSourceErrorCode.RESPONSE_TOO_LARGE,
        ),
        (
            FakeResponse(chunks=["bad"]),
            EuDualUseSourceErrorCode.RESPONSE_INVALID,
        ),
        (
            FakeResponse(chunks=[b""]),
            EuDualUseSourceErrorCode.RESPONSE_INVALID,
        ),
        (
            FakeResponse(failure=RuntimeError("secret")),
            EuDualUseSourceErrorCode.NETWORK_UNAVAILABLE,
        ),
    ],
)
def test_https_transport_failures_are_stable(
    response: FakeResponse, code: EuDualUseSourceErrorCode
) -> None:
    connection = FakeConnection(response)
    source_error(
        code,
        lambda: transport_for(connection).get(EU_DUAL_USE_SOURCE_URL, maximum_bytes=1),
    )
    assert connection.closed == 1


def test_https_transport_rejects_url_bounds_and_connection_failures() -> None:
    connection = FakeConnection(FakeResponse())
    transport = transport_for(connection)
    for url in (
        EU_DUAL_USE_SOURCE_URL + "?x=1",
        "http://publications.europa.eu" + adapter._CELLAR_PATH,
        "https://attacker.invalid/file",
    ):
        source_error(
            EuDualUseSourceErrorCode.INVALID_URL,
            lambda url=url: transport.get(url, maximum_bytes=10),
        )
    for maximum in (True, 0, MAX_EU_DUAL_USE_ARCHIVE_BYTES + 1):
        with pytest.raises(ValueError):
            transport.get(EU_DUAL_USE_SOURCE_URL, maximum_bytes=cast(Any, maximum))
    for timeout in (True, 0, 121, "30"):
        with pytest.raises(ValueError):
            HttpsEuDualUseTransport(timeout_seconds=cast(Any, timeout))
    with pytest.raises(ValueError):
        HttpsEuDualUseTransport(connection_factory=cast(Any, "bad"))

    broken = FakeConnection(FakeResponse(), request_failure=RuntimeError("secret"))
    source_error(
        EuDualUseSourceErrorCode.NETWORK_UNAVAILABLE,
        lambda: transport_for(broken).get(EU_DUAL_USE_SOURCE_URL, maximum_bytes=10),
    )
    assert broken.closed == 1

    close_broken = FakeConnection(
        FakeResponse(chunks=[b"x", b""]), close_failure=RuntimeError()
    )
    assert (
        transport_for(close_broken)
        .get(EU_DUAL_USE_SOURCE_URL, maximum_bytes=10)
        .content
        == b"x"
    )
    assert close_broken.closed == 1

    def factory_failure(
        host: str, timeout: float, context: ssl.SSLContext
    ) -> FakeConnection:
        raise RuntimeError("secret")

    no_connection = HttpsEuDualUseTransport(connection_factory=factory_failure)
    source_error(
        EuDualUseSourceErrorCode.NETWORK_UNAVAILABLE,
        lambda: no_connection.get(EU_DUAL_USE_SOURCE_URL, maximum_bytes=10),
    )

    default_type = FakeConnection(FakeResponse(chunks=[b"x", b""]))
    assert (
        transport_for(default_type)
        .get(EU_DUAL_USE_SOURCE_URL, maximum_bytes=10)
        .content_type
        == "application/octet-stream"
    )


def test_http_document_and_default_connection_are_defensive(
    monkeypatch: Any,
) -> None:
    actions: tuple[Callable[[], object], ...] = (
        lambda: EuDualUseHttpDocument("", "x", b"x"),
        lambda: EuDualUseHttpDocument("x", "", b"x"),
        lambda: EuDualUseHttpDocument("x", "y", cast(Any, "bad")),
    )
    for action in actions:
        with pytest.raises(ValueError):
            action()

    sentinel = object()

    def factory(host: str, *, timeout: float, context: ssl.SSLContext) -> object:
        assert host == "publications.europa.eu"
        assert timeout == 3.0
        assert isinstance(context, ssl.SSLContext)
        return sentinel

    monkeypatch.setattr(http.client, "HTTPSConnection", factory)
    context = ssl.create_default_context()
    assert (
        adapter._default_connection("publications.europa.eu", 3.0, context) is sentinel
    )
