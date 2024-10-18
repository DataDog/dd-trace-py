import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def disable_coverage_for_subprocess():
    try:
        del os.environ["COV_CORE_SOURCE"]
    except KeyError:
        pass


def pytest_configure(config):
    config.addinivalue_line("markers", "serial: mark test to run serially")


def pytest_collection_modifyitems(config, items):
    serial = []
    parallel = []

    for item in items:
        if "serial" in item.keywords:
            serial.append(item)
        else:
            parallel.append(item)

    items[:] = serial + parallel


def pytest_runtest_protocol(item, nextitem):
    if "serial" in item.keywords:
        # Run serial tests sequentially
        if nextitem and "serial" in nextitem.keywords:
            return False
    return None
