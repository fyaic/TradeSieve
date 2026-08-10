"""Validate bounded live four-source CLI, REST, and CRM evidence."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from tradesieve.application.official_screening import (
    OfficialOfacListResult,
    OfficialScreeningResult,
)
from tradesieve.application.official_source_refresh import OfficialSourceRefreshResult
from tradesieve.domain.official_sources import OfficialSourceWriteOutcome


def _read(path: str) -> bytes:
    content = Path(path).read_bytes()
    if not content or len(content) > 4 * 1024 * 1024:
        raise RuntimeError("live evidence file has invalid size")
    return content


def _ofac_by_kind(result: OfficialScreeningResult) -> dict[str, OfficialOfacListResult]:
    values = {item.list_kind: item for item in result.ofac.lists}
    if set(values) != {"SDN", "CONSOLIDATED"}:
        raise RuntimeError("screening result does not bind both OFAC lists")
    return values


def main() -> None:
    if len(sys.argv) != 6:
        raise SystemExit("five evidence file paths are required")
    refresh = OfficialSourceRefreshResult.model_validate_json(_read(sys.argv[1]))
    replay = OfficialSourceRefreshResult.model_validate_json(_read(sys.argv[2]))
    cli = OfficialScreeningResult.model_validate_json(_read(sys.argv[3]))
    rest = OfficialScreeningResult.model_validate_json(_read(sys.argv[4]))
    crm_payload = json.loads(_read(sys.argv[5]))
    if (
        not isinstance(crm_payload, dict)
        or crm_payload.get("live_official_sources") is not True
    ):
        raise RuntimeError("CRM response is not live official-source evidence")
    crm = OfficialScreeningResult.model_validate(crm_payload.get("result"))

    if refresh.outcome is not OfficialSourceWriteOutcome.APPLIED:
        raise RuntimeError("first live refresh was not applied")
    if replay.outcome is not OfficialSourceWriteOutcome.IDEMPOTENT:
        raise RuntimeError("live refresh replay was not idempotent")
    for field in (
        "bundle_id",
        "bundle_content_hash",
        "fsf_snapshot_id",
        "fsf_snapshot_content_hash",
        "dual_use_snapshot_id",
        "dual_use_snapshot_content_hash",
        "ofac_sdn_snapshot_id",
        "ofac_sdn_snapshot_content_hash",
        "ofac_consolidated_snapshot_id",
        "ofac_consolidated_snapshot_content_hash",
    ):
        if getattr(refresh, field) != getattr(replay, field):
            raise RuntimeError("live refresh replay changed source identity")
    if not (
        refresh.fsf_entity_count >= 1_000
        and refresh.dual_use_entry_count >= 300
        and refresh.ofac_sdn_entry_count >= 10_000
        and refresh.ofac_consolidated_entry_count >= 100
    ):
        raise RuntimeError("live source counts are below conservative safety floors")

    if cli.model_dump() != rest.model_dump():
        raise RuntimeError("CLI and REST did not return the same application result")
    if cli.source_bundle_id != refresh.bundle_id or (
        cli.source_bundle_content_hash != refresh.bundle_content_hash
    ):
        raise RuntimeError("screening did not bind the refreshed bundle")
    if cli.signal.value != "RED" or cli.business_action.value != "HOLD":
        raise RuntimeError("known live candidates did not remain held")
    if cli.automatic_clearance is not False:
        raise RuntimeError("live screening attempted automatic clearance")

    cli_ofac = _ofac_by_kind(cli)
    crm_ofac = _ofac_by_kind(crm)
    sdn = cli_ofac["SDN"]
    consolidated = cli_ofac["CONSOLIDATED"]
    if sdn.source_snapshot_id != refresh.ofac_sdn_snapshot_id or (
        consolidated.source_snapshot_id != refresh.ofac_consolidated_snapshot_id
    ):
        raise RuntimeError("screening OFAC identity does not match refresh")
    if not sdn.name_evidence or not any(
        "RUSSIA-EO14024" in item.programs for item in sdn.name_evidence
    ):
        raise RuntimeError("known current OFAC Russia candidate was not evidenced")
    if not any(status in {"CANDIDATE", "AMBIGUOUS"} for status in sdn.name_statuses):
        raise RuntimeError("known current OFAC name did not produce a candidate")
    if crm.source_bundle_id != refresh.bundle_id or {
        kind: item.source_snapshot_id for kind, item in crm_ofac.items()
    } != {kind: item.source_snapshot_id for kind, item in cli_ofac.items()}:
        raise RuntimeError("CRM did not use the same active four-source bundle")

    print(
        json.dumps(
            {
                "cli_rest_same_result": True,
                "crm_same_bundle": True,
                "dual_use_entries": refresh.dual_use_entry_count,
                "fsf_entities": refresh.fsf_entity_count,
                "idempotent_replay": True,
                "ofac_consolidated_entries": (refresh.ofac_consolidated_entry_count),
                "ofac_russia_candidate_held": True,
                "ofac_sdn_entries": refresh.ofac_sdn_entry_count,
                "status": "PASS",
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
