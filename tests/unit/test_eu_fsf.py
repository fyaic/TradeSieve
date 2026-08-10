"""Official EU FSF source, parser, integrity, and exact-match evidence."""

from __future__ import annotations

import http.client
import json
import ssl
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any, cast

import pytest

import tradesieve.adapters.eu_fsf as eu_adapter
from tradesieve.adapters.eu_fsf import (
    EuFsfDistribution,
    EuFsfOfficialSourceConnector,
    EuFsfRetrievedSource,
    EuFsfSourceError,
    EuFsfSourceErrorCode,
    EuFsfXmlParser,
    HttpsEuFsfTransport,
)
from tradesieve.domain.eu_fsf import (
    EU_FSF_DATASET_ID,
    EU_FSF_DATASET_URL,
    EU_FSF_DISTRIBUTION_TITLE,
    EU_FSF_REUSE_LICENCE_ID,
    EU_FSF_REUSE_LICENCE_RESOURCE,
    MAX_EU_FSF_RAW_BYTES,
    EuFsfAlias,
    EuFsfEntity,
    EuFsfExactEvidence,
    EuFsfExactIndex,
    EuFsfExactMatch,
    EuFsfExactMatchStatus,
    EuFsfExactQuery,
    EuFsfIdentifier,
    EuFsfNameCandidates,
    EuFsfNameCandidateStatus,
    EuFsfNameEvidence,
    EuFsfNameIndex,
    EuFsfNameQuery,
    EuFsfRegulation,
    EuFsfSnapshot,
    EuFsfSubjectType,
    normalize_eu_fsf_identifier,
    normalize_eu_fsf_name,
)
from tradesieve.ports.eu_fsf import EuFsfHttpDocument

NOW = datetime(2026, 8, 10, 3, tzinfo=UTC)
DOWNLOAD_URL = (
    "https://webgate.ec.europa.eu/fsd/fsf/public/files/"
    "xmlFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw"
)


def fsf_entity(
    *,
    entity_id: str = "983",
    reference: str = "EU.620.38",
    subject: str = "enterprise",
    alias_id: str = "2080",
    name: str = "Benevolence International Foundation",
    identifier_id: str = "161565",
    identifier_type: str = "other",
    identifier_number: str = "36-3823186",
    identifier_country: str = "US",
    known_expired: str = "false",
    known_false: str = "false",
    reported_lost: str = "false",
    revoked: str = "false",
    extra_entity_attribute: str = "",
    extra_entity_child: str = "",
    extra_alias_child: str = "",
    extra_identifier_child: str = "",
) -> str:
    return f"""
    <sanctionEntity designationDate="2002-11-21" designationDetails=""
      unitedNationId="" euReferenceNumber="{reference}" logicalId="{entity_id}"
      {extra_entity_attribute}>
      <regulation regulationType="amendment" organisationType="commission"
        publicationDate="2024-01-17" entryIntoForceDate="2024-01-18"
        numberTitle="2024/372 (OJ L18012024)" programme="TAQA" logicalId="189099">
        <publicationUrl>https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=OJ:L_202400372</publicationUrl>
      </regulation>
      <subjectType code="{subject}" classificationCode="E"/>
      <nameAlias firstName="" middleName="" lastName="" wholeName="{name}"
        function="" gender="" title="" nameLanguage="EN" strong="true"
        regulationLanguage="en" logicalId="{alias_id}">
        <remark>source remark</remark>
        <regulationSummary regulationType="amendment"/>
        {extra_alias_child}
      </nameAlias>
      <address city="Palos Hills" countryIso2Code="US" logicalId="302"/>
      <identification diplomatic="false" knownExpired="{known_expired}"
        knownFalse="{known_false}" reportedLost="{reported_lost}"
        revokedByIssuer="{revoked}" issuedBy="" latinNumber=""
        nameOnDocument="" number="{identifier_number}" region=""
        identificationTypeCode="{identifier_type}"
        identificationTypeDescription="Other identification number"
        countryIso2Code="{identifier_country}" countryDescription="UNITED STATES"
        regulationLanguage="en" logicalId="{identifier_id}">
        <remark>Employer Identification Number</remark>
        <regulationSummary regulationType="amendment"/>
        {extra_identifier_child}
      </identification>
      {extra_entity_child}
    </sanctionEntity>
    """


def fsf_xml(
    entities: str | None = None,
    *,
    generation: str = "2026-08-05T16:47:04.449+02:00",
    global_file_id: str = "184961",
    root_tag: str = "export",
    root_attributes: str = "",
    prefix: str = "",
) -> bytes:
    body = fsf_entity() if entities is None else entities
    return (
        f'{prefix}<?xml version="1.0" encoding="UTF-8"?>'
        f'<{root_tag} xmlns="http://eu.europa.ec/fpi/fsd/export" '
        f'generationDate="{generation}" globalFileId="{global_file_id}" '
        f"{root_attributes}>{body}</{root_tag}>"
    ).encode()


def regulation() -> EuFsfRegulation:
    return EuFsfRegulation(
        programme="TAQA",
        number_title="2024/372",
        publication_date=date(2024, 1, 17),
        entry_into_force_date=date(2024, 1, 18),
        publication_url="https://eur-lex.europa.eu/example",
        native_locator="/entity/regulation",
    )


def alias(logical_id: str = "1", name: str = "Example Entity") -> EuFsfAlias:
    return EuFsfAlias.create(
        logical_id=logical_id,
        whole_name=name,
        strong=True,
        language="EN",
        native_locator=f"/entity/alias/{logical_id}",
    )


def identifier(
    logical_id: str = "10",
    *,
    number: str = "36-3823186",
    type_code: str = "regnumber",
    country: str | None = "US",
    known_expired: bool = False,
    known_false: bool = False,
    reported_lost: bool = False,
    revoked: bool = False,
) -> EuFsfIdentifier:
    return EuFsfIdentifier.create(
        logical_id=logical_id,
        type_code=type_code,
        number=number,
        country_code=country,
        known_expired=known_expired,
        known_false=known_false,
        reported_lost=reported_lost,
        revoked_by_issuer=revoked,
        native_locator=f"/entity/identifier/{logical_id}",
    )


