"""Runtime management command tests."""

from __future__ import annotations

import io
import json
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import psycopg
import pytest
from alembic.config import Config

from tradesieve import manage
from tradesieve.application.auth import Operation
from tradesieve.application.official_screening import OfficialScreeningRequest
from tradesieve.application.source_snapshot_contracts import SourceSnapshotListing
from tradesieve.config import Settings
from tradesieve.demo_source_snapshot import DemoSourceActor
from tradesieve.runtime import RuntimeStatus


def test_sqlalchemy_database_url_selects_psycopg_driver() -> None:
    assert manage.sqlalchemy_database_url(  # pragma: allowlist secret
        "postgresql://user:pass@db/name"
    ) == ("postgresql+psycopg://user:pass@db/name")
    explicit = "postgresql+psycopg://user:pass@db/name"  # pragma: allowlist secret
    assert manage.sqlalchemy_database_url(explicit) == explicit
    assert (
        manage.alembic_database_url(  # pragma: allowlist secret
            "postgresql://user:p%40ss@db/name"
        )
        == "postgresql+psycopg://user:p%%40ss@db/name"
    )


def test_migrate_configures_real_revision_path_driver_and_encoded_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_upgrade(config: Config, target: str) -> None:
        captured["url"] = config.get_main_option("sqlalchemy.url")
        captured["script_location"] = config.get_main_option("script_location")
        captured["target"] = target

    monkeypatch.setattr("tradesieve.manage.command.upgrade", fake_upgrade)
    settings = Settings(
        database_url="postgresql://tradesieve:p%40ss@postgres:5432/tradesieve"  # pragma: allowlist secret
    )
    manage.migrate(settings)
    assert captured["url"] == (
        "postgresql+psycopg://tradesieve:p%40ss@postgres:5432/tradesieve"
    )
    assert str(captured["script_location"]).endswith("/migrations")
    assert captured["target"] == "head"


def test_migrate_uses_explicit_root_in_installed_package_layout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    deployment_root = tmp_path / "deployment"
    migrations = deployment_root / "migrations"
    migrations.mkdir(parents=True)
    (deployment_root / "alembic.ini").write_text("[alembic]\n", encoding="utf-8")
    (migrations / "env.py").write_text("", encoding="utf-8")
    fake_installed_module = (
        tmp_path / ".venv/lib/python3.13/site-packages/tradesieve/manage.py"
    )
    captured: dict[str, str] = {}

    def fake_upgrade(config: Config, target: str) -> None:
        captured["config"] = config.config_file_name or ""
        captured["scripts"] = config.get_main_option("script_location") or ""
        captured["target"] = target

    monkeypatch.setattr(manage, "__file__", str(fake_installed_module))
    monkeypatch.setattr("tradesieve.manage.command.upgrade", fake_upgrade)
    manage.migrate(Settings(migration_root=deployment_root))
    assert captured == {
        "config": str(deployment_root / "alembic.ini"),
        "scripts": str(migrations),
        "target": "head",
    }


@pytest.mark.parametrize("missing_path", ["alembic.ini", "migrations/env.py"])
def test_migration_config_refuses_missing_deployment_resources(
    tmp_path: Path, missing_path: str
) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    if missing_path != "alembic.ini":
        (tmp_path / "alembic.ini").write_text("[alembic]\n", encoding="utf-8")
    if missing_path != "migrations/env.py":
        (migrations / "env.py").write_text("", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="missing Alembic"):
        manage.migration_config(Settings(migration_root=tmp_path))


@pytest.mark.parametrize("ready", [False, True])
def test_inspect_runtime_returns_status_and_details(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    ready: bool,
) -> None:
    status = RuntimeStatus(ready, {"database": "OK" if ready else "UNAVAILABLE"})
    monkeypatch.setattr(manage, "check_readiness", lambda settings: status)
    assert manage.inspect_runtime(Settings()) == (0 if ready else 1)
    payload = json.loads(capsys.readouterr().out)
    assert payload["ready"] is ready
    assert payload["expected_migration"] == "20260810_0007"
    assert payload["expected_source_coverage"] == "synthetic-demo-sources-v1"
    assert payload["expected_rule_coverage"] == "synthetic-demo-rules-v1"


