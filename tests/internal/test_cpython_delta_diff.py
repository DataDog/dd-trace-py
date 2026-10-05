"""Synthetic-diff pins for inventory→work-item join and enum classification."""

from __future__ import annotations

from importlib.machinery import ModuleSpec
import importlib.util
import pathlib
import sys
from typing import Any

import pytest


_REPO_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parents[2]
_DIFF_SCRIPT: pathlib.Path = _REPO_ROOT / "scripts" / "cpython_delta" / "diff.py"
_COMMON_SCRIPT: pathlib.Path = _REPO_ROOT / "scripts" / "cpython_delta" / "common.py"


@pytest.fixture(scope="module")
def diff_mod() -> Any:
    spec: ModuleSpec | None = importlib.util.spec_from_file_location("cpython_delta_diff_join", _DIFF_SCRIPT)
    assert spec is not None and spec.loader is not None
    module: Any = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def common_mod() -> Any:
    spec: ModuleSpec | None = importlib.util.spec_from_file_location("cpython_delta_common", _COMMON_SCRIPT)
    assert spec is not None and spec.loader is not None
    module: Any = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _inventory_row(key: str, symbol: str, kind: str, file: str, line: int) -> dict[str, Any]:
    return {
        "key": key,
        "symbol": symbol,
        "kind": kind,
        "sites": [{"file": file, "line": line}],
        "cpython_paths": [],
        "notes": "",
    }


def test_fixed_watch_paths_include_public_code_and_pystate_headers(common_mod: Any) -> None:
    watch: tuple[str, ...] = common_mod.FIXED_WATCH_PATHS
    assert "Include/cpython/code.h" in watch
    assert "Include/cpython/pystate.h" in watch


def test_inventory_scan_roots_include_setup_py(common_mod: Any) -> None:
    """Profiling build/version gates in setup.py must be inventoried."""
    roots: tuple[str, ...] = common_mod.INVENTORY_SCAN_ROOTS
    assert "setup.py" in roots


