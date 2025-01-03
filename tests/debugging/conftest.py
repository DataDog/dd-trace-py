import sys

import pytest


@pytest.fixture
def stuff():
    was_loaded = "tests.submod.stuff" in sys.modules
    if was_loaded:
        del sys.modules["tests.submod.stuff"]

    __import__("tests.submod.stuff")
    yield sys.modules["tests.submod.stuff"]

    del sys.modules["tests.submod.stuff"]
    if was_loaded:
        __import__("tests.submod.stuff")