def entity(
    logical_id: str = "1",
    *,
    reference: str = "EU.1",
    identifiers: tuple[EuFsfIdentifier, ...] | None = None,
) -> EuFsfEntity:
    return EuFsfEntity(
        logical_id=logical_id,
        eu_reference_number=reference,
        united_nations_id=None,
        designation_date=date(2026, 8, 1),
        subject_type=EuFsfSubjectType.ENTERPRISE,
        regulation=regulation(),
        aliases=(alias(),),
        identifiers=(identifier(),) if identifiers is None else identifiers,
    )


def snapshot(entities: tuple[EuFsfEntity, ...] | None = None) -> EuFsfSnapshot:
    return EuFsfSnapshot(
        generation_date=datetime(2026, 8, 5, 16, 47, tzinfo=UTC),
        global_file_id="184961",
        raw_content_hash="sha256:" + "a" * 64,
        raw_byte_length=100,
        entities=(entity(),) if entities is None else entities,
    )


def distribution() -> EuFsfDistribution:
    return EuFsfDistribution(
        distribution_id="distribution-1",
        download_url=DOWNLOAD_URL,
        licence_id=EU_FSF_REUSE_LICENCE_ID,
        licence_resource=EU_FSF_REUSE_LICENCE_RESOURCE,
    )


def catalogue_payload(
    *,
    download_url: object = DOWNLOAD_URL,
    access_url: object = DOWNLOAD_URL,
    licence_id: object = EU_FSF_REUSE_LICENCE_ID,
    licence_resource: object = EU_FSF_REUSE_LICENCE_RESOURCE,
    distributions: list[object] | None = None,
) -> bytes:
    selected: object = {
        "id": "distribution-1",
        "title": {"en": EU_FSF_DISTRIBUTION_TITLE},
        "format": {"id": "XML"},
        "access_url": [access_url],
        "download_url": [download_url],
        "license": {"id": licence_id, "resource": licence_resource},
    }
    values = (
        [selected, {"id": "html", "title": {"en": "Other"}, "format": None}]
        if distributions is None
        else distributions
    )
    return json.dumps(
        {"result": {"id": EU_FSF_DATASET_ID, "distributions": values}},
        separators=(",", ":"),
    ).encode()


class FakeTransport:
    def __init__(
        self,
        catalogue: EuFsfHttpDocument,
        source: EuFsfHttpDocument | None = None,
        *,
        failure: Exception | None = None,
    ) -> None:
        self.catalogue = catalogue
        self.source = source
        self.failure = failure
        self.calls: list[tuple[str, int]] = []

    def get(self, url: str, *, maximum_bytes: int) -> EuFsfHttpDocument:
        self.calls.append((url, maximum_bytes))
        if self.failure is not None:
            raise self.failure
        if url == EU_FSF_DATASET_URL:
            return self.catalogue
        assert self.source is not None
        return self.source


def valid_transport() -> FakeTransport:
    return FakeTransport(
        EuFsfHttpDocument(
            EU_FSF_DATASET_URL, "application/json; charset=utf-8", catalogue_payload()
        ),
        EuFsfHttpDocument(DOWNLOAD_URL, "application/xml", fsf_xml()),
    )


def assert_source_error(code: EuFsfSourceErrorCode, action: Any) -> EuFsfSourceError:
    with pytest.raises(EuFsfSourceError) as caught:
        action()
    assert caught.value.code is code
    assert str(caught.value)
    assert "http" not in str(caught.value).lower()
    return caught.value


def corrupt(instance: Any, **changes: Any) -> Any:
    """Bypass static typing only for defensive runtime-corruption tests."""
    return replace(instance, **changes)


def test_live_shaped_xml_parses_into_immutable_evidence_and_exact_match() -> None:
    content = fsf_xml()
    parsed = EuFsfXmlParser().parse(content)

    assert parsed == EuFsfXmlParser().parse(content)
    assert parsed.generation_date.isoformat() == "2026-08-05T16:47:04.449000+02:00"
    assert parsed.global_file_id == "184961"
    assert parsed.raw_byte_length == len(content)
    assert parsed.raw_content_hash.startswith("sha256:")
    assert parsed.snapshot_id.startswith("eu-fsf-")
    assert parsed.content_hash.startswith("sha256:")
    item = parsed.entities[0]
    assert item.logical_id == "983"
    assert item.eu_reference_number == "EU.620.38"
    assert item.subject_type is EuFsfSubjectType.ENTERPRISE
    assert item.designation_date == date(2002, 11, 21)
    assert item.regulation.programme == "TAQA"
    assert item.aliases[0].normalized_name == ("benevolence international foundation")
    assert item.aliases[0].assertion_hash.startswith("sha256:")
    assert item.identifiers[0].normalized_number == "363823186"
    assert item.identifiers[0].assertion_hash.startswith("sha256:")
    assert item.entity_hash.startswith("sha256:")

    result = EuFsfExactIndex(parsed).query(
        EuFsfExactQuery("other", "36 382-3186", "US")
    )
    assert result.status is EuFsfExactMatchStatus.MATCH
    assert len(result.evidence) == 1
    evidence = result.evidence[0]
    assert evidence.snapshot_id == parsed.snapshot_id
    assert evidence.snapshot_content_hash == parsed.content_hash
    assert evidence.eu_reference_number == "EU.620.38"
    assert evidence.country_consistent is True


