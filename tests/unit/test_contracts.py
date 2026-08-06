"""Canonical application-contract behavior and safety boundaries."""

from __future__ import annotations

import copy
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from tradesieve.application.contract_examples import (
    event_examples,
    incomplete_customer_onboarding_request,
    structurally_invalid_request,
    transaction_review_required_result,
    transaction_screening_request,
)
from tradesieve.application.contracts import (
    Case,
    CaseStateChangedEventData,
    ClassificationCandidate,
    ClassificationScheme,
    DecisionScope,
    EventEnvelope,
    ExternalObject,
    GoodsLine,
    HashedVersionReference,
    HumanDecision,
    HumanDecisionRecordedEventData,
    HumanDecisionRequest,
    HumanDecisionType,
    OwnershipControlRelationship,
    PaymentPath,
    ProposedAction,
    RegulatedActivity,
    RelationshipType,
    RuleEvaluationOutcome,
    RuleEvaluationRecord,
    RuleFactPath,
    ScreeningRequest,
    ScreeningResult,
    SourceStatus,
    VersionReference,
    VersionSet,
)

HASH = "sha256:" + "a" * 64


def test_executable_contract_examples_round_trip_in_unit_suite() -> None:
    request = transaction_screening_request()
    incomplete = incomplete_customer_onboarding_request()
    invalid = structurally_invalid_request(incomplete)
    with pytest.raises(ValidationError):
        ScreeningRequest.model_validate(invalid)

    result = transaction_review_required_result(request)
    assert result.result_hash == result.canonical_result_hash()
    assert len(result.rule_evaluations) == 3
    assert set(event_examples(result)) == {
        "screening.completed",
        "case.state_changed",
        "human_decision.recorded",
    }


def incomplete_request() -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "tenant_id": "01J4Z3Y5N6P7Q8R9S0T1U2V3W4",
        "correlation_id": "123e4567-e89b-12d3-a456-426614174000",
        "data_classification": "SYNTHETIC",
        "external_object": {
            "system": "synthetic-crm",
            "object_type": "CUSTOMER",
            "object_id": "customer-incomplete-1",
            "object_version": "1",
        },
        "proposed_action": "CUSTOMER_ONBOARDING",
        "legal_nexus": [],
        "parties": [],
        "ownership_and_control": [],
        "goods": [],
        "route": None,
        "documents": [],
        "payment": None,
    }


def test_incomplete_business_facts_are_valid_transport_shape() -> None:
    request = ScreeningRequest.model_validate(incomplete_request())

    assert request.proposed_action is ProposedAction.CUSTOMER_ONBOARDING
    assert request.activities == []
    assert request.parties == []
    assert request.goods == []
    assert request.route is None
    assert request.payment is None
    assert request.tenant_id.startswith("0")
    assert request.canonical_input_hash().startswith("sha256:")
    assert request.canonical_input_hash() == request.canonical_input_hash()


def test_semantic_input_hash_excludes_trace_but_includes_business_scope() -> None:
    original_payload = incomplete_request()
    original = ScreeningRequest.model_validate(original_payload)

    retry_payload = copy.deepcopy(original_payload)
    retry_payload["correlation_id"] = "correlation-retry-2"
    retry = ScreeningRequest.model_validate(retry_payload)

    changed_payload = copy.deepcopy(original_payload)
    changed_payload["proposed_action"] = "QUOTE_RELEASE"
    changed = ScreeningRequest.model_validate(changed_payload)

    other_tenant_payload = copy.deepcopy(original_payload)
    other_tenant_payload["tenant_id"] = "tenant-2"
    other_tenant = ScreeningRequest.model_validate(other_tenant_payload)

    assert retry.canonical_input_hash() == original.canonical_input_hash()
    assert changed.canonical_input_hash() != original.canonical_input_hash()
    assert other_tenant.canonical_input_hash() != original.canonical_input_hash()


def test_datetime_offsets_normalize_to_utc_for_json_and_semantic_hashes() -> None:
    utc_payload = incomplete_request()
    utc_payload["action_due_at"] = "2026-08-07T08:00:00Z"
    offset_payload = copy.deepcopy(utc_payload)
    offset_payload["action_due_at"] = "2026-08-07T16:00:00+08:00"

    utc_request = ScreeningRequest.model_validate(utc_payload)
    offset_request = ScreeningRequest.model_validate(offset_payload)
    assert utc_request.model_dump(mode="json")["action_due_at"] == (
        "2026-08-07T08:00:00Z"
    )
    assert offset_request.model_dump(mode="json") == utc_request.model_dump(mode="json")
    assert offset_request.canonical_input_hash() == utc_request.canonical_input_hash()

    utc_result_payload = minimal_result()
    utc_result_payload["created_at"] = "2026-08-07T08:00:00Z"
    offset_result_payload = copy.deepcopy(utc_result_payload)
    offset_result_payload["created_at"] = "2026-08-07T16:00:00+08:00"
    utc_result = ScreeningResult.model_validate(utc_result_payload)
    offset_result = ScreeningResult.model_validate(offset_result_payload)
    assert offset_result.model_dump(mode="json")["created_at"] == (
        "2026-08-07T08:00:00Z"
    )
    assert offset_result.canonical_result_hash() == utc_result.canonical_result_hash()


