"""Explicit, ephemeral identity adapter for synthetic demo mode only."""

from __future__ import annotations

import secrets
from collections.abc import Callable, Mapping
from copy import deepcopy

from tradesieve.ports.identity import IdentityVerificationError


class DemoVerifiedClaimsProvider:
    """Issue one ephemeral opaque bearer for an explicitly enabled demo identity."""

    def __init__(
        self,
        *,
        mode: str,
        enabled: bool,
        claims: Mapping[str, object],
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        if mode != "demo":
            raise ValueError("demo identity provider requires demo mode")
        if not enabled:
            raise ValueError("demo identity provider requires explicit opt-in")
        if claims.get("demo") is not True:
            raise ValueError("demo identity claims must be explicitly marked")
        bearer_token = (token_factory or (lambda: secrets.token_urlsafe(32)))()
        if not isinstance(bearer_token, str) or not bearer_token:
            raise ValueError("demo token factory returned an empty token")
        self._bearer_token = bearer_token
        self._claims = deepcopy(dict(claims))

    def issue_bearer_token(self) -> str:
        """Return this process-local demo bearer for synthetic tooling."""

        return self._bearer_token

    def verify(self, bearer_token: str) -> Mapping[str, object]:
        if not isinstance(bearer_token, str) or not secrets.compare_digest(
            bearer_token, self._bearer_token
        ):
            raise IdentityVerificationError
        return deepcopy(self._claims)