def test_exact_index_distinguishes_none_match_ambiguity_and_review_required() -> None:
    active = identifier("10", country="US")
    same_entity_second = identifier("11", country=None)
    first = entity("1", reference="EU.1", identifiers=(active, same_entity_second))
    second = entity(
        "2", reference="EU.2", identifiers=(identifier("20", country="CA"),)
    )
    index = EuFsfExactIndex(snapshot((first, second)))

    no_match = index.query(EuFsfExactQuery("regnumber", "not-listed"))
    assert no_match.status is EuFsfExactMatchStatus.NO_MATCH
    assert no_match.evidence == ()
    assert index.snapshot_id.startswith("eu-fsf-")

    ambiguous = index.query(EuFsfExactQuery("regnumber", "36-3823186"))
    assert ambiguous.status is EuFsfExactMatchStatus.AMBIGUOUS
    assert [item.entity_logical_id for item in ambiguous.evidence] == ["1", "1", "2"]
    assert ambiguous.evidence[0].country_consistent is None

    conflicting_index = EuFsfExactIndex(
        snapshot(
            (
                entity(
                    "1", reference="EU.1", identifiers=(identifier("10", country="US"),)
                ),
                entity(
                    "2", reference="EU.2", identifiers=(identifier("20", country="CA"),)
                ),
            )
        )
    )
    country_conflict = conflicting_index.query(
        EuFsfExactQuery("regnumber", "36-3823186", "CN")
    )
    assert country_conflict.status is EuFsfExactMatchStatus.REVIEW_REQUIRED
    assert {item.country_consistent for item in country_conflict.evidence} == {False}

    unusable = entity(
        identifiers=(
            identifier(known_expired=True),
            identifier("11", known_false=True),
            identifier("12", reported_lost=True),
            identifier("13", revoked=True),
        )
    )
    review = EuFsfExactIndex(snapshot((unusable,))).query(
        EuFsfExactQuery("regnumber", "36-3823186", "US")
    )
    assert review.status is EuFsfExactMatchStatus.REVIEW_REQUIRED
    assert all(not item.identifier.usable_for_exact_match for item in review.evidence)


def test_same_entity_multiple_exact_assertions_remains_one_entity_match() -> None:
    one = entity(identifiers=(identifier("10"), identifier("11", country=None)))
    result = EuFsfExactIndex(snapshot((one,))).query(
        EuFsfExactQuery("regnumber", "36-3823186")
    )
    assert result.status is EuFsfExactMatchStatus.MATCH
    assert len(result.evidence) == 2


def test_name_index_returns_review_candidates_without_claiming_exact_match() -> None:
    first = replace(
        entity("1", reference="EU.1"),
        aliases=(alias("1", "  北方  国际物流  "),),
    )
    second = replace(
        entity("2", reference="EU.2"),
        aliases=(replace(alias("2", "СЕВЕР ЛОГИСТИК"), strong=False),),
    )
    index = EuFsfNameIndex(snapshot((first, second)))

    candidate = index.query(EuFsfNameQuery("北方 国际物流"))
    assert candidate.status is EuFsfNameCandidateStatus.CANDIDATE
    assert [item.eu_reference_number for item in candidate.evidence] == ["EU.1"]
    assert candidate.evidence[0].alias.assertion_hash.startswith("sha256:")
    assert index.query(EuFsfNameQuery("север логистик")).status is (
        EuFsfNameCandidateStatus.REVIEW_REQUIRED
    )
    assert index.query(EuFsfNameQuery("not listed")).status is (
        EuFsfNameCandidateStatus.NO_CANDIDATE
    )


def test_name_index_distinguishes_same_entity_aliases_from_ambiguous_entities() -> None:
    first = replace(
        entity("1", reference="EU.1"),
        aliases=(
            alias("1", "Listed Example"),
            alias("2", "ＬＩＳＴＥＤ   EXAMPLE"),
        ),
    )
    same_entity = EuFsfNameIndex(snapshot((first,))).query(
        EuFsfNameQuery("listed example")
    )
    assert same_entity.status is EuFsfNameCandidateStatus.CANDIDATE
    assert len(same_entity.evidence) == 2

    second = replace(
        entity("2", reference="EU.2"),
        aliases=(alias("3", "Listed Example"),),
    )
    ambiguous = EuFsfNameIndex(snapshot((first, second))).query(
        EuFsfNameQuery("Listed Example")
    )
    assert ambiguous.status is EuFsfNameCandidateStatus.AMBIGUOUS
    assert [item.entity_logical_id for item in ambiguous.evidence] == ["1", "1", "2"]


def test_normalization_is_bounded_typed_and_does_not_make_names_identifiers() -> None:
    assert normalize_eu_fsf_name("  ＡＣＭＥ\t Logistics ") == "acme logistics"
    assert normalize_eu_fsf_identifier("regnumber", " 36‐382_3186 ") == ("363823186")
    assert normalize_eu_fsf_identifier("imo", " IMO 1234567 ") == "1234567"
    assert normalize_eu_fsf_identifier("swiftbic", " abcd us 00 ") == "ABCDUS00"

    for action in (
        lambda: normalize_eu_fsf_name(""),
        lambda: normalize_eu_fsf_identifier("unknown", "1"),
        lambda: normalize_eu_fsf_identifier("regnumber", "---"),
        lambda: EuFsfExactQuery("unknown", "1"),
        lambda: EuFsfExactQuery("regnumber", "", None),
        lambda: EuFsfExactQuery("regnumber", "1", "USA"),
    ):
        with pytest.raises(ValueError):
            action()


def test_parser_skips_non_identifier_placeholder_and_accepts_unknown_country_code() -> (
    None
):
    content = fsf_xml(fsf_entity(identifier_number="-", identifier_country="00"))
    parsed = EuFsfXmlParser().parse(content)
    assert parsed.entities[0].identifiers == ()

    content = fsf_xml(fsf_entity(identifier_country="00"))
    parsed = EuFsfXmlParser().parse(content)
    assert parsed.entities[0].identifiers[0].country_code is None


def test_official_connector_discovers_fixed_distribution_and_hashes_bytes() -> None:
    transport = valid_transport()
    connector = EuFsfOfficialSourceConnector(transport, clock=lambda: NOW)

    first = connector.retrieve()
    second = connector.retrieve()

    assert first == second
    assert first.retrieved_at == NOW
    assert first.content == fsf_xml()
    assert first.content_hash.startswith("sha256:")
    assert first.content_type == "application/xml"
    assert first.distribution.download_url == DOWNLOAD_URL
    assert first.distribution.licence_id == EU_FSF_REUSE_LICENCE_ID
    assert [item[0] for item in transport.calls] == [
        EU_FSF_DATASET_URL,
        DOWNLOAD_URL,
        EU_FSF_DATASET_URL,
        DOWNLOAD_URL,
    ]


