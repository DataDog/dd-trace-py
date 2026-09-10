import pytest

from tests.testing._itr_env import clear_itr_rollout_env  # noqa: F401


@pytest.fixture(autouse=True)
def clear_ci_itr_rollout_env() -> None:
    clear_itr_rollout_env()
