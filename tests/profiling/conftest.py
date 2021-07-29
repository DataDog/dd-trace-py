import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def disable_coverage_for_subprocess():
    try:
        del os.environ["COV_CORE_SOURCE"]
    except KeyError:
        pass
