import sys

import pytest

from ddtrace.internal.utils.inspection import _functions_for_code_gc


@pytest.fixture
def stuff():
    _functions_for_code_gc.cache_clear()
    was_loaded = "tests.submod.stuff" in sys.modules
    if was_loaded:
        del sys.modules["tests.submod.stuff"]

    __import__("tests.submod.stuff")
    yield sys.modules["tests.submod.stuff"]

    del sys.modules["tests.submod.stuff"]
    if was_loaded:
        __import__("tests.submod.stuff")