@pytest.mark.parametrize(
    ("catalogue", "code"),
    [
        (
            EuFsfHttpDocument(EU_FSF_DATASET_URL, "text/html", b"{}"),
            EuFsfSourceErrorCode.CATALOGUE_INVALID,
        ),
        (
            EuFsfHttpDocument(EU_FSF_DATASET_URL + "/moved", "application/json", b"{}"),
            EuFsfSourceErrorCode.CATALOGUE_INVALID,
        ),
        (
            EuFsfHttpDocument(EU_FSF_DATASET_URL, "application/json", b"\xff"),
            EuFsfSourceErrorCode.CATALOGUE_INVALID,
        ),
        (
            EuFsfHttpDocument(
                EU_FSF_DATASET_URL,
                "application/json",
                b'{"result":{},"result":{}}',
            ),
            EuFsfSourceErrorCode.CATALOGUE_INVALID,
        ),
        (
            EuFsfHttpDocument(
                EU_FSF_DATASET_URL,
                "application/json",
                json.dumps({"result": {"id": "wrong", "distributions": []}}).encode(),
            ),
            EuFsfSourceErrorCode.CATALOGUE_INVALID,
        ),
        (
            EuFsfHttpDocument(
                EU_FSF_DATASET_URL,
                "application/json",
                json.dumps(
                    {"result": {"id": EU_FSF_DATASET_ID, "distributions": []}}
                ).encode(),
            ),
            EuFsfSourceErrorCode.CATALOGUE_INVALID,
        ),
    ],
)
def test_connector_rejects_invalid_catalogue_documents(
    catalogue: EuFsfHttpDocument, code: EuFsfSourceErrorCode
) -> None:
    connector = EuFsfOfficialSourceConnector(
        FakeTransport(catalogue), clock=lambda: NOW
    )
    assert_source_error(code, connector.retrieve)


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (
            catalogue_payload(distributions=["invalid"]),
            EuFsfSourceErrorCode.CATALOGUE_INVALID,
        ),
        (
            catalogue_payload(distributions=[{"title": "invalid", "format": {}}]),
            EuFsfSourceErrorCode.DISTRIBUTION_INVALID,
        ),
        (
            catalogue_payload(
                distributions=[
                    {
                        "id": "bad",
                        "title": {"en": EU_FSF_DISTRIBUTION_TITLE},
                        "format": {"id": "XML"},
                        "license": {},
                    }
                ]
            ),
            EuFsfSourceErrorCode.DISTRIBUTION_INVALID,
        ),
        (
            catalogue_payload(download_url="https://attacker.invalid/file"),
            EuFsfSourceErrorCode.DISTRIBUTION_INVALID,
        ),
        (
            catalogue_payload(access_url="https://attacker.invalid/file"),
            EuFsfSourceErrorCode.DISTRIBUTION_INVALID,
        ),
        (
            catalogue_payload(licence_id="wrong"),
            EuFsfSourceErrorCode.DISTRIBUTION_INVALID,
        ),
        (
            catalogue_payload(licence_resource="https://wrong.invalid"),
            EuFsfSourceErrorCode.DISTRIBUTION_INVALID,
        ),
        (
            catalogue_payload(distributions=[]),
            EuFsfSourceErrorCode.CATALOGUE_INVALID,
        ),
    ],
)
def test_connector_rejects_non_exact_or_ambiguous_distribution(
    payload: bytes, code: EuFsfSourceErrorCode
) -> None:
    connector = EuFsfOfficialSourceConnector(
        FakeTransport(
            EuFsfHttpDocument(EU_FSF_DATASET_URL, "application/json", payload)
        ),
        clock=lambda: NOW,
    )
    assert_source_error(code, connector.retrieve)


def test_connector_rejects_skipped_candidate_empty_id_and_download_failures() -> None:
    skipped = catalogue_payload(
        distributions=[
            {
                "id": "other",
                "title": {"en": "Other"},
                "format": {"id": "JSON"},
            }
        ]
    )
    assert_source_error(
        EuFsfSourceErrorCode.DISTRIBUTION_INVALID,
        EuFsfOfficialSourceConnector(
            FakeTransport(
                EuFsfHttpDocument(EU_FSF_DATASET_URL, "application/json", skipped)
            )
        ).retrieve,
    )
    empty_id = catalogue_payload(
        distributions=[
            {
                "id": "",
                "title": {"en": EU_FSF_DISTRIBUTION_TITLE},
                "format": {"id": "XML"},
                "access_url": [DOWNLOAD_URL],
                "download_url": [DOWNLOAD_URL],
                "license": {
                    "id": EU_FSF_REUSE_LICENCE_ID,
                    "resource": EU_FSF_REUSE_LICENCE_RESOURCE,
                },
            }
        ]
    )
    assert_source_error(
        EuFsfSourceErrorCode.DISTRIBUTION_INVALID,
        EuFsfOfficialSourceConnector(
            FakeTransport(
                EuFsfHttpDocument(EU_FSF_DATASET_URL, "application/json", empty_id)
            )
        ).retrieve,
    )

    class DownloadFailureTransport(FakeTransport):
        def get(self, url: str, *, maximum_bytes: int) -> EuFsfHttpDocument:
            if url == DOWNLOAD_URL:
                raise RuntimeError("secret")
            return super().get(url, maximum_bytes=maximum_bytes)

    assert_source_error(
        EuFsfSourceErrorCode.NETWORK_UNAVAILABLE,
        EuFsfOfficialSourceConnector(
            DownloadFailureTransport(
                EuFsfHttpDocument(
                    EU_FSF_DATASET_URL, "application/json", catalogue_payload()
                )
            )
        ).retrieve,
    )

    class TypedDownloadFailureTransport(DownloadFailureTransport):
        def get(self, url: str, *, maximum_bytes: int) -> EuFsfHttpDocument:
            if url == DOWNLOAD_URL:
                raise EuFsfSourceError(EuFsfSourceErrorCode.HTTP_STATUS)
            return super().get(url, maximum_bytes=maximum_bytes)

    assert_source_error(
        EuFsfSourceErrorCode.HTTP_STATUS,
        EuFsfOfficialSourceConnector(
            TypedDownloadFailureTransport(
                EuFsfHttpDocument(
                    EU_FSF_DATASET_URL, "application/json", catalogue_payload()
                )
            )
        ).retrieve,
    )