def test_structural_constraints_remain_fail_closed() -> None:
    payload = incomplete_request()
    payload["unexpected"] = "ignored only by an unsafe adapter"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ScreeningRequest.model_validate(payload)

    payload = incomplete_request()
    payload["action_due_at"] = "2026-08-07T08:00:00"
    with pytest.raises(ValidationError, match="timezone"):
        ScreeningRequest.model_validate(payload)

    payload = incomplete_request()
    del payload["schema_version"]
    with pytest.raises(ValidationError, match="Field required"):
        ScreeningRequest.model_validate(payload)


def test_duplicate_and_dangling_request_references_are_rejected() -> None:
    payload = incomplete_request()
    payload["parties"] = [
        {"party_ref": "party-1", "roles": ["BUYER"], "entity_type": "ORGANIZATION"},
        {"party_ref": "party-1", "roles": ["SELLER"], "entity_type": "ORGANIZATION"},
    ]
    with pytest.raises(ValidationError, match="duplicate party_ref: party-1"):
        ScreeningRequest.model_validate(payload)

    payload = incomplete_request()
    payload["goods"] = [
        {
            "line_ref": "line-1",
            "manufacturer_ref": "missing-manufacturer",
            "end_use": {"end_user_ref": "missing-party"},
        }
    ]
    payload["documents"] = [
        {
            "document_ref": "doc-1",
            "document_type": "TECHNICAL_SPECIFICATION",
            "related_goods_refs": ["missing-line"],
        }
    ]
    payload["payment"] = {
        "payment_ref": "payment-1",
        "payer_ref": "missing-payer",
        "evidence_refs": ["missing-doc"],
    }
    with pytest.raises(ValidationError) as exc_info:
        ScreeningRequest.model_validate(payload)
    message = str(exc_info.value)
    assert (
        "unknown party refs: missing-manufacturer, missing-party, missing-payer"
        in message
    )
    assert "unknown goods refs: missing-line" in message
    assert "unknown evidence refs: missing-doc" in message


def test_nested_candidate_identity_and_evidence_references_are_checked() -> None:
    payload = incomplete_request()
    payload["goods"] = [
        {
            "line_ref": "line-1",
            "classification_candidates": [
                {
                    "candidate_ref": "candidate-1",
                    "scheme": "HS",
                    "code": "853710",
                    "evidence_refs": ["missing-doc"],
                },
                {
                    "candidate_ref": "candidate-1",
                    "scheme": "CN",
                    "code": "85371098",
                },
            ],
        }
    ]
    with pytest.raises(ValidationError, match="duplicate candidate_ref: candidate-1"):
        ScreeningRequest.model_validate(payload)

    payload["goods"] = [
        {
            "line_ref": "line-1",
            "classification_candidates": [
                {
                    "candidate_ref": "candidate-1",
                    "scheme": "HS",
                    "code": "853710",
                    "evidence_refs": ["missing-doc"],
                },
                {
                    "candidate_ref": "candidate-2",
                    "scheme": "CN",
                    "code": "85371098",
                },
            ],
        }
    ]
    with pytest.raises(ValidationError, match="unknown evidence refs: missing-doc"):
        ScreeningRequest.model_validate(payload)


