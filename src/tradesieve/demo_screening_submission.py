"""Explicitly demo-only synthetic screening intake and entitlement wiring."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from tradesieve.application.auth import (
    ActorContext,
    ActorType,
    Operation,
    RequestContext,
    ResolvedTargetFacts,
    Scope,
)
from tradesieve.application.contract_examples import transaction_screening_request
from tradesieve.application.contracts import ScreeningRequest
from tradesieve.application.screening_intake import (
    JSON_MEDIA_TYPE,
    CanonicalScreeningIntake,
)
from tradesieve.config import Settings

DEMO_SCREENING_ACTOR = "demo-screening-submitter"
DEMO_SCREENING_CLIENT = "tradesieve-demo-cli"
DEMO_SCREENING_TARGET_TYPE = "SCREENING_IDEMPOTENCY"
_DEMO_SCREENING_TARGET_ID = re.compile(r"^idem-[a-f0-9]{64}$")


class DemoScreeningFixture(StrEnum):
    BASELINE = "baseline"
    CHANGED = "changed"


def _require_demo(settings: Settings) -> None:
    if settings.mode != "demo":
        raise RuntimeError("synthetic screening requires explicit demo mode")


def _normalized_time(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("demo screening time must be timezone-aware")
    return value.astimezone(UTC)


def _fixture_request(fixture: DemoScreeningFixture) -> ScreeningRequest:
    if type(fixture) is not DemoScreeningFixture:
        raise TypeError("demo screening fixture must be typed")
    document = transaction_screening_request().model_dump(mode="json")
    if fixture is DemoScreeningFixture.CHANGED:
        external_object = document.get("external_object")
        if not isinstance(external_object, dict):
            raise RuntimeError("synthetic screening fixture is unavailable")
        external_object["object_version"] = "2"
    return ScreeningRequest.model_validate(document)


def synthetic_demo_screening_intake(
    settings: Settings,
    fixture: DemoScreeningFixture,
    *,
    now: datetime,
) -> CanonicalScreeningIntake:
    """Build one strict canonical intake from the repository synthetic request."""

    _require_demo(settings)
    normalized = _normalized_time(now)
    request = _fixture_request(fixture)
    raw_body = json.dumps(
        request.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    context = RequestContext(
        actor=ActorContext(
            subject=DEMO_SCREENING_ACTOR,
            client_id=DEMO_SCREENING_CLIENT,
            tenant_id=request.tenant_id,
            actor_type=ActorType.SERVICE,
            scopes=frozenset({Scope.SCREENING_SUBMIT}),
            roles=frozenset(),
            issuer="http://localhost/tradesieve-demo",
            audience="tradesieve-demo",
            issued_at=normalized - timedelta(minutes=1),
            expires_at=normalized + timedelta(hours=1),
            demo_identity=True,
        ),
        tenant_id=request.tenant_id,
        correlation_id=request.correlation_id,
    )
    return CanonicalScreeningIntake.decode(
        raw_body,
        JSON_MEDIA_TYPE,
        context=context,
        received_at=normalized,
    )


class DemoScreeningEntitlementResolver:
    """Allow only the fixed demo submitter over derived idempotency targets."""

    def __init__(self, settings: Settings) -> None:
        _require_demo(settings)
        self._tenant_id = transaction_screening_request().tenant_id

    def resolve(
        self,
        *,
        actor_subject: str,
        actor_tenant_id: str,
        operation: str,
        target_tenant_id: str,
        target_type: str,
        target_id: str,
    ) -> ResolvedTargetFacts | None:
        exact = (
            actor_subject == DEMO_SCREENING_ACTOR
            and actor_tenant_id == self._tenant_id
            and operation == Operation.SCREENING_SUBMIT.value
            and target_tenant_id == self._tenant_id
            and target_type == DEMO_SCREENING_TARGET_TYPE
            and _DEMO_SCREENING_TARGET_ID.fullmatch(target_id) is not None
        )
        return ResolvedTargetFacts() if exact else None


__all__ = [
    "DEMO_SCREENING_ACTOR",
    "DEMO_SCREENING_CLIENT",
    "DemoScreeningEntitlementResolver",
    "DemoScreeningFixture",
    "synthetic_demo_screening_intake",
]