@pytest.mark.parametrize("command_name", ["migrate", "bootstrap-demo"])
def test_main_routes_mutating_commands(
    monkeypatch: pytest.MonkeyPatch, command_name: str
) -> None:
    called: list[str] = []
    monkeypatch.setattr(sys, "argv", ["tradesieve.manage", command_name])
    monkeypatch.setattr(manage, "get_settings", Settings)
    monkeypatch.setattr(manage, "migrate", lambda settings: called.append("migrate"))
    monkeypatch.setattr(
        manage, "bootstrap_demo", lambda settings: called.append("bootstrap-demo")
    )
    manage.main()
    assert called == [command_name]


def test_main_propagates_inspect_exit_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["tradesieve.manage", "inspect"])
    monkeypatch.setattr(manage, "get_settings", Settings)
    monkeypatch.setattr(manage, "inspect_runtime", lambda settings: 1)
    with pytest.raises(SystemExit) as exc_info:
        manage.main()
    assert exc_info.value.code == 1


@pytest.mark.parametrize(("ready", "expected_exit"), [(True, 0), (False, 2)])
def test_list_sources_prints_one_safe_envelope_and_distinct_exit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    ready: bool,
    expected_exit: int,
) -> None:
    class FakeListing:
        def __init__(self, listing_ready: bool) -> None:
            self.ready = listing_ready

        def model_dump(self, *, mode: str) -> dict[str, object]:
            assert mode == "json"
            return {
                "deployment_id": "demo",
                "source_set_id": "synthetic-demo-sources-v1",
                "ready": self.ready,
                "status": "CURRENT" if self.ready else "UNAVAILABLE",
                "issues": [],
                "sources": [],
            }

    class FakeService:
        def __init__(self, repository: object) -> None:
            assert repository is not None

        def query_source_set(
            self, deployment_id: str, source_set_id: str
        ) -> FakeListing:
            assert (deployment_id, source_set_id) == (
                "demo",
                "synthetic-demo-sources-v1",
            )
            return FakeListing(ready)

    monkeypatch.setattr(manage, "connect", lambda settings: nullcontext(object()))
    monkeypatch.setattr(manage, "SourceRegistryService", FakeService)
    assert manage.list_sources(Settings()) == expected_exit
    payload = json.loads(capsys.readouterr().out)
    assert payload["ready"] is ready
    assert isinstance(payload["sources"], list)


def test_main_propagates_list_sources_exit_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["tradesieve.manage", "list-sources"])
    monkeypatch.setattr(manage, "get_settings", Settings)
    monkeypatch.setattr(manage, "list_sources", lambda settings: 2)
    with pytest.raises(SystemExit) as exc_info:
        manage.main()
    assert exc_info.value.code == 2


def test_list_rules_prints_safe_active_projection(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeView:
        def model_dump(self, *, mode: str) -> dict[str, object]:
            assert mode == "json"
            return {
                "summary": {
                    "tenant_id": "demo-tenant",
                    "deployment_id": "demo",
                    "rule_set_id": "synthetic-demo-rules-v1",
                    "bundle_id": "synthetic-demo-bundle-v1",
                    "state": "ACTIVE",
                    "active": True,
                },
                "rules": [
                    {
                        "rule_id": "synthetic-rule",
                        "citations": [
                            {
                                "citation_ref": "synthetic-citation",
                                "policy_content_hash": "sha256:" + "a" * 64,
                            }
                        ],
                    }
                ],
            }

    class FakeService:
        def current_active(self, authorized: object) -> FakeView:
            assert authorized == "authorized-read"
            return FakeView()

    monkeypatch.setattr(manage, "connect", lambda settings: nullcontext(object()))
    monkeypatch.setattr(
        manage,
        "build_demo_rule_services",
        lambda settings, connection: ("bundle", FakeService(), "authorization"),
    )
    monkeypatch.setattr(
        manage,
        "authorize_demo_rule_request",
        lambda *args, **kwargs: "authorized-read",
    )
    assert manage.list_rules(Settings()) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"active_bundle", "status"}
    assert payload["status"] == "ACTIVE"
    assert payload["active_bundle"]["summary"]["state"] == "ACTIVE"
    serialized = json.dumps(payload)
    assert "private_policy_text" not in serialized
    assert "internal_notes" not in serialized


