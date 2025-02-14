import os
import time

import pytest


@pytest.fixture(scope="session", autouse=True)
def disable_coverage_for_subprocess():
    try:
        del os.environ["COV_CORE_SOURCE"]
    except KeyError:
        pass

@pytest.fixture(autouse=True)
def measure_time():
    start = time.monotonic_ns()
    yield
    end = time.monotonic_ns()
    duration = end - start
    print(f"Test took {duration}ns, {duration / (10 ** 6)}ms, {duration / (10 ** 9)}s")
