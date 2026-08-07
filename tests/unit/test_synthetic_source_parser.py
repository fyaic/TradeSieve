"""Attack and boundary evidence for the one finite synthetic source parser."""

from __future__ import annotations

import json
import traceback
from collections.abc import Mapping
from typing import Any

import pytest

from tradesieve.adapters.synthetic_source_parser import SyntheticJsonSourceParser
from tradesieve.domain.source_snapshot import (
    MAX_ASSERTIONS_PER_RECORD,
    MAX_NATIVE_VALUE_BYTES,
    MAX_RAW_OBJECT_BYTES,
    MAX_RECORDS_PER_SNAPSHOT,
)
from tradesieve.ports.source_snapshot import (
    FINITE_PARSER_ERROR_MESSAGES,
    SYNTHETIC_CHARSET,
    SYNTHETIC_MEDIA_TYPE,
    SYNTHETIC_PARSER_VERSION,
    SYNTHETIC_SCHEMA_ID,
    FiniteParserError,
    FiniteParserErrorCode,
    FiniteParserId,
)


def assertion(
    field: object = "entity.name", value: object = "Synthetic Entity"
) -> dict[str, object]:
    return {"field": field, "value": value}


def record(
    record_id: object = "record-1",
    *,
    assertions: object | None = None,
    effective_from: object = "2026-08-01",
    effective_to: object = None,
) -> dict[str, object]:
    return {
        "record_id": record_id,
        "effective_from": effective_from,
        "effective_to": effective_to,
        "assertions": [assertion()] if assertions is None else assertions,
    }


def document(
    records: object | None = None,
    *,
    schema_id: object = SYNTHETIC_SCHEMA_ID,
    declared_record_count: object | None = None,
    extra: Mapping[str, object] | None = None,
) -> dict[str, object]:
    values = [record()] if records is None else records
    result: dict[str, object] = {
        "schema_id": schema_id,
        "declared_record_count": (
            len(values)
            if declared_record_count is None and isinstance(values, list)
            else declared_record_count
        ),
        "records": values,
    }
    if extra:
        result.update(extra)
    return result