def test_valid_reference_graph_covers_all_typed_fact_groups() -> None:
    payload = incomplete_request()
    payload.update(
        {
            "activities": ["SALE", "EXPORT", "TRANSPORT"],
            "legal_nexus": [
                {
                    "nexus_ref": "nexus-eu",
                    "nexus_type": "REGULATORY_REGIME",
                    "regime_code": "EU",
                    "basis": "EU trade-compliance scope pending Member State review",
                    "fact_class": "UNKNOWN",
                    "evidence_refs": ["doc-1"],
                }
            ],
            "parties": [
                {
                    "party_ref": "owner-1",
                    "roles": ["BENEFICIAL_OWNER"],
                    "entity_type": "PERSON",
                },
                {
                    "party_ref": "manufacturer-1",
                    "roles": ["SUPPLIER"],
                    "entity_type": "ORGANIZATION",
                },
                {
                    "party_ref": "buyer-1",
                    "roles": ["BUYER", "END_USER", "PAYER"],
                    "entity_type": "ORGANIZATION",
                },
                {
                    "party_ref": "carrier-1",
                    "roles": ["CARRIER"],
                    "entity_type": "ORGANIZATION",
                },
                {
                    "party_ref": "vessel-1",
                    "roles": ["VESSEL"],
                    "entity_type": "VESSEL",
                },
                {
                    "party_ref": "bank-1",
                    "roles": ["BENEFICIARY_BANK"],
                    "entity_type": "BANK",
                },
            ],
            "ownership_and_control": [
                {
                    "relationship_ref": "ownership-1",
                    "from_party_ref": "owner-1",
                    "to_party_ref": "buyer-1",
                    "relationship_type": "OWNERSHIP",
                    "ownership_percentage": "25.5",
                    "evidence_refs": ["doc-1"],
                }
            ],
            "goods": [
                {
                    "line_ref": "line-1",
                    "description": "Synthetic module",
                    "manufacturer_ref": "manufacturer-1",
                    "quantity": "10",
                    "quantity_unit": "each",
                    "classification_candidates": [
                        {
                            "candidate_ref": "classification-1",
                            "scheme": "ECCN",
                            "code": "EAR99",
                            "candidate_only": True,
                            "evidence_refs": ["doc-1"],
                        }
                    ],
                    "end_use": {"end_user_ref": "buyer-1"},
                },
                {"line_ref": "line-2", "end_use": {}},
            ],
            "route": {
                "carrier_ref": "carrier-1",
                "vessel_ref": "vessel-1",
            },
            "documents": [
                {
                    "document_ref": "doc-1",
                    "document_type": "TECHNICAL_SPECIFICATION",
                    "related_party_refs": ["buyer-1"],
                    "related_goods_refs": ["line-1"],
                }
            ],
            "payment": {
                "payment_ref": "payment-1",
                "payer_ref": "buyer-1",
                "beneficiary_bank_ref": "bank-1",
                "amount": "100.25",
                "currency": "EUR",
                "evidence_refs": ["doc-1"],
            },
        }
    )

    request = ScreeningRequest.model_validate(payload)

    assert request.goods[0].classification_candidates[0].candidate_only is True
    assert request.legal_nexus[0].regime_code == "EU"
    assert request.goods[0].quantity_unit == "each"
    assert request.activities[1].value == "EXPORT"
    assert request.payment is not None
    assert str(request.payment.amount) == "100.25"


def test_relationship_windows_and_other_classification_names_are_structural() -> None:
    with pytest.raises(ValidationError, match="cannot be self-referential"):
        OwnershipControlRelationship(
            relationship_ref="relationship-self",
            from_party_ref="party-1",
            to_party_ref="party-1",
            relationship_type=RelationshipType.OWNERSHIP,
        )

    with pytest.raises(
        ValidationError, match="valid_to must be on or after valid_from"
    ):
        OwnershipControlRelationship(
            relationship_ref="relationship-1",
            from_party_ref="owner-1",
            to_party_ref="subject-1",
            relationship_type=RelationshipType.CONTROL,
            valid_from=datetime(2026, 8, 7, tzinfo=UTC),
            valid_to=datetime(2026, 8, 6, tzinfo=UTC),
        )

    relationship = OwnershipControlRelationship(
        relationship_ref="relationship-1",
        from_party_ref="owner-1",
        to_party_ref="subject-1",
        relationship_type=RelationshipType.CONTROL,
        valid_from=datetime(2026, 8, 6, tzinfo=UTC),
        valid_to=datetime(2026, 8, 7, tzinfo=UTC),
    )
    assert relationship.valid_to is not None

    with pytest.raises(ValidationError, match="scheme_name is required"):
        ClassificationCandidate(
            candidate_ref="candidate-1",
            scheme=ClassificationScheme.OTHER,
            code="SYNTHETIC-CODE",
        )

    candidate = ClassificationCandidate(
        candidate_ref="candidate-1",
        scheme=ClassificationScheme.OTHER,
        scheme_name="Synthetic classification scheme",
        code="SYNTHETIC-CODE",
    )
    assert candidate.scheme_name == "Synthetic classification scheme"


@pytest.mark.parametrize(
    "payload",
    [
        b'{"payment_ref":"payment-1","amount":0.100000000000000001}',
        b'{"line_ref":"line-1","unit_value":0.100000000000000001}',
        b'{"line_ref":"line-1","total_value":0.100000000000000001}',
    ],
)
def test_money_rejects_json_numbers_before_float_rounding(payload: bytes) -> None:
    model = PaymentPath if b"payment_ref" in payload else GoodsLine
    with pytest.raises(
        ValidationError, match="money amount must be a canonical decimal string"
    ):
        model.model_validate_json(payload)


