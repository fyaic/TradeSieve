"""Identity ports at the cryptographic and tenant-mapping trust boundaries."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol


class IdentityVerificationError(Exception):
    """A credential could not be safely verified by the identity provider."""


class VerifiedClaimsProvider(Protocol):
    """Cryptographically verify a bearer token and normalize trusted claims.

    A production RFC 9068 adapter must require ``typ=at+jwt`` (or
    ``application/at+jwt``) so an ID token cannot be confused with an access token,
    validate the signature against issuer-bound keys, enforce an asymmetric algorithm
    allowlist, reject ``none`` and algorithm confusion, validate issuer and audience
    exactly, and never follow token-supplied ``jku`` or ``x5u`` URLs. Key rotation may
    use a bounded cache refresh with one retry. The application must never decode a JWT
    or accept unverified claims.
    """

    def verify(self, bearer_token: str) -> Mapping[str, object]:
        """Return verified normalized claims or raise IdentityVerificationError."""

        ...


class TenantResolver(Protocol):
    """Map an external issuer/tenant pair to a known canonical tenant."""

    def resolve(self, issuer: str, external_tenant_id: str) -> str | None: ...


class ActorResolver(Protocol):
    """Map an issuer-local subject to one stable canonical actor identity."""

    def resolve(
        self,
        issuer: str,
        external_subject: str,
        client_id: str,
        principal_kind: str,
    ) -> str | None: ...