def test_connector_rejects_download_shape_clock_and_dependency_failures() -> None:
    invalid_downloads = (
        EuFsfHttpDocument(DOWNLOAD_URL + "x", "application/xml", fsf_xml()),
        EuFsfHttpDocument(DOWNLOAD_URL, "text/html", fsf_xml()),
    )
    for downloaded in invalid_downloads:
        transport = FakeTransport(
            EuFsfHttpDocument(
                EU_FSF_DATASET_URL, "application/json", catalogue_payload()
            ),
            downloaded,
        )
        assert_source_error(
            EuFsfSourceErrorCode.RESPONSE_INVALID,
            EuFsfOfficialSourceConnector(transport, clock=lambda: NOW).retrieve,
        )

    invalid_clock = EuFsfOfficialSourceConnector(
        valid_transport(), clock=lambda: datetime(2026, 8, 10)
    )
    assert_source_error(EuFsfSourceErrorCode.RESPONSE_INVALID, invalid_clock.retrieve)

    for failure in (
        RuntimeError("secret dependency detail"),
        EuFsfSourceError(EuFsfSourceErrorCode.HTTP_STATUS),
    ):
        connector = EuFsfOfficialSourceConnector(
            FakeTransport(
                EuFsfHttpDocument(
                    EU_FSF_DATASET_URL, "application/json", catalogue_payload()
                ),
                failure=failure,
            )
        )
        expected = (
            failure.code
            if isinstance(failure, EuFsfSourceError)
            else EuFsfSourceErrorCode.NETWORK_UNAVAILABLE
        )
        assert_source_error(expected, connector.retrieve)


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (b"", EuFsfSourceErrorCode.XML_BOUNDS_EXCEEDED),
        ("not bytes", EuFsfSourceErrorCode.XML_BOUNDS_EXCEEDED),
        (
            b'<!DOCTYPE export [<!ENTITY x "boom">]><export/>',
            EuFsfSourceErrorCode.XML_SECURITY_REJECTED,
        ),
        (b"<broken>", EuFsfSourceErrorCode.XML_SCHEMA_DRIFT),
        (
            fsf_xml(root_tag="wrong"),
            EuFsfSourceErrorCode.XML_SCHEMA_DRIFT,
        ),
        (
            fsf_xml(root_attributes='unexpected="true"'),
            EuFsfSourceErrorCode.XML_SCHEMA_DRIFT,
        ),
        (fsf_xml(entities=""), EuFsfSourceErrorCode.XML_INVALID),
        (
            fsf_xml(generation="not-a-date"),
            EuFsfSourceErrorCode.XML_INVALID,
        ),
        (
            fsf_xml(generation="2026-08-05T16:47:04"),
            EuFsfSourceErrorCode.XML_INVALID,
        ),
        (
            fsf_xml(fsf_entity(extra_entity_attribute='unknown="1"')),
            EuFsfSourceErrorCode.XML_SCHEMA_DRIFT,
        ),
        (
            fsf_xml(fsf_entity(extra_entity_child="<unknown/>")),
            EuFsfSourceErrorCode.XML_SCHEMA_DRIFT,
        ),
        (
            fsf_xml(fsf_entity(subject="unknown")),
            EuFsfSourceErrorCode.XML_SCHEMA_DRIFT,
        ),
        (
            fsf_xml(fsf_entity(extra_alias_child="<unknown/>")),
            EuFsfSourceErrorCode.XML_SCHEMA_DRIFT,
        ),
        (
            fsf_xml(fsf_entity(extra_identifier_child="<unknown/>")),
            EuFsfSourceErrorCode.XML_SCHEMA_DRIFT,
        ),
        (
            fsf_xml(fsf_entity(known_false="maybe")),
            EuFsfSourceErrorCode.XML_INVALID,
        ),
        (
            fsf_xml(fsf_entity(identifier_type="newtype")),
            EuFsfSourceErrorCode.XML_INVALID,
        ),
    ],
)
def test_parser_fails_closed_on_security_shape_schema_and_value_attacks(
    content: Any, code: EuFsfSourceErrorCode
) -> None:
    assert_source_error(code, lambda: EuFsfXmlParser().parse(content))


def _remove_element(document: str, tag: str) -> str:
    start = document.index(f"<{tag}")
    end = document.index(f"</{tag}>", start) + len(f"</{tag}>")
    return document[:start] + document[end:]


def test_parser_rejects_truncation_root_children_missing_shape_dates_and_text() -> None:
    namespace = "http://eu.europa.ec/fpi/fsd/export"
    truncated = (
        f'<export xmlns="{namespace}" generationDate="{NOW.isoformat()}" '
        'globalFileId="1">'
    ).encode()
    cases = (
        (truncated, EuFsfSourceErrorCode.XML_INVALID),
        (
            fsf_xml(fsf_entity() + "<unknown/>"),
            EuFsfSourceErrorCode.XML_SCHEMA_DRIFT,
        ),
        (
            fsf_xml(_remove_element(fsf_entity(), "regulation")),
            EuFsfSourceErrorCode.XML_INVALID,
        ),
        (
            fsf_xml(_remove_element(fsf_entity(), "nameAlias")),
            EuFsfSourceErrorCode.XML_INVALID,
        ),
        (
            fsf_xml(
                fsf_entity().replace(
                    'publicationDate="2024-01-17"', 'publicationDate="bad"'
                )
            ),
            EuFsfSourceErrorCode.XML_INVALID,
        ),
        (
            fsf_xml(
                fsf_entity().replace(">https://eur-lex", "><child/>https://eur-lex")
            ),
            EuFsfSourceErrorCode.XML_SCHEMA_DRIFT,
        ),
        (
            fsf_xml(
                fsf_entity().replace(
                    ">https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=OJ:L_202400372</publicationUrl>",
                    "></publicationUrl>",
                )
            ),
            EuFsfSourceErrorCode.XML_INVALID,
        ),
        (
            fsf_xml(
                fsf_entity().replace("</publicationUrl>", "</publicationUrl><unknown/>")
            ),
            EuFsfSourceErrorCode.XML_SCHEMA_DRIFT,
        ),
    )
    for content, code in cases:
        assert_source_error(
            code, lambda content=content: EuFsfXmlParser().parse(content)
        )


