"""Fail-closed pins for profiler bring-up scripts on this stack."""

from __future__ import annotations

from importlib.machinery import ModuleSpec
import importlib.util
import pathlib
from typing import Any

import pytest


_REPO_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parents[2]
_VERIFY_SCRIPT: pathlib.Path = _REPO_ROOT / "scripts" / "verify_profiler_compatibility.py"
_RUN_PROFILING_TESTS: pathlib.Path = _REPO_ROOT / "scripts" / "run-profiling-tests"


@pytest.fixture(scope="module")
def verify_mod() -> Any:
    spec: ModuleSpec | None = importlib.util.spec_from_file_location("verify_profiler_compatibility", _VERIFY_SCRIPT)
    assert spec is not None and spec.loader is not None
    module: Any = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unparsed_pprof_fails_when_files_missing(verify_mod: Any) -> None:
    result: dict[str, Any] = {"passed": True}
    out: dict[str, Any] = verify_mod._fail_unparsed_pprof(result, [], ImportError("zstandard"))
    assert out["passed"] is False
    assert out["pprof_written"] is False
    assert "Wall-time sample contract not met" in out["error"]


def test_unparsed_pprof_fails_when_files_exist_but_unparsed(verify_mod: Any) -> None:
    result: dict[str, Any] = {"passed": True}
    out: dict[str, Any] = verify_mod._fail_unparsed_pprof(result, ["/tmp/compat.pprof"], ImportError("protobuf"))
    assert out["passed"] is False
    assert out["pprof_written"] is True
    assert "content not validated" in out["error"]


def test_run_profiling_tests_fails_closed_on_missing_venvs() -> None:
    text: str = _RUN_PROFILING_TESTS.read_text()
    assert "ERROR: No 'profile' riot venvs found" in text
    assert "ERROR: No 'profile-memalloc' riot venvs found" in text
    assert "ERROR: required riot venvs/tests missing." in text
    assert "WARNING: No 'profile' riot venvs found" not in text
    assert "missing_venvs=1" in text
