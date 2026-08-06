"""Strict private rule-bundle persistence codec tests."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from tradesieve.adapters.rule_bundle_codec import (
    decode_bundle_payload,
    decode_rescreen_impact,
    encode_bundle_payload,
    encode_rescreen_impact,
)
from tradesieve.domain.rule_bundle import (
    CanonicalFactPath,
    DataCompletenessPresenceSpec,
    EffectiveWindow,
    InternalPolicyCitation,
    LegalNexusPresenceSpec,
    OfficialSourceProvisionCitation,
    RuleAction,
    RuleActivity,
    RuleBundleVersion,
    RuleEvaluationOutcome,
    RuleEvaluatorKind,
    RuleFixture,
    RuleScope,
    RuleVersion,
    calculate_rescreen_impact,
    rule_bundle_ref,
)

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
HASH_A = "sha256:" + "a" * 64


def complex_bundle(version: str = "1.0.0") -> RuleBundleVersion:
    internal = InternalPolicyCitation(
        "citation-a",
        "policy-1",
        "1.0.0",
        "section-1",
        "Private policy text retained only in persistence.",
    )
    official = OfficialSourceProvisionCitation(
        "citation-b", "source-1", "snapshot-1", HASH_A, "article-1"
    )
    legal = RuleVersion(
        rule_id="rule-a",
        version=version,
        kind=RuleEvaluatorKind.LEGAL_NEXUS_PRESENCE,
        owner=None,
        effective_window=EffectiveWindow(NOW - timedelta(days=1), None),
        scope=RuleScope(
            (RuleAction.PAYMENT, RuleAction.QUOTE_RELEASE),
            (RuleActivity.EXPORT, RuleActivity.SALE),
        ),
        evaluator=LegalNexusPresenceSpec(),
        citations=(internal, official),
        fixtures=(
            RuleFixture(
                "fixture-a",
                RuleAction.PAYMENT,
                (RuleActivity.EXPORT,),
                (),
                RuleEvaluationOutcome.MISSING_FACTS,
                (CanonicalFactPath.LEGAL_NEXUS,),
            ),
            RuleFixture(
                "fixture-b",
                RuleAction.QUOTE_RELEASE,
                (),
                (CanonicalFactPath.LEGAL_NEXUS,),
                RuleEvaluationOutcome.FACTS_PRESENT,
            ),
        ),
        internal_notes=None,
    )
    completeness = RuleVersion(
        rule_id="rule-b",
        version=version,
        kind=RuleEvaluatorKind.DATA_COMPLETENESS_PRESENCE,
        owner="rule-owner",
        effective_window=EffectiveWindow(
            NOW - timedelta(days=1), NOW + timedelta(days=1)
        ),
        scope=RuleScope((RuleAction.QUOTE_RELEASE,), ()),
        evaluator=DataCompletenessPresenceSpec(
            (CanonicalFactPath.GOODS, CanonicalFactPath.PARTIES)
        ),
        citations=(),
        fixtures=(),
        internal_notes="Private rule note.",
    )
    return RuleBundleVersion(
        tenant_id="tenant-1",
        deployment_id="demo",
        rule_set_id="ruleset-1",
        bundle_id="bundle-1",
        version=version,
        owner=None,
        effective_window=EffectiveWindow(NOW - timedelta(days=1), None),
        authored_by="author-1",
        rules=(legal, completeness),
        internal_notes="Private bundle note.",
    )


def decode(payload: object, bundle: RuleBundleVersion) -> RuleBundleVersion:
    reference = rule_bundle_ref(bundle)
    return decode_bundle_payload(
        payload,
        tenant_id=bundle.tenant_id,
        deployment_id=bundle.deployment_id,
        rule_set_id=bundle.rule_set_id,
        bundle_id=bundle.bundle_id,
        version=bundle.version,
        content_hash=reference.content_hash,
    )


def test_full_private_bundle_round_trips_from_jsonb_and_strict_json_text() -> None:
    bundle = complex_bundle()
    payload = encode_bundle_payload(bundle)
    assert decode(payload, bundle) == bundle
    assert decode(json.dumps(payload), bundle) == bundle
    serialized = json.dumps(payload)
    assert "Private policy text" in serialized
    assert "Private rule note" in serialized
    assert "Private bundle note" in serialized
    assert payload["rules"][0]["content_hash"] == bundle.rules[0].content_hash()


def test_bundle_decoder_rejects_unknown_keys_bad_types_enums_times_and_hashes() -> None:
    bundle = complex_bundle()
    base = encode_bundle_payload(bundle)
    corruptions: list[dict[str, object]] = []

    unknown = copy.deepcopy(base)
    unknown["unknown"] = True
    corruptions.append(unknown)
    nested_unknown = copy.deepcopy(base)
    nested_unknown["rules"][0]["scope"]["unknown"] = []
    corruptions.append(nested_unknown)
    wrong_array = copy.deepcopy(base)
    wrong_array["rules"] = "not-an-array"
    corruptions.append(wrong_array)
    wrong_enum = copy.deepcopy(base)
    wrong_enum["rules"][0]["kind"] = "UNKNOWN"
    corruptions.append(wrong_enum)
    wrong_time = copy.deepcopy(base)
    wrong_time["effective_window"]["effective_from"] = "2026-08-06T12:00:00+00:00"
    corruptions.append(wrong_time)
    invalid_time = copy.deepcopy(base)
    invalid_time["effective_window"]["effective_from"] = "not-a-timeZ"
    corruptions.append(invalid_time)
    noncanonical_time = copy.deepcopy(base)
    noncanonical_time["effective_window"]["effective_from"] = (
        "2026-08-05T12:00:00.000000Z"
    )
    corruptions.append(noncanonical_time)
    wrong_rule_hash = copy.deepcopy(base)
    wrong_rule_hash["rules"][0]["content_hash"] = HASH_A
    corruptions.append(wrong_rule_hash)
    wrong_bundle_hash = copy.deepcopy(base)
    wrong_bundle_hash["content_hash"] = HASH_A
    corruptions.append(wrong_bundle_hash)
    wrong_bool = copy.deepcopy(base)
    wrong_bool["rules"][0]["fixtures"][0]["activities"] = [True]
    corruptions.append(wrong_bool)
    wrong_schema = copy.deepcopy(base)
    wrong_schema["schema_version"] = "2.0.0"
    corruptions.append(wrong_schema)
    wrong_evaluator = copy.deepcopy(base)
    wrong_evaluator["rules"][0]["evaluator"] = []
    corruptions.append(wrong_evaluator)
    wrong_citation = copy.deepcopy(base)
    wrong_citation["rules"][0]["citations"][0] = []
    corruptions.append(wrong_citation)
    changed_content = copy.deepcopy(base)
    changed_content["internal_notes"] = "Tampered private note."
    corruptions.append(changed_content)
    non_json = copy.deepcopy(base)
    non_json["internal_notes"] = {"not-json"}
    corruptions.append(non_json)

    for payload in corruptions:
        with pytest.raises((TypeError, ValueError)):
            decode(payload, bundle)

    with pytest.raises(ValueError, match="identity"):
        decode_bundle_payload(
            base,
            tenant_id="tenant-2",
            deployment_id="demo",
            rule_set_id="ruleset-1",
            bundle_id="bundle-1",
            version="1.0.0",
            content_hash=rule_bundle_ref(bundle).content_hash,
        )
    with pytest.raises(ValueError, match="valid JSON"):
        decode("{", bundle)
    with pytest.raises(ValueError, match="duplicate key"):
        decode('{"schema_version":"1.0.0","schema_version":"1.0.0"}', bundle)
    with pytest.raises(ValueError, match="JSON object"):
        decode('"string-json"', bundle)

    oversized = copy.deepcopy(base)
    oversized["internal_notes"] = "x" * 1_048_577
    with pytest.raises(ValueError, match="size limit"):
        decode(oversized, bundle)

    with pytest.raises(ValueError, match="typed"):
        encode_bundle_payload(cast(RuleBundleVersion, object()))

    forged = complex_bundle()
    object.__setattr__(
        forged.effective_window,
        "effective_from",
        forged.effective_window.effective_from.replace(tzinfo=None),
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        encode_bundle_payload(forged)


def test_rescreen_impact_round_trip_and_strict_shape() -> None:
    bundle = complex_bundle()
    impact = calculate_rescreen_impact(None, bundle)
    payload = encode_rescreen_impact(impact)
    assert payload is not None
    assert decode_rescreen_impact(payload) == impact
    assert decode_rescreen_impact(json.dumps(payload)) == impact
    assert encode_rescreen_impact(None) is None

    for corrupt in (
        {**payload, "unknown": True},
        {**payload, "rescreen_required": 1},
        {**payload, "affected_fact_paths": ["unknown"]},
        {**payload, "previous_bundle": {"bundle_id": "bundle-1"}},
    ):
        with pytest.raises((TypeError, ValueError)):
            decode_rescreen_impact(corrupt)
    with pytest.raises(ValueError, match="duplicate key"):
        decode_rescreen_impact('{"schema_version":"1.0.0","schema_version":"1.0.0"}')
    with pytest.raises(ValueError, match="typed"):
        encode_rescreen_impact(object())  # type: ignore[arg-type]
    wrong_schema = copy.deepcopy(payload)
    wrong_schema["schema_version"] = "2.0.0"
    with pytest.raises(ValueError, match="unsupported"):
        decode_rescreen_impact(wrong_schema)
