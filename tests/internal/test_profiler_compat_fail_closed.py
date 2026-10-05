"""Fail-closed pins for profiler bring-up scripts on this stack."""

from __future__ import annotations

from importlib.machinery import ModuleSpec
import importlib.util
import json
import pathlib
import sys
from typing import Any

import pytest


def _fake_python_check_output(*args: Any, **kwargs: Any) -> str:
    """Return MAJOR.MINOR or a full version string based on the -c script."""
    cmd: list[Any] = args[0] if args else []
    script: str = str(cmd[2]) if len(cmd) > 2 else ""
    if "version_info" in script:
        return "3.16\n"
    return "3.16.0 (main, Jan 1 2026)\n"


_REPO_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parents[2]
_VERIFY_SCRIPT: pathlib.Path = _REPO_ROOT / "scripts" / "verify_profiler_compatibility.py"
_RUN_PROFILING_TESTS: pathlib.Path = _REPO_ROOT / "scripts" / "run-profiling-tests"
_VERSION_REGISTRY: pathlib.Path = _REPO_ROOT / "scripts" / "profiles" / "profiling_versions.json"


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
    assert "profiling_versions.json" in text
    assert "REGISTRY_FILE" in text
    assert "default_python" in text


def test_hex_for_version(verify_mod: Any) -> None:
    hex_315: str = verify_mod._hex_for_version(3, 15)
    hex_316: str = verify_mod._hex_for_version(3, 16)
    assert hex_315 == "0x030f0000"
    assert hex_316 == "0x03100000"


def test_open_checklist_rows_lists_undone(verify_mod: Any) -> None:
    entry: dict[str, Any] = {
        "checklist": {
            "alpha": [{"id": "native_abi", "done": True, "note": "done"}],
            "beta": [{"id": "asyncio_hook", "done": False, "note": "still open"}],
            "rc": [],
            "final": [{"id": "ssi_oci", "done": False, "note": "final only"}],
        }
    }
    rows: list[str] = verify_mod._open_checklist_rows(entry)
    assert rows == [
        "[beta] asyncio_hook: still open",
        "[final] ssi_oci: final only",
    ]