def test_money_accepts_and_preserves_bounded_decimal_strings() -> None:
    payment = PaymentPath.model_validate_json(
        b'{"payment_ref":"payment-1","amount":"0.100001","currency":"EUR"}'
    )
    goods = GoodsLine.model_validate_json(
        b'{"line_ref":"line-1","unit_value":"10.20","total_value":"20.40"}'
    )

    assert payment.model_dump(mode="json")["amount"] == "0.100001"
    assert goods.model_dump(mode="json")["unit_value"] == "10.2"
    assert goods.model_dump(mode="json")["total_value"] == "20.4"

    equivalent = GoodsLine.model_validate_json(
        b'{"line_ref":"line-1","unit_value":"10.200"}'
    )
    assert equivalent.model_dump(mode="json")["unit_value"] == "10.2"

    integer = GoodsLine.model_validate_json(
        b'{"line_ref":"line-1","unit_value":"100","total_value":"100.00"}'
    )
    assert integer.model_dump(mode="json")["unit_value"] == "100"
    assert integer.model_dump(mode="json")["total_value"] == "100"
    ten = GoodsLine.model_validate_json(b'{"line_ref":"line-1","unit_value":"10"}')
    assert ten.model_dump(mode="json")["unit_value"] == "10"

    first_payload = incomplete_request()
    first_payload["goods"] = [{"line_ref": "line-1", "unit_value": "100"}]
    second_payload = copy.deepcopy(first_payload)
    second_payload["goods"] = [{"line_ref": "line-1", "unit_value": "100.00"}]
    first = ScreeningRequest.model_validate(first_payload)
    second = ScreeningRequest.model_validate(second_payload)
    assert first.canonical_input_hash() == second.canonical_input_hash()

    with pytest.raises(ValidationError, match="canonical decimal string"):
        PaymentPath.model_validate_json(b'{"payment_ref":"payment-1","amount":"01.00"}')

    for invalid in (b'"1e2"', b'"0"', b'"0.000000"'):
        payload = b'{"payment_ref":"payment-1","amount":' + invalid + b"}"
        with pytest.raises(ValidationError, match="canonical decimal string"):
            PaymentPath.model_validate_json(payload)


def test_all_decimal_facts_reject_json_numbers_before_float_coercion() -> None:
    with pytest.raises(ValidationError, match="quantity must be a canonical"):
        GoodsLine.model_validate_json(
            b'{"line_ref":"line-1","quantity":0.100000000000000001}'
        )
    with pytest.raises(ValidationError, match="percentage must be a canonical"):
        OwnershipControlRelationship.model_validate_json(
            b'{"relationship_ref":"r-1","from_party_ref":"p-1",'
            b'"to_party_ref":"p-2","relationship_type":"OWNERSHIP",'
            b'"ownership_percentage":25.000000000000001}'
        )
    with pytest.raises(ValidationError, match="confidence must be a canonical"):
        ClassificationCandidate.model_validate_json(
            b'{"candidate_ref":"c-1","scheme":"HS","code":"853710",'
            b'"confidence":0.500000000000001}'
        )


def test_all_decimal_fact_lexical_forms_hash_identically() -> None:
    first_payload = incomplete_request()
    first_payload.update(
        {
            "parties": [
                {
                    "party_ref": "party-1",
                    "roles": ["BENEFICIAL_OWNER"],
                    "entity_type": "PERSON",
                },
                {
                    "party_ref": "party-2",
                    "roles": ["CUSTOMER"],
                    "entity_type": "ORGANIZATION",
                },
            ],
            "ownership_and_control": [
                {
                    "relationship_ref": "relationship-1",
                    "from_party_ref": "party-1",
                    "to_party_ref": "party-2",
                    "relationship_type": "OWNERSHIP",
                    "ownership_percentage": "25.5",
                }
            ],
            "goods": [
                {
                    "line_ref": "line-1",
                    "quantity": "10",
                    "classification_candidates": [
                        {
                            "candidate_ref": "candidate-1",
                            "scheme": "HS",
                            "code": "853710",
                            "confidence": "0.5",
                        }
                    ],
                }
            ],
        }
    )
    second_payload = copy.deepcopy(first_payload)
    second_payload["ownership_and_control"] = [
        {
            "relationship_ref": "relationship-1",
            "from_party_ref": "party-1",
            "to_party_ref": "party-2",
            "relationship_type": "OWNERSHIP",
            "ownership_percentage": "25.5000",
        }
    ]
    second_payload["goods"] = [
        {
            "line_ref": "line-1",
            "quantity": "10.000000",
            "classification_candidates": [
                {
                    "candidate_ref": "candidate-1",
                    "scheme": "HS",
                    "code": "853710",
                    "confidence": "0.50000",
                }
            ],
        }
    ]

    first = ScreeningRequest.model_validate(first_payload)
    second = ScreeningRequest.model_validate(second_payload)
    assert first.canonical_input_hash() == second.canonical_input_hash()
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def minimal_result() -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "tenant_id": "tenant-1",
        "correlation_id": "correlation-1",
        "screening_id": "screening-1",
        "case_id": "case-1",
        "state": "REVIEW_REQUIRED",
        "signal": "YELLOW",
        "highest_priority": "P1",
        "business_action": "HOLD",
        "summary": "Material facts remain incomplete.",
        "rule_evaluations": [
            {
                "evaluation_id": "rule-evaluation-1",
                "bundle": {
                    "resource_id": "rule-bundle-1",
                    "version": "1.0.0",
                    "content_hash": "sha256:" + "c" * 64,
                },
                "rule": {
                    "resource_id": "rule-1",
                    "version": "1.0.0",
                    "content_hash": "sha256:" + "d" * 64,
                },
                "outcome": "MISSING_FACTS",
                "missing_fact_paths": ["goods"],
            }
        ],
        "findings": [
            {
                "finding_id": "finding-1",
                "kind": "FACT_INCOMPLETE",
                "priority": "P1",
                "status": "OPEN",
                "fact_class": "UNKNOWN",
                "summary": "A required fact has not been supplied.",
                "required_evidence_refs": ["requirement-1"],
                "rule_evaluation_refs": ["rule-evaluation-1"],
                "required_action": "OBTAIN_EVIDENCE",
                "owner_role": "COMPLIANCE_REVIEWER",
            }
        ],
        "required_evidence": [
            {
                "requirement_ref": "requirement-1",
                "evidence_type": "OTHER",
                "description": "Supply the missing fact evidence.",
                "status": "REQUIRED",
            }
        ],
        "holds": [
            {
                "hold_id": "hold-1",
                "scope": "QUOTE_RELEASE",
                "reason": "A material fact is missing.",
                "release_condition": "Resolve the open P1 finding.",
                "active": True,
            }
        ],
        "version_set": {
            "input_schema": "1.0.0",
            "input_hash": HASH,
            "rule_bundle": {
                "resource_id": "rule-bundle-1",
                "version": "1.0.0",
                "content_hash": "sha256:" + "c" * 64,
            },
            "sources": [],
            "rules": [
                {
                    "resource_id": "rule-1",
                    "version": "1.0.0",
                    "content_hash": "sha256:" + "d" * 64,
                }
            ],
            "matcher": {"resource_id": "matcher", "version": "1"},
            "models": [],
        },
        "result_hash": "sha256:" + "b" * 64,
        "created_at": "2026-08-06T08:00:00Z",
    }


