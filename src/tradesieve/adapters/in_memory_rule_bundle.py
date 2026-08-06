"""Deterministic in-memory rule-bundle repository for application and unit use."""

from __future__ import annotations

from threading import RLock

from tradesieve.domain.rule_bundle import (
    DraftWriteOutcome,
    InvalidLifecycleTransition,
    LifecycleWriteOutcome,
    RuleBundleCommandAuditRecord,
    RuleBundleCommandReason,
    RuleBundleEventType,
    RuleBundleLifecycleEvent,
    RuleBundleRef,
    RuleBundleVersion,
    RuleSetLifecycle,
    fold_rule_bundle_events,
    rule_bundle_ref,
    semantically_applied_lifecycle_transition,
    validate_atomic_command_audit,
)
from tradesieve.ports.rule_bundle import RuleBundlePersistenceError

type RuleSetKey = tuple[str, str, str]
type BundleKey = tuple[str, str, str, str, str]


class InMemoryRuleBundleRepository:
    """Store immutable content and use copy-then-swap atomic write boundaries."""

    def __init__(self) -> None:
        self._bundles: dict[BundleKey, RuleBundleVersion] = {}
        self._events: dict[RuleSetKey, tuple[RuleBundleLifecycleEvent, ...]] = {}
        self._audits: dict[RuleSetKey, tuple[RuleBundleCommandAuditRecord, ...]] = {}
        self._fail_next_atomic = False
        self._lock = RLock()

    def fail_next_atomic_write(self) -> None:
        with self._lock:
            self._fail_next_atomic = True

    def corrupt_remove_bundle_for_test(
        self,
        tenant_id: str,
        deployment_id: str,
        rule_set_id: str,
        bundle_id: str,
        version: str,
    ) -> None:
        with self._lock:
            self._bundles.pop(
                (tenant_id, deployment_id, rule_set_id, bundle_id, version),
                None,
            )

    def corrupt_remove_drafted_event_for_test(
        self,
        tenant_id: str,
        deployment_id: str,
        rule_set_id: str,
        reference: RuleBundleRef,
    ) -> None:
        with self._lock:
            scope = (tenant_id, deployment_id, rule_set_id)
            self._events[scope] = tuple(
                event
                for event in self._events.get(scope, ())
                if not (
                    event.event_type is RuleBundleEventType.DRAFTED
                    and event.new_bundle == reference
                )
            )

    def corrupt_replace_lifecycle_events_for_test(
        self,
        tenant_id: str,
        deployment_id: str,
        rule_set_id: str,
        events: tuple[RuleBundleLifecycleEvent, ...],
    ) -> None:
        with self._lock:
            self._events[(tenant_id, deployment_id, rule_set_id)] = events

    def save_draft_atomic(
        self,
        bundle: RuleBundleVersion,
        drafted_event: RuleBundleLifecycleEvent,
        applied_audit: RuleBundleCommandAuditRecord,
        idempotent_audit: RuleBundleCommandAuditRecord,
    ) -> DraftWriteOutcome:
        with self._lock:
            scope = self._bundle_scope(bundle)
            key = self._bundle_key(bundle)
            reference = rule_bundle_ref(bundle)
            if (
                drafted_event.event_type is not RuleBundleEventType.DRAFTED
                or drafted_event.previous_bundle is not None
                or drafted_event.new_bundle != reference
                or self._event_scope(drafted_event) != scope
            ):
                raise ValueError(
                    "draft event must exactly identify the immutable bundle"
                )
            validate_atomic_command_audit(
                applied_audit,
                scope,
                reference,
                RuleBundleCommandReason.DRAFT_APPLIED,
                drafted_event,
            )
            validate_atomic_command_audit(
                idempotent_audit,
                scope,
                reference,
                RuleBundleCommandReason.DRAFT_IDEMPOTENT,
                None,
            )
            existing = self._bundles.get(key)
            if existing is not None:
                if existing.content_hash() != reference.content_hash:
                    return DraftWriteOutcome.CONFLICT
                matching_drafts = tuple(
                    event
                    for event in self._events.get(scope, ())
                    if event.event_type is RuleBundleEventType.DRAFTED
                    and event.new_bundle == reference
                )
                if len(matching_drafts) != 1:
                    raise RuleBundlePersistenceError
                audits = self._audits_with(scope, idempotent_audit)
                self._raise_if_atomic_failure()
                self._audits[scope] = audits
                return DraftWriteOutcome.IDEMPOTENT

            events = self._events.get(scope, ()) + (drafted_event,)
            try:
                fold_rule_bundle_events(events)
            except InvalidLifecycleTransition:
                return DraftWriteOutcome.CONFLICT
            audits = self._audits_with(scope, applied_audit)
            bundles = dict(self._bundles)
            bundles[key] = bundle
            self._raise_if_atomic_failure()
            self._bundles = bundles
            self._events[scope] = events
            self._audits[scope] = audits
            return DraftWriteOutcome.APPLIED

    def append_lifecycle_atomic(
        self,
        event: RuleBundleLifecycleEvent,
        applied_audit: RuleBundleCommandAuditRecord,
        idempotent_audit: RuleBundleCommandAuditRecord,
    ) -> LifecycleWriteOutcome:
        with self._lock:
            if event.event_type is RuleBundleEventType.DRAFTED:
                raise ValueError("DRAFTED must use save_draft_atomic")
            scope = self._event_scope(event)
            target = event.new_bundle or event.previous_bundle
            assert target is not None
            self._require_stored_references(scope, event)
            validate_atomic_command_audit(
                applied_audit,
                scope,
                target,
                self._applied_reason(event.event_type),
                event,
            )
            validate_atomic_command_audit(
                idempotent_audit,
                scope,
                target,
                self._idempotent_reason(event.event_type),
                None,
            )
            current_events = self._events.get(scope, ())
            same_id = next(
                (
                    stored
                    for stored in current_events
                    if stored.event_id == event.event_id
                ),
                None,
            )
            if same_id is not None:
                raise RuleBundlePersistenceError
            lifecycle = fold_rule_bundle_events(current_events)
            responsible = semantically_applied_lifecycle_transition(
                event, lifecycle, current_events
            )
            if responsible is not None:
                audits = self._audits_with(scope, idempotent_audit)
                self._raise_if_atomic_failure()
                self._audits[scope] = audits
                return LifecycleWriteOutcome.IDEMPOTENT
            events = current_events + (event,)
            try:
                fold_rule_bundle_events(events)
            except InvalidLifecycleTransition:
                return LifecycleWriteOutcome.CONFLICT
            audits = self._audits_with(scope, applied_audit)
            self._raise_if_atomic_failure()
            self._events[scope] = events
            self._audits[scope] = audits
            return LifecycleWriteOutcome.APPLIED

    def append_command_audit(self, audit: RuleBundleCommandAuditRecord) -> None:
        with self._lock:
            scope = (audit.tenant_id, audit.deployment_id, audit.rule_set_id)
            self._audits[scope] = self._audits_with(scope, audit)

    def get_bundle(
        self,
        tenant_id: str,
        deployment_id: str,
        rule_set_id: str,
        bundle_id: str,
        version: str,
    ) -> RuleBundleVersion | None:
        with self._lock:
            return self._bundles.get(
                (tenant_id, deployment_id, rule_set_id, bundle_id, version)
            )

    def list_bundles(
        self,
        tenant_id: str,
        deployment_id: str,
        rule_set_id: str,
        *,
        limit: int,
    ) -> tuple[RuleBundleVersion, ...]:
        self._require_limit(limit)
        with self._lock:
            values = (
                bundle
                for key, bundle in self._bundles.items()
                if key[:3] == (tenant_id, deployment_id, rule_set_id)
            )
            return tuple(
                sorted(values, key=lambda item: (item.bundle_id, item.version))
            )[:limit]

    def list_lifecycle_events(
        self,
        tenant_id: str,
        deployment_id: str,
        rule_set_id: str,
        *,
        limit: int,
    ) -> tuple[RuleBundleLifecycleEvent, ...]:
        self._require_limit(limit)
        with self._lock:
            return self._events.get((tenant_id, deployment_id, rule_set_id), ())[:limit]

    def get_lifecycle_snapshot(
        self,
        tenant_id: str,
        deployment_id: str,
        rule_set_id: str,
    ) -> tuple[tuple[RuleBundleLifecycleEvent, ...], RuleSetLifecycle]:
        with self._lock:
            events = self._events.get((tenant_id, deployment_id, rule_set_id), ())
            try:
                return events, fold_rule_bundle_events(events)
            except (InvalidLifecycleTransition, ValueError) as exc:
                raise RuleBundlePersistenceError from exc

    def list_command_audits(
        self,
        tenant_id: str,
        deployment_id: str,
        rule_set_id: str,
        *,
        limit: int,
    ) -> tuple[RuleBundleCommandAuditRecord, ...]:
        self._require_limit(limit)
        with self._lock:
            return self._audits.get((tenant_id, deployment_id, rule_set_id), ())[:limit]

    def _audits_with(
        self, scope: RuleSetKey, audit: RuleBundleCommandAuditRecord
    ) -> tuple[RuleBundleCommandAuditRecord, ...]:
        current = self._audits.get(scope, ())
        if any(item.command_event_id == audit.command_event_id for item in current):
            raise RuleBundlePersistenceError
        return current + (audit,)

    def _require_stored_references(
        self, scope: RuleSetKey, event: RuleBundleLifecycleEvent
    ) -> None:
        for reference in (event.previous_bundle, event.new_bundle):
            if reference is None:
                continue
            stored = self.get_bundle(*scope, reference.bundle_id, reference.version)
            if stored is None or rule_bundle_ref(stored) != reference:
                raise RuleBundlePersistenceError

    @staticmethod
    def _applied_reason(event_type: RuleBundleEventType) -> RuleBundleCommandReason:
        return {
            RuleBundleEventType.APPROVED: RuleBundleCommandReason.APPROVED,
            RuleBundleEventType.ACTIVATED: RuleBundleCommandReason.ACTIVATED,
            RuleBundleEventType.RETIRED: RuleBundleCommandReason.RETIRED,
            RuleBundleEventType.ROLLED_BACK: RuleBundleCommandReason.ROLLED_BACK,
        }[event_type]

    @staticmethod
    def _idempotent_reason(event_type: RuleBundleEventType) -> RuleBundleCommandReason:
        return {
            RuleBundleEventType.APPROVED: RuleBundleCommandReason.APPROVE_IDEMPOTENT,
            RuleBundleEventType.ACTIVATED: RuleBundleCommandReason.ACTIVATE_IDEMPOTENT,
            RuleBundleEventType.RETIRED: RuleBundleCommandReason.RETIRE_IDEMPOTENT,
            RuleBundleEventType.ROLLED_BACK: RuleBundleCommandReason.ROLLBACK_IDEMPOTENT,
        }[event_type]

    def _raise_if_atomic_failure(self) -> None:
        if self._fail_next_atomic:
            self._fail_next_atomic = False
            raise RuleBundlePersistenceError

    @staticmethod
    def _require_limit(limit: int) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")

    @staticmethod
    def _bundle_scope(bundle: RuleBundleVersion) -> RuleSetKey:
        return (bundle.tenant_id, bundle.deployment_id, bundle.rule_set_id)

    @staticmethod
    def _bundle_key(bundle: RuleBundleVersion) -> BundleKey:
        return (
            bundle.tenant_id,
            bundle.deployment_id,
            bundle.rule_set_id,
            bundle.bundle_id,
            bundle.version,
        )

    @staticmethod
    def _event_scope(event: RuleBundleLifecycleEvent) -> RuleSetKey:
        return (event.tenant_id, event.deployment_id, event.rule_set_id)
