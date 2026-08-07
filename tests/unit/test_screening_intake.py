"""Strict, private, immutable canonical screening-intake evidence."""

from __future__ import annotations

import copy
import json
import traceback
from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from typing import cast

import pytest

import tradesieve.application.screening_intake as intake_module
from tradesieve.application.auth import (
    ActorContext,
    ActorType,
    RequestContext,
    Scope,
)
from tradesieve.application.contracts import ScreeningRequest
from tradesieve.application.screening_intake import (
    JSON_MEDIA_TYPE,
    MAX_BODY_BYTES,
    MAX_FIELD_ISSUES,
    MAX_JSON_DEPTH,
    MAX_JSON_NODES,
    CanonicalScreeningIntake,
    FieldIssueReason,
    ScreeningIntakeError,
    ScreeningIntakeErrorCode,
    ScreeningIntakeFieldIssue,
)

NOW = datetime(2026, 8, 7, 9, 30, tzinfo=UTC)


def payload() -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "tenant_id": "tenant-1",
        "correlation_id": "correlation-1",
        "data_classification": "SYNTHETIC",
        "external_object": {
            "system": "synthetic-crm",
            "object_type": "CUSTOMER",
            "object_id": "customer-1",
            "object_version": "1",
        },
        "proposed_action": "CUSTOMER_ONBOARDING",
        "activities": [],
        "action_due_at": None,
        "legal_nexus": [],
        "parties": [],
        "ownership_and_control": [],
        "goods": [],
        "route": None,
        "documents": [],
        "payment": None,
    }


def actor_context(*, tenant_id: str = "tenant-1") -> ActorContext:
    return ActorContext(
        subject="actor-1",
        client_id="client-1",
        tenant_id=tenant_id,
        actor_type=ActorType.SERVICE,
        scopes=frozenset({Scope.SCREENING_SUBMIT}),
        roles=frozenset(),
        issuer="https://identity.example.test/",
        audience="urn:tradesieve:test",
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=5),
        demo_identity=False,
    )


def request_context(
    *, tenant_id: str = "tenant-1", correlation_id: str = "correlation-1"
) -> RequestContext:
    return RequestContext(
        actor=actor_context(tenant_id=tenant_id),
        tenant_id=tenant_id,
        correlation_id=correlation_id,
    )


def encode(
    document: object,
    *,
    ensure_ascii: bool = False,
    separators: tuple[str, str] | None = (",", ":"),
    indent: int | None = None,
    sort_keys: bool = False,
) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=ensure_ascii,
        separators=separators,
        indent=indent,
        sort_keys=sort_keys,
    ).encode()


def decode(
    document: dict[str, object],
    *,
    context: RequestContext | None = None,
    received_at: datetime = NOW,
    raw_body: bytes | None = None,
) -> CanonicalScreeningIntake:
    return CanonicalScreeningIntake.decode(
        encode(document) if raw_body is None else raw_body,
        JSON_MEDIA_TYPE,
        context=context or request_context(),
        received_at=received_at,
    )


def rejected(
    raw_body: object,
    expected: ScreeningIntakeErrorCode,
    *,
    media_type: object = JSON_MEDIA_TYPE,
    context: RequestContext | None = None,
    received_at: datetime = NOW,
) -> ScreeningIntakeError:
    with pytest.raises(ScreeningIntakeError) as exc_info:
        CanonicalScreeningIntake.decode(
            raw_body,
            media_type,
            context=context or request_context(),
            received_at=received_at,
        )
    assert exc_info.value.code is expected
    return exc_info.value


def test_valid_intake_binds_exact_private_evidence_and_normalizes_receive_time() -> (
    None
):
    document = payload()
    document["parties"] = [
        {
            "party_ref": "party-1",
            "roles": ["CUSTOMER"],
            "entity_type": "ORGANIZATION",
            "legal_name": "PRIVATE-RAW-SENTINEL",
        }
    ]
    raw_body = encode(document, indent=2)
    context = request_context()
    intake = decode(
        document,
        raw_body=raw_body,
        context=context,
        received_at=datetime(2026, 8, 7, 17, 30, tzinfo=timezone(timedelta(hours=8))),
    )

    assert intake.media_type == JSON_MEDIA_TYPE
    assert intake.byte_length == len(raw_body)
    assert intake.byte_hash.startswith("sha256:")
    assert intake.canonical_hash == intake.request.canonical_input_hash()
    assert intake.schema_version == "1.0.0"
    assert intake.received_at == NOW
    assert intake.context == context
    assert intake.context is not context
    assert intake.context.actor.client_id == "client-1"
    assert intake.context.actor.subject == "actor-1"
    assert "PRIVATE-RAW-SENTINEL" not in repr(intake)
    assert raw_body not in repr(intake).encode()