@pytest.mark.parametrize(
    ("active", "expected_status"),
    [(None, "NO_ACTIVE_BUNDLE"), ("unavailable", "UNAVAILABLE")],
)
def test_list_rules_returns_safe_unavailable_exit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    active: object,
    expected_status: str,
) -> None:
    class FakeService:
        def current_active(self, authorized: object) -> None:
            assert authorized == "authorized-read"
            if active == "unavailable":
                raise psycopg.OperationalError("private database detail")
            return None

    monkeypatch.setattr(manage, "connect", lambda settings: nullcontext(object()))
    monkeypatch.setattr(
        manage,
        "build_demo_rule_services",
        lambda settings, connection: ("bundle", FakeService(), "authorization"),
    )
    monkeypatch.setattr(
        manage,
        "authorize_demo_rule_request",
        lambda *args, **kwargs: "authorized-read",
    )
    assert manage.list_rules(Settings()) == 2
    assert json.loads(capsys.readouterr().out) == {
        "active_bundle": None,
        "status": expected_status,
    }


def test_list_rules_is_hard_disabled_before_identity_in_production(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = Settings(
        mode="production",
        database_url="postgresql://service:strong-password@db/tradesieve",  # pragma: allowlist secret
        demo_bootstrap_enabled=False,
        rule_bundle_tenant_id="tenant-1",
        deployment_id="production-1",
        required_source_set="approved-sources-v1",
        required_rule_set="approved-rules-v1",
        official_api_token_sha256="sha256:" + "a" * 64,
    )
    monkeypatch.setattr(
        manage,
        "build_demo_rule_services",
        lambda settings, connection: pytest.fail("identity path must not run"),
    )
    assert manage.list_rules(settings) == 3
    assert json.loads(capsys.readouterr().out) == {
        "active_bundle": None,
        "status": "DISABLED",
    }


def test_main_propagates_list_rules_exit_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["tradesieve.manage", "list-rules"])
    monkeypatch.setattr(manage, "get_settings", Settings)
    monkeypatch.setattr(manage, "list_rules", lambda settings: 3)
    with pytest.raises(SystemExit) as exc_info:
        manage.main()
    assert exc_info.value.code == 3


