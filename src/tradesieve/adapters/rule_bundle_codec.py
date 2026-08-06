"""Strict JSON codecs for private immutable rule-bundle persistence payloads."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from tradesieve.domain.rule_bundle import (
    MAX_CANONICAL_CONTENT_BYTES,
    CanonicalFactPath,
    CitationKind,
    DataCompletenessPresenceSpec,
    EffectiveWindow,
    InternalPolicyCitation,
    LegalNexusPresenceSpec,
    OfficialSourceProvisionCitation,
    RescreenImpact,
    RuleAction,
    RuleActivity,
    RuleBundleRef,
    RuleBundleVersion,
    RuleEvaluationOutcome,
    RuleEvaluatorKind,
    RuleFixture,
    RuleScope,
    RuleVersion,
    rule_bundle_ref,
)

PAYLOAD_SCHEMA_VERSION = "1.0.0"


def encode_bundle_payload(bundle: RuleBundleVersion) -> dict[str, Any]:
    """Encode full private bundle content using an explicit versioned document."""

    if not isinstance(bundle, RuleBundleVersion):
        raise ValueError("bundle must be typed")
    reference = rule_bundle_ref(bundle)
    payload: dict[str, Any] = {
        "schema_version": PAYLOAD_SCHEMA_VERSION,
        "tenant_id": bundle.tenant_id,
        "deployment_id": bundle.deployment_id,
        "rule_set_id": bundle.rule_set_id,
        "bundle_id": bundle.bundle_id,
        "version": bundle.version,
        "content_hash": reference.content_hash,
        "owner": bundle.owner,
        "effective_window": _encode_window(bundle.effective_window),
        "authored_by": bundle.authored_by,
        "rules": [_encode_rule(rule) for rule in bundle.rules],
        "internal_notes": bundle.internal_notes,
    }
    _require_json_size(payload, "bundle payload")
    return payload


def decode_bundle_payload(
    payload: object,
    *,
    tenant_id: str,
    deployment_id: str,
    rule_set_id: str,
    bundle_id: str,
    version: str,
    content_hash: str,
) -> RuleBundleVersion:
    """Strictly reconstruct and hash-check a stored private bundle document."""

    payload = _load_json_value(payload, "bundle payload")
    document = _require_object(
        payload,
        {
            "schema_version",
            "tenant_id",
            "deployment_id",
            "rule_set_id",
            "bundle_id",
            "version",
            "content_hash",
            "owner",
            "effective_window",
            "authored_by",
            "rules",
            "internal_notes",
        },
        "bundle payload",
    )
    _require_json_size(document, "bundle payload")
    if _require_string(document["schema_version"], "schema_version") != (
        PAYLOAD_SCHEMA_VERSION
    ):
        raise ValueError("unsupported bundle payload schema")
    expected_identity = (
        tenant_id,
        deployment_id,
        rule_set_id,
        bundle_id,
        version,
        content_hash,
    )
    payload_identity = tuple(
        _require_string(document[key], key)
        for key in (
            "tenant_id",
            "deployment_id",
            "rule_set_id",
            "bundle_id",
            "version",
            "content_hash",
        )
    )
    if payload_identity != expected_identity:
        raise ValueError("bundle payload identity does not match its row")
    rules = tuple(
        _decode_rule(item) for item in _require_list(document["rules"], "rules")
    )
    bundle = RuleBundleVersion(
        tenant_id=tenant_id,
        deployment_id=deployment_id,
        rule_set_id=rule_set_id,
        bundle_id=bundle_id,
        version=version,
        owner=_optional_string(document["owner"], "owner"),
        effective_window=_decode_window(document["effective_window"]),
        authored_by=_require_string(document["authored_by"], "authored_by"),
        rules=rules,
        internal_notes=_optional_string(document["internal_notes"], "internal_notes"),
    )
    if rule_bundle_ref(bundle).content_hash != content_hash:
        raise ValueError("bundle payload content hash mismatch")
    return bundle


def encode_rescreen_impact(impact: RescreenImpact | None) -> dict[str, Any] | None:
    if impact is None:
        return None
    if not isinstance(impact, RescreenImpact):
        raise ValueError("rescreen impact must be typed")
    payload: dict[str, Any] = {
        "schema_version": PAYLOAD_SCHEMA_VERSION,
        "previous_bundle": _encode_reference(impact.previous_bundle),
        "new_bundle": _encode_reference(impact.new_bundle),
        "added_rule_ids": list(impact.added_rule_ids),
        "removed_rule_ids": list(impact.removed_rule_ids),
        "changed_rule_ids": list(impact.changed_rule_ids),
        "affected_fact_paths": [item.value for item in impact.affected_fact_paths],
        "rescreen_required": impact.rescreen_required,
    }
    _require_json_size(payload, "rescreen impact")
    return payload


def decode_rescreen_impact(payload: object) -> RescreenImpact:
    payload = _load_json_value(payload, "rescreen impact")
    document = _require_object(
        payload,
        {
            "schema_version",
            "previous_bundle",
            "new_bundle",
            "added_rule_ids",
            "removed_rule_ids",
            "changed_rule_ids",
            "affected_fact_paths",
            "rescreen_required",
        },
        "rescreen impact",
    )
    _require_json_size(document, "rescreen impact")
    if _require_string(document["schema_version"], "schema_version") != (
        PAYLOAD_SCHEMA_VERSION
    ):
        raise ValueError("unsupported rescreen impact schema")
    return RescreenImpact(
        previous_bundle=_decode_optional_reference(document["previous_bundle"]),
        new_bundle=_decode_optional_reference(document["new_bundle"]),
        added_rule_ids=_string_tuple(document["added_rule_ids"], "added_rule_ids"),
        removed_rule_ids=_string_tuple(
            document["removed_rule_ids"], "removed_rule_ids"
        ),
        changed_rule_ids=_string_tuple(
            document["changed_rule_ids"], "changed_rule_ids"
        ),
        affected_fact_paths=_enum_tuple(
            document["affected_fact_paths"],
            CanonicalFactPath,
            "affected_fact_paths",
        ),
        rescreen_required=_require_bool(
            document["rescreen_required"], "rescreen_required"
        ),
    )


def _encode_rule(rule: RuleVersion) -> dict[str, Any]:
    return {
        "rule_id": rule.rule_id,
        "version": rule.version,
        "content_hash": rule.content_hash(),
        "kind": rule.kind.value,
        "owner": rule.owner,
        "effective_window": _encode_window(rule.effective_window),
        "scope": {
            "proposed_actions": [item.value for item in rule.scope.proposed_actions],
            "activities": [item.value for item in rule.scope.activities],
        },
        "evaluator": _encode_evaluator(rule),
        "citations": [_encode_citation(item) for item in rule.citations],
        "fixtures": [_encode_fixture(item) for item in rule.fixtures],
        "internal_notes": rule.internal_notes,
    }


def _decode_rule(payload: object) -> RuleVersion:
    document = _require_object(
        payload,
        {
            "rule_id",
            "version",
            "content_hash",
            "kind",
            "owner",
            "effective_window",
            "scope",
            "evaluator",
            "citations",
            "fixtures",
            "internal_notes",
        },
        "rule",
    )
    scope = _require_object(
        document["scope"], {"proposed_actions", "activities"}, "rule scope"
    )
    evaluator = _decode_evaluator(document["evaluator"])
    rule = RuleVersion(
        rule_id=_require_string(document["rule_id"], "rule_id"),
        version=_require_string(document["version"], "rule version"),
        kind=RuleEvaluatorKind(_require_string(document["kind"], "rule kind")),
        owner=_optional_string(document["owner"], "rule owner"),
        effective_window=_decode_window(document["effective_window"]),
        scope=RuleScope(
            _enum_tuple(scope["proposed_actions"], RuleAction, "proposed_actions"),
            _enum_tuple(scope["activities"], RuleActivity, "activities"),
        ),
        evaluator=evaluator,
        citations=tuple(
            _decode_citation(item)
            for item in _require_list(document["citations"], "citations")
        ),
        fixtures=tuple(
            _decode_fixture(item)
            for item in _require_list(document["fixtures"], "fixtures")
        ),
        internal_notes=_optional_string(document["internal_notes"], "internal_notes"),
    )
    stored_hash = _require_string(document["content_hash"], "rule content hash")
    if rule.content_hash() != stored_hash:
        raise ValueError("rule payload content hash mismatch")
    return rule


def _encode_evaluator(rule: RuleVersion) -> dict[str, Any]:
    if isinstance(rule.evaluator, LegalNexusPresenceSpec):
        return {"kind": rule.evaluator.kind.value}
    return {
        "kind": rule.evaluator.kind.value,
        "required_fact_paths": [
            item.value for item in rule.evaluator.required_fact_paths
        ],
    }


def _decode_evaluator(
    payload: object,
) -> LegalNexusPresenceSpec | DataCompletenessPresenceSpec:
    if not isinstance(payload, dict):
        raise ValueError("evaluator must be a JSON object")
    kind = RuleEvaluatorKind(_require_string(payload.get("kind"), "evaluator kind"))
    if kind is RuleEvaluatorKind.LEGAL_NEXUS_PRESENCE:
        _require_exact_keys(payload, {"kind"}, "legal-nexus evaluator")
        return LegalNexusPresenceSpec()
    _require_exact_keys(
        payload,
        {"kind", "required_fact_paths"},
        "data-completeness evaluator",
    )
    return DataCompletenessPresenceSpec(
        _enum_tuple(
            payload["required_fact_paths"],
            CanonicalFactPath,
            "required_fact_paths",
        )
    )


def _encode_citation(
    citation: InternalPolicyCitation | OfficialSourceProvisionCitation,
) -> dict[str, Any]:
    if isinstance(citation, InternalPolicyCitation):
        return {
            "kind": citation.kind.value,
            "citation_ref": citation.citation_ref,
            "policy_id": citation.policy_id,
            "policy_version": citation.policy_version,
            "provision_locator": citation.provision_locator,
            "private_policy_text": citation.private_policy_text,
        }
    return {
        "kind": citation.kind.value,
        "citation_ref": citation.citation_ref,
        "source_id": citation.source_id,
        "snapshot_id": citation.snapshot_id,
        "snapshot_content_hash": citation.snapshot_content_hash,
        "provision_locator": citation.provision_locator,
    }


def _decode_citation(
    payload: object,
) -> InternalPolicyCitation | OfficialSourceProvisionCitation:
    if not isinstance(payload, dict):
        raise ValueError("citation must be a JSON object")
    kind = CitationKind(_require_string(payload.get("kind"), "citation kind"))
    if kind is CitationKind.INTERNAL_POLICY:
        _require_exact_keys(
            payload,
            {
                "kind",
                "citation_ref",
                "policy_id",
                "policy_version",
                "provision_locator",
                "private_policy_text",
            },
            "internal citation",
        )
        return InternalPolicyCitation(
            citation_ref=_require_string(payload["citation_ref"], "citation_ref"),
            policy_id=_require_string(payload["policy_id"], "policy_id"),
            policy_version=_require_string(payload["policy_version"], "policy_version"),
            provision_locator=_require_string(
                payload["provision_locator"], "provision_locator"
            ),
            private_policy_text=_require_string(
                payload["private_policy_text"], "private_policy_text"
            ),
        )
    _require_exact_keys(
        payload,
        {
            "kind",
            "citation_ref",
            "source_id",
            "snapshot_id",
            "snapshot_content_hash",
            "provision_locator",
        },
        "official citation",
    )
    return OfficialSourceProvisionCitation(
        citation_ref=_require_string(payload["citation_ref"], "citation_ref"),
        source_id=_require_string(payload["source_id"], "source_id"),
        snapshot_id=_require_string(payload["snapshot_id"], "snapshot_id"),
        snapshot_content_hash=_require_string(
            payload["snapshot_content_hash"], "snapshot_content_hash"
        ),
        provision_locator=_require_string(
            payload["provision_locator"], "provision_locator"
        ),
    )


def _encode_fixture(fixture: RuleFixture) -> dict[str, Any]:
    return {
        "fixture_id": fixture.fixture_id,
        "proposed_action": fixture.proposed_action.value,
        "activities": [item.value for item in fixture.activities],
        "present_fact_paths": [item.value for item in fixture.present_fact_paths],
        "expected_outcome": fixture.expected_outcome.value,
        "expected_missing_fact_paths": [
            item.value for item in fixture.expected_missing_fact_paths
        ],
    }


def _decode_fixture(payload: object) -> RuleFixture:
    document = _require_object(
        payload,
        {
            "fixture_id",
            "proposed_action",
            "activities",
            "present_fact_paths",
            "expected_outcome",
            "expected_missing_fact_paths",
        },
        "fixture",
    )
    return RuleFixture(
        fixture_id=_require_string(document["fixture_id"], "fixture_id"),
        proposed_action=RuleAction(
            _require_string(document["proposed_action"], "proposed_action")
        ),
        activities=_enum_tuple(document["activities"], RuleActivity, "activities"),
        present_fact_paths=_enum_tuple(
            document["present_fact_paths"],
            CanonicalFactPath,
            "present_fact_paths",
        ),
        expected_outcome=RuleEvaluationOutcome(
            _require_string(document["expected_outcome"], "expected_outcome")
        ),
        expected_missing_fact_paths=_enum_tuple(
            document["expected_missing_fact_paths"],
            CanonicalFactPath,
            "expected_missing_fact_paths",
        ),
    )


def _encode_window(window: EffectiveWindow) -> dict[str, str | None]:
    return {
        "effective_from": _datetime_to_text(window.effective_from),
        "effective_until": (
            _datetime_to_text(window.effective_until)
            if window.effective_until is not None
            else None
        ),
    }


def _decode_window(payload: object) -> EffectiveWindow:
    document = _require_object(
        payload, {"effective_from", "effective_until"}, "effective window"
    )
    return EffectiveWindow(
        _datetime_from_text(document["effective_from"], "effective_from"),
        (
            None
            if document["effective_until"] is None
            else _datetime_from_text(document["effective_until"], "effective_until")
        ),
    )


def _encode_reference(reference: RuleBundleRef | None) -> dict[str, str] | None:
    if reference is None:
        return None
    return {
        "bundle_id": reference.bundle_id,
        "version": reference.version,
        "content_hash": reference.content_hash,
    }


def _decode_optional_reference(payload: object) -> RuleBundleRef | None:
    if payload is None:
        return None
    document = _require_object(
        payload, {"bundle_id", "version", "content_hash"}, "bundle reference"
    )
    return RuleBundleRef(
        _require_string(document["bundle_id"], "bundle_id"),
        _require_string(document["version"], "version"),
        _require_string(document["content_hash"], "content_hash"),
    )


def _require_object(
    value: object, expected_keys: set[str], field: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a JSON object")
    _require_exact_keys(value, expected_keys, field)
    return value


def _require_exact_keys(
    value: dict[object, object], expected_keys: set[str], field: str
) -> None:
    if set(value) != expected_keys or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field} has unexpected or missing keys")


def _require_list(value: object, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a JSON array")
    return value


def _require_string(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def _optional_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _require_string(value, field)


def _require_bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    return tuple(_require_string(item, field) for item in _require_list(value, field))


def _enum_tuple[E](value: object, enum_type: type[E], field: str) -> tuple[E, ...]:
    return tuple(
        enum_type(_require_string(item, field))  # type: ignore[call-arg]
        for item in _require_list(value, field)
    )


def _datetime_to_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("persisted datetime must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _datetime_from_text(value: object, field: str) -> datetime:
    text = _require_string(value, field)
    if not text.endswith("Z"):
        raise ValueError(f"{field} must be a canonical UTC datetime")
    try:
        parsed = datetime.fromisoformat(text.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{field} must be a canonical UTC datetime") from exc
    if _datetime_to_text(parsed) != text:
        raise ValueError(f"{field} must be a canonical UTC datetime")
    return parsed


def _require_json_size(payload: object, field: str) -> None:
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be JSON serializable") from exc
    if len(encoded) > MAX_CANONICAL_CONTENT_BYTES:
        raise ValueError(f"{field} exceeds the encoded size limit")


def _load_json_value(value: object, field: str) -> object:
    if not isinstance(value, str):
        return value

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, member in pairs:
            if key in result:
                raise ValueError(f"{field} contains a duplicate key")
            result[key] = member
        return result

    try:
        return json.loads(
            value,
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _: (_ for _ in ()).throw(
                ValueError(f"{field} contains a non-finite number")
            ),
        )
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"{field} must contain valid JSON") from exc
