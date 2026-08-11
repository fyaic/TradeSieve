"""Generated contract fixtures, schema parity, and drift-failure tests."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from tradesieve.application import source_snapshot_query
from tradesieve.application.contract_examples import (
    event_examples,
    incomplete_customer_onboarding_request,
    structurally_invalid_request,
    transaction_review_required_result,
    transaction_screening_request,
)
from tradesieve.application.contracts import EventEnvelope, ScreeningRequest
from tradesieve.application.official_screening import (
    OfficialScreeningRequest,
    OfficialScreeningResult,
)
from tradesieve.application.official_source_refresh import OfficialSourceRefreshResult
from tradesieve.application.source_snapshot_contracts import (
    SOURCE_SNAPSHOT_CONTRACT_MODELS,
    SourceSnapshotDetail,
    SourceSnapshotHistory,
    SourceSnapshotListing,
)

ROOT = Path(__file__).parents[2]
GENERATOR = ROOT / "scripts/generate_contract.py"


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def test_generated_golden_incomplete_result_and_event_examples_round_trip() -> None:
    request = transaction_screening_request()
    incomplete = incomplete_customer_onboarding_request()
    result = transaction_review_required_result(request)
    events = event_examples(result)

    assert load_json(ROOT / "examples/requests/transaction-screening.json") == (
        request.model_dump(mode="json")
    )
    assert load_json(
        ROOT / "examples/requests/customer-onboarding.incomplete.json"
    ) == incomplete.model_dump(mode="json")
    assert load_json(
        ROOT / "examples/responses/transaction-screening.review-required.json"
    ) == result.model_dump(mode="json")
    assert result.result_hash == result.canonical_result_hash()

    event_paths = {
        "screening.completed": "screening.completed.json",
        "case.state_changed": "case.state-changed.json",
        "human_decision.recorded": "human-decision.recorded.json",
    }
    for event_type, filename in event_paths.items():
        committed = load_json(ROOT / "examples/events" / filename)
        parsed = EventEnvelope.model_validate(committed)
        assert committed == events[event_type].model_dump(mode="json")
        assert parsed.event_type.value == event_type


def test_committed_negative_fixture_is_rejected_and_not_published_as_valid() -> None:
    incomplete = incomplete_customer_onboarding_request()
    invalid = structurally_invalid_request(incomplete)
    path = (
        ROOT / "examples/requests/invalid/transaction-screening.structural-error.json"
    )
    assert load_json(path) == invalid

    with pytest.raises(ValidationError) as exc_info:
        ScreeningRequest.model_validate(invalid)
    message = str(exc_info.value)
    assert "unexpected_business_override" in message
    assert "timezone" in message

    openapi = load_json(ROOT / "api/openapi/tradesieve.v1.json")
    assert isinstance(openapi, dict)
    published = openapi["paths"]["/v1/screenings"]["post"]["requestBody"]["content"][
        "application/json"
    ]["examples"]
    assert "invalid" not in published


def test_openapi_and_shared_registry_have_generated_schema_parity() -> None:
    openapi = load_json(ROOT / "api/openapi/tradesieve.v1.json")
    registry = load_json(ROOT / "api/schemas/tradesieve.contracts.v1.json")
    assert isinstance(openapi, dict)
    assert isinstance(registry, dict)
    openapi_schemas = openapi["components"]["schemas"]
    shared_schemas = registry["$defs"]

    assert set(openapi_schemas) == set(shared_schemas)
    assert openapi["x-tradesieve-canonical-source"]["model"] == (
        "tradesieve.application.contracts"
    )
    assert openapi["x-tradesieve-canonical-source"]["additional_models"] == [
        "tradesieve.application.source_snapshot_contracts",
        "tradesieve.application.official_screening",
        "tradesieve.application.official_source_refresh",
    ]
    assert openapi["x-tradesieve-schema-entrypoints"] == {
        "SourceSnapshotListing": {"$ref": "#/components/schemas/SourceSnapshotListing"},
        "SourceSnapshotDetail": {"$ref": "#/components/schemas/SourceSnapshotDetail"},
        "SourceSnapshotHistory": {"$ref": "#/components/schemas/SourceSnapshotHistory"},
        "OfficialScreeningRequest": {
            "$ref": "#/components/schemas/OfficialScreeningRequest"
        },
        "OfficialScreeningResult": {
            "$ref": "#/components/schemas/OfficialScreeningResult"
        },
        "OfficialSourceRefreshResult": {
            "$ref": "#/components/schemas/OfficialSourceRefreshResult"
        },
    }
    for schema in openapi_schemas.values():
        if schema.get("type") == "object":
            assert schema.get("additionalProperties") is False

    request_properties = openapi_schemas["ScreeningRequest"]["properties"]
    assert {
        "activities",
        "legal_nexus",
        "parties",
        "ownership_and_control",
        "goods",
        "route",
        "documents",
        "payment",
    } <= set(request_properties)
    assert (
        openapi_schemas["EventEnvelope"]["properties"]["data"]["discriminator"][
            "propertyName"
        ]
        == "kind"
    )
    assert "never legal clearance" in openapi_schemas["Signal"]["description"]
    amount_schema = openapi_schemas["PaymentPath"]["properties"]["amount"]
    assert {item["type"] for item in amount_schema["anyOf"]} == {"string", "null"}
    decimal_fields = (
        ("GoodsLine", "quantity"),
        ("OwnershipControlRelationship", "ownership_percentage"),
        ("ClassificationCandidate", "confidence"),
    )
    for schema_name, field_name in decimal_fields:
        schema = openapi_schemas[schema_name]["properties"][field_name]
        assert {item["type"] for item in schema["anyOf"]} == {"string", "null"}


def test_source_snapshot_contract_roots_have_parity_and_one_way_identity() -> None:
    openapi = load_json(ROOT / "api/openapi/tradesieve.v1.json")
    registry = load_json(ROOT / "api/schemas/tradesieve.contracts.v1.json")
    assert isinstance(openapi, dict)
    assert isinstance(registry, dict)
    roots = (
        SourceSnapshotListing,
        SourceSnapshotDetail,
        SourceSnapshotHistory,
    )
    root_names = [model.__name__ for model in roots]

    assert roots == SOURCE_SNAPSHOT_CONTRACT_MODELS
    assert registry["x-tradesieve-entrypoints"][-6:-3] == root_names
    assert list(openapi["x-tradesieve-schema-entrypoints"])[:3] == root_names
    assert all(name in openapi["components"]["schemas"] for name in root_names)
    assert all(name in registry["$defs"] for name in root_names)
    assert source_snapshot_query.SourceSnapshotListing is SourceSnapshotListing
    assert source_snapshot_query.SourceSnapshotDetail is SourceSnapshotDetail
    assert source_snapshot_query.SourceSnapshotHistory is SourceSnapshotHistory


def test_source_snapshot_schema_closure_is_operations_safe_and_redacted() -> None:
    registry = load_json(ROOT / "api/schemas/tradesieve.contracts.v1.json")
    assert isinstance(registry, dict)
    definitions = registry["$defs"]
    pending = [
        "SourceSnapshotListing",
        "SourceSnapshotDetail",
        "SourceSnapshotHistory",
    ]
    closure: dict[str, object] = {}
    while pending:
        name = pending.pop()
        if name in closure:
            continue
        schema = definitions[name]
        closure[name] = schema

        def collect_refs(value: object) -> None:
            if isinstance(value, dict):
                reference = value.get("$ref")
                if isinstance(reference, str) and reference.startswith("#/$defs/"):
                    pending.append(reference.removeprefix("#/$defs/"))
                for nested in value.values():
                    collect_refs(nested)
            elif isinstance(value, list):
                for nested in value:
                    collect_refs(nested)

        collect_refs(schema)

    serialized = json.dumps(closure, sort_keys=True).lower()
    for forbidden in (
        "raw_bytes",
        "original_name",
        "native_locator",
        "native_value",
        "normalized_value",
        "record_locator",
        "assertion_locator",
        "records",
        "assertions",
        "source_locator",
        "private_payload",
        "credential_secret_ref",
        "licence_summary",
        "contractual_constraints",
        "legal_scope",
        "data_scope",
        "access_method",
        "owner",
        "responsible_operator",
        "jurisdiction",
        "reason",
        "actor_id",
        "actor_subject",
        "client_id",
        "command_event_id",
        "authorization_event_id",
        "lifecycle_event_id",
        "path",
        "error",
        "message",
        "traceback",
        "exception",
    ):
        assert f'"{forbidden}"' not in serialized
    history_schema = closure["SourceSnapshotHistoryRecord"]
    assert isinstance(history_schema, dict)
    history_properties = history_schema["properties"]
    assert isinstance(history_properties, dict)
    assert "actor_type" in history_properties
    actor_type_schema = closure["LifecycleActorType"]
    assert isinstance(actor_type_schema, dict)
    assert actor_type_schema["enum"] == ["HUMAN", "SERVICE", "AGENT"]


def test_official_preview_adds_one_explicit_rest_surface_without_webhook_drift() -> (
    None
):
    openapi = load_json(ROOT / "api/openapi/tradesieve.v1.json")
    assert isinstance(openapi, dict)

    def canonical_hash(value: object) -> str:
        payload = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        return hashlib.sha256(payload).hexdigest()

    assert len(openapi["paths"]) == 10
    assert (
        canonical_hash(openapi["paths"])
        == (
            "aa766127d86dd1871e6554b1ab931ec8e5c9336456202fb9a84889707c06f2cf"  # pragma: allowlist secret
        )
    )
    assert (
        canonical_hash(openapi["webhooks"])
        == (
            "5fda7869ed3461c00b413a7a3c375207489a1afaf8802d30e8a983786b5e56f4"  # pragma: allowlist secret
        )
    )
    assert all(
        "snapshot" not in path.lower() and "raw" not in path.lower()
        for path in openapi["paths"]
    )


def test_official_preview_contract_and_example_are_generated_and_redacted() -> None:
    openapi = load_json(ROOT / "api/openapi/tradesieve.v1.json")
    registry = load_json(ROOT / "api/schemas/tradesieve.contracts.v1.json")
    example = load_json(ROOT / "examples/requests/official-screening.json")
    assert isinstance(openapi, dict)
    assert isinstance(registry, dict)

    parsed = OfficialScreeningRequest.model_validate(example)
    assert parsed.party_names[0].name == "JOINT STOCK COMPANY SOVCOMFLOT"
    operation = openapi["paths"]["/v1/official-screenings"]["post"]
    assert operation["security"] == [{"OfficialScreeningBearer": []}]
    assert operation["x-tradesieve-implementation-status"] == "technical-preview"
    assert operation["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/OfficialScreeningRequest"
    }
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/OfficialScreeningResult"
    }
    assert openapi["components"]["securitySchemes"]["OfficialScreeningBearer"] == {
        "type": "http",
        "description": "Deployment-scoped workload bearer token.",
        "scheme": "bearer",
    }
    assert registry["x-tradesieve-entrypoints"][-3:] == [
        OfficialScreeningRequest.__name__,
        OfficialScreeningResult.__name__,
        OfficialSourceRefreshResult.__name__,
    ]
    result_properties = registry["$defs"]["OfficialScreeningResult"]["properties"]
    assert "ofac" in result_properties
    assert result_properties["automatic_clearance"]["const"] is False
    ofac_identifier_evidence = registry["$defs"]["OfficialOfacIdentifierEvidence"]
    assert "identifier_uid" in ofac_identifier_evidence["properties"]
    assert "identifier_value" not in ofac_identifier_evidence["properties"]
    ofac_name_evidence = registry["$defs"]["OfficialOfacNameEvidence"]
    assert "source_name" not in ofac_name_evidence["properties"]


def test_redocly_ignore_is_limited_to_pathless_schema_roots() -> None:
    assert (ROOT / ".redocly.lint-ignore.yaml").read_text(encoding="utf-8") == (
        "api/openapi/tradesieve.v1.json:\n"
        "  no-unused-components:\n"
        "    - '#/components/schemas/SourceSnapshotDetail'\n"
        "    - '#/components/schemas/SourceSnapshotHistory'\n"
        "    - '#/components/schemas/SourceSnapshotListing'\n"
        "    - '#/components/schemas/OfficialSourceRefreshResult'\n"
    )


def test_generator_is_byte_deterministic_and_check_mode_detects_temp_drift(
    tmp_path: Path,
) -> None:
    root = tmp_path / "contract-root"
    shutil.copytree(ROOT / "api", root / "api")
    shutil.copytree(ROOT / "examples", root / "examples")
    env = {**os.environ, "TRADESIEVE_CONTRACT_ROOT": str(root)}
    command = [sys.executable, str(GENERATOR)]

    first = subprocess.run(
        command, env=env, capture_output=True, text=True, check=False
    )
    assert first.returncode == 0, first.stdout + first.stderr
    first_bytes = {
        path.relative_to(root): path.read_bytes()
        for path in sorted(root.rglob("*.json"))
    }

    second = subprocess.run(
        command, env=env, capture_output=True, text=True, check=False
    )
    assert second.returncode == 0, second.stdout + second.stderr
    second_bytes = {
        path.relative_to(root): path.read_bytes()
        for path in sorted(root.rglob("*.json"))
    }
    assert second_bytes == first_bytes

    stale_path = root / "examples/requests/customer-onboarding.incomplete.json"
    stale_path.write_text("{}\n", encoding="utf-8")
    check = subprocess.run(
        [*command, "--check"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert check.returncode == 1
    assert "customer-onboarding.incomplete.json" in check.stdout
