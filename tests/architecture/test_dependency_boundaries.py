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
