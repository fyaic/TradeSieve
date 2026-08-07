"""Strict private persistence codec evidence for screening request contexts."""

from __future__ import annotations

import copy
import json
import traceback
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from typing import cast

import pytest

import tradesieve.adapters.screening_submission_codec as codec_module
from tradesieve.adapters.screening_submission_codec import (
    MAX_REQUEST_CONTEXT_BYTES,
    REQUEST_CONTEXT_SCHEMA_VERSION,
    ScreeningSubmissionContextCodecError,
    decode_request_context,
    encode_request_context,
)
from tradesieve.application.auth import (
    ActorContext,
    ActorType,
    RequestContext,
    Role,
    Scope,
)

ISSUED_AT = datetime(2026, 8, 7, 8, 0, 0, 123456, tzinfo=UTC)
EXPIRES_AT = ISSUED_AT + timedelta(minutes=5)
PRIVATE_SENTINEL = "PRIVATE-CONTEXT-IDENTITY-SENTINEL"


class StringSubclass(str):
    pass


class BytesSubclass(bytes):
    pass


class DatetimeSubclass(datetime):
    pass


class ActorContextSubclass(ActorContext):
    pass


class RequestContextSubclass(RequestContext):
    pass


def context() -> RequestContext:
    return RequestContext(
        actor=ActorContext(
            subject="actor-1",
            client_id="client-1",
            tenant_id="tenant-1",
            actor_type=ActorType.SERVICE,
            scopes=frozenset(
                {
                    Scope.SCREENING_READ,
                    Scope.SCREENING_SUBMIT,
                    Scope.AUDIT_EXPORT,
                }
            ),
            roles=frozenset({Role.AUDITOR, Role.COMPLIANCE_OWNER}),
            issuer="https://identity.example.test/",
            audience="urn:tradesieve:测试",
            issued_at=ISSUED_AT,
            expires_at=EXPIRES_AT,
            demo_identity=False,
        ),
        tenant_id="tenant-1",
        correlation_id="correlation-1",
    )


def canonical_document() -> dict[str, object]:
    return {
        "schema_version": REQUEST_CONTEXT_SCHEMA_VERSION,
        "tenant_id": "tenant-1",
        "correlation_id": "correlation-1",
        "actor": {
            "subject": "actor-1",
            "client_id": "client-1",
            "tenant_id": "tenant-1",
            "actor_type": "SERVICE",
            "scopes": ["audit:export", "screening:read", "screening:submit"],
            "roles": ["auditor", "compliance_owner"],
            "issuer": "https://identity.example.test/",
            "audience": "urn:tradesieve:测试",
            "issued_at": "2026-08-07T08:00:00.123456Z",
            "expires_at": "2026-08-07T08:05:00.123456Z",
            "demo_identity": False,
        },
    }


def canonical_bytes(document: object) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def assert_safe_failure(operation: Callable[[], object]) -> None:
    with pytest.raises(ScreeningSubmissionContextCodecError) as exc_info:
        operation()
    error = exc_info.value
    assert str(error) == "screening submission context codec failed"
    assert repr(error) == (
        "ScreeningSubmissionContextCodecError("
        "'screening submission context codec failed')"
    )
    assert error.__cause__ is None
    assert error.__context__ is None
    rendered = "".join(traceback.format_exception(error))
    assert PRIVATE_SENTINEL not in str(error)
    assert PRIVATE_SENTINEL not in repr(error)
    assert PRIVATE_SENTINEL not in rendered


def test_codec_emits_one_exact_canonical_utf8_document_and_round_trips() -> None:
    original = context()
    encoded = encode_request_context(original)
    assert type(encoded) is bytes
    assert encoded == canonical_bytes(canonical_document())
    assert b" " not in encoded
    assert "测试".encode() in encoded
    assert len(encoded) < MAX_REQUEST_CONTEXT_BYTES

    decoded = decode_request_context(encoded)
    assert decoded == original
    assert decoded is not original
    assert decoded.actor is not original.actor
    assert type(decoded.actor.scopes) is frozenset
    assert type(decoded.actor.roles) is frozenset
    assert decoded.actor.issued_at.tzinfo is UTC
    assert decoded.actor.expires_at.tzinfo is UTC
    assert encode_request_context(decoded) == encoded