def test_git_diff_paths_disables_rename_detection(diff_mod: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Rename-only moves must become delete/add hunks (``--no-renames``)."""
    captured: list[list[str]] = []

    class _FakeProc:
        def __init__(self, stdout: str = "", returncode: int = 0) -> None:
            self.stdout: str = stdout
            self.returncode: int = returncode
            self.stderr: str = ""

    def _fake_run_git(_cpython: Any, args: list[str]) -> _FakeProc:
        captured.append(list(args))
        if args[:2] == ["ls-tree", "-r"]:
            return _FakeProc(stdout="Include/internal/pycore_frame.h\n")
        if args and args[0] == "diff":
            return _FakeProc(stdout="")
        return _FakeProc()

    monkeypatch.setattr(diff_mod, "_run_git", _fake_run_git)
    _diff_text: str
    _used: list[str]
    _diff_text, _used = diff_mod.git_diff_paths(
        pathlib.Path("/tmp/fake-cpython"),
        "v3.14.0",
        "v3.15.0a7",
        ["Include/internal/pycore_frame.h"],
    )
    diff_calls: list[list[str]] = [args for args in captured if args and args[0] == "diff"]
    assert diff_calls, "expected a git diff invocation"
    assert "--no-renames" in diff_calls[0]


def test_join_links_inventory_tokens_absent_from_symbol_regex(diff_mod: Any) -> None:
    """Inventoried fields/types outside ``_SYMBOL_TOKEN_RE`` still become work items."""
    inventory: dict[str, Any] = {
        "symbols": [
            _inventory_row(
                "field_access:co_code_adaptive",
                "co_code_adaptive",
                "struct_field",
                "ddtrace/internal/datadog/profiling/stack/src/echion/frame.cc",
                149,
            ),
            _inventory_row(
                "type:PyCodeObject",
                "PyCodeObject",
                "struct_type",
                "ddtrace/internal/datadog/profiling/stack/src/echion/frame.cc",
                140,
            ),
            _inventory_row(
                "field_access:current_frame",
                "current_frame",
                "struct_field",
                "ddtrace/internal/datadog/profiling/stack/src/profiling_helpers/frame_accessors.h",
                36,
            ),
            _inventory_row(
                "type:PyThreadState",
                "PyThreadState",
                "struct_type",
                "ddtrace/internal/datadog/profiling/stack/src/profiling_helpers/frame_accessors.h",
                30,
            ),
            _inventory_row(
                "field_access:task_node",
                "task_node",
                "struct_field",
                "ddtrace/internal/datadog/profiling/stack/src/echion/task.cc",
                10,
            ),
        ]
    }
    diff_text: str = """diff --git a/Include/cpython/code.h b/Include/cpython/code.h
--- a/Include/cpython/code.h
+++ b/Include/cpython/code.h
@@ -10,3 +10,4 @@ typedef struct {
     PyObject *co_name;
-    char co_code_adaptive[1];
+    char co_code_adaptive[8];
 } PyCodeObject;
diff --git a/Include/cpython/pystate.h b/Include/cpython/pystate.h
--- a/Include/cpython/pystate.h
+++ b/Include/cpython/pystate.h
@@ -20,2 +20,3 @@ typedef struct _ts {
-    struct _frame *current_frame;
+    _PyInterpreterFrame *current_frame;
 } PyThreadState;
diff --git a/Modules/_asynciomodule.c b/Modules/_asynciomodule.c
--- a/Modules/_asynciomodule.c
+++ b/Modules/_asynciomodule.c
@@ -100,1 +100,2 @@ typedef struct {
-    llist_node task_node;
+    llist_node task_node; /* layout shift */
 } TaskObj;
"""
    hunks: list[Any] = diff_mod.parse_unified_diff(diff_text)
    curated_misses: list[str] = ["co_code_adaptive", "current_frame", "task_node", "PyCodeObject", "PyThreadState"]
    for name in curated_misses:
        assert all(name not in hunk.symbols for hunk in hunks), name

    doc: dict[str, Any] = diff_mod.join_worklist("v3.14.0", "v3.15.0a7", inventory, hunks)
    by_symbol: dict[str, dict[str, Any]] = {item["symbol"]: item for item in doc["work_items"]}

    assert by_symbol["co_code_adaptive"]["inventory_key"] == "field_access:co_code_adaptive"
    assert by_symbol["co_code_adaptive"]["ddtrace_sites"]
    assert by_symbol["PyCodeObject"]["inventory_key"] == "type:PyCodeObject"
    assert by_symbol["current_frame"]["inventory_key"] == "field_access:current_frame"
    assert by_symbol["PyThreadState"]["inventory_key"] == "type:PyThreadState"
    assert by_symbol["task_node"]["inventory_key"] == "field_access:task_node"

    stable_keys: set[str] = {row["key"] for row in doc["stable_inventory"]}
    assert "field_access:co_code_adaptive" not in stable_keys
    assert "field_access:current_frame" not in stable_keys
    assert "field_access:task_node" not in stable_keys


def test_classify_added_frame_enum(diff_mod: Any) -> None:
    kind: str
    priority: str
    kind, priority = diff_mod._classify_change(
        "FRAME_SUSPENDED_YIELD_FROM_LOCKED",
        ["+    FRAME_SUSPENDED_YIELD_FROM_LOCKED = 3,"],
    )
    assert kind == "new_field_or_enum"
    assert priority == "silent_misread"


def test_classify_removed_frame_enum(diff_mod: Any) -> None:
    kind: str
    priority: str
    kind, priority = diff_mod._classify_change(
        "FRAME_COMPLETED",
        ["-    FRAME_COMPLETED = 4,"],
    )
    assert kind == "removed"
    assert priority == "breaks_build"


def test_classify_renumbered_frame_enum_requires_both_sides(diff_mod: Any) -> None:
    kind: str
    priority: str
    kind, priority = diff_mod._classify_change(
        "FRAME_CREATED",
        [
            "-    FRAME_CREATED = 0,",
            "+    FRAME_CREATED = 1,",
        ],
    )
    assert kind == "renumbered_enum"
    assert priority == "breaks_build"


def test_classify_indented_plus_line_counts_as_added(diff_mod: Any) -> None:
    """Whitespace after ``+`` must not make a both-sides change look removed-only."""
    kind: str
    priority: str
    kind, priority = diff_mod._classify_change(
        "FRAME_CREATED",
        [
            "-FRAME_CREATED = 0,",
            "+    FRAME_CREATED = 1,",
        ],
    )
    assert kind == "renumbered_enum"
    assert priority == "breaks_build"
