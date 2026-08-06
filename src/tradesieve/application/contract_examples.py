"""Synthetic, executable examples for the generated canonical contract artifacts."""

from __future__ import annotations

from tradesieve.application.contracts import (
    EventEnvelope,
    ScreeningRequest,
    ScreeningResult,
)


def transaction_screening_request() -> ScreeningRequest:
    """Return the complete synthetic transaction used by contract tests and docs."""

    return ScreeningRequest.model_validate(
        {
            "schema_version": "1.0.0",
            "tenant_id": "tenant-demo",
            "correlation_id": "crm-quote-001",
            "data_classification": "SYNTHETIC",
            "external_object": {
                "system": "demo-crm",
                "object_type": "QUOTE",
                "object_id": "demo-quote-001",
                "object_version": "1",
            },
            "proposed_action": "QUOTE_RELEASE",
            "activities": ["SALE", "SUPPLY", "EXPORT", "TRANSPORT"],
            "action_due_at": "2026-08-07T08:00:00Z",
            "legal_nexus": [
                {
                    "nexus_ref": "nexus-eu",
                    "nexus_type": "REGULATORY_REGIME",
                    "regime_code": "EU",
                    "basis": (
                        "Potential EU scope; applicable Member State remains unknown."
                    ),
                    "fact_class": "UNKNOWN",
                    "evidence_refs": ["doc-invoice"],
                    "source_refs": ["demo-policy-scope:1"],
                },
                {
                    "nexus_ref": "nexus-bank",
                    "nexus_type": "BANK_POLICY",
                    "regime_code": "BANK_POLICY_UNKNOWN",
                    "fact_class": "UNKNOWN",
                    "evidence_refs": ["doc-invoice"],
                },
            ],
            "parties": [
                {
                    "party_ref": "seller-1",
                    "roles": ["SELLER", "PAYEE"],
                    "entity_type": "ORGANIZATION",
                    "legal_name": "Northern Bridge Logistics Demo Ltd",
                    "country": "CN",
                    "identifiers": [
                        {
                            "type": "DEMO_REGISTRATION_ID",
                            "value": "CN-DEMO-0001",
                            "issuer": "CN",
                        }
                    ],
                    "address": "Synthetic address, Shanghai",
                },
                {
                    "party_ref": "buyer-1",
                    "roles": ["BUYER", "END_USER", "PAYER"],
                    "entity_type": "ORGANIZATION",
                    "legal_name": "Волга Компонентс Демо ООО",
                    "aliases": ["Volga Components Demo LLC"],
                    "country": "RU",
                    "address": "Synthetic address, Moscow",
                },
                {
                    "party_ref": "owner-1",
                    "roles": ["BENEFICIAL_OWNER"],
                    "entity_type": "PERSON",
                    "legal_name": "Synthetic Owner Person",
                    "country": "KZ",
                },
                {
                    "party_ref": "manufacturer-1",
                    "roles": ["SUPPLIER"],
                    "entity_type": "ORGANIZATION",
                    "legal_name": "Demo Controls Laboratory",
                    "country": "CN",
                },
                {
                    "party_ref": "carrier-1",
                    "roles": ["CARRIER"],
                    "entity_type": "ORGANIZATION",
                    "legal_name": "Synthetic Carrier Co",
                    "country": "KZ",
                },
                {
                    "party_ref": "vessel-1",
                    "roles": ["VESSEL"],
                    "entity_type": "VESSEL",
                    "legal_name": "MV Synthetic Route",
                    "country": "KZ",
                },
                {
                    "party_ref": "origin-bank-1",
                    "roles": ["ORIGINATING_BANK"],
                    "entity_type": "BANK",
                    "legal_name": "Synthetic Origin Bank",
                    "country": "CN",
                },
                {
                    "party_ref": "beneficiary-bank-1",
                    "roles": ["BENEFICIARY_BANK"],
                    "entity_type": "BANK",
                    "legal_name": "Synthetic Settlement Bank",
                    "country": "KZ",
                },
            ],
            "ownership_and_control": [
                {
                    "relationship_ref": "ownership-1",
                    "from_party_ref": "owner-1",
                    "to_party_ref": "buyer-1",
                    "relationship_type": "OWNERSHIP",
                    "ownership_percentage": "25.5",
                    "fact_class": "SOURCE_ASSERTION",
                    "evidence_refs": ["doc-ownership"],
                    "source_refs": ["demo-company-registry:2026-08-06"],
                    "valid_from": "2026-01-01T00:00:00Z",
                    "reviewer_status": "UNREVIEWED",
                }
            ],
            "goods": [
                {
                    "line_ref": "line-1",
                    "description": (
                        "Synthetic industrial control module for compliance testing"
                    ),
                    "manufacturer": "Demo Controls Laboratory",
                    "manufacturer_ref": "manufacturer-1",
                    "technical_specification": (
                        "Synthetic specifications; performance details incomplete."
                    ),
                    "classification_candidates": [
                        {
                            "candidate_ref": "classification-hs-1",
                            "scheme": "HS",
                            "code": "853710",
                            "candidate_only": True,
                            "rationale": "Supplier-provided candidate code; not reviewed.",
                            "fact_class": "SOURCE_ASSERTION",
                            "evidence_refs": ["doc-tech"],
                            "reviewer_status": "UNREVIEWED",
                        },
                        {
                            "candidate_ref": "classification-dual-use-1",
                            "scheme": "EU_DUAL_USE_ANNEX_I",
                            "code": "3A999-SYNTHETIC-CANDIDATE",
                            "candidate_only": True,
                            "rationale": (
                                "Synthetic candidate retained for human classification."
                            ),
                            "fact_class": "INFERENCE",
                            "evidence_refs": ["doc-tech"],
                            "source_refs": ["demo-eu-dual-use:1"],
                            "reviewer_status": "UNREVIEWED",
                        },
                    ],
                    "quantity": "10",
                    "quantity_unit": "each",
                    "unit_value": "1250.00",
                    "total_value": "12500.00",
                    "currency": "EUR",
                    "origin_country": "CN",
                    "end_use": {
                        "description": (
                            "Industrial automation; installation detail not provided."
                        ),
                        "end_user_ref": "buyer-1",
                        "country": "RU",
                    },
                }
            ],
            "route": {
                "origin_country": "CN",
                "loading_location": "Shanghai",
                "transit_countries": ["KZ"],
                "discharge_location": "Synthetic inland terminal",
                "destination_country": "RU",
                "final_use_country": "RU",
                "carrier_ref": "carrier-1",
                "vessel_ref": "vessel-1",
            },
            "documents": [
                {
                    "document_ref": "doc-tech",
                    "document_type": "TECHNICAL_SPECIFICATION",
                    "external_reference": "DEMO-DATASHEET-1",
                    "content_hash": "sha256:" + "a" * 64,
                    "media_type": "application/pdf",
                    "related_party_refs": ["manufacturer-1"],
                    "related_goods_refs": ["line-1"],
                },
                {
                    "document_ref": "doc-ownership",
                    "document_type": "OWNERSHIP_RECORD",
                    "content_hash": "sha256:" + "b" * 64,
                    "media_type": "application/json",
                    "related_party_refs": ["owner-1", "buyer-1"],
                },
                {
                    "document_ref": "doc-invoice",
                    "document_type": "INVOICE",
                    "external_reference": "DEMO-INVOICE-1",
                    "content_hash": "sha256:" + "c" * 64,
                    "media_type": "application/pdf",
                    "related_party_refs": ["seller-1", "buyer-1"],
                    "related_goods_refs": ["line-1"],
                },
            ],
            "payment": {
                "payment_ref": "payment-1",
                "payer_ref": "buyer-1",
                "payee_ref": "seller-1",
                "originating_bank_ref": "origin-bank-1",
                "beneficiary_bank_ref": "beneficiary-bank-1",
                "amount": "12500.00",
                "currency": "EUR",
                "origin_country": "RU",
                "destination_country": "CN",
                "purpose": "Payment for synthetic industrial control modules.",
                "evidence_refs": ["doc-invoice"],
            },
        }
    )


