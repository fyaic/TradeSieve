from __future__ import annotations

import hashlib
import http.client
import ssl
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any, cast

import pytest

import tradesieve.adapters.ofac_sls as adapter
from tradesieve.adapters.ofac_sls import (
    OFAC_CONSOLIDATED_XML_URL,
    OFAC_SDN_XML_URL,
    HttpsOfacSlsTransport,
    OfacSlsOfficialSourceConnector,
    OfacSlsRetrievedSource,
    OfacSlsSourceError,
    OfacSlsSourceErrorCode,
    OfacSlsXmlParser,
)
from tradesieve.domain.ofac_sls import (
    MAX_OFAC_RAW_BYTES,
    OFAC_EXACT_IDENTIFIER_TYPES,
    OfacCandidateStatus,
    OfacFactKind,
    OfacIdentifierCandidates,
    OfacIdentifierIndex,
    OfacIdentifierQuery,
    OfacNameCandidates,
    OfacNameIndex,
    OfacNameQuery,
    OfacSlsListKind,
    OfacSnapshot,
    OfacSnapshotDiff,
    OfacSubjectType,
    OfacVesselInfo,
    diff_ofac_snapshots,
    normalize_ofac_identifier,
    normalize_ofac_name,
)
from tradesieve.ports.ofac_sls import OfacSlsHttpDocument

NOW = datetime(2026, 8, 10, 9, 0, tzinfo=UTC)
LAST_MODIFIED = datetime(2026, 8, 7, 18, 36, 51, tzinfo=UTC)
NS = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/XML"


def source_xml(*, count: int = 2, second_number: str = "RU-42") -> bytes:
    return f"""<?xml version="1.0" standalone="yes"?>
<sdnList xmlns="{NS}">
  <publshInformation>
    <Publish_Date>08/07/2026</Publish_Date>
    <Record_Count>{count}</Record_Count>
  </publshInformation>
  <sdnEntry>
    <uid>100</uid><firstName>ACME</firstName><lastName>LOGISTICS</lastName>
    <title>Carrier</title><sdnType>Vessel</sdnType><remarks>Review only</remarks>
    <programList><program>RUSSIA-EO14024</program><program>UKRAINE-EO13662</program></programList>
    <idList>
      <id><uid>101</uid><idType>Registration Number</idType><idNumber>RU-42</idNumber><idCountry>Russia</idCountry><issueDate>2020</issueDate><expirationDate>2030</expirationDate></id>
      <id><uid>102</uid><idType>Gender</idType><idNumber>Male</idNumber></id>
    </idList>
    <akaList>
      <aka><uid>103</uid><type>a.k.a.</type><category>strong</category><lastName>ACME SHIPPING</lastName></aka>
      <aka><uid>104</uid><type>f.k.a.</type><category>weak</category><firstName>ACME</firstName><lastName>LINES</lastName></aka>
    </akaList>
    <addressList><address><uid>105</uid><address1>1 Port Road</address1><address2>Suite 2</address2><address3>Dock 3</address3><city>Shanghai</city><stateOrProvince>Shanghai</stateOrProvince><postalCode>200000</postalCode><country>China</country><region>Asia</region></address></addressList>
    <nationalityList><nationality><uid>106</uid><country>Russia</country><mainEntry>true</mainEntry></nationality></nationalityList>
    <citizenshipList><citizenship><uid>107</uid><country>Russia</country><mainEntry>false</mainEntry></citizenship></citizenshipList>
    <dateOfBirthList><dateOfBirthItem><uid>108</uid><dateOfBirth>1980</dateOfBirth><mainEntry>true</mainEntry></dateOfBirthItem></dateOfBirthList>
    <placeOfBirthList><placeOfBirthItem><uid>109</uid><placeOfBirth>Moscow</placeOfBirth><mainEntry>true</mainEntry></placeOfBirthItem></placeOfBirthList>
    <vesselInfo><callSign>CALL</callSign><vesselType>Cargo</vesselType><vesselFlag>Russia</vesselFlag><vesselOwner>Owner</vesselOwner><tonnage>10</tonnage><grossRegisteredTonnage>20</grossRegisteredTonnage></vesselInfo>
  </sdnEntry>
  <sdnEntry>
    <uid>200</uid><lastName>SECOND ENTITY</lastName><sdnType>Entity</sdnType>
    <programList><program>RUSSIA-EO14024</program></programList>
    <idList><id><uid>201</uid><idType>Registration Number</idType><idNumber>{second_number}</idNumber></id></idList>
    <akaList><aka><uid>202</uid><type>a.k.a.</type><category>strong</category><lastName>ACME SHIPPING</lastName></aka></akaList>
  </sdnEntry>
</sdnList>""".encode()


def retrieved(
    *,
    content: bytes | None = None,
    kind: OfacSlsListKind = OfacSlsListKind.SDN,
    server_digest: str | None = None,
) -> OfacSlsRetrievedSource:
    data = content if content is not None else source_xml()
    content_hash = f"sha256:{hashlib.sha256(data).hexdigest()}"
    return OfacSlsRetrievedSource(
        list_kind=kind,
        retrieved_at=NOW,
        source_last_modified=LAST_MODIFIED,
        content_type="text/xml",
        server_digest=server_digest,
        content_hash=content_hash,
        content=data,
    )


def snapshot(**kwargs: Any) -> OfacSnapshot:
    base = OfacSlsXmlParser().parse(retrieved())
    return replace(base, **kwargs)


