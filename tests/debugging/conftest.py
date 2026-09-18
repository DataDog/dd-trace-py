import sys

import pytest

from ddtrace.internal.utils import inspection


def _is_submod(name):
    return name == "tests.submod" or name.startswith("tests.submod.")


@pytest.fixture(autouse=True)
def _isolate_volatile_submodules():
    """Tests in this suite import, instrument, and sometimes force-reload
    modules under tests/submod/. Restoring their sys.modules presence and
    forcing a fresh reimport after every test prevents one test's module
    state (and any instrumentation attached to it) from leaking into
    whichever test happens to run next.
    """
    was_loaded = {name for name in sys.modules if _is_submod(name)}

    yield

    for name in [name for name in sys.modules if _is_submod(name)]:
        del sys.modules[name]
    for name in was_loaded:
        __import__(name)
    inspection.clear()


@pytest.fixture
def stuff():
    __import__("tests.submod.stuff")
    return sys.modules["tests.submod.stuff"]