def test_request_reads_reverify_and_return_no_mutable_alias() -> None:
    intake = decode(payload())
    first = intake.request
    second = intake.request
    assert first == second
    assert first is not second

    first.tenant_id = "attacker-change"
    assert intake.request.tenant_id == "tenant-1"
    with pytest.raises(FrozenInstanceError):
        intake.byte_length = 1  # type: ignore[misc]

    copied = copy.copy(intake)
    deep_copied = copy.deepcopy(intake)
    assert copied.request == intake.request
    assert deep_copied.request == intake.request


def test_raw_and_semantic_hashes_preserve_their_distinct_identities() -> None:
    document = payload()
    document["parties"] = [
        {
            "party_ref": "party-1",
            "roles": ["CUSTOMER"],
            "entity_type": "ORGANIZATION",
            "legal_name": "测试公司",
        }
    ]
    compact = encode(document, ensure_ascii=True, sort_keys=True)
    reordered = encode(dict(reversed(document.items())), indent=2, ensure_ascii=False)
    first = decode(document, raw_body=compact)
    second = decode(document, raw_body=reordered)

    assert first.byte_hash != second.byte_hash
    assert first.canonical_hash == second.canonical_hash
    assert first.request == second.request


def test_correlation_is_transport_only_but_tenant_and_business_remain_semantic() -> (
    None
):
    original_document = payload()
    original = decode(original_document)

    correlation_document = copy.deepcopy(original_document)
    correlation_document["correlation_id"] = "correlation-2"
    correlation = decode(
        correlation_document,
        context=request_context(correlation_id="correlation-2"),
    )

    tenant_document = copy.deepcopy(original_document)
    tenant_document["tenant_id"] = "tenant-2"
    tenant = decode(tenant_document, context=request_context(tenant_id="tenant-2"))

    business_document = copy.deepcopy(original_document)
    business_document["proposed_action"] = "QUOTE_RELEASE"
    business = decode(business_document)

    assert correlation.canonical_hash == original.canonical_hash
    assert tenant.canonical_hash != original.canonical_hash
    assert business.canonical_hash != original.canonical_hash


def test_utc_offsets_canonicalize_and_reverify_round_trip() -> None:
    utc_document = payload()
    utc_document["action_due_at"] = "2026-08-07T09:30:00Z"
    offset_document = copy.deepcopy(utc_document)
    offset_document["action_due_at"] = "2026-08-07T17:30:00+08:00"
    utc_intake = decode(utc_document)
    offset_intake = decode(offset_document)

    assert utc_intake.byte_hash != offset_intake.byte_hash
    assert utc_intake.canonical_hash == offset_intake.canonical_hash
    assert offset_intake.request.model_dump(mode="json")["action_due_at"] == (
        "2026-08-07T09:30:00Z"
    )

    encoded_request = encode(offset_intake.request.model_dump(mode="json"), indent=2)
    round_trip = decode(offset_document, raw_body=encoded_request)
    reverification = round_trip.reverify()
    assert reverification == round_trip
    assert reverification is not round_trip
    assert reverification.canonical_hash == offset_intake.canonical_hash


class BytesSubclass(bytes):
    pass


class StringSubclass(str):
    pass


@pytest.mark.parametrize(
    "raw_body",
    ["{}", bytearray(b"{}"), memoryview(b"{}"), BytesSubclass(b"{}")],
)
def test_only_exact_bytes_are_accepted(raw_body: object) -> None:
    rejected(raw_body, ScreeningIntakeErrorCode.BODY_TYPE)


@pytest.mark.parametrize(
    "media_type",
    [
        None,
        b"application/json",
        "Application/JSON",
        "application/json; charset=utf-8",
        StringSubclass("application/json"),
    ],
)
def test_only_exact_json_media_type_is_accepted(media_type: object) -> None:
    rejected(
        encode(payload()), ScreeningIntakeErrorCode.MEDIA_TYPE, media_type=media_type
    )


