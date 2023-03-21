import sys

import pytest


@pytest.fixture
def stuff():
    was_loaded = False
    if "tests.submod.stuff" in sys.modules:
        was_loaded = True
        del sys.modules["tests.submod.stuff"]

    __import__("tests.submod.stuff")
    yield sys.modules["tests.submod.stuff"]

    del sys.modules["tests.submod.stuff"]
    if was_loaded:
        __import__("tests.submod.stuff")
