"""Tests for the new-Python-function annotation checker."""

from __future__ import annotations

import importlib.util
import pathlib
import types

import pytest


def _load() -> types.ModuleType:
    script_path: pathlib.Path = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "check_new_untyped_defs.py"
    spec: importlib.machinery.ModuleSpec | None = importlib.util.spec_from_file_location(
        "check_new_untyped_defs", script_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {script_path}")
    module: types.ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE: types.ModuleType = _load()


@pytest.mark.parametrize(
    ("path", "base", "head", "expected"),
    [
        (
            "ddtrace/internal/example.py",
            None,
            "def new_handler(request):\n    return request\n",
            [(1, "new_handler", "missing type on parameter request and return")],
        ),
        (
            "tests/example/test_example.py",
            None,
            "async def test_new_handler(client):\n    return client\n",
            [(1, "test_new_handler", "missing type on parameter client and return")],
        ),
        (
            "ddtrace/contrib/internal/example/patch.py",
            None,
            "def new_handler(request: object) -> object:\n    return request\n",
            [],
        ),
        (
            "ddtrace/internal/example.py",
            None,
            "class Handler:\n    def handle(self) -> None:\n        pass\n",
            [],
        ),
        (
            "ddtrace/internal/example.py",
            None,
            "class Handler:\n    def handle(self):\n        pass\n",
            [(2, "Handler.handle", "missing type on return")],
        ),
        (
            "ddtrace/internal/example.py",
            None,
            "class Handler:\n    @classmethod\n    def handle(cls):\n        pass\n",
            [(3, "Handler.handle", "missing type on return")],
        ),
        (
            "ddtrace/internal/example.py",
            None,
            "class Handler:\n    @staticmethod\n    def handle(value):\n        pass\n",
            [(3, "Handler.handle", "missing type on parameter value and return")],
        ),
        (
            "ddtrace/internal/example.py",
            None,
            "def func():\n    pass\n",
            [(1, "func", "missing type on return")],
        ),
        (
            "ddtrace/internal/example.py",
            None,
            "def func(value: object):\n    pass\n",
            [(1, "func", "missing type on return")],
        ),
        (
            "ddtrace/internal/example.py",
            None,
            "def func(value) -> None:\n    pass\n",
            [(1, "func", "missing type on parameter value")],
        ),
        (
            "ddtrace/internal/example.py",
            None,
            "def func(*args, **kwargs):\n    pass\n",
            [(1, "func", "missing type on parameter *args, **kwargs and return")],
        ),
        (
            "ddtrace/internal/example.py",
            "def legacy(request):\n    return request\n",
            "def legacy(request):\n    return request + 1\n",
            [],
        ),
        (
            "ddtrace/internal/example.py",
            "def legacy(request):\n    return request\n",
            "def legacy(request, extra):\n    return extra\n",
            [(1, "legacy", "missing type on parameter extra")],
        ),
        (
            "ddtrace/internal/example.py",
            "def legacy(request):\n    return request\n",
            "def legacy(request, extra: int) -> int:\n    return extra\n",
            [],
        ),
        (
            "ddtrace/internal/example.py",
            "class Handler:\n    def old(self):\n        pass\n",
            "class Handler:\n    def old(self):\n        pass\n\n    def added(self, value):\n        return value\n",
            [(5, "Handler.added", "missing type on parameter value and return")],
        ),
        (
            "ddtrace/vendor/example.py",
            None,
            "def new_handler(request):\n    return request\n",
            [],
        ),
        (
            "tests/contrib/grpc/hello_pb2.py",
            None,
            "def new_handler(request):\n    return request\n",
            [],
        ),
        (
            "ddtrace/internal/example.py",
            None,
            "def outer():\n    def inner(value):\n        return value\n    return inner\n",
            [
                (1, "outer", "missing type on return"),
                (2, "outer.inner", "missing type on parameter value and return"),
            ],
        ),
        (
            "ddtrace/internal/example.py",
            None,
            "def snapshot() -> None:\n    n_pauses = 1\n    return None\n",
            [(2, "snapshot", "missing type on n_pauses")],
        ),
        (
            "ddtrace/internal/example.py",
            None,
            "def snapshot() -> None:\n    n_pauses: int = 1\n    return None\n",
            [],
        ),
        (
            "ddtrace/internal/example.py",
            "def legacy() -> None:\n    n = 1\n    return None\n",
            "def legacy() -> None:\n    n = 2\n    extra = 1\n    return None\n",
            [(3, "legacy", "missing type on extra")],
        ),
        (
            "ddtrace/internal/example.py",
            None,
            "def walk() -> None:\n    for i in range(3):\n        pass\n",
            [],
        ),
        (
            "ddtrace/internal/example.py",
            None,
            "def delta() -> None:\n    current, previous = [], []\n",
            [],
        ),
        (
            "ddtrace/internal/example.py",
            None,
            'NAME = "runtime.python.gc"\n',
            [(1, "<module>", "missing type on NAME")],
        ),
        (
            "ddtrace/internal/example.py",
            None,
            'NAME: str = "runtime.python.gc"\n',
            [],
        ),
        (
            "ddtrace/internal/example.py",
            None,
            "from typing import TypeAlias\n\nHandler: TypeAlias = int | str\n",
            [],
        ),
        (
            "ddtrace/internal/example.py",
            None,
            "class GC:\n    def __init__(self) -> None:\n        self._lock = None\n",
            [(3, "GC.__init__", "missing type on self._lock")],
        ),
        (
            "ddtrace/internal/example.py",
            None,
            "class GC:\n    def __init__(self) -> None:\n        self._lock: object = None\n",
            [],
        ),
        (
            "ddtrace/internal/example.py",
            None,
            "class GC:\n    GEN = 3\n",
            [(2, "GC", "missing type on GEN")],
        ),
        (
            "ddtrace/internal/example.py",
            "class GC:\n    def __init__(self) -> None:\n        self._count = 0\n",
            "class GC:\n    def __init__(self) -> None:\n        self._count = 1\n        self._max: int = 0\n",
            [],
        ),
    ],
)
def test_find_violations(
    path: str,
    base: str | None,
    head: str,
    expected: list[tuple[int, str, str]],
) -> None:
    assert _MODULE.find_violations(path, base, head) == expected


def test_unparseable_source_is_skipped() -> None:
    assert _MODULE.find_violations("ddtrace/internal/example.py", None, "def (\n") == []


def test_parse_name_status_keeps_rename_base_path() -> None:
    output: str = "\n".join(
        [
            "A\tddtrace/new.py",
            "M\tddtrace/old.py",
            "R100\tddtrace/before.py\tddtrace/after.py",
        ]
    )
    assert _MODULE._parse_name_status(output) == [
        ("ddtrace/new.py", None),
        ("ddtrace/old.py", "ddtrace/old.py"),
        ("ddtrace/after.py", "ddtrace/before.py"),
    ]