def test_body_size_boundaries_are_exact() -> None:
    base = encode(payload())
    exact = base + b" " * (MAX_BODY_BYTES - len(base))
    intake = decode(payload(), raw_body=exact)
    assert intake.byte_length == MAX_BODY_BYTES
    rejected(b"", ScreeningIntakeErrorCode.BODY_EMPTY)
    rejected(exact + b" ", ScreeningIntakeErrorCode.BODY_TOO_LARGE)


@pytest.mark.parametrize(
    ("raw_body", "expected"),
    [
        (b"\xef\xbb\xbf{}", ScreeningIntakeErrorCode.BOM_FORBIDDEN),
        (b'{"x":"\xff"}', ScreeningIntakeErrorCode.UTF8_REQUIRED),
        (b"   ", ScreeningIntakeErrorCode.JSON_INVALID),
        (b'{"x":"line\nfeed"}', ScreeningIntakeErrorCode.JSON_INVALID),
        (b'{"schema_version":"1.0.0"}{}', ScreeningIntakeErrorCode.JSON_TRAILING),
    ],
)
def test_encoding_invalid_json_control_and_trailing_attacks_are_finite(
    raw_body: bytes, expected: ScreeningIntakeErrorCode
) -> None:
    rejected(raw_body, expected)


def test_trailing_whitespace_and_escaped_structure_characters_are_safe() -> None:
    document = payload()
    document["parties"] = [
        {
            "party_ref": "party-1",
            "roles": ["CUSTOMER"],
            "entity_type": "ORGANIZATION",
            "legal_name": 'braces [{ and quote " and slash \\',
        }
    ]
    raw_body = encode(document) + b" \n\t"
    assert decode(document, raw_body=raw_body).request.parties[0].legal_name


@pytest.mark.parametrize("nested", [False, True])
def test_duplicate_keys_are_rejected_at_every_depth(nested: bool) -> None:
    raw_text = encode(payload()).decode()
    target = '"system":"synthetic-crm"' if nested else '"schema_version":"1.0.0"'
    raw_text = raw_text.replace(target, f"{target},{target}", 1)
    rejected(raw_text.encode(), ScreeningIntakeErrorCode.JSON_DUPLICATE_KEY)


@pytest.mark.parametrize("token", ["1.0", "1e3", "-0.5"])
def test_float_tokens_are_rejected_before_canonical_validation(token: str) -> None:
    raw_text = encode(payload()).decode()
    raw_text = raw_text[:-1] + f',"attacker_number":{token}}}'
    rejected(raw_text.encode(), ScreeningIntakeErrorCode.JSON_FLOAT_FORBIDDEN)


def test_nested_float_is_rejected_but_integer_reaches_authoritative_model() -> None:
    document = payload()
    document["payment"] = {"payment_ref": "payment-1", "amount": "1"}
    raw_text = encode(document).decode().replace('"amount":"1"', '"amount":1.5')
    rejected(raw_text.encode(), ScreeningIntakeErrorCode.JSON_FLOAT_FORBIDDEN)

    raw_text = encode(document).decode().replace('"amount":"1"', '"amount":1')
    error = rejected(raw_text.encode(), ScreeningIntakeErrorCode.REQUEST_INVALID)
    assert ScreeningIntakeFieldIssue("/payment/amount", FieldIssueReason.VALUE) in (
        error.issues
    )


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_tokens_are_rejected(token: str) -> None:
    raw_text = encode(payload()).decode()
    raw_text = raw_text[:-1] + f',"attacker_number":{token}}}'
    rejected(raw_text.encode(), ScreeningIntakeErrorCode.JSON_NON_FINITE)


@pytest.mark.parametrize("raw_body", [b"[]", b'"value"', b"1", b"true", b"null"])
def test_json_root_must_be_an_object(raw_body: bytes) -> None:
    rejected(raw_body, ScreeningIntakeErrorCode.JSON_ROOT)


