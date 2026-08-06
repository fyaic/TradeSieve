"""Management operation safety tests."""

from __future__ import annotations

import pytest

from tradesieve.config import Settings
from tradesieve.runtime import bootstrap_demo


def test_demo_bootstrap_is_denied_when_disabled() -> None:
    settings = Settings(
        mode="production",
        database_url="postgresql://service:strong-password@db/tradesieve",  # pragma: allowlist secret
        demo_bootstrap_enabled=False,
        deployment_id="production-eu-1",
        required_source_set="approved-sources-v1",
        required_rule_set="approved-rules-v1",
    )
    with pytest.raises(RuntimeError, match="disabled outside explicit demo mode"):
        bootstrap_demo(settings)
