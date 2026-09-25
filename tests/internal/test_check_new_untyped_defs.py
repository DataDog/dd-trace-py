"""Tests for the new-untyped-definitions checker."""

from __future__ import annotations

import importlib.util
import pathlib
import types
from typing import Optional

import pytest


_SCRIPT_PATH: pathlib.Path = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "check_new_untyped_defs.py"
_SPEC: Optional[importlib.machinery.ModuleSpec] = importlib.util.spec_from_file_location(
    "check_new_untyped_defs", _SCRIPT_PATH
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE: types.ModuleType = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


@pytest.mark.parametrize(
    ("path", "source", "added_lines", "expected"),
    [
        (
            "ddtrace/contrib/internal/example/patch.py",
            "def new_handler(request):\n    return request\n",
            {1},
            [(1, "new_handler")],
        ),
        (
            "tests/contrib/example/test_example.py",
            "async def test_new_handler(client):\n    return client\n",
            {1},
            [(1, "test_new_handler")],
        ),
        (
            "ddtrace/contrib/internal/example/patch.py",
            "def new_handler(request: object) -> object:\n    return request\n",
            {1},
            [],
        ),
        (
            "ddtrace/contrib/internal/example/patch.py",
            "class Handler:\n    def handle(self) -> None:\n        pass\n",
            {2},
            [],
        ),
        (
            "ddtrace/contrib/internal/example/patch.py",
            "class Handler:\n    @classmethod\n    def handle(cls):\n        pass\n",
            {3},
            [],
        ),
        (
            "ddtrace/contrib/internal/example/patch.py",
            "def legacy(request):\n    return request\n\ndef added(request: object) -> object:\n    return request\n",
            {4, 5},
            [],
        ),
        (
            "ddtrace/internal/example.py",
            "def new_handler(request):\n    return request\n",
            {1},
            [],
        ),
    ],
)
def test_find_new_untyped_defs(path: str, source: str, added_lines: set[int], expected: list[tuple[int, str]]) -> None:
    assert _MODULE._find_new_untyped_defs(path, source, added_lines) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("def func(value):\n    pass\n", True),
        ("def func(value: object):\n    pass\n", False),
        ("def func(value) -> None:\n    pass\n", False),
        ("def func(self, value):\n    pass\n", True),
        ("def func(self):\n    pass\n", False),
        ("def func():\n    pass\n", False),
        ("def func(*args, **kwargs):\n    pass\n", True),
    ],
)
def test_is_completely_untyped(source: str, expected: bool) -> None:
    function = _MODULE.ast.parse(source).body[0]
    assert _MODULE._is_completely_untyped(function) is expected


def test_added_lines_tracks_current_file_line_numbers(monkeypatch: pytest.MonkeyPatch) -> None:
    diff: str = "\n".join(
        [
            "diff --git a/example.py b/example.py",
            "--- a/example.py",
            "+++ b/example.py",
            "@@ -1,2 +1,3 @@",
            " unchanged",
            "+added",
            " unchanged",
            "@@ -10,0 +12,2 @@",
            "+another",
            "+addition",
        ]
    )

    def fake_run_git(args: list[str], *, check: bool = True) -> types.SimpleNamespace:
        return types.SimpleNamespace(stdout=diff)

    monkeypatch.setattr(_MODULE, "_run_git", fake_run_git)

    assert _MODULE._added_lines("base", "example.py") == {2, 12, 13}
