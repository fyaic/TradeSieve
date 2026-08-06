"""Strict finite parser for the one Phase 1 synthetic source format."""

from __future__ import annotations

import json
import unicodedata
from typing import Never

from tradesieve.domain.source_snapshot import (
    MAX_ASSERTIONS_PER_RECORD,
    MAX_NATIVE_VALUE_BYTES,
    MAX_RAW_OBJECT_BYTES,
    MAX_RECORDS_PER_SNAPSHOT,
    ParsedAssertionInput,
    ParsedRecordInput,
)
from tradesieve.ports.source_snapshot import (
    SYNTHETIC_CHARSET,
    SYNTHETIC_MEDIA_TYPE,
    SYNTHETIC_PARSER_VERSION,
    SYNTHETIC_SCHEMA_ID,
    FiniteParserError,
    FiniteParserErrorCode,
    FiniteParserId,
    FiniteParserOutput,
)

MAX_JSON_NESTING = 32

TOP_LEVEL_FIELDS = frozenset({"schema_id", "declared_record_count", "records"})
RECORD_FIELDS = frozenset({"record_id", "effective_from", "effective_to", "assertions"})
ASSERTION_FIELDS = frozenset({"field", "value"})


class _DuplicateKey(Exception):
    pass


class _FloatNotAllowed(Exception):
    pass


class _NonFiniteNumber(Exception):
    pass


class _ValueOutOfBounds(Exception):
    pass


def _fail(code: FiniteParserErrorCode) -> Never:
    raise FiniteParserError(code) from None


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey
        result[key] = value
    return result


def _reject_float(_token: str) -> float:
    raise _FloatNotAllowed


def _reject_non_finite(_token: str) -> object:
    raise _NonFiniteNumber


def _bounded_integer(token: str) -> int:
    if len(token.encode("ascii")) > MAX_NATIVE_VALUE_BYTES:
        raise _ValueOutOfBounds
    return int(token)


def _enforce_safe_nesting(text: str) -> None:
    depth = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
        elif character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > MAX_JSON_NESTING:
                _fail(FiniteParserErrorCode.UNSAFE_NESTING)
        elif character in "]}":
            depth -= 1


def _exact_object(value: object, expected_fields: frozenset[str]) -> dict[str, object]:
    if not isinstance(value, dict):
        _fail(FiniteParserErrorCode.INVALID_SHAPE)
    fields = frozenset(value)
    if fields - expected_fields:
        _fail(FiniteParserErrorCode.UNKNOWN_FIELD)
    if fields != expected_fields:
        _fail(FiniteParserErrorCode.INVALID_SHAPE)
    return value


def _bounded_list(value: object, maximum: int) -> list[object]:
    if not isinstance(value, list):
        _fail(FiniteParserErrorCode.INVALID_SHAPE)
    if len(value) > maximum:
        _fail(FiniteParserErrorCode.VALUE_OUT_OF_BOUNDS)
    return value


def _normalized_scalar(value: object) -> tuple[str | int | bool | None, str]:
    if value is None:
        return None, "null"
    if isinstance(value, bool):
        return value, "true" if value else "false"
    if isinstance(value, int):
        return value, str(value)
    if not isinstance(value, str):
        _fail(FiniteParserErrorCode.INVALID_SHAPE)
    normalized = " ".join(unicodedata.normalize("NFKC", value).split())
    if not normalized:
        _fail(FiniteParserErrorCode.VALUE_OUT_OF_BOUNDS)
    try:
        if (
            len(normalized.encode("utf-8")) > MAX_NATIVE_VALUE_BYTES
            or len(
                json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
            )
            > MAX_NATIVE_VALUE_BYTES
        ):
            _fail(FiniteParserErrorCode.VALUE_OUT_OF_BOUNDS)
    except UnicodeError:
        raise FiniteParserError(FiniteParserErrorCode.VALUE_OUT_OF_BOUNDS) from None
    return value, normalized


