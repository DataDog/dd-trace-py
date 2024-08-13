# -*- coding: utf-8 -*-
"""
Common tests to aspects, like ensuring that they don't break when receiving extra arguments.
"""
import os

import pytest

from tests.appsec.iast.aspects.conftest import _iast_patched_module
import tests.appsec.iast.fixtures.aspects.callees


def generate_callers_from_callees(callees_module, callers_file="", callees_module_str=""):
    """
    Generate a callers module from a callees module, calling all it's functions.
    """
    module_functions = [x for x in dir(callees_module) if not x.startswith(("_", "@"))]

    with open(callers_file, "w", encoding="utf-8") as callers:
        callers.write(f"from {callees_module_str} import *\n")
        callers.write(f"import {callees_module_str} as _original_callees\n")

        for function in module_functions:
            callers.write(
                f"""
def callee_{function}(*args, **kwargs):
    return _original_callees.{function}(*args, **kwargs)

def callee_{function}_direct(*args, **kwargs):
    return {function}(*args, **kwargs)\n
            """
            )


PATCHED_CALLERS_FILE = "tests/appsec/iast/fixtures/aspects/callers.py"
UNPATCHED_CALLERS_FILE = "tests/appsec/iast/fixtures/aspects/unpatched_callers.py"

for _file in (PATCHED_CALLERS_FILE, UNPATCHED_CALLERS_FILE):
    generate_callers_from_callees(
        callers_file=_file,
        callees_module=tests.appsec.iast.fixtures.aspects.callees,
        callees_module_str="tests.appsec.iast.fixtures.aspects.callees",
    )

patched_callers = _iast_patched_module(PATCHED_CALLERS_FILE.replace("/", ".")[0:-3])
# This import needs to be done after the file is created (previous line)
# pylint: disable=[wrong-import-position],[no-name-in-module]
from tests.appsec.iast.fixtures.aspects import unpatched_callers  # type: ignore[attr-defined] # noqa: E402


@pytest.mark.parametrize("aspect", [x for x in dir(unpatched_callers) if not x.startswith(("_", "@"))])
@pytest.mark.parametrize("args", [(), ("a"), ("a", "b")])
@pytest.mark.parametrize("kwargs", [{}, {"dry_run": False}, {"dry_run": True}])
def test_aspect_patched_result(aspect, args, kwargs):
    """
    Test that the result of the patched aspect call is the same as the unpatched one.
    """
    assert getattr(patched_callers, aspect)(*args, **kwargs) == getattr(unpatched_callers, aspect)(*args, **kwargs)


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
