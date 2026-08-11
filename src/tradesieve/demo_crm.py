"""Fixed synthetic CRM demonstrator over TradeSieve canonical contracts.

The module intentionally accepts only repository-owned fixture identifiers. It is a
browser demonstrator for the integration boundary, not a live screening engine and
not a substitute for the governed TS-303/TS-401/TS-501 application path.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, TypedDict

from pydantic import Field

from tradesieve.application.auth import (
    ActorContext,
    ActorType,
    RequestContext,
    Scope,
)
from tradesieve.application.contracts import (
    BusinessAction,
    CaseState,
    ClassificationScheme,
    ContractModel,
    DataClassification,
    DocumentType,
    FactClass,
    FindingStatus,
    Priority,
    ProposedAction,
    ScreeningRequest,
    ScreeningResult,
    Signal,
)
from tradesieve.application.official_screening import (
    OfficialGoodsCandidate,
    OfficialPartyName,
    OfficialScreeningRequest,
    OfficialScreeningResult,
    OfficialTechnicalFact,
)
from tradesieve.application.screening_intake import (
    JSON_MEDIA_TYPE,
    CanonicalScreeningIntake,
)
from tradesieve.config import Settings
from tradesieve.domain.eu_dual_use_technical import (
    TechnicalCellType,
    TechnicalFactId,
    TechnicalFactUnit,
    TechnicalProductFamily,
)

_CREATED_AT = datetime(2026, 8, 10, 1, 30, tzinfo=UTC)
_HASH_ZERO = "sha256:" + "0" * 64
_BUNDLE_HASH = "sha256:" + "8" * 64
_SOURCE_HASH = "sha256:" + "d" * 64
_RULE_HASH = "sha256:" + "4" * 64
_DOCUMENT_HASHES = tuple(
    f"sha256:{hashlib.sha256(f'demo-document-{index}'.encode()).hexdigest()}"
    for index in range(1, 32)
)


class DemoCrmDocument(ContractModel):
    label: str
    status: Literal["AVAILABLE", "MISSING", "PENDING"]


class DemoCrmRecord(ContractModel):
    record_id: str
    quote_number: str
    customer_name: str
    customer_country: str
    sales_owner: str
    service_mode: str
    incoterm: str
    origin: str
    destination: str
    route_summary: str
    goods_summary: str
    shipment_summary: str
    amount: str
    currency: str
    action_label: str
    action_due_at: str
    documents: Annotated[list[DemoCrmDocument], Field(max_length=12)]


class DemoCrmRecordSummary(ContractModel):
    record_id: str
    quote_number: str
    customer_name: str
    customer_country: str
    sales_owner: str
    service_mode: str
    route_summary: str
    goods_summary: str
    amount: str
    currency: str
    screening_status: Literal["NOT_SCREENED"] = "NOT_SCREENED"


class DemoCrmMapping(ContractModel):
    external_system: Literal["huazhou-demo-crm"] = "huazhou-demo-crm"
    external_object_type: Literal["QUOTE"] = "QUOTE"
    proposed_action: ProposedAction
    party_count: int
    goods_line_count: int
    document_count: int
    has_route: bool
    has_payment_path: bool
    canonical_input_hash: str


class DemoCrmListResponse(ContractModel):
    demo_only: Literal[True] = True
    data_classification: Literal["SYNTHETIC"] = "SYNTHETIC"
    company_name: Literal["华舟国际货运（演示）有限公司"] = (
        "华舟国际货运（演示）有限公司"
    )
    records: list[DemoCrmRecordSummary]


class DemoCrmDetailResponse(ContractModel):
    demo_only: Literal[True] = True
    data_classification: Literal["SYNTHETIC"] = "SYNTHETIC"
    record: DemoCrmRecord
    mapping: DemoCrmMapping


class DemoCrmIntegrationEvent(ContractModel):
    label: str
    occurred_at: str
    detail: str


class DemoCrmScreeningResponse(ContractModel):
    demo_only: Literal[True] = True
    precomputed_fixture: Literal[True] = True
    data_classification: Literal["SYNTHETIC"] = "SYNTHETIC"
    warning: Literal["预置合成结果，仅用于演示 CRM 拦截效果，不是实时法律审查。"] = (
        "预置合成结果，仅用于演示 CRM 拦截效果，不是实时法律审查。"
    )
    record_id: str
    canonical_input_hash: str
    result: ScreeningResult
    integration_events: list[DemoCrmIntegrationEvent]


class DemoCrmOfficialScreeningResponse(ContractModel):
    demo_only: Literal[True] = True
    live_official_sources: Literal[True] = True
    data_classification: Literal["SYNTHETIC_TRANSACTION"] = "SYNTHETIC_TRANSACTION"
    warning: Literal[
        "合成交易已调用真实活跃官方来源；结果用于技术验收，不是法律放行。"
    ] = "合成交易已调用真实活跃官方来源；结果用于技术验收，不是法律放行。"
    record_id: str
    result: OfficialScreeningResult
    integration_events: list[DemoCrmIntegrationEvent]


@dataclass(frozen=True, slots=True)
class _FindingFixture:
    kind: str
    priority: Priority
    fact_class: FactClass
    summary: str
    required_action: str
    owner_role: str
    evidence_type: DocumentType | None = None
    evidence_description: str | None = None
    fact_path: str | None = None
    uncertainty: str | None = None
    source_ref: str | None = None


@dataclass(frozen=True, slots=True)
class _Scenario:
    record: DemoCrmRecord
    request: ScreeningRequest
    result: ScreeningResult


class _ResultFixture(TypedDict):
    state: CaseState
    signal: Signal
    highest_priority: Priority
    business_action: BusinessAction
    summary: str
    findings: tuple[_FindingFixture, ...]
    suffix: str


def _document_hash(index: int) -> str:
    return _DOCUMENT_HASHES[index - 1]


def _document(
    index: int,
    reference: str,
    document_type: str,
    *,
    party_refs: list[str] | None = None,
    goods_refs: list[str] | None = None,
) -> dict[str, object]:
    return {
        "document_ref": reference,
        "document_type": document_type,
        "external_reference": f"SYNTHETIC-DOC-{index:02d}",
        "content_hash": _document_hash(index),
        "media_type": "application/pdf",
        "related_party_refs": party_refs or [],
        "related_goods_refs": goods_refs or [],
    }


def _base_request(
    *,
    record_id: str,
    correlation_id: str,
    buyer_name: str,
    buyer_country: str,
    buyer_identifier: str | None = None,
    end_user_name: str | None,
    end_user_country: str | None,
    bank_name: str,
    bank_country: str,
    goods: dict[str, object],
    route: dict[str, object],
    documents: list[dict[str, object]],
    amount: str,
    currency: str,
    purpose: str,
) -> ScreeningRequest:
    buyer: dict[str, object] = {
        "party_ref": "buyer-1",
        "roles": ["BUYER", "CONSIGNEE", "PAYER"],
        "entity_type": "ORGANIZATION",
        "legal_name": buyer_name,
        "country": buyer_country,
    }
    if buyer_identifier is not None:
        buyer["identifiers"] = [
            {
                "type": "SYNTHETIC_REGISTRATION_ID",
                "value": buyer_identifier,
                "issuer": buyer_country,
            }
        ]
    parties: list[dict[str, object]] = [
        {
            "party_ref": "seller-1",
            "roles": ["SELLER", "PAYEE", "SHIPPER"],
            "entity_type": "ORGANIZATION",
            "legal_name": "华舟国际货运（演示）有限公司",
            "country": "CN",
            "identifiers": [
                {
                    "type": "SYNTHETIC_REGISTRATION_ID",
                    "value": "CN-SYNTHETIC-91310000-001",
                    "issuer": "CN",
                },
            ],
            "address": "上海市虹口区合成路 18 号",
        },
        buyer,
        {
            "party_ref": "bank-1",
            "roles": ["ORIGINATING_BANK"],
            "entity_type": "BANK",
            "legal_name": bank_name,
            "country": bank_country,
        },
    ]
    if end_user_name is not None and end_user_country is not None:
        parties.append(
            {
                "party_ref": "end-user-1",
                "roles": ["END_USER"],
                "entity_type": "ORGANIZATION",
                "legal_name": end_user_name,
                "country": end_user_country,
            }
        )
        end_use = goods.get("end_use")
        if isinstance(end_use, dict):
            end_use["end_user_ref"] = "end-user-1"

    return ScreeningRequest.model_validate(
        {
            "schema_version": "1.0.0",
            "tenant_id": "tenant-demo",
            "correlation_id": correlation_id,
            "data_classification": DataClassification.SYNTHETIC,
            "external_object": {
                "system": "huazhou-demo-crm",
                "object_type": "QUOTE",
                "object_id": record_id,
                "object_version": "1",
            },
            "proposed_action": "QUOTE_RELEASE",
            "activities": ["SALE", "SUPPLY", "EXPORT", "TRANSPORT"],
            "action_due_at": "2026-08-10T10:00:00Z",
            "legal_nexus": [
                {
                    "nexus_ref": "nexus-policy-1",
                    "nexus_type": "BANK_POLICY",
                    "regime_code": "SYNTHETIC_BANK_POLICY",
                    "basis": "Synthetic bank and customer compliance policy scope.",
                    "fact_class": "SOURCE_ASSERTION",
                    "source_refs": ["demo-crm-policy:1.0.0"],
                },
            ],
            "parties": parties,
            "ownership_and_control": [],
            "goods": [goods],
            "route": route,
            "documents": documents,
            "payment": {
                "payment_ref": "payment-1",
                "payer_ref": "buyer-1",
                "payee_ref": "seller-1",
                "originating_bank_ref": "bank-1",
                "amount": amount,
                "currency": currency,
                "origin_country": bank_country,
                "destination_country": "CN",
                "purpose": purpose,
                "evidence_refs": [documents[0]["document_ref"]],
            },
        }
    )


def _result(
    request: ScreeningRequest,
    *,
    state: CaseState,
    signal: Signal,
    highest_priority: Priority,
    business_action: BusinessAction,
    summary: str,
    findings: tuple[_FindingFixture, ...],
    suffix: str,
) -> ScreeningResult:
    evaluation_id = f"eval-{suffix}"
    rule_id = f"demo-crm-{suffix}"
    missing_paths = sorted(
        {
            "goods"
            if item.fact_path and item.fact_path.startswith("goods")
            else "parties"
            if item.fact_path and item.fact_path.startswith("parties")
            else "route"
            if item.fact_path and item.fact_path.startswith("route")
            else "payment"
            if item.fact_path and item.fact_path.startswith("payment")
            else "legal_nexus"
            for item in findings
            if item.fact_class is FactClass.UNKNOWN
        }
    )
    outcome = "MISSING_FACTS" if missing_paths else "FACTS_PRESENT"
    required_evidence: list[dict[str, object]] = []
    finding_values: list[dict[str, object]] = []
    for index, item in enumerate(findings, start=1):
        requirement_ref = f"req-{suffix}-{index}"
        has_requirement = (
            item.evidence_type is not None and item.evidence_description is not None
        )
        if has_requirement:
            required_evidence.append(
                {
                    "requirement_ref": requirement_ref,
                    "evidence_type": item.evidence_type,
                    "description": item.evidence_description,
                    "status": "REQUIRED",
                }
            )
        uncertainty = []
        if item.fact_path is not None and item.uncertainty is not None:
            uncertainty.append(
                {
                    "fact_path": item.fact_path,
                    "description": item.uncertainty,
                }
            )
        finding_values.append(
            {
                "finding_id": f"fnd-{suffix}-{index}",
                "kind": item.kind,
                "priority": item.priority,
                "status": FindingStatus.OPEN,
                "fact_class": item.fact_class,
                "summary": item.summary,
                "evidence_refs": [],
                "source_refs": [item.source_ref] if item.source_ref else [],
                "rule_evaluation_refs": [evaluation_id],
                "uncertainty": uncertainty,
                "required_evidence_refs": [requirement_ref] if has_requirement else [],
                "required_action": item.required_action,
                "owner_role": item.owner_role,
                "due_before": request.proposed_action,
            }
        )

    rules = [
        {
            "resource_id": rule_id,
            "version": "1.0.0",
            "content_hash": _RULE_HASH,
        }
    ]
    hold_values: list[dict[str, object]] = []
    if business_action is not BusinessAction.NO_ACTION:
        hold_values.append(
            {
                "hold_id": f"hold-{suffix}",
                "scope": request.proposed_action,
                "reason": "合成审查仍有待处理事项，CRM 必须保留业务门禁。",
                "release_condition": "由授权合规人员处理开放事项并记录适用范围。",
                "active": True,
            }
        )
    payload: dict[str, object] = {
        "schema_version": "1.0.0",
        "tenant_id": request.tenant_id,
        "correlation_id": request.correlation_id,
        "screening_id": f"scr-{suffix}",
        "case_id": f"case-{suffix}",
        "state": state,
        "signal": signal,
        "highest_priority": highest_priority,
        "business_action": business_action,
        "summary": summary,
        "rule_evaluations": [
            {
                "evaluation_id": evaluation_id,
                "bundle": {
                    "resource_id": "synthetic-demo-crm-bundle",
                    "version": "1.0.0",
                    "content_hash": _BUNDLE_HASH,
                },
                "rule": rules[0],
                "outcome": outcome,
                "missing_fact_paths": missing_paths,
            }
        ],
        "findings": finding_values,
        "required_evidence": required_evidence,
        "holds": hold_values,
        "version_set": {
            "input_schema": "1.0.0",
            "input_hash": request.canonical_input_hash(),
            "sources": [
                {
                    "resource_id": "synthetic-demo-crm-source",
                    "version": "2026-08-10",
                    "content_hash": _SOURCE_HASH,
                }
            ],
            "rule_bundle": {
                "resource_id": "synthetic-demo-crm-bundle",
                "version": "1.0.0",
                "content_hash": _BUNDLE_HASH,
            },
            "rules": rules,
            "matcher": {
                "resource_id": "synthetic-deterministic-fixture",
                "version": "1",
                "content_hash": _RULE_HASH,
            },
            "models": [],
        },
        "result_hash": _HASH_ZERO,
        "created_at": _CREATED_AT,
    }
    draft = ScreeningResult.model_validate(payload)
    payload["result_hash"] = draft.canonical_result_hash()
    result = ScreeningResult.model_validate(payload)
    if result.result_hash != result.canonical_result_hash():
        raise RuntimeError("synthetic CRM result integrity failure")
    return result


def _record(
    *,
    record_id: str,
    quote_number: str,
    customer_name: str,
    customer_country: str,
    sales_owner: str,
    service_mode: str,
    incoterm: str,
    origin: str,
    destination: str,
    route_summary: str,
    goods_summary: str,
    shipment_summary: str,
    amount: str,
    currency: str,
    action_due_at: str,
    documents: list[tuple[str, str]],
) -> DemoCrmRecord:
    return DemoCrmRecord.model_validate(
        {
            "record_id": record_id,
            "quote_number": quote_number,
            "customer_name": customer_name,
            "customer_country": customer_country,
            "sales_owner": sales_owner,
            "service_mode": service_mode,
            "incoterm": incoterm,
            "origin": origin,
            "destination": destination,
            "route_summary": route_summary,
            "goods_summary": goods_summary,
            "shipment_summary": shipment_summary,
            "amount": amount,
            "currency": currency,
            "action_label": "报价放行",
            "action_due_at": action_due_at,
            "documents": [
                {"label": label, "status": status} for label, status in documents
            ],
        }
    )


def _make_scenarios() -> dict[str, _Scenario]:
    scenarios: list[tuple[DemoCrmRecord, ScreeningRequest, _ResultFixture]] = []

    record = _record(
        record_id="crm-quote-260810-0047",
        quote_number="HZ-260810-0047",
        customer_name="Benevolence International Foundation（公开名单测试样本）",
        customer_country="美国 / 俄罗斯路线（合成交易）",
        sales_owner="顾明远",
        service_mode="中欧班列 + 卡车",
        incoterm="DAP Moscow",
        origin="上海",
        destination="莫斯科",
        route_summary="上海 - 阿拉山口 - 阿拉木图 - 莫斯科",
        goods_summary="工业 PLC I/O 模块及伺服驱动器",
        shipment_summary="6 木箱，2,480 kg，14.6 m³",
        amount="86,420.00",
        currency="EUR",
        action_due_at="今天 18:00 前",
        documents=[
            ("商业发票", "AVAILABLE"),
            ("装箱单", "AVAILABLE"),
            ("技术规格书", "PENDING"),
            ("最终用户声明", "MISSING"),
        ],
    )
    documents = [
        _document(1, "doc-invoice", "INVOICE", party_refs=["buyer-1"]),
        _document(2, "doc-registration", "OTHER", party_refs=["buyer-1"]),
    ]
    request = _base_request(
        record_id=record.record_id,
        correlation_id="crm-demo-red-0047",
        buyer_name="Benevolence International Foundation",
        buyer_country="US",
        buyer_identifier="RU-SYNTHETIC-7704-884219",
        end_user_name=None,
        end_user_country=None,
        bank_name="Synthetic Eurasia Settlement Bank",
        bank_country="KZ",
        goods={
            "line_ref": "line-1",
            "description": "Industrial PLC I/O modules and servo drives",
            "manufacturer": "苏州澜工自动化设备（演示）有限公司",
            "classification_candidates": [
                {
                    "candidate_ref": "hs-1",
                    "scheme": "HS",
                    "code": "853710",
                    "candidate_only": True,
                    "rationale": "Supplier candidate code, pending review.",
                },
                {
                    "candidate_ref": "annex-i-1",
                    "scheme": "EU_DUAL_USE_ANNEX_I",
                    "code": "3A001",
                    "candidate_only": True,
                    "rationale": (
                        "Public Annex I code used only to exercise the live gate."
                    ),
                },
            ],
            "quantity": "36",
            "quantity_unit": "pieces",
            "total_value": "86420.00",
            "currency": "EUR",
            "origin_country": "CN",
            "end_use": {
                "description": "Industrial automation; installation site unavailable.",
                "country": "RU",
            },
        },
        route={
            "origin_country": "CN",
            "loading_location": "Shanghai",
            "transit_countries": ["KZ"],
            "discharge_location": "Moscow rail terminal",
            "destination_country": "RU",
            "final_use_country": "RU",
        },
        documents=documents,
        amount="86420.00",
        currency="EUR",
        purpose="Synthetic payment for industrial control equipment.",
    )
    scenarios.append(
        (
            record,
            request,
            {
                "state": CaseState.ESCALATE,
                "signal": Signal.RED,
                "highest_priority": Priority.P0,
                "business_action": BusinessAction.ESCALATE,
                "summary": "合成名单登记号精确命中，且最终用户与技术参数不完整。暂停报价并升级合规复核。",
                "suffix": "red-0047",
                "findings": (
                    _FindingFixture(
                        kind="SYNTHETIC_EXACT_IDENTIFIER_MATCH",
                        priority=Priority.P0,
                        fact_class=FactClass.SOURCE_ASSERTION,
                        summary="买方登记号与合成制裁数据中的记录精确一致。该结果仅用于演示强标识符拦截。",
                        required_action="SUSPEND_AND_RESOLVE_SYNTHETIC_MATCH",
                        owner_role="COMPLIANCE_REVIEWER",
                        evidence_type=DocumentType.OTHER,
                        evidence_description="主体登记文件、别名和所有权控制证明。",
                        source_ref="synthetic-demo-crm-source:2026-08-10",
                    ),
                    _FindingFixture(
                        kind="END_USER_AND_END_USE_INCOMPLETE",
                        priority=Priority.P1,
                        fact_class=FactClass.UNKNOWN,
                        summary="最终用户、安装地点和具体用途没有形成可核验证据。",
                        required_action="OBTAIN_END_USER_STATEMENT",
                        owner_role="SALES_OPERATIONS",
                        evidence_type=DocumentType.END_USER_STATEMENT,
                        evidence_description="由最终用户签署的用途、安装地点和转售限制声明。",
                        fact_path="goods.line-1.end_use",
                        uncertainty="最终用户及安装地点未知。",
                    ),
                    _FindingFixture(
                        kind="GOODS_CLASSIFICATION_UNRESOLVED",
                        priority=Priority.P1,
                        fact_class=FactClass.UNKNOWN,
                        summary="工业控制模块只有供应商 HS 候选编码，缺少型号和完整技术参数。",
                        required_action="OBTAIN_TECHNICAL_SPECIFICATION",
                        owner_role="EXPORT_CONTROL_REVIEWER",
                        evidence_type=DocumentType.TECHNICAL_SPECIFICATION,
                        evidence_description="制造商、型号、固件版本及完整技术规格书。",
                        fact_path="goods.line-1.technical_specification",
                        uncertainty="无法仅凭品名和 HS 候选完成敏感货物判断。",
                    ),
                ),
            },
        )
    )

    record = _record(
        record_id="crm-quote-260810-0039",
        quote_number="HZ-260810-0039",
        customer_name="莱茵仓储技术（合成）有限公司",
        customer_country="德国",
        sales_owner="许之宁",
        service_mode="空运",
        incoterm="CIP Frankfurt",
        origin="深圳",
        destination="法兰克福",
        route_summary="深圳宝安 - 香港 - 法兰克福",
        goods_summary="高能量密度锂离子二次电芯（非电池包）",
        shipment_summary="12 箱，386 kg，1.8 m³；电芯单体运输",
        amount="42,780.00",
        currency="EUR",
        action_due_at="明天 12:00 前",
        documents=[
            ("商业发票", "AVAILABLE"),
            ("制造商技术规格书", "AVAILABLE"),
            ("MSDS", "MISSING"),
            ("UN38.3 测试概要", "MISSING"),
            ("航空运输鉴定书", "PENDING"),
        ],
    )
    documents = [_document(3, "doc-invoice", "INVOICE", party_refs=["buyer-1"])]
    request = _base_request(
        record_id=record.record_id,
        correlation_id="crm-demo-yellow-0039",
        buyer_name="Rheinlager Technik Demo GmbH",
        buyer_country="DE",
        end_user_name="Rheinlager Technik Demo GmbH",
        end_user_country="DE",
        bank_name="Synthetic Main Bank Frankfurt",
        bank_country="DE",
        goods={
            "line_ref": "line-1",
            "description": (
                "High-energy-density lithium-ion secondary cells; not assembled "
                "batteries"
            ),
            "manufacturer": "东莞恒芯能源（演示）有限公司",
            "model_or_part_number": "HX-4820-SYNTHETIC",
            "technical_specification": (
                "Synthetic reviewed manufacturer sheet: secondary cell; 380 Wh/kg "
                "at 20 C; not a battery assembly."
            ),
            "classification_candidates": [
                {
                    "candidate_ref": "annex-i-1",
                    "scheme": "EU_DUAL_USE_ANNEX_I",
                    "code": "3A001",
                    "candidate_only": True,
                    "rationale": (
                        "Synthetic qualified candidate for deterministic 3A001.e.1 "
                        "technical-assertion acceptance."
                    ),
                }
            ],
            "quantity": "120",
            "quantity_unit": "cells",
            "total_value": "42780.00",
            "currency": "EUR",
            "origin_country": "CN",
            "end_use": {
                "description": "Warehouse mobile power units",
                "country": "DE",
            },
        },
        route={
            "origin_country": "CN",
            "loading_location": "Shenzhen Bao'an",
            "transit_countries": ["HK"],
            "discharge_location": "Frankfurt Airport",
            "destination_country": "DE",
            "final_use_country": "DE",
        },
        documents=documents,
        amount="42780.00",
        currency="EUR",
        purpose="Synthetic payment for high-energy-density lithium-ion cells.",
    )
    scenarios.append(
        (
            record,
            request,
            {
                "state": CaseState.INCOMPLETE,
                "signal": Signal.YELLOW,
                "highest_priority": Priority.P1,
                "business_action": BusinessAction.REQUEST_EVIDENCE,
                "summary": "危险品运输与电池合规资料不完整。报价可继续准备，但不得提交承运人确认。",
                "suffix": "yellow-0039",
                "findings": (
                    _FindingFixture(
                        kind="BATTERY_TRANSPORT_DOCUMENTS_INCOMPLETE",
                        priority=Priority.P1,
                        fact_class=FactClass.UNKNOWN,
                        summary="缺少 MSDS、UN38.3 测试概要和适用的运输鉴定资料。",
                        required_action="OBTAIN_BATTERY_TRANSPORT_DOCUMENTS",
                        owner_role="LOGISTICS_OPERATOR",
                        evidence_type=DocumentType.TECHNICAL_SPECIFICATION,
                        evidence_description="MSDS、UN38.3 测试概要及承运人要求的运输鉴定文件。",
                        fact_path="goods.line-1.technical_specification",
                        uncertainty="电池规格和运输条件尚未核实。",
                    ),
                ),
            },
        )
    )

    record = _record(
        record_id="crm-quote-260809-0186",
        quote_number="HZ-260809-0186",
        customer_name="海湾机电贸易（合成）有限公司",
        customer_country="阿联酋",
        sales_owner="周静宜",
        service_mode="海运整箱",
        incoterm="CFR Jebel Ali",
        origin="宁波",
        destination="杰贝阿里",
        route_summary="宁波 - 新加坡 - 杰贝阿里，后续目的地未确认",
        goods_summary="数控机床伺服驱动器及编码器",
        shipment_summary="1 x 20GP，8,960 kg，24.2 m³",
        amount="128,650.00",
        currency="USD",
        action_due_at="8 月 12 日截关前",
        documents=[
            ("商业发票", "AVAILABLE"),
            ("技术规格书", "AVAILABLE"),
            ("最终用户声明", "MISSING"),
            ("转售路径说明", "MISSING"),
        ],
    )
    documents = [
        _document(4, "doc-invoice", "INVOICE", party_refs=["buyer-1"]),
        _document(5, "doc-tech", "TECHNICAL_SPECIFICATION", goods_refs=["line-1"]),
    ]
    request = _base_request(
        record_id=record.record_id,
        correlation_id="crm-demo-yellow-0186",
        buyer_name="Gulf Motion Trading Demo FZE",
        buyer_country="AE",
        end_user_name=None,
        end_user_country=None,
        bank_name="Synthetic Gulf Commercial Bank",
        bank_country="AE",
        goods={
            "line_ref": "line-1",
            "description": "CNC servo drives and optical encoders",
            "manufacturer": "无锡泰衡精密控制（演示）有限公司",
            "model_or_part_number": "TH-SD8-SYNTHETIC",
            "technical_specification": "Rated power and encoder resolution provided.",
            "classification_candidates": [
                {
                    "candidate_ref": "hs-1",
                    "scheme": "HS",
                    "code": "850440",
                    "candidate_only": True,
                }
            ],
            "quantity": "48",
            "quantity_unit": "sets",
            "total_value": "128650.00",
            "currency": "USD",
            "origin_country": "CN",
            "end_use": {
                "description": "Resale for industrial automation projects",
                "country": "AE",
            },
        },
        route={
            "origin_country": "CN",
            "loading_location": "Ningbo",
            "transit_countries": ["SG"],
            "discharge_location": "Jebel Ali",
            "destination_country": "AE",
            "final_use_country": None,
        },
        documents=documents,
        amount="128650.00",
        currency="USD",
        purpose="Synthetic payment for industrial servo drives.",
    )
    scenarios.append(
        (
            record,
            request,
            {
                "state": CaseState.REVIEW_REQUIRED,
                "signal": Signal.YELLOW,
                "highest_priority": Priority.P1,
                "business_action": BusinessAction.HOLD,
                "summary": "最终用户、最终使用国和转售路径没有闭环。暂停报价放行并补充证据。",
                "suffix": "yellow-0186",
                "findings": (
                    _FindingFixture(
                        kind="REEXPORT_PATH_UNRESOLVED",
                        priority=Priority.P1,
                        fact_class=FactClass.UNKNOWN,
                        summary="贸易商位于转运枢纽，最终用户和最终使用国尚未提供。",
                        required_action="OBTAIN_FINAL_USER_AND_REEXPORT_PATH",
                        owner_role="COMPLIANCE_REVIEWER",
                        evidence_type=DocumentType.END_USER_STATEMENT,
                        evidence_description="最终用户、最终使用国、安装地点和转售路径说明。",
                        fact_path="route.final_use_country",
                        uncertainty="目的港不等于最终使用地点。",
                    ),
                ),
            },
        )
    )

    record = _record(
        record_id="crm-quote-260810-0052",
        quote_number="HZ-260810-0052",
        customer_name="北海工业维护（合成）有限公司",
        customer_country="荷兰",
        sales_owner="韩致远",
        service_mode="海运拼箱",
        incoterm="CIF Rotterdam",
        origin="青岛",
        destination="鹿特丹",
        route_summary="青岛 - 釜山 - 鹿特丹",
        goods_summary="工业橡胶软管、密封件和维修工具",
        shipment_summary="9 托，3,120 kg，10.4 m³",
        amount="18,736.50",
        currency="EUR",
        action_due_at="8 月 14 日开船前",
        documents=[
            ("商业发票", "AVAILABLE"),
            ("装箱单", "AVAILABLE"),
            ("产品规格书", "AVAILABLE"),
            ("最终用途声明", "AVAILABLE"),
        ],
    )
    documents = [
        _document(6, "doc-invoice", "INVOICE", party_refs=["buyer-1"]),
        _document(7, "doc-tech", "TECHNICAL_SPECIFICATION", goods_refs=["line-1"]),
        _document(8, "doc-end-use", "END_USER_STATEMENT", party_refs=["end-user-1"]),
    ]
    request = _base_request(
        record_id=record.record_id,
        correlation_id="crm-demo-green-0052",
        buyer_name="Northsea Industrial Maintenance Demo B.V.",
        buyer_country="NL",
        end_user_name="Northsea Industrial Maintenance Demo B.V.",
        end_user_country="NL",
        bank_name="Synthetic Netherlands Commercial Bank",
        bank_country="NL",
        goods={
            "line_ref": "line-1",
            "description": "Industrial rubber hoses, seals and maintenance tools",
            "manufacturer": "青岛海呈橡塑制品（演示）有限公司",
            "model_or_part_number": "HC-MRO-SYNTHETIC",
            "technical_specification": "Material, dimensions and pressure rating provided.",
            "classification_candidates": [
                {
                    "candidate_ref": "hs-1",
                    "scheme": "HS",
                    "code": "400921",
                    "candidate_only": True,
                }
            ],
            "quantity": "420",
            "quantity_unit": "pieces",
            "total_value": "18736.50",
            "currency": "EUR",
            "origin_country": "CN",
            "end_use": {
                "description": "Maintenance of food-packaging production lines",
                "country": "NL",
            },
        },
        route={
            "origin_country": "CN",
            "loading_location": "Qingdao",
            "transit_countries": ["KR"],
            "discharge_location": "Rotterdam",
            "destination_country": "NL",
            "final_use_country": "NL",
        },
        documents=documents,
        amount="18736.50",
        currency="EUR",
        purpose="Synthetic payment for industrial maintenance supplies.",
    )
    scenarios.append(
        (
            record,
            request,
            {
                "state": CaseState.REVIEW_REQUIRED,
                "signal": Signal.GREEN_CANDIDATE,
                "highest_priority": Priority.NONE,
                "business_action": BusinessAction.MONITOR,
                "summary": "合成确定性检查未发现开放 P0/P1。该结果只是绿灯候选，仍需授权人工确认适用范围。",
                "suffix": "green-0052",
                "findings": (),
            },
        )
    )

    record = _record(
        record_id="crm-quote-260808-0124",
        quote_number="HZ-260808-0124",
        customer_name="马尔马拉智能系统（合成）有限公司",
        customer_country="土耳其",
        sales_owner="陆思齐",
        service_mode="海运拼箱",
        incoterm="CPT Istanbul",
        origin="上海",
        destination="伊斯坦布尔",
        route_summary="上海 - 丹吉尔 - 伊斯坦布尔",
        goods_summary="高精度压力传感器及数据采集模块",
        shipment_summary="4 木箱，720 kg，3.6 m³",
        amount="63,980.00",
        currency="USD",
        action_due_at="8 月 11 日 16:00 前",
        documents=[
            ("商业发票", "AVAILABLE"),
            ("型号清单", "AVAILABLE"),
            ("完整技术参数", "PENDING"),
            ("最终用途声明", "MISSING"),
        ],
    )
    documents = [
        _document(9, "doc-invoice", "INVOICE", party_refs=["buyer-1"]),
        _document(
            10, "doc-model-list", "TECHNICAL_SPECIFICATION", goods_refs=["line-1"]
        ),
    ]
    request = _base_request(
        record_id=record.record_id,
        correlation_id="crm-demo-yellow-0124",
        buyer_name="Marmara Smart Systems Demo A.Ş.",
        buyer_country="TR",
        end_user_name=None,
        end_user_country=None,
        bank_name="Synthetic Bosphorus Participation Bank",
        bank_country="TR",
        goods={
            "line_ref": "line-1",
            "description": "High-accuracy pressure sensors and data acquisition modules",
            "manufacturer": "杭州衡微仪器（演示）有限公司",
            "model_or_part_number": "HW-PS90-SYNTHETIC",
            "classification_candidates": [
                {
                    "candidate_ref": "hs-1",
                    "scheme": "HS",
                    "code": "902620",
                    "candidate_only": True,
                }
            ],
            "quantity": "80",
            "quantity_unit": "sets",
            "total_value": "63980.00",
            "currency": "USD",
            "origin_country": "CN",
            "end_use": {
                "description": "Industrial monitoring projects; site not supplied.",
                "country": "TR",
            },
        },
        route={
            "origin_country": "CN",
            "loading_location": "Shanghai",
            "transit_countries": ["MA"],
            "discharge_location": "Istanbul",
            "destination_country": "TR",
            "final_use_country": "TR",
        },
        documents=documents,
        amount="63980.00",
        currency="USD",
        purpose="Synthetic payment for pressure sensors and data modules.",
    )
    scenarios.append(
        (
            record,
            request,
            {
                "state": CaseState.INCOMPLETE,
                "signal": Signal.YELLOW,
                "highest_priority": Priority.P1,
                "business_action": BusinessAction.REQUEST_EVIDENCE,
                "summary": "精密传感器技术参数和最终用途资料不足。补件并由出口管制人员复核。",
                "suffix": "yellow-0124",
                "findings": (
                    _FindingFixture(
                        kind="SENSITIVE_GOODS_CANDIDATE_INCOMPLETE",
                        priority=Priority.P1,
                        fact_class=FactClass.UNKNOWN,
                        summary="品名和 HS 候选指向精密传感器，但缺少量程、精度、环境等级等参数。",
                        required_action="OBTAIN_FULL_TECHNICAL_PARAMETERS",
                        owner_role="EXPORT_CONTROL_REVIEWER",
                        evidence_type=DocumentType.TECHNICAL_SPECIFICATION,
                        evidence_description="制造商完整数据表，包括量程、精度、工作环境和接口。",
                        fact_path="goods.line-1.technical_specification",
                        uncertainty="不能仅凭 HS 候选判断是否属于受控或敏感物项。",
                    ),
                    _FindingFixture(
                        kind="END_USE_INCOMPLETE",
                        priority=Priority.P1,
                        fact_class=FactClass.UNKNOWN,
                        summary="最终用户、项目地点和具体工业用途没有形成证据。",
                        required_action="OBTAIN_END_USER_STATEMENT",
                        owner_role="SALES_OPERATIONS",
                        evidence_type=DocumentType.END_USER_STATEMENT,
                        evidence_description="最终用户、项目地点、具体用途及不转售声明。",
                        fact_path="parties.end-user-1",
                        uncertainty="当前买方不等于已确认最终用户。",
                    ),
                ),
            },
        )
    )

    result: dict[str, _Scenario] = {}
    for scenario_record, scenario_request, result_kwargs in scenarios:
        scenario_result = _result(scenario_request, **result_kwargs)
        result[scenario_record.record_id] = _Scenario(
            record=scenario_record,
            request=scenario_request,
            result=scenario_result,
        )
    return result


_SCENARIOS = _make_scenarios()


def list_demo_crm_records(settings: Settings) -> DemoCrmListResponse:
    _require_demo(settings)
    records = [
        DemoCrmRecordSummary(
            **scenario.record.model_dump(
                exclude={
                    "documents",
                    "incoterm",
                    "origin",
                    "destination",
                    "shipment_summary",
                    "action_label",
                    "action_due_at",
                }
            )
        )
        for scenario in _SCENARIOS.values()
    ]
    return DemoCrmListResponse(records=records)


def get_demo_crm_record(settings: Settings, record_id: str) -> DemoCrmDetailResponse:
    _require_demo(settings)
    scenario = _SCENARIOS.get(record_id)
    if scenario is None:
        raise KeyError(record_id)
    request = scenario.request
    return DemoCrmDetailResponse(
        record=scenario.record,
        mapping=DemoCrmMapping(
            proposed_action=request.proposed_action,
            party_count=len(request.parties),
            goods_line_count=len(request.goods),
            document_count=len(request.documents),
            has_route=request.route is not None,
            has_payment_path=request.payment is not None,
            canonical_input_hash=request.canonical_input_hash(),
        ),
    )


def screen_demo_crm_record(
    settings: Settings,
    record_id: str,
    *,
    now: datetime,
) -> DemoCrmScreeningResponse:
    _require_demo(settings)
    if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("demo CRM screening time must be timezone-aware")
    scenario = _SCENARIOS.get(record_id)
    if scenario is None:
        raise KeyError(record_id)
    normalized_now = now.astimezone(UTC)
    raw_body = json.dumps(
        scenario.request.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    context = RequestContext(
        actor=ActorContext(
            subject="demo-crm-gateway",
            client_id="huazhou-demo-crm",
            tenant_id=scenario.request.tenant_id,
            actor_type=ActorType.SERVICE,
            scopes=frozenset({Scope.SCREENING_SUBMIT}),
            roles=frozenset(),
            issuer="http://localhost/tradesieve-demo-crm",
            audience="tradesieve-demo",
            issued_at=normalized_now - timedelta(minutes=1),
            expires_at=normalized_now + timedelta(minutes=30),
            demo_identity=True,
        ),
        tenant_id=scenario.request.tenant_id,
        correlation_id=scenario.request.correlation_id,
    )
    intake = CanonicalScreeningIntake.decode(
        raw_body,
        JSON_MEDIA_TYPE,
        context=context,
        received_at=normalized_now,
    )
    if intake.request.canonical_input_hash() != scenario.result.version_set.input_hash:
        raise RuntimeError("synthetic CRM fixture input/result mismatch")
    rendered_time = normalized_now.isoformat().replace("+00:00", "Z")
    return DemoCrmScreeningResponse(
        record_id=record_id,
        canonical_input_hash=intake.canonical_hash,
        result=scenario.result,
        integration_events=[
            DemoCrmIntegrationEvent(
                label="CRM 提交报价门禁",
                occurred_at=rendered_time,
                detail="CRM 仅提交规范字段和幂等业务引用。",
            ),
            DemoCrmIntegrationEvent(
                label="TradeSieve 返回审查结果",
                occurred_at=rendered_time,
                detail="CRM 保存不透明 screening/case ID 并执行 business_action。",
            ),
        ],
    )


def official_request_for_demo_crm_record(
    settings: Settings,
    record_id: str,
) -> OfficialScreeningRequest:
    """Map one fixed synthetic CRM record into the live official-source contract."""

    _require_demo(settings)
    scenario = _SCENARIOS.get(record_id)
    if scenario is None:
        raise KeyError(record_id)
    annex_candidates = [
        candidate.code
        for line in scenario.request.goods
        for candidate in line.classification_candidates
        if candidate.scheme is ClassificationScheme.EU_DUAL_USE_ANNEX_I
    ]
    annex_code = annex_candidates[0] if len(annex_candidates) == 1 else None
    hs_candidates = [
        candidate.code
        for line in scenario.request.goods
        for candidate in line.classification_candidates
        if candidate.scheme is ClassificationScheme.HS
    ]
    hs_code = hs_candidates[0] if len(hs_candidates) == 1 else None
    technical_specification_available = bool(
        scenario.request.goods
        and all(line.technical_specification for line in scenario.request.goods)
    )
    if record_id == "crm-quote-260810-0039":
        goods = OfficialGoodsCandidate(
            hs_code=hs_code,
            annex_i_code=annex_code,
            classification_verified=True,
            technical_specification_available=True,
            product_family=TechnicalProductFamily.ELECTROCHEMICAL_CELL,
            technical_facts=[
                OfficialTechnicalFact(
                    fact_id=TechnicalFactId.IS_BATTERY,
                    unit=TechnicalFactUnit.BOOLEAN,
                    boolean_value=False,
                    evidence_ref="manufacturer-datasheet-hx-4820",
                    verified=True,
                ),
                OfficialTechnicalFact(
                    fact_id=TechnicalFactId.CELL_TYPE,
                    unit=TechnicalFactUnit.CELL_TYPE,
                    text_value=TechnicalCellType.SECONDARY,
                    evidence_ref="manufacturer-datasheet-hx-4820",
                    verified=True,
                ),
                OfficialTechnicalFact.model_validate(
                    {
                        "fact_id": "energy_density_wh_per_kg",
                        "unit": "WH_PER_KG",
                        "numeric_value": "380",
                        "evidence_ref": "manufacturer-datasheet-hx-4820",
                        "verified": True,
                    }
                ),
                OfficialTechnicalFact.model_validate(
                    {
                        "fact_id": "measurement_temperature_celsius",
                        "unit": "CELSIUS",
                        "numeric_value": "20",
                        "evidence_ref": "manufacturer-datasheet-hx-4820",
                        "verified": True,
                    }
                ),
            ],
        )
    else:
        goods = OfficialGoodsCandidate(
            hs_code=hs_code,
            annex_i_code=annex_code,
            classification_verified=False,
            technical_specification_available=technical_specification_available,
        )
    return OfficialScreeningRequest(
        party_names=[
            OfficialPartyName(name=party.legal_name)
            for party in scenario.request.parties
            if party.legal_name is not None
        ],
        goods=goods,
    )


def _require_demo(settings: Settings) -> None:
    if settings.mode != "demo":
        raise RuntimeError("synthetic CRM demonstrator requires explicit demo mode")


__all__ = [
    "DemoCrmDetailResponse",
    "DemoCrmIntegrationEvent",
    "DemoCrmListResponse",
    "DemoCrmOfficialScreeningResponse",
    "DemoCrmScreeningResponse",
    "get_demo_crm_record",
    "list_demo_crm_records",
    "official_request_for_demo_crm_record",
    "screen_demo_crm_record",
]