def _optional_date(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        _fail(FiniteParserErrorCode.INVALID_SHAPE)
    return value


class SyntheticJsonSourceParser:
    """No source-selected behavior: one parser ID, schema, and release version."""

    parser_id = FiniteParserId.SYNTHETIC_JSON_V1
    parser_version = SYNTHETIC_PARSER_VERSION
    media_type = SYNTHETIC_MEDIA_TYPE
    charset = SYNTHETIC_CHARSET

    def parse(self, content: bytes) -> FiniteParserOutput:
        if not isinstance(content, bytes):
            _fail(FiniteParserErrorCode.INVALID_INPUT)
        if len(content) > MAX_RAW_OBJECT_BYTES:
            _fail(FiniteParserErrorCode.CONTENT_TOO_LARGE)
        if content.startswith(b"\xef\xbb\xbf"):
            _fail(FiniteParserErrorCode.BOM_NOT_ALLOWED)
        try:
            text = content.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise FiniteParserError(FiniteParserErrorCode.INVALID_ENCODING) from None
        _enforce_safe_nesting(text)
        try:
            decoded: object = json.loads(
                text,
                object_pairs_hook=_unique_object,
                parse_float=_reject_float,
                parse_int=_bounded_integer,
                parse_constant=_reject_non_finite,
            )
        except _DuplicateKey:
            raise FiniteParserError(FiniteParserErrorCode.DUPLICATE_KEY) from None
        except _FloatNotAllowed:
            raise FiniteParserError(FiniteParserErrorCode.FLOAT_NOT_ALLOWED) from None
        except _NonFiniteNumber:
            raise FiniteParserError(FiniteParserErrorCode.NON_FINITE_NUMBER) from None
        except _ValueOutOfBounds:
            raise FiniteParserError(FiniteParserErrorCode.VALUE_OUT_OF_BOUNDS) from None
        except (json.JSONDecodeError, UnicodeError):
            raise FiniteParserError(FiniteParserErrorCode.INVALID_JSON) from None
        return self._materialize(decoded)

    @staticmethod
    def _materialize(decoded: object) -> FiniteParserOutput:
        document = _exact_object(decoded, TOP_LEVEL_FIELDS)
        if document["schema_id"] != SYNTHETIC_SCHEMA_ID:
            _fail(FiniteParserErrorCode.SCHEMA_MISMATCH)
        declared_count = document["declared_record_count"]
        if (
            isinstance(declared_count, bool)
            or not isinstance(declared_count, int)
            or not 0 <= declared_count <= MAX_RECORDS_PER_SNAPSHOT
        ):
            _fail(FiniteParserErrorCode.VALUE_OUT_OF_BOUNDS)
        raw_records = _bounded_list(document["records"], MAX_RECORDS_PER_SNAPSHOT)
        records = tuple(
            SyntheticJsonSourceParser._record(record, index)
            for index, record in enumerate(raw_records)
        )
        return FiniteParserOutput(SYNTHETIC_SCHEMA_ID, declared_count, records)

    @staticmethod
    def _record(value: object, record_index: int) -> ParsedRecordInput:
        record = _exact_object(value, RECORD_FIELDS)
        record_id = record["record_id"]
        if not isinstance(record_id, str):
            _fail(FiniteParserErrorCode.INVALID_SHAPE)
        raw_assertions = _bounded_list(record["assertions"], MAX_ASSERTIONS_PER_RECORD)
        assertions = tuple(
            SyntheticJsonSourceParser._assertion(assertion, record_index, index)
            for index, assertion in enumerate(raw_assertions)
        )
        try:
            return ParsedRecordInput(
                source_record_id=record_id,
                native_locator=f"/records/{record_index}",
                effective_from=_optional_date(record["effective_from"]),
                effective_to=_optional_date(record["effective_to"]),
                assertions=assertions,
            )
        except (TypeError, UnicodeError, ValueError):
            raise FiniteParserError(FiniteParserErrorCode.VALUE_OUT_OF_BOUNDS) from None

    @staticmethod
    def _assertion(
        value: object, record_index: int, assertion_index: int
    ) -> ParsedAssertionInput:
        assertion = _exact_object(value, ASSERTION_FIELDS)
        field = assertion["field"]
        if not isinstance(field, str):
            _fail(FiniteParserErrorCode.INVALID_SHAPE)
        native, normalized = _normalized_scalar(assertion["value"])
        try:
            return ParsedAssertionInput(
                field_name=field,
                native_locator=(
                    f"/records/{record_index}/assertions/{assertion_index}/value"
                ),
                native_value=native,
                normalized_value=normalized,
            )
        except (TypeError, UnicodeError, ValueError):
            raise FiniteParserError(FiniteParserErrorCode.VALUE_OUT_OF_BOUNDS) from None