def test_scaffold_stubs_registry_and_baseline(
    verify_mod: Any,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path: pathlib.Path = tmp_path / "profiling_versions.json"
    baseline_path: pathlib.Path = tmp_path / "compatibility_baselines.json"
    registry_path.write_text(
        json.dumps(
            {
                "default_python": "3.15",
                "versions": {},
                "checklist_template": {
                    "alpha": [{"id": "native_abi", "done": False, "note": "do natives"}],
                    "beta": [],
                    "rc": [],
                    "final": [{"id": "ssi_oci", "done": False, "note": "final only"}],
                },
            }
        )
        + "\n"
    )
    baseline_path.write_text("{}\n")
    monkeypatch.setattr(verify_mod, "_VERSION_REGISTRY_FILE", registry_path)
    monkeypatch.setattr(verify_mod, "_BASELINE_FILE", baseline_path)

    verify_mod._scaffold_version("3.16")

    registry: dict[str, Any] = json.loads(registry_path.read_text())
    baselines: dict[str, Any] = json.loads(baseline_path.read_text())
    entry: dict[str, Any] = registry["versions"]["3.16"]
    assert entry["hex"] == "0x03100000"
    assert entry["major"] == 3
    assert entry["minor"] == 16
    assert entry["checklist"]["alpha"][0]["id"] == "native_abi"
    assert entry["checklist"]["alpha"][0]["done"] is False
    assert "3.16" in baselines
    assert baselines["3.16"]["scaffolded"] is True
    assert baselines["3.16"]["asyncio_guards"]["passed"] is False
    assert baselines["3.16"]["profiler_samples"]["passed"] is False


def test_version_registry_default_python_present() -> None:
    data: dict[str, Any] = json.loads(_VERSION_REGISTRY.read_text())
    default_python: str = data["default_python"]
    assert default_python == "3.15"
    assert "3.15" in data["versions"]
    assert data["versions"]["3.15"]["hex"] == "0x030f0000"


def test_quick_cannot_combine_with_baseline(
    verify_mod: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["verify_profiler_compatibility.py", "--quick", "--baseline"])
    with pytest.raises(SystemExit, match="--quick cannot be combined with --baseline"):
        verify_mod.main()


def test_baseline_cannot_combine_with_compare(
    verify_mod: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["verify_profiler_compatibility.py", "--baseline", "--compare"])
    with pytest.raises(SystemExit, match="--baseline and --compare are mutually exclusive"):
        verify_mod.main()


def test_compare_rejects_scaffolded_baseline(
    verify_mod: Any,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    baseline_path: pathlib.Path = tmp_path / "compatibility_baselines.json"
    baseline_path.write_text(
        json.dumps(
            {
                "3.16": {
                    "scaffolded": True,
                    "asyncio_guards": {"passed": False},
                    "profiler_samples": {
                        "passed": False,
                        "min_wall_time_samples": 5,
                        "asyncio_task_names_seen": ["compat-task-0"],
                    },
                }
            }
        )
        + "\n"
    )
    monkeypatch.setattr(verify_mod, "_BASELINE_FILE", baseline_path)
    monkeypatch.setattr(verify_mod, "_find_python", lambda _version: sys.executable)
    monkeypatch.setattr(verify_mod.subprocess, "check_output", _fake_python_check_output)

    class _FakeProc:
        returncode: int = 0
        stdout: str = json.dumps(
            {
                "asyncio_guards": {"passed": True},
                "profiler_samples": {
                    "passed": True,
                    "wall_time_samples": 10,
                    "asyncio_task_names_seen": ["compat-task-0"],
                },
            }
        )
        stderr: str = ""

    monkeypatch.setattr(verify_mod.subprocess, "run", lambda *args, **kwargs: _FakeProc())
    monkeypatch.setattr(sys, "argv", ["verify_profiler_compatibility.py", "--compare"])
    with pytest.raises(SystemExit) as excinfo:
        verify_mod.main()
    assert excinfo.value.code == 1
    captured: str = capsys.readouterr().out
    assert "scaffold stub" in captured


def test_skipped_profiler_samples_fails_full_run(
    verify_mod: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(verify_mod, "_find_python", lambda _version: sys.executable)
    monkeypatch.setattr(verify_mod.subprocess, "check_output", _fake_python_check_output)

    class _FakeProc:
        returncode: int = 0
        stdout: str = json.dumps(
            {
                "asyncio_guards": {"passed": True},
                "profiler_samples": {
                    "passed": False,
                    "skipped": True,
                    "reason": "stack unavailable: missing",
                },
            }
        )
        stderr: str = ""

    monkeypatch.setattr(verify_mod.subprocess, "run", lambda *args, **kwargs: _FakeProc())
    monkeypatch.setattr(sys, "argv", ["verify_profiler_compatibility.py"])
    with pytest.raises(SystemExit) as excinfo:
        verify_mod.main()
    assert excinfo.value.code == 1
    captured: str = capsys.readouterr().out
    assert "Some checks FAILED" in captured


def test_run_profiling_tests_wires_uwsgi_when_supported() -> None:
    text: str = _RUN_PROFILING_TESTS.read_text()
    assert '--hash-only "^profile-uwsgi\\$"' in text
    assert "ERROR: No 'profile-uwsgi' riot venvs found" in text
    assert "not wired in this runner yet" not in text
    assert "pip install exited" in text


def test_compare_enforces_min_wall_when_baseline_omits_sample_count(verify_mod: Any) -> None:
    results: dict[str, Any] = {
        "asyncio_guards": {"passed": True},
        "profiler_samples": {
            "passed": True,
            "wall_time_samples": 1,
            "asyncio_task_names_seen": ["compat-task-0"],
        },
    }
    baseline: dict[str, Any] = {
        "asyncio_guards": {"passed": True},
        "profiler_samples": {
            "passed": True,
            "min_wall_time_samples": 2,
            "asyncio_task_names_seen": ["compat-task-0"],
        },
    }
    failures: list[str] = verify_mod._compare_with_baseline(results, baseline)
    assert any("wall_time_samples dropped" in f for f in failures)
