"""Shared fixtures for source inventories and freeze enforcement."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from package_split.evidence import GateReport


class Args:
    """Minimal argparse stand-in for calling the freeze gate directly."""

    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)

    def __getattr__(self, name: str) -> Any:
        return None


def make_report(gate: str, manifest_path: Path) -> GateReport:
    return GateReport(gate, manifest_path, "test")


def make_tree(root: Path, package_name: str, files: dict[str, str]) -> None:
    """Write fixture sources without requiring a package contract or build."""
    for relative, content in files.items():
        path = root / package_name / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
