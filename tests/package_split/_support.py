"""Shared fixture helpers for the package-split tests."""

from __future__ import annotations

from pathlib import Path


def make_tree(root: Path, package_name: str, files: dict[str, str]) -> None:
    """Write files (relative path -> content) under root/package_name."""
    for relative, content in files.items():
        path = root / package_name / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
