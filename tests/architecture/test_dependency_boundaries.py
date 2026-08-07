"""Enforce the inward dependency direction from ADR-0004."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).parents[2] / "src" / "tradesieve"
FORBIDDEN_IMPORTS = {
    "domain": (
        "tradesieve.application",
        "tradesieve.adapters",
        "fastapi",
        "mcp",
        "sqlalchemy",
    ),
    "application": (
        "tradesieve.adapters",
        "fastapi",
        "mcp",
        "sqlalchemy",
    ),
}


def imported_modules(path: Path, package_root: Path = PACKAGE_ROOT) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package_parts = (
        package_root.name,
        *path.relative_to(package_root).parent.parts,
    )
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                modules.add(node.module)
                modules.update(f"{node.module}.{alias.name}" for alias in node.names)
                continue

            parent_levels = max(node.level - 1, 0)
            base_parts = package_parts[: len(package_parts) - parent_levels]
            if node.module:
                resolved_module = ".".join((*base_parts, *node.module.split(".")))
                modules.add(resolved_module)
                modules.update(
                    f"{resolved_module}.{alias.name}" for alias in node.names
                )
            else:
                modules.update(
                    ".".join((*base_parts, alias.name)) for alias in node.names
                )
    return modules


def layer_violations(
    package_root: Path, layer: str, forbidden_imports: tuple[str, ...]
) -> list[str]:
    layer_root = package_root / layer
    source_files = sorted(layer_root.rglob("*.py"))
    assert source_files, f"expected Python sources in {layer_root}"

    violations: list[str] = []
    for source_file in source_files:
        for imported in imported_modules(source_file, package_root):
            if any(
                imported == forbidden or imported.startswith(f"{forbidden}.")
                for forbidden in forbidden_imports
            ):
                violations.append(
                    f"{source_file.relative_to(package_root)} imports {imported}"
                )
    return violations


@pytest.mark.parametrize("layer", sorted(FORBIDDEN_IMPORTS))
def test_layers_do_not_import_outward_dependencies(layer: str) -> None:
    violations = layer_violations(PACKAGE_ROOT, layer, FORBIDDEN_IMPORTS[layer])
    assert not violations, "outward dependency imports found:\n" + "\n".join(violations)


def test_source_snapshot_contracts_are_a_one_way_schema_boundary() -> None:
    contracts_path = PACKAGE_ROOT / "application" / "source_snapshot_contracts.py"
    query_path = PACKAGE_ROOT / "application" / "source_snapshot_query.py"
    contract_imports = imported_modules(contracts_path)
    forbidden = (
        "tradesieve.adapters",
        "tradesieve.application.source_snapshot",
        "tradesieve.application.source_snapshot_query",
        "tradesieve.manage",
        "tradesieve.runtime",
    )
    violations = sorted(
        imported
        for imported in contract_imports
        if any(
            imported == prefix or imported.startswith(f"{prefix}.")
            for prefix in forbidden
        )
    )

    assert not violations
    assert "tradesieve.application.source_snapshot_contracts" in imported_modules(
        query_path
    )


def test_screening_intake_is_a_private_one_way_application_boundary() -> None:
    intake_path = PACKAGE_ROOT / "application" / "screening_intake.py"
    imports = imported_modules(intake_path)
    forbidden = (
        "tradesieve.adapters",
        "tradesieve.ports",
        "tradesieve.manage",
        "tradesieve.runtime",
        "fastapi",
        "httpx",
        "mcp",
        "os",
        "pathlib",
        "psycopg",
        "requests",
        "socket",
        "sqlalchemy",
        "urllib",
    )
    violations = sorted(
        imported
        for imported in imports
        if any(
            imported == prefix or imported.startswith(f"{prefix}.")
            for prefix in forbidden
        )
    )

    assert not violations
    assert "tradesieve.application.auth.RequestContext" in imports
    assert "tradesieve.application.contracts.ScreeningRequest" in imports


def test_screening_submission_domain_and_application_boundaries_are_one_way() -> None:
    domain_path = PACKAGE_ROOT / "domain" / "screening_submission.py"
    application_path = PACKAGE_ROOT / "application" / "screening_submission.py"
    adapter_path = PACKAGE_ROOT / "adapters" / "in_memory_screening_submission.py"
    domain_imports = imported_modules(domain_path)
    application_imports = imported_modules(application_path)
    adapter_imports = imported_modules(adapter_path)
    forbidden_domain = (
        "tradesieve.application",
        "tradesieve.adapters",
        "tradesieve.ports",
        "fastapi",
        "httpx",
        "mcp",
        "os",
        "pathlib",
        "psycopg",
        "socket",
        "sqlalchemy",
        "urllib",
        "uuid",
    )
    forbidden_application = (
        "tradesieve.adapters",
        "tradesieve.ports",
        "tradesieve.manage",
        "tradesieve.runtime",
        "fastapi",
        "httpx",
        "mcp",
        "os",
        "pathlib",
        "psycopg",
        "socket",
        "sqlalchemy",
        "urllib",
        "uuid",
    )

    assert not {
        imported
        for imported in domain_imports
        if any(
            imported == prefix or imported.startswith(f"{prefix}.")
            for prefix in forbidden_domain
        )
    }
    assert not {
        imported
        for imported in application_imports
        if any(
            imported == prefix or imported.startswith(f"{prefix}.")
            for prefix in forbidden_application
        )
    }
    assert "tradesieve.domain.screening_submission" in application_imports
    assert "tradesieve.application.auth.AuthorizationRequest" in application_imports
    assert "tradesieve.application.auth.AuthorizationService" in application_imports
    assert "tradesieve.application.screening_submission" in adapter_imports
    assert "tradesieve.application.screening_intake" in adapter_imports
    assert "threading.RLock" in adapter_imports

    application_tree = ast.parse(
        application_path.read_text(encoding="utf-8"), filename=str(application_path)
    )
    classes = {
        node.name: node
        for node in application_tree.body
        if isinstance(node, ast.ClassDef)
    }
    bound_methods = {
        node.name
        for node in classes["BoundScreeningSubmission"].body
        if isinstance(node, ast.FunctionDef)
    }
    assert "_verified_intake_for_persistence" in bound_methods
    assert not {
        name
        for name in bound_methods
        if not name.startswith("_") and name.startswith(("raw", "list", "export"))
    }
    protocol_methods = {
        node.name
        for node in classes["ScreeningSubmissionUnitOfWork"].body
        if isinstance(node, ast.FunctionDef)
    }
    assert protocol_methods == {"submit_atomic", "read_result", "resolve_attempt"}

    service_methods = {
        node.name
        for node in classes["ScreeningSubmissionService"].body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    }
    assert service_methods == {"submit"}
    service_submit = next(
        node
        for node in classes["ScreeningSubmissionService"].body
        if isinstance(node, ast.FunctionDef) and node.name == "submit"
    )
    assert [argument.arg for argument in service_submit.args.args] == [
        "self",
        "intake",
        "idempotency_key",
    ]
    assert not service_submit.args.kwonlyargs


@pytest.mark.parametrize(
    ("statement", "expected_import"),
    [
        ("from ..application import command", "tradesieve.application"),
        ("from .. import adapters", "tradesieve.adapters"),
        ("from ..adapters import client", "tradesieve.adapters"),
        ("from tradesieve import adapters", "tradesieve.adapters"),
        ("from tradesieve import application", "tradesieve.application"),
        ("from tradesieve.adapters import client", "tradesieve.adapters"),
    ],
)
def test_outward_import_forms_are_detected(
    tmp_path: Path, statement: str, expected_import: str
) -> None:
    package_root = tmp_path / "tradesieve"
    domain_root = package_root / "domain"
    domain_root.mkdir(parents=True)
    fixture = domain_root / "boundary_violation.py"
    fixture.write_text(f"{statement}\n", encoding="utf-8")

    violations = layer_violations(
        package_root,
        "domain",
        FORBIDDEN_IMPORTS["domain"],
    )
    assert f"domain/boundary_violation.py imports {expected_import}" in violations


def test_demo_runtime_entrypoints_never_construct_authorized_requests_directly() -> (
    None
):
    """AuthorizationService must remain the sole demo authorization factory."""

    violations: list[str] = []
    for name in (
        "demo_rule_bundle.py",
        "demo_source_snapshot.py",
        "manage.py",
        "runtime.py",
    ):
        path = PACKAGE_ROOT / name
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            direct_name = (
                isinstance(node.func, ast.Name) and node.func.id == "AuthorizedRequest"
            )
            direct_attribute = (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "AuthorizedRequest"
            )
            if direct_name or direct_attribute:
                violations.append(f"{name}:{node.lineno}")
    assert not violations, "direct AuthorizedRequest construction found: " + ", ".join(
        violations
    )