def assert_error(code: OfacSlsSourceErrorCode, action: Any) -> None:
    with pytest.raises(OfacSlsSourceError) as caught:
        action()
    assert caught.value.code is code
    assert str(caught.value) == code.value


def test_parser_preserves_full_source_evidence_and_replay_identity() -> None:
    parsed = OfacSlsXmlParser().parse(retrieved())
    replayed = OfacSlsXmlParser().parse(retrieved())

    assert parsed.list_kind is OfacSlsListKind.SDN
    assert parsed.publish_date == date(2026, 8, 7)
    assert parsed.declared_record_count == 2
    assert parsed.retrieved_at == NOW
    assert parsed.source_last_modified == LAST_MODIFIED
    assert parsed.snapshot_id == replayed.snapshot_id
    assert parsed.content_hash == replayed.content_hash
    assert parsed.entries[0].whole_name == "ACME LOGISTICS"
    assert parsed.entries[0].programs == (
        "RUSSIA-EO14024",
        "UKRAINE-EO13662",
    )
    assert parsed.entries[0].aliases[0].whole_name == "ACME SHIPPING"
    assert parsed.entries[0].aliases[1].whole_name == "ACME LINES"
    assert parsed.entries[0].addresses[0].address_lines == (
        "1 Port Road",
        "Suite 2",
        "Dock 3",
    )
    assert parsed.entries[0].identifiers[0].normalized_number == "RU42"
    assert parsed.entries[0].identifiers[1].normalized_number is None
    assert {fact.kind for fact in parsed.entries[0].facts} == set(OfacFactKind)
    assert parsed.entries[0].vessel_info == OfacVesselInfo(
        call_sign="CALL",
        vessel_type="Cargo",
        vessel_flag="Russia",
        vessel_owner="Owner",
        tonnage=10,
        gross_registered_tonnage=20,
        native_locator="/sdnList/sdnEntry[uid='100']/vesselInfo",
    )
    assert parsed.entries[1].vessel_info is None
    assert parsed.entries[0].entry_hash.startswith("sha256:")
    assert parsed.entries[0].canonical_content()["remarks"] == "Review only"


def test_candidate_indexes_are_deterministic_and_never_clear() -> None:
    parsed = OfacSlsXmlParser().parse(retrieved())
    identifiers = OfacIdentifierIndex(parsed)
    names = OfacNameIndex(parsed)

    exact = identifiers.search(OfacIdentifierQuery("Registration Number", "ru 42"))
    missing = identifiers.search(OfacIdentifierQuery("Registration Number", "none"))
    name = names.search(OfacNameQuery("acme logistics"))
    alias = names.search(OfacNameQuery(" ACME   SHIPPING "))
    absent_name = names.search(OfacNameQuery("not present"))

    assert exact.status is OfacCandidateStatus.AMBIGUOUS
    assert [item.entry_uid for item in exact.evidence] == ["100", "200"]
    assert exact.evidence[0].programs == (
        "RUSSIA-EO14024",
        "UKRAINE-EO13662",
    )
    assert missing.status is OfacCandidateStatus.NO_CANDIDATE
    assert name.status is OfacCandidateStatus.CANDIDATE
    assert name.evidence[0].alias_category is None
    assert alias.status is OfacCandidateStatus.AMBIGUOUS
    assert {item.alias_category for item in alias.evidence} == {"strong"}
    assert absent_name.status is OfacCandidateStatus.NO_CANDIDATE

    single = OfacSlsXmlParser().parse(retrieved(content=source_xml(second_number="XX")))
    assert (
        OfacIdentifierIndex(single)
        .search(OfacIdentifierQuery("Registration Number", "RU-42"))
        .status
        is OfacCandidateStatus.CANDIDATE
    )


def test_snapshot_diff_uses_stable_uid_and_entry_hash() -> None:
    original = snapshot()
    modified_entry = replace(original.entries[0], title="Changed")
    current = replace(
        original,
        declared_record_count=2,
        entries=(modified_entry, replace(original.entries[1], uid="300")),
    )

    diff = diff_ofac_snapshots(original, current)
    same = diff_ofac_snapshots(original, original)

    assert diff.added_uids == ("300",)
    assert diff.removed_uids == ("200",)
    assert diff.modified_uids == ("100",)
    assert same.added_uids == same.removed_uids == same.modified_uids == ()
    assert diff.list_kind is OfacSlsListKind.SDN


class FakeTransport:
    def __init__(self, document: OfacSlsHttpDocument | Exception) -> None:
        self.document = document
        self.calls: list[tuple[str, int]] = []

    def get(self, url: str, *, maximum_bytes: int) -> OfacSlsHttpDocument:
        self.calls.append((url, maximum_bytes))
        if isinstance(self.document, Exception):
            raise self.document
        return self.document


def document(
    *,
    content: bytes | None = None,
    url: str = OFAC_SDN_XML_URL,
    content_type: str = "text/xml; charset=utf-8",
    last_modified: str | None = "Fri, 07 Aug 2026 18:36:51 GMT",
    digest: str | None = None,
) -> OfacSlsHttpDocument:
    data = content if content is not None else source_xml()
    actual_digest = digest
    if digest is None:
        actual_digest = f"sha-256{hashlib.sha256(data).hexdigest()}"
    return OfacSlsHttpDocument(
        final_url=url,
        content_type=content_type,
        content=data,
        last_modified=last_modified,
        digest=actual_digest,
    )


