#!/usr/bin/env python3
"""Bounded live evidence probe for OFAC SLS comprehensive XML sources."""

from __future__ import annotations

import json
import sys
from typing import Any

from tradesieve.adapters.ofac_sls import (
    HttpsOfacSlsTransport,
    OfacSlsOfficialSourceConnector,
    OfacSlsSourceError,
    OfacSlsXmlParser,
)
from tradesieve.domain.ofac_sls import OfacSlsListKind, diff_ofac_snapshots


def _snapshot_evidence(
    connector: OfacSlsOfficialSourceConnector,
    parser: OfacSlsXmlParser,
    list_kind: OfacSlsListKind,
) -> dict[str, Any]:
    retrieved = connector.retrieve(list_kind)
    snapshot = parser.parse(retrieved)
    replay = parser.parse(retrieved)
    delta = diff_ofac_snapshots(snapshot, replay)
    if snapshot.snapshot_id != replay.snapshot_id or any(
        (delta.added_uids, delta.removed_uids, delta.modified_uids)
    ):
        raise RuntimeError("OFAC replay invariant failed")
    return {
        "alias_count": sum(len(entry.aliases) for entry in snapshot.entries),
        "content_hash": snapshot.raw_content_hash,
        "entry_count": len(snapshot.entries),
        "identifier_count": sum(len(entry.identifiers) for entry in snapshot.entries),
        "list_kind": snapshot.list_kind.value,
        "publish_date": snapshot.publish_date.isoformat(),
        "raw_byte_length": snapshot.raw_byte_length,
        "replay_stable": True,
        "server_digest_verified": retrieved.server_digest is not None,
        "snapshot_id": snapshot.snapshot_id,
        "source_last_modified": (
            retrieved.source_last_modified.isoformat()
            if retrieved.source_last_modified is not None
            else None
        ),
    }


def run_probe() -> dict[str, object]:
    connector = OfacSlsOfficialSourceConnector(HttpsOfacSlsTransport())
    parser = OfacSlsXmlParser()
    sources = [
        _snapshot_evidence(connector, parser, list_kind)
        for list_kind in (OfacSlsListKind.SDN, OfacSlsListKind.CONSOLIDATED)
    ]
    return {
        "history_boundary": "locally_preserved_snapshots_only",
        "source": "OFAC_SANCTIONS_LIST_SERVICE",
        "sources": sources,
        "status": "PASS",
    }


def main() -> int:
    try:
        evidence = run_probe()
    except OfacSlsSourceError as error:
        print(f"OFAC_SLS_LIVE_PROBE_FAILED:{error.code.value}", file=sys.stderr)
        return 2
    except Exception:
        print("OFAC_SLS_LIVE_PROBE_FAILED:INTERNAL_INVARIANT", file=sys.stderr)
        return 2
    print(json.dumps(evidence, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