def minimal_case() -> dict[str, object]:
    result = minimal_result()
    return {
        "tenant_id": result["tenant_id"],
        "correlation_id": result["correlation_id"],
        "case_id": result["case_id"],
        "state": result["state"],
        "signal": result["signal"],
        "highest_priority": result["highest_priority"],
        "business_action": result["business_action"],
        "rule_evaluations": result["rule_evaluations"],
        "findings": result["findings"],
        "required_evidence": result["required_evidence"],
        "holds": result["holds"],
        "version_set": result["version_set"],
        "effective_human_decision": None,
        "updated_at": "2026-08-06T08:00:00Z",
    }


def test_rule_evaluation_uses_finite_paths_strict_semver_and_non_clearance() -> None:
    result = ScreeningResult.model_validate(minimal_result())
    evaluation = result.rule_evaluations[0]
    assert evaluation.outcome is RuleEvaluationOutcome.MISSING_FACTS
    assert evaluation.missing_fact_paths == [RuleFactPath.GOODS]

    base = evaluation.model_dump(mode="json")
    for bad_path in ["goods.line-1.classification", "unknown", "GOODS"]:
        invalid = copy.deepcopy(base)
        invalid["missing_fact_paths"] = [bad_path]
        with pytest.raises(ValidationError):
            RuleEvaluationRecord.model_validate(invalid)
    too_many = copy.deepcopy(base)
    too_many["missing_fact_paths"] = ["goods"] * 33
    with pytest.raises(ValidationError, match="at most 32"):
        RuleEvaluationRecord.model_validate(too_many)
    unsorted = copy.deepcopy(base)
    unsorted["missing_fact_paths"] = ["goods", "activities"]
    with pytest.raises(ValidationError, match="sorted and unique"):
        RuleEvaluationRecord.model_validate(unsorted)

    for field in ("bundle", "rule"):
        for bad_version in ["1", "1.0", "01.0.0", " 1.0.0 ", "1000000000.0.0"]:
            invalid = copy.deepcopy(base)
            reference = invalid[field]
            assert isinstance(reference, dict)
            reference["version"] = bad_version
            with pytest.raises(
                ValidationError, match="strict release semantic version"
            ):
                RuleEvaluationRecord.model_validate(invalid)

    assert (
        HashedVersionReference(
            resource_id="rule-1",
            version="999999999.999999999.999999999",
            content_hash=HASH,
        ).version
        == "999999999.999999999.999999999"
    )

    for outcome, missing in [
        (RuleEvaluationOutcome.FACTS_PRESENT, ["goods"]),
        (RuleEvaluationOutcome.NOT_APPLICABLE, ["goods"]),
        (RuleEvaluationOutcome.MISSING_FACTS, []),
    ]:
        invalid = copy.deepcopy(base)
        invalid["outcome"] = outcome
        invalid["missing_fact_paths"] = missing
        with pytest.raises(ValidationError, match="only MISSING_FACTS"):
            RuleEvaluationRecord.model_validate(invalid)


def _graph_payload(model: type[ScreeningResult] | type[Case]) -> dict[str, object]:
    return minimal_result() if model is ScreeningResult else minimal_case()