def encoded(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def assert_parse_error(
    content: Any,
    code: FiniteParserErrorCode,
    *,
    secret: str | None = None,
) -> None:
    with pytest.raises(FiniteParserError) as caught:
        SyntheticJsonSourceParser().parse(content)
    assert caught.value.code is code
    assert str(caught.value) == FINITE_PARSER_ERROR_MESSAGES[code]
    assert len(str(caught.value)) <= 80
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__
    public_error = "\n".join(
        (
            str(caught.value),
            repr(caught.value),
            "".join(traceback.format_exception(caught.value)),
        )
    )
    if secret is not None:
        assert secret not in public_error


@pytest.fixture
def parser() -> SyntheticJsonSourceParser:
    return SyntheticJsonSourceParser()


def test_parser_identity_shape_normalization_and_native_locators_are_exact(
    parser: SyntheticJsonSourceParser,
) -> None:
    content = encoded(
        document(
            [
                record(
                    "record-1",
                    assertions=[
                        assertion("entity.name", "  Ｓｙｎｔｈｅｔｉｃ\t Entity  "),
                        assertion("entity.count", 17),
                        assertion("entity.enabled", True),
                        assertion("entity.disabled", False),
                        assertion("entity.note", None),
                    ],
                ),
                record(
                    "record-2",
                    assertions=[],
                    effective_from=None,
                    effective_to="2026-08-31",
                ),
            ],
            declared_record_count=7,
        )
    )

    first = parser.parse(content)
    second = parser.parse(content)

    assert first == second
    assert parser.parser_id is FiniteParserId.SYNTHETIC_JSON_V1
    assert parser.parser_version == SYNTHETIC_PARSER_VERSION
    assert parser.media_type == SYNTHETIC_MEDIA_TYPE
    assert parser.charset == SYNTHETIC_CHARSET
    assert first.schema_id == SYNTHETIC_SCHEMA_ID
    assert first.declared_record_count == 7
    assert len(first.records) == 2
    assert first.records[0].native_locator == "/records/0"
    assert first.records[1].native_locator == "/records/1"
    assert first.records[1].effective_from is None
    assert first.records[1].effective_to == "2026-08-31"
    parsed_assertions = first.records[0].assertions
    assert tuple(item.native_locator for item in parsed_assertions) == (
        "/records/0/assertions/0/value",
        "/records/0/assertions/1/value",
        "/records/0/assertions/2/value",
        "/records/0/assertions/3/value",
        "/records/0/assertions/4/value",
    )
    assert tuple(item.native_value for item in parsed_assertions) == (
        "  Ｓｙｎｔｈｅｔｉｃ\t Entity  ",
        17,
        True,
        False,
        None,
    )
    assert tuple(item.normalized_value for item in parsed_assertions) == (
        "Synthetic Entity",
        "17",
        "true",
        "false",
        "null",
    )


@pytest.mark.parametrize(
    ("content", "code"),
    [
        ("not-bytes", FiniteParserErrorCode.INVALID_INPUT),
        (b"\xef\xbb\xbf{}", FiniteParserErrorCode.BOM_NOT_ALLOWED),
        (b"\xff", FiniteParserErrorCode.INVALID_ENCODING),
        (b"", FiniteParserErrorCode.INVALID_JSON),
        (b"{} trailing-sensitive-value", FiniteParserErrorCode.INVALID_JSON),
        (b'{"schema_id":"a","schema_id":"b"}', FiniteParserErrorCode.DUPLICATE_KEY),
        (
            encoded(document([record(assertions=[assertion(value=1.25)])])),
            FiniteParserErrorCode.FLOAT_NOT_ALLOWED,
        ),
    ],
)
def test_parser_rejects_input_encoding_json_duplicate_float_and_trailing_attacks(
    content: Any, code: FiniteParserErrorCode
) -> None:
    assert_parse_error(
        content,
        code,
        secret="trailing-sensitive-value",  # pragma: allowlist secret
    )


@pytest.mark.parametrize(
    ("content", "code", "secret"),
    [
        (
            b"\xffsensitive-raw-byte-body",
            FiniteParserErrorCode.INVALID_ENCODING,
            "sensitive-raw-byte-body",
        ),
        (
            b'{"sensitive-json-body":',
            FiniteParserErrorCode.INVALID_JSON,
            "sensitive-json-body",
        ),
    ],
)
def test_external_decode_and_json_failures_suppress_body_bearing_tracebacks(
    content: bytes, code: FiniteParserErrorCode, secret: str
) -> None:
    assert_parse_error(content, code, secret=secret)


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_parser_rejects_every_non_finite_number_token(token: str) -> None:
    content = (
        '{"schema_id":"tradesieve-synthetic-source-v1",'
        '"declared_record_count":1,"records":[{"record_id":"record-1",'
        '"effective_from":null,"effective_to":null,"assertions":['
        f'{{"field":"entity.value","value":{token}}}'
        "]}]}"
    ).encode()

    assert_parse_error(content, FiniteParserErrorCode.NON_FINITE_NUMBER)


@pytest.mark.parametrize(
    "value",
    [
        document(extra={"callback": "sensitive-import-path"}),
        document([{**record(), "url": "https://sensitive.invalid"}]),
        document([record(assertions=[{**assertion(), "template": "sensitive"}])]),
    ],
)
def test_parser_rejects_unknown_fields_at_every_allowed_object_level(
    value: object,
) -> None:
    assert_parse_error(
        encoded(value),
        FiniteParserErrorCode.UNKNOWN_FIELD,
        secret="sensitive",  # pragma: allowlist secret
    )


@pytest.mark.parametrize(
    "value",
    [
        [],
        {"schema_id": SYNTHETIC_SCHEMA_ID, "records": []},
        document(records="not-a-list", declared_record_count=0),
        document(records=[[]]),
        document(records=[{"record_id": "record-1"}]),
        document(records=[record(record_id=None)]),
        document(records=[record(assertions="not-a-list")]),
        document(records=[record(assertions=[[]])]),
        document(records=[record(assertions=[{"field": "entity.name"}])]),
        document(records=[record(assertions=[assertion(field=7)])]),
        document(records=[record(effective_from=7)]),
        document(records=[record(assertions=[assertion(value={"nested": True})])]),
    ],
)
def test_parser_rejects_missing_wrong_type_and_nested_shapes(value: object) -> None:
    assert_parse_error(encoded(value), FiniteParserErrorCode.INVALID_SHAPE)


def test_parser_rejects_deep_json_before_materializing_it() -> None:
    content = b"[" * 2000 + b"0" + b"]" * 2000

    assert_parse_error(content, FiniteParserErrorCode.UNSAFE_NESTING)


def test_parser_rejects_wrong_schema_without_echoing_it() -> None:
    assert_parse_error(
        encoded(document(schema_id="sensitive-unsupported-schema")),
        FiniteParserErrorCode.SCHEMA_MISMATCH,
        secret="sensitive-unsupported-schema",  # pragma: allowlist secret
    )


@pytest.mark.parametrize(
    "declared_count",
    [True, "1", -1, MAX_RECORDS_PER_SNAPSHOT + 1],
)
def test_declared_count_must_be_a_bounded_integer(declared_count: object) -> None:
    assert_parse_error(
        encoded(document(records=[], declared_record_count=declared_count)),
        FiniteParserErrorCode.VALUE_OUT_OF_BOUNDS,
    )


def test_raw_record_and_assertion_count_bounds_are_exact(
    parser: SyntheticJsonSourceParser,
) -> None:
    empty_record = record(assertions=[])
    maximum_records = [
        {**empty_record, "record_id": f"record-{index}"}
        for index in range(MAX_RECORDS_PER_SNAPSHOT)
    ]
    output = parser.parse(encoded(document(maximum_records)))
    assert len(output.records) == MAX_RECORDS_PER_SNAPSHOT

    assert_parse_error(
        encoded(document([*maximum_records, record("record-overflow")])),
        FiniteParserErrorCode.VALUE_OUT_OF_BOUNDS,
    )

    maximum_assertions = [
        assertion(f"field.{index}", index) for index in range(MAX_ASSERTIONS_PER_RECORD)
    ]
    output = parser.parse(encoded(document([record(assertions=maximum_assertions)])))
    assert len(output.records[0].assertions) == MAX_ASSERTIONS_PER_RECORD

    assert_parse_error(
        encoded(
            document(
                [
                    record(
                        assertions=[
                            *maximum_assertions,
                            assertion("field.overflow", 65),
                        ]
                    )
                ]
            )
        ),
        FiniteParserErrorCode.VALUE_OUT_OF_BOUNDS,
    )


def test_raw_byte_limit_allows_exact_boundary_and_rejects_one_more(
    parser: SyntheticJsonSourceParser,
) -> None:
    base = encoded(document(records=[]))
    exact = base + b" " * (MAX_RAW_OBJECT_BYTES - len(base))

    assert parser.parse(exact).records == ()
    assert_parse_error(exact + b" ", FiniteParserErrorCode.CONTENT_TOO_LARGE)


def test_native_string_and_integer_encoded_bounds_are_exact(
    parser: SyntheticJsonSourceParser,
) -> None:
    exact_string = "x" * (MAX_NATIVE_VALUE_BYTES - 2)
    output = parser.parse(
        encoded(document([record(assertions=[assertion(value=exact_string)])]))
    )
    assert output.records[0].assertions[0].native_value == exact_string

    too_large_native_json = "x" * (MAX_NATIVE_VALUE_BYTES - 1)
    assert_parse_error(
        encoded(
            document([record(assertions=[assertion(value=too_large_native_json)])])
        ),
        FiniteParserErrorCode.VALUE_OUT_OF_BOUNDS,
    )

    too_large_normalized = "x" * (MAX_NATIVE_VALUE_BYTES + 1)
    assert_parse_error(
        encoded(document([record(assertions=[assertion(value=too_large_normalized)])])),
        FiniteParserErrorCode.VALUE_OUT_OF_BOUNDS,
    )

    exact_integer_token = "7" * MAX_NATIVE_VALUE_BYTES
    integer_content = encoded(document()).replace(
        b'"Synthetic Entity"', exact_integer_token.encode()
    )
    output = parser.parse(integer_content)
    assert output.records[0].assertions[0].normalized_value == exact_integer_token

    oversized_integer_content = encoded(document()).replace(
        b'"Synthetic Entity"', (exact_integer_token + "7").encode()
    )
    assert_parse_error(
        oversized_integer_content, FiniteParserErrorCode.VALUE_OUT_OF_BOUNDS
    )


@pytest.mark.parametrize(
    "value",
    [
        "   \t\n  ",
        "\ud800",
    ],
)
def test_blank_and_non_utf8_encodable_strings_fail_with_safe_bound_code(
    value: str,
) -> None:
    content = json.dumps(document(), separators=(",", ":")).replace(
        '"Synthetic Entity"', json.dumps(value)
    )

    assert_parse_error(content.encode(), FiniteParserErrorCode.VALUE_OUT_OF_BOUNDS)


@pytest.mark.parametrize(
    "value",
    [
        document([record(record_id="")]),
        document([record(record_id="r" * 129)]),
        document([record(effective_from="")]),
        document([record(assertions=[assertion(field="bad field")])]),
    ],
)
def test_domain_text_and_field_bounds_are_reported_without_body(value: object) -> None:
    assert_parse_error(encoded(value), FiniteParserErrorCode.VALUE_OUT_OF_BOUNDS)


def test_parser_error_requires_a_typed_stable_code() -> None:
    with pytest.raises(ValueError, match="must be typed"):
        FiniteParserError("INVALID_JSON")  # type: ignore[arg-type]
