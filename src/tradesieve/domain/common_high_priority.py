"""Deterministic candidate lookup for the official BIS Common High Priority List."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any

CHPL_SOURCE_URL = (
    "https://www.bis.gov/licensing/country-guidance/"
    "common-high-priority-items-list-chpl"
)
CHPL_PUBLICATION_DATE = date(2024, 2, 23)
CHPL_SOURCE_CHECKED_ON = date(2026, 8, 11)


class ChplTier(StrEnum):
    TIER_1 = "TIER_1"
    TIER_2 = "TIER_2"
    TIER_3_A = "TIER_3_A"
    TIER_3_B = "TIER_3_B"
    TIER_4_A = "TIER_4_A"
    TIER_4_B = "TIER_4_B"


@dataclass(frozen=True, slots=True)
class ChplItem:
    hs6: str
    tier: ChplTier


def _items(tier: ChplTier, codes: tuple[str, ...]) -> tuple[ChplItem, ...]:
    return tuple(ChplItem(hs6=code, tier=tier) for code in codes)


CHPL_ITEMS = (
    *_items(ChplTier.TIER_1, ("854231", "854232", "854233", "854239")),
    *_items(
        ChplTier.TIER_2,
        ("851762", "852691", "853221", "853224", "854800"),
    ),
    *_items(
        ChplTier.TIER_3_A,
        (
            "847150",
            "850440",
            "851769",
            "852589",
            "852910",
            "852990",
            "853669",
            "853690",
            "854110",
            "854121",
            "854129",
            "854130",
            "854149",
            "854151",
            "854159",
            "854160",
        ),
    ),
    *_items(
        ChplTier.TIER_3_B,
        (
            "848210",
            "848220",
            "848230",
            "848250",
            "880730",
            "901310",
            "901380",
            "901420",
            "901480",
        ),
    ),
    *_items(
        ChplTier.TIER_4_A,
        (
            "847180",
            "848610",
            "848620",
            "848640",
            "853400",
            "854320",
            "902750",
            "903020",
            "903032",
            "903039",
            "903082",
        ),
    ),
    *_items(
        ChplTier.TIER_4_B,
        ("845710", "845811", "845891", "845961", "846693"),
    ),
)
_CHPL_BY_HS6 = {item.hs6: item for item in CHPL_ITEMS}
CHPL_CONTENT_HASH = (
    "sha256:"
    + hashlib.sha256(
        json.dumps(
            [[item.hs6, item.tier.value] for item in CHPL_ITEMS],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()
)


def normalize_hs6(value: Any) -> str:
    """Normalize a documented 6/8/10 digit HS candidate to its HS-6 prefix."""

    if not isinstance(value, str):
        raise ValueError("HS candidate must be text")
    compact = value.replace(".", "").replace(" ", "")
    if not compact.isascii() or not compact.isdigit() or len(compact) not in {6, 8, 10}:
        raise ValueError("HS candidate must contain 6, 8 or 10 ASCII digits")
    return compact[:6]


def find_chpl_candidate(value: str) -> ChplItem | None:
    """Return exact HS-6 candidate evidence; never a legal classification."""

    return _CHPL_BY_HS6.get(normalize_hs6(value))


__all__ = [
    "CHPL_CONTENT_HASH",
    "CHPL_ITEMS",
    "CHPL_PUBLICATION_DATE",
    "CHPL_SOURCE_CHECKED_ON",
    "CHPL_SOURCE_URL",
    "ChplItem",
    "ChplTier",
    "find_chpl_candidate",
    "normalize_hs6",
]
