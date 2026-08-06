#!/usr/bin/env python3
"""Generate reviewed contract artifacts from canonical Pydantic application models."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from pydantic.json_schema import models_json_schema

from tradesieve.application.contract_examples import (
    event_examples,
    incomplete_customer_onboarding_request,
    structurally_invalid_request,
    transaction_review_required_result,
    transaction_screening_request,
)
from tradesieve.application.contracts import CONTRACT_MODELS

ROOT = Path(
    os.environ.get("TRADESIEVE_CONTRACT_ROOT", Path(__file__).resolve().parents[1])
).resolve()
OPENAPI_PATH = ROOT / "api/openapi/tradesieve.v1.json"
SCHEMA_PATH = ROOT / "api/schemas/tradesieve.contracts.v1.json"
REQUEST_PATH = ROOT / "examples/requests/transaction-screening.json"
INCOMPLETE_REQUEST_PATH = ROOT / "examples/requests/customer-onboarding.incomplete.json"
INVALID_REQUEST_PATH = (
    ROOT / "examples/requests/invalid/transaction-screening.structural-error.json"
)
RESPONSE_PATH = ROOT / "examples/responses/transaction-screening.review-required.json"
EVENT_PATHS = {
    "screening.completed": ROOT / "examples/events/screening.completed.json",
    "case.state_changed": ROOT / "examples/events/case.state-changed.json",
    "human_decision.recorded": (ROOT / "examples/events/human-decision.recorded.json"),
}


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def generated_schemas(ref_template: str) -> dict[str, object]:
    _, document = models_json_schema(
        [(model, "validation") for model in CONTRACT_MODELS],
        ref_template=ref_template,
    )
    definitions = document.get("$defs")
    if not isinstance(definitions, dict):
        raise RuntimeError("Pydantic did not generate a $defs schema registry")
    return definitions


def render_artifacts() -> dict[Path, bytes]:
    request = transaction_screening_request()
    incomplete_request = incomplete_customer_onboarding_request()
    invalid_request = structurally_invalid_request(incomplete_request)
    response = transaction_review_required_result(request)
    events = event_examples(response)

    openapi = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    components = openapi["components"]
    components["schemas"] = generated_schemas("#/components/schemas/{model}")
    components["examples"] = {
        "TransactionScreeningRequest": {
            "summary": "Complete synthetic transaction intake",
            "value": request.model_dump(mode="json"),
        },
        "IncompleteCustomerOnboardingRequest": {
            "summary": (
                "Structurally valid intake whose missing facts become findings, not 422"
            ),
            "value": incomplete_request.model_dump(mode="json"),
        },
        "TransactionReviewRequiredResult": {
            "summary": "Conservative hold and review-required result",
            "value": response.model_dump(mode="json"),
        },
        "ScreeningCompletedEvent": {
            "summary": "Minimal screening completion notification",
            "value": events["screening.completed"].model_dump(mode="json"),
        },
        "CaseStateChangedEvent": {
            "summary": "Minimal case state-change notification",
            "value": events["case.state_changed"].model_dump(mode="json"),
        },
        "HumanDecisionRecordedEvent": {
            "summary": "Minimal human-decision notification",
            "value": events["human_decision.recorded"].model_dump(mode="json"),
        },
    }
    screening_content = openapi["paths"]["/v1/screenings"]["post"]["requestBody"][
        "content"
    ]["application/json"]
    screening_content["examples"] = {
        "transaction": {"$ref": "#/components/examples/TransactionScreeningRequest"},
        "incomplete_customer_onboarding": {
            "$ref": "#/components/examples/IncompleteCustomerOnboardingRequest"
        },
    }
    response_content = openapi["paths"]["/v1/screenings"]["post"]["responses"]["201"][
        "content"
    ]["application/json"]
    response_content["examples"] = {
        "review_required": {
            "$ref": "#/components/examples/TransactionReviewRequiredResult"
        }
    }
    openapi["webhooks"] = {
        "tradeSieveEvent": {
            "post": {
                "operationId": "receiveTradeSieveEvent",
                "summary": "Receive a signed minimal TradeSieve event",
                "description": (
                    "Consumers verify the delivery signature, deduplicate event_id, "
                    "and re-read the case before irreversible release."
                ),
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/EventEnvelope"},
                            "examples": {
                                "screening_completed": {
                                    "$ref": (
                                        "#/components/examples/ScreeningCompletedEvent"
                                    )
                                },
                                "case_state_changed": {
                                    "$ref": (
                                        "#/components/examples/CaseStateChangedEvent"
                                    )
                                },
                                "human_decision_recorded": {
                                    "$ref": (
                                        "#/components/examples/"
                                        "HumanDecisionRecordedEvent"
                                    )
                                },
                            },
                        }
                    },
                },
                "responses": {
                    "202": {"description": "Event accepted for idempotent processing"},
                    "400": {"description": "Invalid event envelope"},
                },
            }
        }
    }
    openapi["x-tradesieve-canonical-source"] = {
        "model": "tradesieve.application.contracts",
        "generator": "scripts/generate_contract.py",
        "drift_check": "uv run --locked python scripts/generate_contract.py --check",
    }

    shared_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://tradesieve.invalid/schemas/tradesieve.contracts.v1.json",
        "title": "TradeSieve canonical application contract registry",
        "description": (
            "Generated registry for REST, CLI, MCP, events, and webhooks. Consumers "
            "select a named schema from $defs; do not edit this artifact."
        ),
        "$defs": generated_schemas("#/$defs/{model}"),
        "x-tradesieve-entrypoints": [
            "ScreeningRequest",
            "ScreeningResult",
            "Case",
            "HumanDecisionRequest",
            "EventEnvelope",
        ],
    }

    artifacts = {
        OPENAPI_PATH: json_bytes(openapi),
        SCHEMA_PATH: json_bytes(shared_schema),
        REQUEST_PATH: json_bytes(request.model_dump(mode="json")),
        INCOMPLETE_REQUEST_PATH: json_bytes(incomplete_request.model_dump(mode="json")),
        INVALID_REQUEST_PATH: json_bytes(invalid_request),
        RESPONSE_PATH: json_bytes(response.model_dump(mode="json")),
    }
    artifacts.update(
        {
            EVENT_PATHS[event_type]: json_bytes(event.model_dump(mode="json"))
            for event_type, event in events.items()
        }
    )
    return artifacts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if committed artifacts differ from deterministic generation.",
    )
    check = parser.parse_args().check
    artifacts = render_artifacts()

    stale: list[Path] = []
    for path, expected in artifacts.items():
        if check:
            if not path.is_file() or path.read_bytes() != expected:
                stale.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(expected)

    if stale:
        print("Generated contract artifacts are stale:")
        for path in stale:
            print(f"- {path.relative_to(ROOT)}")
        print("Run: uv run --locked python scripts/generate_contract.py")
        return 1

    action = "verified" if check else "generated"
    print(f"Canonical contract artifacts {action} ({len(artifacts)} files).")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as a repository command
    sys.exit(main())
