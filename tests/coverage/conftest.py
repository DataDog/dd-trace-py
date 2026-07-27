import os

import pytest


_CI_ITR_ROLLOUT_ENV_VARS = (
    "DD_CIVISIBILITY_ITR_ENABLED",
    "_DD_COVERAGE_FILE_LEVEL",
    "_DD_CIVISIBILITY_ITR_FORCE_ENABLE_COVERAGE",
    "_DD_CIVISIBILITY_ITR_PREVENT_TEST_SKIPPING",
)


def _clear_ci_itr_rollout_env() -> None:
    for name in _CI_ITR_ROLLOUT_ENV_VARS:
        os.environ.pop(name, None)


_clear_ci_itr_rollout_env()


@pytest.fixture(autouse=True)
def clear_ci_itr_rollout_env() -> None:
    _clear_ci_itr_rollout_env()