def valid_redirect(*, source_name: str = "SDN", query_suffix: str = "") -> str:
    return (
        "https://wc2h-sls-prod-public-published.s3.us-gov-west-1.amazonaws.com/"
        f"Published/version/date/object/{source_name}.XML?"
        "X-Amz-Algorithm=AWS4-HMAC-SHA256&"
        "X-Amz-Credential=key/20260810/us-gov-west-1/s3/aws4_request&"
        "X-Amz-Date=20260810T100000Z&X-Amz-Expires=3600&"
        "X-Amz-Security-Token=token&"
        f"X-Amz-Signature={'a' * 64}&X-Amz-SignedHeaders=host&"
        "response-content-disposition=attachment&response-content-type=text/xml"
        f"{query_suffix}"
    )


def test_connector_binds_fixed_url_digest_media_type_and_clock() -> None:
    transport = FakeTransport(document())
    connector = OfacSlsOfficialSourceConnector(transport, clock=lambda: NOW)

    result = connector.retrieve(OfacSlsListKind.SDN)

    assert transport.calls == [(OFAC_SDN_XML_URL, MAX_OFAC_RAW_BYTES)]
    assert result.list_kind is OfacSlsListKind.SDN
    assert result.retrieved_at == NOW
    assert result.source_last_modified == LAST_MODIFIED
    assert result.content_type == "text/xml"
    assert result.server_digest == result.content_hash
    assert OfacSlsXmlParser().parse(result).entries[0].uid == "100"

    no_headers = FakeTransport(replace(document(last_modified=None), digest=None))
    result_without_headers = OfacSlsOfficialSourceConnector(
        no_headers, clock=lambda: NOW
    ).retrieve(OfacSlsListKind.SDN)
    assert result_without_headers.source_last_modified is None
    assert result_without_headers.server_digest is None


@pytest.mark.parametrize(
    ("doc", "code"),
    [
        (
            document(url=OFAC_CONSOLIDATED_XML_URL),
            OfacSlsSourceErrorCode.RESPONSE_INVALID,
        ),
        (document(content_type="text/html"), OfacSlsSourceErrorCode.RESPONSE_INVALID),
        (
            document(digest="sha-256" + "0" * 64),
            OfacSlsSourceErrorCode.CONTENT_DIGEST_MISMATCH,
        ),
        (document(digest="bad"), OfacSlsSourceErrorCode.RESPONSE_INVALID),
        (document(last_modified="bad"), OfacSlsSourceErrorCode.RESPONSE_INVALID),
    ],
)
def test_connector_rejects_untrusted_response_metadata(
    doc: OfacSlsHttpDocument, code: OfacSlsSourceErrorCode
) -> None:
    assert_error(
        code,
        lambda: OfacSlsOfficialSourceConnector(
            FakeTransport(doc), clock=lambda: NOW
        ).retrieve(OfacSlsListKind.SDN),
    )


def test_connector_maps_transport_failures_and_rejects_bad_dependencies() -> None:
    assert_error(
        OfacSlsSourceErrorCode.NETWORK_UNAVAILABLE,
        lambda: OfacSlsOfficialSourceConnector(
            FakeTransport(RuntimeError("secret")), clock=lambda: NOW
        ).retrieve(OfacSlsListKind.SDN),
    )
    source_failure = OfacSlsSourceError(OfacSlsSourceErrorCode.RESPONSE_INVALID)
    assert_error(
        OfacSlsSourceErrorCode.RESPONSE_INVALID,
        lambda: OfacSlsOfficialSourceConnector(
            FakeTransport(source_failure), clock=lambda: NOW
        ).retrieve(OfacSlsListKind.SDN),
    )
    with pytest.raises(ValueError):
        OfacSlsOfficialSourceConnector(object())  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        OfacSlsOfficialSourceConnector(FakeTransport(document())).retrieve("SDN")  # type: ignore[arg-type]
    assert_error(
        OfacSlsSourceErrorCode.RESPONSE_INVALID,
        lambda: OfacSlsOfficialSourceConnector(
            FakeTransport(document()), clock=lambda: datetime(2026, 1, 1)
        ).retrieve(OfacSlsListKind.SDN),
    )


class FakeResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        content: bytes = b"<x/>",
        headers: dict[str, str] | None = None,
        non_bytes: bool = False,
    ) -> None:
        self.status = status
        self.content = content
        self.headers = headers or {"Content-Type": "text/xml"}
        self.offset = 0
        self.non_bytes = non_bytes

    def getheader(self, name: str) -> str | None:
        return self.headers.get(name)

    def read(self, amount: int | None = None) -> bytes:
        if self.non_bytes:
            return "bad"  # type: ignore[return-value]
        size = len(self.content) if amount is None else amount
        chunk = self.content[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


class FakeConnection:
    def __init__(self, response: FakeResponse, *, fail_request: bool = False) -> None:
        self.response = response
        self.fail_request = fail_request
        self.requests: list[tuple[str, str, dict[str, str] | None]] = []
        self.closed = 0

    def request(
        self, method: str, url: str, *, headers: dict[str, str] | None = None
    ) -> None:
        if self.fail_request:
            raise OSError("secret")
        self.requests.append((method, url, headers))

    def getresponse(self) -> FakeResponse:
        return self.response

    def close(self) -> None:
        self.closed += 1


def transport_for(
    response: FakeResponse, *, fail_request: bool = False
) -> tuple[HttpsOfacSlsTransport, FakeConnection]:
    connection = FakeConnection(response, fail_request=fail_request)
    transport = HttpsOfacSlsTransport(
        timeout_seconds=1,
        connection_factory=lambda _host, _timeout: connection,
    )
    return transport, connection


def test_https_transport_reads_bounded_identity_response_and_closes() -> None:
    data = b"0123456789"
    transport, connection = transport_for(
        FakeResponse(
            content=data,
            headers={
                "Content-Type": "text/xml",
                "Content-Length": str(len(data)),
                "Last-Modified": "date",
                "Digest": "digest",
            },
        )
    )

    result = transport.get(OFAC_SDN_XML_URL, maximum_bytes=len(data))

    assert result.content == data
    assert result.last_modified == "date"
    assert result.digest == "digest"
    assert connection.closed == 1
    assert connection.requests[0][0:2] == (
        "GET",
        "/api/PublicationPreview/exports/SDN.XML",
    )
    request_headers = connection.requests[0][2]
    assert request_headers is not None
    assert request_headers["Accept-Encoding"] == "identity"


def test_https_transport_accepts_one_vetted_official_download_redirect() -> None:
    data = b"official XML"
    origin = FakeConnection(
        FakeResponse(status=302, headers={"Location": valid_redirect()})
    )
    download = FakeConnection(
        FakeResponse(
            content=data,
            headers={
                "Content-Type": "text/xml",
                "Content-Length": str(len(data)),
            },
        )
    )
    connections = [origin, download]
    hosts: list[str] = []

    def factory(host: str, _timeout: float) -> FakeConnection:
        hosts.append(host)
        return connections.pop(0)

    result = HttpsOfacSlsTransport(connection_factory=factory).get(
        OFAC_SDN_XML_URL, maximum_bytes=len(data)
    )

    assert result.content == data
    assert hosts == [
        "sanctionslistservice.ofac.treas.gov",
        "wc2h-sls-prod-public-published.s3.us-gov-west-1.amazonaws.com",
    ]
    assert origin.closed == download.closed == 1
    assert download.requests[0][1].startswith("/Published/")
    assert "X-Amz-Signature=" in download.requests[0][1]


@pytest.mark.parametrize(
    ("response", "maximum", "code"),
    [
        (FakeResponse(status=302), 10, OfacSlsSourceErrorCode.RESPONSE_INVALID),
        (
            FakeResponse(headers={"Content-Encoding": "gzip"}),
            10,
            OfacSlsSourceErrorCode.RESPONSE_INVALID,
        ),
        (
            FakeResponse(headers={"Content-Length": "bad"}),
            10,
            OfacSlsSourceErrorCode.RESPONSE_INVALID,
        ),
        (
            FakeResponse(headers={"Content-Length": "0"}),
            10,
            OfacSlsSourceErrorCode.RESPONSE_INVALID,
        ),
        (
            FakeResponse(headers={"Content-Length": "11"}),
            10,
            OfacSlsSourceErrorCode.RESPONSE_TOO_LARGE,
        ),
        (
            FakeResponse(content=b"12345678901"),
            10,
            OfacSlsSourceErrorCode.RESPONSE_TOO_LARGE,
        ),
        (FakeResponse(content=b""), 10, OfacSlsSourceErrorCode.RESPONSE_INVALID),
        (FakeResponse(non_bytes=True), 10, OfacSlsSourceErrorCode.RESPONSE_INVALID),
    ],
)
def test_https_transport_fails_closed(
    response: FakeResponse, maximum: int, code: OfacSlsSourceErrorCode
) -> None:
    transport, connection = transport_for(response)
    assert_error(code, lambda: transport.get(OFAC_SDN_XML_URL, maximum_bytes=maximum))
    assert connection.closed == 1


def test_https_transport_rejects_input_and_maps_network_error() -> None:
    transport, connection = transport_for(FakeResponse(), fail_request=True)
    assert_error(
        OfacSlsSourceErrorCode.NETWORK_UNAVAILABLE,
        lambda: transport.get(OFAC_SDN_XML_URL, maximum_bytes=10),
    )
    assert connection.closed == 1
    for timeout in (True, 0, 121, "1"):
        with pytest.raises(ValueError):
            HttpsOfacSlsTransport(timeout_seconds=timeout)  # type: ignore[arg-type]
    for maximum in (True, 0, MAX_OFAC_RAW_BYTES + 1):
        with pytest.raises(ValueError):
            HttpsOfacSlsTransport().get(
                OFAC_SDN_XML_URL,
                maximum_bytes=maximum,
            )
    for url in (
        "http://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.XML",
        "https://example.test/api/PublicationPreview/exports/SDN.XML",
        OFAC_SDN_XML_URL + "?x=1",
    ):
        assert_error(
            OfacSlsSourceErrorCode.URL_INVALID,
            lambda url=url: HttpsOfacSlsTransport().get(url, maximum_bytes=10),
        )


@pytest.mark.parametrize(
    "location",
    [
        None,
        "x" * 16_385,
        valid_redirect().replace("https://", "http://", 1),
        valid_redirect().replace(
            "wc2h-sls-prod-public-published.s3.us-gov-west-1.amazonaws.com",
            "example.test",
        ),
        valid_redirect().replace(".amazonaws.com/", ".amazonaws.com:443/"),
        valid_redirect().replace("https://", "https://user" + chr(58) + "pass@", 1),
        valid_redirect() + "#fragment",
        valid_redirect().replace("/Published/", "/Other/"),
        valid_redirect(source_name="CONSOLIDATED"),
        valid_redirect().replace("/version/date/", "/version/../"),
        valid_redirect(query_suffix="&X-Amz-Date=20260810T100000Z"),
        valid_redirect().replace("&X-Amz-Security-Token=token", ""),
        valid_redirect().replace("X-Amz-Security-Token=token", "X-Amz-Security-Token="),
        valid_redirect().replace("AWS4-HMAC-SHA256", "OTHER"),
        valid_redirect().replace("X-Amz-SignedHeaders=host", "X-Amz-SignedHeaders=x"),
        valid_redirect().replace(
            "response-content-type=text/xml", "response-content-type=x"
        ),
        valid_redirect().replace("key/20260810/us-gov-west-1/s3/aws4_request", "wrong"),
        valid_redirect().replace("20260810T100000Z", "bad"),
        valid_redirect().replace("a" * 64, "bad"),
        valid_redirect().replace("X-Amz-Expires=3600", "X-Amz-Expires=bad"),
        valid_redirect().replace("X-Amz-Expires=3600", "X-Amz-Expires=0"),
        valid_redirect().replace("X-Amz-Expires=3600&", "invalid&"),
    ],
)
def test_https_transport_rejects_untrusted_redirects(
    location: str | None,
) -> None:
    transport, connection = transport_for(
        FakeResponse(status=302, headers={"Location": location} if location else {})
    )
    assert_error(
        OfacSlsSourceErrorCode.RESPONSE_INVALID,
        lambda: transport.get(OFAC_SDN_XML_URL, maximum_bytes=10),
    )
    assert connection.closed == 1


def test_https_transport_does_not_follow_a_second_redirect() -> None:
    origin = FakeConnection(
        FakeResponse(status=302, headers={"Location": valid_redirect()})
    )
    download = FakeConnection(FakeResponse(status=302))
    connections = [origin, download]
    transport = HttpsOfacSlsTransport(
        connection_factory=lambda _host, _timeout: connections.pop(0)
    )

    assert_error(
        OfacSlsSourceErrorCode.HTTP_STATUS_INVALID,
        lambda: transport.get(OFAC_SDN_XML_URL, maximum_bytes=10),
    )
    assert origin.closed == download.closed == 1


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (b"", OfacSlsSourceErrorCode.XML_INVALID),
        (b"<bad/>", OfacSlsSourceErrorCode.XML_SCHEMA_DRIFT),
        (b"<!DOCTYPE x><x/>", OfacSlsSourceErrorCode.XML_SECURITY_REJECTED),
        (b"<!ENTITY x 'y'><x/>", OfacSlsSourceErrorCode.XML_SECURITY_REJECTED),
        (source_xml(count=3), OfacSlsSourceErrorCode.RECORD_COUNT_MISMATCH),
        (
            source_xml().replace(b"08/07/2026", b"bad"),
            OfacSlsSourceErrorCode.XML_INVALID,
        ),
        (
            source_xml().replace(b"<Record_Count>2", b"<Record_Count>bad"),
            OfacSlsSourceErrorCode.XML_INVALID,
        ),
        (
            source_xml().replace(b"<Record_Count>2", b"<Record_Count>0"),
            OfacSlsSourceErrorCode.XML_BOUNDS_EXCEEDED,
        ),
        (
            source_xml().replace(b"<sdnType>Vessel", b"<sdnType>Other"),
            OfacSlsSourceErrorCode.XML_SCHEMA_DRIFT,
        ),
        (
            source_xml().replace(b"<mainEntry>true", b"<mainEntry>maybe"),
            OfacSlsSourceErrorCode.XML_INVALID,
        ),
        (
            source_xml().replace(b"<tonnage>10", b"<tonnage>-1"),
            OfacSlsSourceErrorCode.XML_INVALID,
        ),
        (
            source_xml().replace(b"<uid>200", b"<uid>100"),
            OfacSlsSourceErrorCode.XML_DUPLICATE_IDENTITY,
        ),
        (
            source_xml().replace(
                b"<program>UKRAINE-EO13662</program>",
                b"<program>RUSSIA-EO14024</program>",
            ),
            OfacSlsSourceErrorCode.XML_DUPLICATE_IDENTITY,
        ),
        (
            source_xml().replace(b"<category>weak", b"<category>other"),
            OfacSlsSourceErrorCode.XML_INVALID,
        ),
    ],
)
def test_parser_rejects_malformed_untrusted_or_inconsistent_xml(
    content: bytes, code: OfacSlsSourceErrorCode
) -> None:
    if not content:
        with pytest.raises(ValueError):
            retrieved(content=content)
        return
    assert_error(code, lambda: OfacSlsXmlParser().parse(retrieved(content=content)))