def test_list_source_snapshots_is_disabled_before_connection_in_production(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = Settings(
        mode="production",
        database_url="postgresql://service:strong-password@db/tradesieve",  # pragma: allowlist secret
        demo_bootstrap_enabled=False,
        rule_bundle_tenant_id="tenant-1",
        deployment_id="production-1",
        required_source_set="approved-sources-v1",
        required_rule_set="approved-rules-v1",
        official_api_token_sha256="sha256:" + "a" * 64,
    )
    monkeypatch.setattr(
        manage,
        "connect",
        lambda settings: pytest.fail("production must not connect"),
    )
    monkeypatch.setattr(
        manage,
        "authorize_demo_source_request",
        lambda *args, **kwargs: pytest.fail("production must not resolve identity"),
    )
    assert manage.list_source_snapshots(settings) == 3
    assert capsys.readouterr().out == '{"snapshots":[]}\n'


def test_list_source_snapshots_proves_bytes_then_authorizes_one_real_query(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []
    connection = object()

    class Ready:
        def is_ready(self) -> bool:
            calls.append("readiness")
            return True

    class Query:
        def list_snapshots(self, authorized: object, *, source_id: str) -> object:
            calls.append("query")
            assert authorized == "authorized-source-read"
            assert source_id == "synthetic-source-v1"
            return SimpleNamespace(
                snapshots=[object()],
                model_dump_json=lambda: '{"snapshots":[{"snapshot_id":"snapshot-a"}]}',
            )

    monkeypatch.setattr(manage, "connect", lambda settings: nullcontext(connection))

    def build_readiness(settings: Settings, active: object) -> Ready:
        del settings
        assert active is connection
        calls.append("build-readiness")
        return Ready()

    def build_query(settings: Settings, active: object) -> object:
        del settings
        assert active is connection
        calls.append("build-query")
        return SimpleNamespace(authorization="authorization", query=Query())

    monkeypatch.setattr(
        manage,
        "build_source_snapshot_readiness_service",
        build_readiness,
    )
    monkeypatch.setattr(
        manage,
        "build_demo_source_services",
        build_query,
    )

    def authorize(*args: object, **kwargs: object) -> str:
        calls.append("authorize")
        assert args[1] == "authorization"
        assert kwargs["actor"] is DemoSourceActor.READER
        assert kwargs["operation"] is Operation.SOURCE_READ
        return "authorized-source-read"

    monkeypatch.setattr(manage, "authorize_demo_source_request", authorize)
    assert manage.list_source_snapshots(Settings()) == 0
    assert capsys.readouterr().out == ('{"snapshots":[{"snapshot_id":"snapshot-a"}]}\n')
    assert calls == [
        "build-readiness",
        "readiness",
        "build-query",
        "authorize",
        "query",
    ]


def test_list_source_snapshots_unready_never_authorizes_or_queries(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class Unready:
        def is_ready(self) -> bool:
            return False

    monkeypatch.setattr(manage, "connect", lambda settings: nullcontext(object()))
    monkeypatch.setattr(
        manage,
        "build_source_snapshot_readiness_service",
        lambda settings, connection: Unready(),
    )
    monkeypatch.setattr(
        manage,
        "build_demo_source_services",
        lambda settings, connection: pytest.fail("query graph must not be built"),
    )
    assert manage.list_source_snapshots(Settings()) == 2
    assert capsys.readouterr().out == '{"snapshots":[]}\n'


@pytest.mark.parametrize("failure_stage", ["connect", "exit", "query", "serialize"])
def test_list_source_snapshots_failures_have_one_exact_empty_envelope(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure_stage: str,
) -> None:
    class Ready:
        def is_ready(self) -> bool:
            return True

    class Query:
        def list_snapshots(self, authorized: object, *, source_id: str) -> object:
            del authorized, source_id
            if failure_stage == "query":
                raise RuntimeError("private database, auth, audit, or storage detail")
            if failure_stage == "serialize":
                return SimpleNamespace(
                    snapshots=[object()],
                    model_dump_json=lambda: (_ for _ in ()).throw(
                        ValueError("private corrupt DTO detail")
                    ),
                )
            return SourceSnapshotListing(snapshots=[])

    if failure_stage == "connect":
        monkeypatch.setattr(
            manage,
            "connect",
            lambda settings: (_ for _ in ()).throw(
                psycopg.OperationalError("private database detail")
            ),
        )
    elif failure_stage == "exit":

        class ExitFailure:
            def __enter__(self) -> object:
                return object()

            def __exit__(self, *args: object) -> None:
                del args
                raise psycopg.OperationalError("private cleanup detail")

        monkeypatch.setattr(manage, "connect", lambda settings: ExitFailure())
    else:
        monkeypatch.setattr(manage, "connect", lambda settings: nullcontext(object()))
    monkeypatch.setattr(
        manage,
        "build_source_snapshot_readiness_service",
        lambda settings, connection: Ready(),
    )
    monkeypatch.setattr(
        manage,
        "build_demo_source_services",
        lambda settings, connection: SimpleNamespace(
            authorization=object(), query=Query()
        ),
    )
    monkeypatch.setattr(
        manage,
        "authorize_demo_source_request",
        lambda *args, **kwargs: object(),
    )
    assert manage.list_source_snapshots(Settings()) == 2
    assert capsys.readouterr().out == '{"snapshots":[]}\n'


def test_list_source_snapshots_empty_listing_is_exit_two(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class Ready:
        def is_ready(self) -> bool:
            return True

    query = SimpleNamespace(
        list_snapshots=lambda authorized, source_id: SourceSnapshotListing(snapshots=[])
    )
    monkeypatch.setattr(manage, "connect", lambda settings: nullcontext(object()))
    monkeypatch.setattr(
        manage,
        "build_source_snapshot_readiness_service",
        lambda settings, connection: Ready(),
    )
    monkeypatch.setattr(
        manage,
        "build_demo_source_services",
        lambda settings, connection: SimpleNamespace(
            authorization=object(), query=query
        ),
    )
    monkeypatch.setattr(
        manage,
        "authorize_demo_source_request",
        lambda *args, **kwargs: object(),
    )
    assert manage.list_source_snapshots(Settings()) == 2
    assert capsys.readouterr().out == '{"snapshots":[]}\n'


def test_main_propagates_list_source_snapshots_exit_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["tradesieve.manage", "list-source-snapshots"])
    monkeypatch.setattr(manage, "get_settings", Settings)
    monkeypatch.setattr(manage, "list_source_snapshots", lambda settings: 2)
    with pytest.raises(SystemExit) as exc_info:
        manage.main()
    assert exc_info.value.code == 2


def submission_result(disposition: str) -> object:
    from datetime import UTC, datetime

    from tradesieve.application.screening_submission import (
        ScreeningSubmissionServiceDisposition,
        ScreeningSubmissionServiceResult,
    )
    from tradesieve.domain.screening_submission import (
        ScreeningAcceptanceReceipt,
        ScreeningReceiptType,
    )

    return ScreeningSubmissionServiceResult(
        disposition=ScreeningSubmissionServiceDisposition(disposition),
        receipt=ScreeningAcceptanceReceipt(
            receipt_type=ScreeningReceiptType.SCREENING_ACCEPTED,
            schema_version="1.0.0",
            accepted_at=datetime(2026, 8, 7, 4, 5, tzinfo=UTC),
            intake_id="intake-safe",
            screening_id="screening-safe",
            outbox_event_id="outbox-safe",
        ),
    )


def demo_fixture(value: str) -> Any:
    from tradesieve.demo_screening_submission import DemoScreeningFixture

    return DemoScreeningFixture(value)


def service_error(code: str) -> Exception:
    from tradesieve.application.screening_submission import (
        ScreeningSubmissionServiceError,
        ScreeningSubmissionServiceErrorCode,
    )

    return ScreeningSubmissionServiceError(ScreeningSubmissionServiceErrorCode(code))


@pytest.mark.parametrize(
    "disposition",
    [
        "APPLIED",
        "REPLAY",
    ],
)
def test_submit_demo_screening_prints_only_the_safe_receipt(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    disposition: str,
) -> None:
    calls: list[tuple[object, object]] = []

    def submit(settings: Settings, key: object, fixture: object) -> object:
        assert settings.mode == "demo"
        calls.append((key, fixture))
        return submission_result(disposition)

    monkeypatch.setattr(manage, "submit_demo_screening", submit)
    assert (
        manage.submit_demo_screening_command(
            Settings(),
            idempotency_key="private-synthetic-key",
            fixture=demo_fixture("baseline"),
        )
        == 0
    )
    assert calls == [("private-synthetic-key", demo_fixture("baseline"))]
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "disposition": disposition,
        "receipt": {
            "accepted_at": "2026-08-07T04:05:00Z",
            "intake_id": "intake-safe",
            "outbox_event_id": "outbox-safe",
            "receipt_type": "SCREENING_ACCEPTED",
            "schema_version": "1.0.0",
            "screening_id": "screening-safe",
        },
        "status": "ACCEPTED",
    }
    serialized = json.dumps(payload)
    for private in (
        "private-synthetic-key",
        "key_digest",
        "canonical_hash",
        "byte_hash",
        "authorization_event_id",
        "actor_subject",
        "correlation_id",
    ):
        assert private not in serialized


def test_submit_demo_screening_maps_conflict_without_a_receipt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def conflict(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise service_error("IDEMPOTENCY_CONFLICT")

    monkeypatch.setattr(manage, "submit_demo_screening", conflict)
    assert (
        manage.submit_demo_screening_command(
            Settings(),
            idempotency_key="private-conflict-key",
            fixture=demo_fixture("changed"),
        )
        == 4
    )
    assert json.loads(capsys.readouterr().out) == {"status": "IDEMPOTENCY_CONFLICT"}


def test_submit_demo_screening_is_disabled_before_runtime_in_production(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        manage,
        "submit_demo_screening",
        lambda *args, **kwargs: pytest.fail("disabled command reached runtime"),
    )
    settings = Settings(
        mode="production",
        database_url="postgresql://service:strong-password@db/tradesieve",  # pragma: allowlist secret
        demo_bootstrap_enabled=False,
        rule_bundle_tenant_id="tenant-1",
        deployment_id="production-1",
        required_source_set="approved-sources-v1",
        required_rule_set="approved-rules-v1",
        official_api_token_sha256="sha256:" + "a" * 64,
    )
    assert (
        manage.submit_demo_screening_command(
            settings,
            idempotency_key="private-key",
            fixture=demo_fixture("baseline"),
        )
        == 3
    )
    assert json.loads(capsys.readouterr().out) == {"status": "DISABLED"}


@pytest.mark.parametrize(
    "failure",
    [
        service_error("UNAVAILABLE"),
        psycopg.OperationalError("private-database-sentinel"),
        RuntimeError("private-runtime-sentinel"),
    ],
)
def test_submit_demo_screening_failures_are_fixed_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: Exception,
) -> None:
    monkeypatch.setattr(
        manage,
        "submit_demo_screening",
        lambda *args, **kwargs: (_ for _ in ()).throw(failure),
    )
    assert (
        manage.submit_demo_screening_command(
            Settings(),
            idempotency_key="private-key-sentinel",
            fixture=demo_fixture("baseline"),
        )
        == 2
    )
    output = capsys.readouterr().out
    assert json.loads(output) == {"status": "UNAVAILABLE"}
    assert "sentinel" not in output


def test_main_routes_demo_submission_options_and_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[tuple[str, Any]] = []

    def submit(
        settings: Settings,
        idempotency_key: str,
        fixture: Any,
    ) -> int:
        assert settings.mode == "demo"
        called.append((idempotency_key, fixture))
        return 4

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tradesieve-manage",
            "submit-demo-screening",
            "--idempotency-key",
            "synthetic-key",
            "--fixture",
            "changed",
        ],
    )
    monkeypatch.setattr(manage, "get_settings", Settings)
    monkeypatch.setattr(
        manage,
        "submit_demo_screening_command",
        submit,
    )
    with pytest.raises(SystemExit) as exc_info:
        manage.main()
    assert exc_info.value.code == 4
    assert called == [("synthetic-key", demo_fixture("changed"))]