@pytest.mark.parametrize("model", [ScreeningResult, Case])
def test_result_and_case_require_complete_exact_rule_evaluation_graph(
    model: type[ScreeningResult] | type[Case],
) -> None:
    empty = _graph_payload(model)
    empty["rule_evaluations"] = []
    with pytest.raises(ValidationError, match="at least 1"):
        model.model_validate(empty)

    duplicate_evaluation = _graph_payload(model)
    evaluations = duplicate_evaluation["rule_evaluations"]
    assert isinstance(evaluations, list)
    evaluations.append(copy.deepcopy(evaluations[0]))
    with pytest.raises(ValidationError, match="duplicate evaluation_id"):
        model.model_validate(duplicate_evaluation)

    duplicate_rule = _graph_payload(model)
    evaluations = duplicate_rule["rule_evaluations"]
    assert isinstance(evaluations, list)
    duplicate = copy.deepcopy(evaluations[0])
    assert isinstance(duplicate, dict)
    duplicate["evaluation_id"] = "rule-evaluation-2"
    duplicate["outcome"] = "NOT_APPLICABLE"
    duplicate["missing_fact_paths"] = []
    evaluations.append(duplicate)
    with pytest.raises(ValidationError, match="identities must be unique"):
        model.model_validate(duplicate_rule)

    unordered_evaluations = _graph_payload(model)
    evaluations = unordered_evaluations["rule_evaluations"]
    assert isinstance(evaluations, list)
    second = copy.deepcopy(evaluations[0])
    assert isinstance(second, dict)
    second["evaluation_id"] = "rule-evaluation-2"
    second["rule"] = {
        "resource_id": "rule-2",
        "version": "1.0.0",
        "content_hash": "sha256:" + "e" * 64,
    }
    second["outcome"] = "NOT_APPLICABLE"
    second["missing_fact_paths"] = []
    evaluations.insert(0, second)
    version_set = unordered_evaluations["version_set"]
    assert isinstance(version_set, dict)
    rules = version_set["rules"]
    assert isinstance(rules, list)
    rules.append(second["rule"])
    with pytest.raises(ValidationError, match="sorted by rule identity"):
        model.model_validate(unordered_evaluations)

    bundle_mismatch = _graph_payload(model)
    evaluations = bundle_mismatch["rule_evaluations"]
    assert isinstance(evaluations, list)
    evaluation = evaluations[0]
    assert isinstance(evaluation, dict)
    bundle_ref = evaluation["bundle"]
    assert isinstance(bundle_ref, dict)
    bundle_ref["content_hash"] = "sha256:" + "e" * 64
    with pytest.raises(ValidationError, match="version_set.rule_bundle"):
        model.model_validate(bundle_mismatch)

    rule_mismatch = _graph_payload(model)
    evaluations = rule_mismatch["rule_evaluations"]
    assert isinstance(evaluations, list)
    evaluation = evaluations[0]
    assert isinstance(evaluation, dict)
    rule_ref = evaluation["rule"]
    assert isinstance(rule_ref, dict)
    rule_ref["content_hash"] = "sha256:" + "e" * 64
    with pytest.raises(ValidationError, match="exactly match every"):
        model.model_validate(rule_mismatch)

    extra_version_rule = _graph_payload(model)
    version_set = extra_version_rule["version_set"]
    assert isinstance(version_set, dict)
    rules = version_set["rules"]
    assert isinstance(rules, list)
    rules.append(
        {
            "resource_id": "rule-2",
            "version": "1.0.0",
            "content_hash": "sha256:" + "e" * 64,
        }
    )
    with pytest.raises(ValidationError, match="exactly match every"):
        model.model_validate(extra_version_rule)


def test_result_ids_and_required_evidence_references_are_unambiguous() -> None:
    result_payload = minimal_result()
    result = ScreeningResult.model_validate(result_payload)
    assert result.findings[0].required_evidence_refs == ["requirement-1"]

    duplicate_payload = copy.deepcopy(result_payload)
    findings = duplicate_payload["findings"]
    assert isinstance(findings, list)
    findings.append(copy.deepcopy(findings[0]))
    with pytest.raises(ValidationError, match="duplicate finding_id: finding-1"):
        ScreeningResult.model_validate(duplicate_payload)

    dangling_payload = copy.deepcopy(result_payload)
    dangling_findings = dangling_payload["findings"]
    assert isinstance(dangling_findings, list)
    dangling_finding = dangling_findings[0]
    assert isinstance(dangling_finding, dict)
    dangling_finding["required_evidence_refs"] = ["missing-requirement"]
    with pytest.raises(
        ValidationError, match="unknown required evidence refs: missing-requirement"
    ):
        ScreeningResult.model_validate(dangling_payload)

    unknown_evaluation_payload = copy.deepcopy(result_payload)
    findings = unknown_evaluation_payload["findings"]
    assert isinstance(findings, list)
    finding = findings[0]
    assert isinstance(finding, dict)
    finding["rule_evaluation_refs"] = ["missing-evaluation"]
    with pytest.raises(
        ValidationError, match="unknown rule evaluation refs: missing-evaluation"
    ):
        ScreeningResult.model_validate(unknown_evaluation_payload)


