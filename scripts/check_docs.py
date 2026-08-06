#!/usr/bin/env python3
"""Small dependency-free repository documentation checks."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "AGENTS.md",
    "README.md",
    "docs/README.md",
    "docs/requirements/original-request.md",
    "docs/requirements/problem-and-scope.md",
    "docs/architecture/initial-system-shape.md",
    "docs/architecture/agent-interface-principles.md",
    "docs/decisions/0001-independent-control-plane.md",
    "docs/decisions/0002-human-clearance-only.md",
    "docs/decisions/0003-one-contract-multiple-interfaces.md",
    "research/sources.yaml",
)
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


def check_local_links(path: Path, text: str) -> list[str]:
    errors: list[str] = []
    for raw_target in MARKDOWN_LINK.findall(text):
        target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
        if not target or target.startswith(("#", "http://", "https://", "mailto:")):
            continue
        file_part = unquote(target.split("#", 1)[0])
        resolved = (path.parent / file_part).resolve()
        try:
            resolved.relative_to(ROOT)
        except ValueError:
            errors.append(f"{path.relative_to(ROOT)}: link escapes repository: {target}")
            continue
        if not resolved.exists():
            errors.append(f"{path.relative_to(ROOT)}: broken local link: {target}")
    return errors


def main() -> int:
    errors: list[str] = []
    for required in REQUIRED:
        if not (ROOT / required).is_file():
            errors.append(f"missing required file: {required}")

    checked = 0
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or ".git" in path.parts:
            continue
        if path.suffix.lower() not in {".md", ".yaml", ".yml", ".json", ".py", ".sh"}:
            continue
        checked += 1
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(ROOT)
        if not text.endswith("\n"):
            errors.append(f"{relative}: missing final newline")
        left_marker = "<" * 7
        right_marker = ">" * 7
        if left_marker in text or right_marker in text:
            errors.append(f"{relative}: unresolved merge marker")
        if path.suffix.lower() == ".md":
            errors.extend(check_local_links(path, text))

    registry = (ROOT / "research/sources.yaml").read_text(encoding="utf-8")
    source_count = len(re.findall(r"^  - id: ", registry, flags=re.MULTILINE))
    if source_count < 10:
        errors.append("research/sources.yaml: expected at least 10 registered sources")

    if errors:
        print("Documentation checks failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"Documentation checks passed ({checked} files, {source_count} registered sources).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
