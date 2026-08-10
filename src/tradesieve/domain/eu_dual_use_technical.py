"""Source-bound deterministic technical assertions for selected EU Annex I branches.

The assertions in this module compare supplied, reviewed facts with a deliberately
small set of hand-reviewed legal thresholds.  They do not infer an Annex I code and
they never create legal clearance.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from tradesieve.domain.eu_dual_use import (
    EU_DUAL_USE_CELEX,
    EuDualUseControlList,
)

EU_DUAL_USE_TECHNICAL_RULE_BUNDLE_ID: Final = "eu-dual-use-technical-3a001-v1"
EU_DUAL_USE_TECHNICAL_RULE_BUNDLE_VERSION: Final = "1.0.0"
EU_DUAL_USE_3A001_ENTRY_HASH: Final = (
    "sha256:6f742e495b1b4a497d3918f247943e0f21ab2de1e3366b2849ff699ded6a8732"
)


class TechnicalProductFamily(StrEnum):
    ADC_INTEGRATED_CIRCUIT = "ADC_INTEGRATED_CIRCUIT"
    ELECTROCHEMICAL_CELL = "ELECTROCHEMICAL_CELL"


class TechnicalFactId(StrEnum):
    RESOLUTION_BITS = "resolution_bits"
    SAMPLE_RATE_MSPS = "sample_rate_msps"
    STORES_OR_PROCESSES_DIGITISED_DATA = "stores_or_processes_digitised_data"
    CELL_TYPE = "cell_type"
    ENERGY_DENSITY_WH_PER_KG = "energy_density_wh_per_kg"
    CONTINUOUS_POWER_DENSITY_W_PER_KG = "continuous_power_density_w_per_kg"
    MEASUREMENT_TEMPERATURE_CELSIUS = "measurement_temperature_celsius"
    IS_BATTERY = "is_battery"


class TechnicalFactUnit(StrEnum):
    BITS = "BITS"
    MSPS = "MSPS"
    BOOLEAN = "BOOLEAN"
    CELL_TYPE = "CELL_TYPE"
    WH_PER_KG = "WH_PER_KG"
    W_PER_KG = "W_PER_KG"
    CELSIUS = "CELSIUS"


class TechnicalCellType(StrEnum):
    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"


class TechnicalAssertionStatus(StrEnum):
    MATCHED = "MATCHED"
    NOT_MATCHED = "NOT_MATCHED"
    INCOMPLETE = "INCOMPLETE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNSUPPORTED = "UNSUPPORTED"
    SOURCE_MISMATCH = "SOURCE_MISMATCH"


class TechnicalComparisonOperator(StrEnum):
    GREATER_THAN = "GREATER_THAN"
    GREATER_THAN_OR_EQUAL = "GREATER_THAN_OR_EQUAL"
    EQUAL = "EQUAL"


@dataclass(frozen=True, slots=True)
class EuDualUseTechnicalFact:
    fact_id: TechnicalFactId
    unit: TechnicalFactUnit
    numeric_value: Decimal | None
    boolean_value: bool | None
    text_value: TechnicalCellType | None
    evidence_ref: str
    verified: bool

    def __post_init__(self) -> None:
        if not isinstance(self.fact_id, TechnicalFactId) or not isinstance(
            self.unit, TechnicalFactUnit
        ):
            raise ValueError("technical fact identity must be typed")
        supplied = sum(
            value is not None
            for value in (self.numeric_value, self.boolean_value, self.text_value)
        )
        if supplied != 1:
            raise ValueError("technical fact must contain exactly one typed value")
        if self.numeric_value is not None and (
            not isinstance(self.numeric_value, Decimal)
            or not self.numeric_value.is_finite()
            or self.numeric_value <= 0
        ):
            raise ValueError("technical numeric fact must be finite and positive")
        if self.boolean_value is not None and not isinstance(self.boolean_value, bool):
            raise ValueError("technical boolean fact must be typed")
        if self.text_value is not None and not isinstance(
            self.text_value, TechnicalCellType
        ):
            raise ValueError("technical text fact must be typed")
        if (
            not isinstance(self.evidence_ref, str)
            or not self.evidence_ref
            or len(self.evidence_ref) > 128
        ):
            raise ValueError("technical fact evidence reference is invalid")
        if not isinstance(self.verified, bool):
            raise ValueError("technical fact verification state must be boolean")
        expected = _FACT_UNITS[self.fact_id]
        if self.unit is not expected:
            raise ValueError("technical fact unit does not match its identity")
        expected_kind = _FACT_VALUE_KINDS[self.fact_id]
        actual_kind = (
            "numeric"
            if self.numeric_value is not None
            else "boolean"
            if self.boolean_value is not None
            else "text"
        )
        if actual_kind != expected_kind:
            raise ValueError("technical fact value type does not match its identity")
        if self.fact_id is TechnicalFactId.RESOLUTION_BITS and (
            self.numeric_value is None
            or self.numeric_value != self.numeric_value.to_integral_value()
        ):
            raise ValueError("ADC resolution must be a whole number of bits")


_FACT_UNITS: Final = MappingProxyType(
    {
        TechnicalFactId.RESOLUTION_BITS: TechnicalFactUnit.BITS,
        TechnicalFactId.SAMPLE_RATE_MSPS: TechnicalFactUnit.MSPS,
        TechnicalFactId.STORES_OR_PROCESSES_DIGITISED_DATA: (TechnicalFactUnit.BOOLEAN),
        TechnicalFactId.CELL_TYPE: TechnicalFactUnit.CELL_TYPE,
        TechnicalFactId.ENERGY_DENSITY_WH_PER_KG: TechnicalFactUnit.WH_PER_KG,
        TechnicalFactId.CONTINUOUS_POWER_DENSITY_W_PER_KG: (TechnicalFactUnit.W_PER_KG),
        TechnicalFactId.MEASUREMENT_TEMPERATURE_CELSIUS: (TechnicalFactUnit.CELSIUS),
        TechnicalFactId.IS_BATTERY: TechnicalFactUnit.BOOLEAN,
    }
)
_FACT_VALUE_KINDS: Final = MappingProxyType(
    {
        TechnicalFactId.RESOLUTION_BITS: "numeric",
        TechnicalFactId.SAMPLE_RATE_MSPS: "numeric",
        TechnicalFactId.STORES_OR_PROCESSES_DIGITISED_DATA: "boolean",
        TechnicalFactId.CELL_TYPE: "text",
        TechnicalFactId.ENERGY_DENSITY_WH_PER_KG: "numeric",
        TechnicalFactId.CONTINUOUS_POWER_DENSITY_W_PER_KG: "numeric",
        TechnicalFactId.MEASUREMENT_TEMPERATURE_CELSIUS: "numeric",
        TechnicalFactId.IS_BATTERY: "boolean",
    }
)


@dataclass(frozen=True, slots=True)
class EuDualUseTechnicalComparison:
    clause_id: str
    fact_id: TechnicalFactId
    operator: TechnicalComparisonOperator
    actual_value: str
    threshold_value: str
    unit: TechnicalFactUnit
    matched: bool


@dataclass(frozen=True, slots=True)
class EuDualUseTechnicalAssessment:
    status: TechnicalAssertionStatus
    rule_id: str | None
    rule_version: str
    rule_bundle_id: str
    rule_bundle_content_hash: str
    source_celex: str
    source_entry_code: str
    source_entry_content_hash: str
    source_native_locator: str | None
    required_facts: tuple[TechnicalFactId, ...]
    missing_facts: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    comparisons: tuple[EuDualUseTechnicalComparison, ...]
    automatic_clearance: bool = False

    def __post_init__(self) -> None:
        if self.automatic_clearance is not False:
            raise ValueError("technical assessment cannot grant automatic clearance")


_RULE_DEFINITION: Final = {
    "bundle_id": EU_DUAL_USE_TECHNICAL_RULE_BUNDLE_ID,
    "bundle_version": EU_DUAL_USE_TECHNICAL_RULE_BUNDLE_VERSION,
    "source_celex": EU_DUAL_USE_CELEX,
    "source_entry": "3A001",
    "source_entry_content_hash": EU_DUAL_USE_3A001_ENTRY_HASH,
    "rules": {
        "eu-3a001-a-5-a-adc": {
            "resolution_bits": [8, 10, 12, 14, 16],
            "sample_rate_msps_strictly_greater_than": [1300, 600, 400, 250, 65],
        },
        "eu-3a001-a-14-adc-processing": {
            "resolution_bits": [8, 10, 12, 14, 16],
            "sample_rate_msps_strictly_greater_than": [
                1300,
                1000,
                1000,
                400,
                180,
            ],
        },
        "eu-3a001-e-1-primary-cell": {
            "alternatives": [
                {"energy_density_wh_per_kg": 550, "power_density_w_per_kg": 50},
                {"energy_density_wh_per_kg": 50, "power_density_w_per_kg": 350},
            ]
        },
        "eu-3a001-e-1-secondary-cell": {
            "energy_density_wh_per_kg_strictly_greater_than": 350
        },
    },
}
EU_DUAL_USE_TECHNICAL_RULE_BUNDLE_CONTENT_HASH: Final = (
    "sha256:"
    + hashlib.sha256(
        json.dumps(
            _RULE_DEFINITION,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
)


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _missing(
    facts: dict[TechnicalFactId, EuDualUseTechnicalFact],
    required: tuple[TechnicalFactId, ...],
) -> tuple[str, ...]:
    missing: list[str] = []
    for fact_id in required:
        fact = facts.get(fact_id)
        if fact is None:
            missing.append(fact_id.value)
        elif not fact.verified:
            missing.append(f"verified:{fact_id.value}")
    return tuple(missing)


def _comparison(
    *,
    clause_id: str,
    fact_id: TechnicalFactId,
    actual: Decimal,
    operator: TechnicalComparisonOperator,
    threshold: Decimal,
) -> EuDualUseTechnicalComparison:
    matched = (
        actual > threshold
        if operator is TechnicalComparisonOperator.GREATER_THAN
        else actual >= threshold
        if operator is TechnicalComparisonOperator.GREATER_THAN_OR_EQUAL
        else actual == threshold
    )
    return EuDualUseTechnicalComparison(
        clause_id=clause_id,
        fact_id=fact_id,
        operator=operator,
        actual_value=_decimal_text(actual),
        threshold_value=_decimal_text(threshold),
        unit=_FACT_UNITS[fact_id],
        matched=matched,
    )


class EuDualUseTechnicalAssertionEngine:
    """Evaluate reviewed facts against the bounded source-bound assertion bundle."""

    def __init__(self, control_list: EuDualUseControlList) -> None:
        if not isinstance(control_list, EuDualUseControlList):
            raise ValueError("technical assertion engine requires a typed control list")
        self._control_list = control_list

    def assess(
        self,
        *,
        annex_i_code: str | None,
        classification_verified: bool,
        product_family: TechnicalProductFamily | None,
        facts: tuple[EuDualUseTechnicalFact, ...],
    ) -> EuDualUseTechnicalAssessment | None:
        if not isinstance(classification_verified, bool):
            raise ValueError("classification verification state must be boolean")
        if product_family is not None and not isinstance(
            product_family, TechnicalProductFamily
        ):
            raise ValueError("technical product family must be typed")
        if not isinstance(facts, tuple) or any(
            not isinstance(fact, EuDualUseTechnicalFact) for fact in facts
        ):
            raise ValueError("technical facts must be a typed tuple")
        if product_family is None and not facts:
            return None
        if annex_i_code != "3A001" or product_family is None:
            return self._result(
                status=TechnicalAssertionStatus.UNSUPPORTED,
                rule_id=None,
                source_native_locator=None,
                required=(),
                missing=("supported_technical_rule",),
                evidence_refs=(),
                comparisons=(),
            )
        entry = next(
            (item for item in self._control_list.entries if item.code == "3A001"),
            None,
        )
        if entry is None or entry.content_hash != EU_DUAL_USE_3A001_ENTRY_HASH:
            return self._result(
                status=TechnicalAssertionStatus.SOURCE_MISMATCH,
                rule_id=None,
                source_native_locator=(entry.native_locator if entry else None),
                required=(),
                missing=("approved_technical_rule_for_active_source",),
                evidence_refs=(),
                comparisons=(),
            )
        if not classification_verified:
            return self._result(
                status=TechnicalAssertionStatus.INCOMPLETE,
                rule_id=None,
                source_native_locator=entry.native_locator,
                required=(),
                missing=("qualified_classifier_review",),
                evidence_refs=(),
                comparisons=(),
            )
        indexed = {fact.fact_id: fact for fact in facts}
        if len(indexed) != len(facts):
            raise ValueError("technical fact identities must be unique")
        if product_family is TechnicalProductFamily.ADC_INTEGRATED_CIRCUIT:
            return self._assess_adc(indexed, entry.native_locator)
        return self._assess_cell(indexed, entry.native_locator)

    def _assess_adc(
        self,
        facts: dict[TechnicalFactId, EuDualUseTechnicalFact],
        locator: str,
    ) -> EuDualUseTechnicalAssessment:
        required = (
            TechnicalFactId.RESOLUTION_BITS,
            TechnicalFactId.SAMPLE_RATE_MSPS,
            TechnicalFactId.STORES_OR_PROCESSES_DIGITISED_DATA,
        )
        missing = _missing(facts, required)
        evidence = tuple(
            sorted({fact.evidence_ref for fact in facts.values() if fact.verified})
        )
        if missing:
            return self._result(
                status=TechnicalAssertionStatus.INCOMPLETE,
                rule_id=None,
                source_native_locator=locator,
                required=required,
                missing=missing,
                evidence_refs=evidence,
                comparisons=(),
            )
        resolution = facts[TechnicalFactId.RESOLUTION_BITS].numeric_value
        sample_rate = facts[TechnicalFactId.SAMPLE_RATE_MSPS].numeric_value
        processes = facts[
            TechnicalFactId.STORES_OR_PROCESSES_DIGITISED_DATA
        ].boolean_value
        assert (
            resolution is not None and sample_rate is not None and processes is not None
        )
        rule_id = "eu-3a001-a-14-adc-processing" if processes else "eu-3a001-a-5-a-adc"
        if resolution < 8:
            comparisons = (
                _comparison(
                    clause_id="resolution-minimum",
                    fact_id=TechnicalFactId.RESOLUTION_BITS,
                    actual=resolution,
                    operator=TechnicalComparisonOperator.GREATER_THAN_OR_EQUAL,
                    threshold=Decimal(8),
                ),
            )
        else:
            thresholds = (
                (Decimal(16), Decimal(180 if processes else 65)),
                (Decimal(14), Decimal(400 if processes else 250)),
                (Decimal(12), Decimal(1000 if processes else 400)),
                (Decimal(10), Decimal(1000 if processes else 600)),
                (Decimal(8), Decimal(1300)),
            )
            threshold = next(
                rate for lower_bound, rate in thresholds if resolution >= lower_bound
            )
            comparisons = (
                _comparison(
                    clause_id="sample-rate-threshold",
                    fact_id=TechnicalFactId.SAMPLE_RATE_MSPS,
                    actual=sample_rate,
                    operator=TechnicalComparisonOperator.GREATER_THAN,
                    threshold=threshold,
                ),
            )
        status = (
            TechnicalAssertionStatus.MATCHED
            if all(item.matched for item in comparisons)
            else TechnicalAssertionStatus.NOT_MATCHED
        )
        return self._result(
            status=status,
            rule_id=rule_id,
            source_native_locator=locator,
            required=required,
            missing=(),
            evidence_refs=evidence,
            comparisons=comparisons,
        )

    def _assess_cell(
        self,
        facts: dict[TechnicalFactId, EuDualUseTechnicalFact],
        locator: str,
    ) -> EuDualUseTechnicalAssessment:
        common: tuple[TechnicalFactId, ...] = (
            TechnicalFactId.IS_BATTERY,
            TechnicalFactId.CELL_TYPE,
            TechnicalFactId.ENERGY_DENSITY_WH_PER_KG,
            TechnicalFactId.MEASUREMENT_TEMPERATURE_CELSIUS,
        )
        missing = _missing(facts, common)
        evidence = tuple(
            sorted({fact.evidence_ref for fact in facts.values() if fact.verified})
        )
        if missing:
            return self._result(
                status=TechnicalAssertionStatus.INCOMPLETE,
                rule_id=None,
                source_native_locator=locator,
                required=common,
                missing=missing,
                evidence_refs=evidence,
                comparisons=(),
            )
        is_battery = facts[TechnicalFactId.IS_BATTERY].boolean_value
        cell_type = facts[TechnicalFactId.CELL_TYPE].text_value
        energy = facts[TechnicalFactId.ENERGY_DENSITY_WH_PER_KG].numeric_value
        temperature = facts[
            TechnicalFactId.MEASUREMENT_TEMPERATURE_CELSIUS
        ].numeric_value
        assert (
            is_battery is not None
            and cell_type is not None
            and energy is not None
            and temperature is not None
        )
        if is_battery:
            return self._result(
                status=TechnicalAssertionStatus.NOT_APPLICABLE,
                rule_id="eu-3a001-e-1-cell-exclusion",
                source_native_locator=locator,
                required=common,
                missing=("review_other_controls_and_catch_all",),
                evidence_refs=evidence,
                comparisons=(),
            )
        temperature_comparison = _comparison(
            clause_id="measurement-temperature",
            fact_id=TechnicalFactId.MEASUREMENT_TEMPERATURE_CELSIUS,
            actual=temperature,
            operator=TechnicalComparisonOperator.EQUAL,
            threshold=Decimal(20),
        )
        if not temperature_comparison.matched:
            return self._result(
                status=TechnicalAssertionStatus.INCOMPLETE,
                rule_id=None,
                source_native_locator=locator,
                required=common,
                missing=("measurement_at_20_celsius",),
                evidence_refs=evidence,
                comparisons=(temperature_comparison,),
            )
        if cell_type is TechnicalCellType.SECONDARY:
            rule_id = "eu-3a001-e-1-secondary-cell"
            threshold_comparisons: tuple[EuDualUseTechnicalComparison, ...] = (
                _comparison(
                    clause_id="secondary-cell",
                    fact_id=TechnicalFactId.ENERGY_DENSITY_WH_PER_KG,
                    actual=energy,
                    operator=TechnicalComparisonOperator.GREATER_THAN,
                    threshold=Decimal(350),
                ),
            )
            required = common
            matched = threshold_comparisons[0].matched
        else:
            power_id = TechnicalFactId.CONTINUOUS_POWER_DENSITY_W_PER_KG
            required = (*common, power_id)
            power_missing = _missing(facts, (power_id,))
            if power_missing:
                return self._result(
                    status=TechnicalAssertionStatus.INCOMPLETE,
                    rule_id="eu-3a001-e-1-primary-cell",
                    source_native_locator=locator,
                    required=required,
                    missing=power_missing,
                    evidence_refs=evidence,
                    comparisons=(),
                )
            power = facts[power_id].numeric_value
            assert power is not None
            threshold_comparisons = tuple(
                comparison
                for clause_id, energy_threshold, power_threshold in (
                    ("primary-alternative-1", Decimal(550), Decimal(50)),
                    ("primary-alternative-2", Decimal(50), Decimal(350)),
                )
                for comparison in (
                    _comparison(
                        clause_id=clause_id,
                        fact_id=TechnicalFactId.ENERGY_DENSITY_WH_PER_KG,
                        actual=energy,
                        operator=TechnicalComparisonOperator.GREATER_THAN,
                        threshold=energy_threshold,
                    ),
                    _comparison(
                        clause_id=clause_id,
                        fact_id=power_id,
                        actual=power,
                        operator=TechnicalComparisonOperator.GREATER_THAN,
                        threshold=power_threshold,
                    ),
                )
            )
            rule_id = "eu-3a001-e-1-primary-cell"
            complete_clauses = {item.clause_id for item in threshold_comparisons}
            matched = any(
                all(
                    item.matched
                    for item in threshold_comparisons
                    if item.clause_id == clause_id
                )
                for clause_id in complete_clauses
            )
        comparisons = (temperature_comparison, *threshold_comparisons)
        status = (
            TechnicalAssertionStatus.MATCHED
            if matched
            else TechnicalAssertionStatus.NOT_MATCHED
        )
        return self._result(
            status=status,
            rule_id=rule_id,
            source_native_locator=locator,
            required=required,
            missing=(),
            evidence_refs=evidence,
            comparisons=comparisons,
        )

    @staticmethod
    def _result(
        *,
        status: TechnicalAssertionStatus,
        rule_id: str | None,
        source_native_locator: str | None,
        required: tuple[TechnicalFactId, ...],
        missing: tuple[str, ...],
        evidence_refs: tuple[str, ...],
        comparisons: tuple[EuDualUseTechnicalComparison, ...],
    ) -> EuDualUseTechnicalAssessment:
        return EuDualUseTechnicalAssessment(
            status=status,
            rule_id=rule_id,
            rule_version=EU_DUAL_USE_TECHNICAL_RULE_BUNDLE_VERSION,
            rule_bundle_id=EU_DUAL_USE_TECHNICAL_RULE_BUNDLE_ID,
            rule_bundle_content_hash=(EU_DUAL_USE_TECHNICAL_RULE_BUNDLE_CONTENT_HASH),
            source_celex=EU_DUAL_USE_CELEX,
            source_entry_code="3A001",
            source_entry_content_hash=EU_DUAL_USE_3A001_ENTRY_HASH,
            source_native_locator=source_native_locator,
            required_facts=required,
            missing_facts=missing,
            evidence_refs=evidence_refs,
            comparisons=comparisons,
        )


__all__ = [
    "EU_DUAL_USE_3A001_ENTRY_HASH",
    "EU_DUAL_USE_TECHNICAL_RULE_BUNDLE_CONTENT_HASH",
    "EU_DUAL_USE_TECHNICAL_RULE_BUNDLE_ID",
    "EuDualUseTechnicalAssertionEngine",
    "EuDualUseTechnicalAssessment",
    "EuDualUseTechnicalComparison",
    "EuDualUseTechnicalFact",
    "TechnicalAssertionStatus",
    "TechnicalCellType",
    "TechnicalComparisonOperator",
    "TechnicalFactId",
    "TechnicalFactUnit",
    "TechnicalProductFamily",
]