def test_depth_boundary_and_max_plus_one_are_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(intake_module, "MAX_JSON_DEPTH", 3)
    assert decode(payload()).request.tenant_id == "tenant-1"
    monkeypatch.setattr(intake_module, "MAX_JSON_DEPTH", 2)
    rejected(encode(payload()), ScreeningIntakeErrorCode.JSON_DEPTH)

    monkeypatch.setattr(intake_module, "MAX_JSON_DEPTH", MAX_JSON_DEPTH)
    deep = b'{"unknown":' + b"[" * MAX_JSON_DEPTH + b"0" + b"]" * MAX_JSON_DEPTH + b"}"
    rejected(deep, ScreeningIntakeErrorCode.JSON_DEPTH)


def _node_count(document: object) -> int:
    pending = [document]
    count = 0
    while pending:
        value = pending.pop()
        count += 1
        if isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return count


def test_node_boundary_and_max_plus_one_are_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    count = _node_count(payload())
    assert count < MAX_JSON_NODES
    monkeypatch.setattr(intake_module, "MAX_JSON_NODES", count)
    assert decode(payload()).request.tenant_id == "tenant-1"
    monkeypatch.setattr(intake_module, "MAX_JSON_NODES", count - 1)
    rejected(encode(payload()), ScreeningIntakeErrorCode.JSON_NODES)


@pytest.mark.parametrize(
    ("mutation", "pointer", "reason"),
    [
        (lambda item: item.pop("schema_version"), "/schema_version", "REQUIRED"),
        (lambda item: item.update(tenant_id="bad tenant"), "/tenant_id", "FORMAT"),
        (lambda item: item.update(correlation_id=[]), "/correlation_id", "TYPE"),
        (
            lambda item: item.update(data_classification="UNKNOWN"),
            "/data_classification",
            "VALUE",
        ),
        (
            lambda item: item.update(external_object={}),
            "/external_object/system",
            "REQUIRED",
        ),
        (
            lambda item: item.update(proposed_action="UNKNOWN"),
            "/proposed_action",
            "VALUE",
        ),
        (lambda item: item.update(activities=["SALE"] * 17), "/activities", "BOUNDS"),
        (
            lambda item: item.update(action_due_at="not-a-date"),
            "/action_due_at",
            "FORMAT",
        ),
        (
            lambda item: item.update(legal_nexus=[{}]),
            "/legal_nexus/0/nexus_ref",
            "REQUIRED",
        ),
        (lambda item: item.update(parties=[{}]), "/parties/0/party_ref", "REQUIRED"),
        (
            lambda item: item.update(ownership_and_control=[{}]),
            "/ownership_and_control/0/relationship_ref",
            "REQUIRED",
        ),
        (lambda item: item.update(goods=[{}]), "/goods/0/line_ref", "REQUIRED"),
        (lambda item: item.update(route={"unknown": True}), "/route", "EXTRA"),
        (
            lambda item: item.update(documents=[{}]),
            "/documents/0/document_ref",
            "REQUIRED",
        ),
        (lambda item: item.update(payment={}), "/payment/payment_ref", "REQUIRED"),
    ],
)
def test_every_canonical_request_field_family_has_safe_finite_issues(
    mutation: object, pointer: str, reason: str
) -> None:
    document = payload()
    cast(AnyMutation, mutation)(document)
    error = rejected(encode(document), ScreeningIntakeErrorCode.REQUEST_INVALID)
    assert ScreeningIntakeFieldIssue(pointer, FieldIssueReason(reason)) in error.issues


class AnyMutation:
    def __call__(self, document: dict[str, object]) -> object: ...


def test_unknown_fields_and_reference_graph_fail_without_input_disclosure() -> None:
    sentinel = "SECRET-NAME-TOKEN-SQL-/tmp/private"
    graph_sentinel = "SECRET-NAME-TOKEN-SQL-private"
    unknown = payload()
    unknown[sentinel] = sentinel
    extra_error = rejected(encode(unknown), ScreeningIntakeErrorCode.REQUEST_INVALID)
    assert extra_error.issues == (
        ScreeningIntakeFieldIssue("", FieldIssueReason.EXTRA),
    )

    graph = payload()
    graph["parties"] = [
        {
            "party_ref": graph_sentinel,
            "roles": ["BUYER"],
            "entity_type": "ORGANIZATION",
        },
        {
            "party_ref": graph_sentinel,
            "roles": ["SELLER"],
            "entity_type": "ORGANIZATION",
        },
    ]
    graph_error = rejected(encode(graph), ScreeningIntakeErrorCode.REQUEST_INVALID)
    assert ScreeningIntakeFieldIssue("", FieldIssueReason.REFERENCE_GRAPH) in (
        graph_error.issues
    )
    for error in (extra_error, graph_error):
        rendered = "".join(traceback.format_exception(error))
        for private_value in (sentinel, graph_sentinel):
            assert private_value not in str(error)
            assert private_value not in repr(error)
            assert private_value not in rendered
        assert error.__cause__ is None
        assert error.__context__ is None


