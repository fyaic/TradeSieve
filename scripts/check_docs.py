#!/usr/bin/env python3
"""Small dependency-free repository documentation checks."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
IGNORED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "build",
    "dist",
}
REQUIRED = (
    "AGENTS.md",
    ".dockerignore",
    ".env.example",
    "Dockerfile",
    "README.md",
    "alembic.ini",
    "compose.yaml",
    "docs/README.md",
    "docs/requirements/original-request.md",
    "docs/requirements/problem-and-scope.md",
    "docs/product/service-definition.md",
    "docs/product/personas-and-journeys.md",
    "docs/product/requirements.md",
    "docs/product/mvp-scope.md",
    "docs/architecture/initial-system-shape.md",
    "docs/architecture/mvp-service-architecture.md",
    "docs/architecture/domain-model.md",
    "docs/architecture/integration-design.md",
    "docs/architecture/agent-interface-principles.md",
    "docs/architecture/security-and-trust.md",
    "docs/architecture/implementation-blueprint.md",
    "docs/decisions/0001-independent-control-plane.md",
    "docs/decisions/0002-human-clearance-only.md",
    "docs/decisions/0003-one-contract-multiple-interfaces.md",
    "docs/decisions/0004-phase-1-modular-python-service.md",
    "docs/decisions/0005-pydantic-canonical-contract-source.md",
    "docs/delivery/agile-operating-model.md",
    "docs/delivery/phase-1-plan.md",
    "docs/delivery/phase-1-backlog.md",
    "docs/getting-started/mvp-user-experience.md",
    "docs/getting-started/docker-reference.md",
    "api/openapi/tradesieve.v1.json",
    "examples/requests/transaction-screening.json",
    "examples/responses/transaction-screening.review-required.json",
    "research/sources.yaml",
)
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


def reject_duplicate_json_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json_text(text: str):
    return json.loads(text, object_pairs_hook=reject_duplicate_json_keys)


def check_local_links(path: Path, text: str) -> list[str]:
    errors: list[str] = []
    for raw_target in MARKDOWN_LINK.findall(text):
        target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
        if not target or target.startswith(("#", "http://", "https://", "mailto:")):
            continue
        file_part = unquote(target.split("#", 1)[0])
        resolved = (path.parent / file_part).resolve()
        try:
            resolved.relative_to(ROOT)
        except ValueError:
            errors.append(
                f"{path.relative_to(ROOT)}: link escapes repository: {target}"
            )
            continue
        if not resolved.exists():
            errors.append(f"{path.relative_to(ROOT)}: broken local link: {target}")
    return errors


def resolve_ref(document: object, ref: str) -> object:
    if not ref.startswith("#/"):
        raise ValueError(f"only internal references are supported: {ref}")
    current = document
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            raise ValueError(f"unresolved reference: {ref}")
        current = current[part]
    return current


def iter_refs(value: object):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "$ref" and isinstance(item, str):
                yield item
            else:
                yield from iter_refs(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_refs(item)


def validate_instance(
    instance: object, schema: object, document: dict, path: str
) -> list[str]:
    if not isinstance(schema, dict):
        return [f"{path}: schema is not an object"]
    if "$ref" in schema:
        try:
            resolved = resolve_ref(document, schema["$ref"])
        except ValueError as exc:
            return [f"{path}: {exc}"]
        return validate_instance(instance, resolved, document, path)

    if "oneOf" in schema:
        alternatives = [
            validate_instance(instance, candidate, document, path)
            for candidate in schema["oneOf"]
        ]
        if not any(not result for result in alternatives):
            return [f"{path}: does not match any oneOf alternative"]
        return []

    errors: list[str] = []
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: value {instance!r} is not in enum")

    expected = schema.get("type")
    expected_types = expected if isinstance(expected, list) else [expected]
    type_map = {
        "object": dict,
        "array": list,
        "string": str,
        "boolean": bool,
        "null": type(None),
    }

    def matches_type(type_name: str) -> bool:
        if type_name == "number":
            return isinstance(instance, (int, float)) and not isinstance(instance, bool)
        if type_name == "integer":
            return isinstance(instance, int) and not isinstance(instance, bool)
        return type_name in type_map and isinstance(instance, type_map[type_name])

    if expected and not any(matches_type(item) for item in expected_types):
        return [f"{path}: expected type {expected!r}, got {type(instance).__name__}"]

    if isinstance(instance, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: missing required property {key!r}")
        if schema.get("additionalProperties") is False:
            for key in instance:
                if key not in properties:
                    errors.append(f"{path}: unexpected property {key!r}")
        for key, value in instance.items():
            if key in properties:
                errors.extend(
                    validate_instance(value, properties[key], document, f"{path}.{key}")
                )
    elif isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            errors.append(f"{path}: expected at least {schema['minItems']} items")
        if "items" in schema:
            for index, item in enumerate(instance):
                errors.extend(
                    validate_instance(
                        item, schema["items"], document, f"{path}[{index}]"
                    )
                )
    elif isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            errors.append(f"{path}: shorter than minLength")
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append(f"{path}: does not match pattern {schema['pattern']!r}")
    elif isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: must be greater than {schema['exclusiveMinimum']}")

    return errors


def check_openapi() -> list[str]:
    errors: list[str] = []
    spec_path = ROOT / "api/openapi/tradesieve.v1.json"
    request_path = ROOT / "examples/requests/transaction-screening.json"
    response_path = (
        ROOT / "examples/responses/transaction-screening.review-required.json"
    )
    try:
        spec = load_json_text(spec_path.read_text(encoding="utf-8"))
        request = load_json_text(request_path.read_text(encoding="utf-8"))
        response = load_json_text(response_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return [f"OpenAPI/example load failed: {exc}"]

    if spec.get("openapi") != "3.1.0":
        errors.append("api/openapi/tradesieve.v1.json: expected OpenAPI 3.1.0")
    operation_ids: list[str] = []
    for path_item in spec.get("paths", {}).values():
        if not isinstance(path_item, dict):
            continue
        for operation in path_item.values():
            if isinstance(operation, dict) and "operationId" in operation:
                operation_ids.append(operation["operationId"])
    duplicates = sorted(
        {item for item in operation_ids if operation_ids.count(item) > 1}
    )
    if duplicates:
        errors.append(f"OpenAPI duplicate operationId values: {duplicates}")

    for ref in iter_refs(spec):
        try:
            resolve_ref(spec, ref)
        except ValueError as exc:
            errors.append(f"api/openapi/tradesieve.v1.json: {exc}")

    request_schema = spec["components"]["schemas"]["ScreeningRequest"]
    response_schema = spec["components"]["schemas"]["ScreeningResult"]
    errors.extend(validate_instance(request, request_schema, spec, "request example"))
    errors.extend(
        validate_instance(response, response_schema, spec, "response example")
    )

    for numeric_type in ("number", "integer"):
        if not validate_instance(True, {"type": numeric_type}, spec, "boolean fixture"):
            errors.append(
                f"OpenAPI validator regression: boolean accepted as {numeric_type}"
            )

    closed_no_action_request = {
        "decision": "CLOSED_NO_ACTION",
        "scope": {
            "proposed_action": "QUOTE_RELEASE",
            "external_object": {
                "system": "synthetic-crm",
                "object_type": "QUOTE",
                "object_id": "demo-withdrawn-001",
                "object_version": "1",
            },
        },
        "rationale": "Synthetic proposal was withdrawn by the requester.",
        "resolved_finding_ids": [],
        "evidence_refs": [],
        "expires_at": None,
    }
    errors.extend(
        validate_instance(
            closed_no_action_request,
            spec["components"]["schemas"]["HumanDecisionRequest"],
            spec,
            "CLOSED_NO_ACTION contract fixture",
        )
    )
    if "NOT_APPLICABLE" not in spec["components"]["schemas"]["Signal"]["enum"]:
        errors.append("OpenAPI signal contract lacks CLOSED_NO_ACTION representation")

    required_decision_fields = {
        "rationale",
        "resolved_finding_ids",
        "evidence_refs",
        "version_set",
        "reviewer_id",
        "reviewer_role",
        "recorded_at",
    }
    actual_decision_fields = set(
        spec["components"]["schemas"]["HumanDecision"].get("required", [])
    )
    missing_decision_fields = sorted(required_decision_fields - actual_decision_fields)
    if missing_decision_fields:
        errors.append(
            "OpenAPI human decision record lacks required provenance fields: "
            f"{missing_decision_fields}"
        )

    proposed_action_ref = {"$ref": "#/components/schemas/ProposedAction"}
    screening_action = spec["components"]["schemas"]["ScreeningRequest"]["properties"][
        "proposed_action"
    ]
    decision_action = spec["components"]["schemas"]["DecisionScope"]["properties"][
        "proposed_action"
    ]
    if (
        screening_action != proposed_action_ref
        or decision_action != proposed_action_ref
    ):
        errors.append("OpenAPI intake and decision scopes do not share ProposedAction")
    return errors


def main() -> int:
    errors: list[str] = []
    for required in REQUIRED:
        if not (ROOT / required).is_file():
            errors.append(f"missing required file: {required}")

    checked = 0
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or any(part in IGNORED_PARTS for part in path.parts):
            continue
        if path.suffix.lower() not in {".md", ".yaml", ".yml", ".json", ".py", ".sh"}:
            continue
        checked += 1
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(ROOT)
        if not text.endswith("\n"):
            errors.append(f"{relative}: missing final newline")
        left_marker = "<" * 7
        right_marker = ">" * 7
        if left_marker in text or right_marker in text:
            errors.append(f"{relative}: unresolved merge marker")
        if path.suffix.lower() == ".md":
            errors.extend(check_local_links(path, text))
        if path.suffix.lower() == ".json":
            try:
                load_json_text(text)
            except (json.JSONDecodeError, ValueError) as exc:
                errors.append(f"{relative}: invalid JSON: {exc}")

    registry = (ROOT / "research/sources.yaml").read_text(encoding="utf-8")
    source_ids = re.findall(r"^  - id: ([^\s]+)", registry, flags=re.MULTILINE)
    source_count = len(source_ids)
    if source_count < 10:
        errors.append("research/sources.yaml: expected at least 10 registered sources")
    duplicate_source_ids = sorted(
        {source_id for source_id in source_ids if source_ids.count(source_id) > 1}
    )
    if duplicate_source_ids:
        errors.append(
            f"research/sources.yaml: duplicate source IDs: {duplicate_source_ids}"
        )

    errors.extend(check_openapi())

    old_repository_url = "github.com/" + "veil-chow-fyaic" + "/TradeSieve"
    for path in sorted(ROOT.rglob("*")):
        if path.is_file() and not any(part in IGNORED_PARTS for part in path.parts):
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if old_repository_url in text:
                errors.append(
                    f"{path.relative_to(ROOT)}: stale pre-transfer repository URL"
                )

    if errors:
        print("Documentation checks failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print(
        f"Documentation checks passed ({checked} files, {source_count} registered sources)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
