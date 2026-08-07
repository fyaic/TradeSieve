"""Strict canonical codec for private screening submission request contexts."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Final, Never, cast

from tradesieve.application.auth import (
    ActorContext,
    ActorType,
    RequestContext,
    Role,
    Scope,
)

REQUEST_CONTEXT_SCHEMA_VERSION: Final = "1.0.0"
MAX_REQUEST_CONTEXT_BYTES: Final = 8_192
_SAFE_IDENTIFIER: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DOCUMENT_KEYS: Final = frozenset(
    {"schema_version", "tenant_id", "correlation_id", "actor"}
)
_ACTOR_KEYS: Final = frozenset(
    {
        "subject",
        "client_id",
        "tenant_id",
        "actor_type",
        "scopes",
        "roles",
        "issuer",
        "audience",
        "issued_at",
        "expires_at",
        "demo_identity",
    }
)


class ScreeningSubmissionContextCodecError(Exception):
    """Fixed private-codec failure without submitted identity or payload detail."""

    def __init__(self) -> None:
        super().__init__("screening submission context codec failed")


def _invalid() -> ScreeningSubmissionContextCodecError:
    return ScreeningSubmissionContextCodecError()


def _codec_call[T](operation: Callable[[], T]) -> T:
    failure: ScreeningSubmissionContextCodecError | None = None
    result: T | None = None
    try:
        result = operation()
    except Exception:
        failure = _invalid()
    if failure is not None:
        raise failure
    return cast(T, result)


def _identifier(value: object) -> str:
    if type(value) is not str or _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise _invalid()
    return value


def _opaque_text(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > 256
        or not value.isprintable()
        or any(character.isspace() for character in value)
    ):
        raise _invalid()
    return value


def _datetime_text(value: object) -> str:
    if type(value) is not datetime or value.tzinfo is not UTC:
        raise _invalid()
    return value.isoformat().replace("+00:00", "Z")


def _datetime_value(value: object) -> datetime:
    if type(value) is not str or not value.endswith("Z"):
        raise _invalid()
    parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    if parsed.tzinfo is not UTC or _datetime_text(parsed) != value:
        raise _invalid()
    return parsed


def _enum_values[E: (Scope, Role)](
    value: object,
    enum_type: type[E],
) -> frozenset[E]:
    if type(value) is not list or len(value) > len(enum_type):
        raise _invalid()
    if any(type(item) is not str for item in value):
        raise _invalid()
    values = cast(list[str], value)
    if values != sorted(set(values)):
        raise _invalid()
    return frozenset(enum_type(item) for item in values)


def _verified_context(value: object) -> RequestContext:
    if type(value) is not RequestContext or type(value.actor) is not ActorContext:
        raise _invalid()
    actor = value.actor
    tenant_id = _identifier(value.tenant_id)
    correlation_id = _identifier(value.correlation_id)
    subject = _identifier(actor.subject)
    client_id = _identifier(actor.client_id)
    actor_tenant_id = _identifier(actor.tenant_id)
    if actor_tenant_id != tenant_id or type(actor.actor_type) is not ActorType:
        raise _invalid()
    if type(actor.scopes) is not frozenset or any(
        type(scope) is not Scope for scope in actor.scopes
    ):
        raise _invalid()
    if type(actor.roles) is not frozenset or any(
        type(role) is not Role for role in actor.roles
    ):
        raise _invalid()
    issuer = _opaque_text(actor.issuer)
    audience = _opaque_text(actor.audience)
    issued_at_text = _datetime_text(actor.issued_at)
    expires_at_text = _datetime_text(actor.expires_at)
    if actor.expires_at <= actor.issued_at or type(actor.demo_identity) is not bool:
        raise _invalid()
    return RequestContext(
        actor=ActorContext(
            subject=subject,
            client_id=client_id,
            tenant_id=actor_tenant_id,
            actor_type=actor.actor_type,
            scopes=frozenset(actor.scopes),
            roles=frozenset(actor.roles),
            issuer=issuer,
            audience=audience,
            issued_at=_datetime_value(issued_at_text),
            expires_at=_datetime_value(expires_at_text),
            demo_identity=actor.demo_identity,
        ),
        tenant_id=tenant_id,
        correlation_id=correlation_id,
    )


def _document(context: RequestContext) -> dict[str, object]:
    verified = _verified_context(context)
    actor = verified.actor
    return {
        "schema_version": REQUEST_CONTEXT_SCHEMA_VERSION,
        "tenant_id": verified.tenant_id,
        "correlation_id": verified.correlation_id,
        "actor": {
            "subject": actor.subject,
            "client_id": actor.client_id,
            "tenant_id": actor.tenant_id,
            "actor_type": actor.actor_type.value,
            "scopes": sorted(scope.value for scope in actor.scopes),
            "roles": sorted(role.value for role in actor.roles),
            "issuer": actor.issuer,
            "audience": actor.audience,
            "issued_at": _datetime_text(actor.issued_at),
            "expires_at": _datetime_text(actor.expires_at),
            "demo_identity": actor.demo_identity,
        },
    }


def _canonical_bytes(document: object) -> bytes:
    encoded = json.dumps(
        document,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if not 1 <= len(encoded) <= MAX_REQUEST_CONTEXT_BYTES:
        raise _invalid()
    return encoded


def encode_request_context(context: RequestContext) -> bytes:
    """Encode a complete exact request context into canonical private bytes."""

    return _codec_call(lambda: _canonical_bytes(_document(context)))


def _reject_number(_value: str) -> Never:
    raise _invalid()


def _decode_unwrapped(payload: object) -> RequestContext:
    if (
        type(payload) is not bytes
        or not 1 <= len(payload) <= MAX_REQUEST_CONTEXT_BYTES
        or payload.startswith(b"\xef\xbb\xbf")
    ):
        raise _invalid()
    text = payload.decode("utf-8")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise _invalid()
            result[key] = value
        return result

    document = json.loads(
        text,
        object_pairs_hook=reject_duplicates,
        parse_float=_reject_number,
        parse_int=_reject_number,
        parse_constant=_reject_number,
    )
    if type(document) is not dict or set(document) != _DOCUMENT_KEYS:
        raise _invalid()
    if document["schema_version"] != REQUEST_CONTEXT_SCHEMA_VERSION:
        raise _invalid()
    actor = document["actor"]
    if type(actor) is not dict or set(actor) != _ACTOR_KEYS:
        raise _invalid()
    if type(actor["actor_type"]) is not str:
        raise _invalid()
    scopes = _enum_values(actor["scopes"], Scope)
    roles = _enum_values(actor["roles"], Role)
    if type(actor["demo_identity"]) is not bool:
        raise _invalid()
    rebuilt = RequestContext(
        actor=ActorContext(
            subject=_identifier(actor["subject"]),
            client_id=_identifier(actor["client_id"]),
            tenant_id=_identifier(actor["tenant_id"]),
            actor_type=ActorType(actor["actor_type"]),
            scopes=scopes,
            roles=roles,
            issuer=_opaque_text(actor["issuer"]),
            audience=_opaque_text(actor["audience"]),
            issued_at=_datetime_value(actor["issued_at"]),
            expires_at=_datetime_value(actor["expires_at"]),
            demo_identity=actor["demo_identity"],
        ),
        tenant_id=_identifier(document["tenant_id"]),
        correlation_id=_identifier(document["correlation_id"]),
    )
    verified = _verified_context(rebuilt)
    if _canonical_bytes(_document(verified)) != payload:
        raise _invalid()
    return verified


def decode_request_context(payload: object) -> RequestContext:
    """Decode only the exact canonical private request-context representation."""

    return _codec_call(lambda: _decode_unwrapped(payload))


__all__ = [
    "MAX_REQUEST_CONTEXT_BYTES",
    "REQUEST_CONTEXT_SCHEMA_VERSION",
    "ScreeningSubmissionContextCodecError",
    "decode_request_context",
    "encode_request_context",
]
