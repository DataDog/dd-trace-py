# -*- coding: utf-8 -*-
"""
Common tests to aspects, ensuring that they don't break when receiving empty arguments.
"""
import os

import pytest

from ddtrace.appsec._iast._taint_tracking import TagMappingMode  # noqa: F401
from tests.appsec.iast.aspects.conftest import _iast_patched_module
import tests.appsec.iast.fixtures.aspects.callees


def generate_callers_from_callees(callers_file=""):
    """
    Generate a callers module from a callees module, calling all it's functions.
    """
    module_functions = [
        "bytearray",
        "bytes",
        "str",
    ]

    with open(callers_file, "w", encoding="utf-8") as callers:
        for function in module_functions:
            callers.write(
                f"""
def callee_{function}(*args, **kwargs):
    return {function}(*args, **kwargs)\n
                """
            )


PATCHED_CALLERS_FILE = "tests/appsec/iast/fixtures/aspects/callers.py"
UNPATCHED_CALLERS_FILE = "tests/appsec/iast/fixtures/aspects/unpatched_callers.py"

for _file in (PATCHED_CALLERS_FILE, UNPATCHED_CALLERS_FILE):
    generate_callers_from_callees(
        callers_file=_file,
    )

patched_callers = _iast_patched_module(PATCHED_CALLERS_FILE.replace("/", ".")[0:-3])
# This import needs to be done after the file is created (previous line)
# pylint: disable=[wrong-import-position],[no-name-in-module]
from tests.appsec.iast.fixtures.aspects import unpatched_callers  # type: ignore[attr-defined] # noqa: E402


@pytest.mark.parametrize("aspect", [x for x in dir(unpatched_callers) if not x.startswith(("_", "@"))])
def test_aspect_patched_result(aspect):
    """
    Test that the result of the patched aspect call is the same as the unpatched one.
    """
    assert getattr(patched_callers, aspect)() == getattr(unpatched_callers, aspect)()


def teardown_module(_):
    """
    Remove the callers file after the tests are done.
    """
    for _file in (PATCHED_CALLERS_FILE, UNPATCHED_CALLERS_FILE):
        try:
            os.remove(_file)
        except FileNotFoundError:
            pass
        assert not os.path.exists(_file)
