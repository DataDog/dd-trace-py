import pytest

from tests import ci_itr_env_cleanup


@pytest.fixture(autouse=True)
def clear_ci_itr_rollout_env() -> None:
    ci_itr_env_cleanup.clear()
