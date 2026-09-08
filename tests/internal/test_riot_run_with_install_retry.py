"""Tests for scripts/riot_run_with_install_retry.py."""

from __future__ import annotations

from collections.abc import Sequence
from importlib.machinery import ModuleSpec
import importlib.util
import pathlib
from types import ModuleType

import pytest


_SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "riot_run_with_install_retry.py"
_TESTS_YML = pathlib.Path(__file__).resolve().parents[2] / ".gitlab" / "tests.yml"


@pytest.fixture(scope="module")
def retry_mod() -> ModuleType:
    spec: ModuleSpec | None = importlib.util.spec_from_file_location("riot_run_with_install_retry", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_riot_jobs_use_install_retry_wrapper() -> None:
    text: str = _TESTS_YML.read_text()
    assert "riot_run_with_install_retry.py" in text
    assert "${RIOT_RUN_CMD}" in text


def test_attempts_from_env_defaults_and_clamps(retry_mod: ModuleType) -> None:
    assert retry_mod.attempts_from_env({}) == 3
    assert retry_mod.attempts_from_env({"RIOT_INSTALL_RETRIES": "5"}) == 5
    assert retry_mod.attempts_from_env({"RIOT_INSTALL_RETRIES": "0"}) == 1
    assert retry_mod.attempts_from_env({"RIOT_INSTALL_RETRIES": "nope"}) == 3


def test_install_failure_is_retryable_test_failure_is_not(retry_mod: ModuleType) -> None:
    install_output: str = "Failed to install venv dependencies torch~=2.11.0\nBrokenPipeError"
    assert retry_mod.is_retryable_venv_install_failure(install_output) is True
    assert retry_mod.is_retryable_venv_install_failure("BrokenPipeError: [Errno 32] Broken pipe") is True
    assert retry_mod.is_retryable_venv_install_failure("Test failed with exit code 1") is False
    mixed: str = "Failed to install venv dependencies foo\nTest failed with exit code 1"
    assert retry_mod.is_retryable_venv_install_failure(mixed) is False


def test_retries_venv_install_failure_then_succeeds(retry_mod: ModuleType) -> None:
    calls: list[int] = []

    def fake_run(argv: Sequence[str]) -> tuple[int, str]:
        calls.append(1)
        if len(calls) < 2:
            return 1, "Failed to install venv dependencies torch~=2.11.0"
        return 0, "ok"

    code: int = retry_mod.run_with_install_retry(["riot", "run", "abc"], attempts=3, run=fake_run)
    assert code == 0
    assert len(calls) == 2


def test_does_not_retry_test_failure(retry_mod: ModuleType) -> None:
    calls: list[int] = []

    def fake_run(argv: Sequence[str]) -> tuple[int, str]:
        calls.append(1)
        return 1, "Test failed with exit code 1"

    code: int = retry_mod.run_with_install_retry(["riot", "run", "abc"], attempts=3, run=fake_run)
    assert code == 1
    assert len(calls) == 1


def test_exhausts_install_retries(retry_mod: ModuleType) -> None:
    calls: list[int] = []

    def fake_run(argv: Sequence[str]) -> tuple[int, str]:
        calls.append(1)
        return 1, "Failed to install venv dependencies nvidia_nccl_cu13"

    code: int = retry_mod.run_with_install_retry(["riot", "run", "abc"], attempts=3, run=fake_run)
    assert code == 1
    assert len(calls) == 3


def test_main_requires_a_command(retry_mod: ModuleType) -> None:
    code: int = retry_mod.main([])
    assert code == 2