def test_nested_decoder_failure_has_no_private_cause_context_or_traceback_text() -> (
    None
):
    sentinel = "PRIVATE-DUPLICATE-DECODER-SENTINEL"
    raw_text = encode(payload()).decode()
    raw_text = raw_text[:-1] + f',"{sentinel}":1,"{sentinel}":2}}'
    error = rejected(raw_text.encode(), ScreeningIntakeErrorCode.JSON_DUPLICATE_KEY)
    rendered = "".join(traceback.format_exception(error))
    assert sentinel not in str(error)
    assert sentinel not in repr(error)
    assert sentinel not in rendered
    assert error.__cause__ is None
    assert error.__context__ is None


def test_error_ordering_uniqueness_and_issue_limit_are_deterministic() -> None:
    document = payload()
    document["parties"] = [{} for _ in range(MAX_FIELD_ISSUES + 1)]
    first = rejected(encode(document), ScreeningIntakeErrorCode.REQUEST_INVALID)
    second = rejected(encode(document), ScreeningIntakeErrorCode.REQUEST_INVALID)
    assert len(first.issues) == MAX_FIELD_ISSUES
    assert first.issues == tuple(sorted(set(first.issues)))
    assert second.issues == first.issues


def test_context_rebinding_is_exact_sorted_and_redacted() -> None:
    error = rejected(
        encode(payload()),
        ScreeningIntakeErrorCode.CONTEXT_MISMATCH,
        context=request_context(
            tenant_id="other-tenant", correlation_id="other-correlation"
        ),
    )
    assert error.issues == (
        ScreeningIntakeFieldIssue("/correlation_id", FieldIssueReason.CONTEXT_MISMATCH),
        ScreeningIntakeFieldIssue("/tenant_id", FieldIssueReason.CONTEXT_MISMATCH),
    )
    assert "tenant-1" not in str(error)
    assert "other-tenant" not in repr(error)


def test_receive_metadata_must_be_typed_and_timezone_aware() -> None:
    valid = decode(payload())
    with pytest.raises(ScreeningIntakeError) as wrong_context:
        replace(valid, _context=cast(RequestContext, object()))
    assert wrong_context.value.code is ScreeningIntakeErrorCode.RECEIVE_METADATA_INVALID

    rejected(
        encode(payload()),
        ScreeningIntakeErrorCode.RECEIVE_METADATA_INVALID,
        received_at=datetime(2026, 8, 7, 9, 30),
    )


def test_context_and_actor_inputs_and_returned_views_have_no_mutable_alias() -> None:
    original_actor = actor_context()
    original_context = RequestContext(
        actor=original_actor,
        tenant_id="tenant-1",
        correlation_id="correlation-1",
    )
    intake = decode(payload(), context=original_context)

    object.__setattr__(original_actor, "client_id", "caller-mutated-client")
    object.__setattr__(original_context, "correlation_id", "caller-mutated-correlation")
    assert intake.context.actor.client_id == "client-1"
    assert intake.context.correlation_id == "correlation-1"
    assert intake.request.tenant_id == "tenant-1"

    returned = intake.context
    object.__setattr__(returned.actor, "client_id", "returned-mutated-client")
    object.__setattr__(returned, "correlation_id", "returned-mutated-correlation")
    assert intake.context.actor.client_id == "client-1"
    assert intake.context.correlation_id == "correlation-1"
    assert intake.request.correlation_id == "correlation-1"