def test_decoded_context_is_defensive_and_reverified_from_bytes() -> None:
    payload = encode_request_context(context())
    first = decode_request_context(payload)
    stable = copy.deepcopy(first)
    object.__setattr__(first.actor, "subject", "mutated-actor")
    second = decode_request_context(payload)
    assert second == stable
    assert second.actor.subject == "actor-1"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("subject", StringSubclass("actor-1")),
        ("subject", "bad actor"),
        ("client_id", "x" * 129),
        ("tenant_id", "other-tenant"),
        ("actor_type", "SERVICE"),
        ("scopes", {Scope.SCREENING_SUBMIT}),
        ("scopes", frozenset({"screening:submit"})),
        ("roles", {Role.AUDITOR}),
        ("roles", frozenset({"auditor"})),
        ("issuer", ""),
        ("issuer", "https://identity.example.test/private value"),
        ("issuer", "x" * 257),
        ("audience", "private\nvalue"),
        ("issued_at", ISSUED_AT.replace(tzinfo=None)),
        ("issued_at", ISSUED_AT.astimezone(timezone(timedelta(hours=8)))),
        (
            "issued_at",
            DatetimeSubclass(2026, 8, 7, 8, 0, 0, 123456, tzinfo=UTC),
        ),
        ("expires_at", ISSUED_AT),
        ("demo_identity", 0),
    ],
)
def test_encode_rejects_every_inexact_actor_field(field: str, value: object) -> None:
    original = context()
    altered_actor = replace(
        original.actor,
        **{field: value},  # type: ignore[arg-type]
    )
    altered = replace(original, actor=altered_actor)
    assert_safe_failure(lambda: encode_request_context(altered))


@pytest.mark.parametrize(
    "value",
    [
        object(),
        RequestContextSubclass(
            actor=context().actor,
            tenant_id="tenant-1",
            correlation_id="correlation-1",
        ),
        RequestContext(
            actor=ActorContextSubclass(
                subject=context().actor.subject,
                client_id=context().actor.client_id,
                tenant_id=context().actor.tenant_id,
                actor_type=context().actor.actor_type,
                scopes=context().actor.scopes,
                roles=context().actor.roles,
                issuer=context().actor.issuer,
                audience=context().actor.audience,
                issued_at=context().actor.issued_at,
                expires_at=context().actor.expires_at,
                demo_identity=context().actor.demo_identity,
            ),
            tenant_id="tenant-1",
            correlation_id="correlation-1",
        ),
        replace(context(), tenant_id=StringSubclass("tenant-1")),
        replace(context(), correlation_id=StringSubclass("correlation-1")),
    ],
)
def test_encode_requires_exact_context_and_identifiers(value: object) -> None:
    assert_safe_failure(lambda: encode_request_context(cast(RequestContext, value)))


def test_encode_and_decode_firewall_private_identity_and_nested_exceptions() -> None:
    private = replace(
        context(),
        actor=replace(context().actor, issuer=f"{PRIVATE_SENTINEL} value"),
    )
    assert_safe_failure(lambda: encode_request_context(private))
    assert_safe_failure(
        lambda: decode_request_context(
            b'{"actor":{"subject":"' + PRIVATE_SENTINEL.encode() + b'"}}'
        )
    )