def incomplete_customer_onboarding_request() -> ScreeningRequest:
    """Return structurally valid intake with material facts left for controls."""

    return ScreeningRequest.model_validate(
        {
            "schema_version": "1.0.0",
            "tenant_id": "tenant-demo",
            "correlation_id": "crm-customer-incomplete-001",
            "data_classification": "SYNTHETIC",
            "external_object": {
                "system": "demo-crm",
                "object_type": "CUSTOMER",
                "object_id": "demo-customer-incomplete-001",
                "object_version": "1",
            },
            "proposed_action": "CUSTOMER_ONBOARDING",
            "activities": [],
            "legal_nexus": [],
            "parties": [
                {
                    "party_ref": "customer-1",
                    "roles": ["CUSTOMER"],
                    "entity_type": "ORGANIZATION",
                }
            ],
            "ownership_and_control": [],
            "goods": [],
            "route": None,
            "documents": [],
            "payment": None,
        }
    )


def structurally_invalid_request(
    incomplete_request: ScreeningRequest,
) -> dict[str, object]:
    """Return a negative fixture that adapters must reject before application use."""

    payload = incomplete_request.model_dump(mode="json")
    payload["action_due_at"] = "2026-08-07T08:00:00"
    payload["unexpected_business_override"] = "ALLOW"
    return payload


