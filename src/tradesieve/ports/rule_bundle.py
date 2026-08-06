"""Persistence boundary for immutable rule bundles, lifecycle, and command audit."""

from __future__ import annotations

from typing import Protocol

from tradesieve.domain.rule_bundle import (
    DraftWriteOutcome,
    LifecycleWriteOutcome,
    RuleBundleCommandAuditRecord,
    RuleBundleLifecycleEvent,
    RuleBundleVersion,
    RuleSetLifecycle,
)


class RuleBundlePersistenceError(Exception):
    def __init__(self) -> None:
        super().__init__("rule bundle persistence unavailable")


class RuleBundleRepository(Protocol):
    """Repository/UoW port whose named atomic methods define transaction boundaries."""

    def save_draft_atomic(
        self,
        bundle: RuleBundleVersion,
        drafted_event: RuleBundleLifecycleEvent,
        applied_audit: RuleBundleCommandAuditRecord,
        idempotent_audit: RuleBundleCommandAuditRecord,
    ) -> DraftWriteOutcome: ...

    def append_lifecycle_atomic(
        self,
        event: RuleBundleLifecycleEvent,
        applied_audit: RuleBundleCommandAuditRecord,
        idempotent_audit: RuleBundleCommandAuditRecord,
    ) -> LifecycleWriteOutcome: ...

    def append_command_audit(self, audit: RuleBundleCommandAuditRecord) -> None: ...

    def get_bundle(
        self,
        tenant_id: str,
        deployment_id: str,
        rule_set_id: str,
        bundle_id: str,
        version: str,
    ) -> RuleBundleVersion | None: ...

    def list_bundles(
        self,
        tenant_id: str,
        deployment_id: str,
        rule_set_id: str,
        *,
        limit: int,
    ) -> tuple[RuleBundleVersion, ...]: ...

    def list_lifecycle_events(
        self,
        tenant_id: str,
        deployment_id: str,
        rule_set_id: str,
        *,
        limit: int,
    ) -> tuple[RuleBundleLifecycleEvent, ...]: ...

    def get_lifecycle_snapshot(
        self,
        tenant_id: str,
        deployment_id: str,
        rule_set_id: str,
    ) -> tuple[tuple[RuleBundleLifecycleEvent, ...], RuleSetLifecycle]: ...

    def list_command_audits(
        self,
        tenant_id: str,
        deployment_id: str,
        rule_set_id: str,
        *,
        limit: int,
    ) -> tuple[RuleBundleCommandAuditRecord, ...]: ...