def test_normalization_and_source_models_validate_invariants() -> None:
    assert normalize_ofac_name("  ＡＣＭＥ\t Logistics ") == "acme logistics"
    assert normalize_ofac_identifier("Registration Number", " ru‐_42 ") == "RU42"
    with pytest.raises(ValueError):
        normalize_ofac_name("")
    with pytest.raises(ValueError):
        normalize_ofac_identifier("Gender", "Male")
    assert "Registration Number" in OFAC_EXACT_IDENTIFIER_TYPES

    with pytest.raises(ValueError):
        OfacSlsHttpDocument("", "x", b"x", None, None)
    with pytest.raises(ValueError):
        OfacSlsHttpDocument("x", "", b"x", None, None)
    with pytest.raises(ValueError):
        OfacSlsHttpDocument("x", "x", "x", None, None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        OfacSlsHttpDocument("x", "x", b"x", "", None)


def test_candidate_and_diff_models_reject_inconsistent_manual_construction() -> None:
    parsed = snapshot()
    query = OfacIdentifierQuery("Registration Number", "RU-42")
    with pytest.raises(ValueError):
        OfacIdentifierCandidates(
            OfacCandidateStatus.NO_CANDIDATE,
            query,
            (OfacIdentifierIndex(parsed).search(query).evidence[0],),
        )
    with pytest.raises(ValueError):
        OfacNameCandidates(OfacCandidateStatus.CANDIDATE, OfacNameQuery("x"), ())
    with pytest.raises(ValueError):
        diff_ofac_snapshots(
            parsed, replace(parsed, list_kind=OfacSlsListKind.CONSOLIDATED)
        )
    with pytest.raises(ValueError):
        diff_ofac_snapshots(parsed, object())  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        OfacSnapshotDiff(
            OfacSlsListKind.SDN,
            "bad",
            parsed.snapshot_id,
            (),
            (),
            (),
        )


def test_retrieved_source_and_snapshot_validate_integrity() -> None:
    data = source_xml()
    value = retrieved()
    with pytest.raises(ValueError):
        replace(value, content_hash="sha256:" + "0" * 64)
    with pytest.raises(ValueError):
        replace(value, server_digest="sha256:" + "0" * 64)
    with pytest.raises(ValueError):
        replace(value, retrieved_at=datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        replace(value, source_last_modified=datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        replace(value, content=b"")
    with pytest.raises(ValueError):
        OfacSlsRetrievedSource(
            list_kind=OfacSlsListKind.SDN,
            retrieved_at=NOW,
            source_last_modified=None,
            content_type="text/xml",
            server_digest=None,
            content_hash=f"sha256:{hashlib.sha256(data).hexdigest()}",
            content="bad",  # type: ignore[arg-type]
        )

    parsed = snapshot()
    with pytest.raises(ValueError):
        replace(parsed, declared_record_count=3)
    with pytest.raises(ValueError):
        replace(parsed, entries=tuple(reversed(parsed.entries)))
    with pytest.raises(ValueError):
        replace(parsed, raw_content_hash="bad")
    with pytest.raises(ValueError):
        replace(parsed, raw_byte_length=0)


def test_domain_components_reject_invalid_values() -> None:
    parsed = snapshot()
    entry = parsed.entries[0]
    alias = entry.aliases[0]
    address = entry.addresses[0]
    identifier = entry.identifiers[0]
    fact = entry.facts[0]
    vessel = entry.vessel_info
    assert vessel is not None

    invalid_values = (
        lambda: replace(alias, uid="0"),
        lambda: replace(alias, category="other"),
        lambda: replace(alias, normalized_name="wrong"),
        lambda: replace(address, address_lines=("",)),
        lambda: replace(identifier, normalized_number="wrong"),
        lambda: replace(fact, main_entry=cast(Any, 1)),
        lambda: replace(vessel, tonnage=-1),
        lambda: replace(entry, whole_name="wrong"),
        lambda: replace(entry, programs=("B", "A")),
        lambda: replace(entry, aliases=(alias, alias)),
        lambda: replace(entry, subject_type=OfacSubjectType.ENTITY),
    )
    for action in invalid_values:
        with pytest.raises(ValueError):
            action()


def test_default_connection_factory_and_error_type_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = object()
    monkeypatch.setattr(ssl, "create_default_context", lambda **_kwargs: sentinel)

    class Connection:
        def __init__(self, host: str, *, timeout: float, context: object) -> None:
            assert host == "host"
            assert timeout == 1.0
            assert context is sentinel

    monkeypatch.setattr(http.client, "HTTPSConnection", Connection)
    assert isinstance(adapter._default_connection("host", 1.0), Connection)
    with pytest.raises(ValueError):
        OfacSlsSourceError("bad")  # type: ignore[arg-type]


def test_transport_and_retrieved_source_cover_defensive_type_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid_allowed_url = (
        "https://sanctionslistservice.ofac.treas.gov:443/"
        "api/PublicationPreview/exports/SDN.XML"
    )
    monkeypatch.setattr(adapter, "_ALLOWED_URLS", frozenset({invalid_allowed_url}))
    assert_error(
        OfacSlsSourceErrorCode.URL_INVALID,
        lambda: adapter._validate_url(invalid_allowed_url),
    )

    value = retrieved()
    with pytest.raises(ValueError):
        replace(value, list_kind=cast(Any, "SDN"))
    with pytest.raises(ValueError):
        replace(value, content_type="")

    assert_error(
        OfacSlsSourceErrorCode.RESPONSE_INVALID,
        lambda: OfacSlsOfficialSourceConnector(
            FakeTransport(document(last_modified="Fri, 07 Aug 2026 18:36:51")),
            clock=lambda: NOW,
        ).retrieve(OfacSlsListKind.SDN),
    )


def test_parser_accepts_absent_optional_collections_and_vessel_numbers() -> None:
    content = source_xml()
    content = content.replace(
        b"<tonnage>10</tonnage><grossRegisteredTonnage>20</grossRegisteredTonnage>",
        b"",
    )
    content = content.replace(
        b"<idList><id><uid>201</uid><idType>Registration Number</idType><idNumber>RU-42</idNumber></id></idList>",
        b"",
    )
    content = content.replace(
        b"<akaList><aka><uid>202</uid><type>a.k.a.</type><category>strong</category><lastName>ACME SHIPPING</lastName></aka></akaList>",
        b"",
    )

    parsed = OfacSlsXmlParser().parse(retrieved(content=content))

    assert parsed.entries[0].vessel_info is not None
    assert parsed.entries[0].vessel_info.tonnage is None
    assert parsed.entries[0].vessel_info.gross_registered_tonnage is None
    assert parsed.entries[1].aliases == ()
    assert parsed.entries[1].identifiers == ()


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (
            source_xml().replace(b"<sdnEntry>", b'<sdnEntry unexpected="1">', 1),
            OfacSlsSourceErrorCode.XML_SCHEMA_DRIFT,
        ),
        (
            source_xml().replace(
                b"<title>Carrier</title>", b"<title>Carrier</title><unexpected/>"
            ),
            OfacSlsSourceErrorCode.XML_SCHEMA_DRIFT,
        ),
        (
            source_xml().replace(
                b"<title>Carrier</title>",
                b"<title>Carrier</title><title>Duplicate</title>",
            ),
            OfacSlsSourceErrorCode.XML_SCHEMA_DRIFT,
        ),
        (
            source_xml().replace(
                b"<title>Carrier</title>", b"<title><nested/></title>"
            ),
            OfacSlsSourceErrorCode.XML_SCHEMA_DRIFT,
        ),
        (
            source_xml().replace(b"<title>Carrier</title>", b"<title/>"),
            OfacSlsSourceErrorCode.XML_INVALID,
        ),
        (
            source_xml().replace(b"<tonnage>10</tonnage>", b"<tonnage>bad</tonnage>"),
            OfacSlsSourceErrorCode.XML_INVALID,
        ),
        (
            source_xml().replace(
                b"</publshInformation>",
                b"</publshInformation><publshInformation><Publish_Date>08/07/2026</Publish_Date><Record_Count>2</Record_Count></publshInformation>",
                1,
            ),
            OfacSlsSourceErrorCode.XML_SCHEMA_DRIFT,
        ),
        (
            source_xml().replace(
                b"<publshInformation>\n    <Publish_Date>08/07/2026</Publish_Date>\n    <Record_Count>2</Record_Count>\n  </publshInformation>",
                b"",
            ),
            OfacSlsSourceErrorCode.XML_SCHEMA_DRIFT,
        ),
        (
            source_xml().replace(b"</sdnList>", b"<unexpected/></sdnList>"),
            OfacSlsSourceErrorCode.XML_SCHEMA_DRIFT,
        ),
        (source_xml()[:-10], OfacSlsSourceErrorCode.XML_INVALID),
        (
            f'<sdnList xmlns="{NS}"/>'.encode(),
            OfacSlsSourceErrorCode.XML_INVALID,
        ),
        (
            source_xml().replace(
                b"<programList><program>RUSSIA-EO14024</program><program>UKRAINE-EO13662</program></programList>",
                b"<programList/>",
            ),
            OfacSlsSourceErrorCode.XML_INVALID,
        ),
        (
            source_xml().replace(b"<uid>104</uid>", b"<uid>103</uid>"),
            OfacSlsSourceErrorCode.XML_DUPLICATE_IDENTITY,
        ),
        (
            source_xml().replace(
                b"<firstName>ACME</firstName><lastName>LINES</lastName>", b""
            ),
            OfacSlsSourceErrorCode.XML_INVALID,
        ),
        (
            source_xml().replace(b"<address><uid>105</uid>", b"<address><uid>0</uid>"),
            OfacSlsSourceErrorCode.XML_INVALID,
        ),
        (
            source_xml().replace(b"<id><uid>101</uid>", b"<id><uid>0</uid>"),
            OfacSlsSourceErrorCode.XML_INVALID,
        ),
        (
            source_xml().replace(
                b"<nationality><uid>106</uid>", b"<nationality><uid>0</uid>"
            ),
            OfacSlsSourceErrorCode.XML_INVALID,
        ),
        (
            source_xml().replace(
                b"<sdnType>Vessel</sdnType>", b"<sdnType>Entity</sdnType>"
            ),
            OfacSlsSourceErrorCode.XML_INVALID,
        ),
    ],
)
def test_parser_rejects_structural_and_nested_integrity_failures(
    content: bytes, code: OfacSlsSourceErrorCode
) -> None:
    assert_error(code, lambda: OfacSlsXmlParser().parse(retrieved(content=content)))


@pytest.mark.parametrize(
    "limit_name",
    [
        "MAX_OFAC_ENTRIES",
        "MAX_OFAC_PROGRAMS_PER_ENTRY",
        "MAX_OFAC_ALIASES_PER_ENTRY",
        "MAX_OFAC_ADDRESSES_PER_ENTRY",
        "MAX_OFAC_IDENTIFIERS_PER_ENTRY",
        "MAX_OFAC_FACTS_PER_ENTRY",
    ],
)
def test_parser_enforces_every_collection_bound(
    monkeypatch: pytest.MonkeyPatch, limit_name: str
) -> None:
    limit = 0 if limit_name == "MAX_OFAC_ADDRESSES_PER_ENTRY" else 1
    if limit_name == "MAX_OFAC_FACTS_PER_ENTRY":
        limit = 3
    monkeypatch.setattr(adapter, limit_name, limit)
    content = source_xml(count=1) if limit_name == "MAX_OFAC_ENTRIES" else source_xml()
    assert_error(
        OfacSlsSourceErrorCode.XML_BOUNDS_EXCEEDED,
        lambda: OfacSlsXmlParser().parse(retrieved(content=content)),
    )


def test_parser_maps_iterator_and_vessel_model_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError):
        OfacSlsXmlParser().parse(cast(Any, object()))

    def bad_iterparse(*_args: object, **_kwargs: object) -> object:
        raise TypeError("untrusted parser failure")

    monkeypatch.setattr(ElementTree, "iterparse", bad_iterparse)
    assert_error(
        OfacSlsSourceErrorCode.XML_INVALID,
        lambda: OfacSlsXmlParser().parse(retrieved()),
    )
    monkeypatch.undo()

    def bad_vessel(**_kwargs: object) -> object:
        raise ValueError("untrusted model failure")

    monkeypatch.setattr(adapter, "OfacVesselInfo", bad_vessel)
    assert_error(
        OfacSlsSourceErrorCode.XML_INVALID,
        lambda: OfacSlsXmlParser().parse(retrieved()),
    )