def test_parser_enforces_entity_alias_and_identifier_caps(
    monkeypatch: Any,
) -> None:
    second = fsf_entity(
        entity_id="984", reference="EU.620.39", alias_id="2081", identifier_id="161566"
    )
    monkeypatch.setattr(eu_adapter, "MAX_EU_FSF_ENTITIES", 1)
    assert_source_error(
        EuFsfSourceErrorCode.XML_BOUNDS_EXCEEDED,
        lambda: EuFsfXmlParser().parse(fsf_xml(fsf_entity() + second)),
    )
    monkeypatch.setattr(eu_adapter, "MAX_EU_FSF_ENTITIES", 20_000)
    monkeypatch.setattr(eu_adapter, "MAX_EU_FSF_ALIASES_PER_ENTITY", 0)
    assert_source_error(
        EuFsfSourceErrorCode.XML_BOUNDS_EXCEEDED,
        lambda: EuFsfXmlParser().parse(fsf_xml()),
    )
    monkeypatch.setattr(eu_adapter, "MAX_EU_FSF_ALIASES_PER_ENTITY", 256)
    monkeypatch.setattr(eu_adapter, "MAX_EU_FSF_IDENTIFIERS_PER_ENTITY", 0)
    assert_source_error(
        EuFsfSourceErrorCode.XML_BOUNDS_EXCEEDED,
        lambda: EuFsfXmlParser().parse(fsf_xml()),
    )


def test_parser_rejects_duplicate_entity_alias_identifier_and_reference_identity() -> (
    None
):
    duplicate_entity = fsf_entity() + fsf_entity()
    assert_source_error(
        EuFsfSourceErrorCode.XML_DUPLICATE_IDENTITY,
        lambda: EuFsfXmlParser().parse(fsf_xml(duplicate_entity)),
    )

    duplicate_reference = fsf_entity() + fsf_entity(
        entity_id="984", alias_id="2081", identifier_id="161566"
    )
    assert_source_error(
        EuFsfSourceErrorCode.XML_DUPLICATE_IDENTITY,
        lambda: EuFsfXmlParser().parse(fsf_xml(duplicate_reference)),
    )

    duplicate_alias = fsf_entity().replace(
        "</nameAlias>",
        "</nameAlias>"
        + fsf_entity()
        .split("<nameAlias", 1)[1]
        .split("</nameAlias>", 1)[0]
        .join(("<nameAlias", "</nameAlias>")),
        1,
    )
    assert_source_error(
        EuFsfSourceErrorCode.XML_DUPLICATE_IDENTITY,
        lambda: EuFsfXmlParser().parse(fsf_xml(duplicate_alias)),
    )

    duplicate_identifier = fsf_entity().replace(
        "</identification>",
        "</identification>"
        + fsf_entity()
        .split("<identification", 1)[1]
        .split("</identification>", 1)[0]
        .join(("<identification", "</identification>")),
        1,
    )
    assert_source_error(
        EuFsfSourceErrorCode.XML_DUPLICATE_IDENTITY,
        lambda: EuFsfXmlParser().parse(fsf_xml(duplicate_identifier)),
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


class ConnectionFactory:
    def __init__(self, connections: list[FakeConnection]) -> None:
        self.connections = connections
        self.calls: list[tuple[str, float, ssl.SSLContext]] = []

    def __call__(
        self, host: str, timeout: float, context: ssl.SSLContext
    ) -> FakeConnection:
        self.calls.append((host, timeout, context))
        return self.connections.pop(0)


def test_https_transport_reads_bounded_identity_response_and_closes() -> None:
    connection = FakeConnection(
        FakeResponse(
            headers={"Content-Length": "7", "Content-Type": "application/json"},
            chunks=[b"pay", b"load", b""],
        ),
        close_failure=RuntimeError("close is best effort"),
    )
    factory = ConnectionFactory([connection])
    context = ssl.create_default_context()
    transport = HttpsEuFsfTransport(
        timeout_seconds=5,
        connection_factory=factory,
        ssl_context=context,
    )

    result = transport.get(EU_FSF_DATASET_URL, maximum_bytes=100)

    assert result.content == b"payload"
    assert result.content_type == "application/json"
    assert result.final_url == EU_FSF_DATASET_URL
    assert connection.closed == 1
    assert connection.requests[0][0] == "GET"
    assert connection.requests[0][1].startswith("/api/hub/search/datasets/")
    assert connection.requests[0][2]["Accept-Encoding"] == "identity"
    assert factory.calls == [("data.europa.eu", 5.0, context)]


@pytest.mark.parametrize("timeout", [True, 0, 121, "30"])
def test_https_transport_rejects_invalid_timeout(timeout: Any) -> None:
    with pytest.raises(ValueError):
        HttpsEuFsfTransport(timeout_seconds=timeout)


@pytest.mark.parametrize("maximum", [True, 0, MAX_EU_FSF_RAW_BYTES + 1])
def test_https_transport_rejects_invalid_maximum(maximum: Any) -> None:
    transport = HttpsEuFsfTransport(connection_factory=ConnectionFactory([]))
    with pytest.raises(ValueError):
        transport.get(EU_FSF_DATASET_URL, maximum_bytes=maximum)


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://data.europa.eu/api/hub/search/datasets/x",
        "https://user@data.europa.eu/api/hub/search/datasets/x",
        "https://data.europa.eu:444/api/hub/search/datasets/x",
        EU_FSF_DATASET_URL + "?extra=1",
        "https://attacker.invalid/file?token=safe",
        "https://webgate.ec.europa.eu/wrong?token=safe",
        "https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content",
        "https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content?token=",
        "https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content?token=bad%20token",
    ],
)
def test_https_transport_rejects_every_non_exact_url_before_network(url: str) -> None:
    transport = HttpsEuFsfTransport(connection_factory=ConnectionFactory([]))
    assert_source_error(
        EuFsfSourceErrorCode.INVALID_URL,
        lambda: transport.get(url, maximum_bytes=100),
    )


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (FakeResponse(status=503), EuFsfSourceErrorCode.HTTP_STATUS),
        (
            FakeResponse(headers={"Content-Encoding": "gzip"}),
            EuFsfSourceErrorCode.RESPONSE_INVALID,
        ),
        (
            FakeResponse(headers={"Content-Length": "invalid"}),
            EuFsfSourceErrorCode.RESPONSE_INVALID,
        ),
        (
            FakeResponse(headers={"Content-Length": "-1"}),
            EuFsfSourceErrorCode.RESPONSE_INVALID,
        ),
        (
            FakeResponse(headers={"Content-Length": "101"}),
            EuFsfSourceErrorCode.RESPONSE_TOO_LARGE,
        ),
        (
            FakeResponse(chunks=[b"x" * 101]),
            EuFsfSourceErrorCode.RESPONSE_TOO_LARGE,
        ),
        (
            FakeResponse(chunks=["not-bytes"]),
            EuFsfSourceErrorCode.RESPONSE_INVALID,
        ),
        (
            FakeResponse(failure=RuntimeError("read failed")),
            EuFsfSourceErrorCode.NETWORK_UNAVAILABLE,
        ),
    ],
)
def test_https_transport_fails_closed_on_response_errors(
    response: FakeResponse, code: EuFsfSourceErrorCode
) -> None:
    connection = FakeConnection(response)
    transport = HttpsEuFsfTransport(connection_factory=ConnectionFactory([connection]))
    assert_source_error(
        code, lambda: transport.get(EU_FSF_DATASET_URL, maximum_bytes=100)
    )
    assert connection.closed == 1


