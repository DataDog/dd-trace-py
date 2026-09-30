"""Backtest scorer pins: work-item symbols only, and only for 3.14.0→3.15.0a7."""

from __future__ import annotations

from importlib.machinery import ModuleSpec
import importlib.util
import pathlib
import sys
from typing import Any

import pytest


_REPO_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parents[2]
_DIFF_SCRIPT: pathlib.Path = _REPO_ROOT / "scripts" / "cpython_delta" / "diff.py"

# These strings sit in stable_inventory. The old blob scorer counted them
# without any work-item symbol (6 expected rows, plus AsyncioDebug as remote_debugging).
_STABLE_FALSE_HITS: list[dict[str, str]] = [
    {"symbol": "FRAME_CREATED"},
    {"symbol": "FRAME_OWNED_BY_CSTACK"},
    {"symbol": "Py_TAG_INT"},
    {"symbol": "base_frame"},
    {"symbol": "_Py_AsyncioDebug"},
    {"symbol": "gi_frame_state"},
    {"symbol": "AsyncioDebug"},
    {"symbol": "_remote_debugging"},
]


@pytest.fixture(scope="module")
def diff_mod() -> Any:
    spec: ModuleSpec | None = importlib.util.spec_from_file_location("cpython_delta_diff", _DIFF_SCRIPT)
    assert spec is not None and spec.loader is not None
    module: Any = importlib.util.module_from_spec(spec)
    # Register before exec so dataclass string annotations can resolve the module.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _calibration_doc(
    work_items: list[dict[str, Any]],
    stable: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    stable_rows: list[dict[str, str]] = list(stable or [])
    return {
        "old": "v3.14.0",
        "new": "v3.15.0a7",
        "work_items": work_items,
        "stable_inventory": stable_rows,
    }


def _row(recall: dict[str, Any], row_id: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = list(recall["rows"])
    found: dict[str, Any] | None = next((row for row in rows if row["id"] == row_id), None)
    assert found is not None
    return found


def test_empty_worklist_does_not_score_stable_inventory(diff_mod: Any) -> None:
    work_doc: dict[str, Any] = _calibration_doc([], _STABLE_FALSE_HITS)
    recall: dict[str, Any] = diff_mod.run_backtest_recall(work_doc)
    assert recall["matched"] == 0
    assert f"{recall['matched']}/{recall['expected']}" != "6/8"
    assert all(not row["hit"] for row in recall["rows"])
    assert "(blob match)" not in str(recall)


def test_asyncio_debug_symbol_does_not_satisfy_remote_debugging(diff_mod: Any) -> None:
    work_doc: dict[str, Any] = _calibration_doc(
        [
            {"symbol": "_Py_AsyncioDebug", "cpython_paths": ["Modules/_asynciomodule.c"]},
            {"symbol": "AsyncioDebug", "cpython_paths": []},
        ],
        _STABLE_FALSE_HITS,
    )
    recall: dict[str, Any] = diff_mod.run_backtest_recall(work_doc)
    remote: dict[str, Any] = _row(recall, "remote_debugging")
    asyncio_debug: dict[str, Any] = _row(recall, "asyncio_debug_sym")
    assert remote["hit"] is False
    assert asyncio_debug["hit"] is True
    assert "_Py_AsyncioDebug" in asyncio_debug["matched_symbols"]


def test_remote_debugging_requires_remote_debugging_path(diff_mod: Any) -> None:
    work_doc: dict[str, Any] = _calibration_doc(
        [
            {
                "symbol": "task_node",
                "cpython_paths": ["Modules/_remote_debugging/module.c"],
            }
        ]
    )
    recall: dict[str, Any] = diff_mod.run_backtest_recall(work_doc)
    remote: dict[str, Any] = _row(recall, "remote_debugging")
    assert remote["hit"] is True
    assert any("_remote_debugging" in hit for hit in remote["matched_symbols"])


def test_backtest_refuses_other_tag_pairs(diff_mod: Any) -> None:
    work_doc: dict[str, Any] = {
        "old": "v3.15.0",
        "new": "v3.16.0a1",
        "work_items": [{"symbol": "FRAME_CREATED"}, {"symbol": "_remote_debugging"}],
        "stable_inventory": _STABLE_FALSE_HITS,
    }
    refusal: str | None
    scored: dict[str, Any] | None
    refusal, scored = diff_mod.maybe_run_backtest("v3.15.0", "v3.16.0a1", work_doc)
    assert refusal is not None
    assert "v3.14.0" in refusal
    assert "v3.15.0a7" in refusal
    assert scored is None
    assert "8/8" not in refusal
    with pytest.raises(SystemExit, match="not scoring v3.15.0→v3.16.0a1"):
        diff_mod.run_backtest_recall(work_doc)
