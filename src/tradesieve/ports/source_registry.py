"""Persistence port for governed source definitions and runtime observations."""

from __future__ import annotations

from typing import Protocol

from tradesieve.domain.source_registry import (
    ObservationWriteOutcome,
    SourceRegistration,
    SourceRuntimeObservation,
    SourceSetManifest,
)


class SourceRegistryRepository(Protocol):
    def save_manifest(self, manifest: SourceSetManifest) -> None: ...

    def get_manifest(
        self, deployment_id: str, source_set_id: str
    ) -> SourceSetManifest | None: ...

    def save_registration(self, registration: SourceRegistration) -> None: ...

    def get_registration(
        self, deployment_id: str, source_id: str
    ) -> SourceRegistration | None: ...

    def save_observation(
        self, observation: SourceRuntimeObservation
    ) -> ObservationWriteOutcome: ...

    def list_entries(
        self, deployment_id: str, source_set_id: str, *, limit: int
    ) -> tuple[tuple[SourceRegistration, SourceRuntimeObservation | None], ...]: ...
