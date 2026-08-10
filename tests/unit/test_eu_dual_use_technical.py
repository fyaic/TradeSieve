"""Deterministic source-bound EU dual-use technical assertion tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

import pytest

import tradesieve.domain.eu_dual_use_technical as technical
from tradesieve.domain.eu_dual_use import EuDualUseControlEntry, EuDualUseControlList
from tradesieve.domain.eu_dual_use_technical import (
    EU_DUAL_USE_TECHNICAL_RULE_BUNDLE_CONTENT_HASH,
    EU_DUAL_USE_TECHNICAL_RULE_BUNDLE_ID,
    EuDualUseTechnicalAssertionEngine,
    EuDualUseTechnicalAssessment,
    EuDualUseTechnicalFact,
    TechnicalAssertionStatus,
    TechnicalCellType,
    TechnicalFactId,
    TechnicalFactUnit,
    TechnicalProductFamily,
)

NOW = datetime(2026, 8, 10, 8, tzinfo=UTC)


def control_list() -> EuDualUseControlList:
    entries = tuple(
        EuDualUseControlEntry(
            code=f"{category}A{index:03d}",
            text=f"Official assertion fixture {category}-{index}",
            native_locator=f"/ANNEX/NP[NO.P='{category}A{index:03d}']",
        )
        for category in range(10)
        for index in range(30)
    )
    return EuDualUseControlList(
        retrieved_at=NOW,
        source_archive_hash="sha256:" + "a" * 64,
        source_archive_bytes=100,
        formex_document_hash="sha256:" + "b" * 64,
        entries=entries,
    )


def source_bound_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> EuDualUseTechnicalAssertionEngine:
    listing = control_list()
    entry = next(item for item in listing.entries if item.code == "3A001")
    monkeypatch.setattr(technical, "EU_DUAL_USE_3A001_ENTRY_HASH", entry.content_hash)
    return EuDualUseTechnicalAssertionEngine(listing)


def numeric(
    fact_id: TechnicalFactId,
    unit: TechnicalFactUnit,
    value: str,
    *,
    verified: bool = True,
    evidence_ref: str = "technical-sheet-1",
) -> EuDualUseTechnicalFact:
    return EuDualUseTechnicalFact(
        fact_id=fact_id,
        unit=unit,
        numeric_value=Decimal(value),
        boolean_value=None,
        text_value=None,
        evidence_ref=evidence_ref,
        verified=verified,
    )


def boolean(
    fact_id: TechnicalFactId,
    value: bool,
    *,
    verified: bool = True,
) -> EuDualUseTechnicalFact:
    return EuDualUseTechnicalFact(
        fact_id=fact_id,
        unit=TechnicalFactUnit.BOOLEAN,
        numeric_value=None,
        boolean_value=value,
        text_value=None,
        evidence_ref="technical-sheet-1",
        verified=verified,
    )


def cell_type(value: TechnicalCellType) -> EuDualUseTechnicalFact:
    return EuDualUseTechnicalFact(
        fact_id=TechnicalFactId.CELL_TYPE,
        unit=TechnicalFactUnit.CELL_TYPE,
        numeric_value=None,
        boolean_value=None,
        text_value=value,
        evidence_ref="technical-sheet-1",
        verified=True,
    )


def adc_facts(
    resolution: str,
    rate: str,
    *,
    processes: bool,
    rate_verified: bool = True,
) -> tuple[EuDualUseTechnicalFact, ...]:
    return (
        numeric(
            TechnicalFactId.RESOLUTION_BITS,
            TechnicalFactUnit.BITS,
            resolution,
        ),
        numeric(
            TechnicalFactId.SAMPLE_RATE_MSPS,
            TechnicalFactUnit.MSPS,
            rate,
            verified=rate_verified,
        ),
        boolean(TechnicalFactId.STORES_OR_PROCESSES_DIGITISED_DATA, processes),
    )


def cell_facts(
    kind: TechnicalCellType,
    energy: str,
    *,
    battery: bool = False,
    temperature: str = "20",
    power: str | None = None,
) -> tuple[EuDualUseTechnicalFact, ...]:
    values = [
        boolean(TechnicalFactId.IS_BATTERY, battery),
        cell_type(kind),
        numeric(
            TechnicalFactId.ENERGY_DENSITY_WH_PER_KG,
            TechnicalFactUnit.WH_PER_KG,
            energy,
        ),
        numeric(
            TechnicalFactId.MEASUREMENT_TEMPERATURE_CELSIUS,
            TechnicalFactUnit.CELSIUS,
            temperature,
        ),
    ]
    if power is not None:
        values.append(
            numeric(
                TechnicalFactId.CONTINUOUS_POWER_DENSITY_W_PER_KG,
                TechnicalFactUnit.W_PER_KG,
                power,
            )
        )
    return tuple(values)


def assess_adc(
    engine: EuDualUseTechnicalAssertionEngine,
    facts: tuple[EuDualUseTechnicalFact, ...],
) -> EuDualUseTechnicalAssessment:
    result = engine.assess(
        annex_i_code="3A001",
        classification_verified=True,
        product_family=TechnicalProductFamily.ADC_INTEGRATED_CIRCUIT,
        facts=facts,
    )
    assert result is not None
    return result


def assess_cell(
    engine: EuDualUseTechnicalAssertionEngine,
    facts: tuple[EuDualUseTechnicalFact, ...],
) -> EuDualUseTechnicalAssessment:
    result = engine.assess(
        annex_i_code="3A001",
        classification_verified=True,
        product_family=TechnicalProductFamily.ELECTROCHEMICAL_CELL,
        facts=facts,
    )
    assert result is not None
    return result


def test_no_technical_payload_is_not_inferred() -> None:
    assert (
        EuDualUseTechnicalAssertionEngine(control_list()).assess(
            annex_i_code="3A001",
            classification_verified=True,
            product_family=None,
            facts=(),
        )
        is None
    )


def test_unsupported_candidate_and_source_drift_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listing = control_list()
    engine = EuDualUseTechnicalAssertionEngine(listing)
    unsupported = engine.assess(
        annex_i_code="2B001",
        classification_verified=True,
        product_family=TechnicalProductFamily.ADC_INTEGRATED_CIRCUIT,
        facts=adc_facts("12", "500", processes=False),
    )
    assert unsupported is not None
    assert unsupported.status is TechnicalAssertionStatus.UNSUPPORTED
    assert unsupported.missing_facts == ("supported_technical_rule",)

    mismatch = engine.assess(
        annex_i_code="3A001",
        classification_verified=True,
        product_family=TechnicalProductFamily.ADC_INTEGRATED_CIRCUIT,
        facts=adc_facts("12", "500", processes=False),
    )
    assert mismatch is not None
    assert mismatch.status is TechnicalAssertionStatus.SOURCE_MISMATCH
    assert mismatch.source_native_locator == "/ANNEX/NP[NO.P='3A001']"

    entry = next(item for item in listing.entries if item.code == "3A001")
    without_entry = replace(
        listing,
        entries=tuple(
            replace(item, code="3B001") if item.code == "3A001" else item
            for item in listing.entries
        ),
    )
    monkeypatch.setattr(technical, "EU_DUAL_USE_3A001_ENTRY_HASH", entry.content_hash)
    absent = EuDualUseTechnicalAssertionEngine(without_entry).assess(
        annex_i_code="3A001",
        classification_verified=True,
        product_family=TechnicalProductFamily.ADC_INTEGRATED_CIRCUIT,
        facts=adc_facts("12", "500", processes=False),
    )
    assert absent is not None
    assert absent.status is TechnicalAssertionStatus.SOURCE_MISMATCH
    assert absent.source_native_locator is None


def test_unverified_classification_and_facts_are_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = source_bound_engine(monkeypatch)
    unclassified = engine.assess(
        annex_i_code="3A001",
        classification_verified=False,
        product_family=TechnicalProductFamily.ADC_INTEGRATED_CIRCUIT,
        facts=adc_facts("12", "500", processes=False),
    )
    assert unclassified is not None
    assert unclassified.status is TechnicalAssertionStatus.INCOMPLETE
    assert unclassified.missing_facts == ("qualified_classifier_review",)

    incomplete = assess_adc(
        engine,
        adc_facts("12", "500", processes=False, rate_verified=False),
    )
    assert incomplete.status is TechnicalAssertionStatus.INCOMPLETE
    assert incomplete.missing_facts == ("verified:sample_rate_msps",)
    assert incomplete.required_facts == (
        TechnicalFactId.RESOLUTION_BITS,
        TechnicalFactId.SAMPLE_RATE_MSPS,
        TechnicalFactId.STORES_OR_PROCESSES_DIGITISED_DATA,
    )


@pytest.mark.parametrize(
    ("resolution", "threshold", "processes", "rule_id"),
    [
        ("8", "1300", False, "eu-3a001-a-5-a-adc"),
        ("10", "600", False, "eu-3a001-a-5-a-adc"),
        ("12", "400", False, "eu-3a001-a-5-a-adc"),
        ("14", "250", False, "eu-3a001-a-5-a-adc"),
        ("16", "65", False, "eu-3a001-a-5-a-adc"),
        ("8", "1300", True, "eu-3a001-a-14-adc-processing"),
        ("10", "1000", True, "eu-3a001-a-14-adc-processing"),
        ("12", "1000", True, "eu-3a001-a-14-adc-processing"),
        ("14", "400", True, "eu-3a001-a-14-adc-processing"),
        ("16", "180", True, "eu-3a001-a-14-adc-processing"),
    ],
)
def test_adc_thresholds_are_strict_and_source_bound(
    monkeypatch: pytest.MonkeyPatch,
    resolution: str,
    threshold: str,
    processes: bool,
    rule_id: str,
) -> None:
    engine = source_bound_engine(monkeypatch)
    boundary = assess_adc(engine, adc_facts(resolution, threshold, processes=processes))
    assert boundary.status is TechnicalAssertionStatus.NOT_MATCHED
    assert boundary.rule_id == rule_id
    assert boundary.comparisons[0].matched is False

    above = assess_adc(
        engine,
        adc_facts(resolution, str(Decimal(threshold) + 1), processes=processes),
    )
    assert above.status is TechnicalAssertionStatus.MATCHED
    assert above.comparisons[0].threshold_value == threshold
    assert above.rule_bundle_id == EU_DUAL_USE_TECHNICAL_RULE_BUNDLE_ID
    assert (
        above.rule_bundle_content_hash == EU_DUAL_USE_TECHNICAL_RULE_BUNDLE_CONTENT_HASH
    )
    assert above.automatic_clearance is False


def test_adc_below_minimum_resolution_does_not_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = assess_adc(
        source_bound_engine(monkeypatch),
        adc_facts("7", "9999", processes=False),
    )
    assert result.status is TechnicalAssertionStatus.NOT_MATCHED
    assert result.comparisons[0].actual_value == "7"
    assert result.comparisons[0].threshold_value == "8"


def test_secondary_cell_threshold_and_measurement_conditions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = source_bound_engine(monkeypatch)
    boundary = assess_cell(engine, cell_facts(TechnicalCellType.SECONDARY, "350"))
    assert boundary.status is TechnicalAssertionStatus.NOT_MATCHED
    assert [item.matched for item in boundary.comparisons] == [True, False]

    matched = assess_cell(engine, cell_facts(TechnicalCellType.SECONDARY, "351.50"))
    assert matched.status is TechnicalAssertionStatus.MATCHED
    assert matched.rule_id == "eu-3a001-e-1-secondary-cell"
    assert matched.comparisons[1].actual_value == "351.5"

    wrong_temperature = assess_cell(
        engine,
        cell_facts(TechnicalCellType.SECONDARY, "351", temperature="25"),
    )
    assert wrong_temperature.status is TechnicalAssertionStatus.INCOMPLETE
    assert wrong_temperature.missing_facts == ("measurement_at_20_celsius",)
    assert wrong_temperature.comparisons[0].matched is False

    excluded = assess_cell(
        engine,
        cell_facts(TechnicalCellType.SECONDARY, "500", battery=True),
    )
    assert excluded.status is TechnicalAssertionStatus.NOT_APPLICABLE
    assert excluded.missing_facts == ("review_other_controls_and_catch_all",)


@pytest.mark.parametrize(
    ("energy", "power", "expected"),
    [
        ("551", "51", TechnicalAssertionStatus.MATCHED),
        ("51", "351", TechnicalAssertionStatus.MATCHED),
        ("550", "50", TechnicalAssertionStatus.NOT_MATCHED),
    ],
)
def test_primary_cell_alternative_thresholds(
    monkeypatch: pytest.MonkeyPatch,
    energy: str,
    power: str,
    expected: TechnicalAssertionStatus,
) -> None:
    result = assess_cell(
        source_bound_engine(monkeypatch),
        cell_facts(TechnicalCellType.PRIMARY, energy, power=power),
    )
    assert result.status is expected
    assert result.rule_id == "eu-3a001-e-1-primary-cell"
    assert len(result.comparisons) == 5


def test_cell_missing_facts_remain_named(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = source_bound_engine(monkeypatch)
    missing_common = assess_cell(engine, ())
    assert missing_common.status is TechnicalAssertionStatus.INCOMPLETE
    assert "is_battery" in missing_common.missing_facts

    missing_power = assess_cell(
        engine,
        cell_facts(TechnicalCellType.PRIMARY, "600"),
    )
    assert missing_power.status is TechnicalAssertionStatus.INCOMPLETE
    assert missing_power.missing_facts == ("continuous_power_density_w_per_kg",)


def test_duplicate_and_untyped_engine_inputs_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = source_bound_engine(monkeypatch)
    duplicate = adc_facts("12", "500", processes=False)
    with pytest.raises(ValueError, match="unique"):
        assess_adc(engine, (*duplicate, duplicate[0]))
    for action in (
        lambda: EuDualUseTechnicalAssertionEngine(cast(Any, object())),
        lambda: engine.assess(
            annex_i_code="3A001",
            classification_verified=cast(Any, 1),
            product_family=TechnicalProductFamily.ADC_INTEGRATED_CIRCUIT,
            facts=(),
        ),
        lambda: engine.assess(
            annex_i_code="3A001",
            classification_verified=True,
            product_family=cast(Any, "bad"),
            facts=(),
        ),
        lambda: engine.assess(
            annex_i_code="3A001",
            classification_verified=True,
            product_family=TechnicalProductFamily.ADC_INTEGRATED_CIRCUIT,
            facts=cast(Any, []),
        ),
        lambda: engine.assess(
            annex_i_code="3A001",
            classification_verified=True,
            product_family=TechnicalProductFamily.ADC_INTEGRATED_CIRCUIT,
            facts=cast(Any, (object(),)),
        ),
    ):
        with pytest.raises(ValueError):
            action()


def test_technical_fact_defensive_validation() -> None:
    base = numeric(
        TechnicalFactId.RESOLUTION_BITS,
        TechnicalFactUnit.BITS,
        "12",
    )
    invalid: tuple[Any, ...] = (
        lambda: replace(base, fact_id=cast(Any, "bad")),
        lambda: replace(base, unit=cast(Any, "bad")),
        lambda: replace(base, numeric_value=None),
        lambda: replace(base, boolean_value=True),
        lambda: replace(base, numeric_value=cast(Any, "12")),
        lambda: replace(base, numeric_value=Decimal("NaN")),
        lambda: replace(base, numeric_value=Decimal("0")),
        lambda: EuDualUseTechnicalFact(
            TechnicalFactId.IS_BATTERY,
            TechnicalFactUnit.BOOLEAN,
            None,
            cast(Any, 1),
            None,
            "evidence-1",
            True,
        ),
        lambda: EuDualUseTechnicalFact(
            TechnicalFactId.CELL_TYPE,
            TechnicalFactUnit.CELL_TYPE,
            None,
            None,
            cast(Any, "PRIMARY"),
            "evidence-1",
            True,
        ),
        lambda: replace(base, evidence_ref=""),
        lambda: replace(base, evidence_ref="x" * 129),
        lambda: replace(base, verified=cast(Any, 1)),
        lambda: replace(base, unit=TechnicalFactUnit.MSPS),
        lambda: EuDualUseTechnicalFact(
            TechnicalFactId.RESOLUTION_BITS,
            TechnicalFactUnit.BITS,
            None,
            True,
            None,
            "evidence-1",
            True,
        ),
        lambda: replace(base, numeric_value=Decimal("12.5")),
    )
    for action in invalid:
        with pytest.raises(ValueError):
            action()

    assessment = EuDualUseTechnicalAssessment(
        status=TechnicalAssertionStatus.INCOMPLETE,
        rule_id=None,
        rule_version="1.0.0",
        rule_bundle_id=EU_DUAL_USE_TECHNICAL_RULE_BUNDLE_ID,
        rule_bundle_content_hash=EU_DUAL_USE_TECHNICAL_RULE_BUNDLE_CONTENT_HASH,
        source_celex="32025R2003",
        source_entry_code="3A001",
        source_entry_content_hash="sha256:" + "a" * 64,
        source_native_locator=None,
        required_facts=(),
        missing_facts=("fact",),
        evidence_refs=(),
        comparisons=(),
    )
    with pytest.raises(ValueError, match="clearance"):
        replace(assessment, automatic_clearance=True)
