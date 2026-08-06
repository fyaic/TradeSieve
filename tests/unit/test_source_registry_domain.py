"""Governed source registry domain behavior and freshness tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tradesieve.domain.source_registry import (
    MAX_FRESHNESS_SECONDS,
    MAX_LONG_TEXT_LENGTH,
    MAX_REQUIRED_SOURCES,
    MAX_SHORT_TEXT_LENGTH,
    ActivationBlockReason,
    SourceAccessMethod,
    SourceActivationRefused,
    SourceAvailability,
    SourceEvaluation,
    SourceFreshnessStatus,
    SourceRegistration,
    SourceRuntimeObservation,
    SourceSetManifest,
    evaluate_required_source_set,
    evaluate_source,
)

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def registration(**overrides: object) -> SourceRegistration:
    values: dict[str, object] = {
        "deployment_id": "deployment-1",
        "source_set_id": "required-sources-v1",
        "source_id": "source-1",
        "name": "Synthetic source",
        "owner": "Source owner",
        "responsible_operator": "source-operator",
        "jurisdiction": "SYNTHETIC",
        "legal_scope": "Synthetic legal scope",
        "data_scope": "Synthetic entity records",
        "access_method": SourceAccessMethod.INTERNAL,
        "credential_secret_ref": "vault:tradesieve/sources/source-1",
        "licence_summary": "Synthetic fixture; no production use",
        "contractual_constraints": "Private synthetic contract note",
        "refresh_expectation": timedelta(hours=1),
        "stale_after": timedelta(hours=2),
    }
    values.update(overrides)
    return SourceRegistration(**values)  # type: ignore[arg-type]


def observation(**overrides: object) -> SourceRuntimeObservation:
    values: dict[str, object] = {
        "deployment_id": "deployment-1",
        "source_id": "source-1",
        "availability": SourceAvailability.AVAILABLE,
        "observed_at": NOW,
        "active_snapshot_id": "snapshot-1",
        "retrieved_at": NOW,
        "effective_from": NOW - timedelta(days=1),
    }
    values.update(overrides)
    return SourceRuntimeObservation(**values)  # type: ignore[arg-type]


def test_incomplete_draft_persists_but_activation_reports_every_reason() -> None:
    draft = SourceRegistration("deployment-1", "required-sources-v1", "source-draft")
    assert draft.active is False
    assert draft.activation_block_reasons() == (
        ActivationBlockReason.MISSING_NAME,
        ActivationBlockReason.MISSING_OWNER,
        ActivationBlockReason.MISSING_RESPONSIBLE_OPERATOR,
        ActivationBlockReason.MISSING_JURISDICTION,
        ActivationBlockReason.MISSING_LEGAL_SCOPE,
        ActivationBlockReason.MISSING_DATA_SCOPE,
        ActivationBlockReason.MISSING_LICENCE_SUMMARY,
        ActivationBlockReason.MISSING_ACCESS_METHOD,
        ActivationBlockReason.MISSING_REFRESH_EXPECTATION,
        ActivationBlockReason.MISSING_STALE_THRESHOLD,
    )
    with pytest.raises(SourceActivationRefused) as exc_info:
        draft.activated()
    assert exc_info.value.reasons == draft.activation_block_reasons()
    assert str(exc_info.value) == "source activation refused"


def test_blank_governance_and_bad_threshold_order_block_activation() -> None:
    draft = registration(
        owner="  ",
        stale_after=timedelta(minutes=30),
    )
    assert draft.activation_block_reasons() == (
        ActivationBlockReason.MISSING_OWNER,
        ActivationBlockReason.STALE_THRESHOLD_BEFORE_REFRESH,
    )
    with pytest.raises(ValueError, match="complete governance"):
        registration(active=True, owner="")


def test_complete_governance_activates_immutably() -> None:
    draft = registration()
    active = draft.activated()
    assert draft.active is False
    assert active.active is True
    assert active.activation_block_reasons() == ()


@pytest.mark.parametrize(
    "overrides",
    [
        {"deployment_id": "bad id"},
        {"source_set_id": ""},
        {"source_id": "bad/id"},
        {"name": "x" * (MAX_SHORT_TEXT_LENGTH + 1)},
        {"legal_scope": "x" * (MAX_LONG_TEXT_LENGTH + 1)},
        {"credential_secret_ref": "raw-password"},
        {"refresh_expectation": timedelta(0)},
        {"refresh_expectation": timedelta(microseconds=1)},
        {"stale_after": timedelta(seconds=MAX_FRESHNESS_SECONDS + 1)},
    ],
)
def test_registration_rejects_unsafe_identity_text_secret_and_duration(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        registration(**overrides)


def test_registration_accepts_exact_short_and_long_text_boundaries() -> None:
    bounded = registration(
        name="n" * MAX_SHORT_TEXT_LENGTH,
        legal_scope="l" * MAX_LONG_TEXT_LENGTH,
    )
    assert len(bounded.name or "") == MAX_SHORT_TEXT_LENGTH
    assert len(bounded.legal_scope or "") == MAX_LONG_TEXT_LENGTH


@pytest.mark.parametrize(
    "overrides",
    [
        {"deployment_id": "bad id"},
        {"source_id": "bad/id"},
        {"active_snapshot_id": "bad/id"},
        {"observed_at": NOW.replace(tzinfo=None)},
        {"retrieved_at": NOW.replace(tzinfo=None)},
        {"effective_from": NOW.replace(tzinfo=None)},
        {"retrieved_at": NOW + timedelta(seconds=1)},
    ],
)
def test_observation_rejects_invalid_identity_and_naive_time(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        observation(**overrides)


def test_status_is_derived_current_then_stale_at_exact_boundary() -> None:
    active = registration().activated()
    runtime = observation()
    current = evaluate_source(active, runtime, now=NOW + timedelta(minutes=30))
    stale = evaluate_source(active, runtime, now=NOW + timedelta(hours=2))
    assert current.status is SourceFreshnessStatus.CURRENT
    assert stale.status is SourceFreshnessStatus.STALE


@pytest.mark.parametrize(
    ("source", "runtime", "now", "expected"),
    [
        (
            registration(),
            observation(),
            NOW,
            SourceFreshnessStatus.QUARANTINED,
        ),
        (
            registration().activated(),
            None,
            NOW,
            SourceFreshnessStatus.UNAVAILABLE,
        ),
        (
            registration().activated(),
            observation(availability=SourceAvailability.QUARANTINED),
            NOW,
            SourceFreshnessStatus.QUARANTINED,
        ),
        (
            registration().activated(),
            observation(availability=SourceAvailability.UNAVAILABLE),
            NOW,
            SourceFreshnessStatus.UNAVAILABLE,
        ),
        (
            registration().activated(),
            observation(active_snapshot_id=None),
            NOW,
            SourceFreshnessStatus.UNAVAILABLE,
        ),
        (
            registration().activated(),
            observation(retrieved_at=None),
            NOW,
            SourceFreshnessStatus.UNAVAILABLE,
        ),
        (
            registration().activated(),
            observation(observed_at=NOW + timedelta(seconds=1)),
            NOW,
            SourceFreshnessStatus.UNAVAILABLE,
        ),
        (
            registration().activated(),
            observation(effective_from=NOW + timedelta(seconds=1)),
            NOW,
            SourceFreshnessStatus.UNAVAILABLE,
        ),
    ],
)
def test_non_current_states_remain_explicit(
    source: SourceRegistration,
    runtime: SourceRuntimeObservation | None,
    now: datetime,
    expected: SourceFreshnessStatus,
) -> None:
    assert evaluate_source(source, runtime, now=now).status is expected


def test_status_evaluation_rejects_untrusted_time_or_identity() -> None:
    active = registration().activated()
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_source(active, observation(), now=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="identity mismatch"):
        evaluate_source(active, observation(source_id="source-2"), now=NOW)
    with pytest.raises(ValueError, match="identity mismatch"):
        evaluate_source(
            registration(),
            observation(source_id="source-2"),
            now=NOW,
        )


def evaluation(source_id: str, status: SourceFreshnessStatus) -> SourceEvaluation:
    source = registration(source_id=source_id).activated()
    runtime = observation(source_id=source_id)
    if status is SourceFreshnessStatus.CURRENT:
        return evaluate_source(source, runtime, now=NOW)
    if status is SourceFreshnessStatus.STALE:
        return evaluate_source(source, runtime, now=NOW + timedelta(hours=2))
    if status is SourceFreshnessStatus.QUARANTINED:
        return evaluate_source(
            source,
            observation(
                source_id=source_id,
                availability=SourceAvailability.QUARANTINED,
            ),
            now=NOW,
        )
    return evaluate_source(
        source,
        observation(
            source_id=source_id,
            availability=SourceAvailability.UNAVAILABLE,
        ),
        now=NOW,
    )


def test_required_manifest_detects_empty_missing_and_mixed_sources() -> None:
    empty = evaluate_required_source_set((), ())
    assert empty.ready is False
    assert empty.required_source_ids == ()
    assert empty.issues == ()

    missing = evaluate_required_source_set((), ("source-missing",))
    assert missing.status is SourceFreshnessStatus.UNAVAILABLE
    assert [(issue.source_id, issue.status) for issue in missing.issues] == [
        ("source-missing", SourceFreshnessStatus.UNAVAILABLE)
    ]

    mixed = evaluate_required_source_set(
        (
            evaluation("source-current", SourceFreshnessStatus.CURRENT),
            evaluation("source-stale", SourceFreshnessStatus.STALE),
            evaluation("optional-unavailable", SourceFreshnessStatus.UNAVAILABLE),
        ),
        ("source-current", "source-stale"),
    )
    assert mixed.ready is False
    assert mixed.status is SourceFreshnessStatus.STALE
    assert [(issue.source_id, issue.status) for issue in mixed.issues] == [
        ("source-stale", SourceFreshnessStatus.STALE)
    ]


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (
            [SourceFreshnessStatus.STALE, SourceFreshnessStatus.QUARANTINED],
            SourceFreshnessStatus.QUARANTINED,
        ),
        (
            [SourceFreshnessStatus.QUARANTINED, SourceFreshnessStatus.UNAVAILABLE],
            SourceFreshnessStatus.UNAVAILABLE,
        ),
        ([SourceFreshnessStatus.CURRENT], SourceFreshnessStatus.CURRENT),
    ],
)
def test_required_readiness_uses_fail_closed_status_priority(
    statuses: list[SourceFreshnessStatus], expected: SourceFreshnessStatus
) -> None:
    evaluations = tuple(
        evaluation(f"source-{index}", status) for index, status in enumerate(statuses)
    )
    ids = tuple(item.registration.source_id for item in evaluations)
    result = evaluate_required_source_set(evaluations, ids)
    assert result.status is expected
    assert result.ready is (expected is SourceFreshnessStatus.CURRENT)


@pytest.mark.parametrize(
    "manifest",
    [
        ("bad deployment", "set-1", ()),
        ("deployment-1", "bad/set", ()),
        ("deployment-1", "set-1", ("bad/id",)),
        ("deployment-1", "set-1", ("source-2", "source-1")),
        ("deployment-1", "set-1", ("source-1", "source-1")),
        (
            "deployment-1",
            "set-1",
            tuple(f"source-{index:03d}" for index in range(MAX_REQUIRED_SOURCES + 1)),
        ),
    ],
)
def test_manifest_requires_safe_sorted_unique_identity(
    manifest: tuple[str, str, tuple[str, ...]],
) -> None:
    with pytest.raises(ValueError):
        SourceSetManifest(*manifest)


def test_readiness_function_rejects_invalid_expected_identity() -> None:
    with pytest.raises(ValueError):
        evaluate_required_source_set((), ("bad/id",))
    with pytest.raises(ValueError, match="sorted and unique"):
        evaluate_required_source_set((), ("source-2", "source-1"))