def official_request_bytes() -> bytes:
    return json.dumps(
        {
            "schema_version": "1.0.0",
            "party_identifiers": [
                {"type": "regnumber", "value": "private-value", "country": "US"}
            ],
            "goods": {
                "annex_i_code": "3A001",
                "classification_verified": True,
                "technical_specification_available": True,
            },
        }
    ).encode()


def test_official_request_reader_accepts_file_and_bounded_stdin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    request_file = tmp_path / "request.json"
    request_file.write_bytes(official_request_bytes())
    from_file = manage._read_official_screening_request(str(request_file))
    assert isinstance(from_file, OfficialScreeningRequest)
    assert from_file.goods.annex_i_code == "3A001"

    monkeypatch.setattr(
        sys, "stdin", SimpleNamespace(buffer=io.BytesIO(official_request_bytes()))
    )
    from_stdin = manage._read_official_screening_request("-")
    assert from_stdin == from_file


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"\xff",
        b"not-json",
        b'{"schema_version":"1.0.0","schema_version":"1.0.0"}',
        b"{}",
        b"x" * (1024 * 1024 + 1),
    ],
)
def test_official_request_reader_rejects_invalid_bounded_stdin(
    monkeypatch: pytest.MonkeyPatch, content: bytes
) -> None:
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(content)))
    with pytest.raises(ValueError, match="request input"):
        manage._read_official_screening_request("-")