def test_semantic_result_hash_excludes_trace_and_itself() -> None:
    result = ScreeningResult.model_validate(minimal_result())
    retried = result.model_copy(update={"correlation_id": "correlation-retry-2"})
    rehashed = result.model_copy(update={"result_hash": "sha256:" + "c" * 64})
    changed = result.model_copy(update={"summary": "A materially changed result."})

    assert retried.canonical_result_hash() == result.canonical_result_hash()
    assert rehashed.canonical_result_hash() == result.canonical_result_hash()
    assert changed.canonical_result_hash() != result.canonical_result_hash()


def test_case_projection_keeps_evidence_and_version_provenance() -> None:
    case = Case.model_validate(minimal_case())

    assert case.required_evidence[0].requirement_ref == "requirement-1"
    assert case.version_set.input_hash == HASH


def test_version_set_rejects_duplicate_resource_ids() -> None:
    version = VersionReference(resource_id="source-1", version="2026-08-06")
    with pytest.raises(ValidationError, match="duplicate sources resource_id"):
        VersionSet(
            input_schema="1.0.0",
            input_hash=HASH,
            rule_bundle=HashedVersionReference(
                resource_id="bundle-1", version="1.0.0", content_hash=HASH
            ),
            sources=[version, copy.deepcopy(version)],
            rules=[
                HashedVersionReference(
                    resource_id="rule-1", version="1.0.0", content_hash=HASH
                )
            ],
            matcher=VersionReference(resource_id="matcher", version="1"),
            models=[],
        )

    version_set = VersionSet(
        input_schema="1.0.0",
        input_hash=HASH,
        rule_bundle=HashedVersionReference(
            resource_id="bundle-1", version="1.0.0", content_hash=HASH
        ),
        sources=[],
        rules=[
            HashedVersionReference(
                resource_id="rule-1", version="1.0.0", content_hash=HASH
            )
        ],
        matcher=VersionReference(resource_id="matcher", version="1"),
        models=[],
    )
    assert version_set.input_hash == HASH

    with pytest.raises(ValidationError, match="sorted by immutable identity"):
        VersionSet(
            input_schema="1.0.0",
            input_hash=HASH,
            rule_bundle=HashedVersionReference(
                resource_id="bundle-1", version="1.0.0", content_hash=HASH
            ),
            sources=[],
            rules=[
                HashedVersionReference(
                    resource_id="z-rule", version="1.0.0", content_hash=HASH
                ),
                HashedVersionReference(
                    resource_id="a-rule", version="1.0.0", content_hash=HASH
                ),
            ],
            matcher=VersionReference(resource_id="matcher", version="1"),
            models=[],
        )


def test_human_clearance_requires_a_timezone_aware_expiry() -> None:
    scope = DecisionScope(
        proposed_action=ProposedAction.QUOTE_RELEASE,
        activities=[RegulatedActivity.SALE, RegulatedActivity.EXPORT],
        external_object=ExternalObject(
            system="synthetic-crm",
            object_type="QUOTE",
            object_id="quote-1",
            object_version="1",
        ),
    )
    with pytest.raises(ValidationError, match="expires_at is required"):
        HumanDecisionRequest(
            decision=HumanDecisionType.HUMAN_CLEARED,
            scope=scope,
            rationale="Reviewed against the cited evidence and versions.",
            resolved_finding_ids=[],
            evidence_refs=[],
            expires_at=None,
        )

    decision = HumanDecisionRequest(
        decision=HumanDecisionType.HUMAN_CLEARED,
        scope=scope,
        rationale="Reviewed against the cited evidence and versions.",
        resolved_finding_ids=[],
        evidence_refs=[],
        expires_at=datetime(2026, 8, 8, tzinfo=UTC),
    )
    assert decision.expires_at is not None

    closed = HumanDecisionRequest(
        decision=HumanDecisionType.CLOSED_NO_ACTION,
        scope=scope,
        rationale="Reviewed against the cited evidence and versions.",
        resolved_finding_ids=[],
        evidence_refs=[],
        expires_at=None,
    )
    assert closed.expires_at is None