def test_actor_and_request_context_tenants_must_agree() -> None:
    mismatched = RequestContext(
        actor=actor_context(tenant_id="tenant-1"),
        tenant_id="tenant-2",
        correlation_id="correlation-1",
    )
    document = payload()
    document["tenant_id"] = "tenant-2"
    rejected(
        encode(document),
        ScreeningIntakeErrorCode.RECEIVE_METADATA_INVALID,
        context=mismatched,
    )


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("subject", StringSubclass("actor-1")),
        ("client_id", 1),
        ("tenant_id", "bad tenant"),
        ("actor_type", "SERVICE"),
        ("scopes", {Scope.SCREENING_SUBMIT}),
        ("scopes", frozenset({"screening:submit"})),
        ("roles", set()),
        ("roles", frozenset({"auditor"})),
        ("issuer", StringSubclass("https://identity.example.test/")),
        ("audience", "private audience"),
        ("demo_identity", 1),
        ("issued_at", datetime(2026, 8, 7, 9, 29)),
        ("expires_at", NOW - timedelta(minutes=2)),
    ],
)
def test_actor_receive_metadata_requires_exact_safe_runtime_types(
    field_name: str, replacement: object
) -> None:
    actor = actor_context()
    object.__setattr__(actor, field_name, replacement)
    context = RequestContext(
        actor=actor,
        tenant_id="tenant-1",
        correlation_id="correlation-1",
    )
    rejected(
        encode(payload()),
        ScreeningIntakeErrorCode.RECEIVE_METADATA_INVALID,
        context=context,
    )


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("tenant_id", StringSubclass("tenant-1")),
        ("correlation_id", StringSubclass("correlation-1")),
    ],
)
def test_request_context_metadata_requires_exact_string_types(
    field_name: str, replacement: object
) -> None:
    context = request_context()
    object.__setattr__(context, field_name, replacement)
    rejected(
        encode(payload()),
        ScreeningIntakeErrorCode.RECEIVE_METADATA_INVALID,
        context=context,
    )


def test_post_construction_receive_and_context_corruption_fail_closed() -> None:
    valid = decode(payload())

    changed_receive_time = copy.deepcopy(valid)
    object.__setattr__(changed_receive_time, "received_at", NOW + timedelta(seconds=1))
    with pytest.raises(ScreeningIntakeError) as changed_time_error:
        _ = changed_receive_time.request
    assert changed_time_error.value.code is ScreeningIntakeErrorCode.INTEGRITY_FAILURE

    noncanonical_receive_time = copy.deepcopy(valid)
    object.__setattr__(
        noncanonical_receive_time,
        "received_at",
        datetime(2026, 8, 7, 17, 30, tzinfo=timezone(timedelta(hours=8))),
    )
    with pytest.raises(ScreeningIntakeError) as noncanonical_time_error:
        noncanonical_receive_time.reverify()
    assert (
        noncanonical_time_error.value.code
        is ScreeningIntakeErrorCode.RECEIVE_METADATA_INVALID
    )

    invalid_receive_time = copy.deepcopy(valid)
    object.__setattr__(invalid_receive_time, "received_at", "PRIVATE-BAD-DATETIME")
    with pytest.raises(ScreeningIntakeError) as invalid_time_error:
        _ = invalid_receive_time.context
    assert (
        invalid_time_error.value.code
        is ScreeningIntakeErrorCode.RECEIVE_METADATA_INVALID
    )
    assert "PRIVATE-BAD-DATETIME" not in repr(invalid_time_error.value)

    corrupted_actor = copy.deepcopy(valid)
    object.__setattr__(
        corrupted_actor._context.actor, "client_id", "corrupted-valid-client"
    )
    with pytest.raises(ScreeningIntakeError) as actor_error:
        _ = corrupted_actor.request
    assert actor_error.value.code is ScreeningIntakeErrorCode.INTEGRITY_FAILURE

    corrupted_context = copy.deepcopy(valid)
    object.__setattr__(corrupted_context, "_context", object())
    with pytest.raises(ScreeningIntakeError) as context_error:
        corrupted_context.reverify()
    assert context_error.value.code is ScreeningIntakeErrorCode.RECEIVE_METADATA_INVALID


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("byte_length", 1),
        ("byte_hash", "sha256:" + "0" * 64),
        ("_canonical_request_json", b"{}"),
        ("canonical_hash", "sha256:" + "0" * 64),
        ("schema_version", "9.9.9"),
    ],
)
def test_constructor_reverification_rejects_metadata_tampering(
    field_name: str, replacement: object
) -> None:
    valid = decode(payload())
    with pytest.raises(ScreeningIntakeError) as exc_info:
        replace(valid, **{field_name: replacement})  # type: ignore[arg-type]
    assert exc_info.value.code is ScreeningIntakeErrorCode.INTEGRITY_FAILURE
    assert exc_info.value.issues == ()