def test_official_request_reader_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unavailable"):
        manage._read_official_screening_request(str(tmp_path / "missing.json"))


def test_screen_official_command_prints_canonical_result(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request = OfficialScreeningRequest.model_validate_json(official_request_bytes())
    calls: list[object] = []

    class FakeResult:
        def model_dump_json(self) -> str:
            return '{"automatic_clearance":false,"business_action":"HOLD"}'

    class FakeService:
        def __init__(self, *args: object, **kwargs: object) -> None:
            assert len(args) == 3
            assert kwargs["fsf_parser"] is not None
            assert kwargs["ofac_parser"] is not None

        def screen(self, value: object) -> FakeResult:
            calls.append(value)
            return FakeResult()

    monkeypatch.setattr(
        manage, "_read_official_screening_request", lambda path: request
    )
    monkeypatch.setattr(manage, "OfficialScreeningService", FakeService)
    assert manage.screen_official_command(request_location="request.json") == 0
    assert calls == [request]
    assert json.loads(capsys.readouterr().out) == {
        "automatic_clearance": False,
        "business_action": "HOLD",
    }


def test_screen_official_command_redacts_input_and_source_failures(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        manage,
        "_read_official_screening_request",
        lambda path: (_ for _ in ()).throw(ValueError("private-input")),
    )
    assert manage.screen_official_command(request_location="private-path") == 3
    assert json.loads(capsys.readouterr().out) == {"status": "INVALID_REQUEST"}

    request = OfficialScreeningRequest.model_validate_json(official_request_bytes())
    monkeypatch.setattr(
        manage, "_read_official_screening_request", lambda path: request
    )

    class BrokenService:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def screen(self, value: object) -> object:
            raise RuntimeError("private-source")

    monkeypatch.setattr(manage, "OfficialScreeningService", BrokenService)
    assert manage.screen_official_command(request_location="private-path") == 2
    output = capsys.readouterr().out
    assert json.loads(output) == {"status": "OFFICIAL_SOURCE_UNAVAILABLE"}
    assert "private" not in output


def test_main_routes_official_screening_request_and_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        sys,
        "argv",
        ["tradesieve-manage", "screen-official", "--request", "-"],
    )
    monkeypatch.setattr(manage, "get_settings", Settings)

    def route(*, request_location: str) -> int:
        calls.append(request_location)
        return 2

    monkeypatch.setattr(manage, "screen_official_command", route)
    with pytest.raises(SystemExit) as caught:
        manage.main()
    assert caught.value.code == 2
    assert calls == ["-"]


