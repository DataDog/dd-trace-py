"""Shared fixtures for source inventories."""

from __future__ import annotations

from pathlib import Path


def make_tree(root: Path, package_name: str, files: dict[str, str]) -> None:
    """Write fixture sources without requiring a package contract or build."""
    for relative, content in files.items():
        path = root / package_name / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
