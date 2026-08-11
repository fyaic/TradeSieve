"""Official BIS Common High Priority List candidate lookup tests."""

from __future__ import annotations

from typing import Any, cast

import pytest

from tradesieve.domain.common_high_priority import (
    CHPL_CONTENT_HASH,
    CHPL_ITEMS,
    CHPL_SOURCE_URL,
    ChplTier,
    find_chpl_candidate,
    normalize_hs6,
)


def test_official_chpl_fixture_has_exact_unique_50_code_tiers() -> None:
    assert len(CHPL_ITEMS) == 50
    assert len({item.hs6 for item in CHPL_ITEMS}) == 50
    assert CHPL_CONTENT_HASH.startswith("sha256:")
    assert CHPL_SOURCE_URL.startswith("https://www.bis.gov/")
    assert find_chpl_candidate("8542.31") is not None
    assert find_chpl_candidate("85423190").tier is ChplTier.TIER_1  # type: ignore[union-attr]
    assert find_chpl_candidate("8504400000").tier is ChplTier.TIER_3_A  # type: ignore[union-attr]
    assert find_chpl_candidate("845710").tier is ChplTier.TIER_4_B  # type: ignore[union-attr]
    assert find_chpl_candidate("400921") is None


@pytest.mark.parametrize(
    "value",
    [cast(Any, 854231), "", "85423", "8542312", "８５４２３１", "8542-A"],
)
def test_hs_candidate_normalization_rejects_ambiguous_shapes(value: Any) -> None:
    with pytest.raises(ValueError):
        normalize_hs6(value)


def test_hs_candidate_normalization_accepts_document_punctuation() -> None:
    assert normalize_hs6("85 42.31") == "854231"