def test_type_equal_computed_metadata_exploits_fail_exact_type_checks() -> None:
    valid = decode(payload())
    replacements: list[tuple[str, object]] = [
        ("byte_length", float(valid.byte_length)),
        ("byte_length", True),
        ("byte_hash", StringSubclass(valid.byte_hash)),
        ("_canonical_request_json", bytearray(valid._canonical_request_json)),
        (
            "_canonical_request_json",
            BytesSubclass(valid._canonical_request_json),
        ),
        ("canonical_hash", StringSubclass(valid.canonical_hash)),
        ("schema_version", StringSubclass(valid.schema_version)),
        ("media_type", StringSubclass(valid.media_type)),
        ("_raw_bytes", bytearray(valid._raw_bytes)),
        ("_raw_bytes", BytesSubclass(valid._raw_bytes)),
        ("_receive_metadata_hash", StringSubclass(valid._receive_metadata_hash)),
    ]
    for field_name, replacement in replacements:
        with pytest.raises(ScreeningIntakeError) as exc_info:
            replace(valid, **{field_name: replacement})  # type: ignore[arg-type]
        assert exc_info.value.code is ScreeningIntakeErrorCode.INTEGRITY_FAILURE
        assert exc_info.value.issues == ()


def test_raw_media_and_context_corruption_fail_closed_on_construction_and_read() -> (
    None
):
    valid = decode(payload())
    with pytest.raises(ScreeningIntakeError) as media_error:
        replace(valid, media_type="text/json")
    assert media_error.value.code is ScreeningIntakeErrorCode.MEDIA_TYPE

    changed_document = payload()
    changed_document["proposed_action"] = "QUOTE_RELEASE"
    with pytest.raises(ScreeningIntakeError) as raw_error:
        replace(valid, _raw_bytes=encode(changed_document))
    assert raw_error.value.code is ScreeningIntakeErrorCode.INTEGRITY_FAILURE

    corrupted = copy.copy(valid)
    object.__setattr__(corrupted, "_raw_bytes", b"not-json")
    with pytest.raises(ScreeningIntakeError) as read_error:
        _ = corrupted.request
    assert read_error.value.code is ScreeningIntakeErrorCode.JSON_INVALID
    with pytest.raises(ScreeningIntakeError) as context_error:
        _ = corrupted.context
    assert context_error.value.code is ScreeningIntakeErrorCode.JSON_INVALID


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("byte_length", 1),
        ("byte_hash", "sha256:" + "0" * 64),
        ("_canonical_request_json", b"{}"),
        ("canonical_hash", "sha256:" + "0" * 64),
        ("schema_version", "9.9.9"),
    ],
)
def test_context_read_rejects_post_construction_computed_corruption(
    field_name: str, replacement: object
) -> None:
    corrupted = copy.deepcopy(decode(payload()))
    object.__setattr__(corrupted, field_name, replacement)
    with pytest.raises(ScreeningIntakeError) as exc_info:
        _ = corrupted.context
    assert exc_info.value.code is ScreeningIntakeErrorCode.INTEGRITY_FAILURE
    assert exc_info.value.issues == ()


