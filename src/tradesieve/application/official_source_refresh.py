"""Retrieve, verify, project, and atomically activate official screening sources."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal, Protocol

from tradesieve.application.contracts import ContractModel
from tradesieve.domain.eu_dual_use import EuDualUseControlList
from tradesieve.domain.eu_fsf import EuFsfSnapshot
from tradesieve.domain.official_sources import (
    OfficialSourceBundle,
    OfficialSourceWriteOutcome,
)


class _RetrievedFsf(Protocol):
    @property
    def retrieved_at(self) -> datetime: ...

    @property
    def content_hash(self) -> str: ...

    @property
    def content_type(self) -> str: ...

    @property
    def content(self) -> bytes: ...


class _FsfSource(Protocol):
    def retrieve(self) -> _RetrievedFsf: ...


class _FsfParser(Protocol):
    def parse(self, content: bytes) -> EuFsfSnapshot: ...


class _RetrievedDualUse(Protocol):
    @property
    def retrieved_at(self) -> datetime: ...

    @property
    def content_hash(self) -> str: ...

    @property
    def content_type(self) -> str: ...

    @property
    def content(self) -> bytes: ...

    @property
    def control_list(self) -> EuDualUseControlList: ...


class _DualUseSource(Protocol):
    def retrieve_source(self) -> _RetrievedDualUse: ...


class OfficialSourceRepository(Protocol):
    def activate(self, bundle: OfficialSourceBundle) -> OfficialSourceWriteOutcome: ...


class OfficialSourceRefreshResult(ContractModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    outcome: OfficialSourceWriteOutcome
    bundle_id: str
    bundle_content_hash: str
    fsf_snapshot_id: str
    fsf_snapshot_content_hash: str
    fsf_entity_count: int
    fsf_alias_count: int
    fsf_identifier_count: int
    dual_use_snapshot_id: str
    dual_use_snapshot_content_hash: str
    dual_use_entry_count: int
    activated_at: str


class OfficialSourceRefreshService:
    """One fail-closed source refresh; persistence owns the atomic pointer change."""

    def __init__(
        self,
        fsf_source: _FsfSource,
        dual_use_source: _DualUseSource,
        repository: OfficialSourceRepository,
        *,
        fsf_parser: _FsfParser,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not hasattr(fsf_source, "retrieve"):
            raise ValueError("FSF source must implement retrieve")
        if not hasattr(dual_use_source, "retrieve_source"):
            raise ValueError("dual-use source must implement retrieve_source")
        if not hasattr(repository, "activate"):
            raise ValueError("official source repository must implement activate")
        if not hasattr(fsf_parser, "parse"):
            raise ValueError("FSF parser must implement parse")
        if clock is not None and not callable(clock):
            raise ValueError("refresh clock must be callable")
        self._fsf_source = fsf_source
        self._dual_use_source = dual_use_source
        self._repository = repository
        self._fsf_parser = fsf_parser
        self._clock = clock or (lambda: datetime.now(UTC))

    def refresh(self) -> OfficialSourceRefreshResult:
        retrieved_fsf = self._fsf_source.retrieve()
        fsf_snapshot = self._fsf_parser.parse(retrieved_fsf.content)
        if fsf_snapshot.raw_content_hash != retrieved_fsf.content_hash:
            raise RuntimeError("official FSF retrieval/parser integrity mismatch")
        retrieved_dual_use = self._dual_use_source.retrieve_source()
        if (
            retrieved_dual_use.control_list.source_archive_hash
            != retrieved_dual_use.content_hash
        ):
            raise RuntimeError("official dual-use retrieval/parser integrity mismatch")
        bundle = OfficialSourceBundle(
            fsf_raw_content=retrieved_fsf.content,
            fsf_content_type=retrieved_fsf.content_type.partition(";")[0]
            .strip()
            .lower(),
            fsf_retrieved_at=retrieved_fsf.retrieved_at,
            fsf_snapshot=fsf_snapshot,
            dual_use_raw_content=retrieved_dual_use.content,
            dual_use_content_type=retrieved_dual_use.content_type,
            dual_use_control_list=retrieved_dual_use.control_list,
            activated_at=self._clock(),
        )
        outcome = self._repository.activate(bundle)
        if not isinstance(outcome, OfficialSourceWriteOutcome):
            raise RuntimeError(
                "official source persistence returned an invalid outcome"
            )
        return OfficialSourceRefreshResult(
            outcome=outcome,
            bundle_id=bundle.bundle_id,
            bundle_content_hash=bundle.content_hash,
            fsf_snapshot_id=fsf_snapshot.snapshot_id,
            fsf_snapshot_content_hash=fsf_snapshot.content_hash,
            fsf_entity_count=len(fsf_snapshot.entities),
            fsf_alias_count=sum(len(item.aliases) for item in fsf_snapshot.entities),
            fsf_identifier_count=sum(
                len(item.identifiers) for item in fsf_snapshot.entities
            ),
            dual_use_snapshot_id=retrieved_dual_use.control_list.snapshot_id,
            dual_use_snapshot_content_hash=(
                retrieved_dual_use.control_list.content_hash
            ),
            dual_use_entry_count=len(retrieved_dual_use.control_list.entries),
            activated_at=bundle.activated_at.isoformat(),
        )


__all__ = [
    "OfficialSourceRefreshResult",
    "OfficialSourceRefreshService",
    "OfficialSourceRepository",
]