def test_clearance_expiry_invariants_apply_to_records_and_events() -> None:
    decision_payload = {
        "decision": "HUMAN_CLEARED",
        "scope": {
            "proposed_action": "QUOTE_RELEASE",
            "activities": ["SALE"],
            "external_object": {
                "system": "synthetic-crm",
                "object_type": "QUOTE",
                "object_id": "quote-1",
                "object_version": "1",
            },
        },
        "rationale": "Reviewed against the cited evidence and versions.",
        "resolved_finding_ids": ["finding-1"],
        "evidence_refs": ["document-1"],
        "expires_at": "2026-08-07T00:00:00Z",
        "tenant_id": "tenant-1",
        "correlation_id": "correlation-1",
        "decision_id": "decision-1",
        "version_set": minimal_result()["version_set"],
        "reviewer_id": "reviewer-1",
        "reviewer_role": "COMPLIANCE_REVIEWER",
        "recorded_at": "2026-08-06T00:00:00Z",
        "effective_from": "2026-08-08T00:00:00Z",
    }
    with pytest.raises(
        ValidationError, match="expires_at must be on or after effective_from"
    ):
        HumanDecision.model_validate(decision_payload)

    decision_payload["effective_from"] = "2026-08-06T00:00:00Z"
    assert HumanDecision.model_validate(decision_payload).expires_at is not None

    case_event = {
        "kind": "case.state_changed",
        "case_id": "case-1",
        "previous_state": "REVIEW_REQUIRED",
        "case_state": "HUMAN_CLEARED",
        "business_action": "ALLOW_WITHIN_HUMAN_DECISION",
        "decision_expires_at": None,
    }
    with pytest.raises(ValidationError, match="expires_at is required"):
        CaseStateChangedEventData.model_validate(case_event)
    case_event["decision_expires_at"] = "2026-08-07T00:00:00Z"
    assert (
        CaseStateChangedEventData.model_validate(case_event).decision_expires_at
        is not None
    )

    decision_event = {
        "kind": "human_decision.recorded",
        "case_id": "case-1",
        "decision_id": "decision-1",
        "decision": "HUMAN_CLEARED",
        "decision_effective_from": "2026-08-08T00:00:00Z",
        "decision_expires_at": None,
    }
    with pytest.raises(ValidationError, match="expires_at is required"):
        HumanDecisionRecordedEventData.model_validate(decision_event)
    decision_event["decision_expires_at"] = "2026-08-07T00:00:00Z"
    with pytest.raises(
        ValidationError,
        match="decision_expires_at must be on or after decision_effective_from",
    ):
        HumanDecisionRecordedEventData.model_validate(decision_event)
    decision_event["decision_expires_at"] = "2026-08-09T00:00:00Z"
    assert (
        HumanDecisionRecordedEventData.model_validate(
            decision_event
        ).decision_expires_at
        is not None
    )


def test_source_status_represents_never_activated_and_current_sources() -> None:
    unavailable = SourceStatus.model_validate(
        {"source_id": "source-1", "status": "UNAVAILABLE"}
    )
    assert unavailable.active_snapshot_id is None
    assert unavailable.retrieved_at is None

    with pytest.raises(ValidationError, match="CURRENT source requires"):
        SourceStatus.model_validate({"source_id": "source-1", "status": "CURRENT"})

    current = SourceStatus.model_validate(
        {
            "source_id": "source-1",
            "status": "CURRENT",
            "active_snapshot_id": "snapshot-1",
            "retrieved_at": "2026-08-06T00:00:00Z",
        }
    )
    assert current.active_snapshot_id == "snapshot-1"


def test_event_envelope_is_minimal_discriminated_and_type_consistent() -> None:
    payload = {
        "event_id": "evt-01",
        "event_type": "case.state_changed",
        "event_version": "1.0",
        "occurred_at": "2026-08-06T08:00:00Z",
        "tenant_id": "tenant-demo",
        "correlation_id": "correlation-1",
        "subject": {
            "screening_id": "screening-1",
            "case_id": "case-1",
            "external_object_type": "QUOTE",
            "external_object_id": "quote-1",
        },
        "data": {
            "kind": "case.state_changed",
            "case_id": "case-1",
            "previous_state": "INCOMPLETE",
            "case_state": "REVIEW_REQUIRED",
            "business_action": "HOLD",
            "decision_expires_at": None,
        },
    }

    event = EventEnvelope.model_validate(payload)
    assert event.data.kind == "case.state_changed"
    assert "findings" not in event.model_dump(mode="json")["data"]

    case_mismatch = copy.deepcopy(payload)
    case_mismatch["subject"] = {
        "screening_id": "screening-1",
        "case_id": "case-2",
        "external_object_type": "QUOTE",
        "external_object_id": "quote-1",
    }
    with pytest.raises(ValidationError, match="subject.case_id must match"):
        EventEnvelope.model_validate(case_mismatch)

    screening_mismatch = copy.deepcopy(payload)
    screening_mismatch["event_type"] = "screening.completed"
    screening_mismatch["data"] = {
        "kind": "screening.completed",
        "screening_id": "screening-2",
        "case_id": "case-1",
        "case_state": "REVIEW_REQUIRED",
        "business_action": "HOLD",
        "signal": "YELLOW",
        "result_hash": HASH,
    }
    with pytest.raises(ValidationError, match="subject.screening_id must match"):
        EventEnvelope.model_validate(screening_mismatch)

    payload["event_type"] = "screening.completed"
    with pytest.raises(ValidationError, match="event_type must match data.kind"):
        EventEnvelope.model_validate(payload)
