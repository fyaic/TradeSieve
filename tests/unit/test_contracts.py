"""Canonical application-contract behavior and safety boundaries."""

from __future__ import annotations

import copy
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from tradesieve.application.contracts import (
    ClassificationCandidate,
    ClassificationScheme,
    DecisionScope,
    EventEnvelope,
    ExternalObject,
    GoodsLine,
    HumanDecisionRequest,
    HumanDecisionType,
    OwnershipControlRelationship,
    PaymentPath,
    ProposedAction,
    RelationshipType,
    ScreeningRequest,
    VersionReference,
    VersionSet,
)

HASH = "sha256:" + "a" * 64


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
    assert request.parties == []
    assert request.goods == []
    assert request.route is None
    assert request.payment is None
    assert request.tenant_id.startswith("0")
    assert request.canonical_input_hash().startswith("sha256:")
    assert request.canonical_input_hash() == request.canonical_input_hash()


def test_structural_constraints_remain_fail_closed() -> None:
    payload = incomplete_request()
    payload["unexpected"] = "ignored only by an unsafe adapter"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ScreeningRequest.model_validate(payload)

    payload = incomplete_request()
    payload["action_due_at"] = "2026-08-07T08:00:00"
    with pytest.raises(ValidationError, match="timezone"):
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
    assert "unknown party refs: missing-party, missing-payer" in message
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
            "legal_nexus": [
                {
                    "nexus_ref": "nexus-eu",
                    "nexus_type": "REGULATORY_REGIME",
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
    assert request.goods[0].quantity_unit == "each"
    assert request.payment is not None
    assert str(request.payment.amount) == "100.25"


def test_relationship_windows_and_other_classification_names_are_structural() -> None:
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
        ValidationError, match="money amounts must be canonical decimal strings"
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
    assert goods.model_dump(mode="json")["unit_value"] == "10.20"
    assert goods.model_dump(mode="json")["total_value"] == "20.40"

    with pytest.raises(ValidationError, match="canonical decimal strings"):
        PaymentPath.model_validate_json(b'{"payment_ref":"payment-1","amount":"01.00"}')


def test_version_set_rejects_duplicate_resource_ids() -> None:
    version = VersionReference(resource_id="source-1", version="2026-08-06")
    with pytest.raises(ValidationError, match="duplicate sources resource_id"):
        VersionSet(
            input_schema="1.0.0",
            input_hash=HASH,
            sources=[version, copy.deepcopy(version)],
            rules=[],
            matcher=VersionReference(resource_id="matcher", version="1"),
            models=[],
        )

    version_set = VersionSet(
        input_schema="1.0.0",
        input_hash=HASH,
        sources=[],
        rules=[],
        matcher=VersionReference(resource_id="matcher", version="1"),
        models=[],
    )
    assert version_set.input_hash == HASH


def test_human_clearance_requires_a_timezone_aware_expiry() -> None:
    scope = DecisionScope(
        proposed_action=ProposedAction.QUOTE_RELEASE,
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

    payload["event_type"] = "screening.completed"
    with pytest.raises(ValidationError, match="event_type must match data.kind"):
        EventEnvelope.model_validate(payload)