def test_https_transport_validates_redirect_before_second_connection() -> None:
    first = FakeConnection(FakeResponse(status=302, headers={"Location": DOWNLOAD_URL}))
    second = FakeConnection(
        FakeResponse(headers={"Content-Type": "application/xml"}, chunks=[b"x", b""])
    )
    factory = ConnectionFactory([first, second])
    result = HttpsEuFsfTransport(connection_factory=factory).get(
        EU_FSF_DATASET_URL, maximum_bytes=100
    )
    assert result.final_url == DOWNLOAD_URL
    assert result.content == b"x"
    assert [item[0] for item in factory.calls] == [
        "data.europa.eu",
        "webgate.ec.europa.eu",
    ]
    assert first.closed == second.closed == 1

    bad_redirect = FakeConnection(
        FakeResponse(status=302, headers={"Location": "https://attacker.invalid"})
    )
    assert_source_error(
        EuFsfSourceErrorCode.INVALID_URL,
        lambda: HttpsEuFsfTransport(
            connection_factory=ConnectionFactory([bad_redirect])
        ).get(EU_FSF_DATASET_URL, maximum_bytes=100),
    )


def test_https_transport_rejects_missing_and_excess_redirects() -> None:
    missing = FakeConnection(FakeResponse(status=302))
    assert_source_error(
        EuFsfSourceErrorCode.REDIRECT_INVALID,
        lambda: HttpsEuFsfTransport(
            connection_factory=ConnectionFactory([missing])
        ).get(EU_FSF_DATASET_URL, maximum_bytes=100),
    )

    connections = [
        FakeConnection(FakeResponse(status=302, headers={"Location": DOWNLOAD_URL}))
        for _ in range(4)
    ]
    assert_source_error(
        EuFsfSourceErrorCode.REDIRECT_INVALID,
        lambda: HttpsEuFsfTransport(
            connection_factory=ConnectionFactory(connections)
        ).get(EU_FSF_DATASET_URL, maximum_bytes=100),
    )


def test_https_transport_maps_connection_and_request_failures_safely() -> None:
    request_failure = FakeConnection(
        FakeResponse(), request_failure=RuntimeError("request secret")
    )
    assert_source_error(
        EuFsfSourceErrorCode.NETWORK_UNAVAILABLE,
        lambda: HttpsEuFsfTransport(
            connection_factory=ConnectionFactory([request_failure])
        ).get(EU_FSF_DATASET_URL, maximum_bytes=100),
    )
    assert request_failure.closed == 1

    def failing_factory(
        _host: str, _timeout: float, _context: ssl.SSLContext
    ) -> FakeConnection:
        raise RuntimeError("connect secret")

    assert_source_error(
        EuFsfSourceErrorCode.NETWORK_UNAVAILABLE,
        lambda: HttpsEuFsfTransport(connection_factory=failing_factory).get(
            EU_FSF_DATASET_URL, maximum_bytes=100
        ),
    )