def transaction_review_required_result(request: ScreeningRequest) -> ScreeningResult:
    """Return the expected conservative result for the complete synthetic request."""

    payload: dict[str, object] = {
        "schema_version": "1.0.0",
        "tenant_id": request.tenant_id,
        "correlation_id": request.correlation_id,
        "screening_id": "scr-demo-001",
        "case_id": "case-demo-001",
        "state": "REVIEW_REQUIRED",
        "signal": "YELLOW",
        "highest_priority": "P1",
        "business_action": "HOLD",
        "summary": (
            "Synthetic transaction has unresolved party identity, goods "
            "classification, end-use, and legal-nexus evidence."
        ),
        "rule_evaluations": [
            {
                "evaluation_id": "eval-goods-completeness-001",
                "bundle": {
                    "resource_id": "synthetic-phase1-rule-bundle",
                    "version": "1.0.0",
                    "content_hash": "sha256:" + "8" * 64,
                },
                "rule": {
                    "resource_id": "demo-goods-completeness",
                    "version": "1.0.0",
                    "content_hash": "sha256:" + "2" * 64,
                },
                "outcome": "MISSING_FACTS",
                "missing_fact_paths": ["goods"],
            },
            {
                "evaluation_id": "eval-party-completeness-001",
                "bundle": {
                    "resource_id": "synthetic-phase1-rule-bundle",
                    "version": "1.0.0",
                    "content_hash": "sha256:" + "8" * 64,
                },
                "rule": {
                    "resource_id": "demo-party-completeness",
                    "version": "1.0.0",
                    "content_hash": "sha256:" + "1" * 64,
                },
                "outcome": "MISSING_FACTS",
                "missing_fact_paths": ["parties"],
            },
            {
                "evaluation_id": "eval-nexus-completeness-001",
                "bundle": {
                    "resource_id": "synthetic-phase1-rule-bundle",
                    "version": "1.0.0",
                    "content_hash": "sha256:" + "8" * 64,
                },
                "rule": {
                    "resource_id": "demo-policy-scope",
                    "version": "1.0.0",
                    "content_hash": "sha256:" + "3" * 64,
                },
                "outcome": "MISSING_FACTS",
                "missing_fact_paths": ["legal_nexus"],
            },
        ],
        "findings": [
            {
                "finding_id": "fnd-party-001",
                "kind": "PARTY_IDENTITY_INCOMPLETE",
                "priority": "P1",
                "status": "OPEN",
                "fact_class": "UNKNOWN",
                "summary": (
                    "Buyer registration identifier is missing; resolution is incomplete."
                ),
                "source_refs": ["demo-sanctions:2026-08-06"],
                "rule_evaluation_refs": ["eval-party-completeness-001"],
                "uncertainty": [
                    {
                        "fact_path": "parties.buyer-1.identifiers",
                        "description": (
                            "No authoritative registration identifier was supplied."
                        ),
                    }
                ],
                "required_evidence_refs": ["req-buyer-id"],
                "required_action": "OBTAIN_REGISTRATION_IDENTIFIER_AND_REVIEW",
                "owner_role": "COMPLIANCE_REVIEWER",
                "due_before": "QUOTE_RELEASE",
            },
            {
                "finding_id": "fnd-goods-001",
                "kind": "GOODS_CLASSIFICATION_UNRESOLVED",
                "priority": "P1",
                "status": "OPEN",
                "fact_class": "UNKNOWN",
                "summary": (
                    "Submitted HS and control codes are candidates, not determinations."
                ),
                "evidence_refs": ["doc-tech"],
                "source_refs": ["demo-eu-dual-use:1"],
                "rule_evaluation_refs": ["eval-goods-completeness-001"],
                "uncertainty": [
                    {
                        "fact_path": "goods.line-1.classification_candidates",
                        "description": (
                            "Technical parameters and human classification are incomplete."
                        ),
                    }
                ],
                "required_evidence_refs": ["req-goods-tech", "req-end-use"],
                "required_action": "OBTAIN_TECHNICAL_AND_END_USE_EVIDENCE",
                "owner_role": "EXPORT_CONTROL_REVIEWER",
                "due_before": "QUOTE_RELEASE",
            },
            {
                "finding_id": "fnd-nexus-001",
                "kind": "LEGAL_NEXUS_UNRESOLVED",
                "priority": "P1",
                "status": "OPEN",
                "fact_class": "UNKNOWN",
                "summary": (
                    "Applicable EU Member State and bank policy remain unconfirmed."
                ),
                "evidence_refs": ["doc-invoice"],
                "source_refs": ["demo-policy-scope:1"],
                "rule_evaluation_refs": ["eval-nexus-completeness-001"],
                "uncertainty": [
                    {
                        "fact_path": "legal_nexus",
                        "description": "Legal and contractual scope is incomplete.",
                    }
                ],
                "required_evidence_refs": ["req-legal-nexus"],
                "required_action": "CONFIRM_LEGAL_NEXUS_AND_BANK_POLICY",
                "owner_role": "LEGAL_COMPLIANCE_OWNER",
                "due_before": "QUOTE_RELEASE",
            },
        ],
        "required_evidence": [
            {
                "requirement_ref": "req-buyer-id",
                "evidence_type": "OTHER",
                "description": "Buyer authoritative registration identifier.",
                "status": "REQUIRED",
                "related_party_refs": ["buyer-1"],
            },
            {
                "requirement_ref": "req-goods-tech",
                "evidence_type": "TECHNICAL_SPECIFICATION",
                "description": (
                    "Manufacturer model, part number, and complete technical data sheet."
                ),
                "status": "REQUIRED",
                "related_goods_refs": ["line-1"],
            },
            {
                "requirement_ref": "req-end-use",
                "evidence_type": "END_USER_STATEMENT",
                "description": "Final installation, use, and end-user statement.",
                "status": "REQUIRED",
                "related_party_refs": ["buyer-1"],
                "related_goods_refs": ["line-1"],
            },
            {
                "requirement_ref": "req-legal-nexus",
                "evidence_type": "OTHER",
                "description": (
                    "Applicable Member State and bank/customer policy scope."
                ),
                "status": "REQUIRED",
            },
        ],
        "holds": [
            {
                "hold_id": "hold-quote-001",
                "scope": "QUOTE_RELEASE",
                "reason": "Open P1 findings require evidence and human review.",
                "release_condition": (
                    "Resolve all open P1 findings and record a scoped human decision."
                ),
                "active": True,
            }
        ],
        "version_set": {
            "input_schema": "1.0.0",
            "input_hash": request.canonical_input_hash(),
            "rule_bundle": {
                "resource_id": "synthetic-phase1-rule-bundle",
                "version": "1.0.0",
                "content_hash": "sha256:" + "8" * 64,
            },
            "sources": [
                {
                    "resource_id": "demo-sanctions",
                    "version": "2026-08-06",
                    "content_hash": "sha256:" + "d" * 64,
                },
                {
                    "resource_id": "demo-company-registry",
                    "version": "2026-08-06",
                    "content_hash": "sha256:" + "e" * 64,
                },
            ],
            "rules": [
                {
                    "resource_id": "demo-goods-completeness",
                    "version": "1.0.0",
                    "content_hash": "sha256:" + "2" * 64,
                },
                {
                    "resource_id": "demo-party-completeness",
                    "version": "1.0.0",
                    "content_hash": "sha256:" + "1" * 64,
                },
                {
                    "resource_id": "demo-policy-scope",
                    "version": "1.0.0",
                    "content_hash": "sha256:" + "3" * 64,
                },
            ],
            "matcher": {
                "resource_id": "demo-deterministic",
                "version": "1",
                "content_hash": "sha256:" + "3" * 64,
            },
            "models": [],
        },
        "created_at": "2026-08-06T08:00:00Z",
    }
    payload["result_hash"] = "sha256:" + "0" * 64
    draft = ScreeningResult.model_validate(payload)
    payload["result_hash"] = draft.canonical_result_hash()
    result = ScreeningResult.model_validate(payload)
    assert result.result_hash == result.canonical_result_hash()
    return result