@pytest.mark.parametrize("target", ["decoder", "pydantic", "canonicalization"])
def test_unexpected_nested_exceptions_are_suppressed_without_private_detail(
    monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    sentinel = f"PRIVATE-UNEXPECTED-{target}-SENTINEL"

    def explode(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError(sentinel)

    if target == "decoder":
        monkeypatch.setattr(intake_module, "_decode_document", explode)
    elif target == "pydantic":
        monkeypatch.setattr(
            ScreeningRequest,
            "model_validate",
            classmethod(explode),
        )
    else:
        monkeypatch.setattr(intake_module, "_canonical_request_bytes", explode)

    error = rejected(encode(payload()), ScreeningIntakeErrorCode.INTEGRITY_FAILURE)
    rendered = "".join(traceback.format_exception(error))
    assert sentinel not in str(error)
    assert sentinel not in repr(error)
    assert sentinel not in rendered
    assert error.__cause__ is None
    assert error.__context__ is None


def test_unexpected_exception_firewall_does_not_catch_base_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def interrupt(*_args: object, **_kwargs: object) -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr(intake_module, "_canonical_request_bytes", interrupt)
    with pytest.raises(KeyboardInterrupt):
        decode(payload())


def test_public_error_defaults_are_stable_and_finite() -> None:
    error = ScreeningIntakeError(ScreeningIntakeErrorCode.INTEGRITY_FAILURE)
    assert error.issues == ()
    assert str(error) == "screening intake rejected: INTEGRITY_FAILURE"
    assert set(ScreeningIntakeErrorCode)
    assert set(FieldIssueReason)

    issues = tuple(
        sorted(
            ScreeningIntakeFieldIssue(f"/parties/{index}", FieldIssueReason.VALUE)
            for index in range(MAX_FIELD_ISSUES)
        )
    )
    bounded = ScreeningIntakeError(ScreeningIntakeErrorCode.REQUEST_INVALID, issues)
    assert bounded.issues == issues

    extra = ScreeningIntakeFieldIssue(
        f"/parties/{MAX_FIELD_ISSUES}", FieldIssueReason.VALUE
    )
    with pytest.raises(ValueError, match="^invalid screening intake error$"):
        ScreeningIntakeError(
            ScreeningIntakeErrorCode.REQUEST_INVALID, issues + (extra,)
        )
    with pytest.raises(ValueError, match="^invalid screening intake error$"):
        ScreeningIntakeError(
            ScreeningIntakeErrorCode.REQUEST_INVALID, tuple(reversed(issues))
        )


def test_public_field_issue_constructor_rejects_unsafe_types_and_pointers() -> None:
    sentinel = "PRIVATE-POINTER-CONSTRUCTOR-SENTINEL"
    reason_sentinel = "PRIVATE-REASON-SENTINEL"
    invalid_values: tuple[tuple[object, object], ...] = (
        (StringSubclass("/tenant_id"), FieldIssueReason.VALUE),
        (f"/{sentinel}", FieldIssueReason.VALUE),
        ("/parties/" + "9" * 1024, FieldIssueReason.VALUE),
        ("/tenant_id", reason_sentinel),
    )
    for pointer, reason in invalid_values:
        with pytest.raises((TypeError, ValueError)) as exc_info:
            ScreeningIntakeFieldIssue(
                cast(str, pointer), cast(FieldIssueReason, reason)
            )
        rendered = "".join(traceback.format_exception(exc_info.value))
        assert sentinel not in str(exc_info.value)
        assert sentinel not in repr(exc_info.value)
        assert sentinel not in rendered
        assert reason_sentinel not in rendered


def test_public_error_constructor_rejects_non_finite_or_untyped_arguments() -> None:
    issue = ScreeningIntakeFieldIssue("/tenant_id", FieldIssueReason.VALUE)
    code_sentinel = "PRIVATE-CODE-SENTINEL"
    invalid_calls: tuple[Callable[[], object], ...] = (
        lambda: ScreeningIntakeError(cast(ScreeningIntakeErrorCode, code_sentinel)),
        lambda: ScreeningIntakeError(
            ScreeningIntakeErrorCode.REQUEST_INVALID,
            cast(tuple[ScreeningIntakeFieldIssue, ...], [issue]),
        ),
        lambda: ScreeningIntakeError(
            ScreeningIntakeErrorCode.REQUEST_INVALID,
            cast(tuple[ScreeningIntakeFieldIssue, ...], (object(),)),
        ),
        lambda: ScreeningIntakeError(
            ScreeningIntakeErrorCode.REQUEST_INVALID, (issue, issue)
        ),
    )
    for invalid_call in invalid_calls:
        with pytest.raises((TypeError, ValueError)) as exc_info:
            invalid_call()
        rendered = "".join(traceback.format_exception(exc_info.value))
        assert code_sentinel not in str(exc_info.value)
        assert code_sentinel not in repr(exc_info.value)
        assert code_sentinel not in rendered