def test_encode_rejects_a_document_larger_than_the_configured_private_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(codec_module, "MAX_REQUEST_CONTEXT_BYTES", 1)
    assert_safe_failure(lambda: encode_request_context(context()))


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "{}",
        bytearray(b"{}"),
        BytesSubclass(b"{}"),
        b"",
        b" " * MAX_REQUEST_CONTEXT_BYTES,
        b"x" * (MAX_REQUEST_CONTEXT_BYTES + 1),
        b"\xef\xbb\xbf{}",
        b"\xff",
        b"{} trailing",
        b"[]",
        b'{"duplicate":"first","duplicate":"second"}',
        b'{"actor":{"duplicate":"first","duplicate":"second"}}',
        b'{"number":1}',
        b'{"number":1.0}',
        b'{"number":NaN}',
        b'{"number":Infinity}',
    ],
)
def test_decode_rejects_representation_attacks_and_exact_size_edges(
    payload: object,
) -> None:
    assert_safe_failure(lambda: decode_request_context(payload))


def semantic_payload(
    mutation: Callable[[dict[str, object], dict[str, object]], None],
) -> bytes:
    document = canonical_document()
    actor = cast(dict[str, object], document["actor"])
    mutation(document, actor)
    return canonical_bytes(document)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda document, _actor: document.pop("tenant_id"),
        lambda document, _actor: document.update(extra="value"),
        lambda document, _actor: document.update(schema_version="2.0.0"),
        lambda document, _actor: document.update(tenant_id=1),
        lambda document, _actor: document.update(tenant_id="other-tenant"),
        lambda document, _actor: document.update(correlation_id=True),
        lambda document, _actor: document.update(actor=[]),
        lambda _document, actor: actor.pop("subject"),
        lambda _document, actor: actor.update(extra="value"),
        lambda _document, actor: actor.update(subject=True),
        lambda _document, actor: actor.update(client_id="bad client"),
        lambda _document, actor: actor.update(tenant_id="other-tenant"),
        lambda _document, actor: actor.update(actor_type="OTHER"),
        lambda _document, actor: actor.update(actor_type=1),
        lambda _document, actor: actor.update(actor_type=None),
        lambda _document, actor: actor.update(scopes="screening:submit"),
        lambda _document, actor: actor.update(scopes=["other:scope"]),
        lambda _document, actor: actor.update(
            scopes=["screening:read"] * (len(Scope) + 1)
        ),
        lambda _document, actor: actor.update(
            scopes=["screening:read", "screening:read"]
        ),
        lambda _document, actor: actor.update(
            scopes=["screening:submit", "audit:export"]
        ),
        lambda _document, actor: actor.update(scopes=[1]),
        lambda _document, actor: actor.update(scopes=[None]),
        lambda _document, actor: actor.update(roles="auditor"),
        lambda _document, actor: actor.update(roles=["other_role"]),
        lambda _document, actor: actor.update(roles=["auditor", "auditor"]),
        lambda _document, actor: actor.update(issuer=""),
        lambda _document, actor: actor.update(audience="bad value"),
        lambda _document, actor: actor.update(issued_at="2026-08-07T08:00:00+00:00"),
        lambda _document, actor: actor.update(issued_at="2026-08-07T08:00:00.1234560Z"),
        lambda _document, actor: actor.update(issued_at=True),
        lambda _document, actor: actor.update(expires_at="not-a-timeZ"),
        lambda _document, actor: actor.update(expires_at="2026-08-07T08:00:00.123456Z"),
        lambda _document, actor: actor.update(demo_identity=0),
        lambda _document, actor: actor.update(demo_identity="false"),
    ],
)
def test_decode_rejects_wrong_keys_types_enums_times_bounds_and_order(
    mutation: Callable[[dict[str, object], dict[str, object]], None],
) -> None:
    assert_safe_failure(lambda: decode_request_context(semantic_payload(mutation)))


@pytest.mark.parametrize(
    "payload",
    [
        json.dumps(canonical_document(), ensure_ascii=False, sort_keys=True).encode(),
        json.dumps(
            canonical_document(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode(),
        (
            b'{"schema_version":"1.0.0","tenant_id":"tenant-1",'
            + b'"correlation_id":"correlation-1","actor":{}}'
        ),
    ],
)
def test_decode_rejects_semantically_similar_noncanonical_bytes(payload: bytes) -> None:
    assert payload != encode_request_context(context())
    assert_safe_failure(lambda: decode_request_context(payload))
