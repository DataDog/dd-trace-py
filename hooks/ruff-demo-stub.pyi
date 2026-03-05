# Demo: .pyi stub included in pre-commit (Codex P2). Safe to remove.
"""Minimal stub with fixable ruff violation to demonstrate .pyi support."""

from typing import Dict
from typing import List

x: List[int]  # noqa: UP006
y: Dict[str, int]  # noqa: UP006
