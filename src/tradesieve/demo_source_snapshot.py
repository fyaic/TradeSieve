"""Explicitly demo-only source fixtures, actors, and trusted entitlements."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from tradesieve.application.auth import (
    ActorContext,
    ActorType,
    AuthorizationRequest,
    AuthorizationService,
    AuthorizedRequest,
    Operation,
    RequestContext,
    ResolvedTargetFacts,
    Role,
    Scope,
    TargetObject,
)
from tradesieve.application.source_snapshot import (
    SOURCE_SNAPSHOT_TARGET_TYPE,
    SOURCE_TARGET_TYPE,
    ImmutableSourceSnapshotIdentity,
    source_authorization_target_id,
    source_snapshot_authorization_target_id,
)
from tradesieve.config import Settings
from tradesieve.domain.source_registry import SourceAccessMethod, SourceRegistration
from tradesieve.domain.source_snapshot import (
    MAX_QUERY_LIMIT,
    ParsedSourceSnapshot,
    bytes_sha256,
)
from tradesieve.ports.source_snapshot import (
    SourceSnapshotRepository,
)

DEMO_SOURCE_ID = "synthetic-source-v1"
DEMO_SOURCE_OPERATOR = "demo-source-operator"
DEMO_SOURCE_APPROVER = "demo-source-approver"
DEMO_SOURCE_READER = "demo-source-reader"
DEMO_SOURCE_EFFECTIVE_FROM = datetime(2026, 1, 1, tzinfo=UTC)
DEMO_SOURCE_APPROVAL_REASON = "Synthetic demo source snapshot reviewed."
DEMO_SOURCE_ACTIVATION_REASON = "Synthetic demo source snapshot activated."


class DemoSourceActor(StrEnum):
    OPERATOR = "OPERATOR"
    APPROVER = "APPROVER"
    READER = "READER"


@dataclass(frozen=True, slots=True)
class DemoSourceFixture:
    fixture_id: str
    original_name: str
    content: bytes
    effective_from: datetime

    def __post_init__(self) -> None:
        if (
            not self.fixture_id.startswith("synthetic-demo-source-")
            or not self.original_name.startswith("synthetic-demo-source-")
            or not self.original_name.endswith(".json")
            or not isinstance(self.content, bytes)
            or not self.content
            or self.effective_from.tzinfo is None
            or self.effective_from.utcoffset() != timedelta(0)
        ):
            raise ValueError("synthetic demo source fixture is invalid")

    @property
    def content_hash(self) -> str:
        return bytes_sha256(self.content)


def synthetic_demo_source_fixtures(settings: Settings) -> tuple[DemoSourceFixture, ...]:
    _require_demo(settings)
    first = _fixture_bytes(
        [
            {
                "record_id": "synthetic-entity-alpha",
                "effective_from": "2026-01-01",
                "effective_to": None,
                "assertions": [
                    {"field": "name", "value": "SYNTHETIC ALPHA COMPONENTS"},
                    {"field": "reference", "value": "SYNTHETIC-ONLY-0001"},
                ],
            }
        ]
    )
    second = _fixture_bytes(
        [
            {
                "record_id": "synthetic-entity-alpha",
                "effective_from": "2026-01-01",
                "effective_to": None,
                "assertions": [
                    {
                        "field": "name",
                        "value": "SYNTHETIC ALPHA COMPONENTS UPDATED",
                    },
                    {"field": "reference", "value": "SYNTHETIC-ONLY-0001"},
                ],
            },
            {
                "record_id": "synthetic-entity-beta",
                "effective_from": "2026-02-01",
                "effective_to": None,
                "assertions": [
                    {"field": "name", "value": "SYNTHETIC BETA LOGISTICS"},
                    {"field": "reference", "value": "SYNTHETIC-ONLY-0002"},
                ],
            },
        ]
    )
    return (
        DemoSourceFixture(
            "synthetic-demo-source-v1",
            "synthetic-demo-source-v1.json",
            first,
            DEMO_SOURCE_EFFECTIVE_FROM,
        ),
        DemoSourceFixture(
            "synthetic-demo-source-v2",
            "synthetic-demo-source-v2.json",
            second,
            DEMO_SOURCE_EFFECTIVE_FROM + timedelta(days=31),
        ),
    )


def demo_source_registration(settings: Settings) -> SourceRegistration:
    _require_demo(settings)
    return SourceRegistration(
        deployment_id=settings.deployment_id,
        source_set_id=settings.required_source_set,
        source_id=DEMO_SOURCE_ID,
        name="Synthetic source fixture",
        owner="TradeSieve demo",
        responsible_operator=DEMO_SOURCE_OPERATOR,
        jurisdiction="SYNTHETIC",
        legal_scope="Synthetic screening behavior only",
        data_scope="Synthetic entities with no production data",
        access_method=SourceAccessMethod.INTERNAL,
        licence_summary="Synthetic demo fixture; no production use",
        refresh_expectation=timedelta(hours=1),
        stale_after=timedelta(hours=2),
        active=True,
    )


def demo_snapshot_identity(
    snapshot: ParsedSourceSnapshot,
) -> ImmutableSourceSnapshotIdentity:
    snapshot.verify_integrity()
    return ImmutableSourceSnapshotIdentity(
        snapshot_id=snapshot.snapshot_id,
        content_hash=snapshot.content_hash,
    )


class DemoSourceEntitlementResolver:
    """Resolve exact fixture targets and creator facts from verified persistence."""

    def __init__(
        self,
        settings: Settings,
        repository: SourceSnapshotRepository,
        fixtures: tuple[DemoSourceFixture, ...],
    ) -> None:
        _require_demo(settings)
        if (
            not isinstance(fixtures, tuple)
            or len(fixtures) != 2
            or any(not isinstance(item, DemoSourceFixture) for item in fixtures)
        ):
            raise ValueError("demo source resolver requires two fixtures")
        self._repository = repository
        self._deployment_id = settings.deployment_id
        self._source_set_id = settings.required_source_set
        self._tenant_id = settings.rule_bundle_tenant_id
        self._source_target = source_authorization_target_id(
            self._deployment_id, self._source_set_id, DEMO_SOURCE_ID
        )
        self._fixture_hashes = frozenset(item.content_hash for item in fixtures)

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
        if actor_tenant_id != self._tenant_id or target_tenant_id != self._tenant_id:
            return None
        source_entitlement = (actor_subject, operation, target_type, target_id)
        if source_entitlement in {
            (
                DEMO_SOURCE_READER,
                Operation.SOURCE_READ,
                SOURCE_TARGET_TYPE,
                self._source_target,
            ),
            *(
                (
                    DEMO_SOURCE_OPERATOR,
                    item,
                    SOURCE_TARGET_TYPE,
                    self._source_target,
                )
                for item in (
                    Operation.SOURCE_SNAPSHOT_INGEST,
                    Operation.SOURCE_SNAPSHOT_PARSE,
                    Operation.SOURCE_SNAPSHOT_VALIDATE,
                )
            ),
        }:
            return ResolvedTargetFacts()
        if (
            actor_subject != DEMO_SOURCE_APPROVER
            or operation
            not in {
                Operation.SOURCE_SNAPSHOT_APPROVE,
                Operation.SOURCE_SNAPSHOT_ACTIVATE,
                Operation.SOURCE_SNAPSHOT_ROLLBACK,
            }
            or target_type != SOURCE_SNAPSHOT_TARGET_TYPE
        ):
            return None
        snapshots = self._repository.list_snapshots(
            self._deployment_id, DEMO_SOURCE_ID, limit=MAX_QUERY_LIMIT
        )
        matches = tuple(
            snapshot
            for snapshot in snapshots
            if snapshot.raw_object.content_hash in self._fixture_hashes
            and source_snapshot_authorization_target_id(
                self._deployment_id,
                self._source_set_id,
                DEMO_SOURCE_ID,
                snapshot.snapshot_id,
                snapshot.content_hash,
            )
            == target_id
        )
        if len(matches) != 1:
            return None
        snapshot = matches[0]
        snapshot.verify_integrity()
        _, lifecycle = self._repository.get_lifecycle_snapshot(
            self._deployment_id, DEMO_SOURCE_ID
        )
        creator = lifecycle.creator_for(snapshot.reference())
        if creator != DEMO_SOURCE_OPERATOR:
            return None
        return ResolvedTargetFacts(author_actor_id=creator)


def authorize_demo_source_request(
    settings: Settings,
    authorization: AuthorizationService,
    *,
    actor: DemoSourceActor,
    operation: Operation,
    now: datetime,
    identity: ImmutableSourceSnapshotIdentity | None = None,
) -> AuthorizedRequest:
    """Authorize one bound demo request through the real authorization service."""

    _require_demo(settings)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("demo source authorization time must be timezone-aware")
    subject, actor_type, scopes, roles = {
        DemoSourceActor.OPERATOR: (
            DEMO_SOURCE_OPERATOR,
            ActorType.SERVICE,
            frozenset({Scope.SOURCE_OPERATE}),
            frozenset({Role.SOURCE_OPERATOR}),
        ),
        DemoSourceActor.APPROVER: (
            DEMO_SOURCE_APPROVER,
            ActorType.HUMAN,
            frozenset({Scope.SOURCE_APPROVE}),
            frozenset({Role.SOURCE_APPROVER}),
        ),
        DemoSourceActor.READER: (
            DEMO_SOURCE_READER,
            ActorType.HUMAN,
            frozenset({Scope.SOURCE_READ}),
            frozenset(),
        ),
    }[actor]
    governance = operation in {
        Operation.SOURCE_SNAPSHOT_APPROVE,
        Operation.SOURCE_SNAPSHOT_ACTIVATE,
        Operation.SOURCE_SNAPSHOT_ROLLBACK,
    }
    if governance is not (identity is not None):
        raise ValueError("demo governance requires one immutable snapshot identity")
    if identity is None:
        target_type = SOURCE_TARGET_TYPE
        target_id = source_authorization_target_id(
            settings.deployment_id, settings.required_source_set, DEMO_SOURCE_ID
        )
    else:
        target_type = SOURCE_SNAPSHOT_TARGET_TYPE
        target_id = source_snapshot_authorization_target_id(
            settings.deployment_id,
            settings.required_source_set,
            DEMO_SOURCE_ID,
            identity.snapshot_id,
            identity.content_hash,
        )
    normalized = now.astimezone(UTC)
    context = RequestContext(
        actor=ActorContext(
            subject=subject,
            client_id="tradesieve-demo-runtime",
            tenant_id=settings.rule_bundle_tenant_id,
            actor_type=actor_type,
            scopes=scopes,
            roles=roles,
            issuer="http://localhost/tradesieve-demo",
            audience="tradesieve-demo",
            issued_at=normalized - timedelta(minutes=1),
            expires_at=normalized + timedelta(hours=1),
            demo_identity=True,
        ),
        tenant_id=settings.rule_bundle_tenant_id,
        correlation_id=f"demo-{operation.value.lower().replace('_', '-')}",
    )
    return authorization.require(
        AuthorizationRequest(
            context=context,
            operation=operation,
            target=TargetObject(
                settings.rule_bundle_tenant_id,
                target_type,
                target_id,
            ),
        ),
        now=normalized,
    )


def _fixture_bytes(records: list[dict[str, object]]) -> bytes:
    return json.dumps(
        {
            "schema_id": "tradesieve-synthetic-source-v1",
            "declared_record_count": len(records),
            "records": records,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _require_demo(settings: Settings) -> None:
    if settings.mode != "demo" or not settings.demo_bootstrap_enabled:
        raise RuntimeError(
            "synthetic demo source identities require explicit demo mode"
        )


__all__ = [
    "DEMO_SOURCE_ACTIVATION_REASON",
    "DEMO_SOURCE_APPROVAL_REASON",
    "DEMO_SOURCE_APPROVER",
    "DEMO_SOURCE_ID",
    "DEMO_SOURCE_OPERATOR",
    "DEMO_SOURCE_READER",
    "DemoSourceActor",
    "DemoSourceEntitlementResolver",
    "DemoSourceFixture",
    "authorize_demo_source_request",
    "demo_snapshot_identity",
    "demo_source_registration",
    "synthetic_demo_source_fixtures",
]