def test_refresh_official_sources_command_prints_safe_receipt(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[object] = []

    class Result:
        def model_dump_json(self) -> str:
            return '{"bundle_id":"official-bundle-safe","outcome":"APPLIED"}'

    class Service:
        def __init__(self, *args: object, **kwargs: object) -> None:
            assert len(args) == 4
            assert kwargs["fsf_parser"] is not None
            assert kwargs["ofac_parser"] is not None

        def refresh(self) -> Result:
            calls.append("refresh")
            return Result()

    monkeypatch.setattr(manage, "connect", lambda settings: nullcontext(object()))
    monkeypatch.setattr(
        manage, "PostgresOfficialSourceRepository", lambda connection: "repository"
    )
    monkeypatch.setattr(manage, "OfficialSourceRefreshService", Service)

    assert manage.refresh_official_sources_command(Settings()) == 0
    assert calls == ["refresh"]
    assert json.loads(capsys.readouterr().out) == {
        "bundle_id": "official-bundle-safe",
        "outcome": "APPLIED",
    }


def test_refresh_official_sources_command_redacts_failures(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class Service:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def refresh(self) -> object:
            raise RuntimeError("private-source-detail")

    monkeypatch.setattr(manage, "connect", lambda settings: nullcontext(object()))
    monkeypatch.setattr(
        manage, "PostgresOfficialSourceRepository", lambda connection: "repository"
    )
    monkeypatch.setattr(manage, "OfficialSourceRefreshService", Service)
    assert manage.refresh_official_sources_command(Settings()) == 2
    output = capsys.readouterr().out
    assert json.loads(output) == {"status": "OFFICIAL_SOURCE_REFRESH_FAILED"}
    assert "private" not in output


def test_screen_active_command_uses_persisted_service_and_redacts_failures(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request = OfficialScreeningRequest.model_validate_json(official_request_bytes())
    calls: list[object] = []

    class Result:
        def model_dump_json(self) -> str:
            return '{"business_action":"HOLD","source_bundle_id":"safe"}'

    class Service:
        def __init__(self, repository: object) -> None:
            assert repository == "repository"

        def screen(self, value: object) -> Result:
            calls.append(value)
            return Result()

    monkeypatch.setattr(
        manage, "_read_official_screening_request", lambda path: request
    )
    monkeypatch.setattr(manage, "connect", lambda settings: nullcontext(object()))
    monkeypatch.setattr(
        manage, "PostgresOfficialSourceRepository", lambda connection: "repository"
    )
    monkeypatch.setattr(manage, "PersistedOfficialScreeningService", Service)
    assert (
        manage.screen_active_command(Settings(), request_location="request.json") == 0
    )
    assert calls == [request]
    assert json.loads(capsys.readouterr().out) == {
        "business_action": "HOLD",
        "source_bundle_id": "safe",
    }

    monkeypatch.setattr(
        manage,
        "_read_official_screening_request",
        lambda path: (_ for _ in ()).throw(ValueError("private-input")),
    )
    assert manage.screen_active_command(Settings(), request_location="private") == 3
    assert json.loads(capsys.readouterr().out) == {"status": "INVALID_REQUEST"}

    monkeypatch.setattr(
        manage, "_read_official_screening_request", lambda path: request
    )

    class BrokenService(Service):
        def screen(self, value: object) -> Result:
            raise RuntimeError("private-database")

    monkeypatch.setattr(manage, "PersistedOfficialScreeningService", BrokenService)
    assert manage.screen_active_command(Settings(), request_location="private") == 2
    output = capsys.readouterr().out
    assert json.loads(output) == {"status": "ACTIVE_OFFICIAL_SOURCE_UNAVAILABLE"}
    assert "private" not in output


@pytest.mark.parametrize(
    ("command", "expected"),
    [("refresh-official-sources", "refresh"), ("screen-active", "active")],
)
def test_main_routes_persisted_official_commands(
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    expected: str,
) -> None:
    calls: list[tuple[str, str | None]] = []
    argv = ["tradesieve-manage", command]
    if command == "screen-active":
        argv.extend(["--request", "-"])
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(manage, "get_settings", Settings)

    def refresh(_settings: Settings) -> int:
        calls.append(("refresh", None))
        return 2

    def active(_settings: Settings, *, request_location: str) -> int:
        calls.append(("active", request_location))
        return 2

    monkeypatch.setattr(
        manage,
        "refresh_official_sources_command",
        refresh,
    )
    monkeypatch.setattr(
        manage,
        "screen_active_command",
        active,
    )
    with pytest.raises(SystemExit) as caught:
        manage.main()
    assert caught.value.code == 2
    assert calls == [(expected, "-" if expected == "active" else None)]