def event_examples(result: ScreeningResult) -> dict[str, EventEnvelope]:
    """Return one minimal webhook envelope for every discriminated event variant."""

    common = {
        "event_version": "1.0",
        "occurred_at": "2026-08-06T08:00:01Z",
        "tenant_id": result.tenant_id,
        "correlation_id": result.correlation_id,
        "subject": {
            "screening_id": result.screening_id,
            "case_id": result.case_id,
            "external_object_type": "QUOTE",
            "external_object_id": "demo-quote-001",
        },
    }
    return {
        "screening.completed": EventEnvelope.model_validate(
            {
                **common,
                "event_id": "evt-screening-001",
                "event_type": "screening.completed",
                "data": {
                    "kind": "screening.completed",
                    "screening_id": result.screening_id,
                    "case_id": result.case_id,
                    "case_state": result.state,
                    "business_action": result.business_action,
                    "signal": result.signal,
                    "result_hash": result.result_hash,
                },
            }
        ),
        "case.state_changed": EventEnvelope.model_validate(
            {
                **common,
                "event_id": "evt-case-state-001",
                "event_type": "case.state_changed",
                "data": {
                    "kind": "case.state_changed",
                    "case_id": result.case_id,
                    "previous_state": "INCOMPLETE",
                    "case_state": result.state,
                    "business_action": result.business_action,
                    "decision_expires_at": None,
                },
            }
        ),
        "human_decision.recorded": EventEnvelope.model_validate(
            {
                **common,
                "event_id": "evt-decision-001",
                "event_type": "human_decision.recorded",
                "data": {
                    "kind": "human_decision.recorded",
                    "case_id": result.case_id,
                    "decision_id": "decision-demo-001",
                    "decision": "HUMAN_BLOCKED",
                    "decision_effective_from": "2026-08-06T08:00:01Z",
                    "decision_expires_at": None,
                },
            }
        ),
    }