def test_defensive_typed_value_objects_reject_corruption() -> None:
    base_alias = alias()
    base_identifier = identifier()
    base_entity = entity()
    base_snapshot = snapshot()
    base_evidence = EuFsfExactEvidence(
        snapshot_id=base_snapshot.snapshot_id,
        snapshot_content_hash=base_snapshot.content_hash,
        entity_logical_id="1",
        eu_reference_number="EU.1",
        subject_type=EuFsfSubjectType.ENTERPRISE,
        identifier=base_identifier,
        country_consistent=True,
    )
    base_match = EuFsfExactMatch(
        EuFsfExactMatchStatus.MATCH,
        EuFsfExactQuery("regnumber", "36-3823186"),
        (base_evidence,),
    )
    base_name_query = EuFsfNameQuery("Example Entity")
    base_name_evidence = EuFsfNameEvidence(
        snapshot_id=base_snapshot.snapshot_id,
        snapshot_content_hash=base_snapshot.content_hash,
        entity_logical_id="1",
        eu_reference_number="EU.1",
        subject_type=EuFsfSubjectType.ENTERPRISE,
        alias=base_alias,
    )
    base_name_candidates = EuFsfNameCandidates(
        EuFsfNameCandidateStatus.CANDIDATE,
        base_name_query,
        (base_name_evidence,),
    )

    invalid_values = (
        lambda: corrupt(regulation(), publication_date=cast(Any, "bad")),
        lambda: corrupt(regulation(), entry_into_force_date=cast(Any, "bad")),
        lambda: corrupt(base_alias, logical_id="bad"),
        lambda: corrupt(base_alias, normalized_name="wrong"),
        lambda: corrupt(base_alias, strong=1),
        lambda: corrupt(base_identifier, logical_id="bad"),
        lambda: corrupt(base_identifier, type_code="bad"),
        lambda: corrupt(base_identifier, normalized_number="wrong"),
        lambda: corrupt(base_identifier, country_code="USA"),
        lambda: corrupt(base_identifier, known_false=1),
        lambda: corrupt(base_entity, logical_id="bad"),
        lambda: corrupt(base_entity, designation_date=cast(Any, "bad")),
        lambda: corrupt(base_entity, eu_reference_number="bad value"),
        lambda: corrupt(base_entity, subject_type="enterprise"),
        lambda: corrupt(base_entity, regulation="bad"),
        lambda: corrupt(base_entity, aliases=()),
        lambda: corrupt(base_entity, identifiers=[]),
        lambda: corrupt(base_snapshot, generation_date=datetime(2026, 8, 5)),
        lambda: corrupt(base_snapshot, global_file_id="bad"),
        lambda: corrupt(base_snapshot, raw_content_hash="bad"),
        lambda: corrupt(base_snapshot, raw_byte_length=True),
        lambda: corrupt(base_snapshot, entities=()),
        lambda: EuFsfExactIndex(cast(Any, "bad")),
        lambda: EuFsfExactIndex(base_snapshot).query(cast(Any, "bad")),
        lambda: corrupt(base_evidence, snapshot_id="bad"),
        lambda: corrupt(base_evidence, snapshot_content_hash="bad"),
        lambda: corrupt(base_evidence, entity_logical_id="bad"),
        lambda: corrupt(base_evidence, eu_reference_number="bad value"),
        lambda: corrupt(base_evidence, subject_type="bad"),
        lambda: corrupt(base_evidence, identifier="bad"),
        lambda: corrupt(base_evidence, country_consistent="yes"),
        lambda: corrupt(base_match, status="MATCH"),
        lambda: corrupt(base_match, query="bad"),
        lambda: corrupt(base_match, evidence=[]),
        lambda: EuFsfExactMatch(
            EuFsfExactMatchStatus.NO_MATCH, base_match.query, (base_evidence,)
        ),
        lambda: EuFsfExactMatch(EuFsfExactMatchStatus.MATCH, base_match.query, ()),
        lambda: EuFsfNameQuery(""),
        lambda: EuFsfNameIndex(cast(Any, "bad")),
        lambda: EuFsfNameIndex(base_snapshot).query(cast(Any, "bad")),
        lambda: corrupt(base_name_evidence, snapshot_id="bad"),
        lambda: corrupt(base_name_evidence, snapshot_content_hash="bad"),
        lambda: corrupt(base_name_evidence, entity_logical_id="bad"),
        lambda: corrupt(base_name_evidence, eu_reference_number="bad value"),
        lambda: corrupt(base_name_evidence, subject_type="bad"),
        lambda: corrupt(base_name_evidence, alias="bad"),
        lambda: corrupt(base_name_candidates, status="CANDIDATE"),
        lambda: corrupt(base_name_candidates, query="bad"),
        lambda: corrupt(base_name_candidates, evidence=[]),
        lambda: EuFsfNameCandidates(
            EuFsfNameCandidateStatus.NO_CANDIDATE,
            base_name_query,
            (base_name_evidence,),
        ),
        lambda: EuFsfNameCandidates(
            EuFsfNameCandidateStatus.CANDIDATE, base_name_query, ()
        ),
        lambda: EuFsfDistribution("", DOWNLOAD_URL, "x", "x"),
        lambda: EuFsfRetrievedSource(
            distribution(), NOW, "application/xml", "bad", b"x"
        ),
        lambda: EuFsfRetrievedSource(
            cast(Any, "bad"), NOW, "application/xml", "sha256:" + "a" * 64, b"x"
        ),
        lambda: EuFsfRetrievedSource(
            distribution(),
            cast(Any, datetime(2026, 8, 10)),
            "application/xml",
            "sha256:" + "a" * 64,
            b"x",
        ),
        lambda: EuFsfRetrievedSource(
            distribution(), NOW, "", "sha256:" + "a" * 64, b"x"
        ),
        lambda: EuFsfRetrievedSource(
            distribution(), NOW, "application/xml", "sha256:" + "a" * 64, b""
        ),
        lambda: EuFsfOfficialSourceConnector(cast(Any, object())),
        lambda: EuFsfSourceError(cast(Any, "bad")),
        lambda: EuFsfHttpDocument("", "x", b"x"),
        lambda: EuFsfHttpDocument("x", "", b"x"),
        lambda: EuFsfHttpDocument("x", "y", cast(Any, "not bytes")),
        lambda: HttpsEuFsfTransport(connection_factory=cast(Any, "bad")),
    )
    for action in invalid_values:
        with pytest.raises(ValueError):
            action()


def test_entity_and_snapshot_order_and_uniqueness_are_integrity_protected() -> None:
    with pytest.raises(ValueError, match="aliases"):
        replace(entity(), aliases=(alias("2"), alias("1")))
    with pytest.raises(ValueError, match="identifiers"):
        replace(entity(), identifiers=(identifier("11"), identifier("10")))
    with pytest.raises(ValueError, match="identifiers"):
        replace(entity(), identifiers=(identifier("10"), identifier("10")))
    with pytest.raises(ValueError, match="entities"):
        snapshot((entity("2", reference="EU.2"), entity("1", reference="EU.1")))
    with pytest.raises(ValueError, match="entities"):
        snapshot((entity("1", reference="EU.1"), entity("1", reference="EU.2")))
    with pytest.raises(ValueError, match="reference"):
        snapshot((entity("1", reference="EU.1"), entity("2", reference="EU.1")))


def test_default_connection_factory_builds_stdlib_https_connection(
    monkeypatch: Any,
) -> None:
    sentinel = object()

    def factory(host: str, *, timeout: float, context: ssl.SSLContext) -> object:
        assert host == "data.europa.eu"
        assert timeout == 3.0
        assert isinstance(context, ssl.SSLContext)
        return sentinel

    monkeypatch.setattr(http.client, "HTTPSConnection", factory)
    context = ssl.create_default_context()
    assert eu_adapter._default_connection("data.europa.eu", 3.0, context) is sentinel