def test_domain_models_reject_all_untyped_or_inconsistent_edges() -> None:
    parsed = snapshot()
    entry = parsed.entries[0]
    fact = entry.facts[0]
    identifier_query = OfacIdentifierQuery("Registration Number", "RU-42")
    name_query = OfacNameQuery("ACME")
    identifier_evidence = OfacIdentifierIndex(parsed).search(identifier_query).evidence
    name_evidence = (
        OfacNameIndex(parsed).search(OfacNameQuery("ACME LOGISTICS")).evidence
    )

    invalid_actions: tuple[Callable[[], object], ...] = (
        lambda: replace(fact, kind=cast(Any, "nationality")),
        lambda: replace(entry, list_kind=cast(Any, "SDN")),
        lambda: replace(entry, normalized_name="wrong"),
        lambda: replace(entry, subject_type=cast(Any, "Vessel")),
        lambda: replace(entry, aliases=cast(Any, [entry.aliases[0]])),
        lambda: replace(entry, vessel_info=cast(Any, object())),
        lambda: replace(parsed, list_kind=cast(Any, "SDN")),
        lambda: replace(parsed, publish_date=cast(Any, "2026-08-07")),
        lambda: replace(parsed, declared_record_count=True),
        lambda: replace(parsed, retrieved_at=datetime(2026, 1, 1)),
        lambda: replace(parsed, source_last_modified=datetime(2026, 1, 1)),
        lambda: replace(parsed, entries=cast(Any, [])),
        lambda: OfacIdentifierQuery("Gender", "Male"),
        lambda: OfacIdentifierCandidates(
            cast(Any, "CANDIDATE"), identifier_query, identifier_evidence
        ),
        lambda: OfacIdentifierCandidates(
            OfacCandidateStatus.CANDIDATE, cast(Any, object()), identifier_evidence
        ),
        lambda: OfacIdentifierCandidates(
            OfacCandidateStatus.CANDIDATE,
            identifier_query,
            cast(Any, (object(),)),
        ),
        lambda: OfacIdentifierIndex(cast(Any, object())),
        lambda: OfacIdentifierIndex(parsed).search(cast(Any, object())),
        lambda: OfacNameCandidates(cast(Any, "CANDIDATE"), name_query, name_evidence),
        lambda: OfacNameCandidates(
            OfacCandidateStatus.CANDIDATE, cast(Any, object()), name_evidence
        ),
        lambda: OfacNameCandidates(
            OfacCandidateStatus.CANDIDATE, name_query, cast(Any, (object(),))
        ),
        lambda: OfacNameIndex(cast(Any, object())),
        lambda: OfacNameIndex(parsed).search(cast(Any, object())),
    )
    for action in invalid_actions:
        with pytest.raises(ValueError):
            action()


def test_diff_models_reject_cross_list_overlap_and_bad_uid_collections() -> None:
    parsed = snapshot()
    consolidated_entries = tuple(
        replace(entry, list_kind=OfacSlsListKind.CONSOLIDATED)
        for entry in parsed.entries
    )
    consolidated = replace(
        parsed,
        list_kind=OfacSlsListKind.CONSOLIDATED,
        entries=consolidated_entries,
    )
    with pytest.raises(ValueError):
        diff_ofac_snapshots(parsed, consolidated)

    valid = (parsed.snapshot_id, parsed.snapshot_id)
    invalid_actions: tuple[Callable[[], object], ...] = (
        lambda: OfacSnapshotDiff(cast(Any, "SDN"), *valid, (), (), ()),
        lambda: OfacSnapshotDiff(OfacSlsListKind.SDN, *valid, ("2", "1"), (), ()),
        lambda: OfacSnapshotDiff(OfacSlsListKind.SDN, *valid, ("1",), ("1",), ()),
    )
    for action in invalid_actions:
        with pytest.raises(ValueError):
            action()
