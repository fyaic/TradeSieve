"""Private strict decoder and immutable evidence for canonical screening intake.

This application boundary creates no interface or durable record. Exact submitted bytes
remain private to the artifact while all public failures are finite and redacted.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final, cast, get_args

from pydantic import BaseModel, ValidationError

from tradesieve.application.auth import (
    ActorContext,
    ActorType,
    RequestContext,
    Role,
    Scope,
)
from tradesieve.application.contracts import ScreeningRequest

JSON_MEDIA_TYPE: Final = "application/json"
MAX_BODY_BYTES: Final = 1_048_576
MAX_JSON_DEPTH: Final = 32
MAX_JSON_NODES: Final = 50_000
MAX_FIELD_ISSUES: Final = 64
_MAX_POINTER_LENGTH: Final = 1024
_MAX_POINTER_SEGMENTS: Final = 64
_SAFE_IDENTIFIER: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class ScreeningIntakeErrorCode(StrEnum):
    """Finite public screening-intake failure categories."""

    BODY_TYPE = "BODY_TYPE"
    BODY_EMPTY = "BODY_EMPTY"
    BODY_TOO_LARGE = "BODY_TOO_LARGE"
    MEDIA_TYPE = "MEDIA_TYPE"
    BOM_FORBIDDEN = "BOM_FORBIDDEN"
    UTF8_REQUIRED = "UTF8_REQUIRED"
    JSON_INVALID = "JSON_INVALID"
    JSON_TRAILING = "JSON_TRAILING"
    JSON_DUPLICATE_KEY = "JSON_DUPLICATE_KEY"
    JSON_FLOAT_FORBIDDEN = "JSON_FLOAT_FORBIDDEN"
    JSON_NON_FINITE = "JSON_NON_FINITE"
    JSON_ROOT = "JSON_ROOT"
    JSON_DEPTH = "JSON_DEPTH"
    JSON_NODES = "JSON_NODES"
    REQUEST_INVALID = "REQUEST_INVALID"
    CONTEXT_MISMATCH = "CONTEXT_MISMATCH"
    RECEIVE_METADATA_INVALID = "RECEIVE_METADATA_INVALID"
    INTEGRITY_FAILURE = "INTEGRITY_FAILURE"


class FieldIssueReason(StrEnum):
    """Finite redacted reasons attached to safe JSON pointers."""

    REQUIRED = "REQUIRED"
    EXTRA = "EXTRA"
    TYPE = "TYPE"
    FORMAT = "FORMAT"
    VALUE = "VALUE"
    BOUNDS = "BOUNDS"
    REFERENCE_GRAPH = "REFERENCE_GRAPH"
    CONTEXT_MISMATCH = "CONTEXT_MISMATCH"


@dataclass(frozen=True, slots=True, order=True)
class ScreeningIntakeFieldIssue:
    pointer: str
    reason: FieldIssueReason

    def __post_init__(self) -> None:
        if type(self.pointer) is not str or type(self.reason) is not FieldIssueReason:
            raise TypeError("invalid screening intake field issue")
        if not _is_safe_pointer(self.pointer):
            raise ValueError("invalid screening intake field issue")


class ScreeningIntakeError(Exception):
    """Only public failure emitted by the strict intake boundary."""

    def __init__(
        self,
        code: ScreeningIntakeErrorCode,
        issues: tuple[ScreeningIntakeFieldIssue, ...] = (),
    ) -> None:
        if (
            type(code) is not ScreeningIntakeErrorCode
            or type(issues) is not tuple
            or any(type(issue) is not ScreeningIntakeFieldIssue for issue in issues)
        ):
            raise TypeError("invalid screening intake error")
        if len(issues) > MAX_FIELD_ISSUES or issues != tuple(sorted(set(issues))):
            raise ValueError("invalid screening intake error")
        self.code = code
        self.issues = issues
        super().__init__(f"screening intake rejected: {code.value}")


def _safe_call[T](operation: Callable[[], T]) -> T:
    failure: ScreeningIntakeError | None = None
    result: T | None = None
    try:
        result = operation()
    except ScreeningIntakeError as error:
        failure = error
    except Exception:
        failure = ScreeningIntakeError(ScreeningIntakeErrorCode.INTEGRITY_FAILURE)
    if failure is not None:
        raise failure
    return cast(T, result)


class _DuplicateJsonKey(Exception):
    pass


class _FloatToken(Exception):
    pass


class _NonFiniteToken(Exception):
    pass


def _object_from_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _reject_float(_token: str) -> Any:
    raise _FloatToken


def _reject_non_finite(_token: str) -> Any:
    raise _NonFiniteToken


def _scan_structure(text: str) -> None:
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
            continue
        if character == '"':
            in_string = True
        elif character in "{[":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise ScreeningIntakeError(ScreeningIntakeErrorCode.JSON_DEPTH)
        elif character in "}]":
            depth -= 1


def _bounded_document(document: object) -> None:
    stack: list[tuple[object, int]] = [(document, 1)]
    nodes = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES:
            raise ScreeningIntakeError(ScreeningIntakeErrorCode.JSON_NODES)
        if depth > MAX_JSON_DEPTH:
            raise ScreeningIntakeError(ScreeningIntakeErrorCode.JSON_DEPTH)
        if isinstance(value, dict):
            stack.extend((nested, depth + 1) for nested in value.values())
        elif isinstance(value, list):
            stack.extend((nested, depth + 1) for nested in value)


def _decode_document(raw_body: object, media_type: object) -> dict[str, object]:
    if type(raw_body) is not bytes:
        raise ScreeningIntakeError(ScreeningIntakeErrorCode.BODY_TYPE)
    if type(media_type) is not str or media_type != JSON_MEDIA_TYPE:
        raise ScreeningIntakeError(ScreeningIntakeErrorCode.MEDIA_TYPE)
    if not raw_body:
        raise ScreeningIntakeError(ScreeningIntakeErrorCode.BODY_EMPTY)
    if len(raw_body) > MAX_BODY_BYTES:
        raise ScreeningIntakeError(ScreeningIntakeErrorCode.BODY_TOO_LARGE)
    if raw_body.startswith(b"\xef\xbb\xbf"):
        raise ScreeningIntakeError(ScreeningIntakeErrorCode.BOM_FORBIDDEN)

    encoding_failed = False
    try:
        text = raw_body.decode("utf-8")
    except UnicodeDecodeError:
        encoding_failed = True
        text = ""
    if encoding_failed:
        raise ScreeningIntakeError(ScreeningIntakeErrorCode.UTF8_REQUIRED)

    _scan_structure(text)
    decoder = json.JSONDecoder(
        object_pairs_hook=_object_from_pairs,
        parse_float=_reject_float,
        parse_constant=_reject_non_finite,
    )
    start = len(text) - len(text.lstrip())
    failure: ScreeningIntakeErrorCode | None = None
    try:
        document, end = decoder.raw_decode(text, start)
    except _DuplicateJsonKey:
        failure = ScreeningIntakeErrorCode.JSON_DUPLICATE_KEY
        document, end = None, len(text)
    except _FloatToken:
        failure = ScreeningIntakeErrorCode.JSON_FLOAT_FORBIDDEN
        document, end = None, len(text)
    except _NonFiniteToken:
        failure = ScreeningIntakeErrorCode.JSON_NON_FINITE
        document, end = None, len(text)
    except (json.JSONDecodeError, RecursionError, ValueError):
        failure = ScreeningIntakeErrorCode.JSON_INVALID
        document, end = None, len(text)
    if failure is not None:
        raise ScreeningIntakeError(failure)
    if text[end:].strip():
        raise ScreeningIntakeError(ScreeningIntakeErrorCode.JSON_TRAILING)
    if not isinstance(document, dict):
        raise ScreeningIntakeError(ScreeningIntakeErrorCode.JSON_ROOT)
    _bounded_document(document)
    return document


def _model_pointer_segments(model: type[BaseModel]) -> frozenset[str]:
    pending = [model]
    visited: set[type[BaseModel]] = set()
    segments: set[str] = set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        for name, model_field in current.model_fields.items():
            segments.add(name)
            for candidate in (
                model_field.annotation,
                *get_args(model_field.annotation),
            ):
                if (
                    isinstance(candidate, type)
                    and issubclass(candidate, BaseModel)
                    and candidate not in visited
                ):
                    pending.append(candidate)
                for nested in get_args(candidate):
                    if (
                        isinstance(nested, type)
                        and issubclass(nested, BaseModel)
                        and nested not in visited
                    ):
                        pending.append(nested)
    return frozenset(segments)


_SAFE_POINTER_SEGMENTS = _model_pointer_segments(ScreeningRequest)


def _is_safe_pointer(pointer: str) -> bool:
    if pointer == "":
        return True
    if len(pointer) > _MAX_POINTER_LENGTH or not pointer.startswith("/"):
        return False
    segments = pointer.removeprefix("/").split("/")
    return len(segments) <= _MAX_POINTER_SEGMENTS and all(
        segment.isascii() and (segment.isdigit() or segment in _SAFE_POINTER_SEGMENTS)
        for segment in segments
    )


def _safe_pointer(location: tuple[int | str, ...]) -> str:
    safe: list[str] = []
    for segment in location:
        if isinstance(segment, int):
            safe.append(str(segment))
        elif segment in _SAFE_POINTER_SEGMENTS:
            safe.append(segment.replace("~", "~0").replace("/", "~1"))
    return "" if not safe else "/" + "/".join(safe)


def _issue_reason(error_type: str, pointer: str) -> FieldIssueReason:
    if error_type == "missing":
        return FieldIssueReason.REQUIRED
    if error_type == "extra_forbidden":
        return FieldIssueReason.EXTRA
    if any(
        marker in error_type
        for marker in (
            "too_long",
            "too_short",
            "greater_than",
            "less_than",
            "max_digits",
            "max_places",
            "whole_digits",
        )
    ):
        return FieldIssueReason.BOUNDS
    if any(
        marker in error_type
        for marker in ("pattern", "parsing", "timezone", "date", "decimal")
    ):
        return FieldIssueReason.FORMAT
    if error_type == "value_error":
        return (
            FieldIssueReason.REFERENCE_GRAPH
            if pointer == ""
            else FieldIssueReason.VALUE
        )
    if error_type.endswith("_type") or error_type in {"model_attributes_type"}:
        return FieldIssueReason.TYPE
    return FieldIssueReason.VALUE


def _validation_issues(error: ValidationError) -> tuple[ScreeningIntakeFieldIssue, ...]:
    issues = {
        ScreeningIntakeFieldIssue(
            _safe_pointer(item["loc"]),
            _issue_reason(item["type"], _safe_pointer(item["loc"])),
        )
        for item in error.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )
    }
    return tuple(sorted(issues))[:MAX_FIELD_ISSUES]


def _validate_request(document: dict[str, object]) -> ScreeningRequest:
    issues: tuple[ScreeningIntakeFieldIssue, ...] | None = None
    try:
        request = ScreeningRequest.model_validate(document)
    except ValidationError as error:
        issues = _validation_issues(error)
        request = None
    if issues is not None:
        raise ScreeningIntakeError(ScreeningIntakeErrorCode.REQUEST_INVALID, issues)
    assert request is not None
    return request


def _canonical_request_bytes(request: ScreeningRequest) -> bytes:
    return json.dumps(
        request.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _content_hash(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _valid_identifier(value: object) -> bool:
    return type(value) is str and _SAFE_IDENTIFIER.fullmatch(value) is not None


def _valid_opaque_text(value: object) -> bool:
    return (
        type(value) is str
        and 0 < len(value) <= 256
        and value.isprintable()
        and not any(character.isspace() for character in value)
    )


def _canonical_datetime(value: object, *, require_utc: bool) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() is None
        or (require_utc and value.tzinfo is not UTC)
    ):
        raise ScreeningIntakeError(ScreeningIntakeErrorCode.RECEIVE_METADATA_INVALID)
    return value.astimezone(UTC)


def _copy_context(value: object, *, require_utc: bool) -> RequestContext:
    if type(value) is not RequestContext or type(value.actor) is not ActorContext:
        raise ScreeningIntakeError(ScreeningIntakeErrorCode.RECEIVE_METADATA_INVALID)
    actor = value.actor
    if (
        not _valid_identifier(value.tenant_id)
        or not _valid_identifier(value.correlation_id)
        or not _valid_identifier(actor.subject)
        or not _valid_identifier(actor.client_id)
        or not _valid_identifier(actor.tenant_id)
        or actor.tenant_id != value.tenant_id
        or type(actor.actor_type) is not ActorType
        or type(actor.scopes) is not frozenset
        or any(type(scope) is not Scope for scope in actor.scopes)
        or type(actor.roles) is not frozenset
        or any(type(role) is not Role for role in actor.roles)
        or not _valid_opaque_text(actor.issuer)
        or not _valid_opaque_text(actor.audience)
        or type(actor.demo_identity) is not bool
    ):
        raise ScreeningIntakeError(ScreeningIntakeErrorCode.RECEIVE_METADATA_INVALID)
    issued_at = _canonical_datetime(actor.issued_at, require_utc=require_utc)
    expires_at = _canonical_datetime(actor.expires_at, require_utc=require_utc)
    if expires_at <= issued_at:
        raise ScreeningIntakeError(ScreeningIntakeErrorCode.RECEIVE_METADATA_INVALID)
    copied_actor = ActorContext(
        subject=actor.subject,
        client_id=actor.client_id,
        tenant_id=actor.tenant_id,
        actor_type=actor.actor_type,
        scopes=frozenset(actor.scopes),
        roles=frozenset(actor.roles),
        issuer=actor.issuer,
        audience=actor.audience,
        issued_at=issued_at,
        expires_at=expires_at,
        demo_identity=actor.demo_identity,
    )
    return RequestContext(
        actor=copied_actor,
        tenant_id=value.tenant_id,
        correlation_id=value.correlation_id,
    )


def _receive_metadata_hash(received_at: datetime, context: RequestContext) -> str:
    actor = context.actor
    payload = {
        "received_at": received_at.isoformat(),
        "tenant_id": context.tenant_id,
        "correlation_id": context.correlation_id,
        "actor": {
            "subject": actor.subject,
            "client_id": actor.client_id,
            "tenant_id": actor.tenant_id,
            "actor_type": actor.actor_type.value,
            "scopes": sorted(scope.value for scope in actor.scopes),
            "roles": sorted(role.value for role in actor.roles),
            "issuer": actor.issuer,
            "audience": actor.audience,
            "issued_at": actor.issued_at.isoformat(),
            "expires_at": actor.expires_at.isoformat(),
            "demo_identity": actor.demo_identity,
        },
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    return _content_hash(encoded)


def _context_issues(
    request: ScreeningRequest, context: RequestContext
) -> tuple[ScreeningIntakeFieldIssue, ...]:
    issues: list[ScreeningIntakeFieldIssue] = []
    if request.tenant_id != context.tenant_id:
        issues.append(
            ScreeningIntakeFieldIssue("/tenant_id", FieldIssueReason.CONTEXT_MISMATCH)
        )
    if request.correlation_id != context.correlation_id:
        issues.append(
            ScreeningIntakeFieldIssue(
                "/correlation_id", FieldIssueReason.CONTEXT_MISMATCH
            )
        )
    return tuple(sorted(issues))


@dataclass(frozen=True, slots=True)
class CanonicalScreeningIntake:
    """Immutable private intake evidence, verified from exact bytes on every read."""

    _raw_bytes: bytes = field(repr=False)
    media_type: str
    byte_length: int
    byte_hash: str
    _canonical_request_json: bytes = field(repr=False)
    canonical_hash: str
    schema_version: str
    received_at: datetime
    _context: RequestContext = field(repr=False)
    _receive_metadata_hash: str = field(repr=False)

    def __post_init__(self) -> None:
        def initialize() -> None:
            received_at = _canonical_datetime(self.received_at, require_utc=False)
            context = _copy_context(self._context, require_utc=False)
            object.__setattr__(self, "received_at", received_at)
            object.__setattr__(self, "_context", context)
            self._verified_request_unwrapped()

        _safe_call(initialize)

    @classmethod
    def decode(
        cls,
        raw_body: object,
        media_type: object,
        *,
        context: RequestContext,
        received_at: datetime,
    ) -> CanonicalScreeningIntake:
        """Strictly decode and bind exact bytes to authenticated receive metadata."""

        def build() -> CanonicalScreeningIntake:
            document = _decode_document(raw_body, media_type)
            request = _validate_request(document)
            canonical_json = _canonical_request_bytes(request)
            normalized_received_at = _canonical_datetime(received_at, require_utc=False)
            copied_context = _copy_context(context, require_utc=False)
            exact_raw_body = cast(bytes, raw_body)
            exact_media_type = cast(str, media_type)
            return cls(
                _raw_bytes=exact_raw_body,
                media_type=exact_media_type,
                byte_length=len(exact_raw_body),
                byte_hash=_content_hash(exact_raw_body),
                _canonical_request_json=canonical_json,
                canonical_hash=request.canonical_input_hash(),
                schema_version=request.schema_version,
                received_at=normalized_received_at,
                _context=copied_context,
                _receive_metadata_hash=_receive_metadata_hash(
                    normalized_received_at, copied_context
                ),
            )

        return _safe_call(build)

    def _verified_context_unwrapped(self) -> RequestContext:
        received_at = _canonical_datetime(self.received_at, require_utc=True)
        context = _copy_context(self._context, require_utc=True)
        if type(self._receive_metadata_hash) is not str:
            raise ScreeningIntakeError(ScreeningIntakeErrorCode.INTEGRITY_FAILURE)
        if self._receive_metadata_hash != _receive_metadata_hash(received_at, context):
            raise ScreeningIntakeError(ScreeningIntakeErrorCode.INTEGRITY_FAILURE)
        return context

    def _verified_request_unwrapped(self) -> ScreeningRequest:
        if (
            type(self._raw_bytes) is not bytes
            or type(self.media_type) is not str
            or type(self.byte_length) is not int
            or type(self.byte_hash) is not str
            or type(self._canonical_request_json) is not bytes
            or type(self.canonical_hash) is not str
            or type(self.schema_version) is not str
        ):
            raise ScreeningIntakeError(ScreeningIntakeErrorCode.INTEGRITY_FAILURE)
        context = self._verified_context_unwrapped()
        document = _decode_document(self._raw_bytes, self.media_type)
        request = _validate_request(document)
        context_issues = _context_issues(request, context)
        if context_issues:
            raise ScreeningIntakeError(
                ScreeningIntakeErrorCode.CONTEXT_MISMATCH, context_issues
            )
        expected_canonical_json = _canonical_request_bytes(request)
        if (
            self.byte_length != len(self._raw_bytes)
            or self.byte_hash != _content_hash(self._raw_bytes)
            or self._canonical_request_json != expected_canonical_json
            or self.canonical_hash != request.canonical_input_hash()
            or self.schema_version != request.schema_version
        ):
            raise ScreeningIntakeError(ScreeningIntakeErrorCode.INTEGRITY_FAILURE)
        return request

    @property
    def context(self) -> RequestContext:
        """Return a fresh verified receive-context snapshot without exposing storage."""

        def read_context() -> RequestContext:
            self._verified_request_unwrapped()
            return self._verified_context_unwrapped()

        return _safe_call(read_context)

    @property
    def request(self) -> ScreeningRequest:
        """Return a fresh canonical model only after complete identity verification."""

        return _safe_call(self._verified_request_unwrapped)

    def reverify(self) -> CanonicalScreeningIntake:
        """Reconstruct the artifact through the same strict verified constructor."""

        def rebuild() -> CanonicalScreeningIntake:
            self._verified_request_unwrapped()
            return type(self)(
                _raw_bytes=self._raw_bytes,
                media_type=self.media_type,
                byte_length=self.byte_length,
                byte_hash=self.byte_hash,
                _canonical_request_json=self._canonical_request_json,
                canonical_hash=self.canonical_hash,
                schema_version=self.schema_version,
                received_at=self.received_at,
                _context=self._context,
                _receive_metadata_hash=self._receive_metadata_hash,
            )

        return _safe_call(rebuild)


__all__ = [
    "CanonicalScreeningIntake",
    "FieldIssueReason",
    "JSON_MEDIA_TYPE",
    "MAX_BODY_BYTES",
    "MAX_FIELD_ISSUES",
    "MAX_JSON_DEPTH",
    "MAX_JSON_NODES",
    "ScreeningIntakeError",
    "ScreeningIntakeErrorCode",
    "ScreeningIntakeFieldIssue",
]
