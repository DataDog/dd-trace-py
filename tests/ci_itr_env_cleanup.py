import os


_CI_ITR_ROLLOUT_ENV_VARS = (
    "DD_CIVISIBILITY_ITR_ENABLED",
    "_DD_COVERAGE_FILE_LEVEL",
    "_DD_CIVISIBILITY_ITR_FORCE_ENABLE_COVERAGE",
    "_DD_CIVISIBILITY_ITR_PREVENT_TEST_SKIPPING",
)
_CI_ITR_ROLLOUT_ENV_SUITES = ("coverage", "dd_coverage", "pytest", "testing")


def should_clear() -> bool:
    suite_name = os.environ.get("SUITE_NAME", "")
    return any(suite in suite_name for suite in _CI_ITR_ROLLOUT_ENV_SUITES)


def clear() -> None:
    if not should_clear():
        return
    for name in _CI_ITR_ROLLOUT_ENV_VARS:
        os.environ.pop(name, None)


clear()
