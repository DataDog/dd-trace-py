import os


_ITR_ROLLOUT_ENV_VARS = (
    "DD_CIVISIBILITY_ITR_ENABLED",
    "_DD_COVERAGE_FILE_LEVEL",
    "_DD_CIVISIBILITY_ITR_FORCE_ENABLE_COVERAGE",
    "_DD_CIVISIBILITY_ITR_PREVENT_TEST_SKIPPING",
)


def clear_itr_rollout_env() -> None:
    for name in _ITR_ROLLOUT_ENV_VARS:
        os.environ.pop(name, None)


clear_itr_rollout_env()
