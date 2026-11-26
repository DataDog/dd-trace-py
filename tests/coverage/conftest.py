import pytest

from tests.testing._itr_env import clear_itr_rollout_env  # noqa: F401


@pytest.fixture(autouse=True)
def clear_ci_itr_rollout_env() -> None:
    clear_itr_rollout_env()


@pytest.fixture(autouse=True)
def python_collector_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    # These tests check the Python collector, in its file-level and line-level modes. On CPython 3.12+, native line
    # collection replaces that collector unless it is turned off, so it is turned off here and these tests do not
    # exercise native collection; tests/testing covers it. Subprocess tests inherit the variable, and a test that
    # checks native collection sets it itself.
    monkeypatch.setenv("_DD_COVERAGE_NATIVE_LINES", "false")
