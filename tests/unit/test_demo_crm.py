"""Synthetic CRM fixture and canonical-boundary tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from importlib.resources import files

import pytest

import tradesieve.demo_crm as demo_crm
from tradesieve.application.contracts import (
    BusinessAction,
    CaseState,
    FactClass,
    Priority,
    ScreeningResult,
    Signal,
)
from tradesieve.config import Settings


def production_settings() -> Settings:
    return Settings(
        mode="production",
        database_url="postgresql://service@database/tradesieve",
        demo_bootstrap_enabled=False,
        rule_bundle_tenant_id="tenant-production",
        deployment_id="deployment-production",
        required_source_set="approved-source-set",
        required_rule_set="approved-rule-set",
        official_api_token_sha256="sha256:" + "a" * 64,
    )


def test_all_synthetic_records_map_and_screen_conservatively() -> None:
    settings = Settings()
    listing = demo_crm.list_demo_crm_records(settings)

    assert listing.demo_only is True
    assert listing.data_classification == "SYNTHETIC"
    assert len(listing.records) == 5
    assert len({record.record_id for record in listing.records}) == 5
    assert all(record.screening_status == "NOT_SCREENED" for record in listing.records)

    signals: set[Signal] = set()
    actions: set[BusinessAction] = set()
    for record in listing.records:
        detail = demo_crm.get_demo_crm_record(settings, record.record_id)
        response = demo_crm.screen_demo_crm_record(
            settings,
            record.record_id,
            now=datetime(2026, 8, 10, 8, 15, tzinfo=UTC),
        )
        result = response.result
        signals.add(result.signal)
        actions.add(result.business_action)

        assert detail.record.record_id == record.record_id
        assert detail.mapping.external_system == "huazhou-demo-crm"
        assert detail.mapping.canonical_input_hash == response.canonical_input_hash
        assert response.precomputed_fixture is True
        assert result.version_set.input_hash == response.canonical_input_hash
        assert result.result_hash == result.canonical_result_hash()
        assert result.state not in {
            CaseState.HUMAN_CLEARED,
            CaseState.HUMAN_BLOCKED,
            CaseState.CLOSED_NO_ACTION,
        }
        assert result.business_action is not BusinessAction.ALLOW_WITHIN_HUMAN_DECISION
        assert len(response.integration_events) == 2
        assert all(
            event.occurred_at == "2026-08-10T08:15:00Z"
            for event in response.integration_events
        )

    assert signals == {Signal.RED, Signal.YELLOW, Signal.GREEN_CANDIDATE}
    assert actions == {
        BusinessAction.ESCALATE,
        BusinessAction.REQUEST_EVIDENCE,
        BusinessAction.HOLD,
        BusinessAction.MONITOR,
    }


def test_red_fixture_uses_a_strong_synthetic_identifier_and_p0_hold() -> None:
    scenario = demo_crm._SCENARIOS["crm-quote-260810-0047"]
    buyer = next(
        party for party in scenario.request.parties if party.party_ref == "buyer-1"
    )

    assert buyer.identifiers[0].value == "RU-SYNTHETIC-7704-884219"
    assert scenario.result.signal is Signal.RED
    assert scenario.result.highest_priority is Priority.P0
    assert scenario.result.business_action is BusinessAction.ESCALATE
    assert scenario.result.holds[0].active is True
    assert scenario.result.findings[0].kind == "SYNTHETIC_EXACT_IDENTIFIER_MATCH"


def test_demo_records_map_to_live_official_screening_facts_conservatively() -> None:
    settings = Settings()
    record_ids = [
        item.record_id for item in demo_crm.list_demo_crm_records(settings).records
    ]
    requests = {
        record_id: demo_crm.official_request_for_demo_crm_record(
            settings,
            record_id,
        )
        for record_id in record_ids
    }

    listed = requests["crm-quote-260810-0047"]
    assert any(
        item.name == "Benevolence International Foundation"
        for item in listed.party_names
    )
    assert listed.goods.annex_i_code == "3A001"
    assert listed.goods.hs_code == "853710"
    assert listed.goods.classification_verified is False
    assert listed.goods.technical_specification_available is False
    cell = requests["crm-quote-260810-0039"].goods
    assert cell.annex_i_code == "3A001"
    assert cell.hs_code is None
    assert cell.classification_verified is True
    assert cell.technical_specification_available is True
    assert cell.product_family == "ELECTROCHEMICAL_CELL"
    assert {item.fact_id.value for item in cell.technical_facts} == {
        "is_battery",
        "cell_type",
        "energy_density_wh_per_kg",
        "measurement_temperature_celsius",
    }
    assert all(request.party_names for request in requests.values())
    assert (
        sum(request.goods.classification_verified for request in requests.values()) == 1
    )
    assert any(
        request.goods.technical_specification_available for request in requests.values()
    )
    assert any(request.goods.annex_i_code is None for request in requests.values())
    assert requests["crm-quote-260809-0186"].goods.hs_code == "850440"
    assert requests["crm-quote-260810-0052"].goods.hs_code == "400921"


def test_demo_functions_reject_production_mode_and_unknown_records() -> None:
    settings = production_settings()
    with pytest.raises(RuntimeError, match="explicit demo mode"):
        demo_crm.list_demo_crm_records(settings)
    with pytest.raises(RuntimeError, match="explicit demo mode"):
        demo_crm.get_demo_crm_record(settings, "crm-quote-260810-0047")
    with pytest.raises(RuntimeError, match="explicit demo mode"):
        demo_crm.screen_demo_crm_record(
            settings,
            "crm-quote-260810-0047",
            now=datetime.now(UTC),
        )
    with pytest.raises(RuntimeError, match="explicit demo mode"):
        demo_crm.official_request_for_demo_crm_record(
            settings,
            "crm-quote-260810-0047",
        )

    demo_settings = Settings()
    with pytest.raises(KeyError, match="missing-record"):
        demo_crm.get_demo_crm_record(demo_settings, "missing-record")
    with pytest.raises(KeyError, match="missing-record"):
        demo_crm.screen_demo_crm_record(
            demo_settings,
            "missing-record",
            now=datetime.now(UTC),
        )
    with pytest.raises(KeyError, match="missing-record"):
        demo_crm.official_request_for_demo_crm_record(
            demo_settings,
            "missing-record",
        )


@pytest.mark.parametrize(
    "invalid_now",
    ["2026-08-10T08:00:00Z", datetime(2026, 8, 10, 8, 0)],
)
def test_screening_time_must_be_an_aware_datetime(invalid_now: object) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        demo_crm.screen_demo_crm_record(
            Settings(),
            "crm-quote-260810-0047",
            now=invalid_now,  # type: ignore[arg-type]
        )


def test_screening_refuses_an_input_result_fixture_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record_id = "crm-quote-260810-0047"
    scenario = demo_crm._SCENARIOS[record_id]
    altered_versions = scenario.result.version_set.model_copy(
        update={"input_hash": "sha256:" + "f" * 64}
    )
    altered_result = scenario.result.model_copy(
        update={"version_set": altered_versions}
    )
    monkeypatch.setitem(
        demo_crm._SCENARIOS,
        record_id,
        replace(scenario, result=altered_result),
    )

    with pytest.raises(RuntimeError, match="input/result mismatch"):
        demo_crm.screen_demo_crm_record(
            Settings(),
            record_id,
            now=datetime.now(UTC),
        )


def test_internal_fixture_helpers_cover_every_fact_path_and_optional_shape() -> None:
    base = demo_crm._SCENARIOS["crm-quote-260810-0052"].request
    findings = (
        demo_crm._FindingFixture(
            kind="GOODS_GAP",
            priority=Priority.P1,
            fact_class=FactClass.UNKNOWN,
            summary="Synthetic goods gap.",
            required_action="REQUEST_GOODS",
            owner_role="ANALYST",
            fact_path="goods.line-1",
            uncertainty="Goods are incomplete.",
        ),
        demo_crm._FindingFixture(
            kind="PARTY_GAP",
            priority=Priority.P1,
            fact_class=FactClass.UNKNOWN,
            summary="Synthetic party gap.",
            required_action="REQUEST_PARTY",
            owner_role="ANALYST",
            fact_path="parties.buyer-1",
            uncertainty="Party is incomplete.",
        ),
        demo_crm._FindingFixture(
            kind="ROUTE_GAP",
            priority=Priority.P1,
            fact_class=FactClass.UNKNOWN,
            summary="Synthetic route gap.",
            required_action="REQUEST_ROUTE",
            owner_role="ANALYST",
            fact_path="route.final_use_country",
            uncertainty="Route is incomplete.",
        ),
        demo_crm._FindingFixture(
            kind="PAYMENT_GAP",
            priority=Priority.P1,
            fact_class=FactClass.UNKNOWN,
            summary="Synthetic payment gap.",
            required_action="REQUEST_PAYMENT",
            owner_role="ANALYST",
            fact_path="payment.originating_bank_ref",
            uncertainty="Payment is incomplete.",
        ),
        demo_crm._FindingFixture(
            kind="NEXUS_GAP",
            priority=Priority.P1,
            fact_class=FactClass.UNKNOWN,
            summary="Synthetic nexus gap.",
            required_action="REQUEST_NEXUS",
            owner_role="ANALYST",
            fact_path="other.scope",
            uncertainty="Nexus is incomplete.",
        ),
    )

    result = demo_crm._result(
        base,
        state=CaseState.INCOMPLETE,
        signal=Signal.YELLOW,
        highest_priority=Priority.P1,
        business_action=BusinessAction.NO_ACTION,
        summary="Synthetic helper coverage result.",
        findings=findings,
        suffix="helper-coverage",
    )

    assert result.rule_evaluations[0].missing_fact_paths == [
        "goods",
        "legal_nexus",
        "parties",
        "payment",
        "route",
    ]
    assert result.required_evidence == []
    assert result.holds == []

    document = demo_crm._document(11, "doc-empty", "OTHER")
    assert document["related_party_refs"] == []
    assert document["related_goods_refs"] == []

    request_without_end_use = demo_crm._base_request(
        record_id="helper-request",
        correlation_id="helper-request",
        buyer_name="Synthetic Buyer",
        buyer_country="NL",
        end_user_name="Synthetic End User",
        end_user_country="NL",
        bank_name="Synthetic Bank",
        bank_country="NL",
        goods={
            "line_ref": "line-1",
            "description": "Synthetic general goods",
            "quantity": "1",
            "quantity_unit": "piece",
        },
        route={"origin_country": "CN", "destination_country": "NL"},
        documents=[demo_crm._document(12, "doc-helper", "INVOICE")],
        amount="1.00",
        currency="EUR",
        purpose="Synthetic helper request.",
    )
    assert len(request_without_end_use.parties) == 4
    assert request_without_end_use.goods[0].end_use is None


def test_result_builder_detects_its_own_hash_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = demo_crm._SCENARIOS["crm-quote-260810-0052"].request
    hashes = iter(["sha256:" + "1" * 64, "sha256:" + "2" * 64])

    def changing_hash(self: ScreeningResult) -> str:
        return next(hashes)

    monkeypatch.setattr(ScreeningResult, "canonical_result_hash", changing_hash)
    with pytest.raises(RuntimeError, match="result integrity failure"):
        demo_crm._result(
            request,
            state=CaseState.REVIEW_REQUIRED,
            signal=Signal.GREEN_CANDIDATE,
            highest_priority=Priority.NONE,
            business_action=BusinessAction.MONITOR,
            summary="Synthetic integrity test.",
            findings=(),
            suffix="hash-failure",
        )


def test_all_visible_assets_are_self_contained_and_avoid_automatic_clearance() -> None:
    html = demo_crm.__file__
    assert html is not None
    root = files("tradesieve").joinpath("static", "demo_crm")
    page = root.joinpath("index.html").read_text(encoding="utf-8")
    css = root.joinpath("app.css").read_text(encoding="utf-8")
    javascript = root.joinpath("app.js").read_text(encoding="utf-8")

    assert "TradeSieve" in page
    assert "合成交易 / 真实官方来源" in page
    assert "prefers-color-scheme: dark" in css
    assert "prefers-reduced-motion" in css
    assert "HUMAN_CLEARED" not in javascript
    assert "ALLOW_WITHIN_HUMAN_DECISION" not in javascript
    assert "http://" not in page + css + javascript
    assert "https://" not in page + css + javascript
    assert "—" not in page + javascript
    assert "–" not in page + javascript
